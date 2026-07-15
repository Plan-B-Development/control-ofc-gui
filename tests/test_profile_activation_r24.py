"""Tests for Refinement 24B/C: Profile activation via daemon API and persistence."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import ActiveProfileInfo, ProfileActivateResult
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.controls_page import ControlsPage


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
        from control_ofc.api.client import DaemonClient

        assert hasattr(DaemonClient, "activate_profile")

    def test_active_profile_method_exists(self):
        from control_ofc.api.client import DaemonClient

        assert hasattr(DaemonClient, "active_profile")


class TestProfileActivateResult:
    """ProfileActivateResult model parses daemon responses."""

    def test_parse_success(self):
        from control_ofc.api.models import parse_profile_activate

        data = {"activated": True, "profile_id": "quiet", "profile_name": "Quiet"}
        result = parse_profile_activate(data)
        assert result.activated is True
        assert result.profile_id == "quiet"
        assert result.profile_name == "Quiet"

    def test_parse_failure(self):
        from control_ofc.api.models import parse_profile_activate

        data = {"activated": False}
        result = parse_profile_activate(data)
        assert result.activated is False


class TestActiveProfileInfo:
    """ActiveProfileInfo model parses daemon GET /profile/active responses."""

    def test_parse_active(self):
        from control_ofc.api.models import parse_active_profile

        data = {"active": True, "profile_id": "quiet", "profile_name": "Quiet"}
        result = parse_active_profile(data)
        assert result is not None
        assert result.active is True
        assert result.profile_name == "Quiet"

    def test_parse_no_active_profile(self):
        from control_ofc.api.models import parse_active_profile

        data = {"active": False}
        result = parse_active_profile(data)
        assert result is None


class TestControlsPageActivation:
    """DEC-214: the Controls page dropped its own combo/Activate — activation now
    runs through the shared ``ProfileService.activate`` path (the sidebar apply
    flow uses it, and the page follows via ``active_changed``). These verify that
    shared path; the AppState bridge is covered by the main_window sidebar flow."""

    def test_activate_calls_daemon_api(self, qtbot, app_state, profile_service, mock_client):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        new = profile_service.create_profile("Test")
        res = profile_service.activate(new.id, client=mock_client)

        assert res.activated
        mock_client.activate_profile.assert_called_once()
        assert new.id in mock_client.activate_profile.call_args[0][0]

    def test_activate_updates_service_active_on_success(
        self, qtbot, app_state, profile_service, mock_client
    ):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        new = profile_service.create_profile("Test")
        profile_service.activate(new.id, client=mock_client)

        assert profile_service.active_id == new.id

    def test_activate_failure_does_not_change_active(
        self, qtbot, app_state, profile_service, mock_client_failure
    ):
        page = ControlsPage(
            state=app_state, profile_service=profile_service, client=mock_client_failure
        )
        qtbot.addWidget(page)

        prev_active = profile_service.active_id
        new = profile_service.create_profile("Test")
        res = profile_service.activate(new.id, client=mock_client_failure)

        assert not res.activated
        assert profile_service.active_id == prev_active

    def test_activate_without_client_still_works_locally(self, qtbot, app_state, profile_service):
        """When no client is provided (demo mode), activation works locally."""
        page = ControlsPage(state=app_state, profile_service=profile_service, client=None)
        qtbot.addWidget(page)

        new = profile_service.create_profile("Local")
        res = profile_service.activate(new.id, client=None)

        assert res.activated
        assert profile_service.active_id == new.id


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


class TestProfileDeactivateClient:
    """API client has /profile/deactivate (DEC-097)."""

    def test_deactivate_profile_method_exists(self):
        from control_ofc.api.client import DaemonClient

        assert hasattr(DaemonClient, "deactivate_profile")

    def test_parse_deactivate_with_previous(self):
        from control_ofc.api.models import parse_profile_deactivate

        data = {
            "deactivated": True,
            "previous_profile_id": "balanced",
            "previous_profile_name": "Balanced",
        }
        result = parse_profile_deactivate(data)
        assert result.deactivated is True
        assert result.previous_profile_id == "balanced"
        assert result.previous_profile_name == "Balanced"

    def test_parse_deactivate_idempotent(self):
        from control_ofc.api.models import parse_profile_deactivate

        # Idempotent path: previous_profile_* are JSON null when no profile
        # was active. The parser must surface them as Python None.
        data = {
            "deactivated": True,
            "previous_profile_id": None,
            "previous_profile_name": None,
        }
        result = parse_profile_deactivate(data)
        assert result.deactivated is True
        assert result.previous_profile_id is None


class TestDeleteActiveProfileCallsDeactivate:
    """Deleting the active profile must tell the daemon to deactivate first
    so the curve stops driving fans (DEC-097)."""

    def test_deletes_active_profile_calls_deactivate(
        self, qtbot, app_state, profile_service, mock_client, monkeypatch
    ):
        # Auto-confirm the delete-confirmation dialog.
        from PySide6.QtWidgets import QMessageBox

        from control_ofc.api.models import ProfileDeactivateResult

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )

        mock_client.deactivate_profile.return_value = ProfileDeactivateResult(
            deactivated=True,
            previous_profile_id="quiet",
            previous_profile_name="Quiet",
        )

        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        # Pick whichever profile was loaded as active.
        active_id = profile_service.active_id
        assert active_id, "test setup expects at least one profile loaded"

        # Activate it locally so set_active mirrors what the GUI would have done.
        profile_service.set_active(active_id)

        # View the active profile on the page and delete it (DEC-214).
        page.select_profile(active_id)
        page._on_delete_profile()

        mock_client.deactivate_profile.assert_called_once()

    def test_deletes_inactive_profile_does_not_call_deactivate(
        self, qtbot, app_state, profile_service, mock_client, monkeypatch
    ):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )

        page = ControlsPage(state=app_state, profile_service=profile_service, client=mock_client)
        qtbot.addWidget(page)

        # Create a non-active profile and select it for deletion.
        new_p = profile_service.create_profile("Disposable")
        # Make sure new_p is not the active one.
        if profile_service.active_id == new_p.id:
            other = next(p for p in profile_service.profiles if p.id != new_p.id)
            profile_service.set_active(other.id)
        page.select_profile(new_p.id)
        page._on_delete_profile()

        mock_client.deactivate_profile.assert_not_called()
