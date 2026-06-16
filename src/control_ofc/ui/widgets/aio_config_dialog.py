"""One-click "Configure AIO" dialog (DEC-157).

Gathers the user's intent for a guided AIO setup — a constant pump speed and a
radiator-fan group bound to a sensor — and returns it. The actual control/curve
creation is done by ``profile_service.build_aio_controls`` so this stays a thin,
testable UI layer. A pump runs at a CONSTANT speed (never a temperature curve);
read-only / monitor-only coolers degrade gracefully (no pump section).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from control_ofc.services.profile_service import AIO_PUMP_DEFAULT_PCT, AIO_PUMP_PRESETS


class AioConfigDialog(QDialog):
    """Collect a pump speed + radiator fans + radiator sensor for AIO setup."""

    def __init__(
        self,
        *,
        pump_label: str | None,
        monitor_only: bool,
        fan_candidates: list[dict],  # [{id, source, label, preselect}]
        sensor_choices: list[dict],  # [{id, label, preferred}]
        default_sensor_id: str | None = None,
        default_pump_pct: int = AIO_PUMP_DEFAULT_PCT,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AioConfigDialog")
        self.setWindowTitle("Configure AIO")
        self.setMinimumWidth(460)

        self._pump_buttons = QButtonGroup(self)
        self._has_pump = bool(pump_label) and not monitor_only

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Set up your liquid cooler in one step: a constant-speed pump and a "
            "radiator-fan group bound to a temperature sensor."
        )
        intro.setObjectName("AioConfig_Label_intro")
        intro.setWordWrap(True)
        intro.setProperty("class", "PageSubtitle")
        layout.addWidget(intro)

        # ── Pump section ──────────────────────────────────────────────
        pump_group = QGroupBox("Pump")
        pump_group.setObjectName("AioConfig_Group_pump")
        pump_layout = QVBoxLayout(pump_group)
        if self._has_pump:
            pump_layout.addWidget(QLabel(f"Detected pump: {pump_label}"))
            note = QLabel("Pumps run best at a constant speed, not a temperature curve.")
            note.setObjectName("AioConfig_Label_pumpNote")
            note.setWordWrap(True)
            note.setProperty("class", "PageSubtitle")
            pump_layout.addWidget(note)
            row = QHBoxLayout()
            for name, pct in AIO_PUMP_PRESETS:
                rb = QRadioButton(f"{name} ({pct}%)")
                rb.setObjectName(f"AioConfig_Radio_pump{pct}")
                rb.setProperty("pump_pct", pct)
                if pct == default_pump_pct:
                    rb.setChecked(True)
                self._pump_buttons.addButton(rb, pct)
                row.addWidget(rb)
            pump_layout.addLayout(row)
        else:
            mon = QLabel(
                "No controllable pump detected (monitor-only cooler). The coolant "
                "temperature is shown for monitoring; control the pump with your "
                "cooler's vendor tooling."
            )
            mon.setObjectName("AioConfig_Label_monitorOnly")
            mon.setWordWrap(True)
            pump_layout.addWidget(mon)
        layout.addWidget(pump_group)

        # ── Radiator section ──────────────────────────────────────────
        rad_group = QGroupBox("Radiator fans")
        rad_group.setObjectName("AioConfig_Group_radiator")
        rad_layout = QVBoxLayout(rad_group)
        rad_layout.addWidget(QLabel("Include these fans in the radiator group:"))
        self._fan_list = QListWidget()
        self._fan_list.setObjectName("AioConfig_List_radiatorFans")
        self._fan_list.setMaximumHeight(140)
        for cand in fan_candidates:
            item = QListWidgetItem(f"[{cand['source']}] {cand['label']}")
            item.setData(Qt.ItemDataRole.UserRole, cand)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if cand.get("preselect") else Qt.CheckState.Unchecked
            )
            self._fan_list.addItem(item)
        if not fan_candidates:
            empty = QLabel("No controllable fans available to assign yet.")
            empty.setProperty("class", "PageSubtitle")
            rad_layout.addWidget(empty)
        rad_layout.addWidget(self._fan_list)

        rad_layout.addWidget(QLabel("Bind the radiator-fan curve to:"))
        self._sensor_combo = QComboBox()
        self._sensor_combo.setObjectName("AioConfig_Combo_radiatorSensor")
        for ch in sensor_choices:
            prefix = "★ " if ch.get("preferred") else ""
            self._sensor_combo.addItem(f"{prefix}{ch['label']}", ch["id"])
        if default_sensor_id:
            idx = self._sensor_combo.findData(default_sensor_id)
            if idx >= 0:
                self._sensor_combo.setCurrentIndex(idx)
        sensor_note = QLabel(
            "★ Coolant temperature is recommended — the radiator's job is to "
            "cool the loop. CPU temperature also works but is spikier."
        )
        sensor_note.setObjectName("AioConfig_Label_sensorNote")
        sensor_note.setWordWrap(True)
        sensor_note.setProperty("class", "PageSubtitle")
        rad_layout.addWidget(self._sensor_combo)
        rad_layout.addWidget(sensor_note)
        layout.addWidget(rad_group)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Create controls")
        ok_btn.setObjectName("AioConfig_Btn_create")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("AioConfig_Btn_cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def get_result(self) -> dict:
        """Return the chosen setup (call after ``exec()`` returns accepted).

        ``pump_pct`` is ``None`` for a monitor-only cooler; ``radiator_members``
        is the list of checked fan dicts; ``radiator_sensor_id`` may be ``""``.
        """
        pump_pct: int | None = None
        if self._has_pump:
            checked = self._pump_buttons.checkedButton()
            if checked is not None:
                pump_pct = int(checked.property("pump_pct"))

        radiator_members: list[dict] = []
        for i in range(self._fan_list.count()):
            item = self._fan_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                radiator_members.append(item.data(Qt.ItemDataRole.UserRole))

        return {
            "pump_pct": pump_pct,
            "radiator_members": radiator_members,
            "radiator_sensor_id": self._sensor_combo.currentData() or "",
        }
