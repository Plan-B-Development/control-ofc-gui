"""Regression tests for the 2026-05-06 audit remediation.

Covers DEC-098 (kernel_warnings parsing + popup, support-bundle additions)
and DEC-099 (per-call timeout, DaemonTimeout subclass, write outcome enum,
fake-daemon load test).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    KernelWarning,
    OperationMode,
    parse_capabilities,
)
from control_ofc.constants import API_TIMEOUT_S
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import (
    KERNEL_MODULE_FILTER,
    DiagnosticsService,
)

# ---------------------------------------------------------------------------
# DEC-098 — parse_capabilities populates kernel_warnings
# ---------------------------------------------------------------------------


class TestKernelWarningsParsing:
    """The daemon emits kernel_warnings as a list of dicts under amd_gpu."""

    def test_parses_critical_rdna_hang_warning(self):
        payload = {
            "api_version": 1,
            "daemon_version": "1.6.1",
            "ipc_transport": "uds/http",
            "devices": {
                "openfan": {"present": True, "channels": 10, "rpm_support": True},
                "hwmon": {"present": True, "pwm_header_count": 2},
                "amd_gpu": {
                    "present": True,
                    "display_label": "9070XT",
                    "model_name": "RX 9070 XT",
                    "fan_control_method": "pmfw_curve",
                    "pmfw_supported": True,
                    "fan_rpm_available": True,
                    "fan_write_supported": True,
                    "is_discrete": True,
                    "overdrive_enabled": True,
                    "kernel_warnings": [
                        {
                            "id": "rdna_hang_kernel_6_19_x",
                            "severity": "critical",
                            "message": "Kernel 6.19.7 is affected by ...",
                        }
                    ],
                },
                "aio_hwmon": {"present": False, "status": "unsupported"},
                "aio_usb": {"present": False, "status": "unsupported"},
            },
            "features": {},
            "limits": {"pwm_percent_min": 0, "pwm_percent_max": 100},
        }
        caps = parse_capabilities(payload)
        assert len(caps.amd_gpu.kernel_warnings) == 1
        kw = caps.amd_gpu.kernel_warnings[0]
        assert kw.id == "rdna_hang_kernel_6_19_x"
        assert kw.severity == "critical"
        assert kw.message.startswith("Kernel 6.19.7")

    def test_missing_field_yields_empty_list(self):
        """Older daemons without the field must not break the parser."""
        payload = {
            "api_version": 1,
            "daemon_version": "1.6.0",  # pre-DEC-098
            "ipc_transport": "uds/http",
            "devices": {
                "openfan": {"present": True, "channels": 10},
                "hwmon": {"present": True},
                "amd_gpu": {"present": True, "display_label": "9070XT"},
                "aio_hwmon": {"present": False, "status": "unsupported"},
                "aio_usb": {"present": False, "status": "unsupported"},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(payload)
        assert caps.amd_gpu.kernel_warnings == []

    def test_null_field_yields_empty_list(self):
        payload = {
            "api_version": 1,
            "daemon_version": "1.6.1",
            "ipc_transport": "uds/http",
            "devices": {
                "openfan": {},
                "hwmon": {},
                "amd_gpu": {
                    "present": True,
                    "display_label": "9070XT",
                    "kernel_warnings": None,  # daemon shouldn't emit this, but be tolerant
                },
                "aio_hwmon": {"present": False, "status": "unsupported"},
                "aio_usb": {"present": False, "status": "unsupported"},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(payload)
        assert caps.amd_gpu.kernel_warnings == []


# ---------------------------------------------------------------------------
# DEC-098 — support bundle additions
# ---------------------------------------------------------------------------


class TestSupportBundleKernelInfo:
    def test_collect_kernel_info_includes_uname_release(self):
        """Captures the running kernel release string for triage."""
        info = DiagnosticsService.collect_kernel_info()
        # release should be a non-empty string on Linux
        assert info["release"] is not None
        assert len(info["release"]) > 0
        # cmdline / ppfeaturemask are best-effort — just assert keys exist
        assert "cmdline" in info
        assert "amdgpu_ppfeaturemask" in info

    def test_export_support_bundle_writes_kernel_section(self, tmp_path):
        svc = DiagnosticsService()
        out = tmp_path / "bundle.json"
        svc.export_support_bundle(out)

        data = json.loads(out.read_text())
        assert "kernel" in data["system"], data["system"]
        kernel = data["system"]["kernel"]
        assert "release" in kernel
        # Modules string is independent of journalctl access.
        assert "kernel_modules" in data["system"]

    def test_collect_kernel_modules_filters_to_known_drivers(self):
        """Filtered module list keeps the bundle small + focused."""
        out = DiagnosticsService.collect_kernel_modules()
        # Output is a string. If lsmod ran, every non-header line must
        # start with one of the filtered prefixes.
        for line in out.splitlines()[1:]:  # skip header
            if line.startswith("("):
                continue  # placeholder line ("no matching modules:...")
            stripped = line.strip()
            if not stripped:
                continue
            assert any(stripped.startswith(mod) for mod in KERNEL_MODULE_FILTER), (
                f"Unfiltered line in lsmod output: {line!r}"
            )


# ---------------------------------------------------------------------------
# DEC-099 — DaemonTimeout error subclass
# ---------------------------------------------------------------------------


class TestDaemonTimeoutSubclass:
    def test_daemon_timeout_is_distinct_from_unavailable(self):
        """DaemonTimeout must subclass DaemonError but NOT DaemonUnavailable.

        Catching DaemonUnavailable should not mask a DaemonTimeout — the UI
        message rewriting depends on this.
        """
        assert issubclass(DaemonTimeout, DaemonError)
        assert not issubclass(DaemonTimeout, DaemonUnavailable)
        assert not issubclass(DaemonUnavailable, DaemonTimeout)

    def test_daemon_timeout_default_fields(self):
        e = DaemonTimeout(message="took too long")
        assert e.code == "daemon_timeout"
        assert e.retryable is True
        assert e.source == "connection"
        assert "took too long" in str(e)


# ---------------------------------------------------------------------------
# DEC-099 — _post / _get plumb per-call timeout
# ---------------------------------------------------------------------------


class _FakeHttpClient:
    """httpx.Client stand-in that records the per-call timeout kwarg."""

    def __init__(self):
        self.last_post_timeout: object = None
        self.last_get_timeout: object = None

    def post(self, _path: str, *, json=None, **kwargs):
        self.last_post_timeout = kwargs.get("timeout", "<unset>")

        class _Resp:
            status_code = 200

            def json(self):
                return {}

        return _Resp()

    def get(self, _path: str, **kwargs):
        self.last_get_timeout = kwargs.get("timeout", "<unset>")

        class _Resp:
            status_code = 200

            def json(self):
                return {}

        return _Resp()

    def close(self):
        pass


def _make_client_with_fake_http() -> tuple[DaemonClient, _FakeHttpClient]:
    """Construct a DaemonClient backed by _FakeHttpClient (no real socket)."""
    client = DaemonClient.__new__(DaemonClient)
    fake = _FakeHttpClient()
    client._client = fake
    client._socket_path = "/tmp/test-fake.sock"
    client._default_timeout = API_TIMEOUT_S
    return client, fake


class TestPerCallTimeout:
    def test_post_without_timeout_omits_kwarg(self):
        client, fake = _make_client_with_fake_http()
        client._post("/test", json={})
        assert fake.last_post_timeout == "<unset>"

    def test_post_with_timeout_passes_kwarg(self):
        client, fake = _make_client_with_fake_http()
        client._post("/test", json={}, timeout=8.0)
        assert fake.last_post_timeout == 8.0

    def test_get_with_timeout_passes_kwarg(self):
        client, fake = _make_client_with_fake_http()
        client._get("/test", timeout=2.0)
        assert fake.last_get_timeout == 2.0

    def test_verify_hwmon_pwm_uses_8s_timeout(self):
        """The verify endpoint must override the global 5s default — daemon
        sleeps 3s and worst case is ~4.5s; 5s leaves no margin."""
        client, fake = _make_client_with_fake_http()
        # Stub the parse helper since the fake returns {}.
        with patch(
            "control_ofc.api.client.parse_hwmon_verify_result",
            return_value=MagicMock(),
        ):
            client.verify_hwmon_pwm("hwmon:test", "lease-id")
        assert fake.last_post_timeout == 8.0

    def test_calibrate_openfan_computes_dynamic_timeout(self):
        """Calibrate timeout = (steps + 1) * hold_seconds + 10s headroom."""
        client, fake = _make_client_with_fake_http()
        with patch(
            "control_ofc.api.client.parse_calibration_result",
            return_value=MagicMock(),
        ):
            client.calibrate_openfan(channel=0, steps=10, hold_seconds=5)
        # (10 + 1) * 5 + 10 = 65
        assert fake.last_post_timeout == 65.0


# ---------------------------------------------------------------------------
# DEC-099 — _post raises DaemonTimeout vs DaemonUnavailable distinctly
# ---------------------------------------------------------------------------


class _RaisingHttpClient:
    """Raises a configured exception on every call."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def post(self, *_args, **_kwargs):
        raise self._exc

    def get(self, *_args, **_kwargs):
        raise self._exc

    def close(self):
        pass


