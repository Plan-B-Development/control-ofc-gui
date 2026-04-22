"""Fan Configuration Wizard — guided fan identification and labelling.

Stops each controllable fan one at a time so the user can observe which
physical fan changed, then assign a human-readable label. Labels persist
via AppSettings.fan_aliases and propagate across the entire UI.

Uses QWizard for standard multi-step navigation with Back/Next/Finish/Cancel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import ConnectionState
from control_ofc.constants import THERMAL_ABORT_C

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.app_state import AppState
    from control_ofc.services.control_loop import ControlLoopService
    from control_ofc.services.lease_service import LeaseService

log = logging.getLogger(__name__)

# Fallback spindown duration when the wizard cannot read the controller's actual
# stop-delay.  Five seconds covers the longest seen in practice.
_FALLBACK_SPINDOWN_S = 5

_LABEL_PRESETS = [
    "",
    "CPU Cooler",
    "Rear Exhaust",
    "Front Intake Top",
    "Front Intake Bottom",
    "Top Exhaust Left",
    "Top Exhaust Right",
    "Bottom Intake",
    "Radiator Top",
    "Radiator Front",
    "Side Intake",
    "Case Fan",
    "Pump",
]

# Page IDs
PAGE_INTRO = 0
PAGE_DISCOVERY = 1
PAGE_TEST = 2
PAGE_REVIEW = 3


class FanConfigWizard(QWizard):
    """Guided wizard for identifying and labelling controllable fans."""

    labels_saved = Signal(dict)  # {fan_id: label}

    def __init__(
        self,
        state: AppState,
        client: DaemonClient | None = None,
        control_loop: ControlLoopService | None = None,
        lease_service: LeaseService | None = None,
        spindown_seconds: int = _FALLBACK_SPINDOWN_S,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fan Configuration Wizard")
        self.setMinimumSize(650, 500)

        self._state = state
        self._client = client
        self._control_loop = control_loop
        self._lease_service = lease_service
        self.spindown_seconds = max(5, min(12, spindown_seconds))

        # Build target list from current fan data
        self._targets = self._build_targets()
        self._selected_indices: list[int] = []  # set by discovery page
        self._labels: dict[str, str] = {}  # fan_id → label
        self._notes: dict[str, str] = {}  # fan_id → notes
        self._current_test_idx = 0
        self._override_active = False
        self._lease_acquired = False

        # Pages
        self._intro_page = IntroPage(state)
        self.setPage(PAGE_INTRO, self._intro_page)

        self._discovery_page = DiscoveryPage(self._targets, state)
        self.setPage(PAGE_DISCOVERY, self._discovery_page)

        self._test_page = IdentifyFanPage(self)
        self.setPage(PAGE_TEST, self._test_page)

        self._review_page = ReviewPage(self)
        self.setPage(PAGE_REVIEW, self._review_page)

    def _build_targets(self) -> list[dict]:
        targets = []
        for fan in self._state.fans:
            if not fan.rpm:
                continue  # Skip fans without RPM or with RPM=0 (empty slots)
            # Skip amdgpu hwmon entries — GPU fans use PMFW, not hwmon pwm1
            if fan.source == "hwmon" and "amdgpu" in fan.id:
                continue
            targets.append(
                {
                    "id": fan.id,
                    "source": fan.source,
                    "rpm": fan.rpm,
                    "has_tach": True,
                    "existing_label": self._state.fan_display_name(fan.id),
                    "prior_pwm": fan.last_commanded_pwm,
                }
            )
        return targets

    def nextId(self) -> int:
        current = self.currentId()
        if current == PAGE_INTRO:
            return PAGE_DISCOVERY
        if current == PAGE_DISCOVERY:
            self._selected_indices = self._discovery_page.selected_indices()
            if self._selected_indices:
                self._current_test_idx = 0
                self._enter_override()
                return PAGE_TEST
            return PAGE_REVIEW
        if current == PAGE_TEST:
            return PAGE_REVIEW
        return -1  # finish

    def current_target(self) -> dict | None:
        if not self._selected_indices:
            return None
        if self._current_test_idx >= len(self._selected_indices):
            return None
        idx = self._selected_indices[self._current_test_idx]
        return self._targets[idx]

    def advance_to_next_fan(self) -> bool:
        """Advance to the next fan target. Returns False if all fans are done."""
        self._current_test_idx += 1
        if self._current_test_idx >= len(self._selected_indices):
            return False
        self._test_page.initializePage()
        return True

    def _enter_override(self) -> None:
        if self._control_loop and not self._override_active:
            self._control_loop.set_manual_override(True)
            self._override_active = True
            log.info("Wizard: manual override activated")
        # Acquire lease for hwmon targets
        has_hwmon = any(self._targets[i]["source"] != "openfan" for i in self._selected_indices)
        if has_hwmon and self._lease_service and not self._lease_acquired:
            self._lease_service.acquire()
            self._lease_acquired = True
            log.info("Wizard: hwmon lease acquired")

    def _exit_override(self) -> None:
        # Restore all fans to 100% as safety measure
        self._restore_all_fans()
        if self._control_loop and self._override_active:
            self._control_loop.set_manual_override(False)
            self._override_active = False
            log.info("Wizard: manual override deactivated")
        if self._lease_service and self._lease_acquired:
            self._lease_service.release()
            self._lease_acquired = False
            log.info("Wizard: hwmon lease released")

    def _restore_all_fans(self) -> None:
        """Restore all fans to their prior PWM (fallback: 30%)."""
        if not self._client:
            return
        for target in self._targets:
            try:
                self.restore_fan(target)
            except DaemonError as e:
                log.warning("Failed to restore fan %s: %s", target["id"], e.message)

    @staticmethod
    def _parse_openfan_channel(fan_id: str) -> int | None:
        if fan_id.startswith("openfan:ch"):
            try:
                return int(fan_id[len("openfan:ch") :])
            except ValueError:
                pass
        return None

    def stop_fan(self, target: dict) -> str | None:
        """Stop a single fan for identification. Returns error message or None on success."""
        if not self._client:
            return "No daemon client available"
        fan_id = target["id"]
        log.info("Wizard: stopping fan %s for identification", fan_id)
        try:
            if target["source"] == "openfan":
                ch = self._parse_openfan_channel(fan_id)
                if ch is not None:
                    self._client.set_openfan_pwm(ch, 0)
                else:
                    return f"Cannot parse OpenFan channel from {fan_id}"
            elif target["source"] == "amd_gpu":
                gpu_id = fan_id.removeprefix("amd_gpu:")
                self._client.set_gpu_fan_speed(gpu_id, 0)
            else:
                # hwmon — need lease
                if self._lease_service and self._lease_service.is_held:
                    self._client.set_hwmon_pwm(fan_id, 0, self._lease_service.lease_id)
                else:
                    return "hwmon lease not held — cannot stop fan"
            return None
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Failed to stop fan %s: %s", fan_id, e)
            return str(e)

    def restore_fan(self, target: dict) -> None:
        """Restore a fan to its prior PWM after identification (fallback: 30%)."""
        if not self._client:
            return
        fan_id = target["id"]
        restore_pct = target.get("prior_pwm") or 30
        log.info("Wizard: restoring fan %s to %d%%", fan_id, restore_pct)
        try:
            if target["source"] == "openfan":
                ch = self._parse_openfan_channel(fan_id)
                if ch is not None:
                    self._client.set_openfan_pwm(ch, restore_pct)
            elif target["source"] == "amd_gpu":
                gpu_id = fan_id.removeprefix("amd_gpu:")
                self._client.set_gpu_fan_speed(gpu_id, restore_pct)
            else:
                if self._lease_service and self._lease_service.is_held:
                    self._client.set_hwmon_pwm(fan_id, restore_pct, self._lease_service.lease_id)
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Failed to restore fan %s: %s", fan_id, e)

    def check_thermal_safe(self) -> bool:
        """Check if any CPU sensor exceeds the thermal abort threshold."""
        for s in self._state.sensors:
            if s.kind.lower().startswith("cpu") and s.value_c > THERMAL_ABORT_C:
                return False
        return True

    def accept(self) -> None:
        """Save labels and clean up on Finish."""
        self._exit_override()
        # Save labels
        for fan_id, label in self._labels.items():
            if label:
                self._state.set_fan_alias(fan_id, label)
        log.info("Wizard: saved %d fan label(s)", len(self._labels))
        self.labels_saved.emit(dict(self._labels))
        super().accept()

    def reject(self) -> None:
        """Clean up on Cancel — restore fans but don't save labels."""
        self._exit_override()
        log.info("Wizard: cancelled, labels not saved")
        super().reject()

    def done(self, result: int) -> None:
        """Ensure cleanup happens regardless of how the wizard closes."""
        if self._override_active:
            self._exit_override()
        super().done(result)


