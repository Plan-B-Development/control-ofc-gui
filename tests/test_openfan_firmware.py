"""OpenFAN firmware update, GUI side (DEC-481, DEC-482, DEC-483).

The standing rules apply: presence before absence; ``isVisibleTo(parent)``,
never ``isVisible()``, under offscreen; ``.click()`` rather than the handler;
a relationship whose right-hand side the defect cannot satisfy.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog, QLabel, QProgressBar, QPushButton, QWidget

from control_ofc.api.errors import (
    DaemonError,
    DaemonTimeout,
    DaemonUnavailable,
    is_openfan_maintenance_refusal,
    is_soft_safety_refusal,
)
from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    ControlCapability,
    DaemonStatus,
    FanReading,
    OpenFanBoardSnapshot,
    OpenfanCapability,
    OpenFanDaemonWrite,
    OpenFanDevice,
    OpenFanFirmwareClaim,
    OpenFanFirmwareStaged,
    OpenFanFirmwareWrite,
    OpenFanMaintenanceRecord,
    OpenFanMaintenanceSummary,
    OpenFanStageTiming,
    OpenFanUpdateEvidence,
    OpenFanUpdateRefusal,
    OpenFanUsbDevice,
    OperationMode,
    parse_capabilities,
    parse_openfan_device,
    parse_openfan_firmware_staged,
    parse_openfan_maintenance_record,
    parse_status,
)
from control_ofc.constants import OPENFAN_DEVICE_TIMEOUT_S
from control_ofc.services import openfan_firmware_view as view
from control_ofc.services import uf2
from control_ofc.services.alerts_view import next_action_for_warning
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_features import daemon_supports
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.uf2 import KnownRelease, inspect_uf2, prepare_firmware
from control_ofc.ui.pages.diagnostics_workers import (
    _is_soft_refusal,
    _OpenFanFirmwareWorker,
    unexpected_error_message,
)
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.pwm_report_controller import RUN_ACTIVE_REASON
from control_ofc.ui.widgets import openfan_firmware_dialog as dlg_mod
from control_ofc.ui.widgets.openfan_firmware_dialog import OpenFanFirmwareDialog
from tests.test_uf2 import CONFIG, image, uf2_file

SERIAL = "DE615CB14721492C"
NOW = 1_800_000_000_000

# ── fixtures ─────────────────────────────────────────────────────────────────


def _device(**kw) -> OpenFanDevice:
    base = OpenFanDevice(
        present=True,
        link="connected",
        port="/dev/ttyACM1",
        usb=OpenFanUsbDevice(
            port="8-8",
            vendor_id="2e8a",
            product_id="000a",
            serial=SERIAL,
            config_descriptor_hex="0902aa",
        ),
        interface_number=0,
        hw_info={"HW_REV": "01", "MCU": "STM32F411CE"},
        fw_info={"FW_REV": "01", "PROTOCOL_VERSION": "1"},
        update_available=True,
    )
    return replace(base, **kw)


def _record(**kw) -> OpenFanMaintenanceRecord:
    base = OpenFanMaintenanceRecord(
        run_id="ofmaint-1",
        state="running",
        stage="waiting_for_file",
        stage_started_unix_ms=NOW - 20_000,
        stage_deadline_unix_ms=NOW + 300_000,
        cancellable=False,
        started_unix_ms=NOW - 30_000,
        bootloader_requested=True,
        bootloader_seen=True,
        bootloader_trigger=">07",
        bootloader_drive="sdb",
        stages=[
            OpenFanStageTiming("preparing", NOW - 30_000, NOW - 29_000),
            OpenFanStageTiming("parking", NOW - 29_000, NOW - 28_500),
            OpenFanStageTiming("entering_bootloader", NOW - 28_500, NOW - 22_000),
            OpenFanStageTiming("waiting_for_file", NOW - 22_000, None),
        ],
        expected_usb_serial=SERIAL,
        usb_port="8-8",
        interface_number=0,
        tty="/dev/ttyACM1",
        firmware=OpenFanFirmwareClaim(sha256="ab" * 32, size=512),
        before=OpenFanBoardSnapshot(hw_info={"HW_REV": "01"}, fw_info={"FW_REV": "01"}),
    )
    return replace(base, **kw)


def _finished(outcome: str, **kw) -> OpenFanMaintenanceRecord:
    return _record(state="finished", outcome=outcome, finished_unix_ms=NOW, cancellable=False, **kw)


def _good_file(tmp_path: Path) -> Path:
    path = tmp_path / "OpenFAN_Firmware.uf2"
    path.write_bytes(uf2_file(image()))
    return path


# ── the view-model ───────────────────────────────────────────────────────────


class TestOutcomes:
    @pytest.mark.parametrize(
        ("outcome", "tone", "words"),
        [
            ("needs_recovery", "crit", "needs recovery"),
            ("firmware_copied_board_not_back", "crit", "board not back"),
            ("board_back_control_not_restored", "crit", "fan control not confirmed"),
            ("exact_build_verified", "ok", "exact build verified"),
            ("back_on_previous_firmware", "warn", "not applied"),
            ("no_firmware_change", "warn", "No firmware change"),
        ],
    )
    def test_each_outcome_has_its_words_and_tone(self, outcome, tone, words):
        v = view.outcome_view(_finished(outcome))
        assert v is not None and v.tone == tone
        assert words.lower() in v.title.lower()

    def test_a_completed_run_is_ok_only_when_the_evidence_agrees(self):
        consistent = _finished(
            "completed_build_not_confirmed",
            evidence=OpenFanUpdateEvidence(verdict="consistent_with_file"),
        )
        unsure = replace(consistent, evidence=OpenFanUpdateEvidence(verdict="inconclusive"))
        assert view.outcome_view(consistent).tone == "ok"
        assert view.outcome_view(unsure).tone == "warn"
        assert "not confirmed" in view.outcome_view(unsure).title
        assert "no build identifier" in view.outcome_view(consistent).summary

    def test_a_cancel_is_its_own_quiet_outcome(self):
        v = view.outcome_view(_finished("no_firmware_change", cancelled=True))
        assert v.tone == "info" and v.title == "Update cancelled"

    def test_recovery_says_how_to_leave_update_mode_and_that_the_daemon_watches(self):
        steps = " ".join(view.outcome_view(_finished("needs_recovery")).steps)
        for words in ("RPI-RP2", "RESET", "off and on", "BOOT", "keeps watching"):
            assert words in steps

    def test_an_unknown_outcome_is_rendered_not_dropped(self):
        v = view.outcome_view(_finished("brand_new_token"))
        assert "brand_new_token" in v.title

    def test_the_daemons_detail_and_an_interruption_are_carried(self):
        v = view.outcome_view(
            _finished("needs_recovery", outcome_detail="the wait ran out", interrupted=True)
        )
        assert v.detail.startswith("the wait ran out")
        assert "repeated nothing" in v.detail

    def test_a_running_record_has_no_outcome(self):
        assert view.outcome_view(_record()) is None


class TestRunView:
    def test_stages_done_current_and_pending_with_their_durations(self):
        rows = {r.token: r for r in view.build_run_view(_record(), NOW).stages}
        assert [rows[t].state for t in ("preparing", "parking", "entering_bootloader")] == [
            view.ROW_DONE
        ] * 3
        assert rows["entering_bootloader"].duration == "6 s"
        assert rows["waiting_for_file"].state == view.ROW_CURRENT
        assert rows["checking"].state == view.ROW_PENDING

    def test_a_failed_run_marks_the_stage_it_stopped_in(self):
        rows = {r.token: r for r in view.build_run_view(_finished("needs_recovery"), NOW).stages}
        assert rows["waiting_for_file"].state == view.ROW_STOPPED
        assert rows["waiting_for_return"].state == view.ROW_PENDING

    def test_a_completed_run_marks_every_stage_it_ran_done(self):
        rec = _finished("completed_build_not_confirmed", stage="restoring_control")
        rec.stages.append(OpenFanStageTiming("restoring_control", NOW - 2_000, NOW))
        rows = {r.token: r for r in view.build_run_view(rec, NOW).stages}
        assert rows["restoring_control"].state == view.ROW_DONE

    def test_waiting_for_the_file_names_the_drive_and_port_and_wants_the_file(self):
        v = view.build_run_view(_record(), NOW)
        assert v.wants_file and v.running
        assert "sdb" in v.instruction and "8-8" in v.instruction
        assert v.time_left == "About 5 min 00 s left for this stage."

    def test_another_boards_drive_is_a_warning(self):
        v = view.build_run_view(_record(other_bootloader_drives=["sdc"]), NOW)
        assert v.warnings and "sdc" in v.warnings[0] and "do not copy" in v.warnings[0]

    def test_the_1200_baud_fallback_is_said(self):
        v = view.build_run_view(
            _record(stage="entering_bootloader", bootloader_trigger="1200_baud"), NOW
        )
        assert "1200-baud" in v.instruction

    def test_cancel_is_offered_only_while_the_daemon_says_it_can_be(self):
        assert view.build_run_view(_record(stage="parking", cancellable=True), NOW).can_cancel
        assert not view.build_run_view(_record(), NOW).can_cancel

    def test_the_headline_lowers_only_the_first_letter_of_the_stage(self):
        # The parking label names the board; lowering the whole label printed
        # "openfan" in the running headline.
        assert "OpenFAN" in view.stage_label("parking")
        v = view.build_run_view(_record(stage="parking"), NOW)
        assert "set every OpenFAN channel" in v.headline

    def test_time_up(self):
        assert "is up" in view.time_left(NOW - 1, NOW)
        assert view.time_left(None, NOW) == ""


def _gate(**kw) -> str:
    """``start_block_reason`` with every condition met, then *kw*."""
    data = uf2_file(image())
    args = {
        "demo": False,
        "device": _device(),
        "live_link": "connected",
        "external_block": "",
        "inspection": inspect_uf2(data),
        "plan": view.WritePlan(view.WRITE_MANUAL),
        "prepared": True,
        "confirmed": True,
        "starting": False,
    }
    args.update(kw)
    return view.start_block_reason(**args)


class TestStartGate:
    def _reason(self, **kw) -> str:
        return _gate(**kw)

    def test_every_condition_holds(self):
        assert self._reason() == ""

    @pytest.mark.parametrize(
        ("change", "words"),
        [
            ({"demo": True}, "Demo mode"),
            ({"starting": True}, "Starting"),
            ({"external_block": "a report runs"}, "a report runs"),
            ({"device": None}, "Reading"),
            ({"device": _device(present=False)}, "No OpenFAN controller"),
            ({"live_link": "reconnecting"}, "not connected (reconnecting)"),
            ({"inspection": None}, "Choose a firmware file"),
            ({"prepared": False}, "could not be prepared"),
            ({"confirmed": False}, "Tick the confirmation"),
        ],
    )
    def test_each_condition_blocks_with_its_reason(self, change, words):
        assert words in self._reason(**change)

    def test_a_refusal_is_the_daemons_own_words(self):
        device = _device(
            update_available=False,
            update_refusals=[OpenFanUpdateRefusal("calibration_active", "a calibration runs")],
        )
        assert self._reason(device=device) == "a calibration runs"

    def test_a_refusal_without_words_shows_its_token(self):
        device = _device(
            update_available=False, update_refusals=[OpenFanUpdateRefusal("brand_new", "")]
        )
        assert "(brand_new)" in self._reason(device=device)

    def test_a_bad_file_blocks(self):
        bad = inspect_uf2(uf2_file(image(names=False)))
        assert self._reason(inspection=bad) == "The chosen file cannot be used."


class TestFileFindings:
    def test_a_published_file_is_recognised_and_an_unknown_one_points_at_releases(self):
        r = inspect_uf2(uf2_file(image()))
        unknown = view.file_findings(r, _device())
        assert any("releases" in f.text.lower() for f in unknown)
        from control_ofc.services.uf2 import KNOWN_RELEASES

        known = view.file_findings(replace(r, release=KNOWN_RELEASES[2]), _device())
        assert known[0].tone == "ok" and "published fingerprint" in known[0].text

    def test_a_different_hardware_revision_is_shown_never_gated(self):
        texts = " ".join(
            f.text for f in view.file_findings(inspect_uf2(uf2_file(image())), _device())
        )
        assert "hardware revision 03" in texts and "reports 01" in texts
        assert "does not block" in texts

    def test_a_file_like_the_running_firmware_warns_the_result_cannot_confirm(self):
        r = inspect_uf2(uf2_file(image()))
        same = _device(
            usb=replace(_device().usb, config_descriptor_hex=CONFIG.hex()),
            hw_info={"HW_REV": "03", "MCU": "PICO2040"},
            fw_info={"FW_REV": "01", "PROTOCOL_VERSION": "01"},
        )
        found = view.file_findings(r, same)
        assert any(f.tone == "warn" and "not be able to confirm" in f.text for f in found)
        differs = view.file_findings(r, _device())
        assert any("differs from the running firmware" in f.text for f in differs)

    def test_a_refused_file_lists_its_problems(self):
        r = inspect_uf2(uf2_file(image(names=False)))
        found = view.file_findings(r, _device())
        assert found and all(f.tone == "crit" for f in found)


class TestAlert:
    def test_no_update_no_alert(self):
        assert view.firmware_update_alert(None) is None

    def test_a_running_update_is_one_warning_naming_its_stage(self):
        a = view.firmware_update_alert(
            OpenFanMaintenanceSummary("r1", "waiting_for_file", "running")
        )
        assert (a.key, a.level) == (view.UPDATE_ALERT_RUNNING, "warning")
        assert "copy the firmware file" in a.detail

    def test_the_stage_in_the_alert_keeps_the_boards_name(self):
        a = view.firmware_update_alert(OpenFanMaintenanceSummary("r1", "parking", "running"))
        assert "Stage: set every OpenFAN channel" in a.detail

    def test_recovery_is_an_error_naming_the_outcome(self):
        a = view.firmware_update_alert(
            OpenFanMaintenanceSummary("r1", "finished", "needs_recovery", "needs_recovery")
        )
        assert (a.key, a.level) == (view.UPDATE_ALERT_RECOVERY, "error")
        assert "needs recovery" in a.detail

    def test_only_openfan_fans_and_only_during_an_update(self):
        s = OpenFanMaintenanceSummary("r1", "parking", "running")
        assert view.suppresses_fan_staleness(s, "openfan:ch03")
        assert not view.suppresses_fan_staleness(s, "hwmon:nct6798:pwm2")
        assert not view.suppresses_fan_staleness(None, "openfan:ch03")

    def test_the_alert_names_where_to_look(self):
        assert "Update OpenFAN Firmware" in next_action_for_warning(
            {"_key": view.UPDATE_ALERT_RUNNING, "source": "openfan"}
        )


class TestSmallPieces:
    def test_device_rows_say_unknown_when_the_board_did_not_answer(self):
        rows = dict(view.device_rows(_device(hw_info=None, fw_info=None, link="unresponsive")))
        assert rows["Hardware report"] == "No answer — unknown"
        assert rows["Connection"].startswith("Not answering")
        assert rows["USB serial number"] == SERIAL
        assert view.device_rows(None) == [("Controller", "Reading…")]

    def test_an_unknown_link_token_is_shown_as_sent(self):
        assert view.link_text("brand_new") == "brand_new"

    def test_channels_carry_the_daemons_pump_mark(self):
        lines = view.channel_lines(
            ["openfan:ch01", "hwmon:x:pwm1", "openfan:ch00"],
            lambda f: f.upper(),
            lambda f: f == "openfan:ch01",
        )
        assert [(line.fan_id, line.pump) for line in lines] == [
            ("openfan:ch00", False),
            ("openfan:ch01", True),
        ]
        assert "OPENFAN:CH01" in view.pump_note(lines)
        assert view.pump_note(lines[:1]) == ""

    def test_evidence_and_reports(self):
        rec = _finished(
            "completed_build_not_confirmed",
            evidence=OpenFanUpdateEvidence(True, True, None, True, "consistent_with_file"),
            after=OpenFanBoardSnapshot(hw_info={"HW_REV": "03"}, fw_info={"FW_REV": "01"}),
            firmware=OpenFanFirmwareClaim("ab" * 32, 512, None, {"HW_REV": "03"}),
        )
        rows = dict(view.evidence_rows(rec))
        assert rows["USB descriptor"] == "Matches the file and changed"
        assert rows["Firmware reports"] == "Unknown"
        assert view.info_rows(rec)[0] == ("HW_REV", "01", "03", "03")

    def test_an_update_holding_the_controller_is_busy_not_protection(self):
        busy = {"reason": "openfan_maintenance"}
        assert is_openfan_maintenance_refusal(busy)
        assert not is_soft_safety_refusal("validation_error", True, busy)
        # The same code and flag without that reason is protection (DEC-297).
        assert is_soft_safety_refusal("validation_error", True, None)
        assert not is_openfan_maintenance_refusal(None)
        # Either way the workers show it softly, in the daemon's own words.
        err = DaemonError(
            code="validation_error", message="an update runs", retryable=True, status=409
        )
        assert _is_soft_refusal(err)
        assert _is_soft_refusal(replace(err, details=busy))

    def test_the_bundle_record_drops_the_serial_and_keeps_the_rest(self):
        raw = {
            "run_id": "r1",
            "expected_usb_serial": SERIAL,
            "before": {"usb": {"serial": SERIAL, "port": "8-8"}},
            "after": {"usb": {"serial": SERIAL}},
            "outcome": "needs_recovery",
        }
        out = view.bundle_record(raw)
        assert SERIAL not in json.dumps(out)
        assert out["before"]["usb"]["port"] == "8-8" and out["outcome"] == "needs_recovery"
        assert raw["before"]["usb"]["serial"] == SERIAL, "the original is untouched"


# ── the wire ─────────────────────────────────────────────────────────────────

RECORD_JSON = {
    "api_version": 1,
    "run_id": "ofmaint-7",
    "state": "finished",
    "stage": "restoring_control",
    "stage_started_unix_ms": 1,
    "stage_deadline_unix_ms": None,
    "cancellable": False,
    "started_unix_ms": 1,
    "finished_unix_ms": 9,
    "outcome": "completed_build_not_confirmed",
    "outcome_detail": "restored",
    "interrupted": False,
    "cancelled": False,
    "bootloader_requested": True,
    "bootloader_seen": True,
    "board_answered": True,
    "bootloader_trigger": "1200_baud",
    "bootloader_drive": "sdb",
    "other_bootloader_drives": ["sdc"],
    "notes": ["n"],
    "stages": [{"stage": "preparing", "started_unix_ms": 1, "ended_unix_ms": 2}],
    "expected_usb_serial": SERIAL,
    "usb_port": "8-8",
    "interface_number": 0,
    "tty": "/dev/ttyACM1",
    "firmware": {"sha256": "ab" * 32, "size": 512, "info": {"HW_REV": "03"}},
    "before": {"usb": {"port": "8-8", "vendor_id": "2e8a", "product_id": "000a"}},
    "after": {"hw_info": {"HW_REV": "03"}, "fw_info": None},
    "evidence": {
        "descriptor_changed": True,
        "descriptor_matches_file": True,
        "info_matches_file": None,
        "info_changed": True,
        "verdict": "consistent_with_file",
    },
}


class TestWire:
    def test_status_carries_the_link_and_the_update(self):
        s = parse_status(
            {
                "openfan_link": "maintenance",
                "openfan_maintenance": {"run_id": "r", "stage": "parking", "state": "running"},
            }
        )
        assert s.openfan_link == "maintenance"
        assert s.openfan_maintenance == OpenFanMaintenanceSummary("r", "parking", "running")

    @pytest.mark.parametrize(
        "data",
        [{}, {"openfan_link": 3, "openfan_maintenance": "running"}, {"openfan_maintenance": None}],
    )
    def test_absent_or_malformed_reads_as_nothing(self, data):
        s = parse_status(data)
        assert s.openfan_link is None and s.openfan_maintenance is None

    def test_the_capability_flag_gates_through_the_registry(self):
        caps = parse_capabilities({"control": {"openfan_firmware_maintenance": True}})
        assert caps.control.openfan_firmware_maintenance is True
        assert daemon_supports("openfan_firmware_maintenance", caps) is True
        assert daemon_supports("openfan_firmware_maintenance", parse_capabilities({})) is False

    def test_the_device_answer(self):
        d = parse_openfan_device(
            {
                "present": True,
                "link": "connected",
                "usb": {"port": "8-8", "serial": SERIAL, "config_descriptor_hex": "09"},
                "interface_number": 0,
                "hw_info": {"HW_REV": "01", "bad": 3},
                "update_available": False,
                "update_refusals": [{"reason": "diagnostic_active", "message": "busy"}, "x"],
            }
        )
        assert d.present and d.usb.serial == SERIAL and d.hw_info == {"HW_REV": "01"}
        assert d.fw_info is None
        assert d.update_refusals == [OpenFanUpdateRefusal("diagnostic_active", "busy")]

    def test_a_record_round_trips_and_keeps_its_raw_body(self):
        r = parse_openfan_maintenance_record(RECORD_JSON)
        assert r.run_id == "ofmaint-7" and r.board_answered and r.bootloader_drive == "sdb"
        assert r.firmware.info == {"HW_REV": "03"} and r.after.fw_info is None
        assert r.evidence.verdict == "consistent_with_file" and r.evidence.info_matches_file is None
        assert r.stages[0].ended_unix_ms == 2 and r.other_bootloader_drives == ["sdc"]
        assert r.raw["expected_usb_serial"] == SERIAL

    def test_a_malformed_record_degrades_to_defaults(self):
        r = parse_openfan_maintenance_record(
            {"run_id": 5, "cancellable": "yes", "stages": "x", "firmware": [], "evidence": 1}
        )
        assert r.run_id == "" and r.cancellable is False and r.stages == []
        assert r.evidence is None and r.firmware.sha256 == ""


class TestClient:
    def test_the_requests_hit_dec_481s_routes(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient(socket_path="/nonexistent.sock")
        seen = []

        def get(path, timeout=None):
            seen.append(("GET", path, timeout))
            return {"present": True} if path.endswith("device") else RECORD_JSON

        monkeypatch.setattr(client, "_get", get)
        monkeypatch.setattr(
            client,
            "_post",
            lambda path, json=None: seen.append(("POST", path, json)) or {"run_id": "r9"},
        )
        monkeypatch.setattr(
            client,
            "_delete",
            lambda path: (
                seen.append(("DELETE", path, None)) or {"run_id": "r9", "cancel_requested": True}
            ),
        )
        assert client.openfan_device().present
        assert client.start_openfan_maintenance(SERIAL, {"sha256": "x"}) == "r9"
        assert client.openfan_maintenance_status().run_id == "ofmaint-7"
        assert client.cancel_openfan_maintenance().cancel_requested
        assert seen == [
            ("GET", "/fans/openfan/device", OPENFAN_DEVICE_TIMEOUT_S),
            (
                "POST",
                "/fans/openfan/maintenance",
                {"expected_usb_serial": SERIAL, "firmware": {"sha256": "x"}},
            ),
            ("GET", "/fans/openfan/maintenance", None),
            ("DELETE", "/fans/openfan/maintenance", None),
        ]
        client.close()

    def test_no_run_yet_is_none(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient(socket_path="/nonexistent.sock")

        def _404(_path):
            raise DaemonError(code="not_found", message="no run", status=404)

        monkeypatch.setattr(client, "_get", _404)
        assert client.openfan_maintenance_status() is None
        client.close()


# ── the worker ───────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, *, start_error=None, status_error=None, no_run=False) -> None:
        self.calls: list[str] = []
        self.staged = None
        self.stage_error = None
        self._start_error = start_error
        self._status_error = status_error
        self._no_run = no_run

    def openfan_device(self):
        self.calls.append("device")
        return _device()

    def start_openfan_maintenance(self, serial, firmware, *, daemon_write=False):
        how = ":daemon" if daemon_write else ""
        self.calls.append(f"start:{serial}:{firmware['sha256']}{how}")
        if self._start_error:
            raise self._start_error
        return "ofmaint-1"

    def stage_openfan_firmware(self, data):
        self.calls.append(f"stage:{len(data)}")
        if self.stage_error:
            raise self.stage_error
        return self.staged

    def openfan_maintenance_status(self):
        self.calls.append("status")
        if self._status_error:
            raise self._status_error
        return None if self._no_run else _record()

    def cancel_openfan_maintenance(self):
        self.calls.append("cancel")


def _worker(fake) -> tuple[_OpenFanFirmwareWorker, dict]:
    worker = _OpenFanFirmwareWorker("/nonexistent.sock")
    worker._client = fake
    got = {
        "run": [],
        "run_error": [],
        "start_failed": [],
        "device": [],
        "started": [],
        "unconfirmed": [],
    }
    worker.run_updated.connect(got["run"].append)
    worker.run_error.connect(lambda c, m: got["run_error"].append((c, m)))
    worker.start_failed.connect(lambda c, m: got["start_failed"].append((c, m)))
    worker.started.connect(got["started"].append)
    worker.start_unconfirmed.connect(lambda c, m: got["unconfirmed"].append((c, m)))
    worker.device_ready.connect(got["device"].append)
    return worker, got


class TestWorker:
    def test_a_start_posts_then_reads_the_run_back(self, qapp):
        fake = _FakeClient()
        worker, got = _worker(fake)
        worker.do_start(SERIAL, {"sha256": "x"}, None)
        assert fake.calls == [f"start:{SERIAL}:x", "status"]
        assert got["started"] == ["ofmaint-1"], "the run the daemon's 202 named"
        assert [r.run_id for r in got["run"]] == ["ofmaint-1"]
        assert got["start_failed"] == [] and got["run_error"] == []

    def test_a_refused_start_is_soft_and_says_nothing_started(self, qapp):
        refusal = DaemonError(
            code="validation_error", message="a calibration runs", retryable=True, status=409
        )
        worker, got = _worker(_FakeClient(start_error=refusal))
        worker.do_start(SERIAL, {"sha256": "x"}, None)
        assert got["start_failed"] == [("unavailable", "a calibration runs")]
        assert got["run_error"] == [] and got["run"] == []

    @pytest.mark.parametrize(
        ("error", "words"),
        [
            (DaemonTimeout(), "did not answer the start in time"),
            (DaemonUnavailable(), "could not be reached"),
            (ConnectionResetError("reset by peer"), "connection was lost"),
            (DaemonError(code="parse_error", message="garbled", status=202), "could not be read"),
            (RuntimeError("boom"), "unexpected error"),
        ],
        ids=["timeout", "unavailable", "connection", "unreadable-202", "unexpected"],
    )
    def test_a_start_with_no_answer_may_have_started(self, qapp, error, words):
        worker, got = _worker(_FakeClient(start_error=error))
        worker.do_start(SERIAL, {"sha256": "x"}, None)
        assert len(got["unconfirmed"]) == 1 and words in got["unconfirmed"][0][1]
        assert got["start_failed"] == [], "no answer is not a refusal"
        assert got["started"] == [] and got["run"] == []

    def test_a_failed_read_after_the_start_is_not_a_failed_start(self, qapp):
        worker, got = _worker(
            _FakeClient(status_error=DaemonError(code="internal", message="boom", status=500))
        )
        worker.do_start(SERIAL, {"sha256": "x"}, None)
        assert got["run_error"] == [("error", "boom")]
        assert got["start_failed"] == [], "the run started; the next poll finds it"

    def test_cancel_and_device(self, qapp):
        fake = _FakeClient()
        worker, got = _worker(fake)
        worker.do_cancel()
        worker.do_device()
        assert fake.calls == ["cancel", "status", "device"]
        assert len(got["run"]) == 1 and got["device"][0].usb.serial == SERIAL


# ── DEC-482: one alert for the update ────────────────────────────────────────


def _state_with(update: OpenFanMaintenanceSummary | None) -> AppState:
    state = AppState()
    state.set_status(DaemonStatus(openfan_maintenance=update))
    state.set_fans(
        [
            FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=12_000),
            FanReading(id="hwmon:nct6798:pwm2", source="hwmon", rpm=700, age_ms=12_000),
        ]
    )
    return state


def _keys(state: AppState) -> dict[str, str]:
    return {o.key: o.level for o in state.alerts.present()}


class TestAlertConsolidation:
    def test_staleness_alerts_exist_without_an_update(self, qapp):
        keys = _keys(_state_with(None))
        assert "fan_stale:openfan:ch00" in keys and "fan_stale:hwmon:nct6798:pwm2" in keys
        assert not any(k.startswith("openfan_update") for k in keys)

    def test_an_update_stands_in_for_the_openfan_fans_only(self, qapp):
        keys = _keys(_state_with(OpenFanMaintenanceSummary("r1", "parking", "running")))
        assert keys[view.UPDATE_ALERT_RUNNING] == "warning"
        assert "fan_stale:hwmon:nct6798:pwm2" in keys, "other fans keep theirs"
        assert "fan_stale:openfan:ch00" not in keys

    def test_recovery_raises_the_error_and_closes_the_warning(self, qapp):
        state = _state_with(OpenFanMaintenanceSummary("r1", "parking", "running"))
        assert view.UPDATE_ALERT_RUNNING in _keys(state)
        state.set_status(
            DaemonStatus(
                openfan_maintenance=OpenFanMaintenanceSummary(
                    "r1", "finished", "needs_recovery", "needs_recovery"
                )
            )
        )
        state.set_fans(state.fans)
        keys = _keys(state)
        assert keys[view.UPDATE_ALERT_RECOVERY] == "error"
        assert view.UPDATE_ALERT_RUNNING not in keys

    def test_a_stall_on_an_openfan_fan_is_never_masked(self, qapp):
        state = AppState()
        state.set_status(
            DaemonStatus(openfan_maintenance=OpenFanMaintenanceSummary("r1", "parking", "running"))
        )
        stalled = FanReading(id="openfan:ch01", source="openfan", rpm=0, age_ms=100)
        stalled.stall_detected = True
        state.set_fans([stalled])
        assert "fan_stall:openfan:ch01" in _keys(state)

    def test_the_poll_that_ends_an_update_raises_no_staleness(self, qapp):
        # A poll applies its status, then its sensors and its fans, and the last
        # two each reconcile (`polling.py`): the update that has just ended must
        # not be judged against the previous poll's paused readings.
        state = _state_with(OpenFanMaintenanceSummary("r1", "restoring_control", "running"))
        assert "fan_stale:openfan:ch00" not in _keys(state), "precondition: stood in for"
        state.set_status(DaemonStatus())
        state.set_sensors([])
        state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=100)])
        raised = {o.key for o in state.alerts.unacknowledged()}
        assert "fan_stale:openfan:ch00" not in raised
        # The stand-in ends with that poll: a reading still stale after it warns.
        state.set_status(DaemonStatus())
        state.set_sensors([])
        state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=12_000)])
        assert "fan_stale:openfan:ch00" in _keys(state)

    def test_a_daemon_gone_long_enough_drops_the_update_alert(self, qapp):
        state = _state_with(OpenFanMaintenanceSummary("r1", "parking", "running"))
        assert view.UPDATE_ALERT_RUNNING in _keys(state)
        state.set_connection(ConnectionState.DISCONNECTED)
        state._thermal_clear_timer.timeout.emit()
        assert view.UPDATE_ALERT_RUNNING not in _keys(state)


# ── the window ───────────────────────────────────────────────────────────────


def _dialog(qtbot, tmp_path, **kw) -> OpenFanFirmwareDialog:
    kw.setdefault("daemon_write_supported", False)
    dialog = OpenFanFirmwareDialog(
        channels=[], prepared_dir=tmp_path / "prepared", now_ms=lambda: NOW, **kw
    )
    qtbot.addWidget(dialog)
    return dialog


def _ready(qtbot, tmp_path, monkeypatch) -> OpenFanFirmwareDialog:
    """A window with a connected controller and a checked, prepared file."""
    dialog = _dialog(qtbot, tmp_path)
    dialog.set_live_status("connected", None)
    dialog.apply_device(_device())
    path = _good_file(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )
    dialog.findChild(QPushButton, "OfwDialog_Btn_choose").click()
    return dialog


class TestWindowSetup:
    def test_start_needs_a_checked_file_and_the_confirmation(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        assert start.isEnabled() is False
        assert "confirmation" in start.toolTip()
        dialog.findChild(type(dialog._confirm), "OfwDialog_Check_confirm").setChecked(True)
        assert start.isEnabled() is True

    def test_the_prepared_copy_is_private_and_offered_for_dragging(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        sha = dialog._inspection.sha256
        prepared = tmp_path / "prepared" / f"OpenFAN-{sha[:8]}.uf2"
        assert prepared.read_bytes() == _good_file(tmp_path).read_bytes()
        assert stat.S_IMODE(prepared.stat().st_mode) == 0o600
        handle = dialog.findChild(dlg_mod.FileDragHandle, "OfwDialog_Label_dragFile")
        assert handle.path == prepared
        assert handle.mime_data().urls() == [QUrl.fromLocalFile(str(prepared))]
        opened = []
        monkeypatch.setattr(dlg_mod.QDesktopServices, "openUrl", staticmethod(opened.append))
        dialog.findChild(QPushButton, "OfwDialog_Btn_openFolder").click()
        assert opened == [QUrl.fromLocalFile(str(prepared.parent))]

    def test_a_refused_file_never_reaches_start(self, qtbot, tmp_path, monkeypatch):
        bad = tmp_path / "other.uf2"
        bad.write_bytes(uf2_file(image(names=False)))
        dialog = _dialog(qtbot, tmp_path)
        dialog.set_live_status("connected", None)
        dialog.apply_device(_device())
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), ""))
        )
        dialog.findChild(QPushButton, "OfwDialog_Btn_choose").click()
        dialog._confirm.setChecked(True)
        assert dialog.findChild(QPushButton, "OfwDialog_Btn_start").isEnabled() is False
        finding = dialog.findChild(QLabel, "OfwDialog_Label_finding0")
        assert "not OpenFAN firmware" in finding.text()
        assert not (tmp_path / "prepared").exists() or not list((tmp_path / "prepared").iterdir())

    def test_start_sends_the_serial_and_the_claim_once(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        sent = []
        dialog.start_requested.connect(lambda s, c, w: sent.append((s, c, w)))
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        start.click()
        start.click()
        assert sent == [(SERIAL, dialog._inspection.claim(), None)], "no daemon write here"
        assert start.isEnabled() is False

    def test_a_refused_start_is_shown_and_start_comes_back(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        start.click()
        assert start.isEnabled() is False
        dialog.apply_start_error("unavailable", "a calibration runs")
        assert "a calibration runs" in dialog.findChild(QLabel, "OfwDialog_Label_status").text()
        assert start.isEnabled() is True

    def test_a_link_that_drops_blocks_start(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        assert start.isEnabled() is True
        dialog.set_live_status("unresponsive", None)
        assert start.isEnabled() is False and "not connected" in start.toolTip()

    def test_a_report_run_blocks_start(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        dialog.set_external_block(RUN_ACTIVE_REASON)
        assert dialog.findChild(QPushButton, "OfwDialog_Btn_start").isEnabled() is False


class TestWindowFollowsARun:
    def test_an_older_run_is_ignored_while_starting(self, qtbot, tmp_path, monkeypatch):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog.apply_run(_finished("no_firmware_change", run_id="old", started_unix_ms=NOW - 9))
        dialog._confirm.setChecked(True)
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        dialog.apply_run(_finished("no_firmware_change", run_id="old", started_unix_ms=NOW - 9))
        assert dialog.mode == dlg_mod.MODE_SETUP and dialog.following == ""
        dialog.apply_run(_record(run_id="new", stage="preparing", started_unix_ms=NOW))
        assert (dialog.mode, dialog.following) == (dlg_mod.MODE_RUN, "new")
        assert dialog._timer.isActive()

    def test_the_run_the_daemon_named_is_followed_not_one_that_looks_new(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        dialog.apply_started("ours")
        # Newer than the click and not the run before it — but not the 202's.
        dialog.apply_run(_record(run_id="other", stage="preparing", started_unix_ms=NOW + 5))
        assert dialog.mode == dlg_mod.MODE_SETUP and dialog.following == ""
        # The 202's, even stamped before the click.
        dialog.apply_run(_record(run_id="ours", stage="preparing", started_unix_ms=NOW - 1000))
        assert (dialog.mode, dialog.following) == (dlg_mod.MODE_RUN, "ours")

    def test_while_starting_the_poll_asks_only_for_the_run_the_daemon_named(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        polls = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog.apply_started("ours")
        other = OpenFanMaintenanceSummary("other", "parking", "running")
        dialog.set_live_status("maintenance", other)
        assert polls == []
        ours = OpenFanMaintenanceSummary("ours", "parking", "running")
        dialog.set_live_status("maintenance", ours)
        assert polls == [1]

    def test_a_start_with_no_answer_is_checked_not_called_failed(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        status = dialog.findChild(QLabel, "OfwDialog_Label_status")
        start.click()
        polls = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog.apply_start_unconfirmed("unavailable", "The daemon did not answer in time.")
        assert "may have started" in status.text() and "did not start" not in status.text()
        assert polls == [1] and start.isEnabled() is False
        dialog.apply_run_error("unavailable", "Daemon unavailable during the status read.")
        assert status.text().startswith("Still checking whether the update started")
        # The run it made turns up: followed.
        dialog.apply_run(_record(run_id="new", stage="parking", started_unix_ms=NOW))
        assert (dialog.mode, dialog.following) == (dlg_mod.MODE_RUN, "new")

    def test_an_unexpected_failure_of_the_start_reads_as_a_sentence(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        dialog._confirm.setChecked(True)
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        dialog.apply_start_unconfirmed("error", unexpected_error_message("the update start"))
        text = dialog.findChild(QLabel, "OfwDialog_Label_status").text()
        assert text.startswith("The update start ended with an unexpected error")
        assert "log). The update may have started" in text

    def test_a_start_with_no_answer_and_no_run_fails_on_a_fresh_answer(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog = _ready(qtbot, tmp_path, monkeypatch)
        old = _finished("no_firmware_change", run_id="old", started_unix_ms=NOW - 9)
        dialog.apply_run(old)
        dialog._confirm.setChecked(True)
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        status = dialog.findChild(QLabel, "OfwDialog_Label_status")
        start.click()
        dialog.apply_start_unconfirmed("unavailable", "The daemon did not answer in time.")
        # This answer may be to a poll sent before the start reached the daemon.
        dialog.apply_run(old)
        assert "may have started" in status.text() and start.isEnabled() is False
        dialog.apply_run(old)
        assert "did not start" in status.text()
        assert start.isEnabled() is True and dialog.mode == dlg_mod.MODE_SETUP

    def test_a_window_opened_mid_update_picks_it_up(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        polls = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog.set_live_status(
            "maintenance", OpenFanMaintenanceSummary("ofmaint-1", "waiting_for_file", "running")
        )
        assert polls, "an update this window is not following is asked for"
        dialog.apply_run(_record())
        assert dialog.mode == dlg_mod.MODE_RUN
        instruction = dialog.findChild(QLabel, "OfwDialog_Label_instruction")
        assert "sdb" in instruction.text()
        assert dialog.findChild(QPushButton, "OfwDialog_Btn_cancel").isEnabled() is False

    def test_the_prepared_copy_is_offered_again_after_a_restart(self, qtbot, tmp_path):
        data = uf2_file(image())
        r = inspect_uf2(data)
        prepared = prepare_firmware(data, r, tmp_path / "prepared")
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record(firmware=OpenFanFirmwareClaim(sha256=r.sha256, size=len(data))))
        handle = dialog.findChild(dlg_mod.FileDragHandle, "OfwDialog_Label_dragFile")
        assert handle.path == prepared
        assert dialog._prepared_box.isVisibleTo(dialog)

    def test_a_missing_copy_says_which_file_to_copy(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record())
        note = dialog.findChild(QLabel, "OfwDialog_Label_prepared")
        assert "not on this computer" in note.text() and ("ab" * 8) in note.text()

    def test_a_cancellable_run_offers_cancel_and_sends_it(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record(stage="parking", cancellable=True))
        cancels = []
        dialog.cancel_requested.connect(lambda: cancels.append(1))
        button = dialog.findChild(QPushButton, "OfwDialog_Btn_cancel")
        assert button.isEnabled() is True
        button.click()
        assert cancels == [1] and button.isEnabled() is False

    def test_a_failed_cancel_offers_cancel_again_only_while_the_run_allows_it(
        self, qtbot, tmp_path
    ):
        dialog = _dialog(qtbot, tmp_path)
        button = dialog.findChild(QPushButton, "OfwDialog_Btn_cancel")
        dialog.apply_run(_record(stage="parking", cancellable=True))
        button.click()
        assert button.isEnabled() is False
        dialog.apply_run_error("error", "the daemon did not answer")
        assert button.isEnabled() is True, "a cancel lost in transit can be retried"
        dialog.apply_run(_record(stage="entering_bootloader", cancellable=False))
        dialog.apply_run_error("error", "the update is past the point where it can be stopped")
        assert button.isEnabled() is False

    def test_the_end_shows_the_result_and_stops_polling(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record())
        assert dialog._timer.isActive()
        dialog.apply_run(_finished("firmware_copied_board_not_back"))
        assert dialog.mode == dlg_mod.MODE_RESULT and not dialog._timer.isActive()
        title = dialog.findChild(QLabel, "OfwDialog_Label_outcomeTitle")
        assert title.text() == view.outcome_view(_finished("firmware_copied_board_not_back")).title
        assert dialog.findChild(QPushButton, "OfwDialog_Btn_again").isVisibleTo(dialog)

    def test_a_board_needing_recovery_is_shown_on_open(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.set_live_status(
            "reconnecting",
            OpenFanMaintenanceSummary("ofmaint-1", "finished", "needs_recovery", "needs_recovery"),
        )
        dialog.apply_run(_finished("needs_recovery"))
        assert dialog.mode == dlg_mod.MODE_RESULT
        steps = dialog.findChild(QLabel, "OfwDialog_Label_outcomeSteps").text()
        assert "RESET" in steps

    def test_a_finished_run_nobody_followed_is_only_remembered(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_finished("completed_build_not_confirmed"))
        assert dialog.mode == dlg_mod.MODE_SETUP
        last = dialog.findChild(QLabel, "OfwDialog_Label_last")
        assert last.text().startswith("Last update:") and last.isVisibleTo(dialog)

    def test_a_run_the_daemon_lost_returns_to_setup(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record())
        dialog.apply_run(None)
        assert dialog.mode == dlg_mod.MODE_SETUP and dialog.following == ""
        assert "no longer has a record" in dialog.findChild(QLabel, "OfwDialog_Label_status").text()

    def test_closing_never_cancels(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        dialog.apply_run(_record(stage="parking", cancellable=True))
        cancels = []
        dialog.cancel_requested.connect(lambda: cancels.append(1))
        dialog.findChild(QPushButton, "OfwDialog_Btn_close").click()
        assert cancels == [] and not dialog._timer.isActive()

    def test_one_poll_in_flight_at_a_time(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path)
        polls = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog.apply_run(_record())
        dialog._timer.timeout.emit()
        dialog._timer.timeout.emit()
        assert polls == [1]
        dialog.apply_run(_record())
        dialog._timer.timeout.emit()
        assert polls == [1, 1]


# ── the Hardware page ────────────────────────────────────────────────────────


def _page(
    qtbot, *, flag=True, write=False, link="connected", update=None, mode=None
) -> HardwarePage:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    if mode is not None:
        state.set_mode(mode)
    state.set_capabilities(
        Capabilities(
            openfan=OpenfanCapability(present=True, channels=10),
            control=ControlCapability(
                openfan_firmware_maintenance=flag, openfan_firmware_write=write
            ),
        )
    )
    state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900)])
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=None)
    qtbot.addWidget(page)
    state.set_status(DaemonStatus(openfan_link=link, openfan_maintenance=update))
    page._sync_diagnostic_enablement()
    return page


def _button(page) -> QPushButton:
    return page.findChild(QPushButton, "Hardware_Btn_openfanFirmware")


class TestHardwarePage:
    @pytest.mark.parametrize("advertised", [True, False])
    def test_the_button_follows_the_wire_flag(self, qtbot, advertised):
        page = _page(qtbot, flag=advertised)
        caps = page._state.capabilities
        assert _button(page).isVisibleTo(page) == caps.control.openfan_firmware_maintenance

    def test_demo_mode_hides_it(self, qtbot):
        page = _page(qtbot)
        assert _button(page).isVisibleTo(page)  # presence before absence
        page._state.set_mode(OperationMode.DEMO)
        page._sync_diagnostic_enablement()
        assert not _button(page).isVisibleTo(page)

    def test_enabled_only_while_connected(self, qtbot):
        page = _page(qtbot)
        assert _button(page).isEnabled()
        page._state.set_status(DaemonStatus(openfan_link="reconnecting"))
        assert not _button(page).isEnabled()
        assert "reconnecting" in _button(page).toolTip()

    def test_a_report_run_stands_it_down_but_never_hides_an_update(self, qtbot):
        page = _page(qtbot)
        page._on_report_active(True)
        assert not _button(page).isEnabled() and _button(page).toolTip() == RUN_ACTIVE_REASON
        page._state.set_status(
            DaemonStatus(
                openfan_link="maintenance",
                openfan_maintenance=OpenFanMaintenanceSummary("r", "parking", "running"),
            )
        )
        assert _button(page).isEnabled(), "an update can always be followed"

    def test_a_board_needing_recovery_after_a_restart_keeps_the_way_in(self, qtbot):
        # A restart with the board in its bootloader adopts no controller: no
        # link, no presence — only the daemon's needs-recovery summary.
        page = _page(qtbot, link=None)
        page._state.set_capabilities(
            Capabilities(
                openfan=OpenfanCapability(present=False, channels=0),
                control=ControlCapability(openfan_firmware_maintenance=True),
            )
        )
        page._sync_diagnostic_enablement()
        assert not _button(page).isVisibleTo(page), "precondition: nothing to update"
        page._state.set_status(
            DaemonStatus(
                openfan_maintenance=OpenFanMaintenanceSummary(
                    "r1", "finished", "needs_recovery", "needs_recovery"
                )
            )
        )
        assert _button(page).isVisibleTo(page) and _button(page).isEnabled()

    def test_one_window_at_a_time(self, qtbot, monkeypatch):
        page = _page(qtbot)
        monkeypatch.setattr(page, "_ensure_ofw_worker", lambda: True)
        _button(page).click()
        first = page._ofw_dialog
        assert first is not None
        _button(page).click()
        assert page._ofw_dialog is first
        assert len(page.findChildren(OpenFanFirmwareDialog)) == 1
        first.findChild(QPushButton, "OfwDialog_Btn_close").click()
        assert page._ofw_dialog is None

    def test_runs_reach_the_window_and_the_support_bundle(self, qtbot, monkeypatch, tmp_path):
        page = _page(qtbot)
        # The host's own journal can name a real board (SERIAL is one), so the
        # bundle's journal and kernel-log reads are kept off the host.
        monkeypatch.setattr(page._diag, "fetch_journal_entries", lambda: "journal")
        monkeypatch.setattr(page._diag, "fetch_kernel_log_amdgpu", lambda: "kernel")
        monkeypatch.setattr(page, "_ensure_ofw_worker", lambda: True)
        _button(page).click()
        record = parse_openfan_maintenance_record(RECORD_JSON)
        page._on_ofw_run(record)
        # A finished run this window did not follow is remembered, not shown.
        last = page._ofw_dialog.findChild(QLabel, "OfwDialog_Label_last")
        assert last.text().startswith("Last update: Update complete")
        bundle = tmp_path / "bundle.json"
        page._diag.export_support_bundle(bundle)
        data = json.loads(bundle.read_text())
        assert data["openfan_firmware_update"]["run_id"] == "ofmaint-7"
        assert SERIAL not in bundle.read_text()
        assert data["daemon_status"]["openfan_link"] == "connected"

    def test_the_start_signals_reach_the_window(self, qtbot, monkeypatch):
        page = _page(qtbot)

        def build(worker, thread, worker_cls, connect):
            # The page's own wiring, on a worker that talks to no daemon.
            w = worker_cls("/nonexistent.sock")
            w._client = _FakeClient(no_run=True)
            connect(w)
            return w, None, True

        monkeypatch.setattr(page, "_ensure_worker", build)
        _button(page).click()
        dialog, worker = page._ofw_dialog, page._ofw_worker
        assert dialog is not None and worker is not None
        dialog._starting = True  # as a click on Start leaves it
        worker.started.emit("ours")
        qtbot.waitUntil(lambda: dialog._started_run_id == "ours", timeout=2000)
        worker.start_unconfirmed.emit("unavailable", "No answer.")
        qtbot.waitUntil(lambda: dialog._unconfirmed, timeout=2000)
        page.cleanup()

    def test_cleanup_tears_the_worker_down(self, qtbot):
        page = _page(qtbot)
        page._ofw_worker = _OpenFanFirmwareWorker("/nonexistent.sock")
        page.cleanup()
        assert page._ofw_worker is None and page._ofw_thread is None


# ── DEC-483: the daemon writes the file itself ───────────────────────────────

RELEASE_NAME = "2026-09-27 release"
OTHER_BOARD = "E66038B7132F2A27"
CAN_WRITE = OpenFanDaemonWrite(available=True)
NO_ACCESS = OpenFanDaemonWrite(
    available=False,
    reason="no_usb_access",
    message="the daemon may not open USB devices, so it cannot write the firmware itself",
)


def _staged(data: bytes | None = None, **kw) -> OpenFanFirmwareStaged:
    """The daemon's answer to an upload of *data* (the good test file by default)."""
    data = uf2_file(image()) if data is None else data
    base = OpenFanFirmwareStaged(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        release=RELEASE_NAME,
        verdict="daemon_write",
        message=f"the {RELEASE_NAME} — Control-OFC writes it itself and reads every byte back",
    )
    return replace(base, **kw)


