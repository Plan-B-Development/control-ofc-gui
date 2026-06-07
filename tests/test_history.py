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


# DEC-146 P2-1 upgraded prefill from plain append to merge-sort-dedupe; the
# tests below pin the merged ordering (the chart's np.searchsorted hover
# lookup requires sorted series — unsorted reconnect prefill was a real bug).


def test_prefill_sensor_merges_with_existing_series_sorted():
    """prefill_sensor on a series that already has live readings must MERGE:
    every value present exactly once and timestamps strictly ascending. The
    pre-DEC-146 append left older daemon points AFTER newer live readings,
    drawing zigzag chart artifacts and corrupting hover lookups."""
    from itertools import pairwise

    store = HistoryStore()
    # Seed with one live reading first (newest timestamp).
    store.record_sensors([SensorReading(id="cpu", value_c=55.0)])

    # Prefill with older daemon history — must merge BEFORE the live reading.
    now_ms = int(time.time() * 1000)
    points = [
        HistoryPoint(ts=now_ms - 3000, v=40.0),
        HistoryPoint(ts=now_ms - 2000, v=42.0),
    ]
    store.prefill_sensor("cpu", points)
    series = store.get_series("sensor:cpu")
    assert len(series) == 3, f"expected 3 entries after merge, got {len(series)}"
    timestamps = [r.timestamp for r in series]
    assert timestamps == sorted(timestamps), "series must be sorted ascending"
    assert all(b > a for a, b in pairwise(timestamps)), "timestamps must be strictly increasing"
    # Order is now deterministic: prefill (older) before the live reading.
    assert [r.value for r in series] == [40.0, 42.0, 55.0]


def test_prefill_sensor_double_prefill_dedupes_exact_timestamps():
    """A repeated prefill with identical points converted at the same instant
    must not duplicate entries — exact-timestamp collisions keep one copy."""
    from unittest.mock import patch

    store = HistoryStore()
    points = [
        HistoryPoint(ts=2_000_000 - 3000, v=40.0),
        HistoryPoint(ts=2_000_000 - 2000, v=42.0),
    ]
    # Freeze both clocks so the wall→monotonic conversion is identical for
    # both prefill calls (time.time()*1000 == 2_000_000 ms).
    with patch("time.monotonic", lambda: 1000.0), patch("time.time", lambda: 2000.0):
        store.prefill_sensor("cpu", points)
        store.prefill_sensor("cpu", points)
        series = store.get_series("sensor:cpu")
    assert len(series) == 2, f"duplicate prefill must dedupe, got {len(series)}"
    assert [r.value for r in series] == [40.0, 42.0]


def test_prefill_sensor_backfills_gap_between_old_and_live():
    """Reconnect shape: the series holds pre-disconnect readings (old) plus a
    fresh live reading; daemon history covering the disconnect gap must land
    BETWEEN them, keeping the series sorted."""
    from collections import deque

    from control_ofc.services.history_store import TimestampedReading

    store = HistoryStore()
    base = time.monotonic()
    store._series["sensor:cpu"] = deque(
        [
            TimestampedReading(timestamp=base - 10.0, value=30.0),  # pre-disconnect
            TimestampedReading(timestamp=base, value=55.0),  # fresh live reading
        ]
    )
    now_ms = int(time.time() * 1000)
    # Daemon history covering the disconnect gap (~5 s ago).
    store.prefill_sensor("cpu", [HistoryPoint(ts=now_ms - 5000, v=44.0)])
    series = store.get_series("sensor:cpu")
    assert [r.value for r in series] == [30.0, 44.0, 55.0]
    timestamps = [r.timestamp for r in series]
    assert timestamps == sorted(timestamps)


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
