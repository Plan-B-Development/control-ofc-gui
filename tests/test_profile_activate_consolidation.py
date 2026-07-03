"""Regression tests for the profile activate-flow consolidation (Cluster 3).

Profile activation is now a single shared path — ``ProfileService.activate`` —
delegated to by both the Controls and Dashboard pages. These tests pin the two
bugs the consolidation fixed:

1. the Dashboard combo used to stay on a *failed* pick (no revert);
2. the Dashboard combo was populated once at startup and went stale after any
   profile CRUD elsewhere;

plus the service's new signals and the load-bearing daemon-confirm-before-local
ordering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import ProfileActivateResult
from control_ofc.ui.pages.dashboard_page import DashboardPage


def _other_id(profile_service) -> str:
    """An id that is not the currently-active one (fixture loads >= 2)."""
    return next(p.id for p in profile_service.profiles if p.id != profile_service.active_id)


# ---------------------------------------------------------------------------
# Bug fix 1 — Dashboard reverts its combo when the daemon rejects the switch
# ---------------------------------------------------------------------------


class TestDashboardComboRevert:
    def test_reverts_combo_on_daemon_reject(self, qtbot, app_state, profile_service):
        client = MagicMock()
        client.activate_profile.return_value = ProfileActivateResult(activated=False)
        page = DashboardPage(state=app_state, profile_service=profile_service, client=client)
        qtbot.addWidget(page)
        page.populate_profiles()
        assert page._profile_combo.count() >= 2

        active_id = profile_service.active_id
        other = _other_id(profile_service)
        # Select a different profile — the pick the daemon will reject.
        page._profile_combo.setCurrentIndex(page._profile_combo.findData(other))
        assert page._profile_combo.currentData() == other

        page._on_apply_profile()

        # Bug fix: the combo reverts to the previously-active profile rather than
        # stranding on the failed pick, and local state is untouched.
        assert page._profile_combo.currentData() == active_id
        assert profile_service.active_id == active_id

    def test_apply_success_sets_active_and_state(self, qtbot, app_state, profile_service):
        client = MagicMock()
        client.activate_profile.return_value = ProfileActivateResult(activated=True)
        page = DashboardPage(state=app_state, profile_service=profile_service, client=client)
        qtbot.addWidget(page)
        page.populate_profiles()

        target = next(p for p in profile_service.profiles if p.id != profile_service.active_id)
        page._profile_combo.setCurrentIndex(page._profile_combo.findData(target.id))
        page._on_apply_profile()

        assert profile_service.active_id == target.id
        assert app_state.active_profile_name == target.name


# ---------------------------------------------------------------------------
# Bug fix 2 — Dashboard combo refreshes after CRUD elsewhere
# ---------------------------------------------------------------------------


class TestDashboardComboStaleness:
    def test_combo_refreshes_after_create_and_delete(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service, client=None)
        qtbot.addWidget(page)
        page.populate_profiles()
        before = page._profile_combo.count()

        created = profile_service.create_profile("Freshly Made")
        # profiles_changed → populate_profiles rebuild (previously stale).
        assert page._profile_combo.count() == before + 1
        assert page._profile_combo.findData(created.id) >= 0

        profile_service.delete_profile(created.id)
        assert page._profile_combo.count() == before
        assert page._profile_combo.findData(created.id) < 0

    def test_combo_preserves_selection_across_rebuild(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service, client=None)
        qtbot.addWidget(page)
        page.populate_profiles()

        keep = _other_id(profile_service)
        page._profile_combo.setCurrentIndex(page._profile_combo.findData(keep))

        # A create elsewhere rebuilds the combo but must keep the user's pick.
        profile_service.create_profile("Another")
        assert page._profile_combo.currentData() == keep


# ---------------------------------------------------------------------------
# ProfileService signals
# ---------------------------------------------------------------------------


class TestProfileServiceSignals:
    def test_active_changed_fires_on_set_active(self, qtbot, profile_service):
        seen: list[str] = []
        profile_service.active_changed.connect(seen.append)

        target = _other_id(profile_service)
        assert profile_service.set_active(target) is True
        assert seen == [target]

        # Edge-triggered: the same id again must not re-emit.
        profile_service.set_active(target)
        assert seen == [target]

    def test_active_changed_fires_via_activate_local(self, qtbot, profile_service):
        seen: list[str] = []
        profile_service.active_changed.connect(seen.append)

        target = _other_id(profile_service)
        profile_service.activate(target, client=None)
        assert seen == [target]

    def test_profiles_changed_fires_on_create_and_delete(self, qtbot, profile_service):
        fired: list[bool] = []
        profile_service.profiles_changed.connect(lambda: fired.append(True))

        created = profile_service.create_profile("Temp")
        assert fired  # create → save_profile → profiles_changed

        fired.clear()
        profile_service.delete_profile(created.id)
        assert fired  # delete → profiles_changed


# ---------------------------------------------------------------------------
# activate() ordering — daemon-confirm-before-local
# ---------------------------------------------------------------------------


class TestActivateOrdering:
    def test_reject_does_not_change_active_id(self, qtbot, profile_service):
        client = MagicMock()
        client.activate_profile.return_value = ProfileActivateResult(activated=False)
        prev = profile_service.active_id
        target = _other_id(profile_service)

        res = profile_service.activate(target, client=client)

        assert res.activated is False
        assert res.error == "Activation rejected by daemon"
        # Load-bearing ordering: a rejected activation must NOT set local active.
        assert profile_service.active_id == prev

    def test_daemon_error_is_captured_not_raised(self, qtbot, profile_service):
        client = MagicMock()
        client.activate_profile.side_effect = DaemonError(
            code="validation_error",
            message="boom",
            retryable=False,
            source="validation",
            status=400,
        )
        prev = profile_service.active_id
        target = _other_id(profile_service)

        res = profile_service.activate(target, client=client)

        assert res.activated is False
        assert res.error == "boom"
        assert profile_service.active_id == prev

    def test_success_sets_active_after_confirm(self, qtbot, profile_service):
        client = MagicMock()
        client.activate_profile.return_value = ProfileActivateResult(activated=True)
        target = _other_id(profile_service)

        res = profile_service.activate(target, client=client)

        assert res.activated is True
        assert res.local_only is False
        assert profile_service.active_id == target
        client.activate_profile.assert_called_once()

    def test_no_client_is_local_only(self, qtbot, profile_service):
        target = _other_id(profile_service)

        res = profile_service.activate(target, client=None)

        assert res.activated is True
        assert res.local_only is True
        assert profile_service.active_id == target

    def test_unknown_profile_returns_error(self, qtbot, profile_service):
        res = profile_service.activate("does-not-exist", client=None)

        assert res.activated is False
        assert res.error
        assert profile_service.active_id != "does-not-exist"
