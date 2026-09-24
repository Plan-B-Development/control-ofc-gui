"""`CTRL-k`: a failed activation says why, from the sidebar and the Dashboard.

Before this, only DEC-403's own rule refusal reached the window banner; a
daemon rejection or a transport failure re-marked the sidebar (or showed
"Failed" on the Dashboard) and the reason went only to the log. These drive the
real click / page method against a client that fails, and assert the banner.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.services.profile_service import ProfileActivateOutcome
from control_ofc.ui.main_window import MainWindow

_REJECT = DaemonError(code="validation_error", message="curve 'c1' has no points")
_GONE = DaemonUnavailable(message="connection refused")


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def _failing_client(exc: Exception | None) -> Mock:
    client = Mock()
    if exc is None:
        client.activate_profile.return_value = Mock(activated=False)
    else:
        client.activate_profile.side_effect = exc
    return client


class TestFailureMessage:
    def test_success_has_no_message(self):
        assert ProfileActivateOutcome(activated=True).failure_message("Quiet") is None

    def test_a_daemon_reason_is_prefixed_with_the_profile(self):
        out = ProfileActivateOutcome(activated=False, error="Activation rejected by daemon")
        assert out.failure_message("Quiet") == (
            "Could not activate “Quiet”: Activation rejected by daemon"
        )

    def test_a_rule_refusal_is_shown_verbatim(self):
        """DEC-403's message already names the fans to fix (the user's choice)."""
        out = ProfileActivateOutcome(activated=False, error="Add: X", refused_by_rule=True)
        assert out.failure_message("Quiet") == "Add: X"


@pytest.mark.parametrize("exc", [_REJECT, _GONE, None], ids=["rejected", "unreachable", "false"])
class TestEveryFailureReachesTheBanner:
    def test_sidebar_apply(self, window, profile_service, exc):
        profile = profile_service.create_profile("Quiet")
        window._client = _failing_client(exc)
        window._populate_sidebar_profiles(select_id=profile.id)
        assert window.sidebar.profile_combo.currentData() == profile.id  # precondition
        window.findChild(QPushButton, "Sidebar_Btn_applyProfile").click()

        expected = profile_service.activate(profile.id, client=window._client)
        assert not expected.activated  # precondition: this client really fails
        assert window.error_banner._message_label.text() == expected.failure_message("Quiet")
        assert "“Quiet”" in window.error_banner._message_label.text()

    def test_dashboard_apply(self, window, profile_service, exc):
        profile = profile_service.create_profile("Quiet")
        page = window.dashboard_page
        page._client = _failing_client(exc)
        page._activate_profile_by_id(profile.id)

        expected = profile_service.activate(profile.id, client=page._client)
        assert not expected.activated  # precondition
        assert window.error_banner._message_label.text() == expected.failure_message("Quiet")


def test_a_successful_apply_raises_no_banner(window, profile_service):
    """The opposite branch — a stuck 'always show' would pass the tests above."""
    profile = profile_service.create_profile("Quiet")
    client = Mock()
    client.activate_profile.return_value = Mock(activated=True)
    window._client = client
    window._populate_sidebar_profiles(select_id=profile.id)
    # The fixture's window is disconnected, so the banner already carries that
    # message; a success must leave it exactly as it was.
    before = window.error_banner._message_label.text()
    window.findChild(QPushButton, "Sidebar_Btn_applyProfile").click()
    assert profile_service.active_id == profile.id  # precondition: it activated
    assert window.error_banner._message_label.text() == before
    assert "Could not activate" not in before


def _ok_client() -> Mock:
    client = Mock()
    client.activate_profile.return_value = Mock(activated=True)
    return client


def _sidebar_apply(window, profile_id: str, client) -> None:
    window._client = client
    window._populate_sidebar_profiles(select_id=profile_id)
    assert window.sidebar.profile_combo.currentData() == profile_id  # precondition
    window.findChild(QPushButton, "Sidebar_Btn_applyProfile").click()


class TestALaterSuccessTakesTheFailureDown:
    """Review finding (P3, fixed in DEC-416 by the user's choice): `show_warning`
    never auto-dismisses, so before this a failure banner stayed up after the
    retry that succeeded — the normal next step once CTRL-k shows the reason."""

    def test_sidebar_retry(self, window, profile_service):
        profile = profile_service.create_profile("Quiet")
        _sidebar_apply(window, profile.id, _failing_client(_REJECT))
        assert not window.error_banner.isHidden()  # precondition: the failure is up
        _sidebar_apply(window, profile.id, _ok_client())
        assert profile_service.active_id == profile.id  # precondition: it activated
        assert window.error_banner.isHidden()

    def test_dashboard_retry(self, window, profile_service):
        profile = profile_service.create_profile("Quiet")
        page = window.dashboard_page
        page._client = _failing_client(_GONE)
        page._activate_profile_by_id(profile.id)
        assert not window.error_banner.isHidden()  # precondition
        page._client = _ok_client()
        page._activate_profile_by_id(profile.id)
        assert profile_service.active_id == profile.id  # precondition
        assert window.error_banner.isHidden()

    def test_re_applying_the_active_profile_clears_it_too(self, window, profile_service):
        """The same-id retry: a fix keyed on `active_changed` would miss this,
        because the active id never moves."""
        profile = profile_service.create_profile("Quiet")
        _sidebar_apply(window, profile.id, _ok_client())
        assert profile_service.active_id == profile.id  # precondition: already active
        _sidebar_apply(window, profile.id, _failing_client(_REJECT))
        assert "Could not activate" in window.error_banner._message_label.text()
        _sidebar_apply(window, profile.id, _ok_client())
        assert window.error_banner.isHidden()

    def test_a_warning_shown_since_is_left_alone(self, window, profile_service):
        profile = profile_service.create_profile("Quiet")
        _sidebar_apply(window, profile.id, _failing_client(_REJECT))
        window.error_banner.show_warning("Daemon disconnected — retrying...")
        _sidebar_apply(window, profile.id, _ok_client())
        assert not window.error_banner.isHidden()
        assert window.error_banner._message_label.text() == "Daemon disconnected — retrying..."


class TestHideIfShowing:
    def test_hides_only_the_named_message(self, qtbot):
        from control_ofc.ui.widgets.error_banner import ErrorBanner

        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_warning("A")
        assert banner.hide_if_showing("B") is False
        assert not banner.isHidden()
        assert banner.hide_if_showing("A") is True
        assert banner.isHidden()
        assert banner.hide_if_showing("A") is False, "an already-hidden banner reports False"
