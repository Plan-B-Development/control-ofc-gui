"""DEC-109 — theme controllability, startup restoration, and WCAG AA coverage.

These tests cover the post-DEC-109 expectations:

- The active-theme registry returns the most recently registered tokens, and
  falls back to the default-dark tokens before anything is registered.
- ``check_contrast_warnings`` now flags every pair the audit found to be
  silently failing under the legacy default-dark theme.
- The expanded default-dark token set still passes every check it is now
  evaluated against.
- The two bundled presets load cleanly and themselves pass the AA pass.
- ``ensure_bundled_themes_installed`` copies presets on first run and is
  idempotent (does not clobber a user-edited file on subsequent runs).
- ``_resolve_startup_theme`` returns the persisted theme when its name
  matches a JSON file in ``themes_dir()``, and degrades gracefully when
  the file is missing, corrupted, or has a different name.
- The Diagnostics page and TimelineChart expose a ``set_theme`` method so
  the main window can re-render their theme-sensitive surfaces on the fly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.ui.theme import (
    ThemeTokens,
    active_theme,
    bundled_themes_dir,
    check_contrast_warnings,
    contrast_ratio,
    default_dark_theme,
    ensure_bundled_themes_installed,
    list_bundled_themes,
    load_theme,
    set_active_theme,
)

# ---------------------------------------------------------------------------
# Active-theme registry
# ---------------------------------------------------------------------------


class TestActiveThemeRegistry:
    def test_active_theme_defaults_to_default_dark(self):
        # Reset any previously registered theme to mimic a fresh process.
        set_active_theme(default_dark_theme())
        # Re-import to clear, then explicitly clear via module-level handle.
        from control_ofc.ui import theme as theme_module

        theme_module._active_theme = None
        t = active_theme()
        assert t.name == "Default Dark"

    def test_set_active_theme_round_trips(self):
        custom = ThemeTokens(name="Test Theme", app_bg="#123456")
        set_active_theme(custom)
        assert active_theme().name == "Test Theme"
        assert active_theme().app_bg == "#123456"
        # Restore default for other tests
        set_active_theme(default_dark_theme())


# ---------------------------------------------------------------------------
# Expanded contrast checks
# ---------------------------------------------------------------------------


class TestExpandedContrastChecks:
    """The audit identified WCAG-AA failures the old checker silently allowed.

    Each test mutates a copy of the default theme so that one specific token
    falls below its threshold and asserts the new checker surfaces it. These
    would have failed before DEC-109 because the old checker only inspected
    six token pairs.
    """

    def test_flags_primary_button_text_on_accent_below_aa(self):
        t = default_dark_theme()
        # Light text on bright accent — should fail AA 4.5:1
        t.accent_primary = "#7ec8e3"  # original cyan accent, 1.9:1 with white
        warnings = check_contrast_warnings(t)
        assert any("primary_btn_text" in w and "accent_primary" in w for w in warnings)

    def test_flags_primary_button_text_on_hover_below_aa(self):
        t = default_dark_theme()
        t.accent_secondary = "#7ec8e3"
        warnings = check_contrast_warnings(t)
        assert any("primary_btn_text" in w and "accent_secondary" in w for w in warnings)

    def test_flags_nav_text_active_on_nav_item_active_below_3(self):
        t = default_dark_theme()
        # Original failing pair: #4a90d9 on #2a4a7f = 2.6:1
        t.nav_text_active = "#4a90d9"
        t.nav_item_active = "#2a4a7f"
        warnings = check_contrast_warnings(t)
        assert any("nav_text_active" in w and "nav_item_active" in w for w in warnings)

    def test_flags_chart_axis_text_on_chart_bg_below_3(self):
        t = default_dark_theme()
        t.chart_axis_text = "#606878"  # original failing colour
        warnings = check_contrast_warnings(t)
        assert any("chart_axis_text" in w and "chart_bg" in w for w in warnings)

    def test_flags_input_placeholder_on_input_bg_below_3(self):
        t = default_dark_theme()
        t.input_placeholder = "#606878"  # original failing colour
        warnings = check_contrast_warnings(t)
        assert any("input_placeholder" in w and "input_bg" in w for w in warnings)

    def test_flags_text_muted_on_surface_2_below_3(self):
        t = default_dark_theme()
        t.text_muted = "#606878"  # original failing colour
        warnings = check_contrast_warnings(t)
        assert any("text_muted" in w and "surface_2" in w for w in warnings)


# ---------------------------------------------------------------------------
# Default-dark passes the expanded set
# ---------------------------------------------------------------------------


class TestDefaultDarkPassesAA:
    """The post-DEC-109 default-dark theme must pass every contrast pair
    the checker now enforces. If a future change reverts to a sub-AA value
    this test fires before the editor's 'No contrast issues' lies again."""

    def test_default_theme_has_no_warnings(self):
        t = default_dark_theme()
        warnings = check_contrast_warnings(t)
        assert warnings == [], f"Default Dark regressed: {warnings}"

    @pytest.mark.parametrize(
        ("fg", "bg", "minimum"),
        [
            ("text_primary", "surface_2", 4.5),
            ("text_muted", "surface_2", 3.0),
            ("primary_btn_text", "accent_primary", 4.5),
            ("primary_btn_text", "accent_secondary", 4.5),
            ("nav_text_active", "nav_item_active", 3.0),
            ("chart_axis_text", "chart_bg", 3.0),
            ("input_placeholder", "input_bg", 3.0),
        ],
    )
    def test_specific_pair_passes(self, fg, bg, minimum):
        t = default_dark_theme()
        ratio = contrast_ratio(getattr(t, fg), getattr(t, bg))
        assert ratio >= minimum, f"{fg} vs {bg} = {ratio:.2f}:1 (min {minimum})"


