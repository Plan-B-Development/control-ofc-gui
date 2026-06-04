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
from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
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
    member_minimum_pct,
)

if TYPE_CHECKING:
    from control_ofc.services.demo_service import DemoService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.services.profile_service import Profile

log = logging.getLogger(__name__)

# Matches "openfan:chNN" and extracts the channel number.
_OPENFAN_CH_RE = re.compile(r"^openfan:ch(\d+)$")

# Safety auto-resume for pause_writes_for_header. The daemon's verify endpoint
# waits ~6s before reading back (raised from 3 s in DEC-101 — slow-spinning
# fans needed more settle time). 9 s leaves 3 s headroom for slow IPC plus
# the post-wait restore-PWM write, and still avoids pinning a header
# indefinitely if the verify caller crashes mid-call. Must stay strictly
# greater than the daemon's VERIFY_WAIT_SECONDS to avoid the 1 Hz control
# loop racing the daemon's readback. See DEC-101.
VERIFY_PAUSE_SAFETY_MS = 9000

# Per-call write timeout: PWM writes complete in <100 ms typically; the
# serial-timeout cap for OpenFan is 500 ms per channel. 2 s leaves 4x margin
# for the worst single-command case while shrinking the window during which
# the GUI blocks on a contended daemon. Pair with a single retry on
# `DaemonTimeout` (see `do_write`) so a transient mutex-held window doesn't
# count as a failure. See DEC-099.
WRITE_TIMEOUT_S = 2.0

# Outcomes the write worker reports back to the control loop. Replaces a
# bool so diagnostics can distinguish a timeout (likely overload or
# thermal-emergency mutex hold) from a 4xx (request shape) from a 503
# (transient hardware fault). DEC-099.
OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_UNAVAILABLE = "unavailable"  # connection refused / socket gone
OUTCOME_VALIDATION = "validation"  # 4xx — request shape problem
OUTCOME_OTHER = "other"  # 5xx other than connection / unspecified failure


def _dispatch_write(
    client: DaemonClient,
    target_id: str,
    pwm_int: int,
    lease_id: str,
    *,
    timeout: float | None = None,
) -> bool:
    """Route a PWM write to the right ``DaemonClient`` call by target-id prefix.

    Shared by the background ``_WriteWorker`` (which passes a per-call
    ``timeout``, DEC-099) and the synchronous test/fallback path in
    ``ControlLoopService._write_target`` (which passes none). ``timeout=None``
    omits the kwarg entirely, preserving the exact call shape each path used
    before this was unified. Returns True on a dispatched write; False for an
    unrecognised target or a hwmon write with no lease.
    """
    kw: dict[str, float] = {} if timeout is None else {"timeout": timeout}
    m = _OPENFAN_CH_RE.match(target_id)
    if m:
        client.set_openfan_pwm(int(m.group(1)), pwm_int, **kw)
        return True
    if target_id.startswith("amd_gpu:"):
        client.set_gpu_fan_speed(target_id.removeprefix("amd_gpu:"), pwm_int, **kw)
        return True
    if target_id.startswith("intel_gpu:"):
        # Intel discrete GPU fans are read-only (firmware-managed, DEC-121).
        # They are never offered as controllable members, so this is normally
        # unreachable — the explicit no-op makes the read-only contract
        # intentional rather than an accidental fallthrough.
        return False
    if target_id.startswith("hwmon:"):
        if not lease_id:
            return False
        client.set_hwmon_pwm(target_id, pwm_int, lease_id, **kw)
        return True
    return False


