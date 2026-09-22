"""Drives one PWM Test Report run: the Qt shell around the pure runner (DEC-404).

Owned by the Hardware page rather than by the report window, on purpose: a run
must outlive its window. Closing the window mid-run cancels the run (after a
confirmation), but the cancel still has to be seen through — the running
diagnostic acknowledged, the hand-back waited for, the final state read and the
report saved — and a controller that died with the window would lose all of
that.

It owns:

* the :class:`~control_ofc.services.pwm_report.runner.ReportRunner`;
* one ``_PwmReportWorker`` on its own ``QThread`` with its own client (the
  ``_CharacterizationWorker`` pattern);
* a 1 s ``QTimer`` for the runner's poll cadence and hand-back waits;
* the trace, fed from ``AppState.fans_updated`` — the app's own 1 Hz poll, the
  last of the three signals each cycle emits, so status and sensors are current
  when it fires;
* the checkpoints: the report is saved after every step and when it ends.

Nothing here writes to a fan. Every write is a daemon diagnostic.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from control_ofc.api.models import ConnectionState
from control_ofc.services.pwm_report import store
from control_ofc.services.pwm_report.runner import (
    REASON_APP_CLOSED,
    REASON_CANCELLED,
    RUN_ACTIVE_REASON,
    Call,
    CallOutcome,
    PlannedStep,
    ReportRunner,
)
from control_ofc.services.pwm_report.trace import TraceRecorder
from control_ofc.ui.pages.diagnostics_workers import _PwmReportWorker

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

__all__ = ["RUN_ACTIVE_REASON", "PwmReportController"]

#: The runner's tick. Its poll cadence and hand-back waits are measured in
#: seconds, so a 1 s tick is the coarsest that honours them.
TICK_MS = 1000

#: How long a synchronous cancel may block an application exit.
EXIT_CANCEL_TIMEOUT_S = 2.0


class PwmReportController(QObject):
    """One run at a time; the last document stays readable after it ends."""

    #: Anything about the run changed — the window re-renders from the runner.
    changed = Signal()
    #: The run ended and its report is saved (the document).
    finished = Signal(object)
    #: A run started (True) or ended (False). The Hardware and System State
    #: pages disable their own diagnostics on it: they share one daemon slot.
    run_active_changed = Signal(bool)
    #: A checkpoint or the final save failed (message).
    save_failed = Signal(str)
    #: "Re-apply profile" finished (ok, message).
    reapply_done = Signal(bool, str)

    _call_request = Signal(int, str, object)

    def __init__(
        self,
        state: AppState | None,
        socket_path: str,
        parent: QObject | None = None,
        *,
        directory: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        worker_factory: Callable[[str], QObject] | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._socket_path = socket_path
        self._directory = directory
        self._clock = clock
        self._worker_factory = worker_factory or _PwmReportWorker
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._runner: ReportRunner | None = None
        self._trace: TraceRecorder | None = None
        self._started_at: float | None = None
        self._saved_progress = -1
        self._saved_phase = ""
        #: `finished` fires once per run: a late answer to a call the runner no
        #: longer waits for must not announce the end a second time.
        self._finish_announced = False
        self._reapply_req: int | None = None
        self._reapply_seq = 10_000_000  # disjoint from the runner's ids
        self.last_path: Path | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        if state is not None:
            state.fans_updated.connect(self._on_poll)
            state.connection_changed.connect(self._on_connection)
        # A report left `in_progress` by a crash is repaired once, here — the
        # first time anyone opens the report this session (store.py).
        with contextlib.suppress(Exception):
            store.recover_cut_off_reports(directory)

    # ── Introspection ───────────────────────────────────────────────────────

    @property
    def runner(self) -> ReportRunner | None:
        return self._runner

    def is_running(self) -> bool:
        return self._runner is not None and not self._runner.finished

    def document(self) -> dict | None:
        return self._runner.doc if self._runner is not None else None

    def elapsed_s(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    # ── Run control ─────────────────────────────────────────────────────────

    def start(self, doc: dict, plan: list[PlannedStep], probe_consent: frozenset[str]) -> bool:
        if self.is_running():
            return False
        if not self._ensure_worker():
            return False
        self._runner = ReportRunner(doc, plan, probe_consent=probe_consent)
        self._trace = TraceRecorder()
        self._started_at = self._clock()
        self._saved_progress = -1
        self._saved_phase = ""
        self._finish_announced = False
        self.run_active_changed.emit(True)
        self._dispatch(self._runner.start(self._clock()))
        self._timer.start()
        self._after_change()
        return True

    def cancel(self, reason: str = REASON_CANCELLED) -> None:
        if not self.is_running():
            return
        self._dispatch(self._runner.request_cancel(self._clock(), reason))
        self._after_change()

    def reapply_profile(self, profile_id: str) -> bool:
        """``POST /profile/activate`` for *profile_id*. The window confirms first:
        activating clears every active manual override (DEC-189)."""
        if not profile_id or not self._ensure_worker():
            return False
        self._reapply_seq += 1
        self._reapply_req = self._reapply_seq
        self._call_request.emit(
            self._reapply_req, "reapply_profile", {"args": {"profile_id": profile_id}}
        )
        return True

    def shutdown(self) -> None:
        """Application exit (S4-9 (5)): stop the daemon diagnostic, save the report
        ``interrupted``, tear the worker down. No prompt — the user is quitting.

        The cancel is issued synchronously on a short-lived client of its own,
        because the worker is about to be joined. Best effort: if it fails, the
        daemon finishes the diagnostic and restores the header by itself, which
        is the same guarantee a GUI crash relies on.
        """
        self._timer.stop()
        runner = self._runner
        if runner is not None and not runner.finished:
            slot = runner.active_slot
            if slot:
                self._cancel_synchronously(slot)
            runner.abandon(self._clock(), REASON_APP_CLOSED)
            self._checkpoint(force=True)
            self._finish_announced = True
            self.run_active_changed.emit(False)
        self._teardown_worker()

    # ── Worker plumbing ─────────────────────────────────────────────────────

    def _ensure_worker(self) -> bool:
        if self._worker is not None:
            return True
        if not self._socket_path:
            return False
        thread = QThread(self)
        worker = self._worker_factory(self._socket_path)
        worker.moveToThread(thread)
        self._call_request.connect(worker.do_call, Qt.ConnectionType.QueuedConnection)
        worker.call_done.connect(self._on_call_done, Qt.ConnectionType.QueuedConnection)
        thread.start()
        self._thread, self._worker = thread, worker
        return True

    def _teardown_worker(self) -> None:
        # Close the client BEFORE joining — the hardware page's rule, for the
        # same reason: a verify blocks for seconds, and closing the client is
        # the only interrupt that lets the join complete (CLAUDE.md: do not
        # re-attempt the worker-teardown reorder).
        worker, thread = self._worker, self._thread
        self._worker = self._thread = None
        if worker is not None:
            QObject.disconnect(worker, None, None, None)
            shutdown = getattr(worker, "shutdown", None)
            if callable(shutdown):
                shutdown()
        if thread is not None:
            thread.quit()
            if not thread.wait(2000):
                log.warning("PWM report thread did not stop within 2s, terminating")
                thread.terminate()
                thread.wait(1000)

    def _cancel_synchronously(self, slot: str) -> None:
        from control_ofc.api.client import DaemonClient

        client = None
        try:
            client = DaemonClient(socket_path=self._socket_path, timeout=EXIT_CANCEL_TIMEOUT_S)
            {
                "characterization": client.cancel_characterization,
                "control_path": client.cancel_control_path_discovery,
                "stall_probe": client.cancel_stall_probe,
            }[slot]()
        except Exception as e:  # best effort on the way out; logged
            log.info("PWM report: exit cancel of %s not confirmed (%s)", slot, e)
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.close()

    def _dispatch(self, calls: list[Call]) -> None:
        for call in calls:
            self._call_request.emit(
                call.req_id,
                call.kind,
                {"header_id": call.header_id, "args": dict(call.args)},
            )

    # ── Inputs ──────────────────────────────────────────────────────────────

    @Slot(int, object)
    def _on_call_done(self, req_id: int, outcome: object) -> None:
        if req_id == self._reapply_req:
            self._reapply_req = None
            ok = isinstance(outcome, CallOutcome) and outcome.ok
            message = (
                "The profile was re-applied."
                if ok
                else f"Re-applying the profile failed: {getattr(outcome, 'error_message', '')}"
            )
            self.reapply_done.emit(ok, message)
            return
        if self._runner is None or not isinstance(outcome, CallOutcome):
            return
        self._dispatch(self._runner.on_outcome(req_id, outcome, self._clock()))
        self._after_change()

    @Slot()
    def _on_tick(self) -> None:
        if self._runner is None:
            return
        self._dispatch(self._runner.on_tick(self._clock()))
        self._after_change()

    @Slot(list)
    def _on_poll(self, fans: list) -> None:
        runner = self._runner
        if runner is None or runner.finished or self._state is None:
            return
        status = self._state.daemon_status
        thermal = status.thermal_state if status is not None else "normal"
        if self._trace is not None:
            self._trace.add_sample(
                int(self.elapsed_s() * 1000),
                thermal_state=thermal,
                fans=fans,
                sensors=self._state.sensors,
            )
        self._dispatch(runner.observe(self._clock(), connected=True, thermal_state=thermal))
        self._after_change()

    @Slot(object)
    def _on_connection(self, conn: object) -> None:
        if self._runner is None or conn != ConnectionState.DISCONNECTED:
            return
        self._dispatch(self._runner.observe(self._clock(), connected=False, thermal_state=""))
        self._after_change()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _after_change(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._checkpoint()
        self.changed.emit()
        if runner.finished and not self._finish_announced:
            self._finish_announced = True
            self._timer.stop()
            self.run_active_changed.emit(False)
            self.finished.emit(runner.doc)

    def _checkpoint(self, *, force: bool = False) -> None:
        """Save after every finished step and when the run ends — not on every
        transition, which would rewrite a multi-megabyte trace a dozen times a
        step for nothing."""
        runner = self._runner
        if runner is None:
            return
        done, _total = runner.progress()
        if not force and done == self._saved_progress and runner.phase == self._saved_phase:
            return
        self._saved_progress, self._saved_phase = done, runner.phase
        if self._trace is not None:
            runner.doc["trace"] = self._trace.to_dict()
        try:
            self.last_path = store.save_report(runner.doc, self._directory)
        except (OSError, ValueError) as e:
            log.warning("PWM report: could not save %s: %s", runner.doc.get("report_id"), e)
            self.save_failed.emit(str(e))
