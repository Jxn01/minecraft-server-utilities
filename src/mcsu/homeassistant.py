"""Home Assistant integration for fleet mode, over MQTT discovery.

With ``[homeassistant] enabled = true`` in ``fleet.toml``, ``mcsu fleet run``
connects to the broker Home Assistant uses (normally the Mosquitto app) and
announces one device -- *Minecraft*, by default -- whose entities control the
whole fleet:

=====================  =========================================================
Entity                 What it does
=====================  =========================================================
Active server (select) ``Off`` + every server's title. Choosing one switches to
                       it (players get a countdown, the old world a backup);
                       ``Off`` stops the running server.
State (sensor)         stopped / starting / running / restarting (crashed, the
                       watchdog is bringing it back) / switching / stopping /
                       crashed (the watchdog gave up)
Players online         count; the ``players`` attribute lists the names
Server (sensor)        loader and Minecraft version of the active server
Online since           when the active server finished starting (timestamp)
Last backup            the newest backup archive of the active server
Memory                 the server JVM's resident memory (MiB, Linux)
Crash loop             problem sensor: on when a server crashed repeatedly and
                       was left stopped
Last action            what the last command did (e.g. a whitelist summary)
Restart / Back up now / Stop (buttons)
Player name (text)     a Java Edition name for the two buttons below
Whitelist player / Remove from whitelist (buttons) -- on EVERY server at once
=====================  =========================================================

Everything carries an availability topic with a last-will message, so the
entities read *unavailable* whenever the daemon is not running.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcsu import __version__
from mcsu.mqtt import MqttClient, Will

if TYPE_CHECKING:
    from mcsu.fleet import FleetDaemon

log = logging.getLogger("mcsu.homeassistant")

OFF = "Off"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "minecraft"


def discovery_messages(
    *, prefix: str, base: str, node: str, device_name: str, titles: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """Every discovery config as (topic, payload). Pure, so the tests can inspect it."""
    device = {
        "identifiers": [f"mcsu_{node}"],
        "name": device_name,
        "manufacturer": "mcsu",
        "model": "Minecraft server fleet",
        "sw_version": __version__,
    }
    common = {"availability_topic": f"{base}/availability", "device": device}
    state = f"{base}/state"

    def entity(component: str, key: str, name: str, **extra: Any) -> tuple[str, dict[str, Any]]:
        cfg = {
            "name": name,
            "unique_id": f"mcsu_{node}_{key}",
            "default_entity_id": f"{component}.{node}_{key}",
            **common,
            **extra,
        }
        return f"{prefix}/{component}/mcsu_{node}/{key}/config", cfg

    def value(field: str) -> str:
        # MQTT sensors read the literal payload "None" as unknown.
        return f"{{{{ value_json.{field} if value_json.{field} is not none else 'None' }}}}"

    return [
        entity(
            "select",
            "active_server",
            "Active server",
            icon="mdi:minecraft",
            options=[OFF, *titles],
            command_topic=f"{base}/cmd/active",
            state_topic=state,
            value_template="{{ value_json.active_title or '" + OFF + "' }}",
        ),
        entity(
            "sensor",
            "state",
            "State",
            icon="mdi:server",
            state_topic=state,
            value_template="{{ value_json.state }}",
        ),
        entity(
            "sensor",
            "players_online",
            "Players online",
            icon="mdi:account-group",
            state_topic=state,
            value_template="{{ value_json.players_online }}",
            state_class="measurement",
            unit_of_measurement="players",
            json_attributes_topic=state,
            json_attributes_template="{{ {'players': value_json.players} | tojson }}",
        ),
        entity(
            "sensor",
            "version",
            "Server",
            icon="mdi:package-variant",
            state_topic=state,
            value_template=value("version"),
        ),
        entity(
            "sensor",
            "started",
            "Online since",
            device_class="timestamp",
            state_topic=state,
            value_template=value("started"),
        ),
        entity(
            "sensor",
            "last_backup",
            "Last backup",
            icon="mdi:backup-restore",
            state_topic=state,
            value_template=value("last_backup"),
            entity_category="diagnostic",
        ),
        entity(
            "sensor",
            "memory",
            "Memory",
            icon="mdi:memory",
            state_topic=state,
            unit_of_measurement="MiB",
            state_class="measurement",
            value_template=value("memory_mib"),
            entity_category="diagnostic",
        ),
        entity(
            "binary_sensor",
            "crash_loop",
            "Crash loop",
            device_class="problem",
            state_topic=state,
            value_template="{{ 'ON' if value_json.crash_loop else 'OFF' }}",
        ),
        entity(
            "sensor",
            "last_action",
            "Last action",
            icon="mdi:history",
            state_topic=state,
            value_template="{{ value_json.last_action or 'None' }}",
            entity_category="diagnostic",
        ),
        entity(
            "button",
            "restart",
            "Restart",
            icon="mdi:restart",
            command_topic=f"{base}/cmd/restart",
            payload_press="PRESS",
        ),
        entity(
            "button",
            "backup",
            "Back up now",
            icon="mdi:content-save",
            command_topic=f"{base}/cmd/backup",
            payload_press="PRESS",
        ),
        entity(
            "button",
            "stop",
            "Stop",
            icon="mdi:stop",
            command_topic=f"{base}/cmd/stop",
            payload_press="PRESS",
        ),
        entity(
            "text",
            "player",
            "Player name",
            icon="mdi:account-edit",
            command_topic=f"{base}/cmd/player",
            state_topic=f"{base}/player",
            min=0,
            max=16,
            pattern="^[A-Za-z0-9_]{0,16}$",
        ),
        entity(
            "button",
            "whitelist_add",
            "Whitelist player",
            icon="mdi:account-plus",
            command_topic=f"{base}/cmd/whitelist_add",
            payload_press="PRESS",
        ),
        entity(
            "button",
            "whitelist_remove",
            "Remove from whitelist",
            icon="mdi:account-remove",
            command_topic=f"{base}/cmd/whitelist_remove",
            payload_press="PRESS",
        ),
    ]


class HomeAssistantBridge:
    """Connects a :class:`~mcsu.fleet.FleetDaemon` to Home Assistant over MQTT."""

    def __init__(self, daemon: FleetDaemon, *, client_factory: Any = MqttClient) -> None:
        cfg = daemon.fleet.homeassistant
        self.daemon = daemon
        self.cfg = cfg
        self.node = slug(daemon.fleet.settings.name)
        self.base = f"{cfg.base_topic}/{self.node}"
        self.player = ""
        self._last_doc: dict[str, Any] | None = None
        self.client = client_factory(
            cfg.broker,
            cfg.port,
            client_id=f"mcsu-{self.node}",
            username=cfg.username or None,
            password=cfg.resolved_password(),
            keepalive=cfg.keepalive,
            will=Will(f"{self.base}/availability", b"offline", qos=1, retain=True),
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        daemon.add_status_listener(self.publish_state)

    def titles(self) -> list[str]:
        return [m.title for m in self.daemon.ordered_members()]

    def start(self) -> None:
        self.client.subscribe(f"{self.base}/cmd/+", qos=1)
        self.client.start()

    def stop(self) -> None:
        self.client.publish(f"{self.base}/availability", "offline", qos=1, retain=True)
        self.client.stop()

    # -- MQTT callbacks ------------------------------------------------------ #

    def _on_connect(self, client: Any) -> None:
        for topic, payload in discovery_messages(
            prefix=self.cfg.discovery_prefix,
            base=self.base,
            node=self.node,
            device_name=self.cfg.device_name,
            titles=self.titles(),
        ):
            client.publish(topic, json.dumps(payload), qos=1, retain=True)
        client.publish(f"{self.base}/availability", "online", qos=1, retain=True)
        client.publish(f"{self.base}/player", self.player, qos=1, retain=True)
        if self._last_doc is not None:
            client.publish(f"{self.base}/state", json.dumps(self._last_doc), qos=1, retain=True)
        log.info("Home Assistant discovery published for %d servers", len(self.titles()))

    def _on_message(self, topic: str, payload: bytes) -> None:
        command = topic.rsplit("/", 1)[-1]
        text = payload.decode("utf-8", "replace").strip()
        if command == "active":
            if text == OFF:
                self.daemon.request("stop")
                return
            member = self.daemon.member_by_title(text)
            if member is None:
                log.warning("Home Assistant asked for an unknown server %r", text)
                return
            self.daemon.request("start", member.name)
        elif command == "player":
            self.player = text[:16]
            self.client.publish(f"{self.base}/player", self.player, qos=1, retain=True)
        elif command in ("restart", "backup", "stop") and text == "PRESS":
            self.daemon.request(command)
        elif command in ("whitelist_add", "whitelist_remove") and text == "PRESS":
            if self.player:
                self.daemon.request(command, self.player)
        else:
            log.debug("ignoring %s = %r", topic, text)

    # -- state --------------------------------------------------------------- #

    def publish_state(self, doc: dict[str, Any]) -> None:
        self._last_doc = doc
        self.client.publish(f"{self.base}/state", json.dumps(doc), qos=1, retain=True)
