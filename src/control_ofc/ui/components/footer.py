"""Global footer / status strip (DEC-208, extended DEC-222).

Left: app version + kernel + arch (static host identity from ``platform``, read
once — not sampled telemetry) plus the operation-mode word. Right: poll freshness,
the thermal-safety chip, the cooling-readiness chip, a health indicator and the
Rescan Hardware / Export Support Bundle actions.

DEC-222 re-homed four indicators here from the retired ``DashboardStatusStrip``:
poll-age, operation mode, the clickable thermal detail (DEC-185) and the
cooling-readiness chip (DEC-206). They were Dashboard-only before; the footer is
always visible, so every page now gets them. A **dumb view** — ``main_window``
wires the buttons and feeds every setter.
"""

from __future__ import annotations

import platform
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from control_ofc.api.models import OperationMode, ReadinessRollup
from control_ofc.constants import APP_VERSION
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.glow import PulsingLed
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.status_banner import MODE_LABELS, THERMAL_STATES, format_poll_age


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


def _chip_button(object_name: str, tooltip: str) -> QPushButton:
    """A flat, focusable chip button.

    Focusable and clickable rather than hover-only, so the detail behind it is
    reachable by keyboard as well as mouse (WCAG 1.4.13) — the property the
    DEC-185 thermal chip and DEC-206 readiness chip both had in the old strip.
    """
    btn = QPushButton("")
    btn.setObjectName(object_name)
    btn.setFlat(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tooltip)
    return btn


class StatusFooter(QWidget):
    """The always-visible footer strip below the sidebar + content."""

    rescan_clicked = Signal()
    export_bundle_clicked = Signal()
    thermal_clicked = Signal()
    readiness_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusFooter_Root")
        # DEC-258: was setFixedHeight(36) against a 43px hint, so the footer's
        # own chips were clipped vertically at the default font and worsened as
        # the user scaled text up. A minimum keeps the strip compact without
        # capping it below its content.
        self.setMinimumHeight(36)
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
        layout.addWidget(_divider())

        # Operation mode (demo / read-only / automatic) — DEC-222. Answers the
        # "am I in demo mode?" Dashboard question from every page.
        self._mode_label = _meta_label("", "StatusFooter_Label_mode")
        layout.addWidget(self._mode_label)

        layout.addStretch(1)

        # Poll freshness — how long since the last successful poll (DEC-222).
        self._poll_age = _meta_label("Not updated yet", "StatusFooter_Label_pollAge")
        layout.addWidget(self._poll_age)

        self._thermal_btn = _chip_button("StatusFooter_Chip_thermal", "Show thermal-safety detail")
        self._thermal_btn.clicked.connect(self.thermal_clicked)
        layout.addWidget(self._thermal_btn)

        self._readiness_btn = _chip_button("StatusFooter_Chip_readiness", "")
        self._readiness_btn.clicked.connect(self.readiness_clicked)
        self._readiness_btn.hide()
        layout.addWidget(self._readiness_btn)

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

        # Sane initial render before the first poll.
        self.set_thermal_state("normal")
        self.set_readiness_rollup(None)  # hidden until the daemon sends a rollup

    def cleanup(self) -> None:
        """Deterministically tear down the health LED's animation.

        ``PulsingLed`` registers itself with the global ``AnimationController``,
        which holds targets weakly — but a widget whose C++ object is deleted while
        its Python wrapper survives stays in that set, so the next pause/resume
        broadcast calls into a dead QTimer. Idempotent; mirrors the pages' cleanup.
        """
        self._health_led.cleanup()

    def set_live(self, live: bool) -> None:
        """Show or hide the poll-driven chips.

        With no daemon connection there is no current thermal or readiness state,
        so continuing to display the last one would present stale data as fact —
        and since the footer is on every page, a stray "Thermal: Emergency" would
        follow the user everywhere. Hidden is the honest render; the ribbon's
        connection state and the poll-age label say why.
        """
        self._thermal_btn.setVisible(live)
        if not live:
            self._readiness_btn.hide()

    def set_warning_count(self, count: int) -> None:
        """Reflect the app's warning count in the health rollup (dumb view)."""
        if count > 0:
            self._health_label.setText(f"{count} warning{'s' if count != 1 else ''}")
            self._health_led.set_color_role("warn")
        else:
            self._health_label.setText("All systems nominal")
            self._health_led.set_color_role("ok")

    def set_operation_mode(self, mode: OperationMode) -> None:
        """Show the current operation mode (DEC-222)."""
        self._mode_label.setText(MODE_LABELS.get(mode, ""))
        set_chip_class(
            self._mode_label,
            "DemoBadge" if mode == OperationMode.DEMO else "CardMeta",
            skip_if_unchanged=True,
        )

    def update_poll_age(self, now: float, last_poll: float | None) -> None:
        """Refresh the "Updated Xs ago" label (DEC-222)."""
        seconds = None if last_poll is None else now - last_poll
        self._poll_age.setText(format_poll_age(seconds))

    def set_thermal_state(self, thermal: str) -> None:
        """Drive the thermal chip from ``DaemonStatus.thermal_state`` (DEC-185)."""
        label, css = THERMAL_STATES.get(thermal or "normal", (f"Thermal: {thermal}", "InfoChip"))
        self._thermal_btn.setText(label)
        set_chip_class(self._thermal_btn, css, skip_if_unchanged=True)

    def set_readiness_rollup(self, rollup: ReadinessRollup | None) -> None:
        """Drive the cooling-readiness chip (DEC-206).

        Hidden entirely when the daemon sends no rollup (older daemon / pre-seed
        / demo). Otherwise a green "Cooling ready" when all checks pass, else an
        amber/red chip naming the number to fix, with the most-important next step
        in the tooltip. The tooltip is a daemon string, so it is HTML-escaped
        (``html.escape``) before ``setToolTip`` — Qt then renders it as plain text,
        never as markup (defence-in-depth, mirroring the item views' PlainText rule).
        """
        if rollup is None:
            self._readiness_btn.hide()
            return
        overall = (rollup.overall or "ok").lower()
        n = rollup.to_fix_count
        if overall == "ok":
            label, css = "✓ Cooling ready", "SuccessChip"
        elif overall in ("warning", "critical"):
            glyph = "⛔" if overall == "critical" else "⚠"
            css = "CriticalChip" if overall == "critical" else "WarningChip"
            label = f"{glyph} Cooling: {n} to fix" if n else f"{glyph} Cooling: needs attention"
        else:  # info / unknown — advisory notes only
            label, css = "Cooling: notes", "InfoChip"
        self._readiness_btn.setText(label)
        set_chip_class(self._readiness_btn, css, skip_if_unchanged=True)
        self._readiness_btn.setToolTip(
            escape(rollup.top_summary or "Open the Hardware page's readiness report")
        )
        self._readiness_btn.show()