class IntroPage(QWizardPage):
    """Page 1: Warning and pre-flight checks."""

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Fan Configuration Wizard")
        self.setSubTitle("Identify and label your fans")
        self._state = state

        layout = QVBoxLayout(self)

        warning = QLabel(
            "This wizard will <b>stop each fan one at a time</b> for several "
            "seconds so you can observe which physical fan changed.\n\n"
            "• Your system should be <b>idle and cool</b> before starting.\n"
            "• You can <b>abort at any time</b> — fans will be restored to their prior speed.\n"
            "• Only one fan is tested at a time.\n"
            "• Temperature is monitored during each test for safety."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._status_label)
        layout.addStretch()

    def initializePage(self) -> None:
        errors = []
        if self._state.connection != ConnectionState.CONNECTED:
            errors.append("Daemon is not connected.")
        if not self._state.fans:
            errors.append("No controllable fan outputs detected.")
        for s in self._state.sensors:
            if s.kind.lower().startswith("cpu") and s.value_c > THERMAL_ABORT_C:
                errors.append(
                    f"CPU temperature too high ({s.value_c:.1f}°C > {THERMAL_ABORT_C}°C)."
                )
                break

        if errors:
            self._status_label.setText("Cannot proceed:\n• " + "\n• ".join(errors))
            self._status_label.setProperty("class", "CriticalChip")
        else:
            fan_count = len(self._state.fans)
            self._status_label.setText(f"Ready — {fan_count} controllable fan(s) detected.")
            self._status_label.setProperty("class", "SuccessChip")

    def isComplete(self) -> bool:
        if self._state.connection != ConnectionState.CONNECTED:
            return False
        return bool(self._state.fans)


