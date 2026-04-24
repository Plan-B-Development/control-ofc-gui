"""Diagnostics Batch A remediation tests — fan dedup (P1.1), Control method
column (P1.2), Overview hwmon runtime reality (P1.3), and sensor
classification wiring (P2.1).

Reference: DIAGNOSTICS_REMEDIATION.md.
"""

from __future__ import annotations

from control_ofc.api.models import (
    AmdGpuCapability,
    BoardInfo,
    Capabilities,
    ConnectionState,
    FanReading,
    FeatureFlags,
    HardwareDiagnosticsResult,
    HwmonCapability,
    HwmonDiagnostics,
    HwmonHeader,
    OperationMode,
    SensorReading,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.diagnostics_page import (
    _CONTROL_METHOD_TOOLTIPS,
    DiagnosticsPage,
)

# ─── Helpers ─────────────────────────────────────────────────────────


def _state(caps=None, hwmon_headers=None):
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    if caps is not None:
        s.set_capabilities(caps)
    if hwmon_headers is not None:
        s.set_hwmon_headers(hwmon_headers)
    return s


def _make_page(qtbot, state=None, diag=None):
    s = state if state is not None else _state()
    page = DiagnosticsPage(state=s, diagnostics_service=diag)
    qtbot.addWidget(page)
    # AppState.set_capabilities() in _state() emits before the page exists
    # and can listen for the signal. Re-apply after construction so the
    # Overview labels reflect the pre-populated state.
    if s.capabilities is not None:
        page._on_capabilities(s.capabilities)
    return page, s


def _caps_with_gpu(fan_control_method: str) -> Capabilities:
    return Capabilities(
        amd_gpu=AmdGpuCapability(
            present=True,
            display_label="9070XT",
            pci_id="0000:03:00.0",
            fan_control_method=fan_control_method,
        ),
    )


def _caps_with_hwmon(pwm_header_count: int = 3, write_support: bool = True) -> Capabilities:
    return Capabilities(
        hwmon=HwmonCapability(
            present=True,
            pwm_header_count=pwm_header_count,
            write_support=write_support,
            lease_required=True,
        ),
        features=FeatureFlags(hwmon_write_supported=write_support),
    )


def _diag_with_writable(
    writable: int, total: int = 3, board_vendor: str = ""
) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(total_headers=total, writable_headers=writable),
        thermal_safety=ThermalSafetyInfo(state="normal"),
        board=BoardInfo(vendor=board_vendor),
    )


# ─── P1.1 — Fan dedup (matches dashboard DEC-047) ────────────────────


class TestFanDedup:
    """_on_fans applies filter_displayable_fans so the GPU/hwmon overlap is
    collapsed to one row, matching the dashboard (DEC-047)."""

    def test_gpu_hwmon_overlap_collapsed_to_one_row(self, qtbot):
        """Classic regression case: amdgpu exposes the fan via both
        amd_gpu:<BDF> and hwmon:...:<BDF>:fan1. Only the amd_gpu row wins."""
        page, _ = _make_page(qtbot)
        fans = [
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0),
            FanReading(id="hwmon:amdgpu:0000:03:00.0:fan1", source="hwmon", rpm=0),
        ]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 1
        assert page._fan_table.item(0, 1).text() == "amd_gpu"

    def test_openfan_only_not_affected(self, qtbot):
        page, _ = _make_page(qtbot)
        fans = [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200),
            FanReading(id="openfan:ch01", source="openfan", rpm=800),
        ]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 2

    def test_amd_gpu_only_not_affected(self, qtbot):
        page, _ = _make_page(qtbot)
        fans = [FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 1

    def test_multi_gpu_not_coalesced(self, qtbot):
        """Two GPUs at different BDFs must both survive dedup — dedup is
        per-BDF, not per-source."""
        page, _ = _make_page(qtbot)
        fans = [
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0),
            FanReading(id="amd_gpu:0000:08:00.0", source="amd_gpu", rpm=0),
        ]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 2

    def test_hwmon_only_no_bdf_conflict(self, qtbot):
        """Pure motherboard hwmon fans (no GPU overlap) are rendered as-is."""
        page, _ = _make_page(qtbot)
        fans = [
            FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1200),
            FanReading(id="hwmon:nct6798:fan2", source="hwmon", rpm=900),
        ]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 2

    def test_gpu_row_still_renders_control_method(self, qtbot):
        """Dedup must preserve the surviving row's fan_control_method
        classification (integration with P1.2)."""
        state = _state(caps=_caps_with_gpu("pmfw_curve"))
        page, _ = _make_page(qtbot, state=state)
        fans = [
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0),
            FanReading(id="hwmon:amdgpu:0000:03:00.0:fan1", source="hwmon", rpm=0),
        ]
        page._on_fans(fans)
        assert page._fan_table.rowCount() == 1
        assert page._fan_table.item(0, 2).text() == "PMFW curve"


