"""The evidence behind a fan-control test result (`WIRE-f`, W-DIAGG Run C).

Qt-free, shared by every surface that shows a verify result: System State and
the Hardware page for ``POST /hwmon/{id}/verify``, System State for
``POST /gpu/{id}/fan/verify``. Both endpoints return the header's state before
and after the test, and until Run C the GUI rendered only the RPM from it — the
``pwm_enable`` pair that is the direct evidence of a BIOS/EC reclaim, the duty
the header read back, the GPU's applied speed and its zero-RPM setting were all
parsed and dropped. And the two device kinds reported differently: the GPU path
said what speed it set and how long it waited, the hwmon path said neither.

A result therefore carries two things here: a one-line ``summary`` shown with
the verdict every time, and ``rows`` — before/after pairs the page puts behind
"Show test evidence". Values the daemon did not report render as
:data:`UNKNOWN_TEXT`, never as a zero or an empty string.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..api.models import GpuVerifyResult, HwmonVerifyResult
from .fan_mode import PWM_ENABLE_MANUAL, pwm_enable_label, pwm_enable_with_value

UNKNOWN_TEXT = "—"
#: The after-column of a test that stopped before it measured anything.
NOT_MEASURED_TEXT = "not measured"

ROW_FAN_MODE = "Fan mode (pwm_enable)"
ROW_DUTY = "Duty read back"
ROW_RPM = "RPM"
ROW_APPLIED_SPEED = "Applied fan speed"
ROW_ZERO_RPM = "Zero-RPM idle"

#: `GpuVerifyResult.fan_control_method` → how the summary names the write path.
#: An unrecognised token is shown as itself (273-i).
_GPU_METHOD_TEXT = {
    "pmfw_curve": "the PMFW fan curve",
    "hwmon_pwm": "pwm1",
}


@dataclass(frozen=True)
class EvidenceRow:
    """One before/after pair."""

    label: str
    before: str
    after: str


@dataclass(frozen=True)
class VerifyEvidence:
    """What a test did and what it read, for one result."""

    #: One line, shown with the verdict every time. Empty when the daemon
    #: reported nothing to summarise.
    summary: str = ""
    #: The before/after table, shown behind "Show test evidence". Empty when no
    #: value on either side was reported.
    rows: tuple[EvidenceRow, ...] = ()


def _pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


def _num(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}"


def _duty(pct: int | None, raw: int | None) -> str:
    if pct is None and raw is None:
        return UNKNOWN_TEXT
    if raw is None:
        return f"{pct}%"
    if pct is None:
        return f"raw {raw}"
    return f"{pct}% (raw {raw})"


def _mode(mode: int | None) -> str:
    text = pwm_enable_with_value(mode)
    return UNKNOWN_TEXT if text is None else text


def _zero_rpm(enabled: bool | None) -> str:
    if enabled is None:
        return UNKNOWN_TEXT
    return "enabled" if enabled else "disabled"


def _mode_clause(result_token: str, final: int | None) -> str:
    """What the fan mode did, judged against MANUAL — the mode the test wrote.

    Not against the mode the header started in: a header nothing controlled
    starts automatic, the test sets it manual, and a BIOS that takes it back
    ends it automatic again — "stayed automatic" would sit under the daemon's
    reclaim verdict (the daemon judges ``final != 1`` whatever the start). The
    one non-manual end the daemon passes as ``effective`` is a driver reporting
    a 100% duty as mode 0 (DEC-326), so that verdict is what the wording reads,
    rather than a second copy of the daemon's alias rule. "" when the end mode
    was not reported: a change nobody measured is not claimed.
    """
    label = pwm_enable_label(final)
    if label is None:
        return ""
    if final == PWM_ENABLE_MANUAL:
        return "fan mode stayed manual"
    if result_token == "effective":
        return (
            f"fan mode reads {label.lower()}, this driver's way of reporting 100% (not a reclaim)"
        )
    return f"fan mode ended {label.lower()}, not manual"


def _test_clause(pct: int, wait_s: int, via: str = "") -> str:
    """ "set 60% through the PMFW fan curve and waited 8 s".

    "Set", never "held" or "drove": the test commanded the duty, and whether it
    held is exactly what the verdict and the table go on to answer — a reclaim
    or a rejected write would make either word a false claim.
    """
    if not pct:
        return ""
    clause = f"set {pct}%"
    if via:
        clause = f"{clause} through {via}"
    return f"{clause} and waited {wait_s} s" if wait_s else clause


def _summary(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    return f"Test: {'; '.join(parts)}" if parts else ""


def _rows_with_data(rows: list[EvidenceRow]) -> tuple[EvidenceRow, ...]:
    """The table, or nothing when every cell is unknown (an older daemon)."""
    if all(r.before == UNKNOWN_TEXT and r.after in (UNKNOWN_TEXT, NOT_MEASURED_TEXT) for r in rows):
        return ()
    return tuple(rows)


def hwmon_verify_evidence(result: HwmonVerifyResult) -> VerifyEvidence:
    """Evidence for one ``POST /hwmon/{id}/verify`` result.

    A verify stopped for a mid-run pump (DEC-418) never finished its settle, so
    its after-readings are not a measurement: the after column reads
    :data:`NOT_MEASURED_TEXT` and the summary claims no change — and names no
    duty, because the daemon's pump check can stop it before its write, with
    the duty planned for an ordinary fan never set.
    """
    init, final = result.initial_state, result.final_state
    stopped_early = result.result == "pump_protected_mid_run"

    def after(value: str) -> str:
        return NOT_MEASURED_TEXT if stopped_early else value

    rows = [
        EvidenceRow(ROW_FAN_MODE, _mode(init.pwm_enable), after(_mode(final.pwm_enable))),
        EvidenceRow(
            ROW_DUTY,
            _duty(init.pwm_percent, init.pwm_raw),
            after(_duty(final.pwm_percent, final.pwm_raw)),
        ),
        EvidenceRow(ROW_RPM, _num(init.rpm), after(_num(final.rpm))),
    ]
    if stopped_early:
        parts = ["stopped before it measured anything"]
    else:
        rpm = (
            f"RPM {init.rpm} → {final.rpm}"
            if init.rpm is not None and final.rpm is not None
            else ""
        )
        parts = [
            _test_clause(result.test_pwm_percent, result.wait_seconds),
            rpm,
            _mode_clause(result.result, final.pwm_enable),
        ]
    return VerifyEvidence(summary=_summary(parts), rows=_rows_with_data(rows))


def gpu_verify_evidence(result: GpuVerifyResult) -> VerifyEvidence:
    """Evidence for one ``POST /gpu/{id}/fan/verify`` result.

    The fan-mode row appears only where either state carries ``pwm_enable`` (the
    legacy ``pwm1`` path), and the zero-RPM row only where either carries
    ``zero_rpm_enabled`` (the PMFW path): a row of two unknowns would suggest the
    card should have reported something it has no way to report.
    """
    init, final = result.initial_state, result.final_state
    rows = [
        EvidenceRow(ROW_APPLIED_SPEED, _pct(init.applied_speed_pct), _pct(final.applied_speed_pct)),
        EvidenceRow(ROW_RPM, _num(init.rpm), _num(final.rpm)),
    ]
    if init.pwm_enable is not None or final.pwm_enable is not None:
        rows.append(EvidenceRow(ROW_FAN_MODE, _mode(init.pwm_enable), _mode(final.pwm_enable)))
    if init.zero_rpm_enabled is not None or final.zero_rpm_enabled is not None:
        rows.append(
            EvidenceRow(
                ROW_ZERO_RPM, _zero_rpm(init.zero_rpm_enabled), _zero_rpm(final.zero_rpm_enabled)
            )
        )
    method = result.fan_control_method
    via = _GPU_METHOD_TEXT.get(method, method)
    if result.result == "write_failed":
        # The daemon reads the "after" RPM straight after a rejected write, with
        # no settle (`wait_seconds` is 0): it is a reading, not an effect, so
        # the summary names neither a set duty nor an RPM change.
        attempt = _test_clause(result.test_speed_pct, 0, via)
        parts = [f"tried to {attempt}" if attempt else "", "the write was rejected"]
        return VerifyEvidence(summary=_summary(parts), rows=_rows_with_data(rows))
    test = _test_clause(result.test_speed_pct, result.wait_seconds, via)
    rpm = f"RPM {init.rpm} → {final.rpm}" if init.rpm is not None and final.rpm is not None else ""
    return VerifyEvidence(summary=_summary([test, rpm]), rows=_rows_with_data(rows))
