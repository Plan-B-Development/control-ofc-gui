"""Always-visible header status strip."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from control_ofc.api.models import ConnectionState, OperationMode

# Shared label/chip maps so the dashboard's DashboardStatusStrip (DEC-176/177)
# renders connection + mode identically to this global banner. A single source
# of truth keeps the two status surfaces from drifting.
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
    OperationMode.MANUAL_OVERRIDE: "Manual Override",
    OperationMode.READ_ONLY: "Read-only",
    OperationMode.DEMO: "Demo mode",
}


class StatusBanner(QWidget):
    """Horizontal strip showing connection state, active profile, mode, and warnings."""

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

        self._mode_label = QLabel("")
        layout.addWidget(self._mode_label)

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
        self._connection_label.setProperty("class", CONNECTION_CHIP.get(state, ""))
        # Force style refresh
        self._connection_label.style().unpolish(self._connection_label)
        self._connection_label.style().polish(self._connection_label)

    def set_active_profile(self, name: str) -> None:
        self._profile_label.setText(name if name else "No profile")

    def set_operation_mode(self, mode: OperationMode) -> None:
        self._mode_label.setText(MODE_LABELS.get(mode, ""))

        is_manual = mode == OperationMode.MANUAL_OVERRIDE
        self._mode_label.setProperty("class", "ManualBadge" if is_manual else "")
        self._mode_label.style().unpolish(self._mode_label)
        self._mode_label.style().polish(self._mode_label)

        self._demo_badge.setVisible(mode == OperationMode.DEMO)

    def set_warning_count(self, count: int) -> None:
        if count > 0:
            self._warning_label.setText(f"{count} warning{'s' if count != 1 else ''}")
            self._warning_label.show()
        else:
            self._warning_label.setText("")
            self._warning_label.hide()
