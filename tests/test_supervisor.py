"""Integration tests for the Supervisor using the fake-java fixture.

These exercise the real control loop: starting the server, detecting
readiness, emitting events, taking a backup, and shutting down — all without
Java or a network.
"""

from __future__ import annotations

import threading
import time

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
