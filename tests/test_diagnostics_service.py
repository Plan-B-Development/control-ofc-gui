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
    IdentifyStatusEntry,
    OpenfanCapability,
    OperationMode,
    OverrideStatusEntry,
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
# Event signals (DEC-111)
# ---------------------------------------------------------------------------


class TestEventSignals:
    """``DiagnosticsService`` emits Qt signals so the view can subscribe live."""

    def test_log_event_emits_event_appended(self, qtbot):
        svc = DiagnosticsService()
        with qtbot.waitSignal(svc.event_appended, timeout=1000) as blocker:
            svc.log_event("warning", "control_loop", "test message")
        # The signal payload carries the DiagEvent itself so the view does
        # not have to re-read the deque to render the new row.
        (ev,) = blocker.args
        assert ev.level == "warning"
        assert ev.source == "control_loop"
        assert ev.message == "test message"

    def test_clear_events_emits_events_cleared(self, qtbot):
        svc = DiagnosticsService()
        svc.log_event("info", "test", "one")
        with qtbot.waitSignal(svc.events_cleared, timeout=1000):
            svc.clear_events()
        assert svc.events == []


# ---------------------------------------------------------------------------
# filter_events (DEC-111)
# ---------------------------------------------------------------------------


class TestFilterEvents:
    """Helper for ad-hoc filtering — the view consumes this for export/copy."""

    def _populate(self) -> DiagnosticsService:
        svc = DiagnosticsService()
        svc.log_event("info", "polling", "Daemon connected")
        svc.log_event("warning", "control_loop", "Fan ch00 write failed")
        svc.log_event("error", "lease", "Lease lost: timeout")
        svc.log_event("info", "gui", "Theme changed: Solar Light")
        return svc

    def test_no_filter_returns_all(self):
        svc = self._populate()
        assert len(svc.filter_events()) == 4

    def test_filter_by_levels(self):
        svc = self._populate()
        result = svc.filter_events(levels={"warning", "error"})
        assert len(result) == 2
        assert {ev.level for ev in result} == {"warning", "error"}

    def test_filter_by_sources(self):
        svc = self._populate()
        result = svc.filter_events(sources={"polling", "lease"})
        assert {ev.source for ev in result} == {"polling", "lease"}

    def test_filter_by_search_substring(self):
        svc = self._populate()
        result = svc.filter_events(search="theme")
        assert len(result) == 1
        assert result[0].source == "gui"

    def test_filter_search_matches_source(self):
        # Searching for a source token should also match — useful when the
        # user types "control" to find every control_loop row regardless
        # of the message wording.
        svc = self._populate()
        result = svc.filter_events(search="control")
        assert len(result) == 1
        assert result[0].source == "control_loop"

    def test_filter_combines_all_three(self):
        svc = self._populate()
        result = svc.filter_events(
            levels={"warning", "error", "info"},
            sources={"gui"},
            search="solar",
        )
        assert len(result) == 1
        assert "Solar Light" in result[0].message

    def test_known_sources_sorted(self):
        svc = self._populate()
        assert svc.known_sources() == ["control_loop", "gui", "lease", "polling"]


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

    def test_includes_overrides_and_identify(self):
        """DEC-169: the support bundle records daemon-held overrides + identify
        holds so what the daemon was actively pinning is captured."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_status(
            DaemonStatus(
                overall_status="healthy",
                overrides=[
                    OverrideStatusEntry(control_id="pump", pwm_percent=40, expires_in_secs=12)
                ],
                fan_identify=[IdentifyStatusEntry(fan_id="openfan:ch00", expires_in_secs=8)],
            )
        )
        text = DiagnosticsService(state=state).format_daemon_status()
        assert "Override: pump 40% (expires 12s)" in text
        assert "Identify: openfan:ch00 (expires 8s)" in text


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
        svc = DiagnosticsService(state=state)
        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "gpu" in data
        assert data["gpu"]["model"] == "RX 7900 XTX"
