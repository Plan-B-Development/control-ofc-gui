"""Curve edit dialog — for Linear and Flat curve parameter editing."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from onlyfans.services.profile_service import CurveConfig, CurveType


class CurveEditDialog(QDialog):
    """Modal dialog for editing Linear or Flat curve parameters."""

    def __init__(
        self, curve: CurveConfig, sensor_items: list[tuple[str, str]] | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Curve: {curve.name}")
        self.setMinimumWidth(400)
        self._curve = curve

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(curve.name)
        self._name_edit.setObjectName("CurveEditDialog_Edit_name")
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        # Sensor selector
        sensor_row = QHBoxLayout()
        sensor_row.addWidget(QLabel("Sensor:"))
        self._sensor_combo = QComboBox()
        self._sensor_combo.setObjectName("CurveEditDialog_Combo_sensor")
        if sensor_items:
            for sid, label in sensor_items:
                self._sensor_combo.addItem(label, sid)
            if curve.sensor_id:
                idx = self._sensor_combo.findData(curve.sensor_id)
                if idx >= 0:
                    self._sensor_combo.setCurrentIndex(idx)
        sensor_row.addWidget(self._sensor_combo, 1)
        layout.addLayout(sensor_row)

        # Type-specific parameters
        if curve.type == CurveType.LINEAR:
            self._build_linear_params(layout, curve)
        elif curve.type == CurveType.FLAT:
            self._build_flat_params(layout, curve)

        layout.addStretch()

        # Save/Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("CurveEditDialog_Btn_save")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _build_linear_params(self, layout, curve):
        layout.addWidget(QLabel("Linear Curve Parameters:"))
        fields = [
            ("Start Temperature (\u00b0C):", "start_temp_c", 0, 120),
            ("Start Output (%):", "start_output_pct", 0, 100),
            ("End Temperature (\u00b0C):", "end_temp_c", 0, 120),
            ("End Output (%):", "end_output_pct", 0, 100),
        ]
        self._param_spins = {}
        for label, attr, lo, hi in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(1)
            spin.setValue(getattr(curve, attr))
            spin.setObjectName(f"CurveEditDialog_Spin_{attr}")
            row.addWidget(spin)
            layout.addLayout(row)
            self._param_spins[attr] = spin

    def _build_flat_params(self, layout, curve):
        layout.addWidget(QLabel("Flat Curve Parameters:"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Output (%):"))
        self._flat_spin = QDoubleSpinBox()
        self._flat_spin.setRange(0, 100)
        self._flat_spin.setDecimals(1)
        self._flat_spin.setValue(curve.flat_output_pct)
        self._flat_spin.setObjectName("CurveEditDialog_Spin_flatOutput")
        row.addWidget(self._flat_spin)
        layout.addLayout(row)

    def apply_to_curve(self) -> None:
        """Apply dialog values back to the curve object."""
        self._curve.name = self._name_edit.text().strip() or self._curve.name
        self._curve.sensor_id = self._sensor_combo.currentData() or ""

        if self._curve.type == CurveType.LINEAR and hasattr(self, "_param_spins"):
            for attr, spin in self._param_spins.items():
                setattr(self._curve, attr, spin.value())
        elif self._curve.type == CurveType.FLAT and hasattr(self, "_flat_spin"):
            self._curve.flat_output_pct = self._flat_spin.value()
