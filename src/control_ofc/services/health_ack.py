"""Occurrence-keyed silencing for the System State health surfaces (DEC-359).

One implementation, consumed by every half of the page: the condition cards, the
board notes, the Interference Monitor, and the Safety & GPU rows. The rule it
encodes is ISA-18.2's, arrived at over three ADRs:

* **DEC-282** — an acknowledgement marks an *occurrence*, never a bare key, or
  it silences every future recurrence forever.
* **DEC-357** — so the stored key carries what the machine said at the time.
* **DEC-358** — but compared by **rank**, not equality, or a change for the
  *better* breaks the silence too. That is what made a clean fan-control test
  resurrect every note the user had dismissed.

This module is the fourth step: the same rule, in one place, for surfaces that
previously had no lifecycle at all. It is Qt-free and holds no state.

Token format
------------
``key@level`` or ``key#fingerprint@level``. Both halves are opaque strings here:

* **fingerprint** identifies the *occurrence* — which modules collided, which
  ACPI ranges clashed. A different fingerprint is a different occurrence and is
  never covered by an older silence, which is how a condition that *changed*
  comes back. Empty for an item with only one possible shape.
* **level** is an ordered token whose rank the **caller** resolves, because the
  scales are domain-specific and belong with their vocabularies: evidence
  (``reference`` < … < ``observed``) for a note, severity for a condition. A
  silence holds while ``rank(current) <= rank(stored)``.

Storing the level as a token rather than as an integer is deliberate. An integer
would be meaningless in a settings file and — worse — would silently re-map every
stored silence if a scale ever gained a value in the middle. The token stays
correct as long as the *name* means what it meant.

Board-note tokens written by DEC-357/358 are ``key@evidence`` with no
fingerprint, which this format is a strict superset of — so existing dismissals
keep working with **no migration**.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Hard cap on a persisted silence list (`ACK-g`).
#:
#: Not a guess at "enough": it is ~4x the 36-quirk vendor table plus the ten
#: base conditions, so a machine cannot reach it by silencing everything it can
#: actually display. It exists because the list is user-controlled and
#: unbounded growth in a settings file is how `logs_level_filters` got its own
#: cap. Pruning against the live key set does the real work; this is the
#: backstop for keys that stop being produced entirely.
SILENCE_CAP = 200

_FINGERPRINT_CHARS = 12


@dataclass(frozen=True)
class Occurrence:
    """One silenceable thing, as it is *right now*."""

    key: str
    #: Digest of what produced this occurrence; "" when the item is binary.
    fingerprint: str
    #: Ordered token — `rank_of` turns it into a number. Domain-specific.
    level: str


def fingerprint(parts: Iterable[str]) -> str:
    """A short, stable digest of what produced an occurrence.

    Sorted before hashing, so the same set of facts in a different order is the
    same occurrence — the daemon does not promise an order for
    ``acpi_conflicts`` or ``module_collisions``, and treating a reordering as a
    new occurrence would un-silence on a refetch that found nothing new.

    Empty input yields ``""``, i.e. "this item has only one shape", rather than
    the hash of nothing — a binary condition and a condition whose evidence
    vanished must not collide.
    """
    items = sorted(p for p in parts if p)
    if not items:
        return ""
    joined = "␟".join(items)  # a separator no sysfs path or module name contains
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS]


def occurrence_token(occ: Occurrence) -> str:
    """The string a silence is stored as.

    The no-fingerprint form is emitted **without** the ``#``, which keeps board
    notes byte-identical to what DEC-357 and DEC-358 wrote and is the whole
    reason this change needs no settings migration.
    """
    head = f"{occ.key}#{occ.fingerprint}" if occ.fingerprint else occ.key
    return f"{head}@{occ.level}"


def parse_token(token: str) -> Occurrence | None:
    """Split a stored token, or ``None`` if it is not one.

    ``rpartition``/``partition`` rather than ``split``: a key may contain both
    separators (a quirk id can carry ``#``, a header id carries ``:`` and could
    carry ``@``), a level never does, and a fingerprint is hex. So the **last**
    ``@`` ends the key+fingerprint, and the **first** ``#`` after that split
    begins the fingerprint.
    """
    head, sep, level = token.rpartition("@")
    if not sep or not head:
        return None
    key, hsep, fp = head.partition("#")
    return Occurrence(key=key, fingerprint=fp if hsep else "", level=level)


def build_index(
    stored: Iterable[str],
    rank_of: Callable[[str], int],
    known_level: Callable[[str], bool] | None = None,
) -> dict[tuple[str, str], int]:
    """Map each silenced ``(key, fingerprint)`` to the **highest** rank stored.

    Highest-wins because entries accumulate: silence a note while it is
    unverified, then again once it is observed, and the second is the one that
    describes what the user chose to live with.

    ``known_level`` drops tokens whose level this scale does not recognise, and
    it is the **opposite** of the unknown-ranks-highest rule the rank functions
    use — deliberately, because the two answer different questions.

    * For the level of the occurrence happening *now*, unknown must rank high,
      so a state this build does not understand is **shown** rather than
      swallowed by an existing silence.
    * For a level read back from *storage*, ranking unknown high produces the
      broadest possible silence from a corrupt, foreign or cross-vocabulary
      token — fail-open. Measured: `state_rank("observed")` is 4 (it is an
      *evidence* word, unknown to the pill scale) against `state_rank("crit")`
      of 3, so one stray token would mute a critical condition permanently, and
      `prune` would keep it forever because its key is live.

    Both directions are "fail loud". Omitting ``known_level`` keeps the old
    permissive behaviour, so every caller that can be cross-contaminated passes
    one.
    """
    index: dict[tuple[str, str], int] = {}
    for token in stored:
        occ = parse_token(token)
        if occ is None:
            continue
        if known_level is not None and not known_level(occ.level):
            continue
        rank = rank_of(occ.level)
        ident = (occ.key, occ.fingerprint)
        if rank > index.get(ident, -1):
            index[ident] = rank
    return index


def is_silenced(index: dict[tuple[str, str], int], occ: Occurrence, rank: int) -> bool:
    """Does a stored silence still cover this occurrence?

    Two conditions, and both matter:

    * the fingerprint must **match exactly** — a condition whose evidence
      changed is a new occurrence and speaks again;
    * the rank must not have **escalated** past where it was silenced — which
      is what lets a silence survive things improving.
    """
    return rank <= index.get((occ.key, occ.fingerprint), -1)


def clear_key(stored: Iterable[str], key: str) -> list[str]:
    """Every stored token except those silencing ``key``, at any occurrence.

    Un-silencing has to remove *all* of a key's tokens, not the one whose
    fingerprint and level match right now. Under occurrence matching the two
    differ, and removing only the current one would leave an older, broader
    silence standing while the UI reported the item as un-silenced (DEC-358
    found the same trap one dimension down).
    """
    out: list[str] = []
    for token in stored:
        occ = parse_token(token)
        if occ is None or occ.key != key:
            out.append(token)
    return out


def prune(stored: Iterable[str], live_keys: set[str], cap: int = SILENCE_CAP) -> list[str]:
    """Drop silences for keys this machine can no longer produce, then cap.

    ``live_keys`` is what the current hardware can actually raise. A silence for
    anything else is dead weight: it can never match, and it inflates the
    Settings restore counter — the one place the user can see this list and the
    one place a wrong number misleads them.

    Order is preserved and the cap keeps the **most recent** entries, because
    appends are chronological and the newest silence is the one the user is most
    likely to still want. Unparseable tokens are dropped: they can never match
    either, and keeping them would let a corrupt settings file grow forever.
    """
    kept = [t for t in stored if (occ := parse_token(t)) is not None and occ.key in live_keys]
    return kept[-cap:] if len(kept) > cap else kept
