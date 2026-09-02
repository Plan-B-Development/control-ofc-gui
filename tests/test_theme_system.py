"""Tests for the theme system — tokens, contrast, migration, and hardcoded color lint."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar
from unittest import mock

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from control_ofc.ui.theme import (
    _PALETTE_DISABLED_ROLES,
    _PALETTE_ROLES,
    _migrate_tokens,
    build_palette,
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


class TestFanCardStyling:
    """DEC-222: the fan zone/tile QSS was removed with those widgets. The new fan
    cards style themselves from the shared card + chip classes, so those are what
    must exist — a missing one would render the cards unstyled."""

    def test_fan_card_classes_present_in_stylesheet(self):
        qss = build_stylesheet(default_dark_theme())
        for cls in (
            ".Card",
            ".CardValue",
            ".CardMeta",
            ".SuccessChip",
            ".WarningChip",
            ".CriticalChip",
            ".InfoChip",
        ):
            assert cls in qss, cls

    def test_ghost_buttons_have_a_visible_focus_indicator(self):
        """WCAG 2.4.7. The ghost variant is transparent fill *and* transparent
        border, and a QSS-styled button loses Qt's native focus rect — so without
        an explicit rule a tab-focused ghost button is invisible. That is the
        Dashboard tile's only action, and the Cancel/Discard button on every
        modal footer."""
        qss = build_stylesheet(default_dark_theme())
        assert 'QPushButton[variant="ghost"]:focus' in qss
        rule = qss.split('QPushButton[variant="ghost"]:focus')[1].split("}")[0]
        assert "border" in rule, "focus rule sets no border — nothing would be visible"
        tokens = default_dark_theme()
        # Not the accent: accent is the primary-action language, and focus landing
        # on a modal's ghost Cancel would then match its filled-accent Save.
        assert tokens.accent_primary not in rule
        assert tokens.text_primary in rule

    def test_fan_tile_density_rules_are_scoped_to_the_tile(self):
        """DEC-238's density rules must never leak to page-level cards, which
        want the roomier inset. Scoping is by attribute selector so it also beats
        plain `.Card` regardless of rule order."""
        qss = build_stylesheet(default_dark_theme())
        assert '.Card[density="tile"]' in qss
        # The bare .Card padding must survive alongside it.
        assert "padding: 12px" in qss

    def test_retired_fan_zone_classes_are_gone(self):
        """The zone/tile rules had no other consumer; leaving them would be dead
        QSS shipped to every user."""
        qss = build_stylesheet(default_dark_theme())
        for cls in (".FanGroupCard", ".FanGroupTitle", ".FanGroupChip", ".FanTile"):
            assert cls not in qss, cls


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
            "hover_bg",
            "pressed_bg",
            "selected_bg",
            "disabled_bg",
            "disabled_text",
            "status_ok",
            "status_warn",
            "status_crit",
            "status_info",
            "chart_bg",
            "chart_grid",
            "chart_axis_text",
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

    def test_migrate_preserves_unchanged_chart_grid(self):
        """B1: chart_grid is not renamed, so migration must NOT drop a custom
        value. Regression — an identity entry in the token map deleted it on
        every load."""
        migrated = _migrate_tokens({"name": "X", "version": 2, "chart_grid": "#ff0000"})
        assert migrated["chart_grid"] == "#ff0000"

    def test_load_theme_preserves_custom_chart_grid(self, tmp_path):
        """B1 end-to-end: a saved theme's custom gridline colour must survive
        load_theme. The direct-construction test in test_chart_theme_adherence
        cannot catch this — it bypasses migration."""
        import json

        path = tmp_path / "grid.json"
        path.write_text(json.dumps({"name": "Grid", "version": 2, "chart_grid": "#ff0000"}))
        tokens = load_theme(path)
        assert tokens.chart_grid == "#ff0000"


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

# Hex colour literal: ``#`` + 3-8 hex chars ending at a word boundary.
# Deliberately NO quote lookarounds: the retired pattern
# ``r'(?<!")#[0-9a-fA-F]{6,8}(?!")'`` excluded any hex adjacent to a quote —
# which is exactly the shape a real violation takes (``QColor("#ff0000")``,
# ``BG = "#101014"`` and ``setStyleSheet("color: #ff0000")`` all went unseen),
# and its ``{6,8}`` floor also missed 3/4-digit CSS shorthands like ``#fff``.
# False positives are suppressed structurally instead (see the helpers below).
_HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b")

_HEX_LETTERS = frozenset("abcdefABCDEF")


def _iter_code_strings(tree: ast.AST):
    """Yield ``(lineno, value)`` for every string literal that is *code* —
    i.e. every string constant except docstrings.

    In Python a ``#`` outside a string literal always starts a comment, and
    comments never reach the AST — so scanning non-docstring string constants
    (f-string literal chunks included, via their ``Constant`` parts) covers
    every place a functioning hex colour can live while structurally dropping
    comment/docstring prose."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


#: A CSS/Qt colour keyword anywhere in the enclosing string literal. Used to
#: rescue all-decimal shorthands (``#000``) from the prose exemption below.
_CSS_CTX = re.compile(r"(?i)color|background|border|solid|rgba?\(")


def _is_colour_shaped(match: str, enclosing: str = "") -> bool:
    """True when *match* (``#`` + hex chars) is a renderable colour literal.

    3/4-digit matches made only of decimal digits are usually prose references —
    the hwmon guidance strings cite "PR #114" / "issue #106" and HTML entities
    like ``&#8226;`` — so those require at least one hex *letter* (keeping
    ``#fff`` and ``#f00c`` caught)... **unless** the enclosing string looks like
    CSS, which rescues ``#000``/``#111``. Without that rescue the guard was blind
    to pure black, the single most likely hardcoded value and exactly the
    "hard black on a dark surface" antipattern the visual rules ban.

    6/8-digit matches are always colour-shaped: an all-decimal ``#101014`` is a
    real colour. 5/7 hex digits is not a valid CSS/Qt colour form at all.
    """
    digits = match[1:]
    if len(digits) in (6, 8):
        return True
    if len(digits) in (3, 4):
        return any(c in _HEX_LETTERS for c in digits) or bool(_CSS_CTX.search(enclosing))
    return False


def _find_hex_violations(source: str) -> list[str]:
    """All hardcoded hex colours in *source*, as ``"line: match"`` strings."""
    violations = []
    for lineno, value in _iter_code_strings(ast.parse(source)):
        for m in _HEX_PATTERN.finditer(value):
            if _is_colour_shaped(m.group(0), value):
                # Line of the match within a (real-newline) multi-line literal.
                line = lineno + value.count("\n", 0, m.start())
                violations.append(f"{line}: {m.group(0)}")
    return violations


class TestNoHardcodedColors:
    def test_no_hardcoded_hex_in_widget_code(self):
        """Widget, page, and service code must not contain hardcoded hex colours."""
        src_dir = Path(__file__).parent.parent / "src" / "control_ofc"
        violations = []

        # Recursive over the whole UI tree — pages, widgets, components, and any
        # future ui/<subpackage>/ are scanned automatically — plus the Qt-free
        # view-model layer in services/.
        check_dirs = [
            src_dir / "ui",
            src_dir / "services",
        ]
        # theme.py owns the tokens; theme_editor.py styles swatches from them.
        # Matched on the path, not the bare name: under a recursive walk a future
        # services/theme.py would otherwise be silently exempted.
        allowed_files = {"ui/theme.py", "ui/widgets/theme_editor.py"}

        for check_dir in check_dirs:
            assert check_dir.exists(), f"scanned directory vanished: {check_dir}"
            for py_file in sorted(check_dir.rglob("*.py")):
                if py_file.relative_to(src_dir).as_posix() in allowed_files:
                    continue
                for violation in _find_hex_violations(py_file.read_text()):
                    violations.append(f"{py_file.relative_to(src_dir)}:{violation}")

        assert not violations, "Hardcoded hex colours found in widget/page code:\n" + "\n".join(
            violations
        )


class TestHexGuardSelfTest:
    """The old guard's quote lookarounds excluded exactly the shape a real
    violation takes, so it could not flag anything realistic — and nothing
    noticed, because a lint that finds 0 violations looks identical to one that
    cannot. These pin the detector itself so it cannot silently rot again."""

    @pytest.mark.parametrize(
        "snippet",
        [
            'c = QColor("#ff0000")',
            'BG = "#101014"',  # all-decimal 6-digit is still a colour
            'w.setStyleSheet("color: #ff0000")',
            'w.setStyleSheet(f"color: #ff0000; background: {tok};")',  # f-string chunk
            'w.setStyleSheet("QFrame { background: #fff; }")',  # 3-digit shorthand
            'EDGE = "#ff0000cc"',  # 8-digit #RRGGBBAA
            'TINT = "#0f0a"',  # 4-digit #RGBA shorthand
            'PALETTE = ["#123abc", "#abc123"]',
            'QSS = """\n.Card {\n  color: #ff0000;\n}\n"""',  # multi-line literal
        ],
    )
    def test_catches_previously_missed_forms(self, snippet):
        assert _find_hex_violations(snippet), f"guard missed: {snippet!r}"

    def test_reports_the_line_inside_a_multiline_literal(self):
        snippet = 'QSS = """\n.Card {\n  color: #ff0000;\n}\n"""'
        assert _find_hex_violations(snippet) == ["3: #ff0000"]

    @pytest.mark.parametrize(
        "snippet",
        [
            "x = 1  # legacy tint was #ff0000",  # trailing comment
            "# full-line comment mentioning #ff0000",
            '"""Module docstring citing #ff0000."""',
            'def f():\n    """Docstring citing #ff0000."""\n    return 1',
            'class C:\n    """Class docstring citing #ff0000."""',
            # Decimal 3/4-digit prose in *data* strings (hwmon_guidance.py style):
            'MSG = "fixed by frankcrawford/it87 PR #114 and issue #106"',
            'ROCM = "see ROCm issue #6101 for the SMU regression"',
            'BULLET = "&#8226;&nbsp;"',  # HTML entity
            'tinted = f"color: {tokens.text_muted};"',  # token-driven, no literal
            'BAD_SHAPE_5 = "#12abc"',  # 5 digits — not a renderable colour form
            'BAD_SHAPE_7 = "#1234abc"',  # 7 digits — not a renderable colour form
            'LONG_RUN = "#123456789abc"',  # >8 hex chars — an id, not a colour
        ],
    )
    def test_ignores_prose_comments_and_docstrings(self, snippet):
        assert _find_hex_violations(snippet) == [], f"false positive on: {snippet!r}"

    @pytest.mark.parametrize(
        "snippet",
        [
            'w.setStyleSheet("background: #000")',
            'w.setStyleSheet("border: 1px solid #111")',
            'QSS = "QFrame { background-color: #000; }"',
            'TINT = "color: #0009"',  # 4-digit all-decimal, CSS context
        ],
    )
    def test_catches_all_decimal_shorthand_in_css_context(self, snippet):
        """All-decimal 3/4-digit shorthands are exempted as prose ("PR #114"),
        which used to let ``#000`` — the likeliest hardcoded value of all —
        through. A CSS keyword in the same literal rescues them."""
        assert _find_hex_violations(snippet), f"guard missed: {snippet!r}"

    @pytest.mark.parametrize(
        "snippet",
        [
            'MSG = "tracked in PR #120 and issue #106"',
            'NOTE = "kernel bug #4498 affects this chip"',
        ],
    )
    def test_css_rescue_does_not_flag_prose_without_css_words(self, snippet):
        """The rescue must stay scoped — prose issue numbers carry no CSS
        keyword, so they are still exempt."""
        assert _find_hex_violations(snippet) == [], f"false positive on: {snippet!r}"


