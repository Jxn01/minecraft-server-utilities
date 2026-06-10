"""Shared pytest fixtures.

The star fixture is ``fake_java``: a tiny executable that stands in for a real
JVM + server jar. It emits a couple of vanilla-style console lines (including
the "Done" readiness marker), echoes a join, and exits cleanly when it reads
``stop`` on stdin. That lets the process-management and supervisor tests run a
realistic lifecycle in well under a second with no Java or network required.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def fake_java(tmp_path: Path) -> Path:
    """Create an executable that imitates `java -jar server.jar`."""
    script = tmp_path / ("fakejava.py")
    script.write_text(
        textwrap.dedent(
            """
            import sys, time
            # Ignore all JVM/jar args; behave like a minimal server console.
            print("[12:00:00] [Server thread/INFO]: Starting minecraft server")
            print("[12:00:01] [Server thread/INFO]: Done (1.234s)! For help, type \\"help\\"")
            sys.stdout.flush()
            for line in sys.stdin:
                cmd = line.strip()
                if cmd == "stop":
                    print("[12:00:05] [Server thread/INFO]: Stopping the server")
                    sys.stdout.flush()
                    break
                elif cmd == "boom":
                    raise SystemExit(1)
                else:
                    print("[12:00:02] [Server thread/INFO]: Unknown command: " + cmd)
                    sys.stdout.flush()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    if os.name == "nt":
        launcher = tmp_path / "fakejava.bat"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return launcher

    launcher = tmp_path / "fakejava"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    """A server directory containing a dummy jar and a world to back up."""
    d = tmp_path / "server"
    (d / "world").mkdir(parents=True)
    (d / "world" / "level.dat").write_bytes(b"\x00fake nbt data\x00" * 64)
    (d / "world" / "region").mkdir()
    (d / "world" / "region" / "r.0.0.mca").write_bytes(b"chunkdata" * 128)
    (d / "server.jar").write_text("not a real jar", encoding="utf-8")
    return d
