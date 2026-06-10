from __future__ import annotations

import threading
import time
from datetime import datetime

from mcsu.events import Event, EventBus, EventType
from mcsu.scheduler import Scheduler, _next_daily

# --------------------------------------------------------------------------- #
# EventBus
# --------------------------------------------------------------------------- #


def test_event_bus_targeted_delivery():
    bus = EventBus()
    seen = []
    bus.subscribe(EventType.PLAYER_JOIN, lambda e: seen.append(e.get("player")))
    bus.emit(EventType.PLAYER_JOIN, "joined", player="Steve")
    bus.emit(EventType.PLAYER_LEAVE, "left", player="Alex")
    assert seen == ["Steve"]


def test_event_bus_global_listener():
    bus = EventBus()
    count = []
    bus.subscribe_all(lambda e: count.append(e.type))
    bus.emit(EventType.INFO, "x")
    bus.emit(EventType.SERVER_READY, "y")
    assert count == [EventType.INFO, EventType.SERVER_READY]


def test_event_bus_isolates_listener_errors():
    bus = EventBus()
    errors = []
    bus.on_listener_error(lambda exc, ev: errors.append(exc))
    good_calls = []
    bus.subscribe(EventType.INFO, lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(EventType.INFO, lambda e: good_calls.append(1))
    bus.emit(EventType.INFO, "x")
    assert len(errors) == 1
    assert good_calls == [1]  # second listener still ran


def test_unsubscribe():
    bus = EventBus()
    seen = []
    listener = lambda e: seen.append(1)  # noqa: E731
    bus.subscribe(EventType.INFO, listener)
    bus.unsubscribe(EventType.INFO, listener)
    bus.emit(EventType.INFO, "x")
    assert seen == []


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #


def test_scheduler_runs_interval_job():
    sched = Scheduler()
    event = threading.Event()
    # The scheduler ticks every 0.5s; an interval below that fires on first tick.
    sched.every(0.01, event.set, name="fast")
    sched.start()
    try:
        assert event.wait(2.0), "interval job did not run"
    finally:
        sched.stop()


def test_scheduler_zero_interval_disabled():
    sched = Scheduler()
    calls = []
    sched.every(0, lambda: calls.append(1))
    sched.start()
    time.sleep(0.6)
    sched.stop()
    assert calls == []


def test_scheduler_error_handler():
    sched = Scheduler()
    errors = []
    sched.on_error(lambda name, exc: errors.append((name, str(exc))))
    sched.every(0.01, lambda: (_ for _ in ()).throw(ValueError("nope")), name="bad")
    sched.start()
    time.sleep(1.0)
    sched.stop()
    assert errors and errors[0][0] == "bad"


def test_next_daily_picks_today_then_tomorrow():
    now = datetime(2026, 6, 10, 3, 0, 0)
    # 04:00 today is still ahead.
    assert _next_daily([(4, 0)], now) == datetime(2026, 6, 10, 4, 0, 0)
    # 02:00 already passed -> tomorrow.
    assert _next_daily([(2, 0)], now) == datetime(2026, 6, 11, 2, 0, 0)
    # Multiple times -> earliest upcoming.
    assert _next_daily([(2, 0), (4, 0), (23, 0)], now) == datetime(2026, 6, 10, 4, 0, 0)


def test_event_dataclass_get():
    ev = Event(type=EventType.INFO, message="hi", data={"k": "v"})
    assert ev.get("k") == "v"
    assert ev.get("missing", "default") == "default"
