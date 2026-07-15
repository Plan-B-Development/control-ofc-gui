"""DEC-208: the built-in default theme is the green Control-OFC palette.

Verifies the palette is WCAG-clean, the primary-button text flipped dark, the
bundled-font tokens default correctly, and the generated stylesheet references
the heading font plus the new shared-component classes.
"""

from __future__ import annotations

from control_ofc.ui.theme import (
    ThemeTokens,
    build_stylesheet,
    check_contrast_warnings,
    default_dark_theme,
)


def test_default_theme_is_green_and_named_default_dark():
    t = default_dark_theme()
    # Name is intentionally kept so a saved theme_name=="Default Dark" adopts
    # green with no settings migration.
    assert t.name == "Default Dark"
    assert t.app_bg == "#0A0E08"
    assert t.surface_1 == "#0D1610"
    assert t.surface_2 == "#14241C"
    assert t.accent_primary == "#1FB88A"
    assert t.border_default == "#1A2E20"
    assert t.text_primary == "#C8D4C0"


def test_primary_button_text_is_dark():
    # White on the teal accent is 2.54:1 (fails WCAG AA); the green theme flips
    # the primary-button label dark so it clears 4.5:1.
    assert default_dark_theme().primary_btn_text == "#0A0E08"


def test_font_tokens_default_to_bundled_families():
    t = default_dark_theme()
    assert t.font_family == "DM Sans"
    assert t.font_family_heading == "Space Grotesk"


def test_green_default_passes_wcag_contrast():
    assert check_contrast_warnings(default_dark_theme()) == []


def test_status_colours_match_theme_mockup():
    t = default_dark_theme()
    assert t.status_ok == "#1FB88A"  # unifies with the accent
    assert t.status_warn == "#FFAA00"
    assert t.status_crit == "#FF4D4D"
    assert t.status_info == "#00A8FF"


def test_stylesheet_references_heading_font():
    css = build_stylesheet(default_dark_theme())
    assert "Space Grotesk" in css


def test_stylesheet_defines_new_component_classes():
    css = build_stylesheet(default_dark_theme())
    for token in (
        ".BracketCard",
        ".SectionHeader",
        ".SectionBar",
        ".Pill_success",
        ".Pill_warning",
        ".Pill_critical",
        'variant="secondary"',
        'variant="danger"',
        ".DenseTable",
        "#StatusRibbon_Root",
        "#StatusFooter_Root",
        "#ModalScrim",
    ):
        assert token in css, f"missing QSS for {token}"


def test_empty_heading_font_token_omits_font_family_rule():
    # A theme with no heading font (e.g. Classic Blue) must not emit an empty
    # `font-family: '';` rule.
    css = build_stylesheet(ThemeTokens(font_family_heading=""))
    assert "font-family: ''" not in css
