"""Control loop — curve evaluation, hysteresis, write suppression.

The GUI owns fan curve logic. Each cycle:
1. Read latest sensor data from AppState
2. Evaluate active profile's logical controls
3. Apply 2 deg C hysteresis deadband
4. Suppress writes below 1% PWM change threshold
5. Post writes to a background worker thread (non-blocking)
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ConnectionState,
    FanReading,
    Freshness,
    OperationMode,
    SensorReading,
)
from control_ofc.constants import (
    CONTROL_LOOP_INTERVAL_MS,
    HYSTERESIS_DEADBAND_C,
    PWM_WRITE_THRESHOLD_PCT,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    LogicalControl,
    ProfileService,
)

if TYPE_CHECKING:
    from control_ofc.services.demo_service import DemoService
    from control_ofc.services.profile_service import Profile

log = logging.getLogger(__name__)

# Matches "openfan:chNN" and extracts the channel number.
_OPENFAN_CH_RE = re.compile(r"^openfan:ch(\d+)$")


class _WriteWorker(QObject):
    """Runs in a QThread — executes blocking HTTP write calls off the main thread."""

    write_completed = Signal(str, bool)  # target_id, success

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot(str, int, str)
    def do_write(self, target_id: str, pwm_int: int, lease_id: str) -> None:
        """Execute a single PWM write (called on worker thread via signal)."""
        try:
            client = self._ensure_client()
            m = _OPENFAN_CH_RE.match(target_id)
            if m:
                channel = int(m.group(1))
                client.set_openfan_pwm(channel, pwm_int)
            elif target_id.startswith("amd_gpu:"):
                gpu_id = target_id.removeprefix("amd_gpu:")
                client.set_gpu_fan_speed(gpu_id, pwm_int)
            elif target_id.startswith("hwmon:"):
                if lease_id:
                    client.set_hwmon_pwm(target_id, pwm_int, lease_id)
                else:
                    self.write_completed.emit(target_id, False)
                    return
            else:
                self.write_completed.emit(target_id, False)
                return
            self.write_completed.emit(target_id, True)
        except DaemonError as e:
            log.warning("Write failed for %s: %s", target_id, e.message)
            self.write_completed.emit(target_id, False)
        except (ConnectionError, OSError) as e:
            log.warning("Write worker connection error for %s: %s", target_id, e)
            with contextlib.suppress(Exception):
                if self._client:
                    self._client.close()
            self._client = None
            self.write_completed.emit(target_id, False)

    def shutdown(self) -> None:
        if self._client:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


@dataclass
class TargetState:
    """Per-target tracking for hysteresis and write suppression."""

    last_transition_temp: float | None = None
    last_commanded_pwm: float | None = None
    last_output: float | None = None  # last tuned output for step rate limiting


@dataclass
class ControlLoopStatus:
    """Snapshot of control loop state for UI display."""

    running: bool = False
    targets_active: int = 0
    targets_skipped: int = 0
    last_error: str = ""
    warnings: list[str] = field(default_factory=list)
    control_outputs: dict[str, float] = field(default_factory=dict)  # control_id -> output %


class ControlLoopService(QObject):
    """Runs the fan control loop on a QTimer."""

    status_changed = Signal(ControlLoopStatus)
    write_performed = Signal(str, float)  # target_id, pwm_percent
    _request_write = Signal(str, int, str)  # target_id, pwm_int, lease_id

    def __init__(
        self,
        state: AppState,
        profile_service: ProfileService,
        client: DaemonClient | None = None,
        lease_service: LeaseService | None = None,
        demo_service: DemoService | None = None,
        parent: QObject | None = None,
        socket_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._profile_service = profile_service
        self._client = client
        self._lease = lease_service
        self._demo = demo_service

        self._target_states: dict[str, TargetState] = {}
        self._write_failure_counts: dict[str, int] = {}
        self._manual_override = False
        self._running = False

        # Background write worker (P0-G1: avoid blocking main thread).
        # Only created when socket_path is explicitly provided (production).
        # When None (tests), falls back to synchronous writes via self._client.
        self._write_thread: QThread | None = None
        self._write_worker: _WriteWorker | None = None
        if client is not None and socket_path is not None:
            self._write_thread = QThread()
            self._write_worker = _WriteWorker(socket_path)
            self._write_worker.moveToThread(self._write_thread)
            self._request_write.connect(self._write_worker.do_write)
            self._write_worker.write_completed.connect(self._on_write_completed)
            self._write_thread.start()

        self._timer = QTimer(self)
        self._timer.setInterval(CONTROL_LOOP_INTERVAL_MS)
        self._timer.timeout.connect(self._cycle)

        # Reset hysteresis when profile changes
        self._state.active_profile_changed.connect(self._on_profile_changed)

        # Trigger immediate evaluation when new sensor data arrives (P1-G3).
        # Halves worst-case thermal response time from ~4s to ~2s.
        self._state.sensors_updated.connect(self._on_sensors_updated)

        # Respond to lease loss (P0-G2: safe fallback)
        if lease_service is not None:
            lease_service.lease_lost.connect(self._on_lease_lost)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def manual_override(self) -> bool:
        return self._manual_override

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._timer.start()
            log.info("Control loop started (interval=%dms)", CONTROL_LOOP_INTERVAL_MS)

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._timer.stop()
            log.info("Control loop stopped")

    def set_manual_override(self, active: bool) -> None:
        """Enter or exit manual override mode."""
        if active == self._manual_override:
            return
        self._manual_override = active
        if active:
            self._state.set_mode(OperationMode.MANUAL_OVERRIDE)
            log.info("Manual override enabled")
        else:
            self._reset_hysteresis()
            self._state.set_mode(OperationMode.AUTOMATIC)
            log.info("Manual override disabled, returning to automatic")

    def write_manual_pwm(self, target_id: str, pwm_percent: float) -> bool:
        """Write a manual PWM value (bypasses curve evaluation)."""
        if not self._manual_override:
            return False
        return self._write_target(target_id, pwm_percent)

    def reevaluate_now(self) -> None:
        """Force an immediate control loop re-evaluation.

        Resets hysteresis, exits manual override, and runs one cycle against
        the current active profile. Used after ``/profile/activate`` so the
        GUI writes the new profile's curve outputs without waiting for the
        next timer tick, and without relying on ``active_profile_changed``
        (which is suppressed when the profile name is unchanged).
        """
        self._reset_hysteresis()
        if self._manual_override:
            self._manual_override = False
            log.info("Manual override cleared by profile re-evaluate")
        if self._running:
            self._cycle()

    def shutdown(self) -> None:
        self.stop()
        if self._write_worker:
            self._write_worker.shutdown()
        if self._write_thread:
            self._write_thread.quit()
            if not self._write_thread.wait(2000):
                log.warning("Control loop write thread did not stop within 2s, terminating")
                self._write_thread.terminate()
                self._write_thread.wait(1000)

    def _on_sensors_updated(self, _sensors: list) -> None:
        """Evaluate immediately when fresh sensor data arrives (P1-G3)."""
        if self._running and not self._manual_override:
            self._cycle()

    def _on_profile_changed(self, _name: str) -> None:
        self._reset_hysteresis()
        if self._manual_override:
            self._manual_override = False
            log.info("Profile changed -- exiting manual override")
        # Evaluate the new profile immediately instead of waiting for next timer tick
        if self._running:
            self._cycle()

    def _reset_hysteresis(self) -> None:
        self._target_states.clear()

    def _cycle(self) -> None:
        """One control loop iteration."""
        status = ControlLoopStatus(running=True)

        if not self._prerequisites_met():
            status.warnings.append("Prerequisites not met")
            self.status_changed.emit(status)
            return

        if self._manual_override:
            return

        profile = self._profile_service.active_profile
        if not profile:
            return

        sensors = {s.id: s for s in self._state.sensors}
        fans = {f.id: f for f in self._state.fans}

        for control in profile.controls:
            self._evaluate_control(control, profile, sensors, fans, status)

        self.status_changed.emit(status)

    def _prerequisites_met(self) -> bool:
        """Check if conditions allow automatic control."""
        if self._state.mode == OperationMode.DEMO:
            return True
        return (
            self._state.connection == ConnectionState.CONNECTED
            and self._state.mode in (OperationMode.AUTOMATIC, OperationMode.MANUAL_OVERRIDE)
            and self._profile_service.active_profile is not None
        )

    def _evaluate_control(
        self,
        control: LogicalControl,
        profile: Profile,
        sensors: dict[str, SensorReading],
        fans: dict[str, FanReading],
        status: ControlLoopStatus,
    ) -> None:
        """Evaluate one logical control and write to its members."""
        # Determine raw desired PWM
        if control.mode == ControlMode.MANUAL:
            desired_pwm = control.manual_output_pct
        else:
            curve = profile.get_curve(control.curve_id)
            if curve is None:
                status.warnings.append(f"Curve {control.curve_id!r} not found for {control.name}")
                status.targets_skipped += 1
                return

            desired_pwm = self._evaluate_curve_with_hysteresis(
                control.id, curve, sensors, fans, status
            )
            if desired_pwm is None:
                status.targets_skipped += 1
                return

        # Apply tuning pipeline
        desired_pwm = self._apply_tuning(control, desired_pwm)
        status.control_outputs[control.id] = desired_pwm

        # Write to each member (skip if no members assigned)
        if not control.members:
            return

        for member in control.members:
            target_id = member.target_id
            if self._should_write(target_id, desired_pwm):
                if self._write_target(target_id, desired_pwm):
                    status.targets_active += 1
                    self.write_performed.emit(target_id, desired_pwm)
                    # Track success (sync path — async path uses _on_write_completed)
                    if self._write_worker is None:
                        self._on_write_completed(target_id, True)
                else:
                    status.targets_skipped += 1
                    # Track failure (sync path)
                    if self._write_worker is None:
                        self._on_write_completed(target_id, False)
            else:
                status.targets_active += 1  # still active, just suppressed

    def _apply_tuning(self, control: LogicalControl, raw_output: float) -> float:
        """Apply per-control tuning: offset, minimum, step rate, start/stop."""
        output = raw_output + control.offset_pct
        output = max(control.minimum_pct, output)

        # Step rate limiting
        ts = self._target_states.get(control.id)
        last_output = ts.last_output if ts else None
        if last_output is not None:
            max_up = last_output + control.step_up_pct
            max_down = last_output - control.step_down_pct
            output = max(max_down, min(max_up, output))

        # Start/stop thresholds
        if control.stop_pct > 0 and output < control.stop_pct:
            output = 0.0
        if output > 0 and last_output is not None and last_output == 0 and control.start_pct > 0:
            output = max(output, control.start_pct)

        output = max(0.0, min(100.0, output))

        # Track for next cycle
        if control.id not in self._target_states:
            self._target_states[control.id] = TargetState()
        self._target_states[control.id].last_output = output

        return output

    def _evaluate_curve_with_hysteresis(
        self,
        control_id: str,
        curve: CurveConfig,
        sensors: dict[str, SensorReading],
        fans: dict[str, FanReading],
        status: ControlLoopStatus,
    ) -> float | None:
        """Evaluate curve with hysteresis deadband. Returns desired PWM or None."""
        sensor_id = curve.sensor_id
        if not sensor_id:
            if sensors:
                sensor_id = next(iter(sensors))
            else:
                status.warnings.append(f"No sensor for control {control_id}")
                return None

        sensor = sensors.get(sensor_id)
        if sensor is None:
            status.warnings.append(f"Sensor {sensor_id} not found")
            return None

        if sensor.freshness == Freshness.INVALID:
            status.warnings.append(f"Sensor {sensor_id} invalid (age={sensor.age_ms}ms)")
            return None

        if sensor.freshness == Freshness.STALE:
            status.warnings.append(f"Sensor {sensor_id} stale (age={sensor.age_ms}ms)")

        current_temp = sensor.value_c
        ts = self._target_states.setdefault(control_id, TargetState())

        # Hysteresis deadband: hold the last commanded PWM when temperature falls
        # within the deadband below the last transition point. Only update the
        # anchor when the curve produces a DIFFERENT output (real transition).
        if (
            ts.last_transition_temp is not None
            and ts.last_commanded_pwm is not None
            and current_temp <= ts.last_transition_temp
            and current_temp >= ts.last_transition_temp - HYSTERESIS_DEADBAND_C
        ):
            return ts.last_commanded_pwm

        desired = curve.interpolate(current_temp)
        desired = max(0.0, min(100.0, desired))

        # Only move the anchor when the output actually changes — this prevents
        # the deadband from "following" a continuously rising temperature.
        if ts.last_commanded_pwm is None or abs(desired - ts.last_commanded_pwm) >= 0.5:
            ts.last_transition_temp = current_temp
        ts.last_commanded_pwm = desired

        return desired

    def _should_write(self, target_id: str, desired_pwm: float) -> bool:
        for fan in self._state.fans:
            if fan.id == target_id:
                if fan.last_commanded_pwm is None:
                    return True  # First write — always allow
                return abs(desired_pwm - fan.last_commanded_pwm) >= PWM_WRITE_THRESHOLD_PCT
        return True  # Fan not in state yet — allow first write

    def _on_write_completed(self, target_id: str, success: bool) -> None:
        """Handle write result from background worker (runs on main thread)."""
        if success:
            count = self._write_failure_counts.get(target_id, 0)
            if count > 0:
                count -= 1
                if count == 0:
                    self._write_failure_counts.pop(target_id, None)
                else:
                    self._write_failure_counts[target_id] = count
            if count < 3 and self._state:
                self._state.remove_warning(f"write_fail:{target_id}")
        else:
            count = self._write_failure_counts.get(target_id, 0) + 1
            self._write_failure_counts[target_id] = count
            if count >= 3 and self._state:
                self._state.add_warning(
                    level="warning",
                    source="control_loop",
                    message=(
                        f"Fan '{target_id}' write failed {count} times — check lease/connection"
                    ),
                    key=f"write_fail:{target_id}",
                )

    def _on_lease_lost(self, reason: str) -> None:
        """Handle lease loss — transition to READ_ONLY for safety (P0-G2)."""
        log.error("Lease lost: %s — transitioning to READ_ONLY", reason)
        self._state.set_mode(OperationMode.READ_ONLY)
        self._state.add_warning(
            level="error",
            source="lease",
            message="Fan control paused: lost daemon lease. Fans returning to BIOS control.",
            key="lease_lost",
        )

    def _write_target(self, target_id: str, pwm_percent: float) -> bool:
        """Write PWM — dispatches to background worker or falls back to sync."""
        pwm_int = round(pwm_percent)

        if self._state.mode == OperationMode.DEMO and self._demo is not None:
            self._demo.set_fan_pwm(target_id, pwm_int)
            return True

        # Determine lease_id for hwmon writes
        lease_id = ""
        if target_id.startswith("hwmon:"):
            if self._lease and self._lease.is_held:
                lease_id = self._lease.lease_id or ""
            else:
                if self._lease:
                    self._lease.acquire()
                log.warning("hwmon write skipped -- no lease for %s", target_id)
                return False

        # Prefer background worker (production, non-blocking).
        # AutoConnection resolves to QueuedConnection since the worker lives
        # on a different QThread, so do_write executes on the worker thread.
        if self._write_worker is not None:
            self._request_write.emit(target_id, pwm_int, lease_id)
            return True

        # Synchronous fallback (tests only — production writes use _request_write signal)
        if self._client is None:
            return False
        try:
            m = _OPENFAN_CH_RE.match(target_id)
            if m:
                channel = int(m.group(1))
                self._client.set_openfan_pwm(channel, pwm_int)
                return True
            elif target_id.startswith("amd_gpu:"):
                gpu_id = target_id.removeprefix("amd_gpu:")
                self._client.set_gpu_fan_speed(gpu_id, pwm_int)
                return True
            elif target_id.startswith("hwmon:") and lease_id:
                self._client.set_hwmon_pwm(target_id, pwm_int, lease_id)
                return True
        except DaemonError as e:
            log.warning("Write failed for %s: %s", target_id, e.message)
            return False

        return False
