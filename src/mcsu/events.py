"""A tiny synchronous publish/subscribe event bus.

The supervisor publishes structured events (player joins, backups, crashes,
console lines, ...) and subscribers — notifiers, the player tracker, the CLI
console view — react to them. Keeping this decoupled means new integrations
(a web dashboard, a Prometheus exporter, ...) can be added without touching
the core control loop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Every kind of event the suite can emit."""

    # Lifecycle
    SERVER_STARTING = "server_starting"
    SERVER_READY = "server_ready"
    SERVER_STOPPING = "server_stopping"
    SERVER_STOPPED = "server_stopped"
    SERVER_CRASHED = "server_crashed"
    SERVER_RESTART_SCHEDULED = "server_restart_scheduled"

    # Players
    PLAYER_JOIN = "player_join"
    PLAYER_LEAVE = "player_leave"
    PLAYER_CHAT = "player_chat"
    PLAYER_DEATH = "player_death"
    PLAYER_ADVANCEMENT = "player_advancement"

    # Backups
    BACKUP_STARTED = "backup_started"
    BACKUP_COMPLETED = "backup_completed"
    BACKUP_FAILED = "backup_failed"
    BACKUP_SKIPPED = "backup_skipped"

    # Console / diagnostics
    CONSOLE_LINE = "console_line"
    SERVER_WARNING = "server_warning"
    SERVER_ERROR = "server_error"

    # Generic / catch-all
    INFO = "info"


@dataclass(slots=True)
class Event:
    """A single immutable event with an arbitrary data payload."""

    type: EventType
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


Listener = Callable[[Event], None]


class EventBus:
    """Thread-safe synchronous event dispatcher.

    Listeners registered for a specific :class:`EventType` receive only those
    events; listeners registered with ``subscribe_all`` receive everything.
    Exceptions raised by a listener are isolated so one misbehaving subscriber
    cannot take down the control loop.
    """

    def __init__(self) -> None:
        self._listeners: dict[EventType, list[Listener]] = {}
        self._global: list[Listener] = []
        self._lock = threading.RLock()
        self._error_handler: Callable[[BaseException, Event], None] | None = None

    def on_listener_error(self, handler: Callable[[BaseException, Event], None]) -> None:
        """Install a hook invoked when a listener raises (defaults to silent)."""
        self._error_handler = handler

    def subscribe(self, event_type: EventType, listener: Listener) -> None:
        with self._lock:
            self._listeners.setdefault(event_type, []).append(listener)

    def subscribe_all(self, listener: Listener) -> None:
        with self._lock:
            self._global.append(listener)

    def unsubscribe(self, event_type: EventType, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners.get(event_type, []):
                self._listeners[event_type].remove(listener)

    def publish(self, event: Event) -> None:
        """Dispatch ``event`` to all matching listeners, newest registrations last."""
        with self._lock:
            targets = list(self._global) + list(self._listeners.get(event.type, []))
        for listener in targets:
            try:
                listener(event)
            except Exception as exc:
                if self._error_handler is not None:
                    self._error_handler(exc, event)

    def emit(
        self,
        event_type: EventType,
        message: str = "",
        **data: Any,
    ) -> Event:
        """Convenience wrapper that builds and publishes an :class:`Event`."""
        event = Event(type=event_type, message=message, data=data)
        self.publish(event)
        return event
