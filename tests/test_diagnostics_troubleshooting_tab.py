"""Tests for the Diagnostics ▸ Troubleshooting tab (DEC-124).

DEC-124 split the old Fans tab: the Fan Status table stays on Fans, and all
Hardware Readiness content moved to a dedicated Troubleshooting tab, redesigned
as a flattened health report. The deep accordion-in-accordion card of
DEC-115/DEC-116 is retired; the verdict banner, blocking alerts, and an issue
checklist are now always visible (a strict strengthening of DEC-116's "never
hide an essential warning" rule), with five flat on-demand detail sections
below. These tests lock down:
  * the five detail sub-sections exist and start collapsed,
  * each sub-section actually contains the widgets it should,
  * the verdict + alerts + summary + board + checklist live OUTSIDE every
    collapsible detail section (never trapped behind a collapse),
  * there is no outer "Hardware Readiness" collapsible card any more,
  * the issue checklist renders one row per detected problem (with the right
    severity badge, label, fix, and doc link) and a "no issues" line when
    healthy, and clears cleanly on re-render,
  * the BIOS-interference section is hidden until — and auto-expands only
    when — there is a real revert count, and survives a theme refresh.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QWidget

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
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

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

# DEC-124: every readiness summary/alert/verdict/checklist widget is a direct
# child of the card frame, NEVER trapped inside one of the five detail
# sub-sections (so a collapsed section can't hide an essential warning).
OUTSIDE_DETAIL_NAMES = [
    "Diagnostics_Label_readinessVerdict",
    "Diagnostics_Label_moduleCollisions",
    "Diagnostics_Label_moduleConflicts",
    "Diagnostics_Label_revertHeadline",
    "Diagnostics_Label_dualChipWarning",
    "Diagnostics_Label_vendorQuirk",
    "Diagnostics_Label_acpiConflicts",
    "Diagnostics_Label_hwReadySummary",
    "Diagnostics_Label_boardInfo",
    "Diagnostics_Label_noIssues",
]

# The retired DEC-115 outer collapsible card.
RETIRED_CARD_SECTION = "Diagnostics_Section_hwReadiness"


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


def _diag_acpi_and_revert() -> HardwareDiagnosticsResult:
    """A problem board with two issues active: an ACPI conflict and a BIOS
    revert — used to prove the checklist renders one row per detected problem."""
    return _diag(
        hwmon=HwmonDiagnostics(
            total_headers=1,
            writable_headers=1,
            enable_revert_counts={"pwm1": 5},
        ),
        acpi_conflicts=[
            AcpiConflictInfo(
                io_range="0x0290-0x0299",
                claimed_by="ACPI",
                conflicts_with_driver="it87",
            )
        ],
    )


def _healthy_diag() -> HardwareDiagnosticsResult:
    # vendor="" avoids any vendor quirk; all headers writable; one chip; no
    # reverts/acpi/collisions → detect_readiness_problems is empty (SuccessChip).
    return _diag(
        board=BoardInfo(vendor="", name="Generic"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
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

    def test_readiness_widgets_are_outside_detail_sections(self, qtbot):
        page = _make_page(qtbot)
        for name in OUTSIDE_DETAIL_NAMES:
            assert page.findChild(QWidget, name) is not None, f"missing {name}"
            for section_name in SECTION_NAMES:
                section = page.findChild(CollapsibleSection, section_name)
                assert section.findChild(QWidget, name) is None, (
                    f"{name} must not be trapped inside {section_name}"
                )

    def test_no_outer_collapsible_card(self, qtbot):
        # DEC-124: the deep accordion-in-accordion card of DEC-115 is retired.
        page = _make_page(qtbot)
        assert page.findChild(CollapsibleSection, RETIRED_CARD_SECTION) is None

    def test_verdict_not_behind_any_collapse(self, qtbot):
        # The verdict banner must be always-visible — not nested in any
        # collapsible section at all.
        page = _make_page(qtbot)
        verdict = page.findChild(QLabel, "Diagnostics_Label_readinessVerdict")
        assert verdict is not None
        for section in page.findChildren(CollapsibleSection):
            assert section.findChild(QLabel, "Diagnostics_Label_readinessVerdict") is None


class TestIssueChecklist:
    """DEC-124: the always-visible issue checklist — one row per detected
    problem, or a single 'no issues' line when healthy."""

    def test_no_issues_line_when_healthy(self, qtbot):
        page = _make_page(qtbot)
        assert detect_readiness_problems(_healthy_diag()) == []
        page._populate_hw_diagnostics(_healthy_diag())
        assert not page._no_issues_label.isHidden()
        assert page._issue_rows == []

    def test_one_row_per_detected_problem(self, qtbot):
        page = _make_page(qtbot)
        diag = _diag_acpi_and_revert()
        problems = detect_readiness_problems(diag)
        assert len(problems) >= 2  # acpi + bios revert
        page._populate_hw_diagnostics(diag)

        assert page._no_issues_label.isHidden()
        assert len(page._issue_rows) == len(problems)
        for p in problems:
            key = p["key"]
            row = page.findChild(QFrame, f"Diagnostics_IssueRow_{key}")
            assert row is not None, f"missing issue row for {key}"

            badge = page.findChild(QLabel, f"Diagnostics_IssueBadge_{key}")
            expected_chip = "CriticalChip" if p["severity"] == "critical" else "WarningChip"
            assert badge.property("class") == expected_chip

            label = page.findChild(QLabel, f"Diagnostics_IssueLabel_{key}")
            assert label.text() == p["label"]

            fix = page.findChild(QLabel, f"Diagnostics_IssueFix_{key}")
            assert fix.openExternalLinks() is True
            assert 'href="' in fix.text()
            assert p["doc_url"] in fix.text()

    def test_critical_problem_uses_critical_badge(self, qtbot):
        page = _make_page(qtbot)
        # A revert count >= 10 is classified critical.
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))
        badge = page.findChild(QLabel, "Diagnostics_IssueBadge_bios_revert")
        assert badge is not None
        assert badge.property("class") == "CriticalChip"
        assert badge.text() == "CRITICAL"

    def test_issue_rows_outside_detail_sections(self, qtbot):
        # The checklist is a top-level health summary, never buried in a
        # collapsible detail section.
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))
        for section_name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, section_name)
            assert section.findChild(QFrame, "Diagnostics_IssueRow_bios_revert") is None

    def test_rows_cleared_on_healthy_rerender(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))
        assert page._issue_rows  # at least one row
        page._populate_hw_diagnostics(_healthy_diag())
        assert page._issue_rows == []
        assert not page._no_issues_label.isHidden()


class TestBiosSectionVisibility:
    """DEC-116 (retained by DEC-124): the BIOS-interference section is hidden
    whenever there is no interference to report, and is revealed + expanded only
    when the daemon reports a real revert count."""

    def _section(self, page) -> CollapsibleSection:
        return page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")

    def test_section_hidden_before_populate(self, qtbot):
        page = _make_page(qtbot)
        assert self._section(page).isHidden() is True

    def test_section_revealed_and_expands_when_revert_present(self, qtbot):
        page = _make_page(qtbot)
        section = self._section(page)
        assert section.isHidden() is True

        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))

        assert section.isHidden() is False
        assert section.is_expanded() is True
        assert not page._revert_label.isHidden()
        assert not page._revert_footnote_label.isHidden()

    def test_section_hidden_without_revert(self, qtbot):
        page = _make_page(qtbot)
        section = self._section(page)

        page._populate_hw_diagnostics(_diag())  # no enable_revert_counts

        assert section.isHidden() is True
        assert section.is_expanded() is False
        assert page._revert_label.isHidden()

    def test_section_hidden_when_all_counts_zero(self, qtbot):
        page = _make_page(qtbot)
        section = self._section(page)

        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 0))

        assert section.isHidden() is True
        assert page._revert_label.isHidden()

    def test_section_re_hidden_when_revert_clears(self, qtbot):
        page = _make_page(qtbot)
        section = self._section(page)

        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))
        assert section.isHidden() is False

        page._populate_hw_diagnostics(_diag())  # interference resolved
        assert section.isHidden() is True

    def test_auto_expand_survives_theme_refresh(self, qtbot):
        page = _make_page(qtbot)
        section = page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")

        diag = _diag_with_revert("pwm1", 50)
        page._populate_hw_diagnostics(diag)
        assert section.is_expanded() is True

        # set_theme re-renders from the cached diagnostics result; the section
        # must not silently collapse or hide a still-active problem.
        page._diag.last_hw_diagnostics = diag
        page.set_theme(None)
        assert section.isHidden() is False
        assert section.is_expanded() is True
        assert not page._revert_label.isHidden()


class TestAlwaysVisibleContent:
    def test_summary_visible_after_populate(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag())
        assert not page._hw_ready_summary.isHidden()
        assert "writable" in page._hw_ready_summary.text()

    def test_revert_headline_visible_and_outside_sections(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 7))
        assert not page._revert_headline_label.isHidden()
        for section_name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, section_name)
            assert section.findChild(QWidget, "Diagnostics_Label_revertHeadline") is None


class TestRenderConsistency:
    """DEC-115 DRY (retained): the chip/module tables are populated from the
    same chip_rows()/module_rows() the pop-out report uses, so they cannot
    drift."""

    def test_chip_table_matches_chip_rows(self, qtbot):
        from control_ofc.ui.widgets.readiness_report import chip_rows

        page = _make_page(qtbot)
        diag = _diag()
        page._populate_hw_diagnostics(diag)
        rows = chip_rows(diag)
        assert page._chip_table.rowCount() == len(rows)
        for i, r in enumerate(rows):
            assert page._chip_table.item(i, 0).text() == r.chip
            assert page._chip_table.item(i, 1).text() == r.driver
            assert page._chip_table.item(i, 2).text() == r.status
            assert page._chip_table.item(i, 3).text() == r.mainline
            assert page._chip_table.item(i, 4).text() == r.headers

    def test_modules_table_matches_module_rows(self, qtbot):
        from control_ofc.ui.widgets.readiness_report import module_rows

        page = _make_page(qtbot)
        diag = _diag()
        page._populate_hw_diagnostics(diag)
        rows = module_rows(diag)
        assert page._modules_table.rowCount() == len(rows)
        for i, r in enumerate(rows):
            assert page._modules_table.item(i, 0).text() == r.name
            assert page._modules_table.item(i, 1).text() == r.loaded
            assert page._modules_table.item(i, 2).text() == r.mainline
