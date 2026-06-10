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
