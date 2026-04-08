"""Left navigation sidebar with brand mark."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget

from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, PAGE_DIAGNOSTICS, PAGE_SETTINGS
from control_ofc.ui.branding import banner_image_path

_NAV_ITEMS = [
    (PAGE_DASHBOARD, "Dashboard"),
    (PAGE_CONTROLS, "Controls"),
    (PAGE_SETTINGS, "Settings"),
    (PAGE_DIAGNOSTICS, "Diagnostics"),
]


class Sidebar(QWidget):
    """Vertical navigation bar with brand mark and mutually exclusive page buttons."""

    page_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 16)
        layout.setSpacing(4)

        # Brand mark at top
        banner_path = banner_image_path()
        if banner_path:
            brand_label = QLabel()
            brand_label.setObjectName("Sidebar_Brand_image")
            pixmap = QPixmap(str(banner_path))
            scaled = pixmap.scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
            brand_label.setPixmap(scaled)
            brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(brand_label)
            layout.addSpacing(8)
        else:
            brand_text = QLabel("Control-OFC")
            brand_text.setObjectName("Sidebar_Brand_text")
            brand_text.setProperty("class", "PageTitle")
            brand_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(brand_text)
            layout.addSpacing(8)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[int, QPushButton] = {}

        for page_id, label in _NAV_ITEMS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName(f"NavButton_{label}")
            self._group.addButton(btn, page_id)
            self._buttons[page_id] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # About button
        self._about_btn = QPushButton("About")
        self._about_btn.setObjectName("NavButton_About")
        self._about_btn.setToolTip("About Control-OFC")
        self._about_btn.clicked.connect(self._show_about)
        layout.addWidget(self._about_btn)

        self._group.idToggled.connect(self._on_toggled)

        # Select dashboard by default
        self._buttons[PAGE_DASHBOARD].setChecked(True)

    def _on_toggled(self, button_id: int, checked: bool) -> None:
        if checked:
            self.page_changed.emit(button_id)

    def _show_about(self) -> None:
        from control_ofc.ui.about_dialog import AboutDialog

        dlg = AboutDialog(self)
        dlg.exec()

    def select_page(self, page_id: int) -> None:
        btn = self._buttons.get(page_id)
        if btn:
            btn.setChecked(True)
