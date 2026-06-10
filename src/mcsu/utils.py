"""Small, dependency-free helpers shared across the package.

Everything here is pure-Python standard library so the suite runs
unchanged on Windows, Linux, and macOS.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta

# --------------------------------------------------------------------------- #
# Platform helpers
# --------------------------------------------------------------------------- #

IS_WINDOWS = os.name == "nt"
IS_POSIX = os.name == "posix"


def platform_summary() -> str:
    """Return a short, human-readable description of the host platform."""
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def find_java(explicit: str | None = None) -> str:
    """Resolve the Java executable to use.

    Resolution order: an explicit path, the ``JAVA_HOME`` environment
    variable, then ``java`` on ``PATH``. The returned value is always a
    string suitable for :func:`subprocess.Popen`; it is *not* validated to
    exist so callers can surface a clear error at launch time.
    """
    if explicit:
        return explicit
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = os.path.join(java_home, "bin", "java.exe" if IS_WINDOWS else "java")
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("java") or "java"


# --------------------------------------------------------------------------- #
# Time / formatting helpers
# --------------------------------------------------------------------------- #

_DURATION_UNITS = {
    "w": 604800,
    "d": 86400,
    "h": 3600,
    "m": 60,
    "s": 1,
}
_DURATION_RE = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)


def parse_duration(value: str | int | float) -> int:
    """Parse a human duration such as ``"1h30m"`` or ``"240s"`` to seconds.

    Plain numbers (``int``/``float`` or a bare numeric string) are treated as
    seconds. Raises :class:`ValueError` for anything that cannot be parsed.
    """
    if isinstance(value, (int, float)):
        return int(value)
    text = value.strip().lower()
    if not text:
        raise ValueError("empty duration")
    if text.isdigit():
        return int(text)
    total = 0
    matched = 0
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _DURATION_UNITS[unit]
        matched += 1
    if matched == 0:
        raise ValueError(f"could not parse duration: {value!r}")
    return total


def format_duration(seconds: float) -> str:
    """Render a number of seconds as a compact ``2h5m`` style string."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    parts: list[str] = []
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            qty, seconds = divmod(seconds, size)
            parts.append(f"{qty}{unit}")
    return "".join(parts)


def format_bytes(num: float) -> str:
    """Render a byte count using binary units (KiB, MiB, ...)."""
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(num) < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} EiB"


def utcnow() -> datetime:
    """Timezone-aware current time in UTC (avoids naive-datetime pitfalls)."""
    return datetime.now(UTC)


def timestamp_slug(when: datetime | None = None) -> str:
    """Return a filesystem-safe timestamp like ``2026-06-10_14-30-05``."""
    when = when or datetime.now()
    return when.strftime("%Y-%m-%d_%H-%M-%S")


def parse_timestamp_slug(slug: str) -> datetime | None:
    """Inverse of :func:`timestamp_slug`; returns ``None`` if it doesn't match."""
    try:
        return datetime.strptime(slug, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def humanize_age(when: datetime, *, now: datetime | None = None) -> str:
    """Describe how long ago ``when`` was, e.g. ``"3m ago"`` or ``"just now"``."""
    now = now or datetime.now(tz=when.tzinfo)
    delta: timedelta = now - when
    secs = int(delta.total_seconds())
    if secs < 5:
        return "just now"
    if secs < 0:
        return "in the future"
    return f"{format_duration(secs)} ago"


# --------------------------------------------------------------------------- #
# Terminal output helpers
# --------------------------------------------------------------------------- #

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("MCSU_FORCE_COLOR") is not None:
        return True
    return sys.stdout.isatty()


def colorize(text: str, *styles: str) -> str:
    """Wrap ``text`` in ANSI styles, respecting ``NO_COLOR`` and non-TTYs."""
    if not styles or not _color_enabled():
        return text
    prefix = "".join(_COLORS.get(s, "") for s in styles)
    return f"{prefix}{text}{_COLORS['reset']}"


def human_join(items: list[str], conjunction: str = "and") -> str:
    """Join a list into ``"a, b, and c"`` style prose."""
    items = [str(i) for i in items]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return ", ".join(items[:-1]) + f", {conjunction} {items[-1]}"
