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
    expected_chips: list[str] | None = None,
    kernel_detected_chips: list[str] | None = None,
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
        expected_chips=expected_chips or [],
        kernel_detected_chips=kernel_detected_chips or [],
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
        # DEC-106 added an IT8883/STEALTH-ICE medium entry that also
        # matches it8696. The original IT8696E SmartFan-6 HIGH entry
        # must still be present.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert any(q.severity == "high" for q in quirks)

    def test_gigabyte_it8688_returns_high(self):
        # DEC-106 added an IT8688E AM4 500-series INFO entry that also
        # matches. The original SmartFan-5 HIGH entry must still be present.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8688")
        assert any(q.severity == "high" for q in quirks)

    def test_msi_nct6687_returns_medium_and_high(self):
        # Original DB had exactly one medium + one high quirk. DEC-106
        # added an INFO auto-allowlist entry and a MEDIUM AM4 500-series
        # entry. The contract is "both severities are present", not the
        # exact total count.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        severities = {q.severity for q in quirks}
        assert "medium" in severities
        assert "high" in severities

    def test_asus_nct679x_returns_medium(self):
        # DEC-106 added an ASUS+NCT6798D INFO entry that also matches.
        # The original ACPI-conflict MEDIUM entry must still be present.
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "nct6798")
        acpi_medium = [q for q in quirks if q.severity == "medium" and "ACPI" in q.summary]
        assert acpi_medium, (
            f"Expected ASUS+NCT6798 MEDIUM ACPI quirk to still be present; "
            f"got: {[(q.severity, q.summary) for q in quirks]}"
        )

    def test_no_quirk_for_unknown_vendor(self):
        quirks = lookup_vendor_quirks("Unknown Vendor", "it8696")
        assert quirks == []

    def test_no_quirk_for_unmatched_chip(self):
        # No Gigabyte+nct6798 quirk has ever been seeded (Gigabyte boards
        # are ITE-based, not Nuvoton). DEC-106 did not change this — the
        # AM5 800-series ASRock NCT6798D quirks are vendor-keyed to
        # ASRock / ASUS, not Gigabyte.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "nct6798")
        assert quirks == []

    def test_empty_inputs_return_empty(self):
        assert lookup_vendor_quirks("", "it8696") == []
        assert lookup_vendor_quirks("Gigabyte", "") == []

    def test_quirk_has_details(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert len(quirks[0].details) > 0

    def test_case_insensitive_vendor(self):
        # The contract is "case-insensitive vendor matching works": an
        # all-uppercase vendor must return the same set of quirks as a
        # mixed-case vendor. Asserting set equality on summaries is
        # strictly stronger than the original exact-count assertion AND
        # survives future quirk additions, because both sides of the
        # comparison see the same DB.
        upper = lookup_vendor_quirks("GIGABYTE TECHNOLOGY CO., LTD.", "it8696")
        canonical = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert {q.summary for q in upper} == {q.summary for q in canonical}, (
            "Case-insensitive vendor lookup must return the SAME quirks "
            "as the canonical-cased lookup"
        )
        assert canonical, "Sanity: canonical lookup must itself return matches"


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
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "effective"
        assert result.initial_state.rpm == 1200
        assert result.final_state.rpm == 900
        assert result.test_pwm_percent == 70

    def test_parse_reverted(self):
        """Parse the daemon's pwm_enable_reverted payload (see
        daemon/src/api/handlers/hwmon_ctl.rs::classify_verify_result)."""
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "pwm_enable_reverted",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "rpm": 1200},
            "final_state": {"pwm_enable": 2, "pwm_raw": 128, "rpm": 1200},
            "test_pwm_percent": 70,
            "wait_seconds": 6,
            "details": "BIOS reclaimed pwm_enable",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "pwm_enable_reverted"
        assert result.initial_state.pwm_enable == 1
        assert result.final_state.pwm_enable == 2

    def test_parse_restore_failed_default_false(self):
        """Audit P2.5: ``restore_failed`` defaults to False when the daemon
        does not include the field (older daemons, or successful restores —
        the field is ``skip_serializing_if = "is_false"`` on the daemon side).
        """
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 178, "pwm_percent": 70, "rpm": 900},
            "test_pwm_percent": 70,
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
        }
        result = parse_hwmon_verify_result(data)
        assert result.restore_failed is False

    def test_parse_restore_failed_true(self):
        """Audit P2.5 regression: when the daemon's post-verify restore PWM
        write fails, ``restore_failed: true`` reaches the GUI dataclass and
        the diagnostics page can surface the warning. Previously the daemon
        silently swallowed the error and the GUI had no signal that the
        header was left at the verify test value.
        """
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 51, "pwm_percent": 20, "rpm": 700},
            "test_pwm_percent": 20,
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
            "restore_failed": True,
        }
        result = parse_hwmon_verify_result(data)
        assert result.restore_failed is True


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
    """DEC-158: vendor quirks render as per-advisory rows (a coloured severity
    badge + summary + collapsible detail) in ``Diagnostics_Container_advisories``,
    replacing the old single flat ``Diagnostics_Label_vendorQuirk``."""

    def test_advisory_container_exists_and_hidden(self, qtbot):
        page, _ = _make_page(qtbot)
        assert page._advisory_container is not None
        assert page._advisory_container.isHidden()
        assert page._advisory_rows == []

    def test_advisory_shown_for_gigabyte_it8696(self, qtbot):
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
        assert not page._advisory_container.isHidden()
        assert page._advisory_rows
        # HIGH SmartFan quirk sorts first.
        summary = page.findChild(QLabel, "Diagnostics_AdvisorySummary_0")
        assert "SmartFan" in summary.text()

    def test_advisory_critical_for_gigabyte_it8689(self, qtbot):
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
        assert not page._advisory_container.isHidden()
        badge = page.findChild(QLabel, "Diagnostics_AdvisoryBadge_0")
        assert badge.property("class") == "CriticalChip"
        assert "CRITICAL" in badge.text()

    def test_advisory_hidden_for_unknown_vendor(self, qtbot):
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
        assert page._advisory_container.isHidden()
        assert page._advisory_rows == []


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
        # Per-row body label carries the per-header counts (rich text).
        body = page.findChild(QLabel, "Diagnostics_Label_revertCounts")
        assert not body.isHidden()
        assert "7 revert(s)" in body.text()
        assert "3 revert(s)" in body.text()
        # The "watchdog" explanation moved to its own footnote label as part
        # of the v1.7.1 severity-ramp refactor — assert it on the new home so
        # the original intent (operator sees the watchdog explanation) holds.
        footnote = page.findChild(QLabel, "Diagnostics_Label_revertFootnote")
        assert footnote is not None
        assert not footnote.isHidden()
        assert "watchdog" in footnote.text().lower()

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
            wait_seconds=6,
            details="PWM control working",
        )
        page._show_verify_result(result)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "working" in label.text().lower()
        assert label.property("class") == "SuccessChip"

    def test_verify_shows_reverted_result(self, qtbot):
        """Daemon emits 'pwm_enable_reverted' — GUI must surface it as
        CriticalChip with the 'overridden' message. Regression test for the
        audit finding where status_map used short keys the daemon never sent."""
        page, _ = _make_page(qtbot)
        result = HwmonVerifyResult(
            header_id="h1",
            result="pwm_enable_reverted",
            initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=2, rpm=1200),
            test_pwm_percent=70,
            wait_seconds=6,
            details="BIOS reclaimed",
        )
        page._show_verify_result(result)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "overridden" in label.text().lower()
        assert label.property("class") == "CriticalChip"

    def test_verify_shows_clamped_result(self, qtbot):
        """Daemon emits 'pwm_value_clamped' — GUI must surface it as
        WarningChip with the 'clamped' message."""
        page, _ = _make_page(qtbot)
        result = HwmonVerifyResult(
            header_id="h1",
            result="pwm_value_clamped",
            initial_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            test_pwm_percent=70,
            wait_seconds=6,
            details="PWM register overridden",
        )
        page._show_verify_result(result)
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "clamped" in label.text().lower()
        assert label.property("class") == "WarningChip"


