"""Tests for the Fan Configuration Wizard."""

from __future__ import annotations

import pytest

from onlyfans.api.models import ConnectionState, FanReading, OperationMode, SensorReading
from onlyfans.services.app_state import AppState
from onlyfans.ui.widgets.fan_wizard import (
    DiscoveryPage,
    FanConfigWizard,
    IdentifyFanPage,
    IntroPage,
)


def _make_wizard_state():
    """Standalone helper for tests that don't use the fixture."""
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_fans(
        [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50),
            FanReading(id="openfan:ch01", source="openfan", rpm=1100, age_ms=50),
            FanReading(id="openfan:ch02", source="openfan", rpm=800, age_ms=50),
        ]
    )
    state.set_sensors(
        [
            SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=45.0, age_ms=50),
        ]
    )
    return state


@pytest.fixture()
def wizard_state():
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_fans(
        [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50),
            FanReading(id="openfan:ch01", source="openfan", rpm=1100, age_ms=50),
            FanReading(id="openfan:ch02", source="openfan", rpm=800, age_ms=50),
        ]
    )
    state.set_sensors(
        [
            SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=45.0, age_ms=50),
        ]
    )
    return state


class TestWizardCreation:
    def test_wizard_creates_with_targets(self, qtbot, wizard_state):
        wizard = FanConfigWizard(wizard_state)
        qtbot.addWidget(wizard)
        assert len(wizard._targets) == 3
        assert wizard._targets[0]["id"] == "openfan:ch00"

    def test_wizard_has_all_pages(self, qtbot, wizard_state):
        wizard = FanConfigWizard(wizard_state)
        qtbot.addWidget(wizard)
        assert wizard.page(0) is not None  # intro
        assert wizard.page(1) is not None  # discovery
        assert wizard.page(2) is not None  # test (single, reused for all fans)
        assert wizard.page(3) is not None  # review


class TestIntroPage:
    def test_preflight_passes_when_connected(self, qtbot, wizard_state):
        page = IntroPage(wizard_state)
        qtbot.addWidget(page)
        page.initializePage()
        assert page.isComplete()

    def test_preflight_fails_no_connection(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.DISCONNECTED)
        page = IntroPage(state)
        qtbot.addWidget(page)
        page.initializePage()
        assert not page.isComplete()

    def test_preflight_fails_no_fans(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        # No fans set
        page = IntroPage(state)
        qtbot.addWidget(page)
        page.initializePage()
        assert not page.isComplete()

    def test_preflight_fails_high_temp(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_fans([FanReading(id="f1", source="openfan", rpm=1000, age_ms=50)])
        state.set_sensors(
            [
                SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=90.0, age_ms=50),
            ]
        )
        page = IntroPage(state)
        qtbot.addWidget(page)
        page.initializePage()
        # Still complete (temp check is warning, not block) — but status shows warning
        assert (
            "too high" in page._status_label.text().lower() or "90.0" in page._status_label.text()
        )


class TestDiscoveryPage:
    def test_all_selected_by_default(self, qtbot, wizard_state):
        targets = [
            {
                "id": "f1",
                "source": "openfan",
                "rpm": 1000,
                "has_tach": True,
                "existing_label": "f1",
            },
            {"id": "f2", "source": "openfan", "rpm": 900, "has_tach": True, "existing_label": "f2"},
        ]
        page = DiscoveryPage(targets, wizard_state)
        qtbot.addWidget(page)
        assert page.selected_indices() == [0, 1]

    def test_deselect_one(self, qtbot, wizard_state):
        targets = [
            {
                "id": "f1",
                "source": "openfan",
                "rpm": 1000,
                "has_tach": True,
                "existing_label": "f1",
            },
            {"id": "f2", "source": "openfan", "rpm": 900, "has_tach": True, "existing_label": "f2"},
        ]
        page = DiscoveryPage(targets, wizard_state)
        qtbot.addWidget(page)
        page._checkboxes[0].setChecked(False)
        assert page.selected_indices() == [1]


class TestLabelPresets:
    def test_label_presets_populated(self, qtbot, wizard_state):
        wizard = FanConfigWizard(wizard_state)
        qtbot.addWidget(wizard)
        # Create a test page to check presets
        test_page = IdentifyFanPage(wizard)
        assert test_page._label_combo.count() > 5  # presets loaded


class TestThermalGuard:
    def test_thermal_safe_below_threshold(self, qtbot, wizard_state):
        wizard = FanConfigWizard(wizard_state)
        qtbot.addWidget(wizard)
        assert wizard.check_thermal_safe()

    def test_thermal_unsafe_above_threshold(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_sensors(
            [
                SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=90.0, age_ms=50),
            ]
        )
        state.set_fans([FanReading(id="f1", source="openfan", rpm=1000, age_ms=50)])
        wizard = FanConfigWizard(state)
        qtbot.addWidget(wizard)
        assert not wizard.check_thermal_safe()


class TestChannelParsing:
    def test_parse_openfan_channel(self):
        assert FanConfigWizard._parse_openfan_channel("openfan:ch04") == 4
        assert FanConfigWizard._parse_openfan_channel("openfan:ch00") == 0
        assert FanConfigWizard._parse_openfan_channel("hwmon:xyz") is None
        assert FanConfigWizard._parse_openfan_channel("openfan:chXX") is None


# ---------------------------------------------------------------------------
# R59 — RPM filtering, stop_fan errors, restore policy
# ---------------------------------------------------------------------------


class TestBuildTargetsFiltering:
    """Detected fans page only includes fans with RPM readings (R59)."""

    def test_fans_without_rpm_excluded(self, qtbot):
        state = _make_wizard_state()
        # Add a fan with rpm=None — should be excluded
        state.fans.append(FanReading(id="hwmon:no_tach", source="hwmon", rpm=None, age_ms=100))
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)

        ids = [t["id"] for t in wizard._targets]
        assert "hwmon:no_tach" not in ids

    def test_fans_with_rpm_included(self, qtbot):
        state = _make_wizard_state()
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)

        # Default _make_state has fans with rpm values
        assert len(wizard._targets) > 0
        for t in wizard._targets:
            assert t["rpm"] is not None

    def test_fans_with_zero_rpm_excluded(self, qtbot):
        """Fans with rpm=0 (disconnected headers) should be excluded (R60)."""
        state = _make_wizard_state()
        state.fans.append(FanReading(id="hwmon:empty_slot", source="hwmon", rpm=0, age_ms=100))
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)

        ids = [t["id"] for t in wizard._targets]
        assert "hwmon:empty_slot" not in ids

    def test_amdgpu_hwmon_excluded(self, qtbot):
        """amdgpu hwmon fans (not writable via pwm) should be excluded (R60)."""
        state = _make_wizard_state()
        state.fans.append(
            FanReading(
                id="hwmon:amdgpu:0000:03:00.0:pwm1:pwm1",
                source="hwmon",
                rpm=500,
                age_ms=100,
            )
        )
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)

        ids = [t["id"] for t in wizard._targets]
        assert "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1" not in ids