def _write(phase: str, **kw) -> OpenFanFirmwareWrite:
    return replace(
        OpenFanFirmwareWrite(release=RELEASE_NAME, phase=phase, total_bytes=40_960), **kw
    )


def _write_record(stage: str, phase: str, **kw) -> OpenFanMaintenanceRecord:
    """A run that asked the daemon to write, now in *stage*."""
    timings = [
        OpenFanStageTiming("preparing", NOW - 30_000, NOW - 29_000),
        OpenFanStageTiming("parking", NOW - 29_000, NOW - 28_500),
        OpenFanStageTiming("entering_bootloader", NOW - 28_500, NOW - 22_000),
        OpenFanStageTiming("writing_firmware", NOW - 22_000, None),
    ]
    if stage != "writing_firmware":
        timings[-1] = replace(timings[-1], ended_unix_ms=NOW - 5_000)
        timings.append(OpenFanStageTiming(stage, NOW - 5_000, None))
    return _record(stage=stage, stages=timings, firmware_write=_write(phase, **kw))


class TestWritePlan:
    def _plan(self, **kw) -> view.WritePlan:
        data = uf2_file(image())
        args = {
            "supported": True,
            "device": _device(daemon_write=CAN_WRITE),
            "inspection": inspect_uf2(data),
            "staged": _staged(data),
            "stage_error": "",
        }
        args.update(kw)
        return view.write_plan(**args)

    def test_the_daemon_writes_only_a_file_it_keeps_and_only_while_it_may(self):
        plan = self._plan()
        assert (plan.method, plan.tone, plan.pending) == (view.WRITE_DAEMON, view.TONE_OK, False)
        assert RELEASE_NAME in plan.text
        # The opposite branch: the same file, without the USB access.
        denied = self._plan(device=_device(daemon_write=NO_ACCESS))
        assert denied.method == view.WRITE_MANUAL
        assert "openfan-firmware-write" in denied.text and RELEASE_NAME in denied.text

    def test_another_reason_it_cannot_write_is_the_daemons_own_words(self):
        busy = OpenFanDaemonWrite(available=False, reason="brand_new", message="the bus is busy")
        plan = self._plan(device=_device(daemon_write=busy))
        assert plan.method == view.WRITE_MANUAL and "the bus is busy" in plan.text
        bare = self._plan(device=_device(daemon_write=replace(busy, message=None)))
        assert "(brand_new)" in bare.text

    def test_a_device_answer_without_the_field_is_the_copy_by_hand(self):
        assert self._plan(device=_device(daemon_write=None)).method == view.WRITE_MANUAL

    def test_until_the_daemon_answers_start_waits(self):
        plan = self._plan(staged=None)
        assert plan.pending and plan.method == view.WRITE_MANUAL
        assert _gate(plan=plan) == "Asking the daemon about the file…"

    def test_a_file_it_will_not_write_is_copied_by_hand(self):
        unknown = self._plan(
            staged=_staged(verdict="manual_copy", reason="unknown_build", release=None)
        )
        assert unknown.method == view.WRITE_MANUAL
        assert "only the published OpenFAN releases" in unknown.text
        invalid = self._plan(
            staged=_staged(
                verdict="manual_copy",
                reason="invalid_image",
                release=None,
                message="Control-OFC will not write this file itself: block 3 is out of order",
            )
        )
        assert "block 3 is out of order." in invalid.text
        assert invalid.text.endswith("You copy it onto the board's drive.")
        new = self._plan(staged=_staged(verdict="brand_new", reason=None, message=""))
        assert new.method == view.WRITE_MANUAL and "(brand_new)" in new.text

    def test_a_file_the_daemon_refuses_blocks_start_in_its_words(self):
        plan = self._plan(
            staged=_staged(
                verdict="refused",
                reason="firmware_known_broken",
                message="this is the 2023 FW_01 binary",
            )
        )
        assert (plan.method, plan.tone) == (view.WRITE_REFUSED, view.TONE_CRIT)
        assert _gate(plan=plan) == "This is the 2023 FW_01 binary."

    def test_an_unanswered_upload_is_the_copy_by_hand_and_says_how_to_ask_again(self):
        plan = self._plan(staged=None, stage_error="Daemon unavailable during the file upload.")
        assert plan.method == view.WRITE_MANUAL and not plan.pending
        assert "Daemon unavailable during the file upload" in plan.text
        assert "Read again" in plan.text
        assert _gate(plan=plan) == "", "the copy by hand can still start"

    def test_an_older_daemon_or_a_refused_file_never_asks(self):
        older = self._plan(supported=False)
        assert older.method == view.WRITE_MANUAL and "does not write" in older.text
        bad = inspect_uf2(uf2_file(image(names=False)))
        assert self._plan(inspection=bad) == view.WritePlan(view.WRITE_MANUAL)

    def test_the_confirmation_names_who_writes(self):
        daemon, manual = view.confirm_text(view.WRITE_DAEMON), view.confirm_text(view.WRITE_MANUAL)
        assert daemon != manual
        assert (
            "cannot finish writing" in daemon
            and "copy the file onto the board's drive myself" in manual
        )


