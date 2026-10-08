"""GSA-i / GSA-j: the dashboard chart and rail tell the truth about time.

GSA-i — a reading is stamped when it was sampled (``now - age_ms``), so a sensor
the daemon stopped refreshing adds no point; the rail marks it stale and keeps it
out of the group max; gaps break the line (NaN) and the hover omits a series
that has no sample under the cursor, naming the rest as the rail does.

GSA-j — the chart's time base is ``CLOCK_BOOTTIME`` (it runs through suspend),
the one clock the store, the chart's ``now`` and the annotations share, so the
daemon's wall-clock prefill after a resume lands on the live points instead of
duplicating them.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtWidgets import QTreeWidgetItem

from control_ofc.api.models import (
    DEFAULT_DAEMON_POLL_INTERVAL_MS,
    FanReading,
    HistoryPoint,
    SensorReading,
)
from control_ofc.constants import HISTORY_GAP_BREAK_S
from control_ofc.services.app_state import AppState
from control_ofc.services.history_store import HistoryStore, TimestampedReading, boottime_s
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.timeline_chart import TimelineChart, _sample_near, _SeriesCache


class _Clocks:
    """A boottime clock and a wall clock that a test moves together (or not)."""

    def __init__(self, boot: float = 10_000.0, wall_ms: int = 1_790_000_000_000) -> None:
        self.boot = boot
        self.wall = wall_ms

    def advance(self, seconds: float) -> None:
        self.boot += seconds
        self.wall += round(seconds * 1000)

    def store(self) -> HistoryStore:
        return HistoryStore(clock=lambda: self.boot, wall_ms=lambda: self.wall)


def _cpu(value: float, age_ms: int) -> SensorReading:
    return SensorReading(id="cpu", kind="cpu_temp", label="Tctl", value_c=value, age_ms=age_ms)


# ---------------------------------------------------------------------------
# GSA-i — stamping
# ---------------------------------------------------------------------------


class TestSampleTimeStamping:
    def test_reading_is_stamped_at_its_sample_time(self):
        clocks = _Clocks()
        store = clocks.store()
        store.record_sensors([_cpu(50.0, age_ms=3000)])
        (point,) = store.get_series("sensor:cpu")
        assert point.timestamp == pytest.approx(clocks.boot - 3.0)

    def test_stale_sensor_adds_no_point(self):
        """A frozen sensor: each poll reports the same sample, older by a second."""
        clocks = _Clocks()
        store = clocks.store()
        store.record_sensors([_cpu(50.0, age_ms=200)])
        for age_ms in (1200, 2200, 5200, 9200):  # FRESH -> STALE, never refreshed
            clocks.advance(1.0)
            frozen = _cpu(50.0, age_ms=age_ms)
            store.record_sensors([frozen])
        assert frozen.freshness_at(DEFAULT_DAEMON_POLL_INTERVAL_MS).name == "STALE"
        assert len(store.get_series("sensor:cpu")) == 1

        # The sensor recovers: a new sample is a new point.
        clocks.advance(1.0)
        store.record_sensors([_cpu(55.0, age_ms=100)])
        assert [r.value for r in store.get_series("sensor:cpu")] == [50.0, 55.0]

    def test_stale_fan_rpm_adds_no_point(self):
        clocks = _Clocks()
        store = clocks.store()
        store.record_fans([FanReading(id="f1", source="hwmon", rpm=900, age_ms=100)])
        clocks.advance(1.0)
        store.record_fans([FanReading(id="f1", source="hwmon", rpm=900, age_ms=1100)])
        clocks.advance(1.0)
        store.record_fans([FanReading(id="f1", source="hwmon", rpm=950, age_ms=150)])
        assert [r.value for r in store.get_series("fan:f1:rpm")] == [900.0, 950.0]


# ---------------------------------------------------------------------------
# GSA-i — the rail
# ---------------------------------------------------------------------------


@pytest.fixture
def panel(qtbot):
    p = SensorSeriesPanel(SeriesSelectionModel(), state=AppState())
    qtbot.addWidget(p)
    return p


def _group_item(panel: SensorSeriesPanel, key: str) -> QTreeWidgetItem:
    return panel._group_items[key]


# Older than the chart's break width: the daemon has stopped refreshing it.
_STALE_MS = int(HISTORY_GAP_BREAK_S * 1000) + 2000


class TestRailStaleness:
    def test_stale_row_is_marked_and_out_of_the_group_max(self, panel):
        fresh = SensorReading(id="a", kind="cpu_temp", label="A", value_c=50.0, age_ms=300)
        hot = SensorReading(id="b", kind="cpu_temp", label="B", value_c=90.0, age_ms=300)
        panel.update_sensors([fresh, hot])
        # Precondition: while both are fresh the hot one IS the max, unmarked.
        assert _group_item(panel, "cpu").text(1) == "max 90.0°C"
        assert "stale" not in panel._sensor_items["b"].text(1)

        frozen = SensorReading(id="b", kind="cpu_temp", label="B", value_c=90.0, age_ms=_STALE_MS)
        panel.update_sensors([fresh, frozen])  # same structure -> in-place update path
        assert "stale" in panel._sensor_items["b"].text(1)
        assert f"{_STALE_MS // 1000} s" in panel._sensor_items["b"].toolTip(1)
        assert "stale" not in panel._sensor_items["a"].text(1)
        assert panel._sensor_items["a"].toolTip(1) == ""
        assert _group_item(panel, "cpu").text(1) == "max 50.0°C"

    def test_slow_daemon_cadence_is_not_stale(self, panel):
        """At a 6 s daemon cadence a healthy reading is often 5 s old: it is
        current, so it is neither marked nor dropped from the max (DEC-270)."""
        slow = SensorReading(id="b", kind="cpu_temp", label="B", value_c=90.0, age_ms=5000)
        assert (
            slow.freshness_at(DEFAULT_DAEMON_POLL_INTERVAL_MS).name == "STALE"
        )  # the 2 s rule would have marked it
        panel.update_sensors([slow])
        assert panel._sensor_items["b"].text(1) == "90.0\u00b0C"
        assert _group_item(panel, "cpu").text(1) == "max 90.0\u00b0C"

    def test_group_with_no_fresh_reading_shows_no_max(self, panel):
        frozen = SensorReading(id="b", kind="cpu_temp", label="B", value_c=90.0, age_ms=_STALE_MS)
        panel.update_sensors([frozen])  # rebuild path
        assert "stale" in panel._sensor_items["b"].text(1)
        assert _group_item(panel, "cpu").text(1) == "max —"

    def test_stale_fan_row_is_marked(self, panel):
        panel.update_fans([FanReading(id="openfan:ch00", source="openfan", rpm=800, age_ms=200)])
        assert panel._fan_items["openfan:ch00"].text(1) == "800 RPM"
        panel.update_fans(
            [FanReading(id="openfan:ch00", source="openfan", rpm=800, age_ms=_STALE_MS)]
        )
        assert "stale" in panel._fan_items["openfan:ch00"].text(1)


# ---------------------------------------------------------------------------
# GSA-i — gaps and hover
# ---------------------------------------------------------------------------


def _readings(*pairs: tuple[float, float]) -> list[TimestampedReading]:
    return [TimestampedReading(timestamp=t, value=v) for t, v in pairs]


class TestGapBreaks:
    def test_gap_wider_than_the_break_gets_a_nan_at_its_midpoint(self):
        gap = HISTORY_GAP_BREAK_S + 5
        cache = _SeriesCache(_readings((100.0, 40.0), (101.0, 41.0)), generation=0)
        cache.append(_readings((101.0 + gap, 42.0)))
        x, y = cache.window(now=101.0 + gap, window_s=1000)
        assert np.isnan(y).sum() == 1
        nan_at = int(np.flatnonzero(np.isnan(y))[0])
        assert x[nan_at] == pytest.approx(-gap / 2)
        assert y[-1] == 42.0  # the last entry stays a real sample
        assert cache.last_ts == 101.0 + gap

    def test_spacing_within_the_break_draws_through(self):
        # A slow but healthy daemon cadence (6 s) stays one unbroken line.
        cache = _SeriesCache(_readings((100.0, 40.0), (106.0, 41.0), (112.0, 42.0)), 0)
        _, y = cache.window(now=112.0, window_s=1000)
        assert not np.isnan(y).any()

    def test_chart_draws_both_series_kinds_with_breaks(self, qtbot):
        clocks = _Clocks()
        store = clocks.store()
        for key in ("sensor:cpu", "fan:f1:rpm"):
            store._append(key, clocks.boot - 60, 40.0)
            store._append(key, clocks.boot - 59, 41.0)
            store._append(key, clocks.boot - 1, 42.0)  # 58 s disconnect before this
        chart = TimelineChart(store)
        qtbot.addWidget(chart)
        chart.update_chart()

        temp = chart._temp_items["sensor:cpu"]
        assert np.isnan(temp.getOriginalDataset()[1]).any()
        # The curve the PlotDataItem drew breaks at the NaN rather than bridging.
        assert temp.curve.opts["connect"] == "finite"
        rpm = chart._rpm_items["fan:f1:rpm"]
        assert np.isnan(rpm.getData()[1]).any()
        assert rpm.opts["connect"] == "finite"
        chart.cleanup()


class TestStaleSeriesLeavesTheWindow:
    def test_line_and_dot_clear_once_the_last_sample_scrolls_out(self, qtbot):
        clocks = _Clocks()
        store = clocks.store()
        for key in ("sensor:cpu", "fan:f1:rpm"):
            store._append(key, clocks.boot - 2, 40.0)
            store._append(key, clocks.boot - 1, 41.0)
        chart = TimelineChart(store)
        qtbot.addWidget(chart)
        chart.set_range_index(0)  # 30 s
        chart.update_chart()
        # Presence first: both series and their dots are drawn.
        assert len(chart._temp_items["sensor:cpu"].getOriginalDataset()[0]) == 2
        assert len(chart._rpm_items["fan:f1:rpm"].getData()[0]) == 2
        assert len(chart._latest_items["sensor:cpu"].getData()[0]) == 1

        clocks.advance(60.0)  # frozen: no new samples, all now older than 30 s
        chart.update_chart()
        x, _ = chart._temp_items["sensor:cpu"].getOriginalDataset()
        assert x is None or len(x) == 0
        assert len(chart._rpm_items["fan:f1:rpm"].getData()[0]) == 0
        for key in ("sensor:cpu", "fan:f1:rpm"):
            assert len(chart._latest_items[key].getData()[0]) == 0
        chart.cleanup()


class TestHoverSample:
    def test_nearest_sample_between_two_points(self):
        xd = np.array([-10.0, -9.0, -8.0])
        yd = np.array([40.0, 41.0, 42.0])
        assert _sample_near(xd, yd, -9.2) == 41.0
        assert _sample_near(xd, yd, -8.4) == 42.0

    def test_hover_in_a_gap_omits_the_series(self):
        xd = np.array([-60.0, -59.0, -30.0, -1.0])
        yd = np.array([40.0, 41.0, np.nan, 42.0])
        assert _sample_near(xd, yd, -59.2) == 41.0  # presence on data
        for x in (-58.0, -45.0, -30.0, -2.0):
            assert _sample_near(xd, yd, x) is None

    def test_hover_beyond_the_data_omits_the_series(self):
        xd = np.array([-20.0, -19.0])
        yd = np.array([40.0, 41.0])
        assert _sample_near(xd, yd, -18.0) == 41.0  # just past the newest point
        assert _sample_near(xd, yd, -1.0) is None  # long after it: no sample
        assert _sample_near(xd, yd, -200.0) is None  # before the series began


def _shown_chart(qtbot, store: HistoryStore, keys: list[str]) -> TimelineChart:
    selection = SeriesSelectionModel()
    chart = TimelineChart(store, selection=selection)
    qtbot.addWidget(chart)
    selection.update_known_keys(keys)
    for key in keys:
        selection.set_visible(key, True)
    chart.resize(640, 480)
    with qtbot.waitExposed(chart):
        chart.show()
    chart.update_chart()
    return chart


class TestHoverReadout:
    def test_gap_series_omitted_and_names_come_from_the_resolver(self, qtbot, monkeypatch):
        clocks = _Clocks()
        store = clocks.store()
        whole, gapped = "sensor:hwmon:k10temp:Tctl", "fan:hwmon:it87:pwm1:rpm"
        for i in range(400):  # covers the default 5 min window
            store._append(whole, clocks.boot - 400 + i, 45.0)
        for i in range(30):  # only the last 30 s: nothing under the plot centre
            store._append(gapped, clocks.boot - 30 + i, 1200.0)
        chart = _shown_chart(qtbot, store, [whole, gapped])
        chart.set_label_resolver({whole: "CPU Tctl", gapped: "Rear exhaust"}.get)

        captured: list[str] = []
        monkeypatch.setattr(chart._hover_label, "setText", captured.append)
        rect = chart._plot_widget.getPlotItem().sceneBoundingRect()
        chart._on_mouse_moved((rect.center(),))
        assert captured
        text = captured[-1]
        assert "CPU Tctl: 45.0" in text
        assert "Rear exhaust" not in text and "1200" not in text
        chart.cleanup()

    def test_dashboard_hover_names_series_as_the_rail_does(self, qtbot):
        state = AppState()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        state.set_sensors(
            [SensorReading(id="hwmon:k10temp:Tctl", kind="cpu_temp", label="Tctl", age_ms=100)]
        )
        state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=800, age_ms=100)])
        state.apply_fan_rename("openfan:ch00", "Front intake")
        assert page._chart._series_label("sensor:hwmon:k10temp:Tctl") == "Tctl"
        assert page._chart._series_label("fan:openfan:ch00:rpm") == (
            page._sensor_panel._fan_items["openfan:ch00"].text(0)
        )
        assert "Front intake" in page._chart._series_label("fan:openfan:ch00:rpm")


# ---------------------------------------------------------------------------
# GSA-j — one time base that runs through suspend
# ---------------------------------------------------------------------------


class TestBoottimeBase:
    def test_default_clock_is_boottime(self, monkeypatch):
        asked: list[int] = []

        def fake_clock_gettime(clk_id: int) -> float:
            asked.append(clk_id)
            return 123.0

        monkeypatch.setattr(time, "clock_gettime", fake_clock_gettime)
        assert HistoryStore().now() == 123.0
        assert boottime_s() == 123.0
        assert asked == [time.CLOCK_BOOTTIME, time.CLOCK_BOOTTIME]

    def test_resume_prefill_lands_on_live_points_without_duplicates(self):
        clocks = _Clocks()
        store = clocks.store()
        # Live polling before the sleep, stamped at sample time (age 400 ms).
        # The daemon's ring holds the same samples; its stamps differ from the
        # GUI's back-stamp by transport jitter (30 ms here), never exactly.
        daemon_ring: list[HistoryPoint] = []
        for i in range(10):
            reading = _cpu(40.0 + i, age_ms=400)
            store.record_sensors([reading])
            daemon_ring.append(HistoryPoint(ts=clocks.wall - 430, v=reading.value_c))
            clocks.advance(1.0)
        pre_sleep = [r.timestamp for r in store.get_series("sensor:cpu")]
        assert len(pre_sleep) == 10

        # Suspend for 20 minutes: boottime and wall time both run on.
        clocks.advance(1200.0)
        # After resume the daemon ring also holds post-resume samples.
        for i in range(3):
            daemon_ring.append(HistoryPoint(ts=clocks.wall - 3000 + i * 1000, v=60.0 + i))
        store.record_sensors([_cpu(70.0, age_ms=400)])
        store.prefill_sensor("cpu", daemon_ring)

        series = store.get_series("sensor:cpu")
        values = [r.value for r in series]
        assert values == [*(40.0 + i for i in range(10)), 60.0, 61.0, 62.0, 70.0]
        # Pre-sleep points stay where they were taken — 20 min ago, not seconds.
        assert [r.timestamp for r in series[:10]] == pre_sleep
        assert clocks.boot - series[9].timestamp > 1200
        timestamps = [r.timestamp for r in series]
        assert timestamps == sorted(timestamps)

    def test_chart_and_annotations_use_the_store_clock(self, qtbot):
        clocks = _Clocks(boot=50_000.0)
        store = clocks.store()
        store._append("sensor:cpu", clocks.boot - 10, 45.0)
        selection = SeriesSelectionModel()
        selection.update_known_keys(["sensor:cpu"])
        selection.set_visible("sensor:cpu", True)
        page = DashboardPage(state=AppState(), history=store, selection=selection)
        qtbot.addWidget(page)
        x, _ = page._chart._windowed_series("sensor:cpu", store.now())
        page._chart.update_chart()
        drawn_x, _ = page._chart._temp_items["sensor:cpu"].getOriginalDataset()
        assert list(drawn_x) == pytest.approx([-10.0])
        assert list(x) == pytest.approx([-10.0])

        page._annotate("Profile: quiet")
        (_, ts, _) = page._chart._annotations[-1]
        assert ts == clocks.boot
