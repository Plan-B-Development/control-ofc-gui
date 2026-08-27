"""Tests for DaemonClient._handle() and typed response parsers (R64/R65)."""

from __future__ import annotations

import httpx
import pytest

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ProfileSearchDirsResult,
    parse_capabilities,
    parse_config_write,
    parse_hardware_diagnostics,
    parse_profile_search_dirs,
)

# ---------------------------------------------------------------------------
# DaemonClient._handle()
# ---------------------------------------------------------------------------


class TestHandle:
    """Unit tests for the static _handle() method."""

    def test_handle_non_json_response_raises_parse_error(self):
        resp = httpx.Response(
            502,
            text="Bad Gateway",
            headers={"content-type": "text/plain"},
        )
        with pytest.raises(DaemonError) as exc_info:
            DaemonClient._handle(resp, "GET", "/status")

        err = exc_info.value
        assert err.code == "parse_error"
        assert err.retryable is True
        assert err.source == "internal"
        assert err.status == 502
        assert err.endpoint == "/status"
        assert err.method == "GET"
        assert "Non-JSON response" in err.message
        assert "Bad Gateway" in err.message

    def test_handle_valid_json_returns_dict(self):
        resp = httpx.Response(200, json={"ok": True, "value": 42})
        result = DaemonClient._handle(resp, "GET", "/sensors")
        assert result == {"ok": True, "value": 42}

    def test_handle_error_response_raises_daemon_error(self):
        error_body = {
            "error": {
                "code": "validation_error",
                "message": "delay_secs must be >= 0",
                "retryable": False,
                "source": "validation",
                "details": {"field": "delay_secs"},
            }
        }
        resp = httpx.Response(400, json=error_body)
        with pytest.raises(DaemonError) as exc_info:
            DaemonClient._handle(resp, "POST", "/config/startup-delay")

        err = exc_info.value
        assert err.code == "validation_error"
        assert err.message == "delay_secs must be >= 0"
        assert err.retryable is False
        assert err.source == "validation"
        assert err.status == 400
        assert err.details == {"field": "delay_secs"}
        assert err.endpoint == "/config/startup-delay"
        assert err.method == "POST"

    def test_handle_error_response_missing_fields_uses_defaults(self):
        """When the error envelope has no nested fields, defaults kick in."""
        resp = httpx.Response(500, json={"error": {}})
        with pytest.raises(DaemonError) as exc_info:
            DaemonClient._handle(resp, "GET", "/caps")

        err = exc_info.value
        assert err.code == "unknown"
        assert err.retryable is False
        assert err.source == ""
        assert err.status == 500

    def test_handle_error_response_no_error_key(self):
        """A 4xx response without an 'error' key still raises with defaults."""
        resp = httpx.Response(404, json={"message": "not found"})
        with pytest.raises(DaemonError) as exc_info:
            DaemonClient._handle(resp, "GET", "/missing")

        err = exc_info.value
        assert err.code == "unknown"
        assert err.status == 404


# ---------------------------------------------------------------------------
# POST /config/startup-delay — parsed by the shared DEC-243 setter parser
#
# `parse_startup_delay` / `StartupDelayResult` were retired in DEC-285. The
# route predated the DEC-243 setter shape and answered with only `delay_secs`,
# which is why it needed its own parser — and why it could not go through the
# Settings page's single `_write_daemon_key` write path. The daemon now also
# sends `key`/`value`, so one parser covers every `POST /config/*`.
# ---------------------------------------------------------------------------


class TestParseStartupDelay:
    def test_new_daemon_reply_carries_the_shared_setter_shape(self):
        result = parse_config_write(
            {
                "updated": True,
                "key": "startup.delay_secs",
                "value": 15,
                "delay_secs": 15,
                "note": "Takes effect on next daemon restart",
            }
        )
        assert result.updated is True
        assert result.key == "startup.delay_secs"
        assert result.value == 15
        assert "restart" in result.note

    def test_pre_2_23_0_reply_still_parses(self):
        """An older daemon omits key/value. The caller supplies the key and only
        reads `note`, so the write path stays correct — it must not raise."""
        result = parse_config_write(
            {"updated": True, "delay_secs": 15, "note": "Takes effect on next daemon restart"}
        )
        assert result.updated is True
        assert result.key == ""
        assert result.value is None
        assert "restart" in result.note


