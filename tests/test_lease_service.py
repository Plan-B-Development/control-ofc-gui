"""Tests for hwmon lease lifecycle management."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import LeaseReleasedResult, LeaseResult
from control_ofc.constants import LEASE_API_TIMEOUT_S
from control_ofc.services.lease_service import LeaseService


@pytest.fixture()
def mock_client():
    client = MagicMock()
    client.hwmon_lease_take.return_value = LeaseResult(
        lease_id="lease-abc", owner_hint="gui", ttl_seconds=60
    )
    client.hwmon_lease_renew.return_value = LeaseResult(
        lease_id="lease-abc", owner_hint="gui", ttl_seconds=60
    )
    client.hwmon_lease_release.return_value = LeaseReleasedResult(released=True)
    return client


def test_acquire_success(mock_client, qtbot):
    svc = LeaseService(mock_client)
    with qtbot.waitSignal(svc.lease_acquired, timeout=1000):
        assert svc.acquire() is True
    assert svc.lease_id == "lease-abc"
    assert svc.is_held is True


def test_acquire_already_held(mock_client, qtbot):
    svc = LeaseService(mock_client)
    svc.acquire()
    # Second acquire should return True without calling take again
    assert svc.acquire() is True
    assert mock_client.hwmon_lease_take.call_count == 1


def test_acquire_failure(mock_client, qtbot):
    # POST /hwmon/lease/take force-takes unconditionally per DEC-049, so the
    # only realistic failure mode is 503 hardware_unavailable when no hwmon
    # controller is present (M14).
    mock_client.hwmon_lease_take.side_effect = DaemonError(
        code="hardware_unavailable", message="no hwmon controller"
    )
    svc = LeaseService(mock_client)
    with qtbot.waitSignal(svc.lease_lost, timeout=1000):
        assert svc.acquire() is False
    assert svc.is_held is False


def test_release(mock_client, qtbot):
    svc = LeaseService(mock_client)
    svc.acquire()
    svc.release()
    assert svc.is_held is False
    assert svc.lease_id is None
    # DEC-108: lease HTTP calls pass an explicit per-call timeout so a
    # hung daemon cannot stretch the main-thread block to API_TIMEOUT_S.
    mock_client.hwmon_lease_release.assert_called_once_with(
        "lease-abc", timeout=LEASE_API_TIMEOUT_S
    )


def test_release_when_not_held(mock_client):
    svc = LeaseService(mock_client)
    svc.release()  # should not raise
    mock_client.hwmon_lease_release.assert_not_called()


def test_renew_success(mock_client, qtbot):
    svc = LeaseService(mock_client)
    svc.acquire()
    with qtbot.waitSignal(svc.lease_renewed, timeout=1000):
        svc._renew()
    assert svc.is_held is True


def test_renew_failure_retries_then_drops_lease(mock_client, qtbot):
    """Renewal retries 3 times with backoff before declaring lease lost."""
    svc = LeaseService(mock_client)
    svc.acquire()
    mock_client.hwmon_lease_renew.side_effect = DaemonError(
        code="not_found", message="lease expired"
    )

    # First call schedules a retry — lease still held
    svc._renew()
    assert svc.is_held is True
    assert svc._renew_retry_count == 1

    # Simulate retries 2 and 3
    svc._renew()
    assert svc.is_held is True
    assert svc._renew_retry_count == 2

    svc._renew()
    assert svc.is_held is True
    assert svc._renew_retry_count == 3

    # 4th call exceeds max retries — lease truly lost
    with qtbot.waitSignal(svc.lease_lost, timeout=1000):
        svc._renew()
    assert svc.is_held is False
    assert svc._renew_retry_count == 0


def test_renew_retry_resets_on_success(mock_client, qtbot):
    """A successful renew after a failure resets the retry counter."""
    svc = LeaseService(mock_client)
    svc.acquire()

    # Fail once
    mock_client.hwmon_lease_renew.side_effect = DaemonError(code="not_found", message="temporary")
    svc._renew()
    assert svc._renew_retry_count == 1

    # Succeed on retry
    mock_client.hwmon_lease_renew.side_effect = None
    mock_client.hwmon_lease_renew.return_value = LeaseResult(
        lease_id="lease-abc", owner_hint="gui", ttl_seconds=60
    )
    with qtbot.waitSignal(svc.lease_renewed, timeout=1000):
        svc._renew()
    assert svc._renew_retry_count == 0
    assert svc.is_held is True


def test_daemon_unavailable_on_renew_retries(mock_client, qtbot):
    """DaemonUnavailable during renewal also retries before dropping lease."""
    from control_ofc.api.errors import DaemonUnavailable

    svc = LeaseService(mock_client)
    svc.acquire()
    assert svc.is_held is True

    mock_client.hwmon_lease_renew.side_effect = DaemonUnavailable()
    # Exhaust all retries (3 retries + 1 final call)
    svc._renew()
    svc._renew()
    svc._renew()
    with qtbot.waitSignal(svc.lease_lost, timeout=1000):
        svc._renew()
    assert svc.is_held is False


def test_shutdown_releases(mock_client, qtbot):
    svc = LeaseService(mock_client)
    svc.acquire()
    svc.shutdown()
    assert svc.is_held is False
    mock_client.hwmon_lease_release.assert_called_once()


def test_renew_failure_stops_recurring_timer(mock_client, qtbot):
    """Audit P2.6 regression: while the retry chain (5s/10s/15s backoff) is
    in flight, the recurring 30s renew timer must be stopped so a recurring
    tick cannot race the backoff retry and produce overlapping renew API
    calls. The recurring timer restarts only after a retry succeeds.
    """
    svc = LeaseService(mock_client)
    svc.acquire()
    assert svc._renew_timer.isActive(), "recurring timer must run after acquire"

    mock_client.hwmon_lease_renew.side_effect = DaemonError(
        code="hardware_unavailable", message="transient"
    )

    # First failure schedules the first backoff retry — recurring timer must
    # be stopped to avoid double-firing renews while the backoff is pending.
    svc._renew()
    assert svc._renew_retry_count == 1
    assert svc.is_held is True
    assert not svc._renew_timer.isActive(), "recurring timer must be suspended during retry chain"


def test_renew_retry_success_restarts_recurring_timer(mock_client, qtbot):
    """When a backoff retry succeeds, the recurring 30s timer must restart
    so periodic renewal continues. Without this, a recovered lease would
    never auto-renew again until the next manual call.
    """
    svc = LeaseService(mock_client)
    svc.acquire()

    # Force a failure → recurring timer stops, retry pending.
    mock_client.hwmon_lease_renew.side_effect = DaemonError(
        code="hardware_unavailable", message="transient"
    )
    svc._renew()
    assert not svc._renew_timer.isActive()
    assert svc._renew_retry_count == 1

    # Retry succeeds — recurring timer must come back up.
    mock_client.hwmon_lease_renew.side_effect = None
    mock_client.hwmon_lease_renew.return_value = LeaseResult(
        lease_id="lease-abc", owner_hint="gui", ttl_seconds=60
    )
    with qtbot.waitSignal(svc.lease_renewed, timeout=1000):
        svc._renew()

    assert svc._renew_retry_count == 0
    assert svc._renew_timer.isActive(), "recurring timer must restart after a retry succeeds"


def test_renew_retry_exhausted_leaves_timer_stopped(mock_client, qtbot):
    """When all retries fail, the recurring timer stays stopped — there is no
    lease to renew, and a recurring tick would just re-trigger the failure.
    """
    svc = LeaseService(mock_client)
    svc.acquire()
    mock_client.hwmon_lease_renew.side_effect = DaemonError(
        code="not_found", message="lease expired"
    )

    # Exhaust retries.
    svc._renew()
    svc._renew()
    svc._renew()
    with qtbot.waitSignal(svc.lease_lost, timeout=1000):
        svc._renew()

    assert svc.is_held is False
    assert not svc._renew_timer.isActive()


# ---------------------------------------------------------------------------
# T2 (test-tests audit): timer interval and owner string assertions.
#
# These constants and the "gui" literal are passed to the daemon and define
# the wire contract. Mutation testing showed:
#  - `LEASE_RENEW_INTERVAL_S * 1000` mutated to `* 100` or `/ 1000` survived
#    — no test asserted the actual ms interval.
#  - `client.hwmon_lease_take("gui")` mutated to take("XXguiXX") survived
#    — no test asserted the literal owner-hint string sent to the daemon.
# These tests lock both down.
# ---------------------------------------------------------------------------


def test_renew_timer_interval_matches_constant(mock_client):
    """LeaseService._renew_timer.interval() must equal LEASE_RENEW_INTERVAL_S * 1000.

    The constant is exposed in the daemon API contract: the GUI must renew
    *before* the 60s daemon TTL elapses, so changing this magic number
    without an audit could cause silent lease loss.
    """
    from control_ofc.constants import LEASE_RENEW_INTERVAL_S

    svc = LeaseService(mock_client)
    assert svc._renew_timer.interval() == LEASE_RENEW_INTERVAL_S * 1000
    # And LEASE_RENEW_INTERVAL_S must itself be < the daemon's 60s TTL
    # (locked in case someone bumps it past the safety margin).
    assert LEASE_RENEW_INTERVAL_S < 60, "renew interval must be < daemon's 60s lease TTL"


def test_acquire_sends_literal_gui_owner_hint(mock_client):
    """LeaseService.acquire() must call hwmon_lease_take with the literal "gui".

    The owner-hint is the string the daemon's profile engine checks against
    when deciding whether to defer hwmon writes (DEC-074: 'gui' takes priority
    over 'profile-engine'). Sending the wrong string would cause silent
    dual-writer conflicts.
    """
    svc = LeaseService(mock_client)
    svc.acquire()
    mock_client.hwmon_lease_take.assert_called_once_with("gui", timeout=LEASE_API_TIMEOUT_S)


def test_renew_starts_recurring_timer_with_correct_interval_on_acquire(mock_client):
    """After acquire() the recurring timer must be ACTIVE and using the
    full LEASE_RENEW_INTERVAL_S * 1000 ms cadence — not a zero/instant timer."""
    from control_ofc.constants import LEASE_RENEW_INTERVAL_S

    svc = LeaseService(mock_client)
    assert not svc._renew_timer.isActive(), "timer must not run before acquire"
    svc.acquire()
    assert svc._renew_timer.isActive(), "timer must run after successful acquire"
    # Interval should not have been altered by acquire().
    assert svc._renew_timer.interval() == LEASE_RENEW_INTERVAL_S * 1000


# ---------------------------------------------------------------------------
# DEC-108: worker-mode plumbing — HTTP calls run on a dedicated QThread so
# the Qt main thread never blocks on a contended daemon.
#
# These tests do NOT exercise the real DaemonClient HTTP path (that is
# covered by the daemon-side IPC integration tests + the sync-fallback
# branches above which share the same retry / signal logic). They verify:
#  - worker mode is engaged when socket_path is supplied,
#  - acquire() queues a take request via the request signal,
#  - the take_completed slot updates state and emits lease_acquired,
#  - duplicate take / renew requests are coalesced,
#  - shutdown() joins the worker thread before closing its client (P2-C).
# ---------------------------------------------------------------------------


def test_worker_mode_creates_thread_when_socket_path_given(mock_client, qtbot):
    """Supplying socket_path engages the worker-thread path."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        assert svc._worker is not None
        assert svc._worker_thread is not None
        assert svc._worker_thread.isRunning()
    finally:
        # Don't call full shutdown — the worker has a real DaemonClient and
        # the socket doesn't exist. Just quit the thread cleanly.
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_sync_mode_creates_no_thread_when_no_socket_path(mock_client):
    """Without socket_path, the legacy in-process path is used (tests)."""
    svc = LeaseService(mock_client)
    assert svc._worker is None
    assert svc._worker_thread is None


