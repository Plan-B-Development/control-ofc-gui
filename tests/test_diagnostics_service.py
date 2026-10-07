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
    OpenFanSilentBoardEntry,
    OperationMode,
    OverrideStatusEntry,
    SensorReading,
    SubsystemStatus,
)
from control_ofc.services.alerts import AlertOccurrence, AlertTransition
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import (
    DiagnosticsService,
    bundle_cmdline,
    format_uptime,
    scrub_journal,
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
                id="s1", kind="cpu_temp", label="Tctl", value_c=45.0, source="hwmon", age_ms=100
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
                id="s1", kind="cpu_temp", label="Tctl", value_c=45.0, source="hwmon", age_ms=100
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

    def test_bundle_lists_each_chip_with_its_sysfs_name(self, tmp_path):
        """``BRD-x``: the bundle shows the name sysfs published beside the
        canonical one, so an it87 v2.0 board's suffixed naming (DEC-442) is
        visible in a support report. Driven from the wire payload through the
        parser, so it proves the field is carried, not merely rendered."""
        from control_ofc.api.models import parse_hardware_diagnostics

        svc = DiagnosticsService(state=AppState())
        svc.last_hw_diagnostics = parse_hardware_diagnostics(
            {
                "hwmon": {
                    "chips_detected": [
                        {"chip_name": "it8696", "sysfs_chip_name": "it8696_a008090a"},
                        # A daemon older than DEC-442 sends no such field.
                        {"chip_name": "nct6799"},
                    ]
                }
            }
        )
        bundle_path = tmp_path / "support.json"
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run", side_effect=FileNotFoundError
        ):
            svc.export_support_bundle(bundle_path)

        chips = json.loads(bundle_path.read_text())["hardware_diagnostics"]["hwmon"]["chips"]
        assert chips == [
            {"chip_name": "it8696", "sysfs_chip_name": "it8696_a008090a"},
            {"chip_name": "nct6799", "sysfs_chip_name": ""},
        ]

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


# ---------------------------------------------------------------------------
# GSA-g — the support bundle keeps board serials and boot disk ids out
# ---------------------------------------------------------------------------

_BOARD = "E6614103E7AB1234"  # the serial the daemon names in its own log line
_LINKED = "E66141FFAB009876"  # only ever in a /dev/serial/by-id/ link's name
_SILENT = "DF6050A04B2C3D4E"  # known to the GUI from a silent board (DEC-484)
_BY_ID_PATH = f"/dev/serial/by-id/usb-Karanovic_Research_OpenFan_{_LINKED}-if00"
_ROOT_UUID = "5f0c1d2e-3a4b-4c5d-8e9f-0a1b2c3d4e5f"
_LUKS_UUID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
_CMDLINE = (
    f"BOOT_IMAGE=/vmlinuz-linux root=UUID={_ROOT_UUID} rw "
    f"rd.luks.name={_LUKS_UUID}=cryptroot rd.luks.options={_LUKS_UUID}=tpm2-device=auto "
    "amdgpu.ppfeaturemask=0xfff7ffff acpi_enforce_resources=lax it87.force_id=0x8628 "
    'quiet splash="a b"\n'
)
_JOURNAL = "\n".join(
    [
        "2026-10-07T10:00:00+0000 h control-ofc-daemon[1]: Opening configured serial port "
        f"/dev/serial/by-id/usb-Karanovic_Research_OpenFan_{_LINKED}-if00",
        "2026-10-07T10:00:01+0000 h control-ofc-daemon[1]: Failed to open configured serial "
        f"port /dev/serial/by-id/usb-Karanovic_Research_OpenFan_{_LINKED}-if00: No such file",
        f"2026-10-07T10:00:02+0000 h control-ofc-daemon[1]: OpenFan board {_BOARD} is back on "
        "USB port 8-8 — adopting it",
        f"2026-10-07T10:00:03+0000 h control-ofc-daemon[1]: silent board {_SILENT.lower()}",
    ]
)

# What `journalctl -k --grep=amdgpu|smu` returns at boot once the command line has
# an `amdgpu.*` parameter: the kernel logs the whole line.
_KERNEL_LOG = "\n".join(
    [
        f"Oct 07 08:33:20 h kernel: Command line: {_CMDLINE.strip()}",
        f"Oct 07 08:33:20 h kernel: Kernel command line: {_CMDLINE.strip()}",
        "Oct 07 08:33:21 h kernel: [drm] amdgpu kernel modesetting enabled.",
    ]
)


def _export_with_host(tmp_path, monkeypatch, svc: DiagnosticsService) -> str:
    """Export a bundle on a simulated host: this command line and this journal."""
    from pathlib import Path

    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == "/proc/cmdline":
            return _CMDLINE
        return real_read_text(self, *args, **kwargs)

    def run(args, **kwargs):
        result = MagicMock(returncode=0, stderr="")
        if "-u" in args:
            result.stdout = _JOURNAL
        elif "-k" in args:
            result.stdout = _KERNEL_LOG
        else:
            result.stdout = ""
        return result

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr("control_ofc.services.diagnostics_service.subprocess.run", run)
    out = tmp_path / "support.json"
    svc.export_support_bundle(out)
    return real_read_text(out)


