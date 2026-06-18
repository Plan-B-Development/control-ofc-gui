"""Regression tests for the 2026-05-06 audit remediation.

Covers DEC-098 (kernel_warnings parsing + popup, support-bundle additions)
and DEC-099 (per-call timeout, DaemonTimeout subclass, write outcome enum,
fake-daemon load test).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    KernelWarning,
    parse_capabilities,
)
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
                            "id": "rdna_hang_kernel_6_18_6_19",
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
        assert kw.id == "rdna_hang_kernel_6_18_6_19"
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

    def test_verify_hwmon_pwm_uses_long_timeout(self):
        """The verify endpoint must override the global default — daemon
        sleeps 6 s (DEC-101, raised from 3 s) and worst case is ~7.5 s
        round-trip; 5 s would leave no margin. Asserts ≥ 9 s so a future
        regression that drops the timeout below the daemon wait is caught
        even if the literal value drifts within reason.
        """
        client, fake = _make_client_with_fake_http()
        # Stub the parse helper since the fake returns {}.
        with patch(
            "control_ofc.api.client.parse_hwmon_verify_result",
            return_value=MagicMock(),
        ):
            client.verify_hwmon_pwm("hwmon:test")
        assert isinstance(fake.last_post_timeout, float)
        assert fake.last_post_timeout >= 9.0, (
            f"verify_hwmon_pwm timeout={fake.last_post_timeout} must be ≥ 9 s "
            f"(daemon wait 6 s + slack). DEC-101."
        )


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
        with pytest.raises(DaemonUnavailable) as exc_info:
            client._post("/foo", json={})
        assert not isinstance(exc_info.value, DaemonTimeout)
        assert exc_info.value.endpoint == "/foo"


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
            acknowledged_kernel_warnings=["rdna_hang_kernel_6_18_6_19"]
        )

        caps = Capabilities()
        caps.amd_gpu = AmdGpuCapability(
            present=True,
            kernel_warnings=[
                KernelWarning(
                    id="rdna_hang_kernel_6_18_6_19",
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
                    id="smu_mismatch_navi48_r9700",
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
        assert unack[0].id == "smu_mismatch_navi48_r9700"

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

        guidance = lookup_amd_gpu_guidance("rdna_hang_kernel_6_18_6_19")
        assert guidance is not None
        assert guidance.summary
        assert guidance.references, "must include at least one reference URL"

    def test_renamed_ids_invalidate_old_acknowledgements(self):
        # DEC-114: the warning ids were renamed when their advice materially
        # changed (the 6.19-only hang warning recommended an also-affected
        # 6.18 kernel), so old acknowledgements no longer match and the GUI
        # re-prompts with the corrected guidance.
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        assert lookup_amd_gpu_guidance("rdna_hang_kernel_6_19_x") is None
        assert lookup_amd_gpu_guidance("smu_mismatch_navi48_r9700_kernel_7_0") is None
        assert lookup_amd_gpu_guidance("rdna_hang_kernel_6_18_6_19") is not None
        assert lookup_amd_gpu_guidance("smu_mismatch_navi48_r9700") is not None

    def test_unknown_warning_id_returns_none(self):
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        assert lookup_amd_gpu_guidance("totally_made_up") is None

    def test_empty_warning_id_returns_none(self):
        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        assert lookup_amd_gpu_guidance("") is None
