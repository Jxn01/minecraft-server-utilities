"""Cross-platform Minecraft server process management.

Rather than wrapping a terminal multiplexer like ``screen`` (Linux/macOS only
and awkward to script against), this owns the Java process directly via
:mod:`subprocess`: it captures the console, lets you push commands to stdin,
detects readiness and exit, and shuts the server down gracefully (``stop``
then escalating to terminate/kill) on every platform.

Console output is fanned out to registered callbacks on a reader thread, which
the supervisor uses to drive log parsing, the event bus, and live console
mirroring — without ever blocking the control loop.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from mcsu.errors import ServerError
from mcsu.utils import IS_WINDOWS, find_java

LineCallback = Callable[[str], None]


class ServerProcess:
    """Owns a single Java server subprocess and its console I/O."""

    def __init__(
        self,
        *,
        server_dir: str | Path,
        jar: str,
        java_path: str | None = None,
        min_memory: str = "2G",
        max_memory: str = "4G",
        extra_flags: list[str] | None = None,
        server_args: list[str] | None = None,
        stop_timeout: int = 90,
    ) -> None:
        self.server_dir = Path(server_dir).resolve()
        self.jar = jar
        self.java_path = find_java(java_path)
        self.min_memory = min_memory
        self.max_memory = max_memory
        self.extra_flags = list(extra_flags or [])
        self.server_args = list(server_args or ["nogui"])
        self.stop_timeout = stop_timeout

        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._callbacks: list[LineCallback] = []
        self._lock = threading.RLock()
        self._stdin_lock = threading.Lock()

    # -- command line ------------------------------------------------------ #

    def build_command(self) -> list[str]:
        cmd = [self.java_path, f"-Xms{self.min_memory}", f"-Xmx{self.max_memory}"]
        cmd.extend(self.extra_flags)
        cmd.extend(["-jar", self.jar])
        cmd.extend(self.server_args)
        return cmd

    # -- console subscribers ----------------------------------------------- #

    def add_line_callback(self, callback: LineCallback) -> None:
        self._callbacks.append(callback)

    # -- lifecycle --------------------------------------------------------- #

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        with self._lock:
            return self._proc.poll() if self._proc else None

    def start(self) -> None:
        """Launch the server. Raises :class:`ServerError` on misconfiguration."""
        with self._lock:
            if self.is_running():
                raise ServerError("server is already running")
            jar_path = self.server_dir / self.jar
            if not jar_path.is_file():
                raise ServerError(f"server jar not found: {jar_path}. Run `mcsu install` first.")
            popen_kwargs: dict[str, object] = {
                "cwd": str(self.server_dir),
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "encoding": "utf-8",
                "errors": "replace",
            }
            # Put the child in its own process group/job so we can signal the
            # whole JVM tree and so Ctrl-C to the supervisor isn't forwarded
            # to the child prematurely.
            if IS_WINDOWS:
                # CREATE_NEW_PROCESS_GROUP only exists on Windows.
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            else:
                popen_kwargs["start_new_session"] = True
            try:
                self._proc = subprocess.Popen(  # type: ignore[call-overload]
                    self.build_command(), **popen_kwargs
                )
            except (OSError, ValueError) as exc:
                raise ServerError(f"failed to launch server: {exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name="mcsu-console", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.rstrip("\n")
            for callback in list(self._callbacks):
                try:
                    callback(line)
                except Exception:
                    pass

    # -- input ------------------------------------------------------------- #

    def send(self, command: str) -> None:
        """Write a command to the server's stdin (newline appended)."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise ServerError("server is not running")
        with self._stdin_lock:
            try:
                proc.stdin.write(command.rstrip("\n") + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise ServerError(f"failed to send command: {exc}") from exc

    # -- shutdown ---------------------------------------------------------- #

    def stop(self, *, timeout: int | None = None) -> int | None:
        """Gracefully stop the server, escalating if it ignores ``stop``.

        Sends the ``stop`` console command, waits up to ``timeout`` seconds,
        then terminates and finally kills the process group. Returns the
        process exit code (or ``None`` if it was never running).
        """
        timeout = self.stop_timeout if timeout is None else timeout
        with self._lock:
            proc = self._proc
            if proc is None:
                return None
            if proc.poll() is not None:
                return proc.returncode

        # 1) Polite: ask the server to stop and save.
        try:
            self.send("stop")
        except ServerError:
            pass
        if self._wait(proc, timeout):
            return proc.returncode

        # 2) Firm: signal terminate to the process group.
        self._signal_group(proc, terminate=True)
        if self._wait(proc, 15):
            return proc.returncode

        # 3) Final: kill.
        self._signal_group(proc, terminate=False)
        self._wait(proc, 10)
        return proc.returncode

    def kill(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            self._signal_group(proc, terminate=False)

    def wait(self, timeout: float | None = None) -> int | None:
        with self._lock:
            proc = self._proc
        if proc is None:
            return None
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def _wait(self, proc: subprocess.Popen[str], timeout: float) -> bool:
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _signal_group(self, proc: subprocess.Popen[str], *, terminate: bool) -> None:
        try:
            if IS_WINDOWS:
                if terminate:
                    proc.terminate()
                else:
                    proc.kill()
                return
            # POSIX: signal the whole process group created via start_new_session.
            sig = signal.SIGTERM if terminate else signal.SIGKILL
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except ProcessLookupError:
                pass
        except OSError:
            # Fall back to the single-process API.
            try:
                proc.terminate() if terminate else proc.kill()
            except OSError:
                pass


def wait_for_ready(
    process: ServerProcess,
    *,
    ready_event: threading.Event,
    timeout: float = 300.0,
) -> bool:
    """Block until ``ready_event`` is set or the server exits/time runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_event.wait(0.25):
            return True
        if not process.is_running():
            return False
    return False
