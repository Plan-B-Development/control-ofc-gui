"""Tests for application settings service."""

from __future__ import annotations

from onlyfans.services.app_settings_service import AppSettings, AppSettingsService


def test_default_settings():
    s = AppSettings()
    assert s.version == 1
    assert s.default_startup_page == 0
    assert s.restore_last_page is True
    assert s.theme_name == "Default Dark"


def test_roundtrip():
    original = AppSettings(
        default_startup_page=2,
        restore_last_page=False,
        demo_on_disconnect=True,
        theme_name="Custom",
        fun_mode=False,
        show_splash=False,
    )
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.default_startup_page == 2
    assert restored.restore_last_page is False
    assert restored.demo_on_disconnect is True
    assert restored.theme_name == "Custom"
    assert restored.fun_mode is False
    assert restored.show_splash is False


def test_from_dict_handles_missing_keys():
    restored = AppSettings.from_dict({})
    assert restored.version == 1
    assert restored.default_startup_page == 0


def test_service_load_creates_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    assert svc.settings.version == 1
    assert svc.settings.theme_name == "Default Dark"


def test_service_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc1 = AppSettingsService()
    svc1.load()
    svc1.update(theme_name="Ocean Blue", default_startup_page=1)

    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.theme_name == "Ocean Blue"
    assert svc2.settings.default_startup_page == 1


def test_service_update_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(demo_on_disconnect=True)
    assert svc.settings.demo_on_disconnect is True
    assert svc.settings.restore_last_page is True  # unchanged


def test_export_import(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Exported Theme")

    export_path = tmp_path / "export.json"
    svc.export_settings(export_path)

    imported = svc.import_settings(export_path)
    assert imported.theme_name == "Exported Theme"

    svc.apply_imported(AppSettings(theme_name="Applied"))
    assert svc.settings.theme_name == "Applied"


def test_fan_aliases_roundtrip():
    original = AppSettings(fan_aliases={"openfan:ch00": "CPU Cooler", "hwmon:fan1": "Rear"})
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.fan_aliases == {"openfan:ch00": "CPU Cooler", "hwmon:fan1": "Rear"}


def test_card_sensor_bindings_roundtrip():
    original = AppSettings(card_sensor_bindings={"cpu_temp": "sensor:cpu1"})
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.card_sensor_bindings == {"cpu_temp": "sensor:cpu1"}


def test_hidden_chart_series_roundtrip():
    original = AppSettings(hidden_chart_series=["sensor:gpu", "fan:ch01:rpm"])
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.hidden_chart_series == ["sensor:gpu", "fan:ch01:rpm"]


def test_from_dict_unknown_keys_ignored():
    """Extra keys in JSON should not crash deserialization."""
    data = {"version": 1, "unknown_future_key": "value", "theme_name": "Test"}
    restored = AppSettings.from_dict(data)
    assert restored.theme_name == "Test"


def test_service_persist_fan_aliases(tmp_path, monkeypatch):
    """Fan aliases persist across save/load cycle."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(fan_aliases={"openfan:ch00": "Front Intake"})

    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.fan_aliases == {"openfan:ch00": "Front Intake"}