# ---------------------------------------------------------------------------
# R54 — Color dialog and startup page regressions
# ---------------------------------------------------------------------------


class TestColorDialogAppStylesheetCleared:
    """Color dialogs clear app stylesheet to prevent QWidget cascade corruption (R58)."""

    def test_theme_editor_clears_app_stylesheet(self):
        """theme_editor.py clears app stylesheet before QColorDialog."""
        import inspect

        from control_ofc.ui.widgets.theme_editor import ColorSwatch

        source = inspect.getsource(ColorSwatch._pick_color)
        assert "DontUseNativeDialog" in source
        assert 'app.setStyleSheet("")' in source or "app.setStyleSheet('')" in source
        assert "saved_stylesheet" in source
        assert "self.window()" in source

    def test_sensor_series_panel_clears_app_stylesheet(self):
        """sensor_series_panel.py clears app stylesheet before QColorDialog."""
        from pathlib import Path

        src = Path("src/control_ofc/ui/widgets/sensor_series_panel.py").read_text()
        assert "DontUseNativeDialog" in src
        assert 'app.setStyleSheet("")' in src or "app.setStyleSheet('')" in src
        assert "saved_stylesheet" in src
        assert "self.window()" in src


class TestStartupPageNavSync:
    """Sidebar selection must match restored page on startup (R54)."""

    def test_sidebar_matches_restored_page(self, qtbot):
        import os
        import tempfile

        from control_ofc.api.models import ConnectionState, OperationMode
        from control_ofc.constants import PAGE_SETTINGS
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.services.app_state import AppState
        from control_ofc.services.profile_service import ProfileService
        from control_ofc.ui.main_window import MainWindow

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


class TestBackgroundLeakGuard:
    """DEC-225: the blanket ``QWidget`` rule must not paint a background, or
    app_bg leaks through every bare container onto the lighter .Card surfaces
    (the dashboard fan-card metric blocks were the visible symptom). The window
    fill is anchored on #MainWindow instead, and the top-level popup surface that
    used to free-ride on the blanket rule (QMenu) keeps an explicit fill."""

    @staticmethod
    def _block(qss: str, selector: str) -> str:
        """Body of the ``selector { ... }`` rule where *selector* is the whole
        selector.

        Anchored to the start of a line so a compound rule that merely *ends*
        with the same word cannot be returned instead — unanchored, asking for
        ``QPushButton`` yields the body of ``#Sidebar QPushButton``, and the
        assertion then silently checks the wrong rule.
        """
        m = re.search(r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^{}]*)\}", qss)
        return m.group(1) if m else ""

    def test_global_qwidget_rule_paints_no_background(self):
        """A blanket QWidget background is exactly the leak — it must be gone,
        while the base text colour + font size stay."""
        block = self._block(build_stylesheet(default_dark_theme()), "QWidget")
        assert block, "global QWidget rule missing"
        assert "background-color" not in block
        assert "color:" in block

    def test_window_fill_is_anchored_on_mainwindow(self):
        """Removing the blanket fill must not leave the top-level window unpainted."""
        t = default_dark_theme()
        block = self._block(build_stylesheet(t), "#MainWindow")
        assert "background-color" in block
        assert t.app_bg in block

    def test_popup_menu_has_an_explicit_fill(self):
        """QMenu used to free-ride on the blanket QWidget background (DEC-225); it
        must now carry its own explicit surface_1 fill (a bare "has a
        background-color" check would pass on a wrong hardcoded colour)."""
        t = default_dark_theme()
        block = self._block(build_stylesheet(t), "QMenu")
        assert block, "QMenu rule missing"
        assert t.surface_1 in block

    def test_fan_card_metric_divider_class_is_theme_driven(self):
        """The hairline that replaces the inset panel takes the border tone."""
        t = default_dark_theme()
        block = self._block(build_stylesheet(t), ".CardDivider")
        assert t.border_default in block


# ---------------------------------------------------------------------------
# Palette (DEC-226)
# ---------------------------------------------------------------------------

# Qt's built-in light palette — the exact colours that leaked through in v2.27.0.
QT_DEFAULT_WINDOW = "#efefef"
QT_DEFAULT_BASE = "#ffffff"


@pytest.fixture
def restore_app_theme(qtbot):
    """Save/restore everything ``apply_theme`` mutates.

    That is all four channels: the application palette, stylesheet and font,
    plus ``theme._active_theme`` — the module global behind ``active_theme()``,
    which eight other test modules read. Restoring three of the four would leave
    the probe theme active for the rest of the session, which is the same
    "applied only some of the channels" mistake this release is fixing.

    Depends on ``qtbot`` so a QApplication exists: without it the fixture is
    order-dependent and errors when the test is run on its own.
    """
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from control_ofc.ui import theme as theme_mod

    app = QApplication.instance()
    saved_palette = QPalette(app.palette())
    saved_stylesheet = app.styleSheet()
    saved_font = app.font()
    saved_active = theme_mod._active_theme
    try:
        yield app
    finally:
        app.setPalette(saved_palette)
        app.setStyleSheet(saved_stylesheet)
        app.setFont(saved_font)
        theme_mod._active_theme = saved_active


class TestThemePalette:
    """DEC-226: Qt's stylesheet engine re-resolves each styled widget's palette
    from the *application* palette instead of inheriting the parent's QSS-derived
    colours. Every surface Qt paints from the palette rather than from a QSS rule
    therefore needs the palette to carry the theme, or it falls back to Qt's light
    default. Until DEC-225 the blanket ``QWidget`` background hid that; removing
    it put ``#efefef`` behind every scroll viewport and ``#ffffff`` behind the
    Logs snapshot panes."""

    def test_window_role_is_app_bg(self):
        """The user-visible symptom: the canvas behind the dashboard fan cards."""
        from PySide6.QtGui import QPalette

        t = default_dark_theme()
        assert build_palette(t).color(QPalette.ColorRole.Window).name() == t.app_bg.lower()

    def test_base_role_is_input_bg(self):
        """Base backs QPlainTextEdit and any unstyled item view."""
        from PySide6.QtGui import QPalette

        t = default_dark_theme()
        assert build_palette(t).color(QPalette.ColorRole.Base).name() == t.input_bg.lower()

    def test_every_role_qt_exposes_is_mapped(self):
        """The regression guard with real teeth. Any role missing from the map
        keeps Qt's stock colour — which is the entire bug: unmapped Window and
        Base put #efefef and #ffffff on screen. A hex-denylist would only catch
        the two roles that happened to leak; this catches all of them, and also
        trips deliberately when a Qt upgrade adds a role nobody has judged yet.

        ``NoRole`` is a sentinel, not a colour, and ``NColorRoles`` is the enum
        terminator — neither is paintable, so both are excluded."""
        from PySide6.QtGui import QPalette

        mapped = {role for role, _ in _PALETTE_ROLES}
        exposed = {
            role.name
            for role in QPalette.ColorRole
            if role not in (QPalette.ColorRole.NoRole, QPalette.ColorRole.NColorRoles)
        }
        assert not (exposed - mapped), (
            f"roles left on Qt's stock colour: {sorted(exposed - mapped)}"
        )

    def test_every_role_resolves_to_the_token_it_should(self):
        """Complements the coverage test: a role can be listed and still be wired
        to the wrong token.

        The expected pairs are written out here independently rather than read
        back from ``_PALETTE_ROLES`` — a test that sources its expectation from
        the table it is checking cannot detect two roles being transposed, which
        is the likeliest way this table goes wrong."""
        from PySide6.QtGui import QPalette

        expected = {
            "Window": "app_bg",
            "WindowText": "text_primary",
            "Base": "input_bg",
            "AlternateBase": "table_row_alt_bg",
            "Text": "input_text",
            "PlaceholderText": "input_placeholder",
            "Button": "surface_2",
            "ButtonText": "text_primary",
            "BrightText": "status_crit",
            "ToolTipBase": "surface_2",
            "ToolTipText": "text_primary",
            "Highlight": "selected_bg",
            "HighlightedText": "text_primary",
            "Link": "accent_primary",
            "LinkVisited": "accent_secondary",
            "Light": "surface_3",
            "Midlight": "surface_2",
            "Mid": "border_default",
            "Dark": "app_bg",
            "Shadow": "code_block_bg",
            "Accent": "accent_primary",
        }
        expected_disabled = {
            "WindowText": "disabled_text",
            "Text": "disabled_text",
            "ButtonText": "disabled_text",
            "Base": "disabled_bg",
            "Button": "disabled_bg",
            "Highlight": "disabled_bg",
            "HighlightedText": "disabled_text",
        }
        # Adding a role to the source table without judging it here must fail.
        assert dict(_PALETTE_ROLES) == expected
        assert dict(_PALETTE_DISABLED_ROLES) == expected_disabled

        t = default_dark_theme()
        palette = build_palette(t)
        for role_name, token_name in expected.items():
            role = getattr(QPalette.ColorRole, role_name, None)
            if role is None:  # role absent on this Qt — skipped by build_palette too
                continue
            assert palette.color(role).name() == getattr(t, token_name).lower(), role_name
        disabled = QPalette.ColorGroup.Disabled
        for role_name, token_name in expected_disabled.items():
            role = getattr(QPalette.ColorRole, role_name, None)
            if role is None:
                continue
            assert palette.color(disabled, role).name() == getattr(t, token_name).lower(), role_name

    def test_palette_agrees_with_the_stylesheet(self):
        """The palette exists to back what QSS paints, so where both cover a
        surface they must not disagree — a tooltip or button that changes colour
        depending on which engine drew it is the same class of bug. Reads the
        colour out of the generated stylesheet rather than re-asserting the
        palette map against itself, so drifting either side fails this."""
        from PySide6.QtGui import QPalette

        t = default_dark_theme()
        palette = build_palette(t)
        qss = build_stylesheet(t)
        block = TestBackgroundLeakGuard._block
        for selector, role_name in (
            ("QToolTip", "ToolTipBase"),
            ("QPushButton", "Button"),
        ):
            rule = block(qss, selector)
            assert rule, f"{selector} rule missing"
            role = getattr(QPalette.ColorRole, role_name)
            assert palette.color(role).name() in rule.lower(), (
                f"{role_name} palette colour is not the one {selector} paints"
            )

    def test_invalid_token_colour_falls_back_instead_of_going_black(self):
        """``is_valid_color`` accepts #RGBA but QColor does not, and Qt stores an
        invalid QColor as opaque black — so one 4-digit hex in a hand-edited
        theme would black out a whole surface. A dropped QSS declaration merely
        falls through; a palette role cannot, so build_palette must fall back."""
        from PySide6.QtGui import QColor, QPalette

        assert not QColor("#abcd").isValid()  # the premise
        t = default_dark_theme()
        t.app_bg = "#abcd"
        window = build_palette(t).color(QPalette.ColorRole.Window).name()
        assert window != "#000000"
        assert window == default_dark_theme().app_bg.lower()

    def test_custom_tokens_flow_through(self):
        """A user theme must move the palette too, not just the stylesheet."""
        from PySide6.QtGui import QPalette

        t = default_dark_theme()
        t.app_bg = "#123456"
        t.input_bg = "#654321"
        palette = build_palette(t)
        assert palette.color(QPalette.ColorRole.Window).name() == "#123456"
        assert palette.color(QPalette.ColorRole.Base).name() == "#654321"

    def test_disabled_group_uses_the_disabled_tokens(self):
        from PySide6.QtGui import QPalette

        t = default_dark_theme()
        palette = build_palette(t)
        disabled = QPalette.ColorGroup.Disabled
        assert palette.color(disabled, QPalette.ColorRole.Text).name() == t.disabled_text.lower()
        assert palette.color(disabled, QPalette.ColorRole.Base).name() == t.disabled_bg.lower()

    def test_apply_theme_pushes_all_three_channels(self, restore_app_theme):
        """A site that pushes only some of palette/stylesheet/font is the DEC-226
        regression itself, so the entry point must push all three."""
        from PySide6.QtGui import QPalette

        from control_ofc.ui.theme import active_theme, apply_theme

        app = restore_app_theme
        t = default_dark_theme()
        t.name = "Palette Probe"
        t.app_bg = "#010203"
        apply_theme(t)
        assert app.palette().color(QPalette.ColorRole.Window).name() == "#010203"
        assert "#010203" in app.styleSheet()
        assert app.font().family() == t.font_family
        assert active_theme().name == "Palette Probe"


