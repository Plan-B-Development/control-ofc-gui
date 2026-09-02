"""Painted two-line row for the Logs event list (DEC-314).

Brief §4 asks for a dense, scannable row carrying a severity marker, one primary
message line, one subdued metadata line and a collapsed repeat count. A
``QTableWidget`` row of four text cells cannot express that, and the previous page's
answer — a real ``StatusPill`` **child widget per row** — put a live widget tree behind
every visible line, which is what brief §11 rules out.

A delegate paints only the rows on screen and owns no widgets at all.

**Every colour and every font size here comes from the theme** (``active_theme()`` read
at paint time, so a live theme change is picked up on the next repaint with no
plumbing — the ``RadialGauge``/``CardResizeGrip`` convention). The handful of pixel
constants below are the "one-off dimension… keep it local, explain why" case brief §13
allows: each is a marker or a gap, not page composition, and none of them is a font
metric — the row's height is derived from ``QFontMetrics`` so it tracks the user's font
size and DPI.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from control_ofc.services.logs_view import REPEAT_MARK, LogRowVM
from control_ofc.ui.theme import ThemeTokens, active_theme, font_sizes
from control_ofc.ui.widgets.log_event_model import ROW_ROLE

# Width of the severity edge. A marker, not a layout dimension: it reads as a colour
# stripe at any DPI and deliberately does not scale with the font.
_EDGE_W = 3
# Gap between the edge and the text block, and the block's inset from the row's top and
# bottom. Small fixed gutters; the row's *height* is a font metric (see sizeHint).
_PAD_X = 8
_PAD_Y = 5
# Inner padding of the repeat badge, and its corner radius.
_BADGE_PAD_X = 6
_BADGE_RADIUS = 3


def severity_color(theme: ThemeTokens, level_state: str) -> str:
    """The semantic status token for a row's severity.

    One mapping, shared by the row edge, the message text and the activity histogram,
    so the same event is never green in one widget and blue in another. The states are
    the ``StatusPill`` vocabulary the rest of the app already uses (``logs_view``'s
    ``_LEVEL_STATE``), which is why info is the OK colour rather than the info colour:
    the user has already learned that pill.
    """
    if level_state == "crit":
        return theme.status_crit
    if level_state == "warn":
        return theme.status_warn
    return theme.status_ok


def message_color(theme: ThemeTokens, level_state: str) -> str:
    """Message-text colour: severity-tinted for warnings and errors, plain otherwise.

    Preserved from the pre-redesign table deliberately. The severity edge now carries
    the signal on its own, but dropping the tint would make an ERR line *less* visible
    than it is today, and brief §15 is explicit that a visual redesign is not
    permission to regress existing behaviour.
    """
    if level_state in ("crit", "warn"):
        return severity_color(theme, level_state)
    return theme.text_primary


class LogRowDelegate(QStyledItemDelegate):
    """Paints one :class:`LogRowVM` as severity edge + message line + meta line."""

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        """(message, meta) fonts, both derived from the theme's size multipliers."""
        sizes = font_sizes(active_theme().base_font_size_pt)
        msg = QFont(option.font)
        msg.setPointSize(sizes["body"])
        meta = QFont(option.font)
        meta.setPointSize(sizes["small"])
        return msg, meta

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        msg_font, meta_font = self._fonts(opt)
        height = QFontMetrics(msg_font).height() + QFontMetrics(meta_font).height() + 2 * _PAD_Y
        return QSize(opt.rect.width(), height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(ROW_ROLE)
        if not isinstance(row, LogRowVM):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        theme = active_theme()
        rect = opt.rect
        state = opt.state

        painter.save()
        painter.setClipRect(rect)

        # ── Background: selected wins over hover; unselected rows stay on the
        # surface behind them so the list reads as one continuous body.
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(theme.selected_bg))
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(theme.hover_bg))

        # ── Severity edge.
        painter.fillRect(
            QRect(rect.left(), rect.top(), _EDGE_W, rect.height()),
            QColor(severity_color(theme, row.level_state)),
        )

        msg_font, meta_font = self._fonts(opt)
        msg_fm, meta_fm = QFontMetrics(msg_font), QFontMetrics(meta_font)
        text_left = rect.left() + _EDGE_W + _PAD_X
        text_right = rect.right() - _PAD_X

        # ── Repeat badge, drawn first so the message can be elided around it.
        if row.repeat_count > 1:
            badge = f"{REPEAT_MARK}{row.repeat_count}"
            badge_w = meta_fm.horizontalAdvance(badge) + 2 * _BADGE_PAD_X
            badge_h = meta_fm.height()
            badge_rect = QRect(text_right - badge_w, rect.top() + _PAD_Y, badge_w, badge_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.surface_3))
            painter.drawRoundedRect(badge_rect, _BADGE_RADIUS, _BADGE_RADIUS)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setFont(meta_font)
            painter.setPen(QColor(theme.text_secondary))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)
            text_right -= badge_w + _PAD_X

        # ── Message line.
        msg_w = max(0, text_right - text_left)
        painter.setFont(msg_font)
        painter.setPen(QColor(message_color(theme, row.level_state)))
        painter.drawText(
            QRect(text_left, rect.top() + _PAD_Y, msg_w, msg_fm.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            msg_fm.elidedText(row.message, Qt.TextElideMode.ElideRight, msg_w),
        )

        # ── Meta line: quieter, and the full width (the badge sits on line one).
        meta_w = max(0, rect.right() - _PAD_X - text_left)
        painter.setFont(meta_font)
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            QRect(text_left, rect.top() + _PAD_Y + msg_fm.height(), meta_w, meta_fm.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            meta_fm.elidedText(meta_line(row), Qt.TextElideMode.ElideRight, meta_w),
        )

        # ── Keyboard focus ring (DEC-251). A stylesheet suppresses Qt's native focus
        # rect, so without this the keyboard user has no idea which row is current.
        # Drawn inside the row rect so it never changes the row's size.
        if state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme.text_primary))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

        painter.restore()


def meta_line(row: LogRowVM) -> str:
    """The subdued second line: time, source, and the correlation key when there is one.

    ``component`` is the only field promoted onto the row. It is the one the inspector
    correlates on, so surfacing it is what lets the user see *why* two rows are
    related without opening either. Everything else stays in the Details tab.
    """
    parts = [row.time_str, row.source or "—"]
    if row.component:
        parts.append(row.component)
    return "  ·  ".join(parts)
