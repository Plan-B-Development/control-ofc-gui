"""`G216` (DEC-462's review residuals): what the GUI says is active.

* `WUI-a` — the sidebar's Stop and the Dashboard's combo follow the daemon's
  running profile even when the GUI does not hold it (a ``--profile`` from a
  system folder): ``set_active`` records no id the GUI does not hold (`WUI-d`),
  so AppState's daemon-reported name is the only signal. Stop is enabled by
  either source and names the profile; the combo names it in its placeholder
  ("Running: <name> (not in this GUI)"); "No active profile" only with both empty.
* `WUI-b` — a failed Dashboard Apply from the stopped state goes back to the
  placeholder instead of naming the profile that failed.
* `WUI-c` — a delete the daemon refuses (`409 profile_in_use`) leaves AppState
  naming the profile and says so on the main window's banner.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import ProfileDeactivateResult
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import delete_refused_message
from control_ofc.ui.pages.dashboard_page import NO_ACTIVE_PROFILE_TEXT, unheld_profile_text

_GONE = DaemonUnavailable(message="connection refused")
_IN_USE = DaemonError(code="profile_in_use", message="profile is active")
# A profile the daemon runs from its own folder — no GUI profile has this id or name.
_UNHELD_ID = "sys-silent"
_UNHELD_NAME = "Silent-Server"


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    # `load()` leaves a starter active; every test here starts from the stopped state.
    profile_service.set_active("")
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def _client() -> Mock:
    client = Mock()
    client.deactivate_profile.return_value = ProfileDeactivateResult(
        deactivated=True, previous_profile_id=_UNHELD_ID, previous_profile_name=_UNHELD_NAME
    )
    return client


def _stop_btn(window) -> QPushButton:
    return window.findChild(QPushButton, "Sidebar_Btn_stopProfile")


def _two_profiles(window, profile_service):
    a = profile_service.create_profile("Alpha")
    b = profile_service.create_profile("Bravo")
    window._populate_sidebar_profiles(select_id=b.id)
    return a, b


def _daemon_runs_unheld(app_state, profile_service):
    """What the poll does (`AppState.apply_status`): id first, then name. The id
    reaches ``ProfileService.set_active``, which records no id for it (`WUI-d`)."""
    app_state.set_active_profile_id(_UNHELD_ID)
    app_state.set_active_profile(_UNHELD_NAME)
    assert profile_service.active_id == "", "precondition: the GUI does not hold it"


# ── WUI-a: the sidebar's Stop ─────────────────────────────────────────────


class TestStopFollowsTheDaemon:
    def test_an_unheld_running_profile_enables_stop_and_is_named(
        self, window, profile_service, app_state
    ):
        btn = _stop_btn(window)
        assert not btn.isEnabled(), "precondition: nothing running"

        _daemon_runs_unheld(app_state, profile_service)

        assert btn.isEnabled()
        assert f"'{_UNHELD_NAME}'" in btn.toolTip()

    def test_stop_on_an_unheld_profile_deactivates_and_disables(
        self, window, profile_service, app_state
    ):
        _daemon_runs_unheld(app_state, profile_service)
        window._client = _client()

        _stop_btn(window).click()

        window._client.deactivate_profile.assert_called_once_with()
        assert app_state.active_profile_name == ""
        assert window.error_banner._message_label.text() == MainWindow.STOP_PROFILE_MESSAGE
        assert not _stop_btn(window).isEnabled()

    def test_a_held_profile_is_named_and_a_rename_relabels_it(
        self, window, profile_service, app_state
    ):
        _a, b = _two_profiles(window, profile_service)
        profile_service.set_active(b.id)
        assert "'Bravo'" in _stop_btn(window).toolTip()

        b.name = "Charlie"
        profile_service.save_profile(b)

        assert "'Charlie'" in _stop_btn(window).toolTip()

    def test_with_both_sources_empty_it_is_disabled(self, window, profile_service, app_state):
        _daemon_runs_unheld(app_state, profile_service)
        assert _stop_btn(window).isEnabled(), "precondition: enabled by the name"

        app_state.set_active_profile_id("")
        app_state.set_active_profile("")

        assert not _stop_btn(window).isEnabled()
        assert _stop_btn(window).toolTip() == "No profile is running"


# ── WUI-a: the Dashboard's "Active profile" combo ───────────────────────


class TestDashboardComboFollowsTheDaemon:
    def test_an_unheld_running_profile_is_named_in_the_placeholder(
        self, window, profile_service, app_state
    ):
        combo = window.dashboard_page._profile_combo
        a, _b = _two_profiles(window, profile_service)
        # The user browsed to Alpha without applying it: the combo names a
        # profile that is not running, which is the state this must correct.
        combo.setCurrentIndex(combo.findData(a.id))
        assert combo.currentIndex() >= 0, "precondition"

        _daemon_runs_unheld(app_state, profile_service)

        assert combo.currentIndex() == -1
        assert combo.placeholderText() == unheld_profile_text(_UNHELD_NAME)

    def test_the_placeholder_says_no_profile_only_when_both_are_empty(
        self, window, profile_service, app_state
    ):
        combo = window.dashboard_page._profile_combo
        _two_profiles(window, profile_service)
        _daemon_runs_unheld(app_state, profile_service)
        assert combo.placeholderText() != NO_ACTIVE_PROFILE_TEXT, "precondition"

        app_state.set_active_profile_id("")
        app_state.set_active_profile("")

        assert combo.currentIndex() == -1
        assert combo.placeholderText() == NO_ACTIVE_PROFILE_TEXT

    def test_a_held_profile_is_still_selected_by_id(self, window, profile_service, app_state):
        """The opposite branch: a profile the GUI holds is selected, not named."""
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        profile_service.set_active(b.id)
        app_state.set_active_profile(b.name)
        assert combo.currentData() == b.id

    def test_a_cleared_service_id_alone_keeps_the_running_name(
        self, window, profile_service, app_state
    ):
        """Stop's first signal: the service id clears while AppState still names
        the profile. The combo follows the name, not the cleared id."""
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        app_state.set_active_profile_id(b.id)
        app_state.set_active_profile(b.name)
        assert profile_service.active_id == b.id, "precondition: held and active"
        assert combo.currentData() == b.id, "precondition"

        profile_service.set_active("")

        assert combo.currentData() == b.id

    def test_an_unheld_daemon_id_beats_a_held_profile_of_the_same_name(
        self, window, profile_service, app_state
    ):
        """The daemon runs its own "Bravo" (an id the GUI does not hold): the
        GUI's "Bravo" is a different profile and must not be selected."""
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        assert combo.findText(b.name) >= 0, "precondition: a held profile has the name"

        app_state.set_active_profile_id(_UNHELD_ID)
        app_state.set_active_profile(b.name)

        assert combo.currentIndex() == -1
        assert combo.placeholderText() == unheld_profile_text(b.name)

    def test_an_unheld_profile_by_name_alone_is_named(self, window, profile_service, app_state):
        """No daemon id (an older daemon) and a name no held profile has: the
        by-name path cannot select it, so it names it — over a browsed pick."""
        combo = window.dashboard_page._profile_combo
        a, _b = _two_profiles(window, profile_service)
        combo.setCurrentIndex(combo.findData(a.id))
        assert combo.currentIndex() >= 0, "precondition"

        app_state.set_active_profile(_UNHELD_NAME)

        assert app_state.active_profile_id == "", "precondition: no daemon id"
        assert combo.currentIndex() == -1
        assert combo.placeholderText() == unheld_profile_text(_UNHELD_NAME)

    def test_a_held_profile_by_name_alone_is_selected(self, window, profile_service, app_state):
        """A daemon that reports no id (polling.py's `/profile/active` fallback):
        the held profile is found by its name, and the unheld text is not shown."""
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        app_state.set_active_profile(b.name)
        assert app_state.active_profile_id == "", "precondition: no daemon id"
        assert combo.currentData() == b.id

    def test_a_held_running_profile_after_an_unheld_one(self, window, profile_service, app_state):
        combo = window.dashboard_page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        _daemon_runs_unheld(app_state, profile_service)

        app_state.set_active_profile_id(b.id)
        app_state.set_active_profile(b.name)

        assert combo.currentData() == b.id


