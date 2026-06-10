"""A minimal, monotonic-clock task scheduler.

Drives the periodic work the original loop hand-rolled with ``seconds %
interval``: backups, scheduled restarts, and watchdog checks. Supports both
fixed intervals and wall-clock daily times, is driven from a single thread,
and uses a monotonic clock so it is immune to system-clock jumps (NTP steps,
DST) for interval tasks.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

Task = Callable[[], None]


@dataclass
class _IntervalJob:
    name: str
    interval: float
    func: Task
    next_run: float  # monotonic deadline


@dataclass
class _DailyJob:
    name: str
    times: list[tuple[int, int]]  # (hour, minute)
    func: Task
    next_run: datetime = field(default=datetime.max)


class Scheduler:
    """Runs registered jobs on a background thread until stopped.

    Jobs must be cheap or offload their own work; a long-running job will delay
    others. The control loop uses this for coarse-grained, minute/hour-scale
    tasks where that trade-off is fine and the code stays simple.
    """

    def __init__(self) -> None:
        self._interval_jobs: list[_IntervalJob] = []
        self._daily_jobs: list[_DailyJob] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._error_handler: Callable[[str, BaseException], None] | None = None

    def on_error(self, handler: Callable[[str, BaseException], None]) -> None:
        self._error_handler = handler

    def every(self, interval_seconds: float, func: Task, *, name: str = "") -> None:
        """Register a job to run every ``interval_seconds`` (0 disables it)."""
        if interval_seconds <= 0:
            return
        with self._lock:
            self._interval_jobs.append(
                _IntervalJob(
                    name=name or func.__name__,
                    interval=interval_seconds,
                    func=func,
                    next_run=time.monotonic() + interval_seconds,
                )
            )

    def daily(self, times: list[str], func: Task, *, name: str = "") -> None:
        """Register a job to run at each wall-clock ``HH:MM`` in ``times``."""
        parsed: list[tuple[int, int]] = []
        for t in times:
            if ":" not in t:
                continue
            hour, minute = t.split(":", 1)
            parsed.append((int(hour), int(minute)))
        if not parsed:
            return
        job = _DailyJob(name=name or func.__name__, times=parsed, func=func)
        job.next_run = _next_daily(parsed, datetime.now())
        with self._lock:
            self._daily_jobs.append(job)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mcsu-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(0.5)

    def _tick(self) -> None:
        now_mono = time.monotonic()
        now_wall = datetime.now()
        with self._lock:
            interval_jobs = list(self._interval_jobs)
            daily_jobs = list(self._daily_jobs)

        for ijob in interval_jobs:
            if now_mono >= ijob.next_run:
                # Schedule the next run *before* executing so a long job does
                # not cause drift compounding, and skip missed slots.
                missed = int((now_mono - ijob.next_run) // ijob.interval) + 1
                ijob.next_run += ijob.interval * missed
                self._safe(ijob.name, ijob.func)

        for djob in daily_jobs:
            if now_wall >= djob.next_run:
                djob.next_run = _next_daily(djob.times, now_wall + timedelta(minutes=1))
                self._safe(djob.name, djob.func)

    def _safe(self, name: str, func: Task) -> None:
        try:
            func()
        except Exception as exc:
            if self._error_handler:
                self._error_handler(name, exc)


def _next_daily(times: list[tuple[int, int]], after: datetime) -> datetime:
    candidates: list[datetime] = []
    for hour, minute in times:
        today = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append(today if today > after else today + timedelta(days=1))
    return min(candidates)
