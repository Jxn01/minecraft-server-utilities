from __future__ import annotations

import threading
import time

import pytest

from mcsu.errors import ServerError
from mcsu.server import ServerProcess, wait_for_ready


def _make(server_dir, fake_java) -> ServerProcess:
    return ServerProcess(
        server_dir=server_dir,
        jar="server.jar",
        java_path=str(fake_java),
        stop_timeout=10,
    )


def test_build_command(server_dir, fake_java):
    proc = _make(server_dir, fake_java)
    proc.min_memory = "1G"
    proc.max_memory = "2G"
    proc.extra_flags = ["-XX:+UseG1GC"]
    cmd = proc.build_command()
    assert cmd[0] == str(fake_java)
    assert "-Xms1G" in cmd and "-Xmx2G" in cmd
    assert "-XX:+UseG1GC" in cmd
    assert cmd[-2:] == ["server.jar", "nogui"]


def test_missing_jar_raises(tmp_path, fake_java):
    proc = ServerProcess(server_dir=tmp_path, jar="nope.jar", java_path=str(fake_java))
    with pytest.raises(ServerError, match="server jar not found"):
        proc.start()


def test_lifecycle_start_ready_stop(server_dir, fake_java):
    proc = _make(server_dir, fake_java)
    lines: list[str] = []
    ready = threading.Event()
    proc.add_line_callback(lines.append)
    proc.add_line_callback(lambda ln: ready.set() if "Done (" in ln else None)

    proc.start()
    assert proc.is_running()
    assert wait_for_ready(proc, ready_event=ready, timeout=10)
    assert any("Done (" in ln for ln in lines)

    code = proc.stop()
    assert code == 0
    assert not proc.is_running()


def test_send_command_roundtrip(server_dir, fake_java):
    proc = _make(server_dir, fake_java)
    lines: list[str] = []
    proc.add_line_callback(lines.append)
    proc.start()
    time.sleep(0.5)
    proc.send("hello")
    time.sleep(0.5)
    proc.stop()
    assert any("Unknown command: hello" in ln for ln in lines)


def test_double_start_raises(server_dir, fake_java):
    proc = _make(server_dir, fake_java)
    proc.start()
    try:
        with pytest.raises(ServerError, match="already running"):
            proc.start()
    finally:
        proc.stop()


def test_send_when_not_running_raises(server_dir, fake_java):
    proc = _make(server_dir, fake_java)
    with pytest.raises(ServerError, match="not running"):
        proc.send("list")