# ── WUI-b: a failed Dashboard Apply from the stopped state ──────────────


class TestFailedApplyFromStopped:
    def test_it_goes_back_to_the_placeholder(self, window, profile_service, app_state):
        page = window.dashboard_page
        combo = page._profile_combo
        _a, b = _two_profiles(window, profile_service)
        assert profile_service.active_id == "", "precondition: stopped"
        combo.setCurrentIndex(combo.findData(b.id))
        assert combo.currentData() == b.id, "precondition: the pick is on screen"
        page._client = Mock()
        page._client.activate_profile.side_effect = _GONE

        window.findChild(QPushButton, "Dashboard_Btn_apply").click()

        assert profile_service.active_id == "", "precondition: the Apply failed"
        assert combo.currentIndex() == -1
        assert combo.placeholderText() == NO_ACTIVE_PROFILE_TEXT

    def test_a_failed_apply_with_a_held_profile_running_still_reverts_to_it(
        self, window, profile_service, app_state
    ):
        """The opposite branch: a non-empty revert target is re-selected."""
        page = window.dashboard_page
        combo = page._profile_combo
        a, b = _two_profiles(window, profile_service)
        profile_service.set_active(a.id)
        combo.setCurrentIndex(combo.findData(b.id))
        page._client = Mock()
        page._client.activate_profile.side_effect = _GONE

        window.findChild(QPushButton, "Dashboard_Btn_apply").click()

        assert combo.currentData() == a.id

    def test_a_failed_apply_with_a_profile_running_by_name_returns_to_it(
        self, window, profile_service, app_state
    ):
        """No daemon id (an older daemon): the profile runs by name only, the
        service id is empty, and a failed Apply must land back on it — not on
        the placeholder, and never on "not in this GUI" for a held profile."""
        page = window.dashboard_page
        combo = page._profile_combo
        a, b = _two_profiles(window, profile_service)
        app_state.set_active_profile(b.name)
        assert profile_service.active_id == "" and combo.currentData() == b.id, "precondition"
        combo.setCurrentIndex(combo.findData(a.id))
        page._client = Mock()
        page._client.activate_profile.side_effect = _GONE

        window.findChild(QPushButton, "Dashboard_Btn_apply").click()

        assert combo.currentData() == b.id


