"""Tests for Refinement 24B/C: Profile activation via daemon API and persistence."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from onlyfans.api.errors import DaemonError
from onlyfans.api.models import ActiveProfileInfo, ProfileActivateResult
from onlyfans.services.app_state import AppState
from onlyfans.ui.pages.controls_page import ControlsPage


@pytest.fixture()
def mock_client():
    """Create a mock DaemonClient."""
    client = MagicMock()
    client.activate_profile.return_value = ProfileActivateResult(
        activated=True, profile_id="quiet", profile_name="Quiet"
    )
    client.active_profile.return_value = ActiveProfileInfo(
        active=True, profile_id="quiet", profile_name="Quiet"
    )
    return client


@pytest.fixture()
def mock_client_failure():
    """Create a mock DaemonClient that rejects activation."""
    client = MagicMock()
    client.activate_profile.side_effect = DaemonError(
        code="validation_error",
        message="profile not found",
        retryable=False,
        source="validation",
        status=404,
    )
    return client


class TestDaemonClientMethods:
    """API client has profile activation methods."""

    def test_activate_profile_method_exists(self):
        from onlyfans.api.client import DaemonClient

        assert hasattr(DaemonClient, "activate_profile")

    def test_active_profile_method_exists(self):
        from onlyfans.api.client import DaemonClient

        assert hasattr(DaemonClient, "active_profile")


class TestProfileActivateResult:
    """ProfileActivateResult model parses daemon responses."""

    def test_parse_success(self):
        from onlyfans.api.models import parse_profile_activate

        data = {"activated": True, "profile_id": "quiet", "profile_name": "Quiet"}
        result = parse_profile_activate(data)
        assert result.activated is True
        assert result.profile_id == "quiet"
        assert result.profile_name == "Quiet"

    def test_parse_failure(self):
        from onlyfans.api.models import parse_profile_activate

        data = {"activated": False}
        result = parse_profile_activate(data)
        assert result.activated is False


class TestActiveProfileInfo:
    """ActiveProfileInfo model parses daemon GET /profile/active responses."""

    def test_parse_active(self):
        from onlyfans.api.models import parse_active_profile

        data = {"active": True, "profile_id": "quiet", "profile_name": "Quiet"}
        result = parse_active_profile(data)
        assert result is not None
        assert result.active is True
        assert result.profile_name == "Quiet"

    def test_parse_no_active_profile(self):
        from onlyfans.api.models import parse_active_profile

        data = {"active": False}
        result = parse_active_profile(data)
        assert result is None


class TestControlsPageActivation:
    """Controls page activation calls daemon API."""

    def test_activate_calls_daemon_api(self, qtbot, app_state, profile_service, mock_client):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        # Create and select a profile
        profile_service.create_profile("Test")
        page._refresh_profile_combo()
        page._profile_combo.setCurrentIndex(0)
        profile_id = page._profile_combo.currentData()

        # Activate
        page._on_activate()

        # Verify daemon API was called with the profile path
        mock_client.activate_profile.assert_called_once()
        call_arg = mock_client.activate_profile.call_args[0][0]
        assert profile_id in call_arg  # path contains the profile ID

    def test_activate_updates_state_on_success(
        self, qtbot, app_state, profile_service, mock_client
    ):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        # Activate whichever profile is selected (default from load())
        selected_name = page._profile_combo.currentText().lstrip("* ")

        page._on_activate()

        # AppState should reflect the selected profile's name
        assert app_state.active_profile_name == selected_name

    def test_activate_failure_does_not_update_state(
        self, qtbot, app_state, profile_service, mock_client_failure
    ):
        page = ControlsPage(
            state=app_state, profile_service=profile_service, client=mock_client_failure
        )
        qtbot.addWidget(page)

        old_name = app_state.active_profile_name

        page._on_activate()

        # AppState should NOT be updated on failure
        assert app_state.active_profile_name == old_name

    def test_activate_without_client_still_works_locally(self, qtbot, app_state, profile_service):
        """When no client is provided (demo mode), activation works locally."""
        page = ControlsPage(state=app_state, profile_service=profile_service, client=None)
        qtbot.addWidget(page)

        selected_name = page._profile_combo.currentText().lstrip("* ")

        page._on_activate()

        assert app_state.active_profile_name == selected_name


class TestDaemonProfileQuery:
    """GUI queries daemon active profile on connect."""

    def test_active_profile_updates_state(self):
        """Verify the polling service handler updates AppState correctly."""
        state = AppState()
        info = ActiveProfileInfo(active=True, profile_id="quiet", profile_name="Quiet")

        # Simulate what _on_active_profile does
        if info and info.active:
            state.set_active_profile(info.profile_name)

        assert state.active_profile_name == "Quiet"

    def test_no_active_profile_leaves_state_empty(self):
        state = AppState()

        # Simulate no active profile
        info = None
        if info and info.active:
            state.set_active_profile(info.profile_name)

        assert state.active_profile_name == ""
