"""Runtime state shared between the running supervisor and one-shot commands.

A small JSON file in ``<server>/.mcsu/state.json`` lets ``mcsu status`` and
``mcsu stop`` discover a supervisor started by ``mcsu run`` (possibly in
another terminal or under systemd/nssm) and report on it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_FILENAME = "state.json"


@dataclass(slots=True)
class RuntimeState:
    supervisor_pid: int = 0
    server_pid: int = 0
    status: str = "stopped"  # starting|running|stopping|stopped|crashed
    started_at: float = 0.0
    ready_at: float = 0.0
    restarts: int = 0
    last_backup: str = ""
    mc_version: str = ""
    loader: str = ""
    extra: dict[str, object] = field(default_factory=dict)


class StateStore:
    """Atomic reader/writer for the runtime state file."""

    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / STATE_FILENAME

    def read(self) -> RuntimeState | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        known = set(RuntimeState.__slots__)  # type: ignore[attr-defined]
        return RuntimeState(**{k: v for k, v in data.items() if k in known})

    def write(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def write_control(state_dir: str | Path, command: str) -> None:
    """Drop a control command (``stop``/``restart``/``backup``) for a supervisor."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "control").write_text(command, encoding="utf-8")


def pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if process:
            ctypes.windll.kernel32.CloseHandle(process)  # type: ignore[attr-defined]
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
