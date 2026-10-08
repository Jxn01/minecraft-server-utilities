# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

## [1.1.2] — 2026-10-08

### Fixed

- **A crashed server read `running`** in fleet mode while its watchdog was
  restarting it: the fleet only ever moved *to* `running`, never away from it,
  so Home Assistant, `status.json` and `mcsu fleet status` showed a server up
  that was not. A crash now reads the new state **`restarting`** (with
  `last_action` = `<title> crashed; restarting (auto-restart #N)`) until the
  server is up again; a planned restart reads `starting`. See the states table
  in `docs/fleet.md`.
- **State changes reached `status.json` and Home Assistant only at the next
  `status_interval`** (30 s by default) when they came from the server itself
  (finished starting, crashed) rather than from a command. They are now written
  and published at once, as the documentation already said.
- **Crash loops of slow-booting servers were never caught.** `restart_window`
  was a wall-clock window, so a modpack that boots for longer than
  `restart_window / max_restarts` and crashes right after "Done" booted and
  crashed forever. A crash now counts when it comes within `restart_window` of
  the server's own (re)start; a server that stayed up for a whole window starts
  the count afresh.
- **A healthy server could be handled as crashed** when a health check read the
  process in the instant between a restart building it and starting it: the
  "crash" restarted it again, leaving a second server process on the same
  world. The crash handler now re-checks under its lock that the process really
  exited.

## [1.1.1] — 2026-10-08

### Fixed

- **Paper/Spigot/Purpur servers were never detected as ready**: their console
  prints `[12:34:56 INFO]: ...`, which the line parser did not recognise, so the
  "Done" line, joins, leaves and chat all went unseen. The parser now strips
  ANSI colour codes and accepts every console prefix in use: vanilla, Forge
  with a logger segment, Forge/NeoForge 1.17+ with a date, Fabric's
  `(Logger)` style and Bukkit-style servers.

## [1.1.0] — 2026-10-08

### Added

- **Fleet mode** (`mcsu fleet run|list|status|start|stop|whitelist`): a daemon
  that keeps many prepared servers and runs at most one, switching with an
  in-game countdown and a backup of the outgoing world, resuming the active
  server after a restart, detecting crash loops (and never resuming into one),
  and writing a `status.json` (plus an optional public copy). Configured by a
  `fleet.toml` beside the server directories. See `docs/fleet.md`.
- **Home Assistant integration** over MQTT discovery: one device with an
  *Active server* select, state, players, version, uptime, last backup,
  memory, crash-loop and last-action sensors, restart/backup/stop buttons and a
  fleet-wide whitelist (player-name text + add/remove buttons). See
  `docs/home-assistant.md`.
- **A dependency-free MQTT 3.1.1 client** (`mcsu.mqtt`): CONNECT with
  credentials and a last-will, QoS 0/1 publish, retained messages, subscribe,
  keepalive pings, reconnect with backoff and resubscription.
- **Fleet-wide whitelist** (`mcsu.whitelist`) that edits `whitelist.json` with
  the UUID each server expects — Mojang's for `online-mode=true`, the derived
  offline UUID otherwise — so it works for stopped servers and for names
  without a Java account.
- **`launch = "args_files"`** for Forge/NeoForge 1.17+ servers, which start from
  `@unix_args.txt` argument files instead of `-jar`.
- `deploy/mcsu-fleet.service` and `examples/fleet.example.toml`.

### Fixed

- A crash was handled **twice** when the main loop and the watchdog both noticed
  the dead process: two racing state-file writes and, potentially, two server
  restarts. A crash is now handled once per process.
- Concurrent writes of the runtime state file shared one temporary file name, so
  one writer could replace the other's file mid-write.

## [1.0.0] — 2026-06-10

Initial release of `mcsu`: a cross-platform, dependency-free Python suite for
running and babysitting Minecraft servers.

### Added

- **`mcsu` CLI** with subcommands: `init`, `install`, `versions`, `run`,
  `status`, `stop`, `restart`, `cmd`, `console`, `backup`, `properties`,
  `players`. Also runnable as `python -m mcsu`.
- **Cross-platform process supervision** — owns the Java server directly via
  `subprocess` (no `screen`/`tmux`), with graceful stop escalation and console
  fan-out. Works on Windows, Linux, and macOS.
- **Multi-loader installer** — Vanilla, Paper, Folia, Purpur, Fabric, Quilt,
  Forge, and NeoForge, resolved from official APIs with checksum verification.
- **From-scratch RCON client** implementing the Source RCON protocol over
  stdlib sockets, including multi-packet responses.
- **Rotating world backups** (`tar.gz`/`tar`/`zip`) with retention by count and
  age, consistent snapshots via `save-off`/`save-all`, traversal-safe restore,
  and a "skip backup when nobody's been online" optimization.
- **Scheduled restarts** with in-game countdown broadcasts, supporting both
  fixed intervals and wall-clock daily times.
- **Crash watchdog** with rate-limited auto-restart and crash-loop detection.
- **Player tracking** with persistent play-time statistics.
- **Discord webhook notifications** wired to an internal event bus.
- **`server.properties` and `eula.txt` management**, including automatic RCON
  configuration.
- **Annotated TOML configuration** with human-friendly durations.
- Full test suite (pytest), type checking (mypy), linting/formatting (ruff),
  multi-OS / multi-Python CI, and systemd/Docker deployment templates.

[1.0.0]: https://github.com/jxn01/minecraft-server-utilities/releases/tag/v1.0.0
