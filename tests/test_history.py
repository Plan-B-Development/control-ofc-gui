"""Tests for the history store."""

from __future__ import annotations

import time

from control_ofc.api.models import FanReading, HistoryPoint, SensorReading
from control_ofc.services.history_store import HistoryStore


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


# ---------------------------------------------------------------------------
# T2 (test-tests audit): prefill_sensor on a pre-existing series + prune
# boundary semantics.
#
# Mutation testing showed:
#  - prefill_sensor's "create deque if missing" path mutated to "always create"
#    (replacing the existing series) survived — no test populated a series and
#    then called prefill again to verify append-not-replace semantics.
#  - _prune's `while series and series[0].timestamp < cutoff` boundary mutated
#    to `<=` survived — no test exercised an entry exactly at the cutoff to
#    distinguish strict vs non-strict.
# ---------------------------------------------------------------------------


def test_prefill_sensor_appends_to_existing_series():
    """prefill_sensor on a series that already has live readings must APPEND
    (not replace, not duplicate). The existing readings are higher-timestamp
    than the prefill, so a correct append leaves both visible."""
    store = HistoryStore()
    # Seed with one live reading first.
    store.record_sensors([SensorReading(id="cpu", value_c=55.0)])
    initial = store.get_series("sensor:cpu")
    assert len(initial) == 1, "precondition: one live reading"

    # Now prefill with daemon history — should append, not replace.
    now_ms = int(time.time() * 1000)
    points = [
        HistoryPoint(ts=now_ms - 3000, v=40.0),
        HistoryPoint(ts=now_ms - 2000, v=42.0),
    ]
    store.prefill_sensor("cpu", points)
    series = store.get_series("sensor:cpu")
    # Three entries total: 2 prefilled + 1 from record_sensors.
    assert len(series) == 3, f"expected 3 entries after append, got {len(series)}"
    # All three values must be present (order not important here — order
    # depends on whether prefill happens before or after the live reading).
    values = sorted(r.value for r in series)
    assert values == [40.0, 42.0, 55.0]


def test_prune_boundary_keeps_entry_exactly_at_cutoff():
    """The prune predicate is `timestamp < cutoff` (strict less-than) — an
    entry whose timestamp equals the cutoff must be RETAINED. Locks down
    `<` vs `<=` on the prune loop's condition."""
    import time as _time
    from collections import deque
    from unittest.mock import patch

    from control_ofc.services.history_store import TimestampedReading

    store = HistoryStore(max_age_s=5)

    # Establish a deterministic monotonic baseline by recording at t=0 (real).
    base = _time.monotonic()

    # Seed two entries at known monotonic timestamps:
    #   entry A: t = base (the future cutoff will land *exactly* here)
    #   entry B: t = base + 1 (clearly inside the window)
    store._series["sensor:cpu"] = deque(
        [
            TimestampedReading(timestamp=base, value=10.0),
            TimestampedReading(timestamp=base + 1.0, value=20.0),
        ]
    )

    # Patch monotonic so cutoff = base + 5 - 5 = base.
    # Then entry A (timestamp = base) is EXACTLY at the cutoff.
    with patch("time.monotonic", lambda: base + 5.0):
        series = store.get_series("sensor:cpu")
        # The strict `<` predicate keeps the entry at the cutoff.
        assert len(series) == 2, (
            "entry exactly at cutoff must be retained "
            f"(strict `<`), got {len(series)} entries: {[r.value for r in series]}"
        )
        assert series[0].value == 10.0
        assert series[1].value == 20.0


def test_prune_drops_entry_just_past_cutoff():
    """Companion to the boundary test: an entry whose timestamp is even
    1 nanosecond past the cutoff must be dropped."""
    import time as _time
    from collections import deque
    from unittest.mock import patch

    from control_ofc.services.history_store import TimestampedReading

    store = HistoryStore(max_age_s=5)
    base = _time.monotonic()
    store._series["sensor:cpu"] = deque(
        [
            TimestampedReading(timestamp=base - 0.001, value=10.0),  # past cutoff
            TimestampedReading(timestamp=base + 1.0, value=20.0),
        ]
    )

    # cutoff = base + 5 - 5 = base. Entry A is base - 0.001 < base → pruned.
    with patch("time.monotonic", lambda: base + 5.0):
        series = store.get_series("sensor:cpu")
        assert len(series) == 1
        assert series[0].value == 20.0
