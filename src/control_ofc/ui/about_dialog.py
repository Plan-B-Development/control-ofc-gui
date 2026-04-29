"""About dialog — brand, version, credits."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from control_ofc.constants import APP_NAME, APP_VERSION


class AboutDialog(QDialog):
    """About dialog with version info and credits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(480, 320)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Title
        title = QLabel(APP_NAME)
        title.setProperty("class", "PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Version
        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setProperty("class", "PageSubtitle")
        layout.addWidget(version_label)

        # Tagline
        tagline = QLabel("Fan control for Linux")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("font-style: italic;")
        layout.addWidget(tagline)

        # Credits
        credits_label = QLabel("Open-source fan control")
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
