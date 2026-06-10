"""Parse Minecraft server console lines into structured events.

Vanilla, Paper, Purpur, Fabric, Forge, NeoForge, and Quilt all share the
vanilla logging backbone (``[HH:MM:SS] [Thread/LEVEL]: message``), so a single
set of tolerant patterns covers them. Where loaders differ (Forge prefixes
some lines, Fabric/Quilt add their own threads) the patterns are written
loosely enough to still match the payload we care about.

The parser is intentionally stateless and side-effect free: feed it a line,
get back a :class:`ParsedLine`. Stateful aggregation (who is online, play
time) lives in :mod:`mcsu.players`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class LineKind(StrEnum):
    PLAIN = "plain"
    SERVER_READY = "server_ready"
    PLAYER_JOIN = "player_join"
    PLAYER_LEAVE = "player_leave"
    CHAT = "chat"
    DEATH = "death"
    ADVANCEMENT = "advancement"
    RCON_READY = "rcon_ready"
    WARNING = "warning"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass(slots=True)
class ParsedLine:
    kind: LineKind
    raw: str
    timestamp: str | None = None
    level: str | None = None
    player: str | None = None
    message: str | None = None


# Leading "[12:34:56] [Server thread/INFO]:" style prefix. Forge/NeoForge may
# insert an extra "[modid/]" segment; we capture time + level loosely.
_PREFIX = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\]\s*"
    r"\[(?P<thread>[^/\]]+)/(?P<level>[A-Z]+)\]"
    r"(?:\s*\[[^\]]*\])?:\s*(?P<body>.*)$"
)

# "Done (12.345s)! For help, type "help"" — emitted once the world is loaded.
_READY = re.compile(r"Done \([0-9.,]+s\)!")

# RCON listener started — useful to know when it's safe to connect.
_RCON_READY = re.compile(r"RCON running on|Thread RCON Listener")

# "Steve joined the game" / "Steve left the game"
_JOIN = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) joined the game$")
_LEAVE = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) left the game$")

# Chat: "<Steve> hello" (vanilla) or "[Not Secure] <Steve> hi" (1.19+).
_CHAT = re.compile(r"^(?:\[Not Secure\]\s*)?<(?P<player>[A-Za-z0-9_]{1,16})>\s*(?P<message>.*)$")

# Server shutting down.
_STOPPING = re.compile(r"Stopping (the )?server")

# A compact set of vanilla death-message verbs. We match "<player> <verb> ..."
# without trying to enumerate every death string; false positives are avoided
# by requiring a known death keyword.
_DEATH_KEYWORDS = (
    "was slain",
    "was shot",
    "was killed",
    "was blown up",
    "was fireballed",
    "was pricked",
    "drowned",
    "blew up",
    "hit the ground too hard",
    "fell from a high place",
    "fell out of the world",
    "burned to death",
    "was burned to a crisp",
    "tried to swim in lava",
    "went up in flames",
    "suffocated",
    "starved to death",
    "was squashed",
    "was poked to death",
    "froze to death",
    "withered away",
    "was struck by lightning",
    "discovered the floor was lava",
    "experienced kinetic energy",
    "was impaled",
    "was squished",
    "didn't want to live",
)
_DEATH_VERBS = "|".join(re.escape(k) for k in _DEATH_KEYWORDS)
_DEATH_RE = re.compile(r"^(?P<player>[A-Za-z0-9_]{1,16}) (?P<rest>(?:" + _DEATH_VERBS + r").*)$")

# "Steve has made the advancement [Stone Age]" / completed the challenge / reached the goal
_ADVANCEMENT = re.compile(
    r"^(?P<player>[A-Za-z0-9_]{1,16}) has "
    r"(?:made the advancement|completed the challenge|reached the goal) "
    r"\[(?P<title>[^\]]+)\]$"
)


def parse_line(raw: str) -> ParsedLine:
    """Classify a single console line into a :class:`ParsedLine`."""
    line = raw.rstrip("\r\n")
    match = _PREFIX.match(line)
    if not match:
        # Lines without the standard prefix (stack traces, loader banners).
        return ParsedLine(kind=LineKind.PLAIN, raw=line)

    time = match.group("time")
    level = match.group("level")
    body = match.group("body")

    def make(kind: LineKind, **kw: object) -> ParsedLine:
        return ParsedLine(kind=kind, raw=line, timestamp=time, level=level, **kw)  # type: ignore[arg-type]

    if _READY.search(body):
        return make(LineKind.SERVER_READY, message=body)
    if _RCON_READY.search(body):
        return make(LineKind.RCON_READY, message=body)
    if _STOPPING.search(body):
        return make(LineKind.STOPPING, message=body)

    m = _JOIN.match(body)
    if m:
        return make(LineKind.PLAYER_JOIN, player=m.group("player"), message=body)
    m = _LEAVE.match(body)
    if m:
        return make(LineKind.PLAYER_LEAVE, player=m.group("player"), message=body)
    m = _ADVANCEMENT.match(body)
    if m:
        return make(LineKind.ADVANCEMENT, player=m.group("player"), message=m.group("title"))
    m = _CHAT.match(body)
    if m:
        return make(LineKind.CHAT, player=m.group("player"), message=m.group("message"))
    m = _DEATH_RE.match(body)
    if m:
        return make(LineKind.DEATH, player=m.group("player"), message=body)

    if level in ("ERROR", "FATAL"):
        return make(LineKind.ERROR, message=body)
    if level == "WARN":
        return make(LineKind.WARNING, message=body)
    return make(LineKind.PLAIN, message=body)
