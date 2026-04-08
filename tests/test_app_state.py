"""Tests for the central AppState."""

from __future__ import annotations

from onlyfans.api.models import (
    ConnectionState,
    HwmonHeader,
    OperationMode,
    SensorReading,
)
from onlyfans.services.app_state import AppState


def test_set_connection_emits_signal(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.connection_changed, timeout=1000) as blocker:
        state.set_connection(ConnectionState.CONNECTED)
    assert blocker.args == [ConnectionState.CONNECTED]
    assert state.connection == ConnectionState.CONNECTED


def test_set_connection_no_duplicate_signal(qtbot):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    # Second call with same value should not emit
    signals = []
    state.connection_changed.connect(lambda s: signals.append(s))
    state.set_connection(ConnectionState.CONNECTED)
    assert len(signals) == 0


def test_set_mode_emits_signal(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.mode_changed, timeout=1000):
        state.set_mode(OperationMode.DEMO)
    assert state.mode == OperationMode.DEMO


def test_fan_display_name_alias():
    state = AppState()
    state.fan_aliases = {"openfan:ch00": "Front Intake 1"}
    assert state.fan_display_name("openfan:ch00") == "Front Intake 1"


def test_fan_display_name_hwmon_label():
    state = AppState()
    state.set_hwmon_headers([HwmonHeader(id="hwmon:test:pwm1", label="CPU Fan")])
    assert state.fan_display_name("hwmon:test:pwm1") == "CPU Fan"


def test_fan_display_name_fallback_to_id():
    state = AppState()
    assert state.fan_display_name("openfan:ch05") == "openfan:ch05"


def test_warning_count_updates(qtbot):
    state = AppState()
    signals = []
    state.warning_count_changed.connect(lambda c: signals.append(c))
    state.set_sensors(
        [
            SensorReading(id="fresh", age_ms=500),
            SensorReading(id="stale", age_ms=5000),
        ]
    )
    assert state.warning_count == 1
    assert signals == [1]


def test_set_active_profile(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.active_profile_changed, timeout=1000):
        state.set_active_profile("Balanced")
    assert state.active_profile_name == "Balanced"
