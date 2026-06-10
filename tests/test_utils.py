from __future__ import annotations

from datetime import datetime

import pytest

from mcsu.utils import (
    colorize,
    format_bytes,
    format_duration,
    human_join,
    parse_duration,
    parse_timestamp_slug,
    timestamp_slug,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("240s", 240),
        ("1h", 3600),
        ("1h30m", 5400),
        ("2d", 172800),
        ("1w", 604800),
        ("90", 90),
        (3600, 3600),
        (45.0, 45),
        ("1h 30m 15s", 5415),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "10x", "  "])
def test_parse_duration_invalid(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (59, "59s"), (60, "1m"), (3661, "1h1m1s"), (90000, "1d1h")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(1024 * 1024 * 5) == "5.0 MiB"


def test_timestamp_slug_roundtrip():
    when = datetime(2026, 6, 10, 14, 30, 5)
    slug = timestamp_slug(when)
    assert slug == "2026-06-10_14-30-05"
    assert parse_timestamp_slug(slug) == when


def test_parse_timestamp_slug_invalid():
    assert parse_timestamp_slug("not-a-timestamp") is None


def test_colorize_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert colorize("hi", "red") == "hi"


def test_colorize_forced(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("MCSU_FORCE_COLOR", "1")
    out = colorize("hi", "red")
    assert out.startswith("\033[31m") and out.endswith("\033[0m")


def test_human_join():
    assert human_join([]) == ""
    assert human_join(["a"]) == "a"
    assert human_join(["a", "b"]) == "a and b"
    assert human_join(["a", "b", "c"]) == "a, b, and c"