class TestUnstyledSurfacesRenderThemed:
    """Pixel-level guard for DEC-226. A stylesheet-only assertion cannot catch
    this bug — the leaked colours never appeared in the QSS text; they came from
    the palette behind it. These tests paint the two widget shapes that actually
    regressed and read the result back."""

    @staticmethod
    def _render(widget, point):
        widget.show()
        QApplication.processEvents()
        colour = widget.grab().toImage().pixelColor(*point).name()
        widget.hide()
        return colour

    def test_scroll_area_canvas_is_app_bg(self, qtbot, restore_app_theme):
        """The dashboard fan-card backdrop. ``QScrollArea.setWidget()`` force-sets
        ``autoFillBackground`` on the content widget, so it paints the palette's
        Window role — grey until the theme palette is applied."""
        from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

        from control_ofc.ui.theme import apply_theme

        t = default_dark_theme()
        apply_theme(t)

        host = QWidget()
        host.setObjectName("MainWindow")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(QWidget())
        layout.addWidget(scroll)
        host.resize(200, 200)
        qtbot.addWidget(host)

        colour = self._render(host, (100, 150))
        assert colour != QT_DEFAULT_WINDOW, "Qt's light Window role is showing through"
        assert colour == t.app_bg.lower()

    def test_readonly_output_pane_is_the_code_surface(self, qtbot, restore_app_theme):
        """The Logs page's read-only probe panes. ``QPlainTextEdit`` had no QSS rule
        at all, so its viewport painted the palette's Base role — pure white until
        DEC-226."""
        from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

        from control_ofc.ui.theme import apply_theme

        t = default_dark_theme()
        apply_theme(t)

        host = QWidget()
        host.setObjectName("MainWindow")
        layout = QVBoxLayout(host)
        pane = QPlainTextEdit()
        pane.setReadOnly(True)
        layout.addWidget(pane)
        host.resize(200, 200)
        qtbot.addWidget(host)

        colour = self._render(host, (100, 100))
        assert colour != QT_DEFAULT_BASE, "Qt's white Base role is showing through"
        assert colour == t.code_block_bg.lower()

    def test_light_preset_canvas_is_its_own_app_bg(self, qtbot, restore_app_theme):
        """The light preset is the case a dark-only guard cannot speak for: its
        tokens sit near Qt's stock light values, so 'not #efefef' is a weak
        assertion there and the exact token is the only real check."""
        from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

        from control_ofc.ui.theme import apply_theme

        preset = Path(__file__).resolve().parent.parent / (
            "src/control_ofc/ui/presets/solar_light.json"
        )
        t = load_theme(preset)
        assert t.app_bg.lower() != default_dark_theme().app_bg.lower()  # really a light theme
        apply_theme(t)

        host = QWidget()
        host.setObjectName("MainWindow")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(QWidget())
        layout.addWidget(scroll)
        host.resize(200, 200)
        qtbot.addWidget(host)

        assert self._render(host, (100, 150)) == t.app_bg.lower()


class TestThemeSwitchAppliesPalette:
    """Switching theme must move every channel, not just the stylesheet. Startup
    and the switch path are separate call sites, and this release exists because
    a theme was applied through only some of its channels — so the switch path
    gets its own guard rather than trusting that it matches startup."""

    def test_main_window_theme_change_repaints_the_palette(self, qtbot, restore_app_theme):
        from PySide6.QtGui import QPalette

        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.services.app_state import AppState
        from control_ofc.services.profile_service import ProfileService
        from control_ofc.ui.main_window import MainWindow

        app = restore_app_theme
        profile_svc = ProfileService()
        profile_svc.load()
        win = MainWindow(
            state=AppState(),
            settings_service=AppSettingsService(),
            profile_service=profile_svc,
        )
        qtbot.addWidget(win)

        switched = default_dark_theme()
        switched.name = "Switched"
        switched.app_bg = "#0b0c0d"
        win._on_theme_changed(switched)

        assert app.palette().color(QPalette.ColorRole.Window).name() == "#0b0c0d"


class TestSettingsPathLabelsFollowTheme:
    """The Settings dir-picker path labels were tinted with an inline token
    f-string, which freezes the colour at construction — and SettingsPage is
    not in the MainWindow ``set_theme`` fan-out, so a live dark→light switch
    left them at the old theme's muted tint. They now carry the scoped
    ``.MutedLabel`` QSS class, which re-resolves from the freshly applied
    stylesheet on every theme change."""

    def test_muted_label_rule_is_token_driven(self):
        t = default_dark_theme()
        block = TestBackgroundLeakGuard._block(build_stylesheet(t), ".MutedLabel")
        assert block, ".MutedLabel rule missing"
        assert t.text_muted in block

    def test_dir_picker_label_recolours_on_live_theme_switch(
        self, qtbot, restore_app_theme, monkeypatch, tmp_path
    ):
        """Outcome assertion: the label's *effective* colour (the palette the
        QSS engine resolves for it) must track the active theme's text_muted —
        first under the dark default, then after a live switch to a theme with
        a different muted tint."""
        from PySide6.QtGui import QPalette

        from control_ofc.paths import set_path_overrides
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.ui.pages.settings_page import SettingsPage
        from control_ofc.ui.theme import apply_theme

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()  # clear any leaked global directory overrides
        svc = AppSettingsService()
        svc.load()

        dark = default_dark_theme()
        apply_theme(dark)
        page = SettingsPage(settings_service=svc)
        qtbot.addWidget(page)

        for label in (
            page._profiles_dir_label,
            page._themes_dir_label,
            page._export_dir_label,
        ):
            label.ensurePolished()
            assert (
                label.palette().color(QPalette.ColorRole.WindowText).name()
                == dark.text_muted.lower()
            ), label.objectName()

        switched = default_dark_theme()
        switched.name = "Muted Probe"
        switched.text_muted = "#123456"
        apply_theme(switched)

        for label in (
            page._profiles_dir_label,
            page._themes_dir_label,
            page._export_dir_label,
        ):
            label.ensurePolished()
            assert label.palette().color(QPalette.ColorRole.WindowText).name() == "#123456", (
                label.objectName()
            )


class TestReadOnlyPaneStyling:
    """The one unstyled widget class that earned a rule rather than just palette
    backing. Every ``QPlainTextEdit`` in the app — the Logs inspector's probe panes
    and its message/raw panes — is read-only monospace command/log output, which is
    what code_block_bg is for (DEC-226)."""

    def test_plain_text_edit_uses_the_code_block_surface(self):
        t = default_dark_theme()
        block = TestBackgroundLeakGuard._block(build_stylesheet(t), "QPlainTextEdit")
        assert block, "QPlainTextEdit rule missing"
        assert t.code_block_bg in block
        assert t.border_default in block


class TestApplyThemeIsTheOnlyEntryPoint:
    """DEC-226 shipped because the theme was applied through only some of its
    channels. ``apply_theme`` now bundles all four, but that is a convention
    until something enforces it — a new site that reaches for ``setStyleSheet``
    with a generated stylesheet directly would silently reintroduce the bug.
    This pins the convention as a source-level guard."""

    def test_no_module_pushes_a_generated_stylesheet_outside_apply_theme(self):
        src = Path(__file__).resolve().parent.parent / "src"
        offenders = []
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if "setStyleSheet(build_stylesheet(" not in line.replace(" ", ""):
                    continue
                # theme.apply_theme is the sanctioned site.
                if path.name == "theme.py":
                    continue
                offenders.append(f"{path.relative_to(src)}:{lineno}")
        assert not offenders, (
            "these push a generated stylesheet without the palette/font that go "
            f"with it — route them through apply_theme(): {offenders}"
        )


