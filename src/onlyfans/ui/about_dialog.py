"""About dialog — brand, version, credits."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from onlyfans.constants import APP_NAME, APP_VERSION
from onlyfans.ui.branding import banner_image_path
from onlyfans.ui.microcopy import get as mc


class AboutDialog(QDialog):
    """Branded About dialog with version info and credits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(480, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Banner image
        banner_path = banner_image_path()
        if banner_path:
            banner_label = QLabel()
            pixmap = QPixmap(str(banner_path))
            if pixmap.width() > 440:
                pixmap = pixmap.scaledToWidth(440, Qt.TransformationMode.SmoothTransformation)
            banner_label.setPixmap(pixmap)
            banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(banner_label)

        # Title
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Version
        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setProperty("class", "PageSubtitle")
        layout.addWidget(version_label)

        # Tagline
        tagline = QLabel(mc("about_tagline"))
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("font-style: italic;")
        layout.addWidget(tagline)

        # Credits
        credits_label = QLabel(mc("about_credits"))
        credits_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_label.setProperty("class", "PageSubtitle")
        layout.addWidget(credits_label)

        # Tech info
        tech = QLabel("PySide6 (Qt6) | Python | Linux")
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tech.setProperty("class", "PageSubtitle")
        layout.addWidget(tech)

        layout.addStretch()

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("About_Btn_close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
