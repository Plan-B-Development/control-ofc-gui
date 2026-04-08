"""Tests for Refinement 26: Controls page card layout hardening.

Covers transition paths — create, refresh, activate, apply, delete — verifying
no card stacking, duplication, or order loss occurs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from onlyfans.api.models import ProfileActivateResult
from onlyfans.services.profile_service import (
    CurveType,
)
from onlyfans.ui.pages.controls_page import ControlsPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _curve_ids(page: ControlsPage) -> list[str]:
    """Return curve card IDs from the flow container in layout order."""
    return page._curves_flow.card_ids()


def _control_ids(page: ControlsPage) -> list[str]:
    """Return control card IDs from the flow container in layout order."""
    return page._controls_flow.card_ids()


# ---------------------------------------------------------------------------
# A. Card count matches model
# ---------------------------------------------------------------------------


class TestCardCountMatchesModel:
    """Card count in the UI must match the profile model at all times."""

    def test_curve_card_count_matches_profile(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        profile = page._get_current_profile()
        assert len(_curve_ids(page)) == len(profile.curves)

    def test_control_card_count_matches_profile(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        profile = page._get_current_profile()
        assert len(_control_ids(page)) == len(profile.controls)


# ---------------------------------------------------------------------------
# B. Append to end
# ---------------------------------------------------------------------------


class TestAppendToEnd:
    """New cards must appear at the end of the sequence."""

    def test_new_curve_appends_after_existing(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        ids_before = _curve_ids(page)
        page._on_add_curve(CurveType.FLAT)
        ids_after = _curve_ids(page)

        # Existing IDs preserved at same positions; new ID at the end
        assert ids_after[: len(ids_before)] == ids_before
        assert len(ids_after) == len(ids_before) + 1

    def test_new_control_appends_after_existing(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        # Ensure at least one curve exists for curve_id assignment
        profile = page._get_current_profile()
        if not profile.curves:
            page._on_add_curve(CurveType.FLAT)

        ids_before = _control_ids(page)
        page._on_new_control(single=True, name="Appended")
        ids_after = _control_ids(page)

        assert ids_after[: len(ids_before)] == ids_before
        assert len(ids_after) == len(ids_before) + 1

    def test_multiple_curves_append_sequentially(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        ids_start = _curve_ids(page)
        page._on_add_curve(CurveType.FLAT)
        page._on_add_curve(CurveType.LINEAR)
        page._on_add_curve(CurveType.GRAPH)
        ids_end = _curve_ids(page)

        assert ids_end[: len(ids_start)] == ids_start
        assert len(ids_end) == len(ids_start) + 3


# ---------------------------------------------------------------------------
# C. Order stability across refresh
# ---------------------------------------------------------------------------


class TestOrderStabilityAcrossRefresh:
    """Card order must survive _refresh_all and related rebuild paths."""

    def test_curve_order_stable_after_refresh(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        ids_before = _curve_ids(page)
        page._refresh_all()
        ids_after = _curve_ids(page)
        assert ids_before == ids_after

    def test_control_order_stable_after_refresh(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        # Create some controls
        page._on_new_control(single=True, name="Role A")
        page._on_new_control(single=True, name="Role B")

        ids_before = _control_ids(page)
        page._refresh_all()
        ids_after = _control_ids(page)
        assert ids_before == ids_after

    def test_curve_order_stable_after_double_refresh(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        ids_before = _curve_ids(page)
        page._refresh_all()
        page._refresh_all()
        ids_after = _curve_ids(page)
        assert ids_before == ids_after


# ---------------------------------------------------------------------------
# D. Profile activate does not stack/duplicate
# ---------------------------------------------------------------------------


class TestProfileActivateStability:
    """Profile activation must not corrupt card layout."""

    def test_activate_preserves_curve_count(self, qtbot, app_state, profile_service):
        mock_client = MagicMock()
        mock_client.activate_profile.return_value = ProfileActivateResult(
            activated=True, profile_id="test", profile_name="Test"
        )
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        count_before = len(_curve_ids(page))
        page._on_activate()
        count_after = len(_curve_ids(page))
        assert count_before == count_after

    def test_activate_preserves_curve_order(self, qtbot, app_state, profile_service):
        mock_client = MagicMock()
        mock_client.activate_profile.return_value = ProfileActivateResult(
            activated=True, profile_id="test", profile_name="Test"
        )
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        ids_before = _curve_ids(page)
        page._on_activate()
        ids_after = _curve_ids(page)
        assert ids_before == ids_after


# ---------------------------------------------------------------------------
# E. No duplicates after repeated operations
# ---------------------------------------------------------------------------


class TestNoDuplicatesAfterRepeatedOps:
    """Rapid add/delete/refresh must not produce duplicate cards."""

    def test_no_duplicate_curves_after_add_delete_cycle(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        # Add 3, delete 1, add 2, refresh
        page._on_add_curve(CurveType.FLAT)
        page._on_add_curve(CurveType.LINEAR)
        page._on_add_curve(CurveType.GRAPH)

        profile = page._get_current_profile()
        if profile.curves:
            page._on_delete_curve(profile.curves[0].id)

        page._on_add_curve(CurveType.FLAT)
        page._on_add_curve(CurveType.LINEAR)

        page._refresh_all()

        ids = _curve_ids(page)
        assert len(ids) == len(set(ids)), "Duplicate curve card IDs found"

        profile = page._get_current_profile()
        assert len(ids) == len(profile.curves)

    def test_no_duplicate_controls_after_add_delete_cycle(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        page._on_new_control(single=True, name="A")
        page._on_new_control(single=True, name="B")
        page._on_new_control(single=True, name="C")

        profile = page._get_current_profile()
        if profile.controls:
            page._on_delete_control(profile.controls[0].id)

        page._on_new_control(single=True, name="D")
        page._refresh_all()

        ids = _control_ids(page)
        assert len(ids) == len(set(ids)), "Duplicate control card IDs found"

        profile = page._get_current_profile()
        assert len(ids) == len(profile.controls)


# ---------------------------------------------------------------------------
# F. Card IDs match profile model
# ---------------------------------------------------------------------------


class TestCardIdsMatchModel:
    """Card IDs in the UI must match the profile model exactly."""

    def test_curve_ids_match_model(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        page._on_add_curve(CurveType.FLAT)
        page._on_add_curve(CurveType.LINEAR)

        profile = page._get_current_profile()
        model_ids = [c.id for c in profile.curves]
        ui_ids = _curve_ids(page)
        assert ui_ids == model_ids

    def test_control_ids_match_model(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        page._on_new_control(single=True, name="X")
        page._on_new_control(single=True, name="Y")

        profile = page._get_current_profile()
        model_ids = [c.id for c in profile.controls]
        ui_ids = _control_ids(page)
        assert ui_ids == model_ids