class DiscoveryPage(QWizardPage):
    """Page 2: Show all targets with checkboxes."""

    def __init__(self, targets: list[dict], state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Detected Fans")
        self.setSubTitle("Select which fans to identify")
        self._targets = targets
        self._state = state
        self._checkboxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)

        # Select all/none
        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.setObjectName("Wizard_Btn_selectAll")
        select_all.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(select_all)
        select_none = QPushButton("Select None")
        select_none.setObjectName("Wizard_Btn_selectNone")
        select_none.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Target table with checkboxes
        self._table = QTableWidget(len(targets), 5)
        self._table.setHorizontalHeaderLabels(["", "ID", "Source", "RPM", "Current Label"])
        self._table.verticalHeader().setVisible(False)
        self._table.setObjectName("Wizard_Table_targets")
        from PySide6.QtWidgets import QHeaderView

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)

        for i, t in enumerate(targets):
            cb = QCheckBox()
            cb.setChecked(True)
            self._checkboxes.append(cb)
            self._table.setCellWidget(i, 0, cb)
            self._table.setItem(i, 1, QTableWidgetItem(t["id"]))
            self._table.setItem(i, 2, QTableWidgetItem(t["source"]))
            rpm_text = str(t["rpm"]) if t["rpm"] is not None else "N/A"
            self._table.setItem(i, 3, QTableWidgetItem(rpm_text))
            self._table.setItem(i, 4, QTableWidgetItem(t["existing_label"]))

        layout.addWidget(self._table, 1)

    def _set_all(self, checked: bool) -> None:
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def selected_indices(self) -> list[int]:
        return [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]


