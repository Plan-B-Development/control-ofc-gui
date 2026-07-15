"""DEC-215: ThemePage — extracted Theme configuration page."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QPushButton

from control_ofc.ui.pages.theme_page import ThemePage
from control_ofc.ui.widgets.theme_editor import ThemeEditorWidget


def _page(qtbot, settings_service):
    page = ThemePage(settings_service=settings_service)
    qtbot.addWidget(page)
    return page


def test_root_and_hosted_objectnames(qtbot, settings_service):
    page = _page(qtbot, settings_service)
    assert page.objectName() == "Theme_Root"
    assert page.findChild(QComboBox, "Settings_Combo_theme") is not None
    assert page.findChild(ThemeEditorWidget, "Settings_ThemeEditor") is not None
    for name in (
        "Settings_Btn_applyTheme",
        "Settings_Btn_saveTheme",
        "Settings_Btn_importTheme",
        "Settings_Btn_exportTheme",
        "Settings_Btn_applyThemeToApp",
    ):
        assert page.findChild(QPushButton, name) is not None, name


def test_apply_globally_emits_and_persists(qtbot, settings_service):
    page = _page(qtbot, settings_service)
    idx = page._card_size_combo.findData("large")
    page._card_size_combo.setCurrentIndex(idx)
    with qtbot.waitSignal(page.theme_changed):
        page._apply_editor_theme_to_app()
    # theme_name + card_size persisted before the emit (DEC-128).
    assert settings_service.settings.card_size == "large"
    assert settings_service.settings.theme_name  # non-empty


def test_card_size_loads_from_settings(qtbot, settings_service):
    settings_service.update(card_size="compact")
    page = _page(qtbot, settings_service)
    assert page._card_size_combo.currentData() == "compact"


def test_cleanup_is_noop(qtbot, settings_service):
    page = _page(qtbot, settings_service)
    page.cleanup()  # must not raise
