"""Fleet mode: many prepared servers, at most one running.

A *fleet* is a directory of ordinary mcsu servers -- each subdirectory holding
its own ``mcsu.toml`` -- plus one ``fleet.toml`` beside them. ``mcsu fleet run``
is a single long-running daemon that owns **at most one** active server at a
time (a :class:`~mcsu.supervisor.Supervisor` on a worker thread) and can switch
between them on request:

* switching away warns players in-game with a countdown, takes a backup of the
  outgoing world when it was played, stops it cleanly, then starts the new one;
* the active choice is persisted, so after a reboot or power cut the daemon
  resumes the server that was running (``resume = true``);
* a server whose watchdog gives up (crash loop) is reported and not resumed;
* a status document (``status.json``) describing every server -- and the active
  one's players, uptime and last backup -- is rewritten every few seconds, for
  ``mcsu fleet status``, for a public status page, and for Home Assistant;
* whitelist changes apply to **every** server at once (see :mod:`mcsu.whitelist`).

Commands reach the daemon from the CLI through a control file (the same
portable mechanism ``mcsu stop`` uses) and from Home Assistant through the MQTT
bridge in :mod:`mcsu.homeassistant`. Both feed one queue, executed in order on
the daemon's own thread.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcsu.config import DEFAULT_CONFIG_NAME, ServerConfig, load_config
from mcsu.errors import ConfigError, McsuError
from mcsu.properties import Properties
from mcsu.state import StateStore, pid_alive
from mcsu.utils import format_duration
from mcsu.whitelist import (
    WhitelistError,
    add_to_file,
    mojang_uuid,
    remove_from_file,
    valid_player_name,
)

log = logging.getLogger("mcsu.fleet")

FLEET_CONFIG_NAME = "fleet.toml"
COMMANDS = {"start", "stop", "restart", "backup", "whitelist_add", "whitelist_remove"}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class FleetSettings:
    """``[fleet]`` -- the daemon's behaviour."""

    name: str = "minecraft"  # identifies the fleet: MQTT topics/entity ids, status document
    root: str = "."  # directory whose subdirectories are the servers (relative to fleet.toml)
    address: str = ""  # what players type to join, shown in the status document
    state_dir: str = ".fleet"  # daemon state, status.json, control file, pid file
    public_status_file: str = ""  # optional extra copy of status.json (e.g. for a web page)
    status_interval: int = 30  # seconds between status rewrites when nothing changes
    resume: bool = True  # after a restart of the daemon, start the server that was active
    backup_on_switch: bool = True  # back up the outgoing world (if it was played) on switch/stop
    switch_warning_seconds: list[int] = field(default_factory=lambda: [60, 30, 10, 5, 4, 3, 2, 1])
    switch_message: str = "This server is closing in {time}: switching to {next}."
    stop_message: str = "This server is closing in {time}."
    shutdown_warning_seconds: int = 10  # countdown when the DAEMON stops (host shutdown)


@dataclass(slots=True)
class ServerMeta:
    """``[servers.<directory>]`` -- optional display overrides for one server."""

    title: str = ""  # shown in Home Assistant and the status page; default: mcsu.toml's name
    description: str = ""
    hidden: bool = False  # keep the directory out of the fleet entirely


@dataclass(slots=True)
class HomeAssistantConfig:
    """``[homeassistant]`` -- the MQTT bridge (see mcsu.homeassistant)."""

    enabled: bool = False
    broker: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    password_file: str = ""  # read instead of `password` when set (keep secrets out of fleet.toml)
    discovery_prefix: str = "homeassistant"
    base_topic: str = "mcsu"
    device_name: str = "Minecraft"
    keepalive: int = 30

    def resolved_password(self) -> str | None:
        if self.password_file:
            try:
                return Path(self.password_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigError(f"homeassistant.password_file: {exc}") from exc
        return self.password or None


@dataclass(slots=True)
class FleetConfig:
    settings: FleetSettings = field(default_factory=FleetSettings)
    servers: dict[str, ServerMeta] = field(default_factory=dict)
    homeassistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)
    _path: Path | None = field(default=None, compare=False, repr=False)

    @property
    def base_dir(self) -> Path:
        return self._path.parent if self._path else Path.cwd()

    @property
    def root_dir(self) -> Path:
        return (self.base_dir / self.settings.root).resolve()

    @property
    def state_dir(self) -> Path:
        return (self.base_dir / self.settings.state_dir).resolve()


