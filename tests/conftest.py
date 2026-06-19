"""Shared fixtures for GUI integration / click tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from control_ofc.api.errors import DaemonUnavailable
from control_ofc.api.models import (
    ActiveProfileInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    GpuFanResetResult,
    OperationMode,
    SensorHistory,
    SensorReading,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import ProfileService

# ---------------------------------------------------------------------------
# Fake daemon client that records calls and returns canned data
# ---------------------------------------------------------------------------


@dataclass
class _PersistentError:
    """Wrapper to distinguish persistent errors from one-shot errors in FakeDaemonClient."""

    exception: Exception


@dataclass
class FakeDaemonClient:
    """Drop-in replacement for DaemonClient that records calls.

    Supports error injection via ``simulate_error`` / ``simulate_unavailable``.
    """

    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    _errors: dict[str, Exception] = field(default_factory=dict)

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append((method, args, kwargs))

    def _maybe_raise(self, method: str) -> None:
        err = self._errors.get(method)
        if err is None:
            return
        if isinstance(err, _PersistentError):
            raise err.exception
        # One-shot: remove after first raise
        del self._errors[method]
        raise err

    def simulate_error(self, method: str, exception: Exception) -> None:
        """Configure *method* to raise *exception* on next call (one-shot)."""
        self._errors[method] = exception

    def simulate_persistent_error(self, method: str, exception: Exception) -> None:
        """Configure *method* to raise *exception* on every call until cleared."""
        self._errors[method] = _PersistentError(exception)

    def clear_errors(self) -> None:
        """Remove all injected errors."""
        self._errors.clear()

    def simulate_unavailable(self) -> None:
        """Set every method to persistently raise DaemonUnavailable."""
        exc = DaemonUnavailable()
        for name in (
            "capabilities",
            "status",
            "sensors",
            "fans",
            "hwmon_headers",
            "hwmon_rescan",
            "sensor_history",
        ):
            self._errors[name] = _PersistentError(exc)

    # -- read endpoints --

    def capabilities(self) -> Capabilities:
        self._record("capabilities")
        self._maybe_raise("capabilities")
        return Capabilities(daemon_version="0.2.0")

    def status(self) -> DaemonStatus:
        self._record("status")
        self._maybe_raise("status")
        return DaemonStatus(overall_status="ok", daemon_version="0.2.0")

    def sensors(self) -> list[SensorReading]:
        self._record("sensors")
        self._maybe_raise("sensors")
        return [
            SensorReading(
                id="hwmon:k10temp:0:Tctl",
                kind="CpuTemp",
                label="Tctl",
                value_c=45.0,
                source="hwmon",
                age_ms=100,
            ),
            SensorReading(
                id="hwmon:amdgpu:0:edge",
                kind="GpuTemp",
                label="edge",
                value_c=38.0,
                source="hwmon",
                age_ms=100,
            ),
        ]

    def fans(self) -> list[FanReading]:
        self._record("fans")
        self._maybe_raise("fans")
        return [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=1200, last_commanded_pwm=128, age_ms=100
            ),
            FanReading(
                id="openfan:ch01", source="openfan", rpm=1100, last_commanded_pwm=128, age_ms=100
            ),
        ]

    def poll(self) -> tuple:
        self._record("poll")
        self._maybe_raise("poll")
        return self.status(), self.sensors(), self.fans()

    def hwmon_headers(self) -> list:
        self._record("hwmon_headers")
        self._maybe_raise("hwmon_headers")
        return []

    def hwmon_rescan(self) -> list:
        self._record("hwmon_rescan")
        self._maybe_raise("hwmon_rescan")
        return []

    def sensor_history(self, entity_id: str, last: int = 250) -> SensorHistory:
        self._record("sensor_history", entity_id, last)
        self._maybe_raise("sensor_history")
        return SensorHistory(entity_id=entity_id, points=[])

    def active_profile(self) -> ActiveProfileInfo | None:
        self._record("active_profile")
        self._maybe_raise("active_profile")
        return ActiveProfileInfo(active=False)

    def reset_gpu_fan(self, gpu_id: str, *, timeout: float | None = None) -> GpuFanResetResult:
        del timeout
        self._record("reset_gpu_fan", gpu_id)
        self._maybe_raise("reset_gpu_fan")
        return GpuFanResetResult(gpu_id=gpu_id, reset=True)


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _neutralize_modals(monkeypatch):
    """Stop any modal dialog from blocking the test run.

    Per pytest-qt's note on modal dialogs, ``QDialog.exec()`` and the static
    ``QMessageBox`` / ``QFileDialog`` / ``QInputDialog`` helpers spin a nested
    event loop and block until the user responds — in a headless test that is
    forever (this is what hung the suite on the delete-profile confirmation).
    We patch each blocking entry point on its shared Qt class: every UI module
    does ``from PySide6.QtWidgets import QMessageBox`` (etc.), so they all
    reference the same class object and one patch covers every importer.

    Defaults are deliberately **safe**: confirmations are *declined*, file
    pickers return "cancelled", and ``exec()`` returns ``Rejected``. A test
    that unexpectedly pops a modal therefore fails by NOT performing the action
    rather than hanging or silently doing something destructive. Tests that
    exercise the accept path override the relevant method explicitly (e.g.
    ``monkeypatch.setattr(QMessageBox, "question", lambda *a, **k:
    QMessageBox.StandardButton.Yes)``) — that wins because it is applied after
    this fixture.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    sb = QMessageBox.StandardButton
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: sb.No, raising=False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False), raising=False)
    monkeypatch.setattr(
        QDialog, "exec", lambda self, *a, **k: QDialog.DialogCode.Rejected, raising=False
    )


@pytest.fixture()
def fake_client():
    return FakeDaemonClient()


@pytest.fixture()
def fake_client_unavailable():
    """FakeDaemonClient pre-configured to raise DaemonUnavailable on all calls."""
    client = FakeDaemonClient()
    client.simulate_unavailable()
    return client


@pytest.fixture()
def app_state():
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def settings_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return AppSettingsService()
