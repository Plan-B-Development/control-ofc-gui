"""Tests for hwmon lease lifecycle management."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import LeaseReleasedResult, LeaseResult
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
    mock_client.hwmon_lease_release.assert_called_once_with("lease-abc")


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
    mock_client.hwmon_lease_take.assert_called_once_with("gui")


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