class TestKeyboardFocusVisibility:
    """DEC-251: every interactive control must show a visible keyboard focus
    indicator (WCAG 2.4.7).

    Asserted by **rendering**, not by grepping the stylesheet for `:focus`. A
    rule can be present and still show nothing: the first attempt at the primary
    variant drew its ring in the accent token, which is the colour that button is
    already filled with — a correct-looking QSS rule that was invisible on
    screen. Only a render diff catches that, and it is what found the original
    gap (six of eight controls rendered pixel-identical focused and unfocused).
    """

    @staticmethod
    def _states(make):
        """Render *make*'s widget unfocused and focused; return both images."""
        from PySide6.QtWidgets import QLineEdit, QWidget

        host = QWidget()
        host.resize(400, 60)
        # A second focusable widget, so focus can genuinely leave the subject.
        elsewhere = QLineEdit(host)
        elsewhere.setGeometry(300, 5, 80, 24)
        subject = make(host)
        subject.setGeometry(10, 5, 180, 30)
        host.show()

        elsewhere.setFocus()
        QApplication.processEvents()
        unfocused = subject.grab().toImage()
        hint_unfocused = subject.sizeHint()

        subject.setFocus()
        QApplication.processEvents()
        focused = subject.grab().toImage()
        hint_focused = subject.sizeHint()

        host.hide()
        return unfocused, focused, hint_unfocused, hint_focused

    @staticmethod
    def _button(variant=None, object_name=None):
        from PySide6.QtWidgets import QPushButton

        def make(parent):
            b = QPushButton("Sample", parent)
            if variant:
                b.setProperty("variant", variant)
            if object_name:
                b.setObjectName(object_name)
            return b

        return make

    @staticmethod
    def _checkbox(parent):
        from PySide6.QtWidgets import QCheckBox

        return QCheckBox("Sample", parent)

    @staticmethod
    def _combo(parent):
        from PySide6.QtWidgets import QComboBox

        c = QComboBox(parent)
        c.addItems(["a", "b"])
        return c

    @staticmethod
    def _line_edit(parent):
        from PySide6.QtWidgets import QLineEdit

        return QLineEdit("sample", parent)

    @staticmethod
    def _list(parent):
        """A keyboard-navigable list — the Settings profile-search-dir editor.

        Carries an item, because that is the shape it ships in. MEASURED: with
        the `QListWidget:focus` rule deleted, focused and unfocused still differ
        — Qt paints a current-item indicator when the list takes focus — so the
        image diff is vacuous for it, exactly as the caret makes it vacuous for
        a QLineEdit. It is asserted by painted value instead.
        """
        from PySide6.QtWidgets import QListWidget

        w = QListWidget(parent)
        w.addItems(["/etc/control-ofc/profiles"])
        return w

    @staticmethod
    def _focused_perimeter(make):
        """Render *make*'s widget focused; return the colours on its outer edge."""
        from PySide6.QtWidgets import QLineEdit, QWidget

        host = QWidget()
        host.resize(400, 60)
        elsewhere = QLineEdit(host)
        elsewhere.setGeometry(300, 5, 80, 24)
        subject = make(host)
        subject.setGeometry(10, 5, 180, 30)
        host.show()

        subject.setFocus()
        QApplication.processEvents()
        image = subject.grab().toImage()
        host.hide()

        w, h = image.width(), image.height()
        edge = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
        edge += [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
        return {image.pixelColor(x, y).name() for x, y in edge}

    def test_focus_rings_a_diff_cannot_see_paint_the_focus_token(self, restore_app_theme):
        """The sweeps below compare images and so cannot see a lost focus ring on
        a control that changes for some OTHER reason when focused. A QLineEdit or
        QPlainTextEdit renders a blinking CARET; a QListWidget paints a
        current-item indicator. Either way focused and unfocused differ whether or
        not any ``:focus`` rule survives. Measured — strip every ``:focus`` rule
        from the built QSS and every subject here still passes the diff.

        That is exactly the trap DEC-273 fixed one pseudo-class over, and
        ``docs/03`` asserts these rules are "enforced by rendering". So assert the
        painted VALUE, the way the disabled sweep asserts ``disabled_bg``: the
        focus ring must actually be ``input_border_focus``. The realistic
        regression is a later selector winning on specificity, which is precisely
        what ``#Sidebar QPushButton`` did to the button ring in DEC-255.
        """
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

        from control_ofc.ui.theme import apply_theme, default_dark_theme

        theme = default_dark_theme()
        apply_theme(theme)
        expected = theme.input_border_focus.lower()

        from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

        def _plain_text(parent):
            return QPlainTextEdit(parent)

        def _line_edit(parent):
            return QLineEdit("sample", parent)

        # QSpinBox/QDoubleSpinBox share the one `QLineEdit:focus, QSpinBox:focus,
        # QDoubleSpinBox:focus` rule and embed a QLineEdit, so they carry the exact
        # caret-driven vacuity this test exists to remove — and were in no render
        # test at all, across five real surfaces (settings, curve editor and dialog,
        # fan-role dialog, theme page).
        missing = {
            label: sorted(painted)
            for label, painted in (
                ("QLineEdit", self._focused_perimeter(_line_edit)),
                ("QPlainTextEdit", self._focused_perimeter(_plain_text)),
                ("QSpinBox", self._focused_perimeter(lambda p: QSpinBox(p))),
                ("QDoubleSpinBox", self._focused_perimeter(lambda p: QDoubleSpinBox(p))),
                ("QListWidget", self._focused_perimeter(self._list)),
            )
            if expected not in painted
        }
        assert not missing, (
            f"a focused control must paint its ring in input_border_focus "
            f"({expected}); these painted none of it on their outer edge, so the "
            f"ring is absent or has been defeated on specificity: {missing}"
        )

    def test_every_interactive_control_renders_a_focus_indicator(self, restore_app_theme):
        """The regression itself: a QSS-styled widget loses Qt's native focus
        rect, so anything without its own `:focus` rule is invisible to a
        keyboard user. Before DEC-251 only ghost buttons and QLineEdit passed."""
        from control_ofc.ui.theme import apply_theme, default_dark_theme

        apply_theme(default_dark_theme())

        subjects = {
            "QPushButton (no variant)": self._button(),
            "QPushButton[variant=primary]": self._button("primary"),
            "QPushButton[variant=secondary]": self._button("secondary"),
            "QPushButton[variant=ghost]": self._button("ghost"),
            "QPushButton[variant=danger]": self._button("danger"),
            "QPushButton#PrimaryButton": self._button(object_name="PrimaryButton"),
            "QCheckBox": self._checkbox,
            "QComboBox": self._combo,
            "QLineEdit": self._line_edit,
            # NOT QListWidget — measured vacuous here: a focused list draws a
            # current-item indicator, so the images differ with the `:focus`
            # rule deleted. It is asserted by painted VALUE in
            # `test_focus_rings_a_diff_cannot_see_paint_the_focus_token`.
        }

        invisible = []
        for label, make in subjects.items():
            unfocused, focused, _, _ = self._states(make)
            if unfocused == focused:
                invisible.append(label)

        assert not invisible, (
            "these render identically focused and unfocused, so a keyboard user "
            f"cannot see where they are (WCAG 2.4.7): {invisible}"
        )

    def test_no_focusable_widget_is_left_without_an_indicator(self, restore_app_theme):
        """Sweep, rather than a hand-written list.

        The first version of this guard enumerated nine widgets it already knew
        about, so it could not fail for a widget nobody added to the dict — and
        six focusable classes shipped invisible behind a green suite, including
        the sidebar navigation. This walks the widget types the app actually
        instantiates and checks every one that can take keyboard focus.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QCheckBox,
            QPlainTextEdit,
            QPushButton,
            QRadioButton,
            QSlider,
        )

        from control_ofc.ui.components.toggle_switch import ToggleSwitch
        from control_ofc.ui.theme import apply_theme, default_dark_theme
        from control_ofc.ui.widgets.theme_editor import ColorSwatch

        apply_theme(default_dark_theme())

        def _btn(variant=None, object_name=None, css_class=None, host=None):
            def make(parent):
                if host:
                    host(parent)
                b = QPushButton("Sample", parent)
                if variant:
                    b.setProperty("variant", variant)
                if object_name:
                    b.setObjectName(object_name)
                if css_class:
                    b.setProperty("class", css_class)
                return b

            return make

        def _sidebar(parent):
            parent.setObjectName("Sidebar")

        def _card(parent):
            parent.setProperty("class", "Card")

        subjects = {
            "QPushButton": _btn(),
            "QPushButton in .Card": _btn(host=_card),
            "QPushButton in #Sidebar": _btn(host=_sidebar),
            "[variant=primary]": _btn("primary"),
            "[variant=secondary]": _btn("secondary"),
            "[variant=ghost]": _btn("ghost"),
            "[variant=danger]": _btn("danger"),
            "#PrimaryButton": _btn(object_name="PrimaryButton"),
            ".CollapsibleSectionHeader": _btn(css_class="CollapsibleSectionHeader"),
            "QCheckBox": lambda p: QCheckBox("Sample", p),
            "QRadioButton": lambda p: QRadioButton("Sample", p),
            "ToggleSwitch": lambda p: ToggleSwitch(p),
            "ColorSwatch": lambda p: ColorSwatch("accent_primary", "#1FB88A", p),
            "QSlider": lambda p: QSlider(Qt.Orientation.Horizontal, p),
            "QComboBox": self._combo,
            "QLineEdit": self._line_edit,
            "QPlainTextEdit": lambda p: QPlainTextEdit("sample", p),
        }

        invisible = []
        for label, make in subjects.items():
            unfocused, focused, _, _ = self._states(make)
            if unfocused == focused:
                invisible.append(label)

        assert not invisible, (
            "these render identically focused and unfocused, so a keyboard user "
            f"cannot see where they are (WCAG 2.4.7): {invisible}"
        )

    def test_no_focus_rule_declares_a_ring_fatter_than_policy(self, restore_app_theme):
        """Check the stylesheet, because no *rendered* property can catch this.

        Three earlier versions of this test were tautological. The first asserted
        ``sizeHint()`` was unchanged between focus states; the second "fixed" it
        by asserting laid-out ``geometry()``; a third tried the rendered pixels.
        The first two pass with the border widened to 6px for the same underlying
        reason — **Qt never runs a layout pass off a ``:focus`` change**, so every
        geometry property is blind here by construction and swapping one for
        another only moves the blind spot. Pixels are not the answer either: a
        focused ``QLineEdit`` legitimately paints a text cursor, and the policy
        deliberately *accepts* a 1px content shift on ``border: none`` widgets
        (see below), so an interior-pixel diff has innocent causes it cannot
        distinguish from guilty ones.

        What is left is the declaration itself, which is exact. Policy
        (``CLAUDE.md § GUI component standard``, DEC-251) is that the ring must
        not resize the control: swap an existing border's colour, or — where the
        resting state is ``border: none`` — declare the border only under
        ``:focus``, accepting 1px over the 2px a transparent resting border would
        cost. Either way the ring is **1px**. A fatter one displaces the widget's
        own contents, which is precisely the regression the geometry tests could
        never see.
        """
        import re

        from control_ofc.ui.theme import apply_theme, build_stylesheet, default_dark_theme

        apply_theme(default_dark_theme())
        qss = re.sub(r"/\*.*?\*/", " ", build_stylesheet(default_dark_theme()), flags=re.S)

        # Fixed-size subcontrols are the one sanctioned exception: the slider
        # handle declares its own width/height, so a border is drawn *inside* it
        # and cannot displace the groove or anything else. Named explicitly, so
        # a fat ring anywhere else still fails.
        exempt = {"QSlider::handle:horizontal:focus"}

        # Selector list + body, for every rule whose selector mentions :focus.
        rule = re.compile(r"([^{}]*:focus[^{}]*)\{([^{}]*)\}", re.S)
        width = re.compile(r"border(?:-width|-top|-right|-bottom|-left)?\s*:\s*([^;]+)", re.I)

        fat = []
        for selector, body in rule.findall(qss):
            selector = " ".join(selector.split())
            if selector in exempt:
                continue
            for decl in width.findall(body):
                px = re.search(r"(\d+)\s*px", decl)
                if px and int(px.group(1)) > 1:
                    fat.append((selector, decl.strip()))

        assert not fat, (
            "these :focus rules declare a ring wider than the 1px policy, which "
            "displaces the control's own contents rather than outlining it "
            f"(DEC-251): {fat}"
        )

    def test_checkbox_is_styled_only_in_the_focused_state(self):
        """QCheckBox has no *resting* rule on purpose.

        Styling the widget at rest makes Qt swap its native indicator for the
        stylesheet's own, which repaints and shrinks the resting box — the same
        subcontrol trap already documented for `QComboBox::drop-down`. Measured:
        a resting rule changed 393px and took the box from 70x23 to 68x20."""
        qss = build_stylesheet(default_dark_theme())
        checkbox_rules = re.findall(r"^\s*(QCheckBox[^\n{]*)\{", qss, re.MULTILINE)
        assert checkbox_rules, "expected at least the focus rule"
        assert all(":focus" in rule for rule in checkbox_rules), (
            "a resting QCheckBox rule replaces Qt's native indicator — keep the "
            f"styling scoped to :focus: {checkbox_rules}"
        )


class TestDisabledStateVisibility:
    """A disabled control must LOOK disabled (DEC-273).

    The same specificity tie DEC-251 documents for ``:focus``, one pseudo-class
    over. ``QPushButton:disabled`` is declared with the base button rules and
    ``QPushButton[variant="..."]`` has equal CSS2 specificity, so every variant —
    declared later — silently won the tie and went on painting its enabled fill
    while disabled. ``#PrimaryButton`` beat the base rule outright on ID
    specificity. Ghost and secondary are ``setEnabled``-toggled in the app today,
    so this was live rather than theoretical.

    Measured before the fix, disabled: ``[variant="primary"]`` painted
    ``#1fb88a`` — the full accent fill, identical to a live primary action —
    ``[variant="secondary"]`` its own ``surface_1``, and ghost/danger stayed
    transparent. Only the bare ``QPushButton`` reached ``disabled_bg``.

    **This asserts the background COLOUR, not that the two renders differ.** The
    first version of this test compared the enabled and disabled images and
    passed with the rules deleted: the QPalette disabled group (DEC-226) recolours
    the *text* on its own, so the images differ either way. "Something changed" is
    not evidence the rule fired — which is the identical trap DEC-251 warns about,
    walked into once more.
    """

    @staticmethod
    def _disabled_background(make):
        """Render *make*'s widget disabled; return its background pixels."""
        from PySide6.QtWidgets import QWidget

        host = QWidget()
        host.resize(400, 60)
        subject = make(host)
        subject.setGeometry(10, 5, 180, 30)
        host.show()

        subject.setEnabled(False)
        QApplication.processEvents()
        image = subject.grab().toImage()
        host.hide()

        # Points inside the 1px border and clear of the centred label.
        return {image.pixelColor(x, y).name() for x, y in ((4, 4), (90, 3), (175, 26))}

    @staticmethod
    def _button(variant=None, object_name=None):
        def make(parent):
            b = QPushButton("Sample", parent)
            if variant:
                b.setProperty("variant", variant)
            if object_name:
                b.setObjectName(object_name)
            return b

        return make

    @staticmethod
    def _scoped_disabled_background(make, host_setup):
        """As ``_disabled_background``, but inside a host that scopes the QSS."""
        from PySide6.QtWidgets import QWidget

        host = QWidget()
        host.resize(400, 60)
        host_setup(host)
        subject = make(host)
        subject.setGeometry(10, 5, 180, 30)
        host.show()

        subject.setEnabled(False)
        QApplication.processEvents()
        image = subject.grab().toImage()
        host.hide()
        bg = {image.pixelColor(x, y).name() for x, y in ((4, 4), (90, 3), (175, 26))}
        # Every colour anywhere in the render, so the LABEL is covered too: the
        # tile-card ghost defect is a text colour, invisible to background probes.
        allpx = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        }
        return bg, allpx

    def test_scoped_button_rules_do_not_defeat_the_disabled_look(self, restore_app_theme):
        """The bare-hosted sweep below cannot see a SCOPED selector winning.

        ``QPushButton[variant="x"]:disabled`` is CSS2 specificity (0,0,2,1), but
        ``.Card[density="tile"] QPushButton[variant="ghost"]`` is (0,0,3,2) and
        ``#Sidebar QPushButton`` is (0,1,0,1) — both outrank it, so a disabled
        button in either host went on painting its enabled colours. Latent today
        (neither is ``setEnabled``-toggled yet) but it falsifies DEC-273's claim to
        cover every variant, and the sibling focus sweep already learned to render
        in these two hosts for exactly this reason (DEC-255).
        """
        from control_ofc.ui.theme import apply_theme, default_dark_theme

        theme = default_dark_theme()
        apply_theme(theme)
        expected = theme.disabled_bg.lower()

        def _sidebar(parent):
            parent.setObjectName("Sidebar")

        def _tile_card(parent):
            parent.setProperty("class", "Card")
            parent.setProperty("density", "tile")

        # Each host has its OWN enabled label colour — the sidebar paints `nav_text`,
        # the tile card `text_primary` — so a single shared "enabled colour" probe is
        # vacuous for whichever host does not use it. That was the real defect: the
        # first version compared both against `text_primary`, so the sidebar arm's
        # label half could never fire.
        #
        # An earlier fix ALSO asserted the positive — that `disabled_text` appears
        # somewhere — and that is not portable. Glyphs are antialiased, so the exact
        # token value only lands on fully-opaque pixels, which depends on the font
        # stack: it held locally and failed on CI's py3.12/py3.13 containers, where
        # no pixel matched exactly. A flat background is safe to compare exactly (it
        # passed everywhere); TEXT is not. Hence a nearest-colour test, which does
        # not care whether any pixel is an exact hit.
        def _rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

        def _dist2(a, b):
            # strict= is required by lint and is the right call anyway: both
            # operands are 3-tuples from _rgb, so a length mismatch is a bug in
            # the parsing, not something to silently truncate.
            return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))

        def _nearer(px, a, b):
            rgb = _rgb(px)
            return _dist2(rgb, a) < _dist2(rgb, b)

        disabled_rgb = _rgb(theme.disabled_text)
        wrong = {}
        for label, make, host, enabled_text in (
            ("#Sidebar QPushButton", self._button(), _sidebar, theme.nav_text),
            (
                '.Card[density="tile"] [variant=ghost]',
                self._button("ghost"),
                _tile_card,
                theme.text_primary,
            ),
        ):
            bg, allpx = self._scoped_disabled_background(make, host)
            if bg != {expected}:
                wrong[f"{label} background"] = sorted(bg)

            # Any pixel closer to the ENABLED label colour than to the disabled one
            # is a glyph that is still being painted live — antialiased or not.
            enabled_rgb = _rgb(enabled_text)
            if enabled_rgb == disabled_rgb:  # pragma: no cover - theme sanity
                continue
            live = [px for px in allpx if _nearer(px, enabled_rgb, disabled_rgb)]
            if live:
                wrong[f"{label} label"] = (
                    f"{len(live)} pixel(s) nearer the enabled {enabled_text} "
                    f"than the disabled {theme.disabled_text}"
                )

        assert not wrong, (
            "a disabled button must paint disabled_bg "
            f"({expected}) and must not keep its enabled label colour, but a "
            f"scoped rule outranks the :disabled rule here: {wrong}"
        )

    def test_every_button_variant_paints_the_disabled_background(self, restore_app_theme):
        from control_ofc.ui.theme import apply_theme, default_dark_theme

        theme = default_dark_theme()
        apply_theme(theme)
        expected = theme.disabled_bg.lower()

        subjects = {
            "QPushButton (no variant)": self._button(),
            '[variant="primary"]': self._button("primary"),
            '[variant="secondary"]': self._button("secondary"),
            '[variant="ghost"]': self._button("ghost"),
            '[variant="danger"]': self._button("danger"),
            "#PrimaryButton": self._button(object_name="PrimaryButton"),
        }

        wrong = {}
        for label, make in subjects.items():
            painted = self._disabled_background(make)
            if painted != {expected}:
                wrong[label] = sorted(painted)

        assert not wrong, (
            f"these paint something other than disabled_bg ({expected}) while "
            "disabled, so the action reads as available when it is not — the "
            "variant rule is winning the specificity tie against "
            f"QPushButton:disabled (DEC-273): {wrong}"
        )


