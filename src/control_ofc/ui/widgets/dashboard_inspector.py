"""Dashboard inspector — Sensors side panel (DEC-184, was DEC-182).

A thin container for the dashboard's right pane: a "Sensors" heading over the
existing :class:`SensorSeriesPanel` (passed in).

DEC-184 reduced this from the former tabbed Sensors/Events/Warnings panel. DEC-222
reduced it again: the Quick Actions and Alerts panels the DEC-213 rail carried were
removed with the Dashboard rebuild, and :class:`WarningsView` (which had lived in
this file) moved to ``widgets/warnings_view`` now that the Logs page is the single
warnings surface. What remains is exactly the Sensors rail.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DashboardInspector(QWidget):
    """Right-pane inspector: the Thermal Sensors series selector."""

    def __init__(
        self,
        sensors_widget: QWidget,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector_Root")
        self.setMinimumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 2)
        layout.setSpacing(4)

        self._heading = QLabel("Thermal Sensors")
        self._heading.setObjectName("Inspector_Heading")
        self._heading.setProperty("class", "SectionTitle")
        layout.addWidget(self._heading)

        # The inspector owns the panel objectName so naming stays consistent
        # regardless of what the page named the composed widget.
        sensors_widget.setObjectName("Inspector_Panel_sensors")
        self._sensors_widget = sensors_widget
        layout.addWidget(sensors_widget, 1)  # the tall part (series selector)

    def sensors_widget(self) -> QWidget:
        """The hosted sensor panel (exposed for tests/wiring)."""
        return self._sensors_widget
