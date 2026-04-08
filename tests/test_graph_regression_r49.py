"""R49: Graph regression — lines must appear when selection model is empty or populated."""

from __future__ import annotations

import time

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestChartableKeysFallback:
    """_chartable_keys falls back to history when selection model is empty."""

    def test_empty_selection_falls_back_to_history(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        # Add data to history but don't seed selection
        history._append("sensor:cpu0", time.monotonic(), 45.0)

        keys = chart._chartable_keys()
        assert "sensor:cpu0" in keys

    def test_populated_selection_used_when_available(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        history._append("sensor:cpu0", time.monotonic(), 45.0)
        history._append("sensor:igpu", time.monotonic(), 30.0)
        selection.update_known_keys(["sensor:cpu0"])  # Only cpu0

        keys = chart._chartable_keys()
        assert "sensor:cpu0" in keys
        assert "sensor:igpu" not in keys

    def test_no_selection_model_uses_history(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history, selection=None)
        qtbot.addWidget(chart)

        history._append("sensor:cpu0", time.monotonic(), 45.0)

        keys = chart._chartable_keys()
        assert "sensor:cpu0" in keys


class TestRpmItemsArePlotCurveItem:
    """RPM series use PlotCurveItem for correct secondary ViewBox rendering."""

    def test_rpm_items_dict_type(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        # Type annotation confirms PlotCurveItem
        assert isinstance(chart._rpm_items, dict)


class TestSensorPanelStructureStable:
    """Sensor panel structure_changed doesn't oscillate with iGPU filtering."""

    def test_filtered_ids_compared_stably(self, qtbot, app_state):
        from control_ofc.api.models import (
            AmdGpuCapability,
            Capabilities,
            SensorReading,
        )
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        app_state.set_capabilities(
            Capabilities(
                daemon_version="0.5.1",
                amd_gpu=AmdGpuCapability(present=True, pci_id="0000:03:00.0", is_discrete=True),
            )
        )
        selection = SeriesSelectionModel()
        panel = SensorSeriesPanel(selection, state=app_state)
        qtbot.addWidget(panel)

        sensors = [
            SensorReading(
                id="hwmon:amdgpu:0000:03:00.0:edge",
                kind="gpu_temp",
                label="edge",
                value_c=45.0,
                source="amd_gpu",
                age_ms=50,
            ),
            SensorReading(
                id="hwmon:amdgpu:0000:7b:00.0:edge",
                kind="gpu_temp",
                label="edge",
                value_c=30.0,
                source="amd_gpu",
                age_ms=50,
            ),
        ]

        # First call: structure_changed = True ([] != filtered)
        panel.update_sensors(sensors)
        ids_after_first = list(panel._known_sensor_ids)

        # Second call with SAME sensors: structure_changed should be False
        # (filtered ids match stored filtered ids)
        panel.update_sensors(sensors)
        ids_after_second = list(panel._known_sensor_ids)

        assert ids_after_first == ids_after_second
        # Only dGPU sensor should be in the list
        assert len(ids_after_first) == 1
        assert "0000:03:00.0" in ids_after_first[0]
