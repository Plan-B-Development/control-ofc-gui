"""Curve edit dialog — for Linear, Flat, Trigger, Mix, and Sync curves."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from control_ofc.services.profile_service import MIX_FUNCTIONS, CurveConfig, CurveType
from control_ofc.ui.components.a11y import name_value_control

# Curve types whose evaluation reads a single sensor (and so show the sensor
# selector). Mix combines other curves at their own sensors and Sync mirrors a
# control's output — neither uses a sensor of its own (DEC-150/151).
_SENSOR_TYPES = (CurveType.LINEAR, CurveType.FLAT, CurveType.TRIGGER)


class CurveEditDialog(QDialog):
    """Modal dialog for editing Linear, Flat, Trigger, Mix, or Sync curves."""

    def __init__(
        self,
        curve: CurveConfig,
        sensor_items: list[tuple[str, str]] | None = None,
        *,
        mix_candidates: list[tuple[str, str]] | None = None,
        sync_candidates: list[tuple[str, str]] | None = None,
        min_output: float = 0.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Curve: {curve.name}")
        self.setMinimumWidth(400)
        self._curve = curve
        self._min_output = max(0.0, min(100.0, min_output))
        self._sensor_combo: QComboBox | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Name
        name_row = QHBoxLayout()
        name_label = QLabel("Name:")
        name_row.addWidget(name_label)
        self._name_edit = QLineEdit(curve.name)
        self._name_edit.setObjectName("CurveEditDialog_Edit_name")
        name_value_control(self._name_edit, name_label)
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        # Sensor selector — only for single-sensor types. Mix/Sync omit it.
        if curve.type in _SENSOR_TYPES:
            sensor_row = QHBoxLayout()
            sensor_label = QLabel("Sensor:")
            sensor_row.addWidget(sensor_label)
            self._sensor_combo = QComboBox()
            self._sensor_combo.setObjectName("CurveEditDialog_Combo_sensor")
            name_value_control(self._sensor_combo, sensor_label)
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
        elif curve.type == CurveType.TRIGGER:
            self._build_trigger_params(layout, curve)
        elif curve.type == CurveType.MIX:
            self._build_mix_params(layout, curve, mix_candidates or [])
        elif curve.type == CurveType.SYNC:
            self._build_sync_params(layout, curve, sync_candidates or [])

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
            field_label = QLabel(label)
            row.addWidget(field_label)
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(1)
            spin.setValue(getattr(curve, attr))
            spin.setObjectName(f"CurveEditDialog_Spin_{attr}")
            # Four identical spin boxes per curve type; the label is the only
            # thing distinguishing them (273-g).
            name_value_control(spin, field_label)
            row.addWidget(spin)
            layout.addLayout(row)
            self._param_spins[attr] = spin

    def _build_flat_params(self, layout, curve):
        layout.addWidget(QLabel("Flat Curve Parameters:"))
        row = QHBoxLayout()
        flat_label = QLabel("Output (%):")
        row.addWidget(flat_label)
        self._flat_spin = QDoubleSpinBox()
        # DEC-312: the strictest role floor across the controls using this curve,
        # matching what the embedded point editor already enforces via
        # `set_min_output`. A Flat curve is how the Fixed pump strategy is stored,
        # and without this the one pump strategy that names an explicit number was
        # the one place the number could be authored below the 30% pump floor.
        #
        # A legacy curve stored below the floor is raised to it on open, which is
        # the safe direction and is what the daemon enforces at eval time anyway —
        # the dialog stops showing a value that was never going to be honoured.
        self._flat_spin.setRange(self._min_output, 100)
        self._flat_spin.setDecimals(1)
        self._flat_spin.setValue(max(curve.flat_output_pct, self._min_output))
        self._flat_spin.setObjectName("CurveEditDialog_Spin_flatOutput")
        name_value_control(self._flat_spin, flat_label)
        row.addWidget(self._flat_spin)
        layout.addLayout(row)

    def _build_trigger_params(self, layout, curve):
        layout.addWidget(QLabel("Trigger Curve Parameters:"))
        hint = QLabel(
            "Below the idle temperature runs the idle speed; above the load "
            "temperature runs the load speed; in between it holds its current state."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        fields = [
            ("Idle Temperature (°C):", "trigger_idle_temp_c", 0, 120),
            ("Load Temperature (°C):", "trigger_load_temp_c", 0, 120),
            ("Idle Output (%):", "trigger_idle_pct", 0, 100),
            ("Load Output (%):", "trigger_load_pct", 0, 100),
        ]
        self._param_spins = {}
        for label, attr, lo, hi in fields:
            row = QHBoxLayout()
            field_label = QLabel(label)
            row.addWidget(field_label)
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(1)
            spin.setValue(getattr(curve, attr))
            spin.setObjectName(f"CurveEditDialog_Spin_{attr}")
            # Four identical spin boxes per curve type; the label is the only
            # thing distinguishing them (273-g).
            name_value_control(spin, field_label)
            row.addWidget(spin)
            layout.addLayout(row)
            self._param_spins[attr] = spin

    def _build_mix_params(self, layout, curve, candidates):
        layout.addWidget(QLabel("Mix Curve Parameters:"))
        hint = QLabel(
            "Combines other curves' outputs (each evaluated at its own sensor) "
            "into one value. Pick a function and the curves to combine."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        fn_row = QHBoxLayout()
        fn_label = QLabel("Function:")
        fn_row.addWidget(fn_label)
        self._mix_function_combo = QComboBox()
        self._mix_function_combo.setObjectName("CurveEditDialog_Combo_mixFunction")
        name_value_control(self._mix_function_combo, fn_label)
        for fn in MIX_FUNCTIONS:
            self._mix_function_combo.addItem(fn.title(), fn)
        fn_idx = self._mix_function_combo.findData(curve.mix_function)
        if fn_idx >= 0:
            self._mix_function_combo.setCurrentIndex(fn_idx)
        fn_row.addWidget(self._mix_function_combo, 1)
        layout.addLayout(fn_row)

        layout.addWidget(QLabel("Curves to combine:"))
        self._mix_list = QListWidget()
        self._mix_list.setObjectName("CurveEditDialog_List_mixCurves")
        selected = set(curve.mix_curve_ids)
        for cid, name in candidates:
            item = QListWidgetItem(name or cid)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if cid in selected else Qt.CheckState.Unchecked
            )
            self._mix_list.addItem(item)
        layout.addWidget(self._mix_list)
        if not candidates:
            empty = QLabel("No other curves available to combine.")
            empty.setProperty("class", "PageSubtitle")
            layout.addWidget(empty)

    def _build_sync_params(self, layout, curve, candidates):
        layout.addWidget(QLabel("Sync Curve Parameters:"))
        hint = QLabel(
            "Mirrors another control's output, plus an offset — keeps fans "
            "tracking a control without re-authoring its curve."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        ctrl_row = QHBoxLayout()
        ctrl_label = QLabel("Mirror control:")
        ctrl_row.addWidget(ctrl_label)
        self._sync_control_combo = QComboBox()
        self._sync_control_combo.setObjectName("CurveEditDialog_Combo_syncControl")
        name_value_control(self._sync_control_combo, ctrl_label)
        self._sync_control_combo.addItem("— Select control —", "")
        for cid, name in candidates:
            self._sync_control_combo.addItem(name or cid, cid)
        ctrl_idx = self._sync_control_combo.findData(curve.sync_control_id)
        if ctrl_idx >= 0:
            self._sync_control_combo.setCurrentIndex(ctrl_idx)
        ctrl_row.addWidget(self._sync_control_combo, 1)
        layout.addLayout(ctrl_row)

        off_row = QHBoxLayout()
        off_label = QLabel("Offset (%):")
        off_row.addWidget(off_label)
        self._sync_offset_spin = QDoubleSpinBox()
        self._sync_offset_spin.setObjectName("CurveEditDialog_Spin_syncOffset")
        name_value_control(self._sync_offset_spin, off_label)
        self._sync_offset_spin.setRange(-100, 100)
        self._sync_offset_spin.setDecimals(1)
        self._sync_offset_spin.setValue(curve.sync_offset_pct)
        off_row.addWidget(self._sync_offset_spin)
        layout.addLayout(off_row)

    def apply_to_curve(self) -> None:
        """Apply dialog values back to the curve object."""
        self._curve.name = self._name_edit.text().strip() or self._curve.name
        if self._sensor_combo is not None:
            self._curve.sensor_id = self._sensor_combo.currentData() or ""

        if self._curve.type == CurveType.LINEAR and hasattr(self, "_param_spins"):
            for attr, spin in self._param_spins.items():
                setattr(self._curve, attr, spin.value())
        elif self._curve.type == CurveType.FLAT and hasattr(self, "_flat_spin"):
            self._curve.flat_output_pct = self._flat_spin.value()
        elif self._curve.type == CurveType.TRIGGER and hasattr(self, "_param_spins"):
            for attr, spin in self._param_spins.items():
                setattr(self._curve, attr, spin.value())
        elif self._curve.type == CurveType.MIX and hasattr(self, "_mix_list"):
            self._curve.mix_function = self._mix_function_combo.currentData()
            self._curve.mix_curve_ids = [
                self._mix_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._mix_list.count())
                if self._mix_list.item(i).checkState() == Qt.CheckState.Checked
            ]
        elif self._curve.type == CurveType.SYNC and hasattr(self, "_sync_control_combo"):
            self._curve.sync_control_id = self._sync_control_combo.currentData() or ""
            self._curve.sync_offset_pct = self._sync_offset_spin.value()

    def accept(self) -> None:
        """Validate trigger thresholds before closing (idle must be below load,
        else the latch would oscillate every cycle)."""
        if self._curve.type == CurveType.TRIGGER and hasattr(self, "_param_spins"):
            idle = self._param_spins["trigger_idle_temp_c"].value()
            load = self._param_spins["trigger_load_temp_c"].value()
            if idle >= load:
                QMessageBox.warning(
                    self,
                    "Invalid Trigger Curve",
                    "Idle temperature must be below load temperature.",
                )
                return
        super().accept()
