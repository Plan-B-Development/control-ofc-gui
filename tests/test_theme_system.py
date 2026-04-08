"""Tests for the theme system — tokens, contrast, migration, and hardcoded color lint."""

from __future__ import annotations

import re
from pathlib import Path

from onlyfans.ui.theme import (
    _migrate_tokens,
    build_stylesheet,
    check_contrast_warnings,
    contrast_ratio,
    default_dark_theme,
    load_theme,
    save_theme,
)

# ---------------------------------------------------------------------------
# Token coverage and consistency
# ---------------------------------------------------------------------------


class TestThemeBasics:
    def test_default_dark_theme_has_name(self):
        t = default_dark_theme()
        assert t.name == "Default Dark"

    def test_default_dark_theme_has_chart_series(self):
        t = default_dark_theme()
        assert len(t.chart_series) >= 6

    def test_load_theme_ignores_unknown_keys(self, tmp_path):
        import json

        path = tmp_path / "future_theme.json"
        path.write_text(json.dumps({"name": "Future", "unknown_field": True}))
        loaded = load_theme(path)
        assert loaded.name == "Future"


class TestTokenCoverage:
    def test_default_theme_has_all_required_tokens(self):
        """Default theme must define all spec-required tokens."""
        t = default_dark_theme()
        required = [
            "app_bg",
            "surface_1",
            "surface_2",
            "surface_3",
            "text_primary",
            "text_secondary",
            "text_muted",
            "accent_primary",
            "accent_secondary",
            "border_default",
            "border_focus",
            "divider",
            "hover_bg",
            "pressed_bg",
            "selected_bg",
            "focus_ring",
            "disabled_bg",
            "disabled_text",
            "status_ok",
            "status_warn",
            "status_crit",
            "status_info",
            "chart_bg",
            "chart_grid",
            "chart_axis_text",
            "chart_line_primary",
            "chart_point",
            "chart_point_selected",
            "chart_point_hover",
            "nav_bg",
            "nav_text",
            "nav_text_active",
            "nav_item_hover",
            "nav_item_active",
            "input_bg",
            "input_text",
            "input_placeholder",
            "input_border",
            "input_border_focus",
            "modal_bg",
            "modal_border",
            "table_header_bg",
            "table_row_bg",
            "table_row_alt_bg",
            "table_row_hover_bg",
            "table_text",
            "primary_btn_text",
        ]
        for token in required:
            assert hasattr(t, token), f"Missing token: {token}"
            value = getattr(t, token)
            assert isinstance(value, str) and value.startswith("#"), (
                f"Token {token} = {value!r} is not a hex colour"
            )

    def test_stylesheet_uses_all_core_tokens(self):
        """build_stylesheet must reference the major tokens."""
        t = default_dark_theme()
        ss = build_stylesheet(t)
        # Spot-check that token values appear in the stylesheet
        assert t.app_bg in ss
        assert t.surface_1 in ss
        assert t.text_primary in ss
        assert t.accent_primary in ss
        assert t.nav_bg in ss
        assert t.input_bg in ss
        assert t.table_header_bg in ss


# ---------------------------------------------------------------------------
# Contrast checking
# ---------------------------------------------------------------------------


class TestContrastChecker:
    def test_high_contrast_passes(self):
        """White on dark blue should have high contrast."""
        ratio = contrast_ratio("#ffffff", "#1a1a2e")
        assert ratio > 10

    def test_low_contrast_detected(self):
        """Similar colors should have low contrast."""
        ratio = contrast_ratio("#505050", "#606060")
        assert ratio < 2

    def test_default_theme_has_no_warnings(self):
        """Default dark theme should pass all contrast checks."""
        t = default_dark_theme()
        warnings = check_contrast_warnings(t)
        assert len(warnings) == 0, f"Unexpected warnings: {warnings}"

    def test_bad_theme_triggers_warning(self):
        """A theme with same text and background should warn."""
        t = default_dark_theme()
        t.text_primary = t.surface_1  # same color -> zero contrast
        warnings = check_contrast_warnings(t)
        assert len(warnings) > 0
        assert "text_primary" in warnings[0]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestTokenMigration:
    def test_old_tokens_migrate_to_new_names(self):
        """Old v1 token names should map to v2 spec names."""
        old_data = {
            "name": "Old Theme",
            "version": 1,
            "window_bg": "#111111",
            "panel_bg": "#222222",
            "raised_surface": "#333333",
            "border": "#444444",
            "success": "#00ff00",
            "warning": "#ffaa00",
            "critical": "#ff0000",
            "selection": "#0000ff",
        }
        migrated = _migrate_tokens(old_data)
        assert migrated["app_bg"] == "#111111"
        assert migrated["surface_1"] == "#222222"
        assert migrated["surface_2"] == "#333333"
        assert migrated["border_default"] == "#444444"
        assert migrated["status_ok"] == "#00ff00"
        assert migrated["status_warn"] == "#ffaa00"
        assert migrated["status_crit"] == "#ff0000"
        assert migrated["selected_bg"] == "#0000ff"
        assert migrated["version"] == 2

    def test_load_theme_with_old_tokens(self, tmp_path):
        """Loading a v1 theme file should produce v2 tokens."""
        import json

        old_theme = {
            "name": "Legacy",
            "version": 1,
            "window_bg": "#0a0a0a",
            "panel_bg": "#1a1a1a",
            "text_primary": "#f0f0f0",
        }
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(old_theme))

        tokens = load_theme(path)
        assert tokens.app_bg == "#0a0a0a"
        assert tokens.surface_1 == "#1a1a1a"
        assert tokens.text_primary == "#f0f0f0"


