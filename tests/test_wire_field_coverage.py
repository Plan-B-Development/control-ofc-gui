"""Wire-field coverage: every field the daemon publishes has a model slot, and
the load-bearing ones are actually read by production code (``WIRE-aj``).

Why this exists
---------------
The 2026-09-05 wire-surface sweep found 41 divergences between what the daemon
serialises and what the GUI models, and the systemic reason none of them was
caught is that **nothing compared the two surfaces**. ``WIRE-a`` — a degraded
daemon runtime config the GUI never parsed — survived a whole release that way.

Two assertions, and the second is the one that matters
------------------------------------------------------
1. *Slot coverage.* Every declared wire key resolves to a field on the GUI
   dataclass that models it. This is what would have caught ``WIRE-h``, where
   ``InventoryPwmControl`` silently dropped eight DEC-316 fields while its
   docstring claimed a field-for-field mirror.
2. *Production read.* For fields declared ``must_be_read``, the name appears in
   production source **outside** ``api/models.py``. A dataclass slot alone is
   not coverage — ``CLAUDE.md § Hard-won lessons`` records twelve recurrences of
   *extracting a rule does not test the call site*, and ``WIRE-ak``'s whole
   parsed-but-never-read category is that lesson in wire-contract form. A field
   the GUI parses and no one reads is decoration, and having it in the type is
   precisely what makes the gap invisible.

The declared surface lives in ``tests/fixtures/wire_fields.json`` and is pinned
on the daemon side by ``daemon/src/api/responses.rs::tests::
wire_field_surface_is_pinned``. Neither copy can drift alone: a new daemon field
reds that Rust test, and updating the fixture to match then reds this one until
the GUI models it.

Scope is honest and partial by design: ten structs covering ``/sensors``,
``/fans``, ``/poll``, ``/hwmon/headers``, ``/inventory/hwmon`` and
``/inventory/cooling-devices`` — the surfaces where drift has actually happened.
Adding a struct is a fixture edit plus a Rust arm; it is not automatic.
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from control_ofc.api import models

FIXTURE = Path(__file__).parent / "fixtures" / "wire_fields.json"
SRC = Path(__file__).resolve().parents[1] / "src" / "control_ofc"
MODELS = SRC / "api" / "models.py"


def _declared() -> list[dict]:
    data = json.loads(FIXTURE.read_text())
    structs = data["structs"]
    # Self-validation: a parser or fixture that yields nothing passes every
    # assertion below while proving nothing (CLAUDE.md's "assert you did not
    # skip every case" rule).
    assert len(structs) >= 11, "the declared wire surface must not shrink silently"
    for s in structs:
        assert s["fields"], f"{s['daemon']} declares no fields"
    return structs


def _production_text() -> str:
    """Every production ``.py`` except ``api/models.py``.

    ``models.py`` is excluded deliberately: it is where the field is *declared*,
    so including it would make the read-check pass on the declaration alone —
    the exact tautology this test exists to avoid.
    """
    parts = []
    for path in sorted(SRC.rglob("*.py")):
        if path == MODELS:
            continue
        parts.append(path.read_text())
    return "\n".join(parts)


PRODUCTION = _production_text()


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_every_wire_field_has_a_model_slot(struct: dict) -> None:
    cls = getattr(models, struct["gui"], None)
    assert cls is not None, f"{struct['gui']} is not exported from control_ofc.api.models"
    assert is_dataclass(cls), f"{struct['gui']} is not a dataclass"

    slots = {f.name for f in dataclass_fields(cls)}
    exempt = set(struct.get("unmodelled", {}))
    missing = [f for f in struct["fields"] if f not in slots and f not in exempt]

    assert not missing, (
        f"{struct['gui']} does not model {len(missing)} field(s) the daemon's "
        f"{struct['daemon']} publishes on {', '.join(struct['endpoints'])}: "
        f"{sorted(missing)}. Either add the field, or declare it in the fixture's "
        f"'unmodelled' map with the reason it is inert."
    )


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_declared_unmodelled_fields_are_really_absent(struct: dict) -> None:
    """An ``unmodelled`` exemption that is no longer true is a stale claim.

    Without this, a field could be modelled *and* exempted, and the exemption
    would silently outlive its reason — the retraction-left-standing failure
    ``CLAUDE.md § Workflow documentation protocol`` rule 2 is about.
    """
    exempt = struct.get("unmodelled", {})
    if not exempt:
        pytest.skip("no exemptions declared")
    cls = getattr(models, struct["gui"])
    slots = {f.name for f in dataclass_fields(cls)}
    stale = sorted(name for name in exempt if name in slots)
    assert not stale, (
        f"{struct['gui']} now models {stale}, which the fixture still exempts as "
        f"unmodelled. Delete the stale exemption."
    )


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_load_bearing_fields_are_read_by_production_code(struct: dict) -> None:
    """A slot is not a consumer.

    Every name in ``must_be_read`` must appear somewhere in production source
    outside ``models.py``. Deliberately coarse — it proves a read site exists,
    not that the read is correct — but it is what separates a field the GUI
    *uses* from one it merely *parses*, which is the whole ``WIRE-ak`` category.
    Only distinctive names are declared here; a generic one like ``id`` would
    match everywhere and assert nothing.
    """
    unread = [name for name in struct.get("must_be_read", []) if name not in PRODUCTION]
    assert not unread, (
        f"{struct['gui']} declares {sorted(unread)} load-bearing, but no production "
        f"module outside api/models.py mentions them — they are parsed and never read."
    )


def test_must_be_read_names_are_declared_fields() -> None:
    """A typo in ``must_be_read`` would make the read-check vacuous for that field."""
    for struct in _declared():
        declared = set(struct["fields"])
        stray = sorted(set(struct.get("must_be_read", [])) - declared)
        assert not stray, f"{struct['daemon']}: must_be_read names not on the wire: {stray}"


def test_must_be_read_names_are_distinctive() -> None:
    """Guard the read-check against names so common they match by accident.

    ``id``/``label``/``source``/``name`` appear in thousands of unrelated lines,
    so declaring one load-bearing would assert nothing at all — the same
    "passes with the rule deleted" trap as an ``isVisible()`` assertion under
    offscreen Qt.
    """
    too_common = {"id", "label", "source", "name", "kind", "state", "value", "index"}
    for struct in _declared():
        bad = sorted(set(struct.get("must_be_read", [])) & too_common)
        assert not bad, (
            f"{struct['daemon']}: {bad} are too common to prove a read site; "
            f"assert them at a specific call site instead."
        )