class TestDiagnosticsPageVerifyWorker:
    """Verify the 6-second hardware probe runs off the UI thread.

    Regression for the audit finding: verify_hwmon_pwm used to be called
    synchronously on the main thread, freezing the UI for ~6s during the test
    (raised from 3 s in DEC-101 — slow-spinning fans need more settle time).
    """

    def test_ensure_verify_worker_requires_socket_path(self, qtbot):
        """No worker is created when the client has no _socket_path."""
        page, _ = _make_page(qtbot, client=None)
        assert page._ensure_verify_worker() is False
        assert page._verify_thread is None
        assert page._verify_worker is None

    def test_ensure_verify_worker_creates_thread(self, qtbot):
        """When a client is supplied, the worker + thread are created once."""
        client = MagicMock()
        client._socket_path = "/tmp/fake.sock"
        page, _ = _make_page(qtbot, client=client)

        assert page._ensure_verify_worker() is True
        assert page._verify_thread is not None
        assert page._verify_worker is not None
        assert page._verify_thread.isRunning()

        # Idempotent — second call does not replace the thread.
        prev_thread = page._verify_thread
        assert page._ensure_verify_worker() is True
        assert page._verify_thread is prev_thread

        page.cleanup()
        assert page._verify_thread is None
        assert page._verify_worker is None

    def test_on_verify_ok_re_enables_button(self, qtbot):
        """Successful verify result re-enables the button and updates label."""
        page, _ = _make_page(qtbot)
        page._verify_btn.setEnabled(False)
        page._verify_btn.setText("Testing...")

        result = HwmonVerifyResult(
            header_id="h1",
            result="effective",
            initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, rpm=900),
            test_pwm_percent=70,
            wait_seconds=6,
            details="PWM control working",
        )
        page._on_verify_ok(result)

        assert page._verify_btn.isEnabled()
        assert page._verify_btn.text() == "Test PWM Control"
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()

    def test_on_verify_error_unavailable(self, qtbot):
        """Daemon-unavailable error surfaces as the unavailable message."""
        page, _ = _make_page(qtbot)
        page._verify_btn.setEnabled(False)
        page._verify_btn.setText("Testing...")

        page._on_verify_error("unavailable", "Daemon unavailable during verify")

        assert page._verify_btn.isEnabled()
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "unavailable" in label.text().lower()

    def test_on_verify_error_generic(self, qtbot):
        """Generic DaemonError surfaces the message with the 'Verify error:' prefix."""
        page, _ = _make_page(qtbot)
        page._on_verify_error("error", "lease expired")

        assert page._verify_btn.isEnabled()
        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        assert "lease expired" in label.text().lower()

    def test_cleanup_is_safe_when_no_worker_created(self, qtbot):
        """cleanup() must be a no-op when no verify has ever been run."""
        page, _ = _make_page(qtbot)
        assert page._verify_thread is None
        page.cleanup()  # must not raise
        assert page._verify_thread is None


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