# ---------------------------------------------------------------------------
# Save/Load roundtrip
# ---------------------------------------------------------------------------


class TestThemeRoundtrip:
    def test_save_and_load(self, tmp_path):
        t = default_dark_theme()
        t.name = "Test Roundtrip"
        t.accent_primary = "#ff00ff"

        path = tmp_path / "test.json"
        save_theme(t, path)
        loaded = load_theme(path)

        assert loaded.name == "Test Roundtrip"
        assert loaded.accent_primary == "#ff00ff"
        assert loaded.app_bg == t.app_bg

    def test_import_with_missing_tokens_uses_defaults(self, tmp_path):
        """Import a theme with only a few tokens — rest should fall back to defaults."""
        import json

        partial = {"name": "Partial", "version": 2, "app_bg": "#000000"}
        path = tmp_path / "partial.json"
        path.write_text(json.dumps(partial))

        tokens = load_theme(path)
        assert tokens.app_bg == "#000000"
        # Missing tokens should have defaults
        default = default_dark_theme()
        assert tokens.text_primary == default.text_primary
        assert tokens.accent_primary == default.accent_primary


# ---------------------------------------------------------------------------
# Hardcoded color lint
# ---------------------------------------------------------------------------

# Pattern: hex color literal (#RRGGBB or #RRGGBBAA) outside of theme.py defaults
_HEX_PATTERN = re.compile(r'(?<!")#[0-9a-fA-F]{6,8}(?!")')


class TestNoHardcodedColors:
    def test_no_hardcoded_hex_in_widget_code(self):
        """Widget and page code must not contain hardcoded hex colours."""
        src_dir = Path(__file__).parent.parent / "src" / "onlyfans"
        violations = []

        # Directories to check (widgets, pages, services — NOT theme.py itself)
        check_dirs = [
            src_dir / "ui" / "pages",
            src_dir / "ui" / "widgets",
            src_dir / "ui",  # sidebar, status_banner, splash, about_dialog, branding
        ]
        # theme_editor.py is allowed to have hex in swatch styling
        allowed_files = {"theme_editor.py", "theme.py"}

        for check_dir in check_dirs:
            if not check_dir.exists():
                continue
            for py_file in check_dir.glob("*.py"):
                if py_file.name in allowed_files:
                    continue
                content = py_file.read_text()
                for line_no, line in enumerate(content.splitlines(), 1):
                    # Skip comments
                    stripped = line.lstrip()
                    if stripped.startswith("#"):
                        continue
                    matches = _HEX_PATTERN.findall(line)
                    if matches:
                        violations.append(f"{py_file.name}:{line_no}: {matches}")

        assert not violations, "Hardcoded hex colours found in widget/page code:\n" + "\n".join(
            violations
        )


# ---------------------------------------------------------------------------
# R54 — Color dialog and startup page regressions
# ---------------------------------------------------------------------------


class TestColorDialogAppStylesheetCleared:
    """Color dialogs clear app stylesheet to prevent QWidget cascade corruption (R58)."""

    def test_theme_editor_clears_app_stylesheet(self):
        """theme_editor.py clears app stylesheet before QColorDialog."""
        import inspect

        from onlyfans.ui.widgets.theme_editor import ColorSwatch

        source = inspect.getsource(ColorSwatch._pick_color)
        assert "DontUseNativeDialog" in source
        assert 'app.setStyleSheet("")' in source or "app.setStyleSheet('')" in source
        assert "saved_stylesheet" in source
        assert "self.window()" in source

    def test_sensor_series_panel_clears_app_stylesheet(self):
        """sensor_series_panel.py clears app stylesheet before QColorDialog."""
        from pathlib import Path

        src = Path("src/onlyfans/ui/widgets/sensor_series_panel.py").read_text()
        assert "DontUseNativeDialog" in src
        assert 'app.setStyleSheet("")' in src or "app.setStyleSheet('')" in src
        assert "saved_stylesheet" in src
        assert "self.window()" in src


class TestStartupPageNavSync:
    """Sidebar selection must match restored page on startup (R54)."""

    def test_sidebar_matches_restored_page(self, qtbot):
        import os
        import tempfile

        from onlyfans.api.models import ConnectionState, OperationMode
        from onlyfans.constants import PAGE_SETTINGS
        from onlyfans.services.app_settings_service import AppSettingsService
        from onlyfans.services.app_state import AppState
        from onlyfans.services.profile_service import ProfileService
        from onlyfans.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            settings_svc = AppSettingsService()
            settings_svc.update(
                restore_last_page=True,
                last_page_index=PAGE_SETTINGS,
            )

            state = AppState()
            state.connection = ConnectionState.DISCONNECTED
            state.mode = OperationMode.READ_ONLY

            profile_svc = ProfileService()
            profile_svc.load()

            win = MainWindow(
                state=state,
                settings_service=settings_svc,
                profile_service=profile_svc,
            )
            qtbot.addWidget(win)

            assert win.page_stack.currentIndex() == PAGE_SETTINGS
            checked_btn = win.sidebar._group.checkedId()
            assert checked_btn == PAGE_SETTINGS