class _WriteWorker(QObject):
    """Runs in a QThread — executes blocking HTTP write calls off the main thread."""

    # target_id, outcome (OUTCOME_*). Replaces the previous bool signal; the
    # control loop maps OUTCOME_OK to success and the rest to failure for the
    # legacy failure-counter, while keeping the outcome for diagnostics.
    write_completed = Signal(str, str)

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    def _do_one(self, client: DaemonClient, target_id: str, pwm_int: int, lease_id: str) -> bool:
        """Issue one write attempt. Returns True on success.

        Delegates target-id routing to ``_dispatch_write`` and applies the 2 s
        per-call timeout (DEC-099) so the global ``API_TIMEOUT_S`` doesn't apply
        to fast-path writes.
        """
        return _dispatch_write(client, target_id, pwm_int, lease_id, timeout=WRITE_TIMEOUT_S)

    @Slot(str, int, str)
    def do_write(self, target_id: str, pwm_int: int, lease_id: str) -> None:
        """Execute a single PWM write (called on worker thread via signal).

        Retry behaviour (DEC-099): on a single ``DaemonTimeout`` we retry
        once before reporting OUTCOME_TIMEOUT. Two timeouts in a row are
        plausibly real overload; a single timeout often coincides with the
        daemon's thermal-emergency override scan releasing the controller
        mutex. Connection failures (``DaemonUnavailable``,
        ``ConnectionError``, ``OSError``) drop the client and never retry —
        the next cycle will reconnect.
        """
        attempted_retry = False
        while True:
            try:
                client = self._ensure_client()
                if self._do_one(client, target_id, pwm_int, lease_id):
                    self.write_completed.emit(target_id, OUTCOME_OK)
                else:
                    # Unrecognised target or missing lease — not retryable.
                    self.write_completed.emit(target_id, OUTCOME_VALIDATION)
                return
            except DaemonTimeout as e:
                if not attempted_retry:
                    log.info("Write timeout for %s, retrying once: %s", target_id, e.message)
                    attempted_retry = True
                    continue
                log.warning("Write timed out twice for %s: %s", target_id, e.message)
                self.write_completed.emit(target_id, OUTCOME_TIMEOUT)
                return
            except DaemonUnavailable as e:
                log.warning("Write unavailable for %s: %s", target_id, e.message)
                with contextlib.suppress(Exception):
                    if self._client is not None:
                        self._client.close()
                self._client = None
                self.write_completed.emit(target_id, OUTCOME_UNAVAILABLE)
                return
            except DaemonError as e:
                # 4xx surfaces as validation; everything else (5xx etc.) as other.
                log.warning("Write failed for %s: %s", target_id, e.message)
                outcome = OUTCOME_VALIDATION if 400 <= e.status < 500 else OUTCOME_OTHER
                self.write_completed.emit(target_id, outcome)
                return
            except (ConnectionError, OSError) as e:
                log.warning("Write worker connection error for %s: %s", target_id, e)
                with contextlib.suppress(Exception):
                    if self._client:
                        self._client.close()
                self._client = None
                self.write_completed.emit(target_id, OUTCOME_UNAVAILABLE)
                return

    def shutdown(self) -> None:
        """Close the underlying DaemonClient.

        MUST be called only after the worker thread has been quit + joined,
        because closing `self._client` mutates state the worker thread reads
        from inside `do_write`. See `ControlLoopService.shutdown()` for the
        correct ordering — that path quits the thread, waits for it to
        drain, then calls this method.
        """
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
        diagnostics: DiagnosticsService | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._profile_service = profile_service
        self._client = client
        self._lease = lease_service
        self._demo = demo_service
        self._diag = diagnostics

        self._target_states: dict[str, TargetState] = {}
        self._write_failure_counts: dict[str, int] = {}
        # Per-target, per-outcome counters (DEC-099). Maps target_id to a
        # dict of outcome -> count. Reset on a clean OUTCOME_OK so the
        # diagnostics view reflects current trouble rather than session
        # totals.
        self._write_outcome_counts: dict[str, dict[str, int]] = {}
        self._manual_override = False
        self._running = False
        self._is_shutdown = False

        # Per-header write pause for in-flight verify coordination (A1).
        # Maps target_id -> generation token. A safety timer auto-resumes after
        # VERIFY_PAUSE_SAFETY_MS so a hung verify cannot pin a header forever.
        self._paused_headers: dict[str, int] = {}

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

        # Acquire the lease whenever hwmon transitions from absent → present
        # (e.g. after /hwmon/rescan finds a controller). Initial acquisition
        # in start() is skipped when hwmon is absent.
        self._state.capabilities_updated.connect(self._on_capabilities_updated)

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
            self._maybe_acquire_lease()
            log.info("Control loop started (interval=%dms)", CONTROL_LOOP_INTERVAL_MS)
            if self._diag is not None:
                self._diag.log_event("info", "control_loop", "Control loop started")

    def _maybe_acquire_lease(self) -> None:
        """Acquire the hwmon lease only when hwmon is actually present.

        Avoids spurious "lease lost" warnings on OpenFan-only or GPU-only
        systems where the daemon returns 503 hardware_unavailable for every
        lease call.
        """
        if not self._lease or self._lease.is_held:
            return
        caps = self._state.capabilities
        if caps is None or caps.hwmon is None or not caps.hwmon.present:
            return
        self._lease.acquire()

    def _on_capabilities_updated(self, _caps) -> None:
        """Retry lease acquisition if hwmon became present after startup."""
        if self._running:
            self._maybe_acquire_lease()

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._timer.stop()
            log.info("Control loop stopped")
            if self._diag is not None:
                self._diag.log_event("info", "control_loop", "Control loop stopped")

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

    def pause_writes_for_header(self, header_id: str) -> None:
        """Pause writes to ``header_id`` until ``resume_writes_for_header`` is
        called or the safety timer fires (A1).

        Used by Diagnostics' verify worker to keep the control loop from
        racing the daemon's 6-second verify wait. A 9-second safety auto-
        resume guarantees a hung verify cannot pin the header forever.
        Each pause issues a fresh generation token; only the matching
        safety callback can auto-resume, so calling pause again before the
        previous safety timer fires is harmless (the older timer becomes a
        no-op).
        """
        if not header_id:
            return
        token = self._paused_headers.get(header_id, 0) + 1
        self._paused_headers[header_id] = token
        QTimer.singleShot(
            VERIFY_PAUSE_SAFETY_MS,
            lambda h=header_id, t=token: self._safety_resume(h, t),
        )

    def resume_writes_for_header(self, header_id: str) -> None:
        """Resume writes to ``header_id`` after a verify completes (A1)."""
        if not header_id:
            return
        self._paused_headers.pop(header_id, None)

    def _safety_resume(self, header_id: str, token: int) -> None:
        """Force resume after VERIFY_PAUSE_SAFETY_MS if still paused with the
        matching token. Newer pauses bump the token and make stale safety
        callbacks no-ops automatically."""
        if self._paused_headers.get(header_id) == token:
            log.warning(
                "Verify-pause safety timeout for %s after %d ms — forcing resume",
                header_id,
                VERIFY_PAUSE_SAFETY_MS,
            )
            self._paused_headers.pop(header_id, None)

    def _is_write_paused(self, target_id: str) -> bool:
        return target_id in self._paused_headers

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
        if self._is_shutdown:
            return
        self._is_shutdown = True
        self.stop()
        # P2-C: quit + join the worker thread BEFORE closing its DaemonClient.
        # `_WriteWorker.shutdown()` mutates `worker._client = None`, which is
        # read by `worker.do_write` on the worker thread. Doing that while
        # the worker may still be running a write is a data race — Python's
        # GIL hides it most of the time but it's not safe.
        if self._write_thread:
            self._write_thread.quit()
            if not self._write_thread.wait(2000):
                log.warning("Control loop write thread did not stop within 2s, terminating")
                self._write_thread.terminate()
                self._write_thread.wait(1000)
        if self._write_worker:
            self._write_worker.shutdown()

    def _on_sensors_updated(self, _sensors: list) -> None:
        """Evaluate immediately when fresh sensor data arrives.

        Restarts the timer to prevent a redundant timer-driven cycle within
        the same interval.
        """
        if self._running and not self._manual_override:
            self._timer.start()
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

        # Apply the control-wide tuning pipeline. This output is used verbatim
        # for every non-GPU member and for any control whose floor is already
        # 0% (e.g. a GPU-only control), so the common path is unchanged.
        control_output = self._apply_tuning(control, desired_pwm)
        status.control_outputs[control.id] = control_output

        # Write to each member (skip if no members assigned)
        if not control.members:
            return

        for member in control.members:
            target_id = member.target_id
            # DEC-119: GPU members carry no GUI floor. In a *mixed* control
            # (GPU grouped with chassis/CPU fans) the control-wide floor is
            # non-zero, so recompute the GPU member's output with its own 0%
            # floor and an independent step-rate tracker — letting it idle
            # below the chassis/CPU floor without disturbing the trajectory the
            # other members follow. Every non-GPU member, and every member of a
            # 0-floor control, reuses ``control_output`` unchanged.
            member_floor = member_minimum_pct(control, member)
            if member_floor < control.minimum_pct:
                output = self._apply_tuning(
                    control,
                    desired_pwm,
                    floor=member_floor,
                    state_key=self._member_state_key(control.id, target_id),
                )
            else:
                output = control_output

            if self._should_write(target_id, output):
                if self._write_target(target_id, output):
                    status.targets_active += 1
                    # Track success (sync path — async path uses _on_write_completed)
                    if self._write_worker is None:
                        self._on_write_completed(target_id, OUTCOME_OK)
                else:
                    status.targets_skipped += 1
                    # Track failure (sync path).  ``_write_target`` only returns
                    # False on validation-shaped failures (no lease, unknown
                    # target prefix) or sync-path DaemonError; counting these as
                    # OUTCOME_OTHER keeps the legacy-bool semantics intact for
                    # the fall-through tests that still invoke it.
                    if self._write_worker is None:
                        self._on_write_completed(target_id, OUTCOME_OTHER)
            else:
                status.targets_active += 1  # still active, just suppressed

    @staticmethod
    def _member_state_key(control_id: str, target_id: str) -> str:
        """Step-rate tracker key for a per-member output branch (DEC-119).

        Namespaced with ``::m::`` so it can never collide with a control's own
        ``control.id`` key. Cleared alongside the control-wide state by
        ``_reset_hysteresis`` (which clears the whole ``_target_states`` dict).
        """
        return f"{control_id}::m::{target_id}"

    def _apply_tuning(
        self,
        control: LogicalControl,
        raw_output: float,
        *,
        floor: float | None = None,
        state_key: str | None = None,
    ) -> float:
        """Apply per-control tuning: offset, minimum, step rate, start/stop.

        ``floor`` overrides the control-wide ``minimum_pct`` — used for the
        per-member GPU floor of 0% (DEC-119). ``state_key`` selects which
        step-rate tracker entry to read/write so a GPU member's independent
        trajectory does not clobber the control-wide one. Both default to the
        control-wide values, in which case this is byte-for-byte identical to
        the original single-output pipeline.
        """
        if floor is None:
            floor = control.minimum_pct
        if state_key is None:
            state_key = control.id

        output = raw_output + control.offset_pct
        output = max(floor, output)

        # Step rate limiting
        ts = self._target_states.get(state_key)
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
        if state_key not in self._target_states:
            self._target_states[state_key] = TargetState()
        self._target_states[state_key].last_output = output

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

    def _on_write_completed(self, target_id: str, outcome: str) -> None:
        """Handle write result from background worker (runs on main thread).

        ``outcome`` is one of the ``OUTCOME_*`` literals (DEC-099). Maps to
        success vs. the legacy failure counter as: ``OUTCOME_OK`` clears one
        count; everything else increments. Timeouts and unavailability are
        tracked separately for diagnostics so a slow daemon (timeouts) is
        distinguishable from a missing one (unavailable) or a request-shape
        bug (validation).
        """
        success = outcome == OUTCOME_OK
        # M9: remember that the GUI drove the GPU this session so closeEvent
        # can reset the fan when no profile is active (daemon would otherwise
        # leave the fan pinned to the last commanded PWM).
        if success and target_id.startswith("amd_gpu:") and self._state is not None:
            self._state.gui_wrote_gpu_fan = True

        # Per-outcome diagnostics counter — diagnostics page can read this
        # to distinguish "daemon is slow" from "daemon is gone".
        if outcome != OUTCOME_OK:
            outcomes = self._write_outcome_counts.setdefault(target_id, {})
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

        if success:
            count = self._write_failure_counts.get(target_id, 0)
            was_warned = count >= 3
            if count > 0:
                count -= 1
                if count == 0:
                    self._write_failure_counts.pop(target_id, None)
                else:
                    self._write_failure_counts[target_id] = count
            if count < 3 and self._state:
                self._state.remove_warning(f"write_fail:{target_id}")
            # Reset per-outcome counters on a clean success — keeps the
            # diagnostics view fresh rather than monotonically growing.
            self._write_outcome_counts.pop(target_id, None)
            # DEC-111: log the recovery edge so the event log shows that a
            # previously-troubled target is healthy again. Only fires when
            # we were past the warning threshold — silent recovery is the
            # common case and not worth a row.
            if was_warned and count < 3 and self._diag is not None:
                self._diag.log_event("info", "control_loop", f"Fan '{target_id}' writes recovered")
        else:
            count = self._write_failure_counts.get(target_id, 0) + 1
            self._write_failure_counts[target_id] = count
            crossed_threshold = count == 3
            if count >= 3 and self._state:
                # Pick the message based on the dominant outcome for a more
                # useful warning than the previous generic one.
                outcomes = self._write_outcome_counts.get(target_id, {})
                if outcomes.get(OUTCOME_TIMEOUT, 0) >= 2:
                    msg = (
                        f"Fan '{target_id}' write timed out {count} times — "
                        f"daemon may be overloaded (try checking thermal state)"
                    )
                elif outcomes.get(OUTCOME_UNAVAILABLE, 0) >= 2:
                    msg = (
                        f"Fan '{target_id}' write failed {count} times — "
                        f"daemon connection lost (check daemon status)"
                    )
                elif outcomes.get(OUTCOME_VALIDATION, 0) >= 2:
                    msg = (
                        f"Fan '{target_id}' write rejected {count} times — "
                        f"check lease or fan configuration"
                    )
                else:
                    msg = f"Fan '{target_id}' write failed {count} times — check lease/connection"
                self._state.add_warning(
                    level="warning",
                    source="control_loop",
                    message=msg,
                    key=f"write_fail:{target_id}",
                )
                # DEC-111: emit the matching event exactly once per
                # threshold crossing (count == 3). The active-warnings
                # dialog reflects current condition; the event log
                # records that it happened.
                if crossed_threshold and self._diag is not None:
                    self._diag.log_event("warning", "control_loop", msg)

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
        if self._diag is not None:
            self._diag.log_event(
                "error",
                "control_loop",
                f"Fan control paused — lost daemon lease ({reason})",
            )

    def _write_target(self, target_id: str, pwm_percent: float) -> bool:
        """Write PWM — dispatches to background worker or falls back to sync."""
        if self._is_write_paused(target_id):
            return False

        pwm_int = round(pwm_percent)

        if self._state.mode == OperationMode.DEMO and self._demo is not None:
            self._demo.set_fan_pwm(target_id, pwm_int)
            return True

        # Determine lease_id for hwmon writes.
        # DEC-108: in worker mode `acquire()` is async — it queues the take
        # request and returns True immediately even though `is_held` is still
        # False. We must only proceed when we actually hold the lease;
        # otherwise we'd send an empty lease_id and the daemon would 403.
        # Skipping the write is fine — `is_held` will flip True on a later
        # cycle once the worker's take_completed signal lands.
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
            return _dispatch_write(self._client, target_id, pwm_int, lease_id)
        except DaemonError as e:
            log.warning("Write failed for %s: %s", target_id, e.message)
            return False
