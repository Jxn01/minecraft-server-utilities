# Configuration reference

`mcsu` is configured with a single TOML file, `mcsu.toml`, created by
`mcsu init` and discovered automatically by walking up from the current
directory (override with `--config`/`-c`). All durations accept either a number
of seconds or a human string like `1h30m`, `240s`, `2d`.

A complete, commented example lives at
[`examples/mcsu.example.toml`](../examples/mcsu.example.toml).

---

## `[server]`

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"minecraft"` | Label used in logs, status, and notifications. |
| `directory` | string | `"."` | Server working directory, **relative to the config file**. |
| `jar` | string | `"server.jar"` | Server jar filename (relative to `directory`). |
| `loader` | string | `"vanilla"` | `vanilla`, `paper`, `folia`, `purpur`, `fabric`, `quilt`, `forge`, `neoforge`. Informational + used as the `install` default. |
| `mc_version` | string | `""` | Minecraft version, e.g. `1.20.4`. |
| `stop_timeout` | int (s) | `90` | How long to wait for a graceful `stop` before escalating to terminate/kill. |
| `auto_accept_eula` | bool | `false` | If `true`, writes `eula=true` to `eula.txt` on startup. **Setting this means you accept the [Minecraft EULA](https://aka.ms/MinecraftEULA).** |

## `[java]`

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | string | `""` | Java executable. Empty = auto-detect via `JAVA_HOME`, then `PATH`. |
| `min_memory` | string | `"2G"` | `-Xms` value. |
| `max_memory` | string | `"4G"` | `-Xmx` value. |
| `extra_flags` | list | G1GC tuning | JVM flags inserted before `-jar`. |
| `server_args` | list | `["nogui"]` | Arguments passed to the server jar. |

The launch command is:
`java -Xms<min> -Xmx<max> <extra_flags...> -jar <jar> <server_args...>`

## `[rcon]`

RCON is how `mcsu` (and you) send commands to a running server.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable RCON features. |
| `host` | string | `"127.0.0.1"` | RCON host. |
| `port` | int | `25575` | RCON port. |
| `password` | string | `""` | RCON password. When set, `mcsu run` writes the matching `enable-rcon`/`rcon.port`/`rcon.password` keys into `server.properties` automatically. |
| `timeout` | float (s) | `5.0` | Socket timeout. |

> **Tip:** set a non-empty `password` so `mcsu` can broadcast restart countdowns
> and flush chunks before backups via RCON instead of stdin.

## `[backup]`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Take scheduled backups. |
| `directory` | string | `"backups"` | Where archives are written (relative to `directory`). |
| `paths` | list | `["world", "world_nether", "world_the_end"]` | Directories/files to include. Missing entries are skipped. |
| `interval` | duration | `3600` | Time between scheduled backups. |
| `format` | string | `"tar.gz"` | `tar.gz`, `tar`, or `zip`. |
| `compression_level` | int | `6` | 0–9. |
| `keep` | int | `48` | Max archives to retain (0 = unlimited). |
| `keep_days` | int | `0` | Also prune archives older than N days (0 = disabled). |
| `skip_if_no_players` | bool | `true` | Skip a scheduled backup if nobody has been online since the last one. |
| `flush_before_backup` | bool | `true` | Run `save-off`/`save-all flush` via RCON for a consistent snapshot, then `save-on`. |

## `[restart]`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable scheduled restarts. |
| `interval` | duration | `14400` | Time between restarts (0 = disabled). |
| `daily_times` | list | `[]` | Wall-clock times like `["04:00"]`. Combine with or replace `interval`. |
| `warning_seconds` | list | `[300, 60, 30, 10, 5, 4, 3, 2, 1]` | Seconds-before-restart at which to broadcast a warning. |
| `warning_message` | string | `"Server restarting in {time}!"` | `{time}` is replaced with the remaining time. |

## `[watchdog]`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Auto-restart the server if it exits unexpectedly. |
| `check_interval` | duration | `15` | Liveness check cadence. |
| `max_restarts` | int | `5` | Auto-restarts allowed within `restart_window` before giving up (0 = unlimited). |
| `restart_window` | duration | `600` | Sliding window for `max_restarts`. |
| `restart_backoff` | duration | `5` | Base backoff between auto-restarts (grows with consecutive crashes). |

## `[notifications]`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable outbound notifications. |
| `discord_webhook` | string | `""` | Discord webhook URL. |
| `username` | string | `"mcsu"` | Webhook display name. |
| `events` | list | ready/stopped/crashed/backup events | Event types that trigger a notification (see [`EventType`](../src/mcsu/events.py)). |
| `notify_player_join` | bool | `false` | Also notify on joins. |
| `notify_player_leave` | bool | `false` | Also notify on leaves. |
| `notify_chat` | bool | `false` | Also relay chat. |