def test_worker_mode_acquire_queues_request_and_returns_true(mock_client, qtbot):
    """In worker mode, acquire() emits a request signal and returns True
    even though the HTTP call has not happened yet."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        with qtbot.waitSignal(svc._request_take, timeout=1000) as blocker:
            assert svc.acquire() is True
        assert blocker.args == ["gui"]
        assert svc._take_in_flight is True
        # The sync mock client is NOT consulted — the worker owns its own
        # DaemonClient pointing at the (non-existent) socket.
        mock_client.hwmon_lease_take.assert_not_called()
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_acquire_coalesces_duplicates(mock_client, qtbot):
    """Two acquire() calls before the first completes must only emit one
    take request — otherwise overlapping in-flight calls would race."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        emitted: list[str] = []
        svc._request_take.connect(lambda hint: emitted.append(hint))
        svc.acquire()
        svc.acquire()
        svc.acquire()
        # Pump the event loop briefly so any queued signals deliver before
        # we count emissions.
        qtbot.wait(10)
        assert emitted == ["gui"], f"duplicate acquire() must coalesce; got {emitted}"
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_take_completed_updates_state_and_emits_signal(mock_client, qtbot):
    """When the worker fires take_completed(success=True, ...), LeaseService
    must apply the new lease_id, start the renew timer, and emit
    lease_acquired."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        svc._take_in_flight = True
        with qtbot.waitSignal(svc.lease_acquired, timeout=1000) as blocker:
            svc._on_take_completed(True, "lease-xyz", 60, "")
        assert blocker.args == ["lease-xyz"]
        assert svc.is_held is True
        assert svc.lease_id == "lease-xyz"
        assert svc._renew_timer.isActive()
        assert svc._take_in_flight is False
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_take_completed_failure_emits_lease_lost(mock_client, qtbot):
    """A failed take_completed must emit lease_lost with the error and leave
    is_held False."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        svc._take_in_flight = True
        with qtbot.waitSignal(svc.lease_lost, timeout=1000) as blocker:
            svc._on_take_completed(False, "", 0, "hardware_unavailable: no chip")
        assert "no chip" in blocker.args[0]
        assert svc.is_held is False
        assert svc._take_in_flight is False
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_renew_coalesces_when_in_flight(mock_client, qtbot):
    """If a renew is still pending on the worker, the next renew tick must
    NOT enqueue a second request — otherwise overlapping renews race the
    daemon's lease table."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        svc._lease_id = "lease-orig"
        emitted: list[str] = []
        svc._request_renew.connect(lambda lid: emitted.append(lid))
        svc._renew()
        svc._renew()
        svc._renew()
        qtbot.wait(10)
        assert emitted == ["lease-orig"], f"in-flight renew must coalesce; got {emitted}"
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_stale_renew_response_after_release_is_discarded(mock_client, qtbot):
    """If release() is called while a renew is in flight, the late
    renew_completed must not resurrect _lease_id."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    try:
        svc._lease_id = "lease-orig"
        svc._renew_in_flight = True
        # User-triggered release clears the lease synchronously.
        svc._lease_id = None
        # Late success arrives from the worker — must be ignored.
        svc._on_renew_completed(True, "lease-new", 60, "")
        assert svc.is_held is False, "stale renew success after release must not re-acquire"
    finally:
        svc._worker_thread.quit()
        svc._worker_thread.wait(1000)


def test_worker_mode_shutdown_joins_thread_then_closes_client(mock_client, qtbot):
    """shutdown() ordering (P2-C): quit + wait the worker thread BEFORE
    closing its DaemonClient, so we never mutate worker state from the main
    thread while the worker is still alive."""
    svc = LeaseService(mock_client, socket_path="/tmp/control-ofc-test.sock")
    thread = svc._worker_thread
    worker = svc._worker
    assert thread is not None and worker is not None
    assert thread.isRunning()
    svc.shutdown()
    assert not thread.isRunning(), "worker thread must be joined by shutdown"
    # close_client zeroes the internal _client reference; calling it
    # explicitly should be idempotent after shutdown already invoked it.
    worker.close_client()
    assert worker._client is None
