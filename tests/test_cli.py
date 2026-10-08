"""Tests for the CLI surface, invoking ``mcsu.cli.main`` directly."""

from __future__ import annotations

import os

import pytest

from mcsu.cli import main


@pytest.fixture(autouse=True)
def _force_color_off(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")


def _init(tmp_path):
    rc = main(["init", "--dir", str(tmp_path), "--name", "cli", "--loader", "paper"])
    assert rc == 0
    return tmp_path / "mcsu.toml"


def test_help_no_command(capsys):
    rc = main([])
    assert rc == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_init_creates_config(tmp_path, capsys):
    cfg = _init(tmp_path)
    assert cfg.is_file()
    assert "Created" in capsys.readouterr().out


def test_init_refuses_overwrite(tmp_path):
    _init(tmp_path)
    rc = main(["init", "--dir", str(tmp_path)])
    assert rc == 1


def test_init_force_overwrite(tmp_path):
    _init(tmp_path)
    rc = main(["init", "--dir", str(tmp_path), "--force"])
    assert rc == 0


def test_properties_set_and_get(tmp_path, capsys):
    cfg = _init(tmp_path)
    assert main(["-c", str(cfg), "properties", "set", "max-players", "42"]) == 0
    capsys.readouterr()
    assert main(["-c", str(cfg), "properties", "get", "max-players"]) == 0
    assert capsys.readouterr().out.strip() == "42"


def test_properties_get_missing_key(tmp_path):
    cfg = _init(tmp_path)
    rc = main(["-c", str(cfg), "properties", "get", "does-not-exist"])
    assert rc == 1


def test_backup_create_list_prune(tmp_path, capsys):
    cfg = _init(tmp_path)
    # Give the server a world to back up.
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"data")

    assert main(["-c", str(cfg), "backup", "create"]) == 0
    capsys.readouterr()
    assert main(["-c", str(cfg), "backup", "list"]) == 0
    out = capsys.readouterr().out
    assert "backup_" in out
    assert main(["-c", str(cfg), "backup", "prune"]) == 0


def test_status_unmanaged(tmp_path, capsys):
    cfg = _init(tmp_path)
    rc = main(["-c", str(cfg), "status"])
    assert rc == 0
    assert "Server: cli" in capsys.readouterr().out


def test_players_empty(tmp_path, capsys):
    cfg = _init(tmp_path)
    rc = main(["-c", str(cfg), "players"])
    assert rc == 0
    assert "No player statistics" in capsys.readouterr().out


def test_cmd_requires_rcon_password(tmp_path):
    cfg = _init(tmp_path)
    # Default config has rcon enabled but empty password -> should error clearly.
    rc = main(["-c", str(cfg), "cmd", "list"])
    assert rc == 1


def test_stop_without_supervisor(tmp_path):
    cfg = _init(tmp_path)
    rc = main(["-c", str(cfg), "stop"])
    assert rc == 1


def test_versions_vanilla(monkeypatch, capsys):
    monkeypatch.setattr(
        "mcsu.installer.ServerInstaller.list_vanilla_versions",
        lambda self, releases_only=True: ["1.20.4", "1.20.2", "1.19.4"],
    )
    rc = main(["versions", "--loader", "vanilla", "--limit", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1.20.4" in out
    assert "and 1 more" in out


def test_versions_loader_builds(monkeypatch, capsys):
    # `versions --loader fabric --mc-version 1.21` lists loader versions.
    monkeypatch.setattr(
        "mcsu.installer.ServerInstaller.list_versions",
        lambda self, loader, mc_version=None, *, include_unstable=False: ["0.16.5", "0.16.4"],
    )
    rc = main(["versions", "--loader", "fabric", "--mc-version", "1.21"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0.16.5" in out
    assert "--loader-version" in out  # actionable install hint


def test_install_invokes_installer(monkeypatch, tmp_path, capsys):
    cfg = _init(tmp_path)
    from mcsu.installer import InstallResult

    def fake_install(self, loader, mc_version, dest, *, loader_version=None):
        os.fspath(dest)
        return InstallResult(loader, mc_version or "1.20.4", "123", dest)

    monkeypatch.setattr("mcsu.installer.ServerInstaller.install", fake_install)
    rc = main(["-c", str(cfg), "install", "--loader", "paper", "--mc-version", "1.20.4"])
    assert rc == 0
    assert "Downloaded paper" in capsys.readouterr().out


def _fleet(tmp_path):
    root = tmp_path / "fleet"
    for name, title, online in (("alpha", "Alpha Pack", "false"), ("beta", "Beta World", "false")):
        d = root / name
        d.mkdir(parents=True)
        (d / "mcsu.toml").write_text(
            f'[server]\nname = "{title}"\nloader = "paper"\nmc_version = "1.18.2"\n'
        )
        (d / "server.properties").write_text(f"online-mode={online}\n")
    (root / "fleet.toml").write_text('[fleet]\nname = "t"\n')
    return root


def test_fleet_list(tmp_path, capsys):
    root = _fleet(tmp_path)
    assert main(["fleet", "--fleet", str(root), "list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "Beta World" in out and "Paper 1.18.2" in out


def test_fleet_status_without_a_daemon(tmp_path, capsys):
    root = _fleet(tmp_path)
    assert main(["fleet", "--fleet", str(root), "status"]) == 1
    assert "no status yet" in capsys.readouterr().err


def test_fleet_whitelist_without_a_daemon_edits_every_file(tmp_path, capsys):
    import json

    root = _fleet(tmp_path)
    assert main(["fleet", "--fleet", str(root), "whitelist", "add", "Steve"]) == 0
    for name in ("alpha", "beta"):
        assert json.loads((root / name / "whitelist.json").read_text())[0]["name"] == "Steve"
    assert main(["fleet", "--fleet", str(root), "whitelist", "add", "bad name"]) == 1


def test_fleet_start_rejects_unknown_servers_and_queues_known_ones(tmp_path, capsys):
    root = _fleet(tmp_path)
    assert main(["fleet", "--fleet", str(root), "start", "nope"]) == 1
    assert main(["fleet", "--fleet", str(root), "start", "beta"]) == 0
    assert '"start"' in (root / ".fleet" / "control").read_text()
