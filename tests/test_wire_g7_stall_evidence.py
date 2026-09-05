"""G7 — the stall alert says what was observed (`WIRE-j`).

The daemon asserts `stall_detected` from `rpm == 0 && pwm > STALL_PWM_THRESHOLD`
where `pwm` is `last_commanded_pwm` — and for an hwmon header that field carries
the poll's *readback* whenever nothing has been commanded (`AIO5-a`). The
Dashboard's `error`-level alert therefore said "RPM=0 while PWM commanded" about
headers nothing was commanding, and the fan-card LOW_RPM heuristic compared the
same ambiguous number against the floor.

`WIRE-o`, this package's other row, adds two new alert conditions and was split
out to `/ofc:new-feature`: whether a BIOS-controlled header is an *alert* or a
*state* is a UX decision, and on many boards it is the normal case.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import FanReading
from control_ofc.services.app_state import _stall_detail
from control_ofc.services.fan_cards_view import FanState, _derive_state
from control_ofc.services.header_inspector_view import requested_pct

# ── The rule itself, now on the data so every consumer can reach it ──────────


def test_the_command_outranks_the_ambiguous_field() -> None:
    fan = FanReading(id="f", last_commanded_pwm=70, pwm_commanded_pct=40)
    assert fan.requested_duty() == (40, False)


def test_the_ambiguous_field_is_used_but_flagged_approximate() -> None:
    fan = FanReading(id="f", last_commanded_pwm=70)
    assert fan.requested_duty() == (70, True)


def test_nothing_reported_is_not_zero() -> None:
    assert FanReading(id="f").requested_duty() == (None, False)


def test_the_header_inspector_still_answers_identically() -> None:
    """The rule moved out of that module; it must not have changed on the way.

    Asserted as a relationship against `FanReading.requested_duty` over several
    shapes, so a delegation that dropped the approximate flag — or inverted the
    precedence — fails here.
    """
    for fan in (
        FanReading(id="f", last_commanded_pwm=70, pwm_commanded_pct=40),
        FanReading(id="f", last_commanded_pwm=70),
        FanReading(id="f"),
    ):
        assert requested_pct(fan) == fan.requested_duty()
    assert requested_pct(None) == (None, False)


# ── The alert wording ────────────────────────────────────────────────────────


def test_a_commanded_stall_states_the_command() -> None:
    detail = _stall_detail(FanReading(id="cpu_fan", pwm_commanded_pct=45, rpm=0))
    assert "the daemon commands 45%" in detail
    assert "may not be under control" not in detail


def test_an_uncommanded_stall_does_not_claim_a_command() -> None:
    """The defect, stated directly: for an uncontrolled hwmon header the only
    number available is a readback, and calling it a command is false."""
    detail = _stall_detail(FanReading(id="cha_fan2", last_commanded_pwm=60, rpm=0))
    assert "the daemon commands" not in detail
    assert "the header reports 60%" in detail
    assert "may not be under control at all" in detail


def test_the_two_wordings_differ(caplog) -> None:
    """Precondition: the branch actually moved. Two `not in` assertions can both
    hold against a single string containing neither phrase."""
    commanded = _stall_detail(FanReading(id="f", pwm_commanded_pct=45))
    ambiguous = _stall_detail(FanReading(id="f", last_commanded_pwm=45))
    assert commanded != ambiguous


def test_a_stall_with_no_duty_at_all_still_reports_something() -> None:
    detail = _stall_detail(FanReading(id="f"))
    assert "stall detected" in detail
    assert "%" not in detail


def test_the_retired_wording_is_gone() -> None:
    """`CLAUDE.md § Workflow documentation protocol` rule 2: a retraction is an
    edit everywhere the claim appears."""
    for fan in (
        FanReading(id="f", pwm_commanded_pct=45),
        FanReading(id="f", last_commanded_pwm=45),
        FanReading(id="f"),
    ):
        assert "while PWM commanded" not in _stall_detail(fan)


# ── The fan-card LOW_RPM heuristic ───────────────────────────────────────────


def _state(fan: FanReading) -> FanState:
    return _derive_state(fan, overridden=False, floor=20.0)


def test_low_rpm_fires_on_a_real_command() -> None:
    fan = FanReading(id="hwmon:x:pwm1", source="hwmon", rpm=0, pwm_commanded_pct=50)
    assert _state(fan) is FanState.LOW_RPM


def test_low_rpm_does_not_fire_on_a_command_below_the_floor() -> None:
    """The opposite branch — an unconditional LOW_RPM would pass the test above."""
    fan = FanReading(id="hwmon:x:pwm1", source="hwmon", rpm=0, pwm_commanded_pct=10)
    assert _state(fan) is not FanState.LOW_RPM


def test_low_rpm_prefers_the_command_over_the_ambiguous_field() -> None:
    """A header the daemon commands at 10% whose *readback* still reads 70%
    must not be flagged: the heuristic is about a command that produced no
    rotation, and 10% is below the floor."""
    fan = FanReading(
        id="hwmon:x:pwm1", source="hwmon", rpm=0, last_commanded_pwm=70, pwm_commanded_pct=10
    )
    assert _state(fan) is not FanState.LOW_RPM


@pytest.mark.parametrize("source", ["amd_gpu", "intel_gpu", "nvidia_gpu"])
def test_gpu_fans_are_still_exempt(source: str) -> None:
    """DEC-047: a zero-RPM idle is normal for a GPU fan."""
    fan = FanReading(id=f"{source}:0", source=source, rpm=0, pwm_commanded_pct=50)
    assert _state(fan) is not FanState.LOW_RPM
