"""Tests for configurable save paths (profiles, themes, export directories)."""

from __future__ import annotations

from pathlib import Path

from control_ofc.paths import (
    _overrides,
    config_dir,
    export_default_dir,
    profiles_dir,
    set_path_overrides,
    themes_dir,
)
from control_ofc.services.app_settings_service import AppSettings

# -- path override tests --


def test_set_path_overrides_profiles(tmp_path, monkeypatch):
    """Setting profiles override causes profiles_dir() to return the override."""
    custom = tmp_path / "my_profiles"
    set_path_overrides(profiles_dir=str(custom))
    try:
        assert profiles_dir() == custom
    finally:
        _overrides.clear()


def test_set_path_overrides_themes(tmp_path, monkeypatch):
    """Setting themes override causes themes_dir() to return the override."""
    custom = tmp_path / "my_themes"
    set_path_overrides(themes_dir=str(custom))
    try:
        assert themes_dir() == custom
    finally:
        _overrides.clear()


def test_set_path_overrides_export(tmp_path, monkeypatch):
    """Setting export override causes export_default_dir() to return the override."""
    custom = tmp_path / "exports"
    set_path_overrides(export_dir=str(custom))
    try:
        assert export_default_dir() == custom
    finally:
        _overrides.clear()


def test_clear_overrides(tmp_path, monkeypatch):
    """Calling set_path_overrides with empty strings clears previous overrides."""
    custom = tmp_path / "custom_profiles"
    set_path_overrides(profiles_dir=str(custom))
    try:
        assert profiles_dir() == custom

        # Clear by calling with empty strings (the default)
        set_path_overrides()
        assert profiles_dir() == config_dir() / "profiles"
        assert themes_dir() == config_dir() / "themes"
        assert export_default_dir() == Path.home()
    finally:
        _overrides.clear()


# -- AppSettings roundtrip --


def test_app_settings_path_fields_roundtrip():
    """Path override fields survive to_dict/from_dict serialization."""
    original = AppSettings(
        profiles_dir_override="/opt/profiles",
        themes_dir_override="/opt/themes",
        export_default_dir="/tmp/exports",
    )
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.profiles_dir_override == "/opt/profiles"
    assert restored.themes_dir_override == "/opt/themes"
    assert restored.export_default_dir == "/tmp/exports"


# -- R65 behaviour-settings roundtrips --


def test_wizard_spindown_roundtrip():
    """wizard_spindown_seconds survives to_dict/from_dict serialization."""
    original = AppSettings(wizard_spindown_seconds=10)
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.wizard_spindown_seconds == 10


def test_startup_delay_roundtrip():
    """daemon_startup_delay_secs survives to_dict/from_dict serialization."""
    original = AppSettings(daemon_startup_delay_secs=5)
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.daemon_startup_delay_secs == 5


def test_hide_toggles_roundtrip():
    """hide_igpu_sensors and hide_unused_fan_headers survive roundtrip when False."""
    original = AppSettings(hide_igpu_sensors=False, hide_unused_fan_headers=False)
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.hide_igpu_sensors is False
    assert restored.hide_unused_fan_headers is False


def test_defaults_preserve_current_behaviour():
    """Default AppSettings() must match the documented R65 defaults."""
    s = AppSettings()
    assert s.wizard_spindown_seconds == 8
    assert s.daemon_startup_delay_secs == 0
    assert s.hide_igpu_sensors is True
    assert s.hide_unused_fan_headers is True
