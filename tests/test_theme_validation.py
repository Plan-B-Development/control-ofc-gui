"""Tests for theme token validation on load and import (F7 / DEC-142, DEC-217)."""

from __future__ import annotations

import json

import pytest

from control_ofc.ui.theme import (
    ThemeTokens,
    _apply_token_dict,
    build_stylesheet,
    load_theme,
    theme_file_path,
)


def test_strict_rejects_bad_color():
    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"app_bg": "not-a-colour"}, strict=True)


def test_strict_rejects_bad_chart_series_entry():
    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"chart_series": ["#fff", "oops"]}, strict=True)


def test_strict_accepts_8digit_and_chart_series():
    t = ThemeTokens()
    _apply_token_dict(
        t, {"modal_bg": "#11223344", "chart_series": ["#abc", "#aabbcc"]}, strict=True
    )
    assert t.modal_bg == "#11223344"
    assert t.chart_series == ["#abc", "#aabbcc"]


def test_non_strict_drops_bad_color_keeps_valid():
    t = ThemeTokens()
    default_bg = ThemeTokens().app_bg
    _apply_token_dict(t, {"app_bg": "oops", "surface_1": "#123456"}, strict=False)
    assert t.app_bg == default_bg  # invalid dropped → default kept
    assert t.surface_1 == "#123456"  # valid applied


def test_clamps_base_font_size():
    t = ThemeTokens()
    _apply_token_dict(t, {"base_font_size_pt": 9999}, strict=False)
    assert t.base_font_size_pt == 16
    t2 = ThemeTokens()
    _apply_token_dict(t2, {"base_font_size_pt": 0}, strict=False)
    assert t2.base_font_size_pt == 7
    t3 = ThemeTokens()
    _apply_token_dict(t3, {"base_font_size_pt": "huge"}, strict=False)
    assert t3.base_font_size_pt == ThemeTokens().base_font_size_pt  # non-int → default


def test_coerces_font_family():
    t = ThemeTokens()
    _apply_token_dict(t, {"font_family": 123}, strict=False)
    assert t.font_family == ThemeTokens().font_family  # non-str ignored
    t2 = ThemeTokens()
    _apply_token_dict(t2, {"font_family": "x" * 1000}, strict=False)
    assert len(t2.font_family) <= 256  # length-capped


def test_load_theme_coerces_corrupt_file(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"name": "Bad", "app_bg": "}; color:red", "base_font_size_pt": 9999}))
    t = load_theme(p)  # must not raise
    assert t.app_bg == ThemeTokens().app_bg  # invalid colour dropped
    assert t.base_font_size_pt == 16  # clamped
    assert t.name == "Bad"  # non-colour string kept


# ---------------------------------------------------------------------------
# DEC-217 — untrusted theme JSON must not reach the filesystem or the
# stylesheet unvalidated. `name` becomes a file stem; the font families are
# interpolated into generated QSS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../app_settings",
        "../" * 85 + "evil",  # deepest escape the 256-char cap allows
        "a/b",
        "..",
        "...",
        "",
        "   ",
        "nul\x00byte",
    ],
)
def test_dec217_unsafe_name_dropped_non_strict(name):
    t = ThemeTokens()
    _apply_token_dict(t, {"name": name}, strict=False)
    assert t.name == ThemeTokens().name  # dropped → dataclass default kept


@pytest.mark.parametrize("name", ["../app_settings", "a/b", "..", ""])
def test_dec217_unsafe_name_raises_strict(name):
    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"name": name}, strict=True)


def test_dec217_traversal_name_cannot_escape_themes_dir(tmp_path):
    """The pre-fix bug: a theme named '../app_settings' overwrote settings."""
    themes = tmp_path / "themes"
    themes.mkdir()
    victim = tmp_path / "app_settings.json"
    victim.write_text('{"real": "settings"}')

    with pytest.raises(ValueError):
        theme_file_path("../app_settings", themes)

    assert victim.read_text() == '{"real": "settings"}'  # untouched


def test_dec217_theme_file_path_rejects_symlink_escape(tmp_path):
    """Containment is checked on the resolved destination, so a themes-dir
    entry symlinked outside is refused too (no '..' involved)."""
    themes = tmp_path / "themes"
    themes.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("secret")
    (themes / "sneaky.json").symlink_to(outside)

    with pytest.raises(ValueError):
        theme_file_path("sneaky", themes)


def test_dec217_theme_file_path_normal_name(tmp_path):
    themes = tmp_path / "themes"
    themes.mkdir()
    assert theme_file_path("My Theme", themes) == themes / "my_theme.json"
    assert theme_file_path("", themes) == themes / "custom.json"  # empty → Custom


def test_dec217_theme_file_path_rejects_overlong_name(tmp_path):
    """The 256-*char* load cap can still yield a filename over NAME_MAX (255
    *bytes*); theme_file_path must reject it as a ValueError rather than let
    save_theme raise an uncaught OSError (DEC-217)."""
    themes = tmp_path / "themes"
    themes.mkdir()
    # 250 ASCII stem + ".json" == 255 bytes == NAME_MAX → the largest that fits.
    assert theme_file_path("a" * 250, themes) == themes / (("a" * 250) + ".json")
    with pytest.raises(ValueError):
        theme_file_path("a" * 251, themes)  # 256 bytes > NAME_MAX
    # Multi-byte: each CJK char is 3 UTF-8 bytes, so 84 * 3 + len(".json") == 257.
    with pytest.raises(ValueError):
        theme_file_path("码" * 84, themes)


def test_dec217_qss_injection_via_heading_font_rejected():
    """font_family_heading lands inside a quoted QSS declaration; a quote or
    brace in it would close that declaration and inject arbitrary rules."""
    payload = "X'; } QLabel { color: transparent; } QLabel { font-family: 'Y"
    t = ThemeTokens()
    _apply_token_dict(t, {"font_family_heading": payload}, strict=False)
    assert t.font_family_heading == ThemeTokens().font_family_heading  # dropped

    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"font_family_heading": payload}, strict=True)


def test_dec217_injected_rules_never_reach_the_stylesheet(tmp_path):
    p = tmp_path / "evil.json"
    p.write_text(
        json.dumps({"name": "Evil", "font_family_heading": "X'; } QLabel { color: transparent; }"})
    )
    tokens = load_theme(p)
    # The unsafe heading font is dropped at the load boundary, so the payload's
    # distinctive injection marker never lands in the generated QSS. (A plain
    # "color: transparent" substring check is unusable — a clean stylesheet
    # already contains "background-color: transparent" several times.)
    assert tokens.font_family_heading == ThemeTokens().font_family_heading
    qss = build_stylesheet(tokens)
    assert "X'; }" not in qss


@pytest.mark.parametrize("family", ["DM Sans", "Space Grotesk", "", "Noto Sans CJK JP"])
def test_dec217_legitimate_font_families_still_accepted(family):
    t = ThemeTokens()
    _apply_token_dict(t, {"font_family_heading": family}, strict=True)
    assert t.font_family_heading == family


@pytest.mark.parametrize("name", ["Default Dark", "Classic Blue", "Mitch's Dark", "v2.1 (wip)"])
def test_dec217_legitimate_names_still_accepted(name):
    t = ThemeTokens()
    _apply_token_dict(t, {"name": name}, strict=True)
    assert t.name == name
