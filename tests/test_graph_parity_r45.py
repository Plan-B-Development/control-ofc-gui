"""R45: Graph/panel parity — selection model seeded from displayable keys only."""

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
from onlyfans.services.history_store import HistoryStore
from onlyfans.services.series_selection import SeriesSelectionModel
from onlyfans.ui.pages.dashboard_page import DashboardPage


def _state_with_dgpu(pci: str = "0000:03:00.0") -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_capabilities(
        Capabilities(
            daemon_version="0.5.1",
            amd_gpu=AmdGpuCapability(
                present=True,
                display_label="9070XT",
                pci_id=pci,
                is_discrete=True,
            ),
        )
    )
    return state


class TestIgpuHiddenFromGraph:
    """iGPU sensors filtered from panel must NOT appear in graph selection."""

    def test_igpu_sensor_not_in_selection_model(self, qtbot):
        state = _state_with_dgpu("0000:03:00.0")
        history = HistoryStore()
        selection = SeriesSelectionModel()
        page = DashboardPage(state=state, history=history, selection=selection)
        qtbot.addWidget(page)

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
        state.set_sensors(sensors)

        # iGPU key should NOT be in the selection model
        known = selection.known_keys()
        assert "sensor:hwmon:amdgpu:0000:03:00.0:edge" in known
        assert "sensor:hwmon:amdgpu:0000:7b:00.0:edge" not in known

    def test_dgpu_sensor_in_selection_model(self, qtbot):
        state = _state_with_dgpu("0000:03:00.0")
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        sensors = [
            SensorReading(
                id="hwmon:amdgpu:0000:03:00.0:edge",
                kind="gpu_temp",
                label="edge",
                value_c=45.0,
                source="amd_gpu",
                age_ms=50,
            ),
        ]
        state.set_sensors(sensors)

        known = page._selection.known_keys()
        assert "sensor:hwmon:amdgpu:0000:03:00.0:edge" in known


class TestGraphPanelParity:
    """Graph draws only entities visible in the panel/table."""

    def test_non_displayable_fan_not_in_selection(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        fans = [
            FanReading(
                id="hwmon:it8696:fan3",
                source="hwmon",
                rpm=0,
                last_commanded_pwm=None,
                age_ms=50,
            ),
        ]
        state.set_fans(fans)

        # Non-displayable hwmon fan (rpm=0, no alias, no pwm) should not
        # appear in selection model
        known = page._selection.known_keys()
        assert "fan:hwmon:it8696:fan3:rpm" not in known

    def test_displayable_fan_in_selection(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        page = DashboardPage(state=state)
        qtbot.addWidget(page)

        fans = [
            FanReading(
                id="openfan:ch00",
                source="openfan",
                rpm=1200,
                age_ms=50,
            ),
        ]
        state.set_fans(fans)

        known = page._selection.known_keys()
        assert "fan:openfan:ch00:rpm" in known


class TestChartableKeysFromSelection:
    """Chart _chartable_keys uses selection model, not raw history."""

    def test_chartable_keys_matches_selection(self, qtbot):
        history = HistoryStore()
        selection = SeriesSelectionModel()
        from onlyfans.ui.widgets.timeline_chart import TimelineChart

        chart = TimelineChart(history, selection=selection)
        qtbot.addWidget(chart)

        # Seed selection with specific keys
        selection.update_known_keys(["sensor:cpu0", "fan:ch00:rpm"])

        keys = chart._chartable_keys()
        assert set(keys) == {"fan:ch00:rpm", "sensor:cpu0"}


class TestSelectionModelKnownKeys:
    """SeriesSelectionModel.known_keys() returns the right set."""

    def test_known_keys(self):
        sel = SeriesSelectionModel()
        sel.update_known_keys(["sensor:a", "sensor:b", "fan:c:rpm"])
        assert sel.known_keys() == {"sensor:a", "sensor:b", "fan:c:rpm"}

    def test_known_keys_empty(self):
        sel = SeriesSelectionModel()
        assert sel.known_keys() == set()
