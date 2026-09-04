"""View-model for one PWM header, as the Hardware page inspects it.

AIO-MB Phase 6 (DEC-318). Qt-free, in the ``services/*_view.py`` pattern: this
module decides *what is true and how it should be worded*, and the card that
draws it decides nothing but spacing and colour.

Phase 6's own design rule is "do not create new intelligence — surface the
intelligence Control-OFC already has". So every value here comes from a daemon
field or from an existing shared helper, and the module's real work is the four
places where a naive rendering would lie:

* **Requested PWM and readback PWM are separate axes and must never collapse.**
  ``FanReading.pwm_commanded_pct`` (DEC-318) is the single-producer command and
  ``pwm_readback_pct`` (DEC-317) the single-producer readback. The older
  ``last_commanded_pwm`` is *neither* for an hwmon header — it carries whichever
  of the two wrote last (register row ``AIO5-a``) — so it is used only as a
  clearly-flagged approximation on a pre-2.33 daemon, and never silently.
* **Absent is not zero, and unknown is not unsupported.** The brief mandates a
  four-token vocabulary (Supported / Unsupported by driver / Unavailable /
  Unknown) precisely because a driver that publishes nothing and a driver that
  publishes "no" mean different things. ``supported_pwm_enable_modes == []``
  means *unknown* and is the common case: the daemon's chip table knows only
  ``it87`` and ``nct6775``.
* **Safety reads the union predicate, never the wire ``role``.** ``role`` is the
  *display* role and a user assignment substitutes for inference there,
  downgrades included (DEC-312). ``services/pump_protection`` owns the one rule.
* **A normal motherboard AIO must produce no warnings.** No coolant telemetry,
  no ``pwm_freq``, no ``fanN_pulses``, one tach behind a splitter — all normal
  (§18). Only genuine problems raise a state above ``ok``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..api.models import Capabilities, FanReading, HwmonHeader
from ..knowledge.hwmon_label_resolver import is_placeholder_hwmon_label
from .daemon_features import unsupported_feature_message
from .pump_protection import header_effective_floor_pct, header_is_pump_protected

# ── The capability vocabulary the brief mandates (§4) ────────────────────────
# Four tokens, and the distinction between the last two is the load-bearing
# part: "Unavailable" is a driver that says no, "Unknown" is a driver that says
# nothing. Collapsing them turns an unmapped chip into a hardware fault.
SUPPORTED = "Supported"
UNSUPPORTED = "Unsupported by driver"
UNAVAILABLE = "Unavailable"
UNKNOWN = "Unknown"

#: Shown wherever a value is genuinely unknown. Never substitute 0.
UNKNOWN_TEXT = "—"

# ── Status vocabulary (§18) ──────────────────────────────────────────────────
STATUS_NORMAL = "Normal"
STATUS_UNAVAILABLE = "Unavailable"
STATUS_UNKNOWN = "Unknown"
STATUS_NEEDS_ATTENTION = "Needs attention"
STATUS_POSSIBLE_OVERRIDE = "Possible device override"
STATUS_CONTROL_RECLAIMED = "Control reclaimed"

# ── Control ownership, using the daemon's own rule (`validation/recorder.rs`) ─
OWNER_DAEMON = "Control-OFC"
OWNER_EXTERNAL = "BIOS / firmware"
OWNER_UNKNOWN = UNKNOWN

#: `pwmN_enable == 1` is manual mode, which is what the daemon writes when it
#: takes a header over. Any other value means something else is driving it.
PWM_ENABLE_MANUAL = 1

_ENABLE_MODE_LABELS = {
    0: "Full speed (no control)",
    1: "Manual",
    2: "Automatic (firmware curve)",
    3: "Automatic (firmware curve)",
    4: "Automatic (firmware curve)",
    5: "Automatic (firmware curve)",
}

_ROLE_LABELS = {
    "pump": "Pump",
    "cpu_fan": "CPU fan",
    "radiator_fan": "Radiator fan",
    "chassis_fan": "Chassis fan",
    "unknown": "Unclassified",
}

_ROLE_SOURCE_LABELS = {
    "none": "Not classified",
    "label": "Hardware label",
    "chip_mapping": "Chip mapping",
    "user_assigned": "Assigned by you",
}


def humanise_token(token: str) -> str:
    """Render an unrecognised wire token readably rather than dropping it.

    The 273-i rule: a newer daemon adding a token must not make a user's header
    vanish from their own screen.
    """
    return token.replace("_", " ").strip().capitalize() or UNKNOWN_TEXT


def _fmt_pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


def _fmt_rpm(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value:,} RPM"


def _fmt_freq(hz: int | None) -> str:
    """Hz, rendered at the precision the value actually justifies.

    A PWM base frequency is typically 25 kHz; showing "25000 Hz" is correct but
    unreadable, and "25.0 kHz" over-precises a 100 Hz reading.
    """
    if hz is None:
        return UNKNOWN_TEXT
    if hz >= 1000:
        return f"{hz / 1000:.1f} kHz"
    return f"{hz} Hz"


@dataclass(frozen=True)
class InfoRow:
    """One label/value line. ``state`` drives the chip colour, never the text."""

    label: str
    value: str
    state: str = "neutral"
    #: Set where the value needs a caveat the value itself cannot carry.
    note: str = ""


@dataclass(frozen=True)
class HeaderInspectorView:
    """Everything the Hardware page shows for a single PWM header."""

    header_id: str
    #: The resolved display name (user alias > label ladder > raw id).
    title: str
    #: e.g. "Nuvoton NCT6799D · pwm5"
    subtitle: str
    #: "Pump" / "CPU fan" / ... — the DISPLAY role, safe to show, never to act on.
    role_label: str
    role_source_label: str
    #: True when the daemon will refuse to stop or under-drive this header.
    pump_protected: bool
    status: str = STATUS_NORMAL
    status_state: str = "ok"
    #: The compact card body.
    live_rows: list[InfoRow] = field(default_factory=list)
    #: "Details" disclosure — identity.
    identity_rows: list[InfoRow] = field(default_factory=list)
    #: "Details" disclosure — the header capability audit.
    capability_rows: list[InfoRow] = field(default_factory=list)
    #: Classification + safety (§5).
    safety_rows: list[InfoRow] = field(default_factory=list)
    #: Action enablement, with the reason a disabled action is disabled (§11).
    can_test: bool = False
    test_disabled_reason: str = ""
    can_characterize: bool = False
    characterize_disabled_reason: str = ""
    #: True when the requested-PWM figure is `last_commanded_pwm` rather than
    #: the single-producer command — i.e. the daemon predates DEC-318.
    requested_is_approximate: bool = False
    #: Cooling device this header belongs to, if any.
    cooling_device_id: str | None = None


def control_ownership(reading: FanReading | None) -> str:
    """Who is driving this header, by the daemon's own rule.

    ``validation/recorder.rs`` classifies ownership from the live ``pwmN_enable``
    mode: manual means the daemon took it over, anything else means something
    else is in charge, and an absent mode means the driver does not expose the
    attribute. Reproduced rather than invented, so the Hardware page and a
    validation session cannot disagree about the same header.
    """
    if reading is None or reading.pwm_enable_mode is None:
        return OWNER_UNKNOWN
    return OWNER_DAEMON if reading.pwm_enable_mode == PWM_ENABLE_MANUAL else OWNER_EXTERNAL


def requested_pct(reading: FanReading | None) -> tuple[int | None, bool]:
    """The duty the daemon asked for, and whether it is an approximation.

    Returns ``(value, approximate)``. ``approximate`` is True only on a pre-2.33
    daemon, where the sole candidate is ``last_commanded_pwm`` — which for an
    hwmon header may actually be the poll's readback (``AIO5-a``). The caller
    must surface that caveat rather than presenting the number as a command;
    silently showing it is precisely the collapse §6 forbids.
    """
    if reading is None:
        return None, False
    if reading.pwm_commanded_pct is not None:
        return reading.pwm_commanded_pct, False
    if reading.last_commanded_pwm is not None:
        return reading.last_commanded_pwm, True
    return None, False


def _tach_capability(header: HwmonHeader) -> str:
    return SUPPORTED if header.rpm_available else UNAVAILABLE


def _enable_modes_text(header: HwmonHeader) -> str:
    """The `pwmN_enable` values this driver accepts.

    EMPTY MEANS UNKNOWN, and this is the common case rather than the exception:
    the daemon's chip table covers ``it87`` and ``nct6775`` and nothing else, so
    most boards land here. Rendering it as "none supported" would report a
    working header as crippled on the majority of hardware.
    """
    modes = header.supported_pwm_enable_modes or []
    if not modes:
        return UNKNOWN
    return ", ".join(str(m) for m in modes)


# NOTE: there is deliberately **no single-sample device-override heuristic here**,
# and one must not be added.
#
# §10's signature is "PWM command and readback correct, but RPM did not follow the
# expected response" — and *expected* is the load-bearing word. A single poll
# sample cannot establish it: a high-static-pressure fan topping out at 4000-6000
# RPM genuinely does 1600-2400 RPM at 40% duty, so any "low duty + high RPM" rule
# fires on ordinary hardware. That is precisely the alarm fatigue §18 forbids, and
# it would break this module's own promise that a normal motherboard AIO raises
# zero warnings.
#
# The sound answer already exists: `CharSummary.possible_device_override`, which
# watches RPM across a whole sweep and is reported by the characterisation dialog
# in the cautious wording §10 requires. `STATUS_POSSIBLE_OVERRIDE` is kept in the
# vocabulary above for that path to use, not for this one to guess with.


def build_header_inspector_view(
    header: HwmonHeader,
    *,
    reading: FanReading | None = None,
    capabilities: Capabilities | None = None,
    display_name: str = "",
    enable_revert_count: int = 0,
) -> HeaderInspectorView:
    """Build the render-ready inspection of one PWM header.

    ``display_name`` is the caller's resolved name (``AppState`` owns the alias
    ladder, so this module stays Qt-free and never reaches into app state).
    ``enable_revert_count`` comes from ``GET /diagnostics/hardware``, which the
    poll worker already fetches — no extra request is made for it.
    """
    raw_label = header.label or ""
    placeholder = is_placeholder_hwmon_label(raw_label, header.pwm_index)
    title = display_name or (raw_label if not placeholder else "") or header.id
    chip = header.chip_name or UNKNOWN_TEXT
    subtitle = f"{chip} · pwm{header.pwm_index}"

    protected = header_is_pump_protected(header, capabilities)
    floor = header_effective_floor_pct(header, capabilities)
    ownership = control_ownership(reading)
    requested, approximate = requested_pct(reading)
    readback = reading.pwm_readback_pct if reading else None

    # ── Live rows (§3, §6) ───────────────────────────────────────────────────
    live: list[InfoRow] = [
        InfoRow("RPM", _fmt_rpm(reading.rpm if reading else None)),
    ]
    requested_note = ""
    if approximate:
        # Never present a possibly-readback value as a command (§6, AIO5-a).
        requested_note = (
            "Reported by an older daemon that does not separate the commanded "
            "duty from the hardware readback."
        )
    live.append(
        InfoRow(
            "Requested PWM",
            _fmt_pct(requested),
            note=requested_note,
        )
    )
    live.append(InfoRow("Readback PWM", _fmt_pct(readback)))

    mode = reading.pwm_enable_mode if reading else None
    live.append(
        InfoRow(
            "Control mode",
            _ENABLE_MODE_LABELS.get(mode, UNKNOWN) if mode is not None else UNKNOWN,
        )
    )
    live.append(InfoRow("Control ownership", ownership))
    if header.pwm_mode is not None:
        live.append(InfoRow("Signal mode", "PWM" if header.pwm_mode == 1 else "DC"))
    if header.pwm_freq_hz is not None:
        live.append(InfoRow("PWM frequency", _fmt_freq(header.pwm_freq_hz)))

    # ── Status (§18): only genuine problems escalate ─────────────────────────
    status, status_state = STATUS_NORMAL, "ok"
    if not header.is_writable and not header.rpm_available:
        status, status_state = STATUS_UNAVAILABLE, "neutral"
    elif reading is None:
        status, status_state = STATUS_UNKNOWN, "neutral"
    elif reading.fan_alarm or reading.stall_detected:
        status, status_state = STATUS_NEEDS_ATTENTION, "critical"
    elif enable_revert_count > 0 and ownership == OWNER_EXTERNAL:
        # The header was taken back and is currently NOT ours. A historical
        # reclaim we successfully recovered from is not a live problem, which is
        # why both halves are required.
        status, status_state = STATUS_CONTROL_RECLAIMED, "warn"
    live.append(InfoRow("Status", status, state=status_state))

    # ── Identity (§4) ────────────────────────────────────────────────────────
    identity = [
        InfoRow("Controller", chip),
        InfoRow("hwmon device", header.device_id or UNKNOWN_TEXT),
        InfoRow("PWM channel", f"pwm{header.pwm_index}"),
        InfoRow(
            "Kernel label",
            # A synthesised `pwmN` is not a label the chip published (DEC-229);
            # showing it here would claim the driver said something it did not.
            UNKNOWN_TEXT if placeholder else (raw_label or UNKNOWN_TEXT),
        ),
        InfoRow("Display name", title),
        InfoRow("Direct AIO device", "Yes" if header.is_aio else "No"),
        InfoRow(
            "Cooling device",
            header.cooling_device_id or "Not assigned",
        ),
        InfoRow("Header id", header.id),
    ]

    # ── Capability audit (§4) ────────────────────────────────────────────────
    capability = [
        InfoRow("PWM write", SUPPORTED if header.is_writable else UNSUPPORTED),
        InfoRow("PWM readback", SUPPORTED if readback is not None else UNKNOWN),
        InfoRow("pwm_enable", SUPPORTED if header.supports_enable else UNAVAILABLE),
        InfoRow("Supported control modes", _enable_modes_text(header)),
        InfoRow(
            "PWM/DC mode",
            UNKNOWN if header.pwm_mode is None else ("PWM" if header.pwm_mode == 1 else "DC"),
        ),
        InfoRow("PWM base frequency", _fmt_freq(header.pwm_freq_hz)),
        InfoRow("RPM telemetry", _tach_capability(header)),
        InfoRow(
            "Tach pulses/rev",
            UNKNOWN if header.tach_pulses_per_rev is None else str(header.tach_pulses_per_rev),
        ),
        InfoRow(
            "RPM thresholds",
            UNKNOWN
            if header.rpm_min_threshold is None and header.rpm_max_threshold is None
            else f"{header.rpm_min_threshold or UNKNOWN_TEXT}"
            f" to {header.rpm_max_threshold or UNKNOWN_TEXT} RPM",
        ),
        InfoRow(
            "Alarm state",
            UNKNOWN
            if reading is None or reading.fan_alarm is None
            else ("Alarm" if reading.fan_alarm else "Clear"),
            state="critical" if (reading is not None and reading.fan_alarm) else "neutral",
        ),
    ]
    if enable_revert_count:
        capability.append(
            InfoRow(
                "BIOS/EC reclaim",
                f"{enable_revert_count} time(s)",
                state="warn",
                note="The firmware has taken this header back; the daemon re-took it.",
            )
        )
    else:
        capability.append(InfoRow("BIOS/EC reclaim", "Not observed"))

    # ── Classification and safety (§5) ───────────────────────────────────────
    safety = [
        InfoRow("Role", _ROLE_LABELS.get(header.role, humanise_token(header.role))),
        InfoRow(
            "Role source",
            _ROLE_SOURCE_LABELS.get(header.role_source, humanise_token(header.role_source)),
        ),
        InfoRow(
            # NOT "the floor for this control": `effective_min_pwm_pct` is
            # reconstructed from the device policy table and excludes the active
            # profile's own `minimum_pct`/`stop_pct`.
            "Device safety floor",
            _fmt_pct(floor),
        ),
        InfoRow(
            "Fan stop",
            "Prohibited" if protected else "Permitted",
            state="warn" if protected else "neutral",
        ),
    ]

    # ── Action enablement (§11) ──────────────────────────────────────────────
    can_test = header.is_writable
    test_reason = "" if can_test else "This header is read-only, so it cannot be driven."

    can_char = bool(header.is_writable and _supports_characterization(capabilities))
    if not header.is_writable:
        char_reason = "This header is read-only, so there is nothing to sweep."
    elif not _supports_characterization(capabilities):
        char_reason = unsupported_feature_message("pwm_characterization")
    elif not header.rpm_available:
        # Degraded, not blocked: the sweep still proves command acceptance and
        # readback, which is two of its three verdicts (§11, §20).
        char_reason = ""
        can_char = True
    else:
        char_reason = ""

    return HeaderInspectorView(
        header_id=header.id,
        title=title,
        subtitle=subtitle,
        role_label=_ROLE_LABELS.get(header.role, humanise_token(header.role)),
        role_source_label=_ROLE_SOURCE_LABELS.get(
            header.role_source, humanise_token(header.role_source)
        ),
        pump_protected=protected,
        status=status,
        status_state=status_state,
        live_rows=live,
        identity_rows=identity,
        capability_rows=capability,
        safety_rows=safety,
        can_test=can_test,
        test_disabled_reason=test_reason,
        can_characterize=can_char,
        characterize_disabled_reason=char_reason,
        requested_is_approximate=approximate,
        cooling_device_id=header.cooling_device_id,
    )


def _supports_characterization(capabilities: Capabilities | None) -> bool:
    if capabilities is None:
        return False
    return bool(getattr(capabilities.control, "pwm_characterization", False))


def build_header_inspector_views(
    headers: list[HwmonHeader],
    *,
    readings: list[FanReading] | None = None,
    capabilities: Capabilities | None = None,
    display_names: dict[str, str] | None = None,
    enable_revert_counts: dict[str, int] | None = None,
) -> list[HeaderInspectorView]:
    """Build views for every header, pumps first then daemon order.

    Pumps lead because §2 and §5 both treat the pump as the header a user is
    looking for; the rest keep the daemon's ordering so the list is stable.
    """
    by_id = {r.id: r for r in (readings or [])}
    names = display_names or {}
    reverts = enable_revert_counts or {}
    views = [
        build_header_inspector_view(
            h,
            reading=by_id.get(h.id),
            capabilities=capabilities,
            display_name=names.get(h.id, ""),
            enable_revert_count=reverts.get(h.id, 0),
        )
        for h in headers
    ]
    return sorted(views, key=lambda v: not v.pump_protected)
