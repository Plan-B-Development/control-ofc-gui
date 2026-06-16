"""AIO Phase 1 (DEC-156) — coolant classification, user override, capability
parsing, and the liquid-cooler role floor. Outcome-focused; no real hardware."""

from __future__ import annotations

from control_ofc.api.models import parse_capabilities, parse_hwmon_headers
from control_ofc.services.app_settings_service import (
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
    AppSettingsService,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    ControlMember,
    control_minimum_pct,
    infer_member_role,
)
from control_ofc.ui.sensor_knowledge import (
    classify_sensor,
    classify_sensor_with_overrides,
    is_liquid_cooler_chip,
)

# ---------------------------------------------------------------------------
# Coolant classification (sensor_knowledge)
# ---------------------------------------------------------------------------


class TestCoolantClassification:
    def test_kraken_chip_classifies_coolant_high(self):
        for chip in ("x53", "z53", "kraken2023", "kraken2023elite", "kraken2"):
            c = classify_sensor(chip, "temp1")
            assert c.source_class == "coolant", chip
            assert c.confidence == "high", chip

    def test_aquacomputer_coolant_label_high(self):
        # d5next is a cooler chip → labelled coolant channel is high confidence.
        c = classify_sensor("d5next", "Coolant temp")
        assert c.source_class == "coolant"
        assert c.confidence == "high"

    def test_aquacomputer_external_probe_not_coolant(self):
        # Avoid false positives: an unlabelled/external probe on a multi-channel
        # cooler must NOT be force-classified as coolant.
        c = classify_sensor("d5next", "External sensor 1")
        assert c.source_class != "coolant"

    def test_coolant_label_on_generic_chip_medium(self):
        c = classify_sensor("nct6798", "Water Pump")
        assert c.source_class == "coolant"
        assert c.confidence == "medium"

    def test_asus_ec_water_in_out_regression(self):
        c_in = classify_sensor("asus_ec_sensors", "Water In")
        assert c_in.source_class == "coolant_in"
        assert c_in.confidence == "high"
        assert "inlet" in c_in.display_description.lower()
        c_out = classify_sensor("asus_ec_sensors", "Water Out")
        assert c_out.source_class == "coolant_out"

    def test_ordinary_sensor_not_coolant(self):
        assert classify_sensor("k10temp", "Tctl").source_class != "coolant"
        assert classify_sensor("it8696", "temp1").source_class != "coolant"

    def test_is_liquid_cooler_chip(self):
        assert is_liquid_cooler_chip("Z53")  # case-insensitive
        assert is_liquid_cooler_chip("d5next")
        assert not is_liquid_cooler_chip("corsaircpro")  # fan hub, not a cooler
        assert not is_liquid_cooler_chip("nct6798")


# ---------------------------------------------------------------------------
# User override resolver (sensor_knowledge)
# ---------------------------------------------------------------------------


class TestOverrideResolver:
    def test_override_forces_coolant(self):
        # A board sensor that would auto-classify as non-coolant.
        auto = classify_sensor("nct6798", "SYSTIN")
        assert auto.source_class != "coolant"
        forced = classify_sensor_with_overrides(
            "hwmon:nct6798:sys:temp1",
            chip_name="nct6798",
            label="SYSTIN",
            overrides={"hwmon:nct6798:sys:temp1": "coolant"},
        )
        assert forced.source_class == "coolant"
        assert forced.confidence == "high"

    def test_no_override_delegates(self):
        r = classify_sensor_with_overrides(
            "hwmon:k10temp:0:Tctl", chip_name="k10temp", label="Tctl", overrides={}
        )
        assert r.source_class == "cpu_control"

    def test_unknown_override_value_delegates(self):
        r = classify_sensor_with_overrides(
            "s1", chip_name="k10temp", label="Tctl", overrides={"s1": "bogus"}
        )
        assert r.source_class == "cpu_control"

    def test_override_only_affects_its_sensor(self):
        r = classify_sensor_with_overrides(
            "other", chip_name="k10temp", label="Tctl", overrides={"s1": "coolant"}
        )
        assert r.source_class == "cpu_control"


# ---------------------------------------------------------------------------
# Override persistence + export exclusion (app_settings)
# ---------------------------------------------------------------------------


def test_sensor_class_overrides_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(sensor_class_overrides={"hwmon:nct6798:sys:temp1": "coolant"})

    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.sensor_class_overrides == {"hwmon:nct6798:sys:temp1": "coolant"}


def test_sensor_class_overrides_excluded_from_export():
    assert "sensor_class_overrides" in MACHINE_SPECIFIC_KEYS
    s = AppSettings(sensor_class_overrides={"s1": "coolant"})
    assert "sensor_class_overrides" not in s.portable_dict()