# ── WUI-c: a delete the daemon refuses ──────────────────────────────────


def _yes(monkeypatch):
    monkeypatch.setattr(
        "control_ofc.ui.pages.controls_page.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )


class TestRefusedDelete:
    def test_a_refused_delete_keeps_the_name_and_says_so(
        self, window, profile_service, app_state, monkeypatch
    ):
        _a, b = _two_profiles(window, profile_service)
        profile_service.set_active(b.id)
        app_state.set_active_profile(b.name)
        page = window.controls_page
        page._client = Mock()
        page._client.deactivate_profile.side_effect = _GONE
        profile_service._client = Mock()
        profile_service._client.delete_profile.side_effect = _IN_USE
        _yes(monkeypatch)

        window.findChild(QPushButton, "Sidebar_Btn_deleteProfile").click()

        assert profile_service.get_profile(b.id) is not None, "precondition: refused"
        assert app_state.active_profile_name == b.name
        assert window.error_banner._message_label.text() == delete_refused_message(
            b.name, _GONE.message
        )
        assert page.viewed_profile_id == b.id

    def test_an_accepted_delete_still_clears_the_name(
        self, window, profile_service, app_state, monkeypatch
    ):
        """The opposite branch: a delete that went through clears AppState."""
        _a, b = _two_profiles(window, profile_service)
        profile_service.set_active(b.id)
        app_state.set_active_profile(b.name)
        window.controls_page._client = _client()
        _yes(monkeypatch)

        window.findChild(QPushButton, "Sidebar_Btn_deleteProfile").click()

        assert profile_service.get_profile(b.id) is None
        assert app_state.active_profile_name == ""

    def test_the_message_carries_the_stop_reason_when_there_is_one(self):
        with_reason = delete_refused_message("Bravo", "connection refused")
        assert with_reason == (
            "Could not delete 'Bravo': the daemon is still running it (connection refused). "
            "Press Stop, then delete."
        )
        assert "()" not in delete_refused_message("Bravo", None)
        assert delete_refused_message("Bravo", None).startswith(
            "Could not delete 'Bravo': the daemon is still running it. "
        )


# ── WUI-d: the daemon switches to a profile the GUI does not hold ───────


class TestDaemonSwitchToUnheldProfile:
    """The daemon wins (`U17`, narrowing DEC-194's old no-op): an unheld id
    clears the local active id, so a Save on the previously-active held profile
    is an ordinary save, not a DEC-188 re-apply that would replace what runs."""

    @staticmethod
    def _page(qtbot, app_state):
        from control_ofc.api.models import ProfileActivateResult
        from control_ofc.services.profile_service import Profile, ProfileService
        from control_ofc.ui.pages.controls_page import ControlsPage

        client = Mock()
        client.create_profile.return_value = {"created": "p1"}
        client.activate_profile.return_value = ProfileActivateResult(
            activated=True, profile_id="p1", profile_name="P1"
        )
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps._daemon_ids.add("p1")
        app_state.active_profile_id_changed.connect(ps.set_active)  # main_window's wiring
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)
        return page, ps, client

    @staticmethod
    def _daemon_runs(app_state, profile_id, name):
        from control_ofc.api.models import DaemonStatus

        app_state.set_status(
            DaemonStatus(
                active_profile_id=profile_id, active_profile_name=name, has_active_profile=True
            )
        )

    def test_save_after_the_switch_does_not_reactivate_the_held_profile(self, qtbot, app_state):
        page, ps, client = self._page(qtbot, app_state)
        self._daemon_runs(app_state, "p1", "P1")
        assert ps.active_id == "p1", "precondition: the held profile was active"

        self._daemon_runs(app_state, _UNHELD_ID, _UNHELD_NAME)
        assert ps.active_id == ""
        page.select_profile("p1")
        page._save_btn.click()

        client.activate_profile.assert_not_called()
        assert page._unsaved_label.text() == "Settings saved"

    def test_save_while_the_daemon_runs_the_held_profile_still_reapplies(self, qtbot, app_state):
        """The opposite branch: DEC-188's re-apply survives for the real case."""
        page, _ps, client = self._page(qtbot, app_state)
        self._daemon_runs(app_state, "p1", "P1")
        page.select_profile("p1")
        page._save_btn.click()

        client.activate_profile.assert_called_once()
        assert page._unsaved_label.text() == "Saved & reapplied to daemon"
