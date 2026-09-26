"""DEC-431 (`G158`): settings import, demo persistence, theme persistence, bundle.

- `DC-p`: an import file with nothing to import is refused before any backup.
- `DC-q`: a demo session reads the real profile folder and never writes or
  removes a file in it; Import Config is unavailable in demo.
- `DC-r`: a saved "Default Dark" wins at startup, and theme Save carries the
  font controls.
- `DC-u`: the "Your setup" free text stays out of the support bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import OperationMode
from control_ofc.paths import config_dir, profiles_dir, set_path_overrides
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    PROFILE_SCHEMA_VERSION,
    ProfileService,
    default_profiles,
)


def _snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.glob("*.json"))} if d.exists() else {}


# ─── DC-q: demo never writes the profile folder ────────────────────────────


class TestDemoProfilesStayInMemory:
    def _seed_old_schema_profile(self) -> tuple[Path, str]:
        """A real profile one schema behind, so load() would migrate and write it back."""
        d = profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        doc = default_profiles()[0].to_dict()
        doc["version"] = PROFILE_SCHEMA_VERSION - 1
        path = d / f"{doc['id']}.json"
        path.write_text(json.dumps(doc))
        return path, doc["id"]

    def test_demo_reads_real_profiles_and_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()
        path, pid = self._seed_old_schema_profile()
        before = _snapshot(profiles_dir())

        svc = ProfileService(persist=False)
        svc.load()
        # Read: the user's own profile is shown in demo.
        assert svc.get_profile(pid) is not None

        # Every write path: edit+save, create, delete.
        edited = svc.get_profile(pid)
        edited.name = "Edited in demo"
        svc.save_profile(edited)
        new = svc.create_profile("Made in demo")
        assert svc.get_profile(new.id) is not None  # kept in memory
        assert svc.delete_profile(pid) is True
        assert svc.get_profile(pid) is None

        # Nothing on disk moved: the migration write-back, the save, the create
        # and the delete all stayed in memory.
        assert _snapshot(profiles_dir()) == before
        assert path.exists()

    def test_the_same_calls_do_write_when_persisting(self, tmp_path, monkeypatch):
        """The opposite branch: the guard must not also silence a real session."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()
        path, pid = self._seed_old_schema_profile()
        before = _snapshot(profiles_dir())

        svc = ProfileService()
        svc.load()
        assert _snapshot(profiles_dir()) != before, "the v6 file is migrated on disk"
        new = svc.create_profile("Made for real")
        assert (profiles_dir() / f"{new.id}.json").exists()
        assert svc.delete_profile(pid) is True
        assert not path.exists()

    def test_demo_on_an_empty_folder_seeds_defaults_in_memory_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()
        svc = ProfileService(persist=False)
        svc.load()
        assert svc.profiles, "defaults are still offered in demo"
        assert not profiles_dir().exists(), "demo must not even create the folder"


# ─── DC-p / DC-q: Import Config ────────────────────────────────────────────


def _settings_page(tmp_path, qtbot, monkeypatch, state=None):
    from control_ofc.ui.pages.settings_page import SettingsPage

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    set_path_overrides()
    svc = AppSettingsService()
    svc.load()
    page = SettingsPage(settings_service=svc, state=state)
    qtbot.addWidget(page)
    return page, svc