class TestPostRaisesCorrectExceptionType:
    def test_timeout_exception_maps_to_daemon_timeout(self):
        client = DaemonClient.__new__(DaemonClient)
        client._client = _RaisingHttpClient(httpx.ReadTimeout("read timed out"))
        client._socket_path = "/tmp/x.sock"
        client._default_timeout = API_TIMEOUT_S
        with pytest.raises(DaemonTimeout) as exc_info:
            client._post("/foo", json={})
        # Must NOT be a DaemonUnavailable — the categories are distinct.
        assert not isinstance(exc_info.value, DaemonUnavailable)
        assert exc_info.value.endpoint == "/foo"
        assert exc_info.value.method == "POST"

    def test_connect_error_maps_to_daemon_unavailable(self):
        client = DaemonClient.__new__(DaemonClient)
        client._client = _RaisingHttpClient(httpx.ConnectError("refused"))
        client._socket_path = "/tmp/x.sock"
        client._default_timeout = API_TIMEOUT_S
        with pytest.raises(DaemonUnavailable) as exc_info:
            client._post("/foo", json={})
        assert not isinstance(exc_info.value, DaemonTimeout)
        assert exc_info.value.endpoint == "/foo"


# ---------------------------------------------------------------------------
# DEC-099 — _WriteWorker outcome enum + retry-on-timeout
# ---------------------------------------------------------------------------