class TestKeyboardFocusContrast:
    """DEC-264: a focus ring must also be *legible*, not merely drawn.

    ``TestKeyboardFocusVisibility`` above renders each control focused and
    unfocused and asserts the pixels differ. That is a necessary check and it is
    not a sufficient one: a ring at 1.65:1 against the surface it sits on still
    changes pixels, so every one of those tests passed while the slider's ring
    was effectively invisible in three of the four shipped themes. ``docs/03``
    has required 3:1 for focus indicators since DEC-251; nothing measured it.

    Deliberately **pure token maths — no ``apply_theme`` call.** That function is
    application-wide and costs over a second per call at suite scale; a
    theme-applying sweep of this size is exactly what blew the CI timeout in
    v2.41.0. ``build_stylesheet`` is a pure function of its tokens, which is all
    this needs.

    The ring colours are read out of the *generated stylesheet* rather than
    restated here, so the test cannot drift from the QSS the way a duplicated
    token list would. Only the adjacency — what each control is drawn on — is
    declared, because the stylesheet cannot tell us what sits behind a
    transparent widget.
    """

    # selector -> the token whose colour the ring is drawn against.
    #
    # Qt draws a border INSIDE the widget rect, so for a filled control the
    # adjacent surface is its own fill, not the page behind it. That distinction
    # is the whole finding for the accent-filled controls: the slider handle and
    # the primary button are filled with `accent_primary`, so a ring in
    # `text_primary` — a token chosen to contrast with the PAGE — has no reason
    # to be legible, and measurably was not.
    FOCUS_SURFACE: ClassVar[dict[str, str]] = {
        "#Sidebar QPushButton:focus": "nav_bg",
        ".CollapsibleSectionHeader:focus": "surface_1",
        "QComboBox:focus": "input_bg",
        "QSlider::handle:horizontal:focus": "accent_primary",
        "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus": "input_bg",
        # The list is filled with surface_1, not input_bg — Qt draws the border
        # inside the widget rect, so the adjacent colour is the list's own fill.
        "QListWidget:focus": "surface_1",
        "QPlainTextEdit:focus": "code_block_bg",
        "QCheckBox:focus, QRadioButton:focus": "app_bg",
        "QTabBar::tab:focus": "surface_1",
        'QPushButton[variant="ghost"]:focus': "app_bg",
        "QPushButton:focus": "surface_2",
        'QPushButton[variant="secondary"]:focus': "surface_1",
        'QPushButton[variant="danger"]:focus': "app_bg",
        'QPushButton[variant="primary"]:focus, QPushButton#PrimaryButton:focus': ("accent_primary"),
    }

    # WCAG 2.1 SC 1.4.11: non-text UI components, which includes focus
    # indicators. Same threshold docs/03 already states.
    MIN_RATIO = 3.0

    @staticmethod
    def _focus_rings(tokens) -> dict[str, str]:
        """Map each `:focus` selector in the built QSS to its border colour."""
        qss = re.sub(r"/\*.*?\*/", "", build_stylesheet(tokens), flags=re.S)
        rings: dict[str, str] = {}
        for match in re.finditer(r"([^{}]*:focus[^{}]*)\{([^{}]*)\}", qss):
            selector = " ".join(match.group(1).split())
            colours = re.findall(
                r"(?:border(?:-color)?)\s*:[^;]*?(#[0-9A-Fa-f]{3,8})", match.group(2)
            )
            if colours:
                rings[selector] = colours[0]
        return rings

    @staticmethod
    def _all_themes() -> list[tuple[str, object]]:
        from control_ofc.ui.theme import list_bundled_themes

        themes = [("default_dark", default_dark_theme())]
        themes += [(p.stem, load_theme(p)) for p in list_bundled_themes()]
        return themes

    def test_every_focus_ring_clears_the_contrast_threshold(self):
        """Every focus ring, in every shipped theme, against what it sits on."""
        failures: list[str] = []
        for theme_name, tokens in self._all_themes():
            for selector, ring in self._focus_rings(tokens).items():
                surface_token = self.FOCUS_SURFACE[selector]
                surface = getattr(tokens, surface_token)
                ratio = contrast_ratio(ring, surface)
                if ratio < self.MIN_RATIO:
                    failures.append(
                        f"{theme_name}: {selector} rings {ring} on "
                        f"{surface_token}={surface} = {ratio:.2f}:1"
                    )
        assert not failures, (
            "focus indicators must clear "
            f"{self.MIN_RATIO}:1 against the surface they are drawn on "
            f"(WCAG 1.4.11, docs/03):\n  " + "\n  ".join(failures)
        )

    def test_every_focus_rule_declares_what_it_sits_on(self):
        """A new focus rule must be measured, not silently exempted.

        Without this, adding a rule to the stylesheet quietly adds an untested
        one: the loop above only checks selectors that appear in the map. This
        is the guard against the hand-scoped blind spot that let the original
        six focus gaps ship — the failure mode is not a wrong entry, it is a
        missing one.
        """
        for theme_name, tokens in self._all_themes():
            undeclared = set(self._focus_rings(tokens)) - set(self.FOCUS_SURFACE)
            assert not undeclared, (
                f"{theme_name}: focus rule(s) with no declared adjacent surface — "
                f"add them to FOCUS_SURFACE so their contrast is measured: {sorted(undeclared)}"
            )


