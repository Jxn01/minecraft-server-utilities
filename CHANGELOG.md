# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/jxn01/minecraft_server_utilities/releases/tag/v1.0.0
