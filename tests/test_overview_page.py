"""DEC-209: OverviewPage — merged rendering + full-parity sensor actions.

Constructs the page directly (like the Diagnostics-tab tests) and drives the
poll handlers. Avoids triggering QMenu/modal exec; action effects are tested via
their methods, on the same shared services the Diagnostics tab uses.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    DaemonStatus,
    FanReading,
    HardwareDiagnosticsResult,
    HwmonHeader,
    OperationMode,
    SensorReading,
    SensorThresholds,
    ThermalSafetyInfo,
    UnavailableSensor,
)
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.pages.overview_page import _FAN_COLS, _S_CONF, _SENSOR_COLS, OverviewPage


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _settings() -> AppSettingsService:
    svc = AppSettingsService()
    svc._settings = AppSettings()
    svc.save = lambda: None  # type: ignore[method-assign]
    return svc


def _page(qtbot, *, settings=None, selection=None, client=None, board_vendor=""):
    s = _state()
    diag = DiagnosticsService(s)
    if board_vendor:
        diag.last_hw_diagnostics = HardwareDiagnosticsResult(
            thermal_safety=ThermalSafetyInfo(state="normal"),
            board=BoardInfo(vendor=board_vendor),
        )
    page = OverviewPage(
        state=s,
        diagnostics_service=diag,
        settings_service=settings,
        series_selection=selection,
        client=client,
    )
    qtbot.addWidget(page)
    return page, s


def _sensor(sid: str = "hwmon:k10temp:Tctl", **kw) -> SensorReading:
    return SensorReading(
        id=sid,
        kind=kw.get("kind", "cpu_temp"),
        label=kw.get("label", sid.split(":")[-1]),
        value_c=kw.get("value_c", 45.0),
        chip_name=kw.get("chip_name", "k10temp"),
        source=kw.get("source", "hwmon"),
        age_ms=kw.get("age_ms", 500),
        thresholds=kw.get("thresholds"),
    )


def test_sensor_table_is_eight_columns():
    assert _SENSOR_COLS == [
        "#",
        "Label",
        "Sensor ID",
        "Source class",
        "Chip",
        "Value (°C)",
        "Age (ms)",
        "Confidence",
    ]


def test_confidence_and_freshness_render_as_pills(qtbot):
    page, _ = _page(qtbot)
    page._on_sensors([_sensor()])
    conf = page._sensor_table.cellWidget(0, _S_CONF).findChild(StatusPill)
    assert conf is not None
    assert conf.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=500)])
    fresh = page._fan_table.cellWidget(0, 5).findChild(StatusPill)
    assert fresh is not None


def test_fan_and_sensor_table_column_structure(qtbot):
    """Re-pin the table column structure so a silent header reorder or removal is
    caught (audit 2026-07-15 Phase 5)."""
    page, _ = _page(qtbot)
    assert page._fan_table.columnCount() == len(_FAN_COLS)
    assert [
        page._fan_table.horizontalHeaderItem(i).text() for i in range(len(_FAN_COLS))
    ] == _FAN_COLS
    assert page._sensor_table.columnCount() == len(_SENSOR_COLS)
    assert [
        page._sensor_table.horizontalHeaderItem(i).text() for i in range(len(_SENSOR_COLS))
    ] == _SENSOR_COLS


def test_confidence_pill_state_reflects_classification(qtbot):
    """The confidence pill's state (not just its presence) follows the sensor
    classification — a trusted CPU sensor is 'ok', an ambiguous motherboard
    sensor is not (audit 2026-07-15 Phase 5)."""
    page, _ = _page(qtbot)
    page._on_sensors(
        [_sensor(sid="hwmon:k10temp:Tdie", chip_name="k10temp", label="Tdie", kind="cpu_temp")]
    )
    trusted = page._sensor_table.cellWidget(0, _S_CONF).findChild(StatusPill)
    trusted_state = trusted.state()
    assert trusted_state == "ok"
    assert trusted.text()  # non-empty confidence label

    page._on_sensors(
        [_sensor(sid="hwmon:nct6798:CPUTIN", chip_name="nct6798", label="CPUTIN", kind="temp")]
    )
    ambiguous = page._sensor_table.cellWidget(0, _S_CONF).findChild(StatusPill)
    assert ambiguous.state() == "neutral"
    assert ambiguous.state() != trusted_state


def test_hide_persists_and_makes_toggle_row(qtbot):
    settings = _settings()
    page, _ = _page(qtbot, settings=settings)
    page._on_sensors([_sensor(sid="a"), _sensor(sid="b")])
    page._set_sensor_hidden("a", True)
    assert settings.settings.diagnostics_hidden_sensor_ids == ["a"]
    assert page._sensor_table.rowCount() == 2  # 1 visible + toggle row
    assert page._is_hidden_toggle_row(1)
    page._set_sensor_hidden("a", False)
    assert settings.settings.diagnostics_hidden_sensor_ids == []


def test_mirror_hidden_pushes_to_series_selection(qtbot):
    settings = _settings()
    selection = SeriesSelectionModel()
    calls: list = []
    selection.set_visible = lambda key, vis: calls.append((key, vis))  # type: ignore[method-assign]
    page, _ = _page(qtbot, settings=settings, selection=selection)
    page._on_sensors([_sensor(sid="x")])
    page._set_sensor_hidden("x", True)
    page._mirror_hidden_to_dashboard()
    assert ("sensor:x", False) in calls


def test_coolant_override_uses_shared_state(qtbot):
    page, s = _page(qtbot)
    page._on_sensors([_sensor(sid="c")])
    page._set_sensor_class_override("c", "coolant")
    assert s.sensor_class_overrides.get("c") == "coolant"


def test_preferred_sensor_action(qtbot):
    class _Client:
        def __init__(self):
            self.cpu = None

        def set_preferred_cpu_sensor(self, sid):
            self.cpu = sid

    client = _Client()
    page, _ = _page(qtbot, client=client)
    page._set_preferred_sensor("hwmon:k10temp:Tctl", "cpu")
    assert client.cpu == "hwmon:k10temp:Tctl"
    # No client → no-op, no crash.
    page_no, _ = _page(qtbot, client=None)
    page_no._set_preferred_sensor("x", "cpu")


def test_double_click_and_return_open_detail(qtbot):
    page, _ = _page(qtbot)
    page._on_sensors([_sensor(sid="d")])
    page._on_sensor_cell_double_clicked(0, 0)
    assert page._sensor_detail_dialog is not None
    page._sensor_detail_dialog.close()
    page._sensor_detail_dialog = None
    page._on_sensor_return(0)
    assert page._sensor_detail_dialog is not None
    page.cleanup()
    assert page._sensor_detail_dialog is None


def test_alarm_paints_value(qtbot):
    page, _ = _page(qtbot)
    page._on_sensors([_sensor(value_c=95.0, thresholds=SensorThresholds(crit_c=90.0))])
    assert "⚠ ALARM" in page._sensor_table.item(0, 5).text()


def test_unavailable_panel(qtbot):
    page, _ = _page(qtbot)
    page._on_status(
        DaemonStatus(
            unavailable_sensors=[
                UnavailableSensor(
                    id="wifi", label="wifi", reason="ENETDOWN", unavailable_for_ms=5000
                )
            ]
        )
    )
    assert not page._unavailable_label.isHidden()  # isVisible() is False on an unshown page
    assert "wifi" in page._unavailable_label.text()


def test_summary_line(qtbot):
    page, _ = _page(qtbot)
    page._on_sensors(
        [_sensor(kind="cpu_temp"), _sensor(sid="g:edge", kind="gpu_temp", label="edge")]
    )
    assert page._sensor_summary_label.text().startswith("Sensors: 2 total")


def test_fan_table_pwm_only_synthesis(qtbot):
    page, s = _page(qtbot)
    s.set_hwmon_headers([HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)])
    page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=500)])
    assert page._fan_table.rowCount() == 2
    assert page._fan_table.item(1, 1).text() == "hwmon (PWM-only)"


def test_fan_row_carries_its_id_for_rename(qtbot):
    """DEC-227: the table shows no ID column, so the id rides on the row's first
    cell — that is the only way a right-click can find the fan it named."""
    page, s = _page(qtbot)
    s.set_hwmon_headers([HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)])
    page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=500)])
    assert page._row_to_fan_id(0) == "openfan:ch00"
    assert page._row_to_fan_id(1) == "hwmon:nct6798:pwm2"  # PWM-only rows too
    assert page._row_to_fan_id(99) == ""


def test_fan_context_menu_offers_rename_and_reset(qtbot):
    page, s = _page(qtbot)
    # Through set_fans, not _on_fans directly: the alias-change repaint re-reads
    # state.fans, which is how the live poll path populates it.
    s.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=500)])

    names = [a.objectName() for a in page.build_fan_menu("openfan:ch00").actions()]
    assert names == ["Overview_Action_renameFan"]

    s.apply_fan_rename("openfan:ch00", "Front Intake")
    names = [a.objectName() for a in page.build_fan_menu("openfan:ch00").actions()]
    assert "Overview_Action_resetFanName" in names
    # The rename repainted the table without waiting for a poll.
    assert page._fan_table.item(0, 0).text() == "Front Intake"

    assert page.build_fan_menu("") is None


def test_fan_row_shows_openfan_channel_label(qtbot):
    """An unnamed OpenFan channel reads as a channel, not a raw daemon id."""
    page, _ = _page(qtbot)
    page._on_fans([FanReading(id="openfan:ch03", source="openfan", rpm=1200, age_ms=500)])
    assert page._fan_table.item(0, 0).text() == "OpenFan CH3"


def test_cards_render_from_status_and_caps(qtbot):
    page, _ = _page(qtbot)
    page._on_status(DaemonStatus(overall_status="ok", uptime_seconds=61))
    assert page._daemon_status_label.text() == "Status: ok"
    assert page._daemon_status_pill.state() == "ok"
    assert page._daemon_uptime_label.text().startswith("Uptime: 1m")


def test_no_diagnostics_objectnames_leak(qtbot):
    page, _ = _page(qtbot)
    for child in page.findChildren(QWidget):
        assert not child.objectName().startswith("Diagnostics_"), child.objectName()


def test_set_theme_does_not_raise(qtbot):
    page, _ = _page(qtbot)
    page._on_sensors([_sensor()])
    page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1, age_ms=500)])
    page.set_theme(None)
    assert page._sensor_table.rowCount() == 1