class TestDaemonWriteRun:
    def test_a_copy_by_hand_has_no_write_stage_and_a_daemon_write_no_copy(self):
        manual = [token for token, _ in view.run_stages(_record())]
        assert "waiting_for_file" in manual and "writing_firmware" not in manual
        daemon = [t for t, _ in view.run_stages(_write_record("writing_firmware", "writing"))]
        assert "writing_firmware" in daemon and "waiting_for_file" not in daemon

    def test_a_write_that_fell_back_shows_the_copy_as_the_current_stage(self):
        record = _write_record("waiting_for_file", "fell_back", fallback_reason="transfer_failed")
        tokens = [t for t, _ in view.run_stages(record)]
        assert tokens.index("writing_firmware") < tokens.index("waiting_for_file")
        rows = {row.token: row.state for row in view.build_run_view(record, NOW).stages}
        assert rows["writing_firmware"] == view.ROW_DONE
        assert rows["waiting_for_file"] == view.ROW_CURRENT

    @pytest.mark.parametrize(
        ("phase", "words"),
        [
            ("identifying", "Nothing is written until"),
            ("writing", "Leave the board connected"),
            ("verifying", "check every byte"),
            ("rebooting", "Restarting the board"),
            ("brand_new", "(brand_new)"),
        ],
    )
    def test_each_phase_says_what_happens(self, phase, words):
        run = view.build_run_view(_write_record("writing_firmware", phase), NOW)
        assert words in run.instruction and run.wants_file is False

    def test_progress_counts_the_bytes_while_writing_and_reading_back(self):
        for phase in ("writing", "verifying"):
            record = _write_record("writing_firmware", phase, done_bytes=8192)
            assert view.build_run_view(record, NOW).progress == (8192, 40_960)
        identifying = _write_record("writing_firmware", "identifying", done_bytes=8192)
        assert view.build_run_view(identifying, NOW).progress is None
        assert view.write_progress(_write("writing", done_bytes=-5)) == (0, 40_960)
        assert view.write_progress(_write("writing", done_bytes=99_999)) == (40_960, 40_960)
        assert view.write_progress(_write("writing", done_bytes=5, total_bytes=0)) is None

    @pytest.mark.parametrize(
        ("reason", "changed", "verified", "why", "what"),
        [
            ("no_usb_access", False, False, "may not open USB devices", "Copy the prepared file"),
            ("transfer_failed", True, False, "already rewritten", "Copy the prepared file"),
            ("flash_id_mismatch", False, False, "not the controller", "only if you are sure"),
            ("no_restart", True, True, "written and read back", "Press the board's RESET"),
            ("brand_new", False, False, "brand_new", "Copy the prepared file"),
        ],
    )
    def test_a_fallback_says_why_what_it_left_and_what_to_do(
        self, reason, changed, verified, why, what
    ):
        record = _write_record(
            "waiting_for_file",
            "fell_back",
            fallback_reason=reason,
            flash_changed=changed,
            verified=verified,
        )
        run = view.build_run_view(record, NOW)
        assert run.instruction.startswith("Control-OFC could not write the firmware itself")
        assert why in run.instruction and what in run.instruction and run.wants_file

    def test_nothing_written_is_said_only_when_nothing_was(self):
        for changed in (False, True):
            text = view.fallback_text(
                _write("fell_back", fallback_reason="transfer_failed", flash_changed=changed)
            )
            assert ("Nothing was written" in text) is (not changed)

    def test_the_result_names_who_wrote_it_and_whose_flash_it_was(self):
        done = _finished(
            "exact_build_verified",
            firmware_write=_write("written", verified=True, flash_id=SERIAL),
        )
        rows = dict(view.write_rows(done))
        assert RELEASE_NAME in rows["Written by"] and "every byte read back" in rows["Written by"]
        assert rows["Flash id"] == f"{SERIAL} — this controller's serial number"
        other = _finished(
            "completed_build_not_confirmed",
            firmware_write=_write(
                "fell_back",
                fallback_reason="flash_id_mismatch",
                fallback_detail="the bootloader on USB port 8-8 has another board's flash, not "
                "this controller's — nothing was written",
                flash_id=OTHER_BOARD,
            ),
        )
        rows = dict(view.write_rows(other))
        assert rows["Flash id"] == f"{OTHER_BOARD} — not this controller's serial number"
        assert "another board's flash" in rows["Written by"]
        assert "copy by hand" in rows["Written by"]
        assert view.write_rows(_finished("completed_build_not_confirmed")) == []

    @pytest.mark.parametrize(
        ("write", "says", "never"),
        [
            (_write("pending"), "before Control-OFC wrote anything", "stopped at"),
            (
                _write("identifying", flash_id=SERIAL),
                "before Control-OFC wrote anything",
                "stopped at",
            ),
            (_write("writing", flash_changed=True), "part of the flash", "every byte read back"),
            (
                _write("rebooting", flash_changed=True, verified=True),
                "every byte read back",
                "part of the flash",
            ),
        ],
        ids=["pending", "identifying", "writing", "rebooting"],
    )
    def test_the_result_says_how_far_an_unfinished_write_got(self, write, says, never):
        record = _finished("needs_recovery", firmware_write=write)
        row = dict(view.write_rows(record))["Written by"]
        assert says in row and never not in row

    def test_needs_recovery_says_what_the_write_left_on_the_board(self):
        whole = view.outcome_view(_finished("needs_recovery"))
        reset = [step for step in whole.steps if "RESET" in step]
        assert reset, "precondition: with the old firmware whole, RESET is a way back"
        partial = view.outcome_view(
            _finished("needs_recovery", firmware_write=_write("writing", flash_changed=True))
        )
        assert partial.title == whole.title
        assert "no firmware was copied" not in partial.summary
        assert "rewritten" in partial.summary
        assert partial.steps[0].startswith("Copy a firmware file")
        assert not set(reset) & set(partial.steps), "RESET is not offered as a way back"
        written = view.outcome_view(
            _finished(
                "needs_recovery",
                firmware_write=_write("rebooting", flash_changed=True, verified=True),
            )
        )
        assert "read every byte back" in written.summary
        assert "RESET" in written.steps[0]
        # Nothing written: the old firmware is whole, as after a copy that never came.
        untouched = view.outcome_view(
            _finished("needs_recovery", firmware_write=_write("identifying", flash_id=SERIAL))
        )
        assert (untouched.summary, untouched.steps) == (whole.summary, whole.steps)

    def test_after_a_daemon_write_the_return_wait_claims_no_copy(self):
        copied = view.build_run_view(_record(stage="waiting_for_return"), NOW)
        assert "copy finished" in copied.instruction, "precondition: a copy by hand says so"
        written = view.build_run_view(
            _write_record("waiting_for_return", "written", flash_changed=True, verified=True), NOW
        )
        assert "copy" not in written.instruction.lower()
        assert "restart" in written.instruction
        # A write that fell back and was then copied by hand is a copy again.
        fell_back = _write_record(
            "waiting_for_return", "fell_back", fallback_reason="transfer_failed"
        )
        stages = [
            *fell_back.stages[:-1],
            OpenFanStageTiming("waiting_for_file", NOW - 5_000, NOW - 1_000),
            OpenFanStageTiming("waiting_for_return", NOW - 1_000, None),
        ]
        run = view.build_run_view(replace(fell_back, stages=stages), NOW)
        assert "copy finished" in run.instruction

    def test_only_its_own_outcome_claims_the_exact_build(self):
        exact = view.outcome_view(_finished("exact_build_verified"))
        assert exact.tone == view.TONE_OK and "read every byte back" in exact.summary
        unconfirmed = view.outcome_view(_finished("completed_build_not_confirmed"))
        assert "read every byte back" not in unconfirmed.summary

    def test_the_bundle_scrubs_every_serial_wherever_the_daemon_wrote_it(self):
        raw = {
            "expected_usb_serial": SERIAL,
            "notes": [
                f"the bootloader has the flash of board {OTHER_BOARD}, not of the {SERIAL} "
                "the update was started for"
            ],
            "firmware_write": {
                "phase": "fell_back",
                "flash_id": OTHER_BOARD,
                "fallback_detail": f"flash of board {OTHER_BOARD.lower()}",
            },
        }
        out = view.bundle_record(raw)
        text = json.dumps(out)
        assert SERIAL not in text and OTHER_BOARD not in text
        assert OTHER_BOARD.lower() not in text
        assert out["firmware_write"]["flash_id"] == view.REDACTED
        assert "has the flash of board" in out["notes"][0], "the daemon's words stay"
        assert raw["firmware_write"]["flash_id"] == OTHER_BOARD, "the original is untouched"
        never = view.bundle_record({"firmware_write": {"flash_id": None}})
        assert never["firmware_write"]["flash_id"] is None, "how far the write got stays"


