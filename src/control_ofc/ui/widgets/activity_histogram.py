"""Activity-over-time strip for the Logs page (DEC-314, brief §3).

A compact, statically painted summary of event volume across the retained feed, with
severity stacked inside each column and the selected time window picked out. Clicking
(or arrowing onto) a column filters the event list to that slice.

**Statically painted, and that is a rule rather than an implementation detail.** The
project's paint policy is stated canonically in ``RadialGauge``'s docstring: no
``QTimer``, no ``QPropertyAnimation``, no ``QGraphicsEffect``, zero ongoing cost when
nothing changes. Brief §3 says the same thing from the other side — "do not animate
every incoming log event". This widget repaints when its buckets change and at no other
time.

Bucket count is **derived**, never assumed: :meth:`preferred_bucket_count` divides the
live widget width by a minimum readable column pitch, so the strip has more resolution
on a wide window and stays legible on a narrow one. The prototype's column count is not
reproduced (brief §3 is explicit that it is illustrative).
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from control_ofc.services.logs_view import LEVEL_ORDER, HistogramBucket, level_state
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.log_row_delegate import severity_color

# Minimum pitch of one column, in device-independent pixels. Below this the bars stop
# being separable by eye and the strip becomes noise. A marker dimension, not page
# composition (brief §13) — it bounds resolution, it does not position anything.
_MIN_BUCKET_PX = 6
# Widest a column may be. This is the LOWER bound on the count, and it is the half that
# matters for a sparse feed: bounding only the count let one event own a 148px-wide
# full-height block that read as a rendering glitch rather than a bar. Bounding the
# width instead gives the normal shape — a narrow bar in a mostly-empty strip — which
# is what a histogram of one event honestly looks like.
_MAX_BUCKET_PX = 24
# How many columns each event may claim, between those two bounds. Sparse feeds sit on
# the width floor; as the feed fills, this pulls the count up until _MIN_BUCKET_PX caps
# it and the strip is back at full resolution.
_BUCKETS_PER_EVENT = 2
# Gap between adjacent columns, and the floor height of a non-empty column so a single
# event is still visible rather than rounding away to nothing.
_BUCKET_GAP = 1
_MIN_BAR_PX = 2
# Strip height as a multiple of the line height — a font metric, so the strip grows
# with the user's font and DPI instead of being pinned to the prototype's pixels.
_HEIGHT_LINES = 3.0


class ActivityHistogram(QWidget):
    """Stacked severity columns over the retained time span."""

    #: A column was activated (click or keyboard). ``-1`` means "clear the selection".
    bucket_clicked = Signal(int)
    #: The width changed enough to change how many columns fit, so the page should
    #: re-bucket. Emitted on the *capacity*, not on every resize event, so dragging a
    #: window edge does not re-derive the whole view on each pixel.
    capacity_changed = Signal()

    def __init__(self, parent: QWidget | None = None, *, object_name: str | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self._buckets: list[HistogramBucket] = []
        self._selected: int | None = None
        self._cursor = 0
        self._capacity = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Activity over time — select a column to filter by time")

    # ── State ────────────────────────────────────────────────────────

    def set_buckets(self, buckets: list[HistogramBucket]) -> None:
        self._buckets = list(buckets)
        if self._selected is not None and self._selected >= len(self._buckets):
            self._selected = None
        self._cursor = min(self._cursor, max(0, len(self._buckets) - 1))
        self.update()

    def buckets(self) -> list[HistogramBucket]:
        return list(self._buckets)

    def set_selected_index(self, index: int | None) -> None:
        self._selected = index if index is not None and 0 <= index < len(self._buckets) else None
        if self._selected is not None:
            self._cursor = self._selected
        self.update()

    def selected_index(self) -> int | None:
        return self._selected

    def preferred_bucket_count(self, row_count: int | None = None) -> int:
        """How many columns to divide the span into (brief §3).

        Brief §3 asks for the count to come from the available width, the retained
        span **and** a minimum bucket width. The width half alone produced a 229-column
        strip for a one-event feed; bounding the *count* alone then over-corrected to
        eight 148px slabs. So both bounds are on the column **width**: never narrower
        than :data:`_MIN_BUCKET_PX` (the legibility cap on the count) and never wider
        than :data:`_MAX_BUCKET_PX` (the floor under it). Between them the count tracks
        the data.

        The bound lives here rather than at the call site because legibility is the
        widget's business — the page knows how much data it has, not how wide a column
        has to be to be seen. Called with no argument (the resize path) it answers the
        pure width question, which is what a capacity check wants.
        """
        capacity = max(1, self.width() // _MIN_BUCKET_PX)
        if row_count is None:
            return capacity
        if row_count <= 0:
            return 0
        floor = max(1, self.width() // _MAX_BUCKET_PX)
        return max(1, min(capacity, max(floor, row_count * _BUCKETS_PER_EVENT)))

    # ── Geometry ─────────────────────────────────────────────────────

    def minimumSizeHint(self) -> QSize:
        """Height from the font metric — never a literal (see the DEC-281/303 traps).

        ``minimumSizeHint`` rather than ``setMinimumHeight``: the latter *overrides*
        the hint instead of raising a floor under it, which is how DEC-281 capped a
        pane 300 px below its own content.
        """
        return QSize(_MIN_BUCKET_PX * 8, int(QFontMetrics(self.font()).height() * _HEIGHT_LINES))

    def sizeHint(self) -> QSize:
        return self.minimumSizeHint()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        capacity = self.preferred_bucket_count()
        if capacity != self._capacity:
            self._capacity = capacity
            self.capacity_changed.emit()

    def _bucket_at(self, x: int) -> int:
        if not self._buckets or self.width() <= 0:
            return -1
        idx = int(x * len(self._buckets) / self.width())
        return max(0, min(len(self._buckets) - 1, idx))

    # ── Interaction ──────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        idx = self._bucket_at(int(event.position().x()))
        if idx < 0:
            return
        self._cursor = idx
        # Clicking the selected column again clears the window, so the filter can be
        # undone with the control that set it as well as with the explicit button.
        self.bucket_clicked.emit(-1 if idx == self._selected else idx)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        idx = self._bucket_at(int(event.position().x()))
        self.setToolTip(self._describe(idx) if idx >= 0 else "")
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._buckets:
            step = -1 if key == Qt.Key.Key_Left else 1
            self._cursor = max(0, min(len(self._buckets) - 1, self._cursor + step))
            self.update()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._buckets:
            self.bucket_clicked.emit(-1 if self._cursor == self._selected else self._cursor)
            return
        if key == Qt.Key.Key_Escape and self._selected is not None:
            self.bucket_clicked.emit(-1)
            return
        super().keyPressEvent(event)

    def _describe(self, index: int) -> str:
        if not (0 <= index < len(self._buckets)):
            return ""
        b = self._buckets[index]
        span = time.strftime("%H:%M:%S", time.localtime(b.start))
        if b.total == 0:
            return f"{span} — no events"
        detail = ", ".join(f"{n} {lv}" for lv, n in b.counts.items() if n)
        noun = "event" if b.total == 1 else "events"
        return f"{span} — {b.total} {noun} ({detail})"

    # ── Paint ────────────────────────────────────────────────────────

    def paintEvent(self, _event: QPaintEvent) -> None:
        theme = active_theme()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.chart_bg))

        if not self._buckets:
            painter.setPen(QColor(theme.text_muted))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No activity in the retained feed"
            )
            painter.end()
            return

        n = len(self._buckets)
        peak = max((b.total for b in self._buckets), default=0)
        pitch = self.width() / n
        usable = max(1, self.height() - 2)

        for i, bucket in enumerate(self._buckets):
            left = int(i * pitch)
            width = max(1, int((i + 1) * pitch) - left - _BUCKET_GAP)

            if i == self._selected:
                painter.fillRect(
                    QRect(left, 0, width + _BUCKET_GAP, self.height()), QColor(theme.selected_bg)
                )

            if peak <= 0 or bucket.total == 0:
                continue
            # Stack severities bottom-up in LEVEL_ORDER so the most serious sits on
            # top, where it is visible even when the column is mostly INFO.
            y = self.height() - 1
            for level in LEVEL_ORDER:
                count = bucket.counts.get(level, 0)
                if count <= 0:
                    continue
                h = max(_MIN_BAR_PX, round(count / peak * usable))
                painter.fillRect(
                    QRect(left, max(0, y - h), width, min(h, y)),
                    QColor(severity_color(theme, level_state(level))),
                )
                y -= h

        if self.hasFocus():
            # DEC-251: the strip is keyboard-operable, so it needs a visible focus
            # indicator, and the cursor column needs to be distinguishable from the
            # selected one.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme.text_primary))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            cur_left = int(self._cursor * pitch)
            cur_w = max(1, int((self._cursor + 1) * pitch) - cur_left - _BUCKET_GAP)
            painter.drawRect(QRect(cur_left, 0, cur_w, self.height() - 1))
        painter.end()
