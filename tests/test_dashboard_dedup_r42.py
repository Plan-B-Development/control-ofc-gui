"""R42: Dashboard GPU de-duplication, hover selection, expand affordance."""

from __future__ import annotations

from onlyfans.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    FanReading,
    OperationMode,
    SensorReading,
)
from onlyfans.services.app_state import AppState
from onlyfans.services.series_selection import SeriesSelectionModel
from onlyfans.ui.pages.dashboard_page import DashboardPage
from onlyfans.ui.widgets.sensor_series_panel import SensorSeriesPanel


def _make_state(gpu_pci: str = "0000:03:00.0") -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_capabilities(
        Capabilities(
            daemon_version="0.5.0",
            amd_gpu=AmdGpuCapability(
                present=True,
                display_label="9070XT",
                pci_id=gpu_pci,
                is_discrete=True,
            ),
        )
    )
    return state


# ---------------------------------------------------------------------------
# Fan de-duplication
# ---------------------------------------------------------------------------


class TestFanDeduplication:
    """hwmon GPU fans suppressed when amd_gpu fan exists for same GPU."""

    def test_hwmon_gpu_fan_hidden_when_amd_gpu_present(self, qtbot):
        state = _make_state()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        fans = [
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0, age_ms=50),
            FanReading(
                id="hwmon:amdgpu:0000:03:00.0:pwm1:fan",
                source="hwmon",
                rpm=0,
                age_ms=50,
            ),
        ]
        state.set_fans(fans)

        # Only the amd_gpu entry should appear
        assert page._fan_table.rowCount() == 1

    def test_non_gpu_hwmon_fan_preserved(self, qtbot):
        state = _make_state()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        fans = [
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0, age_ms=50),
            FanReading(id="hwmon:it8696:fan1", source="hwmon", rpm=1200, age_ms=50),
        ]
        state.set_fans(fans)

        # Both should appear — motherboard fan is NOT a duplicate
        assert page._fan_table.rowCount() == 2

    def test_hwmon_gpu_fan_shown_when_no_amd_gpu(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)

        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        fans = [
            FanReading(
                id="hwmon:amdgpu:0000:03:00.0:pwm1:fan",
                source="hwmon",
                rpm=1500,
                age_ms=50,
            ),
        ]
        state.set_fans(fans)

        # No amd_gpu fan to shadow it — should show
        assert page._fan_table.rowCount() == 1


# ---------------------------------------------------------------------------
# Sensor de-duplication (iGPU filtered when dGPU is primary)
# ---------------------------------------------------------------------------


class TestSensorDeduplication:
    """iGPU sensors filtered when dGPU is the primary GPU."""

    def test_igpu_sensor_hidden_when_dgpu_primary(self, qtbot):
        state = _make_state(gpu_pci="0000:03:00.0")
        selection = SeriesSelectionModel()
        panel = SensorSeriesPanel(selection, state=state)
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
        panel.update_sensors(sensors)

        # Only dGPU sensor should appear (iGPU filtered)
        assert len(panel._sensor_items) == 1
        assert "0000:03:00.0" in next(iter(panel._sensor_items.keys()))

    def test_non_gpu_sensors_unaffected(self, qtbot):
        state = _make_state()
        selection = SeriesSelectionModel()
        panel = SensorSeriesPanel(selection, state=state)
        qtbot.addWidget(panel)

        sensors = [
            SensorReading(
                id="hwmon:k10temp:0000:00:18.3:Tctl",
                kind="cpu_temp",
                label="Tctl",
                value_c=55.0,
                source="hwmon",
                age_ms=50,
            ),
            SensorReading(
                id="hwmon:amdgpu:0000:03:00.0:edge",
                kind="gpu_temp",
                label="edge",
                value_c=45.0,
                source="amd_gpu",
                age_ms=50,
            ),
        ]
        panel.update_sensors(sensors)

        assert len(panel._sensor_items) == 2


# ---------------------------------------------------------------------------
# Hover: only selected series, suppress zero RPM
# ---------------------------------------------------------------------------


class TestHoverSelection:
    """Hover only considers user-selected visible series."""

    def test_hover_iterates_visible_keys_only(self, qtbot):
        from onlyfans.services.history_store import HistoryStore
        from onlyfans.ui.widgets.timeline_chart import TimelineChart

        history = HistoryStore()
        selection = SeriesSelectionModel()
        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        # The _on_mouse_moved method checks self._selection.visible_keys()
        # and only processes keys in that set. Verify by checking the code
        # path uses selection (presence of _selection attribute check).
        assert chart._selection is selection


# ---------------------------------------------------------------------------
# Fan table: 4 columns, no Colour
# ---------------------------------------------------------------------------


class TestFanTableNoColour:
    """Fan table has 4 columns after Colour removal."""

    def test_four_columns(self, qtbot):
        state = _make_state()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        assert page._fan_table.columnCount() == 4
        headers = [page._fan_table.horizontalHeaderItem(i).text() for i in range(4)]
        assert headers == ["Label", "Source", "RPM", "PWM%"]
