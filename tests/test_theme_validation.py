"""Tests for theme token validation on load and import (F7 / DEC-142)."""

from __future__ import annotations

import json

import pytest

from control_ofc.ui.theme import ThemeTokens, _apply_token_dict, load_theme


def test_strict_rejects_bad_color():
    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"app_bg": "not-a-colour"}, strict=True)


def test_strict_rejects_bad_chart_series_entry():
    with pytest.raises(ValueError):
        _apply_token_dict(ThemeTokens(), {"chart_series": ["#fff", "oops"]}, strict=True)


def test_strict_accepts_8digit_and_chart_series():
    t = ThemeTokens()
    _apply_token_dict(
        t, {"modal_overlay": "#11223344", "chart_series": ["#abc", "#aabbcc"]}, strict=True
    )
    assert t.modal_overlay == "#11223344"
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