class TestWireDaemonWrite:
    def test_the_device_answer_carries_daemon_write(self):
        answer = {
            "present": True,
            "daemon_write": {"available": False, "reason": "r", "message": "m"},
        }
        assert parse_openfan_device(answer).daemon_write == OpenFanDaemonWrite(False, "r", "m")
        assert parse_openfan_device({"present": True}).daemon_write is None
        assert parse_openfan_device({"daemon_write": "yes"}).daemon_write is None
        loose = parse_openfan_device({"daemon_write": {"available": "true"}})
        assert loose.daemon_write == OpenFanDaemonWrite(available=False)

    def test_an_upload_answer(self):
        staged = parse_openfan_firmware_staged(
            {
                "api_version": 1,
                "sha256": "AB" * 32,
                "size": 79_360,
                "release": RELEASE_NAME,
                "verdict": "daemon_write",
                "message": "m",
            }
        )
        assert staged == OpenFanFirmwareStaged(
            "ab" * 32, 79_360, RELEASE_NAME, "daemon_write", None, "m"
        )
        bad = parse_openfan_firmware_staged({"size": "big", "verdict": 3, "reason": 4})
        assert bad == OpenFanFirmwareStaged()

    def test_a_record_carries_the_write(self):
        write = {
            "release": RELEASE_NAME,
            "phase": "written",
            "done_bytes": 4096,
            "total_bytes": 4096,
            "flash_id": SERIAL,
            "flash_changed": True,
            "verified": True,
            "fallback_reason": None,
            "fallback_detail": None,
        }
        parsed = parse_openfan_maintenance_record(dict(RECORD_JSON, firmware_write=write))
        assert parsed.firmware_write == OpenFanFirmwareWrite(
            RELEASE_NAME, "written", 4096, 4096, SERIAL, True, True, None, None
        )
        assert parse_openfan_maintenance_record(RECORD_JSON).firmware_write is None
        nulled = parse_openfan_maintenance_record(dict(RECORD_JSON, firmware_write=None))
        assert nulled.firmware_write is None

    def test_the_capability_flag_gates_through_the_registry(self):
        caps = parse_capabilities({"control": {"openfan_firmware_write": True}})
        assert caps.control.openfan_firmware_write is True
        assert daemon_supports("openfan_firmware_write", caps) is True
        assert daemon_supports("openfan_firmware_write", parse_capabilities({})) is False


