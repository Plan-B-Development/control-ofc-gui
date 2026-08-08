"""Restoring persisted splitter pane sizes safely (DEC-245).

Qt-free so the rule is testable without a widget — the view-model half of the
house "view-model + thin renderer" pattern.

DEC-234 chose *not* to persist splitter positions; DEC-245 reverses that and
attaches this clamp as a condition of the reversal. The reason is DEC-222, not
aesthetics: that ADR removed the sensors rail's show/hide toggle in favour of the
splitter handle, stating there is "no hidden state and no width-based
auto-collapse". Restoring a fully-collapsed pane would put that hidden state back
on the next launch with no affordance left to undo it — a soft-lock.

**Be precise about how much of that this clamp actually does.** Measured against
the real panes in a laid-out window, every effective minimum
(``max(minimumSizeHint, minimum)`` along the split axis) is already 50 px or more —
the smallest is the Logs inspector at 50, most are 140-470. Qt raises any non-zero
``setSizes`` value to that minimum by itself, so ``MIN_PANE_PX`` almost never binds
and the widgets do the bulk of the anti-collapse work.

What the floor *does* guarantee is that ``setSizes`` is never handed a literal
``0``, which for a ``childrenCollapsible=True`` splitter (seven of the nine are)
Qt honours as a genuine collapse rather than clamping up. That is the soft-lock
path, and it is narrow — but it is the one that matters, and it is exactly the
case a saved layout can contain.

A corollary worth knowing: because Qt overrides clamped values upward, the
rescale-and-settle arithmetic below is approximate in practice. It sets the
intent; the widget minimums have the final say.
"""

from __future__ import annotations

# A restored pane is never narrower than this. Large enough that the pane and its
# handle are unmistakably grabbable, small enough not to fight a deliberate
# "almost closed" layout.
MIN_PANE_PX = 48


def clamp_restored_sizes(
    saved: list[int] | None,
    current: list[int],
    *,
    min_px: int = MIN_PANE_PX,
) -> list[int] | None:
    """Sizes to apply to a splitter, or ``None`` to leave it at its default.

    ``None`` is returned — rather than a best guess — whenever the saved value
    cannot be trusted to describe *this* splitter:

    * nothing saved;
    * a different number of panes than the widget now has (a release added or
      removed a pane, so the saved layout describes a different widget);
    * the widget has not been laid out yet (``sum(current) == 0``), where there
      is no total to distribute and Qt will size it itself momentarily.

    Otherwise every pane is raised to ``min_px`` and the result is rescaled to the
    total the widget actually has now, so a layout saved at one window size still
    applies at another.
    """
    if not saved or len(saved) != len(current):
        return None
    total = sum(current)
    if total <= 0:
        return None
    # A saved layout cannot be honoured at all if the window is now too small to
    # give every pane its minimum; let Qt distribute evenly instead of returning
    # sizes that would silently violate the floor.
    if total < min_px * len(saved):
        return None

    scale = total / sum(saved) if sum(saved) > 0 else 0
    scaled = [max(min_px, round(s * scale)) if scale else min_px for s in saved]

    # Rounding and the clamp both perturb the total; settle the difference on the
    # widest pane, which has the most room to give or take without hitting min_px.
    drift = total - sum(scaled)
    if drift:
        widest = max(range(len(scaled)), key=lambda i: scaled[i])
        if scaled[widest] + drift >= min_px:
            scaled[widest] += drift
    return scaled
