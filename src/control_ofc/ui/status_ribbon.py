"""Global top status ribbon — the redesign header (DEC-208).

Left: brand mark + a pulsing daemon-status LED + connection label + daemon
uptime. Right: a thermal pill + a warnings indicator that opens Logs. A **dumb
view** — ``main_window`` wires ``AppState`` signals to its setters (mirroring the
global ``StatusBanner``). The mockup's host CPU/RAM bars and user pill are
intentionally omitted (no new telemetry; "User menu: Nil").
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from control_ofc.api.models import ConnectionState
from control_ofc.ui.branding import load_app_icon
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.glow import PulsingLed
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.status_banner import CONNECTION_CHIP, CONNECTION_LABELS
from control_ofc.ui.widgets.status_strip import THERMAL_STATES

# ConnectionState -> pulsing-LED colour role.
_CONNECTION_LED: dict[ConnectionState, str] = {
    ConnectionState.CONNECTED: "ok",
    ConnectionState.DEGRADED: "warn",
    ConnectionState.DISCONNECTED: "crit",
}

# THERMAL_STATES chip class -> StatusPill state.
_CHIP_TO_PILL: dict[str, str] = {
    "SuccessChip": "ok",
    "WarningChip": "warning",
    "CriticalChip": "critical",
    "InfoChip": "info",
}


def format_uptime(seconds: int | None) -> str:
    """Human label for daemon uptime (e.g. ``23h 28m 9s``). Pure/testable."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class StatusRibbon(QWidget):
    """The always-visible header ribbon above the sidebar + content."""

    alerts_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusRibbon_Root")
        self.setFixedHeight(44)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(12)

        # Brand mark (icon + wordmark).
        self._brand_icon = QLabel()
        self._brand_icon.setObjectName("StatusRibbon_Brand_icon")
        icon = load_app_icon()
        if icon is not None and not icon.isNull():
            self._brand_icon.setPixmap(icon.pixmap(20, 20))
        layout.addWidget(self._brand_icon)

        self._brand_text = QLabel("Control-OFC")
        self._brand_text.setObjectName("StatusRibbon_Brand_text")
        self._brand_text.setProperty("class", "RibbonBrand")
        layout.addWidget(self._brand_text)

        # Daemon status: a pulsing LED + a coloured connection label.
        self._daemon_led = PulsingLed("crit", diameter=9)
        self._daemon_led.setObjectName("StatusRibbon_Led_daemon")
        layout.addWidget(self._daemon_led)

        self._daemon_label = QLabel(CONNECTION_LABELS[ConnectionState.DISCONNECTED])
        self._daemon_label.setObjectName("StatusRibbon_Label_daemon")
        set_chip_class(self._daemon_label, CONNECTION_CHIP[ConnectionState.DISCONNECTED])
        layout.addWidget(self._daemon_label)

        # Daemon uptime.
        self._uptime_label = QLabel(format_uptime(None))
        self._uptime_label.setObjectName("StatusRibbon_Label_uptime")
        self._uptime_label.setProperty("class", "CardMeta")
        layout.addWidget(self._uptime_label)

        layout.addStretch(1)

        # Thermal pill (already-available daemon data).
        self._thermal_pill = StatusPill("", "neutral")
        self._thermal_pill.setObjectName("StatusRibbon_Pill_thermal")
        self._thermal_pill.hide()
        layout.addWidget(self._thermal_pill)

        # Warnings indicator → Logs.
        self._alerts_btn = QPushButton("Alerts")
        self._alerts_btn.setObjectName("StatusRibbon_Btn_alerts")
        self._alerts_btn.setProperty("variant", "ghost")
        self._alerts_btn.clicked.connect(self.alerts_clicked)
        layout.addWidget(self._alerts_btn)

        self._alert_badge = StatusPill("0", "warning")
        self._alert_badge.setObjectName("StatusRibbon_Badge_alertCount")
        self._alert_badge.hide()
        layout.addWidget(self._alert_badge)

    # -- setters (dumb view) --

    def set_connection_state(self, state: ConnectionState) -> None:
        self._daemon_label.setText(CONNECTION_LABELS.get(state, "Unknown"))
        set_chip_class(self._daemon_label, CONNECTION_CHIP.get(state, ""))
        self._daemon_led.set_color_role(_CONNECTION_LED.get(state, "neutral"))

    def set_thermal_state(self, thermal_state: str | None) -> None:
        entry = THERMAL_STATES.get(thermal_state or "")
        if entry is None:
            self._thermal_pill.hide()
            return
        label, chip = entry
        self._thermal_pill.set_text(label)
        self._thermal_pill.set_state(_CHIP_TO_PILL.get(chip, "info"))
        self._thermal_pill.show()

    def set_uptime(self, seconds: int | None) -> None:
        self._uptime_label.setText(format_uptime(seconds))

    def set_warning_count(self, count: int) -> None:
        if count > 0:
            self._alert_badge.set_text(str(count))
            self._alert_badge.show()
        else:
            self._alert_badge.hide()