class TestClientDaemonWrite:
    @staticmethod
    def _client(seen: list, answer: dict):
        from control_ofc.api.client import DaemonClient

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=answer)

        client = DaemonClient.__new__(DaemonClient)
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://localhost"
        )
        return client

    def test_the_upload_is_the_raw_file_and_its_answer_is_parsed(self):
        seen: list[httpx.Request] = []
        client = self._client(
            seen,
            {"sha256": "ab" * 32, "size": 3, "verdict": "manual_copy", "reason": "unknown_build"},
        )
        staged = client.stage_openfan_firmware(b"\x00\x01\x02")
        (request,) = seen
        assert (request.method, request.url.path) == ("PUT", "/fans/openfan/firmware")
        assert request.content == b"\x00\x01\x02"
        assert request.headers["content-type"] == "application/octet-stream"
        assert (staged.verdict, staged.reason, staged.size) == ("manual_copy", "unknown_build", 3)

    def test_only_a_daemon_write_names_the_writer(self):
        seen: list[httpx.Request] = []
        client = self._client(seen, {"run_id": "r1"})
        assert client.start_openfan_maintenance(SERIAL, {"sha256": "x"}) == "r1"
        client.start_openfan_maintenance(SERIAL, {"sha256": "x"}, daemon_write=True)
        manual, daemon = (json.loads(r.content) for r in seen)
        assert "write" not in manual, "a copy by hand reads the same to every daemon"
        assert daemon["write"] == "daemon"
        assert manual["firmware"] == daemon["firmware"] == {"sha256": "x"}