# ---------------------------------------------------------------------------
# DEC-101 — verify wait timing safety constants
# ---------------------------------------------------------------------------


class TestVerifyTimingConstantsDec101:
    """Regression: GUI's safety auto-resume and HTTP timeout must always
    stay strictly above the daemon's verify wait. The daemon was bumped
    from 3 s to 6 s in DEC-101; if the GUI side is later changed without
    updating both the safety timer and the HTTP timeout, the 1 Hz control
    loop or the HTTP layer would race the daemon's readback.
    """

    def test_safety_timer_exceeds_daemon_verify_wait_with_slack(self):
        from control_ofc.services.control_loop import VERIFY_PAUSE_SAFETY_MS

        # Daemon's VERIFY_WAIT_SECONDS is 6 s — the safety timer must allow
        # at least 2 s slack for IPC + restore-PWM + classify, otherwise
        # the auto-resume can fire mid-verify.
        assert VERIFY_PAUSE_SAFETY_MS >= 8000, (
            f"VERIFY_PAUSE_SAFETY_MS={VERIFY_PAUSE_SAFETY_MS} ms must be ≥ 8000 ms "
            f"(daemon verify wait 6 s + 2 s slack). DEC-101."
        )

    def test_http_timeout_exceeds_daemon_verify_wait_with_slack(self):
        # The verify HTTP call is hard-coded to a 12 s per-call timeout so
        # the global API_TIMEOUT_S can stay aggressive for fast endpoints.
        # We assert the literal here so a future drift to <8 s is caught.
        import inspect

        from control_ofc.api.client import DaemonClient

        src = inspect.getsource(DaemonClient.verify_hwmon_pwm)
        # Look for the explicit timeout=NN.N kwarg.
        import re

        m = re.search(r"timeout\s*=\s*(\d+(?:\.\d+)?)", src)
        assert m is not None, "verify_hwmon_pwm must pass an explicit timeout"
        timeout_value = float(m.group(1))
        assert timeout_value >= 9.0, (
            f"verify_hwmon_pwm timeout={timeout_value} s must be ≥ 9 s "
            f"(daemon verify wait 6 s + ~3 s round-trip slack). DEC-101."
        )


# ---------------------------------------------------------------------------
# DEC-101 — dual-chip warning banner (Fans tab)
# ---------------------------------------------------------------------------


