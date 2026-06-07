"""Tests for the dashboard's first-launch service-not-enabled hint.

When a user installs both packages, never enables the daemon, and starts the
GUI, the dashboard's disconnected state should surface an actionable hint
with the enable command. This is the soft-landing for users who scrolled
past the post_install message.

The hint must:
- Appear when ``check_daemon_service_state`` reports
  ``installed_but_not_enabled``.
- Stay hidden when the service is enabled or when the probe was unreliable.
- Stay hidden when the dashboard is in CONNECTED state.
- Have selectable command text and a Copy button that places ``ENABLE_COMMAND``
  on the clipboard.
"""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from control_ofc.api.models import ConnectionState
from control_ofc.services.daemon_service_check import (
    ENABLE_COMMAND,
    DaemonServiceState,
)
from control_ofc.ui.pages.dashboard_page import DashboardPage


def _state(*, enabled: bool, active: bool, can_check: bool = True) -> DaemonServiceState:
    return DaemonServiceState(
        socket_exists=False,
        service_enabled=enabled,
        service_active=active,
        can_check=can_check,
    )


def _all_label_text(page: DashboardPage) -> str:
    return "\n".join(lab.text() for lab in page.findChildren(QLabel))


class TestServiceHintVisibility:
    def test_hidden_initially(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        frame = page.findChild(QFrame, "Dashboard_Frame_serviceHint")
        assert frame is not None
        assert frame.isVisible() is False

    def test_visible_when_disconnected_and_service_disabled(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()  # the visibility check needs the widget to be realized
        with patch(
            "control_ofc.ui.pages.dashboard_page.check_daemon_service_state",
            return_value=_state(enabled=False, active=False),
        ):
            page._on_connection_changed(ConnectionState.DISCONNECTED)
        frame = page.findChild(QFrame, "Dashboard_Frame_serviceHint")
        assert frame is not None
        assert frame.isVisible() is True

    def test_hidden_when_service_already_enabled(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        with patch(
            "control_ofc.ui.pages.dashboard_page.check_daemon_service_state",
            return_value=_state(enabled=True, active=True),
        ):
            page._on_connection_changed(ConnectionState.DISCONNECTED)
        frame = page.findChild(QFrame, "Dashboard_Frame_serviceHint")
        assert frame.isVisible() is False

    def test_hidden_when_probe_unreliable(self, qtbot, app_state):
        # No systemctl on path / probe failed → don't show; misleading hint
        # is worse than no hint.
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        with patch(
            "control_ofc.ui.pages.dashboard_page.check_daemon_service_state",
            return_value=_state(enabled=False, active=False, can_check=False),
        ):
            page._on_connection_changed(ConnectionState.DISCONNECTED)
        frame = page.findChild(QFrame, "Dashboard_Frame_serviceHint")
        assert frame.isVisible() is False

    def test_probe_exception_does_not_crash_ui(self, qtbot, app_state):
        # Defensive: any unexpected exception inside the check must not
        # propagate to the Qt event loop. We hide the hint and continue.
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        with patch(
            "control_ofc.ui.pages.dashboard_page.check_daemon_service_state",
            side_effect=RuntimeError("kaboom"),
        ):
            page._on_connection_changed(ConnectionState.DISCONNECTED)
        frame = page.findChild(QFrame, "Dashboard_Frame_serviceHint")
        assert frame.isVisible() is False


class TestEnableCommandCopy:
    def test_copy_button_writes_command_to_clipboard(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        # Clear clipboard before the test to avoid contamination.
        clipboard = QApplication.clipboard()
        clipboard.clear()

        btn = page.findChild(QPushButton, "Dashboard_Btn_copyEnableCommand")
        assert btn is not None
        btn.click()
        assert clipboard.text() == ENABLE_COMMAND

    def test_command_label_shows_enable_command(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        label = page.findChild(QLabel, "Dashboard_Label_enableCommand")
        assert label is not None
        assert label.text() == ENABLE_COMMAND


class TestDashboardCopyText:
    def test_no_hardware_state_keeps_uucp_and_dialout_groups(self, qtbot, app_state):
        # Regression: dashboard previously told Arch users to join the
        # 'dialout' group, which doesn't exist on Arch (correct group is
        # 'uucp'). Both names must stay visible, now framed as a fact about
        # the daemon service (DEC-145): the daemon unit ships
        # SupplementaryGroups=uucp; Debian-family installs may need a
        # 'dialout' drop-in.
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        text = _all_label_text(page)
        assert "uucp" in text
        assert "dialout" in text

    def test_no_hardware_state_routes_to_readiness_report(self, qtbot, app_state):
        # DEC-145: the most common cause of "no hardware" is a missing
        # Super-I/O kernel module, so the empty state must route users to
        # the Troubleshooting readiness report rather than serial-group
        # surgery.
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        text = _all_label_text(page)
        assert "Troubleshooting" in text
        assert "Refresh Hardware Diagnostics" in text

    def test_no_hardware_state_drops_user_directed_serial_advice(self, qtbot, app_state):
        # DEC-145 regression: the old copy told the *user* to verify their
        # own serial-group membership — irrelevant, since the GUI talks to a
        # 0666 socket (DEC-049) and the daemon (root + SupplementaryGroups)
        # owns serial access.
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        text = _all_label_text(page)
        assert "Verify serial-port group membership" not in text
