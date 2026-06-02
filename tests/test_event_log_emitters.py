"""Tests that production services emit DiagEvents at the documented transitions.

DEC-111: every service that takes a ``DiagnosticsService`` reference must
emit at meaningful state transitions only — not on every poll, write, or
renewal tick. These tests pin the exact transitions so a future refactor
that adds per-cycle log_event calls will fail the suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ActiveProfileInfo,
    LeaseReleasedResult,
    LeaseResult,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import (
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    ControlLoopService,
)
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.polling import PollingService
from control_ofc.services.profile_service import ProfileService

# ---------------------------------------------------------------------------
# PollingService — connect / disconnect / active profile (DEC-111)
# ---------------------------------------------------------------------------


def _make_polling_with_diag(state: AppState, diag: DiagnosticsService) -> PollingService:
    """Build a PollingService stripped down to just the slots we exercise."""
    with patch.object(PollingService, "__init__", lambda self, *a, **kw: None):
        svc = PollingService.__new__(PollingService)
        svc._state = state
        svc._was_connected = None
        svc._diag = diag
    return svc


class TestPollingEmitters:
    def test_first_connect_emits_event(self, qtbot):
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_connected()
        sources = [(e.source, e.message) for e in diag.events]
        assert ("polling", "Daemon connected") in sources

    def test_repeated_connect_does_not_re_emit(self, qtbot):
        """Two consecutive successful polls only log once — DEC-111
        guarantees transitions, not per-cycle noise."""
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_connected()
        svc._on_connected()
        connects = [e for e in diag.events if e.message == "Daemon connected"]
        assert len(connects) == 1

    def test_disconnect_after_connect_emits(self, qtbot):
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_connected()
        svc._on_disconnected()
        levels = [(e.level, e.message) for e in diag.events]
        assert ("warning", "Daemon disconnected") in levels

    def test_disconnect_without_prior_connect_is_silent(self, qtbot):
        """Starting up disconnected should not flood the log on every poll."""
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_disconnected()
        svc._on_disconnected()
        disconnects = [e for e in diag.events if e.message == "Daemon disconnected"]
        assert disconnects == []

    def test_active_profile_emits(self, qtbot):
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_active_profile(ActiveProfileInfo(active=True, profile_name="Quiet", profile_id="q"))
        assert any(e.source == "polling" and "Quiet" in e.message for e in diag.events)

    def test_active_profile_none_is_silent(self, qtbot):
        diag = DiagnosticsService()
        svc = _make_polling_with_diag(AppState(), diag)
        svc._on_active_profile(ActiveProfileInfo(active=False))
        assert diag.events == []


# ---------------------------------------------------------------------------
# LeaseService — acquire / release / lost (DEC-111)
# ---------------------------------------------------------------------------


def _make_lease(diag: DiagnosticsService) -> tuple[LeaseService, MagicMock]:
    """LeaseService in sync mode (no socket_path) with a mocked client."""
    client = MagicMock()
    svc = LeaseService(client, diagnostics=diag)
    return svc, client


class TestLeaseEmitters:
    def test_acquire_success_emits(self, qtbot):
        diag = DiagnosticsService()
        svc, client = _make_lease(diag)
        client.hwmon_lease_take.return_value = LeaseResult(lease_id="lid-1", ttl_seconds=60)

        with qtbot.waitSignal(svc.lease_acquired, timeout=1000):
            svc.acquire()

        msgs = [(e.level, e.message) for e in diag.events]
        assert any("lid-1" in m and lvl == "info" for lvl, m in msgs)

    def test_acquire_failure_emits_error(self, qtbot):
        diag = DiagnosticsService()
        svc, client = _make_lease(diag)
        client.hwmon_lease_take.side_effect = DaemonError(
            status=503, code="hardware_unavailable", message="busy"
        )

        with qtbot.waitSignal(svc.lease_lost, timeout=1000):
            svc.acquire()

        levels = {(e.level, e.source) for e in diag.events}
        assert ("error", "lease") in levels

    def test_release_emits_info(self, qtbot):
        diag = DiagnosticsService()
        svc, client = _make_lease(diag)
        client.hwmon_lease_take.return_value = LeaseResult(lease_id="lid-1", ttl_seconds=60)
        client.hwmon_lease_release.return_value = LeaseReleasedResult(released=True)
        svc.acquire()

        # Drop the acquire event so the assertion below is unambiguous.
        diag.clear_events()
        svc.release()
        msgs = [(e.level, e.message) for e in diag.events]
        assert ("info", "Lease released") in msgs


# ---------------------------------------------------------------------------
# ControlLoopService — start/stop, write-fail threshold, lease lost (DEC-111)
# ---------------------------------------------------------------------------


@pytest.fixture()
def control_loop_with_diag(tmp_path, monkeypatch) -> tuple[ControlLoopService, DiagnosticsService]:
    """ControlLoopService in sync (test) mode wired to a fresh DiagnosticsService."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = AppState()
    profile_service = ProfileService()
    profile_service.load()
    client = MagicMock()
    diag = DiagnosticsService()
    loop = ControlLoopService(
        state=state,
        profile_service=profile_service,
        client=client,
        diagnostics=diag,
    )
    return loop, diag


class TestControlLoopEmitters:
    def test_start_and_stop_emit(self, qtbot, control_loop_with_diag):
        loop, diag = control_loop_with_diag
        loop.start()
        loop.stop()
        msgs = [(e.source, e.message) for e in diag.events]
        assert ("control_loop", "Control loop started") in msgs
        assert ("control_loop", "Control loop stopped") in msgs

    def test_write_fail_threshold_emits_once(self, qtbot, control_loop_with_diag):
        """The write-fail event fires exactly once, on the 3rd consecutive
        failure (the threshold crossing). DEC-111 explicitly avoids a row
        per cycle once the warning is already active."""
        loop, diag = control_loop_with_diag
        target = "openfan:ch00"
        loop._on_write_completed(target, OUTCOME_TIMEOUT)  # count=1
        loop._on_write_completed(target, OUTCOME_TIMEOUT)  # count=2
        loop._on_write_completed(target, OUTCOME_TIMEOUT)  # count=3 → emit
        loop._on_write_completed(target, OUTCOME_TIMEOUT)  # count=4 → silent
        warns = [
            e
            for e in diag.events
            if e.source == "control_loop" and "write" in e.message.lower() and e.level == "warning"
        ]
        assert len(warns) == 1

    def test_recovery_after_threshold_emits_info(self, qtbot, control_loop_with_diag):
        loop, diag = control_loop_with_diag
        target = "openfan:ch00"
        # Push past the threshold first.
        for _ in range(3):
            loop._on_write_completed(target, OUTCOME_TIMEOUT)
        diag.clear_events()
        # Then drain failures back below threshold.
        loop._on_write_completed(target, OUTCOME_OK)
        recovered = [e for e in diag.events if "recovered" in e.message and e.level == "info"]
        assert len(recovered) == 1

    def test_lease_lost_emits_error(self, qtbot, control_loop_with_diag):
        loop, diag = control_loop_with_diag
        loop._on_lease_lost("renewal failed")
        errs = [e for e in diag.events if e.level == "error" and "lease" in e.message.lower()]
        assert len(errs) == 1
