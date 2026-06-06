"""Regression tests: first-run chart defaults (series visibility + time range).

Two first-run bugs surfaced by the 2026-06-06 documentation audit:

1. Every sensor series ended up permanently hidden on a fresh config: panel
   rows were built before the dashboard registered their keys in the selection
   model, so ``is_visible()`` returned False, rows were created unchecked, and
   the group-summary ``setText`` fired ``itemChanged`` — whose group branch
   synced the unchecked children back into the model as hidden.
2. ``chart_default_range_index`` (Settings → "Chart default time range",
   default 15m) was persisted and editable but never applied — the chart
   always opened at its hardcoded 5m.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading, SensorReading
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.timeline_chart import TIME_RANGES, TimelineChart


def _sensor(sid: str, kind: str = "CpuTemp") -> SensorReading:
    return SensorReading(id=sid, kind=kind, label=sid, value_c=45.0, source="hwmon", age_ms=100)


def _fan(fid: str) -> FanReading:
    return FanReading(id=fid, source="openfan", rpm=900, last_commanded_pwm=40, age_ms=100)


class TestFirstDiscoveryVisibility:
    def test_sensor_rows_default_visible_on_fresh_config(self, qtbot):
        """First sensor discovery must not push hidden state into the model."""
        model = SeriesSelectionModel()
        panel = SensorSeriesPanel(model)
        qtbot.addWidget(panel)

        panel.update_sensors([_sensor("cpu:tctl"), _sensor("gpu:edge", "GpuTemp")])
        # The dashboard registers displayable keys *after* the panel rebuild —
        # replicating that order is what regresses the bug.
        model.update_known_keys([f"sensor:{sid}" for sid in panel.displayed_sensor_ids()])

        assert model.to_dict()["hidden_keys"] == []
        assert model.is_visible("sensor:cpu:tctl")
        assert model.is_visible("sensor:gpu:edge")

    def test_sensor_rows_stay_visible_after_value_ticks(self, qtbot):
        """Subsequent value-only updates must not flip visibility either."""
        model = SeriesSelectionModel()
        panel = SensorSeriesPanel(model)
        qtbot.addWidget(panel)

        readings = [_sensor("cpu:tctl"), _sensor("gpu:edge", "GpuTemp")]
        panel.update_sensors(readings)
        model.update_known_keys([f"sensor:{sid}" for sid in panel.displayed_sensor_ids()])
        panel.update_sensors(readings)  # in-place value update path

        assert model.is_visible("sensor:cpu:tctl")
        assert model.is_visible("sensor:gpu:edge")

    def test_fan_rows_default_visible_on_fresh_config(self, qtbot):
        model = SeriesSelectionModel()
        panel = SensorSeriesPanel(model)
        qtbot.addWidget(panel)

        panel.update_fans([_fan("openfan:ch00"), _fan("openfan:ch01")])
        model.update_known_keys(["fan:openfan:ch00:rpm", "fan:openfan:ch01:rpm"])

        assert model.to_dict()["hidden_keys"] == []
        assert model.is_visible("fan:openfan:ch00:rpm")
        assert model.is_visible("fan:openfan:ch01:rpm")

    def test_persisted_hidden_series_stay_hidden(self, qtbot):
        """Explicitly hidden keys still come up unchecked after a rebuild."""
        model = SeriesSelectionModel()
        model.load_hidden(["sensor:cpu:tctl"])
        panel = SensorSeriesPanel(model)
        qtbot.addWidget(panel)

        panel.update_sensors([_sensor("cpu:tctl"), _sensor("gpu:edge", "GpuTemp")])
        model.update_known_keys([f"sensor:{sid}" for sid in panel.displayed_sensor_ids()])

        assert not model.is_visible("sensor:cpu:tctl")
        assert model.is_visible("sensor:gpu:edge")


class TestDefaultRange:
    def test_set_range_index_applies(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart.set_range_index(4)
        assert chart._range_combo.currentText() == "15m"
        assert chart._time_range_s == TIME_RANGES[4][1]

    def test_set_range_index_ignores_out_of_bounds(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        before = chart._range_combo.currentIndex()
        chart.set_range_index(99)
        chart.set_range_index(-1)
        assert chart._range_combo.currentIndex() == before

    def test_dashboard_applies_settings_default_range(
        self, qtbot, app_state, profile_service, settings_service
    ):
        """DashboardPage must apply Settings → chart default time range at startup."""
        settings_service.settings.chart_default_range_index = 6  # 30m
        page = DashboardPage(
            state=app_state,
            history=HistoryStore(),
            profile_service=profile_service,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)
        assert page._chart._range_combo.currentText() == "30m"
        assert page._chart._time_range_s == TIME_RANGES[6][1]

    def test_dashboard_default_settings_yield_15m(
        self, qtbot, app_state, profile_service, settings_service
    ):
        """Out of the box the documented default (15m) is what users get."""
        page = DashboardPage(
            state=app_state,
            history=HistoryStore(),
            profile_service=profile_service,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)
        assert page._chart._range_combo.currentText() == "15m"
