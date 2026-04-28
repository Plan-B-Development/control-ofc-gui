"""Fan presence classification (A2).

Distinguishes "controllable, no fan detected" from "uncontrollable" and from
"controllable, fan present" so Diagnostics, Controls fan-role member picker,
and the Fan Wizard can render each state distinctly. The classification is a
pure function of the daemon-supplied ``FanReading`` and ``HwmonHeader`` —
this module does NOT re-derive ``is_writable`` or ``rpm_available`` from
labels, sysfs, or strings.

The four states cover the cases users actually see:

    PRESENT       Tachometer reports >0 RPM, or the PWM-only header is
                  actively driven. Default; no badge needed.
    EMPTY_HEADER  Header is writable, ``fan_input`` exists in sysfs, but
                  RPM is 0 — i.e. nothing plugged in, or a 3-pin fan with
                  no tachometer wire on a 4-pin header. Common on the
                  X870E AORUS MASTER which exposes 8 PWM headers but
                  most users only populate 1-3.
    READ_ONLY     Header reports ``is_writable=False``. Daemon cannot
                  write PWM here — typically BIOS/EC lock or kernel
                  driver gap.
    PWM_ONLY      Header is writable but has no ``fan_input`` attribute
                  (``rpm_available=False``). PWM writes succeed; RPM is
                  inherently unreadable.

When neither input carries enough information to classify (e.g. an OpenFan
fan with no matching header, currently idle), returns ``UNKNOWN`` so the
caller can suppress decoration rather than guessing.
"""

from __future__ import annotations

from enum import Enum

from control_ofc.api.models import FanReading, HwmonHeader


class FanPresence(Enum):
    PRESENT = "present"
    EMPTY_HEADER = "empty_header"
    READ_ONLY = "read_only"
    PWM_ONLY = "pwm_only"
    UNKNOWN = "unknown"


# Short labels used in row decorations and member-picker entries. PRESENT is
# empty so the default rendering carries no badge.
PRESENCE_BADGE: dict[FanPresence, str] = {
    FanPresence.PRESENT: "",
    FanPresence.EMPTY_HEADER: "no fan detected",
    FanPresence.READ_ONLY: "read-only",
    FanPresence.PWM_ONLY: "PWM only — no RPM",
    FanPresence.UNKNOWN: "",
}


PRESENCE_TOOLTIP: dict[FanPresence, str] = {
    FanPresence.PRESENT: ("Fan is responding (RPM > 0 or PWM is being driven)."),
    FanPresence.EMPTY_HEADER: (
        "This header is controllable but the fan tachometer reads 0 RPM. "
        "Either no fan is plugged in, or the fan does not have a tachometer "
        "wire (a 3-pin DC fan on a 4-pin PWM header). Writes will succeed "
        "but no RPM feedback will be visible."
    ),
    FanPresence.READ_ONLY: (
        "This header is read-only — the daemon cannot write PWM to it. "
        "Likely BIOS/EC has it locked, or the kernel driver does not expose "
        "write access for this chip."
    ),
    FanPresence.PWM_ONLY: (
        "This header has a PWM output but no tachometer (fan_input). "
        "PWM writes will be sent but the fan's actual RPM cannot be read."
    ),
    FanPresence.UNKNOWN: ("Not enough data to classify this fan."),
}


def classify_fan_presence(fan: FanReading | None, header: HwmonHeader | None) -> FanPresence:
    """Classify a fan's presence/control state from daemon-supplied fields.

    Args:
        fan: ``FanReading`` from ``/fans`` or ``/poll``. May be ``None``
            when the caller has only a header (PWM-only headers with no
            matching fan reading).
        header: ``HwmonHeader`` from ``/hwmon/headers``. May be ``None``
            for OpenFan or GPU fans that don't go through hwmon.

    Returns:
        ``FanPresence`` value reflecting the most informative state the
        daemon's fields support.
    """
    # Read-only is a property of the header alone — short-circuit before
    # any RPM check so a header that happens to have a working fan still
    # surfaces as READ_ONLY (e.g. BIOS-locked but spinning at full speed).
    if header is not None and not header.is_writable:
        return FanPresence.READ_ONLY

    rpm = fan.rpm if fan is not None else None
    last_pwm = fan.last_commanded_pwm if fan is not None else None

    # Spinning fan trumps everything except read-only.
    if rpm is not None and rpm > 0:
        return FanPresence.PRESENT

    # Header with no tachometer (rpm_available=False from daemon probe).
    # Active write path → PRESENT for display purposes; otherwise PWM_ONLY.
    if header is not None and not header.rpm_available:
        if last_pwm is not None and last_pwm > 0:
            return FanPresence.PRESENT
        return FanPresence.PWM_ONLY

    # Writable header with rpm_available=True but rpm is 0 / missing →
    # the empty-slot case. This is the common case on dual-IO Gigabyte
    # boards where 5+3=8 PWM headers exist but most are unpopulated.
    if header is not None and header.is_writable:
        return FanPresence.EMPTY_HEADER

    # No header context (OpenFan/GPU). Fall back to PWM-driven evidence.
    if last_pwm is not None and last_pwm > 0:
        return FanPresence.PRESENT

    return FanPresence.UNKNOWN
