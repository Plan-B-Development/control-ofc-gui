"""DEC-208: the bundled OFL fonts ship with the package and register at startup."""

from __future__ import annotations

from control_ofc.ui.fonts import fonts_dir, register_bundled_fonts


def test_bundled_font_files_ship():
    d = fonts_dir()
    assert (d / "SpaceGrotesk.ttf").exists()
    assert (d / "DMSans.ttf").exists()
    # The SIL OFL licenses must travel with the fonts.
    assert (d / "OFL-SpaceGrotesk.txt").exists()
    assert (d / "OFL-DMSans.txt").exists()


def test_register_bundled_fonts_makes_families_available(qtbot):
    from PySide6.QtGui import QFontDatabase

    families = register_bundled_fonts()
    assert "Space Grotesk" in families
    assert "DM Sans" in families
    # And they are actually queryable from the Qt font database — these are the
    # exact strings the theme's font tokens use.
    db_families = QFontDatabase.families()
    assert "Space Grotesk" in db_families
    assert "DM Sans" in db_families


def test_register_bundled_fonts_is_idempotent(qtbot):
    first = set(register_bundled_fonts())
    second = set(register_bundled_fonts())
    assert first == second
    assert {"Space Grotesk", "DM Sans"} <= second
