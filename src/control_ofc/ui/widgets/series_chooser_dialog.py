"""Sensor picker dialog — opened from summary card clicks.

Lets the user pick which sensor a card should display. Does NOT
affect chart series visibility (that's the series panel's job).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import FanReading, SensorReading


class SensorPickerDialog(QDialog):
    """Modal dialog for choosing which sensor a summary card displays.

    Returns the selected sensor ID via ``selected_sensor_id`` after accept.
    """

    def __init__(
        self,
        category: str,
        sensors: list[SensorReading] | None = None,
        fans: list[FanReading] | None = None,
        current_binding: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Select Sensor \u2014 {category.replace('_', ' ').title()}")
        self.setMinimumWidth(360)
        self.setMinimumHeight(250)

        self._sensors = sensors or []
        self._fans = fans or []
        self.selected_sensor_id: str = current_binding

        layout = QVBoxLayout(self)

        # Filter to relevant items
        items = self._filter_items(category)

        if not items:
            layout.addWidget(QLabel("No sensors available for this category."))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(4, 4, 4, 4)
            content_layout.setSpacing(4)

            # "Auto (default)" option
            auto_radio = QRadioButton("Auto (default by type)")
            auto_radio.setToolTip("Use automatic kind-based matching")
            if not current_binding:
                auto_radio.setChecked(True)
            auto_radio.toggled.connect(lambda checked: self._on_selected("") if checked else None)
            content_layout.addLayout(self._make_row(auto_radio, "\u2014"))

            self._radios: dict[str, QRadioButton] = {}
            self._value_labels: dict[str, QLabel] = {}

            for item_id, label, value_text in items:
                radio = QRadioButton(label)
                radio.setToolTip(f"ID: {item_id}")
                if item_id == current_binding:
                    radio.setChecked(True)
                radio.toggled.connect(
                    lambda checked, sid=item_id: self._on_selected(sid) if checked else None
                )
                self._radios[item_id] = radio

                value_label = QLabel(value_text)
                value_label.setProperty("class", "ValueLabel")
                self._value_labels[item_id] = value_label

                content_layout.addLayout(self._make_row(radio, value_text, value_label))

            content_layout.addStretch()
            scroll.setWidget(content)
            layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_values(self, sensors: list[SensorReading], fans: list[FanReading]) -> None:
        """Refresh displayed values from latest data."""
        sensor_map = {s.id: s for s in sensors}
        fan_map = {f.id: f for f in fans}
        for item_id, label in self._value_labels.items():
            if item_id in sensor_map:
                label.setText(f"{sensor_map[item_id].value_c:.1f}\u00b0C")
            elif item_id in fan_map:
                f = fan_map[item_id]
                label.setText(f"{f.rpm} RPM" if f.rpm is not None else "\u2014")

    def _on_selected(self, sensor_id: str) -> None:
        self.selected_sensor_id = sensor_id

    @staticmethod
    def _make_row(
        radio: QRadioButton,
        value_text: str,
        value_label: QLabel | None = None,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(radio)
        row.addStretch()
        if value_label is None:
            value_label = QLabel(value_text)
            value_label.setProperty("class", "ValueLabel")
        row.addWidget(value_label)
        return row

    def _filter_items(self, category: str) -> list[tuple[str, str, str]]:
        """Return (id, label, formatted_value) tuples for the category."""
        results: list[tuple[str, str, str]] = []

        if category in ("cpu_temp", "gpu_temp", "mobo_temp"):
            kind_prefix = {"cpu_temp": "cpu", "gpu_temp": "gpu", "mobo_temp": "mb"}[category]
            for s in self._sensors:
                if s.kind.lower().startswith(kind_prefix):
                    results.append((s.id, s.label or s.id, f"{s.value_c:.1f}\u00b0C"))
        elif category == "fans":
            for f in self._fans:
                label = f.id
                rpm_text = f"{f.rpm} RPM" if f.rpm is not None else "\u2014"
                results.append((f.id, label, rpm_text))
        elif category == "warnings":
            # Show all sensors for warnings card
            for s in self._sensors:
                results.append((s.id, s.label or s.id, f"{s.value_c:.1f}\u00b0C"))

        return results
