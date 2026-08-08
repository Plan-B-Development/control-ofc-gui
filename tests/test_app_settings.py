"""Tests for application settings service."""

from __future__ import annotations

import json

from control_ofc.services.app_settings_service import AppSettings, AppSettingsService


def test_default_settings():
    s = AppSettings()
    assert s.version == 3
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
    assert restored.version == 3
    assert restored.default_startup_page == 0


def test_dec216_page_index_migration_decrements_shifted_indices():
    """DEC-216: removing the Diagnostics page (index 3) renumbered pages 4-8 down
    to 3-7. A version-1 file's persisted page indices >= 4 decrement by one so
    restore lands on the same page; the version bumps to 2."""
    m = AppSettings.from_dict({"version": 1, "last_page_index": 6, "default_startup_page": 7})
    assert (m.last_page_index, m.default_startup_page, m.version) == (5, 6, 3)


def test_dec216_migration_boundaries_and_idempotence():
    # Index 3 (old Diagnostics) and below are NOT shifted.
    assert AppSettings.from_dict({"version": 1, "last_page_index": 3}).last_page_index == 3
    assert AppSettings.from_dict({"version": 1, "last_page_index": 2}).last_page_index == 2
    # Index 4 is the FIRST shifted page (Overview) — the exact boundary that
    # separates `>= 4` from `> 4`. Both fields must decrement here or a future
    # off-by-one (`> 4`) would strand a restore on the wrong page (audit Rank 4).
    b = AppSettings.from_dict({"version": 1, "last_page_index": 4, "default_startup_page": 4})
    assert (b.last_page_index, b.default_startup_page) == (3, 3)
    # A versionless legacy file is treated as v1 and migrates (through v2's
    # page shift, landing at the current v3).
    vless = AppSettings.from_dict({"last_page_index": 5})
    assert (vless.last_page_index, vless.version) == (4, 3)
    # An already-page-migrated (v2) file keeps its indices untouched —
    # idempotent for DEC-216 — and advances to v3 (DEC-224 key drop).
    v2 = AppSettings.from_dict({"version": 2, "last_page_index": 6, "default_startup_page": 7})
    assert (v2.last_page_index, v2.default_startup_page, v2.version) == (6, 7, 3)


def test_service_load_creates_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    assert svc.settings.version == 3
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


def test_fan_aliases_roundtrip():
    original = AppSettings(fan_aliases={"openfan:ch00": "CPU Cooler", "hwmon:fan1": "Rear"})
    data = original.to_dict()
    restored = AppSettings.from_dict(data)
    assert restored.fan_aliases == {"openfan:ch00": "CPU Cooler", "hwmon:fan1": "Rear"}


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


# ---------------------------------------------------------------------------
# Write guards (DEC-244). save() writes a whole-object snapshot with no
# read-modify-write, so a service holding the wrong state rewrites every key at
# once. Two guards stop that — an unloaded service and an ephemeral one both
# refuse. These pin both, and pin that in-memory state still updates either way.
# ---------------------------------------------------------------------------


def _settings_file(tmp_path):
    return tmp_path / "control-ofc" / "app_settings.json"


def test_unloaded_service_refuses_to_overwrite_an_existing_file(tmp_path, monkeypatch):
    """The exact defect. A default-constructed service holds *defaults*, not the
    user's settings, yet still points save() at the real file — so one update()
    replaces the lot. Three tests reached this path and wiped the developer's
    chart colours and series selection on every quality-gate run."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seeded = AppSettingsService()
    seeded.load()
    seeded.update(theme_name="Ocean Blue", series_colors={"sensor:cpu": "#ff0000"})
    before = _settings_file(tmp_path).read_bytes()

    unloaded = AppSettingsService()  # never .load()ed
    unloaded.update(theme_name="Clobbered")

    assert _settings_file(tmp_path).read_bytes() == before
    assert unloaded.settings.theme_name == "Clobbered"  # memory still updates


def test_load_of_a_missing_file_authorises_the_first_write(tmp_path, monkeypatch):
    """A fresh install must still be able to save: with no file on disk the
    defaults *are* the truth, so load() has to arm the service."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not _settings_file(tmp_path).exists()

    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Ocean Blue")

    assert json.loads(_settings_file(tmp_path).read_text())["theme_name"] == "Ocean Blue"


