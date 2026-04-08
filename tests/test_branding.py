"""Tests for branding — assets, splash, about dialog, microcopy."""

from __future__ import annotations

from pathlib import Path

from onlyfans.ui import microcopy

# ---------------------------------------------------------------------------
# Asset existence
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "branding"


class TestAssets:
    def test_banner_exists(self):
        assert (_ASSETS_DIR / "banner.png").exists()

    def test_splash_exists(self):
        assert (_ASSETS_DIR / "splash" / "splash.png").exists()

    def test_app_icon_svg_exists(self):
        assert (_ASSETS_DIR / "app_icon" / "app_icon.svg").exists()

    def test_app_icon_svg_is_valid_xml(self):
        import xml.etree.ElementTree as ET

        svg_path = _ASSETS_DIR / "app_icon" / "app_icon.svg"
        tree = ET.parse(svg_path)
        root = tree.getroot()
        assert "svg" in root.tag


# ---------------------------------------------------------------------------
# Microcopy
# ---------------------------------------------------------------------------


class TestMicrocopy:
    def test_fun_mode_returns_fun_text(self):
        microcopy.set_fun_mode(True)
        text = microcopy.get("splash_status_ready")
        assert text == "Cooling content delivered."

    def test_pro_mode_returns_professional_text(self):
        microcopy.set_fun_mode(False)
        text = microcopy.get("splash_status_ready")
        assert text == "Ready"

    def test_unknown_key_returns_key(self):
        text = microcopy.get("nonexistent_key_xyz")
        assert text == "nonexistent_key_xyz"

    def test_toggle_persists(self):
        microcopy.set_fun_mode(True)
        assert microcopy.is_fun_mode() is True
        microcopy.set_fun_mode(False)
        assert microcopy.is_fun_mode() is False

    def test_all_keys_have_both_variants(self):
        """Every microcopy entry must have both fun and professional text."""
        for key, (fun, pro) in microcopy._COPY.items():
            assert fun, f"Missing fun text for {key}"
            assert pro, f"Missing professional text for {key}"
            assert fun != pro, f"Fun and pro text are identical for {key}"


# ---------------------------------------------------------------------------
# Splash screen
# ---------------------------------------------------------------------------


class TestSplash:
    def test_splash_status_updates(self, qtbot):
        from onlyfans.ui.splash import AppSplashScreen

        microcopy.set_fun_mode(False)
        splash = AppSplashScreen()
        qtbot.addWidget(splash)
        splash.set_status("splash_status_ready")
        assert splash._status_text == "Ready"


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------


class TestAboutDialog:
    def test_about_dialog_renders(self, qtbot):
        from onlyfans.ui.about_dialog import AboutDialog

        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "About OnlyFans"

    def test_about_has_close_button(self, qtbot):
        from PySide6.QtWidgets import QPushButton

        from onlyfans.ui.about_dialog import AboutDialog

        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        btn = dlg.findChild(QPushButton, "About_Btn_close")
        assert btn is not None


# ---------------------------------------------------------------------------
# Branding helpers
# ---------------------------------------------------------------------------


class TestBrandingHelpers:
    def test_load_app_icon(self):
        from onlyfans.ui.branding import load_app_icon

        icon = load_app_icon()
        assert icon is not None
        assert not icon.isNull()

    def test_banner_path(self):
        from onlyfans.ui.branding import banner_image_path

        path = banner_image_path()
        assert path is not None
        assert path.exists()

    def test_splash_path(self):
        from onlyfans.ui.branding import splash_image_path

        path = splash_image_path()
        assert path is not None
        assert path.exists()
