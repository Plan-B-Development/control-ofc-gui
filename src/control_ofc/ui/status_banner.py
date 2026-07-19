"""Always-visible header status strip, and the shared status vocabulary.

Besides the banner widget, this module owns the label/chip maps and the poll-age
formatter that every status surface renders from — the banner, the ribbon and
the footer. Keeping one source of truth is what stops those surfaces drifting
into disagreeing about the same daemon state. ``THERMAL_STATES`` and
``format_poll_age`` moved here from the retired ``DashboardStatusStrip``
(DEC-222) for exactly that reason.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.ui.qt_util import set_chip_class

# Shared label/chip maps so every status surface renders connection + mode
# identically. A single source of truth keeps them from drifting.
CONNECTION_LABELS: dict[ConnectionState, str] = {
    ConnectionState.CONNECTED: "Connected",
    ConnectionState.DEGRADED: "Degraded",
    ConnectionState.DISCONNECTED: "Disconnected",
}
CONNECTION_CHIP: dict[ConnectionState, str] = {
    ConnectionState.CONNECTED: "SuccessChip",
    ConnectionState.DEGRADED: "WarningChip",
    ConnectionState.DISCONNECTED: "CriticalChip",
}
MODE_LABELS: dict[OperationMode, str] = {
    OperationMode.AUTOMATIC: "Automatic",
    OperationMode.READ_ONLY: "Read-only",
    OperationMode.DEMO: "Demo mode",
}

# DaemonStatus.thermal_state -> (label, chip class). The daemon reports
# "normal" | "recovery" | "emergency" | "no_sensor_fallback" (DEC-132/165);
# anything else falls back to a neutral info chip rather than being hidden.
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


class StatusBanner(QWidget):
    """Horizontal strip showing connection state, active profile, and warnings.

    The mode *word* moved to the always-visible footer (DEC-222), which now owns
    the operation-mode indicator app-wide; showing it in both places would be the
    duplication that change set out to remove. The loud DEMO badge stays here —
    it is a different affordance, not a second copy of the same label.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBanner")
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        self._connection_label = QLabel("Disconnected")
        self._connection_label.setObjectName("ConnectionStatus")
        layout.addWidget(self._connection_label)

        self._profile_label = QLabel("No profile")
        layout.addWidget(self._profile_label)

        layout.addStretch()

        self._warning_label = QLabel("")
        self._warning_label.setProperty("class", "WarningChip")
        layout.addWidget(self._warning_label)

        self._demo_badge = QLabel("DEMO")
        self._demo_badge.setProperty("class", "DemoBadge")
        self._demo_badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._demo_badge.hide()
        layout.addWidget(self._demo_badge)

    def set_connection_state(self, state: ConnectionState) -> None:
        self._connection_label.setText(CONNECTION_LABELS.get(state, "Unknown"))
        set_chip_class(self._connection_label, CONNECTION_CHIP.get(state, ""))

    def set_active_profile(self, name: str) -> None:
        self._profile_label.setText(name if name else "No profile")

    def set_operation_mode(self, mode: OperationMode) -> None:
        self._demo_badge.setVisible(mode == OperationMode.DEMO)

    def set_warning_count(self, count: int) -> None:
        if count > 0:
            self._warning_label.setText(f"{count} warning{'s' if count != 1 else ''}")
            self._warning_label.show()
        else:
            self._warning_label.setText("")
            self._warning_label.hide()
