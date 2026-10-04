"""What the OpenFAN firmware update window says (DEC-481 to DEC-484).

Qt-free: every decision about wording, tone and what is shown lives here and is
unit-tested headlessly; ``ui/widgets/openfan_firmware_dialog.py`` renders it.

The daemon runs the update, and the file is written either by the daemon itself
— a published release it knows, read back byte for byte (DEC-483) — or by the
user copying it onto the board's drive. So this text has two jobs the rest of
the GUI rarely has: tell the user exactly what to do at one stage, and never
claim more than the daemon can know. Unless the daemon wrote the file and read
it back, it cannot know which build is running — the firmware has no build
identifier — so a result says what the board's own reports show, and no more.

An update is for one of two boards (DEC-484): the adopted controller, which
answers and is parked at 100 % first, or a board on USB that does not answer —
whose channels Control-OFC cannot set at all, so nothing is parked and the text
says so.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from control_ofc.api.models import (
    OpenFanDaemonWrite,
    OpenFanDevice,
    OpenFanFirmwareStaged,
    OpenFanFirmwareWrite,
    OpenFanMaintenanceRecord,
    OpenFanMaintenanceSummary,
    OpenFanSilentBoardEntry,
    OpenFanUsbDevice,
)
from control_ofc.services.cooling_watch import CoolingAlert
from control_ofc.services.uf2 import RELEASES_URL, Uf2Inspection

# ── Stages ────────────────────────────────────────────────────────────

STAGE_WRITING = "writing_firmware"
STAGE_FILE = "waiting_for_file"
STAGE_PARKING = "parking"
#: DEC-484: a silent board the 1200-baud signal did not move.
STAGE_BOOT_BUTTON = "waiting_for_boot_button"

#: The run's stages in order, with what each does in a few words. A run shows
#: the daemon's write only when it was asked for, and the copy by hand only
#: when the user writes the file (:func:`run_stages`).
STAGES: tuple[tuple[str, str], ...] = (
    ("preparing", "Check the controller"),
    (STAGE_PARKING, "Set every OpenFAN channel to 100 %"),
    ("entering_bootloader", "Put the board in update mode"),
    (STAGE_BOOT_BUTTON, "Press BOOT and RESET on the board"),
    (STAGE_WRITING, "Write the firmware and read it back"),
    (STAGE_FILE, "Copy the firmware file"),
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


# ── Which board (DEC-484) ─────────────────────────────────────────────

BOARD_CONNECTED = "connected"
BOARD_SILENT = "silent"


def update_board(device: OpenFanDevice | None) -> str:
    """Which board an update started now is for: the silent one the daemon
    offers, or the connected controller. The daemon offers a silent board only
    while no controller answers, so the two never compete."""
    if device is not None and device.silent_board is not None:
        return BOARD_SILENT
    return BOARD_CONNECTED


def target_usb(device: OpenFanDevice | None) -> OpenFanUsbDevice | None:
    """The USB identity of the board an update would be for."""
    if device is None:
        return None
    if device.silent_board is not None:
        return device.silent_board.usb
    return device.usb


# ── Fixed text ────────────────────────────────────────────────────────

INTRO = (
    "This updates the OpenFAN controller's firmware from a file you choose. "
    "Control-OFC checks the file, puts the board in update mode and watches it. "
    "A published release it knows, Control-OFC can write itself and read back, once "
    "the daemon may open USB devices; any other file you copy onto the board's "
    "RPI-RP2 drive."
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

#: DEC-484: the same windows for a board that does not answer. Nothing is
#: parked: Control-OFC cannot set its channels until it answers.
COOLING_WINDOWS_SILENT: tuple[str, ...] = (
    "The board does not answer, so Control-OFC cannot set its OpenFAN channels: they "
    "stay wherever its firmware has them, before and during the update, and a thermal "
    "emergency cannot reach them until the board answers again. Nothing is set to "
    "100 % first.",
    "While the board is in update mode, its fan chips should keep those settings — the "
    "board's design says so, though it has not yet been measured. Fans on the "
    "motherboard stay under normal control, and a thermal emergency still forces them.",
    COOLING_WINDOWS[2],
    COOLING_WINDOWS[3],
)


def cooling_windows(board: str) -> tuple[str, ...]:
    return COOLING_WINDOWS_SILENT if board == BOARD_SILENT else COOLING_WINDOWS


LEAVE_UPDATE_MODE = (
    "Once the board is in update mode it leaves it in one of three ways: a firmware "
    "file is written to it — by Control-OFC, or by you copying it onto its RPI-RP2 "
    "drive — its RESET button is pressed, or the PC is switched off and on. "
    "Control-OFC restarts it only after writing a firmware and reading it back."
)

CLOSE_NOTE = (
    "Closing this window does not stop the update. Reopen it from the Hardware page to follow it."
)

CONFIRM_TEXT = (
    "I understand that the OpenFAN channels run at 100 % during the update and on "
    "the firmware's own default for a few seconds when it restarts, and that I copy "
    "the file onto the board's drive myself."
)

CONFIRM_TEXT_DAEMON = (
    "I understand that the OpenFAN channels run at 100 % during the update and on "
    "the firmware's own default for a few seconds when it restarts, and that if "
    "Control-OFC cannot finish writing the file, I copy it onto the board's drive myself."
)

CONFIRM_TEXT_SILENT = (
    "I understand that Control-OFC cannot set the OpenFAN channels while the board does "
    "not answer, that they run on the firmware's own default for a few seconds when it "
    "restarts, and that I copy the file onto the board's drive myself."
)

CONFIRM_TEXT_SILENT_DAEMON = (
    "I understand that Control-OFC cannot set the OpenFAN channels while the board does "
    "not answer, that they run on the firmware's own default for a few seconds when it "
    "restarts, and that if Control-OFC cannot finish writing the file, I copy it onto the "
    "board's drive myself."
)

PREPARED_NOTE = (
    "This is the checked copy. Nothing can prove which file reaches the drive — "
    "after the update, the board's own reports are the evidence, shown with the result."
)

DEMO_REFUSAL = "Demo mode has no OpenFAN controller to update."

#: A cancel the daemon took while the run could still stop, that the run then
#: went on past: a board on its way into update mode is not left there (DEC-484).
CANCEL_TOO_LATE = (
    "The cancel came too late: the board was already on its way into update mode, so "
    "the update goes on."
)


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
    unknown = "Unknown"
    silent = device.silent_board
    if silent is not None:
        # DEC-484: the board the update is for, which a probe found not answering.
        board_usb = silent.usb
        return [
            (
                "Controller",
                "On USB, but not answering Control-OFC — it may run firmware Control-OFC "
                "cannot talk to. Its fans are not under fan control.",
            ),
            ("USB serial number", (board_usb.serial if board_usb else None) or unknown),
            ("USB port", (board_usb.port if board_usb else None) or unknown),
            ("Firmware report", "No answer"),
        ]
    if not device.present:
        return [("Controller", "No OpenFAN controller is connected.")]
    usb = device.usb
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
    usb = target_usb(device)
    desc_board = usb.config_descriptor_hex if usb else None
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


def pump_note(lines: Iterable[ChannelLine], board: str = BOARD_CONNECTED) -> str:
    pumps = [line.name for line in lines if line.pump]
    if not pumps:
        return ""
    names = ", ".join(pumps)
    if board == BOARD_SILENT:
        return (
            f"Pump: {names}. It stays wherever the board's firmware has it, and runs on "
            "the firmware's default for the few seconds the new firmware takes to start."
        )
    return (
        f"Pump: {names}. It is set to 100 % like every channel, and runs on the "
        "firmware's default for the few seconds the new firmware takes to start."
    )


# ── Who writes the file (DEC-483) ─────────────────────────────────────

WRITE_DAEMON = "daemon"
WRITE_MANUAL = "manual"
WRITE_REFUSED = "refused"

#: ``verdict`` on ``PUT /fans/openfan/firmware``.
VERDICT_DAEMON_WRITE = "daemon_write"
VERDICT_REFUSED = "refused"
REASON_UNKNOWN_BUILD = "unknown_build"
NO_USB_ACCESS = "no_usb_access"


@dataclass(frozen=True)
class WritePlan:
    """Who writes the chosen file, and what the file step says about it."""

    method: str
    tone: str = TONE_INFO
    text: str = ""
    #: The daemon has not answered about the file yet; Start waits for it.
    pending: bool = False


def _sentence(text: str | None) -> str:
    """The daemon's clause as a sentence: first letter raised, one full stop."""
    text = (text or "").strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def write_plan(
    *,
    supported: bool,
    device: OpenFanDevice | None,
    inspection: Uf2Inspection | None,
    staged: OpenFanFirmwareStaged | None,
    stage_error: str,
) -> WritePlan:
    """Who writes the file: the daemon, only for a file it said it would write
    and only while it may open USB devices; otherwise the user.

    *staged* is the daemon's answer about this very file (the window drops an
    answer about another); *stage_error* why it could not be asked. Like the
    device answer, this is informational: the start decides again.
    """
    if inspection is None or not inspection.ok:
        return WritePlan(WRITE_MANUAL)
    if not supported:
        return WritePlan(
            WRITE_MANUAL,
            TONE_INFO,
            "This daemon does not write the firmware itself: you copy the file onto "
            "the board's drive.",
        )
    if stage_error:
        return WritePlan(
            WRITE_MANUAL,
            TONE_WARN,
            f"The daemon could not be asked whether it can write this file itself "
            f"({stage_error.rstrip('.')}), so you copy it onto the board's drive. "
            "Read again asks once more.",
        )
    if staged is None:
        return WritePlan(
            WRITE_MANUAL,
            TONE_INFO,
            "Asking the daemon whether it can write this file itself…",
            pending=True,
        )
    if staged.verdict == VERDICT_REFUSED:
        return WritePlan(
            WRITE_REFUSED,
            TONE_CRIT,
            _sentence(staged.message)
            or f"No update may use this file ({staged.reason or 'no reason given'}).",
        )
    if staged.verdict != VERDICT_DAEMON_WRITE:
        # `manual_copy`, or a verdict this client does not know: the copy by hand.
        if staged.reason == REASON_UNKNOWN_BUILD:
            text = (
                "Control-OFC writes only the published OpenFAN releases it knows, so you "
                "copy this file onto the board's drive."
            )
        else:
            said = _sentence(staged.message) or (
                f"The daemon will not write this file itself "
                f"({staged.reason or staged.verdict or 'no reason given'})."
            )
            text = f"{said} You copy it onto the board's drive."
        return WritePlan(WRITE_MANUAL, TONE_INFO, text)
    release = f"the {staged.release}" if staged.release else "this published release"
    access = device.daemon_write if device is not None else None
    if access is None:
        return WritePlan(WRITE_MANUAL)
    if not access.available:
        return WritePlan(WRITE_MANUAL, TONE_INFO, _no_access_text(release, access))
    return WritePlan(
        WRITE_DAEMON,
        TONE_OK,
        f"Control-OFC writes {release} itself. Before it writes anything it checks that "
        "the board in update mode is this controller, and it reads every byte back "
        "before the board restarts. If it cannot finish, the update waits for you to "
        "copy the file instead.",
    )