# ─── P1.2 — Control method column ────────────────────────────────────


class TestControlMethodColumnStructure:
    def test_fan_table_has_6_columns(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._fan_table.columnCount() == 6

    def test_control_method_header_label(self, qtbot):
        page, _ = _make_page(qtbot)
        header = page._fan_table.horizontalHeaderItem(2)
        assert header is not None
        assert header.text() == "Control method"

    def test_control_method_header_has_explanatory_tooltip(self, qtbot):
        page, _ = _make_page(qtbot)
        header = page._fan_table.horizontalHeaderItem(2)
        tip = header.toolTip()
        assert "read-only" in tip
        assert "BIOS/EC" in tip


class TestControlMethodFanRows:
    """Control method cell text for each fan/source combination."""

    def test_openfan_row(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200)])
        assert page._fan_table.item(0, 2).text() == "OpenFan USB"

    def test_hwmon_writable_row(self, qtbot):
        state = _state(
            hwmon_headers=[HwmonHeader(id="hwmon:nct6798:fan1", label="CPU", is_writable=True)]
        )
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1000)])
        assert page._fan_table.item(0, 2).text() == "hwmon PWM (lease)"

    def test_hwmon_readonly_row(self, qtbot):
        state = _state(
            hwmon_headers=[HwmonHeader(id="hwmon:nct6683:fan1", label="CPU", is_writable=False)]
        )
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:nct6683:fan1", source="hwmon", rpm=800)])
        assert page._fan_table.item(0, 2).text() == "read-only"

    def test_pwm_only_writable_row(self, qtbot):
        state = _state(
            hwmon_headers=[HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)]
        )
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([])
        assert page._fan_table.rowCount() == 1
        assert page._fan_table.item(0, 2).text() == "hwmon PWM — no RPM"

    def test_pwm_only_readonly_row(self, qtbot):
        state = _state(
            hwmon_headers=[HwmonHeader(id="hwmon:nct6798:pwm3", label="SYS", is_writable=False)]
        )
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([])
        assert page._fan_table.item(0, 2).text() == "read-only"

    def test_gpu_pmfw_curve_row(self, qtbot):
        state = _state(caps=_caps_with_gpu("pmfw_curve"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "PMFW curve"

    def test_gpu_hwmon_pwm_row(self, qtbot):
        state = _state(caps=_caps_with_gpu("hwmon_pwm"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:05:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "hwmon PWM (legacy)"

    def test_gpu_read_only_row(self, qtbot):
        state = _state(caps=_caps_with_gpu("read_only"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "read-only"

    def test_gpu_no_fan_control_row(self, qtbot):
        state = _state(caps=_caps_with_gpu("none"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "no fan control"

    def test_unknown_when_hwmon_header_missing(self, qtbot):
        """hwmon fan with no matching HwmonHeader in state → literal 'unknown'
        (no heuristic fallback)."""
        state = _state(hwmon_headers=[])
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1200)])
        assert page._fan_table.item(0, 2).text() == "unknown"

    def test_unknown_when_gpu_capabilities_missing(self, qtbot):
        """amd_gpu fan with no capabilities loaded → literal 'unknown'."""
        page, _ = _make_page(qtbot)  # default state has no capabilities
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "unknown"

    def test_unknown_for_unrecognised_gpu_method(self, qtbot):
        """Daemon could emit an unrecognised method (forward-compat) — the
        GUI must render 'unknown' rather than guessing."""
        state = _state(caps=_caps_with_gpu("future_method_we_dont_know"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert page._fan_table.item(0, 2).text() == "unknown"


class TestControlMethodTooltips:
    """Per-cell tooltip on the Control method column matches the plain-English
    spec in DIAGNOSTICS_REMEDIATION.md P1.2."""

    def test_readonly_tooltip_has_bios_revert_wording(self, qtbot):
        state = _state(hwmon_headers=[HwmonHeader(id="hwmon:x:fan1", is_writable=False)])
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:x:fan1", source="hwmon", rpm=0)])
        tip = page._fan_table.item(0, 2).toolTip()
        assert "BIOS/EC owns this fan" in tip
        assert "Test PWM Control" in tip

    def test_openfan_tooltip_says_no_lease(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_fans([FanReading(id="openfan:ch00", source="openfan", rpm=1200)])
        assert "No lease required" in page._fan_table.item(0, 2).toolTip()

    def test_pmfw_tooltip_mentions_fan_curve(self, qtbot):
        state = _state(caps=_caps_with_gpu("pmfw_curve"))
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert "fan_curve" in page._fan_table.item(0, 2).toolTip()

    def test_hwmon_writable_tooltip_mentions_lease(self, qtbot):
        state = _state(hwmon_headers=[HwmonHeader(id="hwmon:nct6798:fan1", is_writable=True)])
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1000)])
        assert "lease" in page._fan_table.item(0, 2).toolTip().lower()

    def test_unknown_tooltip_admits_no_classification(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_fans([FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0)])
        assert "not report" in page._fan_table.item(0, 2).toolTip().lower()

    def test_tooltip_dict_covers_every_emitted_string(self):
        """Every display string ``_fan_control_method`` /
        ``_pwm_only_control_method`` can emit must have a tooltip entry."""
        expected = {
            "OpenFan USB",
            "hwmon PWM (lease)",
            "hwmon PWM — no RPM",
            "hwmon PWM (legacy)",
            "PMFW curve",
            "read-only",
            "no fan control",
            "unknown",
        }
        missing = expected - set(_CONTROL_METHOD_TOOLTIPS.keys())
        assert not missing, f"Missing tooltip entries: {missing}"


class TestFanRowTooltip:
    def test_row_tooltip_includes_control_method(self, qtbot):
        """_fan_row_tooltip (used on non-control-method cells) also surfaces
        the control method."""
        state = _state(hwmon_headers=[HwmonHeader(id="hwmon:nct6798:fan1", is_writable=True)])
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1200)])
        # Column 0 gets the row tooltip (not the control-method tooltip)
        row_tip = page._fan_table.item(0, 0).toolTip()
        assert "Control method: hwmon PWM (lease)" in row_tip


# ─── P1.3 — Overview hwmon runtime reality ────────────────────────────


class TestOverviewHwmonRuntimeReality:
    def test_all_readonly_shows_warn_line_after_diagnostics(self, qtbot):
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        diag = DiagnosticsService(state)
        diag.last_hw_diagnostics = _diag_with_writable(writable=0, total=3)
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._refresh_hwmon_and_features(state.capabilities)
        assert "ALL read-only" in page._hwmon_label.text()

    def test_all_readonly_applies_warning_class(self, qtbot):
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        diag = DiagnosticsService(state)
        diag.last_hw_diagnostics = _diag_with_writable(writable=0, total=3)
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._refresh_hwmon_and_features(state.capabilities)
        assert page._hwmon_label.property("class") == "WarningChip"

    def test_writable_headers_clear_warning(self, qtbot):
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        diag = DiagnosticsService(state)
        # First — simulate past warn state
        diag.last_hw_diagnostics = _diag_with_writable(writable=0, total=3)
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._refresh_hwmon_and_features(state.capabilities)
        assert page._hwmon_label.property("class") == "WarningChip"

        # Now diagnostics update — headers become writable
        diag.last_hw_diagnostics = _diag_with_writable(writable=2, total=3)
        page._refresh_hwmon_and_features(state.capabilities)
        assert "ALL read-only" not in page._hwmon_label.text()
        assert page._hwmon_label.property("class") != "WarningChip"

    def test_hwmon_not_present_unchanged(self, qtbot):
        state = _state(caps=Capabilities(hwmon=HwmonCapability(present=False)))
        page, _ = _make_page(qtbot, state=state)
        assert "Not present" in page._hwmon_label.text()
        assert page._hwmon_label.property("class") != "WarningChip"

    def test_without_diagnostics_uses_legacy_line(self, qtbot):
        """Before HW diagnostics is fetched (``last_hw_diagnostics is None``),
        Overview renders the existing daemon-capability line."""
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        page, _ = _make_page(qtbot, state=state)
        assert page._hwmon_label.property("class") != "WarningChip"
        assert "write" in page._hwmon_label.text()
        assert "lease required" in page._hwmon_label.text()

    def test_features_reconciled_when_zero_writable(self, qtbot):
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        diag = DiagnosticsService(state)
        diag.last_hw_diagnostics = _diag_with_writable(writable=0, total=3)
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._refresh_hwmon_and_features(state.capabilities)
        assert "0 writable headers on this system" in page._features_label.text()

    def test_features_unchanged_when_writable(self, qtbot):
        state = _state(caps=_caps_with_hwmon(pwm_header_count=3, write_support=True))
        diag = DiagnosticsService(state)
        diag.last_hw_diagnostics = _diag_with_writable(writable=2, total=3)
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._refresh_hwmon_and_features(state.capabilities)
        text = page._features_label.text()
        assert "0 writable headers" not in text
        assert "hwmon writes" in text


# ─── P2.1 — Sensor classification ─────────────────────────────────────


class TestSensorsTableStructure:
    def test_sensors_table_has_7_columns(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._sensor_table.columnCount() == 7

    def test_chip_column_label(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._sensor_table.horizontalHeaderItem(2).text() == "Chip"

    def test_confidence_column_label(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._sensor_table.horizontalHeaderItem(6).text() == "Confidence"


class TestSensorClassification:
    def test_k10temp_tdie_high_confidence_on_screen(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:k10temp:Tdie",
                    kind="cpu_temp",
                    label="Tdie",
                    value_c=45.0,
                    chip_name="k10temp",
                )
            ]
        )
        # Confidence cell visible on-screen (not only in tooltip)
        assert page._sensor_table.item(0, 6).text() == "High"

    def test_chip_column_shows_driver_name(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:k10temp:Tdie",
                    kind="cpu_temp",
                    label="Tdie",
                    value_c=45.0,
                    chip_name="k10temp",
                )
            ]
        )
        assert page._sensor_table.item(0, 2).text() == "k10temp"

    def test_k10temp_tctl_tooltip_exposes_not_direct_note(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:k10temp:Tctl",
                    kind="cpu_temp",
                    label="Tctl",
                    value_c=45.0,
                    chip_name="k10temp",
                )
            ]
        )
        tip = page._sensor_table.item(0, 0).toolTip()
        assert "not a direct physical" in tip.lower()

    def test_asus_nct6776_cputin_low_confidence(self, qtbot):
        """Quirk path: board vendor = ASUS + chip nct6776 + label CPUTIN
        flips the classification from medium (base CPUTIN) to low (bogus)."""
        state = _state()
        diag = DiagnosticsService(state)
        diag.last_hw_diagnostics = _diag_with_writable(
            writable=3, total=3, board_vendor="ASUSTeK COMPUTER INC."
        )
        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:nct6776:CPUTIN",
                    kind="mobo_temp",
                    label="CPUTIN",
                    value_c=38.0,
                    chip_name="nct6776",
                )
            ]
        )
        assert page._sensor_table.item(0, 6).text() == "Low"
        tip = page._sensor_table.item(0, 0).toolTip()
        assert "ASUS" in tip

    def test_gigabyte_wmi_low_confidence(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:gigabyte_wmi:temp1",
                    kind="mobo_temp",
                    label="temp1",
                    value_c=30.0,
                    chip_name="gigabyte_wmi",
                )
            ]
        )
        assert page._sensor_table.item(0, 6).text() == "Low"

    def test_unknown_driver_low_confidence(self, qtbot):
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="hwmon:unknown_driver:temp1",
                    kind="mobo_temp",
                    label="temp1",
                    value_c=40.0,
                    chip_name="unknown_driver",
                )
            ]
        )
        assert page._sensor_table.item(0, 6).text() == "Low"

    def test_board_vendor_flows_from_diagnostics_into_classification(self, qtbot):
        """Without diagnostics, nct6776+CPUTIN classifies as medium; once
        diagnostics arrives with ASUS vendor, it flips to low. Proves the
        board_vendor path is wired through on_sensors."""
        state = _state()
        page, _ = _make_page(qtbot, state=state)
        sensor = SensorReading(
            id="hwmon:nct6776:CPUTIN",
            kind="mobo_temp",
            label="CPUTIN",
            value_c=38.0,
            chip_name="nct6776",
        )
        page._on_sensors([sensor])
        before = page._sensor_table.item(0, 6).text()

        page._diag.last_hw_diagnostics = _diag_with_writable(
            writable=3, total=3, board_vendor="ASUSTeK COMPUTER INC."
        )
        page._on_sensors([sensor])
        after = page._sensor_table.item(0, 6).text()

        assert before == "Medium"
        assert after == "Low"

    def test_missing_chip_column_renders_emdash(self, qtbot):
        """Sensors without chip_name (unlikely but possible) show em-dash,
        not empty string."""
        page, _ = _make_page(qtbot)
        page._on_sensors(
            [
                SensorReading(
                    id="raw:temp",
                    kind="mobo_temp",
                    label="Temp",
                    value_c=25.0,
                    chip_name="",
                )
            ]
        )
        assert page._sensor_table.item(0, 2).text() == "—"
