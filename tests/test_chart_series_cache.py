"""EFF-1 (2026-07-21 audit): the TimelineChart incremental series cache.

``update_chart`` no longer rebuilds O(history) numpy arrays from Python lists
every tick; ``_SeriesCache`` mirrors each series append-only and serves the
visible window as a searchsorted cut + views. These tests pin the plan-mandated
parity property — the incremental path must yield byte-identical plotted data
to a from-scratch rebuild — plus the generation-driven invalidation, the
compaction memory bound, and the cleanup guard the bench harness exposed.
"""

from __future__ import annotations

import time
from collections import deque

import numpy as np

from control_ofc.api.models import HistoryPoint, SensorReading
from control_ofc.services.history_store import HistoryStore, TimestampedReading
from control_ofc.ui.widgets.timeline_chart import TimelineChart, _SeriesCache


def _readings(*pairs: tuple[float, float]) -> list[TimestampedReading]:
    return [TimestampedReading(timestamp=t, value=v) for t, v in pairs]


def _naive_window(readings, now: float, window_s: float):
    """The pre-EFF-1 rebuild, verbatim: list-comp → np.array → mask."""
    x = np.array([r.timestamp - now for r in readings])
    y = np.array([r.value for r in readings])
    mask = x >= -window_s
    return x[mask], y[mask]


def _seed(store: HistoryStore, key: str, readings) -> None:
    dq = store._series.setdefault(key, deque())
    for r in readings:
        dq.append(r)


# ---------------------------------------------------------------------------
# _SeriesCache — pure numpy, no Qt
# ---------------------------------------------------------------------------


class TestSeriesCache:
    def test_window_matches_naive_rebuild(self):
        readings = _readings((100.0, 40.0), (101.0, 41.0), (102.0, 42.0), (103.0, 43.0))
        cache = _SeriesCache(readings, generation=0)
        x, y = cache.window(now=103.0, window_s=2.0)
        nx, ny = _naive_window(readings, now=103.0, window_s=2.0)
        assert np.array_equal(x, nx)
        assert np.array_equal(y, ny)

    def test_append_then_window_matches_naive(self):
        head = _readings((100.0, 40.0), (101.0, 41.0))
        tail = _readings((102.0, 42.0), (103.0, 43.0))
        cache = _SeriesCache(head, generation=0)
        cache.append(tail)
        x, y = cache.window(now=103.0, window_s=10.0)
        nx, ny = _naive_window(head + tail, now=103.0, window_s=10.0)
        assert np.array_equal(x, nx)
        assert np.array_equal(y, ny)
        assert cache.last_ts == 103.0

    def test_window_all_older_returns_none(self):
        cache = _SeriesCache(_readings((100.0, 40.0)), generation=0)
        x, y = cache.window(now=500.0, window_s=10.0)
        assert x is None and y is None

    def test_compaction_bounds_memory(self):
        """Appending far past the retention span must compact (drop the stale
        left) instead of growing forever — the cache stays ~one retention
        window per series."""
        from control_ofc.constants import HISTORY_DURATION_S

        cache = _SeriesCache(_readings((0.0, 1.0)), generation=0)
        total = int(HISTORY_DURATION_S * 3)
        cache.append(_readings(*[(float(t), float(t % 100)) for t in range(1, total)]))
        # Invariants: capacity only doubles when a full buffer holds less than
        # one retention span (so it is bounded by ~2x the span at 1 Hz), the
        # count oscillates within capacity between compactions, and the
        # surviving span never exceeds two retention windows — NOT 3x total.
        assert len(cache.xs) <= 2 * (HISTORY_DURATION_S + 1)
        assert cache.count <= len(cache.xs)
        assert cache.xs[cache.count - 1] - cache.xs[0] <= 2 * HISTORY_DURATION_S
        # The surviving span is the newest tail and still windows correctly.
        x, y = cache.window(now=float(total - 1), window_s=5.0)
        assert x is not None and len(x) == 6  # now-5 .. now inclusive
        assert x[-1] == 0.0 and y[-1] == float((total - 1) % 100)

    def test_widest_range_still_served_after_compaction(self):
        """Range-combo growth (5 m → 2 h) after compaction has run: the
        compaction cutoff equals the widest selectable range, so the full
        HISTORY_DURATION_S window must still be served intact."""
        from control_ofc.constants import HISTORY_DURATION_S

        cache = _SeriesCache(_readings((0.0, 1.0)), generation=0)
        total = int(HISTORY_DURATION_S * 3)
        cache.append(_readings(*[(float(t), float(t)) for t in range(1, total)]))
        now = float(total - 1)
        x, y = cache.window(now=now, window_s=float(HISTORY_DURATION_S))
        assert x is not None
        # Every second of the widest window is present (nothing compacted away
        # inside it) and values line up with their timestamps.
        assert len(x) == HISTORY_DURATION_S + 1
        assert x[0] == -float(HISTORY_DURATION_S) and x[-1] == 0.0
        assert y[0] == now - HISTORY_DURATION_S and y[-1] == now


