"""Settings persistence: folder moves, mirrored fields, import, failed saves.

`GSA-c` — a profiles/themes folder change is one step: files, saved override,
the override in force, and the daemon's search path, without Save Changes.
`GSA-d` — Save writes only what was edited on the page, and an import reaches
the live owners of the aliases and the chart-series selection.
`GSA-e` — a failed settings write is reported, never raised, and never costs
the window its teardown.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from control_ofc.api.models import Capabilities, ControlCapability
from control_ofc.paths import config_dir, profiles_dir, themes_dir
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.ui.pages.settings_page import SettingsPage

Yes = QMessageBox.StandardButton.Yes
No = QMessageBox.StandardButton.No
Cancel = QMessageBox.StandardButton.Cancel


def _fresh_settings():
    """What the next launch would read."""
    svc = AppSettingsService()
    svc.load()
    return svc.settings


def _answers(monkeypatch, *replies) -> list[str]:
    """Answer successive QMessageBox.question calls; returns the titles asked."""
    queue = list(replies)
    asked: list[str] = []

    def _question(_parent, title, *_a, **_k):
        asked.append(title)
        return queue.pop(0)

    monkeypatch.setattr("control_ofc.ui.pages.settings_page.QMessageBox.question", _question)
    return asked


def _pick_dir(monkeypatch, path) -> None:
    monkeypatch.setattr(
        "control_ofc.ui.pages.settings_page.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(path),
    )


def _seed(directory, name: str, text: str = "{}"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(text)
    return path


def _page(qtbot, app_state, settings_service, client=None) -> SettingsPage:
    app_state.capabilities = Capabilities(control=ControlCapability(profile_search_dir_remove=True))
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    qtbot.addWidget(page)
    return page


def _click(page, name: str) -> None:
    page.findChild(QPushButton, name).click()


# ─── GSA-c ────────────────────────────────────────────────────────────────


class TestFolderChangeIsOneStep:
    def test_browse_without_save_survives_a_restart(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        page = _page(qtbot, app_state, settings_service)
        old = _seed(profiles_dir(), "mine")
        new_dir = tmp_path / "moved-profiles"
        _pick_dir(monkeypatch, new_dir)
        _answers(monkeypatch, Yes)

        _click(page, "Settings_Btn_browseProfilesDir")  # and never press Save

        assert (new_dir / "mine.json").exists() and not old.exists()
        assert _fresh_settings().profiles_dir_override == str(new_dir)
        assert profiles_dir() == new_dir, "the new folder must be in force now"
        assert page._profiles_dir_label.text() == str(new_dir)

    def test_a_second_browse_retires_the_first_choice(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        client = MagicMock()
        page = _page(qtbot, app_state, settings_service, client)
        original = profiles_dir()
        first, second = tmp_path / "first", tmp_path / "second"

        _pick_dir(monkeypatch, first)
        _click(page, "Settings_Btn_browseProfilesDir")
        _pick_dir(monkeypatch, second)
        _click(page, "Settings_Btn_browseProfilesDir")

        calls = [c.kwargs for c in client.update_profile_search_dirs.call_args_list]
        assert calls == [
            {"add": [str(first)], "remove": [str(original)]},
            {"add": [str(second)], "remove": [str(first)]},
        ]

    def test_reset_moves_back_and_follows_on_the_daemon(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        client = MagicMock()
        page = _page(qtbot, app_state, settings_service, client)
        custom = tmp_path / "custom"
        _pick_dir(monkeypatch, custom)
        _click(page, "Settings_Btn_browseProfilesDir")
        _seed(custom, "mine")
        client.reset_mock()
        _answers(monkeypatch, Yes)

        _click(page, "Settings_Btn_resetProfilesDir")

        default = config_dir() / "profiles"
        assert (default / "mine.json").exists()
        assert _fresh_settings().profiles_dir_override == ""
        assert profiles_dir() == default
        client.update_profile_search_dirs.assert_called_once_with(
            add=[str(default)], remove=[str(custom)]
        )

    def test_themes_reset_is_saved_too(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        page = _page(qtbot, app_state, settings_service)
        _pick_dir(monkeypatch, tmp_path / "themes-elsewhere")
        _click(page, "Settings_Btn_browseThemesDir")
        assert _fresh_settings().themes_dir_override == str(tmp_path / "themes-elsewhere")

        _click(page, "Settings_Btn_resetThemesDir")

        assert _fresh_settings().themes_dir_override == ""
        assert themes_dir() == config_dir() / "themes"


class TestSameNamedFiles:
    def _setup(self, qtbot, app_state, settings_service, tmp_path):
        page = _page(qtbot, app_state, settings_service)
        old = _seed(profiles_dir(), "shared", '{"from": "old"}')
        new_dir = tmp_path / "target"
        there = _seed(new_dir, "shared", '{"from": "new"}')
        return page, old, there, new_dir

    def test_no_keeps_the_file_already_there(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        page, old, there, new_dir = self._setup(qtbot, app_state, settings_service, tmp_path)
        _pick_dir(monkeypatch, new_dir)
        asked = _answers(monkeypatch, Yes, No)

        _click(page, "Settings_Btn_browseProfilesDir")

        assert asked == ["Move existing profiles?", "Replace existing files?"]
        assert json.loads(there.read_text()) == {"from": "new"}
        assert old.exists(), "the file not moved stays where it was"

    def test_yes_replaces_it(self, qtbot, monkeypatch, app_state, settings_service, tmp_path):
        page, old, there, new_dir = self._setup(qtbot, app_state, settings_service, tmp_path)
        _pick_dir(monkeypatch, new_dir)
        _answers(monkeypatch, Yes, Yes)

        _click(page, "Settings_Btn_browseProfilesDir")

        assert json.loads(there.read_text()) == {"from": "old"}
        assert not old.exists()

    def test_cancel_changes_nothing(
        self, qtbot, monkeypatch, app_state, settings_service, tmp_path
    ):
        page, old, there, new_dir = self._setup(qtbot, app_state, settings_service, tmp_path)
        before = profiles_dir()
        _pick_dir(monkeypatch, new_dir)
        _answers(monkeypatch, Yes, Cancel)

        _click(page, "Settings_Btn_browseProfilesDir")

        assert old.exists() and json.loads(there.read_text()) == {"from": "new"}
        assert profiles_dir() == before
        assert _fresh_settings().profiles_dir_override == ""


def test_a_folder_change_that_cannot_be_saved_moves_nothing(
    qtbot, monkeypatch, app_state, settings_service, tmp_path
):
    page = _page(qtbot, app_state, settings_service)
    old = _seed(profiles_dir(), "mine")
    before = profiles_dir()
    new_dir = tmp_path / "unsaved"
    _pick_dir(monkeypatch, new_dir)
    _answers(monkeypatch, Yes)
    _fail_writes(monkeypatch)

    _click(page, "Settings_Btn_browseProfilesDir")

    assert old.exists() and not (new_dir / "mine.json").exists()
    assert profiles_dir() == before
    assert settings_service.settings.profiles_dir_override == ""
    assert "not changed" in page._status_label.text()


# ─── GSA-d ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["show_gpu_zero_rpm_warning", "show_aio_pump_info"])
def test_a_dismissed_popup_stays_dismissed_through_save(qtbot, app_state, settings_service, field):
    page = _page(qtbot, app_state, settings_service)
    # The popup's "don't show again", written while the page already exists.
    settings_service.update(**{field: False})
    # An unrelated edit, then Save — without visiting the page first.
    page._restore_page_cb.setChecked(not page._restore_page_cb.isChecked())

    _click(page, "Settings_Btn_saveApp")

    assert getattr(_fresh_settings(), field) is False
    assert page._status_label.text() == "Application settings saved"


def test_arriving_shows_the_stored_value_and_keeps_an_unsaved_edit(
    qtbot, app_state, settings_service
):
    page = _page(qtbot, app_state, settings_service)
    page._hide_igpu_cb.setChecked(not page._hide_igpu_cb.isChecked())  # edited, not saved
    edited = page._hide_igpu_cb.isChecked()
    settings_service.update(show_gpu_zero_rpm_warning=False)

    page.show()

    assert page._gpu_zero_rpm_warn_cb.isChecked() is False
    assert page._hide_igpu_cb.isChecked() is edited


def test_save_with_no_edits_writes_nothing(qtbot, monkeypatch, app_state, settings_service):
    page = _page(qtbot, app_state, settings_service)
    writes: list[object] = []
    monkeypatch.setattr(settings_service, "update", lambda **kw: writes.append(kw))

    _click(page, "Settings_Btn_saveApp")

    assert writes == []
    assert page._status_label.text() == "No changes to save"


def _import_file(tmp_path, settings: dict):
    path = tmp_path / "import.json"
    path.write_text(json.dumps({"export_version": 1, "settings": settings}))
    return path


def test_an_import_survives_the_next_rename_and_series_toggle(
    qtbot, monkeypatch, window, settings_service, tmp_path
):
    state = window._state
    state.set_fan_alias("openfan:ch00", "Old name")
    window._series_selection.set_visible("cpu_temp", False)
    page = window.settings_page
    imported = {"openfan:ch00": "Front", "openfan:ch01": "Rear"}
    path = _import_file(tmp_path, {"fan_aliases": imported, "hidden_chart_series": ["gpu_temp"]})
    monkeypatch.setattr(
        "control_ofc.ui.pages.settings_page.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )

    _click(page, "Settings_Btn_importConfig")
    assert state.fan_aliases == imported, "the live map must hold the import"
    # The next ordinary edits on other surfaces, each persisting its whole map.
    state.set_fan_alias("openfan:ch02", "Top")
    window._series_selection.set_visible("mb_temp", False)

    saved = _fresh_settings()
    assert saved.fan_aliases == {**imported, "openfan:ch02": "Top"}
    assert sorted(saved.hidden_chart_series) == ["gpu_temp", "mb_temp"]


# ─── GSA-e ────────────────────────────────────────────────────────────────


def _fail_writes(monkeypatch) -> None:
    def _raise(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("control_ofc.services.app_settings_service.atomic_write", _raise)


def test_a_failed_write_is_returned_not_raised(monkeypatch, settings_service):
    _fail_writes(monkeypatch)
    error = settings_service.update(demo_on_disconnect=True)
    assert error is not None and "No space left on device" in error


def test_save_reports_a_failed_write_and_retries(qtbot, monkeypatch, app_state, settings_service):
    page = _page(qtbot, app_state, settings_service)
    page._restore_page_cb.setChecked(not page._restore_page_cb.isChecked())
    wanted = page._restore_page_cb.isChecked()
    with monkeypatch.context() as m:
        _fail_writes(m)
        _click(page, "Settings_Btn_saveApp")
    assert page._status_label.text().startswith("Settings not saved")
    assert "No space left on device" in page._status_label.text()

    _click(page, "Settings_Btn_saveApp")  # the disk is back; the edit is still pending

    assert page._status_label.text() == "Application settings saved"
    assert _fresh_settings().restore_last_page is wanted


_CLEANED_PAGES = (
    "dashboard_page",
    "controls_page",
    "overview_page",
    "logs_page",
    "system_state_page",
    "hardware_page",
    "theme_page",
)


@pytest.mark.parametrize("failure", ["write", "raise"])
def test_close_tears_down_every_page_when_persisting_fails(qtbot, monkeypatch, window, failure):
    cleaned: list[str] = []
    for name in _CLEANED_PAGES:
        page = getattr(window, name)
        original = page.cleanup
        monkeypatch.setattr(page, "cleanup", lambda n=name, f=original: (cleaned.append(n), f())[1])
    if failure == "write":
        _fail_writes(monkeypatch)
    else:

        def _boom(**_kw):
            raise RuntimeError("settings exploded")

        monkeypatch.setattr(window._settings_service, "update", _boom)

    window.close()

    assert cleaned == list(_CLEANED_PAGES)
    assert not window._poll_age_timer.isActive()


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    from control_ofc.ui.main_window import MainWindow

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win