# ---------------------------------------------------------------------------
# Bundled presets
# ---------------------------------------------------------------------------


class TestBundledPresets:
    def test_at_least_two_presets_ship(self):
        files = list_bundled_themes()
        names = sorted(p.name for p in files)
        assert "solar_light.json" in names, names
        assert "noctua_dark.json" in names, names

    def test_each_preset_loads_cleanly(self):
        for path in list_bundled_themes():
            tokens = load_theme(path)
            assert tokens.name, f"Preset {path.name} has empty name"
            # Must have the expanded token set the GUI depends on.
            assert hasattr(tokens, "code_block_bg")
            assert isinstance(tokens.chart_series, list)
            assert len(tokens.chart_series) >= 6

    def test_each_preset_passes_aa(self):
        for path in list_bundled_themes():
            tokens = load_theme(path)
            warnings = check_contrast_warnings(tokens)
            assert warnings == [], f"{path.name} fails WCAG AA pairs: {warnings}"


# ---------------------------------------------------------------------------
# First-run install behaviour
# ---------------------------------------------------------------------------


class TestEnsureBundledThemesInstalled:
    def test_copies_presets_on_empty_dir(self, tmp_path):
        installed = ensure_bundled_themes_installed(tmp_path)
        bundled = list_bundled_themes()
        assert len(installed) == len(bundled)
        # Each copy is readable as a theme
        for path in installed:
            assert path.exists()
            load_theme(path)

    def test_idempotent_and_preserves_user_edits(self, tmp_path):
        # First run: copy presets
        ensure_bundled_themes_installed(tmp_path)
        # User edits a preset
        a_preset = next(iter(list_bundled_themes())).name
        target = tmp_path / a_preset
        edited = json.loads(target.read_text())
        edited["name"] = "User Edited"
        target.write_text(json.dumps(edited))
        # Second run should not overwrite
        installed_again = ensure_bundled_themes_installed(tmp_path)
        assert installed_again == []
        assert json.loads(target.read_text())["name"] == "User Edited"

    def test_bundled_dir_lives_inside_package(self):
        d = bundled_themes_dir()
        assert d.exists(), f"Bundled themes dir missing: {d}"
        # Must be inside the package tree so the installed wheel ships it.
        assert "control_ofc" in d.parts


# ---------------------------------------------------------------------------
# Startup theme resolution
# ---------------------------------------------------------------------------


