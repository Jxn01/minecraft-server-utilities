"""A small, dependency-free MQTT 3.1.1 client.

Just what a long-running daemon needs to talk to a broker such as Mosquitto
(which is what Home Assistant uses): CONNECT with credentials and a last-will
message, PUBLISH at QoS 0/1 (optionally retained), SUBSCRIBE, keepalive pings,
and automatic reconnection with exponential backoff that restores every
subscription. Built on :mod:`socket` and :mod:`threading` only, like the RCON
client next door.

Wire format references are the section numbers of the OASIS MQTT 3.1.1 spec.
Not implemented (not needed here): QoS 2, TLS, MQTT 5, persistent sessions --
connections are always clean sessions, and a QoS-1 publish is sent once
without waiting for its PUBACK.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("mcsu.mqtt")

# Control packet types (spec 2.2.1)
CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 12, 13, 14

CONNACK_REASONS = {
    1: "unacceptable protocol version",
    2: "client identifier rejected",
    3: "server unavailable",
    4: "bad user name or password",
    5: "not authorized",
}


class MqttError(Exception):
    """Raised for protocol or connection failures."""


@dataclass(frozen=True, slots=True)
class Will:
    """The message the broker publishes on our behalf if we vanish (spec 3.1.2.5)."""

    topic: str
    payload: bytes
    qos: int = 1
    retain: bool = True


# --------------------------------------------------------------------------- #
# Encoding (pure functions -- unit tested against the spec's byte layouts)
# --------------------------------------------------------------------------- #


def encode_remaining_length(length: int) -> bytes:
    """Variable-length integer, 7 bits per byte, low group first (spec 2.2.3)."""
    if not 0 <= length <= 268_435_455:
        raise MqttError(f"remaining length out of range: {length}")
    out = bytearray()
    while True:
        byte, length = length % 128, length // 128
        out.append(byte | 0x80 if length else byte)
        if not length:
            return bytes(out)


def encode_string(value: str | bytes) -> bytes:
    """UTF-8 string or binary blob prefixed with its 2-byte big-endian length (spec 1.5.3)."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    if len(data) > 0xFFFF:
        raise MqttError("string longer than 65535 bytes")
    return struct.pack("!H", len(data)) + data


def packet(ptype: int, flags: int, body: bytes) -> bytes:
    return bytes([(ptype << 4) | flags]) + encode_remaining_length(len(body)) + body


