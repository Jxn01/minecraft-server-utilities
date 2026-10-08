"""Fleet mode: discovery, one-active switching, resume, crash loops, control file,
whitelist and the status documents."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from mcsu import fleet as fleet_mod
from mcsu.errors import ConfigError, McsuError
from mcsu.fleet import FleetDaemon, discover, fleet_from_dict, load_fleet, send_command


def _toml_path(p: Path) -> str:
    return str(p).replace("\\", "\\\\")


def make_server(
    root: Path, name: str, java: Path, *, title: str | None = None, online: bool = False
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "server.jar").write_text("")
    (d / "world").mkdir()
    (d / "server.properties").write_text(
        f"online-mode={'true' if online else 'false'}\nmax-players=8\n"
    )
    (d / "mcsu.toml").write_text(
        f"""
[server]
name = "{title or name}"
jar = "server.jar"
loader = "fabric"
mc_version = "1.20.1"
stop_timeout = 10
[java]
path = "{_toml_path(java)}"
[rcon]
enabled = false
[backup]
enabled = false
[restart]
enabled = false
[watchdog]
check_interval = 1
max_restarts = 1
restart_window = 600
restart_backoff = 0
"""
    )
    return d


@pytest.fixture
def crash_java(tmp_path: Path) -> Path:
    """A 'server' that exits with an error immediately -- a crash loop in the making."""
    if os.name == "nt":
        launcher = tmp_path / "crashjava.bat"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" -c "raise SystemExit(1)"\r\n', encoding="utf-8"
        )
        return launcher
    launcher = tmp_path / "crashjava"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -c "raise SystemExit(1)"\n', encoding="utf-8"
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher


@pytest.fixture
def fleet_dir(tmp_path: Path, fake_java: Path) -> Path:
    root = tmp_path / "fleet"
    make_server(root, "alpha", fake_java, title="Alpha Pack")
    make_server(root, "beta", fake_java, title="Beta World", online=True)
    (root / "notes").mkdir()  # no mcsu.toml: not a server
    (root / "fleet.toml").write_text(
        """
