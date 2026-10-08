"""The MQTT 3.1.1 client: byte-exact encoders, and the real client against an in-process broker."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from mcsu import mqtt
from mcsu.mqtt import MqttClient, Will

# --------------------------------------------------------------------------- #
# Encoders
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("length", "encoded"),
    [
        (0, b"\x00"),
        (127, b"\x7f"),
        (128, b"\x80\x01"),
        (16_383, b"\xff\x7f"),
        (16_384, b"\x80\x80\x01"),
        (268_435_455, b"\xff\xff\xff\x7f"),  # the spec's maximum (2.2.3)
    ],
)
def test_remaining_length(length: int, encoded: bytes) -> None:
    assert mqtt.encode_remaining_length(length) == encoded


def test_remaining_length_out_of_range() -> None:
    with pytest.raises(mqtt.MqttError):
        mqtt.encode_remaining_length(268_435_456)


def test_connect_packet_minimal() -> None:
    # 10 | len | "MQTT" | level 4 | flags 0x02 (clean session) | keepalive 60 | client id "c"
    assert mqtt.connect_packet("c", keepalive=60) == (
        b"\x10\x0d\x00\x04MQTT\x04\x02\x00\x3c\x00\x01c"
    )


def test_connect_packet_with_credentials_and_will() -> None:
    raw = mqtt.connect_packet(
        "id",
        keepalive=30,
        username="u",
        password="p",
        will=Will("t/a", b"offline", qos=1, retain=True),
    )
    flags = raw[2 + 6 + 1]  # after fixed header (2 bytes), protocol name (6), level (1)
    assert flags == 0x80 | 0x40 | 0x20 | (1 << 3) | 0x04 | 0x02
    assert raw.endswith(b"\x00\x03t/a\x00\x07offline\x00\x01u\x00\x01p")


def test_password_without_username_is_not_sent() -> None:
    raw = mqtt.connect_packet("id", keepalive=30, password="p")
    assert raw[2 + 6 + 1] == 0x02


def test_publish_packet_retained_qos0() -> None:
    assert mqtt.publish_packet("a/b", b"x", retain=True) == b"\x31\x06\x00\x03a/bx"


def test_publish_packet_qos1_carries_a_packet_id() -> None:
    assert mqtt.publish_packet("a", b"", qos=1, packet_id=7) == b"\x32\x05\x00\x01a\x00\x07"


def test_publish_rejects_qos2() -> None:
    with pytest.raises(mqtt.MqttError):
        mqtt.publish_packet("a", b"", qos=2)


def test_subscribe_packet_uses_the_mandatory_flags() -> None:
    assert mqtt.subscribe_packet(1, [("a/#", 1)]) == b"\x82\x08\x00\x01\x00\x03a/#\x01"


def test_decode_publish_roundtrip() -> None:
    raw = mqtt.publish_packet("x/y", b"hello", qos=1, packet_id=42)
    assert mqtt.decode_publish(raw[0] & 0x0F, raw[2:]) == ("x/y", b"hello", 1, 42)


@pytest.mark.parametrize(
    ("pattern", "topic", "match"),
    [
        ("a/b", "a/b", True),
        ("a/+", "a/b", True),
        ("a/+", "a/b/c", False),
        ("a/#", "a/b/c", True),
        ("a/#", "a", True),
        ("+/b", "x/b", True),
        ("a/b", "a/c", False),
    ],
)
def test_topic_matches(pattern: str, topic: str, match: bool) -> None:
    assert mqtt.topic_matches(pattern, topic) is match


# --------------------------------------------------------------------------- #
# An in-process broker, just enough to exercise the real client
# --------------------------------------------------------------------------- #


class FakeBroker:
    def __init__(self, *, refuse: int = 0) -> None:
        self.refuse = refuse
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self.packets: list[tuple[int, int, bytes]] = []
        self.connections: list[socket.socket] = []
        self.connects = 0
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _read(self, conn: socket.socket) -> tuple[int, int, bytes] | None:
        def exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError
                buf += chunk
            return buf

        try:
            header = exact(1)[0]
            mult, length = 1, 0
            while True:
                b = exact(1)[0]
                length += (b & 0x7F) * mult
                if not b & 0x80:
                    break
                mult *= 128
            return header >> 4, header & 0x0F, exact(length) if length else b""
        except (ConnectionError, OSError):
            return None

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        pkt = self._read(conn)
        if pkt is None or pkt[0] != mqtt.CONNECT:
            return
        self.connects += 1
        self.packets.append(pkt)
        conn.sendall(bytes([0x20, 0x02, 0x00, self.refuse]))
        if self.refuse:
            conn.close()
            return
        while (pkt := self._read(conn)) is not None:
            self.packets.append(pkt)
            if pkt[0] == mqtt.PINGREQ:
                conn.sendall(b"\xd0\x00")

    def send_to_clients(self, data: bytes) -> None:
        for conn in self.connections:
            try:
                conn.sendall(data)
            except OSError:
                pass

    def drop_clients(self) -> None:
        for conn in self.connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except OSError:
                pass
        self.connections.clear()

    def of_type(self, ptype: int) -> list[tuple[int, int, bytes]]:
        return [p for p in self.packets if p[0] == ptype]

    def close(self) -> None:
        self._stop = True
        self.drop_clients()
        self.server.close()


def wait_for(cond, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def broker():  # type: ignore[no-untyped-def]
    b = FakeBroker()
    yield b
    b.close()


def make_client(port: int, **kw) -> MqttClient:  # type: ignore[no-untyped-def]
    return MqttClient(
        "127.0.0.1", port, client_id="test", reconnect_min=0.05, reconnect_max=0.2, **kw
    )


def test_connects_runs_on_connect_and_publishes(broker: FakeBroker) -> None:
    client = make_client(broker.port, username="u", password="p", will=Will("s/avail", b"offline"))
    client.on_connect = lambda c: c.publish("s/avail", "online", qos=1, retain=True)
    client.start()
    try:
        assert client.wait_connected(5)
        assert wait_for(lambda: broker.of_type(mqtt.PUBLISH))
        flags, body = broker.of_type(mqtt.PUBLISH)[0][1:]
        topic, payload, qos, _ = mqtt.decode_publish(flags, body)
        assert (topic, payload, qos, flags & 1) == ("s/avail", b"online", 1, 1)
    finally:
        client.stop()
    assert wait_for(lambda: broker.of_type(mqtt.DISCONNECT)), (
        "a clean stop must send DISCONNECT (no will)"
    )


def test_receives_subscribed_messages_and_acks_qos1(broker: FakeBroker) -> None:
    got: list[tuple[str, bytes]] = []
    client = make_client(broker.port)
    client.on_message = lambda t, p: got.append((t, p))
    client.subscribe("cmd/#", qos=1)
    client.start()
    try:
        assert client.wait_connected(5)
        assert wait_for(lambda: broker.of_type(mqtt.SUBSCRIBE))
        broker.send_to_clients(mqtt.publish_packet("cmd/start", b"vanilla", qos=1, packet_id=9))
        broker.send_to_clients(mqtt.publish_packet("other/x", b"ignored"))
        assert wait_for(lambda: got)
        assert got == [("cmd/start", b"vanilla")]
        assert wait_for(lambda: broker.of_type(mqtt.PUBACK))
        assert struct.unpack("!H", broker.of_type(mqtt.PUBACK)[0][2])[0] == 9
    finally:
        client.stop()


def test_reconnects_and_resubscribes(broker: FakeBroker) -> None:
    connects: list[int] = []
    client = make_client(broker.port)
    client.on_connect = lambda c: connects.append(1)
    client.subscribe("cmd/#")
    client.start()
    try:
        assert client.wait_connected(5)
        assert wait_for(lambda: broker.of_type(mqtt.SUBSCRIBE)), (
            "first subscription seen before the drop"
        )
        broker.drop_clients()
        assert wait_for(lambda: len(connects) >= 2, 10), "the client must come back by itself"
        assert wait_for(lambda: len(broker.of_type(mqtt.SUBSCRIBE)) >= 2)
    finally:
        client.stop()


def test_publish_while_disconnected_is_dropped_not_raised() -> None:
    client = make_client(1)  # nothing listens on port 1
    assert client.publish("a", "b") is False


def test_refused_credentials_are_reported() -> None:
    b = FakeBroker(refuse=4)
    client = make_client(b.port, username="u", password="wrong")
    client.start()
    try:
        assert wait_for(lambda: "bad user name or password" in client.last_error)
        assert not client.connected
    finally:
        client.stop()
        b.close()


def test_pings_keep_the_connection_alive(broker: FakeBroker) -> None:
    client = make_client(broker.port, keepalive=1)
    client.start()
    try:
        assert client.wait_connected(5)
        assert wait_for(lambda: broker.of_type(mqtt.PINGREQ), 4)
        assert client.connected
    finally:
        client.stop()
