"""Tests for the Diagnostics > Fans tab progressive-disclosure layout
(DEC-112, extended by DEC-115 / DEC-116).

The dense Hardware Readiness detail is grouped into collapsible sections, and
(DEC-115) the whole card is itself one collapsible section: its persistent area
keeps the verdict + the critical/blocking-alert stack on screen while the body
folds. DEC-116 then split the alerts — only blocking alerts are persistent;
informational alerts (dual-chip, vendor quirks, ACPI) live in the collapsible
body so a manual collapse actually clears them — and hides the BIOS-interference
section entirely when there is no interference to report. These tests lock down:
  * the five detail sub-sections exist and start collapsed,
  * each sub-section actually contains the widgets it should,
  * the verdict + blocking alerts live in the card's persistent area while the
    informational alerts + summary/board live in its collapsible body — none
    trapped in a detail section,
  * the whole card collapses (keeping the verdict + blocking alerts visible,
    hiding the informational alerts) and force-expands on a problem while
    respecting a manual collapse on a healthy board,
  * the outer Diagnostics_Splitter_fans contract is preserved, and
  * the BIOS-interference section is hidden until — and auto-expands only
    when — there is a real revert count, and survives a theme refresh.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

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

# The card (DEC-115/DEC-116) is one collapsible section. Its PERSISTENT area
# keeps the verdict + the critical/blocking-alert stack on screen even when
# folded; its collapsible BODY holds the informational alerts + summary + board
# identity. None of these may be trapped inside one of the five detail
# sub-sections.
CARD_SECTION = "Diagnostics_Section_hwReadiness"

# DEC-116: only blocking alerts stay persistent (visible when folded).
PERSISTENT_NAMES = [
    "Diagnostics_Label_readinessVerdict",
    "Diagnostics_Label_moduleCollisions",
    "Diagnostics_Label_moduleConflicts",
    "Diagnostics_Label_revertHeadline",
]
# DEC-116: informational alerts demoted into the collapsible body so a manual
# collapse clears them.
DEMOTED_ALERT_NAMES = [
    "Diagnostics_Label_dualChipWarning",
    "Diagnostics_Label_vendorQuirk",
    "Diagnostics_Label_acpiConflicts",
]
CARD_BODY_NAMES = [
    "Diagnostics_Label_hwReadySummary",
    "Diagnostics_Label_boardInfo",
]
# Everything that must stay out of the five detail sub-sections (whether it
# lives in the persistent area or the card body).
OUTSIDE_DETAIL_NAMES = CARD_BODY_NAMES + PERSISTENT_NAMES + DEMOTED_ALERT_NAMES


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
    """A problem board with one demoted alert (ACPI conflict) and one
    persistent alert (BIOS revert headline) both active — used to prove a
    manual collapse clears the informational alert but keeps the blocking
    one (DEC-116)."""
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

    def test_card_level_widgets_are_outside_detail_sections(self, qtbot):
        page = _make_page(qtbot)
        for name in OUTSIDE_DETAIL_NAMES:
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


class TestBiosSectionVisibility:
    """DEC-116: the BIOS-interference section is hidden whenever there is no
    interference to report (the normal case on virtually every system), and is
    revealed + expanded only when the daemon reports a real revert count."""

    def _section(self, page) -> CollapsibleSection:
        return page.findChild(CollapsibleSection, "Diagnostics_Section_biosInterference")

    def test_section_hidden_before_populate(self, qtbot):
        # No diagnostics fetched yet → the section must not show an empty header.
        page = _make_page(qtbot)
        assert self._section(page).isHidden() is True

    def test_section_revealed_and_expands_when_revert_present(self, qtbot):
        page = _make_page(qtbot)
        section = self._section(page)
        assert section.isHidden() is True  # hidden before any problem

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
        # Mirrors the demo payload (all-zero counts): no interference, so the
        # section stays hidden rather than expanding to an empty body.
        page = _make_page(qtbot)
        section = self._section(page)

        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 0))

        assert section.isHidden() is True
        assert page._revert_label.isHidden()

    def test_section_re_hidden_when_revert_clears(self, qtbot):
        # A revert appears then clears on a later poll — the section must hide
        # again, not linger visible-but-empty.
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
        # must not silently collapse or hide a still-active problem on a theme
        # switch.
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
        # The headline is a critical alert: it must be shown without expanding
        # anything, and must not live inside a collapsible section.
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 7))
        assert not page._revert_headline_label.isHidden()
        for section_name in SECTION_NAMES:
            section = page.findChild(CollapsibleSection, section_name)
            assert section.findChild(QWidget, "Diagnostics_Label_revertHeadline") is None


class TestCardLevelCollapse:
    """DEC-115: the whole Hardware Readiness card is one collapsible section —
    verdict + alerts persistent (visible when folded), body collapsible, with a
    force-expand on problems that still respects a manual collapse when healthy.
    """

    def _card(self, page) -> CollapsibleSection:
        section = page.findChild(CollapsibleSection, CARD_SECTION)
        assert section is not None, "missing Hardware Readiness card section"
        return section

    def test_card_section_exists_and_starts_expanded(self, qtbot):
        page = _make_page(qtbot)
        assert self._card(page).is_expanded() is True

    def test_verdict_and_alerts_are_persistent(self, qtbot):
        page = _make_page(qtbot)
        persistent = self._card(page).persistent_widget()
        for name in PERSISTENT_NAMES:
            assert persistent.findChild(QWidget, name) is not None, (
                f"{name} must live in the card's persistent (always-visible) area"
            )

    def test_summary_and_board_in_collapsible_body(self, qtbot):
        page = _make_page(qtbot)
        content = self._card(page).content_widget()
        for name in CARD_BODY_NAMES:
            assert content.findChild(QWidget, name) is not None, (
                f"{name} must live in the card's collapsible body"
            )

    def test_demoted_alerts_in_collapsible_body_not_persistent(self, qtbot):
        # DEC-116: informational alerts must live in the foldable body (so a
        # collapse clears them), NOT in the always-visible persistent area.
        page = _make_page(qtbot)
        card = self._card(page)
        content = card.content_widget()
        persistent = card.persistent_widget()
        for name in DEMOTED_ALERT_NAMES:
            assert content.findChild(QWidget, name) is not None, (
                f"{name} must live in the card's collapsible body"
            )
            assert persistent.findChild(QWidget, name) is None, (
                f"{name} must NOT be persistent (it should fold away on collapse)"
            )

    def test_collapse_hides_demoted_keeps_blocking_alerts(self, qtbot):
        # The core DEC-116 behaviour the user asked for: folding the card makes
        # the informational alerts go away, while a blocking alert (active
        # BIOS-revert) stays on screen.
        page = _make_page(qtbot)
        card = self._card(page)
        page._populate_hw_diagnostics(_diag_acpi_and_revert())
        # A problem board force-expands → both alerts are visible by default.
        assert card.is_expanded() is True
        assert page._acpi_label.isVisibleTo(card) is True
        assert page._revert_headline_label.isVisibleTo(card) is True

        card.set_expanded(False)
        # Informational ACPI alert cleared; blocking BIOS-revert headline kept.
        assert page._acpi_label.isVisibleTo(card) is False
        assert page._revert_headline_label.isVisibleTo(card) is True

    def test_collapse_keeps_persistent_visible_hides_body(self, qtbot):
        page = _make_page(qtbot)
        card = self._card(page)
        card.set_expanded(False)
        # The verdict + alert stack stay on screen; the detail body folds away.
        assert card.persistent_widget().isVisibleTo(card) is True
        assert card.content_widget().isVisibleTo(card) is False

    def test_problem_force_expands_collapsed_card(self, qtbot):
        page = _make_page(qtbot)
        card = self._card(page)
        card.set_expanded(False)
        # A revert count is a real problem → the card must re-expand so the
        # detail and "To fix" guidance are visible by default.
        page._populate_hw_diagnostics(_diag_with_revert("pwm1", 12))
        assert card.is_expanded() is True

    def test_healthy_respects_manual_collapse(self, qtbot):
        page = _make_page(qtbot)
        card = self._card(page)
        card.set_expanded(False)
        # A healthy board must NOT fight a user who folded the card.
        page._populate_hw_diagnostics(_healthy_diag())
        assert card.is_expanded() is False


class TestRenderConsistency:
    """DEC-115 DRY: the card's chip/module tables are populated from the same
    chip_rows()/module_rows() the pop-out report uses, so they cannot drift."""

    def test_card_chip_table_matches_chip_rows(self, qtbot):
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

    def test_card_modules_table_matches_module_rows(self, qtbot):
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