def _drive_import(page, monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        "control_ofc.ui.pages.settings_page.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    page.findChild(QPushButton, "Settings_Btn_importConfig").click()


class TestNothingToImport:
    def test_a_settings_backup_is_refused_before_any_backup_is_made(
        self, tmp_path, qtbot, monkeypatch
    ):
        page, svc = _settings_page(tmp_path, qtbot, monkeypatch)
        svc.save()  # an app_settings.json exists, so a backup WOULD be made
        backup_file = tmp_path / "settings_backup_x.json"
        backup_file.write_text(json.dumps(svc.settings.to_dict()))

        _drive_import(page, monkeypatch, backup_file)

        text = page._export_result_label.text()
        assert text.startswith("Nothing imported"), text
        assert "app_settings.json" in text
        assert "Settings imported" not in text
        assert not list((config_dir() / "backups").glob("*.json"))

    def test_a_real_export_still_imports_and_backs_up(self, tmp_path, qtbot, monkeypatch):
        """The opposite branch — the refusal must not catch a file it can read."""
        page, svc = _settings_page(tmp_path, qtbot, monkeypatch)
        svc.save()
        imp = tmp_path / "export.json"
        imp.write_text(json.dumps({"export_version": 1, "settings": {"theme_name": "New"}}))

        _drive_import(page, monkeypatch, imp)

        assert page._export_result_label.text().startswith("Settings imported")
        assert list((config_dir() / "backups").glob("settings_backup_*.json"))


class TestImportUnavailableInDemo:
    def test_import_button_follows_the_mode(self, tmp_path, qtbot, monkeypatch):
        state = AppState()
        page, _svc = _settings_page(tmp_path, qtbot, monkeypatch, state=state)
        btn = page.findChild(QPushButton, "Settings_Btn_importConfig")
        assert btn.isEnabled(), "precondition: live mode offers Import"

        state.set_mode(OperationMode.DEMO)
        assert not btn.isEnabled()
        assert "demo" in btn.toolTip().lower()

        state.set_mode(OperationMode.AUTOMATIC)
        assert btn.isEnabled()

    def test_a_demo_import_call_opens_nothing_and_writes_nothing(
        self, tmp_path, qtbot, monkeypatch
    ):
        state = AppState()
        state.set_mode(OperationMode.DEMO)
        page, _svc = _settings_page(tmp_path, qtbot, monkeypatch, state=state)
        opened: list[bool] = []
        monkeypatch.setattr(
            "control_ofc.ui.pages.settings_page.QFileDialog.getOpenFileName",
            lambda *a, **k: (opened.append(True), ("", ""))[1],
        )
        page._import_settings()
        assert opened == []


# ─── DC-r: Default Dark edits persist ──────────────────────────────────────


class TestDefaultDarkEditsPersist:
    def test_a_saved_default_dark_wins_at_startup(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module
        from control_ofc.ui.theme import default_dark_theme

        doc = {"name": "Default Dark", "version": 2, "app_bg": "#123456"}
        (tmp_path / "default_dark.json").write_text(json.dumps(doc))
        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)

        tokens = main_module._resolve_startup_theme("Default Dark")
        assert default_dark_theme().app_bg != "#123456", "precondition: an edit"
        assert tokens.app_bg == "#123456"

    def test_save_then_startup_round_trip(self, qtbot, settings_service, monkeypatch):
        """The run `DC-r` asked for, end to end: edit, Save, resolve as startup does."""
        from control_ofc import main as main_module
        from control_ofc.ui.pages.theme_page import ThemePage

        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)
        assert page._theme_editor.tokens.name == "Default Dark"
        page._theme_editor.tokens.app_bg = "#123456"
        size = page._font_size_spin.value() + 3
        page._font_size_spin.setValue(size)
        page.findChild(QPushButton, "Settings_Btn_saveTheme").click()

        tokens = main_module._resolve_startup_theme("Default Dark")
        assert tokens.app_bg == "#123456"
        assert tokens.base_font_size_pt == size, "Save must carry the font controls"

    def test_theme_page_selects_the_saved_copy_not_the_bundled_entry(self, qtbot, settings_service):
        from control_ofc.ui.pages.theme_page import ThemePage
        from control_ofc.ui.theme import save_theme, theme_file_path

        first = ThemePage(settings_service=settings_service)
        qtbot.addWidget(first)
        tokens = first._theme_editor.tokens
        tokens.app_bg = "#123456"
        save_theme(tokens, theme_file_path("Default Dark"))

        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)
        assert page._theme_combo.currentIndex() != 0, "the bundled entry is index 0"
        assert page._theme_editor.tokens.app_bg == "#123456"