class TestColorSwatchFocusContrast:
    """DEC-264: the Theme Editor's swatches ring against their own colour.

    Separate from ``TestKeyboardFocusContrast`` because this one needs a widget:
    the rule lives in the swatch's *own* stylesheet, not the application one
    (DEC-255 — a widget stylesheet outranks the app's by origin, so the theme's
    `QPushButton:focus` can never reach it).

    A fixed ring token cannot work here. The surface is whatever colour the token
    holds, so on the `text_primary` swatch a `text_primary` ring was literally
    the same colour — 1.00:1, invisible among ~40 identical siblings, which is
    the exact failure DEC-255 set out to fix for this widget and only half fixed.
    """

    @staticmethod
    def _ring_of(swatch) -> str:
        match = re.search(r"ColorSwatch:focus\s*\{[^}]*?(#[0-9A-Fa-f]{3,8})", swatch.styleSheet())
        assert match, f"no ColorSwatch:focus ring found in {swatch.styleSheet()!r}"
        return match.group(1)

    @staticmethod
    def _editor_swatch_colours(tokens) -> list[tuple[str, str]]:
        """Every colour the editor actually renders a swatch for.

        DEC-266: sampling literals under-samples by construction. The set that
        failed in Classic Blue was data — the theme's own token values — so a
        hand-picked list could never have contained it. Enumerate what the editor
        builds instead: the ``_TOKEN_GROUPS`` entries plus the chart-series slots.
        """
        from control_ofc.ui.widgets.theme_editor import _CHART_SERIES_SLOTS, _TOKEN_GROUPS

        out: list[tuple[str, str]] = []
        for _group, entries in _TOKEN_GROUPS:
            for token, _label in entries:
                value = getattr(tokens, token, None)
                if isinstance(value, str) and value.startswith("#"):
                    out.append((token, value))
        series = getattr(tokens, "chart_series", None) or []
        out += [(f"chart_series[{i}]", c) for i, c in enumerate(series[:_CHART_SERIES_SLOTS])]
        return out

    def test_every_editor_swatch_rings_legibly_in_every_shipped_theme(self, qtbot):
        """DEC-266: the whole editor, under each theme actually applied.

        The first version of this test never called ``apply_theme``, so every
        swatch was built under the default dark theme — whose ``text_primary`` /
        ``primary_btn_text`` *are* a light/dark pair. Classic Blue's are not
        (``#e0e0e8`` vs ``#ffffff``, 1.31:1 apart), so the ring had no dark option
        and 18 of its 54 swatches sat under 3:1 — including the ``text_primary``
        swatch DEC-264 was written to fix. The bug was in a theme the test could
        not see, on tokens the test did not sample.
        """
        from control_ofc.ui.widgets import theme_editor as te

        # The swatch reads the active theme through `theme_editor.active_theme`,
        # so patch that rather than calling the real `apply_theme`. Applying a
        # theme is application-wide and irreversible in-process: `active_theme()`
        # falls back to a fresh default when nothing has been applied yet, so
        # "restoring" it actually applies a theme that was not there before,
        # changing app font metrics for every later test. That surfaced as an
        # unrelated splitter-geometry test failing by a few pixels in full-suite
        # order only. Patching keeps the sweep hermetic and still fails if the
        # ring goes back to being derived from tokens.
        failures: list[str] = []
        for theme_name, tokens in TestKeyboardFocusContrast._all_themes():
            with mock.patch.object(te, "active_theme", return_value=tokens):
                for token, colour in self._editor_swatch_colours(tokens):
                    swatch = te.ColorSwatch(token, colour)
                    qtbot.addWidget(swatch)
                    ratio = contrast_ratio(self._ring_of(swatch), colour)
                    if ratio < 3.0:
                        failures.append(f"{theme_name}.{token} ({colour}) rings at {ratio:.2f}:1")

        assert not failures, (
            "focus rings below 3:1 on Theme Editor swatches — a swatch is one of "
            "~54 identical siblings, so an illegible ring loses the keyboard user "
            "entirely (WCAG 1.4.11):\n  " + "\n  ".join(failures)
        )

    def test_the_ring_does_not_depend_on_theme_tokens(self, qtbot):
        """The ring must be derivable from the swatch alone.

        Any pairing of *theme tokens* is a guess about values a user can edit —
        which is exactly how DEC-264's ``text_primary``/``primary_btn_text`` pair
        held in three shipped themes and failed in the fourth. Pinning the worst
        case over the full grey ramp is what makes the guarantee universal rather
        than a property of the themes that happen to ship today.
        """
        from control_ofc.ui.widgets.theme_editor import ColorSwatch

        worst = 99.0
        for v in range(0, 256, 5):
            grey = f"#{v:02x}{v:02x}{v:02x}"
            swatch = ColorSwatch("t", grey)
            # Parented to the qtbot so the DEC-230 teardown flush destroys them
            # deterministically; 52 unowned widgets is the shape that used to
            # take the shiboken finalizer down.
            qtbot.addWidget(swatch)
            worst = min(worst, contrast_ratio(self._ring_of(swatch), grey))
        assert worst >= 3.0, (
            f"the worst grey-ramp swatch rings at {worst:.2f}:1 — the ring is being "
            "chosen from something other than the swatch's own luminance"
        )
        # The crossover grey is the theoretical floor for a black/white choice.
        # Documented in docs/03 as ~4.6:1, so hold the claim to its own number.
        assert worst >= 4.5, (
            f"worst case is {worst:.2f}:1 but docs/03 claims ~4.6:1 — fix one or the other"
        )

    def test_the_ring_tracks_the_swatch_colour(self, qtbot):
        """Not a constant dressed up as a computation.

        Asserting only "every colour passes" would also pass if the ring were
        hardcoded to something that happens to clear 3:1 on the sampled colours.
        The ring must actually *differ* between a near-black and a near-white
        swatch, which no fixed token can do.
        """
        from control_ofc.ui.widgets.theme_editor import ColorSwatch

        dark = ColorSwatch("t", "#000000")
        light = ColorSwatch("t", "#ffffff")
        qtbot.addWidget(dark)
        qtbot.addWidget(light)
        assert self._ring_of(dark) != self._ring_of(light), (
            "the ring is the same colour on a black and a white swatch — it is not "
            "being chosen against the swatch at all"
        )


