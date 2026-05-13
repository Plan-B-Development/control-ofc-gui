"""Hwmon lease lifecycle — acquire, renew, release.

The daemon requires an exclusive lease for hwmon PWM writes (60 s TTL).
This service manages the lease independently from the control loop.

Threading model (DEC-108):
- In production the actual HTTP calls (`POST /hwmon/lease/{take,renew,release}`)
  run on a dedicated `QThread` worker (`_LeaseWorker`) so the Qt main thread
  is never blocked by a contended daemon. The previous design called these
  HTTP methods directly from the main thread, which could freeze the GUI
  event loop (and thus stall the 1 Hz control-loop timer) for up to
  `API_TIMEOUT_S` seconds when the daemon was under load.
- In tests the worker is omitted (no `socket_path` given): calls go directly
  through the injected `client` mock so existing sync-style tests keep
  working without `qtbot.waitSignal` wrappers around every internal call.
- Either way, `lease_id` / `is_held` are simple snapshots of an internal
  state variable and remain safe to read from the main thread.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.constants import LEASE_API_TIMEOUT_S, LEASE_RENEW_INTERVAL_S

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class _LeaseWorker(QObject):
    """Performs lease HTTP I/O on a dedicated worker thread.

    Each public slot receives a single request, makes the HTTP call against
    its private `DaemonClient` (with a `LEASE_API_TIMEOUT_S` bound), and
    emits a structured completion signal back to `LeaseService` on the main
    thread. Exceptions never propagate out of a slot — they are translated
    into a `success=False` completion so a transient failure cannot crash
    the worker thread and leave the main thread waiting forever.
    """

    # success, lease_id (or ""), ttl_seconds (or 0), error_msg (or "")
    take_completed = Signal(bool, str, int, str)
    renew_completed = Signal(bool, str, int, str)
    # success, error_msg
    release_completed = Signal(bool, str)

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot(str)
    def do_take(self, owner_hint: str) -> None:
        try:
            r = self._ensure_client().hwmon_lease_take(owner_hint, timeout=LEASE_API_TIMEOUT_S)
            self.take_completed.emit(True, r.lease_id, r.ttl_seconds, "")
        except DaemonError as e:
            self.take_completed.emit(False, "", 0, f"{e.code}: {e.message}")
        except Exception as e:
            log.exception("Lease take worker raised")
            self.take_completed.emit(False, "", 0, str(e))

    @Slot(str)
    def do_renew(self, lease_id: str) -> None:
        try:
            r = self._ensure_client().hwmon_lease_renew(lease_id, timeout=LEASE_API_TIMEOUT_S)
            self.renew_completed.emit(True, r.lease_id, r.ttl_seconds, "")
        except DaemonError as e:
            self.renew_completed.emit(False, "", 0, f"{e.code}: {e.message}")
        except Exception as e:
            log.exception("Lease renew worker raised")
            self.renew_completed.emit(False, "", 0, str(e))

    @Slot(str)
    def do_release(self, lease_id: str) -> None:
        try:
            self._ensure_client().hwmon_lease_release(lease_id, timeout=LEASE_API_TIMEOUT_S)
            self.release_completed.emit(True, "")
        except DaemonError as e:
            self.release_completed.emit(False, e.message)
        except Exception as e:
            log.exception("Lease release worker raised")
            self.release_completed.emit(False, str(e))

    def close_client(self) -> None:
        """Drop the underlying `DaemonClient`.

        Caller must guarantee the worker thread is no longer running before
        invoking this — otherwise we mutate `_client` from the main thread
        while the worker may still be using it. `LeaseService.shutdown()`
        enforces this by quitting and joining the worker thread first.
        """
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


class LeaseService(QObject):
    """Manages hwmon lease acquire / renew / release lifecycle."""

    lease_acquired = Signal(str)  # lease_id
    lease_lost = Signal(str)  # reason
    lease_renewed = Signal(str)  # lease_id

    # Internal request signals (only used in worker mode). Connected to the
    # worker's slots via Qt::QueuedConnection so emission from the main
    # thread is non-blocking.
    _request_take = Signal(str)
    _request_renew = Signal(str)
    _request_release = Signal(str)

    # Maximum retries before declaring lease truly lost (P0-G2).
    _MAX_RENEW_RETRIES = 3

    def __init__(
        self,
        client: DaemonClient,
        parent: QObject | None = None,
        *,
        socket_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._lease_id: str | None = None
        self._renew_retry_count = 0
        # Coalesce duplicate in-flight requests in worker mode so a flurry
        # of `acquire()` calls from the control loop / capability-update
        # signals does not queue overlapping `POST /hwmon/lease/take` calls.
        self._take_in_flight = False
        self._renew_in_flight = False

        self._renew_timer = QTimer(self)
        self._renew_timer.setInterval(LEASE_RENEW_INTERVAL_S * 1000)
        self._renew_timer.timeout.connect(self._renew)

        # Worker thread is created lazily — only in production where a real
        # socket path was supplied. Tests pass `socket_path=None` and use
        # the sync fallback against the injected mock client.
        self._worker_thread: QThread | None = None
        self._worker: _LeaseWorker | None = None
        if socket_path is not None:
            self._worker_thread = QThread()
            self._worker = _LeaseWorker(socket_path)
            self._worker.moveToThread(self._worker_thread)
            self._request_take.connect(self._worker.do_take)
            self._request_renew.connect(self._worker.do_renew)
            self._request_release.connect(self._worker.do_release)
            self._worker.take_completed.connect(self._on_take_completed)
            self._worker.renew_completed.connect(self._on_renew_completed)
            self._worker.release_completed.connect(self._on_release_completed)
            self._worker_thread.start()

    @property
    def lease_id(self) -> str | None:
        return self._lease_id

    @property
    def is_held(self) -> bool:
        return self._lease_id is not None

    def acquire(self) -> bool:
        """Take the hwmon lease.

        Worker mode (production): the HTTP call is queued onto the worker
        thread; the actual result arrives later via `lease_acquired` or
        `lease_lost`. Returns `True` to indicate the request was queued
        (or that the lease is already held), `False` only if there is no
        lease to take and no way to request one.

        Sync mode (tests, no `socket_path`): the call happens inline and
        the return value reflects the immediate daemon response.
        """
        if self._lease_id:
            return True
        if self._worker is not None:
            if self._take_in_flight:
                # Duplicate request — silently coalesce.
                return True
            self._take_in_flight = True
            self._request_take.emit("gui")
            return True
        # Sync fallback (tests).
        try:
            result = self._client.hwmon_lease_take("gui", timeout=LEASE_API_TIMEOUT_S)
            self._lease_id = result.lease_id
            self._renew_timer.start()
            log.info("Lease acquired: %s (ttl=%ds)", result.lease_id, result.ttl_seconds)
            self.lease_acquired.emit(result.lease_id)
            return True
        except DaemonError as e:
            reason = f"{e.code}: {e.message}"
            log.warning("Lease acquire failed: %s", reason)
            self.lease_lost.emit(reason)
            return False

    def release(self) -> None:
        """Release the hwmon lease if held.

        Internal state (`_lease_id`, renew timer) is cleared synchronously
        so `is_held` is immediately false; the HTTP call itself happens on
        the worker thread (worker mode) or inline (sync mode).
        """
        if not self._lease_id:
            return
        self._renew_timer.stop()
        lease_id = self._lease_id
        self._lease_id = None
        if self._worker is not None:
            self._request_release.emit(lease_id)
            return
        # Sync fallback.
        try:
            self._client.hwmon_lease_release(lease_id, timeout=LEASE_API_TIMEOUT_S)
            log.info("Lease released: %s", lease_id)
        except DaemonError as e:
            log.warning("Lease release failed: %s", e.message)

    def _renew(self) -> None:
        """Renew the lease periodically with retry on failure (P0-G2).

        In worker mode the HTTP call is queued onto the worker thread and
        the response is handled in `_on_renew_completed`. In sync mode the
        call is inline.

        Retry-vs-recurring fairness: while a retry chain (5 s/10 s/15 s
        backoff) is in flight the recurring 30 s renew timer is stopped so
        a recurring tick cannot race the backoff retry and double up the
        ``lease/renew`` API calls. The recurring timer is restarted once a
        retry succeeds.
        """
        if not self._lease_id:
            self._renew_timer.stop()
            return
        if self._worker is not None:
            if self._renew_in_flight:
                # A previous renew is still pending — skip this tick.
                # The pending request will complete on the worker thread
                # and either succeed (clearing the flag) or fail (entering
                # the backoff path).
                return
            self._renew_in_flight = True
            self._request_renew.emit(self._lease_id)
            return
        # Sync fallback.
        try:
            result = self._client.hwmon_lease_renew(self._lease_id, timeout=LEASE_API_TIMEOUT_S)
            self._on_renew_success(result.lease_id, result.ttl_seconds)
        except DaemonError as e:
            self._on_renew_failure(f"{e.code}: {e.message}", e.message)

    @Slot(bool, str, int, str)
    def _on_take_completed(self, success: bool, lease_id: str, ttl: int, err: str) -> None:
        self._take_in_flight = False
        if success:
            self._lease_id = lease_id
            self._renew_timer.start()
            log.info("Lease acquired: %s (ttl=%ds)", lease_id, ttl)
            self.lease_acquired.emit(lease_id)
        else:
            log.warning("Lease acquire failed: %s", err)
            self.lease_lost.emit(err)

    @Slot(bool, str, int, str)
    def _on_renew_completed(self, success: bool, lease_id: str, ttl: int, err: str) -> None:
        self._renew_in_flight = False
        # If the lease was released (or fully lost) while a renew was in
        # flight the response is stale — discard it. Without this guard a
        # late success could spuriously set `_lease_id` back to a value
        # that no longer represents an active lease on the daemon side.
        if not self._lease_id:
            return
        if success:
            self._on_renew_success(lease_id, ttl)
        else:
            # `err` already includes the code + message from the worker.
            # Pass the same string for both the lease_lost reason and the
            # per-retry log line so behaviour matches the sync path.
            self._on_renew_failure(err, err)

    @Slot(bool, str)
    def _on_release_completed(self, success: bool, err: str) -> None:
        if success:
            log.info("Lease released")
        else:
            log.warning("Lease release failed: %s", err)

    def _on_renew_success(self, lease_id: str, ttl: int) -> None:
        self._lease_id = lease_id
        was_in_retry = self._renew_retry_count > 0
        self._renew_retry_count = 0
        log.debug("Lease renewed: %s (ttl=%ds)", lease_id, ttl)
        # If a retry just succeeded, the recurring timer was stopped at
        # failure time — restart it so periodic renewal resumes.
        if was_in_retry and not self._renew_timer.isActive():
            self._renew_timer.start()
        self.lease_renewed.emit(lease_id)

    def _on_renew_failure(self, lose_reason: str, retry_log_msg: str) -> None:
        """Common renew-failure handler used by both sync and worker paths.

        `lose_reason` is the string emitted as the `lease_lost` payload once
        retries are exhausted; `retry_log_msg` is the per-retry log line.
        They differ only because the sync path uses the bare `DaemonError`
        message for the log line and the formatted `code: message` for the
        eventual lease_lost reason (matching pre-DEC-108 behaviour exactly).
        """
        self._renew_retry_count += 1
        if self._renew_retry_count <= self._MAX_RENEW_RETRIES:
            # Suspend the recurring timer for the duration of the retry
            # chain. Without this, a 30 s tick can fire between two 5 s
            # backoff retries and produce concurrent renew API calls.
            self._renew_timer.stop()
            backoff_ms = self._renew_retry_count * 5000
            log.warning(
                "Lease renewal failed (retry %d/%d in %dms): %s",
                self._renew_retry_count,
                self._MAX_RENEW_RETRIES,
                backoff_ms,
                retry_log_msg,
            )
            QTimer.singleShot(backoff_ms, self._renew)
            return
        # All retries exhausted — lease truly lost.
        log.error(
            "Lease renewal failed after %d retries: %s",
            self._MAX_RENEW_RETRIES,
            retry_log_msg,
        )
        self._lease_id = None
        self._renew_timer.stop()
        self._renew_retry_count = 0
        self.lease_lost.emit(
            f"renewal failed after {self._MAX_RENEW_RETRIES} retries: {lose_reason}"
        )

    def shutdown(self) -> None:
        """Release lease, stop timers, join worker thread.

        Order matters (P2-C): we ask the worker to release first, then quit
        the worker's event loop and wait for it to drain. Closing the
        worker's `DaemonClient` happens AFTER `wait()` so we never mutate
        worker state from the main thread while the worker is still alive.
        """
        self.release()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            if not self._worker_thread.wait(2000):
                log.warning("Lease worker thread did not stop within 2s, terminating")
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)
        if self._worker is not None:
            self._worker.close_client()
