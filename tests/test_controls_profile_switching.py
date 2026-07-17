"""Tests for profile selection, switching, and per-profile content isolation in ControlsPage.

DEC-214: the Controls page dropped its own profile combo + Activate button.
Profile *viewing/editing* on the page is now driven by ``select_profile(id)``
(tracked in ``_viewed_profile_id``); *activation* and the unsaved-changes-on-
switch guard moved to the sidebar Apply flow in ``main_window``. These tests
verify the viewed-profile tracking, per-profile content isolation, and — via a
real ``MainWindow`` — the relocated unsaved-guard.
"""

from __future__ import annotations

import pytest

from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    ProfileService,
)
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import ControlsPage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_state():
    from control_ofc.api.models import ConnectionState, OperationMode

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def controls_page(qtbot, app_state, profile_service):
    """A ControlsPage wired to the tmp_path-backed profile service, no daemon."""
    page = ControlsPage(
        state=app_state,
        profile_service=profile_service,
        client=None,
    )
    qtbot.addWidget(page)
    return page


@pytest.fixture()
def main_window(qtbot, app_state, profile_service, settings_service):
    """A real MainWindow (client=None, non-demo). DEC-214: the unsaved-changes
    guard on a profile switch now lives in its sidebar Apply flow."""
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _viewed_id(page: ControlsPage) -> str:
    """Return the ID of the profile the page currently views (DEC-214)."""
    prof = page._get_current_profile()
    return prof.id if prof else ""


def _select_profile_by_id(page: ControlsPage, profile_id: str) -> None:
    """View a profile on the page by its ID (DEC-214: replaces combo selection)."""
    page.select_profile(profile_id)


def _profile_ids(page: ControlsPage) -> list[str]:
    """Return all known profile IDs from the service, in order."""
    return [p.id for p in page._profile_service.profiles]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProfileSelectionChangesViewedProfile:
    """Selecting a non-active profile in the combo makes it the *viewed* profile."""

    def test_profile_selection_changes_viewed_profile(self, controls_page, profile_service):
        # Start with the three default profiles; the first is active.
        profiles = profile_service.profiles
        assert len(profiles) >= 2
        active = profile_service.active_profile
        assert active is not None

        # Pick a profile that is NOT the active one.
        other = next(p for p in profiles if p.id != active.id)

        # View it on the page.
        _select_profile_by_id(controls_page, other.id)

        # _get_current_profile must now return the OTHER profile, not the active one.
        viewed = controls_page._get_current_profile()
        assert viewed is not None
        assert viewed.id == other.id
        assert viewed.id != active.id


class TestProfileSelectionPreservesAcrossRefresh:
    """A non-active profile selection survives a page refresh."""

    def test_profile_selection_preserves_across_refresh(self, controls_page, profile_service):
        profiles = profile_service.profiles
        active = profile_service.active_profile
        other = next(p for p in profiles if p.id != active.id)

        # View the non-active profile.
        _select_profile_by_id(controls_page, other.id)
        assert _viewed_id(controls_page) == other.id

        # Trigger the refresh path that fires on external CRUD (save/rename/delete).
        controls_page._on_profiles_changed()

        # The same non-active profile must still be viewed.
        assert _viewed_id(controls_page) == other.id


class TestNewProfileShowsBlankSlate:
    """Creating a new profile via _on_new_profile yields an empty profile."""

    def test_new_profile_shows_blank_slate(self, controls_page, profile_service):
        count_before = len(profile_service.profiles)

        # Bypass the QInputDialog by passing a name directly.
        controls_page._on_new_profile(name="Empty Test")

        # A profile was added.
        assert len(profile_service.profiles) == count_before + 1

        # The combo should now point at the new profile.
        viewed = controls_page._get_current_profile()
        assert viewed is not None
        assert viewed.name == "Empty Test"

        # The new profile must have no controls and no curves.
        assert viewed.controls == []
        assert viewed.curves == []


class TestPerProfileContentIsolation:
    """Switching between profiles shows only that profile's controls and curves."""

    def test_per_profile_content_isolation(self, controls_page, profile_service):
        # --- set up profile A with a curve and a control ---
        profile_a = profile_service.create_profile("Profile A")
        curve_a = CurveConfig(
            name="A Curve",
            type=CurveType.FLAT,
            flat_output_pct=42.0,
        )
        control_a = LogicalControl(
            name="A Fan Role",
            mode=ControlMode.CURVE,
            curve_id=curve_a.id,
        )
        profile_a.curves.append(curve_a)
        profile_a.controls.append(control_a)
        profile_service.save_profile(profile_a)

        # --- set up profile B with nothing ---
        profile_b = profile_service.create_profile("Profile B")
        assert profile_b.controls == []
        assert profile_b.curves == []

        # --- Switch to B: must show zero controls and zero curves ---
        # select_profile refreshes the page for the viewed profile (DEC-214).
        _select_profile_by_id(controls_page, profile_b.id)
        viewed_b = controls_page._get_current_profile()
        assert viewed_b is not None
        assert viewed_b.id == profile_b.id
        assert len(viewed_b.controls) == 0
        assert len(viewed_b.curves) == 0
        # Control card dict should be empty after refresh.
        assert len(controls_page._control_cards) == 0
        assert len(controls_page._curve_cards) == 0

        # --- Switch to A: must show its control and curve ---
        _select_profile_by_id(controls_page, profile_a.id)
        viewed_a = controls_page._get_current_profile()
        assert viewed_a is not None
        assert viewed_a.id == profile_a.id
        assert len(viewed_a.controls) == 1
        assert viewed_a.controls[0].name == "A Fan Role"
        assert len(viewed_a.curves) == 1
        assert viewed_a.curves[0].name == "A Curve"
        # UI cards must reflect the profile content.
        assert len(controls_page._control_cards) == 1
        assert len(controls_page._curve_cards) == 1


