"""Tests for the DEC-117 Diagnostics > Sensors tab expansion.

Covers the new 14-column layout, header summary line, inline quirk chips,
hidden-group toggle behaviour, per-row Details button + double-click +
right-click menu, and the AppSettings persistence of the hide-list.

Existing classification / column-index tests live in
``test_diagnostics_enumeration.py`` — those use the new ``_col(page, …)``
helper added in the same change.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    OperationMode,
    SensorReading,
    SensorThresholds,
    ThermalSafetyInfo,
)
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.diagnostics_page import (
    _SENSOR_COL_INDEX,
    DiagnosticsPage,
)


def _state():
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _settings_service() -> AppSettingsService:
    """Return a fresh AppSettingsService with the load() side-effect skipped.

    The default AppSettingsService writes to disk on ``update`` — the test
    fixture mocks ``save`` so persistence is in-memory only. This keeps the
    test isolated AND verifies the same in-memory ``settings`` mutation
    real users see.
    """
    svc = AppSettingsService()
    svc._settings = AppSettings()
    svc.save = lambda: None  # type: ignore[method-assign]
    return svc


def _make_page(qtbot, *, settings=None, selection=None, board_vendor: str = ""):
    s = _state()
    diag = DiagnosticsService(s)
    if board_vendor:
        diag.last_hw_diagnostics = HardwareDiagnosticsResult(
            thermal_safety=ThermalSafetyInfo(state="normal"),
            board=BoardInfo(vendor=board_vendor),
        )
    page = DiagnosticsPage(
        state=s,
        diagnostics_service=diag,
        settings_service=settings,
        series_selection=selection,
    )
    qtbot.addWidget(page)
    return page, s


def _sensor(sid: str, **kw) -> SensorReading:
    """Concise SensorReading constructor for fixtures."""
    return SensorReading(
        id=sid,
        kind=kw.get("kind", "cpu_temp"),
        label=kw.get("label", sid.split(":")[-1]),
        value_c=kw.get("value_c", 45.0),
        chip_name=kw.get("chip_name", "k10temp"),
        source=kw.get("source", "hwmon"),
        age_ms=kw.get("age_ms", 500),
        rate_c_per_s=kw.get("rate_c_per_s"),
        session_min_c=kw.get("session_min_c"),
        session_max_c=kw.get("session_max_c"),
        temp_type=kw.get("temp_type"),
        thresholds=kw.get("thresholds"),
    )


# ─── Header summary line ─────────────────────────────────────────────────


class TestSensorSummaryLine:
    def test_summary_reports_counts_per_kind(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                _sensor("hwmon:k10temp:Tctl", kind="cpu_temp"),
                _sensor("hwmon:nct6798:CPUTIN", kind="mb_temp", chip_name="nct6798"),
                _sensor("amd_gpu:0000:03:00.0:edge", kind="gpu_temp", chip_name="amdgpu"),
                _sensor(
                    "hwmon:nvme:Composite",
                    kind="disk_temp",
                    chip_name="nvme",
                ),
            ]
        )
        text = page._sensor_summary_label.text()
        assert "4 total" in text
        assert "1 CPU" in text
        assert "1 board" in text
        assert "1 GPU" in text
        assert "1 disk" in text

    def test_summary_emdash_when_empty(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([])
        assert "—" in page._sensor_summary_label.text()

    def test_summary_includes_low_confidence_count(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:gigabyte_wmi:temp1", chip_name="gigabyte_wmi")])
        # gigabyte_wmi is classified low-confidence
        assert "1 low-confidence" in page._sensor_summary_label.text()

    def test_summary_includes_hidden_count(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        assert "1 hidden" in page._sensor_summary_label.text()


# ─── Quirk / advisory chips ──────────────────────────────────────────────


class TestQuirkChips:
    def test_bogus_sensor_renders_warning_prefix(self, qtbot):
        """ASUS NCT6776F CPUTIN is the canonical bogus-quirk case.

        The Label cell should prefix the displayed label with the unicode
        warning glyph and switch its colour to the theme's status_warn.
        """
        page, _ = _make_page(qtbot, board_vendor="ASUSTeK COMPUTER INC.")
        page._on_sensors([_sensor("hwmon:nct6776:CPUTIN", chip_name="nct6776", label="CPUTIN")])
        label_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Label"]).text()
        assert label_text.startswith("⚠")

    def test_low_confidence_renders_question_prefix(self, qtbot):
        """Non-bogus low-confidence sensors get a softer '?' prefix so the
        bogus '⚠' chip still stands out as the more severe signal."""
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [_sensor("hwmon:gigabyte_wmi:temp1", chip_name="gigabyte_wmi", label="temp1")]
        )
        label_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Label"]).text()
        assert label_text.startswith("?")

    def test_high_confidence_label_has_no_prefix(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tdie", chip_name="k10temp", label="Tdie")])
        assert page._sensor_table.item(0, _SENSOR_COL_INDEX["Label"]).text() == "Tdie"


# ─── Alarm chip on Value cell ────────────────────────────────────────────


class TestAlarmChip:
    def test_crit_alarm_asserted_shows_alarm_suffix(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                _sensor(
                    "hwmon:k10temp:Tctl",
                    value_c=78.0,
                    thresholds=SensorThresholds(crit_c=105.0, crit_alarm=True),
                )
            ]
        )
        value_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Value (°C)"]).text()
        assert "ALARM" in value_text

    def test_value_at_or_above_crit_shows_alarm_even_without_bit(self, qtbot):
        """The alarm bit is sampled at daemon discovery — temperature can
        cross crit before the bit re-reads. We treat ``value_c >= crit_c``
        as ALARM so the GUI never silently displays a "safe" state when the
        live reading proves otherwise."""
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                _sensor(
                    "hwmon:amdgpu:edge",
                    value_c=110.0,
                    thresholds=SensorThresholds(crit_c=105.0),
                )
            ]
        )
        value_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Value (°C)"]).text()
        assert "ALARM" in value_text

    def test_no_alarm_below_crit_without_alarm_bit(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                _sensor(
                    "hwmon:amdgpu:edge",
                    value_c=60.0,
                    thresholds=SensorThresholds(crit_c=105.0),
                )
            ]
        )
        value_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Value (°C)"]).text()
        assert "ALARM" not in value_text


# ─── Trend + session-range cells ─────────────────────────────────────────


class TestTrendAndSessionCells:
    def test_trend_renders_arrow_and_rate(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl", rate_c_per_s=0.6)])
        trend_text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Trend"]).text()
        assert trend_text.startswith("↑")
        assert "0.6" in trend_text

    def test_trend_suppresses_quiet_rate(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl", rate_c_per_s=0.05)])
        # Below 0.1 °C/s → em-dash (matches the tooltip suppression rule).
        assert page._sensor_table.item(0, _SENSOR_COL_INDEX["Trend"]).text() == "—"

    def test_session_range_formatted_with_min_max(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl", session_min_c=20.0, session_max_c=78.5)])
        text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Session min/max"]).text()
        assert "20.0" in text
        assert "78.5" in text

    def test_session_range_emdash_when_unavailable(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        assert page._sensor_table.item(0, _SENSOR_COL_INDEX["Session min/max"]).text() == "—"


# ─── Driver type column ──────────────────────────────────────────────────


class TestDriverTypeColumn:
    def test_amd_tsi_temp_type_renders_label(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [_sensor("hwmon:nct6683:AMD TSI Addr 98h", chip_name="nct6683", temp_type=5)]
        )
        text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Driver type"]).text()
        assert "AMD TSI" in text

    def test_missing_temp_type_renders_emdash(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl", temp_type=None)])
        assert page._sensor_table.item(0, _SENSOR_COL_INDEX["Driver type"]).text() == "—"


# ─── Hidden-group + Mirror to dashboard ──────────────────────────────────


class TestHideAndMirror:
    def test_hidden_sensor_collapses_into_toggle_row(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors(
            [
                _sensor("hwmon:k10temp:Tctl"),
                _sensor("hwmon:nct6798:CPUTIN", chip_name="nct6798"),
            ]
        )
        # One visible sensor + one toggle row, hidden group collapsed.
        assert page._sensor_table.rowCount() == 2
        # The toggle row is at index 1 and spans all columns.
        assert page._is_hidden_toggle_row(1)
        toggle_text = page._sensor_table.item(1, 0).text()
        assert "1 hidden sensor" in toggle_text
        assert "expand" in toggle_text.lower()

    def test_expand_then_collapse_round_trip(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl"), _sensor("hwmon:k10temp:Tccd1")])
        # Collapsed: 1 visible + 1 toggle = 2 rows.
        assert page._sensor_table.rowCount() == 2

        page._toggle_hidden_group()
        # Expanded: 1 visible + 1 toggle + 1 hidden = 3 rows.
        assert page._sensor_table.rowCount() == 3

        page._toggle_hidden_group()
        assert page._sensor_table.rowCount() == 2

    def test_set_sensor_hidden_persists_to_settings(self, qtbot):
        svc = _settings_service()
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl"), _sensor("hwmon:k10temp:Tccd1")])

        page._set_sensor_hidden("hwmon:k10temp:Tctl", True)
        assert "hwmon:k10temp:Tctl" in svc.settings.diagnostics_hidden_sensor_ids

        page._set_sensor_hidden("hwmon:k10temp:Tctl", False)
        assert "hwmon:k10temp:Tctl" not in svc.settings.diagnostics_hidden_sensor_ids

    def test_mirror_button_hidden_without_selection_model(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._mirror_hidden_btn.isVisible() is False

    def test_mirror_button_pushes_hide_list_to_selection_model(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        selection = SeriesSelectionModel()
        # The selection model only acts on keys it knows about — register
        # the key for the same sensor first to mirror the real flow.
        selection.update_known_keys(["sensor:hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc, selection=selection)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])

        page._mirror_hidden_to_dashboard()
        assert "sensor:hwmon:k10temp:Tctl" not in selection.visible_keys()


# ─── Per-row Details button + double-click + context menu ────────────────


class TestRowInteractions:
    def test_details_button_present_on_each_row(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        widget = page._sensor_table.cellWidget(0, _SENSOR_COL_INDEX["Details"])
        assert widget is not None
        assert widget.text() == "Details"

    def test_double_click_opens_sensor_detail_dialog(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        page._on_sensor_cell_double_clicked(0, _SENSOR_COL_INDEX["Label"])
        assert page._sensor_detail_dialog is not None
        # The dialog title carries the sensor label.
        assert "Tctl" in page._sensor_detail_dialog.windowTitle()
        page._sensor_detail_dialog.accept()

    def test_details_button_click_opens_dialog(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        page._open_sensor_detail("hwmon:k10temp:Tctl")
        assert page._sensor_detail_dialog is not None
        page._sensor_detail_dialog.accept()

    def test_double_click_toggle_row_flips_expander(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl"), _sensor("hwmon:k10temp:Tccd1")])
        # toggle row is index 1
        assert page._hidden_group_expanded is False
        page._on_sensor_cell_double_clicked(1, 0)
        assert page._hidden_group_expanded is True

    def test_row_to_sensor_resolves_visible_row(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl")])
        s = page._row_to_sensor(0)
        assert s is not None
        assert s.id == "hwmon:k10temp:Tctl"

    def test_row_to_sensor_returns_none_for_toggle_row(self, qtbot):
        svc = _settings_service()
        svc.update(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl"])
        page, _ = _make_page(qtbot, settings=svc)
        page._on_sensors([_sensor("hwmon:k10temp:Tctl"), _sensor("hwmon:k10temp:Tccd1")])
        assert page._row_to_sensor(1) is None  # toggle row


# ─── Source class display lookup ─────────────────────────────────────────


class TestSourceClassColumn:
    def test_known_source_class_uses_pretty_display(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors([_sensor("hwmon:k10temp:Tdie", chip_name="k10temp", label="Tdie")])
        text = page._sensor_table.item(0, _SENSOR_COL_INDEX["Source class"]).text()
        # k10temp Tdie → source_class="cpu_die" → display "CPU die"
        assert text == "CPU die"


# ─── DEC-117 settings round-trip ─────────────────────────────────────────


class TestSettingsRoundTrip:
    def test_hidden_list_round_trips_through_appsettings_dict(self):
        s = AppSettings(diagnostics_hidden_sensor_ids=["hwmon:k10temp:Tctl", "amd_gpu:edge"])
        data = s.to_dict()
        loaded = AppSettings.from_dict(data)
        assert loaded.diagnostics_hidden_sensor_ids == ["hwmon:k10temp:Tctl", "amd_gpu:edge"]

    def test_hidden_list_defaults_to_empty_when_absent(self):
        s = AppSettings.from_dict({})
        assert s.diagnostics_hidden_sensor_ids == []