class TestBuiltInDefaultDarkIsItsOwnChoice:
    """`DC-cj`, fixed in DEC-431 at the user's request: beside a saved "Default
    Dark", choosing the built-in palette must survive a restart too."""

    def _save_edited_default_dark(self, qtbot, settings_service):
        from control_ofc.ui.pages.theme_page import ThemePage

        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)
        page._theme_editor.tokens.app_bg = "#123456"
        page.findChild(QPushButton, "Settings_Btn_saveTheme").click()
        return page

    def test_the_built_in_entry_is_labelled_and_first(self, qtbot, settings_service):
        from control_ofc.ui.theme import BUILTIN_DEFAULT_THEME_NAME

        page = self._save_edited_default_dark(qtbot, settings_service)
        combo = page._theme_combo
        assert combo.itemText(0) == BUILTIN_DEFAULT_THEME_NAME
        assert combo.itemData(0) is None
        names = [combo.itemText(i) for i in range(combo.count())]
        assert names.count("Default Dark") == 1, names

    def test_save_selects_the_saved_file_and_apply_names_it(self, qtbot, settings_service):
        from control_ofc import main as main_module

        page = self._save_edited_default_dark(qtbot, settings_service)
        assert page._theme_combo.currentData() is not None, "Save points the picker at the file"
        page.findChild(QPushButton, "Settings_Btn_applyThemeToApp").click()

        assert settings_service.settings.theme_name == "Default Dark"
        assert main_module._resolve_startup_theme("Default Dark").app_bg == "#123456"

    def test_applying_the_built_in_entry_survives_a_restart(self, qtbot, settings_service):
        from control_ofc import main as main_module
        from control_ofc.ui.pages.theme_page import ThemePage
        from control_ofc.ui.theme import BUILTIN_DEFAULT_THEME_NAME, default_dark_theme

        page = self._save_edited_default_dark(qtbot, settings_service)
        page._theme_combo.setCurrentIndex(0)
        page.findChild(QPushButton, "Settings_Btn_applyTheme").click()  # Load
        page.findChild(QPushButton, "Settings_Btn_applyThemeToApp").click()  # Apply

        assert settings_service.settings.theme_name == BUILTIN_DEFAULT_THEME_NAME
        # Startup: the bundled palette, although default_dark.json still exists.
        restored = main_module._resolve_startup_theme(settings_service.settings.theme_name)
        assert restored.app_bg == default_dark_theme().app_bg != "#123456"
        # And the Theme page opens on the built-in entry, not the saved file.
        reopened = ThemePage(settings_service=settings_service)
        qtbot.addWidget(reopened)
        assert reopened._theme_combo.currentIndex() == 0
        assert reopened._theme_editor.tokens.app_bg == default_dark_theme().app_bg

    def test_a_file_with_the_reserved_name_is_ignored_at_startup(self, tmp_path, monkeypatch):
        from control_ofc import main as main_module
        from control_ofc.ui.theme import BUILTIN_DEFAULT_THEME_NAME, default_dark_theme

        doc = {"name": BUILTIN_DEFAULT_THEME_NAME, "version": 2, "app_bg": "#654321"}
        (tmp_path / "impostor.json").write_text(json.dumps(doc))
        monkeypatch.setattr(main_module, "themes_dir", lambda: tmp_path)
        tokens = main_module._resolve_startup_theme(BUILTIN_DEFAULT_THEME_NAME)
        assert tokens.app_bg == default_dark_theme().app_bg


