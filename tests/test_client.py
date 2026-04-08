"""Tests for the daemon IPC client and error handling."""

from __future__ import annotations

from control_ofc.api.errors import DaemonError, DaemonUnavailable


def test_daemon_error_fields():
    err = DaemonError(code="validation_error", message="bad input", status=400)
    assert err.code == "validation_error"
    assert err.status == 400
    assert str(err) == "bad input"


def test_daemon_error_retryable():
    err = DaemonError(code="hardware_unavailable", message="timeout", retryable=True)
    assert err.retryable is True


def test_daemon_unavailable_is_daemon_error():
    err = DaemonUnavailable(message="socket gone")
    assert isinstance(err, DaemonError)
    assert err.code == "daemon_unavailable"
    assert err.retryable is True


def test_default_socket_path():
    from control_ofc.constants import DEFAULT_SOCKET_PATH

    assert DEFAULT_SOCKET_PATH == "/run/control-ofc/control-ofc.sock"
