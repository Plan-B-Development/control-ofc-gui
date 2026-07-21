"""Modal-dialog base with header / body / footer + a translucent scrim (DEC-208).

Qt cannot do a live backdrop blur, so the "blur" behind the modal in the mockups
is approximated by a translucent ``#ModalScrim`` veil over the parent window.
Future dialogs (fan-role, curve editor, …) adopt this base in their own stage.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
    """Header / body / footer dialog frame with a translucent parent scrim."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModalDialog")
        self.setModal(True)
        self._scrim: _ScrimOverlay | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("ModalDialog_Header")
        h_layout = QHBoxLayout(header)
        self._title = QLabel(title, header)
        self._title.setProperty("class", "PageSubtitle")
        h_layout.addWidget(self._title)
        h_layout.addStretch(1)
        outer.addWidget(header)

        self._body = QWidget(self)
        self._body.setObjectName("ModalDialog_Body")
        self._body_layout = QVBoxLayout(self._body)
        outer.addWidget(self._body, 1)

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
        host = self._host_window()
        if host is not None and self._scrim is None:
            self._scrim = _ScrimOverlay(host)
            self._scrim.show()
            self._scrim.raise_()

    def _remove_scrim(self) -> None:
        if self._scrim is not None:
            self._scrim.detach()
            self._scrim = None

    def showEvent(self, event) -> None:
        self._ensure_scrim()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._remove_scrim()
        super().hideEvent(event)
