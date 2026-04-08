"""Tests for diagnostics service — event logging and support bundle export."""

from __future__ import annotations

import json

from onlyfans.api.models import ConnectionState, OperationMode
from onlyfans.services.app_state import AppState
from onlyfans.services.diagnostics_service import DiagnosticsService


def test_log_event():
    svc = DiagnosticsService()
    svc.log_event("info", "test", "Hello")
    assert len(svc.events) == 1
    assert svc.events[0].message == "Hello"
    assert svc.events[0].level == "info"
    assert svc.events[0].source == "test"


def test_log_event_capped():
    svc = DiagnosticsService()
    for i in range(250):
        svc.log_event("info", "test", f"Event {i}")
    assert len(svc.events) == 200  # MAX_EVENTS


def test_clear_events():
    svc = DiagnosticsService()
    svc.log_event("info", "test", "Hello")
    svc.clear_events()
    assert len(svc.events) == 0


def test_event_time_str():
    svc = DiagnosticsService()
    svc.log_event("warning", "lease", "Renewal failed")
    event = svc.events[0]
    # Should have HH:MM:SS format
    assert len(event.time_str.split(":")) == 3


def test_export_support_bundle_minimal(tmp_path):
    svc = DiagnosticsService()
    svc.log_event("info", "test", "Test event")
    path = tmp_path / "bundle.json"
    svc.export_support_bundle(path)

    data = json.loads(path.read_text())
    assert "timestamp" in data
    assert "system" in data
    assert "events" in data
    assert len(data["events"]) == 1
    assert data["events"][0]["message"] == "Test event"


def test_export_support_bundle_with_state(tmp_path, qtbot):
    state = AppState()
    state.connection = ConnectionState.CONNECTED
    state.mode = OperationMode.AUTOMATIC
    state.active_profile_name = "Balanced"

    svc = DiagnosticsService(state)
    path = tmp_path / "bundle.json"
    svc.export_support_bundle(path)

    data = json.loads(path.read_text())
    assert data["state"]["connection"] == "connected"
    assert data["state"]["mode"] == "automatic"
    assert data["state"]["active_profile"] == "Balanced"
    assert "lease" in data
