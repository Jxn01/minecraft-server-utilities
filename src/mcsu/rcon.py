"""A pure-stdlib client for the Source RCON protocol.

Minecraft (Java Edition) exposes the same RCON protocol used by Source engine
games: length-prefixed little-endian packets over TCP. This implementation
handles authentication and multi-packet responses (the server may split long
replies across several ``RESPONSE_VALUE`` packets) using the well-known
"trailing empty packet" technique to detect the end of a response.

Reference: https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
"""

from __future__ import annotations

import socket
import struct
from types import TracebackType

from mcsu.errors import RconError

# Packet types
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

# A sentinel id used to detect the end of a (possibly multi-packet) response.
_MARKER_ID = 0x7FFFFFF0
_MAX_PACKET = 4096 + 16


class RconClient:
    """A synchronous RCON client usable directly or as a context manager.

    Example::

        with RconClient("127.0.0.1", 25575, "secret") as rcon:
            print(rcon.command("list"))
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 25575,
        password: str = "",
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 1

    # -- connection management -------------------------------------------- #

    def connect(self) -> None:
        """Open the socket and authenticate. Raises :class:`RconError`."""
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._sock.settimeout(self.timeout)
        except OSError as exc:
            self._sock = None
            raise RconError(f"could not connect to RCON at {self.host}:{self.port}: {exc}") from exc
        self._authenticate()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> RconClient:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- public API -------------------------------------------------------- #

    def command(self, cmd: str) -> str:
        """Run a server command and return the (decoded) console response."""
        if self._sock is None:
            raise RconError("not connected; call connect() first")
        request_id = self._allocate_id()
        self._send(request_id, SERVERDATA_EXECCOMMAND, cmd)
        # Immediately send a second, empty packet with a known marker id. The
        # server echoes it back *after* the full (possibly multi-part) reply,
        # giving us a reliable end-of-response signal.
        self._send(_MARKER_ID, SERVERDATA_RESPONSE_VALUE, "")

        chunks: list[str] = []
        while True:
            resp_id, _resp_type, body = self._receive()
            if resp_id == _MARKER_ID:
                break
            chunks.append(body)
        return "".join(chunks)

    # -- internals --------------------------------------------------------- #

    def _allocate_id(self) -> int:
        self._next_id += 1
        if self._next_id >= _MARKER_ID:
            self._next_id = 1
        return self._next_id

    def _authenticate(self) -> None:
        auth_id = self._allocate_id()
        self._send(auth_id, SERVERDATA_AUTH, self.password)
        # The server may first send an empty RESPONSE_VALUE, then the auth
        # response. Loop until we see an AUTH_RESPONSE packet.
        while True:
            resp_id, resp_type, _body = self._receive()
            if resp_type == SERVERDATA_AUTH_RESPONSE:
                if resp_id == -1:
                    raise RconError("RCON authentication failed (wrong password?)")
                return
            if resp_type != SERVERDATA_RESPONSE_VALUE:
                raise RconError(f"unexpected packet type during auth: {resp_type}")

    def _send(self, packet_id: int, packet_type: int, body: str) -> None:
        assert self._sock is not None
        payload = body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<ii", packet_id, packet_type) + payload
        framed = struct.pack("<i", len(packet)) + packet
        try:
            self._sock.sendall(framed)
        except OSError as exc:
            raise RconError(f"failed to send RCON packet: {exc}") from exc

    def _receive(self) -> tuple[int, int, str]:
        length = struct.unpack("<i", self._recv_exact(4))[0]
        if length < 10 or length > _MAX_PACKET:
            raise RconError(f"invalid RCON packet length: {length}")
        data = self._recv_exact(length)
        packet_id, packet_type = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8", errors="replace")
        return packet_id, packet_type, body

    def _recv_exact(self, count: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < count:
            try:
                chunk = self._sock.recv(count - len(buf))
            except TimeoutError as exc:
                raise RconError("timed out waiting for RCON response") from exc
            except OSError as exc:
                raise RconError(f"RCON socket error: {exc}") from exc
            if not chunk:
                raise RconError("RCON connection closed by server")
            buf.extend(chunk)
        return bytes(buf)


def send_command(
    cmd: str,
    *,
    host: str = "127.0.0.1",
    port: int = 25575,
    password: str = "",
    timeout: float = 5.0,
) -> str:
    """One-shot helper: connect, run a single command, disconnect."""
    with RconClient(host, port, password, timeout) as client:
        return client.command(cmd)
