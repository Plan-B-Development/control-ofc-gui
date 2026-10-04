"""What the OpenFAN firmware update window says (DEC-481, DEC-482).

Qt-free: every decision about wording, tone and what is shown lives here and is
unit-tested headlessly; ``ui/widgets/openfan_firmware_dialog.py`` renders it.

The daemon runs the update; the user copies the file. So this text has two jobs
the rest of the GUI rarely has: tell the user exactly what to do at one stage,
and never claim more than the daemon can know. It cannot know which build is
running — the firmware has no build identifier — so a result says what the
board's own reports show, and no more.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from control_ofc.api.models import (
    OpenFanDevice,
    OpenFanMaintenanceRecord,
    OpenFanMaintenanceSummary,
)
from control_ofc.services.cooling_watch import CoolingAlert
from control_ofc.services.uf2 import RELEASES_URL, Uf2Inspection

# ── Stages ────────────────────────────────────────────────────────────

#: The run's stages in order, with what each does in a few words.
STAGES: tuple[tuple[str, str], ...] = (
    ("preparing", "Check the controller"),
    ("parking", "Set every OpenFAN channel to 100 %"),
    ("entering_bootloader", "Put the board in update mode"),
    ("waiting_for_file", "Copy the firmware file"),
    ("waiting_for_return", "Wait for the board to restart"),
    ("checking", "Check the board answers"),
    ("restoring_control", "Restore fan control"),
)
_STAGE_LABELS = dict(STAGES)
STAGE_FINISHED = "finished"

#: Stage-row states.
ROW_DONE = "done"
ROW_CURRENT = "current"
ROW_STOPPED = "stopped"
ROW_PENDING = "pending"

STATE_RUNNING = "running"
STATE_NEEDS_RECOVERY = "needs_recovery"


def stage_label(token: str) -> str:
    """A stage's label, or the token itself when this client does not know it."""
    if token == STAGE_FINISHED:
        return "Finished"
    return _STAGE_LABELS.get(token, token or "unknown stage")


def _mid_sentence(label: str) -> str:
    """A label placed inside a sentence: only its first letter is lowered, so a
    name in it keeps its capitals (``OpenFAN``)."""
    return label[:1].lower() + label[1:]


# ── Connection ────────────────────────────────────────────────────────

_LINK_TEXT = {
    "connected": "Connected",
    "unresponsive": "Not answering — the daemon is retrying",
    "reconnecting": "Reconnecting",
    "maintenance": "Held by a firmware update",
}


def link_text(link: str | None) -> str:
    """``status.openfan_link`` in words. An unknown token is shown as sent."""
    if link is None:
        return "Unknown"
    return _LINK_TEXT.get(link, link)


# ── Fixed text ────────────────────────────────────────────────────────

INTRO = (
    "This updates the OpenFAN controller's firmware from a file you choose. "
    "Control-OFC checks the file, puts the board in update mode and watches it; "
    "you copy the file onto the board's RPI-RP2 drive. Control-OFC never writes "
    "the firmware itself."
)

#: The three cooling windows (§3.5), in the order they happen.
COOLING_WINDOWS: tuple[str, ...] = (
    "When the update starts, every OpenFAN channel — pumps included — is set to "
    "100 %. Expect it to be loud.",
    "While the board is in update mode, its fan chips should keep that 100 % — "
    "the board's design says so, though it has not yet been measured — and "
    "Control-OFC cannot change these channels. Fans on the motherboard stay under "
    "normal control, and a thermal emergency still forces them.",
    "When the new firmware starts, it runs every channel at its own default for a "
    "few seconds — in the published firmware, about 1000 RPM and never below "
    "40 % — until Control-OFC takes over again. A pump on the board slows down "
    "during that window.",
    "If the board does not come back, its channels stay on that default until it does.",
)

LEAVE_UPDATE_MODE = (
    "Once the board is in update mode it leaves it in one of three ways: a firmware "
    "file is copied onto its RPI-RP2 drive, its RESET button is pressed, or the PC "
    "is switched off and on. Control-OFC cannot take it out of update mode by itself."
)