class TestDualChipWarningBannerDec101:
    """The dual-chip warning fires when DMI says the board has two ITE chips
    but the kernel only enumerated one (typically: secondary IT87952E on
    Gigabyte X670/X870/Z790 silently failed to bind). The banner must:
        - hide on boards the daemon doesn't know about (empty expected_chips)
        - hide when every expected chip is detected
        - appear with the missing chip name when one is missing
        - explain `mmio=on` remediation
    """

    def test_hidden_when_expected_chips_empty(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chips=[
                HwmonChipInfo(chip_name="it8696", header_count=5),
            ],
            expected_chips=[],  # Older daemon didn't ship the field.
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_dualChipWarning")
        assert label is not None
        assert label.isHidden()

    def test_hidden_when_all_expected_chips_present(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chips=[
                HwmonChipInfo(chip_name="it8696", header_count=5),
                HwmonChipInfo(chip_name="it87952", header_count=3),
            ],
            expected_chips=["it8696", "it87952"],
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_dualChipWarning")
        assert label.isHidden()

    def test_visible_when_secondary_chip_missing(self, qtbot):
        # The reference scenario — the user's actual machine with the
        # secondary IT87952E unbound.
        page, _ = _make_page(qtbot)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chips=[HwmonChipInfo(chip_name="it8696", header_count=5)],
            expected_chips=["it8696", "it87952"],
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_dualChipWarning")
        assert not label.isHidden()
        text = label.text()
        # Must name the missing chip, the board, and the remediation —
        # driver update first (DEC-144), with mmio=on retained as the
        # legacy-build fallback.
        assert "it87952" in text.lower() or "IT87952E" in text
        assert "X870E AORUS MASTER" in text
        assert "it87-dkms-git" in text
        assert "mmio=on" in text
        assert text.find("it87-dkms-git") < text.find("mmio=on"), (
            "DEC-144: the driver update must precede the legacy mmio=on step"
        )
        assert "modprobe" in text.lower() or "modprobe.d" in text.lower()
        # Must not crash on the WarningChip class assignment.
        assert label.property("class") == "WarningChip"


# ---------------------------------------------------------------------------
# DEC-101 — dual-chip verify hint (post-verify result)
# ---------------------------------------------------------------------------


class TestDualChipVerifyHintDec101:
    """When a `pwm_value_clamped` or `no_rpm_effect` outcome lands on a
    board that's missing one of its expected chips, the verify result
    panel appends a one-line pointer to the dual-chip warning. Other
    outcomes / boards are unchanged.
    """

    def test_clamped_on_dual_chip_board_appends_hint(self, qtbot):
        # Set up state with a hwmon header so chip_name resolves.
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon:it8696:0a40:pwm1", chip_name="it8696", is_writable=True),
        ]
        page, _ = _make_page(qtbot, state=state)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chips=[HwmonChipInfo(chip_name="it8696", header_count=5)],
            expected_chips=["it8696", "it87952"],
        )
        # Push the diagnostics into the service so _show_verify_result can
        # see the board context.
        page._diag.last_hw_diagnostics = diag
        # Populate to make the warning visible (also exercises the
        # populate path so a regression there blows up loudly).
        page._populate_hw_diagnostics(diag)

        result = HwmonVerifyResult(
            header_id="hwmon:it8696:0a40:pwm1",
            result="pwm_value_clamped",
            initial_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            test_pwm_percent=70,
            wait_seconds=6,
            details="PWM register overridden",
        )
        page._show_verify_result(result)

        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        assert not label.isHidden()
        text = label.text().lower()
        assert "dual-chip" in text or "fans tab" in text

    def test_effective_does_not_append_hint(self, qtbot):
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon:it8696:0a40:pwm1", chip_name="it8696", is_writable=True),
        ]
        page, _ = _make_page(qtbot, state=state)
        diag = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chips=[HwmonChipInfo(chip_name="it8696", header_count=5)],
            expected_chips=["it8696", "it87952"],
        )
        page._diag.last_hw_diagnostics = diag

        result = HwmonVerifyResult(
            header_id="hwmon:it8696:0a40:pwm1",
            result="effective",
            initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, rpm=900),
            test_pwm_percent=70,
            wait_seconds=6,
            details="working",
        )
        page._show_verify_result(result)

        label = page.findChild(QLabel, "Diagnostics_Label_verifyResult")
        text = label.text().lower()
        assert "dual-chip" not in text
        assert "fans tab" not in text


# ---------------------------------------------------------------------------
# DEC-101 — batch verify all writable headers (2E)
# ---------------------------------------------------------------------------


