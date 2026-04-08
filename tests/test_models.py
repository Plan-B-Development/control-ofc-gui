"""Tests for API response model parsing."""

from __future__ import annotations

from control_ofc.api.models import (
    FanReading,
    Freshness,
    SensorReading,
    parse_calibration_result,
    parse_capabilities,
    parse_fans,
    parse_hwmon_set_pwm,
    parse_lease_released,
    parse_lease_result,
    parse_lease_status,
    parse_sensor_history,
    parse_sensors,
    parse_set_pwm,
    parse_set_pwm_all,
    parse_status,
)


def test_parse_capabilities_minimal():
    data = {"api_version": 1, "daemon_version": "0.1.0", "ipc_transport": "uds"}
    caps = parse_capabilities(data)
    assert caps.api_version == 1
    assert caps.daemon_version == "0.1.0"
    assert caps.openfan.present is False
    assert caps.limits.openfan_stop_timeout_s == 8


def test_parse_capabilities_full():
    data = {
        "api_version": 1,
        "daemon_version": "0.1.0",
        "ipc_transport": "uds",
        "devices": {
            "openfan": {"present": True, "channels": 8, "rpm_support": True, "write_support": True},
            "hwmon": {
                "present": True,
                "pwm_header_count": 3,
                "lease_required": True,
                "write_support": True,
            },
            "aio_hwmon": {"present": False, "status": "unsupported"},
            "aio_usb": {"present": False, "status": "unsupported"},
        },
        "features": {
            "openfan_write_supported": True,
            "hwmon_write_supported": True,
            "lease_required_for_hwmon_writes": True,
        },
        "limits": {
            "pwm_percent_min": 0,
            "pwm_percent_max": 100,
            "openfan_stop_timeout_s": 8,
        },
    }
    caps = parse_capabilities(data)
    assert caps.openfan.present is True
    assert caps.openfan.channels == 8
    assert caps.hwmon.pwm_header_count == 3
    assert caps.limits.openfan_stop_timeout_s == 8


def test_parse_status():
    data = {
        "api_version": 1,
        "overall_status": "healthy",
        "subsystems": [
            {"name": "openfan", "status": "ok", "age_ms": 500, "reason": ""},
        ],
        "counters": {},
    }
    status = parse_status(data)
    assert status.overall_status == "healthy"
    assert len(status.subsystems) == 1
    assert status.subsystems[0].name == "openfan"


def test_parse_status_extracts_uptime():
    data = {
        "overall_status": "ok",
        "subsystems": [],
        "counters": {},
        "uptime_seconds": 3600,
        "gui_last_seen_seconds_ago": 5,
    }
    status = parse_status(data)
    assert status.uptime_seconds == 3600
    assert status.gui_last_seen_seconds_ago == 5


def test_parse_status_missing_uptime_is_none():
    data = {"overall_status": "ok", "subsystems": [], "counters": {}}
    status = parse_status(data)
    assert status.uptime_seconds is None
    assert status.gui_last_seen_seconds_ago is None


def test_parse_sensors():
    data = {
        "sensors": [
            {
                "id": "hwmon:k10temp:Tctl",
                "kind": "CpuTemp",
                "label": "CPU Tctl",
                "value_c": 45.5,
                "source": "hwmon",
                "age_ms": 500,
            },
        ]
    }
    sensors = parse_sensors(data)
    assert len(sensors) == 1
    assert sensors[0].id == "hwmon:k10temp:Tctl"
    assert sensors[0].value_c == 45.5
    assert sensors[0].freshness == Freshness.FRESH


def test_parse_fans():
    data = {
        "fans": [
            {"id": "openfan:ch00", "source": "openfan", "rpm": 850, "age_ms": 300},
            {
                "id": "openfan:ch01",
                "source": "openfan",
                "rpm": 900,
                "last_commanded_pwm": 50,
                "age_ms": 300,
            },
        ]
    }
    fans = parse_fans(data)
    assert len(fans) == 2
    assert fans[0].rpm == 850
    assert fans[0].last_commanded_pwm is None
    assert fans[1].last_commanded_pwm == 50


def test_parse_lease_status_not_held():
    data = {"lease_required": True, "held": False}
    lease = parse_lease_status(data)
    assert lease.lease_required is True
    assert lease.held is False
    assert lease.lease_id is None


def test_parse_lease_status_held():
    data = {
        "lease_required": True,
        "held": True,
        "lease_id": "lease-1",
        "ttl_seconds_remaining": 45,
        "owner_hint": "gui",
    }
    lease = parse_lease_status(data)
    assert lease.held is True
    assert lease.lease_id == "lease-1"
    assert lease.ttl_seconds_remaining == 45


def test_sensor_freshness_stale():
    s = SensorReading(id="test", age_ms=5000)
    assert s.freshness == Freshness.STALE


def test_sensor_freshness_invalid():
    s = SensorReading(id="test", age_ms=15000)
    assert s.freshness == Freshness.INVALID


def test_fan_freshness():
    f = FanReading(id="test", age_ms=500)
    assert f.freshness == Freshness.FRESH


# ---------------------------------------------------------------------------
# Write-response parsers (P2 gap: previously untested)
# ---------------------------------------------------------------------------


