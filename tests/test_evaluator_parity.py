"""Cross-stack evaluator parity (DEC-126).

Drives the GUI evaluator against the canonical ``parity_vectors.json`` and
asserts the hand-authored oracle. The daemon runs the *same* fixture against its
Rust evaluator (``daemon/tests/fixtures/parity_vectors.json``,
``profile_engine.rs``). When the two copies agree on the oracle, GUI-driven and
headless behaviour are pinned together — silent drift (the cause of DEC-096 /
DEC-119) fails on at least one side.

- ``curve_eval``: stateless ``CurveConfig.interpolate`` vs ``evaluate_curve``.
- ``tuning_sequence``: the floor-bearing invariants only. The GUI's stateful
  tuning pipeline moved to the daemon at the 2.0 flip (DEC-165), so sequence
  parity is pinned daemon-side; here the GUI's member classification and load
  sanitisation are held against the oracle's sequences.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.services.profile_service import (
    CONTROL_ROLE_CPU_PUMP,
    CONTROL_ROLE_GPU,
    ROLE_MINIMUM_PCT,
    CurveConfig,
    Profile,
    infer_member_role,
)

FIXTURE = Path(__file__).parent / "fixtures" / "parity_vectors.json"
_VECTORS = json.loads(FIXTURE.read_text())

# Sibling daemon repo (local dev only — absent in GUI-only CI checkouts).
_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "parity_vectors.json"
)


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_fixture_copies_are_byte_identical():
    """The GUI and daemon parity fixtures must be byte-identical (DEC-126).

    Asserting the same oracle on both sides only proves cross-stack parity if
    both load the *same* bytes. This test runs whenever both repos are checked
    out as siblings — locally and during /release (which runs the pytest gate
    with the daemon repo present). It is skipped in single-repo CI; that hole is
    covered instead by each repo's `.github/workflows/parity.yml`, which checks
    out the peer repo and byte-compares this fixture on any change to it.
    """
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "parity_vectors.json drifted between the GUI and daemon copies"
    )


def _id(case: dict) -> str:
    return case["name"]


@pytest.mark.parametrize("case", _VECTORS["curve_eval"], ids=_id)
def test_curve_eval_parity(case):
    curve = CurveConfig.from_dict(case["curve"])
    result = curve.interpolate(case["temp"])
    assert result == pytest.approx(case["expected_pct"], abs=0.01)


# The GUI's full tuning/hysteresis pipeline moved to the daemon at the 2.0 flip
# (DEC-165); its sequence parity is pinned daemon-side against the same fixture
# (``tuning_sequence``). What the GUI still decides is which floor each member
# gets: its member classification stamps ``minimum_pct`` and feeds the floor the
# Controls page shows, and its loader sanitises a pump's ``stop_pct``. The tests
# below hold those GUI rules against the oracle's floor-bearing sequences (GSA-l).


def _members_by_expected_id(case: dict):
    """(member, loaded control, oracle pwm sequence) for each expected member,
    with the profile loaded through the GUI's own path."""
    profile = Profile.from_dict(case["profile"])
    by_id = {m.member_id: (m, c) for c in profile.controls for m in c.members}
    for expected in case["expected"]:
        member, control = by_id[expected["member_id"]]
        yield member, control, expected["pwm"]


_TUNING = _VECTORS["tuning_sequence"]


def test_the_oracle_holds_every_non_gpu_member_at_its_controls_minimum():
    """A non-GPU member never runs between 0 and its control's minimum; a GPU
    member is exempt (DEC-095: 0 % GPU floor). Classifying a member the wrong
    way here would show a floor the daemon does not apply, or hide one it does."""
    seen_gpu_below_minimum = False
    for case in _TUNING:
        for member, control, pwm in _members_by_expected_id(case):
            if infer_member_role(member) == CONTROL_ROLE_GPU:
                seen_gpu_below_minimum |= any(v < control.minimum_pct for v in pwm)
                continue
            for value in pwm:
                assert value == 0 or value >= control.minimum_pct, (case["name"], member)
    assert seen_gpu_below_minimum, "precondition: the oracle exempts a GPU member"


def test_the_oracle_never_stops_or_underruns_a_member_the_gui_calls_a_pump():
    """The pump/CPU floor (DEC-095/167): what the GUI classifies as pump/CPU the
    oracle holds at or above that floor and never stops — so the GUI's loader
    zeroing the member's ``stop_pct`` agrees with the daemon's evaluation."""
    floor = ROLE_MINIMUM_PCT[CONTROL_ROLE_CPU_PUMP]
    pump_cases = 0
    for case in _TUNING:
        for member, control, pwm in _members_by_expected_id(case):
            if infer_member_role(member) != CONTROL_ROLE_CPU_PUMP:
                continue
            pump_cases += 1
            assert min(pwm) >= floor, case["name"]
            assert control.stop_pct == 0.0, case["name"]
    assert pump_cases, "precondition: the oracle carries a pump/CPU member"
