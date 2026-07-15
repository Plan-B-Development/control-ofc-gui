"""ToggleSwitch — an iOS-style on/off switch (DEC-215).

A ``QCheckBox`` subclass that owner-draws an iOS-style track + sliding knob instead
of the default checkbox indicator, matching the settings mockup. Being a QCheckBox
subclass, it keeps the entire checkbox API — ``isChecked()`` / ``setChecked()`` /
``toggled`` and ``findChild(QCheckBox, …)`` — so existing settings widgets and their
tests keep working unchanged. Colours are read from ``active_theme()`` at paint time
(no hardcoded hex — ``components/`` is scanned by the no-hardcoded-colours test), so
the switch also tracks live theme changes for free (DEC-109 pattern).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QWidget

from control_ofc.ui.theme import active_theme

_TRACK_W = 36
_TRACK_H = 20
_MARGIN = 2  # inset of the knob from the track edge
_KNOB_D = _TRACK_H - 2 * _MARGIN  # 16


class ToggleSwitch(QCheckBox):
    """A themed iOS-style toggle. Drop-in for a boolean ``QCheckBox``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The row's label/sublabel are separate QLabels (mockup), so the switch
        # itself carries no text — the whole 36x20 area is the hit target.
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(_TRACK_W, _TRACK_H)

    def hitButton(self, pos) -> bool:
        # The entire painted area toggles (there is no separate indicator box).
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        theme = active_theme()
        checked = self.isChecked()
        enabled = self.isEnabled()

        if checked:
            track = QColor(theme.accent_primary)
            track.setAlpha(51)  # ~0.2 tint (matches the mockup track)
            border = QColor(theme.accent_primary)
            knob = QColor(theme.accent_primary)
            knob_x = self.width() - _KNOB_D - _MARGIN
        else:
            track = QColor(theme.surface_1)
            border = QColor(theme.border_default)
            knob = QColor(theme.text_muted)
            knob_x = _MARGIN

        if not enabled:
            for c in (track, border, knob):
                c.setAlpha(c.alpha() // 2)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Track (pill).
        painter.setPen(border)
        painter.setBrush(track)
        radius = _TRACK_H / 2
        painter.drawRoundedRect(0, 0, _TRACK_W, _TRACK_H, radius, radius)
        # Knob.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(knob_x, _MARGIN, _KNOB_D, _KNOB_D)
        painter.end()