# DEC-214: TestProfileActivationUpdatesComboLabel was deleted with the page profile
# combo — the "* " active-prefix label lived on that combo. Activation itself (setting
# ProfileService.active_id and bridging AppState.active_profile_name) is covered by
# test_profile_activation_r24.py and by TestUnsavedGuardOnProfileSwitch below (which
# drives the sidebar Apply flow that now owns activation).


class TestDeleteProfileSwitchesToActive:
    """Deleting the viewed (non-active) profile falls the page back to the active one."""

    def test_delete_profile_switches_to_active(self, controls_page, profile_service, monkeypatch):
        active = profile_service.active_profile
        assert active is not None

        # Create a sacrificial profile, view it, then delete it.
        victim = profile_service.create_profile("Doomed")
        _select_profile_by_id(controls_page, victim.id)
        assert _viewed_id(controls_page) == victim.id

        # The autouse modal guard declines confirmations by default; opt into
        # "Yes" here to exercise the actual deletion path.
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

        # Delete it.
        controls_page._on_delete_profile()

        # The victim must no longer exist in the profile store.
        assert victim.id not in _profile_ids(controls_page)

        # The page must fall back to viewing the active profile.
        assert _viewed_id(controls_page) == active.id

        # _get_current_profile must also return the active profile.
        viewed = controls_page._get_current_profile()
        assert viewed is not None
        assert viewed.id == active.id


class TestUnsavedGuardOnProfileSwitch:
    """DEC-214: the unsaved-changes-on-switch guard relocated from the removed page
    combo to ``main_window._on_sidebar_apply_profile`` (the sidebar Apply flow)."""

    @staticmethod
    def _select_sidebar(main_window, profile_id: str) -> None:
        combo = main_window.sidebar.profile_combo
        combo.setCurrentIndex(combo.findData(profile_id))

    def test_keep_editing_blocks_switch(self, main_window, profile_service, monkeypatch):
        """Declining the discard prompt vetoes the switch, keeps edits, and snaps
        the sidebar combo back to the still-active profile."""
        active = profile_service.active_profile
        assert active is not None
        assert len(profile_service.profiles) >= 2
        other = next(p for p in profile_service.profiles if p.id != active.id)

        # Simulate in-progress edits on the Controls page, pick a different profile
        # in the sidebar, then decline the discard.
        main_window.controls_page._set_unsaved(True)
        self._select_sidebar(main_window, other.id)
        monkeypatch.setattr(main_window.controls_page, "confirm_discard_unsaved", lambda: False)

        main_window._on_sidebar_apply_profile()

        assert profile_service.active_id == active.id
        assert main_window.controls_page._has_unsaved is True
        assert main_window.sidebar.profile_combo.currentData() == active.id

    def test_discard_allows_switch(self, main_window, profile_service, app_state, monkeypatch):
        """Confirming the discard performs the switch, clears edits, and bridges the
        new active profile into AppState."""
        active = profile_service.active_profile
        assert active is not None
        assert len(profile_service.profiles) >= 2
        other = next(p for p in profile_service.profiles if p.id != active.id)

        main_window.controls_page._set_unsaved(True)
        self._select_sidebar(main_window, other.id)
        monkeypatch.setattr(main_window.controls_page, "confirm_discard_unsaved", lambda: True)

        main_window._on_sidebar_apply_profile()

        assert profile_service.active_id == other.id
        # Following active_changed the page dropped its unsaved flag.
        assert main_window.controls_page._has_unsaved is False
        # DEC-214: activation bridges into AppState so the banner/dashboard update.
        assert app_state.active_profile_name == other.name

    def test_switch_without_unsaved_does_not_prompt(
        self, main_window, profile_service, monkeypatch
    ):
        """With no unsaved edits the guard never calls the confirm dialog."""
        active = profile_service.active_profile
        assert active is not None
        assert len(profile_service.profiles) >= 2
        other = next(p for p in profile_service.profiles if p.id != active.id)

        main_window.controls_page._set_unsaved(False)
        self._select_sidebar(main_window, other.id)

        called = {"n": 0}

        def _boom() -> bool:
            called["n"] += 1
            return True

        monkeypatch.setattr(main_window.controls_page, "confirm_discard_unsaved", _boom)
        main_window._on_sidebar_apply_profile()

        assert called["n"] == 0
        assert profile_service.active_id == other.id


class TestNavigationDoesNotGuardUnsaved:
    """DEC-214 design lock: the unsaved-changes guard fires *only* on the sidebar
    Apply (profile-switch) flow, NOT on plain page navigation. Navigating away from
    Controls with in-progress edits silently proceeds. This pins that decision so a
    future change in either direction (adding a nav guard, or removing the Apply
    guard) trips a test rather than passing silently (audit Rank 2)."""

    def test_navigating_away_with_unsaved_does_not_prompt(self, main_window, monkeypatch):
        from control_ofc.constants import PAGE_LOGS

        main_window.controls_page._set_unsaved(True)

        calls = {"n": 0}

        def _guard() -> bool:
            calls["n"] += 1
            return True

        monkeypatch.setattr(main_window.controls_page, "confirm_discard_unsaved", _guard)

        # Route the sidebar to another page (Logs) exactly as a nav click would.
        main_window._on_nav_activated(PAGE_LOGS, -1)

        # The guard must NOT be consulted, the edits are silently retained, and the
        # page switches regardless.
        assert calls["n"] == 0
        assert main_window.controls_page._has_unsaved is True
        assert main_window.page_stack.currentIndex() == PAGE_LOGS