def test_sensor_class_overrides_whitelist_rejects_unknown_values():
    # Only known source_class values survive the load trust boundary.
    s = AppSettings.from_dict(
        {"sensor_class_overrides": {"good": "coolant", "bad": "rm -rf", "n": 5}}
    )
    assert s.sensor_class_overrides == {"good": "coolant"}


# ---------------------------------------------------------------------------
# app_state setter + signal
# ---------------------------------------------------------------------------


def test_set_sensor_class_override_sets_and_emits(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.sensor_class_override_changed, timeout=1000) as blocker:
        state.set_sensor_class_override("s1", "coolant")
    assert blocker.args == ["s1", "coolant"]
    assert state.sensor_class_overrides == {"s1": "coolant"}


def test_set_sensor_class_override_clear_removes(qtbot):
    state = AppState()
    state.set_sensor_class_override("s1", "coolant")
    with qtbot.waitSignal(state.sensor_class_override_changed, timeout=1000) as blocker:
        state.set_sensor_class_override("s1", "")
    assert blocker.args == ["s1", ""]
    assert "s1" not in state.sensor_class_overrides


# ---------------------------------------------------------------------------
# Capability + header parsing (models)
# ---------------------------------------------------------------------------


def test_parse_aio_hwmon_capability_full():
    caps = parse_capabilities(
        {
            "devices": {
                "aio_hwmon": {
                    "present": True,
                    "status": "supported",
                    "pump_writable": True,
                    "coolant_available": True,
                },
                "aio_usb": {"present": False, "status": "unsupported"},
            }
        }
    )
    assert caps.aio_hwmon.present is True
    assert caps.aio_hwmon.status == "supported"
    assert caps.aio_hwmon.pump_writable is True
    assert caps.aio_hwmon.coolant_available is True


def test_parse_aio_hwmon_old_daemon_fallback():
    # Pre-1.18.0 daemon sends only present+status; new fields default to False.
    caps = parse_capabilities(
        {"devices": {"aio_hwmon": {"present": False, "status": "unsupported"}}}
    )
    assert caps.aio_hwmon.present is False
    assert caps.aio_hwmon.pump_writable is False
    assert caps.aio_hwmon.coolant_available is False


def test_parse_aio_hwmon_absent_devices_defaults():
    caps = parse_capabilities({})
    assert caps.aio_hwmon.present is False
    assert caps.aio_hwmon.status == "unsupported"


def test_parse_hwmon_header_is_aio():
    headers = parse_hwmon_headers(
        {
            "headers": [
                {"id": "hwmon:z53:d:pwm1:pwm1", "is_writable": True, "is_aio": True},
                {"id": "hwmon:it8696:d:pwm1:CPU", "is_writable": True},
            ]
        }
    )
    assert headers[0].is_aio is True
    assert headers[1].is_aio is False  # default for pre-1.18.0 daemon


# ---------------------------------------------------------------------------
# Liquid-cooler role floor (profile_service, DEC-095 + DEC-156)
# ---------------------------------------------------------------------------


class TestAioRoleFloor:
    def test_kraken_pump_labelled_pwm1_floors_at_30(self):
        # A Kraken pump header whose only label is "pwm1" must still floor at
        # 30% via the chip embedded in the stable id (is_aio path).
        m = ControlMember(
            source="hwmon", member_id="hwmon:z53:nodev:pwm1:pwm1", member_label="pwm1"
        )
        assert infer_member_role(m) == CONTROL_ROLE_CPU_PUMP
        assert control_minimum_pct([m]) == 30.0

    def test_aquacomputer_pump_floors_at_30(self):
        m = ControlMember(
            source="hwmon", member_id="hwmon:d5next:nodev:pwm1:pwm1", member_label="pwm1"
        )
        assert infer_member_role(m) == CONTROL_ROLE_CPU_PUMP

    def test_radiator_fan_only_is_chassis_20(self):
        # A radiator fan on an ordinary motherboard header stays chassis (20%).
        m = ControlMember(
            source="hwmon", member_id="hwmon:it8696:d:pwm2:CHA_FAN", member_label="Radiator Top"
        )
        assert infer_member_role(m) == CONTROL_ROLE_CHASSIS
        assert control_minimum_pct([m]) == 20.0

    def test_pump_label_still_floors_at_30(self):
        # Existing label-based path (DEC-095) is unaffected.
        m = ControlMember(
            source="hwmon", member_id="hwmon:it8696:d:pwm3:PUMP", member_label="AIO_PUMP"
        )
        assert infer_member_role(m) == CONTROL_ROLE_CPU_PUMP
