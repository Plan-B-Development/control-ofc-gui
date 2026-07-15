"""Settings page — app preferences, themes, and import/export."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.errors import DaemonError
from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, PAGE_SETTINGS
from control_ofc.paths import (
    app_settings_path,
    atomic_write,
    config_dir,
    export_default_dir,
    load_json_capped,
    profiles_dir,
    set_path_overrides,
    themes_dir,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_import_service import import_profiles
from control_ofc.services.profile_service import ImportCollection, collect_local_profiles_for_import
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.toggle_switch import ToggleSwitch
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import ThemeTokens

log = logging.getLogger(__name__)


def _safe_import_name(name: str, dest_dir: Path) -> str | None:
    """Validate an untrusted import key used as a ``<name>.json`` filename.

    Imported profile/theme dict keys are attacker-controlled: a key like
    ``"../../evil"`` would escape *dest_dir* (audit P1-B). Accept only a bare
    filename, and require the resolved destination to stay inside *dest_dir*
    (defense in depth). Returns *name* when safe, else ``None`` so the caller
    skips it (counted in the existing "invalid item(s) skipped" total). POSIX
    GUI only — backslash handling is out of scope.
    """
    if not name or name != Path(name).name:
        return None
    try:
        dest = (dest_dir / f"{name}.json").resolve()
        if not dest.is_relative_to(dest_dir.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return name


_PAGE_NAMES = {
    PAGE_DASHBOARD: "Dashboard",
    PAGE_CONTROLS: "Controls",
    PAGE_SETTINGS: "Settings",
}


class SettingsPage(QWidget):
    """Application settings, path management, preferred sensors, and backup/restore.

    DEC-215: restyled from a QTabWidget into a single 2-column card surface; the
    former Themes tab moved to its own ``ThemePage`` and Import/Export folded into
    the "Sync & Backup" card.
    """

    def __init__(
        self,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
        client: DaemonClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._settings_svc = settings_service or AppSettingsService()
        self._client = client
        # Phase 4 (DEC-200): preferred-sensor selector state. ``_populating_prefs``
        # suppresses the change→POST while the combos are filled programmatically;
        # ``_prefs_loaded`` gates the one-time lazy fetch on first show.
        self._populating_prefs = False
        self._prefs_loaded = False

        self.setObjectName("Settings_Root")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        # ─── Header: title + subtitle + Save Changes (batched save) ───
        header = QHBoxLayout()
        title = QLabel("Application Settings")
        title.setProperty("class", "PageTitle")
        header.addWidget(title)
        subtitle = QLabel("Application preferences, themes, and backup/restore")
        subtitle.setProperty("class", "PageSubtitle")
        header.addWidget(subtitle)
        header.addStretch()
        save_btn = make_button("Save Changes", "primary", object_name="Settings_Btn_saveApp")
        save_btn.setToolTip("Save application settings")
        save_btn.clicked.connect(self._save_app_settings)
        header.addWidget(save_btn)
        layout.addLayout(header)

        # ─── 2-column card grid (scrollable — DEC-206 deep-link scrolls here) ───
        self._app_scroll = QScrollArea()
        self._app_scroll.setWidgetResizable(True)
        self._app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        cols = QHBoxLayout(body)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(16)
        col1 = QVBoxLayout()
        col1.setSpacing(16)
        col1.addWidget(self._build_general_startup_card())
        col1.addWidget(self._build_operational_card())
        col1.addStretch()
        col2 = QVBoxLayout()
        col2.setSpacing(16)
        col2.addWidget(self._build_path_management_card())
        col2.addWidget(self._build_preferred_sensors_group())
        col2.addWidget(self._build_sync_backup_card())
        col2.addStretch()
        cols.addLayout(col1, 1)
        cols.addLayout(col2, 1)
        self._app_scroll.setWidget(body)
        layout.addWidget(self._app_scroll, 1)

        # Status line
        self._status_label = QLabel("")
        self._status_label.setProperty("class", "PageSubtitle")
        layout.addWidget(self._status_label)

        # Load current values
        self._load_current_settings()

    # ─── Tab builders ────────────────────────────────────────────────

    def _setting_row(self, title: str, subtitle: str, control: QWidget) -> QHBoxLayout:
        """A mockup settings row: a title + sublabel on the left, a control right."""
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addWidget(QLabel(title))
        sub = QLabel(subtitle)
        sub.setProperty("class", "CardMeta")
        text.addWidget(sub)
        row.addLayout(text, 1)
        row.addWidget(control)
        return row

    def _build_general_startup_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.addWidget(SectionHeader("General & Startup"))

        self._startup_page_combo = QComboBox()
        self._startup_page_combo.setObjectName("Settings_Combo_startupPage")
        for page_id, name in sorted(_PAGE_NAMES.items()):
            self._startup_page_combo.addItem(name, page_id)
        v.addLayout(
            self._setting_row(
                "Default startup page",
                "Page to load when the application launches",
                self._startup_page_combo,
            )
        )

        self._restore_page_cb = ToggleSwitch()
        self._restore_page_cb.setObjectName("Settings_Check_restorePage")
        v.addLayout(
            self._setting_row(
                "Restore last selected page",
                "Overrides the default startup page",
                self._restore_page_cb,
            )
        )

        self._demo_disconnect_cb = ToggleSwitch()
        self._demo_disconnect_cb.setObjectName("Settings_Check_demoDisconnect")
        v.addLayout(
            self._setting_row(
                "Start in demo mode",
                "When the daemon is unavailable",
                self._demo_disconnect_cb,
            )
        )

        self._gpu_zero_rpm_warn_cb = ToggleSwitch()
        self._gpu_zero_rpm_warn_cb.setObjectName("Settings_Check_gpuZeroRpmWarn")
        self._gpu_zero_rpm_warn_cb.setToolTip(
            "Display an informational popup explaining that zero-RPM idle mode "
            "is temporarily disabled while a curve controls the GPU fan"
        )
        v.addLayout(
            self._setting_row(
                "Show GPU zero-RPM warning",
                "Warn when adding a GPU fan to a role",
                self._gpu_zero_rpm_warn_cb,
            )
        )

        # Chart default range — retained (part of the batched save; DEC-215 D2).
        self._chart_range_combo = QComboBox()
        self._chart_range_combo.setObjectName("Settings_Combo_chartRange")
        from control_ofc.ui.widgets.timeline_chart import TIME_RANGES

        for label, _seconds in TIME_RANGES:
            self._chart_range_combo.addItem(label)
        v.addLayout(
            self._setting_row(
                "Chart default time range",
                "Initial window on the dashboard graph",
                self._chart_range_combo,
            )
        )
        return card

    def _build_operational_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Operational Behavior"))

        self._wizard_spindown_spin = QSpinBox()
        self._wizard_spindown_spin.setObjectName("Settings_Spin_wizardSpindown")
        self._wizard_spindown_spin.setRange(5, 12)
        self._wizard_spindown_spin.setSuffix(" seconds")
        self._wizard_spindown_spin.setToolTip(
            "How long each fan is stopped during the wizard identification test"
        )
        v.addLayout(
            self._setting_row(
                "Fan Wizard spin-down timer",
                "How long each fan is stopped during identification",
                self._wizard_spindown_spin,
            )
        )

        self._startup_delay_spin = QSpinBox()
        self._startup_delay_spin.setObjectName("Settings_Spin_startupDelay")
        self._startup_delay_spin.setRange(0, 30)
        self._startup_delay_spin.setSuffix(" seconds")
        self._startup_delay_spin.setToolTip(
            "Delay before daemon begins device detection after boot (takes effect on restart)"
        )
        v.addLayout(
            self._setting_row(
                "Daemon startup delay",
                "Delay before device detection after boot (restart to apply)",
                self._startup_delay_spin,
            )
        )

        self._hide_igpu_cb = ToggleSwitch()
        self._hide_igpu_cb.setObjectName("Settings_Check_hideIgpu")
        self._hide_igpu_cb.setToolTip(
            "Hide iGPU temperature sensors when a discrete GPU is present"
        )
        v.addLayout(
            self._setting_row(
                "Auto-hide integrated GPU sensors",
                "Clean up the sensor list",
                self._hide_igpu_cb,
            )
        )

        self._hide_unused_fans_cb = ToggleSwitch()
        self._hide_unused_fans_cb.setObjectName("Settings_Check_hideUnusedFans")
        self._hide_unused_fans_cb.setToolTip("Hide motherboard fan headers that report zero RPM")
        v.addLayout(
            self._setting_row(
                "Auto-hide unused fan headers",
                "Hide headers reporting 0 RPM",
                self._hide_unused_fans_cb,
            )
        )
        return card

    def _build_path_management_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(SectionHeader("Path Management"))
        note = QLabel(
            "Override where profiles, themes, and exports are stored. "
            "Leave blank to use the default XDG location."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        self._profiles_dir_label = QLabel()
        self._profiles_dir_label.setObjectName("Settings_Label_profilesDir")
        v.addLayout(
            self._dir_picker_row("Profiles:", self._profiles_dir_label, self._browse_profiles_dir)
        )
        self._themes_dir_label = QLabel()
        self._themes_dir_label.setObjectName("Settings_Label_themesDir")
        v.addLayout(
            self._dir_picker_row("Themes:", self._themes_dir_label, self._browse_themes_dir)
        )
        self._export_dir_label = QLabel()
        self._export_dir_label.setObjectName("Settings_Label_exportDir")
        v.addLayout(
            self._dir_picker_row("Default export:", self._export_dir_label, self._browse_export_dir)
        )
        return card

    def _dir_picker_row(self, label_text: str, path_label: QLabel, browse_callback) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        path_label.setMinimumWidth(250)
        # Use the *active* theme so the dir-picker label tint follows light
        # vs dark theme changes — pre-DEC-109 this was pinned to the default
        # dark token and looked wrong under any other theme.
        from control_ofc.ui.theme import active_theme

        path_label.setStyleSheet(f"color: {active_theme().text_muted};")
        row.addWidget(path_label, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(browse_callback)
        row.addWidget(browse_btn)
        reset_btn = QPushButton("Reset")
        reset_btn.setToolTip("Reset to default XDG location")
        reset_btn.clicked.connect(lambda: self._reset_dir(path_label))
        row.addWidget(reset_btn)
        return row

    def _build_sync_backup_card(self) -> QWidget:
        """Sync & Backup card (DEC-215) — folds the former Import/Export tab."""
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(SectionHeader("Sync & Backup"))
        note = QLabel(
            "Export or import all application settings — preferences, theme, fan "
            "aliases, chart configuration, and profile bindings."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        # Sync local profiles into the daemon's own store (DEC-161).
        sync_btn = make_button(
            "Sync Local Profiles to Daemon",
            "primary",
            object_name="Settings_Btn_importProfilesToDaemon",
        )
        sync_btn.setToolTip(
            "Import your local fan profiles into the daemon's own store so it can "
            "manage them directly. Profiles already in the daemon are skipped; "
            "your local copies are left untouched. Requires daemon v1.19 or newer."
        )
        sync_btn.clicked.connect(lambda: self.run_profile_import(auto=False))
        v.addWidget(sync_btn)

        btn_row = QHBoxLayout()
        export_btn = make_button(
            "Export Config", "secondary", object_name="Settings_Btn_exportConfig"
        )
        export_btn.clicked.connect(self._export_settings)
        btn_row.addWidget(export_btn, 1)
        import_btn = make_button(
            "Import Config", "secondary", object_name="Settings_Btn_importConfig"
        )
        import_btn.clicked.connect(self._import_settings)
        btn_row.addWidget(import_btn, 1)
        v.addLayout(btn_row)

        backup_note = QLabel("A backup is created automatically before import.")
        backup_note.setWordWrap(True)
        backup_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        backup_note.setProperty("class", "CardMeta")
        v.addWidget(backup_note)

        self._export_result_label = QLabel("")
        v.addWidget(self._export_result_label)
        return card

    # ─── Logic ───────────────────────────────────────────────────────

    def _load_current_settings(self) -> None:
        s = self._settings_svc.settings
        idx = self._startup_page_combo.findData(s.default_startup_page)
        if idx >= 0:
            self._startup_page_combo.setCurrentIndex(idx)
        self._restore_page_cb.setChecked(s.restore_last_page)
        self._demo_disconnect_cb.setChecked(s.demo_on_disconnect)
        self._chart_range_combo.setCurrentIndex(
            max(0, min(s.chart_default_range_index, self._chart_range_combo.count() - 1))
        )
        self._gpu_zero_rpm_warn_cb.setChecked(s.show_gpu_zero_rpm_warning)
        self._wizard_spindown_spin.setValue(s.wizard_spindown_seconds)
        self._startup_delay_spin.setValue(s.daemon_startup_delay_secs)
        self._hide_igpu_cb.setChecked(s.hide_igpu_sensors)
        self._hide_unused_fans_cb.setChecked(s.hide_unused_fan_headers)

        # DEC-215: card_size moved to the Theme page (it owns the combo + its
        # single writer); this page no longer loads or persists it.

        # Directory overrides (show override or default as placeholder)
        self._profiles_dir_label.setText(s.profiles_dir_override or str(profiles_dir()))
        self._themes_dir_label.setText(s.themes_dir_override or str(themes_dir()))
        self._export_dir_label.setText(s.export_default_dir or str(export_default_dir()))

    def _save_app_settings(self) -> None:
        # Determine directory overrides: empty label text means "use default"
        profiles_override = self._profiles_dir_label.text()
        themes_override = self._themes_dir_label.text()
        export_override = self._export_dir_label.text()

        # Clear override if it matches the XDG default
        from control_ofc.paths import config_dir as _config_dir

        xdg_profiles = str(_config_dir() / "profiles")
        xdg_themes = str(_config_dir() / "themes")
        if profiles_override == xdg_profiles:
            profiles_override = ""
        if themes_override == xdg_themes:
            themes_override = ""
        if export_override == str(Path.home()):
            export_override = ""

        self._settings_svc.update(
            default_startup_page=self._startup_page_combo.currentData(),
            restore_last_page=self._restore_page_cb.isChecked(),
            demo_on_disconnect=self._demo_disconnect_cb.isChecked(),
            chart_default_range_index=self._chart_range_combo.currentIndex(),
            show_gpu_zero_rpm_warning=self._gpu_zero_rpm_warn_cb.isChecked(),
            wizard_spindown_seconds=self._wizard_spindown_spin.value(),
            daemon_startup_delay_secs=self._startup_delay_spin.value(),
            hide_igpu_sensors=self._hide_igpu_cb.isChecked(),
            hide_unused_fan_headers=self._hide_unused_fans_cb.isChecked(),
            profiles_dir_override=profiles_override,
            themes_dir_override=themes_override,
            export_default_dir=export_override,
        )

        # Apply path overrides immediately
        set_path_overrides(
            profiles_dir=profiles_override,
            themes_dir=themes_override,
            export_dir=export_override,
        )

        # Push startup delay to daemon if connected
        if self._client:
            from control_ofc.api.errors import DaemonError

            try:
                self._client.set_startup_delay(self._startup_delay_spin.value())
            except DaemonError as e:
                log.warning("Failed to sync startup delay to daemon: %s", e.message)
                self._set_status("Application settings saved (startup delay not synced to daemon)")
                return

        self._set_status("Application settings saved")

    # ─── Preferred sensors (daemon, DEC-200) ───────────────────────

    def _build_preferred_sensors_group(self) -> QWidget:
        group = Card()
        group.setObjectName("Settings_Group_preferredSensors")
        v = QVBoxLayout(group)
        v.setSpacing(8)

        header = SectionHeader("Preferred Sensors")
        refresh_btn = make_button(
            "Refresh from Daemon",
            "secondary",
            object_name="Settings_Btn_refreshPreferredSensors",
        )
        refresh_btn.clicked.connect(self._refresh_preferred_sensors)
        header.add_trailing(refresh_btn)
        v.addWidget(header)

        note = QLabel(
            "Pin which temperature sensor the daemon treats as your CPU and "
            "motherboard reference. Persisted by the daemon and shared across "
            "clients. Advisory only — thermal safety always uses the hottest CPU "
            "sensor. Leave on Automatic to use the daemon's recommendation."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        cpu_label = QLabel("Preferred CPU sensor")
        cpu_label.setProperty("class", "CardMeta")
        v.addWidget(cpu_label)
        self._pref_cpu_combo = QComboBox()
        self._pref_cpu_combo.setObjectName("Settings_Combo_preferredCpu")
        self._pref_cpu_combo.currentIndexChanged.connect(self._on_preferred_cpu_changed)
        v.addWidget(self._pref_cpu_combo)

        mb_label = QLabel("Preferred motherboard sensor")
        mb_label.setProperty("class", "CardMeta")
        v.addWidget(mb_label)
        self._pref_mb_combo = QComboBox()
        self._pref_mb_combo.setObjectName("Settings_Combo_preferredMb")
        self._pref_mb_combo.currentIndexChanged.connect(self._on_preferred_mb_changed)
        v.addWidget(self._pref_mb_combo)

        self._pref_result_label = QLabel("")
        self._pref_result_label.setObjectName("Settings_Label_preferredResult")
        self._pref_result_label.setWordWrap(True)
        v.addWidget(self._pref_result_label)

        self._pref_group = group  # for focus_preferred_sensors (DEC-206 deep-link)
        return group

    def focus_preferred_sensors(self, role: str = "cpu") -> None:
        """Reveal the preferred-sensors picker and focus the CPU or motherboard
        combo — the target of the merged readiness view's "Pick a sensor"
        deep-link (DEC-206). Refreshes the combos from the daemon so the picker is
        populated when arrived at directly, then scrolls the group into view and
        focuses the requested combo. (DEC-215: no tab to select — single surface.)
        """
        if self._client is not None:
            self._refresh_preferred_sensors()
        combo = self._pref_mb_combo if role == "mb" else self._pref_cpu_combo
        self._app_scroll.ensureWidgetVisible(self._pref_group)
        combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Load preferred-sensor options from the daemon the first time Settings
        # is shown — a light, cache-backed GET kept off the startup path.
        if not self._prefs_loaded and self._client is not None:
            self._refresh_preferred_sensors()

    def _refresh_preferred_sensors(self) -> None:
        """Fetch the classified sensor inventory and (re)populate the combos."""
        if self._client is None:
            self._set_pref_result(
                "Daemon not connected — preferred sensors unavailable.", "CautionChip"
            )
            return
        from control_ofc.api.errors import DaemonError

        try:
            inv = self._client.inventory_hwmon()
        except DaemonError as e:
            if getattr(e, "status", None) == 404:
                self._set_pref_result(
                    "This daemon version does not support preferred sensors.", "CautionChip"
                )
            else:
                self._set_pref_result(f"Could not load sensors: {e.message}", "CriticalChip")
            return
        except (ConnectionError, OSError):
            self._set_pref_result("Daemon unavailable — could not load sensors.", "CautionChip")
            return

        self._prefs_loaded = True
        recommended = inv.default_cpu.sensor_id if inv.default_cpu else None
        cpu_pref = inv.preferences.cpu_sensor_id if inv.preferences else None
        mb_pref = inv.preferences.mb_sensor_id if inv.preferences else None
        sensors = [s for s in inv.temp_sensors if s.control_eligible]

        self._populating_prefs = True
        try:
            self._fill_pref_combo(self._pref_cpu_combo, sensors, recommended, cpu_pref)
            self._fill_pref_combo(self._pref_mb_combo, sensors, None, mb_pref)
        finally:
            self._populating_prefs = False
        self._set_pref_result("", "")

    def _fill_pref_combo(self, combo, sensors, recommended, current) -> None:
        combo.clear()
        combo.addItem("Automatic (recommended)", None)
        for s in sensors:
            star = "★ " if recommended and s.id == recommended else ""
            cls = f" — {s.classification}" if s.classification else ""
            combo.addItem(f"{star}{s.label or s.id}{cls}", s.id)
        idx = combo.findData(current) if current else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_preferred_cpu_changed(self, _index: int) -> None:
        if self._populating_prefs:
            return
        self._post_preferred("cpu", self._pref_cpu_combo.currentData())

    def _on_preferred_mb_changed(self, _index: int) -> None:
        if self._populating_prefs:
            return
        self._post_preferred("mb", self._pref_mb_combo.currentData())

    def _post_preferred(self, role: str, sensor_id: str | None) -> None:
        if self._client is None:
            return
        from control_ofc.api.errors import DaemonError

        try:
            if role == "cpu":
                self._client.set_preferred_cpu_sensor(sensor_id)
            else:
                self._client.set_preferred_mb_sensor(sensor_id)
        except DaemonError as e:
            self._set_pref_result(
                f"Could not save preferred {role} sensor: {e.message}", "CriticalChip"
            )
            return
        except (ConnectionError, OSError):
            self._set_pref_result("Daemon unavailable — preferred sensor not saved.", "CautionChip")
            return
        where = "cleared" if sensor_id is None else "saved"
        label = "CPU" if role == "cpu" else "motherboard"
        self._set_pref_result(f"Preferred {label} sensor {where}.", "SuccessChip")

    def _set_pref_result(self, text: str, css: str) -> None:
        from control_ofc.ui.qt_util import set_chip_class

        self._pref_result_label.setText(text)
        if css:
            set_chip_class(self._pref_result_label, css, skip_if_unchanged=True)

    # ─── Directory picker handlers ─────────────────────────────────

    def _browse_profiles_dir(self) -> None:
        current = self._profiles_dir_label.text() or str(profiles_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Profiles Directory", current)
        if path:
            self._handle_dir_change("profiles", self._profiles_dir_label, path, profiles_dir())

    def _browse_themes_dir(self) -> None:
        current = self._themes_dir_label.text() or str(themes_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Themes Directory", current)
        if path:
            self._handle_dir_change("themes", self._themes_dir_label, path, themes_dir())

    def _browse_export_dir(self) -> None:
        current = self._export_dir_label.text() or str(export_default_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Default Export Directory", current)
        if path:
            self._export_dir_label.setText(path)

    def _reset_dir(self, label: QLabel) -> None:
        label.setText("")
        label.setToolTip("Using default XDG location")

    def _handle_dir_change(self, kind: str, label: QLabel, new_path: str, old_dir: Path) -> None:
        """Handle profile/theme directory change: offer to move existing files."""
        new_dir = Path(new_path)
        if new_dir == old_dir:
            label.setText(new_path)
            return

        # Check for existing files to migrate
        existing_files = list(old_dir.glob("*.json")) if old_dir.exists() else []
        if existing_files:
            reply = QMessageBox.question(
                self,
                f"Move existing {kind}?",
                f"Move {len(existing_files)} file(s) from:\n{old_dir}\n\nto:\n{new_dir}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                new_dir.mkdir(parents=True, exist_ok=True)
                moved = 0
                for f in existing_files:
                    try:
                        dest = new_dir / f.name
                        shutil.move(str(f), str(dest))
                        moved += 1
                    except OSError as e:
                        log.warning("Failed to move %s: %s", f, e)
                self._set_status(f"Moved {moved}/{len(existing_files)} files to {new_dir}")

        label.setText(new_path)

        # If profiles dir changed, update daemon via API
        if kind == "profiles" and self._client:
            from control_ofc.api.errors import DaemonError

            try:
                self._client.update_profile_search_dirs(add=[new_path])
                self._set_status("Profile search dirs updated on daemon")
            except DaemonError as exc:
                QMessageBox.warning(
                    self, "Daemon Config", f"Failed to update daemon: {exc.message}"
                )
        elif kind == "profiles":
            self._set_status("Daemon not connected — update profile search dirs manually")

    # ── Daemon profile import (DEC-161) ──────────────────────────────────

    def run_profile_import(self, *, auto: bool) -> None:
        """Migrate the user's local profiles into the daemon's profile store.

        ``auto=True`` is the once-per-install startup offer (gated by
        ``should_offer_import`` in the main window); ``auto=False`` is the
        always-available manual button. Idempotent: a re-run 409-skips ids
        already in the store. Originals on disk are never modified.
        """
        caps = self._state.capabilities if self._state else None
        control = getattr(caps, "control", None)
        storage_ok = bool(control and getattr(control, "profile_storage", False))
        if self._client is None or not storage_ok:
            if not auto:
                QMessageBox.information(
                    self,
                    "Import profiles",
                    "Connect to a daemon that supports profile storage (v1.19 "
                    "or newer) before importing your local profiles.",
                )
            return

        collection = collect_local_profiles_for_import()
        if collection.is_empty:
            if auto:
                self._mark_import_prompted()
            else:
                QMessageBox.information(
                    self, "Import profiles", "No local profiles were found to import."
                )
            return

        if auto:
            # Offered now — never auto-ask again on this install, even on "No".
            self._mark_import_prompted()
            proceed = QMessageBox.question(
                self,
                "Import your profiles?",
                f"Found {len(collection.ready)} local profile(s). Import them "
                "into the daemon so it can manage them?\n\nProfiles already in "
                "the daemon are skipped, and your local copies are left "
                "untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        try:
            report = import_profiles(self._client, collection, on_conflict="skip")
        except DaemonError as e:
            QMessageBox.warning(
                self,
                "Import failed",
                f"The daemon became unavailable during import:\n{e}",
            )
            return

        if not auto:
            self._mark_import_prompted()
        self._present_import_report(report, collection)

    def _mark_import_prompted(self) -> None:
        if not self._settings_svc.settings.daemon_import_prompted:
            self._settings_svc.update(daemon_import_prompted=True)

    def _present_import_report(self, report, collection: ImportCollection) -> None:
        """Show the import result and, when ids collided, offer to re-import the
        skipped profiles as renamed copies (DEC-161)."""
        allow_rename = True
        while True:
            box = QMessageBox(self)
            box.setObjectName("Settings_Dialog_importReport")
            box.setWindowTitle("Profile import")
            box.setIcon(
                QMessageBox.Icon.Warning if report.quarantined else QMessageBox.Icon.Information
            )
            box.setText(self._format_import_report(report))
            box.addButton(QMessageBox.StandardButton.Ok)
            rename_btn = None
            if allow_rename and report.skipped:
                rename_btn = box.addButton(
                    f"Import {len(report.skipped)} skipped as copies",
                    QMessageBox.ButtonRole.ActionRole,
                )
            box.exec()

            if rename_btn is None or box.clickedButton() is not rename_btn:
                break

            skipped_ids = {o.profile_id for o in report.skipped}
            sub = ImportCollection(
                ready=[c for c in collection.ready if c.profile_id in skipped_ids]
            )
            try:
                renamed = import_profiles(self._client, sub, on_conflict="rename")
            except DaemonError as e:
                QMessageBox.warning(self, "Import failed", f"The daemon became unavailable:\n{e}")
                break
            report.imported.extend(renamed.imported)
            report.quarantined.extend(renamed.quarantined)
            report.skipped = []  # every skipped candidate was just reprocessed
            allow_rename = False

        self._export_result_label.setText(
            f"Imported {len(report.imported)}, skipped {len(report.skipped)}, "
            f"quarantined {len(report.quarantined)}."
        )

    @staticmethod
    def _format_import_report(report) -> str:
        lines = [
            f"Imported: {len(report.imported)}",
            f"Skipped (already in daemon): {len(report.skipped)}",
            f"Quarantined (not imported): {len(report.quarantined)}",
        ]
        if report.quarantined:
            lines.append("")
            lines.append("Quarantined:")
            for o in report.quarantined[:10]:
                label = o.name or o.source_path
                lines.append(f"  • {label}: {o.reason}")
            if len(report.quarantined) > 10:
                lines.append(f"  • … and {len(report.quarantined) - 10} more")
        return "\n".join(lines)

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "control_ofc_settings.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            # Build comprehensive export including profiles
            export_data = self._build_full_export()
            Path(path).write_text(json.dumps(export_data, indent=2) + "\n")
            self._set_export_result("Settings exported successfully", "SuccessChip")
        except (OSError, ValueError, TypeError) as e:
            self._set_export_result(f"Export failed: {e}", "CriticalChip")

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            raw = load_json_capped(Path(path))
            if not isinstance(raw, dict):
                self._set_export_result("Import failed: invalid file format", "CriticalChip")
                return

            export_ver = raw.get("export_version")
            if export_ver is not None:
                if isinstance(export_ver, bool) or not isinstance(export_ver, (int, float)):
                    self._set_export_result(
                        "Import failed: unrecognized export version", "CriticalChip"
                    )
                    return
                if export_ver > 1:
                    self._set_export_result(
                        f"Import failed: unsupported export version {export_ver} "
                        "(max supported: 1)",
                        "CriticalChip",
                    )
                    return

            # Auto-backup current settings before applying anything.
            backup_path = self._create_backup()

            # Merge imported settings onto the current ones so machine-specific
            # state (window geometry, data-dir overrides, …) is preserved and
            # only portable preferences are overwritten (DEC-140). Stripping the
            # machine keys from the *incoming* data means even a legacy full
            # export can never move the window or wipe local overrides.
            incoming = raw.get("settings")
            if isinstance(incoming, dict):
                from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS

                merged = self._settings_svc.settings.to_dict()
                merged.update({k: v for k, v in incoming.items() if k not in MACHINE_SPECIFIC_KEYS})
                imported = self._settings_svc.import_settings_from_dict(merged)
                self._settings_svc.apply_imported(imported)
                self._load_current_settings()
                # Apply live side effects, mirroring a manual Save (F11):
                # data-dir overrides and the daemon startup delay.
                set_path_overrides(
                    profiles_dir=imported.profiles_dir_override,
                    themes_dir=imported.themes_dir_override,
                    export_dir=imported.export_default_dir,
                )
                if self._client:
                    from control_ofc.api.errors import DaemonError

                    try:
                        self._client.set_startup_delay(imported.daemon_startup_delay_secs)
                    except DaemonError as e:
                        log.warning("Failed to sync startup delay on import: %s", e.message)

            # Apply profiles if present.
            skipped = 0
            profiles = raw.get("profiles")
            if isinstance(profiles, dict):
                skipped += self._import_profiles(profiles)

            # Apply custom themes if present.
            themes = raw.get("themes")
            if isinstance(themes, dict):
                skipped += self._import_themes(themes)

            backup_msg = f" (backup: {backup_path.name})" if backup_path else ""
            skip_msg = f" ({skipped} invalid item(s) skipped)" if skipped else ""
            css = "WarningChip" if skipped else "SuccessChip"
            self._set_export_result(
                f"Settings imported{backup_msg}{skip_msg} — "
                "some changes take effect on next launch",
                css,
            )
        except (
            json.JSONDecodeError,
            OSError,
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            self._set_export_result(f"Import failed: {e}", "CriticalChip")

    def _build_full_export(self) -> dict:
        """Build a comprehensive export covering all configurable state."""
        export: dict = {
            "export_version": 1,
            "exported_at": datetime.now().isoformat(),
            # Portable subset only — machine/session state stays local (DEC-140).
            "settings": self._settings_svc.settings.portable_dict(),
        }
        # Include profiles
        from control_ofc.paths import profiles_dir

        pdir = profiles_dir()
        if pdir.exists():
            profiles = {}
            for p in pdir.glob("*.json"):
                try:
                    profiles[p.stem] = load_json_capped(p)
                except (OSError, ValueError) as e:
                    log.warning("Skipping unreadable profile %s: %s", p, e)
            if profiles:
                export["profiles"] = profiles

        # Include all custom themes (not just active)
        td = themes_dir()
        if td.exists():
            themes = {}
            for tf in td.glob("*.json"):
                try:
                    themes[tf.stem] = load_json_capped(tf)
                except (OSError, ValueError) as e:
                    log.warning("Skipping unreadable theme %s: %s", tf, e)
            if themes:
                export["themes"] = themes

        return export

    def _create_backup(self) -> Path | None:
        """Create a timestamped backup of current settings before import."""
        src = app_settings_path()
        if not src.exists():
            return None
        backup_dir = config_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"settings_backup_{stamp}.json"
        shutil.copy2(src, backup)
        log.info("Settings backup created: %s", backup)
        return backup

    def _import_profiles(self, profiles: dict) -> int:
        """Import profile JSON files from export data.

        Returns the number of profiles that failed validation.
        """
        from control_ofc.paths import profiles_dir
        from control_ofc.services.profile_service import Profile

        pdir = profiles_dir()
        pdir.mkdir(parents=True, exist_ok=True)

        # Validate all profiles before writing any to disk.
        valid: dict[str, dict] = {}
        skipped: list[str] = []
        for name, data in profiles.items():
            if not isinstance(data, dict):
                log.warning("Skipping profile '%s': not a JSON object", name)
                skipped.append(name)
                continue
            if _safe_import_name(name, pdir) is None:
                log.warning("Skipping profile '%s': unsafe name (path traversal)", name)
                skipped.append(name)
                continue
            try:
                Profile.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping profile '%s': validation failed: %s", name, exc)
                skipped.append(name)
                continue
            valid[name] = data

        if not valid:
            return len(skipped)

        existing = [name for name in valid if (pdir / f"{name}.json").exists()]
        if existing:
            reply = QMessageBox.question(
                self,
                "Overwrite profiles?",
                f"{len(existing)} profile(s) already exist and will be overwritten:\n"
                + ", ".join(existing[:5])
                + ("\n..." if len(existing) > 5 else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return len(skipped)

        for name, data in valid.items():
            dest = pdir / f"{name}.json"
            atomic_write(dest, json.dumps(data, indent=2) + "\n")
        log.info("Imported %d profile(s)", len(valid))
        return len(skipped)

    def _import_themes(self, themes_data: dict) -> int:
        """Import custom theme JSON files from export data.

        Returns the number of themes that failed validation.
        """
        from control_ofc.ui.theme import _apply_token_dict, _migrate_tokens

        td = themes_dir()
        td.mkdir(parents=True, exist_ok=True)

        valid: dict[str, dict] = {}
        skipped: list[str] = []
        for name, data in themes_data.items():
            if not isinstance(data, dict):
                log.warning("Skipping theme '%s': not a JSON object", name)
                skipped.append(name)
                continue
            if _safe_import_name(name, td) is None:
                log.warning("Skipping theme '%s': unsafe name (path traversal)", name)
                skipped.append(name)
                continue
            try:
                migrated = _migrate_tokens(data)
                # Strict validation: any invalid colour/value raises and the
                # whole theme is skipped (DEC-142).
                _apply_token_dict(ThemeTokens(), migrated, strict=True)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping theme '%s': validation failed: %s", name, exc)
                skipped.append(name)
                continue
            valid[name] = data

        for name, data in valid.items():
            dest = td / f"{name}.json"
            atomic_write(dest, json.dumps(data, indent=2) + "\n")
        log.info("Imported %d theme(s)", len(valid))
        return len(skipped)

    def _set_export_result(self, text: str, css_class: str) -> None:
        self._export_result_label.setText(text)
        set_chip_class(self._export_result_label, css_class, skip_if_unchanged=True)

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)