CLOSE_NOTE = (
    "Closing this window does not stop the update. Reopen it from the Hardware page to follow it."
)

CONFIRM_TEXT = (
    "I understand that the OpenFAN channels run at 100 % during the update and on "
    "the firmware's own default for a few seconds when it restarts, and that I copy "
    "the file onto the board's drive myself."
)

PREPARED_NOTE = (
    "This is the checked copy. Nothing can prove which file reaches the drive — "
    "after the update, the board's own reports are the evidence, shown with the result."
)

DEMO_REFUSAL = "Demo mode has no OpenFAN controller to update."


# ── The controller ────────────────────────────────────────────────────

#: The information keys shown first, in this order; any others follow.
_INFO_ORDER = ("HW_REV", "FW_REV", "PROTOCOL_VERSION", "MCU")


def format_info(info: Mapping[str, str] | None) -> str:
    """``HW_REV 01 · FW_REV 01 · …``, or ``""`` when there is nothing to show."""
    if not info:
        return ""
    keys = [k for k in _INFO_ORDER if k in info] + sorted(k for k in info if k not in _INFO_ORDER)
    return " · ".join(f"{k} {info[k]}" for k in keys)


def device_rows(device: OpenFanDevice | None) -> list[tuple[str, str]]:
    """The Controller section: what the daemon can say about the board."""
    if device is None:
        return [("Controller", "Reading…")]
    if not device.present:
        return [("Controller", "No OpenFAN controller is connected.")]
    usb = device.usb
    unknown = "Unknown"
    no_answer = "No answer — unknown"
    return [
        ("Connection", link_text(device.link)),
        ("USB serial number", (usb.serial if usb else None) or unknown),
        ("USB port", (usb.port if usb else None) or unknown),
        ("Serial device", device.port or unknown),
        ("Hardware report", format_info(device.hw_info) or no_answer),
        ("Firmware report", format_info(device.fw_info) or no_answer),
    ]


# ── The file ──────────────────────────────────────────────────────────

TONE_OK = "ok"
TONE_INFO = "info"
TONE_WARN = "warn"
TONE_CRIT = "crit"


@dataclass(frozen=True)
class Finding:
    tone: str
    text: str


def file_rows(inspection: Uf2Inspection) -> list[tuple[str, str]]:
    """The File section's summary of a checked file."""
    rows = [
        ("Fingerprint (SHA-256)", inspection.sha256),
        ("Size", f"{inspection.size:,} bytes ({inspection.blocks} blocks)"),
    ]
    if not inspection.ok:
        return rows
    not_recorded = "Not recorded in the file"
    rows += [
        ("Program", inspection.program_name or not_recorded),
        ("Built", inspection.build_date or not_recorded),
        ("Pico SDK", inspection.sdk_version or not_recorded),
        ("Build type", inspection.build_type or not_recorded),
        ("Reports", format_info(inspection.info) or "Could not be read from the file"),
    ]
    return rows