def connect_packet(
    client_id: str,
    *,
    keepalive: int,
    username: str | None = None,
    password: str | None = None,
    will: Will | None = None,
) -> bytes:
    flags = 0x02  # clean session
    payload = encode_string(client_id)
    if will is not None:
        flags |= 0x04 | (will.qos << 3) | (0x20 if will.retain else 0)
        payload += encode_string(will.topic) + encode_string(will.payload)
    if username is not None:
        flags |= 0x80
        payload += encode_string(username)
        if password is not None:
            flags |= 0x40
            payload += encode_string(password)
    variable = encode_string("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
    return packet(CONNECT, 0, variable + payload)


def publish_packet(
    topic: str, payload: bytes, *, qos: int = 0, retain: bool = False, packet_id: int = 0
) -> bytes:
    if qos not in (0, 1):
        raise MqttError("only QoS 0 and 1 are supported")
    flags = (qos << 1) | (1 if retain else 0)
    body = encode_string(topic)
    if qos:
        body += struct.pack("!H", packet_id)
    return packet(PUBLISH, flags, body + payload)


def subscribe_packet(packet_id: int, topics: list[tuple[str, int]]) -> bytes:
    body = struct.pack("!H", packet_id)
    for topic, qos in topics:
        body += encode_string(topic) + bytes([qos])
    return packet(SUBSCRIBE, 0x02, body)  # fixed-header flags MUST be 0010 (spec 3.8.1)


def decode_publish(flags: int, body: bytes) -> tuple[str, bytes, int, int]:
    """-> (topic, payload, qos, packet_id) of an incoming PUBLISH (spec 3.3)."""
    if len(body) < 2:
        raise MqttError("truncated PUBLISH")
    (tlen,) = struct.unpack("!H", body[:2])
    topic = body[2 : 2 + tlen].decode("utf-8")
    rest = body[2 + tlen :]
    qos = (flags >> 1) & 0x03
    packet_id = 0
    if qos:
        (packet_id,) = struct.unpack("!H", rest[:2])
        rest = rest[2:]
    return topic, rest, qos, packet_id


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard matching: `+` one level, `#` the rest (spec 4.7)."""
    p_parts, t_parts = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p_parts):
        if part == "#":
            return True
        if i >= len(t_parts) or (part != "+" and part != t_parts[i]):
            return False
    return len(p_parts) == len(t_parts)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

MessageHandler = Callable[[str, bytes], None]
ConnectHandler = Callable[["MqttClient"], None]


class MqttClient:
    """A self-reconnecting MQTT client running its network loop on one thread.

    ``on_connect`` runs on the network thread after every (re)connection --
    publish discovery and state there. ``on_message(topic, payload)`` runs on
    the same thread for every message matching a subscription; keep it quick.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        keepalive: int = 30,
        will: Will | None = None,
        connect_timeout: float = 10.0,
        reconnect_min: float = 1.0,
        reconnect_max: float = 60.0,
    ) -> None:
        self.host, self.port = host, port
        self.client_id = client_id
        self.username, self.password = username, password
        self.keepalive = keepalive
        self.will = will
        self.connect_timeout = connect_timeout
        self.reconnect_min, self.reconnect_max = reconnect_min, reconnect_max
        self.on_connect: ConnectHandler | None = None
        self.on_message: MessageHandler | None = None

        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._subscriptions: dict[str, int] = {}
        self._next_id = 0
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_send = 0.0
        self.last_error: str = ""

    # -- public API ---------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mcsu-mqtt", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Disconnect cleanly (the broker then does NOT publish the will) and stop."""
        self._stop.set()
        if self._connected.is_set():
            try:
                self._send(packet(DISCONNECT, 0, b""))
            except OSError:
                pass
        self._close()
        if self._thread:
            self._thread.join(timeout)

    def wait_connected(self, timeout: float) -> bool:
        return self._connected.wait(timeout)

    def publish(
        self, topic: str, payload: bytes | str, *, qos: int = 0, retain: bool = False
    ) -> bool:
        """Send now if connected; returns False (message dropped) otherwise."""
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        if not self._connected.is_set():
            return False
        try:
            self._send(
                publish_packet(topic, data, qos=qos, retain=retain, packet_id=self._packet_id())
            )
            return True
        except OSError as exc:
            log.debug("publish to %s failed: %s", topic, exc)
            self._close()
            return False

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe now (if connected) and again after every reconnection."""
        self._subscriptions[topic] = qos
        if self._connected.is_set():
            try:
                self._send(subscribe_packet(self._packet_id(), [(topic, qos)]))
            except OSError:
                self._close()

    # -- internals ----------------------------------------------------------- #

    def _packet_id(self) -> int:
        self._next_id = self._next_id % 0xFFFF + 1
        return self._next_id

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise OSError("not connected")
        with self._send_lock:
            sock.sendall(data)
            self._last_send = time.monotonic()

    def _close(self) -> None:
        self._connected.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return buf

    def _read_packet(self, sock: socket.socket) -> tuple[int, int, bytes]:
        # Only this first read may time out (the loop's short idle timeout, so it can send
        # keepalives): nothing has been consumed yet. Once a packet has begun, the rest is read
        # with the full timeout -- timing out half-way would desynchronise the stream for good.
        header = self._recv_exact(sock, 1)[0]
        idle = sock.gettimeout()
        sock.settimeout(self.connect_timeout)
        try:
            multiplier, length = 1, 0
            for _ in range(4):
                byte = self._recv_exact(sock, 1)[0]
                length += (byte & 0x7F) * multiplier
                if not byte & 0x80:
                    break
                multiplier *= 128
            else:
                raise MqttError("malformed remaining length")
            body = self._recv_exact(sock, length) if length else b""
        finally:
            sock.settimeout(idle)
        return header >> 4, header & 0x0F, body

    def _connect_once(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        try:
            sock.sendall(
                connect_packet(
                    self.client_id,
                    keepalive=self.keepalive,
                    username=self.username,
                    password=self.password,
                    will=self.will,
                )
            )
            ptype, _flags, body = self._read_packet(sock)
            if ptype != CONNACK or len(body) < 2:
                raise MqttError(f"expected CONNACK, got packet type {ptype}")
            if body[1] != 0:
                raise MqttError(f"connection refused: {CONNACK_REASONS.get(body[1], body[1])}")
        except BaseException:
            sock.close()
            raise
        # A short read timeout lets the loop wake up to send keepalive pings.
        sock.settimeout(1.0)
        return sock

    def _run(self) -> None:
        delay = self.reconnect_min
        while not self._stop.is_set():
            try:
                self._sock = self._connect_once()
            except (OSError, MqttError) as exc:
                self.last_error = str(exc)
                log.warning("MQTT connect to %s:%s failed: %s", self.host, self.port, exc)
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, self.reconnect_max)
                continue
            delay = self.reconnect_min
            self.last_error = ""
            self._last_send = time.monotonic()
            self._connected.set()
            log.info("MQTT connected to %s:%s", self.host, self.port)
            try:
                if self._subscriptions:
                    self._send(
                        subscribe_packet(self._packet_id(), list(self._subscriptions.items()))
                    )
                if self.on_connect:
                    self.on_connect(self)
                self._loop()
            except (OSError, MqttError, ConnectionError) as exc:
                if not self._stop.is_set():
                    self.last_error = str(exc)
                    log.warning("MQTT connection lost: %s", exc)
            finally:
                self._close()
            if not self._stop.is_set():
                self._stop.wait(delay)

    def _loop(self) -> None:
        sock = self._sock
        assert sock is not None
        awaiting_pong_since: float | None = None
        while not self._stop.is_set():
            now = time.monotonic()
            if self.keepalive and now - self._last_send >= self.keepalive / 2:
                self._send(packet(PINGREQ, 0, b""))
                awaiting_pong_since = awaiting_pong_since or now
            if awaiting_pong_since and now - awaiting_pong_since > self.keepalive:
                raise MqttError("no PINGRESP within the keepalive interval")
            try:
                ptype, flags, body = self._read_packet(sock)
            except TimeoutError:
                continue
            if ptype == PINGRESP:
                awaiting_pong_since = None
            elif ptype == PUBLISH:
                topic, payload, qos, packet_id = decode_publish(flags, body)
                if qos == 1:
                    self._send(packet(PUBACK, 0, struct.pack("!H", packet_id)))
                if self.on_message and any(topic_matches(p, topic) for p in self._subscriptions):
                    try:
                        self.on_message(topic, payload)
                    except Exception:
                        log.exception("MQTT message handler failed for %s", topic)
            # CONNACK, PUBACK and SUBACK need no action here.
