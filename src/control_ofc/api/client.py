"""Synchronous HTTP client for the control-ofc-daemon Unix socket API."""

from __future__ import annotations

from typing import Any

import httpx

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import (
    ActiveProfileInfo,
    CalibrationResult,
    Capabilities,
    DaemonStatus,
    FanReading,
    GpuFanResetResult,
    GpuFanSetResult,
    HardwareDiagnosticsResult,
    HwmonHeader,
    HwmonSetPwmResult,
    HwmonVerifyResult,
    LeaseReleasedResult,
    LeaseResult,
    LeaseState,
    ProfileActivateResult,
    ProfileSearchDirsResult,
    SensorHistory,
    SensorReading,
    SetPwmAllResult,
    SetPwmResult,
    StartupDelayResult,
    parse_active_profile,
    parse_calibration_result,
    parse_capabilities,
    parse_fans,
    parse_gpu_fan_reset,
    parse_gpu_fan_set,
    parse_hardware_diagnostics,
    parse_hwmon_headers,
    parse_hwmon_set_pwm,
    parse_hwmon_verify_result,
    parse_lease_released,
    parse_lease_result,
    parse_lease_status,
    parse_profile_activate,
    parse_profile_search_dirs,
    parse_sensor_history,
    parse_sensors,
    parse_set_pwm,
    parse_set_pwm_all,
    parse_startup_delay,
    parse_status,
)
from control_ofc.constants import API_TIMEOUT_S, DEFAULT_SOCKET_PATH

BASE_URL = "http://localhost"


