"""fan_zones persistence + AppState plumbing + demo seed (DEC-176).

Mirrors the established fan_aliases tests (test_app_settings.py /
test_app_state.py) — fan_zones is the portable, fan_id-keyed sibling of
fan_aliases.
"""

from __future__ import annotations

from control_ofc.services.app_settings_service import (
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
    AppSettingsService,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService

# ---------------------------------------------------------------------------
# AppSettings coercion / round-trip / portability
# ---------------------------------------------------------------------------


def test_fan_zones_roundtrip():
    original = AppSettings(fan_zones={"openfan:ch00": "Front Intake", "hwmon:fan1": "Exhaust"})
    restored = AppSettings.from_dict(original.to_dict())
    assert restored.fan_zones == {"openfan:ch00": "Front Intake", "hwmon:fan1": "Exhaust"}


def test_fan_zones_default_empty():
    assert AppSettings().fan_zones == {}
    assert AppSettings.from_dict({}).fan_zones == {}


def test_fan_zones_garbage_coerced():
    # non-str keys/values dropped by _as_str_dict; a non-dict becomes {}.
    s = AppSettings.from_dict({"fan_zones": {"openfan:ch00": "Intake", "bad": 5, 7: "x"}})
    assert s.fan_zones == {"openfan:ch00": "Intake"}
    assert AppSettings.from_dict({"fan_zones": "not-a-dict"}).fan_zones == {}


def test_fan_zones_portable_like_aliases():
    # DEC-176: zones travel with export (NOT machine-specific), mirroring aliases.
    assert "fan_zones" not in MACHINE_SPECIFIC_KEYS
    s = AppSettings(fan_zones={"openfan:ch00": "Intake"})
    assert s.portable_dict()["fan_zones"] == {"openfan:ch00": "Intake"}


def test_service_persist_fan_zones(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(fan_zones={"openfan:ch00": "Front Intake"})

    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.fan_zones == {"openfan:ch00": "Front Intake"}


# ---------------------------------------------------------------------------
# AppState.set_fan_zone + signal
# ---------------------------------------------------------------------------


def test_set_fan_zone_assigns_and_emits():
    state = AppState()
    seen: list[tuple[str, str]] = []
    state.fan_zones_changed.connect(lambda fid, zone: seen.append((fid, zone)))
    state.set_fan_zone("openfan:ch00", "Intake")
    assert state.fan_zones == {"openfan:ch00": "Intake"}
    assert seen == [("openfan:ch00", "Intake")]


def test_set_fan_zone_strips_whitespace():
    state = AppState()
    state.set_fan_zone("openfan:ch00", "  Intake  ")
    assert state.fan_zones == {"openfan:ch00": "Intake"}


def test_set_fan_zone_empty_unassigns_and_emits_blank():
    state = AppState()
    state.fan_zones = {"openfan:ch00": "Intake"}
    seen: list[tuple[str, str]] = []
    state.fan_zones_changed.connect(lambda fid, zone: seen.append((fid, zone)))
    state.set_fan_zone("openfan:ch00", "   ")  # whitespace-only clears
    assert state.fan_zones == {}
    assert seen == [("openfan:ch00", "")]


def test_set_fan_zone_unassign_missing_is_safe():
    state = AppState()
    state.set_fan_zone("nope", "")  # pop of an absent key must not raise
    assert state.fan_zones == {}


# ---------------------------------------------------------------------------
# Demo seed
# ---------------------------------------------------------------------------


def test_demo_fan_zones_one_zone_per_fan():
    z = DemoService.fan_zones()
    assert z, "demo seed should be populated"
    # dict keys are unique by construction; every value is a non-empty zone name.
    assert all(isinstance(v, str) and v for v in z.values())


def test_demo_fan_zones_leaves_gpu_unassigned():
    # GPU fans are intentionally unzoned so demo exercises the fallback path too.
    z = DemoService.fan_zones()
    assert not any(k.startswith(("amd_gpu:", "intel_gpu:")) for k in z)


def test_demo_fan_zones_ids_are_real_demo_fans():
    valid = {f.id for f in DemoService().fans()}
    assert set(DemoService.fan_zones()) <= valid


# ---------------------------------------------------------------------------
# MainWindow wiring (persist on change)
# ---------------------------------------------------------------------------


def test_main_window_persists_fan_zone_on_change(
    qtbot, app_state, profile_service, settings_service
):
    from control_ofc.ui.main_window import MainWindow

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)

    app_state.set_fan_zone("openfan:ch00", "Intake")
    assert settings_service.settings.fan_zones == {"openfan:ch00": "Intake"}

    app_state.set_fan_zone("openfan:ch00", "")  # unassign also persists
    assert settings_service.settings.fan_zones == {}
