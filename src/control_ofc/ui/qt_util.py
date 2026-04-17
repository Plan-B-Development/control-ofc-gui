"""Qt utility helpers shared across UI modules."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtCore import QObject


@contextmanager
def block_signals(widget: QObject) -> Iterator[None]:
    """Temporarily block signals on *widget*, restoring even if an exception occurs."""
    widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(False)
