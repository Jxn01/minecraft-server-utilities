<div align="center">

# 🧰 mcsu — Minecraft Server Utilities

**Run, back up, and babysit Minecraft servers across versions and mod loaders — on Windows and Linux, with zero runtime dependencies.**

[![CI](https://github.com/jxn01/minecraft-server-utilities/actions/workflows/ci.yml/badge.svg)](https://github.com/jxn01/minecraft-server-utilities/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Runtime deps: 0](https://img.shields.io/badge/runtime%20deps-0-success.svg)](pyproject.toml)

</div>

---

## What is this?

`mcsu` is a single, self-contained command-line tool that handles the unglamorous
work of running a Minecraft (Java Edition) server: keeping it alive, restarting it
on a schedule with fair warning to players, taking consistent rotating world
backups, talking to it over RCON, and pinging you on Discord when something
happens.

The design goal is **set it and forget it**: a tested, dependency-free Python
package that works everywhere and across every major loader.

> **Why it's interesting as a project:** a from-scratch [RCON protocol
> implementation](src/mcsu/rcon.py), multi-API [server-jar
> installer](src/mcsu/installer.py), an event-driven [supervisor](src/mcsu/supervisor.py),
> safe archive handling, and a clean CLI — all on the standard library, with a
> test suite and CI.

## Highlights

| | Feature |
|---|---|
| 🖥️ | **Truly cross-platform.** Owns the Java process directly via `subprocess` — no `screen`, `tmux`, or `systemd` required. Runs the same on Windows, Linux, and macOS. |
| 🧩 | **Every major loader.** Vanilla, Paper, Folia, Purpur, Fabric, Quilt, Forge, and NeoForge — resolved and downloaded from their official APIs, checksum-verified. |
| 🔁 | **Scheduled restarts** with in-game countdown broadcasts (interval-based *and* wall-clock daily times). |
| ❤️‍🩹 | **Crash watchdog** with rate-limited auto-restart and crash-loop detection. |
| 💾 | **Rotating world backups** (`tar.gz`/`tar`/`zip`), retention by count *and* age, consistent snapshots via `save-off`/`save-all`, one-command restore, and a "skip backup when nobody's been online" optimization. |
| 🎛️ | **First-class RCON.** One-shot `mcsu cmd`, an interactive `mcsu console`, and internal use for broadcasts and backup flushing. |
| 🔔 | **Discord notifications** for readiness, crashes, backups, and (optionally) joins/leaves/chat. |
| 👥 | **Player tracking** — who's online plus persistent play-time stats. |
| ⚙️ | **`server.properties` & EULA management**, RCON auto-configuration, and a friendly `mcsu init` scaffold. |
| 🪶 | **Zero runtime dependencies.** Pure standard library. Easy to audit, trivial to deploy. |

## Install

```bash
# From a clone (recommended while iterating):
git clone https://github.com/jxn01/minecraft-server-utilities
cd minecraft-server-utilities
pip install .

# ...or directly from Git:
pip install "git+https://github.com/jxn01/minecraft-server-utilities"
```

Requires **Python 3.11+** and a **Java** runtime appropriate for your Minecraft
version (the JVM that actually runs the server). `mcsu` finds Java via
`--java`/config, `JAVA_HOME`, or `PATH`.

## Quickstart

```bash
# 1. Scaffold a server directory + annotated config
mcsu init --dir ./survival --name survival --loader paper --mc-version 1.20.4

cd survival

# 2. Download the server jar (any loader / version)
mcsu install                 # uses the loader+version from mcsu.toml
#   or explicitly:
mcsu install --loader fabric --mc-version 1.21 --output server.jar

# 3. (Optional) set an RCON password in mcsu.toml, then run it
mcsu run                     # foreground supervisor: the "set & forget" loop
```

In another terminal:

```bash
mcsu status                  # uptime, PID, restarts, last backup
mcsu cmd list                # run any console command over RCON
mcsu console                 # interactive RCON prompt
mcsu backup create           # ad-hoc backup
mcsu backup list
mcsu players                 # play-time leaderboard
mcsu restart                 # graceful restart with player countdown
mcsu stop                    # stop the server and exit the supervisor
```

Everything is also available as `python -m mcsu ...`.

## Working with versions and loaders

`mcsu` installs a specific build of any supported loader, and helps you discover
what's available:

```bash
# What Minecraft versions does a loader support?
mcsu versions --loader paper
mcsu versions --loader fabric

# What builds/loader-versions exist for a given Minecraft version?
mcsu versions --loader paper  --mc-version 1.20.4   # -> Paper build numbers
mcsu versions --loader fabric --mc-version 1.20.4   # -> Fabric loader versions
mcsu versions --loader forge  --mc-version 1.20.4   # -> Forge versions

# Pin an exact loader build:
mcsu install --loader paper  --mc-version 1.20.4 --loader-version 497
mcsu install --loader fabric --mc-version 1.20.4 --loader-version 0.16.5
mcsu install --loader forge  --mc-version 1.20.4 --loader-version 49.0.11
```

Omit `--loader-version` (and/or `--mc-version`) to get the latest. Forge and
NeoForge resolve and download the *installer* jar and print the one-time
`--installServer` step to finish setup.

## Configuration

`mcsu init` writes a fully commented [`mcsu.toml`](examples/mcsu.example.toml).
A trimmed example:

```toml
[server]
name = "survival"
loader = "paper"
mc_version = "1.20.4"
auto_accept_eula = false      # set true to accept the Minecraft EULA automatically

[java]
min_memory = "2G"
max_memory = "6G"

[rcon]
enabled = true
port = 25575
password = "change-me"        # also written to server.properties for you

[backup]
interval = "1h"               # durations accept 1h30m, 240s, ...
format = "tar.gz"
keep = 48
keep_days = 14
skip_if_no_players = true

[restart]
interval = "6h"
daily_times = ["04:00"]
warning_seconds = [300, 60, 30, 10, 5, 4, 3, 2, 1]

[watchdog]
enabled = true
max_restarts = 5              # within restart_window before giving up

[notifications]
enabled = true
discord_webhook = "https://discord.com/api/webhooks/..."
events = ["server_ready", "server_crashed", "backup_completed"]
```

See [`docs/configuration.md`](docs/configuration.md) for every option.

## Running it as a service

`mcsu run` is a well-behaved foreground process (it stops the server cleanly on
`SIGINT`/`SIGTERM`), so any supervisor works:

- **Linux (systemd):** [`docs/deployment.md`](docs/deployment.md) includes a ready unit file.
- **Windows:** wrap `mcsu run` with [NSSM](https://nssm.cc/) or Task Scheduler — see the same doc.

## Architecture

```
              ┌────────────┐   console lines   ┌──────────────┐
 Java server ─┤ ServerProcess├──────────────────▶  log parser  │
   (child)    └─────▲──────┘                    └──────┬───────┘
                    │ stdin / RCON                     │ structured events
                    │                                  ▼
              ┌─────┴───────────────── Supervisor ─────────────────┐
              │  scheduler · watchdog · backups · player tracker   │
              └───────────────────────┬────────────────────────────┘
                                      │ EventBus
                  ┌───────────────────┼────────────────────┐
                  ▼                   ▼                     ▼
            notifications        state file            (your plugin)
```

Each box is an independent, separately tested module under
[`src/mcsu/`](src/mcsu/). The [`EventBus`](src/mcsu/events.py) decouples the
control loop from consumers, so adding a metrics exporter or web dashboard is a
single subscriber.

## Development

```bash
pip install -e ".[dev]"
pytest                 # run the test suite
ruff check .           # lint
mypy                   # type-check
```

Contributions welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE). Minecraft is a trademark of Mojang Synergies AB; this is an
independent, unofficial tool. Running a server means accepting the
[Minecraft EULA](https://aka.ms/MinecraftEULA).
