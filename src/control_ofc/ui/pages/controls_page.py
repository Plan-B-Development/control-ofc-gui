"""Controls page — FanControl-style 2-section layout.

Section A (top): Control cards grid — logical controls with mode, curve, output.
Section B (bottom): Curve cards grid + expandable curve editor.
Profile bar at top for profile management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    ProfileService,
)
from control_ofc.ui.microcopy import get as mc
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard
from control_ofc.ui.widgets.curve_editor import CurveEditor
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.control_loop import ControlLoopService
    from control_ofc.services.lease_service import LeaseService


class ControlsPage(QWidget):
    """FanControl-style controls: profile bar, control cards grid, curve cards grid."""

    profile_activated = Signal(str)

    _log = logging.getLogger(__name__)

    def __init__(
        self,
        state: AppState | None = None,
        profile_service: ProfileService | None = None,
        client: DaemonClient | None = None,
        control_loop: ControlLoopService | None = None,
        lease_service: LeaseService | None = None,
        settings_service: AppSettingsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._profile_service = profile_service or ProfileService()
        self._client = client
        self._control_loop = control_loop
        self._lease_service = lease_service
        self._settings_service = settings_service
        self._control_cards: dict[str, ControlCard] = {}
        self._curve_cards: dict[str, CurveCard] = {}
        self._selected_control_id: str | None = None
        self._has_unsaved: bool = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(8)

        # ─── Profile bar (top) ───────────────────────────────────────
        main_layout.addLayout(self._build_profile_bar())

        # ─── Splitter: Fan Roles (top) / Curves (bottom) ─────────────
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setObjectName("Controls_Splitter_sections")

        # ─── Top pane: Fan Roles ──────────────────────────────────────
        top_pane = QWidget()
        top_layout = QVBoxLayout(top_pane)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        controls_header = QHBoxLayout()
        controls_title = QLabel("Fan Roles")
        controls_title.setProperty("class", "PageTitle")
        controls_header.addWidget(controls_title)
        controls_header.addStretch()
        self._wizard_btn = QPushButton("Fan Wizard")
        self._wizard_btn.setObjectName("Controls_Btn_fanWizard")
        self._wizard_btn.setToolTip("Identify and label your fans")
        self._wizard_btn.clicked.connect(self._on_fan_wizard)
        controls_header.addWidget(self._wizard_btn)

        self._add_control_btn = QPushButton("+ Fan Role")
        self._add_control_btn.setObjectName("Controls_Btn_newControl")
        self._add_control_btn.setToolTip("Create a new fan role")
        self._add_control_btn.clicked.connect(self._on_new_control_menu)
        controls_header.addWidget(self._add_control_btn)
        top_layout.addLayout(controls_header)

        self._controls_empty = QLabel("No fan roles configured. Click + Fan Role to create one.")
        self._controls_empty.setProperty("class", "PageSubtitle")
        self._controls_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._controls_empty.setWordWrap(True)
        top_layout.addWidget(self._controls_empty)

        self._controls_scroll = QScrollArea()
        self._controls_scroll.setWidgetResizable(True)
        self._controls_flow = DraggableFlowContainer()
        self._controls_flow.order_changed.connect(self._on_controls_reordered)
        self._controls_scroll.setWidget(self._controls_flow)
        top_layout.addWidget(self._controls_scroll, 1)

        top_pane.setMinimumHeight(120)
        self._splitter.addWidget(top_pane)

        # ─── Bottom pane: Curves (with drag-to-reorder) ──────────────
        self._curves_section = QWidget()
        curves_section_layout = QVBoxLayout(self._curves_section)
        curves_section_layout.setContentsMargins(0, 0, 0, 0)
        curves_section_layout.setSpacing(8)

        curves_header = QHBoxLayout()
        curves_title = QLabel("Curves")
        curves_title.setProperty("class", "PageTitle")
        curves_header.addWidget(curves_title)
        curves_header.addStretch()

        self._add_curve_btn = QPushButton("+ Curve")
        self._add_curve_btn.setObjectName("Controls_Btn_addCurve")
        self._add_curve_btn.setToolTip("Add a new curve to the library")
        self._add_curve_btn.clicked.connect(self._on_add_curve_menu)
        curves_header.addWidget(self._add_curve_btn)
        curves_section_layout.addLayout(curves_header)

        # Curves empty state
        self._curves_empty = QLabel("No curves. Click + Curve to create one.")
        self._curves_empty.setProperty("class", "PageSubtitle")
        self._curves_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        curves_section_layout.addWidget(self._curves_empty)

        # Curves flow container (draggable fixed-size cards)
        self._curves_scroll = QScrollArea()
        self._curves_scroll.setWidgetResizable(True)
        self._curves_flow = DraggableFlowContainer()
        self._curves_flow.order_changed.connect(self._on_curves_reordered)
        self._curves_scroll.setWidget(self._curves_flow)

        # Curve editor (hidden by default, shown when editing a curve)
        self._editor_frame = QWidget()
        editor_layout = QVBoxLayout(self._editor_frame)
        editor_layout.setContentsMargins(0, 4, 0, 0)

        editor_header = QHBoxLayout()
        self._editor_title = QLabel("Editing: \u2014")
        self._editor_title.setStyleSheet("font-weight: bold;")
        editor_header.addWidget(self._editor_title)
        editor_header.addStretch()
        close_editor_btn = QPushButton("Close Editor")
        close_editor_btn.setObjectName("Controls_Btn_closeEditor")
        close_editor_btn.clicked.connect(self._close_editor)
        editor_header.addWidget(close_editor_btn)
        editor_layout.addLayout(editor_header)

        self._curve_editor = CurveEditor()
        self._curve_editor.setObjectName("Controls_CurveEditor_main")
        self._curve_editor.curve_changed.connect(self._on_curve_changed)
        editor_layout.addWidget(self._curve_editor, 1)

        self._editor_frame.hide()

        # Splitter between curves grid and editor — user can drag to adjust height
        self._curves_editor_splitter = QSplitter(Qt.Orientation.Vertical)
        self._curves_editor_splitter.setObjectName("Controls_Splitter_curvesEditor")
        self._curves_editor_splitter.addWidget(self._curves_scroll)
        self._curves_editor_splitter.addWidget(self._editor_frame)
        self._curves_editor_splitter.setStretchFactor(0, 1)
        self._curves_editor_splitter.setStretchFactor(1, 1)
        self._curves_editor_splitter.setSizes([300, 300])
        self._curves_editor_splitter.setCollapsible(0, False)
        self._curves_editor_splitter.setCollapsible(1, False)
        curves_section_layout.addWidget(self._curves_editor_splitter, 1)

        self._curves_section.setMinimumHeight(120)
        self._splitter.addWidget(self._curves_section)

        # Splitter sizing
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([250, 450])
        main_layout.addWidget(self._splitter, 1)

        # No-controls guidance (shown when curves section is hidden)
        self._no_controls_hint = QLabel(
            "Create a Fan Role first. Curves are assigned to Fan Roles."
        )
        self._no_controls_hint.setProperty("class", "PageSubtitle")
        self._no_controls_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_controls_hint.setWordWrap(True)
        self._no_controls_hint.hide()
        main_layout.addWidget(self._no_controls_hint)

        # ─── Keyboard shortcuts ──────────────────────────────────────
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save_profile)

        # ─── Populate ────────────────────────────────────────────────
        self._refresh_profile_combo()
        self._refresh_all()

        self._prev_sensor_ids: set[str] | None = None

        if self._state:
            self._state.sensors_updated.connect(self._on_sensor_values_updated)
            self._state.fans_updated.connect(self._on_fan_rpm_updated)
            self._state.capabilities_updated.connect(self._on_capabilities_updated)

    # ─── Profile bar ─────────────────────────────────────────────────

    def _build_profile_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.setObjectName("Controls_Combo_profile")
        self._profile_combo.setMinimumWidth(150)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        bar.addWidget(self._profile_combo)

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setObjectName("Controls_Btn_activate")
        self._activate_btn.setToolTip("Set selected profile as active")
        self._activate_btn.clicked.connect(self._on_activate)
        bar.addWidget(self._activate_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("Controls_Btn_save")
        self._save_btn.setToolTip("Save profile changes (Ctrl+S)")
        self._save_btn.clicked.connect(self._on_save_profile)
        bar.addWidget(self._save_btn)

        self._unsaved_label = QLabel("")
        self._unsaved_label.setProperty("class", "WarningChip")
        bar.addWidget(self._unsaved_label)

        bar.addStretch()

        manage_btn = QPushButton("Manage Profiles...")
        manage_btn.setObjectName("Controls_Btn_manageProfiles")
        manage_btn.setToolTip("Create, rename, duplicate, or delete profiles")
        manage_btn.clicked.connect(self._on_manage_profiles)
        bar.addWidget(manage_btn)

        return bar

    # ─── Profile management ──────────────────────────────────────────

    def _refresh_profile_combo(self, selected_id: str = "") -> None:
        """Rebuild the profile combo box.

        Args:
            selected_id: Profile ID to select after rebuild. If empty, keeps the
                         current selection (or falls back to the active profile).
        """
        self._profile_combo.blockSignals(True)
        # Remember current selection before clearing
        if not selected_id:
            selected_id = self._profile_combo.currentData() or ""
        self._profile_combo.clear()
        active_id = self._profile_service.active_id
        select_idx = 0
        for i, p in enumerate(self._profile_service.profiles):
            label = f"* {p.name}" if p.id == active_id else p.name
            self._profile_combo.addItem(label, p.id)
            if p.id == selected_id:
                select_idx = i
        # Fall back to active profile if the requested selection no longer exists
        if not selected_id or self._profile_combo.itemData(select_idx) != selected_id:
            for i in range(self._profile_combo.count()):
                if self._profile_combo.itemData(i) == active_id:
                    select_idx = i
                    break
        self._profile_combo.setCurrentIndex(select_idx)
        self._profile_combo.blockSignals(False)

    def _on_profile_selected(self, index: int) -> None:
        if index < 0:
            return
        # Do NOT call _refresh_profile_combo() here — it would destroy the
        # user's selection. The combo already has the correct index; just
        # refresh the page content for the newly selected profile.
        self._refresh_all()
        self._set_unsaved(False)

    def _on_activate(self) -> None:
        profile_id = self._profile_combo.currentData()
        if not profile_id:
            return

        # Save first so the daemon reads the latest version
        profile = self._profile_service.get_profile(profile_id)
        if not profile:
            return
        self._profile_service.save_profile(profile)

        # Send activation to daemon API
        profile_path = str(self._profile_service.profile_path(profile_id))
        if self._client:
            try:
                result = self._client.activate_profile(profile_path)
                if not result.activated:
                    self._log.warning("Daemon rejected profile activation: %s", profile_id)
                    self._unsaved_label.setText("Activation rejected by daemon")
                    self._unsaved_label.setProperty("class", "CriticalChip")
                    self._unsaved_label.style().unpolish(self._unsaved_label)
                    self._unsaved_label.style().polish(self._unsaved_label)
                    return
                self._log.info("Profile activated on daemon: %s", result.profile_name)
            except DaemonError as exc:
                self._log.error("Profile activation failed: %s", exc)
                self._unsaved_label.setText(f"Activation failed: {exc.message}")
                self._unsaved_label.setProperty("class", "CriticalChip")
                self._unsaved_label.style().unpolish(self._unsaved_label)
                self._unsaved_label.style().polish(self._unsaved_label)
                return
        else:
            self._log.debug("No daemon client — profile activated locally only")

        # Update local state only after daemon confirms (or if no client)
        self._profile_service.set_active(profile_id)
        self._refresh_profile_combo(selected_id=profile_id)
        self._refresh_all()
        if self._state:
            self._state.set_active_profile(profile.name)
        # Force immediate control loop re-evaluation. active_profile_changed
        # does not fire when the name is unchanged (e.g. re-activating after
        # editing curves on the already-active profile), so we cannot rely
        # on the signal-driven path to reset hysteresis and push new writes.
        if self._control_loop is not None:
            self._control_loop.reevaluate_now()
        self.profile_activated.emit(profile_id)
        self._unsaved_label.setText("Profile activated")
        self._unsaved_label.setProperty("class", "SuccessChip")
        self._unsaved_label.style().unpolish(self._unsaved_label)
        self._unsaved_label.style().polish(self._unsaved_label)

    def _on_manage_profiles(self) -> None:
        menu = QMenu(self)
        menu.addAction("New Profile", self._on_new_profile)
        menu.addAction("Rename Profile", self._on_rename_profile)
        menu.addAction("Duplicate Profile", self._on_duplicate_profile)
        menu.addSeparator()
        menu.addAction("Delete Profile", self._on_delete_profile)
        btn = self.findChild(QPushButton, "Controls_Btn_manageProfiles")
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_new_profile(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(
                self, "New Profile", "Profile name:", text="New Profile"
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        new_profile = self._profile_service.create_profile(name)
        self._refresh_profile_combo(selected_id=new_profile.id)
        self._refresh_all()
        self._set_unsaved(False)

    def _on_rename_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        name, ok = QInputDialog.getText(self, "Rename Profile", "New name:", text=profile.name)
        if ok and name.strip() and name.strip() != profile.name:
            profile.name = name.strip()
            self._profile_service.save_profile(profile)
            self._refresh_profile_combo(selected_id=profile.id)

    def _on_duplicate_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        new_profile = self._profile_service.duplicate_profile(profile.id, f"{profile.name} (copy)")
        if new_profile:
            self._refresh_profile_combo(selected_id=new_profile.id)
            self._refresh_all()

    def _on_delete_profile(self) -> None:
        profile_id = self._profile_combo.currentData()
        if profile_id:
            reply = QMessageBox.question(
                self,
                "Delete Profile",
                f"Delete profile '{profile_id}'? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._profile_service.delete_profile(profile_id)
            # After deletion, switch to the active profile (or whatever is now first)
            self._refresh_profile_combo(selected_id=self._profile_service.active_id)
            self._refresh_all()

    def _on_save_profile(self) -> None:
        profile = self._get_current_profile()
        if profile:
            self._profile_service.save_profile(profile)
            self._set_unsaved(False)
            self._unsaved_label.setText(mc("save_success"))
            self._unsaved_label.setProperty("class", "SuccessChip")
            self._unsaved_label.style().unpolish(self._unsaved_label)
            self._unsaved_label.style().polish(self._unsaved_label)

    # ─── Refresh all ─────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)

    # ─── Control cards grid ──────────────────────────────────────────

    def _refresh_controls_grid(self, profile) -> None:
        # Clear existing
        self._controls_flow.clear_cards()
        self._control_cards.clear()

        for control in profile.controls:
            card = ControlCard(control, profile.curves)
            card.selected.connect(self._on_control_selected)
            card.delete_requested.connect(self._on_delete_control)
            card.edit_role_requested.connect(self._on_edit_role)
            self._control_cards[control.id] = card
            self._controls_flow.add_card(card, control.id)

        has_controls = len(profile.controls) > 0
        self._controls_empty.setVisible(not has_controls)
        self._controls_scroll.setVisible(has_controls)
        # Progressive disclosure: show curves section only when controls exist
        self._curves_section.setVisible(has_controls)
        self._no_controls_hint.setVisible(not has_controls)

    def _on_fan_wizard(self) -> None:
        """Open the Fan Configuration Wizard."""
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        spindown = 8
        if self._settings_service:
            spindown = self._settings_service.settings.wizard_spindown_seconds
        wizard = FanConfigWizard(
            state=self._state,
            client=self._client,
            control_loop=self._control_loop,
            lease_service=self._lease_service,
            spindown_seconds=spindown,
            parent=self,
        )
        # Alias persistence is handled by MainWindow via AppState.fan_alias_changed
        wizard.exec()

    def _on_new_control_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Single Output Fan Role", lambda: self._on_new_control(single=True))
        menu.addAction("Group Fan Role (Multi-Fan)", lambda: self._on_new_control(single=False))
        btn = self._add_control_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_new_control(self, single: bool = False, name: str | None = None) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        if name is None:
            default_name = "New Fan Role" if single else "New Group"
            name, ok = QInputDialog.getText(
                self, "New Fan Role", "Control name:", text=default_name
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        curve_id = profile.curves[0].id if profile.curves else ""
        control = LogicalControl(name=name, mode=ControlMode.CURVE, curve_id=curve_id)
        profile.controls.append(control)
        self._refresh_controls_grid(profile)
        self._set_unsaved(True)

    def _on_edit_role(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        control = next((c for c in profile.controls if c.id == control_id), None)
        if not control:
            return

        from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog

        dlg = FanRoleDialog(control, profile.curves, parent=self)
        dlg.set_edit_members_callback(self._on_edit_members)
        if dlg.exec():
            result = dlg.get_result()
            control.name = result["name"]
            control.mode = result["mode"]
            control.curve_id = result["curve_id"]
            control.manual_output_pct = result.get("manual_output_pct", control.manual_output_pct)
            card = self._control_cards.get(control_id)
            if card:
                card.update_control(control, profile.curves)
            self._set_unsaved(True)

    def _on_delete_control(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        profile.controls = [c for c in profile.controls if c.id != control_id]
        self._refresh_controls_grid(profile)
        self._set_unsaved(True)

    def _on_control_selected(self, control_id: str) -> None:
        self._selected_control_id = control_id

    def _on_edit_members(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        control = next((c for c in profile.controls if c.id == control_id), None)
        if not control:
            return

        available: list[dict] = []
        if self._state:
            gpu_writable = (
                self._state.capabilities is not None
                and self._state.capabilities.amd_gpu.fan_write_supported
            )
            for fan in self._state.fans:
                label = self._state.fan_display_name(fan.id)
                if fan.source == "amd_gpu" and not gpu_writable:
                    label = f"{label} (read-only)"
                available.append(
                    {
                        "id": fan.id,
                        "source": fan.source,
                        "label": label,
                    }
                )
            for header in self._state.hwmon_headers:
                if not any(a["id"] == header.id for a in available):
                    available.append(
                        {
                            "id": header.id,
                            "source": "hwmon",
                            "label": header.label or header.id,
                        }
                    )

        # Build assigned_elsewhere map for exclusive membership
        assigned_elsewhere: dict[str, str] = {}
        for other_ctrl in profile.controls:
            if other_ctrl.id != control_id:
                for m in other_ctrl.members:
                    assigned_elsewhere[m.member_id] = other_ctrl.name

        from control_ofc.ui.widgets.member_editor import MemberEditorDialog

        dlg = MemberEditorDialog(control.members, available, assigned_elsewhere, parent=self)
        if dlg.exec():
            new_members = dlg.get_members()
            # Check if any NEW GPU fans were added — show zero-RPM info popup
            old_gpu_ids = {m.member_id for m in control.members if m.source == "amd_gpu"}
            new_gpu_ids = {m.member_id for m in new_members if m.source == "amd_gpu"}
            added_gpu = new_gpu_ids - old_gpu_ids
            if added_gpu and self._settings_service:
                settings = self._settings_service.settings
                if settings.show_gpu_zero_rpm_warning:
                    self._show_gpu_zero_rpm_info()

            control.members = new_members
            card = self._control_cards.get(control_id)
            if card:
                card.update_control(control, profile.curves)
            self._set_unsaved(True)

    # ─── Curve cards grid ────────────────────────────────────────────

    def _refresh_curves_grid(self, profile) -> None:
        self._curves_flow.clear_cards()
        self._curve_cards.clear()

        for curve in profile.curves:
            card = CurveCard(curve)
            card.edit_requested.connect(self._on_edit_curve)
            card.delete_requested.connect(self._on_delete_curve)
            card.rename_requested.connect(self._on_rename_curve)
            card.duplicate_requested.connect(self._on_duplicate_curve)
            self._curve_cards[curve.id] = card
            self._curves_flow.add_card(card, curve.id)

        # Set "Used by" on each curve card
        for _cid, ccard in self._curve_cards.items():
            role_names = [ctrl.name for ctrl in profile.controls if ctrl.curve_id == ccard.curve.id]
            ccard.set_used_by(role_names)

        has_curves = len(profile.curves) > 0
        self._curves_empty.setVisible(not has_curves)
        self._curves_scroll.setVisible(has_curves)

    def _on_controls_reordered(self, new_order: list[str]) -> None:
        """Handle drag-to-reorder of fan role cards."""
        profile = self._get_current_profile()
        if not profile:
            return
        control_map = {c.id: c for c in profile.controls}
        profile.controls = [control_map[cid] for cid in new_order if cid in control_map]
        self._set_unsaved(True)

    def _on_curves_reordered(self, new_order: list[str]) -> None:
        """Handle drag-to-reorder of curve cards."""
        profile = self._get_current_profile()
        if not profile:
            return
        # Reorder profile.curves to match the new card order
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map[cid] for cid in new_order if cid in curve_map]
        self._set_unsaved(True)

    def _on_add_curve_menu(self) -> None:
        menu = QMenu(self)
        for ct in [CurveType.GRAPH, CurveType.LINEAR, CurveType.FLAT]:
            menu.addAction(f"{ct.value.title()} Curve", lambda t=ct: self._on_add_curve(t))
        btn = self._add_curve_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_add_curve(self, curve_type: CurveType = CurveType.GRAPH) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        from control_ofc.ui.widgets.curve_editor import PRESETS

        points = []
        if curve_type == CurveType.GRAPH:
            points = [CurvePoint(p.temp_c, p.output_pct) for p in PRESETS["Linear"]]
        curve = CurveConfig(name=f"New {curve_type.value.title()}", type=curve_type, points=points)
        profile.curves.append(curve)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)

    def _on_delete_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        # Unassign from any controls that reference this curve
        for ctrl in profile.controls:
            if ctrl.curve_id == curve_id:
                ctrl.curve_id = ""
        profile.curves = [c for c in profile.curves if c.id != curve_id]
        # Close editor if editing the deleted curve
        editing = self._curve_editor.get_curve()
        if editing and editing.id == curve_id:
            self._close_editor()
        self._refresh_curves_grid(profile)
        self._refresh_controls_grid(profile)  # update control cards (curve_id cleared)
        self._set_unsaved(True)

    def _on_rename_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        curve = profile.get_curve(curve_id)
        if not curve:
            return
        name, ok = QInputDialog.getText(self, "Rename Curve", "New name:", text=curve.name)
        if ok and name.strip():
            curve.name = name.strip()
            self._refresh_curves_grid(profile)
            self._refresh_controls_grid(profile)
            self._set_unsaved(True)

    def _on_duplicate_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        import uuid

        source = profile.get_curve(curve_id)
        if not source:
            return
        data = source.to_dict()
        data["id"] = str(uuid.uuid4())[:8]
        data["name"] = f"{source.name} (copy)"
        new_curve = CurveConfig.from_dict(data)
        profile.curves.append(new_curve)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)

    def _on_edit_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        curve = profile.get_curve(curve_id)
        if not curve:
            return

        # Linear/Flat: open dialog. Graph: use embedded editor.
        if curve.type in (CurveType.LINEAR, CurveType.FLAT):
            from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

            # Build sensor items from current state
            sensor_items = []
            if self._state:
                for s in self._state.sensors:
                    val_text = f" \u2014 {s.value_c:.1f}\u00b0C" if s.value_c else ""
                    sensor_items.append((s.id, f"{s.label} ({s.kind}){val_text}"))
            dlg = CurveEditDialog(curve, sensor_items, parent=self)
            if dlg.exec():
                dlg.apply_to_curve()
                self._refresh_curves_grid(profile)
                self._set_unsaved(True)
        else:
            self._editor_title.setText(f"Editing: {curve.name}")
            self._curve_editor.set_curve(curve)
            self._editor_frame.show()

    def _close_editor(self) -> None:
        self._editor_frame.hide()

    def _on_curve_changed(self) -> None:
        self._set_unsaved(True)
        # Update control card output previews
        self._update_card_previews()
        # Refresh the curve card preview for the curve being edited
        curve = self._curve_editor.get_curve()
        if curve:
            card = self._curve_cards.get(curve.id)
            if card:
                card.update_curve(curve)

    # ─── Helpers ─────────────────────────────────────────────────────

    def _update_card_previews(self) -> None:
        """Re-evaluate output preview on all cards referencing the curve being edited."""
        profile = self._get_current_profile()
        editing = self._curve_editor.get_curve()
        if not profile or not editing or not self._state:
            return
        for _cid, card in self._control_cards.items():
            ctrl = card.control
            if ctrl.mode == ControlMode.CURVE and ctrl.curve_id == editing.id and editing.sensor_id:
                for s in self._state.sensors:
                    if s.id == editing.sensor_id:
                        output = editing.interpolate(s.value_c)
                        card.update_output_preview(editing.name, s.label, s.value_c, output)
                        break

    def _get_current_profile(self):
        profile_id = self._profile_combo.currentData()
        if profile_id:
            return self._profile_service.get_profile(profile_id)
        return self._profile_service.active_profile

    def _show_gpu_zero_rpm_info(self) -> None:
        """Show an informational popup explaining GPU zero-RPM behaviour."""
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("GPU Fan Control")
        msg.setText("GPU Zero-RPM Mode")
        msg.setInformativeText(
            "When a GPU fan is controlled by a curve, the daemon automatically "
            "disables the GPU\u2019s zero-RPM idle mode so the fan responds at "
            "all temperatures.\n\n"
            "When the curve is removed or the daemon shuts down, zero-RPM mode "
            "is automatically restored so the GPU can stop its fans at idle.\n\n"
            "This is normal behaviour for AMD RDNA3+ GPUs and matches how other "
            "Linux GPU control tools (e.g. LACT) operate."
        )

        dont_show = QCheckBox("Don\u2019t show this again")
        msg.setCheckBox(dont_show)
        msg.exec()

        if dont_show.isChecked() and self._settings_service:
            self._settings_service.update(show_gpu_zero_rpm_warning=False)

    def _set_unsaved(self, unsaved: bool) -> None:
        self._has_unsaved = unsaved
        self._unsaved_label.setText("Unsaved changes" if unsaved else "")
        if unsaved:
            self._unsaved_label.setProperty("class", "WarningChip")
            self._unsaved_label.style().unpolish(self._unsaved_label)
            self._unsaved_label.style().polish(self._unsaved_label)

    def update_control_outputs(self, outputs: dict[str, float]) -> None:
        profile = self._get_current_profile()
        for control_id, output in outputs.items():
            card = self._control_cards.get(control_id)
            if not card:
                continue
            # Find the sensor driving this control for context
            sensor_name = ""
            sensor_value = None
            if profile and self._state:
                ctrl = card.control
                if ctrl.mode == ControlMode.CURVE:
                    curve = profile.get_curve(ctrl.curve_id)
                    if curve and curve.sensor_id:
                        for s in self._state.sensors:
                            if s.id == curve.sensor_id:
                                sensor_name = s.label
                                sensor_value = s.value_c
                                break
            card.set_output(output, sensor_name, sensor_value)

    def set_theme(self, tokens) -> None:
        """Forward theme updates to child widgets that hold theme state."""
        self._curve_editor.set_theme(tokens)
        for card in self._curve_cards.values():
            card.set_theme(tokens)

    def _on_capabilities_updated(self, caps) -> None:
        if not hasattr(caps, "features") or caps.features is None:
            return
        can_write = caps.features.openfan_write_supported or caps.features.hwmon_write_supported
        if not can_write:
            for card in self._control_cards.values():
                card.setEnabled(False)

    def _on_sensor_values_updated(self, sensors) -> None:
        """Called ~1Hz. Rebuild sensor dropdown only when the sensor list changes."""
        current_ids = {s.id for s in sensors}
        if current_ids != self._prev_sensor_ids:
            self._prev_sensor_ids = current_ids
            seen = set()
            items = []
            for s in sensors:
                if s.id not in seen:
                    val_text = f" \u2014 {s.value_c:.1f}\u00b0C" if s.value_c else ""
                    items.append((s.id, f"{s.label} ({s.kind}){val_text}"))
                    seen.add(s.id)
            self._curve_editor.set_available_sensors(items)

        # Always update live temperature display (cheap — single lookup)
        curve = self._curve_editor.get_curve()
        if curve and curve.sensor_id:
            for s in sensors:
                if s.id == curve.sensor_id:
                    self._curve_editor.set_current_sensor_value(s.value_c)
                    break
            else:
                self._curve_editor.set_current_sensor_value(None)
        elif sensors:
            self._curve_editor.set_current_sensor_value(sensors[0].value_c)

        # Update curve card sensor value labels (cheap — dict lookup per card)
        sensor_map = {s.id: (s.label, s.value_c) for s in sensors}
        for _curve_id, ccard in self._curve_cards.items():
            sid = ccard.curve.sensor_id
            if sid and sid in sensor_map:
                label, val = sensor_map[sid]
                ccard.update_sensor_display(label, val)
            elif sid:
                pretty = sid.split(":")[-1] if ":" in sid else sid
                ccard.update_sensor_display(pretty)

    def _on_fan_rpm_updated(self, fans) -> None:
        """Update control card RPM displays. Wired to fans_updated signal."""
        fan_map = {f.id: f for f in fans}
        for _control_id, card in self._control_cards.items():
            ctrl = card.control
            rpms = []
            for m in ctrl.members:
                fan = fan_map.get(m.member_id)
                if fan and fan.rpm is not None:
                    rpms.append(fan.rpm)
            if rpms:
                avg = sum(rpms) // len(rpms)
                card.set_rpm(f"{avg} RPM")
            else:
                card.set_rpm("")
