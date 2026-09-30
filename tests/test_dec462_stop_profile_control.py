"""DEC-462 (`DC-co`, `G181`): stop profile control from the GUI without deleting a profile.

Before this the only GUI route to ``POST /profile/deactivate`` was deleting the
active profile; the remediation text sent users to the tray. The user's choices:
a **Stop** button beside the sidebar's Apply (Q4-A), enabled only while a profile
is active; no confirmation, an info banner saying what the fans do now and how
to resume (Q5-A, 10 s); the Dashboard's "Active profile" combo shows "No active
profile" instead of the last or first profile (Q6-A); and a deactivation keeps
the Controls page on the profile it was showing, unsaved edits included.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import ProfileDeactivateResult
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.dashboard_page import NO_ACTIVE_PROFILE_TEXT

_REJECT = DaemonError(code="internal_error", message="state write failed")
_GONE = DaemonUnavailable(message="connection refused")


def _client(result: ProfileDeactivateResult | None = None, exc: Exception | None = None) -> Mock:
    client = Mock()
    if exc is not None:
        client.deactivate_profile.side_effect = exc
    else:
        client.deactivate_profile.return_value = result or ProfileDeactivateResult(
            deactivated=True, previous_profile_id="x", previous_profile_name="X"
        )
    return client


# ── ProfileService.deactivate: daemon first, then the local id ────────────


class TestServiceDeactivate:
    @pytest.fixture()
    def svc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        state = AppState()
        svc = ProfileService()
        svc.attach_state(state)
        svc.load()
        profile = svc.create_profile("Quiet")
        svc.set_active(profile.id)
        return svc, state, profile

    def test_a_confirmed_stop_clears_the_id_and_asks_for_fresh_headers(self, svc, qtbot):
        service, state, _profile = svc
        seen: list[str] = []
        service.active_changed.connect(seen.append)
        client = _client()
        with qtbot.waitSignal(state.hwmon_headers_refresh_requested, timeout=500):
            out = service.deactivate(client=client)

        assert out.deactivated and not out.local_only and out.error is None
        client.deactivate_profile.assert_called_once_with()
        assert service.active_id == ""
        assert seen == [""]

    @pytest.mark.parametrize("exc", [_REJECT, _GONE], ids=["rejected", "unreachable"])
    def test_a_failed_stop_leaves_the_profile_active(self, svc, exc):
        service, _state, profile = svc
        out = service.deactivate(client=_client(exc=exc))
        assert not out.deactivated
        assert out.error == exc.message
        assert service.active_id == profile.id

    def test_a_daemon_answer_of_false_is_a_failure(self, svc):
        service, _state, profile = svc
        out = service.deactivate(client=_client(ProfileDeactivateResult(deactivated=False)))
        assert not out.deactivated and out.error
        assert service.active_id == profile.id

    def test_demo_clears_locally(self, svc):
        service, _state, _profile = svc
        out = service.deactivate(client=None)
        assert out.deactivated and out.local_only
        assert service.active_id == ""


# ── The sidebar's Stop, through the real click ─────────────────────────


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


def _stop_btn(window) -> QPushButton:
    return window.findChild(QPushButton, "Sidebar_Btn_stopProfile")


def _two_profiles_second_active(window, profile_service, app_state):
    """B active and on screen — B is not the first entry, which is where an
    empty active id used to drop the sidebar and the Controls page."""
    a = profile_service.create_profile("Alpha")
    b = profile_service.create_profile("Bravo")
    profile_service.set_active(b.id)
    app_state.set_active_profile(b.name)
    window._populate_sidebar_profiles(select_id=b.id)
    assert window.sidebar.profile_combo.itemData(0) != b.id  # precondition
    assert window.controls_page.viewed_profile_id == b.id  # precondition
    return a, b


class TestSidebarStop:
    def test_the_button_follows_the_active_profile(self, window, profile_service):
        btn = _stop_btn(window)
        profile_service.set_active("")
        assert not btn.isEnabled()
        assert btn.toolTip() == "No profile is running"
        assert btn.accessibleName() == "Stop profile control"

        profile = profile_service.create_profile("Quiet")
        profile_service.set_active(profile.id)
        assert btn.isEnabled()

    def test_stop_deactivates_keeps_the_profile_and_says_what_happens(
        self, window, profile_service, app_state
    ):
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        window._client = _client()

        _stop_btn(window).click()

        window._client.deactivate_profile.assert_called_once_with()
        assert profile_service.active_id == ""
        assert profile_service.get_profile(b.id) is not None, "Stop deletes nothing"
        assert app_state.active_profile_name == ""
        assert window.error_banner._message_label.text() == MainWindow.STOP_PROFILE_MESSAGE
        assert not _stop_btn(window).isEnabled()

    @pytest.mark.parametrize("exc", [_REJECT, _GONE], ids=["rejected", "unreachable"])
    def test_a_failed_stop_says_so_and_changes_nothing(
        self, window, profile_service, app_state, exc
    ):
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        window._client = _client(exc=exc)

        _stop_btn(window).click()

        text = window.error_banner._message_label.text()
        assert text == f"Could not stop profile control: {exc.message}"
        assert profile_service.active_id == b.id
        assert app_state.active_profile_name == b.name
        assert _stop_btn(window).isEnabled()

    def test_the_sidebar_and_controls_page_stay_on_the_stopped_profile(
        self, window, profile_service, app_state
    ):
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        window.controls_page._set_unsaved(True)
        window._client = _client()

        _stop_btn(window).click()

        combo = window.sidebar.profile_combo
        assert combo.currentData() == b.id
        assert "(active)" not in combo.currentText()
        assert window.controls_page.viewed_profile_id == b.id
        assert window.controls_page.has_unsaved_changes()

    def test_demo_stops_locally(self, window, profile_service, app_state):
        _two_profiles_second_active(window, profile_service, app_state)
        window._client = None
        _stop_btn(window).click()
        assert profile_service.active_id == ""
        # Demo hands nothing back, so it does not claim the fans went anywhere.
        text = window.error_banner._message_label.text()
        assert text == MainWindow.STOP_PROFILE_MESSAGE_DEMO
        assert text != MainWindow.STOP_PROFILE_MESSAGE


# ── A deactivation from elsewhere (tray, another client) ───────────────


class TestControlsPageOnDeactivation:
    def test_an_outside_stop_keeps_the_view_and_the_edits(self, window, profile_service, app_state):
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        window.controls_page._set_unsaved(True)

        profile_service.set_active("")  # what the poll's cleared id does (DEC-194)

        assert window.controls_page.viewed_profile_id == b.id
        assert window.controls_page.has_unsaved_changes()

    def test_an_activation_still_switches_and_clears_unsaved(
        self, window, profile_service, app_state
    ):
        """The opposite branch: following an activation is unchanged."""
        a, _b = _two_profiles_second_active(window, profile_service, app_state)
        window.controls_page._set_unsaved(True)

        profile_service.set_active(a.id)

        assert window.controls_page.viewed_profile_id == a.id
        assert not window.controls_page.has_unsaved_changes()


# ── Deleting the active profile goes through the same path ───────────────


class TestDeleteUsesTheSharedPath:
    def test_deleting_the_active_profile_deactivates_through_the_service(
        self, window, profile_service, app_state, monkeypatch
    ):
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        page = window.controls_page
        page._client = _client()
        calls: list = []
        real = profile_service.deactivate
        monkeypatch.setattr(
            profile_service, "deactivate", lambda **kw: calls.append(kw) or real(**kw)
        )
        monkeypatch.setattr(
            "control_ofc.ui.pages.controls_page.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )

        page._on_delete_profile()

        assert calls == [{"client": page._client}]
        page._client.deactivate_profile.assert_called_once_with()
        assert profile_service.get_profile(b.id) is None
        assert profile_service.active_id == ""

    def test_a_failed_deactivate_still_deletes(
        self, window, profile_service, app_state, monkeypatch
    ):
        """Unchanged behaviour: the file is the canonical source, so the delete goes on."""
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        page = window.controls_page
        page._client = _client(exc=_GONE)
        monkeypatch.setattr(
            "control_ofc.ui.pages.controls_page.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )

        page._on_delete_profile()

        assert profile_service.get_profile(b.id) is None


# ── The Dashboard's "Active profile" combo ───────────────────────────


class TestDashboardCombo:
    def test_it_names_no_profile_when_none_is_active(self, window, profile_service, app_state):
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles_second_active(window, profile_service, app_state)
        assert combo.currentData() == b.id, "precondition: it names the active profile"

        profile_service.set_active("")
        assert combo.currentIndex() == -1
        assert combo.placeholderText() == NO_ACTIVE_PROFILE_TEXT

        profile_service.set_active(b.id)
        assert combo.currentData() == b.id

    def test_the_state_name_clearing_alone_clears_it(self, window, profile_service, app_state):
        """AppState's by-name path (the status banner's source) clears it too."""
        combo = window.dashboard_page._profile_combo
        _two_profiles_second_active(window, profile_service, app_state)
        app_state.set_active_profile("")
        assert combo.currentIndex() == -1

    def test_a_rebuild_with_nothing_active_does_not_pick_the_first_profile(
        self, window, profile_service, app_state
    ):
        page = window.dashboard_page
        _two_profiles_second_active(window, profile_service, app_state)
        profile_service.set_active("")
        page.populate_profiles()
        assert page._profile_combo.count() >= 2, "precondition: there are profiles to pick"
        assert page._profile_combo.currentIndex() == -1


# ── Review P2: a later warning must not inherit the Stop notice's timer ───


class TestBannerTimer:
    @pytest.mark.parametrize("show", ["show_warning", "show_error", "show_info"])
    def test_a_message_without_auto_dismiss_cancels_an_earlier_timer(self, qtbot, show):
        from control_ofc.ui.widgets.error_banner import ErrorBanner

        banner = ErrorBanner()
        qtbot.addWidget(banner)
        banner.show_info("notice", 10_000)
        assert banner._auto_dismiss_timer.isActive(), "precondition: the notice armed it"

        getattr(banner, show)("stays until dismissed", 0)

        assert not banner._auto_dismiss_timer.isActive()
        assert not banner.isHidden()

    def test_a_failed_apply_after_a_stop_is_not_dismissed_by_the_stop_notice(
        self, window, profile_service, app_state
    ):
        a, _b = _two_profiles_second_active(window, profile_service, app_state)
        window._client = _client()
        _stop_btn(window).click()
        assert window.error_banner._auto_dismiss_timer.isActive(), "precondition"

        window._client.activate_profile.side_effect = _GONE
        window._populate_sidebar_profiles(select_id=a.id)
        window.findChild(QPushButton, "Sidebar_Btn_applyProfile").click()

        assert window.error_banner._message_label.text().startswith("Could not activate")
        assert not window.error_banner._auto_dismiss_timer.isActive()
