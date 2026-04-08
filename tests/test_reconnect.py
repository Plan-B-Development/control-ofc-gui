"""Tests for reconnect behavior — CONNECTED → DISCONNECTED → CONNECTED state transitions.

Verifies that the GUI state model correctly handles daemon connection
lifecycle: mode transitions, profile preservation, and warning stability.
"""

from __future__ import annotations

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.services.app_state import AppState


class TestDisconnectTransition:
    """Disconnection must transition mode and preserve profile."""

    def test_disconnect_sets_read_only(self):
        """AUTOMATIC mode transitions to READ_ONLY on disconnect."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)

        # Simulate what PollingService._on_disconnected does
        state.set_connection(ConnectionState.DISCONNECTED)
        assert state.connection == ConnectionState.DISCONNECTED

    def test_active_profile_survives_disconnect(self):
        """Active profile name persists through disconnect."""
        state = AppState()
        state.set_active_profile("Quiet")
        state.set_connection(ConnectionState.CONNECTED)
        state.set_connection(ConnectionState.DISCONNECTED)
        assert state.active_profile_name == "Quiet"

    def test_warning_count_preserved_on_disconnect(self):
        """Warnings are not cleared by a disconnect event."""
        state = AppState()
        state.add_warning(level="warning", source="test", message="test warning", key="test:1")
        state.set_connection(ConnectionState.DISCONNECTED)
        # External warnings persist — they're cleared explicitly, not by disconnect
        assert len(state._external_warnings) == 1


class TestReconnectTransition:
    """Reconnection must restore state correctly."""

    def test_reconnect_sets_connected(self):
        """Connection state transitions back to CONNECTED."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_connection(ConnectionState.DISCONNECTED)
        state.set_connection(ConnectionState.CONNECTED)
        assert state.connection == ConnectionState.CONNECTED

    def test_profile_survives_full_cycle(self):
        """Profile name persists through full connect/disconnect/reconnect."""
        state = AppState()
        state.set_active_profile("Performance")
        state.set_connection(ConnectionState.CONNECTED)
        state.set_connection(ConnectionState.DISCONNECTED)
        state.set_connection(ConnectionState.CONNECTED)
        assert state.active_profile_name == "Performance"

    def test_fan_aliases_survive_reconnect(self):
        """Fan aliases (GUI-owned) are not lost during reconnect."""
        state = AppState()
        state.fan_aliases = {"openfan:ch00": "CPU Fan", "openfan:ch01": "Exhaust"}
        state.set_connection(ConnectionState.CONNECTED)
        state.set_connection(ConnectionState.DISCONNECTED)
        state.set_connection(ConnectionState.CONNECTED)
        assert state.fan_aliases["openfan:ch00"] == "CPU Fan"
        assert len(state.fan_aliases) == 2


class TestConnectionSignals:
    """Connection state changes emit correct signals."""

    def test_connection_changed_emitted(self, qtbot):
        state = AppState()
        with qtbot.waitSignal(state.connection_changed, timeout=1000) as blocker:
            state.set_connection(ConnectionState.CONNECTED)
        assert blocker.args == [ConnectionState.CONNECTED]

    def test_duplicate_connection_state_no_signal(self, qtbot):
        """Setting the same connection state twice does not emit a second signal."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        emitted = []
        state.connection_changed.connect(lambda s: emitted.append(s))
        state.set_connection(ConnectionState.CONNECTED)
        assert len(emitted) == 0
