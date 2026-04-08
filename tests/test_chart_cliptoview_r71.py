"""R71: Dashboard timeline chart — PlotCurveItem AttributeError regression fix.

Verifies that update_chart() completes without error for both temperature
and RPM series, that correct pyqtgraph item types are used, and that
chart data is populated correctly after an update cycle.
"""

from __future__ import annotations

import time

import pyqtgraph as pg

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


def _seed_history(history: HistoryStore, key: str, n: int = 5, base: float = 40.0):
    """Add *n* data points to *history* for *key*."""
    now = time.monotonic()
    for i in range(n):
        history._append(key, now - (n - i), base + i)


class TestUpdateChartNoError:
    """update_chart() must complete without raising for all series types."""

    def test_temp_series_no_error(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "sensor:cpu0", base=45.0)
        selection.update_known_keys(["sensor:cpu0"])
        selection.set_visible("sensor:cpu0", True)

        # Must not raise
        chart.update_chart()

        assert "sensor:cpu0" in chart._temp_items

    def test_rpm_series_no_error(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "fan:openfan:ch00:rpm", base=1200.0)
        selection.update_known_keys(["fan:openfan:ch00:rpm"])
        selection.set_visible("fan:openfan:ch00:rpm", True)

        # Must not raise — this is the core regression test for R71
        chart.update_chart()

        assert "fan:openfan:ch00:rpm" in chart._rpm_items

    def test_mixed_temp_and_rpm_no_error(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "sensor:cpu0", base=45.0)
        _seed_history(history, "fan:openfan:ch00:rpm", base=1200.0)
        keys = ["sensor:cpu0", "fan:openfan:ch00:rpm"]
        selection.update_known_keys(keys)
        for k in keys:
            selection.set_visible(k, True)

        chart.update_chart()

        assert "sensor:cpu0" in chart._temp_items
        assert "fan:openfan:ch00:rpm" in chart._rpm_items


class TestItemTypes:
    """Series use the correct pyqtgraph item types."""

    def test_temp_items_are_plot_data_item(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "sensor:cpu0")
        selection.update_known_keys(["sensor:cpu0"])
        selection.set_visible("sensor:cpu0", True)
        chart.update_chart()

        item = chart._temp_items["sensor:cpu0"]
        assert isinstance(item, pg.PlotDataItem)

    def test_rpm_items_are_plot_curve_item(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "fan:openfan:ch00:rpm", base=1200.0)
        selection.update_known_keys(["fan:openfan:ch00:rpm"])
        selection.set_visible("fan:openfan:ch00:rpm", True)
        chart.update_chart()

        item = chart._rpm_items["fan:openfan:ch00:rpm"]
        assert isinstance(item, pg.PlotCurveItem)
        # PlotCurveItem must NOT have setClipToView — this is the API
        # mismatch that caused the R66 regression
        assert not hasattr(item, "setClipToView")


class TestChartDataPopulated:
    """Chart items contain data after update_chart()."""

    def test_temp_item_has_data(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "sensor:cpu0", n=10, base=45.0)
        selection.update_known_keys(["sensor:cpu0"])
        selection.set_visible("sensor:cpu0", True)
        chart.update_chart()

        item = chart._temp_items["sensor:cpu0"]
        xd, yd = item.getData()
        assert len(xd) > 0
        assert len(yd) > 0

    def test_rpm_item_has_data(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "fan:openfan:ch00:rpm", n=10, base=1200.0)
        selection.update_known_keys(["fan:openfan:ch00:rpm"])
        selection.set_visible("fan:openfan:ch00:rpm", True)
        chart.update_chart()

        item = chart._rpm_items["fan:openfan:ch00:rpm"]
        xd, yd = item.getData()
        assert len(xd) > 0
        assert len(yd) > 0

    def test_hidden_series_cleared(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        _seed_history(history, "sensor:cpu0", n=10, base=45.0)
        selection.update_known_keys(["sensor:cpu0"])
        selection.set_visible("sensor:cpu0", True)
        chart.update_chart()

        # Now hide the series
        selection.set_visible("sensor:cpu0", False)
        chart.update_chart()

        item = chart._temp_items["sensor:cpu0"]
        xd, _yd = item.getData()
        # PlotDataItem.setData([], []) returns None from getData()
        assert xd is None or len(xd) == 0

    def test_repeated_updates_no_error(self, qtbot):
        """Simulates multiple poll cycles — the original bug repeated every cycle."""
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        keys = ["sensor:cpu0", "fan:openfan:ch00:rpm"]
        selection.update_known_keys(keys)
        for k in keys:
            selection.set_visible(k, True)

        now = time.monotonic()
        for cycle in range(5):
            history._append("sensor:cpu0", now + cycle, 45.0 + cycle)
            history._append("fan:openfan:ch00:rpm", now + cycle, 1200.0 + cycle * 10)
            # Must not raise on any cycle
            chart.update_chart()

        assert "sensor:cpu0" in chart._temp_items
        assert "fan:openfan:ch00:rpm" in chart._rpm_items