def _section(cls: type, raw: Any, where: str) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"[{where}] must be a table")
    known = {f.name for f in fields(cls) if not f.name.startswith("_")}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown key(s) in [{where}]: {', '.join(sorted(unknown))}")
    return cls(**raw)


def fleet_from_dict(raw: dict[str, Any], *, path: Path | None = None) -> FleetConfig:
    unknown = set(raw) - {"fleet", "servers", "homeassistant"}
    if unknown:
        raise ConfigError(f"unknown table(s) in fleet.toml: {', '.join(sorted(unknown))}")
    cfg = FleetConfig(
        settings=_section(FleetSettings, raw.get("fleet", {}), "fleet"),
        servers={
            name: _section(ServerMeta, meta, f"servers.{name}")
            for name, meta in raw.get("servers", {}).items()
        },
        homeassistant=_section(HomeAssistantConfig, raw.get("homeassistant", {}), "homeassistant"),
    )
    cfg._path = path
    if cfg.settings.status_interval < 1:
        raise ConfigError("fleet.status_interval must be at least 1 second")
    return cfg


def load_fleet(path: str | Path | None = None) -> FleetConfig:
    candidate = Path(path) if path else Path.cwd() / FLEET_CONFIG_NAME
    if candidate.is_dir():
        candidate = candidate / FLEET_CONFIG_NAME
    if not candidate.is_file():
        raise ConfigError(f"no fleet configuration at {candidate} (see `mcsu fleet --help`)")
    try:
        with candidate.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{candidate}: invalid TOML: {exc}") from exc
    return fleet_from_dict(raw, path=candidate.resolve())


@dataclass(slots=True)
class FleetMember:
    name: str  # the server's directory name: the id used by commands
    title: str
    description: str
    config: ServerConfig

    @property
    def version(self) -> str:
        loader = self.config.loader.capitalize() if self.config.loader else ""
        return f"{loader} {self.config.mc_version}".strip()


def discover(fleet: FleetConfig) -> list[FleetMember]:
    """Every subdirectory of the fleet root that holds an mcsu.toml, sorted by title."""
    members = []
    root = fleet.root_dir
    if not root.is_dir():
        raise ConfigError(f"fleet root {root} does not exist")
    for sub in sorted(root.iterdir()):
        if not (sub / DEFAULT_CONFIG_NAME).is_file():
            continue
        meta = fleet.servers.get(sub.name, ServerMeta())
        if meta.hidden:
            continue
        cfg = load_config(sub / DEFAULT_CONFIG_NAME)
        members.append(FleetMember(sub.name, meta.title or cfg.name, meta.description, cfg))
    if not members:
        raise ConfigError(f"no servers (subdirectories with {DEFAULT_CONFIG_NAME}) under {root}")
    titles = [m.title.casefold() for m in members]
    dupes = {t for t in titles if titles.count(t) > 1}
    if dupes:
        raise ConfigError(f"two servers share a title (Home Assistant needs them unique): {dupes}")
    return sorted(members, key=lambda m: m.title.casefold())


# --------------------------------------------------------------------------- #
# Control file (CLI -> daemon)
# --------------------------------------------------------------------------- #


def send_command(fleet: FleetConfig, command: str, arg: str | None = None) -> None:
    """Queue a command for the running daemon (appended; the daemon drains the file)."""
    if command not in COMMANDS:
        raise McsuError(f"unknown fleet command {command!r}")
    fleet.state_dir.mkdir(parents=True, exist_ok=True)
    with (fleet.state_dir / "control").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"command": command, "arg": arg}) + "\n")


