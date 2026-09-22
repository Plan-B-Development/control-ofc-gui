"""How long each daemon diagnostic takes, for the labels that tell the user
before they start one (DEC-404 S4-9, default 3).

**One place, two consumers.** The validation-session dialog and the PWM Test
Report both label diagnostics with a duration, and the figures are derived from
the daemon's own timing constants. They lived as prose-plus-literals inside the
session dialog (`P8-az`, DEC-405's recomputation); a second copy in the report
would drift the first time the daemon moved a constant, which is exactly what
DEC-405's 6 s → 12 s settle change did to the dialog's labels.

**These are mirrors of daemon constants, not a contract.** None of them is on
the wire, so each is a second copy of a daemon value and will drift if the
daemon moves one — which is why every label built from them says "about", and
why the mirror names match the daemon's so a grep finds both halves. The
daemon source each one mirrors is named beside it.
"""

from __future__ import annotations

import math

# ── Daemon constants mirrored here (daemon `daemon/src/constants.rs`) ─────────

#: ``VERIFY_WAIT_SECONDS`` (6 s) plus the write, readback and restore around it.
VERIFY_SECONDS = 10
#: ``CHARACTERIZATION_DEFAULT_SETTLE_S`` — 12 s since DEC-405.
CHARACTERIZATION_DEFAULT_SETTLE_S = 12
#: ``len(CHARACTERIZATION_DEFAULT_POINTS)`` — 30 … 100 % in steps of 10.
CHARACTERIZATION_DEFAULT_POINT_COUNT = 8
#: ``STABILITY_DEFAULT_S`` and ``STABILITY_MAX_POINTS``: the dwell, and how many
#: daemon-chosen duties get it.
STABILITY_DEFAULT_S = 20
STABILITY_MAX_POINTS = 3
#: ``DISCOVERY_DEFAULT_CYCLES``; each cycle holds a baseline and a perturbed
#: window of the shared settle length.
DISCOVERY_DEFAULT_CYCLES = 2
#: ``DISCOVERY_SETTLE_WAIT_MAX`` (= ``CHARACTERIZATION_SETTLE_MAX_S``).
DISCOVERY_SETTLE_WAIT_MAX_S = 15
#: DEC-407. The probe's own 20 % baseline hold is bounded by the settle maximum;
#: its time below 20 % by ``STALL_PROBE_BUDGET_CAP``; the recovery kick by
#: ``STALL_PROBE_KICK_MAX``.
STALL_PROBE_BASELINE_MAX_S = 15
STALL_PROBE_BUDGET_CAP_S = 180
STALL_PROBE_KICK_MAX_S = 15


def characterization_seconds(unique_points: int, *, bidirectional: bool, stability: bool) -> int:
    """One sweep, per header: every walked step held for the default settle.

    A bidirectional walk visits every duty but the top one twice
    (``2n - 1`` steps); the stability dwell adds its hold at up to three duties.
    """
    steps = (2 * unique_points - 1) if bidirectional else unique_points
    total = steps * CHARACTERIZATION_DEFAULT_SETTLE_S
    if stability:
        total += STABILITY_MAX_POINTS * STABILITY_DEFAULT_S
    return total


def discovery_seconds(*, worst_case: bool = False) -> int:
    """One control-path discovery, per header.

    Two windows per cycle, plus a bounded settle-wait before a baseline whose
    write moved the duty — one in a run that starts at the header's own duty,
    two when the header was found below the discovery floor.
    """
    windows = DISCOVERY_DEFAULT_CYCLES * 2 * CHARACTERIZATION_DEFAULT_SETTLE_S
    waits = 2 if worst_case else 1
    return windows + waits * DISCOVERY_SETTLE_WAIT_MAX_S


def stall_probe_max_seconds() -> int:
    """The longest one probe can run: baseline hold + budget cap + kick."""
    return STALL_PROBE_BASELINE_MAX_S + STALL_PROBE_BUDGET_CAP_S + STALL_PROBE_KICK_MAX_S


_FRACTIONS = {0: "", 1: "¼", 2: "½", 3: "¾"}


def duration_words(seconds: int) -> str:
    """``10 s`` under a minute, else minutes to the nearest quarter (``1½ min``).

    Quarter minutes because the figures are estimates from mirrored constants:
    anything finer would imply a precision nothing here has.
    """
    if seconds < 60:
        return f"{max(1, seconds)} s"
    quarters = math.floor(seconds / 15 + 0.5)
    whole, frac = divmod(quarters, 4)
    if whole == 0:
        return f"{_FRACTIONS[frac]} min"
    return f"{whole}{_FRACTIONS[frac]} min"


def duration_text(seconds: int) -> str:
    """:func:`duration_words` with the approximation marked: ``~1½ min``."""
    return f"~{duration_words(seconds)}"