class TestAccessibleNames:
    """DEC-251: a control whose only label is a glyph or a tooltip announces as
    an anonymous button.

    A tooltip is **not** an accessible name — Qt does not expose it as one, and a
    keyboard-only screen-reader user never triggers it. `setAccessibleName`
    appeared zero times in the whole `ui/` tree before this."""

    def test_colour_swatches_are_named(self, qtbot):
        from control_ofc.ui.widgets.theme_editor import ColorSwatch

        swatch = ColorSwatch("accent_primary", "#1FB88A")
        qtbot.addWidget(swatch)
        assert swatch.text() == "", "precondition: the swatch's content is its colour"
        assert "accent_primary" in swatch.accessibleName()

    @staticmethod
    def _glyph_only_buttons() -> list[tuple[str, int, str, bool]]:
        """Every button in ``ui/`` whose label carries no word.

        Returns ``(relative_path, lineno, label, is_named)``. "Wordless" is
        defined as *no alphanumeric character in the label* rather than by
        length: `+`, `⋮`, `→`, `←`, `↺` and friends say nothing to a screen reader,
        while `OK` is a real word and needs no separate name. An **empty** label
        counts too — that is the shape an icon-only button takes, and it is the
        most nameless case of all.

        A name counts if it arrives either through ``make_button``'s
        ``accessible_name`` (the preferred form, DEC-268) or through a
        ``setAccessibleName`` on the *same target path* in the *innermost
        enclosing scope* —
        the latter so the Theme Editor's raw ``QPushButton("↺")`` still passes.
        That one deliberately stays a raw button: it sets no ``variant``, so
        routing it through the factory would restyle ~50 live instances.

        DEC-269 closed four holes the first version shipped with, each verified
        by a reviewer building the shape and watching the scan miss it:

        * **empty labels were skipped outright** — and `footer.py` really does
          build ``QPushButton("")``, so the claim that the shape did not exist
          was wrong;
        * **module- and class-body scope were never walked**, only function
          bodies;
        * **the name check never inspected the keyword's value**, so
          ``accessible_name=None`` / ``""`` passed the lint while
          ``make_button`` skipped the setter behind its truthiness guard;
        * **the ``setAccessibleName`` fallback matched on the bare attribute
          name**, so ``decoy._add_btn.setAccessibleName(...)`` marked an entirely
          different ``real._add_btn`` as named.

        Still not covered, and deliberately recorded rather than implied: a label
        built at runtime (f-string, variable, module constant), an aliased import
        of ``make_button``, and a ``QPushButton`` subclass. Those need the
        runtime tests beside this one, not more AST.
        """
        src = Path(__file__).resolve().parent.parent / "src" / "control_ofc"
        found: list[tuple[str, int, str, bool]] = []

        # Explicit, greppable opt-out for a button that is built empty and given
        # word-bearing text at runtime. It must be a marker rather than a silent
        # skip on empty labels, because "empty" is also exactly what an icon-only
        # button looks like — the case most in need of a name.
        #
        # Why these cannot simply take an `accessible_name`: an explicit name
        # FREEZES and stops tracking `setText` (measured — a button reading
        # "Unassigned Fans (3)" kept exposing "(0)"). For a chip whose entire job
        # is to announce changing status, a static name is worse than Qt's text
        # fallback, which follows the live text.
        EXEMPT = "a11y: text-at-runtime"

        def target_path(node) -> str | None:
            """Dotted path for an assignment target or attribute receiver.

            Full path, not the last segment: matching on the bare name is what
            let a decoy with a same-named attribute vouch for a different object.
            """
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                inner = target_path(node.value)
                return f"{inner}.{node.attr}" if inner else None
            return None

        for py_file in sorted((src / "ui").rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            lines = source.splitlines()
            tree = ast.parse(source)

            # Scope = each function body FIRST, then the module as a trailing
            # catch-all for anything built at module or class level.
            #
            # Order is load-bearing. Putting the module first re-opened the very
            # hole the dotted-path match closed: `ast.walk(tree)` recurses into
            # every function, so the module pass reached every call site and the
            # `seen` dedupe then skipped the narrower per-function passes
            # entirely — making them dead code and every verdict file-wide. Two
            # locals both named `btn` in unrelated methods would vouch for each
            # other, and `btn` is the name this codebase actually uses.
            scopes: list[ast.AST] = [
                n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            scopes.append(tree)

            seen: set[tuple[int, int]] = set()
            for scope in scopes:
                named_here = {
                    target_path(call.func.value)
                    for call in ast.walk(scope)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setAccessibleName"
                }
                named_here.discard(None)

                for node in ast.walk(scope):
                    if not isinstance(node, ast.Call):
                        continue
                    callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if callee not in ("make_button", "QPushButton"):
                        continue

                    # Label may be positional or `text=`.
                    label = node.args[0] if node.args else None
                    if label is None:
                        label = next((k.value for k in node.keywords if k.arg == "text"), None)
                    if label is None:
                        # No literal label at all — a runtime-built one. Out of
                        # this scan's reach; see the docstring.
                        continue
                    if not (isinstance(label, ast.Constant) and isinstance(label.value, str)):
                        continue
                    text = label.value
                    if text.strip() and any(ch.isalnum() for ch in text):
                        continue  # has a real word — Qt's text fallback suffices

                    # A nested function is walked twice (once via the module, once
                    # as its own scope); report each call site once.
                    key = (node.lineno, node.col_offset)
                    if key in seen:
                        continue
                    seen.add(key)

                    # The keyword must carry a NON-EMPTY string — `make_button`
                    # skips `setAccessibleName` for a falsy value, so presence
                    # alone proves nothing.
                    named = any(
                        k.arg == "accessible_name"
                        and isinstance(k.value, ast.Constant)
                        and isinstance(k.value.value, str)
                        and k.value.value.strip()
                        for k in node.keywords
                    )
                    if not named:
                        # The marker must be on the line IMMEDIATELY above the
                        # call. A two-line window let a marker written for one
                        # button silence the next one below it.
                        marker = lines[node.lineno - 2] if node.lineno >= 2 else ""
                        named = EXEMPT in marker
                    if not named:
                        for assign in ast.walk(scope):
                            if isinstance(assign, ast.Assign) and assign.value is node:
                                for t in assign.targets:
                                    if target_path(t) in named_here:
                                        named = True

                    found.append((py_file.relative_to(src).as_posix(), node.lineno, text, named))
        return found

    def test_every_glyph_only_button_is_named(self):
        """DEC-268: a button whose label is a bare glyph must carry a name.

        Five shipped unnamed — `+` twice, `⋮`, `→`, `←` — each with a perfectly
        good tooltip, which is precisely the trap: a tooltip reads like a fix and
        is not one. Qt does not expose it as an accessible name and a
        keyboard-only screen-reader user never triggers it, so all five announced
        as "button". Review had not caught it in the ~15 releases since DEC-251
        set the rule, which is why this is a lint and not a convention.
        """
        buttons = self._glyph_only_buttons()
        assert buttons, "the AST scan found no glyph buttons at all — it has stopped working"

        unnamed = [f"{f}:{ln} {text!r}" for f, ln, text, named in buttons if not named]
        assert not unnamed, (
            "glyph-only buttons with no accessible name — each announces as an "
            "anonymous 'button' to a screen reader. Pass `accessible_name=` to "
            "`make_button`:\n  " + "\n  ".join(unnamed)
        )

    def test_the_runtime_text_exemption_is_not_quietly_widening(self):
        """DEC-269: the exemption marker is trusted on sight, so pin who has it.

        The lint cannot verify that an exempted button really is given
        word-bearing text later — it is a comment, not a proof. That is
        acceptable for the one button that genuinely needs it, and unacceptable
        as an open door: without this, anyone can silence a permanently
        unlabelled icon button with a false comment and the gate stays green.
        Widening the set is now a visible diff that has to be argued for.
        """
        src = Path(__file__).resolve().parent.parent / "src" / "control_ofc"
        marked = sorted(
            f"{py.relative_to(src).as_posix()}:{n}"
            for py in (src / "ui").rglob("*.py")
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1)
            if "a11y: text-at-runtime" in line
        )
        assert marked == ["ui/components/footer.py:58"], (
            "the runtime-text exemption set changed. Each entry must be a button "
            f"that is genuinely re-labelled at runtime — verify, then update: {marked}"
        )

    def test_the_member_editor_arrows_announce_their_direction(self, qtbot):
        """The rendered outcome, not just the source.

        The AST lint above proves the argument is passed; this proves it reaches
        the widget. `→` and `←` are the pair most dependent on a name — they are
        mirror images that differ only in direction, so an unnamed pair leaves a
        screen-reader user unable to tell add from remove.
        """
        from control_ofc.ui.widgets.member_editor import MemberEditorDialog

        editor = MemberEditorDialog(current_members=[], available_outputs=[])
        qtbot.addWidget(editor)

        add = editor.findChild(QPushButton, "MemberEditor_Btn_add")
        remove = editor.findChild(QPushButton, "MemberEditor_Btn_remove")
        assert add is not None and remove is not None, "objectNames changed"
        assert not any(ch.isalnum() for ch in add.text()), "precondition: label is a bare glyph"

        assert "Add" in add.accessibleName()
        assert "Remove" in remove.accessibleName()
        assert add.accessibleName() != remove.accessibleName(), (
            "the two arrows are mirror images; identical names leave add and "
            "remove indistinguishable"
        )

    def test_every_settings_toggle_takes_its_row_label_as_its_name(self, qtbot):
        """DEC-268: a ToggleSwitch carries no text, so the row must name it.

        `set_accessible_label` existed for this from DEC-255 and no caller ever
        used it, leaving eight booleans on the Settings page all announcing as
        "Toggle" — indistinguishable from one another, which is worse than
        nameless because it reads as deliberate. Naming happens in
        `_setting_row`, the one place holding both the switch and its words.
        """
        from control_ofc.ui.components.toggle_switch import ToggleSwitch
        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        toggles = page.findChildren(ToggleSwitch)
        assert len(toggles) >= 5, f"expected the Settings booleans, found {len(toggles)}"

        generic = [t.objectName() for t in toggles if t.accessibleName() in ("", "Toggle")]
        assert not generic, (
            "these toggles still announce as the generic fallback rather than "
            f"their row label: {generic}"
        )
        names = [t.accessibleName() for t in toggles]
        assert len(set(names)) == len(names), (
            f"two toggles share an accessible name, so they are indistinguishable: {names}"
        )

    def test_buttons_sharing_visible_text_announce_differently(self, qtbot):
        """DEC-269: Qt's text fallback is not enough when two buttons share text.

        Both re-seed buttons on Settings read "Run again", so the fallback made
        them announce identically — the same defect as the eight "Toggle"
        switches, at lower volume. The AST lint cannot catch this: "Run again"
        contains real words, so it is not wordless.
        """
        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        aliases = page.findChild(QPushButton, "Settings_Btn_reseedAliases")
        series = page.findChild(QPushButton, "Settings_Btn_reseedSeries")
        assert aliases is not None and series is not None, "objectNames changed"
        assert aliases.text() == series.text() == "Run again", "precondition"

        assert aliases.accessibleName() and series.accessibleName()
        assert aliases.accessibleName() != series.accessibleName(), (
            "two buttons with identical visible text must not also announce "
            "identically — nothing then distinguishes them by keyboard"
        )

    def test_every_reset_button_in_the_theme_editor_is_named(self, qtbot):
        """There is one per token, so an unnamed glyph is heard dozens of times."""
        from PySide6.QtWidgets import QPushButton

        from control_ofc.ui.widgets.theme_editor import ThemeEditorWidget

        editor = ThemeEditorWidget()
        qtbot.addWidget(editor)

        resets = [b for b in editor.findChildren(QPushButton) if b.text() == "↺"]
        assert len(resets) > 10, "expected the per-token reset buttons"
        unnamed = [b for b in resets if not b.accessibleName()]
        assert not unnamed, f"{len(unnamed)} reset buttons announce only as a glyph"

    @staticmethod
    def _announced_label(widget) -> str:
        """What assistive tech would actually use to say what ``widget`` is.

        **Not** ``widget.accessibleName()``. That reads back the Qt property,
        which is set on every control here and is therefore evidence of nothing:
        Qt's Unix ``QAccessibleComboBox::text(Name)`` falls through to the
        current item and silently discards the property, so a combo can carry a
        perfect ``accessibleName`` and still announce "Dashboard". Asserting the
        property is exactly the false green this helper exists to prevent — the
        first version of this test passed on two controls that announce nothing
        useful.

        So: ask the accessibility interface. A Name distinct from the Value is a
        real name; otherwise fall back to the ``Label`` relation a ``setBuddy``
        publishes, which is what AT-SPI exports as *labelled-by* and what Orca
        reads for a combo box.
        """
        from PySide6.QtGui import QAccessible

        iface = QAccessible.queryAccessibleInterface(widget)
        if iface is None:
            return ""
        name = iface.text(QAccessible.Text.Name)
        value = iface.text(QAccessible.Text.Value)
        if name and name != value:
            return name
        for target, flags in iface.relations():
            if flags & QAccessible.RelationFlag.Label:
                return target.text(QAccessible.Text.Name)
        return ""

    # Surfaces the runtime sweep constructs. Each entry is (name, factory).
    #
    # 273-g replaced a SettingsPage-only check with this. The naming rule moved
    # into `components.a11y.name_value_control` precisely so every surface could
    # use it, and a test that only ever built one page could not notice that the
    # other fourteen had not.
    #
    # Only surfaces that construct headlessly with cheap fixtures are here.
    # Dialogs needing real domain objects are covered by the AST lint below
    # instead — which is the whole reason that lint exists.
    @staticmethod
    def _surface_registry():
        from control_ofc.services.app_state import AppState
        from control_ofc.services.diagnostics_service import DiagnosticsService
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.pages.dashboard_page import DashboardPage
        from control_ofc.ui.pages.logs_page import LogsPage
        from control_ofc.ui.pages.settings_page import SettingsPage
        from control_ofc.ui.pages.system_state_page import SystemStatePage
        from control_ofc.ui.pages.theme_page import ThemePage
        from control_ofc.ui.sidebar import Sidebar
        from control_ofc.ui.widgets.curve_editor import CurveEditor
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
        from control_ofc.ui.widgets.timeline_chart import TimelineChart

        return [
            ("SettingsPage", SettingsPage),
            ("ThemePage", ThemePage),
            ("DashboardPage", DashboardPage),
            ("SystemStatePage", SystemStatePage),
            ("Sidebar", Sidebar),
            ("CurveEditor", CurveEditor),
            # 277-m: the wizard's identify page is the ONLY site where
            # `name_value_control` runs BEFORE `setEditable(True)`, and that
            # ordering changes which `QAccessibleComboBox::text(Name)` path
            # applies — precisely the DEC-269 trap the helper exists to encode.
            # The AST lint proves the call was *written*; only this sweep proves
            # Qt honoured it. It was one of five surfaces left on lint alone.
            ("FanConfigWizard", lambda: FanConfigWizard(AppState())),
            ("LogsPage", lambda: LogsPage(DiagnosticsService(None))),
            ("TimelineChart", lambda: TimelineChart(HistoryStore())),
            ("SensorSeriesPanel", lambda: SensorSeriesPanel(SeriesSelectionModel())),
        ]

    def test_every_value_control_announces_what_it_sets(self, qtbot):
        """273-g: a combo/spin/slider/edit must say what it is for, app-wide.

        Register row 273-g. `SettingsPage` was the only page whose value controls
        were named, and the enforcing test was scoped to it — so fourteen other
        files shipped controls that announce a bare value and nothing else
        ("combo box, Dashboard"), which tells a screen-reader user what it says
        but never what it does.

        Deliberately over every control each surface builds, not a fixed list:
        the naming lives in a shared helper so a control added later is named by
        construction, and a test enumerating today's controls would not notice
        tomorrow's.
        """
        from PySide6.QtWidgets import (
            QAbstractSpinBox,
            QComboBox,
            QLineEdit,
            QPlainTextEdit,
            QSlider,
        )

        # QSlider, deliberately not QAbstractSlider: the latter also matches the
        # QScrollBar Qt creates inside every scroll area, which is generated
        # furniture rather than a control anyone authored — demanding names for
        # those would bury the real findings in ~20 false ones.
        types = (QComboBox, QAbstractSpinBox, QSlider, QLineEdit, QPlainTextEdit)
        checked = 0
        nameless: list[str] = []

        for surface_name, factory in self._surface_registry():
            widget = factory()
            qtbot.addWidget(widget)
            controls = [c for t in types for c in widget.findChildren(t)]
            # A QComboBox owns an internal QLineEdit when editable, and a spin
            # box always owns one; a slider inside a scroll area likewise. Those
            # are implementation details of a named parent, not controls a user
            # tabs to. Nothing else is filtered — in particular a *disabled*
            # control is still checked, because the daemon-config controls ship
            # disabled until the daemon answers and a user who never connects one
            # would otherwise get no coverage at all.
            controls = [
                c for c in controls if not isinstance(c.parent(), QComboBox | QAbstractSpinBox)
            ]
            checked += len(controls)
            nameless += [
                f"{surface_name}: {c.objectName() or repr(c)}"
                for c in controls
                if not self._announced_label(c).strip()
            ]

        # 273-g Tier C is CLOSED: ThemeEditor's ~54 hex inputs are named from
        # their own token (and the chart-series fields from their slot), so the
        # sweep now covers this surface like any other. The floor below rises
        # with them, which is what stops the exclusion being reintroduced by
        # accident — a re-deferred ThemeEditor would drop `checked` back under it.
        assert checked >= 60, (
            f"the sweep found only {checked} value controls across "
            f"{len(self._surface_registry())} surfaces — the registry or the "
            "findChildren filter has stopped finding things"
        )
        assert not nameless, (
            "these controls announce as an anonymous combo/spin/slider/edit with "
            "no indication of what they set:\n  " + "\n  ".join(nameless)
        )

    def test_no_value_control_is_constructed_without_a_name(self):
        """273-g: an AST inventory, because the runtime sweep cannot see everything.

        `test_every_value_control_announces_what_it_sets` only checks surfaces it
        can construct, and several dialogs need real domain objects to build. A
        combo added to one of those would be invisible to it — and invisible is
        how fourteen files came to ship unnamed controls in the first place.

        So this scans the source instead: every `QComboBox()`/`QSpinBox()`/… bound
        to a name must have that name passed to `name_value_control` somewhere in
        the same function, or appear in the deferred list below. Deliberately
        modelled on `test_every_glyph_only_button_is_named`, which does the same
        job for buttons.

        The two mechanisms are complements, not duplicates: the AST lint proves
        the call is *written*, the runtime sweep proves it *works*. Only the
        second could have caught a `setAccessibleName` that Qt silently discards
        — which it did, on the theme picker, during this very change.
        """
        import ast

        # Value controls that announce a bare value with no indication of purpose.
        # QScrollBar is absent deliberately: Qt generates them inside scroll areas
        # and nobody authors one.
        value_types = {
            "QComboBox",
            "QSpinBox",
            "QDoubleSpinBox",
            "QAbstractSpinBox",
            "QLineEdit",
            "QPlainTextEdit",
            "QTextEdit",
            "QSlider",
        }
        # Helpers that name a control on the caller's behalf. `_setting_row` takes
        # (title, control) and calls `name_value_control` itself, so passing a
        # control to it IS naming it.
        naming_helpers = {"name_value_control", "_setting_row"}

        # 273-g Tier C is CLOSED — the two per-instance deferrals are named:
        # `theme_editor.py`'s hex inputs from their own token (and chart-series
        # fields from their slot), `control_card.py`'s manual slider from its own
        # control. The set stays, empty, so the machinery below keeps working and
        # a future deferral has somewhere to go with a reason attached.
        deferred: set[str] = set()

        # Named, but through an indirection this scan cannot follow: each is put
        # into a `controls[key]` dict and passed to `_setting_row` as a subscript,
        # so no bare Name reaches the helper's argument list.
        #
        # NOT a hole. Every one is on `SettingsPage`, which the runtime sweep
        # above constructs and checks by ANNOUNCED name — the stronger of the two
        # assertions. This list exists so that fact is written down instead of the
        # scan quietly under-reporting, and the staleness check below fails if any
        # entry stops matching.
        indirectly_named = {
            "ui/pages/settings_page.py:_startup_delay_spin",
            "ui/pages/settings_page.py:_poll_interval_spin",
            "ui/pages/settings_page.py:_serial_port_edit",
            "ui/pages/settings_page.py:_serial_timeout_spin",
        }

        def bound_name(node):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                return target.id
            if isinstance(target, ast.Attribute):
                return target.attr
            return None

        ui = Path(__file__).resolve().parent.parent / "src" / "control_ofc" / "ui"
        sites, unnamed, matched_deferred, matched_indirect = 0, [], set(), set()
        # 277-g: constructions whose assignment target `bound_name` cannot read —
        # a subscript (`controls["x"] = QComboBox()`) or a tuple unpack. The scan
        # used to `continue` past these BEFORE `sites += 1`, so such a site
        # neither failed the naming check nor inflated the staleness floor: it was
        # invisible in both directions, and the test read as stricter than it was.
        # None exist today; asserting that is what keeps it true.
        skipped_targets: list[str] = []

        for py in sorted(ui.rglob("*.py")):
            rel = py.relative_to(ui.parent).as_posix()
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                named = set()
                for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                    func = call.func
                    fname = (
                        func.id
                        if isinstance(func, ast.Name)
                        else func.attr
                        if isinstance(func, ast.Attribute)
                        else None
                    )
                    if fname not in naming_helpers:
                        continue
                    for arg in call.args:
                        if isinstance(arg, ast.Name):
                            named.add(arg.id)
                        elif isinstance(arg, ast.Attribute):
                            named.add(arg.attr)
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Assign | ast.AnnAssign):
                        continue
                    call = node.value
                    if not (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id in value_types
                    ):
                        continue
                    var = bound_name(node)
                    if var is None:
                        skipped_targets.append(f"{rel}:{node.lineno} {call.func.id}")
                        continue
                    sites += 1
                    key = f"{rel}:{var}"
                    if key in deferred:
                        matched_deferred.add(key)
                    elif key in indirectly_named:
                        matched_indirect.add(key)
                    elif var not in named:
                        unnamed.append(f"{rel}:{node.lineno} {call.func.id} {var}")

        assert not skipped_targets, (
            "these value controls are assigned to a subscript or tuple target, so "
            "this scan cannot tell whether they are named — it would pass over "
            "them silently rather than reporting them (277-g). Bind them to a "
            "plain name, or teach `bound_name` to read the target:\n  "
            + "\n  ".join(skipped_targets)
        )
        assert sites >= 30, (
            f"the AST scan found only {sites} value-control constructions — it has "
            "stopped working, and would pass no matter what shipped"
        )
        assert not unnamed, (
            "these controls are built without being passed to `name_value_control`, "
            "so they announce a bare value and never say what they set:\n  " + "\n  ".join(unnamed)
        )
        assert matched_indirect == indirectly_named, (
            "the indirectly-named list no longer matches reality — unmatched: "
            f"{sorted(indirectly_named - matched_indirect)}. Either those controls "
            "are now named directly (delete the entry) or they moved, and the list "
            "is excusing nothing while looking like it excuses something."
        )
        assert matched_deferred == deferred, (
            "the 273-g phase-2 deferral list no longer matches reality — "
            f"unmatched: {sorted(deferred - matched_deferred)}. Either those "
            "controls were named (delete the entry, closing Tier C) or they moved "
            "and the list is now excusing nothing while looking like it excuses "
            "something."
        )

    def test_a_settings_combo_is_labelled_by_more_than_its_property(self, qtbot):
        """The regression that a property assertion cannot see.

        `setAccessibleName` on a non-editable QComboBox is discarded by Qt on
        Unix, so the naming only works because ``_setting_row`` also calls
        ``setBuddy``. Delete the buddy and ``accessibleName()`` still reads back
        perfectly while the combo announces its current item — this pins the
        mechanism that actually carries the label.
        """
        from PySide6.QtGui import QAccessible
        from PySide6.QtWidgets import QComboBox

        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        for name in ("Settings_Combo_startupPage", "Settings_Combo_preferredCpu"):
            combo = page.findChild(QComboBox, name)
            assert combo is not None, f"objectName changed: {name}"
            iface = QAccessible.queryAccessibleInterface(combo)
            relations = [f for _t, f in iface.relations()]
            assert any(f & QAccessible.RelationFlag.Label for f in relations), (
                f"{name} has no Label relation, so on Linux it announces only its "
                "current item — `setAccessibleName` alone is discarded here"
            )
            assert self._announced_label(combo).strip(), f"{name} resolves to no label"

    def test_dir_picker_buttons_are_distinguishable(self, qtbot):
        """Three identical Browse/Reset pairs, built by one helper.

        Hand-rolled ``QPushButton``s left all six with no objectName (so
        ``findChild`` could not reach them and nothing could test them) and only
        two distinct labels between them — three buttons announcing "Browse..."
        and three announcing "Reset". Same defect as the two "Run again" buttons
        DEC-269 fixed, at three times the volume.
        """
        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        keys = ("profilesDir", "themesDir", "exportDir")
        browse = [
            page.findChild(QPushButton, f"Settings_Btn_browse{k[0].upper()}{k[1:]}") for k in keys
        ]
        reset = [
            page.findChild(QPushButton, f"Settings_Btn_reset{k[0].upper()}{k[1:]}") for k in keys
        ]
        assert all(b is not None for b in browse + reset), (
            "the dir-picker buttons must carry unique objectNames: "
            f"browse={[b is not None for b in browse]} reset={[b is not None for b in reset]}"
        )

        assert {b.text() for b in browse} == {"Browse..."}, "precondition: visible text is shared"
        assert {b.text() for b in reset} == {"Reset"}, "precondition: visible text is shared"

        names = [b.accessibleName() for b in browse + reset]
        assert all(names), f"an unnamed dir-picker button falls back to shared text: {names}"
        assert len(set(names)) == len(names), (
            f"two dir-picker buttons announce identically, so nothing tells them apart: {names}"
        )

    def test_dir_picker_buttons_stay_wired_after_the_component_swap(self, qtbot, monkeypatch):
        """`.click()`, not `_handler()` — the swap to ``make_button`` moved the
        construction, and the connection is the thing most likely to break.
        """
        from PySide6.QtWidgets import QFileDialog

        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        # The export row is the one whose browse handler sets the label directly,
        # with no migrate-files QMessageBox to block a headless run.
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "/tmp/ofc-export")
        )
        page.findChild(QPushButton, "Settings_Btn_browseExportDir").click()
        assert page._export_dir_label.text() == "/tmp/ofc-export", "Browse lost its connection"

        page.findChild(QPushButton, "Settings_Btn_resetExportDir").click()
        assert page._export_dir_label.text() == "", "Reset lost its connection"

    def test_each_dir_picker_row_acts_on_its_own_path(self, qtbot):
        """`_dir_picker_row` takes the label, the callback, the objectName slug
        and the spoken noun as four independent arguments, so a call site can
        pair ``key="themesDir"`` with the *profiles* label and nothing visible
        breaks — the button would simply reset the wrong path under the wrong
        name. Clicking one Reset must clear exactly one label.
        """
        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        labels = {
            "profilesDir": page._profiles_dir_label,
            "themesDir": page._themes_dir_label,
            "exportDir": page._export_dir_label,
        }
        for key, target in labels.items():
            for label in labels.values():
                label.setText(f"/sentinel/{label.objectName()}")
            page.findChild(QPushButton, f"Settings_Btn_reset{key[0].upper()}{key[1:]}").click()

            assert target.text() == "", f"Reset for {key} did not clear its own label"
            others = {k: v.text() for k, v in labels.items() if k != key}
            assert all(others.values()), (
                f"Reset for {key} also cleared another row's path — the helper's "
                f"label/key pairing is crossed: {others}"
            )
