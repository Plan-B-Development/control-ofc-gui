"""Global footer / status strip (DEC-208).

Left: app version + kernel + arch (static host identity from ``platform``, read
once — not sampled telemetry). Right: a health indicator + Rescan Hardware /
Export Support Bundle actions. A **dumb view** — ``main_window`` wires the two
buttons to the existing Diagnostics handlers and feeds the health label from the
warning count.
"""

from __future__ import annotations

import platform

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from control_ofc.constants import APP_VERSION
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.glow import PulsingLed


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("StatusFooter_Divider")
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedHeight(12)
    return line


def _meta_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("class", "CardMeta")
    return label


class StatusFooter(QWidget):
    """The always-visible footer strip below the sidebar + content."""

    rescan_clicked = Signal()
    export_bundle_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusFooter_Root")
        self.setFixedHeight(36)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        layout.addWidget(_meta_label("Control-OFC", "StatusFooter_Label_brand"))
        layout.addWidget(_divider())
        layout.addWidget(_meta_label(f"v{APP_VERSION}", "StatusFooter_Label_version"))
        layout.addWidget(_divider())
        layout.addWidget(_meta_label(f"Kernel {platform.release()}", "StatusFooter_Label_kernel"))
        layout.addWidget(_divider())
        layout.addWidget(_meta_label(platform.machine(), "StatusFooter_Label_arch"))

        layout.addStretch(1)

        self._health_led = PulsingLed("ok", diameter=7)
        self._health_led.setObjectName("StatusFooter_Led_health")
        layout.addWidget(self._health_led)
        self._health_label = _meta_label("All systems nominal", "StatusFooter_Label_health")
        layout.addWidget(self._health_label)

        self._rescan_btn = make_button(
            "Rescan Hardware", "secondary", object_name="StatusFooter_Btn_rescan"
        )
        self._rescan_btn.clicked.connect(self.rescan_clicked)
        layout.addWidget(self._rescan_btn)

        self._export_btn = make_button(
            "Export Support Bundle", "secondary", object_name="StatusFooter_Btn_exportBundle"
        )
        self._export_btn.clicked.connect(self.export_bundle_clicked)
        layout.addWidget(self._export_btn)

    def set_warning_count(self, count: int) -> None:
        """Reflect the app's warning count in the health rollup (dumb view)."""
        if count > 0:
            self._health_label.setText(f"{count} warning{'s' if count != 1 else ''}")
            self._health_led.set_color_role("warn")
        else:
            self._health_label.setText("All systems nominal")
            self._health_led.set_color_role("ok")
