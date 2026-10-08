"""The Home Assistant bridge: discovery payloads and command routing (no broker needed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mcsu.fleet import FleetDaemon, load_fleet
from mcsu.homeassistant import OFF, HomeAssistantBridge, discovery_messages, slug


class FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscribed: list[str] = []
        self.on_connect = None
        self.on_message = None

    def publish(self, topic: str, payload: Any, *, qos: int = 0, retain: bool = False) -> bool:
        self.published.append(
            (topic, payload if isinstance(payload, str) else payload.decode(), qos, retain)
        )
        return True

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture
def daemon(tmp_path: Path, fake_java: Path) -> FleetDaemon:
    root = tmp_path / "fleet"
    for name, title in (("alpha", "Alpha Pack"), ("beta", "Beta World")):
        d = root / name
        d.mkdir(parents=True)
        (d / "mcsu.toml").write_text(
            f'[server]\nname = "{title}"\nloader = "forge"\nmc_version = "1.18.2"\n'
        )
    secret = tmp_path / "mqtt.secret"
    secret.write_text("s3cret\n")
    (root / "fleet.toml").write_text(
        '[fleet]\nname = "JXN Server"\n'
        f'[homeassistant]\nenabled = true\nbroker = "127.0.0.1"\nusername = "minecraft"\n'
        f'password_file = "{str(secret).replace(chr(92), chr(92) * 2)}"\n'
    )
    return FleetDaemon(
        load_fleet(root), bridge_factory=lambda d: HomeAssistantBridge(d, client_factory=FakeClient)
    )


def messages() -> list[tuple[str, dict[str, Any]]]:
    return discovery_messages(
        prefix="homeassistant",
        base="mcsu/x",
        node="x",
        device_name="Minecraft",
        titles=["Alpha Pack", "Beta World"],
    )


def test_slug() -> None:
    assert slug("JXN Server") == "jxn_server"
    assert slug("!!!") == "minecraft"


def test_discovery_payloads_are_unique_and_complete() -> None:
    msgs = messages()
    ids = [cfg["unique_id"] for _, cfg in msgs]
    assert len(ids) == len(set(ids))
    for topic, cfg in msgs:
        assert topic.startswith("homeassistant/") and topic.endswith("/config")
        assert cfg["availability_topic"] == "mcsu/x/availability"
        assert cfg["device"]["identifiers"] == ["mcsu_x"]
    select = next(cfg for _, cfg in msgs if cfg["unique_id"] == "mcsu_x_active_server")
    assert select["options"] == [OFF, "Alpha Pack", "Beta World"]
    assert select["command_topic"] == "mcsu/x/cmd/active"


def test_every_command_topic_is_one_the_bridge_handles() -> None:
    handled = {"active", "player", "restart", "backup", "stop", "whitelist_add", "whitelist_remove"}
    for _, cfg in messages():
        if "command_topic" in cfg:
            assert cfg["command_topic"].rsplit("/", 1)[1] in handled, cfg["unique_id"]


def test_bridge_reads_the_password_file_and_sets_a_will(daemon: FleetDaemon) -> None:
    client = daemon.bridge.client
    assert client.kwargs["password"] == "s3cret"
    assert client.kwargs["username"] == "minecraft"
    assert client.kwargs["will"].topic == "mcsu/jxn_server/availability"


def test_on_connect_publishes_retained_discovery_and_online(daemon: FleetDaemon) -> None:
    bridge = daemon.bridge
    bridge._on_connect(bridge.client)
    published = {t: (p, r) for t, p, _q, r in bridge.client.published}
    assert published["mcsu/jxn_server/availability"] == ("online", True)
    configs = [t for t in published if t.endswith("/config")]
    assert len(configs) == len(messages())
    assert all(published[t][1] for t in configs), "discovery must be retained"


def test_commands_are_routed_to_the_daemon(daemon: FleetDaemon) -> None:
    bridge = daemon.bridge
    queued: list[tuple[str, str | None]] = []
    daemon.request = lambda c, a=None: queued.append((c, a))  # type: ignore[method-assign]
    bridge._on_message("mcsu/jxn_server/cmd/active", b"Beta World")
    bridge._on_message("mcsu/jxn_server/cmd/active", OFF.encode())
    bridge._on_message("mcsu/jxn_server/cmd/active", b"No Such Server")
    bridge._on_message("mcsu/jxn_server/cmd/restart", b"PRESS")
    bridge._on_message("mcsu/jxn_server/cmd/backup", b"press")  # wrong payload: ignored
    bridge._on_message("mcsu/jxn_server/cmd/whitelist_add", b"PRESS")  # no player yet: ignored
    bridge._on_message("mcsu/jxn_server/cmd/player", b"Steve")
    bridge._on_message("mcsu/jxn_server/cmd/whitelist_add", b"PRESS")
    bridge._on_message("mcsu/jxn_server/cmd/whitelist_remove", b"PRESS")
    assert queued == [
        ("start", "beta"),
        ("stop", None),
        ("restart", None),
        ("whitelist_add", "Steve"),
        ("whitelist_remove", "Steve"),
    ]
    assert ("mcsu/jxn_server/player", "Steve", 1, True) in bridge.client.published, (
        "text state echoed"
    )


def test_state_is_published_retained_as_json(daemon: FleetDaemon) -> None:
    daemon._publish_status(force=True)  # the daemon's listeners include the bridge
    state = [
        p for t, p, _q, r in daemon.bridge.client.published if t == "mcsu/jxn_server/state" and r
    ]
    assert state and json.loads(state[-1])["fleet"] == "JXN Server"
