"""RPM sparkline — a static owner-drawn recent-RPM mini-plot (DEC-213).

A cheap custom-paint sparkline for the dashboard fan cards. It paints a single
``QPainterPath`` from a page-injected list of recent RPM points and repaints ONLY
on ``set_points`` / ``set_theme`` — no ``QTimer``, no pyqtgraph, no signals. This
is the pattern (mirroring ``curve_card.CurvePreview``) that keeps the master-brief
performance rule: never a per-card live plot; zero ``paintEvent`` when the
dashboard page is hidden; fed by the existing 1 Hz poll. The constant ``sizeHint``
prevents any render→hint→grow ratchet (DEC-129).
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from control_ofc.ui.theme import active_theme

_SPARK_HEIGHT = 32


class RpmSparkline(QWidget):
    """A static recent-RPM sparkline (custom-paint; no timer / pyqtgraph).

    Reads ``active_theme()`` at paint time (like ``RadialGauge``), so a theme
    switch is picked up on the next data-driven repaint (~1 s) without threading
    ``set_theme`` through the whole fan grid.
    """

    def __init__(self, object_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self._points: tuple[float, ...] = ()
        self._warn = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(_SPARK_HEIGHT)

    def set_points(self, points, *, warn: bool = False) -> None:
        """Set the recent-RPM points + warn flag. Skips a redundant repaint when
        nothing changed (the flat-RPM guard — keeps steady-state cost at zero)."""
        pts = tuple(float(p) for p in points)
        if pts == self._points and warn == self._warn:
            return
        self._points = pts
        self._warn = warn
        self.update()

    def points(self) -> tuple[float, ...]:
        return self._points

    def sizeHint(self) -> QSize:
        # Constant — never derived from the painted data (no ratchet, DEC-129).
        return QSize(80, _SPARK_HEIGHT)

    def paintEvent(self, event) -> None:
        if len(self._points) < 2:
            return
        theme = active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        pad = 3
        lo = min(self._points)
        hi = max(self._points)
        span = max(hi - lo, 1.0)
        n = len(self._points)
        color = QColor(theme.status_warn if self._warn else theme.accent_primary)

        path = QPainterPath()
        xs: list[float] = []
        for i, value in enumerate(self._points):
            x = (i / (n - 1)) * (w - 2 * pad) + pad
            y = h - pad - ((value - lo) / span) * (h - 2 * pad)
            xs.append(x)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # Light area fill under the line (the mockup look), then the line itself.
        fill = QPainterPath(path)
        fill.lineTo(xs[-1], h - pad)
        fill.lineTo(xs[0], h - pad)
        fill.closeSubpath()
        fill_color = QColor(color)
        fill_color.setAlpha(38)
        painter.fillPath(fill, fill_color)

        painter.setPen(QPen(color, 1.5))
        painter.drawPath(path)
        painter.end()
