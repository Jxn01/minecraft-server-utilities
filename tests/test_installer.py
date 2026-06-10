"""Unit tests for the installer's parsing/resolution logic.

Network calls are mocked so these run offline and deterministically. The HTTP
plumbing itself is thin; what we verify here is the loader-specific resolution
(version selection, URL/checksum extraction, Maven metadata parsing).
"""

from __future__ import annotations

import pytest

from mcsu.errors import InstallError
from mcsu.installer import (
    ServerInstaller,
    _flatten_paper_versions,
    _parse_maven_versions,
)


def test_parse_maven_versions():
    xml = """
    <metadata><versioning><versions>
        <version>1.20.1-47.1.0</version>
        <version>1.20.4-49.0.3</version>
    </versions></versioning></metadata>
    """
    assert _parse_maven_versions(xml) == ["1.20.1-47.1.0", "1.20.4-49.0.3"]


def test_flatten_paper_versions():
    grouped = {"1.21": ["1.21.1", "1.21"], "1.20": ["1.20.4"]}
    assert _flatten_paper_versions(grouped) == ["1.21.1", "1.21", "1.20.4"]
    # Already-flat lists pass through.
    assert _flatten_paper_versions(["1.20.4", "1.20.2"]) == ["1.20.4", "1.20.2"]


def test_unknown_loader_rejected(tmp_path):
    installer = ServerInstaller()
    with pytest.raises(InstallError, match="unknown loader"):
        installer.install("bukkit", "1.20.4", tmp_path / "server.jar")


def test_vanilla_resolution(monkeypatch, tmp_path):
    manifest = {
        "latest": {"release": "1.20.4"},
        "versions": [
            {"id": "1.20.4", "type": "release", "url": "https://meta/1.20.4.json"},
        ],
    }
    version_meta = {"downloads": {"server": {"url": "https://dl/server.jar", "sha1": "abc"}}}
    captured = {}

    def fake_get_json(url):
        return manifest if "version_manifest" in url else version_meta

    def fake_download(url, dest, *, sha256=None, sha1=None, progress=None):
        captured["url"] = url
        captured["sha1"] = sha1
        dest.write_bytes(b"jar")
        return dest

    monkeypatch.setattr("mcsu.installer._get_json", fake_get_json)
    monkeypatch.setattr("mcsu.installer._download", fake_download)

    installer = ServerInstaller()
    result = installer.install("vanilla", "latest", tmp_path / "server.jar")
    assert result.loader == "vanilla"
    assert result.mc_version == "1.20.4"
    assert captured["url"] == "https://dl/server.jar"
    assert captured["sha1"] == "abc"


def test_vanilla_unknown_version(monkeypatch, tmp_path):
    manifest = {"latest": {"release": "1.20.4"}, "versions": []}
    monkeypatch.setattr("mcsu.installer._get_json", lambda url: manifest)
    installer = ServerInstaller()
    with pytest.raises(InstallError, match="unknown Minecraft version"):
        installer.install("vanilla", "1.0.0", tmp_path / "server.jar")


def test_paper_resolution(monkeypatch, tmp_path):
    builds = [
        {
            "id": 500,
            "downloads": {
                "server:default": {
                    "url": "https://paper/500/server.jar",
                    "checksums": {"sha256": "deadbeef"},
                }
            },
        }
    ]
    monkeypatch.setattr("mcsu.installer._get_json", lambda url: builds)
    captured = {}

    def fake_download(url, dest, *, sha256=None, sha1=None, progress=None):
        captured.update(url=url, sha256=sha256)
        dest.write_bytes(b"jar")
        return dest

    monkeypatch.setattr("mcsu.installer._download", fake_download)
    installer = ServerInstaller()
    result = installer.install("paper", "1.20.4", tmp_path / "server.jar")
    assert result.loader_version == "500"
    assert captured["sha256"] == "deadbeef"


def test_neoforge_is_installer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "mcsu.installer._get",
        lambda url: b"<version>20.4.100</version><version>20.4.235</version>",
    )
    monkeypatch.setattr(
        "mcsu.installer._download",
        lambda url, dest, **kw: dest.write_bytes(b"jar") or dest,
    )
    installer = ServerInstaller()
    result = installer.install("neoforge", "1.20.4", tmp_path / "server.jar")
    assert result.is_installer is True
    assert result.loader_version == "20.4.235"
    assert result.post_install_hint and "installServer" in result.post_install_hint


def test_install_honors_explicit_loader_version(monkeypatch, tmp_path):
    # A specific Paper build should be selected when --loader-version is given.
    builds = [
        {"id": 500, "downloads": {"server:default": {"url": "u500", "checksums": {}}}},
        {"id": 412, "downloads": {"server:default": {"url": "u412", "checksums": {}}}},
    ]
    monkeypatch.setattr("mcsu.installer._get_json", lambda url: builds)
    captured = {}
    monkeypatch.setattr(
        "mcsu.installer._download",
        lambda url, dest, **kw: captured.update(url=url) or dest.write_bytes(b"j") or dest,
    )
    installer = ServerInstaller()
    result = installer.install("paper", "1.20.4", tmp_path / "s.jar", loader_version="412")
    assert result.loader_version == "412"
    assert captured["url"] == "u412"


def test_list_versions_unknown_loader():
    with pytest.raises(InstallError, match="unknown loader"):
        ServerInstaller().list_versions("bukkit")


def test_list_versions_paper_mc_versions(monkeypatch):
    monkeypatch.setattr(
        "mcsu.installer._get_json",
        lambda url: {"versions": {"1.21": ["1.21.1", "1.21"], "1.20": ["1.20.4"]}},
    )
    out = ServerInstaller().list_versions("paper")
    assert out == ["1.21.1", "1.21", "1.20.4"]


def test_list_versions_paper_builds_for_mc(monkeypatch):
    monkeypatch.setattr("mcsu.installer._get_json", lambda url: [{"id": 500}, {"id": 499}])
    out = ServerInstaller().list_versions("paper", "1.20.4")
    assert out == ["500", "499"]


def test_list_versions_forge_loader_versions(monkeypatch):
    xml = (
        "<version>1.20.3-49.0.1</version>"
        "<version>1.20.4-49.0.2</version>"
        "<version>1.20.4-49.0.11</version>"
    )
    monkeypatch.setattr("mcsu.installer._get", lambda url: xml.encode())
    out = ServerInstaller().list_versions("forge", "1.20.4")
    assert out == ["49.0.11", "49.0.2"]  # newest first, mc prefix stripped


def test_list_versions_forge_mc_versions(monkeypatch):
    xml = "<version>1.20.3-49.0.1</version><version>1.20.4-49.0.2</version>"
    monkeypatch.setattr("mcsu.installer._get", lambda url: xml.encode())
    out = ServerInstaller().list_versions("forge")
    assert out == ["1.20.4", "1.20.3"]


def test_list_versions_fabric_loader_versions(monkeypatch):
    monkeypatch.setattr(
        "mcsu.installer._get_json",
        lambda url: [
            {"version": "0.16.5", "stable": True},
            {"version": "0.16.4", "stable": True},
            {"version": "0.16.0-beta.1", "stable": False},
        ],
    )
    out = ServerInstaller().list_versions("fabric", "1.21")
    assert out == ["0.16.5", "0.16.4"]
    out_all = ServerInstaller().list_versions("fabric", "1.21", include_unstable=True)
    assert "0.16.0-beta.1" in out_all
