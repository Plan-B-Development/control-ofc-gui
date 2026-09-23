"""The run: a pure state machine over the daemon's own diagnostics (DEC-404).

**The GUI orchestrates; the daemon performs.** Every write, every guard and
every restore is the daemon's: this module only decides which diagnostic to ask
for next and records the answer exactly as the daemon gave it. It never computes
a duty, never retries a refused test, and never reads a refusal as a failure of
the hardware.

It is pure and clock-free — the caller passes ``now`` (monotonic seconds) — so
every transition is testable without Qt, a worker or a daemon. The Qt
controller feeds it four kinds of input and executes the :class:`Call` objects
it returns:

* :meth:`ReportRunner.on_outcome` — a call finished;
* :meth:`ReportRunner.on_tick` — once a second (poll cadence, hand-back wait);
* :meth:`ReportRunner.observe` — the app's 1 Hz poll (connection + thermal);
* :meth:`ReportRunner.request_cancel` / :meth:`ReportRunner.abandon` — the user.

Run-level rules (DEC-404 § Runner):

* the thermal state leaving ``normal`` stops the run: the running diagnostic is
  cancelled (the daemon aborts it anyway), the rest are *not tested*, and the
  report ends ``aborted``;
* losing the daemon ends it ``interrupted``; the daemon restores its own header
  when its diagnostic ends, which is why the GUI dying never strands one;
* Cancel sends ``DELETE`` for the running diagnostic and marks the rest *not
  tested — cancelled by you*;
* after every diagnostic the runner waits :data:`HANDBACK_WAIT_S` before the
  next, so DEC-382's next-tick hand-back has landed and the app's poll has seen
  it before anything else is measured.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.catalog import (
    SPECS,
    SWEEP_BIDIRECTIONAL,
    SWEEP_POINTS_PCT,
    SWEEP_STABILITY_S,
    TEST_ORDER,
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
)

#: One engine tick (1 s) + two app polls (1 s each): DEC-382 hands a
#: diagnostic's header back on the next engine tick, and the report's next
#: measurement must not start before the app has seen it.
HANDBACK_WAIT_S = 3.0
#: The daemon's run slots are read back at the app's own 1 Hz cadence.
POLL_INTERVAL_S = 1.0
#: Consecutive failed polls before the daemon is treated as lost. Five seconds
#: rides out a slow reply; a daemon restart takes longer and is caught by the
#: connection state first.
MAX_POLL_FAILURES = 5

CALL_SNAPSHOT = "snapshot"
CALL_PREFLIGHT = "preflight"
CALL_VERIFY = "verify"
CALL_START_PAIRING = "start_pairing"
CALL_START_SWEEP = "start_sweep"
CALL_START_PROBE = "start_probe"
CALL_POLL = "poll"
CALL_CANCEL = "cancel"

SLOT_CHARACTERIZATION = "characterization"
SLOT_CONTROL_PATH = "control_path"
SLOT_STALL_PROBE = "stall_probe"

#: Which daemon run slot each long-running test occupies (poll + cancel target).
SLOT_FOR_TEST: Mapping[str, str] = MappingProxyType(
    {
        TEST_PAIRING: SLOT_CONTROL_PATH,
        TEST_SWEEP: SLOT_CHARACTERIZATION,
        TEST_PROBE: SLOT_STALL_PROBE,
    }
)

_START_CALL: Mapping[str, str] = MappingProxyType(
    {
        TEST_VERIFY: CALL_VERIFY,
        TEST_PAIRING: CALL_START_PAIRING,
        TEST_SWEEP: CALL_START_SWEEP,
        TEST_PROBE: CALL_START_PROBE,
    }
)

#: Run states the daemon's three diagnostics share. An unrecognised token is
#: recorded as-is and treated as terminal (273-i: render, never drop).
_RUN_STATE_TO_STEP: Mapping[str, str] = MappingProxyType(
    {
        "complete": d.STEP_COMPLETE,
        "cancelled": d.STEP_CANCELLED,
        "aborted": d.STEP_ABORTED,
        "failed": d.STEP_ERROR,
    }
)

REASON_CANCELLED = "Cancelled by you."
REASON_WINDOW_CLOSED = "Cancelled: the report window was closed during the run."
REASON_APP_CLOSED = "Control-OFC was closed during the run."
REASON_CONNECTION_LOST = "The connection to the daemon was lost."
REASON_NOT_CONFIRMED = "You did not confirm the stall probe for this header, so it was not run."

#: Shown on every diagnostic button a run stands down (S4-9 (4), S4-13): the
#: report holds the daemon's one diagnostic slot, and a test started between two
#: of its steps would write to a header in the middle of its measurements.
RUN_ACTIVE_REASON = (
    "A PWM Test Report is running and is using the daemon's diagnostic slot. "
    "This is available again when the report finishes."
)


@dataclass(frozen=True)
class Call:
    """One request for the worker. ``req_id`` fences stale answers."""

    req_id: int
    kind: str
    step_id: str | None = None
    header_id: str | None = None
    args: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CallOutcome:
    """What the worker got back. ``body`` is the daemon's JSON, verbatim."""

    ok: bool
    status: int | None = None
    body: object = None
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    #: ``unavailable`` — the daemon could not be reached or did not answer in
    #: time; ``error`` — the daemon answered with an error.
    category: str = ""
    #: The error envelope's ``details`` (e.g. an ineligible probe's ``reason``
    #: token), kept as evidence.
    details: object = None


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    channel_id: str
    test: str


