"""Cards + section header (DEC-208)."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


def _slug(text: str) -> str:
    return "".join(c for c in text.title() if c.isalnum()) or "Section"


class Card(QFrame):
    """A calm surface card (``.Card`` QSS)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")


class BracketCard(QFrame):
    """A card with a left accent bar that intensifies on hover (``.BracketCard``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "BracketCard")
        self.setProperty("warning", "false")


class SectionHeader(QWidget):
    """An accent bar + uppercase heading-font title (the mockup section header)."""

    def __init__(
        self, title: str, parent: QWidget | None = None, *, object_name: str | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name or f"SectionHeader_{_slug(title)}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._bar = QFrame(self)
        self._bar.setProperty("class", "SectionBar")
        self._bar.setFixedSize(3, 14)
        self._label = QLabel(title.upper(), self)
        self._label.setProperty("class", "SectionHeader")
        layout.addWidget(self._bar)
        layout.addWidget(self._label)
        layout.addStretch(1)

    def title(self) -> str:
        return self._label.text()

    def add_trailing(self, widget: QWidget) -> None:
        """Add a widget to the right of the title (before the stretch)."""
        layout = self.layout()
        layout.insertWidget(layout.count() - 1, widget)
