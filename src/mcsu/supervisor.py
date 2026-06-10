"""The supervisor: the central control loop.

``mcsu run`` instantiates a :class:`Supervisor`, which owns the server process
and orchestrates everything around it:

* launches the server and detects readiness
* parses console output into structured events on the bus
* tracks online players and play time
* runs scheduled, warned restarts (with in-game countdown broadcasts)
* takes rotating world backups (skipping idle, unchanged worlds)
* restarts the server automatically if it crashes (rate-limited)
* forwards events to notifiers (Discord, ...)
* maintains the runtime state file and shuts down cleanly on signals

It is deliberately framework-free and single-process so it runs identically
whether started by hand, by systemd, or by an NSSM Windows service.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path

from mcsu.backup import BackupManager
from mcsu.config import ServerConfig
from mcsu.events import EventBus, EventType
from mcsu.logparser import LineKind, parse_line
from mcsu.notifications import DiscordNotifier, NotificationService
from mcsu.players import PlayerTracker
from mcsu.properties import accept_eula, ensure_rcon_settings, is_eula_accepted
from mcsu.rcon import RconClient
from mcsu.scheduler import Scheduler
from mcsu.server import ServerProcess
from mcsu.state import RuntimeState, StateStore
from mcsu.utils import format_duration

log = logging.getLogger("mcsu.supervisor")


class Supervisor:
    """Owns and babysits one Minecraft server for its entire lifetime."""

    def __init__(self, config: ServerConfig, *, console_mirror: bool = True) -> None:
        self.config = config
        self.bus = EventBus()
        self.bus.on_listener_error(
            lambda exc, event: log.warning("listener error on %s: %s", event.type, exc)
        )
        self.players = PlayerTracker(config.state_dir / "players.json")
        self.state = StateStore(config.state_dir)
        self.backups = BackupManager(
            config.server_dir,
            config.backup_dir,
            paths=config.backup.paths,
            archive_format=config.backup.format,
            compression_level=config.backup.compression_level,
            keep=config.backup.keep,
            keep_days=config.backup.keep_days,
        )
        self._scheduler = Scheduler()
        self._scheduler.on_error(
            lambda name, exc: log.error("scheduled job %s failed: %s", name, exc)
        )

        self._proc: ServerProcess | None = None
        self._ready = threading.Event()
        self._shutdown = threading.Event()
        self._restart_requested = threading.Event()
        self._console_mirror = console_mirror
        self._lock = threading.RLock()

        # Sliding window of recent auto-restart timestamps for rate-limiting.
        self._restart_times: deque[float] = deque()
        self._restarts_total = 0
        self._intentional_stop = False

        self._setup_notifications()
        self._configure_schedules()

    # -- setup ------------------------------------------------------------- #

    def _setup_notifications(self) -> None:
        cfg = self.config.notifications
        if not cfg.enabled or not cfg.discord_webhook:
            return
        notifier = DiscordNotifier(cfg.discord_webhook, username=cfg.username)
        events = set(cfg.events)
        if cfg.notify_player_join:
            events.add(EventType.PLAYER_JOIN.value)
        if cfg.notify_player_leave:
            events.add(EventType.PLAYER_LEAVE.value)
        if cfg.notify_chat:
            events.add(EventType.PLAYER_CHAT.value)
        service = NotificationService([notifier], event_types=events)
        service.attach(self.bus)

    def _configure_schedules(self) -> None:
        backup = self.config.backup
        if backup.enabled:
            self._scheduler.every(backup.interval, self._scheduled_backup, name="backup")
        restart = self.config.restart
        if restart.enabled:
            if restart.interval > 0:
                self._scheduler.every(
                    restart.interval, self.request_restart, name="scheduled-restart"
                )
            if restart.daily_times:
                self._scheduler.daily(
                    restart.daily_times, self.request_restart, name="daily-restart"
                )
        wd = self.config.watchdog
        if wd.enabled:
            self._scheduler.every(wd.check_interval, self._watchdog_check, name="watchdog")

    # -- public API -------------------------------------------------------- #

    def run(self) -> int:
        """Run until shutdown is requested. Returns a process exit code."""
        log.info("Starting mcsu supervisor for %r", self.config.name)
        self._prepare_environment()
        self._scheduler.start()
        self._start_server()
        try:
            self._main_loop()
        finally:
            self._scheduler.stop()
            self._teardown()
        return 0

    def shutdown(self) -> None:
        """Signal the supervisor to stop the server and exit (idempotent)."""
        self._shutdown.set()

    def request_restart(self) -> None:
        """Request a graceful, warned restart of the server."""
        self._restart_requested.set()

    # -- environment prep -------------------------------------------------- #

    def _prepare_environment(self) -> None:
        self.config.server_dir.mkdir(parents=True, exist_ok=True)
        if self.config.auto_accept_eula and not is_eula_accepted(self.config.server_dir):
            accept_eula(self.config.server_dir)
            log.info("Accepted Minecraft EULA (auto_accept_eula = true)")
        if self.config.rcon.enabled and self.config.rcon.password:
            changed = ensure_rcon_settings(
                self.config.server_dir,
                port=self.config.rcon.port,
                password=self.config.rcon.password,
            )
            if changed:
                log.info("Enabled RCON in server.properties")

    # -- server lifecycle -------------------------------------------------- #

    def _build_process(self) -> ServerProcess:
        j = self.config.java
        proc = ServerProcess(
            server_dir=self.config.server_dir,
            jar=self.config.jar,
            java_path=j.path,
            min_memory=j.min_memory,
            max_memory=j.max_memory,
            extra_flags=j.extra_flags,
            server_args=j.server_args,
            stop_timeout=self.config.stop_timeout,
        )
        proc.add_line_callback(self._on_console_line)
        return proc

    def _start_server(self) -> None:
        with self._lock:
            self._ready.clear()
            self._intentional_stop = False
            self._proc = self._build_process()
            self.bus.emit(
                EventType.SERVER_STARTING,
                f"{self.config.name} is starting",
                name=self.config.name,
            )
            self._write_state("starting")
            self._proc.start()
            log.info(
                "Server process started (pid=%s): %s",
                self._proc.pid,
                " ".join(self._proc.build_command()),
            )

    def _on_console_line(self, line: str) -> None:
        if self._console_mirror:
            print(line, flush=True)
        parsed = parse_line(line)
        self.bus.emit(EventType.CONSOLE_LINE, line)
        kind = parsed.kind
        if kind is LineKind.SERVER_READY:
            self._ready.set()
            self._write_state("running", ready=True)
            self.bus.emit(
                EventType.SERVER_READY,
                f"{self.config.name} is online and accepting players",
                name=self.config.name,
            )
        elif kind is LineKind.PLAYER_JOIN and parsed.player:
            self.players.player_joined(parsed.player)
            self.bus.emit(
                EventType.PLAYER_JOIN,
                f"{parsed.player} joined ({self.players.online_count} online)",
                player=parsed.player,
                online=self.players.online_count,
            )
        elif kind is LineKind.PLAYER_LEAVE and parsed.player:
            self.players.player_left(parsed.player)
            self.bus.emit(
                EventType.PLAYER_LEAVE,
                f"{parsed.player} left ({self.players.online_count} online)",
                player=parsed.player,
                online=self.players.online_count,
            )
        elif kind is LineKind.CHAT and parsed.player:
            self.bus.emit(
                EventType.PLAYER_CHAT,
                f"<{parsed.player}> {parsed.message}",
                player=parsed.player,
                text=parsed.message,
            )
        elif kind is LineKind.DEATH and parsed.player:
            self.bus.emit(EventType.PLAYER_DEATH, parsed.message or "", player=parsed.player)
        elif kind is LineKind.ADVANCEMENT and parsed.player:
            self.bus.emit(
                EventType.PLAYER_ADVANCEMENT,
                f"{parsed.player} earned {parsed.message}",
                player=parsed.player,
                advancement=parsed.message,
            )
        elif kind is LineKind.ERROR:
            self.bus.emit(EventType.SERVER_ERROR, parsed.message or line)

    # -- main loop --------------------------------------------------------- #

    def _control_path(self) -> Path:
        return self.config.state_dir / "control"

    def _check_control(self) -> None:
        """Honor commands written by ``mcsu stop`` / ``mcsu restart``.

        A simple control file is the most portable way to signal a running
        supervisor: POSIX signals and Windows console events behave very
        differently, but a file works identically everywhere.
        """
        path = self._control_path()
        if not path.is_file():
            return
        try:
            command = path.read_text(encoding="utf-8").strip().lower()
            path.unlink(missing_ok=True)
        except OSError:
            return
        if command == "stop":
            log.info("Received stop command via control file")
            self._shutdown.set()
        elif command == "restart":
            log.info("Received restart command via control file")
            self.request_restart()
        elif command.startswith("backup"):
            log.info("Received backup command via control file")
            threading.Thread(
                target=self.perform_backup, kwargs={"reason": "requested"}, daemon=True
            ).start()

    def _main_loop(self) -> None:
        self._control_path().unlink(missing_ok=True)
        while not self._shutdown.is_set():
            self._check_control()
            if self._restart_requested.is_set():
                self._restart_requested.clear()
                self._perform_restart()
                continue

            proc = self._proc
            if proc is None:
                break
            if not proc.is_running():
                # The server exited on its own. If we didn't ask for it, the
                # watchdog handles recovery; otherwise fall through to restart.
                if self._intentional_stop:
                    self._intentional_stop = False
                    if not self._shutdown.is_set():
                        self._start_server()
                else:
                    self._handle_crash(proc.returncode)
            self._shutdown.wait(1.0)

    # -- restart with countdown ------------------------------------------- #

    def _perform_restart(self) -> None:
        proc = self._proc
        if proc is None or not proc.is_running():
            return
        self.bus.emit(
            EventType.SERVER_RESTART_SCHEDULED,
            f"{self.config.name} will restart shortly",
            name=self.config.name,
        )
        self._broadcast_countdown()
        self._intentional_stop = True
        self._write_state("stopping")
        proc.stop()
        self.players.clear_online()
        if not self._shutdown.is_set():
            self._start_server()

    def _broadcast_countdown(self) -> None:
        cfg = self.config.restart
        warnings = sorted({w for w in cfg.warning_seconds if w > 0}, reverse=True)
        previous = warnings[0] if warnings else 0
        for remaining in warnings:
            if self._shutdown.is_set():
                return
            gap = previous - remaining
            if gap > 0:
                time.sleep(gap)
            message = cfg.warning_message.format(time=format_duration(remaining))
            self._say(message)
            previous = remaining
        if previous > 0:
            time.sleep(previous)

    def _say(self, message: str) -> None:
        """Broadcast an in-game message, preferring RCON, falling back to stdin."""
        if self._try_rcon(f"say {message}"):
            return
        proc = self._proc
        if proc and proc.is_running():
            try:
                proc.send(f"say {message}")
            except Exception:
                pass

    def _try_rcon(self, command: str) -> bool:
        cfg = self.config.rcon
        if not cfg.enabled or not cfg.password or not self._ready.is_set():
            return False
        try:
            with RconClient(cfg.host, cfg.port, cfg.password, cfg.timeout) as client:
                client.command(command)
            return True
        except Exception:
            return False

    # -- crash handling / watchdog ---------------------------------------- #

    def _watchdog_check(self) -> None:
        proc = self._proc
        if proc is None or self._intentional_stop or self._shutdown.is_set():
            return
        if not proc.is_running():
            self._handle_crash(proc.returncode)

    def _handle_crash(self, returncode: int | None) -> None:
        if self._shutdown.is_set():
            return
        wd = self.config.watchdog
        log.warning("Server exited unexpectedly (code=%s)", returncode)
        self.players.clear_online()
        self._ready.clear()
        self.bus.emit(
            EventType.SERVER_CRASHED,
            f"{self.config.name} crashed (exit code {returncode}); attempting recovery",
            returncode=returncode,
        )
        self._write_state("crashed")

        if not wd.enabled:
            self._shutdown.set()
            return

        now = time.monotonic()
        window = wd.restart_window
        while self._restart_times and now - self._restart_times[0] > window:
            self._restart_times.popleft()
        if wd.max_restarts > 0 and len(self._restart_times) >= wd.max_restarts:
            log.error(
                "Reached %d restarts within %ds; giving up to avoid a crash loop.",
                wd.max_restarts,
                window,
            )
            self.bus.emit(
                EventType.SERVER_ERROR,
                f"Crash loop detected ({wd.max_restarts} restarts in {format_duration(window)}); "
                "supervisor is stopping. Investigate the server logs.",
            )
            self._shutdown.set()
            return

        backoff = wd.restart_backoff * (len(self._restart_times) + 1)
        self._restart_times.append(now)
        self._restarts_total += 1
        log.info("Restarting server in %ds (auto-restart #%d)", backoff, self._restarts_total)
        if self._shutdown.wait(backoff):
            return
        self._start_server()

    # -- backups ----------------------------------------------------------- #

    def _scheduled_backup(self) -> None:
        cfg = self.config.backup
        if cfg.skip_if_no_players and not self.players.had_activity_since_reset():
            self.bus.emit(
                EventType.BACKUP_SKIPPED,
                "No players online since the last backup; skipping.",
            )
            return
        self.perform_backup(reason="scheduled")
        self.players.reset_activity()

    def perform_backup(self, *, reason: str = "manual") -> None:
        cfg = self.config.backup
        running = self._proc is not None and self._proc.is_running()
        self.bus.emit(EventType.BACKUP_STARTED, f"Starting {reason} backup")
        flush = cfg.flush_before_backup and running and self._ready.is_set()
        try:
            if flush:
                self._try_rcon("save-off")
                self._try_rcon("save-all flush")
                time.sleep(1.0)
            info = self.backups.create()
            removed = self.backups.prune()
        except Exception as exc:
            log.error("Backup failed: %s", exc)
            self.bus.emit(EventType.BACKUP_FAILED, f"Backup failed: {exc}")
            return
        finally:
            if flush:
                self._try_rcon("save-on")
        msg = f"Backup created: {info.name} ({info.size_human})"
        if removed:
            msg += f"; pruned {len(removed)} old archive(s)"
        log.info(msg)
        self._update_state_field(last_backup=info.name)
        self.bus.emit(
            EventType.BACKUP_COMPLETED, msg, name=info.name, size=info.size, pruned=len(removed)
        )

    # -- state / teardown -------------------------------------------------- #

    def _write_state(self, status: str, *, ready: bool = False) -> None:
        import os

        existing = self.state.read() or RuntimeState()
        existing.supervisor_pid = os.getpid()
        existing.server_pid = self._proc.pid or 0 if self._proc else 0
        existing.status = status
        existing.loader = self.config.loader
        existing.mc_version = self.config.mc_version
        existing.restarts = self._restarts_total
        if status == "starting" and not existing.started_at:
            existing.started_at = time.time()
        if ready:
            existing.ready_at = time.time()
        self.state.write(existing)

    def _update_state_field(self, **fields: object) -> None:
        existing = self.state.read() or RuntimeState()
        for key, value in fields.items():
            setattr(existing, key, value)
        self.state.write(existing)

    def _teardown(self) -> None:
        log.info("Shutting down supervisor...")
        proc = self._proc
        if proc and proc.is_running():
            self._intentional_stop = True
            self.bus.emit(EventType.SERVER_STOPPING, f"{self.config.name} is stopping")
            self._write_state("stopping")
            proc.stop()
        self.bus.emit(EventType.SERVER_STOPPED, f"{self.config.name} has stopped")
        self._write_state("stopped")
        log.info("Supervisor stopped.")


def load_state(server_dir: str | Path) -> RuntimeState | None:
    """Convenience used by the CLI's status/stop commands."""
    return StateStore(Path(server_dir) / ".mcsu").read()
