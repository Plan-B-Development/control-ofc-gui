"""Settings page — app preferences, themes, and import/export."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, PAGE_DIAGNOSTICS, PAGE_SETTINGS
from control_ofc.paths import (
    app_settings_path,
    config_dir,
    export_default_dir,
    profiles_dir,
    set_path_overrides,
    themes_dir,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.ui.theme import ThemeTokens, load_theme, save_theme

log = logging.getLogger(__name__)

_PAGE_NAMES = {
    PAGE_DASHBOARD: "Dashboard",
    PAGE_CONTROLS: "Controls",
    PAGE_SETTINGS: "Settings",
    PAGE_DIAGNOSTICS: "Diagnostics",
}


class SettingsPage(QWidget):
    """Application settings, theme import/export, and backup/restore."""

    theme_changed = Signal(ThemeTokens)
    settings_changed = Signal()

    def __init__(
        self,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
        client: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._settings_svc = settings_service or AppSettingsService()
        self._client = client

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        title = QLabel("Settings")
        title.setProperty("class", "PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Application preferences, themes, and backup/restore")
        subtitle.setProperty("class", "PageSubtitle")
        layout.addWidget(subtitle)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setObjectName("Settings_Tabs_main")
        self._tabs.addTab(self._build_app_tab(), "Application")
        self._tabs.addTab(self._build_theme_tab(), "Themes")
        self._tabs.addTab(self._build_export_tab(), "Import / Export")
        layout.addWidget(self._tabs, 1)

        # Status bar
        self._status_label = QLabel("")
        self._status_label.setProperty("class", "PageSubtitle")
        layout.addWidget(self._status_label)

        # Load current values
        self._load_current_settings()

        if self._state:
            self._state.capabilities_updated.connect(self._on_capabilities_updated)

    # ─── Tab builders ────────────────────────────────────────────────

    def _build_app_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Default startup page
        row = QHBoxLayout()
        row.addWidget(QLabel("Default startup page:"))
        self._startup_page_combo = QComboBox()
        self._startup_page_combo.setObjectName("Settings_Combo_startupPage")
        for page_id, name in sorted(_PAGE_NAMES.items()):
            self._startup_page_combo.addItem(name, page_id)
        row.addWidget(self._startup_page_combo)
        row.addStretch()
        layout.addLayout(row)

        # Checkboxes
        self._restore_page_cb = QCheckBox("Restore last selected page on startup")
        self._restore_page_cb.setObjectName("Settings_Check_restorePage")
        layout.addWidget(self._restore_page_cb)

        self._demo_disconnect_cb = QCheckBox("Start in demo mode when daemon is unavailable")
        self._demo_disconnect_cb.setObjectName("Settings_Check_demoDisconnect")
        layout.addWidget(self._demo_disconnect_cb)

        self._remember_profile_cb = QCheckBox("Remember last active profile")
        self._remember_profile_cb.setObjectName("Settings_Check_rememberProfile")
        layout.addWidget(self._remember_profile_cb)

        # Fun mode
        self._fun_mode_cb = QCheckBox("Fun mode (cheeky microcopy)")
        self._fun_mode_cb.setObjectName("Settings_Check_funMode")
        self._fun_mode_cb.setToolTip("Enable playful text throughout the application")
        layout.addWidget(self._fun_mode_cb)

        # Splash screen
        self._splash_cb = QCheckBox("Show splash screen on startup")
        self._splash_cb.setObjectName("Settings_Check_showSplash")
        layout.addWidget(self._splash_cb)

        # GPU zero-RPM warning
        self._gpu_zero_rpm_warn_cb = QCheckBox(
            "Show GPU zero-RPM warning when adding GPU fan to role"
        )
        self._gpu_zero_rpm_warn_cb.setObjectName("Settings_Check_gpuZeroRpmWarn")
        self._gpu_zero_rpm_warn_cb.setToolTip(
            "Display an informational popup explaining that zero-RPM idle mode "
            "is temporarily disabled while a curve controls the GPU fan"
        )
        layout.addWidget(self._gpu_zero_rpm_warn_cb)

        # Chart default range
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Chart default time range:"))
        self._chart_range_combo = QComboBox()
        self._chart_range_combo.setObjectName("Settings_Combo_chartRange")
        for label in ["10s", "30s", "1m", "5m", "30m", "1h", "2h", "4h", "12h"]:
            self._chart_range_combo.addItem(label)
        row2.addWidget(self._chart_range_combo)
        row2.addStretch()
        layout.addLayout(row2)

        # ─── Behaviour settings ──────────────────────────────────
        behaviour_label = QLabel("Behaviour")
        behaviour_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(behaviour_label)

        # Wizard spin-down timer
        wizard_row = QHBoxLayout()
        wizard_row.addWidget(QLabel("Fan Wizard spin-down timer:"))
        self._wizard_spindown_spin = QSpinBox()
        self._wizard_spindown_spin.setObjectName("Settings_Spin_wizardSpindown")
        self._wizard_spindown_spin.setRange(5, 12)
        self._wizard_spindown_spin.setSuffix(" seconds")
        self._wizard_spindown_spin.setToolTip(
            "How long each fan is stopped during the wizard identification test"
        )
        wizard_row.addWidget(self._wizard_spindown_spin)
        wizard_row.addStretch()
        layout.addLayout(wizard_row)

        # Daemon startup delay
        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Daemon startup delay:"))
        self._startup_delay_spin = QSpinBox()
        self._startup_delay_spin.setObjectName("Settings_Spin_startupDelay")
        self._startup_delay_spin.setRange(0, 30)
        self._startup_delay_spin.setSuffix(" seconds")
        self._startup_delay_spin.setToolTip(
            "Delay before daemon begins device detection after boot (takes effect on restart)"
        )
        delay_row.addWidget(self._startup_delay_spin)
        delay_row.addStretch()
        layout.addLayout(delay_row)

        # iGPU auto-hide
        self._hide_igpu_cb = QCheckBox("Auto-hide integrated GPU sensors")
        self._hide_igpu_cb.setObjectName("Settings_Check_hideIgpu")
        self._hide_igpu_cb.setToolTip(
            "Hide iGPU temperature sensors when a discrete GPU is present"
        )
        layout.addWidget(self._hide_igpu_cb)

        # Unused fan auto-hide
        self._hide_unused_fans_cb = QCheckBox("Auto-hide unused fan headers (0 RPM)")
        self._hide_unused_fans_cb.setObjectName("Settings_Check_hideUnusedFans")
        self._hide_unused_fans_cb.setToolTip("Hide motherboard fan headers that report zero RPM")
        layout.addWidget(self._hide_unused_fans_cb)

        # ─── Data directories ────────────────────────────────────
        dirs_label = QLabel("Data Directories")
        dirs_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        layout.addWidget(dirs_label)

        dirs_note = QLabel(
            "Override where profiles, themes, and exports are stored. "
            "Leave blank to use the default XDG location."
        )
        dirs_note.setWordWrap(True)
        dirs_note.setProperty("class", "PageSubtitle")
        layout.addWidget(dirs_note)

        self._profiles_dir_label = QLabel()
        self._profiles_dir_label.setObjectName("Settings_Label_profilesDir")
        layout.addLayout(
            self._dir_picker_row("Profiles:", self._profiles_dir_label, self._browse_profiles_dir)
        )

        self._themes_dir_label = QLabel()
        self._themes_dir_label.setObjectName("Settings_Label_themesDir")
        layout.addLayout(
            self._dir_picker_row("Themes:", self._themes_dir_label, self._browse_themes_dir)
        )

        self._export_dir_label = QLabel()
        self._export_dir_label.setObjectName("Settings_Label_exportDir")
        layout.addLayout(
            self._dir_picker_row("Default export:", self._export_dir_label, self._browse_export_dir)
        )

        # Save button
        save_btn = QPushButton("Save Application Settings")
        save_btn.setObjectName("Settings_Btn_saveApp")
        save_btn.clicked.connect(self._save_app_settings)
        layout.addWidget(save_btn)

        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _dir_picker_row(self, label_text: str, path_label: QLabel, browse_callback) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        path_label.setMinimumWidth(250)
        path_label.setStyleSheet("color: #aaa;")
        row.addWidget(path_label, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(browse_callback)
        row.addWidget(browse_btn)
        reset_btn = QPushButton("Reset")
        reset_btn.setToolTip("Reset to default XDG location")
        reset_btn.clicked.connect(lambda: self._reset_dir(path_label))
        row.addWidget(reset_btn)
        return row

    def _build_theme_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Top row: theme selector + management buttons
        top = QHBoxLayout()
        top.addWidget(QLabel("Theme:"))
        self._theme_combo = QComboBox()
        self._theme_combo.setObjectName("Settings_Combo_theme")
        self._refresh_theme_list()
        top.addWidget(self._theme_combo, 1)

        apply_btn = QPushButton("Load")
        apply_btn.setObjectName("Settings_Btn_applyTheme")
        apply_btn.setToolTip("Load selected theme into editor")
        apply_btn.clicked.connect(self._apply_selected_theme)
        top.addWidget(apply_btn)

        save_theme_btn = QPushButton("Save")
        save_theme_btn.setObjectName("Settings_Btn_saveTheme")
        save_theme_btn.setToolTip("Save current edits as a theme file")
        save_theme_btn.clicked.connect(self._save_current_theme)
        top.addWidget(save_theme_btn)

        import_btn = QPushButton("Import...")
        import_btn.setObjectName("Settings_Btn_importTheme")
        import_btn.clicked.connect(self._import_theme)
        top.addWidget(import_btn)

        export_btn = QPushButton("Export...")
        export_btn.setObjectName("Settings_Btn_exportTheme")
        export_btn.clicked.connect(self._export_theme)
        top.addWidget(export_btn)
        layout.addLayout(top)

        # Theme name label
        self._theme_name_label = QLabel("Current theme: Default Dark")
        self._theme_name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._theme_name_label)

        # Typography controls
        typo_row = QHBoxLayout()
        typo_row.setSpacing(8)
        typo_row.addWidget(QLabel("Font:"))
        self._font_combo = QComboBox()
        self._font_combo.setObjectName("Settings_Combo_fontFamily")
        from PySide6.QtGui import QFontDatabase

        self._font_combo.addItem("(System Default)", "")
        for family in QFontDatabase.families():
            self._font_combo.addItem(family, family)
        typo_row.addWidget(self._font_combo, 1)

        typo_row.addWidget(QLabel("Size:"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setObjectName("Settings_Spin_fontSize")
        self._font_size_spin.setRange(7, 16)
        self._font_size_spin.setValue(10)
        self._font_size_spin.setSuffix(" pt")
        typo_row.addWidget(self._font_size_spin)
        layout.addLayout(typo_row)

        # Theme editor widget
        from control_ofc.ui.widgets.theme_editor import ThemeEditorWidget

        self._theme_editor = ThemeEditorWidget()
        self._theme_editor.setObjectName("Settings_ThemeEditor")
        self._theme_editor.theme_modified.connect(self._on_theme_edited)
        layout.addWidget(self._theme_editor, 1)

        # Apply to app button
        apply_app_btn = QPushButton("Apply Theme to Application")
        apply_app_btn.setObjectName("Settings_Btn_applyThemeToApp")
        apply_app_btn.setToolTip("Apply the current editor state to the whole application")
        apply_app_btn.clicked.connect(self._apply_editor_theme_to_app)
        layout.addWidget(apply_app_btn)

        return container

    def _build_export_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        note = QLabel(
            "Export or import all application settings — preferences, theme, "
            "fan aliases, chart configuration, and profile bindings.\n\n"
            "A backup of your current settings is created automatically before import."
        )
        note.setWordWrap(True)
        note.setProperty("class", "PageSubtitle")
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        export_btn = QPushButton("Export Settings...")
        export_btn.setObjectName("Settings_Btn_exportConfig")
        export_btn.clicked.connect(self._export_settings)
        btn_row.addWidget(export_btn)

        import_btn = QPushButton("Import Settings...")
        import_btn.setObjectName("Settings_Btn_importConfig")
        import_btn.clicked.connect(self._import_settings)
        btn_row.addWidget(import_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._export_result_label = QLabel("")
        layout.addWidget(self._export_result_label)

        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    # ─── Logic ───────────────────────────────────────────────────────

    def _load_current_settings(self) -> None:
        s = self._settings_svc.settings
        idx = self._startup_page_combo.findData(s.default_startup_page)
        if idx >= 0:
            self._startup_page_combo.setCurrentIndex(idx)
        self._restore_page_cb.setChecked(s.restore_last_page)
        self._demo_disconnect_cb.setChecked(s.demo_on_disconnect)
        self._remember_profile_cb.setChecked(s.remember_last_profile)
        self._chart_range_combo.setCurrentIndex(
            min(s.chart_default_range_index, self._chart_range_combo.count() - 1)
        )
        self._fun_mode_cb.setChecked(s.fun_mode)
        self._splash_cb.setChecked(s.show_splash)
        self._gpu_zero_rpm_warn_cb.setChecked(s.show_gpu_zero_rpm_warning)
        self._wizard_spindown_spin.setValue(s.wizard_spindown_seconds)
        self._startup_delay_spin.setValue(s.daemon_startup_delay_secs)
        self._hide_igpu_cb.setChecked(s.hide_igpu_sensors)
        self._hide_unused_fans_cb.setChecked(s.hide_unused_fan_headers)

        # Directory overrides (show override or default as placeholder)
        self._profiles_dir_label.setText(s.profiles_dir_override or str(profiles_dir()))
        self._themes_dir_label.setText(s.themes_dir_override or str(themes_dir()))
        self._export_dir_label.setText(s.export_default_dir or str(export_default_dir()))

    def _save_app_settings(self) -> None:
        from control_ofc.ui.microcopy import set_fun_mode

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
            remember_last_profile=self._remember_profile_cb.isChecked(),
            chart_default_range_index=self._chart_range_combo.currentIndex(),
            fun_mode=self._fun_mode_cb.isChecked(),
            show_splash=self._splash_cb.isChecked(),
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

        set_fun_mode(self._fun_mode_cb.isChecked())

        # Push startup delay to daemon if connected
        if self._client:
            try:
                self._client.set_startup_delay(self._startup_delay_spin.value())
            except Exception as e:
                log.warning("Failed to sync startup delay to daemon: %s", e)
                self._set_status("Application settings saved (startup delay not synced to daemon)")
                self.settings_changed.emit()
                return

        self._set_status("Application settings saved")
        self.settings_changed.emit()

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
                    except Exception as e:
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

    def _refresh_theme_list(self) -> None:
        self._theme_combo.clear()
        self._theme_combo.addItem("Default Dark", None)
        td = themes_dir()
        if td.exists():
            for p in sorted(td.glob("*.json")):
                try:
                    t = load_theme(p)
                    self._theme_combo.addItem(t.name, str(p))
                except Exception as e:
                    log.warning("Skipping invalid theme %s: %s", p, e)

    def _apply_selected_theme(self) -> None:
        path_str = self._theme_combo.currentData()
        if path_str is None:
            from control_ofc.ui.theme import default_dark_theme

            tokens = default_dark_theme()
        else:
            tokens = load_theme(Path(path_str))
        self._theme_name_label.setText(f"Current theme: {tokens.name}")
        self._theme_editor.set_tokens(tokens)
        # Sync font controls with loaded theme
        idx = self._font_combo.findData(tokens.font_family)
        if idx >= 0:
            self._font_combo.setCurrentIndex(idx)
        else:
            self._font_combo.setCurrentIndex(0)  # system default
        self._font_size_spin.setValue(tokens.base_font_size_pt)
        self._set_status(f"Theme '{tokens.name}' loaded into editor")

    def _save_current_theme(self) -> None:
        tokens = self._theme_editor.tokens
        name = tokens.name or "Custom"
        dest = themes_dir() / f"{name.lower().replace(' ', '_')}.json"
        save_theme(tokens, dest)
        self._refresh_theme_list()
        self._set_status(f"Theme '{name}' saved")

    def _apply_editor_theme_to_app(self) -> None:
        tokens = self._theme_editor.tokens
        # Apply typography settings from the font controls
        tokens.font_family = self._font_combo.currentData() or ""
        tokens.base_font_size_pt = self._font_size_spin.value()
        self._theme_name_label.setText(f"Current theme: {tokens.name}")
        self._settings_svc.update(theme_name=tokens.name)
        self.theme_changed.emit(tokens)
        self._set_status(f"Theme '{tokens.name}' applied to application")

    def _on_theme_edited(self, tokens) -> None:
        self._theme_name_label.setText(f"Current theme: {tokens.name} (modified)")

    def _import_theme(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Theme", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            tokens = load_theme(Path(path))
            dest = themes_dir() / Path(path).name
            save_theme(tokens, dest)
            self._refresh_theme_list()
            self._set_status(f"Theme '{tokens.name}' imported")
        except Exception as e:
            self._set_status(f"Import failed: {e}")

    def _export_theme(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme", "theme.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            tokens = self._theme_editor.tokens
            current_name = tokens.name
            save_theme(tokens, Path(path))
            self._set_status(f"Theme '{current_name}' exported")
        except Exception as e:
            self._set_status(f"Export failed: {e}")

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
        except Exception as e:
            self._set_export_result(f"Export failed: {e}", "CriticalChip")

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            # Read and validate
            raw = json.loads(Path(path).read_text())
            if not isinstance(raw, dict):
                self._set_export_result("Import failed: invalid file format", "CriticalChip")
                return

            export_ver = raw.get("export_version")
            if export_ver is not None and export_ver > 1:
                self._set_export_result(
                    f"Import failed: unsupported export version {export_ver} (max supported: 1)",
                    "CriticalChip",
                )
                return

            # Auto-backup current settings before import
            backup_path = self._create_backup()

            # Apply settings portion
            if "settings" in raw:
                imported = self._settings_svc.import_settings_from_dict(raw["settings"])
                self._settings_svc.apply_imported(imported)
                self._load_current_settings()

            # Apply profiles if present
            skipped = 0
            if "profiles" in raw:
                skipped += self._import_profiles(raw["profiles"])

            # Apply custom themes if present
            if "themes" in raw and isinstance(raw["themes"], dict):
                skipped += self._import_themes(raw["themes"])

            backup_msg = f" (backup: {backup_path.name})" if backup_path else ""
            skip_msg = f" ({skipped} invalid item(s) skipped)" if skipped else ""
            css = "WarningChip" if skipped else "SuccessChip"
            self._set_export_result(f"Settings imported and applied{backup_msg}{skip_msg}", css)
            self.settings_changed.emit()
        except Exception as e:
            self._set_export_result(f"Import failed: {e}", "CriticalChip")

    def _build_full_export(self) -> dict:
        """Build a comprehensive export covering all configurable state."""
        export: dict = {
            "export_version": 1,
            "exported_at": datetime.now().isoformat(),
            "settings": self._settings_svc.settings.to_dict(),
        }
        # Include profiles
        from control_ofc.paths import profiles_dir

        pdir = profiles_dir()
        if pdir.exists():
            profiles = {}
            for p in pdir.glob("*.json"):
                try:
                    profiles[p.stem] = json.loads(p.read_text())
                except Exception:
                    log.warning("Skipping unreadable profile: %s", p)
            if profiles:
                export["profiles"] = profiles

        # Include all custom themes (not just active)
        td = themes_dir()
        if td.exists():
            themes = {}
            for tf in td.glob("*.json"):
                try:
                    themes[tf.stem] = json.loads(tf.read_text())
                except Exception:
                    log.warning("Skipping unreadable theme: %s", tf)
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
            try:
                Profile.from_dict(data)
            except Exception as exc:
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
            dest.write_text(json.dumps(data, indent=2) + "\n")
        log.info("Imported %d profile(s)", len(valid))
        return len(skipped)

    def _import_themes(self, themes_data: dict) -> int:
        """Import custom theme JSON files from export data.

        Returns the number of themes that failed validation.
        """
        from control_ofc.ui.theme import _migrate_tokens

        td = themes_dir()
        td.mkdir(parents=True, exist_ok=True)

        valid: dict[str, dict] = {}
        skipped: list[str] = []
        for name, data in themes_data.items():
            if not isinstance(data, dict):
                log.warning("Skipping theme '%s': not a JSON object", name)
                skipped.append(name)
                continue
            try:
                migrated = _migrate_tokens(data)
                # Verify the migrated data can construct a ThemeTokens.
                tokens = ThemeTokens()
                for k, v in migrated.items():
                    if hasattr(tokens, k):
                        setattr(tokens, k, v)
            except Exception as exc:
                log.warning("Skipping theme '%s': validation failed: %s", name, exc)
                skipped.append(name)
                continue
            valid[name] = data

        for name, data in valid.items():
            dest = td / f"{name}.json"
            dest.write_text(json.dumps(data, indent=2) + "\n")
        log.info("Imported %d theme(s)", len(valid))
        return len(skipped)

    def _set_export_result(self, text: str, css_class: str) -> None:
        self._export_result_label.setText(text)
        old_class = self._export_result_label.property("class")
        if old_class != css_class:
            self._export_result_label.setProperty("class", css_class)
            self._export_result_label.style().unpolish(self._export_result_label)
            self._export_result_label.style().polish(self._export_result_label)

    def _on_capabilities_updated(self, caps) -> None:
        pass  # Reserved for future capability-gated settings

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)