def read_status(fleet: FleetConfig) -> dict[str, Any] | None:
    path = fleet.state_dir / "status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def daemon_pid(fleet: FleetConfig) -> int | None:
    try:
        pid = int((fleet.state_dir / "daemon.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds")


def _rss_mib(pid: int | None) -> int | None:
    """Resident memory of a process in MiB (Linux /proc; None elsewhere)."""
    if not pid:
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


SupervisorFactory = Callable[[ServerConfig], Any]


class FleetDaemon:
    """Owns at most one running server of a fleet; see the module docstring."""

    def __init__(
        self,
        fleet: FleetConfig,
        *,
        supervisor_factory: SupervisorFactory | None = None,
        bridge_factory: Callable[[FleetDaemon], Any] | None = None,
        resolver: Callable[[str], str | None] = mojang_uuid,
    ) -> None:
        self.fleet = fleet
        self.members: dict[str, FleetMember] = {m.name: m for m in discover(fleet)}
        self._supervisor_factory = supervisor_factory or self._default_supervisor
        self._resolver = resolver
        self._commands: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._shutdown = threading.Event()
        self._lock = threading.RLock()
        self._sup: Any = None
        self._thread: threading.Thread | None = None
        self._active: str | None = None
        self._state = "stopped"  # stopped|starting|running|restarting|switching|stopping|crashed
        self._crash_loop = False
        self._last_action = ""
        self._restarts_seen = 0  # the supervisor's restart count when it was last ready
        self._stopping_on_purpose = False
        self._next_status = 0.0
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self.bridge = None
        if fleet.homeassistant.enabled:
            if bridge_factory is None:
                from mcsu.homeassistant import HomeAssistantBridge

                bridge_factory = HomeAssistantBridge
            self.bridge = bridge_factory(self)

    @staticmethod
    def _default_supervisor(config: ServerConfig) -> Any:
        from mcsu.supervisor import Supervisor

        return Supervisor(config, console_mirror=False)

    # -- public API ---------------------------------------------------------- #

    @property
    def active(self) -> str | None:
        return self._active

    def ordered_members(self) -> list[FleetMember]:
        return sorted(self.members.values(), key=lambda m: m.title.casefold())

    def member_by_title(self, title: str) -> FleetMember | None:
        return next((m for m in self.members.values() if m.title == title), None)

    def request(self, command: str, arg: str | None = None) -> None:
        """Queue a command (thread-safe; called from the MQTT thread too)."""
        if command not in COMMANDS:
            log.warning("ignoring unknown command %r", command)
            return
        self._commands.put((command, arg))

    def add_status_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def shutdown(self) -> None:
        self._shutdown.set()

    def run(self) -> int:
        self._acquire_pid_file()
        try:
            persisted = self._load_state()
            self._crash_loop = bool(persisted.get("crash_loop"))
            if self.bridge is not None:
                self.bridge.start()
            wanted = persisted.get("active")
            if self.fleet.settings.resume and wanted in self.members:
                log.info("Resuming %s", wanted)
                self._start(str(wanted))
            self._publish_status(force=True)
            while not self._shutdown.is_set():
                self._drain_control_file()
                try:
                    command, arg = self._commands.get(timeout=1.0)
                except queue.Empty:
                    pass
                else:
                    self._execute(command, arg)
                self._check_worker()
                self._publish_status()
            # The host is going down (or `systemctl stop`): stop the server but keep it as the
            # active choice, so the next start resumes it.
            self._stop_active(
                warn_seconds=[self.fleet.settings.shutdown_warning_seconds],
                next_title=None,
                persist=False,
            )
            self._state = "stopped"
            self._publish_status(force=True)
            if self.bridge is not None:
                self.bridge.stop()
            return 0
        finally:
            (self.fleet.state_dir / "daemon.pid").unlink(missing_ok=True)

    # -- commands ------------------------------------------------------------ #

    def _execute(self, command: str, arg: str | None) -> None:
        log.info("command: %s %s", command, arg or "")
        try:
            if command == "start":
                if arg not in self.members:
                    raise McsuError(f"no server named {arg!r} in this fleet")
                self._start(arg)
            elif command == "stop":
                self._stop_active(next_title=None)
                self._last_action = "Stopped"
            elif command == "restart":
                if self._sup is not None:
                    self._sup.request_restart()
                    self._last_action = (
                        f"Restarting {self.members[self._active].title}" if self._active else ""
                    )
            elif command == "backup":
                if self._sup is not None:
                    threading.Thread(
                        target=self._sup.perform_backup, kwargs={"reason": "requested"}, daemon=True
                    ).start()
                    self._last_action = "Backup requested"
            elif command in ("whitelist_add", "whitelist_remove"):
                self._last_action = self.whitelist(command.removeprefix("whitelist_"), arg or "")
        except (McsuError, OSError) as exc:
            log.error("%s failed: %s", command, exc)
            self._last_action = f"{command} failed: {exc}"
        self._publish_status(force=True)

    def _start(self, name: str) -> None:
        if name == self._active and self._thread and self._thread.is_alive():
            return
        member = self.members[name]
        # Its mcsu.toml as it is NOW: an edit since the daemon started (Java flags, memory, ...)
        # applies at this start. A broken file raises here, before the running server is touched.
        member.config = load_config(self.fleet.root_dir / name / DEFAULT_CONFIG_NAME)
        if self._active is not None:
            self._stop_active(next_title=member.title)
        with self._lock:
            self._crash_loop = False
            self._active = name
            self._state = "starting"
            self._stopping_on_purpose = False
            self._save_state()
            self._sup = self._supervisor_factory(member.config)
            self._restarts_seen = 0
            self._thread = threading.Thread(target=self._sup.run, name=f"mcsu-{name}", daemon=True)
            self._thread.start()
        self._last_action = f"Started {member.title}"
        log.info("Started %s (%s)", member.title, name)

    def _stop_active(
        self, *, next_title: str | None, warn_seconds: list[int] | None = None, persist: bool = True
    ) -> None:
        sup, thread, name = self._sup, self._thread, self._active
        if sup is None or name is None:
            if persist:
                self._active = None
                self._save_state()
            return
        settings = self.fleet.settings
        self._state = "switching" if next_title else "stopping"
        self._stopping_on_purpose = True
        self._publish_status(force=True)
        if sup.ready and sup.online_players:
            self._countdown(sup, warn_seconds or settings.switch_warning_seconds, next_title)
        if settings.backup_on_switch and sup.ready and sup.players.had_activity_since_reset():
            sup.perform_backup(reason="switch" if next_title else "stop")
        sup.shutdown()
        if thread is not None:
            thread.join(timeout=sup.config.stop_timeout + 60)
        with self._lock:
            self._sup = None
            self._thread = None
            if persist:
                self._active = None
                self._save_state()
            self._state = "stopped"

    def _countdown(self, sup: Any, warnings: list[int], next_title: str | None) -> None:
        marks = sorted({w for w in warnings if w > 0}, reverse=True)
        template = (
            self.fleet.settings.switch_message if next_title else self.fleet.settings.stop_message
        )
        for i, remaining in enumerate(marks):
            sup.broadcast(template.format(time=format_duration(remaining), next=next_title or ""))
            # A plain sleep: during a daemon shutdown the shutdown event is already set, and
            # waiting on it would cut the players' warning to nothing.
            time.sleep(remaining - (marks[i + 1] if i + 1 < len(marks) else 0))

    def whitelist(self, action: str, name: str) -> str:
        """Add/remove ``name`` on every server; returns a one-line summary."""
        if not valid_player_name(name):
            raise McsuError(f"{name!r} is not a valid Java Edition player name")
        cache: dict[str, str | None] = {}

        def resolver(player: str) -> str | None:
            if player not in cache:
                cache[player] = self._resolver(player)
            return cache[player]

        changed, failed = 0, []
        for member in self.ordered_members():
            # The FILE is edited for every server, the running one included: `whitelist add` on the
            # console makes the server ask Mojang for the name, which fails for a name that has no
            # Java account -- the normal case on an offline-mode server. mcsu writes the UUID the
            # server would use (offline-derived or Mojang's) and the running server just reloads.
            directory = member.config.server_dir
            try:
                if action == "add":
                    did = add_to_file(directory, name, resolver=resolver)
                else:
                    did = remove_from_file(directory, name)
            except WhitelistError as exc:
                failed.append(f"{member.title}: {exc}")
                continue
            changed += did
            if did and member.name == self._active and self._sup is not None:
                # Reloading also makes enforce-whitelist kick a player who was just removed.
                self._sup.console("whitelist reload")
        verb = "Whitelisted" if action == "add" else "Removed"
        summary = f"{verb} {name} on {changed} server(s)"
        if failed:
            summary += f"; failed on {len(failed)}: {failed[0]}"
        log.info(summary)
        return summary

    # -- monitoring ---------------------------------------------------------- #

    def _check_worker(self) -> None:
        thread, sup, name = self._thread, self._sup, self._active
        if name is None or thread is None or sup is None:
            return
        if thread.is_alive():
            before = self._state
            restarts = getattr(sup, "restarts", 0)
            if sup.ready:
                self._state = "running"
                self._restarts_seen = restarts
            elif restarts > self._restarts_seen:
                # Down, and the watchdog is bringing it back after a crash -- not "running".
                if self._state != "restarting":
                    self._last_action = (
                        f"{self.members[name].title} crashed; restarting (auto-restart #{restarts})"
                    )
                self._state = "restarting"
            elif self._state == "running":
                self._state = "starting"  # a planned restart (scheduled or requested)
            if self._state != before:
                self._publish_status(force=True)  # listeners (Home Assistant) hear it now
            return
        if self._stopping_on_purpose:
            return
        # The supervisor returned on its own: its watchdog gave up (crash loop) or crashed hard.
        log.error("%s stopped on its own (gave up: %s)", name, getattr(sup, "gave_up", None))
        with self._lock:
            self._crash_loop = True
            self._state = "crashed"
            self._last_action = f"{self.members[name].title} crashed and was not restarted"
            self._sup = None
            self._thread = None
            self._active = None  # never resume into a crash loop
            self._save_state()
        self._publish_status(force=True)

    def status_document(self) -> dict[str, Any]:
        sup, active = self._sup, self._active
        member = self.members.get(active) if active else None
        state = StateStore(member.config.state_dir).read() if member else None
        players = sup.online_players if sup is not None else []
        servers = []
        for m in self.ordered_members():
            servers.append(
                {
                    "name": m.name,
                    "title": m.title,
                    "description": m.description,
                    "loader": m.config.loader,
                    "mc_version": m.config.mc_version,
                    "version": m.version,
                    "state": self._state if m.name == active else "stopped",
                    "max_players": _max_players(m.config.server_dir),
                }
            )
        return {
            "fleet": self.fleet.settings.name,
            "address": self.fleet.settings.address,
            "generated": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "active": active,
            "active_title": member.title if member else None,
            "state": self._state,
            "crash_loop": self._crash_loop,
            "players": players,
            "players_online": len(players),
            "max_players": _max_players(member.config.server_dir) if member else None,
            "version": member.version if member else None,
            "started": _iso(state.ready_at) if state and self._state == "running" else None,
            "last_backup": (state.last_backup or None) if state else None,
            "memory_mib": _rss_mib(sup.server_pid) if sup is not None else None,
            "last_action": self._last_action,
            "servers": servers,
        }

    def _publish_status(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_status:
            return
        self._next_status = now + self.fleet.settings.status_interval
        doc = self.status_document()
        _write_json(self.fleet.state_dir / "status.json", doc)
        if self.fleet.settings.public_status_file:
            public = {k: v for k, v in doc.items() if k not in {"memory_mib", "last_action"}}
            _write_json(self.fleet.base_dir / self.fleet.settings.public_status_file, public)
        for listener in list(self._listeners):
            try:
                listener(doc)
            except Exception:
                log.exception("status listener failed")

    # -- persistence --------------------------------------------------------- #

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads((self.fleet.state_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        _write_json(
            self.fleet.state_dir / "state.json",
            {
                "active": self._active,
                "crash_loop": self._crash_loop,
                "updated": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            },
        )

    def _drain_control_file(self) -> None:
        path = self.fleet.state_dir / "control"
        if not path.is_file():
            return
        claimed = path.with_name(f"control.{os.getpid()}")
        try:
            path.replace(claimed)  # atomic: a CLI appending now creates a fresh file
            lines = claimed.read_text(encoding="utf-8").splitlines()
        finally:
            claimed.unlink(missing_ok=True)
        for line in lines:
            try:
                msg = json.loads(line)
                self.request(str(msg["command"]), msg.get("arg"))
            except (ValueError, KeyError, TypeError):
                log.warning("ignoring malformed control line: %r", line)

    def _acquire_pid_file(self) -> None:
        self.fleet.state_dir.mkdir(parents=True, exist_ok=True)
        other = daemon_pid(self.fleet)
        if other and other != os.getpid():
            raise McsuError(f"a fleet daemon is already running (pid {other})")
        (self.fleet.state_dir / "daemon.pid").write_text(str(os.getpid()))


def _max_players(server_dir: Path) -> int | None:
    path = server_dir / "server.properties"
    if not path.is_file():
        return None
    value = Properties.load(path).get_int("max-players", 0)
    return value or None
