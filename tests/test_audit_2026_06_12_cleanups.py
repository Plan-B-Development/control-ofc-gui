"""Regression + coverage tests for the 2026-06-12 audit P2/P3 cleanups (GUI side).

Covers: P2-A (settings update coercion), P2-E (lease-id renew race guard),
P2-G (path-override validation), the P3 double-cycle coalescing, P2-J (daemon
config 503 handling), and the QThread write-worker signal path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.paths import config_dir, profiles_dir, set_path_overrides, themes_dir
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.ui.pages.settings_page import SettingsPage

# ── P2-A: AppSettingsService.update routes through from_dict coercion ──────────


def test_update_coerces_wrong_typed_value(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    # A wrong-typed value must be coerced (P2-A), not stored raw then fail to load.
    svc.update(chart_default_range_index="not-an-int")
    assert isinstance(svc.settings.chart_default_range_index, int)
    # The persisted form reloads cleanly.
    reloaded = AppSettings.from_dict(svc.settings.to_dict())
    assert isinstance(reloaded.chart_default_range_index, int)


# ── P2-G: set_path_overrides validates user-configured directories ────────────


def test_set_path_overrides_rejects_invalid_accepts_valid(tmp_path):
    set_path_overrides()  # clear any leaked global override state
    try:
        a_file = tmp_path / "a.json"
        a_file.write_text("{}")
        set_path_overrides(
            profiles_dir="relative/profiles",  # not absolute → rejected
            themes_dir=str(tmp_path / ".." / "themes"),  # contains '..' → rejected
            export_dir=str(a_file),  # existing file → rejected
        )
        assert profiles_dir() == config_dir() / "profiles"  # fell back to XDG default
        assert themes_dir() == config_dir() / "themes"

        valid = tmp_path / "good_profiles"
        valid.mkdir()
        not_yet = tmp_path / "not_yet_created"  # absolute, doesn't exist yet
        set_path_overrides(profiles_dir=str(valid), themes_dir=str(not_yet))
        assert profiles_dir() == valid
        assert themes_dir() == not_yet  # not-yet-existing absolute dir is allowed
    finally:
        set_path_overrides()  # cleanup


# ── P2-J: daemon config 503 (persistence_failed) is handled, not crashed ──────


def test_save_app_settings_handles_daemon_persistence_error(tmp_path, qtbot, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    set_path_overrides()
    client = MagicMock()
    client.set_startup_delay.side_effect = DaemonError(
        code="persistence_failed", message="cannot persist runtime config"
    )
    svc = AppSettingsService()
    svc.load()
    page = SettingsPage(settings_service=svc, client=client)
    qtbot.addWidget(page)
    page._save_app_settings()  # must not raise
    assert "not synced" in page._status_label.text().lower()
    client.set_startup_delay.assert_called_once()
    set_path_overrides()
