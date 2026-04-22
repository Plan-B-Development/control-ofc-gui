"""Shared fixtures for GUI integration / click tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from control_ofc.api.errors import DaemonUnavailable
from control_ofc.api.models import (
    ActiveProfileInfo,
    CalibrationResult,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    GpuFanResetResult,
    GpuFanSetResult,
    HwmonSetPwmResult,
    LeaseReleasedResult,
    LeaseResult,
    LeaseState,
    OperationMode,
    SensorHistory,
    SensorReading,
    SetPwmAllResult,
    SetPwmResult,
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
            "hwmon_lease_status",
            "sensor_history",
            "calibrate_openfan",
            "set_openfan_pwm",
            "set_openfan_all_pwm",
            "hwmon_lease_take",
            "hwmon_lease_release",
            "hwmon_lease_renew",
            "set_hwmon_pwm",
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

    def hwmon_lease_status(self) -> LeaseState:
        self._record("hwmon_lease_status")
        self._maybe_raise("hwmon_lease_status")
        return LeaseState()

    def set_openfan_pwm(self, channel: int, pwm_percent: int) -> SetPwmResult:
        self._record("set_openfan_pwm", channel, pwm_percent)
        self._maybe_raise("set_openfan_pwm")
        return SetPwmResult(channel=channel, pwm_percent=pwm_percent)

    def set_openfan_all_pwm(self, pwm_percent: int) -> SetPwmAllResult:
        self._record("set_openfan_all_pwm", pwm_percent)
        self._maybe_raise("set_openfan_all_pwm")
        return SetPwmAllResult(pwm_percent=pwm_percent)

    def hwmon_lease_take(self, owner_hint: str = "gui") -> LeaseResult:
        self._record("hwmon_lease_take", owner_hint)
        self._maybe_raise("hwmon_lease_take")
        return LeaseResult(lease_id="fake-lease", owner_hint=owner_hint)

    def hwmon_lease_release(self, lease_id: str) -> LeaseReleasedResult:
        self._record("hwmon_lease_release", lease_id)
        self._maybe_raise("hwmon_lease_release")
        return LeaseReleasedResult(released=True)

    def hwmon_lease_renew(self, lease_id: str) -> LeaseResult:
        self._record("hwmon_lease_renew", lease_id)
        self._maybe_raise("hwmon_lease_renew")
        return LeaseResult(lease_id=lease_id)

    def set_hwmon_pwm(self, header_id: str, pwm_percent: int, lease_id: str) -> HwmonSetPwmResult:
        self._record("set_hwmon_pwm", header_id, pwm_percent, lease_id)
        self._maybe_raise("set_hwmon_pwm")
        return HwmonSetPwmResult(header_id=header_id, pwm_percent=pwm_percent)

    def sensor_history(self, entity_id: str, last: int = 250) -> SensorHistory:
        self._record("sensor_history", entity_id, last)
        self._maybe_raise("sensor_history")
        return SensorHistory(entity_id=entity_id, points=[])

    def calibrate_openfan(
        self, channel: int, steps: int = 10, hold_seconds: int = 5
    ) -> CalibrationResult:
        self._record("calibrate_openfan", channel, steps, hold_seconds)
        self._maybe_raise("calibrate_openfan")
        return CalibrationResult(fan_id=f"openfan:ch{channel:02}")

    def active_profile(self) -> ActiveProfileInfo | None:
        self._record("active_profile")
        self._maybe_raise("active_profile")
        return ActiveProfileInfo(active=False)

    def set_gpu_fan_speed(self, gpu_id: str, speed_pct: int) -> GpuFanSetResult:
        self._record("set_gpu_fan_speed", gpu_id, speed_pct)
        self._maybe_raise("set_gpu_fan_speed")
        return GpuFanSetResult(gpu_id=gpu_id, speed_pct=speed_pct)

    def reset_gpu_fan(self, gpu_id: str) -> GpuFanResetResult:
        self._record("reset_gpu_fan", gpu_id)
        self._maybe_raise("reset_gpu_fan")
        return GpuFanResetResult(gpu_id=gpu_id, reset=True)


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


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
