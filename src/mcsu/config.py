"""Configuration model and TOML (de)serialization.

A single ``mcsu.toml`` describes one managed server instance. The schema is
expressed as dataclasses so it is self-documenting, easy to validate, and
trivial to extend. Loading is tolerant of missing optional sections and
raises :class:`ConfigError` with actionable messages for anything wrong.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from mcsu.errors import ConfigError
from mcsu.utils import parse_duration

DEFAULT_CONFIG_NAME = "mcsu.toml"


# --------------------------------------------------------------------------- #
# Sub-sections
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class JavaConfig:
    """How to launch the JVM that runs the server."""

    path: str | None = None  # None -> auto-detect (JAVA_HOME / PATH)
    min_memory: str = "2G"
    max_memory: str = "4G"
    # Extra JVM flags. The Aikar's-flags style GC tuning is a sensible default
    # for medium servers but is fully overridable.
    extra_flags: list[str] = field(
        default_factory=lambda: [
            "-XX:+UseG1GC",
            "-XX:+ParallelRefProcEnabled",
            "-XX:MaxGCPauseMillis=200",
            "-XX:+UnlockExperimentalVMOptions",
            "-XX:+DisableExplicitGC",
        ]
    )
    # Arguments passed to the server jar itself (after ``-jar server.jar``).
    server_args: list[str] = field(default_factory=lambda: ["nogui"])


@dataclass(slots=True)
class RconConfig:
    """Connection details for the Source RCON protocol."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 25575
    password: str = ""
    timeout: float = 5.0


@dataclass(slots=True)
class BackupConfig:
    """World/backup policy."""

    enabled: bool = True
    directory: str = "backups"
    # Directories (relative to the server dir) to include in a world backup.
    paths: list[str] = field(default_factory=lambda: ["world", "world_nether", "world_the_end"])
    interval: int = 3600  # seconds; parsed from strings like "1h"
    format: str = "tar.gz"  # one of: tar.gz, tar, zip
    compression_level: int = 6
    keep: int = 48  # max number of archives to retain (0 = unlimited)
    keep_days: int = 0  # also prune archives older than N days (0 = disabled)
    # Skip a scheduled backup when nobody has been online since the last one.
    skip_if_no_players: bool = True
    # Flush chunks to disk (save-off/save-all) via RCON before archiving.
    flush_before_backup: bool = True


@dataclass(slots=True)
class RestartConfig:
    """Scheduled restart policy with in-game countdown warnings."""

    enabled: bool = True
    interval: int = 14400  # seconds between restarts (0 = disabled)
    # Optional wall-clock times ("04:00") at which to restart, in addition to
    # or instead of the interval. Empty list = interval-based only.
    daily_times: list[str] = field(default_factory=list)
    # Seconds-before-restart at which to broadcast a warning.
    warning_seconds: list[int] = field(default_factory=lambda: [300, 60, 30, 10, 5, 4, 3, 2, 1])
    warning_message: str = "Server restarting in {time}!"


@dataclass(slots=True)
class WatchdogConfig:
    """Crash detection and auto-restart policy."""

    enabled: bool = True
    check_interval: int = 15  # seconds between liveness checks
    max_restarts: int = 5  # within the window before giving up (0 = unlimited)
    restart_window: int = 600  # sliding window in seconds for max_restarts
    restart_backoff: int = 5  # base seconds of backoff between auto-restarts


@dataclass(slots=True)
class NotificationsConfig:
    """Outbound notifications (currently Discord webhooks)."""

    enabled: bool = False
    discord_webhook: str = ""
    username: str = "mcsu"
    # Event types (by EventType value) that should trigger a notification.
    events: list[str] = field(
        default_factory=lambda: [
            "server_ready",
            "server_stopped",
            "server_crashed",
            "backup_completed",
            "backup_failed",
        ]
    )
    notify_player_join: bool = False
    notify_player_leave: bool = False
    notify_chat: bool = False


@dataclass(slots=True)
class ServerConfig:
    """The top-level configuration object for one managed server."""

    name: str = "minecraft"
    directory: str = "."
    jar: str = "server.jar"
    # Free-form metadata, surfaced in `mcsu status` and notifications.
    loader: str = "vanilla"  # vanilla|paper|purpur|fabric|forge|neoforge|quilt
    mc_version: str = ""
    # The phrase that marks "server finished loading" in the console. Covers
    # vanilla/Paper/Fabric/Forge which all print "Done (..)! For help".
    ready_pattern: str = r"Done \([0-9.,]+s\)"
    stop_timeout: int = 90  # seconds to wait for a graceful stop before killing
    auto_accept_eula: bool = False

    java: JavaConfig = field(default_factory=JavaConfig)
    rcon: RconConfig = field(default_factory=RconConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)

    # Populated by :func:`load_config`; the directory the config was read from.
    _config_path: Path | None = field(default=None, compare=False, repr=False)

    # -- Derived paths ----------------------------------------------------- #

    @property
    def server_dir(self) -> Path:
        """Absolute path to the server working directory."""
        base = self._config_path.parent if self._config_path else Path.cwd()
        return (base / self.directory).resolve()

    @property
    def jar_path(self) -> Path:
        return (self.server_dir / self.jar).resolve()

    @property
    def backup_dir(self) -> Path:
        return (self.server_dir / self.backup.directory).resolve()

    @property
    def state_dir(self) -> Path:
        """Where mcsu keeps its runtime state (pid file, player stats, ...)."""
        return (self.server_dir / ".mcsu").resolve()

    @property
    def log_path(self) -> Path:
        return (self.server_dir / "logs" / "latest.log").resolve()

    def validate(self) -> None:
        """Raise :class:`ConfigError` for self-inconsistent configuration."""
        if not self.name.strip():
            raise ConfigError("server.name must not be empty")
        if self.backup.format not in {"tar.gz", "tar", "zip"}:
            raise ConfigError(
                f"backup.format must be one of tar.gz, tar, zip (got {self.backup.format!r})"
            )
        if not 0 <= self.backup.compression_level <= 9:
            raise ConfigError("backup.compression_level must be between 0 and 9")
        if self.rcon.enabled and not (0 < self.rcon.port < 65536):
            raise ConfigError(f"rcon.port out of range: {self.rcon.port}")
        for t in self.restart.daily_times:
            if not _valid_hhmm(t):
                raise ConfigError(f"restart.daily_times entry not HH:MM: {t!r}")