def test_ephemeral_service_never_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Ocean Blue")
    before = _settings_file(tmp_path).read_bytes()

    svc.make_ephemeral("demo mode")
    assert svc.is_ephemeral
    svc.update(theme_name="Demo Neon")

    assert _settings_file(tmp_path).read_bytes() == before
    assert svc.settings.theme_name == "Demo Neon"  # the session still behaves


def test_unparseable_file_is_quarantined_not_overwritten(tmp_path, monkeypatch):
    """A corrupt file used to load as defaults and then get replaced by the next
    update() — the user's whole config gone behind one log line."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = _settings_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"theme_name": "Ocean Blue", TRUNCATED')

    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Fresh Start")

    quarantined = tmp_path / "control-ofc" / "app_settings.json.corrupt"
    assert quarantined.read_text() == '{"theme_name": "Ocean Blue", TRUNCATED'
    # …and the app carries on with a clean file rather than refusing to save.
    assert json.loads(path.read_text())["theme_name"] == "Fresh Start"


def test_quarantine_keeps_the_first_corrupt_file(tmp_path, monkeypatch):
    """The original is the one worth keeping. By the time a second corruption
    happens the app has already written a clean file, so overwriting would trade
    the user's real settings for a generated one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = _settings_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("ORIGINAL USER DATA {")
    AppSettingsService().load()
    path.write_text("LATER GENERATED JUNK {")
    AppSettingsService().load()

    quarantined = tmp_path / "control-ofc" / "app_settings.json.corrupt"
    assert quarantined.read_text() == "ORIGINAL USER DATA {"


def test_unreadable_file_is_left_alone_and_blocks_writing(tmp_path, monkeypatch):
    """An OSError means we could not read it, not that it is bad. Overwriting a
    healthy config we simply failed to open is the worse outcome, so the service
    stays unloaded and persists nothing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seeded = AppSettingsService()
    seeded.load()
    seeded.update(theme_name="Ocean Blue")
    before = _settings_file(tmp_path).read_bytes()

    from control_ofc.services import app_settings_service as mod

    monkeypatch.setattr(
        mod, "load_json_capped", lambda *a, **k: (_ for _ in ()).throw(OSError("EIO"))
    )
    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Clobbered")

    assert _settings_file(tmp_path).read_bytes() == before
    assert not (tmp_path / "control-ofc" / "app_settings.json.corrupt").exists()


def test_make_ephemeral_cannot_be_undone_by_a_later_load(tmp_path, monkeypatch):
    """The latch is one-way on purpose: if load() re-armed it, any code path that
    reloaded settings mid-session would put the clobber back within reach."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Ocean Blue")
    before = _settings_file(tmp_path).read_bytes()

    svc.make_ephemeral("demo mode")
    svc.load()
    svc.update(theme_name="Clobbered")

    assert svc.is_ephemeral
    assert _settings_file(tmp_path).read_bytes() == before


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


# ---------------------------------------------------------------------------
# DEC-224 (v3): vestige-key drop migration
# ---------------------------------------------------------------------------


def test_dec224_v3_drops_vestige_keys_on_load_and_resave():
    """A v2 file carrying the four removed keys loads clean at version 3, and
    the persisted form no longer contains them (from_dict ignores; to_dict no
    longer emits)."""
    legacy = {
        "version": 2,
        "theme_name": "Kept",
        "fan_zone_order": ["zone_a"],
        "fan_zones_collapsed": True,
        "card_sensor_bindings": {"cpu_temp": "sensor:cpu1"},
        "show_hardware_guidance": False,
    }
    s = AppSettings.from_dict(legacy)
    assert s.version == 3
    assert s.theme_name == "Kept"
    out = s.to_dict()
    for dead in (
        "fan_zone_order",
        "fan_zones_collapsed",
        "card_sensor_bindings",
        "show_hardware_guidance",
    ):
        assert dead not in out


def test_dec224_v3_migration_is_idempotent_and_gated():
    # A v3 file passes through unchanged; a legacy versionless file still gets
    # the DEC-216 page shift AND lands at v3.
    assert AppSettings.from_dict({"version": 3}).version == 3
    m = AppSettings.from_dict({"last_page_index": 6})
    assert (m.last_page_index, m.version) == (5, 3)
