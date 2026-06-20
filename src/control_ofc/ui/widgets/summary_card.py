"""Summary card widget — a compact card showing a label, value, and optional min/max range."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

# Trend glyphs are text/shape (not colour-only) so direction is conveyed without
# relying on colour (WCAG 1.4.1). Empty string hides the glyph.
_TREND_GLYPHS: dict[str, str] = {"up": "↑", "down": "↓", "flat": "→", "": ""}


class SummaryCard(QFrame):
    """A small card displaying a title, prominent value, an optional trend glyph,
    and an optional session min/max range (or free-form detail line).  Used in
    dashboard row 1.

    When ``category`` is set, the card is clickable and emits ``clicked(category)``.
    Height is driven by font metrics (Maximum vertical policy) so the card
    stays compact at any theme text size without hardcoded pixel heights.
    """

    clicked = Signal(str)  # emits the card's category string

    def __init__(self, title: str, value: str = "—", category: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self.setMinimumWidth(140)
        # Height driven by content — Maximum policy prevents stretching beyond sizeHint
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._category = category

        if category:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        self._title_label = QLabel(title)
        self._title_label.setProperty("class", "PageSubtitle")
        self._title_label.setStyleSheet("background: transparent;")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._title_label)

        # Value + trend share a row so the trend glyph sits beside the value
        # without colliding with the freshness glyph (⚠/⏱) the dashboard appends
        # to the value text itself.
        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)

        self._value_label = QLabel(value)
        self._value_label.setProperty("class", "CardValue")
        self._value_label.setStyleSheet("background: transparent;")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        value_row.addWidget(self._value_label)

        self._trend_label = QLabel("")
        self._trend_label.setProperty("class", "CardValue")
        self._trend_label.setStyleSheet("background: transparent;")
        self._trend_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._trend_label.setHidden(True)
        value_row.addWidget(self._trend_label)
        value_row.addStretch()
        layout.addLayout(value_row)

        self._range_label = QLabel("")
        self._range_label.setProperty("class", "CardRange")
        self._range_label.setStyleSheet("background: transparent; opacity: 0.7;")
        self._range_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._range_label.setHidden(True)
        layout.addWidget(self._range_label)

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_trend(self, direction: str) -> None:
        """Show a small ↑/↓/→ trend glyph beside the value.  Pass "" to hide.

        ``direction`` ∈ {"up","down","flat",""}; unknown values hide the glyph.
        Text/shape glyph — never colour-only (WCAG 1.4.1)."""
        glyph = _TREND_GLYPHS.get(direction, "")
        if glyph:
            if self._trend_label.text() != glyph:
                self._trend_label.setText(glyph)
            if self._trend_label.isHidden():
                self._trend_label.setHidden(False)
        elif not self._trend_label.isHidden():
            self._trend_label.setHidden(True)
            self._trend_label.setText("")

    def set_range(self, min_c: float | None, max_c: float | None) -> None:
        """Show session min/max below the value.  Pass None to hide.

        Convenience wrapper over :meth:`set_detail_text` for temperature cards."""
        if min_c is not None and max_c is not None:
            self.set_detail_text(f"↓ {min_c:.1f}°  ↑ {max_c:.1f}°")
        else:
            self.set_detail_text("")

    def set_detail_text(self, text: str) -> None:
        """Show a free-form one-line detail below the value (empty hides it).

        Mutually exclusive with :meth:`set_range` per card — both drive the same
        secondary label, so a given card should use one or the other."""
        if text:
            if self._range_label.text() != text:
                self._range_label.setText(text)
            if self._range_label.isHidden():
                self._range_label.setHidden(False)
        elif not self._range_label.isHidden():
            self._range_label.setHidden(True)
            self._range_label.setText("")

    def set_status_class(self, css_class: str) -> None:
        """Set a semantic class like WarningChip, CriticalChip, SuccessChip."""
        if self._value_label.property("class") == css_class:
            return  # Avoid unnecessary repolish that can dismiss popups
        self._value_label.setProperty("class", css_class)
        self._value_label.style().unpolish(self._value_label)
        self._value_label.style().polish(self._value_label)

    def mousePressEvent(self, event) -> None:
        if self._category:
            self.clicked.emit(self._category)
        super().mousePressEvent(event)
