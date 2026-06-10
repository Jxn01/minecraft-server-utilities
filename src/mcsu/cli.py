"""Command-line interface for mcsu.

A single ``mcsu`` entry point exposes the whole suite through subcommands.
The CLI is built on :mod:`argparse` (no third-party dependency) and is split
into thin handlers that delegate to the library modules, so every feature is
equally usable programmatically.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from mcsu import __version__
from mcsu.config import (
    DEFAULT_CONFIG_NAME,
    ServerConfig,
    load_config,
    to_toml,
)
from mcsu.errors import McsuError
from mcsu.utils import colorize, format_duration, humanize_age


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except McsuError as exc:
        _error(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        return 130


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcsu",
        description="Minecraft Server Utilities — run and babysit Minecraft "
        "servers across versions and mod loaders, on Windows and Linux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `mcsu <command> --help` for command-specific options.",
    )
    parser.add_argument("--version", action="version", version=f"mcsu {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity")
    parser.add_argument(
        "-c", "--config", help=f"path to {DEFAULT_CONFIG_NAME} (default: auto-discover)"
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # init
    p = sub.add_parser("init", help="scaffold a new mcsu.toml and server directory")
    p.add_argument("--dir", default=".", help="server directory to initialize (default: .)")
    p.add_argument("--name", default="minecraft", help="server name")
    p.add_argument("--loader", default="vanilla", help="loader: vanilla|paper|fabric|...")
    p.add_argument("--mc-version", default="", help="Minecraft version, e.g. 1.20.4")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)

    # install
    p = sub.add_parser("install", help="download a server jar for a loader/version")
    p.add_argument("--loader", help="loader (defaults to config / vanilla)")
    p.add_argument("--mc-version", default="", help="Minecraft version (default: latest)")
    p.add_argument("--loader-version", default=None, help="loader/build version (default: latest)")
    p.add_argument("--output", default=None, help="output jar path (default: config jar)")
    p.set_defaults(func=cmd_install)

    # versions
    p = sub.add_parser("versions", help="list available Minecraft versions for a loader")
    p.add_argument("--loader", default="vanilla", help="loader to query")
    p.add_argument("--all", action="store_true", help="include snapshots/betas where applicable")
    p.add_argument("--limit", type=int, default=25, help="max versions to show")
    p.set_defaults(func=cmd_versions)

    # run
    p = sub.add_parser("run", help="run the supervisor in the foreground (main loop)")
    p.add_argument(
        "--no-console", action="store_true", help="don't mirror server console to stdout"
    )
    p.set_defaults(func=cmd_run)

    # status
    p = sub.add_parser("status", help="show the current server/supervisor status")
    p.set_defaults(func=cmd_status)

    # stop / restart
    p = sub.add_parser("stop", help="ask a running supervisor to stop the server and exit")
    p.set_defaults(func=cmd_stop)
    p = sub.add_parser("restart", help="ask a running supervisor to restart the server")
    p.set_defaults(func=cmd_restart)

    # cmd (RCON one-shot)
    p = sub.add_parser("cmd", help="run a console command via RCON")
    p.add_argument("rcon_command", nargs="+", help="the command to run, e.g. list")
    p.set_defaults(func=cmd_cmd)

    # console (interactive RCON)
    p = sub.add_parser("console", help="open an interactive RCON console")
    p.set_defaults(func=cmd_console)

    # backup
    p = sub.add_parser("backup", help="create, list, prune, or restore world backups")
    bsub = p.add_subparsers(dest="backup_action", metavar="<action>")
    bp = bsub.add_parser("create", help="create a new backup now")
    bp.add_argument("--label", default=None, help="optional label appended to the filename")
    bp.set_defaults(func=cmd_backup_create)
    bp = bsub.add_parser("list", help="list existing backups")
    bp.set_defaults(func=cmd_backup_list)
    bp = bsub.add_parser("prune", help="apply retention policy now")
    bp.set_defaults(func=cmd_backup_prune)
    bp = bsub.add_parser("restore", help="restore a backup into the server directory")
    bp.add_argument("name", help="backup filename (or path) to restore")
    bp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    bp.set_defaults(func=cmd_backup_restore)
    p.set_defaults(func=lambda a: _fail("specify a backup action: create|list|prune|restore"))

    # properties
    p = sub.add_parser("properties", help="get or set server.properties values")
    psub = p.add_subparsers(dest="prop_action", metavar="<action>")
    pp = psub.add_parser("get", help="print a property (or all)")
    pp.add_argument("key", nargs="?", help="property key (omit to list all)")
    pp.set_defaults(func=cmd_props_get)
    pp = psub.add_parser("set", help="set a property")
    pp.add_argument("key")
    pp.add_argument("value")
    pp.set_defaults(func=cmd_props_set)
    p.set_defaults(func=lambda a: _fail("specify: properties get|set"))

    # players
    p = sub.add_parser("players", help="show online players and play-time stats")
    p.add_argument("--online", action="store_true", help="show only who is online (via RCON)")
    p.set_defaults(func=cmd_players)

    return parser


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / DEFAULT_CONFIG_NAME
    if config_path.exists() and not args.force:
        _error(f"{config_path} already exists (use --force to overwrite)")
        return 1
    config = ServerConfig(name=args.name, loader=args.loader, mc_version=args.mc_version)
    config_path.write_text(to_toml(config), encoding="utf-8")
    (target_dir / "backups").mkdir(exist_ok=True)
    _ok(f"Created {colorize(str(config_path), 'cyan')}")
    print("\nNext steps:")
    print(
        f"  1. {colorize('mcsu install', 'bold')} "
        f"--loader {args.loader} --mc-version {args.mc_version or '<version>'}"
    )
    print(f"  2. Review {DEFAULT_CONFIG_NAME} (set an RCON password, memory, backups)")
    print(f"  3. {colorize('mcsu run', 'bold')}  (accepts the EULA if auto_accept_eula = true)")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from mcsu.installer import ServerInstaller

    config = _maybe_config(args)
    loader = args.loader or (config.loader if config else "vanilla")
    mc_version = args.mc_version or (config.mc_version if config else "")
    if args.output:
        output = Path(args.output).resolve()
    elif config:
        output = config.jar_path
    else:
        output = Path.cwd() / "server.jar"

    installer = ServerInstaller(progress=_make_progress())
    _info(
        f"Resolving {colorize(loader, 'bold')} for Minecraft "
        f"{colorize(mc_version or 'latest', 'bold')} ..."
    )
    result = installer.install(loader, mc_version, output, loader_version=args.loader_version)
    sys.stdout.write("\n")
    version_note = f" (loader {result.loader_version})" if result.loader_version else ""
    _ok(f"Downloaded {result.loader} {result.mc_version}{version_note} -> {result.jar_path}")
    if result.is_installer and result.post_install_hint:
        _info(result.post_install_hint)
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    from mcsu.installer import ServerInstaller

    installer = ServerInstaller()
    loader = args.loader.lower()
    if loader == "vanilla":
        versions = installer.list_vanilla_versions(releases_only=not args.all)
    else:
        _error(
            f"version listing is currently implemented for 'vanilla' (got {loader!r}). "
            "Use `mcsu install --loader <loader> --mc-version latest`."
        )
        return 2
    shown = versions[: args.limit]
    print(colorize(f"Latest {len(shown)} {loader} versions:", "bold"))
    for v in shown:
        print(f"  {v}")
    if len(versions) > len(shown):
        print(colorize(f"  ... and {len(versions) - len(shown)} more", "dim"))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from mcsu.supervisor import Supervisor

    config = _require_config(args)
    supervisor = Supervisor(config, console_mirror=not args.no_console)

    def handle_signal(signum, _frame):  # type: ignore[no-untyped-def]
        _info(f"Received signal {signum}; shutting down gracefully...")
        supervisor.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    _banner(config)
    return supervisor.run()


def cmd_status(args: argparse.Namespace) -> int:
    from mcsu.state import StateStore, pid_alive

    config = _require_config(args)
    state = StateStore(config.state_dir).read()
    print(colorize(f"Server: {config.name}", "bold"))
    print(f"  Loader:    {config.loader} {config.mc_version}".rstrip())
    print(f"  Directory: {config.server_dir}")
    if state is None:
        print(colorize("  Status:    not managed (no supervisor has run here)", "yellow"))
        return 0
    alive = pid_alive(state.supervisor_pid)
    status = state.status if alive else "stopped (supervisor not running)"
    color = "green" if state.status == "running" and alive else "yellow"
    print(f"  Status:    {colorize(status, color)}")
    if state.server_pid:
        print(f"  Server PID:{state.server_pid:>7}  (supervisor {state.supervisor_pid})")
    if state.ready_at:
        print(f"  Uptime:    {format_duration(time.time() - state.ready_at)}")
    if state.restarts:
        print(f"  Restarts:  {state.restarts}")
    if state.last_backup:
        print(f"  Last backup: {state.last_backup}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from mcsu.state import StateStore, pid_alive, write_control

    config = _require_config(args)
    state = StateStore(config.state_dir).read()
    if not state or not pid_alive(state.supervisor_pid):
        _error("no running supervisor found for this server")
        return 1
    write_control(config.state_dir, "stop")
    _ok("Sent stop request. The supervisor will stop the server and exit.")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    from mcsu.state import StateStore, pid_alive, write_control

    config = _require_config(args)
    state = StateStore(config.state_dir).read()
    if not state or not pid_alive(state.supervisor_pid):
        _error("no running supervisor found for this server")
        return 1
    write_control(config.state_dir, "restart")
    _ok("Sent restart request (players will see the countdown warnings).")
    return 0


def cmd_cmd(args: argparse.Namespace) -> int:
    from mcsu.rcon import send_command

    config = _require_config(args)
    _require_rcon(config)
    command = " ".join(args.rcon_command)
    response = send_command(
        command,
        host=config.rcon.host,
        port=config.rcon.port,
        password=config.rcon.password,
        timeout=config.rcon.timeout,
    )
    print(response.strip() or colorize("(no output)", "dim"))
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    from mcsu.rcon import RconClient

    config = _require_config(args)
    _require_rcon(config)
    _info(
        f"Connecting to RCON at {config.rcon.host}:{config.rcon.port} "
        "(type 'exit' or Ctrl-D to quit)"
    )
    with RconClient(
        config.rcon.host, config.rcon.port, config.rcon.password, config.rcon.timeout
    ) as client:
        while True:
            try:
                line = input(colorize("mcsu> ", "cyan"))
            except EOFError:
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit"):
                break
            response = client.command(line)
            if response.strip():
                print(response.strip())
    return 0


def cmd_backup_create(args: argparse.Namespace) -> int:
    config = _require_config(args)
    manager = _backup_manager(config)
    _info("Creating backup ...")
    info = manager.create(label=args.label)
    manager.prune()
    _ok(f"Created {info.name} ({info.size_human})")
    return 0


def cmd_backup_list(args: argparse.Namespace) -> int:
    config = _require_config(args)
    manager = _backup_manager(config)
    backups = manager.list_backups()
    if not backups:
        print(colorize("No backups yet.", "dim"))
        return 0
    print(colorize(f"{len(backups)} backup(s) in {manager.backup_dir}:", "bold"))
    for info in backups:
        print(
            f"  {info.name:<48} {info.size_human:>10}  "
            f"{colorize(humanize_age(info.created), 'dim')}"
        )
    return 0


def cmd_backup_prune(args: argparse.Namespace) -> int:
    config = _require_config(args)
    manager = _backup_manager(config)
    removed = manager.prune()
    if removed:
        _ok(f"Pruned {len(removed)} archive(s): " + ", ".join(b.name for b in removed))
    else:
        print(colorize("Nothing to prune.", "dim"))
    return 0


def cmd_backup_restore(args: argparse.Namespace) -> int:
    config = _require_config(args)
    manager = _backup_manager(config)
    if not args.yes:
        _warn("Restoring overwrites world files. Stop the server first!")
        confirm = input(f"Restore {args.name} into {config.server_dir}? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 1
    target = manager.restore(args.name)
    _ok(f"Restored {args.name} into {target}")
    return 0


def cmd_props_get(args: argparse.Namespace) -> int:
    from mcsu.properties import Properties

    config = _require_config(args)
    props = Properties.load(config.server_dir / "server.properties")
    if args.key:
        value = props.get(args.key)
        if value is None:
            _error(f"property not set: {args.key}")
            return 1
        print(value)
    else:
        for key, value in props.as_dict().items():
            print(f"{key}={value}")
    return 0


def cmd_props_set(args: argparse.Namespace) -> int:
    from mcsu.properties import Properties

    config = _require_config(args)
    path = config.server_dir / "server.properties"
    props = Properties.load(path)
    props.set(args.key, args.value)
    props.save(path)
    _ok(f"Set {args.key}={args.value}")
    return 0


def cmd_players(args: argparse.Namespace) -> int:
    config = _require_config(args)
    if args.online:
        _require_rcon(config)
        from mcsu.rcon import send_command

        response = send_command(
            "list",
            host=config.rcon.host,
            port=config.rcon.port,
            password=config.rcon.password,
            timeout=config.rcon.timeout,
        )
        print(response.strip())
        return 0

    from mcsu.players import PlayerTracker

    tracker = PlayerTracker(config.state_dir / "players.json")
    stats = tracker.all_stats()
    if not stats:
        print(colorize("No player statistics recorded yet.", "dim"))
        return 0
    print(colorize(f"{'Player':<18}{'Sessions':>10}{'Play time':>14}{'Last seen':>22}", "bold"))
    for s in stats:
        print(
            f"{s.name:<18}{s.sessions:>10}{format_duration(s.total_seconds):>14}{s.last_seen:>22}"
        )
    return 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _maybe_config(args: argparse.Namespace) -> ServerConfig | None:
    try:
        return load_config(args.config)
    except McsuError:
        return None


def _require_config(args: argparse.Namespace) -> ServerConfig:
    return load_config(args.config)


def _require_rcon(config: ServerConfig) -> None:
    if not config.rcon.enabled:
        raise McsuError("RCON is disabled in the config ([rcon] enabled = false)")
    if not config.rcon.password:
        raise McsuError(
            "RCON password is not set. Set [rcon] password in your config "
            "(and rcon.password in server.properties)."
        )


def _backup_manager(config: ServerConfig):  # type: ignore[no-untyped-def]
    from mcsu.backup import BackupManager

    return BackupManager(
        config.server_dir,
        config.backup_dir,
        paths=config.backup.paths,
        archive_format=config.backup.format,
        compression_level=config.backup.compression_level,
        keep=config.backup.keep,
        keep_days=config.backup.keep_days,
    )


def _make_progress():  # type: ignore[no-untyped-def]
    last = [0.0]

    def progress(done: int, total: int) -> None:
        now = time.monotonic()
        if now - last[0] < 0.1 and done != total:
            return
        last[0] = now
        if total > 0:
            pct = done / total * 100
            bar = "█" * int(pct // 4)
            sys.stdout.write(f"\r  [{bar:<25}] {pct:5.1f}%  ")
        else:
            sys.stdout.write(f"\r  downloaded {done // 1024} KiB  ")
        sys.stdout.flush()

    return progress


def _banner(config: ServerConfig) -> None:
    from mcsu.utils import platform_summary

    print(colorize("mcsu", "bold", "cyan") + colorize(f" v{__version__}", "dim"))
    summary = f"  server   {config.name} ({config.loader} {config.mc_version})".rstrip()
    print(colorize(summary, "dim"))
    print(colorize(f"  dir      {config.server_dir}", "dim"))
    print(colorize(f"  host     {platform_summary()}", "dim"))
    print(colorize("  Press Ctrl-C to stop the server and exit.\n", "dim"))


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format=colorize("%(asctime)s", "gray") + " %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _ok(msg: str) -> None:
    print(colorize("✓", "green") + " " + msg)


def _info(msg: str) -> None:
    print(colorize("→", "blue") + " " + msg)


def _warn(msg: str) -> None:
    print(colorize("!", "yellow") + " " + msg)


def _error(msg: str) -> None:
    print(colorize("✗", "red") + " " + msg, file=sys.stderr)


def _fail(msg: str, code: int = 2) -> int:
    """Print an error and return an exit code (for use in subcommand defaults)."""
    _error(msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