class TestWriteWorkerOutcomes:
    """Exercise _WriteWorker.do_write directly without QSignal plumbing."""

    def _make_worker_with_stub(self, stub_client):
        from control_ofc.services.control_loop import _WriteWorker

        worker = _WriteWorker(socket_path="/tmp/x.sock")
        worker._client = stub_client
        emitted: list[tuple[str, str]] = []
        worker.write_completed = MagicMock()
        worker.write_completed.emit = lambda tid, outcome: emitted.append((tid, outcome))
        return worker, emitted

    def test_successful_write_emits_ok(self):
        client = MagicMock(spec=DaemonClient)
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert emitted == [("openfan:ch00", "ok")]
        client.set_openfan_pwm.assert_called_once()

    def test_timeout_retries_once_then_emits_timeout(self):
        from control_ofc.services.control_loop import OUTCOME_TIMEOUT

        client = MagicMock(spec=DaemonClient)
        client.set_openfan_pwm.side_effect = [
            DaemonTimeout(message="first"),
            DaemonTimeout(message="second"),
        ]
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert client.set_openfan_pwm.call_count == 2
        assert emitted == [("openfan:ch00", OUTCOME_TIMEOUT)]

    def test_timeout_then_success_emits_ok(self):
        """Single timeout that recovers on retry — should look like success."""
        from control_ofc.services.control_loop import OUTCOME_OK

        client = MagicMock(spec=DaemonClient)
        client.set_openfan_pwm.side_effect = [
            DaemonTimeout(message="transient"),
            None,  # success on retry
        ]
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert client.set_openfan_pwm.call_count == 2
        assert emitted == [("openfan:ch00", OUTCOME_OK)]

    def test_unavailable_does_not_retry(self):
        from control_ofc.services.control_loop import OUTCOME_UNAVAILABLE

        client = MagicMock(spec=DaemonClient)
        client.set_openfan_pwm.side_effect = DaemonUnavailable(message="refused")
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert client.set_openfan_pwm.call_count == 1
        assert emitted == [("openfan:ch00", OUTCOME_UNAVAILABLE)]

    def test_4xx_emits_validation(self):
        from control_ofc.services.control_loop import OUTCOME_VALIDATION

        client = MagicMock(spec=DaemonClient)
        client.set_openfan_pwm.side_effect = DaemonError(
            code="validation_error",
            message="bad input",
            retryable=False,
            source="validation",
            status=400,
        )
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert emitted == [("openfan:ch00", OUTCOME_VALIDATION)]

    def test_5xx_emits_other(self):
        from control_ofc.services.control_loop import OUTCOME_OTHER

        client = MagicMock(spec=DaemonClient)
        client.set_openfan_pwm.side_effect = DaemonError(
            code="hardware_unavailable",
            message="serial down",
            retryable=True,
            source="hardware",
            status=503,
        )
        worker, emitted = self._make_worker_with_stub(client)
        worker.do_write("openfan:ch00", 50, "")
        assert emitted == [("openfan:ch00", OUTCOME_OTHER)]


