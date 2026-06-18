"""Tests that production services emit DiagEvents at the documented transitions.

DEC-111: every service that takes a ``DiagnosticsService`` reference must
emit at meaningful state transitions only — not on every poll, write, or
renewal tick. These tests pin the exact transitions so a future refactor
that adds per-cycle log_event calls will fail the suite.
"""

from __future__ import annotations

from unittest.mock import patch

from control_ofc.api.models import (
    ActiveProfileInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.polling import PollingService

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