def file_findings(inspection: Uf2Inspection, device: OpenFanDevice | None) -> list[Finding]:
    """What the checks found, each with its limits. Never a gate: the problems
    that refuse a file are :attr:`Uf2Inspection.problems`."""
    if not inspection.ok:
        return [Finding(TONE_CRIT, p) for p in inspection.problems]
    found: list[Finding] = []
    release = inspection.release
    if release is not None:
        found.append(
            Finding(
                TONE_OK,
                f"Matches the published fingerprint of the {release.name} ({release.where}).",
            )
        )
    else:
        found.append(
            Finding(
                TONE_INFO,
                "Not one of the published files Control-OFC knows. A newer release or "
                "your own build will not be listed — check where this file came from. "
                f"Releases: {RELEASES_URL}",
            )
        )

    board: dict[str, str] = {}
    if device is not None:
        board.update(device.hw_info or {})
        board.update(device.fw_info or {})
    info = dict(inspection.info or {})
    hw_file, hw_board = info.get("HW_REV"), board.get("HW_REV")
    if hw_file and hw_board and hw_file != hw_board:
        # Q4: shown, never a gate.
        found.append(
            Finding(
                TONE_INFO,
                f"The file reports hardware revision {hw_file}; your board's firmware "
                f"reports {hw_board}. This is a fixed string built into each firmware, "
                "not read from the board, so Control-OFC shows it and does not block on it.",
            )
        )
    proto_file, proto_board = info.get("PROTOCOL_VERSION"), board.get("PROTOCOL_VERSION")
    if proto_file and proto_board and proto_file != proto_board:
        found.append(
            Finding(
                TONE_INFO,
                f"The file reports protocol version {proto_file}; the running firmware "
                f"reports {proto_board}. If the new firmware does not answer Control-OFC's "
                "commands, the result says so — that is not a failed copy.",
            )
        )

    desc_file = inspection.usb_config_descriptor_hex
    desc_board = device.usb.config_descriptor_hex if device and device.usb else None
    same_reports = bool(info) and all(board.get(k) == v for k, v in info.items())
    if desc_file is None:
        found.append(
            Finding(
                TONE_INFO,
                "No single USB descriptor could be found in this file, so the result "
                "relies on the firmware's reports alone.",
            )
        )
    elif desc_board is not None and desc_file == desc_board:
        if same_reports:
            found.append(
                Finding(
                    TONE_WARN,
                    "This file looks the same as the firmware already running in every "
                    "way Control-OFC can check, so the result will not be able to "
                    "confirm the update.",
                )
            )
        else:
            found.append(
                Finding(
                    TONE_INFO,
                    "The file's USB descriptor is the same as the running firmware's, so "
                    "afterwards only the firmware's reports can show which one runs.",
                )
            )
    elif desc_board is not None:
        found.append(
            Finding(
                TONE_INFO,
                "The file's USB descriptor differs from the running firmware's, so "
                "afterwards the board's descriptor shows which one runs.",
            )
        )
    if inspection.info is None:
        found.append(
            Finding(
                TONE_INFO,
                "The firmware's reports could not be read from this file unambiguously, "
                "so the result relies on the USB descriptor alone.",
            )
        )
    return found


# ── Cooling ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelLine:
    fan_id: str
    name: str
    pump: bool

    @property
    def text(self) -> str:
        return f"{self.name} — pump" if self.pump else self.name


def channel_lines(
    fan_ids: Iterable[str],
    name: Callable[[str], str],
    is_pump_protected: Callable[[str], bool],
) -> list[ChannelLine]:
    """The OpenFAN channels the update affects, each marked when the daemon
    protects it as a pump (the union rule, never the role name alone)."""
    ids = sorted({f for f in fan_ids if f.startswith("openfan:")})
    return [ChannelLine(f, name(f), is_pump_protected(f)) for f in ids]


def pump_note(lines: Iterable[ChannelLine]) -> str:
    pumps = [line.name for line in lines if line.pump]
    if not pumps:
        return ""
    names = ", ".join(pumps)
    return (
        f"Pump: {names}. It is set to 100 % like every channel, and runs on the "
        "firmware's default for the few seconds the new firmware takes to start."
    )


# ── Starting ──────────────────────────────────────────────────────────


