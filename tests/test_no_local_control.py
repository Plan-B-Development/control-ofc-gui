"""Phase 7 safety hardening: the thinned GUI holds NO local control authority.

After the 2.0.0 cutover (DEC-165) the Rust daemon is the sole fan-PWM writer.
The GUI is an editor/viewer/controller-of-intent: it never evaluates curves and
never writes PWM in live mode. These tests are the load-bearing regression
guards that the thinning *stays* thinned — they fail if the control loop, the
lease client, the PWM-write API surface, or sensor-driven local evaluation is
reintroduced.

Demo mode is out of scope here: it intentionally keeps a fenced, interpolate-only
``DemoController`` (covered by ``test_demo_controller.py``). These tests scope to
live mode, which must drive nothing locally.
"""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import MagicMock

import pytest

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import SensorReading
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import ControlsPage


class TestControlServicesDeleted:
    """The GUI-side writer and lease client must no longer exist at all."""

    def test_control_loop_module_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("control_ofc.services.control_loop")

    def test_lease_service_module_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("control_ofc.services.lease_service")

    def test_main_window_constructs_no_loop_or_lease(
        self, qtbot, app_state, profile_service, settings_service
    ):
        win = MainWindow(
            state=app_state,
            profile_service=profile_service,
            settings_service=settings_service,
            demo_mode=False,
        )
        qtbot.addWidget(win)
        assert not hasattr(win, "_control_loop"), "live MainWindow must hold no control loop"
        assert not hasattr(win, "_lease_service"), "live MainWindow must hold no lease service"


class TestDaemonClientHasNoWriteOrLeaseSurface:
    """The daemon owns all PWM writes + the hwmon lease (DEC-165), so the GUI
    client must expose neither. (``reset_gpu_fan`` / ``verify_*`` survive — they
    are daemon-mediated actions, not raw GUI writes.)"""

    @pytest.mark.parametrize(
        "method",
        [
            "set_openfan_pwm",
            "set_hwmon_pwm",
            "set_gpu_fan_speed",
            "hwmon_lease_take",
            "hwmon_lease_release",
            "hwmon_lease_renew",
            "hwmon_lease_status",
        ],
    )
    def test_pwm_and_lease_methods_removed(self, method):
        assert not hasattr(DaemonClient, method), (
            f"DaemonClient must not expose {method!r} after the cutover (DEC-165)"
        )

    def test_verify_hwmon_pwm_takes_no_lease_id(self):
        # The hwmon verify is daemon-performed under the engine's own lease now;
        # the GUI no longer supplies (or holds) a lease_id.
        params = inspect.signature(DaemonClient.verify_hwmon_pwm).parameters
        assert "lease_id" not in params


class TestSensorUpdatesDriveNoControl:
    """Behavioural guard (stronger than an import check): in live mode the GUI
    must issue NO control call merely because sensor telemetry arrived. A
    reintroduced curve evaluator would have to write/override on a sensor tick —
    this proves it does not."""

    @staticmethod
    def _live_page(qtbot, app_state, profile_service, client):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=client)
        qtbot.addWidget(page)
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        ctrl = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
        return page

    def test_rising_temps_trigger_no_daemon_control_calls(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = self._live_page(qtbot, app_state, profile_service, client)
        # Isolate the sensor-driven calls from any one-time setup wiring.
        client.reset_mock()

        # Simulate the 1 Hz poll delivering a rising CPU temperature — exactly
        # the input the deleted control loop would have evaluated into a write.
        for temp in (30.0, 55.0, 70.0, 95.0):
            app_state.set_sensors(
                [SensorReading(id="cpu", kind="CpuTemp", label="Tctl", value_c=temp)]
            )

        # The GUI is a viewer: telemetry must never drive a write/override/eval.
        for control_method in (
            "override_take",
            "override_renew",
            "override_release",
            "fan_identify",
            "verify_hwmon_pwm",
            "verify_gpu_fan",
            "reset_gpu_fan",
            "activate_profile",
            "create_profile",
            "update_profile",
            "delete_profile",
        ):
            assert getattr(client, control_method).call_count == 0, (
                f"a sensor update must not call {control_method!r} — the GUI "
                f"performs no local control (DEC-165)"
            )
        # ...and the page created no override of its own from the telemetry.
        assert page._overrides == {}
