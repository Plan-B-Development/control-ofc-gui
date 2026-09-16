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

The classification is EXHAUSTIVE, and it did not used to be (``AU-d``)
----------------------------------------------------------------------
``must_be_read`` was opt-in, so this module could only confirm the reads someone
had already thought to declare — it could not *discover* a new parsed-but-unread
field, which is the failure that produced ``WIRE-e``/``WIRE-f``/``WIRE-m``/
``WIRE-o``/``WIRE-y`` in the first place (all five were found by a manual sweep).
Measured at the time: 107 of 286 declared fields carried a read assertion, so the
paragraph above **overstated its reach** — it addressed 37% of the category it
named. That claim is retracted; what follows is what replaces it.

Every declared field now lands in exactly one bucket, and the choice is forced by
``test_every_declared_field_is_classified``:

``must_be_read``
    A production read site outside ``api/models.py`` must exist. Checked.
``inert``
    Nothing reads it, with the reason. Checked *the other way* by
    ``test_inert_fields_are_really_unread`` — the day someone wires one up, the
    fixture must move it, so this is a live claim and not a dead list.
``not_assertable``
    The name is too common for a name-based check to prove anything about it
    (``id``, ``label``, ``source``, …). No read claim is made; the bucket is
    restricted to :data:`TOO_COMMON` so it cannot become an escape hatch.
``unmodelled``
    No GUI slot at all, with the reason. Checked by
    ``test_declared_unmodelled_fields_are_really_absent``.

Adding a wire field therefore fails this module until someone says which of those
it is. That is the whole point of the inversion: the guard's reach is now the
same size as its subject.

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


#: Names that appear in thousands of unrelated lines, so a name-based check can
#: prove nothing about them. ``must_be_read`` may not contain one and
#: ``not_assertable`` may contain nothing else.
TOO_COMMON = frozenset({"id", "label", "source", "name", "kind", "state", "value", "index"})


def _key_strings(tree: ast.AST) -> set[str]:
    """String literals used to LOOK SOMETHING UP, as opposed to merely written.

    ``getattr(obj, "field", default)``, ``data.get("field")`` and
    ``data["field"]`` are reads that an identifier-only walk misses, so they have
    to count. A string literal sitting in a **dict-literal key** position does
    not: it declares a table entry, not a read.

    The distinction is not academic (``AU-d``). ``services/provenance.py`` maps
    wire-field names to a provenance class, so every name in that table matched a
    bare ``Constant`` walk — and three ``must_be_read`` entries
    (``TachObservation.delta_rpm``, ``PointStability.dropouts`` and ``.outliers``)
    were satisfied by **nothing but that table**, i.e. by a mapping *about* the
    field rather than by anyone reading it. Restricting to argument position
    moves those three to ``inert`` where they belong, while keeping
    ``Limits.openfan_stop_timeout_s`` (read as
    ``getattr(getattr(caps, "limits", None), "openfan_stop_timeout_s", 0)`` at
    ``settings_page.py:2072``) and ``CharPoint.settled_ms`` (``getattr(point,
    "settled_ms", None)``), which are real reads that only exist in string form.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
                candidate = node.args[1]
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in {"get", "pop", "setdefault"}
                and node.args
            ):
                candidate = node.args[0]
            else:
                continue
        elif isinstance(node, ast.Subscript):
            candidate = node.slice
        else:
            continue
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            out.add(candidate.value)
    return out


def _names_used(tree: ast.AST) -> set[str]:
    """Identifiers and lookup keys this AST actually *uses*.

    Four node kinds cover how a wire field is reached: ``obj.field``
    (``Attribute``), ``Cls(field=…)`` (``keyword``), a bare binding or read
    (``Name``), and a lookup key in argument or subscript position — see
    :func:`_key_strings` for why the last one is not simply "any string".
    Comments are not in the AST at all, which is the whole reason this reads the
    tree rather than the text.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names | _key_strings(tree)


def _attribute_reads(tree: ast.AST) -> set[str]:
    """Only ``obj.field`` accesses. Used for ``api/models.py`` alone.

    In that file a *computed property* is a consumer and ``from_dict`` is not,
    and the two are told apart by shape: ``requested_duty`` reads
    ``self.pwm_commanded_pct`` (an ``Attribute``), while the parser writes
    ``cycle=int(c.get("cycle") or 0)`` — a keyword and a lookup key, both of
    which describe the field being *created* rather than read. Counting the
    latter would let every parsed field satisfy the read check via the parser
    that produced it, which is the tautology this module exists to avoid.
    """
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def _consumer_names() -> set[str]:
    """Every name production code outside ``api/models.py`` reads.

    This is what an ``inert`` claim is checked against: "nothing reads it" means
    nothing *outside the parser that created it*, because ``models.py`` mentions
    every field it parses by construction.
    """
    names: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        if path == MODELS:
            continue
        names |= _names_used(ast.parse(path.read_text(), filename=str(path)))
    return names


def _production_names() -> set[str]:
    """Every name production code reads, ``api/models.py``'s own properties included.

    :func:`_consumer_names` plus the attribute reads inside ``models.py``'s
    function bodies. That addition is deliberate and narrow: ``requested_duty``
    reads ``pwm_commanded_pct`` and is the single site every caller goes through
    (DEC-276, ``WIRE-j``), so excluding the whole file would call that field
    unread — while including the file wholesale would let ``from_dict``'s own
    parse of a field stand in for someone reading it. Attributes only splits
    those two apart; see :func:`_attribute_reads`.
    """
    names = _consumer_names()
    tree = ast.parse(MODELS.read_text(), filename=str(MODELS))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for stmt in node.body:
                names |= _attribute_reads(stmt)
    return names


PRODUCTION_NAMES = _production_names()
CONSUMER_NAMES = _consumer_names()


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


