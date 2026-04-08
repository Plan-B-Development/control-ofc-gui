"""R30: Theme typography system tests.

Covers: font_family and base_font_size_pt in ThemeTokens, font_sizes()
role computation, build_stylesheet() uses computed sizes, theme
save/load roundtrip for typography fields.
"""

from __future__ import annotations

from onlyfans.ui.theme import (
    ThemeTokens,
    build_stylesheet,
    default_dark_theme,
    font_sizes,
)


class TestFontSizesComputation:
    """Role-based font sizes computed correctly from base."""

    def test_default_base_produces_expected_sizes(self):
        fs = font_sizes(10)
        assert fs["body"] == 10
        assert fs["title"] == 16  # 10 * 1.6
        assert fs["section"] == 13  # 10 * 1.3
        assert fs["small"] == 9  # 10 * 0.9
        assert fs["card_title"] == 11  # 10 * 1.1
        assert fs["card_value"] == 22  # 10 * 2.2
        assert fs["brand"] == 14  # 10 * 1.4

    def test_larger_base_scales_proportionally(self):
        fs = font_sizes(14)
        assert fs["body"] == 14
        assert fs["title"] == 22  # 14 * 1.6 = 22.4 → 22
        assert fs["small"] == 13  # 14 * 0.9 = 12.6 → 13

    def test_minimum_base_produces_readable_sizes(self):
        fs = font_sizes(7)
        assert fs["body"] == 7
        assert fs["small"] == 6  # 7 * 0.9 = 6.3 → 6
        assert fs["title"] == 11  # 7 * 1.6 = 11.2 → 11


class TestThemeTokensTypography:
    """ThemeTokens has typography fields with sensible defaults."""

    def test_default_font_family_is_empty(self):
        tokens = ThemeTokens()
        assert tokens.font_family == ""

    def test_default_base_font_size(self):
        tokens = ThemeTokens()
        assert tokens.base_font_size_pt == 10

    def test_custom_font_family(self):
        tokens = ThemeTokens(font_family="Noto Sans")
        assert tokens.font_family == "Noto Sans"

    def test_custom_base_size(self):
        tokens = ThemeTokens(base_font_size_pt=14)
        assert tokens.base_font_size_pt == 14


class TestBuildStylesheetTypography:
    """Stylesheet uses computed font sizes from base, not hardcoded px."""

    def test_stylesheet_contains_computed_body_size(self):
        tokens = ThemeTokens(base_font_size_pt=12)
        css = build_stylesheet(tokens)
        # body = 12pt (base * 1.0)
        assert "12pt" in css

    def test_stylesheet_no_hardcoded_13px(self):
        """The old hardcoded 13px global font size is gone."""
        tokens = default_dark_theme()
        css = build_stylesheet(tokens)
        assert "font-size: 13px" not in css

    def test_stylesheet_has_card_value_class(self):
        tokens = default_dark_theme()
        css = build_stylesheet(tokens)
        assert ".CardValue" in css

    def test_stylesheet_font_sizes_change_with_base(self):
        css_10 = build_stylesheet(ThemeTokens(base_font_size_pt=10))
        css_14 = build_stylesheet(ThemeTokens(base_font_size_pt=14))
        # At base=10, title=16pt; at base=14, title=22pt
        assert "16pt" in css_10
        assert "22pt" in css_14


class TestThemeSaveLoadRoundtrip:
    """Typography fields persist through save/load cycle."""

    def test_roundtrip_preserves_font_family(self, tmp_path):
        from onlyfans.ui.theme import load_theme, save_theme

        tokens = ThemeTokens(font_family="Monospace", base_font_size_pt=14)
        path = tmp_path / "test_theme.json"
        save_theme(tokens, path)
        loaded = load_theme(path)
        assert loaded.font_family == "Monospace"
        assert loaded.base_font_size_pt == 14

    def test_load_theme_without_font_fields_uses_defaults(self, tmp_path):
        """Existing themes without typography fields get defaults."""
        import json

        path = tmp_path / "old_theme.json"
        path.write_text(json.dumps({"name": "Old Theme", "version": 2}))
        from onlyfans.ui.theme import load_theme

        loaded = load_theme(path)
        assert loaded.font_family == ""
        assert loaded.base_font_size_pt == 10
