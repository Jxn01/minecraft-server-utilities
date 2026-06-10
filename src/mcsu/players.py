"""Track online players and accumulate play-time statistics.

Fed by player join/leave events (from the log parser or RCON ``list``), this
keeps an in-memory set of who is online and persists per-player totals to a
small JSON file so statistics survive restarts. This also powers the
"skip backup if nobody has been online" optimization from the original script.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class PlayerStats:
    name: str
    first_seen: str = ""
    last_seen: str = ""
    sessions: int = 0
    total_seconds: int = 0


@dataclass(slots=True)
class _Session:
    name: str
    joined_at: float


class PlayerTracker:
    """Thread-safe online-player set plus persistent play-time accounting."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._state_path = Path(state_path) if state_path else None
        self._lock = threading.RLock()
        self._online: dict[str, _Session] = {}
        self._stats: dict[str, PlayerStats] = {}
        # Tracks whether anyone has joined since the flag was last reset; used
        # to decide if a world has changed and is worth backing up.
        self._activity_since_reset = False
        self._load()

    # -- event ingestion --------------------------------------------------- #

    def player_joined(self, name: str, *, when: float | None = None) -> None:
        ts = when if when is not None else datetime.now().timestamp()
        with self._lock:
            self._online[name] = _Session(name=name, joined_at=ts)
            self._activity_since_reset = True
            stats = self._stats.setdefault(name, PlayerStats(name=name))
            now_iso = _iso(ts)
            if not stats.first_seen:
                stats.first_seen = now_iso
            stats.last_seen = now_iso
            stats.sessions += 1
            self._save_locked()

    def player_left(self, name: str, *, when: float | None = None) -> None:
        ts = when if when is not None else datetime.now().timestamp()
        with self._lock:
            session = self._online.pop(name, None)
            if session is not None:
                elapsed = max(0, int(ts - session.joined_at))
                stats = self._stats.setdefault(name, PlayerStats(name=name))
                stats.total_seconds += elapsed
                stats.last_seen = _iso(ts)
                self._save_locked()

    # -- queries ----------------------------------------------------------- #

    @property
    def online(self) -> list[str]:
        with self._lock:
            return sorted(self._online)

    @property
    def online_count(self) -> int:
        with self._lock:
            return len(self._online)

    def is_online(self, name: str) -> bool:
        with self._lock:
            return name in self._online

    def stats(self, name: str) -> PlayerStats | None:
        with self._lock:
            return self._stats.get(name)

    def all_stats(self) -> list[PlayerStats]:
        with self._lock:
            return sorted(self._stats.values(), key=lambda s: s.total_seconds, reverse=True)

    # -- backup-activity helpers ------------------------------------------ #

    def had_activity_since_reset(self) -> bool:
        """True if anyone has been online since :meth:`reset_activity`.

        Returns True while players are currently online too, so an in-progress
        session always counts as activity worth backing up.
        """
        with self._lock:
            return self._activity_since_reset or bool(self._online)

    def reset_activity(self) -> None:
        with self._lock:
            self._activity_since_reset = False

    def clear_online(self) -> None:
        """Forget the online set (e.g. after a server stop), keeping stats."""
        with self._lock:
            self._online.clear()

    # -- persistence ------------------------------------------------------- #

    def _load(self) -> None:
        if not self._state_path or not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, raw in data.get("stats", {}).items():
            known = set(PlayerStats.__slots__)  # type: ignore[attr-defined]
            self._stats[name] = PlayerStats(**{k: v for k, v in raw.items() if k in known})

    def _save_locked(self) -> None:
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"stats": {name: asdict(s) for name, s in self._stats.items()}}
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError:
            pass


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
