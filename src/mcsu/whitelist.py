"""Whitelist management that works whether or not the server is running.

A running server is told over RCON (``whitelist add <name>``) and resolves the
player's UUID itself. A *stopped* server only has its ``whitelist.json`` file,
and that file is keyed by UUID -- so mcsu has to compute the same UUID the
server would have:

* **online mode** (``online-mode=true``): the player's Mojang account UUID,
  looked up from Mojang's public profile API;
* **offline mode**: ``UUID.nameUUIDFromBytes("OfflinePlayer:" + name)``, a
  version-3 (MD5) UUID derived from the name alone -- reproduced here exactly.

Writing the wrong kind of UUID would silently lock the player out, so the
server's ``online-mode`` property decides which one is used.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from mcsu.errors import McsuError
from mcsu.properties import Properties

MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"
WHITELIST_FILE = "whitelist.json"

# name -> dashed UUID string, or None when the account does not exist
Resolver = Callable[[str], "str | None"]


class WhitelistError(McsuError):
    """Raised when a whitelist change cannot be applied."""


def offline_uuid(name: str) -> str:
    """The UUID an offline-mode server assigns to ``name`` (Java's nameUUIDFromBytes)."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # IETF variant
    return str(uuid.UUID(bytes=bytes(digest)))


def mojang_uuid(name: str, *, timeout: float = 10.0) -> str | None:
    """Look up a Java Edition account's UUID; ``None`` if no such account exists."""
    url = MOJANG_PROFILE_URL.format(name=urllib.parse.quote(name))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # a fixed https:// URL
            if resp.status == 204:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (204, 404):
            return None
        raise WhitelistError(f"Mojang profile lookup for {name!r} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise WhitelistError(f"Mojang profile lookup for {name!r} failed: {exc}") from exc
    raw = data.get("id", "")
    return str(uuid.UUID(raw)) if raw else None


def is_online_mode(server_dir: str | Path) -> bool:
    """Read ``online-mode`` from server.properties (Minecraft's default is true)."""
    path = Path(server_dir) / "server.properties"
    if not path.is_file():
        return True
    return Properties.load(path).get_bool("online-mode", True)


def _load(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except ValueError as exc:
        raise WhitelistError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise WhitelistError(f"{path} must contain a JSON list")
    return data


def _save(path: Path, entries: list[dict[str, str]]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def whitelist_names(server_dir: str | Path) -> list[str]:
    return [e.get("name", "") for e in _load(Path(server_dir) / WHITELIST_FILE)]


def add_to_file(server_dir: str | Path, name: str, *, resolver: Resolver | None = None) -> bool:
    """Add ``name`` to a (stopped) server's whitelist.json. Returns False if already present."""
    path = Path(server_dir) / WHITELIST_FILE
    entries = _load(path)
    if any(e.get("name", "").lower() == name.lower() for e in entries):
        return False
    if is_online_mode(server_dir):
        player_uuid = (resolver or mojang_uuid)(name)
        if player_uuid is None:
            raise WhitelistError(f"no Minecraft Java account is named {name!r}")
    else:
        player_uuid = offline_uuid(name)
    entries.append({"uuid": player_uuid, "name": name})
    _save(path, entries)
    return True


def remove_from_file(server_dir: str | Path, name: str) -> bool:
    """Remove ``name`` (case-insensitively) from whitelist.json. Returns False if absent."""
    path = Path(server_dir) / WHITELIST_FILE
    entries = _load(path)
    kept = [e for e in entries if e.get("name", "").lower() != name.lower()]
    if len(kept) == len(entries):
        return False
    _save(path, kept)
    return True


def valid_player_name(name: str) -> bool:
    """Java Edition names: 3-16 characters, letters, digits and underscore."""
    return 3 <= len(name) <= 16 and all(c.isascii() and (c.isalnum() or c == "_") for c in name)
