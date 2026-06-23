"""Dashboard command + status strip (DEC-176/177).

A self-contained header for the dashboard page: connection / active-profile /
mode / thermal chips, a "time since last successful poll" indicator, a clickable
warning chip, and a compact profile selector + Apply.

It is a **dumb view** — the dashboard page wires `AppState` signals to its setters
(mirroring how `main_window` wires the global `StatusBanner`) and owns the
profile-apply flow. Connection + mode rendering reuses `StatusBanner`'s shared
maps so the two status surfaces can never drift. While the dashboard is active the
global `StatusBanner` is hidden, so this strip is the single status surface.

Chips pair colour with a word (and the warning chip an icon) so colour is never
the only cue (WCAG 1.4.1); the warning chip is a focusable button, so the
click/keyboard path never depends on hover (WCAG 1.4.13).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.ui.status_banner import CONNECTION_CHIP, CONNECTION_LABELS, MODE_LABELS

# DaemonStatus.thermal_state -> (label, chip class). The daemon reports
# "normal" | "recovery" | "emergency" | "no_sensor_fallback" (DEC-132/165);
# anything else falls back to a neutral info chip rather than being hidden.
# Public so the dashboard Safety card renders from the SAME map (no drift).
THERMAL_STATES: dict[str, tuple[str, str]] = {
    "normal": ("Thermal OK", "SuccessChip"),
    "recovery": ("Thermal: Recovery", "WarningChip"),
    "emergency": ("Thermal: Emergency", "CriticalChip"),
    "no_sensor_fallback": ("Thermal: No CPU sensor", "WarningChip"),
}


def format_poll_age(seconds_ago: float | None) -> str:
    """Human label for time since the last successful poll. Pure/testable."""
    if seconds_ago is None:
        return "Not updated yet"
    seconds_ago = max(0.0, seconds_ago)
    if seconds_ago < 2:
        return "Updated just now"
    if seconds_ago < 60:
        return f"Updated {int(seconds_ago)}s ago"
    if seconds_ago < 3600:
        return f"Updated {int(seconds_ago // 60)}m ago"
    return f"Updated {int(seconds_ago // 3600)}h ago"


def _refresh_chip(label: QLabel, css_class: str) -> None:
    """Apply a QSS chip class and force a style repolish so it takes effect."""
    label.setProperty("class", css_class)
    label.style().unpolish(label)
    label.style().polish(label)


class DashboardStatusStrip(QWidget):
    """Connection/profile/mode/thermal/poll-age/warning chips + profile picker."""

    warning_clicked = Signal()
    thermal_clicked = Signal()
    inspector_toggle_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusStrip_Root")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)

        self._connection = QLabel("Disconnected")
        self._connection.setObjectName("StatusStrip_Chip_connection")
        layout.addWidget(self._connection)

        self._profile = QLabel("No profile")
        self._profile.setObjectName("StatusStrip_Label_profile")
        layout.addWidget(self._profile)

        self._mode = QLabel("")
        self._mode.setObjectName("StatusStrip_Chip_mode")
        layout.addWidget(self._mode)

        # Clickable thermal chip (DEC-185): mirrors the warning chip — a flat,
        # focusable button so the thermal-safety detail is reachable by click or
        # keyboard (WCAG 1.4.13), not hover. Always visible (unlike the warning
        # chip); its label + chip class are driven by set_thermal_state.
        self._thermal = QPushButton("")
        self._thermal.setObjectName("StatusStrip_Chip_thermal")
        self._thermal.setFlat(True)
        self._thermal.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thermal.setToolTip("Show thermal-safety detail")
        self._thermal.clicked.connect(self.thermal_clicked)
        layout.addWidget(self._thermal)

        self._poll_age = QLabel("Not updated yet")
        self._poll_age.setObjectName("StatusStrip_Label_pollAge")
        self._poll_age.setProperty("class", "PageSubtitle")
        layout.addWidget(self._poll_age)

        layout.addStretch()

        # Focusable, clickable warning chip (icon + word + colour).
        self._warning = QPushButton("")
        self._warning.setObjectName("StatusStrip_Chip_warnings")
        self._warning.setProperty("class", "WarningChip")
        self._warning.setFlat(True)
        self._warning.setCursor(Qt.CursorShape.PointingHandCursor)
        self._warning.clicked.connect(self.warning_clicked)
        self._warning.hide()
        layout.addWidget(self._warning)

        # Compact profile selector + Apply. The page owns the apply flow and
        # reuses these widgets verbatim (see DashboardPage._on_apply_profile).
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("StatusStrip_Combo_profile")
        self.profile_combo.setMinimumWidth(120)
        layout.addWidget(self.profile_combo)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("StatusStrip_Btn_apply")
        layout.addWidget(self.apply_btn)

        # Sensors toggle: shows/hides the right-hand Sensors pane so the chart can
        # reclaim width on narrow windows. Lives in the strip (not the pane) so it
        # stays reachable while the pane is hidden. The chevron mirrors
        # CollapsibleSection (▾ shown / ▸ hidden); text + tooltip + glyph keep
        # state off a colour-only cue (WCAG 1.4.1).
        self.inspector_toggle = QPushButton("▸  Sensors")
        self.inspector_toggle.setObjectName("Inspector_Btn_toggle")
        self.inspector_toggle.setFlat(True)
        self.inspector_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspector_toggle.setToolTip("Show or hide the sensors panel")
        self.inspector_toggle.clicked.connect(self.inspector_toggle_clicked)
        layout.addWidget(self.inspector_toggle)

        # Sane initial render before the first poll.
        self.set_connection_state(ConnectionState.DISCONNECTED)
        self.set_thermal_state("normal")

    # --- setters: the dashboard page wires AppState signals to these ---

    def set_connection_state(self, state: ConnectionState) -> None:
        self._connection.setText(CONNECTION_LABELS.get(state, "Unknown"))
        _refresh_chip(self._connection, CONNECTION_CHIP.get(state, ""))

    def set_active_profile(self, name: str) -> None:
        self._profile.setText(name if name else "No profile")

    def set_operation_mode(self, mode: OperationMode) -> None:
        self._mode.setText(MODE_LABELS.get(mode, ""))
        css = ""
        if mode == OperationMode.MANUAL_OVERRIDE:
            css = "ManualBadge"
        elif mode == OperationMode.DEMO:
            css = "DemoBadge"
        _refresh_chip(self._mode, css)

    def set_thermal_state(self, thermal: str) -> None:
        label, css = THERMAL_STATES.get(thermal or "normal", (f"Thermal: {thermal}", "InfoChip"))
        self._thermal.setText(label)
        _refresh_chip(self._thermal, css)

    def set_warning_count(self, count: int) -> None:
        if count > 0:
            self._warning.setText(f"⚠ {count} warning{'s' if count != 1 else ''}")
            self._warning.show()
        else:
            self._warning.setText("")
            self._warning.hide()

    def update_poll_age(self, now: float, last_poll: float | None) -> None:
        seconds = None if last_poll is None else now - last_poll
        self._poll_age.setText(format_poll_age(seconds))

    def set_inspector_expanded(self, expanded: bool) -> None:
        """Reflect the Sensors pane's open state on the toggle button."""
        self.inspector_toggle.setText(("▾  " if expanded else "▸  ") + "Sensors")
