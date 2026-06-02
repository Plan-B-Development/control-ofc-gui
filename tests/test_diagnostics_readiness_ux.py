"""Integration tests for the Fans-tab readiness UX: verdict banner, auto-fetch,
'To fix' links, and the pop-out report (DEC-113)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from control_ofc.api.models import (
    AcpiConflictInfo,
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    OperationMode,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage
from control_ofc.ui.widgets.readiness_report import ReadinessReportDialog


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _healthy() -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor="", name="Generic"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
    )


def _problem() -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8689", expected_driver="it87", header_count=2)
            ],
            total_headers=2,
            writable_headers=0,
        ),
        board=BoardInfo(vendor="Gigabyte", name="X870"),
        kernel_modules=[],
        acpi_conflicts=[
            AcpiConflictInfo(io_range="0x290", claimed_by="ACPI", conflicts_with_driver="it87")
        ],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
    )


class _MockClient:
    """Minimal client exposing hardware_diagnostics() for auto-fetch tests."""

    def __init__(self, diag: HardwareDiagnosticsResult) -> None:
        self._diag = diag
        self.calls = 0

    def hardware_diagnostics(self) -> HardwareDiagnosticsResult:
        self.calls += 1
        return self._diag


def _page(qtbot, client=None) -> DiagnosticsPage:
    page = DiagnosticsPage(state=_state(), client=client)
    qtbot.addWidget(page)
    return page


class TestVerdictBanner:
    def test_healthy_verdict_text_and_class(self, qtbot):
        page = _page(qtbot)
        page._populate_hw_diagnostics(_healthy())
        assert "System ready" in page._readiness_verdict_label.text()
        assert page._readiness_verdict_label.property("class") == "SuccessChip"

    def test_problem_verdict_text_and_class(self, qtbot):
        page = _page(qtbot)
        page._populate_hw_diagnostics(_problem())
        assert "attention" in page._readiness_verdict_label.text()
        assert page._readiness_verdict_label.property("class") in ("WarningChip", "CriticalChip")


class TestFixGuidanceLabel:
    def test_hidden_when_healthy(self, qtbot):
        page = _page(qtbot)
        page._populate_hw_diagnostics(_healthy())
        assert page._fix_guidance_label.isHidden()

    def test_visible_with_links_when_problem(self, qtbot):
        page = _page(qtbot)
        page._populate_hw_diagnostics(_problem())
        assert not page._fix_guidance_label.isHidden()
        assert "To fix" in page._fix_guidance_label.text()
        assert "at your own risk" in page._fix_guidance_label.text()
        assert 'href="' in page._fix_guidance_label.text()


class TestClickableLinks:
    def test_rich_alert_labels_open_external_links(self, qtbot):
        page = _page(qtbot)
        for name in (
            "Diagnostics_Label_fixGuidance",
            "Diagnostics_Label_moduleCollisions",
            "Diagnostics_Label_revertCounts",
        ):
            label = page.findChild(QLabel, name)
            assert label is not None
            assert label.openExternalLinks() is True


class TestReportButton:
    def test_disabled_before_fetch_enabled_after(self, qtbot):
        page = _page(qtbot)
        assert page._open_report_btn.isEnabled() is False
        page._populate_hw_diagnostics(_healthy())
        assert page._open_report_btn.isEnabled() is True

    def test_open_report_creates_dialog(self, qtbot):
        page = _page(qtbot)
        page._diag.last_hw_diagnostics = _problem()
        page._open_readiness_report()
        assert isinstance(page._report_dialog, ReadinessReportDialog)
        assert page._report_dialog.isVisible()

    def test_open_report_noop_without_diagnostics(self, qtbot):
        page = _page(qtbot)
        page._diag.last_hw_diagnostics = None
        page._open_readiness_report()
        assert page._report_dialog is None


class TestAutoFetch:
    def test_fetches_once_on_fans_tab(self, qtbot):
        client = _MockClient(_healthy())
        page = _page(qtbot, client=client)
        # Simulate opening the Fans tab (index 2).
        page._on_diag_tab_changed(2)
        assert client.calls == 1
        assert page._diag.last_hw_diagnostics is not None
        # Switching away and back must not re-fetch.
        page._on_diag_tab_changed(0)
        page._on_diag_tab_changed(2)
        assert client.calls == 1

    def test_non_fans_tab_does_not_fetch(self, qtbot):
        client = _MockClient(_healthy())
        page = _page(qtbot, client=client)
        page._on_diag_tab_changed(0)
        assert client.calls == 0
