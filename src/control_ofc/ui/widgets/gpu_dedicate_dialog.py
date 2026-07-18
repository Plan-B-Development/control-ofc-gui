"""One-click "Dedicate GPU Fan" dialog (DEC-221).

Gathers the user's intent to give a writable AMD GPU fan its own dedicated
control + curve with the firmware zero-RPM idle-stop enabled, so the fan can sit
at true 0 RPM when the GPU is cool. The actual control/curve creation is done by
``profile_service.build_gpu_control`` so this stays a thin, testable UI layer.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class GpuDedicateDialog(QDialog):
    """Collect a GPU temperature sensor + zero-RPM opt-in for a dedicated GPU fan."""

    def __init__(
        self,
        *,
        gpu_label: str,
        sensor_choices: list[dict],  # [{id, label, preferred}]
        default_sensor_id: str | None = None,
        default_zero_rpm: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GpuDedicateDialog")
        self.setWindowTitle("Dedicate GPU Fan")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Give {gpu_label} its own fan curve so it can idle at 0 RPM when the GPU "
            "is cool. This creates a dedicated GPU-only control — its curve is "
            "authorable down to 0%, with no chassis or CPU minimum applied."
        )
        intro.setObjectName("GpuDedicate_Label_intro")
        intro.setWordWrap(True)
        intro.setProperty("class", "PageSubtitle")
        layout.addWidget(intro)

        # ── Sensor section ────────────────────────────────────────────
        sensor_group = QGroupBox("Temperature sensor")
        sensor_group.setObjectName("GpuDedicate_Group_sensor")
        sensor_layout = QVBoxLayout(sensor_group)
        sensor_layout.addWidget(QLabel("Bind the GPU fan curve to:"))
        self._sensor_combo = QComboBox()
        self._sensor_combo.setObjectName("GpuDedicate_Combo_sensor")
        for ch in sensor_choices:
            prefix = "★ " if ch.get("preferred") else ""
            self._sensor_combo.addItem(f"{prefix}{ch['label']}", ch["id"])
        if default_sensor_id:
            idx = self._sensor_combo.findData(default_sensor_id)
            if idx >= 0:
                self._sensor_combo.setCurrentIndex(idx)
        if not sensor_choices:
            empty = QLabel("No temperature sensors available to bind yet.")
            empty.setObjectName("GpuDedicate_Label_noSensors")
            empty.setProperty("class", "PageSubtitle")
            sensor_layout.addWidget(empty)
        sensor_note = QLabel(
            "★ A GPU temperature (edge or junction) is recommended so the fan "
            "tracks the GPU's own heat."
        )
        sensor_note.setObjectName("GpuDedicate_Label_sensorNote")
        sensor_note.setWordWrap(True)
        sensor_note.setProperty("class", "PageSubtitle")
        sensor_layout.addWidget(self._sensor_combo)
        sensor_layout.addWidget(sensor_note)
        layout.addWidget(sensor_group)

        # ── Zero-RPM section ──────────────────────────────────────────
        zero_group = QGroupBox("Zero-RPM idle")
        zero_group.setObjectName("GpuDedicate_Group_zeroRpm")
        zero_layout = QVBoxLayout(zero_group)
        self._zero_rpm_check = QCheckBox("Let the fan stop completely when the GPU is cool")
        self._zero_rpm_check.setObjectName("GpuDedicate_Check_zeroRpm")
        self._zero_rpm_check.setChecked(default_zero_rpm)
        zero_layout.addWidget(self._zero_rpm_check)
        zero_note = QLabel(
            "Enables the GPU firmware's zero-RPM stop so the fan sits at true 0 RPM "
            "below the GPU's idle temperature, then spins up as the curve ramps. The "
            "daemon restores automatic zero-RPM control on shutdown. Turn this off to "
            "keep the fan always spinning at the firmware minimum."
        )
        zero_note.setObjectName("GpuDedicate_Label_zeroRpmNote")
        zero_note.setWordWrap(True)
        zero_note.setProperty("class", "PageSubtitle")
        zero_layout.addWidget(zero_note)
        layout.addWidget(zero_group)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Create control")
        ok_btn.setObjectName("GpuDedicate_Btn_create")
        # A GPU fan curve must bind to a sensor — block confirmation when there
        # are none (e.g. dialog opened before the first sensor poll landed), so
        # the flow can't produce a sensorless, never-evaluated curve.
        ok_btn.setEnabled(bool(sensor_choices))
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("GpuDedicate_Btn_cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def get_result(self) -> dict:
        """Return the chosen setup (call after ``exec()`` returns accepted).

        ``sensor_id`` may be ``""`` when no sensor was available; ``zero_rpm`` is
        the checkbox state.
        """
        return {
            "sensor_id": self._sensor_combo.currentData() or "",
            "zero_rpm": self._zero_rpm_check.isChecked(),
        }