# ---------------------------------------------------------------------------
# TimelineChart._windowed_series — cache lifecycle against a real store
# ---------------------------------------------------------------------------


class TestWindowedSeriesParity:
    def _chart(self, qtbot, store: HistoryStore) -> TimelineChart:
        chart = TimelineChart(store)
        qtbot.addWidget(chart)
        return chart

    def test_incremental_ticks_match_naive(self, qtbot):
        store = HistoryStore()
        key = "sensor:cpu"
        base = time.monotonic()
        _seed(store, key, _readings(*[(base + i, 40.0 + i) for i in range(10)]))
        chart = self._chart(qtbot, store)

        x1, y1 = chart._windowed_series(key, now=base + 9)
        nx1, ny1 = _naive_window(store.get_series(key), now=base + 9, window_s=300)
        assert np.array_equal(x1, nx1) and np.array_equal(y1, ny1)

        # Append-only growth → the incremental path must equal a fresh rebuild.
        _seed(store, key, _readings((base + 10, 55.0), (base + 11, 56.0)))
        x2, y2 = chart._windowed_series(key, now=base + 11)
        nx2, ny2 = _naive_window(store.get_series(key), now=base + 11, window_s=300)
        assert np.array_equal(x2, nx2) and np.array_equal(y2, ny2)
        chart.cleanup()

    def test_prefill_merge_invalidates_and_matches_naive(self, qtbot):
        """A prefill MERGE inserts older points mid-series — the generation
        bump must force a rebuild so the merged points appear (a pure
        incremental append would silently miss them)."""
        store = HistoryStore()
        store.record_sensors([SensorReading(id="cpu", kind="CpuTemp", value_c=55.0, age_ms=10)])
        chart = self._chart(qtbot, store)
        now = time.monotonic()
        assert chart._windowed_series("sensor:cpu", now)[0] is not None  # cache built

        now_ms = int(time.time() * 1000)
        store.prefill_sensor(
            "cpu", [HistoryPoint(ts=now_ms - 3000, v=40.0), HistoryPoint(ts=now_ms - 2000, v=42.0)]
        )
        x, y = chart._windowed_series("sensor:cpu", time.monotonic())
        assert len(x) == 3, "merged prefill points must reach the plotted window"
        assert list(y) == [40.0, 42.0, 55.0]
        chart.cleanup()

    def test_clear_invalidates_cache(self, qtbot):
        store = HistoryStore()
        store.record_sensors([SensorReading(id="cpu", kind="CpuTemp", value_c=55.0, age_ms=10)])
        chart = self._chart(qtbot, store)
        assert chart._windowed_series("sensor:cpu", time.monotonic())[0] is not None
        store.clear()
        x, y = chart._windowed_series("sensor:cpu", time.monotonic())
        assert x is None and y is None
        assert "sensor:cpu" not in chart._series_cache  # dropped, not stale
        chart.cleanup()


class TestChartIntegration:
    def test_update_chart_plots_windowed_data_incrementally(self, qtbot):
        store = HistoryStore()
        store.record_sensors([SensorReading(id="cpu", kind="CpuTemp", value_c=50.0, age_ms=10)])
        chart = TimelineChart(store)
        qtbot.addWidget(chart)
        chart.update_chart()
        assert "sensor:cpu" in chart._temp_items
        _, y = chart._temp_items["sensor:cpu"].getOriginalDataset()
        assert list(y) == [50.0]

        store.record_sensors([SensorReading(id="cpu", kind="CpuTemp", value_c=51.0, age_ms=10)])
        chart.update_chart()  # steady-state incremental tick
        _, y = chart._temp_items["sensor:cpu"].getOriginalDataset()
        assert list(y) == [50.0, 51.0]
        chart.cleanup()

    def test_stale_key_removal_drops_cache(self, qtbot):
        store = HistoryStore()
        store.record_sensors([SensorReading(id="cpu", kind="CpuTemp", value_c=50.0, age_ms=10)])
        chart = TimelineChart(store)
        qtbot.addWidget(chart)
        chart.update_chart()
        assert "sensor:cpu" in chart._series_cache
        store.clear()
        chart.update_chart()
        assert "sensor:cpu" not in chart._temp_items
        assert "sensor:cpu" not in chart._series_cache
        chart.cleanup()

    def test_cleanup_without_selection_model_does_not_raise(self, qtbot):
        """Bench-harness find (EFF-1): cleanup() dereferenced _selection
        unguarded while the constructor guards it — a selection-less chart
        must tear down cleanly."""
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart.cleanup()  # must not raise
        chart.cleanup()  # idempotent
