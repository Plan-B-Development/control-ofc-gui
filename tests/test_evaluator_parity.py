"""Cross-stack evaluator parity (DEC-126).

Drives the GUI evaluator against the canonical ``parity_vectors.json`` and
asserts the hand-authored oracle. The daemon runs the *same* fixture against its
Rust evaluator (``daemon/tests/fixtures/parity_vectors.json``,
``profile_engine.rs``). When the two copies agree on the oracle, GUI-driven and
headless behaviour are pinned together — silent drift (the cause of DEC-096 /
DEC-119) fails on at least one side.

- ``curve_eval``: stateless ``CurveConfig.interpolate`` vs ``evaluate_curve``.
- ``tuning_sequence``: the real ``ControlLoopService._cycle`` over a temp
  sequence, comparing the integer wire PWM per member (``member_outputs``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_ofc.api.models import (
    ConnectionState,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.profile_service import CurveConfig, Profile, ProfileService

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
    both load the *same* bytes. Skipped in GUI-only CI; enforced locally and at
    /release via sha256.
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


@pytest.mark.parametrize("vector", _VECTORS["tuning_sequence"], ids=_id)
def test_tuning_sequence_parity(vector, qtbot):
    profile = Profile.from_dict(vector["profile"])
    control_id = profile.controls[0].id
    sensor_id = vector["sensor_id"]

    svc = ProfileService()
    svc._profiles[profile.id] = profile
    svc.set_active(profile.id)

    state = AppState()
    state.connection = ConnectionState.CONNECTED
    state.mode = OperationMode.AUTOMATIC
    loop = ControlLoopService(state, svc, client=MagicMock())
    loop._running = True

    captured: list = []
    loop.status_changed.connect(captured.append)

    tracked = [m["member_id"] for m in vector["expected"]]
    produced: dict[str, list[int]] = {mid: [] for mid in tracked}
    for temp in vector["temps"]:
        state.sensors = [
            SensorReading(id=sensor_id, kind="CpuTemp", label="CPU", value_c=temp, age_ms=100)
        ]
        captured.clear()
        loop._cycle()
        members = captured[-1].member_outputs[control_id]
        for mid in tracked:
            produced[mid].append(round(members[mid]))

    for member in vector["expected"]:
        assert produced[member["member_id"]] == member["pwm"], (
            f"{vector['name']} / {member['member_id']}"
        )