# ---------------------------------------------------------------------------
# DEC-099 — fake-daemon integration test (D5 from audit)
#
# A real HTTP server on a Unix socket that holds requests for ~6 seconds.
# The GUI's write worker should:
#   1. Time out the first attempt (>2s)
#   2. Retry once (also times out)
#   3. Emit OUTCOME_TIMEOUT distinct from OUTCOME_UNAVAILABLE
# ---------------------------------------------------------------------------


class _SlowDaemonHandler(BaseHTTPRequestHandler):
    """Holds every POST for `hold_seconds` to force client-side timeouts."""

    hold_seconds = 6.0

    def do_POST(self):
        time.sleep(self.hold_seconds)
        body = b'{"api_version": 1, "channel": 0, "pwm_percent": 50}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence test output
        pass


class _UDSHttpServer(HTTPServer):
    """HTTPServer subclass that listens on a Unix domain socket."""

    address_family = socket.AF_UNIX

    def server_bind(self):
        # http.server expects (host, port); for AF_UNIX we just bind the path.
        self.socket.bind(self.server_address)
        # Mirror the daemon's chmod 0666 so the test client can connect.
        Path(self.server_address).chmod(0o666)

    def server_activate(self):
        self.socket.listen(8)

    def get_request(self):
        # AF_UNIX accept returns (sock, b'') instead of (sock, address)
        sock, _ = self.socket.accept()
        return sock, ("unix", 0)


