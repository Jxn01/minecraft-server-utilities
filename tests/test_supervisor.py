"""Integration tests for the Supervisor using the fake-java fixture.

These exercise the real control loop: starting the server, detecting
readiness, emitting events, taking a backup, and shutting down — all without
Java or a network.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

from mcsu.config import config_from_dict
from mcsu.events import EventType
from mcsu.supervisor import Supervisor


def _config(server_dir, fake_java, **overrides):
    raw = {
        "server": {
            "name": "test",
            "directory": str(server_dir),
            "jar": "server.jar",
        },
        "java": {"path": str(fake_java)},
        "rcon": {"enabled": False, "password": ""},
        "backup": {"enabled": False},
        "restart": {"enabled": False},
        "watchdog": {"enabled": False},
        "notifications": {"enabled": False},
    }
    for section, values in overrides.items():
        raw.setdefault(section, {}).update(values)
    cfg = config_from_dict(raw)
    # Place the config "at" the server dir so derived paths resolve there.
    cfg._config_path = server_dir / "mcsu.toml"
    return cfg


def _run_until_ready(sup: Supervisor, timeout=10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sup._ready.is_set():
            return True
        time.sleep(0.05)
    return False


def test_supervisor_starts_and_emits_ready(server_dir, fake_java):
    cfg = _config(server_dir, fake_java)
    sup = Supervisor(cfg, console_mirror=False)
    events = []
    sup.bus.subscribe_all(lambda e: events.append(e.type))

    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        assert _run_until_ready(sup), "server never became ready"
        assert EventType.SERVER_STARTING in events
        assert EventType.SERVER_READY in events
    finally:
        sup.shutdown()
        thread.join(timeout=10)
    assert EventType.SERVER_STOPPED in events


def test_supervisor_tracks_join(server_dir, fake_java):
    cfg = _config(server_dir, fake_java)
    sup = Supervisor(cfg, console_mirror=False)
    sup._on_console_line("[12:00:02] [Server thread/INFO]: Steve joined the game")
    assert sup.players.online == ["Steve"]
    assert sup.players.online_count == 1


def test_supervisor_manual_backup(server_dir, fake_java):
    cfg = _config(server_dir, fake_java, backup={"enabled": True, "paths": ["world"]})
    sup = Supervisor(cfg, console_mirror=False)
    completed = []
    sup.bus.subscribe(EventType.BACKUP_COMPLETED, lambda e: completed.append(e))
    sup.perform_backup(reason="test")
    assert len(completed) == 1
    backups = sup.backups.list_backups()
    assert len(backups) == 1


def test_supervisor_skip_backup_when_idle(server_dir, fake_java):
    cfg = _config(
        server_dir,
        fake_java,
        backup={"enabled": True, "paths": ["world"], "skip_if_no_players": True},
    )
    sup = Supervisor(cfg, console_mirror=False)
    skipped = []
    sup.bus.subscribe(EventType.BACKUP_SKIPPED, lambda e: skipped.append(e))
    sup._scheduled_backup()  # no players have joined
    assert len(skipped) == 1
    assert sup.backups.list_backups() == []


def test_supervisor_backup_runs_after_activity(server_dir, fake_java):
    cfg = _config(
        server_dir,
        fake_java,
        backup={"enabled": True, "paths": ["world"], "skip_if_no_players": True},
    )
    sup = Supervisor(cfg, console_mirror=False)
    sup.players.player_joined("Steve", when=time.time())
    sup.players.player_left("Steve", when=time.time())
    sup._scheduled_backup()
    assert len(sup.backups.list_backups()) == 1


def test_crash_loop_detection_gives_up(server_dir, fake_java):
    cfg = _config(
        server_dir,
        fake_java,
        watchdog={"enabled": True, "max_restarts": 2, "restart_window": 600, "restart_backoff": 0},
    )
    sup = Supervisor(cfg, console_mirror=False)
    errors = []
    sup.bus.subscribe(EventType.SERVER_ERROR, lambda e: errors.append(e))
    # Don't actually relaunch the server during recovery in this unit test.
    sup._start_server = lambda: None  # type: ignore[method-assign]
    # max_restarts=2 permits two recoveries; the third crash trips the guard.
    sup._handle_crash(1)
    assert not sup._shutdown.is_set()
    sup._handle_crash(1)
    assert not sup._shutdown.is_set()
    sup._handle_crash(1)
    assert sup._shutdown.is_set()
    assert any("loop" in e.message.lower() for e in errors)


def _slow_boot_then_crash_java(tmp_path: Path, boot_seconds: float) -> Path:
    """A 'server' that boots for ``boot_seconds``, prints Done, and dies at once -- a modpack
    whose crash comes right after a long start."""
    script = tmp_path / "slowcrash.py"
    script.write_text(
        "import sys, time\n"
        f"time.sleep({boot_seconds})\n"
        "print('[12:00:01] [Server thread/INFO]: Done (1.0s)! For help, type \"help\"')\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmp_path / "slowcrash.bat"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}"\r\n', encoding="utf-8")
        return launcher
    launcher = tmp_path / "slowcrash"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher


def test_crash_loop_is_caught_however_long_each_boot_takes(server_dir, tmp_path):
    """Regression: the window was wall-clock, so a pack booting longer than
    restart_window / max_restarts that crashed right after "Done" never tripped the guard --
    it booted and crashed forever (a real 1.12 modpack did, rewriting world data each time)."""
    java = _slow_boot_then_crash_java(tmp_path, boot_seconds=2.5)
    cfg = _config(
        server_dir,
        java,
        watchdog={
            "enabled": True,
            "max_restarts": 3,
            "restart_window": 6,
            "restart_backoff": 0,
            "check_interval": 1,
        },
    )
    sup = Supervisor(cfg, console_mirror=False)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        # Each cycle (2.5 s boot + up to 1 s to notice) ends inside the 6 s window of its own
        # start, so every crash counts and the fourth trips the guard. A wall-clock window would
        # need three cycles inside 6 s -- under 2 s each -- so it never trips.
        thread.join(timeout=40)
        assert not thread.is_alive(), "the watchdog never gave up on the crash loop"
        assert sup.gave_up
        assert sup.restarts == 3
    finally:
        sup.shutdown()
        thread.join(timeout=10)


def test_a_crash_after_a_long_stable_run_starts_the_count_afresh(
    server_dir, fake_java, monkeypatch
):
    cfg = _config(
        server_dir,
        fake_java,
        watchdog={"enabled": True, "max_restarts": 1, "restart_window": 600, "restart_backoff": 0},
    )
    sup = Supervisor(cfg, console_mirror=False)
    clock = [1000.0]
    monkeypatch.setattr("mcsu.supervisor.time.monotonic", lambda: clock[0])

    def fake_start():  # what the real start records
        sup._started_at = clock[0]

    sup._start_server = fake_start  # type: ignore[method-assign]
    fake_start()
    clock[0] += 60
    sup._handle_crash(1)  # 60 s after its start: counts (1 of 1)
    assert not sup._shutdown.is_set()
    clock[0] += 3600
    sup._handle_crash(1)  # after an hour up: an isolated crash, not a loop
    assert not sup._shutdown.is_set()
    clock[0] += 30
    sup._handle_crash(1)  # 30 s after that restart: the second quick crash in a row
    assert sup._shutdown.is_set() and sup.gave_up


def test_a_process_seen_before_it_started_is_not_a_crash(server_dir, fake_java):
    """Regression: a restart builds the new process, then starts it. A health check that read it
    in between saw "not running" and -- once the lock let it in, with the server now up -- handled
    a crash of a healthy server: a spurious restart, and a second JVM on the same world."""
    cfg = _config(
        server_dir,
        fake_java,
        watchdog={"enabled": True, "max_restarts": 5, "restart_window": 600, "restart_backoff": 0},
    )
    sup = Supervisor(cfg, console_mirror=False)
    from types import SimpleNamespace

    up = SimpleNamespace(pid=4242, returncode=None, is_running=lambda: True)
    sup._proc = up  # type: ignore[assignment]
    crashed, starts = [], []
    sup.bus.subscribe(EventType.SERVER_CRASHED, lambda e: crashed.append(e))
    sup._start_server = lambda: starts.append(1)  # type: ignore[method-assign]
    sup._handle_crash(None, up)  # what the stale check reported
    assert crashed == [] and starts == [], "a running server was handled as crashed"
    # Positive control: the same process, really dead, is a crash.
    dead = SimpleNamespace(pid=4242, returncode=1, is_running=lambda: False)
    sup._proc = dead  # type: ignore[assignment]
    sup._handle_crash(1, dead)
    assert len(crashed) == 1 and starts == [1]


def test_crash_with_watchdog_disabled_stops(server_dir, fake_java):
    cfg = _config(server_dir, fake_java, watchdog={"enabled": False})
    sup = Supervisor(cfg, console_mirror=False)
    crashed = []
    sup.bus.subscribe(EventType.SERVER_CRASHED, lambda e: crashed.append(e))
    sup._handle_crash(137)
    assert len(crashed) == 1
    assert sup._shutdown.is_set()


def test_countdown_broadcasts_messages(server_dir, fake_java, monkeypatch):
    cfg = _config(
        server_dir,
        fake_java,
        restart={
            "enabled": True,
            "warning_seconds": [3, 1],
            "warning_message": "Restart in {time}!",
        },
    )
    sup = Supervisor(cfg, console_mirror=False)
    said: list[str] = []
    monkeypatch.setattr(sup, "_say", said.append)
    monkeypatch.setattr("mcsu.supervisor.time.sleep", lambda *_: None)
    sup._broadcast_countdown()
    assert "Restart in 3s!" in said
    assert "Restart in 1s!" in said


def test_control_file_stop(server_dir, fake_java):
    cfg = _config(server_dir, fake_java)
    sup = Supervisor(cfg, console_mirror=False)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        assert _run_until_ready(sup)
        from mcsu.state import write_control

        write_control(cfg.state_dir, "stop")
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        sup.shutdown()
        thread.join(timeout=5)


def test_one_crash_is_handled_once_even_when_two_threads_see_it(server_dir, fake_java):
    """Regression: the main loop AND the watchdog both noticed the same dead process and both
    handled it -- racing state writes, and two restarts of one server."""
    cfg = _config(
        server_dir,
        fake_java,
        watchdog={"enabled": True, "max_restarts": 5, "restart_window": 600, "restart_backoff": 0},
    )
    sup = Supervisor(cfg, console_mirror=False)
    from types import SimpleNamespace

    dead = SimpleNamespace(pid=None, is_running=lambda: False)
    sup._proc = dead  # type: ignore[assignment]
    starts = []

    def fake_start():  # a restart replaces the process
        starts.append(1)
        sup._proc = SimpleNamespace(pid=None, is_running=lambda: True)  # type: ignore[assignment]

    sup._start_server = fake_start  # type: ignore[method-assign]
    barrier = threading.Barrier(2)

    def report():
        barrier.wait()
        sup._handle_crash(1, dead)  # type: ignore[arg-type]

    threads = [threading.Thread(target=report) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert starts == [1], "exactly one restart per crash"
