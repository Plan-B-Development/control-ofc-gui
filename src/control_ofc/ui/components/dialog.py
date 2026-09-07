"""Dialog base with header / body / footer + a translucent scrim (DEC-208).

Qt cannot do a live backdrop blur, so the "blur" behind the modal in the mockups
is approximated by a translucent ``#ModalScrim`` veil over the parent window.
Future dialogs (fan-role, curve editor, …) adopt this base in their own stage.

**The body scrolls and the frame is clamped to the screen (`P8-ba`).** Before
this, header / body / footer went straight into a ``QVBoxLayout`` installed on a
top-level widget. That layout runs under ``SetDefaultConstraint``, which makes
the layout's minimum the *window's* hard minimum — so tall content became a
floor the window could not go below, the footer fell off the bottom of the
display, and the dialog could be neither shrunk nor scrolled. Measured on the
thermal session dialog: a finished session reported ``minimumHeight() == 1037``
and ``resize(800, 600)`` returned ``800x1037``; expanding every section reached
2600, which exceeds a 1440p panel. The footer holds **Stop**, so on a laptop the
recording was mechanically unstoppable from the GUI.

Two things fix it, and the order matters:

1. the body lives in a ``QScrollArea``, which is what actually lowers the
   minimum — measured on this exact tree, 2496 → 146 for a 2418px body, after
   which ``resize(700, 400)`` is honoured;
2. ``showEvent`` clamps the frame to ``availableGeometry()``.

**Header and footer stay OUTSIDE the scroll area**, which is the whole point:
the buttons are reachable at every height rather than scrolling away with the
content. Do not "simplify" by putting the whole outer layout in the scroll area.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.components.buttons import make_button


class _ScrimOverlay(QWidget):
    """A translucent veil covering *host*, tracking its size."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("ModalScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._host = host
        self.setGeometry(host.rect())
        host.installEventFilter(self)

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return False

    def detach(self) -> None:
        if self._host is not None:
            self._host.removeEventFilter(self)
        self.hide()
        self.deleteLater()


class ModalDialog(QDialog):
    """Header / body / footer dialog frame with a translucent parent scrim.

    ``modal=False`` makes the dialog modeless *and* suppresses the scrim, which
    are one decision rather than two: the veil says "the window behind this is
    not usable", so painting it over a window the user can still click would be
    a lie. Used by the session dialog, whose own isolation templates instruct
    the user to set a duty on the Controls page (`P8-bd`).
    """

    def __init__(self, title: str, parent: QWidget | None = None, *, modal: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("ModalDialog")
        self._modal = modal
        self.setModal(modal)
        self._scrim: _ScrimOverlay | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("ModalDialog_Header")
        h_layout = QHBoxLayout(header)
        self._title = QLabel(title, header)
        # DEC-231: dialog titles can embed profile/control names (untrusted, e.g.
        # "Edit Fan Role: {control.name}") — render verbatim so markup is never
        # reinterpreted as rich text. Closes the title for every ModalDialog.
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setObjectName("ModalDialog_Label_title")
        self._title.setProperty("class", "PageSubtitle")
        h_layout.addWidget(self._title)
        h_layout.addStretch(1)
        outer.addWidget(header)

        # The body is the ONLY scrolling region: header and footer are siblings of
        # the scroll area, not children of it, so the footer buttons — Stop among
        # them — stay on screen at every dialog height (`P8-ba`).
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("ModalDialog_Scroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Deliberately unparented here: `setWidget` reparents into the viewport,
        # and passing `self` first would briefly make the body a direct child of
        # the dialog and lay it out twice.
        self._body = QWidget()
        self._body.setObjectName("ModalDialog_Body")
        self._body_layout = QVBoxLayout(self._body)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

        self._footer = QWidget(self)
        self._footer.setObjectName("ModalDialog_Footer")
        self._footer_layout = QHBoxLayout(self._footer)
        self._footer_layout.addStretch(1)
        outer.addWidget(self._footer)

    # -- structure --

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def add_footer_button(
        self, text: str, variant: str = "secondary", *, object_name: str | None = None
    ) -> QPushButton:
        button = make_button(text, variant, object_name=object_name, parent=self._footer)
        self._footer_layout.addWidget(button)
        return button

    # -- scrim lifecycle --

    def _host_window(self) -> QWidget | None:
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    def _ensure_scrim(self) -> None:
        # A modeless dialog gets no veil — see the class docstring.
        if not self._modal:
            return
        host = self._host_window()
        if host is not None and self._scrim is None:
            self._scrim = _ScrimOverlay(host)
            self._scrim.show()
            self._scrim.raise_()

    def _remove_scrim(self) -> None:
        if self._scrim is not None:
            self._scrim.detach()
            self._scrim = None

    # -- screen fit --

    def _clamp_to_screen(self) -> None:
        """Shrink the frame to fit the screen it is opening on (`P8-ba`).

        ``availableGeometry``, never ``screenGeometry``: the latter is the whole
        panel and ignores taskbars, docks and reserved struts, so clamping to it
        puts the footer under the user's panel — which is the bug, one step
        smaller.

        The scroll area is what makes this possible at all. It removed the hard
        minimum, so a ``resize`` here is honoured; before it, this method could
        have run and changed nothing.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - no screen is not a real session
            return
        avail = screen.availableGeometry()
        # Window decoration sits OUTSIDE the client area, so a client height equal
        # to the available height puts the title bar off the top. `frameGeometry`
        # only differs from `geometry` once the WM has mapped the window, so this
        # is 0 on a first show and the real value on any later one — never
        # negative, and never a guessed constant.
        chrome_w = max(0, self.frameGeometry().width() - self.width())
        chrome_h = max(0, self.frameGeometry().height() - self.height())
        max_w = max(1, avail.width() - chrome_w)
        max_h = max(1, avail.height() - chrome_h)
        if self.width() > max_w or self.height() > max_h:
            self.resize(min(self.width(), max_w), min(self.height(), max_h))
        # Fitting is not enough if it is positioned off the edge. Nudge back
        # inside rather than re-centring: a dialog the user has already dragged
        # should stay where they put it wherever that is still legal.
        frame = self.frameGeometry()
        if not avail.contains(frame):
            x = min(max(frame.x(), avail.x()), max(avail.x(), avail.right() - frame.width() + 1))
            y = min(max(frame.y(), avail.y()), max(avail.y(), avail.bottom() - frame.height() + 1))
            self.move(x, y)

    def showEvent(self, event) -> None:
        self._ensure_scrim()
        self._clamp_to_screen()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._remove_scrim()
        super().hideEvent(event)