class IdentifyFanPage(QWizardPage):
    """Page 3: Test one fan at a time with countdown and label input."""

    def __init__(self, wizard: FanConfigWizard, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Identify Fan")
        self._wizard = wizard
        self._testing = False
        self._seconds_remaining = 0

        layout = QVBoxLayout(self)

        # Target info
        self._target_label = QLabel("")
        self._target_label.setProperty("class", "SectionTitle")
        layout.addWidget(self._target_label)

        self._source_label = QLabel("")
        self._source_label.setProperty("class", "PageSubtitle")
        layout.addWidget(self._source_label)

        # Test controls
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Start Test")
        self._test_btn.setObjectName("Wizard_Btn_startTest")
        self._test_btn.clicked.connect(self._start_test)
        test_row.addWidget(self._test_btn)

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setObjectName("Wizard_Btn_abort")
        self._abort_btn.clicked.connect(self._abort_test)
        self._abort_btn.setEnabled(False)
        test_row.addWidget(self._abort_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, self._wizard.spindown_seconds)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m seconds")
        layout.addWidget(self._progress)

        self._rpm_label = QLabel("RPM: —")
        layout.addWidget(self._rpm_label)

        self._status_msg = QLabel("")
        self._status_msg.setWordWrap(True)
        layout.addWidget(self._status_msg)

        # Label input
        label_group = QVBoxLayout()
        label_group.addWidget(QLabel("Assign a label for this fan:"))

        label_row = QHBoxLayout()
        self._label_combo = QComboBox()
        self._label_combo.setObjectName("Wizard_Combo_labelPreset")
        self._label_combo.setEditable(True)
        for preset in _LABEL_PRESETS:
            self._label_combo.addItem(preset)
        label_row.addWidget(self._label_combo, 1)
        label_group.addLayout(label_row)

        self._multi_cb = QCheckBox("Multiple physical fans moved (splitter/hub)")
        self._multi_cb.setObjectName("Wizard_Check_multiFan")
        label_group.addWidget(self._multi_cb)

        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("Notes:"))
        self._notes_edit = QLineEdit()
        self._notes_edit.setObjectName("Wizard_Edit_notes")
        self._notes_edit.setPlaceholderText("Optional notes (e.g., 'controls 3 fans via splitter')")
        notes_row.addWidget(self._notes_edit, 1)
        label_group.addLayout(notes_row)
        layout.addLayout(label_group)

        # Fan cycling buttons
        nav_row = QHBoxLayout()
        self._next_fan_btn = QPushButton("Save Label && Next Fan")
        self._next_fan_btn.setObjectName("Wizard_Btn_nextFan")
        self._next_fan_btn.clicked.connect(self._next_fan)
        nav_row.addWidget(self._next_fan_btn)

        self._skip_btn = QPushButton("Skip — couldn't identify")
        self._skip_btn.setObjectName("Wizard_Btn_skip")
        self._skip_btn.clicked.connect(self._skip_target)
        nav_row.addWidget(self._skip_btn)

        nav_row.addStretch()
        layout.addLayout(nav_row)

        self._all_done = False

        layout.addStretch()

        # Timer
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def isComplete(self) -> bool:
        return self._all_done

    def initializePage(self) -> None:
        target = self._wizard.current_target()
        if not target:
            return
        idx = self._wizard._current_test_idx
        total = len(self._wizard._selected_indices)
        self._target_label.setText(f"Testing fan {idx + 1} of {total}: {target['id']}")
        self._source_label.setText(
            f"Source: {target['source']} | "
            f"RPM: {target['rpm'] if target['rpm'] is not None else 'N/A'} | "
            f"Current label: {target['existing_label']}"
        )
        self._progress.setValue(0)
        self._rpm_label.setText("RPM: —")
        self._status_msg.setText("Press 'Start Test' to stop this fan for identification.")
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._label_combo.setCurrentText("")
        self._multi_cb.setChecked(False)
        self._notes_edit.clear()
        self._testing = False
        self._all_done = False
        self._next_fan_btn.setVisible(True)
        self.completeChanged.emit()

    def cleanupPage(self) -> None:
        """Called when user presses Back — restore fan if testing."""
        if self._testing:
            self._abort_test()

    def _start_test(self) -> None:
        target = self._wizard.current_target()
        if not target:
            return
        # Thermal check
        if not self._wizard.check_thermal_safe():
            self._status_msg.setText(
                f"ABORTED: CPU temperature exceeds {THERMAL_ABORT_C}°C — too hot to test safely."
            )
            self._status_msg.setProperty("class", "CriticalChip")
            return

        self._testing = True
        self._seconds_remaining = self._wizard.spindown_seconds
        self._progress.setValue(0)
        self._test_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._status_msg.setStyleSheet("")

        error = self._wizard.stop_fan(target)
        if error:
            self._testing = False
            self._test_btn.setEnabled(True)
            self._abort_btn.setEnabled(False)
            self._status_msg.setText(f"Failed to stop fan: {error}")
            self._status_msg.setProperty("class", "CriticalChip")
            log.warning("Wizard: test failed to start for %s: %s", target["id"], error)
            return

        self._status_msg.setText("Fan stopped — observe which physical fan changed...")
        self._timer.start()
        log.info("Wizard: test started for %s (%ds)", target["id"], self._wizard.spindown_seconds)

    def _tick(self) -> None:
        self._seconds_remaining -= 1
        elapsed = self._wizard.spindown_seconds - self._seconds_remaining
        self._progress.setValue(elapsed)

        # Update RPM from live state
        target = self._wizard.current_target()
        if target:
            for fan in self._wizard._state.fans:
                if fan.id == target["id"]:
                    rpm_text = str(fan.rpm) if fan.rpm is not None else "N/A"
                    self._rpm_label.setText(f"RPM: {rpm_text}")
                    break

        # Thermal check during test
        if not self._wizard.check_thermal_safe():
            self._abort_test()
            self._status_msg.setText(
                f"ABORTED: CPU temperature exceeded {THERMAL_ABORT_C}°C during test."
            )
            self._status_msg.setProperty("class", "CriticalChip")
            return

        if self._seconds_remaining <= 0:
            self._end_test()

    def _end_test(self) -> None:
        self._timer.stop()
        self._testing = False
        target = self._wizard.current_target()
        if target:
            self._wizard.restore_fan(target)
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._progress.setValue(self._wizard.spindown_seconds)
        self._status_msg.setText("Fan restored. Enter a label below, then click Next.")
        log.info("Wizard: test completed for %s", target["id"] if target else "unknown")

    def _abort_test(self) -> None:
        self._timer.stop()
        self._testing = False
        target = self._wizard.current_target()
        if target:
            self._wizard.restore_fan(target)
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._status_msg.setText("Test aborted — fan restored.")
        log.info("Wizard: test aborted for %s", target["id"] if target else "unknown")

    def _save_current_label(self) -> None:
        """Save the label and notes for the current fan target."""
        target = self._wizard.current_target()
        if target:
            label = self._label_combo.currentText().strip()
            if label:
                self._wizard._labels[target["id"]] = label
            notes = self._notes_edit.text().strip()
            if self._multi_cb.isChecked():
                notes = f"[splitter/hub] {notes}".strip()
            if notes:
                self._wizard._notes[target["id"]] = notes

    def _next_fan(self) -> None:
        """Save label for current fan, advance to next fan or finish."""
        if self._testing:
            self._abort_test()
        self._save_current_label()
        if not self._wizard.advance_to_next_fan():
            # All fans tested — enable QWizard's Next button to go to Review
            self._all_done = True
            self._next_fan_btn.setVisible(False)
            self._skip_btn.setVisible(False)
            self._status_msg.setText("All fans tested. Click Next to review labels.")
            self.completeChanged.emit()

    def _skip_target(self) -> None:
        """Skip this fan without saving a label."""
        if self._testing:
            self._abort_test()
        if not self._wizard.advance_to_next_fan():
            self._all_done = True
            self._next_fan_btn.setVisible(False)
            self._skip_btn.setVisible(False)
            self._status_msg.setText("All fans tested. Click Next to review labels.")
            self.completeChanged.emit()

    def validatePage(self) -> bool:
        self._save_current_label()
        return True


