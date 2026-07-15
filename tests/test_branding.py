"""Tests for branding — app icon, About dialog, sidebar brand mark."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Asset existence
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "branding"


class TestAssets:
    def test_app_icon_svg_exists(self):
        assert (_ASSETS_DIR / "app_icon" / "app_icon.svg").exists()

    def test_app_icon_svg_is_valid_xml(self):
        import xml.etree.ElementTree as ET

        svg_path = _ASSETS_DIR / "app_icon" / "app_icon.svg"
        tree = ET.parse(svg_path)
        root = tree.getroot()
        assert "svg" in root.tag


# ---------------------------------------------------------------------------
# Branding helpers
# ---------------------------------------------------------------------------


class TestBrandingHelpers:
    def test_load_app_icon(self):
        from control_ofc.ui.branding import load_app_icon

        icon = load_app_icon()
        assert icon is not None
        assert not icon.isNull()

    def test_branding_module_does_not_export_image_helpers(self):
        """`banner_image_path` and `splash_image_path` were removed; verify they
        are not re-exposed by accident."""
        from control_ofc.ui import branding

        assert not hasattr(branding, "banner_image_path")
        assert not hasattr(branding, "splash_image_path")

    def test_microcopy_module_is_absent(self):
        """`control_ofc.ui.microcopy` was removed in v1.9.0; importing it
        should fail."""
        import importlib

        try:
            importlib.import_module("control_ofc.ui.microcopy")
        except ModuleNotFoundError:
            return
        raise AssertionError("control_ofc.ui.microcopy should no longer exist")

    def test_splash_module_is_absent(self):
        """`control_ofc.ui.splash` was removed in v1.9.0; importing it should
        fail."""
        import importlib

        try:
            importlib.import_module("control_ofc.ui.splash")
        except ModuleNotFoundError:
            return
        raise AssertionError("control_ofc.ui.splash should no longer exist")


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------


class TestAboutDialog:
    def test_about_dialog_renders(self, qtbot):
        from control_ofc.ui.about_dialog import AboutDialog

        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "About Control-OFC"

    def test_about_has_close_button(self, qtbot):
        from PySide6.QtWidgets import QPushButton

        from control_ofc.ui.about_dialog import AboutDialog

        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        btn = dlg.findChild(QPushButton, "About_Btn_close")
        assert btn is not None

    def test_about_uses_plain_strings(self, qtbot):
        """Tagline and credits are now plain literals, not microcopy keys."""
        from PySide6.QtWidgets import QLabel

        from control_ofc.ui.about_dialog import AboutDialog

        dlg = AboutDialog()
        qtbot.addWidget(dlg)
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert "Fan control for Linux" in labels
        assert "Open-source fan control" in labels


# ---------------------------------------------------------------------------
# Sidebar brand mark
# ---------------------------------------------------------------------------


class TestBrandMark:
    """The brand wordmark moved from the sidebar to the top status ribbon
    (DEC-208); it is still text, never a banner image."""

    def test_ribbon_renders_text_brand(self, qtbot):
        from PySide6.QtWidgets import QLabel

        from control_ofc.ui.status_ribbon import StatusRibbon

        ribbon = StatusRibbon()
        qtbot.addWidget(ribbon)

        text_label = ribbon.findChild(QLabel, "StatusRibbon_Brand_text")
        assert text_label is not None
        assert text_label.text() == "Control-OFC"

    def test_sidebar_no_longer_renders_brand_text(self, qtbot):
        """The brand moved to the ribbon — the sidebar must not duplicate it."""
        from PySide6.QtWidgets import QLabel

        from control_ofc.ui.sidebar import Sidebar

        sidebar = Sidebar()
        qtbot.addWidget(sidebar)

        assert sidebar.findChild(QLabel, "Sidebar_Brand_text") is None

    def test_no_brand_image_objectname_anywhere(self, qtbot):
        """No banner image is rendered — brand is always text (a small icon in
        the ribbon is fine, but never a ``*_Brand_image`` label)."""
        from PySide6.QtWidgets import QLabel

        from control_ofc.ui.sidebar import Sidebar
        from control_ofc.ui.status_ribbon import StatusRibbon

        sidebar = Sidebar()
        qtbot.addWidget(sidebar)
        ribbon = StatusRibbon()
        qtbot.addWidget(ribbon)

        assert sidebar.findChild(QLabel, "Sidebar_Brand_image") is None
        assert ribbon.findChild(QLabel, "StatusRibbon_Brand_image") is None
