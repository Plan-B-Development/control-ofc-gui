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

The declared surface lives in ``tests/fixtures/wire_fields.json`` and it is the
**single declaration** (``P8-cb``). The daemon's
``api/responses.rs::tests::wire_field_surface_is_pinned`` reads that same file and
asserts each struct's serialised key set against it; this module asserts the same
lists against the GUI dataclasses. A daemon field rename therefore reds the Rust
test, naming the fixture — and there is no second list anyone can forget.

**It became an interlock in daemon v2.43.8 / GUI v2.67.1, and was a workflow
before that.** ``G33`` declared the Phase 8 structs on this side only; ``P8-ca``
gave all 29 a daemon-side arm — but each side was still pinned to *its own*
declaration, the Rust ``want`` arrays against the Rust structs and this fixture
against the dataclasses, with nothing comparing the two lists. A rename fixed in
the Rust arm and forgotten here left this fixture stale with both suites green.
The ``want`` arrays are gone; the fixture is what the Rust test reads.

**The copies are the remaining seam, and they are guarded.** It is a shared oracle
in the ``parity_vectors.json`` shape (DEC-126): one byte-identical copy per repo,
compared by ``test_fixture_copies_are_byte_identical`` below when both repos are
checked out as siblings, and by ``.github/workflows/parity.yml`` in both repos for
single-repo CI. Coverage is asserted **both ways** on the daemon side — a struct
declared here with no arm there, or an arm there for a struct not declared here,
fails.

Scope covers the structs behind ``/sensors``, ``/fans``, ``/poll``,
``/hwmon/headers``, ``/inventory/hwmon``, ``/inventory/cooling-devices``,
``/capabilities`` (``Limits``) and ``/diagnostics/hardware`` (``VoltageEntry``) —
the surfaces where drift has actually happened — plus the Phase 8 diagnostic
surfaces: preflight, control-path discovery, PWM characterisation, steady state
and the startup fingerprint. Adding a struct is a fixture edit plus a Rust arm;
it is not automatic.
"""

from __future__ import annotations

import ast
import json
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from control_ofc.api import models

FIXTURE = Path(__file__).parent / "fixtures" / "wire_fields.json"
SRC = Path(__file__).resolve().parents[1] / "src" / "control_ofc"
MODELS = SRC / "api" / "models.py"

#: The daemon's byte-identical copy, present when both repos are checked out as
#: siblings. Same shape as ``test_evaluator_parity._DAEMON_FIXTURE`` (DEC-126).
_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "wire_fields.json"
)


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_fixture_copies_are_byte_identical():
    """The GUI and daemon copies of the wire oracle must be byte-identical (`P8-cb`).

    This is the half that turns the pin into an **interlock**. Both sides now
    check their own source against *this file* — the GUI's dataclasses here, the
    daemon's serialised key sets in ``responses.rs::wire_field_surface_is_pinned``
    — so a rename reds one of them and names the fixture. What that alone cannot
    catch is the two COPIES drifting, which is why they are compared here and, for
    single-repo CI, by ``.github/workflows/parity.yml`` in both repos.

    Before this, each side declared its own list and nothing compared the two: a
    daemon rename fixed in the Rust arm and forgotten here left this fixture stale
    with both suites green.
    """
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "wire_fields.json drifted between the GUI and daemon copies"
    )


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


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the ``Constant`` nodes that are docstrings, so they can be skipped."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def _names_used(tree: ast.AST) -> set[str]:
    """Identifiers and string keys this AST actually *uses*.

    Four node kinds cover how a wire field is reached: ``obj.field``
    (``Attribute``), ``Cls(field=…)`` (``keyword``), a bare binding or read
    (``Name``), and — the one an identifier-only walk misses —
    ``getattr(obj, "field", default)`` / ``data.get("field")``, which is a string
    ``Constant``. Docstrings are excluded; comments are not in the AST at all,
    which is the whole reason this reads the tree rather than the text.
    """
    skip = _docstring_nodes(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
        ):
            names.add(node.value)
    return names


def _production_names() -> set[str]:
    """Every identifier production code *uses*, as opposed to merely declares.

    A substring search over raw source counts comments and docstrings, so a field
    could satisfy ``must_be_read`` by being *described* somewhere rather than
    read — and a guard a prose mention can satisfy is the failure mode
    ``CLAUDE.md § Hard-won lessons`` records for source-scanning tests. Hence the
    AST.

    ``api/models.py`` is included, but **only its function bodies**. That
    distinction is the point, and it is not the same as excluding the file: a
    field *declaration* there is not a consumer, while a method that computes
    from the field is — `FanReading.requested_duty` reads `pwm_commanded_pct`
    and is the single site every caller goes through (DEC-276, `WIRE-j`).
    Excluding the whole file called that field unread; including the whole file
    would let the bare declaration satisfy the check, which is the tautology this
    test exists to avoid.
    """
    names: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if path == MODELS:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    for stmt in node.body:
                        names |= _names_used(stmt)
        else:
            names |= _names_used(tree)
    return names


PRODUCTION_NAMES = _production_names()


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

    Every name in ``must_be_read`` must be an identifier production code outside
    ``models.py`` actually uses — an attribute access, a keyword argument, or a
    binding. Deliberately coarse: it proves a read site exists, not that the read
    is correct. But that is what separates a field the GUI *uses* from one it
    merely *parses*, which is the whole ``WIRE-ak`` category. Only distinctive
    names are declared here; a generic one like ``id`` would match everywhere and
    assert nothing, which ``test_must_be_read_names_are_distinctive`` enforces.
    """
    unread = [name for name in struct.get("must_be_read", []) if name not in PRODUCTION_NAMES]
    assert not unread, (
        f"{struct['gui']} declares {sorted(unread)} load-bearing, but no production "
        f"code USES them — a declaration in api/models.py is not a read site, and "
        f"neither is a comment. They are parsed and never read."
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
