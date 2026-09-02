"""Shared realised-geometry helpers for layout tests.

Qt's layout minimum is not final at `show()`: the stylesheet polish that gives
buttons their padding lands over the following layout passes, so a window's
`minimumWidth()` **grows** for a pass or two afterwards. Measured on the main
window: 1336 immediately after `show()`, settling at 1398 on the third pass.

Reading realised geometry before that settles produces a value that is real but
premature — and a test that compares two such values (a page's realised width
against its own `minimumSizeHint()`) can pass while the layout is visibly
squeezed, because both figures are stale in the same direction. That is the
`AUD2-b` / DEC-314 family: assert the value the widget actually ends up with.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

#: Generous; the observed fixpoint is reached in 3.
_MAX_SETTLE_PASSES = 12


def settle_at_minimum(window: QWidget) -> int:
    """Resize `window` to its own minimum until that minimum stops growing.

    Returns the settled minimum width. Raises if it has not converged, because a
    minimum that never settles is a layout defect in its own right and silently
    measuring a moving target is how this trap keeps recurring.
    """
    previous = -1
    for _ in range(_MAX_SETTLE_PASSES):
        window.resize(window.minimumWidth(), window.minimumHeight())
        QApplication.processEvents()
        if window.minimumWidth() == previous:
            return previous
        previous = window.minimumWidth()
    raise AssertionError(
        f"the window's minimum width never settled: still moving at {previous}px "
        f"after {_MAX_SETTLE_PASSES} layout passes"
    )
