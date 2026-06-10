"""Exception hierarchy for mcsu.

All exceptions raised deliberately by the package derive from
:class:`McsuError`, so callers can catch the whole family with a single
``except`` clause while still being able to discriminate on subtype.
"""

from __future__ import annotations


class McsuError(Exception):
    """Base class for every error raised by mcsu."""


class ConfigError(McsuError):
    """Raised when a configuration file is missing, malformed, or invalid."""


class RconError(McsuError):
    """Raised for RCON connection, authentication, or protocol failures."""


class ServerError(McsuError):
    """Raised when the managed server process cannot be controlled."""


class InstallError(McsuError):
    """Raised when a server jar cannot be resolved or downloaded."""


class BackupError(McsuError):
    """Raised when a backup or restore operation fails."""
