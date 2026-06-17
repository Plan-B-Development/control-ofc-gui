"""Cross-stack role-classification agreement (DEC-162).

The daemon's role-floor backstop independently classifies each control member as
pump/CPU, GPU, or chassis to decide whether the 30% hard pump floor applies. That
classifier (``profile.rs::{member_is_gpu, member_is_pump_or_cpu}``) must agree with
the GUI's author-side ``infer_member_role`` for every member, or a profile the GUI
bakes could be wrongly rejected by the daemon's ``FLOOR_TOO_LOW`` validation.

This drives the GUI ``infer_member_role`` against the canonical
``role_classification.json`` oracle; the daemon runs the *same* byte-identical
fixture (``daemon/tests/fixtures/role_classification.json``,
``profile.rs::role_classification_matches_oracle``). Agreement on both sides pins
the two classifiers together so silent drift fails on at least one side.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.services.profile_service import ControlMember, infer_member_role

FIXTURE = Path(__file__).parent / "fixtures" / "role_classification.json"
_CASES = json.loads(FIXTURE.read_text())["cases"]

# Sibling daemon repo (local dev only — absent in GUI-only CI checkouts).
_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "role_classification.json"
)


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_role_fixture_copies_are_byte_identical():
    """The GUI and daemon role fixtures must be byte-identical (DEC-162).

    Asserting the same oracle on both sides only proves cross-stack agreement if
    the two copies are the same bytes.
    """
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "role_classification.json drifted between the GUI and daemon copies"
    )


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_role_classification_parity(case):
    """GUI ``infer_member_role`` matches the shared oracle for every member."""
    member = ControlMember.from_dict(case)
    assert infer_member_role(member) == case["role"], case["name"]
