"""Tests for the per-sensor session statistics tracker."""

from __future__ import annotations

from control_ofc.services.session_stats import SessionStatsTracker


def test_first_reading_sets_min_max_equal():
    tracker = SessionStatsTracker()
    tracker.update("cpu0", 55.0)
    stats = tracker.get("cpu0")
    assert stats is not None
    assert stats.min_c == 55.0
    assert stats.max_c == 55.0


def test_progressive_min_tracks_lowest():
    tracker = SessionStatsTracker()
    for temp in [50.0, 45.0, 48.0, 42.0, 47.0]:
        tracker.update("cpu0", temp)
    stats = tracker.get("cpu0")
    assert stats is not None
    assert stats.min_c == 42.0


def test_progressive_max_tracks_highest():
    tracker = SessionStatsTracker()
    for temp in [50.0, 55.0, 52.0, 60.0, 58.0]:
        tracker.update("cpu0", temp)
    stats = tracker.get("cpu0")
    assert stats is not None
    assert stats.max_c == 60.0


def test_count_increments():
    tracker = SessionStatsTracker()
    for temp in [40.0, 42.0, 44.0]:
        tracker.update("cpu0", temp)
    stats = tracker.get("cpu0")
    assert stats is not None
    assert stats.count == 3


def test_get_unknown_returns_none():
    tracker = SessionStatsTracker()
    assert tracker.get("nonexistent") is None


def test_reset_clears_all():
    tracker = SessionStatsTracker()
    tracker.update("cpu0", 50.0)
    tracker.update("gpu0", 65.0)
    assert tracker.sensor_count == 2
    tracker.reset()
    assert tracker.sensor_count == 0
    assert tracker.get("cpu0") is None
    assert tracker.get("gpu0") is None


def test_update_batch_multiple_sensors():
    tracker = SessionStatsTracker()
    tracker.update_batch([("cpu0", 50.0), ("gpu0", 70.0), ("nvme0", 35.0)])
    assert tracker.sensor_count == 3
    assert tracker.get("cpu0") is not None
    assert tracker.get("cpu0").min_c == 50.0
    assert tracker.get("gpu0").max_c == 70.0
    assert tracker.get("nvme0").count == 1


def test_sensor_count_property():
    tracker = SessionStatsTracker()
    assert tracker.sensor_count == 0
    tracker.update("cpu0", 50.0)
    assert tracker.sensor_count == 1
    tracker.update("gpu0", 65.0)
    assert tracker.sensor_count == 2
    # Updating an existing sensor should not increase count
    tracker.update("cpu0", 52.0)
    assert tracker.sensor_count == 2
