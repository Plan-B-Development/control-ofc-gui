"""DEC-215: ThemePage — extracted Theme configuration page."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QPushButton

from control_ofc.ui.pages.theme_page import ThemePage
from control_ofc.ui.theme import ThemeTokens, theme_file_path
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


# ---------------------------------------------------------------------------
# DEC-217 — _save_current_theme is the last line of defence for a theme name.
# It must route every save through theme_file_path (never hand-compose a path)
# and must not crash the GUI on a filesystem error. These seal the function the
# original traversal bug lived in (previously 0% covered).
# ---------------------------------------------------------------------------


def test_dec217_save_current_theme_rejects_traversal_name(qtbot, settings_service, monkeypatch):
    """A name that bypassed the load-boundary validation must not compose a path
    outside themes_dir. Reverting _save_current_theme to the pre-DEC-217
    hand-composed ``themes_dir() / f"{name}.json"`` fails this test."""
    page = _page(qtbot, settings_service)
    saved: list = []
    monkeypatch.setattr(
        "control_ofc.ui.pages.theme_page.save_theme", lambda tokens, dest: saved.append(dest)
    )

    evil = ThemeTokens()
    evil.name = "../app_settings"  # dataclass field — bypasses load-boundary validation
    page._theme_editor.set_tokens(evil)
    page._save_current_theme()

    assert saved == []  # theme_file_path raised before any write reached save_theme


def test_dec217_save_current_theme_routes_through_theme_file_path(
    qtbot, settings_service, monkeypatch
):
    """Positive control: a legitimate name is saved to the path the sanctioned
    accessor derives — guards against a no-op mutation of the function."""
    page = _page(qtbot, settings_service)
    saved: list = []
    monkeypatch.setattr(
        "control_ofc.ui.pages.theme_page.save_theme", lambda tokens, dest: saved.append(dest)
    )

    tokens = ThemeTokens()
    tokens.name = "My Save Test"
    page._theme_editor.set_tokens(tokens)
    page._save_current_theme()

    assert saved == [theme_file_path("My Save Test")]


def test_dec217_save_current_theme_survives_oserror(qtbot, settings_service, monkeypatch):
    """A filesystem error from save_theme (full disk, or ENAMETOOLONG on a
    stricter NAME_MAX than the byte cap foresees) must be surfaced, not
    propagated — the widened except (DEC-217)."""
    page = _page(qtbot, settings_service)

    def boom(tokens, dest):
        raise OSError("disk full")

    monkeypatch.setattr("control_ofc.ui.pages.theme_page.save_theme", boom)
    tokens = ThemeTokens()
    tokens.name = "Whatever"
    page._theme_editor.set_tokens(tokens)
    page._save_current_theme()  # must not raise
