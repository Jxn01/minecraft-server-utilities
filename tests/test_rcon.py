"""Tests for the RCON client, driven by an in-process mock RCON server.

The mock implements just enough of the Source RCON protocol — auth, command
echo, and the trailing-empty-packet end-of-response marker — to exercise the
client end to end over a real localhost socket.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from mcsu.errors import RconError
from mcsu.rcon import RconClient, send_command

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_RESPONSE_VALUE = 0
SERVERDATA_EXECCOMMAND = 2


class MockRconServer:
    def __init__(self, password: str = "secret", *, split_long: bool = False) -> None:
        self.password = password
        self.split_long = split_long
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> MockRconServer:
        self._thread.start()
        return self

    def _recv_packet(self, conn):
        raw_len = self._recv_exact(conn, 4)
        if raw_len is None:
            return None
        length = struct.unpack("<i", raw_len)[0]
        data = self._recv_exact(conn, length)
        pid, ptype = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8", "replace")
        return pid, ptype, body

    @staticmethod
    def _recv_exact(conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    @staticmethod
    def _send(conn, pid, ptype, body):
        payload = body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<ii", pid, ptype) + payload
        conn.sendall(struct.pack("<i", len(packet)) + packet)

    def _serve(self):
        conn, _ = self._sock.accept()
        with conn:
            # Authentication handshake.
            packet = self._recv_packet(conn)
            if packet is None:
                return
            pid, ptype, body = packet
            if ptype == SERVERDATA_AUTH:
                ok_id = pid if body == self.password else -1
                self._send(conn, ok_id, SERVERDATA_AUTH_RESPONSE, "")
                if ok_id == -1:
                    return
            # Command loop.
            while True:
                packet = self._recv_packet(conn)
                if packet is None:
                    break
                pid, ptype, body = packet
                if ptype == SERVERDATA_EXECCOMMAND:
                    if body == "longcmd" and self.split_long:
                        self._send(conn, pid, SERVERDATA_RESPONSE_VALUE, "part1-")
                        self._send(conn, pid, SERVERDATA_RESPONSE_VALUE, "part2")
                    else:
                        self._send(conn, pid, SERVERDATA_RESPONSE_VALUE, f"echo:{body}")
                else:
                    # The marker packet — echo it back to signal end-of-response.
                    self._send(conn, pid, SERVERDATA_RESPONSE_VALUE, "")

    def stop(self):
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def rcon_server():
    server = MockRconServer().start()
    yield server
    server.stop()


def test_auth_and_command(rcon_server):
    with RconClient("127.0.0.1", rcon_server.port, "secret") as client:
        assert client.command("list") == "echo:list"


def test_one_shot_helper(rcon_server):
    out = send_command("seed", host="127.0.0.1", port=rcon_server.port, password="secret")
    assert out == "echo:seed"


def test_bad_password():
    server = MockRconServer(password="right").start()
    try:
        with pytest.raises(RconError, match="authentication failed"):
            RconClient("127.0.0.1", server.port, "wrong").connect()
    finally:
        server.stop()


def test_multi_packet_response():
    server = MockRconServer(split_long=True).start()
    try:
        with RconClient("127.0.0.1", server.port, "secret") as client:
            assert client.command("longcmd") == "part1-part2"
    finally:
        server.stop()


def test_connection_refused():
    # Port 1 is virtually never an open RCON endpoint.
    with pytest.raises(RconError, match="could not connect"):
        RconClient("127.0.0.1", 1, "x", timeout=1.0).connect()


def test_command_without_connect():
    client = RconClient("127.0.0.1", 25575, "x")
    with pytest.raises(RconError, match="not connected"):
        client.command("list")