[fleet]
name = "test"
address = "mc.example.org"
status_interval = 1
switch_warning_seconds = []
public_status_file = "public/status.json"
"""
    )
    return root


def wait_for(cond, timeout: float = 15.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Running:
    """A FleetDaemon on a background thread for the duration of a test."""

    def __init__(self, fleet_dir: Path, **kw) -> None:  # type: ignore[no-untyped-def]
        self.daemon = FleetDaemon(
            load_fleet(fleet_dir), resolver=lambda n: "069a79f4-44e9-4726-a5be-fca90e38aaf5", **kw
        )
        self.thread = threading.Thread(target=self.daemon.run, daemon=True)

    def __enter__(self) -> FleetDaemon:
        self.thread.start()
        return self.daemon

    def __exit__(self, *exc: object) -> None:
        self.daemon.shutdown()
        self.thread.join(30)
        assert not self.thread.is_alive(), "the daemon must stop when asked"


def running_state(daemon: FleetDaemon) -> str:
    return daemon.status_document()["state"]


# --------------------------------------------------------------------------- #
# Configuration and discovery
# --------------------------------------------------------------------------- #


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        fleet_from_dict({"fleet": {"nmae": "typo"}})
    with pytest.raises(ConfigError, match="unknown table"):
        fleet_from_dict({"fleeet": {}})


def test_discovery_sorts_by_title_and_skips_non_servers(fleet_dir: Path) -> None:
    members = discover(load_fleet(fleet_dir))
    assert [(m.name, m.title, m.version) for m in members] == [
        ("alpha", "Alpha Pack", "Fabric 1.20.1"),
        ("beta", "Beta World", "Fabric 1.20.1"),
    ]


def test_titles_can_be_overridden_or_hidden(fleet_dir: Path) -> None:
    (fleet_dir / "fleet.toml").write_text(
        '[servers.alpha]\ntitle = "Renamed"\n[servers.beta]\nhidden = true\n'
    )
    assert [m.title for m in discover(load_fleet(fleet_dir))] == ["Renamed"]


def test_duplicate_titles_are_refused(fleet_dir: Path) -> None:
    (fleet_dir / "fleet.toml").write_text('[servers.alpha]\ntitle = "Beta World"\n')
    with pytest.raises(ConfigError, match="share a title"):
        discover(load_fleet(fleet_dir))


# --------------------------------------------------------------------------- #
# The daemon
# --------------------------------------------------------------------------- #


def test_one_server_at_a_time_switching_and_stopping(fleet_dir: Path) -> None:
    with Running(fleet_dir) as daemon:
        daemon.request("start", "alpha")
        assert wait_for(lambda: daemon.active == "alpha" and running_state(daemon) == "running")
        alpha_sup = daemon._sup
        daemon.request("start", "beta")
        assert wait_for(lambda: daemon.active == "beta" and running_state(daemon) == "running")
        assert not alpha_sup._proc.is_running(), "switching must stop the previous server"
        state = json.loads((fleet_dir / ".fleet/state.json").read_text())
        assert state["active"] == "beta"
        daemon.request("stop")
        assert wait_for(lambda: daemon.active is None and running_state(daemon) == "stopped")
        assert json.loads((fleet_dir / ".fleet/state.json").read_text())["active"] is None


def test_resumes_the_active_server_after_a_restart(fleet_dir: Path) -> None:
    with Running(fleet_dir) as daemon:
        daemon.request("start", "beta")
        assert wait_for(lambda: running_state(daemon) == "running")
    # The daemon stopped (host shutdown) but beta stays the active CHOICE...
    assert json.loads((fleet_dir / ".fleet/state.json").read_text())["active"] == "beta"
    # ...so the next daemon brings it back by itself.
    with Running(fleet_dir) as daemon:
        assert wait_for(lambda: daemon.active == "beta" and running_state(daemon) == "running")


def test_a_crash_loop_is_reported_and_never_resumed(
    tmp_path: Path, fleet_dir: Path, crash_java: Path
) -> None:
    make_server(fleet_dir, "gamma", crash_java, title="Gamma Crash")
    with Running(fleet_dir) as daemon:
        daemon.request("start", "gamma")
        assert wait_for(lambda: daemon.status_document()["crash_loop"], 30)
        doc = daemon.status_document()
        assert doc["active"] is None and doc["state"] == "crashed"
        assert "crashed" in doc["last_action"]
    assert json.loads((fleet_dir / ".fleet/state.json").read_text())["active"] is None


def test_cli_commands_arrive_through_the_control_file(fleet_dir: Path) -> None:
    with Running(fleet_dir) as daemon:
        send_command(load_fleet(fleet_dir), "start", "alpha")
        assert wait_for(lambda: daemon.active == "alpha")
        assert not (fleet_dir / ".fleet/control").exists(), "the daemon drains the file"


def test_unknown_commands_and_servers_are_rejected(fleet_dir: Path) -> None:
    with pytest.raises(McsuError):
        send_command(load_fleet(fleet_dir), "format_disk")
    with Running(fleet_dir) as daemon:
        daemon.request("start", "nope")
        assert wait_for(lambda: "failed" in daemon.status_document()["last_action"])
        assert daemon.active is None


def test_a_second_daemon_refuses_to_start(fleet_dir: Path) -> None:
    # Another live process owns the fleet (the parent of this test process stands in for it).
    (fleet_dir / ".fleet").mkdir(exist_ok=True)
    (fleet_dir / ".fleet/daemon.pid").write_text(str(os.getppid()))
    with pytest.raises(McsuError, match="already running"):
        FleetDaemon(load_fleet(fleet_dir))._acquire_pid_file()
    # A stale pid file (dead process) does not block.
    (fleet_dir / ".fleet/daemon.pid").write_text("999999999")
    FleetDaemon(load_fleet(fleet_dir))._acquire_pid_file()


def test_whitelist_reaches_every_server_with_the_right_uuid(fleet_dir: Path) -> None:
    with Running(fleet_dir) as daemon:
        daemon.request("start", "alpha")
        assert wait_for(lambda: running_state(daemon) == "running")
        sent: list[str] = []
        real_console = daemon._sup.console
        daemon._sup.console = lambda cmd: (sent.append(cmd), real_console(cmd))[1]
        daemon.request("whitelist_add", "Steve")
        assert wait_for(lambda: "Whitelisted Steve on 2" in daemon.status_document()["last_action"])
        alpha = json.loads((fleet_dir / "alpha/whitelist.json").read_text())
        beta = json.loads((fleet_dir / "beta/whitelist.json").read_text())
        from mcsu.whitelist import offline_uuid

        assert alpha == [{"uuid": offline_uuid("Steve"), "name": "Steve"}]  # offline-mode server
        assert beta == [{"uuid": "069a79f4-44e9-4726-a5be-fca90e38aaf5", "name": "Steve"}]  # online
        assert sent == ["whitelist reload"], "the running server reloads the file it was given"
        daemon.request("whitelist_remove", "steve")
        assert wait_for(lambda: "Removed steve on 2" in daemon.status_document()["last_action"])
        assert json.loads((fleet_dir / "beta/whitelist.json").read_text()) == []


def test_invalid_player_names_are_refused(fleet_dir: Path) -> None:
    daemon = FleetDaemon(load_fleet(fleet_dir))
    with pytest.raises(McsuError):
        daemon.whitelist("add", "no spaces allowed")


def test_status_documents(fleet_dir: Path) -> None:
    with Running(fleet_dir) as daemon:
        daemon.request("start", "alpha")
        assert wait_for(lambda: running_state(daemon) == "running")
        assert wait_for(
            lambda: json.loads((fleet_dir / ".fleet/status.json").read_text())["state"] == "running"
        )
        doc = json.loads((fleet_dir / ".fleet/status.json").read_text())
        assert doc["active_title"] == "Alpha Pack" and doc["version"] == "Fabric 1.20.1"
        assert doc["address"] == "mc.example.org" and doc["max_players"] == 8
        assert [s["state"] for s in doc["servers"]] == ["running", "stopped"]
        public = json.loads((fleet_dir / "public/status.json").read_text())
        assert "memory_mib" not in public and "last_action" not in public
        assert public["active_title"] == "Alpha Pack"


def test_rss_reader_handles_missing_processes() -> None:
    assert fleet_mod._rss_mib(None) is None
    assert fleet_mod._rss_mib(2**22 + 12345) is None
