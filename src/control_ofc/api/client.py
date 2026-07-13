"""Synchronous HTTP client for the control-ofc-daemon Unix socket API."""

from __future__ import annotations

from typing import Any

import httpx

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.api.models import (
    ActiveProfileInfo,
    Capabilities,
    DaemonStatus,
    FanReading,
    GpuFanResetResult,
    GpuVerifyResult,
    HardwareDiagnosticsResult,
    HardwareReadiness,
    HwmonHeader,
    HwmonInventory,
    HwmonVerifyResult,
    IdentifyResult,
    InventoryReadiness,
    OverrideGrant,
    OverrideReleaseResult,
    OverrideRenewResult,
    PreferredSensorResult,
    ProfileActivateResult,
    ProfileDeactivateResult,
    ProfileSearchDirsResult,
    SensorHistory,
    SensorReading,
    StartupDelayResult,
    SuperIoReport,
    parse_active_profile,
    parse_capabilities,
    parse_fans,
    parse_gpu_fan_reset,
    parse_gpu_verify_result,
    parse_hardware_diagnostics,
    parse_hardware_readiness,
    parse_hwmon_headers,
    parse_hwmon_inventory,
    parse_hwmon_verify_result,
    parse_identify_result,
    parse_inventory_readiness,
    parse_override_grant,
    parse_override_release,
    parse_override_renew,
    parse_preferred_sensor,
    parse_profile_activate,
    parse_profile_deactivate,
    parse_profile_search_dirs,
    parse_sensor_history,
    parse_sensors,
    parse_startup_delay,
    parse_status,
    parse_superio_report,
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

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def close(self) -> None:
        self._client.close()

    # -- helpers --
    #
    # ``timeout`` is per-call: pass an explicit value for endpoints whose
    # daemon-side latency is known to exceed the global default (verify is
    # ~3 s plus IPC; calibrate is ``(steps + 1) * hold_seconds``). Per-call
    # timeouts reuse the connection pool — see HTTPX docs:
    #   https://www.python-httpx.org/advanced/timeouts/
    # ``httpx.TimeoutException`` is now mapped to ``DaemonTimeout`` so the
    # UI can distinguish "daemon is slow" from "daemon is gone" — a verify
    # call that times out client-side may still have completed on the daemon.

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Issue one request and map transport faults to daemon errors.

        Dispatches via ``httpx.Client.request`` so a DELETE can carry a body
        (override release sends ``override_token``) and every verb shares one
        code path. The error mapping is identical for all verbs:

        - ``TimeoutException`` -> ``DaemonTimeout`` ("daemon is slow" — a verify
          that times out client-side may still have completed daemon-side).
        - ``ConnectError`` and every other ``RequestError`` -> ``DaemonUnavailable``
          ("the daemon is gone", retryable). ``RequestError`` is httpx's
          documented base for any fault raised while issuing a request — a
          ``RemoteProtocolError`` on mid-body death (the common case when the
          daemon is SIGKILLed/restarts) or a partial-I/O ``ReadError``/
          ``WriteError`` — so it routes into the same disconnect/reconnect
          handling as ``ConnectError``: the control-loop write worker emits
          ``OUTCOME_UNAVAILABLE`` and drops the client for a clean reconnect.
          ``InvalidURL`` and other non-``RequestError`` httpx faults are our own
          bugs and surface raw.
        """
        try:
            kwargs: dict[str, Any] = {}
            if json is not None:
                kwargs["json"] = json
            if params is not None:
                kwargs["params"] = params
            if timeout is not None:
                kwargs["timeout"] = timeout
            resp = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise DaemonTimeout(message=str(e), endpoint=path, method=method) from e
        except httpx.ConnectError as e:
            raise DaemonUnavailable(message=str(e), endpoint=path, method=method) from e
        except httpx.RequestError as e:
            raise DaemonUnavailable(message=str(e), endpoint=path, method=method) from e
        return self._handle(resp, method, path)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, timeout=timeout)

    def _post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, json=json, params=params, timeout=timeout)

    def _put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._request("PUT", path, json=json, timeout=timeout)

    def _delete(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._request("DELETE", path, json=json, timeout=timeout)

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

    def poll(self) -> tuple[DaemonStatus, list[SensorReading], list[FanReading]]:
        """Batch read: status + sensors + fans in one call."""
        data = self._get("/poll")
        status = parse_status(data.get("status", {}))
        sensors = parse_sensors(data)
        fans = parse_fans(data)
        return status, sensors, fans

    def sensor_history(self, entity_id: str, last: int = 250) -> SensorHistory:
        """GET /sensors/history — retrieve recent history for a sensor.

        ``entity_id`` is a sysfs-derived string (``hwmon:{chip}:{device}:{label}``)
        and may contain query-special characters, so it is passed via httpx
        ``params=`` (percent-encoded) rather than interpolated into the path.
        """
        data = self._get("/sensors/history", params={"id": entity_id, "last": last})
        return parse_sensor_history(data)

    # -- write endpoints --

    def hwmon_rescan(self) -> list[HwmonHeader]:
        """POST /hwmon/rescan — re-enumerate hwmon devices and return fresh headers.

        The daemon also flags its sensor polling loop to rebuild the cached
        descriptor set on the next tick (DEC-133), so newly loaded sensor
        chips appear through the normal 1 Hz poll within ~2 s. PWM *control*
        hardware added after daemon startup still requires a daemon restart.
        Removed as dead code in v1.14.1; restored for the Diagnostics
        "Rescan Hardware" button (DEC-147).
        """
        data = self._post("/hwmon/rescan")
        return parse_hwmon_headers(data)

    def activate_profile(
        self,
        profile_path: str | None = None,
        *,
        profile_id: str | None = None,
    ) -> ProfileActivateResult:
        """POST /profile/activate — activate a profile by path or id.

        Exactly one of ``profile_path`` or ``profile_id`` must be provided.
        The GUI normally passes ``profile_path`` (canonical for on-disk
        profiles); ``profile_id`` is resolved by the daemon as a filename
        stem across every registered profile search dir (the daemon store
        under ``/var/lib/control-ofc/profiles/`` first) — there is no
        separate "bundled" profile set.
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

    def inventory_hwmon(self) -> HwmonInventory:
        """GET /inventory/hwmon — classified temp sensors, the default-CPU
        recommendation, and the persisted preferred-sensor selections (DEC-200).

        Additive/read-only. Raises ``DaemonError`` with ``.status == 404`` on a
        daemon that predates the endpoint — callers hide the new UI in that case.
        """
        return parse_hwmon_inventory(self._get("/inventory/hwmon"))

    def inventory_readiness(self) -> InventoryReadiness:
        """GET /inventory/readiness — the daemon's structured hardware-readiness
        items with an ``overall`` rollup (DEC-200). 404 on pre-2.6 daemons."""
        return parse_inventory_readiness(self._get("/inventory/readiness"))

    def hardware_readiness(self, force: bool = False) -> HardwareReadiness:
        """GET /inventory/hardware-readiness — the combined readiness + Super-I/O
        snapshot for the merged "Cooling Hardware Readiness" page (DEC-207), all
        from ONE shared daemon scan. ``force`` (the page's "Refresh hardware
        assessment" action) requests a fresh scan via ``?refresh=true``; otherwise
        the daemon serves its cached assessment. Read-only. Raises ``DaemonError``
        with ``.status == 404`` on a daemon that predates the endpoint — the page
        shows an "unavailable" state in that case.
        """
        params = {"refresh": "true"} if force else None
        return parse_hardware_readiness(self._get("/inventory/hardware-readiness", params=params))

    def superio_detect(self) -> SuperIoReport:
        """GET /inventory/superio — passive Super-I/O chip detection with
        allowlisted "load this driver" recommendations (DEC-202).

        Read-only; the daemon never probes I/O ports, loads modules, or writes
        hardware. Raises ``DaemonError`` with ``.status == 404`` on a daemon that
        predates the endpoint — callers hide the Super-I/O panel in that case.
        """
        return parse_superio_report(self._get("/inventory/superio"))

    def superio_probe(self) -> SuperIoReport:
        """POST /inventory/superio/probe — the opt-in ACTIVE Super-I/O probe
        (DEC-203). A deliberate one-shot `/dev/port` read that identifies an
        unbound chip. Off by default daemon-side: when disabled it returns the
        normal report with ``port_probe_available == False`` (it does not error).
        404 on daemons predating the endpoint.
        """
        return parse_superio_report(self._post("/inventory/superio/probe", json={}))

    def set_preferred_cpu_sensor(self, sensor_id: str | None) -> PreferredSensorResult:
        """POST /config/preferred-cpu-sensor — pin (id) or clear (``None``) the
        preferred CPU temperature sensor (DEC-200). Validated daemon-side against
        the live sensor set (unknown id → 400); advisory only."""
        return parse_preferred_sensor(
            self._post("/config/preferred-cpu-sensor", json={"sensor_id": sensor_id})
        )

    def set_preferred_mb_sensor(self, sensor_id: str | None) -> PreferredSensorResult:
        """POST /config/preferred-mb-sensor — pin (id) or clear (``None``) the
        preferred motherboard temperature sensor (DEC-200)."""
        return parse_preferred_sensor(
            self._post("/config/preferred-mb-sensor", json={"sensor_id": sensor_id})
        )

    def verify_hwmon_pwm(self, header_id: str) -> HwmonVerifyResult:
        """POST /hwmon/{header_id}/verify — test PWM write effectiveness (~6s).

        Daemon-performed (DEC-165): the daemon pauses its engine, force-takes a
        short-lived internal "verify" lease, drives the test write + readback,
        and restores — the GUI no longer holds or passes an hwmon lease (the
        endpoint takes no body). The daemon sleeps ``VERIFY_WAIT_SECONDS = 6 s``
        between the test write and the readback (DEC-101); worst-case round-trip
        under load is ~7.5 s, so we send a 12 s per-call timeout regardless of
        the global default. See DEC-098 / DEC-101 / DEC-165.
        """
        data = self._post(f"/hwmon/{header_id}/verify", timeout=12.0)
        return parse_hwmon_verify_result(data)

    def active_profile(self) -> ActiveProfileInfo | None:
        """GET /profile/active — query the daemon's currently active profile."""
        data = self._get("/profile/active")
        return parse_active_profile(data)

    def deactivate_profile(self) -> ProfileDeactivateResult:
        """POST /profile/deactivate — clear the daemon's active profile.

        Idempotent: deactivating when no profile is active is a success
        no-op. Returns the previously-active profile id/name (both None
        when there was nothing to deactivate). The daemon falls back to
        imperative-only behaviour after this call until a new profile is
        activated. See DEC-097.
        """
        return parse_profile_deactivate(self._post("/profile/deactivate", json={}))

    def create_profile(self, document: dict[str, Any]) -> dict[str, Any]:
        """POST /profiles — create a daemon-stored profile from a full document.

        The document must carry a stable ``id``; the daemon keys its store on
        it. Returns the daemon's success body (``{profile_id, created,
        warnings, ...}``). Raises ``DaemonError`` on failure, distinguished by
        ``.status``: 409 ``already_exists`` (id already in the store) and 400
        ``validation_error`` (``field_violations`` in ``.details``) are the
        cases the one-time profile import branches on (DEC-161). Requires a
        daemon advertising ``control.profile_storage`` (≥ 1.19).
        """
        return self._post("/profiles", json=document)

    def list_profiles(self) -> list[dict[str, Any]]:
        """GET /profiles — list daemon-stored profile summaries.

        Each entry is a lightweight ``{id, name, description}`` summary (DEC-160)
        — it carries **no** ``controls`` or ``curves``. Fetch a profile's full
        document with :meth:`get_profile` (callers that need the body must
        hydrate per id — DEC-175).
        """
        data = self._get("/profiles")
        profiles = data.get("profiles", [])
        return profiles if isinstance(profiles, list) else []

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        """GET /profiles/{id} — fetch a stored profile's full (lossless) document.

        Returns the raw stored document at the top level (``id``/``name``/
        ``controls``/``curves``/…), ready for ``Profile.from_dict``. Raises
        ``DaemonError`` with status 404 ``validation_error`` if no profile has
        that id.
        """
        return self._get(f"/profiles/{profile_id}")

    def update_profile(self, profile_id: str, document: dict[str, Any]) -> dict[str, Any]:
        """PUT /profiles/{id} — replace a stored profile with a full document."""
        return self._put(f"/profiles/{profile_id}", json=document)

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        """DELETE /profiles/{id} — remove a stored profile."""
        return self._delete(f"/profiles/{profile_id}")

    def override_take(
        self,
        control_id: str,
        pwm_percent: int,
        *,
        ttl_secs: int | None = None,
        timeout: float | None = None,
    ) -> OverrideGrant:
        """POST /control/{id}/override — pin a control's members to a fixed PWM (DEC-163).

        Reverts to autonomous curve control if the GUI stops renewing (daemon
        deadman); renew at the grant's ``renew_secs``.
        """
        payload: dict[str, Any] = {"pwm_percent": pwm_percent}
        if ttl_secs is not None:
            payload["ttl_secs"] = ttl_secs
        return parse_override_grant(
            self._post(f"/control/{control_id}/override", json=payload, timeout=timeout)
        )

    def override_renew(
        self, control_id: str, override_token: int, *, timeout: float | None = None
    ) -> OverrideRenewResult:
        """POST /control/{id}/override/renew — extend an override before its TTL (DEC-163)."""
        return parse_override_renew(
            self._post(
                f"/control/{control_id}/override/renew",
                json={"override_token": override_token},
                timeout=timeout,
            )
        )

    def override_release(
        self, control_id: str, override_token: int, *, timeout: float | None = None
    ) -> OverrideReleaseResult:
        """DELETE /control/{id}/override — release an override, reverting to curve (DEC-163)."""
        return parse_override_release(
            self._delete(
                f"/control/{control_id}/override",
                json={"override_token": override_token},
                timeout=timeout,
            )
        )

    def fan_identify(
        self,
        fan_id: str,
        action: str,
        *,
        ttl_secs: int | None = None,
        timeout: float | None = None,
    ) -> IdentifyResult:
        """POST /fans/{id}/identify — stop/restore one fan for identification (DEC-166).

        ``action`` is ``"stop"`` (forces the fan to 0 with a deadman auto-restore)
        or ``"restore"``. Only the named fan is affected; others keep curve control.
        """
        payload: dict[str, Any] = {"action": action}
        if ttl_secs is not None:
            payload["ttl_secs"] = ttl_secs
        return parse_identify_result(
            self._post(f"/fans/{fan_id}/identify", json=payload, timeout=timeout)
        )

    def reset_gpu_fan(self, gpu_id: str, *, timeout: float | None = None) -> GpuFanResetResult:
        """POST /gpu/{gpu_id}/fan/reset — reset GPU fan to automatic mode."""
        data = self._post(f"/gpu/{gpu_id}/fan/reset", json={}, timeout=timeout)
        return parse_gpu_fan_reset(data)

    def verify_gpu_fan(self, gpu_id: str) -> GpuVerifyResult:
        """POST /gpu/{gpu_id}/fan/verify — test GPU fan-control effectiveness (~6s).

        No lease (GPU writes never require one, DEC-045). The daemon drives a
        test speed, waits ``GPU_VERIFY_WAIT_SECONDS = 6 s``, reads back the
        applied curve + RPM, restores the prior state, and classifies the
        outcome. We send a 12 s per-call timeout to clear the 6 s wait plus
        round-trip overhead, matching ``verify_hwmon_pwm``. See DEC-120.
        """
        data = self._post(
            f"/gpu/{gpu_id}/fan/verify",
            json={},
            timeout=12.0,
        )
        return parse_gpu_verify_result(data)
