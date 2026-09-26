"""`DC-g`: every surface that shows a sensor's class goes through one accessor.

Two call sites classified with ``classify_sensor``'s defaulted ``board_vendor``
and no overrides — the Dashboard series-panel tooltip and the Sensor Detail
dialog — and the Overview summary's low-confidence count ignored the overrides
too. So an ASUS ``CPUTIN`` read as a CPU input on the Dashboard while the
Overview row flagged it, and "Treat as coolant" changed the row but nothing
else. ``AppState.classify_sensor`` now supplies both inputs.

These tests drive each SURFACE, not the helper (the extracted-rule trap), and
compare against ``state.classify_sensor`` rather than a literal. Each first
asserts the bare classifier gives a different answer, so the case can only pass
through a call site that actually supplies the missing input.
"""

from __future__ import annotations

from html import escape

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    OperationMode,
    SensorReading,
    ThermalSafetyInfo,
)
from control_ofc.knowledge.sensor_knowledge import classify_sensor
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.overview_page import _S_LABEL, OverviewPage
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

ASUS = "ASUSTeK COMPUTER INC."


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _cputin() -> SensorReading:
    """On an ASUS board the whole nct67xx family's CPUTIN is bogus (AUD-x)."""
    return SensorReading(
        id="hwmon:nct6798:nct6775.656:CPUTIN",
        kind="cpu_temp",
        label="CPUTIN",
        value_c=41.0,
        chip_name="nct6798",
        source="hwmon",
        age_ms=100,
    )


def _wmi_channel() -> SensorReading:
    """A low-confidence channel the user can mark as coolant."""
    return SensorReading(
        id="hwmon:gigabyte_wmi:x:temp3",
        kind="mb_temp",
        label="temp3",
        value_c=33.0,
        chip_name="gigabyte_wmi",
        source="hwmon",
        age_ms=100,
    )


def _bare(s: SensorReading):
    return classify_sensor(s.chip_name, s.label, s.temp_type)


def _panel(qtbot, state: AppState) -> SensorSeriesPanel:
    panel = SensorSeriesPanel(SeriesSelectionModel(), state=state)
    qtbot.addWidget(panel)
    return panel


def _page(qtbot, state: AppState, *, board_vendor: str = "") -> OverviewPage:
    diag = DiagnosticsService(state)
    if board_vendor:
        diag.set_hw_diagnostics(
            HardwareDiagnosticsResult(
                thermal_safety=ThermalSafetyInfo(state="normal"),
                board=BoardInfo(vendor=board_vendor),
            )
        )
    page = OverviewPage(state=state, diagnostics_service=diag)
    qtbot.addWidget(page)
    return page


class TestAccessor:
    def test_supplies_the_board_vendor_and_the_overrides(self):
        state = _state()
        state.board_info = BoardInfo(vendor=ASUS)
        cputin = _cputin()
        assert state.classify_sensor(cputin) == classify_sensor(
            cputin.chip_name, cputin.label, cputin.temp_type, ASUS
        )
        assert state.classify_sensor(cputin).source_class == "bogus"

        wmi = _wmi_channel()
        state.set_sensor_class_override(wmi.id, "coolant")
        assert state.classify_sensor(wmi).source_class == "coolant"
        assert _bare(wmi).source_class != "coolant"


class TestSeriesPanelTooltip:
    def test_the_board_vendor_reaches_the_tooltip(self, qtbot):
        state = _state()
        state.board_info = BoardInfo(vendor=ASUS)
        panel = _panel(qtbot, state)
        s = _cputin()
        panel.update_sensors([s])

        want, bare = state.classify_sensor(s), _bare(s)
        assert want.display_description != bare.display_description  # precondition
        tip = panel._sensor_items[s.id].toolTip(0)
        assert escape(want.display_description, quote=False) in tip
        assert escape(bare.display_description, quote=False) not in tip

    def test_a_coolant_override_reaches_the_tooltip(self, qtbot):
        state = _state()
        panel = _panel(qtbot, state)
        s = _wmi_channel()
        state.set_sensor_class_override(s.id, "coolant")
        panel.update_sensors([s])

        want, bare = state.classify_sensor(s), _bare(s)
        assert want.display_description != bare.display_description  # precondition
        tip = panel._sensor_items[s.id].toolTip(0)
        assert escape(want.display_description, quote=False) in tip
        assert escape(want.notes[0], quote=False) in tip


class TestOverviewPage:
    def test_the_table_classifies_with_the_board_vendor(self, qtbot):
        state = _state()
        page = _page(qtbot, state, board_vendor=ASUS)
        assert state.board_info.vendor == ASUS  # through the one writer
        s = _cputin()
        page._on_sensors([s])
        assert _bare(s).source_class != "bogus"  # precondition
        assert page._sensor_table.item(0, _S_LABEL).text().startswith("⚠ ")

    def test_the_summary_counts_confidence_after_the_override(self, qtbot):
        state = _state()
        page = _page(qtbot, state)
        s = _wmi_channel()
        page._on_sensors([s])
        # Opposite arm first: unmarked, the channel IS low-confidence.
        assert _bare(s).confidence == "low"
        assert "1 low-confidence" in page._sensor_summary_label.text()

        page._set_sensor_class_override(s.id, "coolant")
        page._on_sensors([s])
        assert state.classify_sensor(s).confidence != "low"
        assert "low-confidence" not in page._sensor_summary_label.text()

    def test_the_detail_dialog_shows_the_overridden_class(self, qtbot):
        state = _state()
        page = _page(qtbot, state)
        s = _wmi_channel()
        page._on_sensors([s])
        page._set_sensor_class_override(s.id, "coolant")

        page._open_sensor_detail(s.id)
        dialog = page._sensor_detail_dialog
        assert dialog is not None
        text = dialog._browser.toPlainText()
        want, bare = state.classify_sensor(s), _bare(s)
        assert want.source_class != bare.source_class  # precondition
        assert f"{want.source_class} — " in text
        assert f"{bare.source_class} — " not in text
        page.cleanup()
