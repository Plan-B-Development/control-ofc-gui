"""Summary card widget — a compact card showing a label and value."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout


class SummaryCard(QFrame):
    """A small card displaying a title and prominent value, used in dashboard row 1.

    When ``category`` is set, the card is clickable and emits ``clicked(category)``.
    Height is driven by font metrics (Maximum vertical policy) so the card
    stays compact at any theme text size without hardcoded pixel heights.
    """

    clicked = Signal(str)  # emits the card's category string

    def __init__(self, title: str, value: str = "\u2014", category: str = "", parent=None) -> None:
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

        self._value_label = QLabel(value)
        self._value_label.setProperty("class", "CardValue")
        self._value_label.setStyleSheet("background: transparent;")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._value_label)

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

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
