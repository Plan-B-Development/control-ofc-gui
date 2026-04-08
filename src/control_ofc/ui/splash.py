"""Branded splash screen shown during application startup."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QSplashScreen, QWidget

from control_ofc.ui.branding import splash_image_path
from control_ofc.ui.microcopy import get as mc


class AppSplashScreen(QSplashScreen):
    """Branded splash screen with status messages."""

    def __init__(self) -> None:
        path = splash_image_path()
        if path:
            pixmap = QPixmap(str(path))
            if pixmap.width() > 600:
                pixmap = pixmap.scaledToWidth(600, Qt.TransformationMode.SmoothTransformation)
        else:
            pixmap = QPixmap(600, 340)
            pixmap.fill(Qt.GlobalColor.black)

        super().__init__(pixmap)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.SplashScreen
        )

        self._status_text = ""
        self.set_status("splash_status_init")

    def set_status(self, key: str) -> None:
        """Update the status message using a microcopy key."""
        self._status_text = mc(key)
        self.showMessage(
            self._status_text,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            Qt.GlobalColor.white,
        )

    def finish_with_delay(self, main_window: QWidget, delay_ms: int = 3000) -> None:
        """Close the splash after a minimum display time."""
        QTimer.singleShot(delay_ms, lambda: self.finish(main_window))