class TestUpgradeKeepsTheBuiltInPalette:
    """Review P2: before DEC-431 startup never read a saved `default_dark.json`, so
    a user whose settings say "Default Dark" was seeing the bundled palette. The
    loader migrates that once, or an old saved copy would take over on upgrade."""

    def test_a_pre_rule_default_dark_becomes_the_built_in_name(self):
        from control_ofc.constants import BUILTIN_DEFAULT_THEME_NAME
        from control_ofc.services.app_settings_service import AppSettings

        migrated = AppSettings.from_dict({"theme_name": "Default Dark"})
        assert migrated.theme_name == BUILTIN_DEFAULT_THEME_NAME
        assert migrated.theme_name_scheme == 1

    def test_a_post_rule_default_dark_means_the_saved_copy(self):
        from control_ofc.services.app_settings_service import AppSettings

        kept = AppSettings.from_dict({"theme_name": "Default Dark", "theme_name_scheme": 1})
        assert kept.theme_name == "Default Dark"

    def test_other_names_are_untouched(self):
        from control_ofc.services.app_settings_service import AppSettings

        assert AppSettings.from_dict({"theme_name": "Solar Light"}).theme_name == "Solar Light"

    def test_the_migration_runs_once(self, settings_service):
        """The next save writes the marker, so a later "Default Dark" (applying the
        saved copy) is not migrated back to the built-in name."""
        from control_ofc.paths import app_settings_path

        settings_service.update(theme_name="Default Dark")
        on_disk = json.loads(app_settings_path().read_text())
        assert on_disk["theme_name"] == "Default Dark"
        assert on_disk["theme_name_scheme"] == 1
        reloaded = AppSettingsService()
        reloaded.load()
        assert reloaded.settings.theme_name == "Default Dark"

    def test_upgrade_on_a_machine_with_an_old_saved_copy(self, tmp_path, monkeypatch):
        """The reviewer's scenario end to end: an old file, an old saved copy."""
        from control_ofc import main as main_module
        from control_ofc.paths import app_settings_path, themes_dir
        from control_ofc.ui.theme import default_dark_theme

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()
        app_settings_path().parent.mkdir(parents=True, exist_ok=True)
        app_settings_path().write_text(json.dumps({"theme_name": "Default Dark"}))
        themes_dir().mkdir(parents=True, exist_ok=True)
        stale = {"name": "Default Dark", "version": 2, "base_font_size_pt": 9}
        (themes_dir() / "default_dark.json").write_text(json.dumps(stale))

        svc = AppSettingsService()
        svc.load()
        tokens = main_module._resolve_startup_theme(svc.settings.theme_name)
        assert default_dark_theme().base_font_size_pt != 9, "precondition: the copy differs"
        assert tokens.base_font_size_pt == default_dark_theme().base_font_size_pt

    def test_an_old_export_is_read_by_the_old_rule(self, tmp_path, qtbot, monkeypatch):
        from control_ofc.constants import BUILTIN_DEFAULT_THEME_NAME

        page, svc = _settings_page(tmp_path, qtbot, monkeypatch)
        svc.save()
        imp = tmp_path / "old_export.json"
        imp.write_text(
            json.dumps({"export_version": 1, "settings": {"theme_name": "Default Dark"}})
        )
        _drive_import(page, monkeypatch, imp)
        assert svc.settings.theme_name == BUILTIN_DEFAULT_THEME_NAME

    def test_a_new_export_keeps_default_dark(self, tmp_path, qtbot, monkeypatch):
        page, svc = _settings_page(tmp_path, qtbot, monkeypatch)
        svc.save()
        imp = tmp_path / "new_export.json"
        imp.write_text(
            json.dumps(
                {
                    "export_version": 1,
                    "settings": {"theme_name": "Default Dark", "theme_name_scheme": 1},
                }
            )
        )
        _drive_import(page, monkeypatch, imp)
        assert svc.settings.theme_name == "Default Dark"


def test_a_theme_file_under_the_reserved_name_never_reaches_the_picker(qtbot, settings_service):
    """Review P3: startup ignores such a file, so the page must too, or it would
    show a theme that is not in force."""
    from control_ofc.paths import themes_dir
    from control_ofc.ui.pages.theme_page import ThemePage
    from control_ofc.ui.theme import BUILTIN_DEFAULT_THEME_NAME, default_dark_theme

    themes_dir().mkdir(parents=True, exist_ok=True)
    doc = {"name": BUILTIN_DEFAULT_THEME_NAME, "version": 2, "app_bg": "#654321"}
    (themes_dir() / "impostor.json").write_text(json.dumps(doc))
    settings_service.update(theme_name=BUILTIN_DEFAULT_THEME_NAME)

    page = ThemePage(settings_service=settings_service)
    qtbot.addWidget(page)
    combo = page._theme_combo
    assert [combo.itemData(i) for i in range(combo.count())].count(None) == 1
    assert all(combo.itemText(i) != BUILTIN_DEFAULT_THEME_NAME for i in range(1, combo.count()))
    assert combo.currentIndex() == 0
    assert page._theme_editor.tokens.app_bg == default_dark_theme().app_bg


# ─── DC-u: the support bundle omits "Your setup" free text ────────────────


def test_support_bundle_omits_hardware_and_cooler_notes(tmp_path, settings_service):
    from control_ofc.services.diagnostics_service import DiagnosticsService

    settings_service.update(
        hardware_notes={"hwmon:x:y:pwm1:CPU": {"notes": "private note"}},
        cooler_notes={"model": "My Cooler", "pump_switch": "hidden"},
        theme_name="Kept Theme",
    )
    assert settings_service.settings.hardware_notes, "precondition: notes stored"
    out = tmp_path / "bundle.json"
    DiagnosticsService(settings_service=settings_service).export_support_bundle(out)
    settings = json.loads(out.read_text())["app_settings"]

    assert settings["theme_name"] == "Kept Theme", "presence: settings are in the bundle"
    assert "hardware_notes" not in settings
    assert "cooler_notes" not in settings
    assert "private note" not in out.read_text()
