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
from control_ofc.services.alerts import AlertOccurrence, AlertTransition
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
        # WIRE-p: the line now names what the daemon actually did. The default
        # mode is "stop", which a pre-2.28.0 daemon is also the only thing that
        # can do — so this fixture reads "stopped".
        assert "Identify: openfan:ch00 stopped (expires 8s)" in text

    def test_identify_records_a_pump_perturbation_as_such(self):
        """WIRE-p: DEC-311 put `mode` on the poll so a client that did not start
        the identify still describes it truthfully. A support bundle recording a
        pump perturbation as a stop misdescribes the one case the field exists
        for — and a pump is exactly the header an engineer reading the bundle
        would be alarmed to see stopped."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_status(
            DaemonStatus(
                overall_status="healthy",
                fan_identify=[
                    IdentifyStatusEntry(
                        fan_id="hwmon:it8696:pci0:pwm3:AIO_PUMP",
                        expires_in_secs=8,
                        mode="pump_perturb",
                        identify_pwm_percent=85,
                    )
                ],
            )
        )
        text = DiagnosticsService(state=state).format_daemon_status()
        assert "held at 85%" in text
        assert "stopped" not in text


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
                    fan_control_method="pmfw_curve",
                    pmfw_supported=True,
                )
            )
        )
        svc = DiagnosticsService(state=state)
        text = svc.format_gpu_status()
        assert "RX 7900 XTX" in text
        assert "pmfw_curve" in text
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

    def test_bundle_keeps_diagnostic_settings_drops_ui_state(self, tmp_path):
        """Release-review finding C: the troubleshooting bundle must retain the
        diagnostically load-bearing machine-specific settings (sensor-class
        overrides, dir overrides that are often the root cause) that portable_dict()
        wrongly stripped, while still dropping pure window/layout state."""
        from control_ofc.services.app_settings_service import AppSettingsService

        settings_service = AppSettingsService()
        settings_service.settings.sensor_class_overrides = {"hwmon:x:t1": "coolant"}
        settings_service.settings.profiles_dir_override = "/home/tester/profiles"
        settings_service.settings.window_geometry = [7, 7, 640, 480]

        svc = DiagnosticsService(state=AppState(), settings_service=settings_service)
        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        app_settings = json.loads(bundle_path.read_text())["app_settings"]
        # Diagnostic keys survive (these reveal the misconfiguration).
        assert app_settings["sensor_class_overrides"] == {"hwmon:x:t1": "coolant"}
        assert app_settings["profiles_dir_override"] == "/home/tester/profiles"
        # Pure window/layout state is dropped.
        assert "window_geometry" not in app_settings

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
                    fan_control_method="pmfw_curve",
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


class TestEventIdentityAndFields:
    """DEC-314: a stable per-event id and optional structured metadata."""

    def test_seq_increases_monotonically(self):
        svc = DiagnosticsService()
        for i in range(3):
            svc.log_event("info", "gui", f"m{i}")
        assert [e.seq for e in svc.events] == [1, 2, 3]

    def test_seq_is_not_reset_by_clearing_the_feed(self):
        """An id reused after a clear can collide with one a view still holds as its
        selection — the exact ambiguity ``seq`` exists to remove."""
        svc = DiagnosticsService()
        svc.log_event("info", "gui", "before")
        svc.clear_events()
        svc.log_event("info", "gui", "after")
        assert svc.events[0].seq == 2

    def test_two_identical_messages_in_one_instant_are_distinguishable(self):
        """The latent bug this closes: selection used to be restored by frozen
        view-model equality, which identical messages logged in the same second
        satisfy."""
        svc = DiagnosticsService()
        svc.log_event("warning", "fan", "stall")
        svc.log_event("warning", "fan", "stall")
        a, b = svc.events
        assert (a.level, a.source, a.message) == (b.level, b.source, b.message)
        assert a.seq != b.seq

    def test_fields_default_to_an_empty_mapping(self):
        svc = DiagnosticsService()
        svc.log_event("info", "gui", "no metadata here")
        assert svc.events[0].fields == {}

    def test_fields_are_coerced_to_strings(self):
        """So a caller may hand over ints or enums without formatting them first."""
        svc = DiagnosticsService()
        svc.log_event("info", "hwmon", "rescan", fields={"headers_found": 7})
        assert svc.events[0].fields == {"headers_found": "7"}

    def test_alert_transitions_carry_their_structured_context(self):
        """The richest structured context the GUI holds, and the one it used to
        flatten into a sentence and discard."""
        state = AppState()
        svc = DiagnosticsService(state)
        svc.attach_alert_source(state)
        svc._on_alert_transitions(
            [
                AlertTransition(
                    "onset",
                    AlertOccurrence(
                        key="fan:stall:cpu_fan",
                        activation_epoch=1_700_000_000.0,
                        level="error",
                        source="fan",
                        component="cpu_fan",
                        title="CPU_FAN stall",
                        detail="Fan stalled",
                        last_detected=1_700_000_000.0,
                    ),
                )
            ]
        )
        event = svc.events[-1]
        assert event.source == "fan"
        assert event.fields["component"] == "cpu_fan"
        assert event.fields["alert_key"] == "fan:stall:cpu_fan"
        assert event.fields["alert"] == "CPU_FAN stall"
        assert "duration_s" not in event.fields, "an onset has not lasted any time yet"