def start_block_reason(
    *,
    demo: bool,
    device: OpenFanDevice | None,
    live_link: str | None,
    external_block: str,
    inspection: Uf2Inspection | None,
    prepared: bool,
    confirmed: bool,
    starting: bool,
) -> str:
    """Why Start is unavailable, or ``""`` when it is available.

    Informational, like the device answer it reads: the daemon decides again,
    atomically, when the request arrives.
    """
    if demo:
        return DEMO_REFUSAL
    if starting:
        return "Starting…"
    if external_block:
        return external_block
    if device is None:
        return "Reading the controller…"
    if not device.present:
        return "No OpenFAN controller is connected."
    if live_link != "connected":
        return f"The OpenFAN controller is not connected ({link_text(live_link).lower()})."
    if not device.update_available:
        first = device.update_refusals[0] if device.update_refusals else None
        if first is None:
            return "The daemon cannot start an update now."
        # The daemon's own words; a refusal without them shows its token rather
        # than nothing.
        return first.message or f"The daemon cannot start an update now ({first.reason})."
    if not (device.usb and device.usb.serial):
        return "The daemon could not read the controller's USB serial number."
    if inspection is None:
        return "Choose a firmware file."
    if not inspection.ok:
        return "The chosen file cannot be used."
    if not prepared:
        return "The checked copy of the file could not be prepared."
    if not confirmed:
        return "Tick the confirmation first."
    return ""


# ── Outcomes ──────────────────────────────────────────────────────────

NO_FIRMWARE_CHANGE = "no_firmware_change"
NEEDS_RECOVERY = "needs_recovery"
FIRMWARE_COPIED_BOARD_NOT_BACK = "firmware_copied_board_not_back"
BOARD_BACK_CONTROL_NOT_RESTORED = "board_back_control_not_restored"
EXACT_BUILD_VERIFIED = "exact_build_verified"
COMPLETED_BUILD_NOT_CONFIRMED = "completed_build_not_confirmed"
BACK_ON_PREVIOUS_FIRMWARE = "back_on_previous_firmware"

VERDICT_CONSISTENT = "consistent_with_file"
VERDICT_PREVIOUS = "previous_firmware"
VERDICT_INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class OutcomeView:
    token: str
    tone: str
    title: str
    summary: str
    steps: tuple[str, ...] = ()
    #: The daemon's own sentence about how it ended.
    detail: str = ""


_RECOVERY_STEPS = (
    "Copy a firmware file onto the board's RPI-RP2 drive — the prepared file, or the "
    "firmware you had before.",
    "Or press the board's RESET button.",
    "Or switch the PC off and on.",
    "If no RPI-RP2 drive appears, follow the BOOT-button procedure in the OpenFAN "
    "firmware documentation, then copy a file.",
    "Control-OFC keeps watching for the board and takes it back as soon as it answers.",
)

_NOT_BACK_STEPS = (
    "Wait a minute: Control-OFC keeps watching for the board and takes it back as soon "
    "as it answers.",
    "If the board is back on USB but never answers, the new firmware may not understand "
    "Control-OFC's commands. That is possible with new firmware and is not a failed copy: "
    "copy back the firmware you had before (put the board in update mode with its BOOT "
    "button), or wait for a Control-OFC update.",
    "If the board does not appear at all, press its RESET button or switch the PC off and on.",
)

_OUTCOMES: dict[str, OutcomeView] = {
    NEEDS_RECOVERY: OutcomeView(
        NEEDS_RECOVERY,
        TONE_CRIT,
        "The board needs recovery",
        "The board is in update mode, or may be, and no firmware was copied. Its OpenFAN "
        "channels should still hold the 100 % they were parked at, unless the board has "
        "restarted.",
        _RECOVERY_STEPS,
    ),
    FIRMWARE_COPIED_BOARD_NOT_BACK: OutcomeView(
        FIRMWARE_COPIED_BOARD_NOT_BACK,
        TONE_CRIT,
        "Firmware copied, board not back",
        "The update drive went away, but the board did not come back answering "
        "Control-OFC. Its fans run on the firmware's own default.",
        _NOT_BACK_STEPS,
    ),
    BOARD_BACK_CONTROL_NOT_RESTORED: OutcomeView(
        BOARD_BACK_CONTROL_NOT_RESTORED,
        TONE_CRIT,
        "Board back, fan control not confirmed",
        "The board answers, but its fan settings had not all landed when the time ran "
        "out. Fan control carries on trying.",
        (
            "Watch the OpenFAN fans on the Dashboard: they should follow the profile "
            "within a few seconds.",
            "If they do not, restart the daemon: it reconnects to the board and drives "
            "the fans from the profile again.",
        ),
    ),
    EXACT_BUILD_VERIFIED: OutcomeView(
        EXACT_BUILD_VERIFIED,
        TONE_OK,
        "Update complete — exact build verified",
        "The board is back under fan control, and its flash holds exactly the selected file.",
    ),
    BACK_ON_PREVIOUS_FIRMWARE: OutcomeView(
        BACK_ON_PREVIOUS_FIRMWARE,
        TONE_WARN,
        "The update was not applied",
        "The board is back under fan control, but its reports match the firmware it had "
        "before, not the selected file.",
        ("Check that the right file went onto the right drive, then start again.",),
    ),
}