# --------------------------------------------------------------------------- #
# Loading / coercion
# --------------------------------------------------------------------------- #

# Fields that accept human durations ("1h30m") and are stored as int seconds.
_DURATION_FIELDS = {
    (BackupConfig, "interval"),
    (RestartConfig, "interval"),
    (WatchdogConfig, "check_interval"),
    (WatchdogConfig, "restart_window"),
    (WatchdogConfig, "restart_backoff"),
}


def _valid_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= h < 24 and 0 <= m < 60


def _coerce_section(cls: type, raw: dict[str, Any]) -> Any:
    """Build a dataclass instance from a raw TOML table, with validation."""
    if not isinstance(raw, dict):
        raise ConfigError(f"expected a table for [{cls.__name__}], got {type(raw).__name__}")
    known = {f.name for f in fields(cls) if not f.name.startswith("_")}
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            raise ConfigError(f"unknown key {key!r} in [{cls.__name__.lower()}] section")
        # Duration coercion for the handful of interval-style fields.
        if (cls, key) in _DURATION_FIELDS and isinstance(value, str):
            try:
                value = parse_duration(value)
            except ValueError as exc:
                raise ConfigError(f"{cls.__name__.lower()}.{key}: {exc}") from exc
        kwargs[key] = value
    return cls(**kwargs)


# Map of ServerConfig field name -> nested dataclass type.
_NESTED = {
    "java": JavaConfig,
    "rcon": RconConfig,
    "backup": BackupConfig,
    "restart": RestartConfig,
    "watchdog": WatchdogConfig,
    "notifications": NotificationsConfig,
}


def config_from_dict(raw: dict[str, Any], *, path: Path | None = None) -> ServerConfig:
    """Build (and validate) a :class:`ServerConfig` from a parsed dict."""
    # The top-level table may either be flat or nested under [server].
    server_raw = dict(raw.get("server", {}))
    nested_raw = {k: raw[k] for k in _NESTED if k in raw}

    # Allow scalar server fields at the top level too (convenience).
    top_scalars = {k: v for k, v in raw.items() if k not in _NESTED and k != "server"}
    server_raw = {**top_scalars, **server_raw}

    known = {f.name for f in fields(ServerConfig) if not f.name.startswith("_")}
    kwargs: dict[str, Any] = {}
    for key, value in server_raw.items():
        if key not in known:
            raise ConfigError(f"unknown key {key!r} in [server] section")
        if key in _NESTED:
            continue
        kwargs[key] = value

    for name, cls in _NESTED.items():
        if name in nested_raw:
            kwargs[name] = _coerce_section(cls, nested_raw[name])

    config = ServerConfig(**kwargs)
    config._config_path = path
    config.validate()
    return config


def load_config(path: str | Path | None = None) -> ServerConfig:
    """Load configuration from ``path`` (or auto-discover ``mcsu.toml``)."""
    resolved = _resolve_config_path(path)
    if resolved is None:
        raise ConfigError(
            "no configuration file found. Run `mcsu init` to create one, "
            "or pass --config /path/to/mcsu.toml"
        )
    try:
        with resolved.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{resolved}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {resolved}: {exc}") from exc
    return config_from_dict(raw, path=resolved)


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        candidate = Path(path)
        if candidate.is_dir():
            candidate = candidate / DEFAULT_CONFIG_NAME
        return candidate if candidate.is_file() else None
    # Walk up from CWD looking for mcsu.toml.
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Serialization (used by `mcsu init`)
# --------------------------------------------------------------------------- #


def to_toml(config: ServerConfig) -> str:
    """Serialize a :class:`ServerConfig` to an annotated TOML document.

    A hand-rolled writer keeps the package dependency-free while producing
    output that is friendlier (comments, ordering) than a generic dumper.
    """
    from mcsu._toml_template import render_template

    return render_template(config)


def _dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: _dataclass_to_dict(getattr(obj, f.name))
            for f in fields(obj)
            if not f.name.startswith("_")
        }
    return obj