class ReviewPage(QWizardPage):
    """Page 4: Review all labels before saving."""

    def __init__(self, wizard: FanConfigWizard, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Review Labels")
        self.setSubTitle("Review and edit labels before saving")
        self._wizard = wizard

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Source", "New Label", "Notes"])
        self._table.setObjectName("Wizard_Table_review")
        from PySide6.QtWidgets import QHeaderView

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 1)

    def initializePage(self) -> None:
        targets = self._wizard._targets
        selected = self._wizard._selected_indices
        labels = self._wizard._labels
        notes = self._wizard._notes

        rows = [targets[i] for i in selected] if selected else targets
        self._table.setRowCount(len(rows))

        for i, t in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(t["id"]))
            self._table.setItem(i, 1, QTableWidgetItem(t["source"]))
            label = labels.get(t["id"], "")
            item = QTableWidgetItem(label)
            self._table.setItem(i, 2, item)
            note = notes.get(t["id"], "")
            self._table.setItem(i, 3, QTableWidgetItem(note))

    def validatePage(self) -> bool:
        # Read back any edits the user made in the review table
        targets = self._wizard._targets
        selected = self._wizard._selected_indices
        rows = [targets[i] for i in selected] if selected else targets

        for i, t in enumerate(rows):
            label_item = self._table.item(i, 2)
            if label_item:
                label = label_item.text().strip()
                if label:
                    self._wizard._labels[t["id"]] = label
                elif t["id"] in self._wizard._labels:
                    del self._wizard._labels[t["id"]]
        return True