def build_plan(selection: Mapping[str, Iterable[str]]) -> list[PlannedStep]:
    """Headers in stable-id order; tests in :data:`TEST_ORDER` (S4-9 (1))."""
    steps: list[PlannedStep] = []
    for channel_id in sorted(selection):
        picked = set(selection[channel_id])
        for test in TEST_ORDER:
            if test in picked:
                steps.append(PlannedStep(f"s{len(steps) + 1}", channel_id, test))
    return steps


def start_refusals(
    *,
    connected: bool,
    thermal_state: str,
    diagnostic_running: bool,
    session_recording: bool,
    local_verify_running: bool,
    demo_mode: bool,
) -> list[str]:
    """Why a run must not start now (DEC-404 § Runner step 1). Empty = go.

    Every reason is a state the user can change, worded as that state rather
    than as an error.
    """
    reasons: list[str] = []
    if demo_mode:
        # D-a: synthetic hardware makes every finding meaningless.
        reasons.append("Demo mode is on: a report about synthetic hardware would mean nothing.")
    if not connected:
        reasons.append("The daemon is not connected.")
    if (thermal_state or "normal") != "normal":
        reasons.append(
            f"The daemon's thermal protection is active ({thermal_state}); wait until it "
            "returns to normal."
        )
    if diagnostic_running:
        reasons.append("Another diagnostic is running on the daemon; wait for it to finish.")
    if session_recording:
        reasons.append(
            "A validation session is recording. It shares the daemon's diagnostic slot — "
            "stop it first."
        )
    if local_verify_running:
        # `PTA-d`: a System State verify, or a Verify All sweep, that this GUI
        # started. `diagnostic_running` sees it only through the poll's
        # `verify_active`, which reads false in the gap between two of a sweep's
        # verifies — so the sweep's remaining headers would be written during the
        # run. This input is the GUI's own record of it, and has no such gap.
        reasons.append(
            "A PWM verify started on the System State page is still running; wait for it to finish."
        )
    return reasons


def _body_dict(body: object) -> Mapping:
    return body if isinstance(body, Mapping) else {}