class TestStartupThemeResolution:
    def test_returns_default_when_name_empty(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)
        tokens = main_module._resolve_startup_theme("")
        assert tokens.name == "Default Dark"

    def test_returns_default_when_name_default(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)
        tokens = main_module._resolve_startup_theme("Default Dark")
        assert tokens.name == "Default Dark"

    def test_loads_matching_preset_by_name(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        # Drop a custom theme file in tmp_path
        custom = {"name": "My Custom", "version": 2, "app_bg": "#abcdef"}
        (tmp_path / "anything.json").write_text(json.dumps(custom))
        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)

        tokens = main_module._resolve_startup_theme("My Custom")
        assert tokens.name == "My Custom"
        assert tokens.app_bg == "#abcdef"

    def test_falls_back_when_persisted_name_missing(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)
        tokens = main_module._resolve_startup_theme("Nonexistent Theme")
        assert tokens.name == "Default Dark"

    def test_skips_corrupted_files_and_keeps_searching(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        (tmp_path / "broken.json").write_text("{not valid json")
        good = {"name": "Good", "version": 2}
        (tmp_path / "good.json").write_text(json.dumps(good))
        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)

        tokens = main_module._resolve_startup_theme("Good")
        assert tokens.name == "Good"

    def test_returns_default_when_themes_dir_missing(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module

        missing = tmp_path / "does_not_exist"
        monkeypatch.setattr(main_module, "themes_dir", lambda: missing)
        tokens = main_module._resolve_startup_theme("Whatever")
        assert tokens.name == "Default Dark"


# ---------------------------------------------------------------------------
# Theme propagation on switch
# ---------------------------------------------------------------------------


class TestTimelineChartSetTheme:
    """TimelineChart.set_theme must update the plot background, axes,
    crosshair colour, and existing series pens — pre-DEC-109 these were
    pinned to the construction-time snapshot of the default-dark theme."""

    def test_set_theme_method_exists(self):
        from control_ofc.ui.widgets.timeline_chart import TimelineChart

        assert hasattr(TimelineChart, "set_theme")

    def test_set_theme_swaps_internal_theme_handle(self, qtbot):
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.widgets.timeline_chart import TimelineChart

        history = HistoryStore()
        chart = TimelineChart(history=history)
        qtbot.addWidget(chart)
        custom = ThemeTokens(name="Switch Test", chart_bg="#abcdef")
        chart.set_theme(custom)
        assert chart._theme.chart_bg == "#abcdef"


class TestDiagnosticsPageSetTheme:
    """DiagnosticsPage.set_theme must repaint freshness cells from cached
    state so colours follow theme changes (previously stuck on default-dark)."""

    def test_set_theme_method_exists(self):
        from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

        assert hasattr(DiagnosticsPage, "set_theme")


class TestDashboardPageSetTheme:
    """DashboardPage.set_theme exists so the main window can propagate
    a theme change to the inline command label and timeline chart."""

    def test_set_theme_method_exists(self):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        assert hasattr(DashboardPage, "set_theme")


# ---------------------------------------------------------------------------
# Reclaim severity colour reads live theme
# ---------------------------------------------------------------------------


class TestReclaimSeverityColorFollowsActiveTheme:
    def test_reclaim_color_changes_when_active_theme_changes(self):
        from control_ofc.ui.pages.diagnostics_page import (
            RECLAIM_SEVERITY_HIGH,
            RECLAIM_SEVERITY_OK,
            RECLAIM_SEVERITY_WARN,
            reclaim_severity_color,
        )

        custom = ThemeTokens(
            name="Reclaim Test",
            status_ok="#111111",
            status_warn="#222222",
            status_crit="#333333",
        )
        set_active_theme(custom)
        try:
            assert reclaim_severity_color(RECLAIM_SEVERITY_OK) == "#111111"
            assert reclaim_severity_color(RECLAIM_SEVERITY_WARN) == "#222222"
            assert reclaim_severity_color(RECLAIM_SEVERITY_HIGH) == "#333333"
        finally:
            set_active_theme(default_dark_theme())


# ---------------------------------------------------------------------------
# Hardcoded-style cleanup (DEC-109)
# ---------------------------------------------------------------------------


class TestNoStaleDefaultDarkSnapshots:
    """The four offenders identified in the audit must no longer pin their
    theme to ``default_dark_theme()`` at import or construction time."""

    def test_diagnostics_page_has_no_module_level_theme_snapshot(self):
        src = Path("src/control_ofc/ui/pages/diagnostics_page.py").read_text()
        # The previous module-level _THEME = default_dark_theme() snapshot
        # is what made theme switches a no-op on the freshness columns.
        assert "_THEME = default_dark_theme()" not in src
        # active_theme is the new source of truth
        assert "active_theme" in src

    def test_settings_page_uses_active_theme_for_dir_picker(self):
        src = Path("src/control_ofc/ui/pages/settings_page.py").read_text()
        # The dir-picker label colour must come from active_theme, not the
        # default-dark snapshot.
        assert "default_dark_theme().text_muted" not in src

    def test_dashboard_page_command_label_uses_token(self):
        src = Path("src/control_ofc/ui/pages/dashboard_page.py").read_text()
        # The rgba(0,0,0,0.25) inline style is the original hardcoded value.
        assert "rgba(0,0,0,0.25)" not in src
        # And the new path uses the code_block_bg token via active_theme.
        assert "code_block_bg" in src

    def test_timeline_chart_uses_active_theme(self):
        src = Path("src/control_ofc/ui/widgets/timeline_chart.py").read_text()
        # The chart must read active_theme() rather than snapshotting the
        # default-dark tokens in __init__.
        assert "active_theme" in src
        assert "def set_theme" in src