def outcome_view(record: OpenFanMaintenanceRecord) -> OutcomeView | None:
    """The result of a finished run, or ``None`` while it runs."""
    if record.is_running:
        return None
    token = record.outcome or ""
    detail = record.outcome_detail or ""
    if token == NO_FIRMWARE_CHANGE:
        if record.cancelled:
            view = OutcomeView(
                token,
                TONE_INFO,
                "Update cancelled",
                "The board was never asked to enter update mode. Nothing changed, and "
                "its fans are back under profile control.",
                ("Start again whenever you are ready.",),
            )
        else:
            view = OutcomeView(
                token,
                TONE_WARN,
                "No firmware change",
                "The board was not updated, and its fans are back under profile control.",
                ("The reason is below. Deal with it, then start again.",),
            )
    elif token == COMPLETED_BUILD_NOT_CONFIRMED:
        verdict = record.evidence.verdict if record.evidence else ""
        consistent = verdict == VERDICT_CONSISTENT
        view = OutcomeView(
            token,
            TONE_OK if consistent else TONE_WARN,
            "Update complete" if consistent else "Update complete — firmware not confirmed",
            "The board is back under fan control. Control-OFC cannot read which build is "
            "running — the firmware has no build identifier — so the evidence below is "
            "what the board's own reports show.",
        )
    elif token in _OUTCOMES:
        view = _OUTCOMES[token]
    else:
        view = OutcomeView(
            token,
            TONE_WARN,
            f"Update finished ({token or 'no outcome'})",
            "This version of Control-OFC does not know this outcome; the details below "
            "are as the daemon recorded them.",
        )
    if record.interrupted:
        detail = (detail + " " if detail else "") + (
            "The daemon stopped during the update; when it started again it repeated nothing."
        )
    return OutcomeView(view.token, view.tone, view.title, view.summary, view.steps, detail)


def outcome_title(token: str | None) -> str:
    """A finished run's title from its outcome token alone (the status summary)."""
    record = OpenFanMaintenanceRecord(state="finished", outcome=token or "")
    view = outcome_view(record)
    return view.title if view else ""


# ── Evidence ──────────────────────────────────────────────────────────

_VERDICT_TEXT = {
    VERDICT_CONSISTENT: "Consistent with the selected file: the board's reports match it "
    "and differ from before the update.",
    VERDICT_PREVIOUS: "The board's reports match the firmware it had before, not the "
    "selected file.",
    VERDICT_INCONCLUSIVE: "Inconclusive: the board's reports cannot tell this file from "
    "the firmware it had before, or a report was missing.",
}


def _compared(changed: bool | None, matches: bool | None) -> str:
    if matches is None:
        return "Unknown"
    if matches:
        if changed is False:
            return "Matches the file — and is the same as before the update"
        return "Matches the file" + (" and changed" if changed else "")
    if changed is False:
        return "Unchanged — still the previous firmware's"
    return "Does not match the file"


def evidence_rows(record: OpenFanMaintenanceRecord) -> list[tuple[str, str]]:
    """What the board's reports show after the update, plainly."""
    ev = record.evidence
    if ev is None:
        return []
    return [
        ("Verdict", _VERDICT_TEXT.get(ev.verdict, ev.verdict or "Unknown")),
        ("USB descriptor", _compared(ev.descriptor_changed, ev.descriptor_matches_file)),
        ("Firmware reports", _compared(ev.info_changed, ev.info_matches_file)),
    ]