class ReportRunner:
    """Owns one report document for the length of one run."""

    def __init__(
        self,
        doc: dict,
        plan: Iterable[PlannedStep],
        *,
        probe_consent: Iterable[str] = (),
    ) -> None:
        self.doc = doc
        self._plan = list(plan)
        self._consent = frozenset(probe_consent)
        self._seq = 0
        self._pending: dict[int, Call] = {}
        #: ``idle`` → ``baseline`` → ``steps`` → ``final`` → ``done``.
        self.phase = "idle"
        self._index = -1
        #: ``preflight`` | ``starting`` | ``running`` | ``handback`` | "".
        self.step_phase = ""
        self._run_id: str | None = None
        self._last_poll_at: float | None = None
        self._poll_in_flight = False
        self._poll_failures = 0
        self._handback_until: float | None = None
        self._cancel_sent = False
        #: ``(report state, reason)`` once the run is stopping early.
        self._stop: tuple[str, str] | None = None
        #: The most recent run body the daemon returned for the current step —
        #: live progress for the Run page; not stored in the document.
        self.live_run: Mapping | None = None
        #: Bumped on every change worth checkpointing to disk.
        self.revision = 0

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def finished(self) -> bool:
        return self.phase == "done"

    @property
    def stopping(self) -> bool:
        return self._stop is not None

    @property
    def current_step(self) -> dict | None:
        steps = self.doc["steps"]
        return steps[self._index] if 0 <= self._index < len(steps) else None

    @property
    def active_slot(self) -> str | None:
        """The daemon run slot a diagnostic of ours may be occupying now."""
        step = self.current_step
        if step is None or self.step_phase not in ("starting", "running"):
            return None
        return SLOT_FOR_TEST.get(step["test"])

    def progress(self) -> tuple[int, int]:
        """(steps finished, steps in the plan)."""
        done = sum(
            1 for s in self.doc["steps"] if s["status"] not in (d.STEP_PENDING, d.STEP_RUNNING)
        )
        return done, len(self.doc["steps"])

    # ── Inputs ───────────────────────────────────────────────────────────────

    def start(self, now: float) -> list[Call]:
        if self.phase != "idle":
            return []
        self.doc["steps"] = [
            {
                "step_id": p.step_id,
                "channel_id": p.channel_id,
                "test": p.test,
                "diagnostic": SPECS[p.test].diagnostic,
                "status": d.STEP_PENDING,
                "reason": "",
                "started_at": None,
                "ended_at": None,
                "request": None,
                "preflight": None,
                "preflight_note": "",
                "start_response": None,
                "result": None,
                "run_state": "",
                "restore_outcome": "",
                "error": None,
            }
            for p in self._plan
        ]
        self.phase = "baseline"
        self._bump()
        return [self._call(CALL_SNAPSHOT, args={"tag": "baseline"})]

    def on_outcome(self, req_id: int, outcome: CallOutcome, now: float) -> list[Call]:
        call = self._pending.pop(req_id, None)
        if call is None or self.finished:
            return []  # a stale answer to a call this run no longer waits for
        if call.kind == CALL_SNAPSHOT:
            return self._on_snapshot(call, outcome, now)
        step = self.current_step
        if step is None or call.step_id != step["step_id"]:
            return []
        if call.kind == CALL_PREFLIGHT:
            return self._on_preflight(step, outcome, now)
        if call.kind == CALL_VERIFY:
            return self._on_verify(step, outcome, now)
        if call.kind in (CALL_START_PAIRING, CALL_START_SWEEP, CALL_START_PROBE):
            return self._on_started(step, outcome, now)
        if call.kind == CALL_POLL:
            self._poll_in_flight = False
            return self._on_poll(step, outcome, now)
        if call.kind == CALL_CANCEL:
            # The next poll reads the terminal state; a 409 here means the run
            # had already ended, which that poll will show just the same.
            return []
        return []

    def on_tick(self, now: float) -> list[Call]:
        if self.phase != "steps":
            return []
        if self.step_phase == "running":
            if self._poll_in_flight:
                return []
            if self._last_poll_at is None or now - self._last_poll_at >= POLL_INTERVAL_S:
                return self._poll(now)
            return []
        if (
            self.step_phase == "handback"
            and self._handback_until is not None
            and now >= self._handback_until
        ):
            self.step_phase = ""
            self._handback_until = None
            return self._advance(now)
        return []

    def observe(self, now: float, *, connected: bool, thermal_state: str) -> list[Call]:
        if self.phase in ("idle", "final", "done"):
            return []
        if not connected:
            return self._lose_daemon(now)
        state = thermal_state or "normal"
        if state != "normal" and self._stop is None:
            self._stop = (
                d.STATE_ABORTED,
                f"The daemon's thermal protection became active ({state}), so the run stopped.",
            )
            self._bump()
            return self._cancel_running()
        return []

    def request_cancel(self, now: float, reason: str = REASON_CANCELLED) -> list[Call]:
        if self.phase in ("idle", "final", "done"):
            return []
        if self._stop is None:
            self._stop = (d.STATE_CANCELLED, reason)
            self._bump()
        return self._cancel_running()

    def abandon(self, now: float, reason: str = REASON_APP_CLOSED) -> None:
        """End the run where it stands, with no further calls.

        For an application exit: the caller cancels the running diagnostic
        itself (synchronously), and the report is saved ``interrupted``. The
        final snapshot is not taken — the report says so rather than inventing
        one.
        """
        if self.phase in ("idle", "done"):
            return
        self._stop = (d.STATE_INTERRUPTED, reason)
        step = self.current_step
        if step is not None and step["status"] == d.STEP_RUNNING:
            self._end_step(step, d.STEP_INTERRUPTED, reason)
        self._mark_rest_not_tested(reason)
        self.doc["snapshots"]["final"] = None
        self._pending.clear()
        self._finalize(final_note=f"The final state was not read: {reason}")

    # ── Transitions ──────────────────────────────────────────────────────────

    def _on_snapshot(self, call: Call, outcome: CallOutcome, now: float) -> list[Call]:
        tag = call.args.get("tag")
        if tag == "baseline":
            if not outcome.ok or not isinstance(outcome.body, Mapping):
                self._stop = (
                    d.STATE_INTERRUPTED,
                    "The starting state could not be read from the daemon "
                    f"({outcome.error_message or 'no answer'}), so nothing was tested.",
                )
                self._mark_rest_not_tested(self._stop[1])
                self.doc["snapshots"]["baseline"] = None
                self._finalize(final_note="No final state was read: the run never started.")
                return []
            self.doc["snapshots"]["baseline"] = outcome.body
            self.doc["environment"] = d.extract_environment(
                outcome.body, self.doc["environment"].get("gui", {})
            )
            self.doc["configuration"] = d.extract_configuration(outcome.body)
            self.phase = "steps"
            self._bump()
            return self._advance(now)
        # final
        if outcome.ok and isinstance(outcome.body, Mapping):
            self.doc["snapshots"]["final"] = outcome.body
            self._finalize(final_note="")
        else:
            self.doc["snapshots"]["final"] = None
            self._finalize(
                final_note=(
                    "The final state could not be read from the daemon "
                    f"({outcome.error_message or 'no answer'})."
                )
            )
        return []

    def _advance(self, now: float) -> list[Call]:
        """Move to the next pending step, or to the final snapshot."""
        if self._stop is not None:
            self._mark_rest_not_tested(self._stop[1])
            return self._begin_final()
        steps = self.doc["steps"]
        nxt = next((i for i, s in enumerate(steps) if s["status"] == d.STEP_PENDING), None)
        if nxt is None:
            return self._begin_final()
        self._index = nxt
        step = steps[nxt]
        step["status"] = d.STEP_RUNNING
        step["started_at"] = d.utc_now_iso()
        self._run_id = None
        self._cancel_sent = False
        self._poll_failures = 0
        self.live_run = None
        if step["test"] == TEST_PROBE and step["channel_id"] not in self._consent:
            # Enforced here as well as by the window's Start gate, so a probe can
            # never start without its own confirmation (S4-2) whatever the UI did.
            self._end_step(step, d.STEP_NOT_TESTED, REASON_NOT_CONFIRMED)
            return self._advance(now)
        self.step_phase = "preflight"
        self._bump()
        return [
            self._call(
                CALL_PREFLIGHT,
                step,
                args={"diagnostic": step["diagnostic"]},
            )
        ]

    def _on_preflight(self, step: dict, outcome: CallOutcome, now: float) -> list[Call]:
        if outcome.ok and isinstance(outcome.body, Mapping):
            step["preflight"] = outcome.body
            if outcome.body.get("verdict") == "blocked":
                self._end_step(
                    step,
                    d.STEP_NOT_TESTED,
                    "The daemon's safety preflight blocked this test.",
                )
                return self._advance(now)
        elif outcome.status == 404:
            step["preflight_note"] = (
                "This daemon has no safety preflight; its own guards still ran."
            )
        else:
            step["preflight_note"] = (
                "The safety preflight could not be read "
                f"({outcome.error_message or 'no answer'}); the daemon's own guards still ran."
            )
        return self._start(step, now)

    def _start(self, step: dict, now: float) -> list[Call]:
        if self._stop is not None:
            self._end_step(step, d.STEP_NOT_TESTED, self._stop[1])
            return self._advance(now)
        test = step["test"]
        args: dict[str, object] = {}
        if test == TEST_SWEEP:
            args = {
                "points_pct": list(SWEEP_POINTS_PCT),
                "bidirectional": SWEEP_BIDIRECTIONAL,
                "stability_seconds": SWEEP_STABILITY_S,
            }
        elif test == TEST_PROBE:
            args = {"acknowledge_below_floor": True}
        step["request"] = dict(args)
        self.step_phase = "starting"
        self._bump()
        return [self._call(_START_CALL[test], step, args=args)]

    def _on_verify(self, step: dict, outcome: CallOutcome, now: float) -> list[Call]:
        if outcome.ok:
            step["result"] = outcome.body
            body = _body_dict(outcome.body)
            step["run_state"] = "complete"
            step["restore_outcome"] = "write_failed" if body.get("restore_failed") else "restored"
            self._end_step(step, d.STEP_COMPLETE, "")
            return self._enter_handback(now)
        wrote = self._record_refusal(step, outcome)
        return self._enter_handback(now) if wrote else self._advance(now)

    def _on_started(self, step: dict, outcome: CallOutcome, now: float) -> list[Call]:
        if not outcome.ok:
            wrote = self._record_refusal(step, outcome)
            return self._enter_handback(now) if wrote else self._advance(now)
        step["start_response"] = outcome.body
        body = _body_dict(outcome.body)
        self._run_id = str(body.get("run_id") or "") or None
        self.step_phase = "running"
        self._last_poll_at = now
        self._bump()
        if self._stop is not None:
            return self._cancel_running()
        return []

    def _poll(self, now: float) -> list[Call]:
        step = self.current_step
        if step is None:
            return []
        self._poll_in_flight = True
        self._last_poll_at = now
        return [self._call(CALL_POLL, step, args={"slot": SLOT_FOR_TEST[step["test"]]})]

    def _on_poll(self, step: dict, outcome: CallOutcome, now: float) -> list[Call]:
        run = _body_dict(outcome.body)
        if step["test"] == TEST_PAIRING:
            run = _body_dict(run.get("run"))
        if not outcome.ok or not run:
            self._poll_failures += 1
            if self._poll_failures >= MAX_POLL_FAILURES:
                return self._lose_daemon(now)
            return []
        self._poll_failures = 0
        run_id = str(run.get("run_id") or "")
        if self._run_id and run_id and run_id != self._run_id:
            self._end_step(
                step,
                d.STEP_INTERRUPTED,
                "Another diagnostic took the daemon's diagnostic slot during this test.",
            )
            return self._enter_handback(now)
        self.live_run = run
        state = str(run.get("state") or "")
        if state == "running":
            return []
        step["result"] = run
        step["run_state"] = state
        step["restore_outcome"] = str(run.get("restore_outcome") or "")
        # An unrecognised terminal token is kept as the status itself (273-i);
        # a missing one is an error, never a silent "complete".
        status = _RUN_STATE_TO_STEP.get(state, state or d.STEP_ERROR)
        reason = ""
        if status == d.STEP_CANCELLED and self._stop is not None:
            reason = self._stop[1]
        self._end_step(step, status, reason)
        return self._enter_handback(now)

    def _record_refusal(self, step: dict, outcome: CallOutcome) -> bool:
        """Record a start the daemon did not run. Returns whether it may have
        written anything (a timeout may have), which decides the hand-back wait.

        A refusal is never a hardware verdict: 409 is a busy slot, a thermal or
        retryable refusal is protection, and an ineligible header is the
        daemon's envelope doing its job.
        """
        step["error"] = {
            "status": outcome.status,
            "code": outcome.error_code,
            "message": outcome.error_message,
            "retryable": outcome.retryable,
            "details": outcome.details,
        }
        if outcome.category == "unavailable":
            self._end_step(
                step,
                d.STEP_ERROR,
                "No answer from the daemon. If the test had started, the daemon "
                "restores the header itself when it ends.",
            )
            return True
        if outcome.status == 409:
            reason = "Another diagnostic was already running on the daemon."
        elif outcome.error_code == "thermal_abort" or (
            outcome.error_code == "validation_error" and outcome.retryable
        ):
            reason = f"The daemon declined for safety: {outcome.error_message}"
        else:
            reason = f"The daemon did not run it: {outcome.error_message}"
        self._end_step(step, d.STEP_NOT_TESTED, reason)
        return False

    def _enter_handback(self, now: float) -> list[Call]:
        self.step_phase = "handback"
        self._handback_until = now + HANDBACK_WAIT_S
        self._bump()
        return []

    def _cancel_running(self) -> list[Call]:
        """Send DELETE once for a diagnostic that is running now.

        Nothing to send for a verify (it is synchronous and short) or while a
        start is still in flight — :meth:`_on_started` sends it on arrival.
        """
        step = self.current_step
        if step is None or self.step_phase != "running" or self._cancel_sent:
            return []
        self._cancel_sent = True
        return [self._call(CALL_CANCEL, step, args={"slot": SLOT_FOR_TEST[step["test"]]})]

    def _lose_daemon(self, now: float) -> list[Call]:
        if self.phase == "baseline":
            return []  # the baseline call's own failure ends the run
        if self._stop is None or self._stop[0] != d.STATE_INTERRUPTED:
            self._stop = (d.STATE_INTERRUPTED, REASON_CONNECTION_LOST)
        step = self.current_step
        if step is not None and step["status"] == d.STEP_RUNNING:
            self._end_step(
                step,
                d.STEP_INTERRUPTED,
                REASON_CONNECTION_LOST
                + " The daemon restores the header itself when its diagnostic ends.",
            )
        self._pending = {k: v for k, v in self._pending.items() if v.kind == CALL_SNAPSHOT}
        self._poll_in_flight = False
        self.step_phase = ""
        self._mark_rest_not_tested(REASON_CONNECTION_LOST)
        return self._begin_final()

    def _begin_final(self) -> list[Call]:
        if self.phase in ("final", "done"):
            return []
        self.phase = "final"
        self.step_phase = ""
        self._bump()
        return [self._call(CALL_SNAPSHOT, args={"tag": "final"})]

    def _finalize(self, *, final_note: str) -> None:
        from control_ofc.services.pwm_report.findings import derive_findings

        self.phase = "done"
        self.step_phase = ""
        state, reason = self._stop if self._stop is not None else (d.STATE_COMPLETE, "")
        self.doc["state"] = state
        self.doc["state_reason"] = reason
        self.doc["finished_at"] = d.utc_now_iso()
        self.doc["final_note"] = final_note
        findings, actions = derive_findings(self.doc)
        self.doc["findings"] = findings
        self.doc["actions"] = actions
        self._bump()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _end_step(self, step: dict, status: str, reason: str) -> None:
        step["status"] = status
        if reason:
            step["reason"] = reason
        step["ended_at"] = d.utc_now_iso()
        self._bump()

    def _mark_rest_not_tested(self, reason: str) -> None:
        for s in self.doc["steps"]:
            if s["status"] == d.STEP_PENDING:
                s["status"] = d.STEP_NOT_TESTED
                s["reason"] = reason
        self._bump()

    def _call(
        self,
        kind: str,
        step: dict | None = None,
        *,
        args: Mapping[str, object] | None = None,
    ) -> Call:
        self._seq += 1
        call = Call(
            req_id=self._seq,
            kind=kind,
            step_id=step["step_id"] if step else None,
            header_id=step["channel_id"] if step else None,
            args=MappingProxyType(dict(args or {})),
        )
        self._pending[call.req_id] = call
        return call

    def _bump(self) -> None:
        self.revision += 1
