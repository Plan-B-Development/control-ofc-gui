"""Tests for v1.2.0 diagnostics: board info, vendor quirks, revert counts,
PWM verify, and support bundle enhancements."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    HwmonHeader,
    HwmonVerifyResult,
    HwmonVerifyState,
    KernelModuleInfo,
    OperationMode,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.hwmon_guidance import lookup_vendor_quirks
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_diag_result(
    *,
    board_vendor: str = "",
    board_name: str = "",
    bios_version: str = "",
    chips: list[HwmonChipInfo] | None = None,
    revert_counts: dict[str, int] | None = None,
) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=chips or [],
            total_headers=3,
            writable_headers=3,
            enable_revert_counts=revert_counts or {},
        ),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        board=BoardInfo(vendor=board_vendor, name=board_name, bios_version=bios_version),
    )


def _make_page(qtbot, state=None, diag=None, client=None):
    s = state or _make_state()
    page = DiagnosticsPage(state=s, diagnostics_service=diag, client=client)
    qtbot.addWidget(page)
    return page, s


# ---------------------------------------------------------------------------
# Vendor quirk lookup tests
# ---------------------------------------------------------------------------


class TestVendorQuirkLookup:
    def test_gigabyte_it8689_returns_critical(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8689")
        assert len(quirks) == 1
        assert quirks[0].severity == "critical"
        assert "SmartFan" in quirks[0].summary

    def test_gigabyte_it8696_returns_high(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert len(quirks) == 1
        assert quirks[0].severity == "high"

    def test_gigabyte_it8688_returns_high(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8688")
        assert len(quirks) == 1
        assert quirks[0].severity == "high"

    def test_msi_nct6687_returns_medium_and_high(self):
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        assert len(quirks) == 2
        severities = {q.severity for q in quirks}
        assert "medium" in severities
        assert "high" in severities

    def test_asus_nct679x_returns_medium(self):
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "nct6798")
        assert len(quirks) == 1
        assert quirks[0].severity == "medium"
        assert "ACPI" in quirks[0].summary

    def test_no_quirk_for_unknown_vendor(self):
        quirks = lookup_vendor_quirks("Unknown Vendor", "it8696")
        assert quirks == []

    def test_no_quirk_for_unmatched_chip(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "nct6798")
        assert quirks == []

    def test_empty_inputs_return_empty(self):
        assert lookup_vendor_quirks("", "it8696") == []
        assert lookup_vendor_quirks("Gigabyte", "") == []

    def test_quirk_has_details(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert len(quirks[0].details) > 0

    def test_case_insensitive_vendor(self):
        quirks = lookup_vendor_quirks("GIGABYTE TECHNOLOGY CO., LTD.", "it8696")
        assert len(quirks) == 1


# ---------------------------------------------------------------------------
# Board info parsing tests
# ---------------------------------------------------------------------------


class TestBoardInfoParsing:
    def test_parse_board_info(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "board": {
                "vendor": "Gigabyte Technology Co., Ltd.",
                "name": "X870E AORUS MASTER",
                "bios_version": "F13a",
            },
        }
        result = parse_hardware_diagnostics(data)
        assert result.board.vendor == "Gigabyte Technology Co., Ltd."
        assert result.board.name == "X870E AORUS MASTER"
        assert result.board.bios_version == "F13a"

    def test_parse_board_info_missing(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.board.vendor == ""
        assert result.board.name == ""

    def test_parse_revert_counts(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {
                "chips_detected": [],
                "total_headers": 1,
                "writable_headers": 1,
                "enable_revert_counts": {"it8696-isa-0a30/pwm1": 5},
            },
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.hwmon.enable_revert_counts == {"it8696-isa-0a30/pwm1": 5}

    def test_parse_revert_counts_empty(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.hwmon.enable_revert_counts == {}


# ---------------------------------------------------------------------------
# Verify result parsing tests
# ---------------------------------------------------------------------------


class TestVerifyResultParsing:
    def test_parse_effective(self):
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 178, "pwm_percent": 70, "rpm": 900},
            "test_pwm_percent": 70,
            "wait_seconds": 3,
            "details": "PWM accepted and RPM changed",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "effective"
        assert result.initial_state.rpm == 1200
        assert result.final_state.rpm == 900
        assert result.test_pwm_percent == 70

    def test_parse_reverted(self):
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "reverted",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "rpm": 1200},
            "final_state": {"pwm_enable": 2, "pwm_raw": 128, "rpm": 1200},
            "test_pwm_percent": 70,
            "wait_seconds": 3,
            "details": "BIOS reclaimed pwm_enable",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "reverted"
        assert result.initial_state.pwm_enable == 1
        assert result.final_state.pwm_enable == 2


# ---------------------------------------------------------------------------
# Diagnostics page — board info display
# ---------------------------------------------------------------------------


class TestDiagnosticsPageBoardInfo:
    def test_board_info_label_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_boardInfo")
        assert label is not None
        assert label.isHidden()

    def test_board_info_shown_on_populate(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            bios_version="F13a",
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_boardInfo")
        assert not label.isHidden()
        assert "Gigabyte" in label.text()
        assert "X870E" in label.text()
        assert "F13a" in label.text()

    def test_board_info_hidden_when_empty(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result()
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_boardInfo")
        assert label.isHidden()


# ---------------------------------------------------------------------------
# Diagnostics page — vendor quirk alerts
# ---------------------------------------------------------------------------


class TestDiagnosticsPageVendorQuirks:
    def test_vendor_quirk_label_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_vendorQuirk")
        assert label is not None
        assert label.isHidden()

    def test_vendor_quirk_shown_for_gigabyte_it8696(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            chips=[
                HwmonChipInfo(
                    chip_name="it8696",
                    expected_driver="it87",
                    header_count=5,
                )
            ],
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_vendorQuirk")
        assert not label.isHidden()
        assert "SmartFan" in label.text()

    def test_vendor_quirk_critical_for_gigabyte_it8689(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            chips=[
                HwmonChipInfo(
                    chip_name="it8689",
                    expected_driver="it87",
                    header_count=5,
                )
            ],
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_vendorQuirk")
        assert not label.isHidden()
        assert label.property("class") == "CriticalChip"

    def test_vendor_quirk_hidden_for_unknown_vendor(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Unknown Vendor",
            chips=[
                HwmonChipInfo(
                    chip_name="it8696",
                    expected_driver="it87",
                    header_count=5,
                )
            ],
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_vendorQuirk")
        assert label.isHidden()


# ---------------------------------------------------------------------------
# Diagnostics page — revert counts
# ---------------------------------------------------------------------------


class TestDiagnosticsPageRevertCounts:
    def test_revert_label_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_revertCounts")
        assert label is not None
        assert label.isHidden()

    def test_revert_counts_shown_when_present(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            revert_counts={"it8696-isa-0a30/pwm1": 7, "it8696-isa-0a30/pwm2": 3}
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_revertCounts")
        assert not label.isHidden()
        assert "7 revert(s)" in label.text()
        assert "3 revert(s)" in label.text()
        assert "watchdog" in label.text().lower()

    def test_revert_counts_hidden_when_empty(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(revert_counts={})
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_revertCounts")
        assert label.isHidden()


# ---------------------------------------------------------------------------
# Diagnostics page — PWM verify UI
# ---------------------------------------------------------------------------


class TestDiagnosticsPageVerifyUI:
    def test_verify_combo_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        combo = page.findChild(QComboBox, "Diagnostics_Combo_verifyHeader")
        assert combo is not None

    def test_verify_button_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_verifyPwm")
        assert btn is not None
        assert "PWM" in btn.text()

    def test_verify_result_label_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert label is not None
        assert label.isHidden()

    def test_verify_combo_populated_from_headers(self, qtbot):
        state = _make_state()
        state.set_hwmon_headers(
            [
                HwmonHeader(id="h1", label="CPU Fan", is_writable=True),
                HwmonHeader(id="h2", label="System Fan", is_writable=True),
                HwmonHeader(id="h3", label="Pump", is_writable=False),
            ]
        )
        page, _ = _make_page(qtbot, state=state)
        diag = _make_diag_result()
        page._populate_hw_diagnostics(diag)
        combo = page.findChild(QComboBox, "Diagnostics_Combo_verifyHeader")
        assert combo.count() == 2
        assert "CPU Fan" in combo.itemText(0)
        assert "System Fan" in combo.itemText(1)

    def test_verify_btn_disabled_when_no_writable_headers(self, qtbot):
        state = _make_state()
        state.set_hwmon_headers(
            [
                HwmonHeader(id="h1", label="Pump", is_writable=False),
            ]
        )
        page, _ = _make_page(qtbot, state=state)
        diag = _make_diag_result()
        page._populate_hw_diagnostics(diag)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_verifyPwm")
        assert not btn.isEnabled()

    def test_verify_no_lease_shows_message(self, qtbot):
        state = _make_state()
        state.set_hwmon_headers(
            [
                HwmonHeader(id="h1", label="Fan", is_writable=True),
            ]
        )
        client = MagicMock()
        page, _ = _make_page(qtbot, state=state, client=client)
        diag = _make_diag_result()
        page._populate_hw_diagnostics(diag)

        page._verify_combo.setCurrentIndex(0)
        page._run_pwm_verify()

        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "lease" in label.text().lower()

    def test_verify_shows_effective_result(self, qtbot):
        page, _ = _make_page(qtbot)
        result = HwmonVerifyResult(
            header_id="h1",
            result="effective",
            initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, rpm=900),
            test_pwm_percent=70,
            wait_seconds=3,
            details="PWM control working",
        )
        page._show_verify_result(result)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "working" in label.text().lower()
        assert label.property("class") == "SuccessChip"

    def test_verify_shows_reverted_result(self, qtbot):
        page, _ = _make_page(qtbot)
        result = HwmonVerifyResult(
            header_id="h1",
            result="reverted",
            initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=2, rpm=1200),
            test_pwm_percent=70,
            wait_seconds=3,
            details="BIOS reclaimed",
        )
        page._show_verify_result(result)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "overridden" in label.text().lower()
        assert label.property("class") == "CriticalChip"


# ---------------------------------------------------------------------------
# Support bundle — hardware diagnostics inclusion
# ---------------------------------------------------------------------------


class TestSupportBundleHwDiag:
    def test_bundle_includes_board_when_hw_diag_fetched(self, tmp_path):

        state = _make_state()
        diag_svc = DiagnosticsService(state)
        diag_svc.last_hw_diagnostics = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            bios_version="F13a",
            revert_counts={"h1": 3},
        )

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag_svc.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "hardware_diagnostics" in bundle
        assert bundle["hardware_diagnostics"]["board"]["vendor"] == (
            "Gigabyte Technology Co., Ltd."
        )
        assert bundle["hardware_diagnostics"]["hwmon"]["enable_revert_counts"] == {"h1": 3}

    def test_bundle_excludes_hw_diag_when_not_fetched(self, tmp_path):
        state = _make_state()
        diag_svc = DiagnosticsService(state)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag_svc.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "hardware_diagnostics" not in bundle
