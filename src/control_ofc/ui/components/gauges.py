"""Static radial (donut) gauge — custom-paint, never animated (DEC-211).

Qt has no gauge primitive, so this is a small custom-paint widget beside the
other custom-paint primitive (``PulsingLed`` in ``glow.py``). It is **static by
design**: the only repaint triggers are ``set_value`` (a data refresh), a theme
change (the owning page re-renders), a resize, and an expose. There is NO
``QTimer`` / ``QPropertyAnimation`` / ``QGraphicsEffect``, so it adds zero ongoing
CPU/GPU load — the hard performance rule (master.txt): drawing must not degrade
the host during graphics-intensive tasks (gaming) while the app is open.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from control_ofc.ui.theme import active_theme

# Semantic state → ThemeTokens colour attribute for the progress arc.
_STATE_COLOR_ATTR: dict[str, str] = {
    "ok": "status_ok",
    "warn": "status_warn",
    "crit": "status_crit",
}


class RadialGauge(QWidget):
    """A donut gauge: a themed ring filled ``fraction`` of the way round, with a
    centred value and a small caption below it.

    ``set_value(fraction, center_text=…, caption=…, state=…)`` is the sole
    content API; ``state`` (``ok`` / ``warn`` / ``crit`` / anything else →
    muted) selects the progress-arc colour. Colours are read from
    :func:`active_theme` at paint time so a theme switch is picked up on the next
    repaint without the widget holding a token snapshot.
    """

    def __init__(self, object_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self._fraction = 0.0
        self._center_text = ""
        self._caption = ""
        self._state = "neutral"
        self.setMinimumSize(140, 140)

    def set_value(
        self,
        fraction: float,
        *,
        center_text: str,
        caption: str = "",
        state: str = "neutral",
    ) -> None:
        """Set the ring fill (clamped 0..1), the centred value, the caption, and
        the semantic ``state``. Triggers the single repaint."""
        self._fraction = max(0.0, min(1.0, fraction))
        self._center_text = center_text
        self._caption = caption
        self._state = state
        self.update()

    def fraction(self) -> float:
        return self._fraction

    def state(self) -> str:
        return self._state

    def _progress_color(self) -> QColor:
        theme = active_theme()
        attr = _STATE_COLOR_ATTR.get(self._state)
        return QColor(getattr(theme, attr) if attr else theme.text_muted)

    def paintEvent(self, event) -> None:
        theme = active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen_width = 8
        side = min(self.width(), self.height()) - pen_width
        if side <= 0:
            painter.end()
            return
        left = (self.width() - side) / 2
        top = (self.height() - side) / 2
        rect = QRectF(left, top, side, side)

        # Full track ring.
        track_pen = QPen(QColor(theme.border_default), pen_width)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # Progress arc — start at 12 o'clock (90°), sweep clockwise (negative).
        if self._fraction > 0:
            prog_pen = QPen(self._progress_color(), pen_width)
            prog_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(prog_pen)
            painter.drawArc(rect, 90 * 16, -int(360 * 16 * self._fraction))

        # Centred value.
        value_font = painter.font()
        value_font.setPointSizeF(max(value_font.pointSizeF(), 1.0) * 1.9)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(QColor(theme.text_primary))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._center_text)

        # Caption below the value.
        if self._caption:
            caption_font = painter.font()
            caption_font.setPointSizeF(max(self.font().pointSizeF(), 1.0) * 0.72)
            caption_font.setBold(False)
            painter.setFont(caption_font)
            painter.setPen(QColor(theme.text_secondary))
            caption_rect = QRectF(
                rect.left(), rect.center().y() + side * 0.15, rect.width(), side * 0.28
            )
            painter.drawText(
                caption_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._caption,
            )

        painter.end()
