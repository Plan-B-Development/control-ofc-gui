"""The PWM-control verification tri-state, from the daemon's own records (DEC-456).

Qt-free. Before DEC-456 the only record of a PWM verify was the GUI's
``last_pwm_verify_effective`` setting — one machine-wide value, remembered by one
GUI install, blind to a verify run from anywhere else and to every
characterisation sweep. A daemon advertising ``control.pwm_verification_records``
keeps the latest conclusive verdict per header instead, so the tri-state is
derived from those records and the setting is consulted only on an older daemon.
"""

from __future__ import annotations

from collections.abc import Iterable

from control_ofc.api.models import HwmonHeader

#: The two ``PwmVerification.state`` tokens that settle anything. Any other
#: value is an opaque token (273-i) and counts as "no verdict".
STATE_VERIFIED = "verified"
STATE_FAILED = "failed"


def pwm_verification_tristate(headers: Iterable[HwmonHeader]) -> bool | None:
    """``False`` if any writable header's latest verdict failed, ``True`` if at
    least one is verified and none failed, ``None`` if no writable header has a
    verdict yet.

    The same tri-state ``last_pwm_verify_effective`` fed (``True`` writes land,
    ``False`` they did not, ``None`` never tested), so every consumer of
    ``pwm_control_verified`` reads it unchanged. A failure outranks a pass
    because the quirk evidence it feeds asks whether writes are *silently
    ignored* on this machine, and one header that ignored them answers that.
    Read-only headers are skipped: nothing can verify them, so they carry no
    verdict and would only ever read as "not yet".
    """
    verified = False
    for header in headers:
        if not header.is_writable or header.pwm_verification is None:
            continue
        if header.pwm_verification.state == STATE_FAILED:
            return False
        if header.pwm_verification.state == STATE_VERIFIED:
            verified = True
    return True if verified else None


def verification_signature(headers: Iterable[HwmonHeader]) -> frozenset[tuple[str, str]]:
    """Every header's ``(id, state)`` that carries a record — what changes when a
    verdict is earned, replaced or dropped.

    The Hardware page compares successive signatures to decide when its
    readiness checklist, whose PWM items count against these records, is out of
    date. Empty on a daemon without the capability, so it never changes there.
    """
    return frozenset(
        (h.id, h.pwm_verification.state) for h in headers if h.pwm_verification is not None
    )
