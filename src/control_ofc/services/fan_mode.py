"""The one vocabulary for an hwmon header's ``pwmN_enable`` (fan mode).

Qt-free. Two tables used to disagree here: the characterisation view called 0
"no control" and knew nothing above 2, while the header inspector called 0
"Full speed (no control)" and 2-5 "Automatic (firmware curve)". The kernel's
``Documentation/hwmon/sysfs-interface.rst`` settles it:

* ``0`` — no fan speed control, i.e. the fan runs at full speed;
* ``1`` — manual control through ``pwmN`` (what the daemon writes when it takes
  a header over);
* ``2`` and above — automatic control; which automatic mode each number means is
  chip-specific (nct6775 uses 2-5, it87 only 2).

So every value from 2 up is "automatic", and a label never claims to know which
firmware mode a number is. Callers that want the raw value beside it (the verify
evidence) use :func:`pwm_enable_with_value`, because the number is the evidence
that distinguishes one automatic mode from another.
"""

from __future__ import annotations

PWM_ENABLE_NO_CONTROL = 0
#: What the daemon writes when it takes a header over.
PWM_ENABLE_MANUAL = 1

LABEL_FULL_SPEED = "Full speed (no control)"
LABEL_MANUAL = "Manual"
LABEL_AUTOMATIC = "Automatic (firmware)"


def pwm_enable_label(mode: int | None) -> str | None:
    """The label for one ``pwmN_enable`` value, or ``None`` when it is unknown.

    ``None`` in means the daemon did not report the mode, which is never the same
    statement as ``0`` — the caller renders its own "unknown" text for it. A
    negative value is outside the kernel's definition and is rendered as itself
    rather than dropped (the 273-i rule).
    """
    if mode is None:
        return None
    if mode == PWM_ENABLE_NO_CONTROL:
        return LABEL_FULL_SPEED
    if mode == PWM_ENABLE_MANUAL:
        return LABEL_MANUAL
    if mode >= 2:
        return LABEL_AUTOMATIC
    return f"mode {mode}"


def pwm_enable_with_value(mode: int | None) -> str | None:
    """:func:`pwm_enable_label` with the raw value beside it — "Automatic
    (firmware) — mode 5". ``None`` when the mode is unknown."""
    label = pwm_enable_label(mode)
    if label is None:
        return None
    if label == f"mode {mode}":
        return label
    return f"{label} — mode {mode}"
