"""Tests for DiagnosticsService — event log, formatting, and support bundle export."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    LeaseState,
    OpenfanCapability,
    OperationMode,
    SensorReading,
    SubsystemStatus,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import (
    DiagnosticsService,
    format_uptime,
)

# ---------------------------------------------------------------------------
# format_uptime
# ---------------------------------------------------------------------------


class TestFormatUptime:
    def test_seconds_only(self):
        assert format_uptime(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_uptime(125) == "2m 5s"

    def test_hours_minutes_seconds(self):
        assert format_uptime(3661) == "1h 1m 1s"

    def test_zero(self):
        assert format_uptime(0) == "0s"

    def test_exact_hour(self):
        assert format_uptime(3600) == "1h 0m 0s"


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


class TestEventLog:
    def test_log_event_adds_to_events(self):
        svc = DiagnosticsService()
        svc.log_event("info", "test", "hello world")
        assert len(svc.events) == 1
        assert svc.events[0].message == "hello world"
        assert svc.events[0].level == "info"
        assert svc.events[0].source == "test"

    def test_clear_events(self):
        svc = DiagnosticsService()
        svc.log_event("info", "test", "msg1")
        svc.log_event("warning", "test", "msg2")
        assert len(svc.events) == 2
        svc.clear_events()
        assert len(svc.events) == 0

    def test_event_time_str_format(self):
        svc = DiagnosticsService()
        svc.log_event("info", "test", "msg")
        ts = svc.events[0].time_str
        assert len(ts) == 8  # HH:MM:SS
        assert ts[2] == ":" and ts[5] == ":"

    def test_max_events_bounded(self):
        svc = DiagnosticsService()
        for i in range(250):
            svc.log_event("info", "test", f"msg-{i}")
        assert len(svc.events) == 200  # MAX_EVENTS


# ---------------------------------------------------------------------------
# format_daemon_status
# ---------------------------------------------------------------------------


class TestFormatDaemonStatus:
    def test_no_state(self):
        svc = DiagnosticsService(state=None)
        assert "No application state" in svc.format_daemon_status()

    def test_with_state_and_capabilities(self):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        state.set_capabilities(Capabilities(daemon_version="1.4.0", api_version=1))
        state.set_status(
            DaemonStatus(
                overall_status="healthy",
                daemon_version="1.4.0",
                subsystems=[SubsystemStatus(name="openfan", status="ok", age_ms=500)],
            )
        )
        state.sensors = [
            SensorReading(
                id="s1", kind="CpuTemp", label="Tctl", value_c=45.0, source="hwmon", age_ms=100
            )
        ]

        svc = DiagnosticsService(state=state)
        text = svc.format_daemon_status()
        assert "connected" in text
        assert "1.4.0" in text
        assert "healthy" in text
        assert "Sensors: 1" in text

    def test_without_status(self):
        state = AppState()
        state.set_connection(ConnectionState.DISCONNECTED)
        svc = DiagnosticsService(state=state)
        text = svc.format_daemon_status()
        assert "not available" in text


# ---------------------------------------------------------------------------
# format_controller_status
# ---------------------------------------------------------------------------


class TestFormatControllerStatus:
    def test_no_state(self):
        svc = DiagnosticsService(state=None)
        assert "No application state" in svc.format_controller_status()

    def test_no_capabilities(self):
        state = AppState()
        svc = DiagnosticsService(state=state)
        text = svc.format_controller_status()
        assert "not yet received" in text

    def test_openfan_present(self):
        state = AppState()
        state.set_capabilities(
            Capabilities(openfan=OpenfanCapability(present=True, channels=8, write_support=True))
        )
        svc = DiagnosticsService(state=state)
        text = svc.format_controller_status()
        assert "Channels: 8" in text
        assert "Write support: Yes" in text

    def test_openfan_not_present(self):
        state = AppState()
        state.set_capabilities(Capabilities(openfan=OpenfanCapability(present=False)))
        svc = DiagnosticsService(state=state)
        text = svc.format_controller_status()
        assert "No OpenFan controller detected" in text


# ---------------------------------------------------------------------------
# format_gpu_status
# ---------------------------------------------------------------------------


class TestFormatGpuStatus:
    def test_gpu_present(self):
        state = AppState()
        state.set_capabilities(
            Capabilities(
                amd_gpu=AmdGpuCapability(
                    present=True,
                    model_name="RX 7900 XTX",
                    display_label="RX 7900 XTX",
                    fan_control_method="pmfw",
                    pmfw_supported=True,
                )
            )
        )
        svc = DiagnosticsService(state=state)
        text = svc.format_gpu_status()
        assert "RX 7900 XTX" in text
        assert "pmfw" in text
        assert "PMFW supported: Yes" in text

    def test_gpu_not_present(self):
        state = AppState()
        state.set_capabilities(Capabilities())
        svc = DiagnosticsService(state=state)
        text = svc.format_gpu_status()
        assert "No AMD discrete GPU" in text

    def test_gpu_fans_shown(self):
        state = AppState()
        state.set_capabilities(
            Capabilities(amd_gpu=AmdGpuCapability(present=True, model_name="RX 9070 XT"))
        )
        state.fans = [
            FanReading(
                id="amd_gpu:0000:2d:00.0",
                source="amd_gpu",
                rpm=1500,
                last_commanded_pwm=60,
                age_ms=200,
            )
        ]
        svc = DiagnosticsService(state=state)
        text = svc.format_gpu_status()
        assert "1500 RPM" in text
        assert "60%" in text

    def test_gpu_no_overdrive_shows_hint(self):
        state = AppState()
        state.set_capabilities(
            Capabilities(
                amd_gpu=AmdGpuCapability(
                    present=True,
                    overdrive_enabled=False,
                    pmfw_supported=False,
                )
            )
        )
        svc = DiagnosticsService(state=state)
        text = svc.format_gpu_status()
        assert "ppfeaturemask" in text


# ---------------------------------------------------------------------------
# fetch_journal_entries
# ---------------------------------------------------------------------------


class TestFetchJournalEntries:
    def test_journalctl_not_found(self):
        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            text = svc.fetch_journal_entries()
        assert "journalctl not found" in text

    def test_journalctl_timeout(self):
        import subprocess

        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="journalctl", timeout=5),
        ):
            text = svc.fetch_journal_entries()
        assert "timed out" in text

    def test_journalctl_success(self):
        mock_result = MagicMock()
        mock_result.stdout = "2024-01-01 daemon started\n2024-01-01 listening"
        mock_result.stderr = ""
        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", return_value=mock_result
        ):
            text = svc.fetch_journal_entries()
        assert "daemon started" in text

    def test_journalctl_empty_with_permission_error(self):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "Failed to get data: Permission denied"
        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", return_value=mock_result
        ):
            text = svc.fetch_journal_entries()
        assert "systemd-journal" in text


# ---------------------------------------------------------------------------
# export_support_bundle
# ---------------------------------------------------------------------------


class TestExportSupportBundle:
    def test_basic_export(self, tmp_path):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        state.set_capabilities(Capabilities(daemon_version="1.4.0"))
        state.set_status(DaemonStatus(overall_status="healthy", daemon_version="1.4.0"))
        state.lease = LeaseState(held=True, lease_id="test-lease")
        state.sensors = [
            SensorReading(
                id="s1", kind="CpuTemp", label="Tctl", value_c=45.0, source="hwmon", age_ms=100
            )
        ]
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=1200, last_commanded_pwm=50, age_ms=100
            )
        ]

        svc = DiagnosticsService(state=state)
        svc.log_event("info", "test", "bundle test")

        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        assert bundle_path.exists()
        data = json.loads(bundle_path.read_text())
        assert "timestamp" in data
        assert data["state"]["connection"] == "connected"
        assert data["capabilities"]["daemon_version"] == "1.4.0"
        assert len(data["events"]) == 1
        assert len(data["fan_state"]) == 1

    def test_export_without_state(self, tmp_path):
        svc = DiagnosticsService(state=None)
        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "state" not in data
        assert "missing_sections" in data
        assert any("AppState" in m for m in data["missing_sections"])

    def test_export_includes_gpu_when_present(self, tmp_path):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        state.set_capabilities(
            Capabilities(
                daemon_version="1.4.0",
                amd_gpu=AmdGpuCapability(
                    present=True,
                    model_name="RX 7900 XTX",
                    fan_control_method="pmfw",
                    pmfw_supported=True,
                    overdrive_enabled=True,
                ),
            )
        )
        state.set_status(DaemonStatus(overall_status="healthy", daemon_version="1.4.0"))
        state.lease = LeaseState()

        svc = DiagnosticsService(state=state)
        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "gpu" in data
        assert data["gpu"]["model"] == "RX 7900 XTX"
