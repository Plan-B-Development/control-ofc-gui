"""DEC-208: the global footer / status strip + the page nav/action shims."""

from __future__ import annotations

import platform

from PySide6.QtWidgets import QLabel

from control_ofc.constants import APP_VERSION
from control_ofc.ui.components.footer import StatusFooter


def test_footer_shows_version_kernel_arch(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    assert footer.objectName() == "StatusFooter_Root"
    labels = {c.objectName(): c.text() for c in footer.findChildren(QLabel)}
    assert labels["StatusFooter_Label_version"] == f"v{APP_VERSION}"
    assert platform.release() in labels["StatusFooter_Label_kernel"]
    assert labels["StatusFooter_Label_arch"] == platform.machine()


def test_footer_health_tracks_warning_count(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    assert footer._health_label.text() == "All systems nominal"
    assert footer._health_led._role == "ok"
    footer.set_warning_count(2)
    assert footer._health_label.text() == "2 warnings"
    assert footer._health_led._role == "warn"
    footer.set_warning_count(1)
    assert footer._health_label.text() == "1 warning"
    footer.set_warning_count(0)
    assert footer._health_label.text() == "All systems nominal"
    assert footer._health_led._role == "ok"


def test_footer_buttons_emit(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    with qtbot.waitSignal(footer.rescan_clicked):
        footer._rescan_btn.click()
    with qtbot.waitSignal(footer.export_bundle_clicked):
        footer._export_btn.click()


def test_pages_expose_nav_and_action_shims():
    # DEC-216: the global footer's two actions moved off the retired Diagnostics
    # page — Rescan Hardware to System State, Export Bundle to Logs.
    from control_ofc.ui.pages.logs_page import LogsPage
    from control_ofc.ui.pages.settings_page import SettingsPage
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    assert hasattr(SystemStatePage, "run_hwmon_rescan")
    assert hasattr(LogsPage, "export_bundle")
    # DEC-215: SettingsPage lost select_tab (single-surface, no sub-tabs).
    assert not hasattr(SettingsPage, "select_tab")


# ---------------------------------------------------------------------------
# DEC-222: four indicators re-homed here from the retired DashboardStatusStrip.
# They were Dashboard-only; the footer is always visible, so every page gets them.
# ---------------------------------------------------------------------------


class TestRehomedIndicators:
    def test_operation_mode_is_shown(self, qtbot):
        from control_ofc.api.models import OperationMode

        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_operation_mode(OperationMode.DEMO)
        assert footer._mode_label.text() == "Demo mode"
        assert footer._mode_label.property("class") == "DemoBadge"

        footer.set_operation_mode(OperationMode.AUTOMATIC)
        assert footer._mode_label.text() == "Automatic"
        assert footer._mode_label.property("class") == "CardMeta"

    def test_poll_age_formats_elapsed_time(self, qtbot):
        footer = StatusFooter()
        qtbot.addWidget(footer)
        assert footer._poll_age.text() == "Not updated yet"
        footer.update_poll_age(100.0, None)
        assert footer._poll_age.text() == "Not updated yet"
        footer.update_poll_age(100.0, 99.0)
        assert footer._poll_age.text() == "Updated just now"
        footer.update_poll_age(100.0, 90.0)
        assert footer._poll_age.text() == "Updated 10s ago"

    def test_thermal_chip_pairs_word_with_colour(self, qtbot):
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_thermal_state("normal")
        assert footer._thermal_btn.text() == "Thermal OK"
        assert footer._thermal_btn.property("class") == "SuccessChip"

        footer.set_thermal_state("emergency")
        assert footer._thermal_btn.text() == "Thermal: Emergency"
        assert footer._thermal_btn.property("class") == "CriticalChip"

    def test_unknown_thermal_state_is_surfaced_not_hidden(self, qtbot):
        """A daemon state the GUI doesn't know must still be shown."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_thermal_state("some_new_state")
        assert "some_new_state" in footer._thermal_btn.text()
        assert footer._thermal_btn.property("class") == "InfoChip"

    def test_thermal_chip_is_clickable_and_focusable(self, qtbot):
        """The detail must be reachable by click or keyboard, not hover (WCAG 1.4.13)."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        seen: list[bool] = []
        footer.thermal_clicked.connect(lambda: seen.append(True))
        footer._thermal_btn.click()
        assert seen == [True]

    def test_readiness_chip_hidden_without_a_rollup(self, qtbot):
        """Older daemons / pre-seed / demo send no rollup — hide, never guess."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.show()
        footer.set_readiness_rollup(None)
        assert footer._readiness_btn.isHidden()

    def test_readiness_chip_counts_items_to_fix(self, qtbot):
        from control_ofc.api.models import ReadinessRollup

        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.show()
        footer.set_readiness_rollup(ReadinessRollup(overall="warning", warning=2))
        assert not footer._readiness_btn.isHidden()
        assert "2 to fix" in footer._readiness_btn.text()
        assert footer._readiness_btn.property("class") == "WarningChip"

    def test_readiness_ok_state(self, qtbot):
        from control_ofc.api.models import ReadinessRollup

        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_readiness_rollup(ReadinessRollup(overall="ok"))
        assert footer._readiness_btn.text() == "✓ Cooling ready"
        assert footer._readiness_btn.property("class") == "SuccessChip"

    def test_readiness_tooltip_is_html_escaped(self, qtbot):
        """The summary is a daemon string — Qt must render it verbatim, never as
        markup (defence-in-depth, mirroring the item views' PlainText rule)."""
        from control_ofc.api.models import ReadinessRollup

        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_readiness_rollup(
            ReadinessRollup(overall="warning", warning=1, top_summary="<b>load it87</b>")
        )
        assert footer._readiness_btn.toolTip() == "&lt;b&gt;load it87&lt;/b&gt;"

    def test_readiness_chip_emits_on_click(self, qtbot):
        from control_ofc.api.models import ReadinessRollup

        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_readiness_rollup(ReadinessRollup(overall="warning", warning=1))
        seen: list[bool] = []
        footer.readiness_clicked.connect(lambda: seen.append(True))
        footer._readiness_btn.click()
        assert seen == [True]
