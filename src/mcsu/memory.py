"""Whether a server's Java heap fits the host's available memory before it starts.

A JVM started with ``-XX:+AlwaysPreTouch`` claims its whole heap at launch, so a host that is
short of memory feels it at once, and the kernel then kills whatever it picks. The fleet checks
first and refuses a start that does not fit, with the numbers and an optional hint
(``[fleet] low_memory_hint``). Linux only: where ``/proc/meminfo`` does not exist the check is
skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

from mcsu.errors import ConfigError

_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_SIZE = re.compile(r"^(\d+)([kKmMgGtT]?)$")


def parse_size(text: str) -> int:
    """Bytes in a Java-style size: ``7G``, ``3072M``, ``512K``, ``1T`` or plain bytes."""
    m = _SIZE.match(text)
    if not m:
        raise ConfigError(f"not a memory size: {text!r} (examples: 3G, 3072M)")
    return int(m.group(1)) * _UNITS[m.group(2).lower()]


def available_bytes(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    """The kernel's ``MemAvailable`` in bytes, or None where it cannot be read."""
    try:
        text = meminfo.read_text()
    except OSError:
        return None
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", text, re.M)
    return int(m.group(1)) * 1024 if m else None


def _gib(n: int) -> str:
    return f"{n / 1024**3:.1f} GiB"


def check_fits(heap: int, available: int | None, margin: int, freed: int = 0) -> str | None:
    """None when ``heap + margin`` fits in ``available + freed`` (``freed``: the heap of a server
    about to stop for this one), or when nothing could be read; otherwise the reason, in numbers."""
    if available is None:
        return None
    have = available + freed
    if heap + margin <= have:
        return None
    extra = f" (with {_gib(freed)} from the server that would stop)" if freed else ""
    return f"needs {_gib(heap)} + {_gib(margin)} margin, {_gib(have)} available{extra}"
