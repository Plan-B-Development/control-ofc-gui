"""Controls page — FanControl-style 2-section layout.

Section A (top): Control cards grid — logical controls with mode, curve, output.
Section B (bottom): Curve cards grid + expandable curve editor.
Profile bar at top for profile management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
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
from control_ofc.api.models import ConnectionState, DaemonStatus
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    CONTROL_ROLE_GPU,
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    ProfileService,
    apply_role_floor,
    control_minimum_pct,
    infer_member_role,
    mix_candidate_curves,
    sync_candidate_controls,
)
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    PRESENCE_TOOLTIP,
    FanPresence,
    classify_fan_presence,
)
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.widgets.card_metrics import DEFAULT_CARD_SIZE
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard
from control_ofc.ui.widgets.curve_editor import CurveEditor
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.demo_controller import DemoController


class ControlsPage(QWidget):
    """FanControl-style controls: profile bar, control cards grid, curve cards grid."""

    profile_activated = Signal(str)

    _log = logging.getLogger(__name__)
    # Manual-override (DEC-163) GUI timing. The renew cadence follows each
    # grant's advised ``renew_secs``; this is only the fallback. The value
    # debounce coalesces a live slider drag into a single re-pin (a new
    # override_take supersedes the prior token) instead of one call per pixel.
    _OVERRIDE_RENEW_FALLBACK_MS = 5000
    _OVERRIDE_VALUE_DEBOUNCE_MS = 200

    def __init__(
        self,
        state: AppState | None = None,
        profile_service: ProfileService | None = None,
        client: DaemonClient | None = None,
        demo_controller: DemoController | None = None,
        settings_service: AppSettingsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._profile_service = profile_service or ProfileService()
        self._client = client
        # Demo-only mini-evaluator (DEC-165). Live mode has no control loop —
        # the daemon is the authoritative writer; this drives the demo manual
        # and curve simulation only.
        self._demo_controller = demo_controller
        self._settings_service = settings_service
        self._control_cards: dict[str, ControlCard] = {}
        self._curve_cards: dict[str, CurveCard] = {}
        self._selected_control_id: str | None = None
        self._has_unsaved: bool = False
        # Last-known write capability. Cards are disabled when the daemon
        # reports no writable backend; tracked here so a rebuild (profile
        # switch) and a later capability change both honour the latest value
        # instead of stranding cards disabled (see _on_capabilities_updated).
        self._cards_writable: bool = True
        # Live manual overrides (DEC-163): control_id -> current override_token.
        # Populated only in daemon-driven (live) mode; demo mode drives the demo
        # control loop instead. The renew timer keeps every held override alive
        # well inside its TTL — a rejected renew means it lapsed (GUI froze /
        # daemon restarted), so the card reverts. The value timer debounces a
        # live slider drag into a single re-pin.
        self._overrides: dict[str, int] = {}
        self._override_pending: dict[str, int] = {}
        # DEC-169: daemon-held overrides this session does NOT own (control_id ->
        # pwm), discovered by reconciling `/status.overrides[]`. Distinct from
        # `_overrides`: no fencing token, so they are display-only (read-only
        # "External" chip) — the renew timer never touches them, and clicking
        # Manual takes a fresh override (explicit ownership). Kept separate so the
        # two authorities (renew timer vs poll reconcile) never collide.
        self._external_overrides: dict[str, int] = {}
        self._override_renew_timer = QTimer(self)
        self._override_renew_timer.setObjectName("Controls_Timer_overrideRenew")
        self._override_renew_timer.timeout.connect(self._renew_overrides)
        self._override_value_timer = QTimer(self)
        self._override_value_timer.setObjectName("Controls_Timer_overrideValue")
        self._override_value_timer.setSingleShot(True)
        self._override_value_timer.setInterval(self._OVERRIDE_VALUE_DEBOUNCE_MS)
        self._override_value_timer.timeout.connect(self._flush_override_values)

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

        # DEC-157: one-click liquid-cooler setup. Hidden until an AIO is
        # detected (see _on_capabilities_updated).
        self._configure_aio_btn = QPushButton("Configure AIO")
        self._configure_aio_btn.setObjectName("Controls_Btn_configureAio")
        self._configure_aio_btn.setToolTip(
            "One-click setup for a liquid cooler — a constant-speed pump and a radiator-fan group"
        )
        self._configure_aio_btn.clicked.connect(self._on_configure_aio)
        self._configure_aio_btn.setVisible(False)
        controls_header.addWidget(self._configure_aio_btn)

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

        # Splitter sizing — equal stretch + equal seeded sizes so the Fan
        # Roles / Curves split stays ~50/50 as the window resizes, while the
        # divider remains user-draggable (DEC-128, D3=A).
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([350, 350])
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
            self._state.connection_changed.connect(self._on_connection_changed)
            # DEC-169: reconcile daemon-held overrides from the 1 Hz poll so a
            # foreign override (another client, or this GUI restarted within the
            # TTL) shows on the card instead of a stale "Curve".
            self._state.status_updated.connect(self._on_status_reconcile)

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
        with block_signals(self._profile_combo):
            # Remember current selection before clearing
            if not selected_id:
                selected_id = self._profile_combo.currentData() or ""
            self._profile_combo.clear()
            active_id = self._profile_service.active_id
            select_idx = 0
            ps = self._profile_service
            for i, p in enumerate(ps.profiles):
                label = f"* {p.name}" if p.id == active_id else p.name
                # Daemon-backed mode: flag profiles that exist only locally
                # (created/edited while offline, or rejected on upload).
                if ps.daemon_backed and not ps.is_published(p.id):
                    label = f"{label}  (draft)"
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

        # Remember previous selection so we can revert on failure
        prev_active_id = self._profile_service.active_id

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
                    self._refresh_profile_combo(selected_id=prev_active_id)
                    return
                self._log.info("Profile activated on daemon: %s", result.profile_name)
            except DaemonError as exc:
                self._log.error("Profile activation failed: %s", exc)
                self._unsaved_label.setText(f"Activation failed: {exc.message or 'unknown error'}")
                self._unsaved_label.setProperty("class", "CriticalChip")
                self._unsaved_label.style().unpolish(self._unsaved_label)
                self._unsaved_label.style().polish(self._unsaved_label)
                self._refresh_profile_combo(selected_id=prev_active_id)
                return
        else:
            self._log.debug("No daemon client — profile activated locally only")

        # Update local state only after daemon confirms (or if no client)
        self._profile_service.set_active(profile_id)
        self._refresh_profile_combo(selected_id=profile_id)
        self._refresh_all()
        if self._state:
            self._state.set_active_profile(profile.name)
        # The daemon re-evaluates the activated profile itself (it is the
        # authoritative engine, DEC-165) — the GUI no longer forces a local
        # control-loop re-evaluation. (Demo mode reflects it on its next tick.)
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
            # If we're deleting the daemon's active profile, tell it to
            # deactivate first so the curve stops driving fans the moment
            # the file is gone (DEC-097). Pre-DEC-097 the daemon kept the
            # in-memory profile until restart, leaving "phantom" curve
            # writes targeting a profile that no longer exists on disk.
            was_active_locally = self._profile_service.active_id == profile_id
            if was_active_locally and self._client is not None:
                try:
                    self._client.deactivate_profile()
                except DaemonError as exc:
                    self._log.warning("Daemon deactivate before delete failed: %s", exc)
                    # Continue with the local delete — the file is the
                    # canonical source for the next activation, and the
                    # daemon will surface the error itself.
            self._profile_service.delete_profile(profile_id)
            if was_active_locally and self._state is not None:
                self._state.set_active_profile("")
            # After deletion, switch to the active profile (or whatever is now first)
            self._refresh_profile_combo(selected_id=self._profile_service.active_id)
            self._refresh_all()

    def _on_save_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        self._profile_service.save_profile(profile)
        self._set_unsaved(False)
        if self._profile_service.daemon_backed and not self._profile_service.is_published(
            profile.id
        ):
            # Written to the local cache but the daemon did not accept it
            # (offline, or rejected on upload) — an unpublished draft (6b).
            self._unsaved_label.setText("Saved locally — daemon offline, not published")
            self._unsaved_label.setProperty("class", "WarningChip")
        else:
            self._unsaved_label.setText("Settings saved")
            self._unsaved_label.setProperty("class", "SuccessChip")
        self._unsaved_label.style().unpolish(self._unsaved_label)
        self._unsaved_label.style().polish(self._unsaved_label)
        # Reflect the new published/draft state in the combo badge.
        self._refresh_profile_combo()

    def _on_connection_changed(self, conn: ConnectionState) -> None:
        """Gate Activate on the daemon being reachable (live mode only).

        Activation is a daemon verb, so it cannot work while the daemon is
        offline. Demo/local mode (no client) activates locally and stays
        enabled.
        """
        if self._client is None:
            return
        connected = conn == ConnectionState.CONNECTED
        self._activate_btn.setEnabled(connected)
        self._activate_btn.setToolTip(
            "Set selected profile as active"
            if connected
            else "Daemon offline — cannot activate a profile"
        )
        if not connected:
            # DEC-169: polling stops while offline, so nothing would clear a
            # stale "External" chip — revert them now. GUI-owned overrides
            # self-correct via the renew timer's rejected renew.
            self._clear_all_external_overrides()

    # ─── Refresh all ─────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)

    # ─── Control cards grid ──────────────────────────────────────────

    def _refresh_controls_grid(self, profile) -> None:
        # Release any live overrides first: the cards are about to be destroyed
        # and rebuilt un-toggled, so a still-held daemon override would leave
        # card state diverged from the daemon (DEC-163).
        self._release_all_overrides()
        # Drop foreign-override tracking too — the cards are being rebuilt fresh;
        # the next poll re-adopts any still-active foreign override (DEC-169).
        self._external_overrides.clear()
        # Clear existing
        self._controls_flow.clear_cards()
        self._control_cards.clear()

        tier = self._card_size_tier()
        for control in profile.controls:
            card = ControlCard(
                control,
                profile.curves,
                card_size=tier,
                user_size=self._stored_card_size(control.id),
            )
            card.selected.connect(self._on_control_selected)
            card.delete_requested.connect(self._on_delete_control)
            card.edit_role_requested.connect(self._on_edit_role)
            card.manual_toggled.connect(self._on_card_manual_toggled)
            card.manual_value_changed.connect(self._on_card_manual_value)
            card.resized.connect(self._on_card_user_resized)
            card.size_reset.connect(self._on_card_size_reset)
            # Rebuilt cards default to enabled; honour the last-known write
            # capability so a profile switch can't silently re-enable a card
            # the daemon reported as non-writable.
            card.setEnabled(self._cards_writable)
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
            spindown_seconds=spindown,
            parent=self,
        )
        # Alias persistence is handled by MainWindow via AppState.fan_alias_changed
        wizard.exec()

    def _on_configure_aio(self) -> None:
        """DEC-157: one-click liquid-cooler setup — build a constant-speed pump
        control + a coolant-bound radiator control via the shared creation path."""
        profile = self._get_current_profile()
        if not profile or not self._state:
            return
        from control_ofc.services.profile_service import (
            ControlMember,
            build_aio_controls,
            detect_aio_setup,
        )
        from control_ofc.ui.sensor_knowledge import classify_sensor_with_overrides
        from control_ofc.ui.widgets.aio_config_dialog import AioConfigDialog

        overrides = self._state.sensor_class_overrides
        det = detect_aio_setup(self._state.hwmon_headers, self._state.sensors, overrides)
        pump_id = det.pump_member.member_id if det.pump_member else None
        preselect_ids = {m.member_id for m in det.radiator_members}

        # Candidate radiator fans: writable hwmon + OpenFan fans, minus the pump.
        candidates: list[dict] = []
        header_by_id = {h.id: h for h in self._state.hwmon_headers}
        seen: set[str] = set()
        for fan in self._state.fans:
            if fan.source in ("amd_gpu", "intel_gpu"):
                continue
            if fan.source == "hwmon":
                h = header_by_id.get(fan.id)
                if h is None or not h.is_writable:
                    continue
            if fan.id == pump_id or fan.id in seen:
                continue
            seen.add(fan.id)
            label = self._state.fan_display_name(fan.id)
            candidates.append(
                {
                    "id": fan.id,
                    "source": fan.source,
                    "label": label,
                    "preselect": fan.id in preselect_ids or "radiator" in label.lower(),
                }
            )
        for header in self._state.hwmon_headers:
            if not header.is_writable or header.id == pump_id or header.id in seen:
                continue
            label = header.label or header.id
            candidates.append(
                {
                    "id": header.id,
                    "source": "hwmon",
                    "label": label,
                    "preselect": header.id in preselect_ids
                    or header.is_aio
                    or "radiator" in label.lower(),
                }
            )

        # Sensor choices, with coolant + CPU flagged preferred.
        sensor_choices: list[dict] = []
        for s in self._state.sensors:
            cls = classify_sensor_with_overrides(
                s.id, chip_name=s.chip_name, label=s.label, overrides=overrides
            )
            preferred = cls.source_class in (
                "coolant",
                "coolant_in",
                "coolant_out",
            ) or s.kind in ("cpu_temp", "CpuTemp")
            sensor_choices.append({"id": s.id, "label": s.label, "preferred": preferred})

        dlg = AioConfigDialog(
            pump_label=det.pump_member.member_label if det.pump_member else None,
            monitor_only=det.monitor_only,
            fan_candidates=candidates,
            sensor_choices=sensor_choices,
            default_sensor_id=det.coolant_sensor_id,
            parent=self,
        )
        if not dlg.exec():
            return
        res = dlg.get_result()
        radiator_members = [
            ControlMember(source=c["source"], member_id=c["id"], member_label=c["label"])
            for c in res["radiator_members"]
        ]
        created = build_aio_controls(
            profile,
            pump_member=det.pump_member if res["pump_pct"] is not None else None,
            pump_pct=res["pump_pct"] or 0,
            radiator_members=radiator_members,
            radiator_sensor_id=res["radiator_sensor_id"],
        )
        if not created:
            return
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)
        # One-time pump-info popup when a pump control was created.
        if (
            res["pump_pct"] is not None
            and det.pump_member is not None
            and self._settings_service
            and self._settings_service.settings.show_aio_pump_info
        ):
            self._show_aio_pump_info()

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
            # Persist per-GPU-member zero-RPM toggles back onto the members.
            zero_rpm_map = result.get("gpu_fan_zero_rpm", {}) or {}
            for member in control.members:
                if member.source == "amd_gpu" and member.member_id in zero_rpm_map:
                    member.fan_zero_rpm = bool(zero_rpm_map[member.member_id])
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
            header_by_id = {h.id: h for h in self._state.hwmon_headers}

            for fan in self._state.fans:
                # DEC-102: drop hwmon-source fans whose header is read-only.
                # Pre-DEC-102 a read-only header could be assigned to a
                # control and produce a 1 Hz EACCES storm. Daemon discovery
                # also drops these (Option A); this is defense-in-depth so
                # an older daemon still under upgrade cannot offer them.
                if fan.source == "hwmon":
                    h = header_by_id.get(fan.id)
                    if h is not None and not h.is_writable:
                        continue
                # DEC-121: Intel discrete GPU fans have no kernel write path
                # (firmware-managed). Never offer them as controllable members.
                # Unlike an AMD read-only GPU (a fixable ppfeaturemask state),
                # this is permanent. The GPU's temperature sensors remain
                # available as curve sensors.
                if fan.source == "intel_gpu":
                    continue
                label = self._state.fan_display_name(fan.id)
                if fan.source == "amd_gpu" and not gpu_writable:
                    label = f"{label} (read-only)"
                # A2: surface "no fan detected" / PWM-only states in the picker
                # so users don't accidentally assign curves to empty headers.
                presence = classify_fan_presence(fan, header_by_id.get(fan.id))
                badge = PRESENCE_BADGE.get(presence, "")
                if badge and "(read-only)" not in label:
                    label = f"{label} ({badge})"
                # DEC-157: tag liquid-cooler headers so AIO members are obvious.
                h_aio = header_by_id.get(fan.id)
                if fan.source == "hwmon" and h_aio is not None and h_aio.is_aio:
                    label += " (AIO pump)" if "pump" in label.lower() else " (AIO radiator)"
                tip = PRESENCE_TOOLTIP.get(presence, "") if presence != FanPresence.PRESENT else ""
                entry = {
                    "id": fan.id,
                    "source": fan.source,
                    "label": label,
                }
                if tip:
                    entry["tooltip"] = tip
                available.append(entry)

            for header in self._state.hwmon_headers:
                # DEC-102: drop read-only hwmon headers from the picker
                # entirely. The previous "(read-only)" suffix labelled the
                # header but still allowed assignment; users (or imported
                # profiles) bound them to controls and the control loop
                # then produced 1 Hz 503/EACCES storms. Read-only headers
                # remain visible in Diagnostics → Fans for awareness.
                if not header.is_writable:
                    continue
                if not any(a["id"] == header.id for a in available):
                    label = header.label or header.id
                    presence = classify_fan_presence(None, header)
                    if PRESENCE_BADGE.get(presence):
                        label = f"{label} ({PRESENCE_BADGE[presence]})"
                    if header.is_aio:
                        label += " (AIO pump)" if "pump" in label.lower() else " (AIO radiator)"
                    tip_parts = [f"ID: {header.id}"]
                    if header.chip_name:
                        tip_parts.append(f"Chip: {header.chip_name}")
                        g = lookup_chip_guidance(header.chip_name)
                        if g:
                            st = "mainline" if g.in_mainline else g.driver_package
                            tip_parts.append(f"Driver: {g.driver_name} ({st})")
                    if presence != FanPresence.PRESENT:
                        tip_parts.append(PRESENCE_TOOLTIP.get(presence, ""))
                    available.append(
                        {
                            "id": header.id,
                            "source": "hwmon",
                            "label": label,
                            "tooltip": "\n".join(p for p in tip_parts if p),
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
            # Membership change can shift the role (chassis ↔ CPU/pump),
            # so reapply the role-aware floor before the next save.
            if apply_role_floor(control):
                self._log.info(
                    "Control '%s' minimum_pct raised to %.0f%% by role policy",
                    control.name,
                    control.minimum_pct,
                )
            card = self._control_cards.get(control_id)
            if card:
                card.update_control(control, profile.curves)
            self._set_unsaved(True)

    # ─── Curve cards grid ────────────────────────────────────────────

    def _refresh_curves_grid(self, profile) -> None:
        self._curves_flow.clear_cards()
        self._curve_cards.clear()

        tier = self._card_size_tier()
        for curve in profile.curves:
            card = CurveCard(
                curve,
                card_size=tier,
                user_size=self._stored_card_size(curve.id),
            )
            card.edit_requested.connect(self._on_edit_curve)
            card.delete_requested.connect(self._on_delete_curve)
            card.rename_requested.connect(self._on_rename_curve)
            card.duplicate_requested.connect(self._on_duplicate_curve)
            card.resized.connect(self._on_card_user_resized)
            card.size_reset.connect(self._on_card_size_reset)
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
        for ct in [
            CurveType.GRAPH,
            CurveType.STEPPED,
            CurveType.LINEAR,
            CurveType.FLAT,
            CurveType.TRIGGER,
            CurveType.MIX,
            CurveType.SYNC,
        ]:
            menu.addAction(f"{ct.value.title()} Curve", lambda t=ct: self._on_add_curve(t))
        btn = self._add_curve_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_add_curve(self, curve_type: CurveType = CurveType.GRAPH) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        from control_ofc.ui.widgets.curve_editor import PRESETS

        points = []
        if curve_type in (CurveType.GRAPH, CurveType.STEPPED):
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

        # Parameter/composite curves open a modal dialog; Graph/Stepped use the
        # embedded point editor.
        if curve.type in (
            CurveType.LINEAR,
            CurveType.FLAT,
            CurveType.TRIGGER,
            CurveType.MIX,
            CurveType.SYNC,
        ):
            from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

            # Build sensor items from current state
            sensor_items = []
            if self._state:
                for s in self._state.sensors:
                    sensor_items.append((s.id, self._sensor_combo_label(s)))
            # Composite curves offer only cycle-free choices (DEC-150/151).
            mix_candidates = (
                mix_candidate_curves(profile, curve.id) if curve.type == CurveType.MIX else None
            )
            sync_candidates = (
                sync_candidate_controls(profile, curve.id) if curve.type == CurveType.SYNC else None
            )
            dlg = CurveEditDialog(
                curve,
                sensor_items,
                mix_candidates=mix_candidates,
                sync_candidates=sync_candidates,
                parent=self,
            )
            if dlg.exec():
                dlg.apply_to_curve()
                self._refresh_curves_grid(profile)
                self._set_unsaved(True)
        else:
            self._editor_title.setText(f"Editing: {curve.name}")
            # Clamp the editable floor to the strictest minimum_pct across all
            # controls referencing this curve. If a curve is shared between a
            # CPU pump role (30%) and a chassis role (20%), the editor enforces
            # 30% so the user cannot author a point that would be clamped at
            # write time for the stricter role.
            min_floor = self._curve_min_output_floor(profile, curve.id)
            self._curve_editor.set_min_output(min_floor)
            self._curve_editor.set_curve(curve)
            self._editor_frame.show()

    def _close_editor(self) -> None:
        self._editor_frame.hide()

    def _curve_min_output_floor(self, profile, curve_id: str) -> float:
        """Return the highest ``minimum_pct`` of any control referencing this curve.

        The editor uses this to clamp curve points so a shared curve cannot
        be authored below the strictest role's safe minimum. Returns 0 when
        no control references the curve (orphan / brand-new curve).
        """
        floor = 0.0
        for ctrl in profile.controls:
            if ctrl.curve_id == curve_id:
                # Use the explicit value if the user raised it, else derive
                # from members so a freshly-created control still gets the
                # role-aware floor before it has been migrated.
                effective = max(ctrl.minimum_pct, control_minimum_pct(ctrl.members))
                floor = max(floor, effective)
        return floor

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

    def _show_aio_pump_info(self) -> None:
        """One-time popup explaining the AIO pump floor (DEC-157)."""
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("AIO Pump")
        msg.setText("The pump runs at a constant speed")
        msg.setInformativeText(
            "Your AIO pump is set to a constant speed with a 30% minimum floor.\n\n"
            "Pumps cool best at a steady speed — running a pump too low reduces "
            "coolant flow and cooling and can stress the pump. Keep it at a constant "
            "level (around 80% is a good default) rather than curving it down with "
            "temperature."
        )
        dont_show = QCheckBox("Don't show this again")
        msg.setCheckBox(dont_show)
        msg.exec()
        if dont_show.isChecked() and self._settings_service:
            self._settings_service.update(show_aio_pump_info=False)

    def _set_unsaved(self, unsaved: bool) -> None:
        self._has_unsaved = unsaved
        self._unsaved_label.setText("Unsaved changes" if unsaved else "")
        if unsaved:
            self._unsaved_label.setProperty("class", "WarningChip")
            self._unsaved_label.style().unpolish(self._unsaved_label)
            self._unsaved_label.style().polish(self._unsaved_label)

    def update_control_outputs(
        self,
        outputs: dict[str, float],
        member_outputs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        profile = self._get_current_profile()
        member_outputs = member_outputs or {}
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
            gpu_output = self._divergent_gpu_output(
                card.control, output, member_outputs.get(control_id, {})
            )
            card.set_output(output, sensor_name, sensor_value, gpu_output_pct=gpu_output)

    @staticmethod
    def _divergent_gpu_output(
        control, control_output: float, members: dict[str, float]
    ) -> float | None:
        """GPU member's applied output when it diverges from the control-wide
        value (DEC-119), else None.

        Only mixed controls (a GPU grouped with chassis/CPU fans) can diverge:
        the GPU member is re-tuned with a 0% floor and may idle below the
        control-wide floor. A GPU-only control's headline already *is* the GPU
        value, so it is never annotated.
        """
        if not members:
            return None
        if not any(infer_member_role(m) != CONTROL_ROLE_GPU for m in control.members):
            return None
        for m in control.members:
            if infer_member_role(m) == CONTROL_ROLE_GPU:
                gpu_out = members.get(m.target_id)
                if gpu_out is not None and abs(gpu_out - control_output) > 1.0:
                    return gpu_out
        return None

    def _card_size_tier(self) -> str:
        """Current card-size density tier from settings (default comfortable)."""
        if self._settings_service is not None:
            return getattr(self._settings_service.settings, "card_size", DEFAULT_CARD_SIZE)
        return DEFAULT_CARD_SIZE

    # ─── Per-card user sizes (DEC-129) ───────────────────────────────

    def _stored_card_size(self, card_id: str) -> tuple[int, int] | None:
        """Persisted [w, h] override for a control/curve id, if any."""
        if self._settings_service is None:
            return None
        raw = self._settings_service.settings.controls_card_sizes.get(card_id)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                return (int(raw[0]), int(raw[1]))
            except (TypeError, ValueError):
                return None
        return None

    def _on_card_user_resized(self, card_id: str, width: int, height: int) -> None:
        """Grip drag finished: persist the snapped size for this card."""
        if self._settings_service is None:
            return
        sizes = dict(self._settings_service.settings.controls_card_sizes)
        sizes[card_id] = [width, height]
        self._prune_card_sizes(sizes)
        self._settings_service.update(controls_card_sizes=sizes)

    def _on_card_size_reset(self, card_id: str) -> None:
        """Grip double-click: the card already restored its theme size —
        drop the persisted override."""
        if self._settings_service is None:
            return
        sizes = dict(self._settings_service.settings.controls_card_sizes)
        if sizes.pop(card_id, None) is not None:
            self._settings_service.update(controls_card_sizes=sizes)

    def _prune_card_sizes(self, sizes: dict[str, list[int]]) -> None:
        """Drop overrides for ids that no longer exist in *any* known profile.

        Keyed across all profiles (not just the active one) so switching
        profiles never sheds the inactive profile's card sizes.
        """
        known: set[str] = set()
        for profile in self._profile_service.profiles:
            known.update(c.id for c in profile.controls)
            known.update(c.id for c in profile.curves)
        for stale in [card_id for card_id in sizes if card_id not in known]:
            del sizes[stale]

    def set_theme(self, tokens) -> None:
        """Forward theme updates to child widgets and re-apply card sizing.

        A theme change can alter the base font size, so every card re-derives
        its width + minimum height from the new base size and the current
        density tier (DEC-128). Cards with a DEC-129 user size keep it
        (re-clamped to the new content minimum, never cleared). Curve cards
        additionally repaint their preview in the new accent colour.
        """
        self._curve_editor.set_theme(tokens)
        base_pt = getattr(tokens, "base_font_size_pt", 10)
        tier = self._card_size_tier()
        for card in self._control_cards.values():
            card.apply_card_size(base_pt, tier)
        for card in self._curve_cards.values():
            card.set_theme(tokens)
            card.apply_card_size(base_pt, tier)

    def _on_capabilities_updated(self, caps) -> None:
        # DEC-157: surface the Configure AIO action only when a liquid cooler is
        # detected (idempotent — capabilities re-fire on every refresh).
        aio = getattr(caps, "aio_hwmon", None)
        self._configure_aio_btn.setVisible(bool(getattr(aio, "present", False)))
        if not hasattr(caps, "features") or caps.features is None:
            return
        # Idempotent both ways: capabilities re-fire on every refresh and every
        # reconnect, so an incomplete snapshot must not leave cards stranded
        # disabled once write support returns.
        self._cards_writable = bool(
            caps.features.openfan_write_supported or caps.features.hwmon_write_supported
        )
        for card in self._control_cards.values():
            card.setEnabled(self._cards_writable)

    def _on_card_manual_toggled(self, control_id: str, active: bool, pct: int) -> None:
        """Per-card Manual toggle: pin or release one control transiently.

        In live mode this is a daemon-owned, expiring, fencing-guarded override
        (DEC-163): it reverts to autonomous curve control if the GUI stops
        renewing (daemon deadman), and the card reverts if a renew is rejected.
        In demo mode (no daemon client) the demo control loop owns the simulated
        manual state. Never touches the saved profile.
        """
        if self._client is not None:
            if active:
                self._take_override(control_id, pct)
            else:
                self._release_override(control_id)
        elif self._demo_controller is not None:
            if active:
                self._demo_controller.set_control_manual(control_id, float(pct))
            else:
                self._demo_controller.clear_control_manual(control_id)

    def _on_card_manual_value(self, control_id: str, pct: int) -> None:
        """Live slider drag while a card is in transient manual mode."""
        if self._client is not None:
            if control_id in self._overrides:
                # Debounce: coalesce a drag into one re-pin (a new override_take
                # supersedes the prior token) instead of one call per pixel.
                self._override_pending[control_id] = pct
                self._override_value_timer.start()
        elif self._demo_controller is not None:
            self._demo_controller.set_control_manual(control_id, float(pct))

    # ── Manual override via the daemon API (DEC-163) ─────────────────────
    def _take_override(self, control_id: str, pct: int) -> None:
        """Pin a control to a fixed PWM on the daemon and start renewing."""
        if self._client is None:
            return
        try:
            grant = self._client.override_take(control_id, pct)
        except DaemonError as exc:
            self._log.warning(
                "Override of control %s failed (%s): %s", control_id, exc.code, exc.message
            )
            self._revert_card_manual(control_id)
            return
        self._overrides[control_id] = grant.override_token
        interval = (
            (grant.renew_secs * 1000) if grant.renew_secs else self._OVERRIDE_RENEW_FALLBACK_MS
        )
        self._override_renew_timer.setInterval(max(1000, interval))
        if not self._override_renew_timer.isActive():
            self._override_renew_timer.start()

    def _release_override(self, control_id: str) -> None:
        """Release a held override; the daemon reverts the control to its curve."""
        self._override_pending.pop(control_id, None)
        token = self._overrides.pop(control_id, None)
        if not self._overrides:
            self._override_renew_timer.stop()
        if token is None or self._client is None:
            return
        try:
            self._client.override_release(control_id, token)
        except DaemonError as exc:
            # Offline / already lapsed — the daemon deadman reverts it anyway.
            self._log.info("Override release for %s not confirmed (%s)", control_id, exc.code)

    def _release_all_overrides(self) -> None:
        """Release every held override (e.g. before the card grid rebuilds) so
        card state never diverges from the daemon."""
        for control_id in list(self._overrides):
            self._release_override(control_id)

    def _renew_overrides(self) -> None:
        """Renew every held override inside its TTL; a rejected renew means the
        override lapsed (GUI froze / daemon restarted) → revert that card."""
        if self._client is None or not self._overrides:
            self._override_renew_timer.stop()
            return
        for control_id, token in list(self._overrides.items()):
            try:
                result = self._client.override_renew(control_id, token)
            except DaemonError as exc:
                self._log.info("Override on %s lapsed (%s) — reverting card", control_id, exc.code)
                self._overrides.pop(control_id, None)
                self._override_pending.pop(control_id, None)
                self._revert_card_manual(control_id)
                continue
            self._overrides[control_id] = result.override_token
        if not self._overrides:
            self._override_renew_timer.stop()

    def _flush_override_values(self) -> None:
        """Apply the latest debounced slider value as a re-pin (which supersedes
        the prior token). Skips controls released mid-drag; reverts on failure."""
        if self._client is None:
            self._override_pending.clear()
            return
        pending = dict(self._override_pending)
        self._override_pending.clear()
        for control_id, pct in pending.items():
            if control_id not in self._overrides:
                continue
            try:
                grant = self._client.override_take(control_id, pct)
            except DaemonError as exc:
                self._log.info(
                    "Override re-pin on %s failed (%s) — reverting", control_id, exc.code
                )
                self._overrides.pop(control_id, None)
                self._revert_card_manual(control_id)
                continue
            self._overrides[control_id] = grant.override_token

    def _revert_card_manual(self, control_id: str) -> None:
        """Visually exit Manual on a card whose override lapsed/failed, without
        re-emitting manual_toggled (which would try to release it again)."""
        card = self._control_cards.get(control_id)
        if card is not None:
            card.clear_manual()

    # ── Foreign-override reconcile from /status (DEC-169) ────────────────
    def _on_status_reconcile(self, status: DaemonStatus) -> None:
        """Reconcile daemon-held overrides from the 1 Hz poll.

        Display-only: an override this session did not create carries no fencing
        token on `/status`, so it can only be *shown* (read-only "External"
        chip), never renewed or released. GUI-owned overrides (`self._overrides`)
        belong to the renew timer and are skipped here, so the two authorities
        never collide. Idempotent — acts only on the per-poll delta.
        """
        if self._client is None:
            return
        foreign = {
            entry.control_id: entry.pwm_percent
            for entry in status.overrides
            if entry.control_id not in self._overrides
        }
        # Adopt new / changed foreign overrides onto their cards.
        for control_id, pwm in foreign.items():
            card = self._control_cards.get(control_id)
            if card is None:
                # No card for this control (a different profile is loaded). It
                # will be picked up on the next poll after a grid rebuild.
                continue
            if self._external_overrides.get(control_id) != pwm:
                card.set_external_override(pwm)
                self._external_overrides[control_id] = pwm
        # Drop tracked ones the daemon no longer reports (expired / released /
        # taken over by the user — which moves them into `self._overrides`).
        for control_id in list(self._external_overrides):
            if control_id not in foreign:
                self._clear_external_override(control_id)

    def _clear_external_override(self, control_id: str) -> None:
        """Stop tracking a foreign override and revert its card (DEC-169)."""
        self._external_overrides.pop(control_id, None)
        card = self._control_cards.get(control_id)
        if card is not None:
            card.clear_external_override()

    def _clear_all_external_overrides(self) -> None:
        """Revert every foreign-override card (e.g. on daemon disconnect)."""
        for control_id in list(self._external_overrides):
            self._clear_external_override(control_id)

    def _sensor_combo_label(self, s) -> str:
        """Curve-editor sensor-combo label, marking coolant + CPU sensors as
        preferred (\u2605) \u2014 the recommended bindings for AIO/radiator curves
        (DEC-157). Selection is still free; this only highlights."""
        from control_ofc.ui.sensor_knowledge import classify_sensor_with_overrides

        val_text = f" \u2014 {s.value_c:.1f}\u00b0C" if s.value_c else ""
        overrides = self._state.sensor_class_overrides if self._state else {}
        cls = classify_sensor_with_overrides(
            s.id, chip_name=s.chip_name, label=s.label, overrides=overrides
        )
        preferred = cls.source_class in (
            "coolant",
            "coolant_in",
            "coolant_out",
        ) or s.kind in ("cpu_temp", "CpuTemp")
        star = "\u2605 " if preferred else ""
        return f"{star}{s.label} ({s.kind}){val_text}"

    def _on_sensor_values_updated(self, sensors) -> None:
        """Called ~1Hz. Rebuild sensor dropdown only when the sensor list changes."""
        current_ids = {s.id for s in sensors}
        if current_ids != self._prev_sensor_ids:
            self._prev_sensor_ids = current_ids
            seen = set()
            items = []
            for s in sensors:
                if s.id not in seen:
                    items.append((s.id, self._sensor_combo_label(s)))
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