class DaemonClient:
    """Synchronous client that talks to control-ofc-daemon over a Unix socket.

    Usage::

        client = DaemonClient()
        caps = client.capabilities()
        sensors = client.sensors()
        client.close()
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, timeout: float = API_TIMEOUT_S):
        transport = httpx.HTTPTransport(uds=socket_path)
        self._client = httpx.Client(transport=transport, base_url=BASE_URL, timeout=timeout)
        self._socket_path = socket_path

    def close(self) -> None:
        self._client.close()

    # -- helpers --

    def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = self._client.get(path)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise DaemonUnavailable(message=str(e)) from e
        return self._handle(resp, "GET", path)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=json)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise DaemonUnavailable(message=str(e)) from e
        return self._handle(resp, "POST", path)

    @staticmethod
    def _handle(resp: httpx.Response, method: str, path: str) -> dict[str, Any]:
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise DaemonError(
                code="parse_error",
                message=f"Non-JSON response from {method} {path}: {resp.text[:200]}",
                retryable=True,
                source="internal",
                status=resp.status_code,
                details=None,
                endpoint=path,
                method=method,
            ) from exc
        if resp.status_code >= 400:
            err = data.get("error", {})
            raise DaemonError(
                code=err.get("code", "unknown"),
                message=err.get("message", resp.text),
                retryable=err.get("retryable", False),
                source=err.get("source", ""),
                status=resp.status_code,
                details=err.get("details"),
                endpoint=path,
                method=method,
            )
        return data

    # -- read endpoints --

    def capabilities(self) -> Capabilities:
        return parse_capabilities(self._get("/capabilities"))

    def status(self) -> DaemonStatus:
        return parse_status(self._get("/status"))

    def sensors(self) -> list[SensorReading]:
        return parse_sensors(self._get("/sensors"))

    def fans(self) -> list[FanReading]:
        return parse_fans(self._get("/fans"))

    def hwmon_headers(self) -> list[HwmonHeader]:
        return parse_hwmon_headers(self._get("/hwmon/headers"))

    def hwmon_lease_status(self) -> LeaseState:
        return parse_lease_status(self._get("/hwmon/lease/status"))

    def poll(self) -> tuple[DaemonStatus, list[SensorReading], list[FanReading]]:
        """Batch read: status + sensors + fans in one call."""
        data = self._get("/poll")
        status = parse_status(data.get("status", {}))
        sensors = parse_sensors(data)
        fans = parse_fans(data)
        return status, sensors, fans

    def sensor_history(self, entity_id: str, last: int = 250) -> SensorHistory:
        """GET /sensors/history — retrieve recent history for a sensor."""
        data = self._get(f"/sensors/history?id={entity_id}&last={last}")
        return parse_sensor_history(data)

    # -- write endpoints --

    def set_openfan_pwm(self, channel: int, pwm_percent: int) -> SetPwmResult:
        data = self._post(f"/fans/openfan/{channel}/pwm", json={"pwm_percent": pwm_percent})
        return parse_set_pwm(data)

    def set_openfan_all_pwm(self, pwm_percent: int) -> SetPwmAllResult:
        data = self._post("/fans/openfan/pwm", json={"pwm_percent": pwm_percent})
        return parse_set_pwm_all(data)

    def hwmon_lease_take(self, owner_hint: str = "gui") -> LeaseResult:
        data = self._post("/hwmon/lease/take", json={"owner_hint": owner_hint})
        return parse_lease_result(data)

    def hwmon_lease_release(self, lease_id: str) -> LeaseReleasedResult:
        data = self._post("/hwmon/lease/release", json={"lease_id": lease_id})
        return parse_lease_released(data)

    def hwmon_lease_renew(self, lease_id: str) -> LeaseResult:
        data = self._post("/hwmon/lease/renew", json={"lease_id": lease_id})
        return parse_lease_result(data)

    def set_hwmon_pwm(self, header_id: str, pwm_percent: int, lease_id: str) -> HwmonSetPwmResult:
        data = self._post(
            f"/hwmon/{header_id}/pwm",
            json={"pwm_percent": pwm_percent, "lease_id": lease_id},
        )
        return parse_hwmon_set_pwm(data)

    def hwmon_rescan(self) -> list[HwmonHeader]:
        """POST /hwmon/rescan — re-enumerate hwmon devices and return fresh headers."""
        data = self._post("/hwmon/rescan")
        return parse_hwmon_headers(data)

    def calibrate_openfan(
        self, channel: int, steps: int = 10, hold_seconds: int = 5
    ) -> CalibrationResult:
        """POST /fans/openfan/{channel}/calibrate — run a PWM-to-RPM calibration sweep."""
        data = self._post(
            f"/fans/openfan/{channel}/calibrate",
            json={"steps": steps, "hold_seconds": hold_seconds},
        )
        return parse_calibration_result(data)

    def activate_profile(
        self,
        profile_path: str | None = None,
        *,
        profile_id: str | None = None,
    ) -> ProfileActivateResult:
        """POST /profile/activate — activate a profile by path or id.

        Exactly one of ``profile_path`` or ``profile_id`` must be provided.
        The GUI normally passes ``profile_path`` (canonical for on-disk
        profiles); ``profile_id`` is supported for daemon-bundled profiles
        and for symmetry with the daemon's documented contract (M8).
        """
        if (profile_path is None) == (profile_id is None):
            raise ValueError("activate_profile requires exactly one of profile_path or profile_id")
        payload = (
            {"profile_path": profile_path}
            if profile_path is not None
            else {"profile_id": profile_id}
        )
        return parse_profile_activate(self._post("/profile/activate", json=payload))

    def set_startup_delay(self, delay_secs: int) -> StartupDelayResult:
        """POST /config/startup-delay — set daemon startup delay (takes effect on restart)."""
        return parse_startup_delay(
            self._post("/config/startup-delay", json={"delay_secs": delay_secs})
        )

    def update_profile_search_dirs(self, add: list[str]) -> ProfileSearchDirsResult:
        """POST /config/profile-search-dirs — add directories to daemon's profile search path."""
        return parse_profile_search_dirs(
            self._post("/config/profile-search-dirs", json={"add": add})
        )

    def hardware_diagnostics(self) -> HardwareDiagnosticsResult:
        """GET /diagnostics/hardware — hardware readiness and driver diagnostics."""
        return parse_hardware_diagnostics(self._get("/diagnostics/hardware"))

    def verify_hwmon_pwm(self, header_id: str, lease_id: str) -> HwmonVerifyResult:
        """POST /hwmon/{header_id}/verify — test PWM write effectiveness (~3s)."""
        data = self._post(f"/hwmon/{header_id}/verify", json={"lease_id": lease_id})
        return parse_hwmon_verify_result(data)

    def active_profile(self) -> ActiveProfileInfo | None:
        """GET /profile/active — query the daemon's currently active profile."""
        data = self._get("/profile/active")
        return parse_active_profile(data)

    def set_gpu_fan_speed(self, gpu_id: str, speed_pct: int) -> GpuFanSetResult:
        """POST /gpu/{gpu_id}/fan/pwm — set GPU fan to a static speed percentage."""
        data = self._post(f"/gpu/{gpu_id}/fan/pwm", json={"speed_pct": speed_pct})
        return parse_gpu_fan_set(data)

    def reset_gpu_fan(self, gpu_id: str) -> GpuFanResetResult:
        """POST /gpu/{gpu_id}/fan/reset — reset GPU fan to automatic mode."""
        data = self._post(f"/gpu/{gpu_id}/fan/reset", json={})
        return parse_gpu_fan_reset(data)
