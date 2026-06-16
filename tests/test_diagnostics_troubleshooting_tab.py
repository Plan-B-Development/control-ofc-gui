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
from control_ofc.ui.hwmon_guidance import severity_display
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.readiness_report import advisory_rows, detect_readiness_problems

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
    "Diagnostics_Container_advisories",  # DEC-158: replaced the flat vendor-quirk label
    "Diagnostics_Label_acpiConflicts",
    "Diagnostics_Label_hwReadySummary",
    "Diagnostics_Label_boardInfo",
    "Diagnostics_Label_noIssues",
    "Diagnostics_Label_readinessDisclaimer",  # DEC-158: panel-level liability note
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


def _diag_gigabyte_it8696() -> HardwareDiagnosticsResult:
    """A Gigabyte IT8696E board → at least one actionable (HIGH SmartFan) vendor
    advisory, used to exercise the per-advisory rows."""
    return _diag(
        board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8696", expected_driver="it87", header_count=5)
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
        # DEC-158: the badge now leads with a severity glyph, so assert the word
        # is present rather than equal to the whole label.
        assert "CRITICAL" in badge.text()

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


class TestAdvisories:
    """DEC-158: board/chip vendor quirks render as per-advisory rows — a coloured
    severity badge (icon + word), an always-visible summary, and a collapsible
    detail whose default-open state follows severity — replacing the old single
    flat ``[SEVERITY] …`` PlainText label so INFO no longer looks like a warning."""

    def test_container_hidden_when_no_advisories(self, qtbot):
        page = _make_page(qtbot)
        assert advisory_rows(_healthy_diag()) == []
        page._populate_hw_diagnostics(_healthy_diag())
        assert page._advisory_container.isHidden()
        assert page._advisory_rows == []

    def test_one_row_per_advisory_with_severity_treatment(self, qtbot):
        page = _make_page(qtbot)
        diag = _diag_gigabyte_it8696()
        advisories = advisory_rows(diag)
        assert advisories  # the DB has at least one Gigabyte IT8696E quirk
        page._populate_hw_diagnostics(diag)

        assert not page._advisory_container.isHidden()
        assert len(page._advisory_rows) == len(advisories)
        for i, quirk in enumerate(advisories):
            disp = severity_display(quirk.severity)
            badge = page.findChild(QLabel, f"Diagnostics_AdvisoryBadge_{i}")
            assert badge is not None, f"missing advisory badge {i}"
            assert disp.word in badge.text()
            assert badge.property("class") == disp.css_class
            summary = page.findChild(QLabel, f"Diagnostics_AdvisorySummary_{i}")
            assert summary.text() == quirk.summary
            section = page.findChild(CollapsibleSection, f"Diagnostics_Section_advisory_{i}")
            assert section is not None
            # CRITICAL/HIGH open by default; MEDIUM/INFO collapsed.
            assert section.is_expanded() is disp.default_expanded

    def test_advisories_sorted_most_severe_first(self, qtbot):
        # The Gigabyte IT8696E board matches both a HIGH SmartFan quirk and a
        # MEDIUM IT8883 quirk; the HIGH one must come first.
        diag = _diag_gigabyte_it8696()
        ranks = [severity_display(q.severity).rank for q in advisory_rows(diag)]
        assert ranks == sorted(ranks, reverse=True)

    def test_info_advisory_uses_info_chip_not_warning(self, qtbot):
        # The whole point of DEC-158: an INFO advisory must not share the warning
        # tiers' orange. ASUS + NCT6798D yields a single info mainline-coverage note.
        page = _make_page(qtbot)
        diag = _diag()  # ASUS ProArt X870E + nct6798
        advisories = advisory_rows(diag)
        assert advisories and all(q.severity == "info" for q in advisories)
        page._populate_hw_diagnostics(diag)
        badge = page.findChild(QLabel, "Diagnostics_AdvisoryBadge_0")
        assert badge.property("class") == "InfoChip"
        assert badge.property("class") not in ("WarningChip", "CautionChip", "CriticalChip")
        section = page.findChild(CollapsibleSection, "Diagnostics_Section_advisory_0")
        assert section.is_expanded() is False

    def test_advisory_detail_has_doc_link(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_gigabyte_it8696())
        detail = page.findChild(QLabel, "Diagnostics_Label_advisoryDetail_0")
        assert detail is not None
        assert detail.openExternalLinks() is True
        assert 'href="' in detail.text()
        assert "manufacturer-quirks" in detail.text()

    def test_rows_cleared_on_rerender(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_gigabyte_it8696())
        assert page._advisory_rows
        page._populate_hw_diagnostics(_healthy_diag())
        assert page._advisory_rows == []
        assert page._advisory_container.isHidden()

    def test_advisories_outside_detail_sections(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_gigabyte_it8696())
        for section_name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, section_name)
            assert section.findChild(QFrame, "Diagnostics_Advisory_0") is None


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
