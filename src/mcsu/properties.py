"""Read and edit ``server.properties`` and ``eula.txt``.

``server.properties`` is a Java ``.properties`` file: ``key=value`` lines with
``#`` comments. This module preserves comments and ordering on round-trip,
which matters because operators keep notes in that file.

Encoding follows Minecraft: before 1.20 the server reads and writes the file as
ISO-8859-1, from 1.20 as UTF-8 (falling back to ISO-8859-1). A file is read as
UTF-8 when it decodes as UTF-8, otherwise as ISO-8859-1, and written back in the
encoding it was read in, so lines mcsu does not touch keep their exact bytes.
Values use Java's escapes (``\\uXXXX`` and friends) -- :meth:`Properties.get`
returns them decoded, and :meth:`Properties.set` writes them escaped, as plain
ASCII any Minecraft version reads correctly.
"""

from __future__ import annotations

import logging
import os
import stat
import string
from collections import OrderedDict
from pathlib import Path
from typing import cast

log = logging.getLogger(__name__)


def unescape(raw: str) -> str:
    """Decode a Java ``.properties`` value.

    ``\\uXXXX`` (four hex digits), ``\\t \\n \\r \\f``, and ``\\<c>`` = ``<c>`` for any other ``c``.
    """
    if "\\" not in raw:
        return raw
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\" or i + 1 == len(raw):
            out.append(c)
            i += 1
            continue
        n = raw[i + 1]
        digits = raw[i + 2 : i + 6]
        if n == "u" and len(digits) == 4 and all(d in string.hexdigits for d in digits):
            out.append(chr(int(digits, 16)))
            i += 6
            continue
        # (A malformed \\u -- which Java refuses outright -- is kept as the letter u.)
        out.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(n, n))
        i += 2
    # Characters beyond the BMP arrive as two \\u escapes (a UTF-16 surrogate pair).
    return "".join(out).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def escape(value: str) -> str:
    """Encode a value the way Java's ``Properties.store`` does: printable ASCII only."""
    out: list[str] = []
    for i, c in enumerate(value):
        if c == "\\":
            out.append("\\\\")
        elif c in "\t\n\r\f":
            out.append({"\t": "\\t", "\n": "\\n", "\r": "\\r", "\f": "\\f"}[c])
        elif c in "=:#!" or (c == " " and i == 0):
            out.append("\\" + c)
        elif 0x20 <= ord(c) <= 0x7E:
            out.append(c)
        else:
            units = c.encode("utf-16-be", "surrogatepass")
            for j in range(0, len(units), 2):
                out.append(f"\\u{int.from_bytes(units[j : j + 2], 'big'):04X}")
    return "".join(out)


class Properties:
    """An order- and comment-preserving view of a ``.properties`` file."""

    def __init__(self) -> None:
        # Stored as a list of (kind, payload). kind in {"kv", "raw"}.
        # "kv" payload is (key, value); "raw" payload is the literal line.
        self._lines: list[tuple[str, object]] = []
        self._index: OrderedDict[str, int] = OrderedDict()
        self.encoding = "utf-8"  # what the file was read as; save() writes it back the same way

    # -- construction ------------------------------------------------------ #

    @classmethod
    def loads(cls, text: str) -> Properties:
        props = cls()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!")):
                props._lines.append(("raw", line))
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                props._index[key] = len(props._lines)
                props._lines.append(("kv", (key, value.strip())))
            else:
                props._lines.append(("raw", line))
        return props

    @classmethod
    def load(cls, path: str | Path) -> Properties:
        p = Path(path)
        if not p.is_file():
            return cls()
        data = p.read_bytes()
        try:
            props = cls.loads(data.decode("utf-8"))
        except UnicodeDecodeError:
            props = cls.loads(data.decode("iso-8859-1"))  # Minecraft before 1.20
            props.encoding = "iso-8859-1"
        return props

    # -- access ------------------------------------------------------------ #

    def get(self, key: str, default: str | None = None) -> str | None:
        idx = self._index.get(key)
        if idx is None:
            return default
        _, payload = self._lines[idx]
        return unescape(payload[1])  # type: ignore[index]

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        if value is None:
            return default
        return value.strip().lower() in ("true", "1", "yes", "on")

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def __contains__(self, key: str) -> bool:
        return key in self._index

    def set(self, key: str, value: object) -> None:
        if isinstance(value, bool):
            value = "true" if value else "false"
        value = escape(str(value))
        idx = self._index.get(key)
        if idx is None:
            self._index[key] = len(self._lines)
            self._lines.append(("kv", (key, value)))
        else:
            self._lines[idx] = ("kv", (key, value))

    def as_dict(self) -> dict[str, str]:
        return {
            payload[0]: unescape(payload[1])  # type: ignore[index]
            for kind, payload in self._lines
            if kind == "kv"
        }

    # -- serialization ----------------------------------------------------- #

    def dumps(self) -> str:
        out: list[str] = []
        for kind, payload in self._lines:
            if kind == "raw":
                out.append(str(payload))
            else:
                key, value = cast("tuple[str, str]", payload)
                out.append(f"{key}={value}")
        return "\n".join(out) + "\n"

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.dumps().encode(self.encoding))


def accept_eula(server_dir: str | Path) -> Path:
    """Write ``eula=true`` to ``eula.txt`` (the caller accepts the EULA)."""
    path = Path(server_dir) / "eula.txt"
    path.write_text(
        "# Generated by mcsu — by setting this you agree to the Minecraft EULA\n"
        "# https://aka.ms/MinecraftEULA\n"
        "eula=true\n",
        encoding="utf-8",
    )
    return path


def is_eula_accepted(server_dir: str | Path) -> bool:
    path = Path(server_dir) / "eula.txt"
    if not path.is_file():
        return False
    return Properties.load(path).get_bool("eula", False)


def ensure_rcon_settings(
    server_dir: str | Path,
    *,
    port: int,
    password: str,
) -> bool:
    """Enable RCON in ``server.properties`` if not already configured.

    Returns ``True`` if the file was modified. Leaves an existing matching
    configuration untouched so we never churn an operator's file needlessly.
    """
    path = Path(server_dir) / "server.properties"
    props = Properties.load(path)
    changed = False
    desired = {
        "enable-rcon": "true",
        "rcon.port": str(port),
        "rcon.password": password,
    }
    for key, value in desired.items():
        if props.get(key) != value:
            props.set(key, value)
            changed = True
    if changed:
        props.save(path)
    _protect_secret(path, written=changed)
    return changed


def _protect_secret(path: Path, *, written: bool) -> None:
    """The file now holds the RCON password: owner-only (0600) when mcsu just wrote it, and in
    any case not readable by others. POSIX only -- Windows ACLs are left alone."""
    if os.name == "nt" or not path.is_file():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    wanted = 0o600 if written else mode & ~0o007
    if wanted != mode:
        try:
            path.chmod(wanted)
        except OSError as exc:  # e.g. owned by another user
            log.warning("Could not restrict %s (it holds the RCON password): %s", path, exc)
