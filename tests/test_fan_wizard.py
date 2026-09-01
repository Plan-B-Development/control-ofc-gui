"""Tests for the Fan Configuration Wizard."""

from __future__ import annotations

import pytest

from control_ofc.api.models import ConnectionState, FanReading, OperationMode, SensorReading
from control_ofc.services.app_state import AppState
from control_ofc.ui.widgets.fan_wizard import (
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
    """stop_fan drives the daemon identify API and surfaces failures (DEC-166)."""

    def test_stop_fan_calls_identify_and_returns_none_on_success(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_wizard_state()

        result = wizard.stop_fan({"id": "openfan:ch00", "source": "openfan"})
        assert result is None
        client.fan_identify.assert_called_once_with("openfan:ch00", "stop")

    def test_stop_fan_hwmon_passes_no_lease(self):
        """hwmon identify is daemon-owned now — addressed by fan id, no lease."""
        from unittest.mock import MagicMock

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_wizard_state()

        result = wizard.stop_fan({"id": "hwmon:nct6798:pwm2", "source": "hwmon"})
        assert result is None
        client.fan_identify.assert_called_once_with("hwmon:nct6798:pwm2", "stop")

    def test_stop_fan_returns_error_on_no_client(self):
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = None
        wizard._state = _make_wizard_state()

        result = wizard.stop_fan({"id": "openfan:ch00", "source": "openfan"})
        assert result is not None
        assert "client" in result.lower()

    def test_stop_fan_returns_error_string_when_daemon_raises(self):
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        client = MagicMock()
        client.fan_identify.side_effect = DaemonError(
            code="not_found", message="unknown fan", status=404
        )
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_wizard_state()

        result = wizard.stop_fan({"id": "openfan:ch09", "source": "openfan"})
        assert result is not None
        assert "unknown fan" in result


class TestRestoreViaIdentify:
    """Restore un-identifies via the daemon; the GUI replays no PWM (DEC-166)."""

    def test_restore_calls_identify_restore(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_wizard_state()

        wizard.restore_fan({"id": "openfan:ch00", "source": "openfan"})
        client.fan_identify.assert_called_once_with("openfan:ch00", "restore")

    def test_restore_noop_without_client(self):
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = None
        wizard._state = _make_wizard_state()
        # Must not raise when there is no client.
        wizard.restore_fan({"id": "openfan:ch00", "source": "openfan"})

    def test_exit_override_restores_all_then_is_idempotent(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_wizard_state()
        wizard._targets = [
            {"id": "openfan:ch00", "source": "openfan"},
            {"id": "openfan:ch01", "source": "openfan"},
        ]
        wizard._identify_active = True

        wizard._exit_override()
        assert wizard._identify_active is False
        assert client.fan_identify.call_count == 2

        # Second call is a no-op (identify no longer active).
        client.fan_identify.reset_mock()
        wizard._exit_override()
        client.fan_identify.assert_not_called()


class TestIdentifyFanPageLifecycle:
    """Real-construction coverage for the per-fan test lifecycle (audit 2026-07-15
    Phase 5): start / tick / end / abort / cleanup, including the thermal abort
    mid-test — the safety-critical path."""

    @staticmethod
    def _wizard_and_page(qtbot, state, client):
        wizard = FanConfigWizard(state=state, client=client)
        qtbot.addWidget(wizard)
        wizard._selected_indices = [0]
        wizard._current_test_idx = 0
        page = wizard._test_page
        page.initializePage()
        return wizard, page

    @staticmethod
    def _hot_cpu():
        return [SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=120.0, age_ms=50)]

    def test_start_test_stops_fan_and_runs_timer(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page._start_test()
        assert page._testing is True
        assert page._timer.isActive()
        client.fan_identify.assert_called_once_with("openfan:ch00", "stop")
        page._abort_test()  # stop the running timer for a clean teardown

    def test_start_test_refuses_when_cpu_hot(self, qtbot):
        from unittest.mock import MagicMock

        state = _make_wizard_state()
        state.set_sensors(self._hot_cpu())
        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, state, client)
        page._start_test()
        assert page._testing is False
        client.fan_identify.assert_not_called()
        assert "abort" in page._status_msg.text().lower()

    def test_tick_thermal_spike_aborts_and_restores(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page._start_test()  # cool → fan stopped, timer running
        assert page._testing
        client.fan_identify.reset_mock()

        wizard_state.set_sensors(self._hot_cpu())  # CPU spikes mid-test
        page._tick()

        assert page._testing is False
        assert not page._timer.isActive()
        client.fan_identify.assert_called_once_with("openfan:ch00", "restore")
        assert "abort" in page._status_msg.text().lower()

    def test_tick_countdown_completes_and_restores(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page._start_test()
        client.fan_identify.reset_mock()
        page._seconds_remaining = 1  # the next tick ends the countdown
        page._tick()
        assert page._testing is False
        client.fan_identify.assert_called_once_with("openfan:ch00", "restore")

    def test_abort_test_restores_fan(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page._start_test()
        client.fan_identify.reset_mock()
        page._abort_test()
        assert page._testing is False
        assert not page._timer.isActive()
        client.fan_identify.assert_called_once_with("openfan:ch00", "restore")

    def test_cleanup_page_aborts_active_test(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page._start_test()
        client.fan_identify.reset_mock()
        page.cleanupPage()  # user pressed Back mid-test
        assert page._testing is False
        client.fan_identify.assert_called_once_with("openfan:ch00", "restore")

    def test_cleanup_page_noop_when_not_testing(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        client = MagicMock()
        _wizard, page = self._wizard_and_page(qtbot, wizard_state, client)
        page.cleanupPage()  # not testing → nothing to restore
        client.fan_identify.assert_not_called()

    def test_validate_page_saves_current_label(self, qtbot, wizard_state):
        from unittest.mock import MagicMock

        wizard, page = self._wizard_and_page(qtbot, wizard_state, MagicMock())
        page._label_combo.setCurrentText("CPU Cooler")
        assert page.validatePage() is True
        assert wizard._labels["openfan:ch00"] == "CPU Cooler"


class TestPumpSafeIdentifyCopy:
    """DEC-311 / AIO-MB Phase 1: the wizard must not tell a user their pump is
    about to stop.

    The daemon decides stop-vs-perturb from the header role; these tests pin
    that the *copy* follows that decision, and — critically — that it does NOT
    follow it against a daemon too old to honour it.
    """

    @staticmethod
    def _state_with_pump(*, header_roles: bool, role: str = "pump"):
        from control_ofc.api.models import (
            Capabilities,
            ControlCapability,
            HwmonHeader,
        )

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        state.set_fans(
            [
                FanReading(id="hwmon:it8696:isa:pwm5:pwm5", source="hwmon", rpm=2400, age_ms=50),
                FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50),
            ]
        )
        state.set_sensors(
            [SensorReading(id="cpu", label="Tctl", kind="CpuTemp", value_c=45.0, age_ms=50)]
        )
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it8696:isa:pwm5:pwm5",
                    label="pwm5",
                    chip_name="it8696",
                    pwm_index=5,
                    role=role,
                    role_source="user_assigned",
                )
            ]
        )
        state.set_capabilities(
            Capabilities(
                control=ControlCapability(
                    autonomous_control=True,
                    fan_identify=True,
                    header_roles=header_roles,
                )
            )
        )
        return state

    def test_pump_header_is_recognised_as_a_pump_target(self, qtbot):
        state = self._state_with_pump(header_roles=True)
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target("hwmon:it8696:isa:pwm5:pwm5") is True
        assert wiz.identify_verb("hwmon:it8696:isa:pwm5:pwm5") == "change speed"

    def test_non_pump_fans_still_stop(self, qtbot):
        state = self._state_with_pump(header_roles=True)
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        # An OpenFan channel has no header at all, so it can never be a pump.
        assert wiz.is_pump_target("openfan:ch00") is False
        assert wiz.identify_verb("openfan:ch00") == "stop"

    def test_a_chassis_role_is_not_a_pump(self, qtbot):
        state = self._state_with_pump(header_roles=True, role="chassis_fan")
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target("hwmon:it8696:isa:pwm5:pwm5") is False

    def test_an_unrecognised_role_token_is_not_treated_as_a_pump(self, qtbot):
        # Forward-compat: a role this GUI has never heard of must not silently
        # acquire pump semantics. Render it, do not act on it (the 273-i rule).
        state = self._state_with_pump(header_roles=True, role="impeller")
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target("hwmon:it8696:isa:pwm5:pwm5") is False

    def test_without_the_capability_the_copy_stays_honest(self, qtbot):
        """The load-bearing case.

        A pre-2.28.0 daemon drives every identified fan to 0, pumps included.
        Even though the header says `role="pump"`, promising "the pump will
        briefly change speed" would be a LIE about what that daemon does — so
        the wizard must keep the "stop" wording.
        """
        state = self._state_with_pump(header_roles=False)
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target("hwmon:it8696:isa:pwm5:pwm5") is False
        assert wiz.identify_verb("hwmon:it8696:isa:pwm5:pwm5") == "stop"

    def test_missing_capabilities_object_does_not_crash(self, qtbot):
        state = self._state_with_pump(header_roles=True)
        state.capabilities = None
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target("hwmon:it8696:isa:pwm5:pwm5") is False

    def test_intro_page_warns_that_a_pump_is_never_stopped(self, qtbot):
        # The intro is shown before any per-fan role is known, so it describes
        # both behaviours rather than promising a stop.
        state = self._state_with_pump(header_roles=True)
        page = IntroPage(state)
        qtbot.addWidget(page)
        from PySide6.QtWidgets import QLabel

        text = " ".join(w.text() for w in page.findChildren(QLabel))
        assert "never stopped" in text.lower(), text
        assert "coolant keeps flowing" in text.lower(), text

    def test_test_page_prompt_names_the_pump_behaviour(self, qtbot):
        state = self._state_with_pump(header_roles=True)
        wiz = FanConfigWizard(state)
        qtbot.addWidget(wiz)
        # Select the pump target and initialise the identify page.
        pump_idx = next(
            i for i, t in enumerate(wiz._targets) if t["id"] == "hwmon:it8696:isa:pwm5:pwm5"
        )
        wiz._selected_indices = [pump_idx]
        wiz._current_test_idx = 0
        page = wiz._test_page
        page.initializePage()
        assert "change speed" in page._status_msg.text().lower(), page._status_msg.text()
