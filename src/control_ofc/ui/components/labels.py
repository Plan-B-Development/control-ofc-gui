"""Label primitives (DEC-238)."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QWidget


def safe_tooltip(text: str) -> str:
    """Escape *text* for a tooltip and force Qt down the rich-text path.

    Escaping alone is not enough. Qt picks plain vs rich text with
    ``mightBeRichText()``, which looks for a ``<`` — and escaping removes every
    one, so an escaped string is rendered *plain* and the entities show through:
    a control named ``CPU & AIO`` displayed as ``CPU &amp; AIO``. ``&`` is common
    in fan names ("Front & Top"); ``<`` is not, so the failure mode is the
    ordinary case, not the adversarial one.

    The wrapper makes Qt parse it, which both decodes the entities back to the
    literal characters and keeps the escaping doing its real job — untrusted
    profile/alias text can still never be interpreted as markup.

    ``white-space: pre`` is not decoration. Two things break without it, both
    measured: ``QTipLabel`` sets ``setWordWrap(mightBeRichText(text))``, so the
    rich-text path alone re-shapes a 442x40 single-line tooltip into a 145x94
    wrapped block — ruinous for a tooltip whose whole job is to show a name the
    tile had to elide; and the HTML parser collapses runs of whitespace, so an
    alias reading ``Front  Double  Space`` would come back single-spaced from the
    one surface that is supposed to reproduce it verbatim.

    Moved here from ``fan_control_card`` (DEC-461) when a second consumer needed
    it: ``ElidedLabel(tooltip_when_elided=True)``.
    """
    return f'<html><body style="white-space: pre">{escape(text)}</body></html>'


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
        tooltip_when_elided: bool = False,
    ) -> None:
        super().__init__(text, parent)
        if object_name:
            self.setObjectName(object_name)
        self._mode = mode
        # Opt-in (DEC-461): the tooltip is the only way back to a name the label
        # had to shorten, so it carries the full text exactly while it is elided,
        # and nothing when it fits — an always-on tooltip repeating a visible
        # name is noise. The caller must not set its own tooltip as well.
        self._tooltip_when_elided = tooltip_when_elided
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        super().setText(text)
        self._sync_elided_tooltip()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_elided_tooltip()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # A theme or font-size change re-shapes the text at the same width.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._sync_elided_tooltip()

    def _sync_elided_tooltip(self) -> None:
        # getattr: QLabel's constructor can run before the flag exists.
        if not getattr(self, "_tooltip_when_elided", False):
            return
        full = self.text()
        self.setToolTip(safe_tooltip(full) if self.elided_text() != full else "")

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
