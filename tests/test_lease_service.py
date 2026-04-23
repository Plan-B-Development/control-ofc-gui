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