class TestWorkerDaemonWrite:
    def test_an_upload_answers_with_the_daemons_verdict(self, qapp):
        fake = _FakeClient()
        fake.staged = _staged()
        worker, _ = _worker(fake)
        staged = []
        worker.staged.connect(staged.append)
        worker.do_stage(b"abc")
        assert fake.calls == ["stage:3"] and staged == [fake.staged]

    def test_a_failed_upload_names_the_file_by_its_fingerprint(self, qapp):
        fake = _FakeClient()
        fake.stage_error = DaemonUnavailable()
        worker, _ = _worker(fake)
        failed = []
        worker.stage_failed.connect(lambda c, m, sha: failed.append((c, m, sha)))
        worker.do_stage(b"abc")
        assert failed == [
            (
                "unavailable",
                "Daemon unavailable during the file upload.",
                hashlib.sha256(b"abc").hexdigest(),
            )
        ]

    def test_a_daemon_write_hands_the_file_over_again_then_starts(self, qapp):
        data = uf2_file(image())
        sha = hashlib.sha256(data).hexdigest()
        fake = _FakeClient()
        fake.staged = _staged(data)
        worker, got = _worker(fake)
        staged = []
        worker.staged.connect(staged.append)
        worker.do_start(SERIAL, {"sha256": sha}, data)
        assert fake.calls == [f"stage:{len(data)}", f"start:{SERIAL}:{sha}:daemon", "status"]
        assert staged == [fake.staged], "the window sees the fresh answer"
        assert got["started"] == ["ofmaint-1"] and got["start_failed"] == []

    @pytest.mark.parametrize(
        "change",
        [{"verdict": "manual_copy", "reason": "unknown_build"}, {"sha256": "cd" * 32}],
        ids=["not-kept", "another-file"],
    )
    def test_a_daemon_that_would_not_write_it_now_starts_nothing(self, qapp, change):
        data = uf2_file(image())
        fake = _FakeClient()
        fake.staged = _staged(data, **change)
        worker, got = _worker(fake)
        worker.do_start(SERIAL, {"sha256": hashlib.sha256(data).hexdigest()}, data)
        assert fake.calls == [f"stage:{len(data)}"], "no start was sent"
        assert len(got["start_failed"]) == 1
        assert "will not write this file itself now" in got["start_failed"][0][1]
        assert got["started"] == [] and got["unconfirmed"] == []

    def test_an_upload_that_fails_before_the_start_starts_nothing(self, qapp):
        data = uf2_file(image())
        fake = _FakeClient()
        fake.stage_error = DaemonTimeout(message="slow")
        worker, got = _worker(fake)
        worker.do_start(SERIAL, {"sha256": "x"}, data)
        assert fake.calls == [f"stage:{len(data)}"]
        assert got["start_failed"] == [
            ("unavailable", "The daemon could not be given the file again: slow")
        ]
        assert got["unconfirmed"] == [], "nothing was sent that could have started a run"


