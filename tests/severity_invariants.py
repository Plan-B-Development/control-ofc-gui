"""Advisory-severity vocabulary predicates — test-layer only (row `SSN-k`).

These two lived in ``control_ofc.ui.hwmon_guidance`` until DEC-366. Both lost
their last production caller in DEC-357, which moved the decision "must the user
act on this?" off the advisory's *tier* and onto its observed
:attr:`~control_ofc.ui.hwmon_guidance.VendorQuirk.consequence` — a quirk matches
on board identity, so ranking off the tier escalated a healthy board.

They are kept, and kept **here**, because the rule they encode is still worth
pinning and is a statement about the shipped
:data:`~control_ofc.ui.hwmon_guidance._SEVERITY_DISPLAY` table rather than about
any caller:

* nothing the advisory panel paints as INFO may be counted as a problem by the
  card (the DOC-g guard — one advisory must not read INFO on one surface and
  WARN on another);
* only CRITICAL and HIGH sit at or above the tier that used to escalate.

Both resolve their reference rank through :func:`severity_display` at **call**
time, exactly as the originals did (``_INFO_DISPLAY.rank`` /
``_SEVERITY_DISPLAY["high"].rank``). Nothing in the suite patches that table
today — measured — but capturing the ranks at import would be a behavioural
difference in a change whose whole claim is that it moves these without altering
them.

Living in ``tests/`` is the enforcement: a predicate no production module can
import cannot acquire the consumer DEC-357 removed. `CLAUDE.md § Hard-won
lessons` — a discriminator read only by tests is decoration when it sits in
production code; as a test-owned invariant over production *data*, it is a test.
"""

from __future__ import annotations

from control_ofc.ui.hwmon_guidance import severity_display


def is_actionable_severity(severity: str) -> bool:
    """Does this advisory severity represent a problem the user should act on?

    Ranked off :func:`severity_display`, never off a string compare against
    ``"info"``. The two are not equivalent: an unrecognised severity degrades to
    the calm INFO *presentation* but is not the literal string ``"info"``, so a
    ``severity != "info"`` test classified it as actionable and the aggregated
    problem card rendered it WARN while the inline panel rendered it INFO — the
    same advisory reading at two different levels depending on the surface.
    Ranking keeps every surface agreeing about an unknown tier.
    """
    return severity_display(severity).rank > severity_display("info").rank


def is_high_severity(severity: str) -> bool:
    """Is this severity at or above the HIGH tier?

    **No production path may call this (DEC-357), which is why it is not
    importable from one.** Its last caller was the ``vendor_quirk`` rollup that
    change deleted, which made any HIGH-or-above advisory paint the System State
    health card CRITICAL — on a machine where nothing had been observed at all.
    The tier is a true statement about the advisory; it is not evidence about
    the machine.
    """
    return severity_display(severity).rank >= severity_display("high").rank
