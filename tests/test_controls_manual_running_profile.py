"""Manual is offered only on the cards of the profile the daemon is running.

``POST /control/{id}/override`` names a control of the daemon's ACTIVE profile
by id alone. From a profile the user was only browsing, Manual pinned the
running profile's control of the same id — a duplicate keeps its ids, so other
fans moved than the card showed — or got a ``404`` and the card snapped back
with no message.
"""

from __future__ import annotations

from control_ofc.services.profile_service import (
    DELETE_REFUSED_DAEMON_ERROR,
    DELETE_REFUSED_OFFLINE,
    DELETE_REFUSED_READ_ONLY,
    ControlMember,
    DeleteRefusal,
    LogicalControl,
)
from control_ofc.ui.pages.controls_page import (
    ControlsPage,
    delete_refused_message,
    manual_unavailable_reason,
)


def _with_member(profile):
    profile.controls = [
        LogicalControl(
            id="ctl",
            name="Case",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
    ]
    return profile


def _manual_btn(page, control_id="ctl"):
    return page._control_cards[control_id]._manual_btn


def test_manual_only_on_the_running_profiles_cards(qtbot, app_state, profile_service):
    running = _with_member(profile_service.active_profile)
    browsed = _with_member(profile_service.duplicate_profile(running.id, "Copy"))
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    page.select_profile(running.id)
    assert _manual_btn(page).isEnabled()

    page.select_profile(browsed.id)
    btn = _manual_btn(page)
    assert browsed.controls[0].id == running.controls[0].id  # the shared-id hazard
    assert not btn.isEnabled()
    assert btn.toolTip() == manual_unavailable_reason(browsed.id, running.id)

    # Activating the browsed profile makes its cards the running ones.
    profile_service.set_active(browsed.id)
    assert _manual_btn(page).isEnabled()


def test_no_manual_anywhere_while_no_profile_runs(qtbot, app_state, profile_service):
    viewed = _with_member(profile_service.active_profile)
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(viewed.id)
    assert _manual_btn(page).isEnabled()  # precondition

    profile_service.set_active("")  # the daemon stopped running a profile
    btn = _manual_btn(page)
    assert not btn.isEnabled()
    assert btn.toolTip() == manual_unavailable_reason(viewed.id, "")


def test_manual_reason_claims_no_daemon_state_it_did_not_receive():
    """An empty active id is also an unheld running profile (WUI-d), an offline
    start, or the moment before the first poll — not proof nothing runs."""
    assert manual_unavailable_reason("a", "a") is None
    assert manual_unavailable_reason("a", "") == manual_unavailable_reason("a", "b")
    assert "Activate this profile" in manual_unavailable_reason("a", "")
    assert "No profile is running" not in manual_unavailable_reason("a", "")


class TestDeleteRefusedMessage:
    def test_offline_says_the_daemon_keeps_its_copy(self):
        text = delete_refused_message("Bravo", None, DeleteRefusal(DELETE_REFUSED_OFFLINE))
        assert "not reachable" in text
        assert "still running" not in text

    def test_daemon_error_carries_its_detail(self):
        text = delete_refused_message(
            "Bravo", None, DeleteRefusal(DELETE_REFUSED_DAEMON_ERROR, "failed to delete profile")
        )
        assert "(failed to delete profile)" in text
        assert "still running" not in text

    def test_read_only_says_it_cannot_be_deleted_here(self):
        text = delete_refused_message("Quiet", None, DeleteRefusal(DELETE_REFUSED_READ_ONLY))
        assert "read-only" in text
        assert "still running" not in text

    def test_a_profile_already_stopped_says_so(self):
        refusal = DeleteRefusal(DELETE_REFUSED_OFFLINE)
        assert "no longer running" in delete_refused_message("B", None, refusal, stopped=True)
        assert "no longer running" not in delete_refused_message("B", None, refusal)

    def test_no_refusal_keeps_the_in_use_wording(self):
        assert delete_refused_message("Bravo", None, None) == delete_refused_message("Bravo", None)
        assert "still running" in delete_refused_message("Bravo", None, None)
