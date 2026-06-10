"""Outbound notifications, wired to the event bus.

Currently ships a Discord webhook notifier (no third-party dependency — it
posts JSON via :mod:`urllib`). The :class:`Notifier` base class makes adding
Slack, ntfy, e-mail, or a custom endpoint a matter of one subclass.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterable

from mcsu.events import Event, EventBus, EventType

# Color accents (Discord embeds use a decimal RGB int) keyed by event type.
_EVENT_COLORS = {
    EventType.SERVER_READY: 0x2ECC71,
    EventType.SERVER_STARTING: 0x3498DB,
    EventType.SERVER_STOPPED: 0x95A5A6,
    EventType.SERVER_STOPPING: 0x95A5A6,
    EventType.SERVER_CRASHED: 0xE74C3C,
    EventType.BACKUP_COMPLETED: 0x1ABC9C,
    EventType.BACKUP_FAILED: 0xE74C3C,
    EventType.PLAYER_JOIN: 0x2ECC71,
    EventType.PLAYER_LEAVE: 0xE67E22,
    EventType.PLAYER_CHAT: 0x7289DA,
}
_DEFAULT_COLOR = 0x7289DA


class Notifier:
    """Base class: subclasses implement :meth:`send`."""

    def send(self, title: str, message: str, *, event: Event | None = None) -> None:
        raise NotImplementedError


class DiscordNotifier(Notifier):
    """Posts compact embeds to a Discord webhook URL."""

    def __init__(self, webhook_url: str, *, username: str = "mcsu", timeout: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.username = username
        self.timeout = timeout

    def send(self, title: str, message: str, *, event: Event | None = None) -> None:
        color = _EVENT_COLORS.get(event.type, _DEFAULT_COLOR) if event else _DEFAULT_COLOR
        payload = {
            "username": self.username,
            "embeds": [
                {
                    "title": title[:256],
                    "description": message[:4000],
                    "color": color,
                }
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "mcsu"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except (urllib.error.URLError, OSError):
            # Notifications are best-effort; never let them break the server loop.
            pass


class NotificationService:
    """Bridges the :class:`EventBus` to one or more :class:`Notifier` backends.

    Sends run on a background thread so a slow webhook can't stall the control
    loop, and only the configured subset of event types is forwarded.
    """

    def __init__(
        self,
        notifiers: Iterable[Notifier],
        *,
        event_types: Iterable[str | EventType],
    ) -> None:
        self._notifiers = list(notifiers)
        self._types = {EventType(e) for e in event_types}

    def attach(self, bus: EventBus) -> None:
        bus.subscribe_all(self._on_event)

    def _on_event(self, event: Event) -> None:
        if event.type not in self._types or not self._notifiers:
            return
        title = _title_for(event)
        message = event.message or ""
        threading.Thread(target=self._dispatch, args=(title, message, event), daemon=True).start()

    def _dispatch(self, title: str, message: str, event: Event) -> None:
        for notifier in self._notifiers:
            notifier.send(title, message, event=event)


_TITLES = {
    EventType.SERVER_READY: "✅ Server is online",
    EventType.SERVER_STARTING: "🚀 Server starting",
    EventType.SERVER_STOPPING: "🔻 Server stopping",
    EventType.SERVER_STOPPED: "🛑 Server stopped",
    EventType.SERVER_CRASHED: "💥 Server crashed",
    EventType.SERVER_RESTART_SCHEDULED: "🔁 Restart scheduled",
    EventType.BACKUP_COMPLETED: "💾 Backup completed",
    EventType.BACKUP_FAILED: "⚠️ Backup failed",
    EventType.BACKUP_SKIPPED: "⏭️ Backup skipped",
    EventType.PLAYER_JOIN: "🟢 Player joined",
    EventType.PLAYER_LEAVE: "🔴 Player left",
    EventType.PLAYER_CHAT: "💬 Chat",
    EventType.SERVER_ERROR: "❗ Server error",
}


def _title_for(event: Event) -> str:
    return _TITLES.get(event.type, event.type.value.replace("_", " ").title())