# ---------------------------------------------------------------------------
# parse_profile_search_dirs
# ---------------------------------------------------------------------------


class TestParseProfileSearchDirs:
    def test_parse_profile_search_dirs(self):
        data = {
            "updated": True,
            "search_dirs": ["/etc/control-ofc/profiles", "/home/user/.config/control-ofc/profiles"],
        }
        result = parse_profile_search_dirs(data)
        assert isinstance(result, ProfileSearchDirsResult)
        assert result.updated is True
        assert result.search_dirs == [
            "/etc/control-ofc/profiles",
            "/home/user/.config/control-ofc/profiles",
        ]

    def test_parse_profile_search_dirs_defaults(self):
        result = parse_profile_search_dirs({})
        assert result.updated is False
        assert result.search_dirs == []

    def test_parse_profile_search_dirs_empty_list(self):
        data = {"updated": True, "search_dirs": []}
        result = parse_profile_search_dirs(data)
        assert result.updated is True
        assert result.search_dirs == []


# ---------------------------------------------------------------------------
# M11: pci_id / pci_bdf coalescing — both endpoints accept either name
# ---------------------------------------------------------------------------


class TestPciFieldCoalescing:
    """Daemon is transitioning to emit both names on both endpoints (M11)."""

    _BDF = "0000:03:00.0"

    def _caps_payload(self, **gpu_overrides) -> dict:
        gpu: dict = {
            "present": True,
            "model_name": "RX 9070 XT",
            "display_label": "9070XT",
            "fan_control_method": "pmfw_curve",
            "fan_write_supported": True,
        }
        gpu.update(gpu_overrides)
        return {
            "api_version": 1,
            "devices": {"amd_gpu": gpu},
        }

    def test_capabilities_accepts_pci_id(self):
        """Legacy name — what the daemon emits today."""
        caps = parse_capabilities(self._caps_payload(pci_id=self._BDF))
        assert caps.amd_gpu.pci_id == self._BDF

    def test_capabilities_accepts_pci_bdf_alias(self):
        """Forward compatibility: daemon switches to canonical name only."""
        caps = parse_capabilities(self._caps_payload(pci_bdf=self._BDF))
        assert caps.amd_gpu.pci_id == self._BDF

    def test_capabilities_prefers_pci_id_when_both_present(self):
        """During the transition the daemon emits both with the same value."""
        caps = parse_capabilities(self._caps_payload(pci_id=self._BDF, pci_bdf=self._BDF))
        assert caps.amd_gpu.pci_id == self._BDF

    def _diag_payload(self, **gpu_overrides) -> dict:
        gpu: dict = {
            "pci_device_id": 30032,
            "pci_revision": 192,
            "model_name": "RX 9070 XT",
            "fan_control_method": "pmfw_curve",
            "overdrive_enabled": True,
        }
        gpu.update(gpu_overrides)
        return {"api_version": 1, "gpu": gpu}

    def test_diagnostics_accepts_pci_bdf(self):
        """Legacy name — what diagnostics emits today."""
        result = parse_hardware_diagnostics(self._diag_payload(pci_bdf=self._BDF))
        assert result.gpu is not None
        assert result.gpu.pci_bdf == self._BDF

    def test_diagnostics_accepts_pci_id_alias(self):
        """Forward compatibility when daemon transitions to canonical name."""
        result = parse_hardware_diagnostics(self._diag_payload(pci_id=self._BDF))
        assert result.gpu is not None
        assert result.gpu.pci_bdf == self._BDF
