"""Cards + section header (DEC-208)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


def _slug(text: str) -> str:
    return "".join(c for c in text.title() if c.isalnum()) or "Section"


class Card(QFrame):
    """A calm surface card (``.Card`` QSS)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")


class ContentSizedCard(Card):
    """A ``Card`` that will not be squashed below what its content needs at the
    width it actually has.

    Qt's default minimum is width-blind: ``QLayout.totalMinimumSize()`` sums each
    child's ``minimumSize``, and a word-wrapped label's minimum is computed at
    some width other than its real one. A card of wrapped text therefore reports
    a minimum that is too small, and whatever contains it — a QSplitter pane, a
    QScrollArea — squashes it and clips the last lines silently. It gets worse as
    the card gets narrower, which is the opposite of what a reader expects.

    Measured on the System State page before this existed: the health card
    under-reported by 17px at the default window and 35px near the minimum one,
    and the interference card by 69px once it moved into a narrower column —
    each of those a whole line off a paragraph or a detail box.

    Overriding ``minimumSizeHint`` is what makes this reach anything: neither
    QSplitter nor QScrollArea propagates ``heightForWidth``, so the honest number
    has to arrive through the one channel they do consult.

    Nothing else is needed on the children. ``QLabel.setWordWrap(True)`` already
    sets the ``heightForWidth`` size-policy flag, and ``QWidget`` defers
    ``hasHeightForWidth()`` to its layout when it has one, so a plain container
    propagates it without help — verified, after a first pass at this shipped a
    helper that set those flags by hand and turned out to be a no-op at all
    twelve of its call sites. ``totalHeightForWidth`` already sees the wrapping;
    what was missing was anyone asking.

    The width used is the last layout pass's, so a *narrowing* resize is briefly
    one pass behind and settles on the next (2 passes, measured). Widening is
    never short — more width can only need less height.
    """

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        layout = self.layout()
        width = self.width()
        if layout is None or width <= 0:
            return hint
        return QSize(hint.width(), max(hint.height(), layout.totalHeightForWidth(width)))


class BracketCard(QFrame):
    """A card with a left accent bar that intensifies on hover (``.BracketCard``).

    ``state`` colours the bar by severity (``"crit"`` / ``"warn"`` / ``"neutral"``)
    through QSS, so it repaints on a live theme change. That matters: the System
    State issue cards previously hand-rolled this shape with a ``QFrame`` strip
    and an inline ``setStyleSheet`` carrying an interpolated token, which freezes
    the colour at render time and never repaints (DEC-258).

    ``warning`` is kept as the original binary toggle for existing callers.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        object_name: str | None = None,
        state: str = "neutral",
    ) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setProperty("class", "BracketCard")
        self.setProperty("warning", "false")
        self.setProperty("state", state)


class SectionHeader(QWidget):
    """An accent bar + uppercase heading-font title (the mockup section header).

    Pass ``step`` to swap the thin accent bar for a numbered step badge — the
    Controls page uses this for its 1 → 2 → 3 workflow cue (DEC-233). Callers
    that omit ``step`` keep the original accent-bar treatment unchanged.
    """

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        object_name: str | None = None,
        step: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name or f"SectionHeader_{_slug(title)}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if step is not None:
            # A filled, numbered badge — one accent element carrying the step
            # number, replacing (not doubling) the plain bar.
            self._bar = QLabel(str(step), self)
            self._bar.setProperty("class", "StepBadge")
            self._bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._bar.setFixedSize(18, 18)
        else:
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