class TestWindowDaemonWrite:
    def _choose(self, qtbot, tmp_path, monkeypatch, *, access=CAN_WRITE, supported=True):
        """A window that may ask the daemon to write, with a checked file chosen."""
        dialog = _dialog(qtbot, tmp_path, daemon_write_supported=supported)
        uploads: list[bytes] = []
        dialog.stage_requested.connect(uploads.append)
        dialog.set_live_status("connected", None)
        dialog.apply_device(_device(daemon_write=access))
        path = _good_file(tmp_path)
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
        )
        dialog.findChild(QPushButton, "OfwDialog_Btn_choose").click()
        return dialog, uploads, path.read_bytes()

    def test_a_chosen_file_goes_to_the_daemon_and_start_waits_for_its_answer(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog, uploads, data = self._choose(qtbot, tmp_path, monkeypatch)
        assert uploads == [data]
        start = dialog.findChild(QPushButton, "OfwDialog_Btn_start")
        dialog._confirm.setChecked(True)
        assert not start.isEnabled() and start.toolTip() == "Asking the daemon about the file…"
        dialog.apply_staged(_staged(data))
        writer = dialog.findChild(QLabel, "OfwDialog_Label_writer")
        assert writer.isVisibleTo(dialog) and f"writes the {RELEASE_NAME} itself" in writer.text()

    def test_a_daemon_write_starts_with_the_files_bytes(self, qtbot, tmp_path, monkeypatch):
        dialog, _, data = self._choose(qtbot, tmp_path, monkeypatch)
        dialog.apply_staged(_staged(data))
        assert dialog._confirm.text() == view.confirm_text(view.WRITE_DAEMON)
        dialog._confirm.setChecked(True)
        sent = []
        dialog.start_requested.connect(lambda s, c, w: sent.append((s, c, w)))
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        assert sent == [(SERIAL, dialog._inspection.claim(), data)]

    def test_without_usb_access_the_same_file_is_copied_by_hand(self, qtbot, tmp_path, monkeypatch):
        dialog, _, data = self._choose(qtbot, tmp_path, monkeypatch, access=NO_ACCESS)
        dialog.apply_staged(_staged(data))
        writer = dialog.findChild(QLabel, "OfwDialog_Label_writer")
        assert "openfan-firmware-write" in writer.text()
        assert dialog._confirm.text() == view.confirm_text(view.WRITE_MANUAL)
        dialog._confirm.setChecked(True)
        sent = []
        dialog.start_requested.connect(lambda s, c, w: sent.append((s, c, w)))
        dialog.findChild(QPushButton, "OfwDialog_Btn_start").click()
        assert sent == [(SERIAL, dialog._inspection.claim(), None)]

    def test_the_file_to_copy_is_offered_only_while_the_user_would_copy_it(
        self, qtbot, tmp_path, monkeypatch
    ):
        dialog, _, data = self._choose(qtbot, tmp_path, monkeypatch)
        box = dialog.findChild(QWidget, "OfwDialog_Widget_prepared")
        assert box.isVisibleTo(dialog), "presence first: offered until the daemon answers"
        dialog.apply_staged(_staged(data))
        assert not box.isVisibleTo(dialog)
        assert dialog._prepared is not None, "still prepared, as the fallback"

    def test_a_change_of_writer_takes_the_confirmation_back(self, qtbot, tmp_path, monkeypatch):
        dialog, _, data = self._choose(qtbot, tmp_path, monkeypatch, access=NO_ACCESS)
        dialog.apply_staged(_staged(data))
        dialog._confirm.setChecked(True)
        dialog.apply_device(_device(daemon_write=NO_ACCESS))
        assert dialog._confirm.isChecked(), "presence first: the same writer keeps it"
        dialog.apply_device(_device(daemon_write=CAN_WRITE))  # the drop-in, then Read again
        assert not dialog._confirm.isChecked()
        assert dialog._confirm.text() == view.confirm_text(view.WRITE_DAEMON)

    def test_an_answer_about_another_file_is_ignored(self, qtbot, tmp_path, monkeypatch):
        dialog, _, _data = self._choose(qtbot, tmp_path, monkeypatch)
        other = hashlib.sha256(b"another file").hexdigest()
        dialog.apply_staged(_staged(b"another file"))
        dialog.apply_stage_error("unavailable", "gone", other)
        assert dialog._plan().pending
        dialog.apply_stage_error("unavailable", "gone", dialog._inspection.sha256)
        assert dialog._plan().method == view.WRITE_MANUAL and not dialog._plan().pending

    def test_read_again_asks_about_the_file_again(self, qtbot, tmp_path, monkeypatch):
        dialog, uploads, data = self._choose(qtbot, tmp_path, monkeypatch)
        dialog.apply_stage_error("unavailable", "gone", dialog._inspection.sha256)
        assert not dialog._plan().pending
        dialog.findChild(QPushButton, "OfwDialog_Btn_refresh").click()
        assert uploads == [data, data] and dialog._plan().pending

    def test_an_older_daemon_is_never_sent_the_file(self, qtbot, tmp_path, monkeypatch):
        dialog, uploads, _ = self._choose(qtbot, tmp_path, monkeypatch, supported=False)
        assert dialog._inspection is not None and dialog._inspection.ok, "a file was checked"
        assert uploads == []
        assert dialog._plan().method == view.WRITE_MANUAL

    def test_fw_01_is_refused_before_anything_is_sent(self, qtbot, tmp_path, monkeypatch):
        data = uf2_file(image())
        broken = KnownRelease(
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            name="2023-09-29 release (FW_01)",
            where="test",
            broken="This is the 2023 FW_01 binary.",
        )
        monkeypatch.setattr(uf2, "KNOWN_RELEASES", (broken,))
        dialog, uploads, _ = self._choose(qtbot, tmp_path, monkeypatch)
        assert uploads == []
        finding = dialog.findChild(QLabel, "OfwDialog_Label_finding0")
        assert finding.text() == broken.broken
        dialog._confirm.setChecked(True)
        assert dialog.findChild(QPushButton, "OfwDialog_Btn_start").isEnabled() is False
        assert not (tmp_path / "prepared").exists() or not list((tmp_path / "prepared").iterdir())

    def test_the_write_shows_its_progress(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path, daemon_write_supported=True)
        dialog.apply_run(_write_record("writing_firmware", "writing", done_bytes=8192))
        bar = dialog.findChild(QProgressBar, "OfwDialog_Progress_write")
        assert bar.isVisibleTo(dialog)
        assert (bar.value(), bar.maximum(), bar.format()) == (8192, 40_960, "8 of 40 KiB")
        dialog.apply_run(_write_record("waiting_for_return", "written", done_bytes=40_960))
        assert not bar.isVisibleTo(dialog)

    def test_a_fallback_offers_the_prepared_copy(self, qtbot, tmp_path):
        data = uf2_file(image())
        r = inspect_uf2(data)
        prepared = prepare_firmware(data, r, tmp_path / "prepared")
        dialog = _dialog(qtbot, tmp_path, daemon_write_supported=True)
        record = _write_record("waiting_for_file", "fell_back", fallback_reason="no_usb_access")
        dialog.apply_run(replace(record, firmware=OpenFanFirmwareClaim(r.sha256, len(data))))
        handle = dialog.findChild(dlg_mod.FileDragHandle, "OfwDialog_Label_dragFile")
        assert handle.path == prepared and dialog._prepared_box.isVisibleTo(dialog)
        instruction = dialog.findChild(QLabel, "OfwDialog_Label_instruction")
        assert instruction.text().startswith("Control-OFC could not write the firmware itself")

    def test_the_result_says_who_wrote_it(self, qtbot, tmp_path):
        dialog = _dialog(qtbot, tmp_path, daemon_write_supported=True)
        dialog.apply_run(_write_record("writing_firmware", "writing"))
        dialog.apply_run(
            _finished(
                "exact_build_verified",
                firmware_write=_write("written", verified=True, flash_id=SERIAL),
            )
        )
        assert dialog.mode == dlg_mod.MODE_RESULT
        name = dialog.findChild(QLabel, "OfwDialog_EvidenceLabel_0")
        value = dialog.findChild(QLabel, "OfwDialog_EvidenceValue_0")
        assert name.text() == "Written by" and "every byte read back" in value.text()


class TestHardwarePageDaemonWrite:
    @pytest.mark.parametrize("advertised", [True, False])
    def test_the_window_may_ask_the_daemon_to_write_only_where_it_can(
        self, qtbot, monkeypatch, advertised
    ):
        page = _page(qtbot, write=advertised)
        monkeypatch.setattr(page, "_ensure_ofw_worker", lambda: True)
        _button(page).click()
        assert page._ofw_dialog is not None
        expected = page._state.capabilities.control.openfan_firmware_write
        assert page._ofw_dialog._write_supported is expected

    def test_an_upload_reaches_the_daemon_and_its_answer_the_window(
        self, qtbot, monkeypatch, tmp_path
    ):
        page = _page(qtbot, write=True)
        data = uf2_file(image())
        fake = _FakeClient(no_run=True)
        fake.staged = _staged(data)

        def build(worker, thread, worker_cls, connect):
            # The page's own wiring, on a worker that talks to no daemon.
            w = worker_cls("/nonexistent.sock")
            w._client = fake
            connect(w)
            return w, None, True

        monkeypatch.setattr(page, "_ensure_worker", build)
        monkeypatch.setattr("control_ofc.paths.cache_dir", lambda: tmp_path)
        _button(page).click()
        dialog = page._ofw_dialog
        assert dialog is not None
        path = tmp_path / "OpenFAN_Firmware.uf2"
        path.write_bytes(data)
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
        )
        dialog.findChild(QPushButton, "OfwDialog_Btn_choose").click()
        qtbot.waitUntil(lambda: dialog._staged is not None, timeout=2000)
        assert dialog._staged == fake.staged and f"stage:{len(data)}" in fake.calls
        fake.stage_error = DaemonUnavailable()
        dialog.findChild(QPushButton, "OfwDialog_Btn_refresh").click()
        qtbot.waitUntil(lambda: bool(dialog._stage_error), timeout=2000)
        page.cleanup()
