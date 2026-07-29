"""Qt utility helpers shared across UI modules."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QSplitter, QWidget

# Shared resize-handle width, in px (DEC-234). This is the grab zone, wider than
# the ~2px hairline the QSplitter::handle QSS paints inside it — comfortable to
# hit without a chunky gripper bar.
SPLITTER_HANDLE_WIDTH = 8


@contextmanager
def block_signals(widget: QObject) -> Iterator[None]:
    """Temporarily block signals on *widget*, restoring even if an exception occurs."""
    widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(False)


def repolish(widget: QWidget) -> None:
    """Re-run the style engine on *widget* after a dynamic property change.

    Qt only re-evaluates QSS rules on an explicit unpolish/polish cycle, so a
    changed ``class`` (or other dynamic property) leaves stale styling without
    this.
    """
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_chip_class(widget: QWidget, css_class: str, *, skip_if_unchanged: bool = False) -> None:
    """Set *widget*'s dynamic ``class`` property and repolish so QSS re-applies.

    ``skip_if_unchanged`` short-circuits when the class already matches, avoiding
    a redundant repolish (which can, e.g., dismiss an open popup).
    """
    if skip_if_unchanged and widget.property("class") == css_class:
        return
    widget.setProperty("class", css_class)
    repolish(widget)


def style_splitter(splitter: QSplitter) -> None:
    """Apply the shared resize-handle convention to *splitter* (DEC-234).

    Every ``QSplitter`` in the app is run through this so handles look and grab
    the same on every page. It sets a comfortable grab width; the visible
    hairline and accent-on-hover come from the ``QSplitter::handle`` rules in
    ``theme.build_stylesheet``. Before DEC-234 handles used Qt's near-invisible
    default at each page's whim — the inconsistency this normalises.
    """
    splitter.setHandleWidth(SPLITTER_HANDLE_WIDTH)