def info_rows(record: OpenFanMaintenanceRecord) -> list[tuple[str, str, str, str]]:
    """``(key, before, after, file)`` for every report key any side has."""

    def merged(hw: Mapping[str, str] | None, fw: Mapping[str, str] | None) -> dict[str, str]:
        out = dict(hw or {})
        out.update(fw or {})
        return out

    before = merged(record.before.hw_info, record.before.fw_info)
    after = merged(record.after.hw_info, record.after.fw_info) if record.after else {}
    claimed = dict(record.firmware.info or {})
    keys = [k for k in _INFO_ORDER if k in before or k in after or k in claimed]
    keys += sorted({*before, *after, *claimed} - set(keys))
    dash = "—"
    return [(k, before.get(k, dash), after.get(k, dash), claimed.get(k, dash)) for k in keys]


# ── A run ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageRow:
    token: str
    label: str
    state: str
    duration: str = ""


@dataclass(frozen=True)
class RunView:
    run_id: str
    running: bool
    can_cancel: bool
    headline: str
    instruction: str
    time_left: str
    #: Whether the user needs the prepared file now.
    wants_file: bool
    warnings: tuple[str, ...]
    stages: tuple[StageRow, ...]
    notes: tuple[str, ...]
    outcome: OutcomeView | None


def _seconds(ms: int) -> str:
    seconds = max(0, round(ms / 1000))
    if seconds < 90:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"


def time_left(deadline_unix_ms: int | None, now_unix_ms: int) -> str:
    if deadline_unix_ms is None:
        return ""
    left = deadline_unix_ms - now_unix_ms
    if left <= 0:
        return "The time for this stage is up; the daemon is ending it."
    return f"About {_seconds(left)} left for this stage."


def _stage_rows(record: OpenFanMaintenanceRecord) -> tuple[StageRow, ...]:
    timings: dict[str, list] = {}
    for t in record.stages:
        timings.setdefault(t.stage, []).append(t)
    ended_in = record.stage
    success = record.outcome in (
        COMPLETED_BUILD_NOT_CONFIRMED,
        BACK_ON_PREVIOUS_FIRMWARE,
        EXACT_BUILD_VERIFIED,
    )
    rows: list[StageRow] = []
    for token, label in STAGES:
        spans = timings.get(token, [])
        spent = sum(
            ((t.ended_unix_ms or t.started_unix_ms) - t.started_unix_ms)
            for t in spans
            if t.ended_unix_ms is not None
        )
        duration = _seconds(spent) if spans and all(t.ended_unix_ms for t in spans) else ""
        if record.is_running:
            if token == record.stage:
                state = ROW_CURRENT
            elif spans:
                state = ROW_DONE
            else:
                state = ROW_PENDING
        elif token == ended_in and not success:
            state = ROW_STOPPED
        elif spans:
            state = ROW_DONE
        else:
            state = ROW_PENDING
        rows.append(StageRow(token, label, state, duration))
    return tuple(rows)


def _instruction(record: OpenFanMaintenanceRecord) -> str:
    port = record.usb_port or "unknown"
    stage = record.stage
    if stage == "preparing":
        return "Checking the controller and reading what its firmware reports about itself."
    if stage == "parking":
        return "Setting every OpenFAN channel to 100 %."
    if stage == "entering_bootloader":
        text = "Asking the board to enter update mode. Its RPI-RP2 drive should appear shortly."
        if record.bootloader_trigger == "1200_baud":
            text += (
                " The board did not respond to the first request, so Control-OFC sent the "
                "1200-baud signal on the same port."
            )
        return text
    if stage == "waiting_for_file":
        drive = record.bootloader_drive
        where = (
            f"the RPI-RP2 drive {drive}, on USB port {port}"
            if drive
            else (f"the board's RPI-RP2 drive, on USB port {port}")
        )
        return (
            f"Copy the prepared file onto {where}. The board restarts by itself once the "
            "copy has finished."
        )
    if stage == "waiting_for_return":
        return (
            "The drive has gone, so the copy finished. Waiting for the board to restart "
            "with its new firmware."
        )
    if stage == "checking":
        return "The board is back on USB. Checking that it answers Control-OFC."
    if stage == "restoring_control":
        return (
            "Handing the board back to fan control, and waiting for the profile's "
            "settings to land on its channels."
        )
    return f"Stage: {stage_label(stage)}."


