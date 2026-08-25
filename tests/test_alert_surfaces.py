"""Alert surfaces (DEC-282) — the compact status bar and the Alert Centre.

Was ``test_warnings_drawer.py``, whose name outlived two redesigns: DEC-184 opened a
warnings *dialog* from the Dashboard status strip, DEC-222 replaced it with a permanent
Logs-page panel, and DEC-282 replaced that with the two surfaces tested here. Its class
was still called ``TestWarningChipOpensWarningsDialog`` while testing neither a chip nor
a dialog.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.widgets.alert_center_dialog import AlertCenterDialog
from control_ofc.ui.widgets.alert_status_bar import AlertStatusBar


class TestAlertCentreRendering:
    """Behaviours inherited from the retired WarningsView, re-asserted on the surface
    that replaced it. They were worth keeping: the plain-text hardening in particular
    is a security property, not a cosmetic one."""

    def test_daemon_strings_render_as_plain_text(self, qtbot, app_state):
        """Alert text embeds daemon-derived sensor labels and fan ids. Markup in one
        must be shown literally, never interpreted (DEC-231)."""
        app_state.add_warning(
            level="warning", source="sensor", message="<b>evil</b>", key="sensor_stale:x"
        )
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)

        detail = dialog.findChild(QLabel, "AlertCenter_Card_0_detail")
        assert detail is not None
        assert "<b>" in detail.text(), "shown literally, not rendered"

    def test_suggested_action_is_shown_for_a_known_condition(self, qtbot, app_state):
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:cpu_fan")
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)

        action = dialog.findChild(QLabel, "AlertCenter_Card_0_action")
        assert action is not None
        assert "fan is spinning" in action.text()

    def test_unknown_condition_gets_no_invented_advice(self, qtbot, app_state):
        """Brief §8 forbids inventing remediation text the app cannot support."""
        app_state.add_warning(level="warning", source="mystery", message="?", key="odd:1")
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)

        assert dialog.findChild(QLabel, "AlertCenter_Card_0_action") is None

    def test_recovered_section_lists_what_already_cleared(self, qtbot, app_state):
        app_state.add_warning(level="warning", source="fan", message="stall", key="fan_stall:f1")
        app_state.remove_warning("fan_stall:f1")
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)

        row = dialog.findChild(QLabel, "AlertCenter_Recovered_0_text")
        assert row is not None
        assert "RECOVERED" in row.text()
        assert dialog.findChild(QLabel, "AlertCenter_Label_noActive") is not None

    def test_empty_state_says_so_in_both_sections(self, qtbot, app_state):
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)
        assert dialog.findChild(QLabel, "AlertCenter_Label_noActive") is not None
        assert dialog.findChild(QLabel, "AlertCenter_Label_noRecovered") is not None

    def test_acknowledged_active_alert_is_de_emphasised_but_still_listed(self, qtbot, app_state):
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")
        app_state.acknowledge_all()
        dialog = AlertCenterDialog(app_state, None)
        qtbot.addWidget(dialog)

        # Still in ACTIVE — the condition has not gone away.
        assert dialog.findChild(QLabel, "AlertCenter_Label_noActive") is None
        assert dialog.findChild(QPushButton, "AlertCenter_Card_0_ack").isEnabled() is False


class TestLogsPageAlertSurface:
    """DEC-282 removed the permanent Active Warnings panel these tests used to find.

    The intent behind them survives and is what is asserted here: the Logs page must
    still be where alerts surface, and it must reflect AppState live rather than
    needing a manual refresh. What changed is that the surface is a one-line status bar
    plus an on-demand Alert Centre, not a panel occupying a quarter of the page.
    """

    def test_logs_page_no_longer_reserves_a_warnings_panel(self, qtbot, app_state):
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        # The panel's widget class is gone entirely, not merely unhosted — leaving it
        # in the tree as dead code would have kept a second, drifting copy of
        # next_action_for_warning alive alongside the one in services/alerts_view.
        assert page.findChild(QWidget, "Logs_View_warnings") is None
        assert page.findChild(AlertStatusBar, "Logs_AlertBar") is not None

    def test_alert_bar_reflects_state_and_is_live(self, qtbot, app_state):
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        assert page._alert_bar.vm.has_active is False
        assert "No active alerts" in page._alert_bar._summary.text()

        # Raised after construction — must appear through the signal, not a refresh().
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")

        assert page._alert_bar.vm.critical_count == 1
        assert "1 critical" in page._alert_bar._summary.text()
        assert page._alert_bar._headline.text() == "stall"

    def test_alert_bar_reports_a_recovery_the_user_never_saw(self, qtbot, app_state):
        """Brief §24: "✓ No active alerts / Recent alert: … recovered at …"."""
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        app_state.add_warning(level="warning", source="fan", message="stall", key="fan_stall:f1")
        app_state.remove_warning("fan_stall:f1")

        assert page._alert_bar.vm.has_active is False
        assert "No active alerts" in page._alert_bar._summary.text()
        assert "Recent alert" in page._alert_bar._recent.text()
        assert "recovered at" in page._alert_bar._recent.text()

    def test_acknowledging_from_the_centre_updates_the_bar(self, qtbot, app_state):
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        dialog = AlertCenterDialog(app_state, page)
        qtbot.addWidget(dialog)

        dialog.findChild(QPushButton, "AlertCenter_Card_0_ack").click()

        assert app_state.unacknowledged_count == 0
        assert app_state.warning_count == 1, "still stalled — acknowledgement is not a fix"

    def test_show_related_logs_narrows_the_table(self, qtbot, app_state):
        """Brief §14, in the simplest form the flat DiagEvent supports."""
        diag = DiagnosticsService()
        page = LogsPage(diagnostics_service=diag, state=app_state)
        qtbot.addWidget(page)
        diag.log_event("warning", "fan", "CPU_FAN stall detected")
        diag.log_event("info", "polling", "Daemon connected")
        assert len(page._rows) == 2

        page.show_related_logs("fan", "cpu_fan")

        assert [r.source for r in page._rows] == ["fan"]
        assert page._source_combo.currentText() == "fan"
