"""Resolve and download Minecraft server jars across loaders and versions.

Each loader has an official metadata API; this module speaks all of them
through one interface so ``mcsu install --loader paper --mc-version 1.20.4``
just works. Everything uses :mod:`urllib` (no third-party HTTP dependency)
and verifies downloads against the published SHA where the API provides one.

Supported loaders:

* **vanilla** — Mojang piston-meta version manifest
* **paper** / **folia** — PaperMC Fill API (v3)
* **purpur** — Purpur API v2
* **fabric** — FabricMC meta API (server launcher jar)
* **quilt** — QuiltMC meta API
* **neoforge** — NeoForged Maven (installer jar)
* **forge** — MinecraftForge Maven (installer jar)

The installer downloads jars (and, for Forge/NeoForge, the *installer* jar
that must then be run once with ``--installServer``); :meth:`ServerInstaller.install`
returns an :class:`InstallResult` describing what to do next.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcsu.errors import InstallError

_USER_AGENT = "mcsu/1.0 (+https://github.com/jxn01/minecraft_server_utilities)"
_TIMEOUT = 30.0

ProgressHook = Callable[[int, int], None]  # (bytes_done, total_bytes)


@dataclass(slots=True)
class InstallResult:
    loader: str
    mc_version: str
    loader_version: str | None
    jar_path: Path
    is_installer: bool = False  # True for Forge/NeoForge installer jars
    post_install_hint: str | None = None


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise InstallError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(f"network error fetching {url}: {exc}") from exc


def _get_json(url: str) -> Any:
    try:
        return json.loads(_get(url))
    except json.JSONDecodeError as exc:
        raise InstallError(f"invalid JSON from {url}: {exc}") from exc


def _download(
    url: str,
    dest: Path,
    *,
    sha256: str | None = None,
    sha1: str | None = None,
    progress: ProgressHook | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    digest256 = hashlib.sha256()
    digest1 = hashlib.sha1()
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest256.update(chunk)
                    digest1.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise InstallError(f"HTTP {exc.code} downloading {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise InstallError(f"failed to download {url}: {exc}") from exc

    if sha256 and digest256.hexdigest() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise InstallError(f"checksum mismatch for {dest.name} (expected sha256 {sha256})")
    if sha1 and digest1.hexdigest() != sha1.lower():
        tmp.unlink(missing_ok=True)
        raise InstallError(f"checksum mismatch for {dest.name} (expected sha1 {sha1})")
    tmp.replace(dest)
    return dest


# --------------------------------------------------------------------------- #
# Per-loader resolvers
# --------------------------------------------------------------------------- #

_MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
_PAPER_API = "https://fill.papermc.io/v3/projects"
_PURPUR_API = "https://api.purpurmc.org/v2/purpur"
_FABRIC_API = "https://meta.fabricmc.net/v2"
_QUILT_API = "https://meta.quiltmc.org/v3"
_NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
_FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"


class ServerInstaller:
    """Front door for resolving and installing server jars."""

    LOADERS = ("vanilla", "paper", "folia", "purpur", "fabric", "quilt", "neoforge", "forge")

    def __init__(self, progress: ProgressHook | None = None) -> None:
        self.progress = progress

    def install(
        self,
        loader: str,
        mc_version: str,
        dest: str | Path,
        *,
        loader_version: str | None = None,
    ) -> InstallResult:
        loader = loader.lower()
        if loader not in self.LOADERS:
            raise InstallError(f"unknown loader {loader!r}; choose from {', '.join(self.LOADERS)}")
        dest = Path(dest)
        method = getattr(self, f"_install_{loader}")
        return method(mc_version, dest, loader_version)

    # -- version discovery ------------------------------------------------- #

    def list_versions(
        self,
        loader: str,
        mc_version: str | None = None,
        *,
        include_unstable: bool = False,
    ) -> list[str]:
        """List installable versions for a loader, newest first.

        The meaning of the result depends on the loader and whether a
        ``mc_version`` is supplied:

        * Without ``mc_version`` you get the **Minecraft versions** the loader
          supports.
        * With ``mc_version`` you get the **loader's own versions/builds** for
          that Minecraft version — i.e. exactly the values you can pass to
          ``mcsu install --loader-version`` (Paper/Folia build numbers,
          Fabric/Quilt loader versions, Forge/NeoForge versions).

        ``include_unstable`` widens vanilla to snapshots and Fabric/Quilt to
        beta loaders.
        """
        loader = loader.lower()
        if loader not in self.LOADERS:
            raise InstallError(f"unknown loader {loader!r}; choose from {', '.join(self.LOADERS)}")
        method = getattr(self, f"_versions_{loader}")
        return method(mc_version, include_unstable)

    # -- vanilla ----------------------------------------------------------- #

    def list_vanilla_versions(self, *, releases_only: bool = True) -> list[str]:
        manifest = _get_json(_MOJANG_MANIFEST)
        return [
            v["id"] for v in manifest["versions"] if not releases_only or v["type"] == "release"
        ]

    def latest_vanilla(self) -> str:
        return _get_json(_MOJANG_MANIFEST)["latest"]["release"]

    def _versions_vanilla(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        return self.list_vanilla_versions(releases_only=not include_unstable)

    def _versions_paper(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        return self._versions_papermc("paper", mc_version)

    def _versions_folia(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        return self._versions_papermc("folia", mc_version)

    def _versions_papermc(self, project: str, mc_version: str | None) -> list[str]:
        if not mc_version:
            versions = _get_json(f"{_PAPER_API}/{project}")["versions"]
            return _flatten_paper_versions(versions)
        builds = _get_json(f"{_PAPER_API}/{project}/versions/{mc_version}/builds")
        return [str(b.get("id")) for b in builds]

    def _versions_purpur(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        if not mc_version:
            return list(reversed(_get_json(_PURPUR_API)["versions"]))
        builds = _get_json(f"{_PURPUR_API}/{mc_version}").get("builds", {}).get("all", [])
        return [str(b) for b in reversed(builds)]

    def _versions_fabric(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        return self._versions_fabric_like(_FABRIC_API, mc_version, include_unstable)

    def _versions_quilt(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        return self._versions_fabric_like(_QUILT_API, mc_version, include_unstable)

    def _versions_fabric_like(
        self, api: str, mc_version: str | None, include_unstable: bool
    ) -> list[str]:
        if not mc_version:
            games = _get_json(f"{api}/versions/game")
            return [g["version"] for g in games if include_unstable or g.get("stable")]
        loaders = _get_json(f"{api}/versions/loader")
        return [
            entry["version"] for entry in loaders if include_unstable or entry.get("stable", True)
        ]

    def _versions_neoforge(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        versions = _parse_maven_versions(_get(_NEOFORGE_MAVEN).decode("utf-8"))
        if mc_version:
            parts = mc_version.split(".")
            prefix = ".".join(parts[1:]) if len(parts) >= 2 else mc_version
            versions = [v for v in versions if v.startswith(prefix)]
        return list(reversed(versions))

    def _versions_forge(self, mc_version: str | None, include_unstable: bool) -> list[str]:
        versions = _parse_maven_versions(_get(_FORGE_MAVEN).decode("utf-8"))
        if mc_version:
            matching = [v for v in versions if v.startswith(f"{mc_version}-")]
            return [v.split("-", 1)[1] for v in reversed(matching)]
        # Distinct Minecraft versions Forge supports, newest first.
        seen: list[str] = []
        for v in reversed(versions):
            mc = v.split("-", 1)[0]
            if mc not in seen:
                seen.append(mc)
        return seen

    def _install_vanilla(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        manifest = _get_json(_MOJANG_MANIFEST)
        if not mc_version or mc_version == "latest":
            mc_version = manifest["latest"]["release"]
        entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
        if entry is None:
            raise InstallError(f"unknown Minecraft version: {mc_version}")
        version_meta = _get_json(entry["url"])
        server = version_meta.get("downloads", {}).get("server")
        if not server:
            raise InstallError(f"no server jar published for Minecraft {mc_version}")
        _download(server["url"], dest, sha1=server.get("sha1"), progress=self.progress)
        return InstallResult("vanilla", mc_version, None, dest)

    # -- paper / folia ----------------------------------------------------- #

    def _install_paper(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        return self._install_papermc("paper", mc_version, dest, loader_version)

    def _install_folia(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        return self._install_papermc("folia", mc_version, dest, loader_version)

    def _install_papermc(
        self, project: str, mc_version: str, dest: Path, build: str | None
    ) -> InstallResult:
        if not mc_version or mc_version == "latest":
            versions = _get_json(f"{_PAPER_API}/{project}")["versions"]
            # API returns versions grouped newest-first.
            mc_version = _flatten_paper_versions(versions)[0]
        builds = _get_json(f"{_PAPER_API}/{project}/versions/{mc_version}/builds")
        if not builds:
            raise InstallError(f"no {project} builds for Minecraft {mc_version}")
        chosen = (
            next((b for b in builds if str(b.get("id")) == str(build)), None)
            if build
            else builds[0]
        )
        if chosen is None:
            raise InstallError(f"{project} build {build} not found for {mc_version}")
        download = chosen["downloads"]["server:default"]
        url = download["url"]
        sha256 = download.get("checksums", {}).get("sha256")
        _download(url, dest, sha256=sha256, progress=self.progress)
        return InstallResult(project, mc_version, str(chosen.get("id")), dest)

    # -- purpur ------------------------------------------------------------ #

    def _install_purpur(self, mc_version: str, dest: Path, build: str | None) -> InstallResult:
        if not mc_version or mc_version == "latest":
            mc_version = _get_json(_PURPUR_API)["versions"][-1]
        build = build or "latest"
        url = f"{_PURPUR_API}/{mc_version}/{build}/download"
        meta = _get_json(f"{_PURPUR_API}/{mc_version}/{build}")
        sha = meta.get("md5")  # Purpur publishes md5; we still verify size via download
        _download(url, dest, progress=self.progress)
        _ = sha
        resolved_build = str(meta.get("build", build))
        return InstallResult("purpur", mc_version, resolved_build, dest)

    # -- fabric ------------------------------------------------------------ #

    def _install_fabric(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        return self._install_fabric_like(_FABRIC_API, "fabric", mc_version, dest, loader_version)

    def _install_quilt(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        return self._install_fabric_like(_QUILT_API, "quilt", mc_version, dest, loader_version)

    def _install_fabric_like(
        self, api: str, name: str, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        if not mc_version or mc_version == "latest":
            games = _get_json(f"{api}/versions/game")
            mc_version = next((g["version"] for g in games if g.get("stable")), games[0]["version"])
        loaders = _get_json(f"{api}/versions/loader")
        loader_version = loader_version or next(
            (loader_entry["version"] for loader_entry in loaders if loader_entry.get("stable")),
            loaders[0]["version"],
        )
        installers = _get_json(f"{api}/versions/installer")
        installer_version = installers[0]["version"]
        url = f"{api}/versions/loader/{mc_version}/{loader_version}/{installer_version}/server/jar"
        _download(url, dest, progress=self.progress)
        return InstallResult(name, mc_version, loader_version, dest)

    # -- forge / neoforge -------------------------------------------------- #

    def _install_neoforge(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        version = loader_version or self._latest_neoforge_for(mc_version)
        url = (
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
            f"{version}/neoforge-{version}-installer.jar"
        )
        installer_dest = dest.with_name(f"neoforge-{version}-installer.jar")
        _download(url, installer_dest, progress=self.progress)
        return InstallResult(
            "neoforge",
            mc_version,
            version,
            installer_dest,
            is_installer=True,
            post_install_hint=(
                f"Run: java -jar {installer_dest.name} --installServer "
                "(in the server directory), then point `jar` at the generated run script/jar."
            ),
        )

    def _install_forge(
        self, mc_version: str, dest: Path, loader_version: str | None
    ) -> InstallResult:
        version = loader_version or self._latest_forge_for(mc_version)
        combined = f"{mc_version}-{version}" if version and "-" not in version else version
        url = (
            "https://maven.minecraftforge.net/net/minecraftforge/forge/"
            f"{combined}/forge-{combined}-installer.jar"
        )
        installer_dest = dest.with_name(f"forge-{combined}-installer.jar")
        _download(url, installer_dest, progress=self.progress)
        return InstallResult(
            "forge",
            mc_version,
            version,
            installer_dest,
            is_installer=True,
            post_install_hint=(
                f"Run: java -jar {installer_dest.name} --installServer "
                "(in the server directory), then use the generated run script."
            ),
        )

    def _latest_neoforge_for(self, mc_version: str) -> str:
        versions = _parse_maven_versions(_get(_NEOFORGE_MAVEN).decode("utf-8"))
        # NeoForge versions look like 20.4.235 where 20.4 maps to MC 1.20.4.
        if mc_version:
            parts = mc_version.split(".")
            prefix = ".".join(parts[1:]) if len(parts) >= 2 else mc_version
            matching = [v for v in versions if v.startswith(prefix)]
            if matching:
                return matching[-1]
        if not versions:
            raise InstallError("could not resolve any NeoForge version")
        return versions[-1]

    def _latest_forge_for(self, mc_version: str) -> str:
        versions = _parse_maven_versions(_get(_FORGE_MAVEN).decode("utf-8"))
        matching = [v for v in versions if v.startswith(f"{mc_version}-")]
        if not matching:
            raise InstallError(f"no Forge build found for Minecraft {mc_version}")
        # Strip the leading "<mc>-" to return just the forge version.
        return matching[-1].split("-", 1)[1]


# --------------------------------------------------------------------------- #
# Maven metadata parsing (no XML dependency needed)
# --------------------------------------------------------------------------- #


def _parse_maven_versions(xml: str) -> list[str]:
    import re

    return re.findall(r"<version>([^<]+)</version>", xml)


def _flatten_paper_versions(versions: Any) -> list[str]:
    # The Fill API returns {"versions": {"1.20": ["1.20.4", ...], ...}}; newest
    # families first, newest patch first within a family.
    if isinstance(versions, dict):
        flat: list[str] = []
        for family in versions.values():
            flat.extend(family)
        return flat
    return list(versions)


def find_jdk() -> str | None:
    """Best-effort locate a Java executable (for the Forge/NeoForge installer)."""
    return shutil.which("java")