def build_run_view(record: OpenFanMaintenanceRecord, now_unix_ms: int) -> RunView:
    """Everything the window shows about one run."""
    running = record.is_running
    warnings: tuple[str, ...] = ()
    if running and record.other_bootloader_drives:
        others = ", ".join(record.other_bootloader_drives)
        warnings = (
            f"Another RPI-RP2 drive is present ({others}). It belongs to a different "
            "board — do not copy onto it.",
        )
    outcome = outcome_view(record)
    return RunView(
        run_id=record.run_id,
        running=running,
        can_cancel=running and record.cancellable,
        headline=(
            f"Update running — {_mid_sentence(stage_label(record.stage))}."
            if running
            else (outcome.title if outcome else "")
        ),
        instruction=_instruction(record) if running else "",
        time_left=time_left(record.stage_deadline_unix_ms, now_unix_ms) if running else "",
        wants_file=running and record.stage == "waiting_for_file",
        warnings=warnings,
        stages=_stage_rows(record),
        notes=tuple(record.notes),
        outcome=outcome,
    )


# ── DEC-482: one alert for the update ─────────────────────────────────

UPDATE_ALERT_RUNNING = "openfan_update:running"
UPDATE_ALERT_RECOVERY = "openfan_update:needs_recovery"


def firmware_update_alert(summary: OpenFanMaintenanceSummary | None) -> CoolingAlert | None:
    """The one alert that stands in for the OpenFAN fans' staleness warnings
    while an update holds the controller or left it needing recovery (DEC-482).

    Keyed on the state, so a run that ends needing recovery closes the warning
    and raises the error, and the event log records both.
    """
    if summary is None:
        return None
    if summary.needs_recovery:
        return CoolingAlert(
            key=UPDATE_ALERT_RECOVERY,
            level="error",
            title="OpenFAN firmware update needs recovery",
            detail=(
                f"{outcome_title(summary.outcome)}. The OpenFAN board is outside normal fan "
                "control until it answers again; Hardware → Update OpenFAN Firmware… "
                "shows the recovery steps."
            ),
        )
    return CoolingAlert(
        key=UPDATE_ALERT_RUNNING,
        level="warning",
        title="OpenFAN firmware update in progress",
        detail=(
            f"Stage: {_mid_sentence(stage_label(summary.stage))}. OpenFAN fan readings pause while "
            "the board is updated, so this alert stands in for their staleness warnings."
        ),
    )


def suppresses_fan_staleness(summary: OpenFanMaintenanceSummary | None, fan_id: str) -> bool:
    """Whether an update's alert stands in for this fan's staleness (DEC-482).
    Only the OpenFAN channels, and only while the daemon reports an update."""
    return summary is not None and fan_id.startswith("openfan:")


# ── Support bundle ────────────────────────────────────────────────────

REDACTED = "(redacted)"


def bundle_record(raw: Mapping) -> dict:
    """The last run as the support bundle carries it: the daemon's record with
    the board's USB serial number removed. The record holds no file path."""
    out = copy.deepcopy(dict(raw))
    if "expected_usb_serial" in out:
        out["expected_usb_serial"] = REDACTED
    for side in ("before", "after"):
        snap = out.get(side)
        usb = snap.get("usb") if isinstance(snap, dict) else None
        if isinstance(usb, dict) and "serial" in usb:
            usb["serial"] = REDACTED
    return out
