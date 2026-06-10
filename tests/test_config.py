from __future__ import annotations

import pytest

from mcsu.config import config_from_dict, load_config, to_toml
from mcsu.errors import ConfigError


def test_defaults():
    cfg = config_from_dict({})
    assert cfg.name == "minecraft"
    assert cfg.java.max_memory == "4G"
    assert cfg.rcon.port == 25575
    assert cfg.backup.format == "tar.gz"


def test_nested_sections():
    cfg = config_from_dict(
        {
            "server": {"name": "survival", "loader": "paper"},
            "java": {"max_memory": "8G"},
            "rcon": {"port": 25580, "password": "secret"},
        }
    )
    assert cfg.name == "survival"
    assert cfg.loader == "paper"
    assert cfg.java.max_memory == "8G"
    assert cfg.rcon.port == 25580
    assert cfg.rcon.password == "secret"


def test_duration_coercion():
    cfg = config_from_dict({"backup": {"interval": "1h30m"}, "restart": {"interval": "6h"}})
    assert cfg.backup.interval == 5400
    assert cfg.restart.interval == 21600


def test_unknown_key_rejected():
    with pytest.raises(ConfigError, match="unknown key"):
        config_from_dict({"server": {"bogus": 1}})


def test_unknown_section_key_rejected():
    with pytest.raises(ConfigError, match="unknown key"):
        config_from_dict({"rcon": {"nope": True}})


def test_invalid_backup_format():
    with pytest.raises(ConfigError, match=r"backup\.format"):
        config_from_dict({"backup": {"format": "rar"}})


def test_invalid_daily_time():
    with pytest.raises(ConfigError, match="daily_times"):
        config_from_dict({"restart": {"daily_times": ["25:00"]}})


def test_roundtrip_through_toml(tmp_path):
    original = config_from_dict(
        {
            "server": {"name": "rt", "loader": "fabric", "mc_version": "1.21"},
            "backup": {"interval": "2h", "keep": 10},
            "rcon": {"password": "pw"},
        }
    )
    path = tmp_path / "mcsu.toml"
    path.write_text(to_toml(original), encoding="utf-8")
    reloaded = load_config(path)
    assert reloaded.name == "rt"
    assert reloaded.loader == "fabric"
    assert reloaded.mc_version == "1.21"
    assert reloaded.backup.interval == 7200
    assert reloaded.backup.keep == 10
    assert reloaded.rcon.password == "pw"


def test_load_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="no configuration file"):
        load_config(tmp_path / "does-not-exist.toml")


def test_derived_paths(tmp_path):
    path = tmp_path / "mcsu.toml"
    path.write_text(to_toml(config_from_dict({"server": {"directory": "world_srv"}})), "utf-8")
    cfg = load_config(path)
    assert cfg.server_dir == (tmp_path / "world_srv").resolve()
    assert cfg.jar_path.name == "server.jar"
    assert cfg.state_dir.name == ".mcsu"