class TestVerifyAllBatchDec101:
    """The batch verify button must:
    - refuse to run without a held lease (no spurious 403s on the daemon)
    - run with no double-click duplication
    - aggregate results in a summary
    - abort cleanly if the lease is lost mid-run
    """

    def test_button_exists_and_has_object_name(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_verifyAll")
        assert btn is not None
        assert btn.text() == "Verify All Writable"

    def test_run_without_lease_shows_error(self, qtbot):
        # No lease held → the progress label must explain instead of
        # firing an unauthorised verify request.
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon:it8696:0a40:pwm1", chip_name="it8696", is_writable=True),
        ]
        client = MagicMock()
        client._socket_path = "/tmp/fake.sock"
        page, _ = _make_page(qtbot, state=state, client=client)
        page._run_pwm_verify_all()

        label = page.findChild(QLabel, "Diagnostics_Label_verifyAllProgress")
        assert label is not None
        assert not label.isHidden()
        assert "lease" in label.text().lower()
        # No batch state must persist after a refused start.
        assert page._verify_all_total == 0

    def test_run_without_writable_headers_shows_message(self, qtbot):
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon:it8696:0a40:pwm1", chip_name="it8696", is_writable=False),
        ]
        # Fake an active lease.
        state.lease.held = True
        state.lease.lease_id = "test-lease"
        client = MagicMock()
        client._socket_path = "/tmp/fake.sock"
        page, _ = _make_page(qtbot, state=state, client=client)

        page._run_pwm_verify_all()
        label = page.findChild(QLabel, "Diagnostics_Label_verifyAllProgress")
        assert "no writable" in label.text().lower()

    def test_summary_renders_results(self, qtbot):
        # Drive _show_verify_all_summary directly to check rendering.
        page, _ = _make_page(qtbot)
        page._verify_all_total = 2
        page._verify_all_results = [
            ("hwmon:a:pwm1", "effective"),
            ("hwmon:a:pwm2", "pwm_value_clamped"),
        ]
        page._show_verify_all_summary()
        label = page.findChild(QLabel, "Diagnostics_Label_verifyAllProgress")
        text = label.text()
        assert "hwmon:a:pwm1" in text
        assert "hwmon:a:pwm2" in text
        assert "OK" in text
        assert "clamped" in text
        # Mixed warning result → WarningChip class.
        assert label.property("class") == "WarningChip"


# ---------------------------------------------------------------------------
# DEC-101 — model parsing for new daemon fields
# ---------------------------------------------------------------------------


class TestHardwareDiagnosticsModelDec101:
    """The Python parser must accept and store the new
    `expected_chips` / `kernel_detected_chips` fields, default them to
    [] when absent, and tolerate non-string list members defensively.
    """

    def test_parse_expected_chips(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "expected_chips": ["it8696", "it87952"],
            "kernel_detected_chips": ["it8696"],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == ["it8696", "it87952"]
        assert result.kernel_detected_chips == ["it8696"]

    def test_missing_fields_default_to_empty(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == []
        assert result.kernel_detected_chips == []

    def test_filters_falsy_entries(self):
        # Defensive: future shape drift could send None/"" entries; the
        # parser must drop them rather than propagate.
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "expected_chips": ["it8696", "", None, "it87952"],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == ["it8696", "it87952"]


# ---------------------------------------------------------------------------
# DEC-101 — dual_chip_warning_html / dual_chip_verify_hint pure functions
# ---------------------------------------------------------------------------


class TestDualChipHelpersDec101:
    def test_warning_lists_missing_chip_pretty_name(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        out = dual_chip_warning_html(
            "X870E AORUS MASTER",
            ["it8696", "it87952"],
            ["it8696"],
        )
        assert out is not None
        # Pretty-names render as IT8696E / IT87952E so users can match
        # the silkscreen on their motherboard.
        assert "IT87952E" in out
        assert "X870E AORUS MASTER" in out

    def test_warning_returns_none_for_unknown_board(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        # No expected chips → no warning regardless of detected list.
        assert dual_chip_warning_html("Some Other Board", [], []) is None
        assert dual_chip_warning_html("Some Other Board", [], ["it8696"]) is None

    def test_warning_returns_none_when_all_present(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        assert (
            dual_chip_warning_html(
                "X870E AORUS MASTER",
                ["it8696", "it87952"],
                ["it8696", "it87952"],
            )
            is None
        )

    def test_verify_hint_only_for_clamped_or_no_rpm(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint

        for result in ("pwm_value_clamped", "no_rpm_effect"):
            assert dual_chip_verify_hint(result, ["it8696", "it87952"], ["it8696"]) is not None
        for result in ("effective", "pwm_enable_reverted", "rpm_unavailable"):
            assert dual_chip_verify_hint(result, ["it8696", "it87952"], ["it8696"]) is None

    def test_verify_hint_none_when_no_dual_chip(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint

        # Single-chip board → never a dual-chip hint.
        assert dual_chip_verify_hint("pwm_value_clamped", ["it8696"], ["it8696"]) is None
        # Empty expected_chips → never a hint.
        assert dual_chip_verify_hint("pwm_value_clamped", [], []) is None