def _no_access_text(release: str, access: OpenFanDaemonWrite) -> str:
    if access.reason == NO_USB_ACCESS:
        why = (
            "the daemon may not open USB devices — the opt-in openfan-firmware-write "
            "drop-in is not installed (the manual says how)"
        )
    else:
        why = (access.message or "").strip().rstrip(".") or (
            f"the daemon cannot now ({access.reason or 'no reason given'})"
        )
    return (
        f"Control-OFC could write {release} itself, but {why}. Until then, you copy the "
        "file onto the board's drive."
    )


def confirm_text(method: str, board: str = BOARD_CONNECTED) -> str:
    """The confirmation for who writes the file, and for which board."""
    if board == BOARD_SILENT:
        return CONFIRM_TEXT_SILENT_DAEMON if method == WRITE_DAEMON else CONFIRM_TEXT_SILENT
    return CONFIRM_TEXT_DAEMON if method == WRITE_DAEMON else CONFIRM_TEXT


# ── Starting ──────────────────────────────────────────────────────────


def start_block_reason(
    *,
    demo: bool,
    device: OpenFanDevice | None,
    live_link: str | None,
    external_block: str,
    inspection: Uf2Inspection | None,
    plan: WritePlan,
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
    silent = device.silent_board
    if silent is not None:
        # DEC-484: no link to wait for — the board's silence is the point.
        refusal = _refusal_text(silent.update_available, silent.update_refusals)
        if refusal:
            return refusal
        if not (silent.usb and silent.usb.serial):
            return "The daemon could not read the board's USB serial number."
    else:
        if not device.present:
            return "No OpenFAN controller is connected."
        if live_link != "connected":
            return f"The OpenFAN controller is not connected ({link_text(live_link).lower()})."
        refusal = _refusal_text(device.update_available, device.update_refusals)
        if refusal:
            return refusal
        if not (device.usb and device.usb.serial):
            return "The daemon could not read the controller's USB serial number."
    if inspection is None:
        return "Choose a firmware file."
    if not inspection.ok:
        return "The chosen file cannot be used."
    if plan.method == WRITE_REFUSED:
        return plan.text
    if plan.pending:
        return "Asking the daemon about the file…"
    # The copy is prepared for a daemon write too: it is the fallback.
    if not prepared:
        return "The checked copy of the file could not be prepared."
    if not confirmed:
        return "Tick the confirmation first."
    return ""


def _refusal_text(available: bool, refusals: list) -> str:
    """The daemon's first reason an update could not start, or ``""``."""
    if available:
        return ""
    first = refusals[0] if refusals else None
    if first is None:
        return "The daemon cannot start an update now."
    # The daemon's own words; a refusal without them shows its token rather
    # than nothing.
    return first.message or f"The daemon cannot start an update now ({first.reason})."


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


#: The channels of a board left in update mode, by whether it was parked first.
_PARKED_HOLD = (
    "Its OpenFAN channels should still hold the 100 % they were parked at, unless the "
    "board has restarted."
)
_SILENT_HOLD = (
    "Its OpenFAN channels should still hold whatever its old firmware last set, unless "
    "the board has restarted."
)


def _hold(record: OpenFanMaintenanceRecord) -> str:
    """What a board in update mode does with its channels (DEC-484: a silent
    board was never parked)."""
    return _SILENT_HOLD if record.board == BOARD_SILENT else _PARKED_HOLD


_RECOVERY_STEPS = (
    "Copy a firmware file onto the board's RPI-RP2 drive — the prepared file, or the "
    "firmware you had before.",
    "Or press the board's RESET button.",
    "Or switch the PC off and on.",
    "If no RPI-RP2 drive appears, follow the BOOT-button procedure in the OpenFAN "
    "firmware documentation, then copy a file.",
    "Control-OFC keeps watching for the board and takes it back as soon as it answers.",
)

#: DEC-483: the daemon's write stopped part-way, so the old firmware is gone
#: and the new one incomplete. Only a firmware file brings the board back.
_PARTIAL_WRITE_SUMMARY = (
    "The board is in update mode, or may be. Control-OFC had begun writing the firmware, "
    "so part of the board's flash is rewritten: it has no complete firmware to run until "
    "one is copied onto it."
)
_PARTIAL_WRITE_STEPS = (
    "Copy a firmware file onto the board's RPI-RP2 drive — the prepared file, or the "
    "firmware you had before.",
    "Pressing RESET or switching the PC off and on does not bring the board back until "
    "then: it needs a firmware first.",
    "If no RPI-RP2 drive appears, follow the BOOT-button procedure in the OpenFAN "
    "firmware documentation, then copy a file.",
    "Control-OFC keeps watching for the board and takes it back as soon as it answers.",
)

#: DEC-483: written and read back, but the board was not restarted from it.
_WRITTEN_NOT_RESTARTED_SUMMARY = (
    "The board is in update mode, or may be. Control-OFC wrote the new firmware and read "
    "every byte back, but the board did not restart from it."
)
_WRITTEN_NOT_RESTARTED_STEPS = (
    "Press the board's RESET button, or switch the PC off and on: the board then starts "
    "the new firmware.",
    "Or copy a firmware file onto the board's RPI-RP2 drive — the prepared file, or the "
    "firmware you had before.",
    "If the board neither comes back nor shows an RPI-RP2 drive, follow the BOOT-button "
    "procedure in the OpenFAN firmware documentation, then copy a file.",
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
        "The board is in update mode, or may be, and no firmware was copied.",
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
        "Control-OFC wrote the selected file and read every byte back, and the board "
        "restarted from it straight away. It is back under fan control.",
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
    if token == NO_FIRMWARE_CHANGE and record.board == BOARD_SILENT:
        # DEC-484: the board may have been signalled, and was never under fan
        # control; how the run ended — answering after all, or not moved — is
        # the daemon's detail.
        if record.cancelled:
            view = OutcomeView(
                token,
                TONE_INFO,
                "Update cancelled",
                "Nothing on the board was changed.",
                ("Start again whenever you are ready.",),
            )
        else:
            view = OutcomeView(
                token,
                TONE_WARN,
                "No firmware change",
                "The board was not updated, and nothing on it was changed.",
                ("The reason is below. Deal with it, then start again.",),
            )
    elif token == NO_FIRMWARE_CHANGE:
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
    elif (
        token == NEEDS_RECOVERY
        and record.firmware_write is not None
        and record.firmware_write.flash_changed
    ):
        # The generic recovery assumes the old firmware is still whole.
        base = _OUTCOMES[token]
        if record.firmware_write.verified:
            summary, steps = _WRITTEN_NOT_RESTARTED_SUMMARY, _WRITTEN_NOT_RESTARTED_STEPS
        else:
            summary, steps = _PARTIAL_WRITE_SUMMARY, _PARTIAL_WRITE_STEPS
        view = OutcomeView(token, base.tone, base.title, f"{summary} {_hold(record)}", steps)
    elif token == NEEDS_RECOVERY:
        base = _OUTCOMES[token]
        view = OutcomeView(
            token, base.tone, base.title, f"{base.summary} {_hold(record)}", base.steps
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


WRITE_PHASE_WRITING = "writing"
WRITE_PHASE_VERIFYING = "verifying"
WRITE_PHASE_WRITTEN = "written"
WRITE_PHASE_FELL_BACK = "fell_back"
FALLBACK_FLASH_ID_MISMATCH = "flash_id_mismatch"
FALLBACK_NO_RESTART = "no_restart"

_WRITE_PHASE_TEXT = {
    "pending": "Opening the board's update interface.",
    "identifying": "Checking that the board in update mode is this controller. Nothing is "
    "written until it is.",
    WRITE_PHASE_WRITING: "Writing the firmware. Leave the board connected.",
    WRITE_PHASE_VERIFYING: "Reading the firmware back to check every byte.",
    "rebooting": "Written and read back. Restarting the board.",
    WRITE_PHASE_WRITTEN: "Written and read back. The board is restarting.",
    WRITE_PHASE_FELL_BACK: "Control-OFC stopped writing. The update waits for you to copy "
    "the file.",
}

_FALLBACK_TEXT = {
    NO_USB_ACCESS: "the daemon may not open USB devices",
    "usb_unavailable": "the board's update interface could not be opened",
    FALLBACK_FLASH_ID_MISMATCH: "the board in update mode is not the controller the update "
    "was started for",
    "transfer_failed": "a transfer to the board failed",
    "readback_mismatch": "the firmware read back was not the file's",
    FALLBACK_NO_RESTART: "the board did not restart after the write",
}


def fallback_text(write: OpenFanFirmwareWrite) -> str:
    """Why the daemon stopped writing and what that left on the board. An
    unknown reason is shown as sent."""
    reason = write.fallback_reason or ""
    why = _FALLBACK_TEXT.get(reason, reason or "no reason given")
    if write.verified:
        left = "The new firmware is written and read back."
    elif write.flash_changed:
        left = (
            "Part of the board's flash was already rewritten, so it needs a firmware "
            "before it can run again."
        )
    else:
        left = "Nothing was written to the board."
    return f"Control-OFC could not write the firmware itself: {why}. {left}"


def write_progress(write: OpenFanFirmwareWrite | None) -> tuple[int, int] | None:
    """``(done, total)`` bytes while the daemon writes or reads back, else ``None``."""
    if write is None or write.phase not in (WRITE_PHASE_WRITING, WRITE_PHASE_VERIFYING):
        return None
    if write.total_bytes <= 0:
        return None
    return (max(0, min(write.done_bytes, write.total_bytes)), write.total_bytes)


def write_rows(record: OpenFanMaintenanceRecord) -> list[tuple[str, str]]:
    """Who wrote the file, for the result. Nothing for a copy by hand."""
    write = record.firmware_write
    if write is None:
        return []
    name = f"the {write.release}" if write.release else "the selected file"
    if write.phase == WRITE_PHASE_WRITTEN:
        checked = "every byte read back" if write.verified else "not read back"
        rows = [("Written by", f"Control-OFC — {name}, {checked}")]
    elif write.phase == WRITE_PHASE_FELL_BACK:
        why = write.fallback_detail or _FALLBACK_TEXT.get(write.fallback_reason or "", "")
        rows = [
            (
                "Written by",
                f"Not by Control-OFC ({why.rstrip('.') or 'no reason given'}); the update "
                "went on to the copy by hand",
            )
        ]
    elif not write.flash_changed:
        rows = [("Written by", "Nobody — the update ended before Control-OFC wrote anything")]
    elif write.verified:
        rows = [
            (
                "Written by",
                f"Control-OFC — {name}, every byte read back; the update ended before the "
                "board came back",
            )
        ]
    else:
        rows = [
            (
                "Written by",
                f"Control-OFC began writing {name}; the update ended before it finished, "
                "with part of the flash rewritten",
            )
        ]
    if write.flash_id:
        same = write.flash_id.upper() == record.expected_usb_serial.upper()
        whose = "this controller's serial number" if same else "not this controller's serial number"
        rows.append(("Flash id", f"{write.flash_id} — {whose}"))
    return rows


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
    #: ``(done, total)`` bytes while the daemon writes or reads back.
    progress: tuple[int, int] | None
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


def run_stages(record: OpenFanMaintenanceRecord) -> tuple[tuple[str, str], ...]:
    """The stages this run has. The daemon's write only when the start asked for
    it; the copy by hand unless the daemon writes — it joins once the write
    falls back to it."""
    entered = {t.stage for t in record.stages} | {record.stage}
    write = record.firmware_write
    shown = []
    for token, label in STAGES:
        # DEC-484: a silent board is never parked, and asked for its buttons
        # only when the signal did not move it.
        if token == STAGE_PARKING and record.board == BOARD_SILENT:
            continue
        if token == STAGE_BOOT_BUTTON and token not in entered:
            continue
        if token == STAGE_WRITING and write is None:
            continue
        if (
            token == STAGE_FILE
            and write is not None
            and token not in entered
            and write.phase != WRITE_PHASE_FELL_BACK
        ):
            continue
        shown.append((token, label))
    return tuple(shown)


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
    for token, label in run_stages(record):
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
    silent = record.board == BOARD_SILENT
    if stage == "preparing":
        if silent:
            return (
                "Checking the board on its USB port. It does not answer, so nothing is "
                "read from it and nothing is set on it."
            )
        return "Checking the controller and reading what its firmware reports about itself."
    if stage == STAGE_PARKING:
        return "Setting every OpenFAN channel to 100 %."
    if stage == STAGE_BOOT_BUTTON:
        return (
            "The board did not enter update mode by itself. Hold its BOOT button, press and "
            f"release its RESET button, then release BOOT — the board on USB port {port}. "
            "Its RPI-RP2 drive then appears and the update goes on. Cancel stops the update "
            "without changing anything, unless the board is already restarting into update "
            "mode — then the update goes on."
        )
    if stage == "entering_bootloader":
        if silent:
            return (
                "Asking the board once more whether it answers, then sending the 1200-baud "
                "signal that puts it in update mode. Its RPI-RP2 drive should appear shortly; "
                "once the board is on its way into update mode, the update goes on even if "
                "Cancel is pressed."
            )
        text = "Asking the board to enter update mode. Its RPI-RP2 drive should appear shortly."
        if record.bootloader_trigger == "1200_baud":
            text += (
                " The board did not respond to the first request, so Control-OFC sent the "
                "1200-baud signal on the same port."
            )
        return text
    if stage == STAGE_WRITING:
        write = record.firmware_write
        phase = write.phase if write is not None else ""
        return _WRITE_PHASE_TEXT.get(phase, f"Writing the firmware ({phase or 'starting'}).")
    if stage == STAGE_FILE:
        drive = record.bootloader_drive
        where = (
            f"the RPI-RP2 drive {drive}, on USB port {port}"
            if drive
            else (f"the board's RPI-RP2 drive, on USB port {port}")
        )
        copy_it = f"Copy the prepared file onto {where}."
        lead = ""
        write = record.firmware_write
        if write is not None and write.phase == WRITE_PHASE_FELL_BACK:
            lead = fallback_text(write) + " "
            if write.fallback_reason == FALLBACK_FLASH_ID_MISMATCH:
                # The drive may be another board's: the daemon could not tell.
                copy_it = (
                    f"Copy the prepared file onto {where} only if you are sure that drive "
                    "is the OpenFAN board's."
                )
            elif write.fallback_reason == FALLBACK_NO_RESTART:
                copy_it = f"Press the board's RESET button, or copy the prepared file onto {where}."
        return f"{lead}{copy_it} The board restarts by itself once the copy has finished."
    if stage == "waiting_for_return":
        if record.firmware_write is not None and STAGE_FILE not in {t.stage for t in record.stages}:
            # Straight from the daemon's write: nobody copied anything.
            return (
                "Control-OFC wrote the firmware and read every byte back, and the board is "
                "restarting from it. Waiting for it to come back on USB."
            )
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
        wants_file=running and record.stage == STAGE_FILE,
        progress=write_progress(record.firmware_write) if running else None,
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


UPDATE_ALERT_SILENT = "openfan_update:silent_board"


def silent_board_alert(board: OpenFanSilentBoardEntry | None) -> CoolingAlert | None:
    """One warning naming an OpenFAN board on USB that does not answer (DEC-484).

    The daemon reports one only on evidence — a probe opened the board's serial
    device and had no answer — and only while no controller answers and no
    update runs. Keyed on the board, so another board is another alert.
    """
    if board is None:
        return None
    # The port, never the serial: the event log carries this sentence into the
    # support bundle, which keeps the board's serial out (DEC-481).
    where = f" on USB port {board.usb_port}" if board.usb_port else " on USB"
    return CoolingAlert(
        key=f"{UPDATE_ALERT_SILENT}:{board.usb_serial}",
        level="warning",
        title="OpenFAN board not answering",
        detail=(
            f"The OpenFAN board{where} does not answer Control-OFC, so its fans are not "
            "under fan control and a thermal emergency cannot reach them. Its firmware may "
            "be one Control-OFC cannot talk to: Hardware → Update OpenFAN Firmware… can "
            "update it."
        ),
    )


def suppresses_fan_staleness(
    summary: OpenFanMaintenanceSummary | None,
    fan_id: str,
    silent: OpenFanSilentBoardEntry | None = None,
) -> bool:
    """Whether one alert stands in for this fan's staleness: an update's
    (DEC-482), or a board's that does not answer (DEC-484), the cause of that
    staleness. Only the OpenFAN channels, and only while the daemon reports it."""
    return (summary is not None or silent is not None) and fan_id.startswith("openfan:")


# ── Support bundle ────────────────────────────────────────────────────

REDACTED = "(redacted)"
#: A serial shorter than this identifies nothing, and scrubbing it from the
#: daemon's words would mangle them.
_MIN_SCRUBBED = 4


def bundle_record(raw: Mapping) -> dict:
    """The last run as the support bundle carries it: the daemon's record with
    every USB serial number removed — the fields that hold one, and any place
    the daemon's own words repeat it (a flash id that did not match is named in
    a note, DEC-483). The record holds no file path."""
    out = copy.deepcopy(dict(raw))
    serials: set[str] = set()

    def take(holder: object, key: str, *, keep_null: bool = False) -> None:
        if not isinstance(holder, dict) or key not in holder:
            return
        value = holder[key]
        if isinstance(value, str) and len(value) >= _MIN_SCRUBBED:
            serials.add(value)
        if value is not None or not keep_null:
            holder[key] = REDACTED

    take(out, "expected_usb_serial")
    for side in ("before", "after"):
        snap = out.get(side)
        take(snap.get("usb") if isinstance(snap, dict) else None, "serial")
    # A flash id never read stays null: that is how far the write got.
    take(out.get("firmware_write"), "flash_id", keep_null=True)
    if not serials:
        return out
    pattern = re.compile(
        "|".join(re.escape(s) for s in sorted(serials, key=len, reverse=True)), re.IGNORECASE
    )

    def scrub(value: object) -> object:
        if isinstance(value, str):
            return pattern.sub(REDACTED, value)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        return value

    return {k: scrub(v) for k, v in out.items()}
