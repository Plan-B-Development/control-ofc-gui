"""Tests for the history store."""

from __future__ import annotations

import time

from control_ofc.api.models import FanReading, HistoryPoint, SensorReading
from control_ofc.services.history_store import HistoryStore, TimestampedReading


def test_record_sensors():
    store = HistoryStore()
    sensors = [SensorReading(id="cpu", kind="cpu_temp", value_c=45.0)]
    store.record_sensors(sensors)
    series = store.get_series("sensor:cpu")
    assert len(series) == 1
    assert series[0].value == 45.0


def test_record_fans_rpm():
    store = HistoryStore()
    fans = [FanReading(id="openfan:ch00", source="openfan", rpm=800)]
    store.record_fans(fans)
    assert len(store.get_series("fan:openfan:ch00:rpm")) == 1


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


def test_get_series_on_fully_aged_out_key_returns_empty(monkeypatch):
    # Prune-to-empty (DEC-236 hardening): once every entry ages past max_age,
    # _prune pops the key, so get_series must return [] — not KeyError — for a
    # series that just aged out (the other prune tests always leave >=1 entry).
    import control_ofc.services.history_store as hs

    store = HistoryStore(max_age_s=2)
    store.record_sensors([SensorReading(id="cpu", value_c=40.0)])
    assert len(store.get_series("sensor:cpu")) == 1
    # Advance the monotonic clock well past max_age so every point is now stale.
    future = time.monotonic() + 100
    monkeypatch.setattr(hs.time, "monotonic", lambda: future)
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
    from collections import deque
    from unittest.mock import patch

    from control_ofc.services.history_store import TimestampedReading

    store = HistoryStore(max_age_s=5)

    # A float-exact baseline: (base + 5.0) - 5.0 must round-trip to *exactly*
    # `base` or the "entry sits on the cutoff" premise breaks. A live
    # time.monotonic() carries enough fractional bits that the round-trip drifts
    # by an ULP and the boundary entry is spuriously pruned — 1000.0 is exact.
    base = 1000.0

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
    from collections import deque
    from unittest.mock import patch

    from control_ofc.services.history_store import TimestampedReading

    store = HistoryStore(max_age_s=5)
    # Fixed, float-exact baseline so (base + 5.0) - 5.0 == base exactly (a live
    # time.monotonic() can drift by an ULP and move the cutoff off `base`).
    base = 1000.0
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


# ---------------------------------------------------------------------------
# EFF-1 (2026-07-21): generation + readings_since — the incremental-cache API
# ---------------------------------------------------------------------------


def test_readings_since_returns_strictly_newer_ascending():
    store = HistoryStore()
    key = "sensor:cpu"
    dq = store._series.setdefault(key, __import__("collections").deque())
    for i in range(5):
        dq.append(TimestampedReading(timestamp=100.0 + i, value=float(i)))

    newer = store.readings_since(key, 101.0)  # strictly greater — 101 excluded
    assert [r.timestamp for r in newer] == [102.0, 103.0, 104.0]
    assert [r.value for r in newer] == [2.0, 3.0, 4.0]

    assert store.readings_since(key, 104.0) == []
    assert [r.timestamp for r in store.readings_since(key, float("-inf"))] == [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
    ]


def test_readings_since_unknown_key_is_empty():
    assert HistoryStore().readings_since("sensor:nope", 0.0) == []


def test_generation_stable_across_plain_appends():
    store = HistoryStore()
    key = "sensor:cpu"
    assert store.generation(key) == 0
    store.record_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=50.0, age_ms=10)])
    store.record_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=51.0, age_ms=10)])
    assert store.generation("sensor:cpu") == 0  # append-only → cache stays valid


def test_generation_bumps_on_prefill_merge():
    store = HistoryStore()
    store.record_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=50.0, age_ms=10)])
    key = "sensor:cpu"
    before = store.generation(key)
    now_ms = int(time.time() * 1000)
    store.prefill_sensor("cpu", [HistoryPoint(ts=now_ms - 5000, v=42.0)])
    assert store.generation(key) == before + 1  # merge restructured the series


