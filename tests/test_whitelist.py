"""Whitelist edits for stopped servers: the UUID must be the one the server itself would use."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcsu import whitelist
from mcsu.whitelist import WhitelistError


def make_server(tmp_path: Path, *, online: bool) -> Path:
    (tmp_path / "server.properties").write_text(f"online-mode={'true' if online else 'false'}\n")
    return tmp_path


def entries(server: Path) -> list[dict[str, str]]:
    return json.loads((server / "whitelist.json").read_text())


def test_offline_uuid_matches_javas_name_uuid_from_bytes() -> None:
    # UUID.nameUUIDFromBytes("OfflinePlayer:Notch") -- the value every offline-mode server writes.
    # (Also checked against 31 real usercache.json entries from offline-mode servers.)
    assert whitelist.offline_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"


def test_offline_server_gets_the_offline_uuid(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=False)
    assert whitelist.add_to_file(
        server, "Notch", resolver=lambda n: pytest.fail("no lookup offline")
    )
    assert entries(server) == [{"uuid": "b50ad385-829d-3141-a216-7e7d7539ba7f", "name": "Notch"}]


def test_online_server_gets_the_account_uuid(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=True)
    account = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
    assert whitelist.add_to_file(server, "Notch", resolver=lambda n: account)
    assert entries(server)[0]["uuid"] == account


def test_no_server_properties_means_online_mode(tmp_path: Path) -> None:
    assert whitelist.is_online_mode(tmp_path) is True


def test_unknown_account_on_an_online_server_is_an_error(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=True)
    with pytest.raises(WhitelistError):
        whitelist.add_to_file(server, "Nobody_Here", resolver=lambda n: None)
    assert not (server / "whitelist.json").exists()


def test_adding_twice_is_a_no_op_case_insensitively(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=False)
    assert whitelist.add_to_file(server, "Steve")
    assert not whitelist.add_to_file(server, "steve")
    assert len(entries(server)) == 1


def test_remove(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=False)
    whitelist.add_to_file(server, "Steve")
    whitelist.add_to_file(server, "Alex")
    assert whitelist.remove_from_file(server, "STEVE")
    assert not whitelist.remove_from_file(server, "Steve")
    assert whitelist.whitelist_names(server) == ["Alex"]


def test_a_broken_file_is_reported_not_overwritten(tmp_path: Path) -> None:
    server = make_server(tmp_path, online=False)
    (server / "whitelist.json").write_text("{not json")
    with pytest.raises(WhitelistError):
        whitelist.add_to_file(server, "Steve")
    assert (server / "whitelist.json").read_text() == "{not json"


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Steve", True),
        ("a_b_123", True),
        ("ab", False),
        ("x" * 17, False),
        ("bad name", False),
        ("ünï", False),
    ],
)
def test_valid_player_name(name: str, ok: bool) -> None:
    assert whitelist.valid_player_name(name) is ok
