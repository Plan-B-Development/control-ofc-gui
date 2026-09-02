"""``QAbstractTableModel`` over the Logs view-model rows (DEC-314).

The Logs page used to drive a ``QTableWidget`` and maintain the visible rows by hand:
a bulk ``_rebuild_table`` for filter changes and a separate incremental ``_append_row``
for live events, the latter juggling deque eviction, cell-widget lifetime and a
``_suppress_selection`` flag whose absence was silent. Two code paths computing the same
view is a divergence waiting to happen, and a seeded 800-step fuzz test existed
precisely to prove they had not diverged yet.

This model has **one** path. ``set_rows`` takes the already-filtered, already-collapsed
list from ``services.logs_view`` and resets. That is affordable because the feed is
capped at ``MAX_EVENTS`` (200) — re-deriving 200 pure dataclasses costs microseconds —
and because a model reset repaints only the rows actually on screen rather than
rebuilding a widget tree, which is the property brief §11 asks for.

**A reset drops the view's selection**, so the page re-anchors it afterwards by
``event_id`` (:meth:`index_of_event`). That is not a workaround: brief §4 requires
selection to be keyed by stable identity rather than row position, and under
newest-first ordering the position of a selected row changes on every append anyway.

One column. The row is painted whole by ``LogRowDelegate``; splitting it into columns
would put layout decisions in the header instead of the delegate.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from control_ofc.services.logs_view import LogRowVM, format_row_line

# The whole view-model for a row, for the delegate. UserRole+1 rather than UserRole so
# a future second payload role does not have to renumber this one.
ROW_ROLE = Qt.ItemDataRole.UserRole + 1

_Index = QModelIndex | QPersistentModelIndex


class LogEventModel(QAbstractTableModel):
    """Rows of :class:`LogRowVM`, newest first, replaced wholesale by ``set_rows``."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[LogRowVM] = []

    # ── Qt model interface ───────────────────────────────────────────

    def rowCount(self, parent: _Index | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: _Index | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return 1

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            # The delegate paints the row itself and ignores this, but Qt's
            # accessibility bridge reads DisplayRole — so a screen reader announces
            # the whole log line rather than an empty cell.
            return format_row_line(row)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.message
        return None

    # ── Page interface ───────────────────────────────────────────────

    def set_rows(self, rows: list[LogRowVM]) -> None:
        """Replace every row. The caller re-anchors selection by ``event_id``."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> list[LogRowVM]:
        """A copy of the current rows — for copy-visible and for tests."""
        return list(self._rows)

    def row_at(self, position: int) -> LogRowVM | None:
        if 0 <= position < len(self._rows):
            return self._rows[position]
        return None

    def index_of_event(self, event_id: int) -> int:
        """Row position of ``event_id``, or ``-1`` when it is not currently visible."""
        for i, row in enumerate(self._rows):
            if row.event_id == event_id:
                return i
        return -1
