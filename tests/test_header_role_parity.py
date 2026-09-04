"""Cross-stack HWMON header pump-protection agreement (AUD3-c, DEC-322).

[SAFETY] This pins the classifier that decides whether the fan wizard may offer
to **stop** a header.

``services/pump_protection.py`` hand-mirrors the daemon's
``hwmon::roles::classify_header_role`` label branches. It has to: a daemon older
than 2.31.0 publishes no ``stop_permitted`` field, so the reconstruction is the
only answer available, and the GUI is documented to support those daemons. Until
this fixture there was no gate holding the two copies together — unlike the two
previous times this project faced exactly the same problem (DEC-126's
``parity_vectors.json``, DEC-162's ``role_classification.json``).

The direction of harm is the unsafe one. If the daemon learns a new
pump-classifying label — ``/ofc:superio-curator`` exists to add precisely that
kind of hardware knowledge — and the GUI's copy does not, the reconstruction
returns "not protected" and the wizard offers to stop a real pump.

The daemon runs the *same* byte-identical fixture
(``daemon/tests/fixtures/header_role_classification.json``,
``roles.rs::header_role_classification_matches_the_cross_stack_oracle``), and
``parity.yml`` in both repos fails if the two copies diverge.

Every case is driven with ``stop_permitted=None``, which is what forces the
reconstruction path rather than the wire field — the whole point is to test the
fallback, since the fallback is what an older daemon leaves the GUI holding.

**Honest limit on how much of this oracle discriminates.** The GUI's
reconstruction short-circuits on ``header.role == "pump"``, and ``role`` is the
daemon's *own* answer arriving over the wire — so for every pump-labelled case
the two sides agree by construction rather than by independent agreement.
Measured: deleting ``"cpu_fan"`` from the GUI's mirrored prefix list fails
exactly **one** of these cases (``a_label_outranks_the_chip_mapping``), not all
of them. The oracle's real discriminating power sits in the cases where ``role``
is *not* ``pump`` but the label and ``is_aio`` evidence still decides the answer,
because that is the only place the GUI reasons independently. Those cases are
therefore the ones to preserve and extend; do not read "29 cases pass" as "29
independent agreements". The ``role == "pump"`` short-circuit is itself correct
and deliberate — trusting the daemon over a local guess is the safe direction —
which is why the limit is recorded here rather than engineered away.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.api.models import Capabilities, HwmonHeader
from control_ofc.services.pump_protection import header_is_pump_protected

FIXTURE = Path(__file__).parent / "fixtures" / "header_role_classification.json"
_CASES = json.loads(FIXTURE.read_text())["cases"]

# Sibling daemon repo (local dev only — absent in GUI-only CI checkouts).
_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "header_role_classification.json"
)


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_header_role_fixture_copies_are_byte_identical():
    """The GUI and daemon copies must be the same bytes.

    Asserting the same oracle on both sides only proves cross-stack agreement if
    the two copies are the same oracle.
    """
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "header_role_classification.json drifted between the GUI and daemon copies"
    )


def _caps() -> Capabilities:
    """Capabilities with ``control.header_roles`` on.

    The reconstruction is gated on it — a pre-2.28.0 daemon has no role model at
    all, so there is nothing to reconstruct from and the predicate returns False
    for everything. Driving the oracle without this would make every case pass
    vacuously as "not protected", which is the failure mode the assertion at the
    foot of this file exists to catch.
    """
    caps = Capabilities()
    caps.control.header_roles = True
    return caps


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_header_pump_protection_parity(case):
    """GUI ``header_is_pump_protected`` matches the shared oracle."""
    header = HwmonHeader(
        id=f"hwmon:{case['chip_name']}:dev:pwm{case['pwm_index']}:{case['label']}",
        label=case["label"],
        chip_name=case["chip_name"],
        pwm_index=case["pwm_index"],
        is_aio=case["is_aio"],
        role=case["role"],
        # None, deliberately: this is the reconstruction path, and `None` must be
        # read as "this daemon did not say" rather than as "no".
        stop_permitted=None,
    )
    assert header_is_pump_protected(header, _caps()) == case["pump_protected"], case["name"]


def test_the_oracle_contains_both_answers():
    """Guard against a vacuous suite.

    If every case were ``pump_protected: false`` the parametrised test above
    would pass with the predicate hard-wired to ``return False`` — which is
    exactly the unsafe direction. Assert the oracle exercises both.
    """
    answers = {c["pump_protected"] for c in _CASES}
    assert answers == {True, False}, f"the oracle only ever expects {answers}"
