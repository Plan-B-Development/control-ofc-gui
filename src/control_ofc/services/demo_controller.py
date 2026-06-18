"""Demo-mode control evaluator (DEC-165).

A demo-only mini-evaluator that replaces the deleted GUI control loop's drive
of simulated fans. Each tick it evaluates the active profile's curves with the
pure ``CurveConfig.interpolate()`` tier only and writes synthetic PWM into the
``DemoService``. It never touches hardware and is not used in live mode, where
the daemon is the authoritative fan-control engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from control_ofc.services.profile_service import ControlMode

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState
    from control_ofc.services.demo_service import DemoService
    from control_ofc.services.profile_service import LogicalControl, Profile, ProfileService

DEMO_TICK_MS = 1000


class DemoController(QObject):
    """1 Hz demo evaluator: active-profile curves → simulated fan PWM.

    Uses ONLY the pure ``CurveConfig.interpolate()`` tier (Graph / Stepped /
    Linear / Flat / Trigger; Mix and Sync fall back to their flat output — a
    documented demo limitation, since those need the multi-curve resolver the
    deleted control loop owned). No tuning, hysteresis, lease, or thermal logic.
    The per-card Manual pin API mirrors the old loop's, so the Controls page's
    demo branch drives this unchanged.
    """

    outputs_changed = Signal(dict)  # control_id -> output %

    def __init__(
        self,
        profile_service: ProfileService,
        demo_service: DemoService,
        state: AppState,
        interval_ms: int = DEMO_TICK_MS,
    ) -> None:
        super().__init__()
        self._profile_service = profile_service
        self._demo = demo_service
        self._state = state
        self._manual: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_control_manual(self, control_id: str, pwm_percent: float) -> None:
        """Pin one control to a fixed PWM (transient per-card Manual). Mirrors
        the old control loop's API so the Controls page demo branch is unchanged."""
        self._manual[control_id] = max(0.0, min(100.0, pwm_percent))
        self.tick()

    def clear_control_manual(self, control_id: str) -> None:
        self._manual.pop(control_id, None)
        self.tick()

    def tick(self) -> None:
        profile = self._profile_service.active_profile
        if profile is None:
            return
        temps = {s.id: s.value_c for s in self._state.sensors}
        outputs: dict[str, float] = {}
        for control in profile.controls:
            output = self._evaluate(profile, control, temps)
            if output is None:
                continue
            outputs[control.id] = output
            pwm = round(output)
            for member in control.members:
                self._demo.set_fan_pwm(member.member_id, pwm)
        if outputs:
            self.outputs_changed.emit(outputs)

    def _evaluate(
        self, profile: Profile, control: LogicalControl, temps: dict[str, float]
    ) -> float | None:
        if control.id in self._manual:
            return self._manual[control.id]
        if control.mode == ControlMode.MANUAL:
            return control.manual_output_pct
        curve = profile.get_curve(control.curve_id)
        if curve is None:
            return None
        return curve.interpolate(temps.get(curve.sensor_id, 45.0))