def test_parse_set_pwm():
    data = {"channel": 2, "pwm_percent": 65, "coalesced": True}
    result = parse_set_pwm(data)
    assert result.channel == 2
    assert result.pwm_percent == 65
    assert result.coalesced is True


def test_parse_set_pwm_all():
    data = {"pwm_percent": 50, "channels_affected": 6}
    result = parse_set_pwm_all(data)
    assert result.pwm_percent == 50
    assert result.channels_affected == 6


def test_parse_lease_result():
    data = {"lease_id": "abc-123", "owner_hint": "gui", "ttl_seconds": 60}
    result = parse_lease_result(data)
    assert result.lease_id == "abc-123"
    assert result.ttl_seconds == 60


def test_parse_lease_released():
    data = {"released": True}
    result = parse_lease_released(data)
    assert result.released is True


def test_parse_hwmon_set_pwm():
    data = {"header_id": "hwmon:nct6775:pwm1", "pwm_percent": 45, "raw_value": 115}
    result = parse_hwmon_set_pwm(data)
    assert result.header_id == "hwmon:nct6775:pwm1"
    assert result.raw_value == 115


def test_parse_sensor_history():
    data = {
        "api_version": 1,
        "entity_id": "hwmon:k10temp:Tctl",
        "points": [
            {"ts": 1711000000000, "v": 42.5},
            {"ts": 1711000001000, "v": 43.0},
        ],
    }
    result = parse_sensor_history(data)
    assert result.entity_id == "hwmon:k10temp:Tctl"
    assert len(result.points) == 2
    assert result.points[0].ts == 1711000000000
    assert result.points[0].v == 42.5
    assert result.points[1].v == 43.0


def test_parse_sensor_history_empty():
    data = {"entity_id": "x", "points": []}
    result = parse_sensor_history(data)
    assert result.entity_id == "x"
    assert result.points == []


def test_parse_calibration_result():
    data = {
        "api_version": 1,
        "fan_id": "openfan:ch00",
        "points": [
            {"pwm_percent": 0, "rpm": 0},
            {"pwm_percent": 50, "rpm": 600},
            {"pwm_percent": 100, "rpm": 1200},
        ],
        "start_pwm": 20,
        "stop_pwm": 10,
        "min_rpm": 600,
        "max_rpm": 1200,
    }
    result = parse_calibration_result(data)
    assert result.fan_id == "openfan:ch00"
    assert len(result.points) == 3
    assert result.points[1].pwm_percent == 50
    assert result.points[1].rpm == 600
    assert result.start_pwm == 20
    assert result.min_rpm == 600
    assert result.max_rpm == 1200


# ---------------------------------------------------------------------------
# Parser resilience — forward-compatibility and missing fields
# ---------------------------------------------------------------------------


class TestParserResilience:
    """Parsers must handle unknown fields and missing optional fields."""

    def test_sensors_with_extra_fields_ignored(self):
        """Unknown fields from future daemon versions are silently dropped."""
        data = {
            "sensors": [
                {
                    "id": "cpu_temp",
                    "kind": "CpuTemp",
                    "label": "CPU",
                    "value_c": 55.0,
                    "age_ms": 200,
                    "new_future_field": "should_be_ignored",
                    "another_unknown": 42,
                }
            ]
        }
        result = parse_sensors(data)
        assert len(result) == 1
        assert result[0].id == "cpu_temp"
        assert result[0].value_c == 55.0

    def test_fans_with_extra_fields_ignored(self):
        data = {
            "fans": [
                {
                    "id": "openfan:ch00",
                    "source": "openfan",
                    "rpm": 1200,
                    "age_ms": 100,
                    "future_metric": True,
                }
            ]
        }
        result = parse_fans(data)
        assert len(result) == 1
        assert result[0].rpm == 1200

    def test_sensors_missing_optional_fields_use_defaults(self):
        """Missing optional fields fall back to dataclass defaults."""
        data = {"sensors": [{"id": "s1", "kind": "CpuTemp", "label": "CPU"}]}
        result = parse_sensors(data)
        assert result[0].value_c == 0.0
        assert result[0].age_ms == 0

    def test_fans_empty_list(self):
        assert parse_fans({"fans": []}) == []

    def test_sensors_empty_list(self):
        assert parse_sensors({"sensors": []}) == []

    def test_capabilities_with_unknown_device(self):
        """Extra device entries don't crash parsing."""
        data = {
            "devices": {
                "openfan": {"present": True},
                "future_device": {"present": True, "model": "X"},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert caps.openfan.present is True

    def test_status_with_extra_subsystem_fields(self):
        """Subsystem entries with unknown fields are handled."""
        data = {
            "api_version": 1,
            "daemon_version": "0.2.0",
            "overall_status": "ok",
            "subsystems": [
                {
                    "name": "serial",
                    "status": "ok",
                    "age_ms": 100,
                    "reason": None,
                    "extra_detail": "should_be_ignored",
                }
            ],
            "counters": {},
        }
        result = parse_status(data)
        assert len(result.subsystems) == 1
        assert result.subsystems[0].name == "serial"
