"""Cross-stack evaluator parity (DEC-126).

Drives the GUI evaluator against the canonical ``parity_vectors.json`` and
asserts the hand-authored oracle. The daemon runs the *same* fixture against its
Rust evaluator (``daemon/tests/fixtures/parity_vectors.json``,
``profile_engine.rs``). When the two copies agree on the oracle, GUI-driven and
headless behaviour are pinned together — silent drift (the cause of DEC-096 /
DEC-119) fails on at least one side.

- ``curve_eval``: stateless ``CurveConfig.interpolate`` vs ``evaluate_curve``.

The GUI's stateful tuning pipeline moved to the daemon at the 2.0 flip (DEC-165),
so its ``tuning_sequence`` parity is pinned daemon-side now; the GUI keeps the
stateless tier honest here (it still backs demo/preview).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.services.profile_service import CurveConfig

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
# (DEC-165); its parity is now pinned daemon-side against the same fixture
# (``tuning_sequence``). The GUI keeps only the stateless ``interpolate`` tier
# honest here, which is what demo/preview still use.
