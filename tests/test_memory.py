"""mcsu.memory: whether a server's heap fits the host's available memory before it starts."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcsu import memory
from mcsu.errors import ConfigError

GIB = 1024**3


def test_parse_size_units() -> None:
    assert memory.parse_size("7G") == 7 * GIB
    assert memory.parse_size("3g") == 3 * GIB
    assert memory.parse_size("3072M") == 3 * GIB
    assert memory.parse_size("512K") == 512 * 1024
    assert memory.parse_size("1T") == 1024 * GIB
    assert memory.parse_size("1048576") == 1048576


@pytest.mark.parametrize("bad", ["", "G", "7GB", "-1G", "1.5G", "seven", " 7G"])
def test_parse_size_refuses_garbage(bad: str) -> None:
    with pytest.raises(ConfigError):
        memory.parse_size(bad)


def test_available_reads_memavailable(tmp_path: Path) -> None:
    f = tmp_path / "meminfo"
    f.write_text(
        "MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    9000000 kB\n"
    )
    assert memory.available_bytes(f) == 9000000 * 1024


def test_available_is_none_without_proc(tmp_path: Path) -> None:
    assert memory.available_bytes(tmp_path / "missing") is None
    (tmp_path / "odd").write_text("MemTotal: 1 kB\n")
    assert memory.available_bytes(tmp_path / "odd") is None


def test_fits() -> None:
    assert memory.check_fits(3 * GIB, 5 * GIB, 1 * GIB) is None
    assert memory.check_fits(7 * GIB, None, 1 * GIB) is None  # nothing to read: no check


def test_does_not_fit_names_the_numbers() -> None:
    reason = memory.check_fits(7 * GIB, 5 * GIB, 1 * GIB)
    assert reason is not None
    assert "7.0 GiB" in reason and "1.0 GiB" in reason and "5.0 GiB" in reason


def test_switching_counts_the_server_that_stops() -> None:
    assert memory.check_fits(4 * GIB, 1 * GIB, 1 * GIB, freed=4 * GIB) is None
    assert memory.check_fits(4 * GIB, 1 * GIB, 1 * GIB, freed=3 * GIB) is not None
