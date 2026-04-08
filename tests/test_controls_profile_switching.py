"""Tests for profile selection, switching, and per-profile content isolation in ControlsPage.

Verifies that the profile combo box correctly tracks the *viewed* profile
(not just the *active* profile), that switching profiles shows the correct
content, and that profile CRUD operations leave the combo in a sane state.
"""

from __future__ import annotations

import pytest

from onlyfans.services.app_state import AppState
from onlyfans.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    ProfileService,
)
from onlyfans.ui.pages.controls_page import ControlsPage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_state():
    from onlyfans.api.models import ConnectionState, OperationMode

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _combo_profile_id(page: ControlsPage) -> str:
    """Return the profile ID stored as user-data on the currently selected combo item."""
    return page._profile_combo.currentData() or ""


def _combo_profile_text(page: ControlsPage) -> str:
    """Return the display text of the currently selected combo item."""
    return page._profile_combo.currentText()


def _select_profile_by_id(page: ControlsPage, profile_id: str) -> None:
    """Programmatically select a profile in the combo by its ID."""
    for i in range(page._profile_combo.count()):
        if page._profile_combo.itemData(i) == profile_id:
            page._profile_combo.setCurrentIndex(i)
            return
    raise ValueError(f"Profile {profile_id!r} not found in combo")


def _combo_ids(page: ControlsPage) -> list[str]:
    """Return all profile IDs in the combo, in order."""
    return [page._profile_combo.itemData(i) for i in range(page._profile_combo.count())]


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

        # Select it in the combo.
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

        # Select the non-active profile.
        _select_profile_by_id(controls_page, other.id)
        assert _combo_profile_id(controls_page) == other.id

        # Trigger a full combo rebuild (the kind that happens on save/rename).
        controls_page._refresh_profile_combo()

        # The same non-active profile must still be selected.
        assert _combo_profile_id(controls_page) == other.id


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

        # Rebuild combo so both new profiles appear.
        controls_page._refresh_profile_combo(selected_id=profile_b.id)
        controls_page._refresh_all()

        # --- Switch to B: must show zero controls and zero curves ---
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


class TestProfileActivationUpdatesComboLabel:
    """Activating a profile puts a '* ' prefix on its combo entry."""

    def test_profile_activation_updates_combo_label(
        self, controls_page, profile_service, app_state
    ):
        profiles = profile_service.profiles
        # Pick the second profile (not initially active).
        target = profiles[1]

        _select_profile_by_id(controls_page, target.id)
        controls_page._on_activate()

        # The combo label for the target should now start with "* ".
        text = _combo_profile_text(controls_page)
        assert text.startswith("* "), f"Expected '* ' prefix, got {text!r}"
        assert text == f"* {target.name}"

        # The profile service must agree.
        assert profile_service.active_id == target.id

        # AppState must reflect the new name.
        assert app_state.active_profile_name == target.name

        # The previously-active profile should NOT have the prefix.
        former_active = profiles[0]
        for i in range(controls_page._profile_combo.count()):
            if controls_page._profile_combo.itemData(i) == former_active.id:
                label = controls_page._profile_combo.itemText(i)
                assert not label.startswith("* "), (
                    f"Former active profile still has '* ' prefix: {label!r}"
                )
                break


class TestDeleteProfileSwitchesToActive:
    """Deleting the viewed (non-active) profile switches the combo to the active profile."""

    def test_delete_profile_switches_to_active(self, controls_page, profile_service):
        active = profile_service.active_profile
        assert active is not None

        # Create a sacrificial profile, select it, then delete it.
        victim = profile_service.create_profile("Doomed")
        controls_page._refresh_profile_combo(selected_id=victim.id)
        _select_profile_by_id(controls_page, victim.id)
        assert _combo_profile_id(controls_page) == victim.id

        # Delete it.
        controls_page._on_delete_profile()

        # The victim must no longer appear in the combo.
        assert victim.id not in _combo_ids(controls_page)

        # The combo must now point to the active profile.
        assert _combo_profile_id(controls_page) == active.id

        # _get_current_profile must also return the active profile.
        viewed = controls_page._get_current_profile()
        assert viewed is not None
        assert viewed.id == active.id