class TestSupportBundlePrivacy:
    def test_no_board_serial_reaches_the_bundle_journal(self, tmp_path, monkeypatch):
        state = AppState()
        state.set_status(DaemonStatus(openfan_silent_board=OpenFanSilentBoardEntry(_SILENT, "8-8")))
        svc = DiagnosticsService(state=state)
        # The Logs page reads the same lines: they stay whole on this machine.
        monkeypatch.setattr(
            "control_ofc.services.diagnostics_service.subprocess.run",
            lambda *a, **k: MagicMock(returncode=0, stderr="", stdout=_JOURNAL),
        )
        assert _BOARD in svc.fetch_journal_entries()

        text = _export_with_host(tmp_path, monkeypatch, svc)
        journal = json.loads(text)["journal"]
        assert "Opening configured serial port /dev/serial/by-id/(redacted)" in journal
        assert "/dev/serial/by-id/(redacted): No such file" in journal
        assert "OpenFan board (redacted) is back on USB port 8-8" in journal
        assert "silent board (redacted)" in journal
        # An event quoting a by-id port, as a Rescan that adopts a pinned port logs.
        svc.log_event("info", "rescan", f"OpenFanController adopted on {_BY_ID_PATH} via rescan")
        text = _export_with_host(tmp_path, monkeypatch, svc)
        messages = [e["message"] for e in json.loads(text)["events"]]
        assert "OpenFanController adopted on /dev/serial/by-id/(redacted) via rescan" in messages
        for serial in (_BOARD, _LINKED, _SILENT):
            assert serial.lower() not in text.lower(), serial

    def test_the_update_records_serial_is_scrubbed_from_the_journal_too(
        self, tmp_path, monkeypatch
    ):
        """A serial only the update record names, repeated in a journal line."""
        svc = DiagnosticsService(state=AppState())
        svc.set_openfan_update_record({"run_id": "ofmaint-1", "expected_usb_serial": "C0FFEE42"})
        monkeypatch.setattr(
            "control_ofc.services.diagnostics_service.DiagnosticsService.fetch_journal_entries",
            lambda self: "x: preparing the update for C0FFEE42",
        )
        text = _export_with_host(tmp_path, monkeypatch, svc)
        assert json.loads(text)["journal"] == "x: preparing the update for (redacted)"

    def test_only_fan_relevant_boot_parameters_reach_the_bundle(self, tmp_path, monkeypatch):
        text = _export_with_host(tmp_path, monkeypatch, DiagnosticsService())
        kernel = json.loads(text)["system"]["kernel"]
        assert kernel["cmdline"] == (
            "amdgpu.ppfeaturemask=0xfff7ffff acpi_enforce_resources=lax it87.force_id=0x8628"
        )
        assert kernel["cmdline_omitted"] == 7
        kernel_log = json.loads(text)["kernel_log_amdgpu"]
        assert (
            "kernel: Kernel command line: amdgpu.ppfeaturemask=0xfff7ffff "
            "acpi_enforce_resources=lax it87.force_id=0x8628 (7 other parameters omitted)"
        ) in kernel_log
        assert "[drm] amdgpu kernel modesetting enabled." in kernel_log
        for secret in (_ROOT_UUID, _LUKS_UUID, "tpm2-device", "BOOT_IMAGE"):
            assert secret not in text, secret


class TestBundleCmdline:
    def test_names_match_with_dash_or_underscore_and_by_module_prefix(self):
        kept, omitted = bundle_cmdline(
            "acpi-enforce-resources=lax nct6775.force_id=0xd428 nct6687.manual=1 "
            "modprobe.blacklist=nouveau k10temp.force=1 asus_ec_sensors.x=1 "
            "nvme_core.default_ps_max_latency_us=0 resume=UUID=1 cryptdevice=UUID=2:root"
        )
        assert kept.split() == [
            "acpi-enforce-resources=lax",
            "nct6775.force_id=0xd428",
            "nct6687.manual=1",
            "modprobe.blacklist=nouveau",
            "k10temp.force=1",
            "asus_ec_sensors.x=1",
        ]
        assert omitted == 3

    def test_a_quoted_value_with_spaces_is_one_parameter_and_is_left_out(self):
        """A quoted span can swallow the parameters after it (``it87.x=0" root=UUID=1"``),
        so a kept parameter never carries whitespace."""
        assert bundle_cmdline('it87.x="a b" foo="c d" bar it87.y="ok"') == ('it87.y="ok"', 3)
        assert bundle_cmdline('it87.force_id=0x8628" root=UUID=X rd.luks.name=Y" quiet') == (
            "",
            2,
        )

    def test_a_by_id_name_with_non_ascii_space_is_redacted_whole(self):
        name = "usb-Vendor\u00a0Name_OpenFan_E6614103E7AB1234-if00"
        out = scrub_journal(f"x: Opening configured serial port /dev/serial/by-id/{name}", set())
        assert out == "x: Opening configured serial port /dev/serial/by-id/(redacted)"

    def test_an_empty_command_line(self):
        assert bundle_cmdline("") == ("", 0)
