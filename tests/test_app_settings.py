"""Tests for application settings service."""

from __future__ import annotations

from control_ofc.services.app_settings_service import AppSettings, AppSettingsService


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
    )
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.default_startup_page == 2
    assert restored.restore_last_page is False
    assert restored.demo_on_disconnect is True
    assert restored.theme_name == "Custom"


def test_legacy_display_keys_ignored():
    """Settings JSON written by older versions may still contain `fun_mode`
    and `show_splash`. Loading must not error and must drop them silently."""
    legacy = {
        "version": 1,
        "theme_name": "Default Dark",
        "fun_mode": False,
        "show_splash": False,
    }
    restored = AppSettings.from_dict(legacy)
    assert restored.theme_name == "Default Dark"
    # Round-tripping must not re-introduce the legacy keys.
    data = restored.to_dict()
    assert "fun_mode" not in data
    assert "show_splash" not in data


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


def test_fan_zone_order_roundtrip():
    original = AppSettings(fan_zone_order=["zone_a", "bucket_openfan"])
    restored = AppSettings.from_dict(original.to_dict())
    assert restored.fan_zone_order == ["zone_a", "bucket_openfan"]


def test_fan_zones_collapsed_roundtrip():
    assert AppSettings.from_dict(
        AppSettings(fan_zones_collapsed=True).to_dict()
    ).fan_zones_collapsed
    # Garbage coerces to the default (False), not a crash (DEC-137 trust boundary).
    assert AppSettings.from_dict({"fan_zones_collapsed": "nope"}).fan_zones_collapsed is False


def test_fan_zone_order_machine_specific_collapsed_portable():
    s = AppSettings(fan_zone_order=["zone_a"], fan_zones_collapsed=True)
    portable = s.portable_dict()
    assert "fan_zone_order" not in portable  # local hardware keys don't travel
    assert portable.get("fan_zones_collapsed") is True  # behaviour pref does


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


# ---------------------------------------------------------------------------
# from_dict input validation (DEC-137) — the import/load trust boundary.
# ---------------------------------------------------------------------------


def test_from_dict_non_dict_returns_defaults():
    assert AppSettings.from_dict([]) == AppSettings()
    assert AppSettings.from_dict("x") == AppSettings()
    assert AppSettings.from_dict(None) == AppSettings()


def test_from_dict_coerces_wrong_types():
    s = AppSettings.from_dict(
        {
            "window_geometry": ["a", "b", "c", "d"],
            "chart_default_range_index": "evil",
            "fan_aliases": "not-a-dict",
            "series_colors": "nope",
            "restore_last_page": "yes",  # not a real bool
        }
    )
    assert s.window_geometry == [100, 100, 1200, 800]
    assert s.chart_default_range_index == 4
    assert s.fan_aliases == {}
    assert s.series_colors == {}
    assert s.restore_last_page is True  # default


def test_from_dict_rejects_bool_as_int():
    # JSON true must not become 1 for an int field.
    assert AppSettings.from_dict({"daemon_startup_delay_secs": True}).daemon_startup_delay_secs == 0


def test_from_dict_clamps_ranges():
    assert AppSettings.from_dict({"wizard_spindown_seconds": 99}).wizard_spindown_seconds == 12
    assert AppSettings.from_dict({"wizard_spindown_seconds": 1}).wizard_spindown_seconds == 5
    assert AppSettings.from_dict({"daemon_startup_delay_secs": 999}).daemon_startup_delay_secs == 30
    assert AppSettings.from_dict({"daemon_startup_delay_secs": -5}).daemon_startup_delay_secs == 0
    assert AppSettings.from_dict({"chart_default_range_index": -1}).chart_default_range_index == 0


def test_from_dict_geometry_validation():
    assert AppSettings.from_dict({"window_geometry": [1, 2, 3]}).window_geometry == [
        100,
        100,
        1200,
        800,
    ]
    assert AppSettings.from_dict({"window_geometry": [0, 0, 0, 0]}).window_geometry == [
        100,
        100,
        1200,
        800,
    ]
    assert AppSettings.from_dict({"window_geometry": [10, 20, 800, 600]}).window_geometry == [
        10,
        20,
        800,
        600,
    ]


def test_from_dict_card_size_enum():
    assert AppSettings.from_dict({"card_size": "huge"}).card_size == "comfortable"
    assert AppSettings.from_dict({"card_size": "compact"}).card_size == "compact"


def test_from_dict_card_sizes_validation():
    s = AppSettings.from_dict(
        {"controls_card_sizes": {"a": [100, 200], "b": [1], "c": "x", "d": [10, -5]}}
    )
    # Only the well-formed [width, height] of positive ints survives.
    assert s.controls_card_sizes == {"a": [100, 200]}


def test_from_dict_geometry_rejects_non_int_element():
    assert AppSettings.from_dict({"window_geometry": [10, 20, 800, "x"]}).window_geometry == [
        100,
        100,
        1200,
        800,
    ]


def test_from_dict_series_colors_drops_invalid():
    s = AppSettings.from_dict({"series_colors": {"a": "#ffffff", "b": "zzz", "c": "red"}})
    assert s.series_colors == {"a": "#ffffff"}


def test_remember_last_profile_removed():
    assert not hasattr(AppSettings(), "remember_last_profile")
    assert "remember_last_profile" not in AppSettings().to_dict()
    # A settings file written by an older version still loads cleanly.
    s = AppSettings.from_dict({"remember_last_profile": False, "theme_name": "Z"})
    assert s.theme_name == "Z"


def test_portable_dict_partition():
    from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS

    s = AppSettings(fan_aliases={"f": "n"}, hidden_chart_series=["x"], window_geometry=[1, 2, 3, 4])
    pd = s.portable_dict()
    for key in MACHINE_SPECIFIC_KEYS:
        assert key not in pd
    assert pd["fan_aliases"] == {"f": "n"}
    assert pd["hidden_chart_series"] == ["x"]
