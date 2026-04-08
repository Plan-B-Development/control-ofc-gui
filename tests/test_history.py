"""Tests for the history store."""

from __future__ import annotations

import time

from onlyfans.api.models import FanReading, HistoryPoint, SensorReading
from onlyfans.services.history_store import HistoryStore


def test_record_sensors():
    store = HistoryStore()
    sensors = [SensorReading(id="cpu", kind="CpuTemp", value_c=45.0)]
    store.record_sensors(sensors)
    series = store.get_series("sensor:cpu")
    assert len(series) == 1
    assert series[0].value == 45.0


def test_record_fans_rpm_and_pwm():
    store = HistoryStore()
    fans = [FanReading(id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40)]
    store.record_fans(fans)
    assert len(store.get_series("fan:openfan:ch00:rpm")) == 1
    assert len(store.get_series("fan:openfan:ch00:pwm")) == 1


def test_record_fan_no_rpm():
    store = HistoryStore()
    fans = [FanReading(id="hwmon:test", source="hwmon")]
    store.record_fans(fans)
    assert store.get_series("fan:hwmon:test:rpm") == []


def test_multiple_recordings():
    store = HistoryStore()
    for temp in [40.0, 42.0, 44.0]:
        store.record_sensors([SensorReading(id="cpu", value_c=temp)])
    series = store.get_series("sensor:cpu")
    assert len(series) == 3
    assert [r.value for r in series] == [40.0, 42.0, 44.0]


def test_clear():
    store = HistoryStore()
    store.record_sensors([SensorReading(id="cpu", value_c=45.0)])
    store.clear()
    assert store.get_series("sensor:cpu") == []


def test_series_keys():
    store = HistoryStore()
    store.record_sensors([SensorReading(id="a"), SensorReading(id="b")])
    keys = store.series_keys()
    assert "sensor:a" in keys
    assert "sensor:b" in keys


def test_prefill_sensor_populates_series():
    """Prefilling from daemon history should create entries."""
    store = HistoryStore()
    now_ms = int(time.time() * 1000)
    points = [
        HistoryPoint(ts=now_ms - 3000, v=40.0),
        HistoryPoint(ts=now_ms - 2000, v=42.0),
        HistoryPoint(ts=now_ms - 1000, v=44.0),
    ]
    store.prefill_sensor("cpu", points)
    series = store.get_series("sensor:cpu")
    assert len(series) == 3
    assert [r.value for r in series] == [40.0, 42.0, 44.0]


def test_prefill_sensor_empty_points_is_noop():
    store = HistoryStore()
    store.prefill_sensor("cpu", [])
    assert store.get_series("sensor:cpu") == []


def test_prefill_sensor_old_points_pruned():
    """Points older than max_age should be pruned during prefill."""
    store = HistoryStore(max_age_s=2)
    now_ms = int(time.time() * 1000)
    points = [
        HistoryPoint(ts=now_ms - 5000, v=30.0),  # 5s ago, beyond 2s window
        HistoryPoint(ts=now_ms - 1000, v=45.0),  # 1s ago, within window
    ]
    store.prefill_sensor("cpu", points)
    series = store.get_series("sensor:cpu")
    assert len(series) == 1
    assert series[0].value == 45.0


def test_pruning_removes_old_entries():
    """Old entries beyond max_age should be pruned."""
    import time
    from unittest.mock import patch

    store = HistoryStore(max_age_s=5)  # 5 second window for test

    # Insert a reading at "now"
    store.record_sensors([SensorReading(id="cpu", value_c=40.0)])
    assert len(store.get_series("sensor:cpu")) == 1

    # Advance monotonic clock by 6 seconds
    original_monotonic = time.monotonic

    def shifted_monotonic():
        return original_monotonic() + 6.0

    with patch("time.monotonic", shifted_monotonic):
        # Add another reading at "now + 6s"
        store.record_sensors([SensorReading(id="cpu", value_c=50.0)])
        series = store.get_series("sensor:cpu")
        # Old entry should be pruned (older than 5s)
        assert len(series) == 1
        assert series[0].value == 50.0
