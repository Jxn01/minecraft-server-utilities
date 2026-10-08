# Fleet mode — many servers, one running

A **fleet** is a set of prepared Minecraft servers of which **at most one runs at
a time**: an old modpack world, a vanilla survival, a Paper minigame server —
all ready, and whichever one your friends want tonight is the one that gets the
machine's RAM and CPU.

```
servers/                     <- the fleet root
├── fleet.toml               <- this file's subject
├── survival/   mcsu.toml    <- an ordinary mcsu server (mcsu init / mcsu install)
├── skyblock/   mcsu.toml
└── rlcraft/    mcsu.toml
```

`mcsu fleet run` is one long-running daemon for the whole fleet. It owns at most
one active server — a regular mcsu supervisor with its watchdog, scheduled
restarts and backups — and switches between them on request.

- [How it behaves](#how-it-behaves)
- [`fleet.toml` reference](#fleettoml-reference)
- [Command line](#command-line)
- [The status document](#the-status-document-statusjson)
- [Whitelist across the fleet](#whitelist-across-the-fleet)
- [Running it as a service](#running-it-as-a-service)
- [Home Assistant](home-assistant.md)

---

## How it behaves

**Switching** (`mcsu fleet start <server>`, or picking a server in Home Assistant):

1. If players are online on the running server, they get an in-game countdown
   (`switch_warning_seconds`, message `switch_message`).
2. If the outgoing world was played since its last backup and
   `backup_on_switch = true`, a backup is taken (the server's own `[backup]`
   settings decide format and retention).
3. The running server is stopped cleanly (`stop`, then terminate/kill after its
   `stop_timeout`).
4. The new server starts. Its state goes `starting` → `running` once it prints
   its "Done" line.

**Stopping** (`mcsu fleet stop`, or *Off* in Home Assistant) does steps 1–3 with
`stop_message`, and leaves no server active.

**Resuming.** The active choice is saved in `<state_dir>/state.json`. When the
daemon starts again — after a reboot, a power cut or `systemctl restart` — and
`resume = true`, it starts the server that was active. A daemon *shutdown*
(SIGTERM/SIGINT) stops the server (warning players for
`shutdown_warning_seconds`) **without** clearing that choice; only an explicit
stop clears it.

**Crash loops.** Each server's own `[watchdog]` restarts it after a crash; while
it is down and coming back the fleet reads `restarting` (never `running`), with
`last_action` = `<title> crashed; restarting (auto-restart #N)`. If the
watchdog gives up (more than `max_restarts` crashes in a row, each within
`restart_window` of its start), the daemon marks
the fleet `crashed`, sets `crash_loop = true` in the status document, clears the
active choice so the next start does **not** resume into the loop, and reports
the last action. Starting any server clears the flag.

**One daemon per fleet.** `<state_dir>/daemon.pid` keeps a second daemon from
starting on the same fleet (a stale file from a dead process is ignored).

**Commands** arrive from the CLI through `<state_dir>/control` (JSON lines,
drained atomically) and from Home Assistant through MQTT. Both feed one queue
that the daemon executes in order on its own thread.

---

## `fleet.toml` reference

Unknown tables or keys are an error (typos fail loudly).

### `[fleet]`

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"minecraft"` | Identifies the fleet: the MQTT topic segment and entity-id prefix (slugified), and `fleet` in the status document. |
| `root` | string | `"."` | Directory whose **subdirectories** are the servers, relative to `fleet.toml`. A subdirectory counts when it contains an `mcsu.toml`. |
| `address` | string | `""` | What players type to join (e.g. `mc.example.org`), copied into the status document for status pages. |
| `state_dir` | string | `".fleet"` | Where the daemon keeps `state.json`, `status.json`, `control` and `daemon.pid`, relative to `fleet.toml`. |
| `public_status_file` | string | `""` | When set, an extra copy of the status document is written here (relative to `fleet.toml`) **without** `memory_mib` and `last_action` — point a web server's document root at it for a public status page. |
| `status_interval` | int (s) | `30` | How often the status document is rewritten when nothing changes. Every command and state change rewrites it immediately. Minimum 1. |
| `resume` | bool | `true` | Start the previously active server when the daemon starts. |
| `backup_on_switch` | bool | `true` | Back up the outgoing world on switch/stop, if anyone played since its last backup. |
| `switch_warning_seconds` | list of int | `[60, 30, 10, 5, 4, 3, 2, 1]` | Countdown marks broadcast before a switch/stop, **only when players are online**. `[]` = no countdown. |
| `switch_message` | string | `"This server is closing in {time}: switching to {next}."` | `{time}` = remaining time, `{next}` = the next server's title. |
| `stop_message` | string | `"This server is closing in {time}."` | Used for stop and daemon shutdown. |
| `shutdown_warning_seconds` | int (s) | `10` | Countdown when the daemon itself is stopped (host shutdown). Keep it short: the service manager's stop timeout must also cover a backup and the server's own stop. |

### `[servers.<directory>]`

Optional, per server directory name:

| Key | Type | Default | Description |
|---|---|---|---|
| `title` | string | the server's `mcsu.toml` `name` | Shown in Home Assistant, the CLI and the status document. Titles must be unique across the fleet. |
| `description` | string | `""` | Free text for status pages (e.g. which client/modpack to use). |
| `hidden` | bool | `false` | Leave this directory out of the fleet entirely. |

### `[homeassistant]`

See [home-assistant.md](home-assistant.md) for what it creates.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Connect to an MQTT broker and announce the fleet to Home Assistant. |
| `broker` | string | `"127.0.0.1"` | MQTT broker host (Home Assistant's Mosquitto app, usually). |
| `port` | int | `1883` | Broker port (plain MQTT; TLS is not supported). |
| `username` | string | `""` | Broker login; empty = anonymous. |
| `password` | string | `""` | Broker password. Prefer `password_file`. |
| `password_file` | string | `""` | File whose first line is the password (read at start; keeps the secret out of `fleet.toml`). Takes precedence over `password`. |
| `discovery_prefix` | string | `"homeassistant"` | Home Assistant's MQTT discovery prefix. |
| `base_topic` | string | `"mcsu"` | Topics live under `<base_topic>/<slug of fleet.name>/`. |
| `device_name` | string | `"Minecraft"` | The device's name in Home Assistant. |
| `keepalive` | int (s) | `30` | MQTT keepalive; the broker publishes the last-will (*unavailable*) within ~1.5× this after the daemon vanishes. |

### Server launch modes

Servers in a fleet are ordinary mcsu servers, so every
[`mcsu.toml` option](configuration.md) applies. Two matter often for
modpacks:

- `[server] launch = "args_files"` with `args_files = ["libraries/net/minecraftforge/forge/<version>/unix_args.txt"]`
  for Forge/NeoForge 1.17+, which start from argument files instead of
  `-jar` (leave `user_jvm_args.txt` out — mcsu sets memory itself).
- `[java] path` per server, because old packs need Java 8 (1.7–1.16), others 17
  (1.17–1.20.4) or 21 (1.20.5+). A `-javaagent:` for a Log4Shell patch goes in
  `[java] extra_flags`.

---

## Command line

All fleet commands take `--fleet path/to/fleet.toml` (default `./fleet.toml`).

| Command | What it does |
|---|---|
| `mcsu fleet run` | Run the daemon in the foreground (SIGINT/SIGTERM stop it gracefully). |
| `mcsu fleet list` | List the servers: directory name, title, loader and version. |
| `mcsu fleet status [--json]` | Print the daemon's status document (or the raw JSON). |
| `mcsu fleet start <server>` | Ask the daemon to switch to `<server>` (its directory name). |
| `mcsu fleet stop` | Ask the daemon to stop the running server. |
| `mcsu fleet whitelist add\|remove <player>` | Add or remove a player on **every** server. With a daemon running it is queued to the daemon; without one, every `whitelist.json` is edited directly. |

---

## The status document (`status.json`)

Written to `<state_dir>/status.json` (and, minus `memory_mib`/`last_action`, to
`public_status_file`), and published to Home Assistant.

| Field | Type | Meaning |
|---|---|---|
| `fleet` | string | `[fleet] name` |
| `address` | string | `[fleet] address` |
| `generated` | ISO 8601 (UTC) | When this document was written |
| `active` | string \| null | Directory name of the active server |
| `active_title` | string \| null | Its title |
| `state` | string | One of the [states](#states) below |
| `crash_loop` | bool | The last server's watchdog gave up |
| `players` | list of string | Players online on the active server |
| `players_online` | int | `len(players)` |
| `max_players` | int \| null | The active server's `max-players` |
| `version` | string \| null | e.g. `Forge 1.18.2` (loader + `mc_version`) |
| `started` | ISO 8601 \| null | When the active server finished starting (only while `running`) |
| `last_backup` | string \| null | The active server's newest backup archive |
| `memory_mib` | int \| null | The server JVM's resident memory (Linux only) |
| `last_action` | string | What the last command did, e.g. `Whitelisted Steve on 18 server(s)` |
| `servers` | list | Every server: `name`, `title`, `description`, `loader`, `mc_version`, `version`, `state`, `max_players` |

### States

| `state` | Meaning |
|---|---|
| `stopped` | No server is running (`Off`). |
| `starting` | The active server is booting — after a start, a switch, a resume or a planned (scheduled or requested) restart — and has not printed its "Done" line yet. |
| `running` | Up and accepting players. |
| `restarting` | It **crashed** and its watchdog is restarting it (`last_action` says which auto-restart). Becomes `running` once it is up again, or `crashed` if the watchdog gives up. |
| `switching` | Warning players, backing up and stopping the old server before starting another. |
| `stopping` | The same, with nothing to follow. |
| `crashed` | The watchdog gave up (crash loop): the server is left stopped, `crash_loop` is `true`, and it is not resumed. |

In `servers`, every server but the active one reads `stopped`.

---

## Whitelist across the fleet

`whitelist add|remove` changes **every** server's `whitelist.json` at once — the
running one included, which is then told `whitelist reload` (with
`enforce-whitelist=true` that also kicks a player who was just removed).

The file is keyed by UUID, so mcsu writes the UUID **each server** would use:

- `online-mode=true`: the player's Mojang account UUID, looked up once per
  command from Mojang's public profile API (a name with no Java account is
  refused for those servers);
- `online-mode=false`: `UUID.nameUUIDFromBytes("OfflinePlayer:<name>")`, the
  offline UUID the server derives from the name alone.

mcsu deliberately does not use the console's `whitelist add`: the server would
ask Mojang for the name, which fails for names without a Java account — the
normal case on an offline-mode server.

---

## Running it as a service

[`deploy/mcsu-fleet.service`](../deploy/mcsu-fleet.service) is a systemd unit
for one fleet:

```bash
sudo cp deploy/mcsu-fleet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mcsu-fleet
journalctl -u mcsu-fleet -f
```

Give it a `TimeoutStopSec` long enough for `shutdown_warning_seconds` + a
backup + the server's `stop_timeout`, so a host shutdown never kills a server
mid-save. On Windows, run `mcsu fleet run` under NSSM or Task Scheduler exactly
like `mcsu run` ([deployment.md](deployment.md)).
