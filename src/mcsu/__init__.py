"""mcsu — Minecraft Server Utilities.

A cross-platform, dependency-free toolkit for running and babysitting
Minecraft servers across many versions and mod loaders (Vanilla, Paper,
Purpur, Fabric, Forge, NeoForge, Quilt, and Spigot-compatible jars).

The public API is intentionally small; most users interact through the
``mcsu`` command-line interface (see :mod:`mcsu.cli`) or by embedding the
:class:`~mcsu.supervisor.Supervisor` in their own automation.
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Minecraft Server Utilities contributors"
__license__ = "MIT"

from mcsu.errors import (
    ConfigError,
    InstallError,
    McsuError,
    RconError,
    ServerError,
)

__all__ = [
    "ConfigError",
    "InstallError",
    "McsuError",
    "RconError",
    "ServerError",
    "__version__",
]
