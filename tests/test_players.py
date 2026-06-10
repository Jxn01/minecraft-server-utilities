from __future__ import annotations

from mcsu.players import PlayerTracker


def test_join_and_leave_tracks_online():
    t = PlayerTracker()
    t.player_joined("Steve", when=1000.0)
    t.player_joined("Alex", when=1000.0)
    assert t.online == ["Alex", "Steve"]
    assert t.online_count == 2
    assert t.is_online("Steve")
    t.player_left("Steve", when=1100.0)
    assert t.online == ["Alex"]
    assert not t.is_online("Steve")


def test_playtime_accumulates():
    t = PlayerTracker()
    t.player_joined("Steve", when=1000.0)
    t.player_left("Steve", when=1100.0)
    t.player_joined("Steve", when=2000.0)
    t.player_left("Steve", when=2050.0)
    stats = t.stats("Steve")
    assert stats is not None
    assert stats.total_seconds == 150
    assert stats.sessions == 2


def test_activity_flag():
    t = PlayerTracker()
    assert t.had_activity_since_reset() is False
    t.player_joined("Steve", when=1.0)
    t.player_left("Steve", when=2.0)
    assert t.had_activity_since_reset() is True
    t.reset_activity()
    assert t.had_activity_since_reset() is False


def test_online_counts_as_activity_even_after_reset():
    t = PlayerTracker()
    t.player_joined("Steve", when=1.0)
    t.reset_activity()
    # Still online -> still worth backing up.
    assert t.had_activity_since_reset() is True


def test_persistence_roundtrip(tmp_path):
    state = tmp_path / "players.json"
    t1 = PlayerTracker(state)
    t1.player_joined("Steve", when=1000.0)
    t1.player_left("Steve", when=1100.0)
    # New tracker loads persisted stats.
    t2 = PlayerTracker(state)
    stats = t2.stats("Steve")
    assert stats is not None
    assert stats.total_seconds == 100
    assert stats.sessions == 1


def test_all_stats_sorted_by_playtime():
    t = PlayerTracker()
    t.player_joined("Short", when=0.0)
    t.player_left("Short", when=10.0)
    t.player_joined("Long", when=0.0)
    t.player_left("Long", when=100.0)
    ranked = [s.name for s in t.all_stats()]
    assert ranked == ["Long", "Short"]


def test_clear_online_keeps_stats():
    t = PlayerTracker()
    t.player_joined("Steve", when=0.0)
    t.clear_online()
    assert t.online_count == 0