def test_classification_names_are_declared_fields() -> None:
    """A typo in any bucket makes that field's classification vacuous.

    Widened from ``must_be_read`` alone (``AU-d``): a misspelled ``inert`` entry
    would leave the real field unclassified *and* leave a claim about a field
    that does not exist, and the exhaustiveness check below would then blame the
    wrong name.
    """
    for struct in _declared():
        declared = set(struct["fields"])
        for bucket in ("must_be_read", "inert", "not_assertable", "unmodelled"):
            stray = sorted(set(struct.get(bucket, [])) - declared)
            assert not stray, f"{struct['daemon']}: {bucket} names not on the wire: {stray}"


def test_must_be_read_names_are_distinctive() -> None:
    """Guard the read-check against names so common they match by accident.

    ``id``/``label``/``source``/``name`` appear in thousands of unrelated lines,
    so declaring one load-bearing would assert nothing at all — the same
    "passes with the rule deleted" trap as an ``isVisible()`` assertion under
    offscreen Qt.
    """
    for struct in _declared():
        bad = sorted(set(struct.get("must_be_read", [])) & TOO_COMMON)
        assert not bad, (
            f"{struct['daemon']}: {bad} are too common to prove a read site; "
            f"assert them at a specific call site instead."
        )


# ---------------------------------------------------------------------------
# The inverted default (`AU-d`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_every_declared_field_is_classified(struct: dict) -> None:
    """Every wire field must be in exactly one bucket — this is the inversion.

    Before ``AU-d``, ``must_be_read`` was opt-in: a field added to the fixture
    with no entry anywhere was silently exempt from the read check, so this
    module could confirm the reads someone had already declared and could never
    *discover* a new unread one. Adding a field now fails here until someone says
    which of the four it is, which is the only way the guard's reach can match
    the category it names.

    Disjointness is asserted too, and it is not pedantry: a field in both
    ``must_be_read`` and ``inert`` carries two contradictory claims, and each of
    the checks below would pass its own half.
    """
    fields = list(struct["fields"])
    buckets = {
        b: set(struct.get(b, [])) for b in ("must_be_read", "inert", "not_assertable", "unmodelled")
    }

    overlaps = []
    names = sorted(buckets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for shared in sorted(buckets[a] & buckets[b]):
                overlaps.append(f"{shared} ({a} + {b})")
    assert not overlaps, (
        f"{struct['daemon']}: fields claimed by two buckets at once: {overlaps}. "
        f"A field is read, or inert, or unprovable, or unmodelled — not two of them."
    )

    classified = set().union(*buckets.values())
    unclassified = [f for f in fields if f not in classified]
    assert not unclassified, (
        f"{struct['daemon']} declares {sorted(unclassified)} with no classification. "
        f"Every wire field must say which it is: 'must_be_read' (a production read "
        f"site exists), 'inert' (nothing reads it — give the reason), "
        f"'not_assertable' (the name is too common to prove anything), or "
        f"'unmodelled' (no GUI slot — give the reason)."
    )


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_inert_fields_are_really_unread(struct: dict) -> None:
    """An ``inert`` claim that is no longer true is a stale claim.

    This is what stops the gap list rotting into a dead one: the day a field
    declared inert acquires a consumer, the fixture has to move it into
    ``must_be_read``. Same discipline as
    ``test_declared_unmodelled_fields_are_really_absent``, and the same failure
    it guards against — a retraction left standing (documentation protocol
    rule 2).

    Checked against :data:`CONSUMER_NAMES`, not :data:`PRODUCTION_NAMES`, because
    ``models.py``'s own ``from_dict`` mentions every field it parses; against the
    wider set this assertion would be vacuous for the four entries whose stated
    reason is precisely that the parser is their only mention.
    """
    inert = struct.get("inert", {})
    if not inert:
        pytest.skip("nothing declared inert")
    now_read = sorted(name for name in inert if name in CONSUMER_NAMES)
    assert not now_read, (
        f"{struct['gui']} declares {now_read} inert, but production code outside "
        f"api/models.py now uses those names. Move them to 'must_be_read' — an "
        f"exemption that has outlived its reason is worse than no exemption."
    )


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_not_assertable_names_are_really_ambiguous(struct: dict) -> None:
    """``not_assertable`` is restricted to the names that genuinely prove nothing.

    Without this the bucket is an escape hatch: any awkward field could be filed
    here and the inversion above would be satisfied while asserting nothing about
    it — the same shape as an ``isVisible()`` assertion under offscreen Qt, which
    passes with the rule deleted.
    """
    declared = struct.get("not_assertable", {})
    if not declared:
        pytest.skip("nothing declared not_assertable")
    abusable = sorted(set(declared) - TOO_COMMON)
    assert not abusable, (
        f"{struct['daemon']}: {abusable} are distinctive enough to assert a read "
        f"site for. 'not_assertable' is only for names in TOO_COMMON; declare "
        f"these 'must_be_read' or 'inert' instead."
    )


@pytest.mark.parametrize("struct", _declared(), ids=lambda s: s["daemon"])
def test_every_exemption_carries_a_reason(struct: dict) -> None:
    """A bucket entry with an empty reason is an unexplained exemption.

    The reason is what the next person reads to decide whether the exemption
    still holds; without it the fixture records that a check was skipped and not
    why, which is how a skip outlives its cause.
    """
    for bucket in ("inert", "not_assertable", "unmodelled"):
        entries = struct.get(bucket, {})
        assert isinstance(entries, dict), (
            f"{struct['daemon']}: '{bucket}' must be a name→reason map"
        )
        blank = sorted(name for name, reason in entries.items() if not str(reason).strip())
        assert not blank, f"{struct['daemon']}: {bucket} entries with no reason: {blank}"
