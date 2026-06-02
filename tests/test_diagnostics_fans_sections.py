"""Tests for the Diagnostics > Fans tab progressive-disclosure layout (DEC-112).

The dense Hardware Readiness detail is grouped into collapsible sections so the
always-relevant summary, critical alerts, and live fan table stay visible. These
tests lock down:
  * the five collapsible sections exist and start collapsed,
  * each section actually contains the widgets it should,
  * always-visible widgets (summary + alert stack) are NOT trapped inside any
    collapsed section,
  * the outer Diagnostics_Splitter_fans contract is preserved, and
  * the BIOS-interference section auto-expands when (and only when) there is a
    real revert count, and survives a theme refresh.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from control_ofc.api.models import (
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
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

SECTION_NAMES = [
    "Diagnostics_Section_detectedHardware",
    "Diagnostics_Section_biosInterference",
    "Diagnostics_Section_thermalGpu",
    "Diagnostics_Section_guidance",
    "Diagnostics_Section_pwmTest",
]

SECTION_MEMBERS = {
    "Diagnostics_Section_detectedHardware": [
        "Diagnostics_Table_chips",
        "Diagnostics_Table_kernelModules",
    ],
    "Diagnostics_Section_biosInterference": [
        "Diagnostics_Label_revertCounts",
        "Diagnostics_Label_revertFootnote",
    ],
    "Diagnostics_Section_thermalGpu": [
        "Diagnostics_Label_thermalSafety",
        "Diagnostics_Label_gpuDiag",
    ],
    "Diagnostics_Section_guidance": [
        "Diagnostics_Label_guidance",
        "Diagnostics_Label_docsLink",
    ],
    "Diagnostics_Section_pwmTest": [
        "Diagnostics_Combo_verifyHeader",
        "Diagnostics_Btn_verifyPwm",
        "Diagnostics_Btn_verifyAll",
        "Diagnostics_Label_verifyAllProgress",
        "Diagnostics_Label_verifyResult",
    ],
}

# Widgets that must remain visible without expanding anything — the readiness
# summary, board identity, and the whole critical-alert stack.
ALWAYS_VISIBLE_NAMES = [
    "Diagnostics_Label_hwReadySummary",
    "Diagnostics_Label_boardInfo",
    "Diagnostics_Label_moduleCollisions",
    "Diagnostics_Label_moduleConflicts",
    "Diagnostics_Label_dualChipWarning",
    "Diagnostics_Label_vendorQuirk",
    "Diagnostics_Label_acpiConflicts",
    "Diagnostics_Label_revertHeadline",
]


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_page(qtbot, state=None) -> DiagnosticsPage:
    page = DiagnosticsPage(state=state or _make_state())
    qtbot.addWidget(page)
    return page


def _diag(**overrides) -> HardwareDiagnosticsResult:
    hwmon = overrides.pop("hwmon", None) or HwmonDiagnostics(
        chips_detected=[
            HwmonChipInfo(
                chip_name="nct6798",
                device_id="nct6798.656",
                expected_driver="nct6775",
                in_mainline_kernel=True,
                header_count=5,
            ),
        ],
        total_headers=5,
        writable_headers=3,
    )
    defaults = dict(
        hwmon=hwmon,
        board=BoardInfo(vendor="ASUS", name="ProArt X870E", bios_version="1234"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(
            state="normal",
            cpu_sensor_found=True,
            emergency_threshold_c=105.0,
            release_threshold_c=80.0,
        ),
    )
    defaults.update(overrides)
    return HardwareDiagnosticsResult(**defaults)


def _diag_with_revert(header_id: str, count: int) -> HardwareDiagnosticsResult:
    return _diag(
        hwmon=HwmonDiagnostics(
            total_headers=1,
            writable_headers=1,
            enable_revert_counts={header_id: count},
        ),
    )


class TestSectionStructure:
    def test_all_sections_exist_and_start_collapsed(self, qtbot):
        page = _make_page(qtbot)
        for name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, name)
            assert section is not None, f"missing section {name}"
            assert section.is_expanded() is False, f"{name} should start collapsed"

    def test_section_members_are_nested_inside_their_section(self, qtbot):
        page = _make_page(qtbot)
        for section_name, members in SECTION_MEMBERS.items():
            section = page.findChild(CollapsibleSection, section_name)
            for member in members:
                assert section.findChild(QWidget, member) is not None, (
                    f"{member} should live inside {section_name}"
                )

    def test_always_visible_widgets_are_outside_every_section(self, qtbot):
        page = _make_page(qtbot)
        for name in ALWAYS_VISIBLE_NAMES:
            assert page.findChild(QWidget, name) is not None, f"missing {name}"
            for section_name in SECTION_NAMES:
                section = page.findChild(CollapsibleSection, section_name)
                assert section.findChild(QWidget, name) is None, (
                    f"{name} must not be trapped inside {section_name}"
                )

    def test_outer_splitter_contract_preserved(self, qtbot):
        # The collapsible refactor must not break the asserted Fans-tab
        # splitter shape (test_table_ux relies on this too).
        page = _make_page(qtbot)
        splitter = page.findChild(QSplitter, "Diagnostics_Splitter_fans")
        assert splitter is not None
        assert splitter.orientation() == Qt.Orientation.Vertical
        assert splitter.childrenCollapsible() is False
        assert splitter.count() == 2


class TestBiosAutoExpand:
    def test_section_expands_when_revert_present(self, qtbot):
        page = _make_page(qtbot)
        section = page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")
        assert section.is_expanded() is False  # collapsed before any problem

        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))

        assert section.is_expanded() is True
        assert not page._revert_label.isHidden()
        assert not page._revert_footnote_label.isHidden()

    def test_section_stays_collapsed_without_revert(self, qtbot):
        page = _make_page(qtbot)
        section = page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")

        page._populate_hw_diagnostics(_diag())  # no enable_revert_counts

        assert section.is_expanded() is False
        assert page._revert_label.isHidden()

    def test_auto_expand_survives_theme_refresh(self, qtbot):
        page = _make_page(qtbot)
        section = page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")

        diag = _diag_with_revert("pwm1", 50)
        page._populate_hw_diagnostics(diag)
        assert section.is_expanded() is True

        # set_theme re-renders from the cached diagnostics result; the section
        # must not silently collapse a still-active problem on a theme switch.
        page._diag.last_hw_diagnostics = diag
        page.set_theme(None)
        assert section.is_expanded() is True
        assert not page._revert_label.isHidden()


class TestAlwaysVisibleContent:
    def test_summary_visible_after_populate(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag())
        assert not page._hw_ready_summary.isHidden()
        assert "writable" in page._hw_ready_summary.text()

    def test_revert_headline_visible_and_outside_sections(self, qtbot):
        # The headline is a critical alert: it must be shown without expanding
        # anything, and must not live inside a collapsible section.
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 7))
        assert not page._revert_headline_label.isHidden()
        for section_name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, section_name)
            assert section.findChild(QWidget, "Diagnostics_Label_revertHeadline") is None
