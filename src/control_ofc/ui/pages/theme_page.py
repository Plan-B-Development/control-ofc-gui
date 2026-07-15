"""Theme page — theme editor, presets, typography, and app-wide apply (DEC-215).

Extracted from the old Settings ▸ Themes tab into its own ``PAGE_THEME`` page. Hosts
the reusable ``ThemeEditorWidget`` and owns the preset combo + Load/Save/Import/Export,
the Font / Base-Size / Card-Size controls, and Apply-Theme-Globally — the sole emitter
of ``theme_changed`` → ``main_window._on_theme_changed``. All colour comes from the
editor + the global QSS, so this page is hex-free (the no-hardcoded-colours guard).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from control_ofc.paths import themes_dir
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card
from control_ofc.ui.theme import ThemeTokens, default_dark_theme, load_theme, save_theme
from control_ofc.ui.widgets.theme_editor import ThemeEditorWidget

log = logging.getLogger(__name__)


class ThemePage(QWidget):
    """The Theme Configuration page (DEC-215)."""

    theme_changed = Signal(ThemeTokens)

    def __init__(self, settings_service: AppSettingsService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Theme_Root")
        self._settings_svc = settings_service or AppSettingsService()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ─── Header: title + subtitle + preset combo + Load/Save/Import/Export ─
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Theme Configuration")
        title.setProperty("class", "PageTitle")
        header.addWidget(title)
        subtitle = QLabel("Application appearance, typography, and contrast")
        subtitle.setProperty("class", "PageSubtitle")
        header.addWidget(subtitle)
        header.addStretch()

        self._theme_combo = QComboBox()
        self._theme_combo.setObjectName("Settings_Combo_theme")
        self._theme_combo.setMinimumWidth(150)
        self._refresh_theme_list()
        header.addWidget(self._theme_combo)

        load_btn = make_button("Load", "secondary", object_name="Settings_Btn_applyTheme")
        load_btn.setToolTip("Load selected theme into editor")
        load_btn.clicked.connect(self._apply_selected_theme)
        header.addWidget(load_btn)
        save_btn = make_button("Save", "primary", object_name="Settings_Btn_saveTheme")
        save_btn.setToolTip("Save current edits as a theme file")
        save_btn.clicked.connect(self._save_current_theme)
        header.addWidget(save_btn)
        import_btn = make_button("Import…", "secondary", object_name="Settings_Btn_importTheme")
        import_btn.clicked.connect(self._import_theme)
        header.addWidget(import_btn)
        export_btn = make_button("Export…", "secondary", object_name="Settings_Btn_exportTheme")
        export_btn.clicked.connect(self._export_theme)
        header.addWidget(export_btn)
        root.addLayout(header)

        self._theme_name_label = QLabel("Current theme: Default Dark")
        self._theme_name_label.setProperty("class", "CardMeta")
        root.addWidget(self._theme_name_label)

        # ─── Typography card: Font Family / Base Size / Card Size ─
        typo = Card()
        typo_layout = QHBoxLayout(typo)
        typo_layout.setSpacing(12)
        typo_layout.addWidget(QLabel("Font Family:"))
        self._font_combo = QComboBox()
        self._font_combo.setObjectName("Settings_Combo_fontFamily")
        self._font_combo.addItem("(System Default)", "")
        for family in QFontDatabase.families():
            self._font_combo.addItem(family, family)
        typo_layout.addWidget(self._font_combo, 1)
        typo_layout.addWidget(QLabel("Base Size:"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setObjectName("Settings_Spin_fontSize")
        self._font_size_spin.setRange(7, 16)
        self._font_size_spin.setValue(10)
        self._font_size_spin.setSuffix(" pt")
        typo_layout.addWidget(self._font_size_spin)
        typo_layout.addWidget(QLabel("Card Size:"))
        self._card_size_combo = QComboBox()
        self._card_size_combo.setObjectName("Settings_Combo_cardSize")
        self._card_size_combo.setToolTip(
            "Density of the Fan Role and Curve cards on the Controls page.\n"
            "Cards also scale automatically with the font size."
        )
        for label, value in (
            ("Compact", "compact"),
            ("Comfortable", "comfortable"),
            ("Large", "large"),
        ):
            self._card_size_combo.addItem(label, value)
        typo_layout.addWidget(self._card_size_combo)
        root.addWidget(typo)

        # ─── Theme editor (colour grid + UI blueprint preview + contrast) ─
        self._theme_editor = ThemeEditorWidget()
        self._theme_editor.setObjectName("Settings_ThemeEditor")
        self._theme_editor.theme_modified.connect(self._on_theme_edited)
        root.addWidget(self._theme_editor, 1)

        # ─── Apply globally ─
        apply_btn = make_button(
            "Apply Theme Globally", "primary", object_name="Settings_Btn_applyThemeToApp"
        )
        apply_btn.setToolTip("Apply the current editor state to the whole application")
        apply_btn.clicked.connect(self._apply_editor_theme_to_app)
        root.addWidget(apply_btn)

        self._status_label = QLabel("")
        self._status_label.setObjectName("Theme_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        root.addWidget(self._status_label)

        self._load_theme_settings()

    # ─── Lifecycle ───────────────────────────────────────────────────

    def cleanup(self) -> None:
        """No-op — the editor owns no timers/threads (closeEvent symmetry)."""

    def _load_theme_settings(self) -> None:
        idx = self._card_size_combo.findData(self._settings_svc.settings.card_size)
        if idx >= 0:
            self._card_size_combo.setCurrentIndex(idx)

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    # ─── Theme handlers (moved verbatim from settings_page, DEC-215) ──

    def _refresh_theme_list(self) -> None:
        self._theme_combo.clear()
        self._theme_combo.addItem("Default Dark", None)
        td = themes_dir()
        if td.exists():
            for p in sorted(td.glob("*.json")):
                try:
                    t = load_theme(p)
                    self._theme_combo.addItem(t.name, str(p))
                except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
                    log.warning("Skipping invalid theme %s: %s", p, e)

    def _apply_selected_theme(self) -> None:
        path_str = self._theme_combo.currentData()
        tokens = default_dark_theme() if path_str is None else load_theme(Path(path_str))
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
        # Persist the card-size tier before emitting so the Controls page reads
        # the new value when set_theme re-applies card sizing (DEC-128).
        self._settings_svc.update(
            theme_name=tokens.name, card_size=self._card_size_combo.currentData()
        )
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
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
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
        except (OSError, ValueError) as e:
            self._set_status(f"Export failed: {e}")
