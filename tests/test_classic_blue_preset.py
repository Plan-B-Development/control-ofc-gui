"""DEC-208: the pre-redesign blue palette ships as the 'Classic Blue' preset.

When the green look became the built-in default, the old blue "Default Dark"
palette was preserved verbatim as a selectable bundled preset so nothing is
lost. It must load cleanly, keep white primary-button text + the system font,
and still pass the WCAG AA contrast pairs it passed as the old default.
"""

from __future__ import annotations

from control_ofc.ui.theme import (
    bundled_themes_dir,
    check_contrast_warnings,
    list_bundled_themes,
    load_theme,
)


def _classic_blue_path():
    return bundled_themes_dir() / "classic_blue.json"


def test_classic_blue_ships_as_a_bundled_preset():
    names = {p.name for p in list_bundled_themes()}
    assert "classic_blue.json" in names, names


def test_classic_blue_loads_with_expected_identity():
    t = load_theme(_classic_blue_path())
    assert t.name == "Classic Blue"
    # The old blue palette, preserved verbatim.
    assert t.app_bg == "#1a1a2e"
    assert t.surface_2 == "#1f2b47"
    assert t.accent_primary == "#2f73c4"


def test_classic_blue_keeps_white_button_text_and_system_font():
    t = load_theme(_classic_blue_path())
    assert t.primary_btn_text == "#ffffff"  # white worked on the blue accent
    assert t.font_family == ""  # system default — the pre-bundle look
    assert t.font_family_heading == ""


def test_classic_blue_passes_wcag_contrast():
    # It was the shipping default before the redesign, so it must still pass.
    assert check_contrast_warnings(load_theme(_classic_blue_path())) == []
