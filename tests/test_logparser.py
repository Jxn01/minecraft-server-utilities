from __future__ import annotations

from mcsu.logparser import LineKind, parse_line


def test_ready_line():
    line = '[12:00:01] [Server thread/INFO]: Done (1.234s)! For help, type "help"'
    parsed = parse_line(line)
    assert parsed.kind is LineKind.SERVER_READY
    assert parsed.timestamp == "12:00:01"
    assert parsed.level == "INFO"


def test_player_join():
    parsed = parse_line("[12:01:00] [Server thread/INFO]: Notch joined the game")
    assert parsed.kind is LineKind.PLAYER_JOIN
    assert parsed.player == "Notch"


def test_player_leave():
    parsed = parse_line("[12:01:00] [Server thread/INFO]: Notch left the game")
    assert parsed.kind is LineKind.PLAYER_LEAVE
    assert parsed.player == "Notch"


def test_chat():
    parsed = parse_line("[12:02:00] [Server thread/INFO]: <Steve> hello world")
    assert parsed.kind is LineKind.CHAT
    assert parsed.player == "Steve"
    assert parsed.message == "hello world"


def test_chat_not_secure_119():
    parsed = parse_line("[12:02:00] [Server thread/INFO]: [Not Secure] <Alex> hi")
    assert parsed.kind is LineKind.CHAT
    assert parsed.player == "Alex"
    assert parsed.message == "hi"


def test_death_message():
    parsed = parse_line("[12:03:00] [Server thread/INFO]: Steve was slain by Zombie")
    assert parsed.kind is LineKind.DEATH
    assert parsed.player == "Steve"


def test_advancement():
    parsed = parse_line(
        "[12:04:00] [Server thread/INFO]: Steve has made the advancement [Stone Age]"
    )
    assert parsed.kind is LineKind.ADVANCEMENT
    assert parsed.player == "Steve"
    assert parsed.message == "Stone Age"


def test_forge_style_prefix_join():
    # Forge/NeoForge can insert an extra bracketed segment after the level.
    line = "[12:05:00] [Server thread/INFO] [minecraft/MinecraftServer]: Bob joined the game"
    parsed = parse_line(line)
    assert parsed.kind is LineKind.PLAYER_JOIN
    assert parsed.player == "Bob"


def test_error_line():
    parsed = parse_line("[12:06:00] [Server thread/ERROR]: Something exploded")
    assert parsed.kind is LineKind.ERROR


def test_warning_line():
    parsed = parse_line("[12:06:00] [Server thread/WARN]: Can't keep up!")
    assert parsed.kind is LineKind.WARNING


def test_stopping_line():
    parsed = parse_line("[12:07:00] [Server thread/INFO]: Stopping the server")
    assert parsed.kind is LineKind.STOPPING


def test_plain_unprefixed():
    parsed = parse_line("\tat java.base/java.lang.Thread.run(Thread.java:840)")
    assert parsed.kind is LineKind.PLAIN


def test_join_message_not_treated_as_chat():
    # A username containing 'join' shouldn't be misread; exact-match guards it.
    parsed = parse_line("[12:08:00] [Server thread/INFO]: notch_join left the game")
    assert parsed.kind is LineKind.PLAYER_LEAVE
    assert parsed.player == "notch_join"


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "line",
    [
        '[12:34:56] [Server thread/INFO]: Done (1.234s)! For help, type "help"',
        "[23:08:52] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer]: "
        'Done (31.130s)! For help, type "help" or "?"',
        "[26May2023 19:14:33.593] [Server thread/INFO] "
        "[net.minecraft.server.dedicated.DedicatedServer/]: "
        'Done (8.158s)! For help, type "help"',
        '[12:34:56] [Server thread/INFO] (Minecraft) Done (3.210s)! For help, type "help"',
        '[16:26:58 INFO]: Done (11.965s)! For help, type "help"',
        '\x1b[0;39m[16:26:58 INFO]: \x1b[0;36mDone (11.965s)! For help, type "help"\x1b[m',
    ],
    ids=["vanilla", "forge-1.12", "forge-1.18", "fabric-logger", "paper", "paper-ansi"],
)
def test_ready_line_in_every_console_format(line: str) -> None:
    assert parse_line(line).kind is LineKind.SERVER_READY


@pytest.mark.parametrize(
    "line",
    [
        "[16:30:01 INFO]: Steve joined the game",
        "\x1b[33m[16:30:01 INFO]: Steve joined the game\x1b[m",
    ],
)
def test_paper_joins_are_seen(line: str) -> None:
    parsed = parse_line(line)
    assert parsed.kind is LineKind.PLAYER_JOIN and parsed.player == "Steve"


def test_paper_levels_are_read() -> None:
    assert parse_line("[16:30:01 ERROR]: Something broke").kind is LineKind.ERROR
    assert parse_line("[16:30:01 WARN]: Careful").kind is LineKind.WARNING