def test_generation_bumps_on_clear():
    store = HistoryStore()
    store.record_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=50.0, age_ms=10)])
    before = store.generation("sensor:cpu")
    store.clear()
    assert store.generation("sensor:cpu") == before + 1


# --- prefill_fan (DEC-248) -------------------------------------------------
# The fan counterpart to prefill_sensor. Added because the screenshot pipeline
# had no public way to seed fan history and reached for the private _append,
# which neither sorts nor bumps the generation the chart's cache watches.


def test_prefill_fan_populates_rpm_series():
    store = HistoryStore()
    now_ms = int(time.time() * 1000)
    store.prefill_fan("openfan:ch00", [HistoryPoint(ts=now_ms - 1000, v=700.0)])
    series = store.get_series("fan:openfan:ch00:rpm")
    assert len(series) == 1
    assert series[0].value == 700.0


def test_prefill_fan_metric_selects_the_series():
    """rpm is measured, pwm is commanded — they must never be conflated."""
    store = HistoryStore()
    now_ms = int(time.time() * 1000)
    store.prefill_fan("f1", [HistoryPoint(ts=now_ms - 1000, v=55.0)], metric="pwm")
    assert len(store.get_series("fan:f1:pwm")) == 1
    assert store.get_series("fan:f1:rpm") == []


def test_prefill_fan_empty_points_is_noop():
    store = HistoryStore()
    store.prefill_fan("f1", [])
    assert store.get_series("fan:f1:rpm") == []


def test_prefill_fan_merges_with_existing_series_sorted():
    """The invariant that motivated this method: older backfill merged behind
    newer live readings must still leave timestamps ascending. Raw _append did
    not, which corrupts the np.searchsorted hover lookup."""
    from itertools import pairwise

    store = HistoryStore()
    # Live reading first (newest timestamp), exactly as at capture time.
    store.record_fans([FanReading(id="f1", source="hwmon", rpm=900)])

    now_ms = int(time.time() * 1000)
    store.prefill_fan(
        "f1",
        [HistoryPoint(ts=now_ms - 3000, v=700.0), HistoryPoint(ts=now_ms - 2000, v=800.0)],
    )

    series = store.get_series("fan:f1:rpm")
    timestamps = [r.timestamp for r in series]
    assert timestamps == sorted(timestamps), "series must be sorted ascending"
    assert all(b > a for a, b in pairwise(timestamps)), "timestamps must be strictly increasing"
    assert [r.value for r in series] == [700.0, 800.0, 900.0]


def test_prefill_fan_bumps_generation():
    """The chart rebuilds only when the generation changes; without the bump a
    backfill is invisible even though the store holds it (the DEC-248 bug)."""
    store = HistoryStore()
    store.record_fans([FanReading(id="f1", source="hwmon", rpm=900)])
    before = store.generation("fan:f1:rpm")
    store.prefill_fan("f1", [HistoryPoint(ts=int(time.time() * 1000) - 5000, v=700.0)])
    assert store.generation("fan:f1:rpm") > before


def test_prefill_fan_dedupes_exact_timestamps():
    from unittest.mock import patch

    store = HistoryStore()
    points = [
        HistoryPoint(ts=2_000_000 - 3000, v=700.0),
        HistoryPoint(ts=2_000_000 - 2000, v=800.0),
    ]
    # get_series() must be read INSIDE the patch: it prunes against the current
    # clock, and points stamped at a frozen monotonic of 1000.0 are older than
    # max_age relative to the real clock, so reading outside returns an empty
    # series rather than the dedupe result.
    with patch("time.monotonic", lambda: 1000.0), patch("time.time", lambda: 2000.0):
        store.prefill_fan("f1", points)
        store.prefill_fan("f1", points)
        series = store.get_series("fan:f1:rpm")
    assert len(series) == 2
    assert [r.value for r in series] == [700.0, 800.0]