class TestSinglePageFanCycling:
    """Single IdentifyFanPage page cycles through fans internally (R61)."""

    def test_wizard_constructs_without_recursion(self, qtbot):
        """Wizard construction must not trigger infinite recursion (R61 regression)."""
        state = _make_wizard_state()
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)
        # If we get here, no RecursionError
        assert wizard._test_page is not None

    def test_advance_cycles_through_fans(self, qtbot):
        state = _make_wizard_state()
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)
        wizard._selected_indices = [0, 1, 2]
        wizard._current_test_idx = 0

        assert wizard.current_target()["id"] == "openfan:ch00"
        assert wizard.advance_to_next_fan() is True
        assert wizard.current_target()["id"] == "openfan:ch01"
        assert wizard.advance_to_next_fan() is True
        assert wizard.current_target()["id"] == "openfan:ch02"
        assert wizard.advance_to_next_fan() is False  # all done


class TestStopFanErrorSurfacing:
    """stop_fan returns error string on failure instead of silently logging (R59)."""

    def test_stop_fan_returns_none_on_success(self):
        from unittest.mock import MagicMock

        state = _make_wizard_state()
        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = state
        wizard._lease_service = None

        target = {"id": "openfan:ch00", "source": "openfan"}
        result = wizard.stop_fan(target)
        assert result is None

    def test_stop_fan_returns_error_on_no_client(self):
        state = _make_wizard_state()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = None
        wizard._state = state
        wizard._lease_service = None

        target = {"id": "openfan:ch00", "source": "openfan"}
        result = wizard.stop_fan(target)
        assert result is not None
        assert "client" in result.lower()


class TestRestorePriorState:
    """Wizard restores fans to prior PWM or 30% fallback (R59)."""

    def test_restore_uses_prior_pwm(self):
        from unittest.mock import MagicMock

        state = _make_wizard_state()
        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = state
        wizard._lease_service = None

        target = {"id": "openfan:ch00", "source": "openfan", "prior_pwm": 65}
        wizard.restore_fan(target)
        client.set_openfan_pwm.assert_called_once_with(0, 65)

    def test_restore_fallback_30_when_no_prior(self):
        from unittest.mock import MagicMock

        state = _make_wizard_state()
        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = state
        wizard._lease_service = None

        target = {"id": "openfan:ch00", "source": "openfan"}
        wizard.restore_fan(target)
        client.set_openfan_pwm.assert_called_once_with(0, 30)

    def test_build_targets_captures_prior_pwm(self, qtbot):
        state = _make_wizard_state()
        # Set a known last_commanded_pwm
        state.fans = [
            FanReading(
                id="openfan:ch00",
                source="openfan",
                rpm=800,
                last_commanded_pwm=55,
                age_ms=100,
            ),
        ]
        wizard = FanConfigWizard(state=state, parent=None)
        qtbot.addWidget(wizard)

        assert wizard._targets[0]["prior_pwm"] == 55
