# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

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
