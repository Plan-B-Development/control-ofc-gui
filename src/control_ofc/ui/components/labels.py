"""Label primitives (DEC-238)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QWidget


class ElidedLabel(QLabel):
    """A single-line label that elides to ``…`` instead of forcing its owner wide.

    Elision happens at **paint** time, so ``text()`` keeps returning the full,
    verbatim string. That matters for more than tidiness: fan and control names
    are untrusted (a user alias or profile data), and the card's XSS guard asserts
    the label stores exactly what it was handed. An elide-by-``setText`` version
    would rewrite that string and quietly break the guard's premise.

    ``minimumSizeHint`` is deliberately tiny — a QLabel normally refuses to shrink
    below its full text, which is precisely what makes one long name widen one
    card in a grid of otherwise uniform tiles. ``sizeHint`` still reports the full
    width, so a layout free to grant it the room still will.

    The text is always rendered as plain text; the caller cannot opt into rich
    text, so stray markup in a name can never be reinterpreted as formatting.

    **Single-line text only.** The custom paint reimplements what this widget
    needs and nothing else, so QLabel features that depend on its own layout are
    silently inert here: ``setWordWrap``, ``setPixmap``/``setMovie``,
    ``setTextFormat(RichText)``, selection via ``setTextInteractionFlags``, and
    buddy mnemonic underlines. Use a plain ``QLabel`` if you need any of them.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        object_name: str | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> None:
        super().__init__(text, parent)
        if object_name:
            self.setObjectName(object_name)
        self._mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        fm = self.fontMetrics()
        # Include the frame/contents inset: a caller that sets contentsMargins
        # (the fan tile's band placeholder does) otherwise reports a minimum
        # narrower than it can actually paint in, and a hard squeeze can leave
        # contentsRect() negative — where elidedText returns "" and the label
        # goes blank instead of showing an ellipsis.
        margins = self.contentsMargins()
        return QSize(
            fm.horizontalAdvance("…") + margins.left() + margins.right(),
            fm.height() + margins.top() + margins.bottom(),
        )

    def _visual_alignment(self) -> Qt.AlignmentFlag:
        """Alignment resolved for the layout direction, as QLabel's own paint does.

        Without this an ElidedLabel left-aligns under an RTL layout where every
        other label in the app right-aligns.
        """
        return QStyle.visualAlignment(self.layoutDirection(), self.alignment())

    def paintEvent(self, event) -> None:
        del event  # the whole label is repainted; Qt's damage region is not needed
        painter = QPainter(self)
        # No PE_Widget draw here: Qt paints the stylesheet box (background *and*
        # border) for a styled widget outside the paint event, so an explicit
        # drawPrimitive is redundant — verified by mutation, a QSS border still
        # renders without it.
        # Take the pen from the palette rather than letting it default: QSS
        # ``color:`` lands on the palette at polish time, so this keeps the label
        # tracking a live theme change exactly as an unpainted QLabel would.
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), int(self._visual_alignment()), self.elided_text())
        painter.end()

    def elided_text(self) -> str:
        """What is actually painted at the current width — assertable headlessly."""
        fm = self.fontMetrics()
        return fm.elidedText(self.text(), self._mode, self.contentsRect().width())
