"""Tests for the theme system — tokens, contrast, migration, and hardcoded color lint."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

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
        """The Logs snapshot panes. ``QPlainTextEdit`` had no QSS rule at all, so
        its viewport painted the palette's Base role — pure white until DEC-226."""
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
    backing. Every ``QPlainTextEdit`` in the app — the four Logs snapshot
    previews and the log inspector's raw-message pane — is read-only monospace
    command/log output, which is what code_block_bg is for (DEC-226)."""

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

    def test_focus_ring_does_not_reflow_neighbouring_content(self, restore_app_theme):
        """Measure laid-out geometry, not ``sizeHint()``.

        The previous version of this test asserted ``sizeHint()`` was unchanged
        between focus states — which is always true for a ``:focus``-only QSS
        change, because Qt does not re-resolve the hint without an explicit
        repolish. It passed with the border widened to 6px. Real layout geometry
        is the property that actually matters, so measure that.
        """
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        from control_ofc.ui.theme import apply_theme, default_dark_theme

        apply_theme(default_dark_theme())

        host = QWidget()
        layout = QVBoxLayout(host)
        first = QPushButton("First", host)
        first.setProperty("variant", "primary")
        second = QPushButton("Second", host)
        layout.addWidget(first)
        layout.addWidget(second)
        host.resize(240, 120)
        host.show()
        QApplication.processEvents()

        second.setFocus()
        QApplication.processEvents()
        before = (first.geometry(), second.geometry())

        first.setFocus()
        QApplication.processEvents()
        after = (first.geometry(), second.geometry())

        host.hide()
        assert before == after, (
            "focusing a button moved it or its neighbour — the ring must not "
            f"reflow the page: {before} -> {after}"
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
