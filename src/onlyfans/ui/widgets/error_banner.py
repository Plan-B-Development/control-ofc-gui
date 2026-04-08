"""Error banner widget — dismissible notification strip for errors and warnings."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class ErrorBanner(QWidget):
    """A horizontal banner that shows error/warning messages with auto-dismiss."""

    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self._icon_label = QLabel()
        layout.addWidget(self._icon_label)

        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label, 1)

        self._dismiss_btn = QPushButton("Dismiss")
        self._dismiss_btn.setObjectName("ErrorBanner_Btn_dismiss")
        self._dismiss_btn.setFixedWidth(80)
        self._dismiss_btn.clicked.connect(self.hide_banner)
        layout.addWidget(self._dismiss_btn)

        self._auto_dismiss_timer = QTimer(self)
        self._auto_dismiss_timer.setSingleShot(True)
        self._auto_dismiss_timer.timeout.connect(self.hide_banner)

    def show_error(self, message: str, auto_dismiss_ms: int = 0) -> None:
        self._icon_label.setText("[!]")
        self._message_label.setText(message)
        self._message_label.setProperty("class", "CriticalChip")
        self._message_label.style().unpolish(self._message_label)
        self._message_label.style().polish(self._message_label)
        self.setVisible(True)
        if auto_dismiss_ms > 0:
            self._auto_dismiss_timer.start(auto_dismiss_ms)

    def show_warning(self, message: str, auto_dismiss_ms: int = 0) -> None:
        self._icon_label.setText("[*]")
        self._message_label.setText(message)
        self._message_label.setProperty("class", "WarningChip")
        self._message_label.style().unpolish(self._message_label)
        self._message_label.style().polish(self._message_label)
        self.setVisible(True)
        if auto_dismiss_ms > 0:
            self._auto_dismiss_timer.start(auto_dismiss_ms)

    def show_info(self, message: str, auto_dismiss_ms: int = 5000) -> None:
        self._icon_label.setText("[i]")
        self._message_label.setText(message)
        self._message_label.setProperty("class", "SuccessChip")
        self._message_label.style().unpolish(self._message_label)
        self._message_label.style().polish(self._message_label)
        self.setVisible(True)
        if auto_dismiss_ms > 0:
            self._auto_dismiss_timer.start(auto_dismiss_ms)

    def hide_banner(self) -> None:
        self._auto_dismiss_timer.stop()
        self.setVisible(False)
        self.dismissed.emit()