@pytest.fixture
def slow_daemon(tmp_path):
    """Spin up a fake daemon that holds requests for 6 seconds."""
    sock_path = tmp_path / "fake-daemon.sock"

    server = _UDSHttpServer(str(sock_path), _SlowDaemonHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield str(sock_path)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


class TestFakeDaemonLoadIntegration:
    """End-to-end: GUI writes against a daemon that holds requests for 6s."""

    def test_slow_daemon_yields_timeout_outcome_not_unavailable(self, slow_daemon):
        """Distinct counters: this is timeout, not unavailability (DEC-099)."""
        from control_ofc.services.control_loop import (
            OUTCOME_TIMEOUT,
            OUTCOME_UNAVAILABLE,
            _WriteWorker,
        )

        worker = _WriteWorker(socket_path=slow_daemon)
        emitted: list[tuple[str, str]] = []
        worker.write_completed = MagicMock()
        worker.write_completed.emit = lambda tid, outcome: emitted.append((tid, outcome))

        # Real client against the fake-daemon UDS — uses the production
        # 2s WRITE_TIMEOUT_S per call, retries once on DaemonTimeout, then
        # surfaces OUTCOME_TIMEOUT. Total elapsed ~4-8s (two 2s-budgeted
        # waits, but httpx may report slightly later under contention).
        worker.do_write("openfan:ch00", 50, "")
        worker.shutdown()

        assert emitted, "worker should emit a result"
        target_id, outcome = emitted[0]
        assert target_id == "openfan:ch00"
        assert outcome == OUTCOME_TIMEOUT, (
            f"expected OUTCOME_TIMEOUT (slow daemon), got {outcome!r}"
        )
        assert outcome != OUTCOME_UNAVAILABLE


# ---------------------------------------------------------------------------
# DEC-099 — outcome-based warning messages in _on_write_completed
# ---------------------------------------------------------------------------


class TestOutcomeAwareWarningMessages:
    def _make_loop(self):
        from control_ofc.services.control_loop import ControlLoopService

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        profile_svc = MagicMock()
        client = MagicMock()
        return ControlLoopService(state=state, profile_service=profile_svc, client=client), state

    def test_timeout_warning_says_overloaded(self):
        from control_ofc.services.control_loop import OUTCOME_TIMEOUT

        svc, state = self._make_loop()
        target = "openfan:ch00"
        for _ in range(3):
            svc._on_write_completed(target, OUTCOME_TIMEOUT)

        warnings = [w for w in state._external_warnings if target in w.get("message", "")]
        assert len(warnings) == 1
        msg = warnings[0]["message"]
        assert "timed out" in msg.lower()
        assert "overload" in msg.lower()

    def test_unavailable_warning_says_connection_lost(self):
        from control_ofc.services.control_loop import OUTCOME_UNAVAILABLE

        svc, state = self._make_loop()
        target = "openfan:ch00"
        for _ in range(3):
            svc._on_write_completed(target, OUTCOME_UNAVAILABLE)

        warnings = [w for w in state._external_warnings if target in w.get("message", "")]
        assert len(warnings) == 1
        msg = warnings[0]["message"]
        assert "connection lost" in msg.lower()

    def test_validation_warning_says_lease_or_config(self):
        from control_ofc.services.control_loop import OUTCOME_VALIDATION

        svc, state = self._make_loop()
        target = "hwmon:nct6683:f1"
        for _ in range(3):
            svc._on_write_completed(target, OUTCOME_VALIDATION)

        warnings = [w for w in state._external_warnings if target in w.get("message", "")]
        assert len(warnings) == 1
        msg = warnings[0]["message"]
        assert "lease" in msg.lower() or "configuration" in msg.lower()


# ---------------------------------------------------------------------------
# DEC-098 — main_window kernel-warning popup (smoke test)
# ---------------------------------------------------------------------------


class TestKernelWarningPopupAcknowledgement:
    """The popup is gated on settings.acknowledged_kernel_warnings — we don't
    need to actually exec the QMessageBox here, just verify the gating
    logic so the user isn't pestered every reconnect."""

    def test_acknowledged_warning_skips_popup(self):
        from control_ofc.services.app_settings_service import (
            AppSettings,
            AppSettingsService,
        )

        settings_svc = AppSettingsService()
        settings_svc._settings = AppSettings(
            acknowledged_kernel_warnings=["rdna_hang_kernel_6_19_x"]
        )

        caps = Capabilities()
        caps.amd_gpu = AmdGpuCapability(
            present=True,
            kernel_warnings=[
                KernelWarning(
                    id="rdna_hang_kernel_6_19_x",
                    severity="critical",
                    message="ack'd",
                )
            ],
        )

        # Simulate the gating logic from main_window without instantiating Qt.
        gpu = caps.amd_gpu
        acknowledged = set(settings_svc.settings.acknowledged_kernel_warnings)
        unack = [
            w
            for w in gpu.kernel_warnings
            if w.id not in acknowledged and w.severity in ("high", "critical")
        ]
        assert unack == [], "acknowledged warning must be filtered out"

    def test_unacknowledged_critical_warning_qualifies(self):
        from control_ofc.services.app_settings_service import (
            AppSettings,
            AppSettingsService,
        )

        settings_svc = AppSettingsService()
        settings_svc._settings = AppSettings(acknowledged_kernel_warnings=[])

        caps = Capabilities()
        caps.amd_gpu = AmdGpuCapability(
            present=True,
            kernel_warnings=[
                KernelWarning(
                    id="smu_mismatch_navi48_r9700_kernel_7_0",
                    severity="critical",
                    message="r9700 mismatch",
                )
            ],
        )

        acknowledged = set(settings_svc.settings.acknowledged_kernel_warnings)
        unack = [
            w
            for w in caps.amd_gpu.kernel_warnings
            if w.id not in acknowledged and w.severity in ("high", "critical")
        ]
        assert len(unack) == 1
        assert unack[0].id == "smu_mismatch_navi48_r9700_kernel_7_0"

    def test_low_severity_warning_does_not_pop(self):
        """info/medium severities log only — never show a modal popup."""
        from control_ofc.services.app_settings_service import (
            AppSettings,
            AppSettingsService,
        )

        settings_svc = AppSettingsService()
        settings_svc._settings = AppSettings(acknowledged_kernel_warnings=[])

        caps = Capabilities()
        caps.amd_gpu = AmdGpuCapability(
            present=True,
            kernel_warnings=[
                KernelWarning(id="some_info", severity="info", message="fyi"),
                KernelWarning(id="some_medium", severity="medium", message="meh"),
            ],
        )

        acknowledged = set(settings_svc.settings.acknowledged_kernel_warnings)
        unack = [
            w
            for w in caps.amd_gpu.kernel_warnings
            if w.id not in acknowledged and w.severity in ("high", "critical")
        ]
        assert unack == []


# ---------------------------------------------------------------------------
# DEC-098 — AmdGpuGuidance lookup
# ---------------------------------------------------------------------------


class TestAmdGpuGuidanceLookup:
    def test_known_warning_id_returns_guidance(self):
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        guidance = lookup_amd_gpu_guidance("rdna_hang_kernel_6_19_x")
        assert guidance is not None
        assert guidance.summary
        assert guidance.references, "must include at least one reference URL"

    def test_unknown_warning_id_returns_none(self):
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        assert lookup_amd_gpu_guidance("totally_made_up") is None

    def test_empty_warning_id_returns_none(self):
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        assert lookup_amd_gpu_guidance("") is None
