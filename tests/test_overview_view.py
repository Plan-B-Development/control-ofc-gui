"""DEC-209: Qt-free Overview view-model builders (headless, no QApplication).

These pin the builders as line-for-line ports of the current Diagnostics
Overview/Fans/Sensors rendering so the new Overview page can't silently diverge.
"""

from __future__ import annotations

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    DaemonStatus,
    FanReading,
    FeatureFlags,
    HwmonCapability,
    HwmonHeader,
    SensorReading,
    SensorThresholds,
)
from control_ofc.services import overview_view as ov


def _sensor(sid: str = "hwmon:k10temp:0000:00:18.3:Tctl", **kw) -> SensorReading:
    return SensorReading(
        id=sid,
        kind=kw.get("kind", "cpu_temp"),
        label=kw.get("label", sid.split(":")[-1]),
        value_c=kw.get("value_c", 45.0),
        chip_name=kw.get("chip_name", "k10temp"),
        source=kw.get("source", "hwmon"),
        age_ms=kw.get("age_ms", 500),
        rate_c_per_s=kw.get("rate_c_per_s"),
        session_min_c=kw.get("session_min_c"),
        session_max_c=kw.get("session_max_c"),
        temp_type=kw.get("temp_type"),
        thresholds=kw.get("thresholds"),
    )


# ─── fan_control_method (decomposed-arg parity) ──


def test_fan_control_method_openfan():
    f = FanReading(id="openfan:ch00", source="openfan", rpm=1200)
    assert ov.fan_control_method(f, [], None) == "OpenFan USB"


def test_fan_control_method_amd_gpu():
    caps = Capabilities(
        amd_gpu=AmdGpuCapability(
            present=True, display_label="9070XT", fan_control_method="pmfw_curve"
        )
    )
    f = FanReading(id="amd_gpu:x", source="amd_gpu", rpm=0)
    assert ov.fan_control_method(f, [], caps) == "PMFW curve"
    assert ov.fan_control_method(f, [], None) == "unknown"  # no caps → unknown


def test_fan_control_method_intel_nvidia_readonly():
    assert (
        ov.fan_control_method(FanReading(id="i", source="intel_gpu", rpm=0), [], None)
        == "read-only"
    )
    assert (
        ov.fan_control_method(FanReading(id="n", source="nvidia_gpu", rpm=0), [], None)
        == "read-only"
    )


def test_fan_control_method_hwmon_writable_vs_readonly():
    f = FanReading(id="hwmon:nct6798:fan1", source="hwmon", rpm=1000)
    writable = [HwmonHeader(id="hwmon:nct6798:fan1", label="CPU", is_writable=True)]
    readonly = [HwmonHeader(id="hwmon:nct6798:fan1", label="CPU", is_writable=False)]
    assert ov.fan_control_method(f, writable, None) == "hwmon PWM"
    assert ov.fan_control_method(f, readonly, None) == "read-only"
    assert ov.fan_control_method(f, [], None) == "unknown"


# ─── build_fan_rows ──


def test_build_fan_rows_pwm_only_synthesis_and_freshness():
    fans = [FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=500)]
    headers = [
        HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)
    ]  # no fan → pwm-only
    rows = ov.build_fan_rows(fans, headers, None, display_name=lambda x: x)
    assert len(rows) == 2
    assert rows[0].control_method == "OpenFan USB"
    assert rows[0].freshness_label == "fresh" and rows[0].freshness_state == "ok"
    assert not rows[0].is_pwm_only
    assert rows[1].is_pwm_only
    assert rows[1].source == "hwmon (PWM-only)"
    assert rows[1].control_method == "hwmon PWM — no RPM"
    assert rows[1].freshness_label == "N/A" and rows[1].freshness_state == "neutral"
    assert rows[1].rpm_text == "—" and rows[1].pwm_text == "—"


def test_build_fan_rows_pwm_only_tooltip_hides_a_synthesised_label():
    """DEC-229: don't pass off the daemon's invented `pwmN` as sysfs truth.

    The row *name* already resolves (DEC-227), so a "Label: pwm1" line beneath
    it contradicts the name directly above and claims a hardware source the
    value never had. A real label is still reported.
    """
    real = HwmonHeader(id="hwmon:it8696:pwm1", label="CPU_FAN", pwm_index=1, is_writable=True)
    fake = HwmonHeader(id="hwmon:it8696:pwm2", label="pwm2", pwm_index=2, is_writable=True)
    rows = ov.build_fan_rows([], [real, fake], None, display_name=lambda x: "Resolved")
    assert "Label: CPU_FAN" in rows[0].row_tooltip
    assert "Label:" not in rows[1].row_tooltip
    # The rest of the tooltip is untouched — only the label line is suppressed.
    assert "ID: hwmon:it8696:pwm2" in rows[1].row_tooltip


def test_build_fan_rows_freshness_state_map():
    fans = [
        FanReading(id="a", source="openfan", rpm=1, age_ms=500),
        FanReading(id="b", source="openfan", rpm=1, age_ms=5000),
        FanReading(id="c", source="openfan", rpm=1, age_ms=20000),
    ]
    rows = {r.name: r.freshness_state for r in ov.build_fan_rows(fans, [], None, lambda x: x)}
    assert rows == {"a": "ok", "b": "warn", "c": "crit"}


# ─── build_sensor_rows ──


def test_build_sensor_rows_moves_source_into_tooltip():
    r = ov.build_sensor_rows([_sensor(source="amd_gpu")], overrides={}, board_vendor="")[0]
    assert r.chip == "k10temp"
    assert "Source: amd_gpu" in r.tooltip  # the daemon-subsystem column is now hover-only
    assert r.confidence_state in ("ok", "info", "neutral", "warn")


def test_build_sensor_rows_alarm_suffix():
    s = _sensor(value_c=95.0, thresholds=SensorThresholds(crit_c=90.0))
    r = ov.build_sensor_rows([s], overrides={}, board_vendor="")[0]
    assert "⚠ ALARM" in r.value_text
    assert r.is_alarm


def test_build_sensor_rows_prefix_matches_flags():
    r = ov.build_sensor_rows([_sensor()], overrides={}, board_vendor="")[0]
    if r.is_quirky:
        assert r.label.startswith("⚠ ")
    elif r.is_low_confidence:
        assert r.label.startswith("? ")
    else:
        assert not r.label.startswith(("⚠ ", "? "))


def test_build_sensor_rows_low_confidence_gets_question_prefix():
    # B5: an unknown chip classifies as low-confidence → "? " prefix and
    # confidence_state "warn". The existing prefix test uses a high-confidence
    # k10temp sensor, so this arm (and the low→warn map entry) was dead.
    r = ov.build_sensor_rows(
        [_sensor(sid="x:temp1", chip_name="mystery", label="temp1")],
        overrides={},
        board_vendor="",
    )[0]
    assert r.is_low_confidence and not r.is_quirky
    assert r.label.startswith("? ")
    assert r.confidence_state == "warn"


def test_build_sensor_rows_quirky_gets_warning_prefix():
    # B5: the ASUS + nct6776 + CPUTIN quirk classifies as bogus → "⚠ " prefix.
    r = ov.build_sensor_rows(
        [_sensor(sid="x:cputin", chip_name="nct6776", label="CPUTIN")],
        overrides={},
        board_vendor="ASUS",
    )[0]
    assert r.is_quirky
    assert r.label.startswith("⚠ ")


# ─── build_sensor_summary ──


def test_build_sensor_summary_counts():
    sensors = [_sensor(kind="cpu_temp"), _sensor(sid="x:edge", kind="gpu_temp", label="edge")]
    line = ov.build_sensor_summary(sensors, hidden_count=1, unavailable_count=2, board_vendor="")
    assert line.startswith("Sensors: 2 total")
    assert "1 CPU" in line and "1 GPU" in line
    assert "2 unavailable" in line and "1 hidden" in line


def test_build_sensor_summary_empty_and_unavailable_only():
    assert (
        ov.build_sensor_summary([], hidden_count=0, unavailable_count=0, board_vendor="")
        == "Sensors: —"
    )
    line = ov.build_sensor_summary([], hidden_count=0, unavailable_count=3, board_vendor="")
    assert "3 unavailable" in line


# ─── daemon health + device discovery ──


def test_build_daemon_health():
    assert ov.build_daemon_health_vm(None, None).version_text == "Daemon: —"
    vm = ov.build_daemon_health_vm(None, DaemonStatus(overall_status="ok", uptime_seconds=3661))
    assert vm.status_text == "Status: ok" and vm.status_state == "ok"
    assert vm.uptime_text.startswith("Uptime: 1h")


def test_build_daemon_health_severity_arms():
    # B5: _STATUS_STATE maps daemon overall_status → chip state. The existing
    # test only exercises the "ok" arm; pin the non-ok arms so a crit↔warn swap
    # in the map cannot slip through.
    assert (
        ov.build_daemon_health_vm(
            None, DaemonStatus(overall_status="error", uptime_seconds=0)
        ).status_state
        == "crit"
    )
    assert (
        ov.build_daemon_health_vm(
            None, DaemonStatus(overall_status="critical", uptime_seconds=0)
        ).status_state
        == "crit"
    )
    assert (
        ov.build_daemon_health_vm(
            None, DaemonStatus(overall_status="degraded", uptime_seconds=0)
        ).status_state
        == "warn"
    )


def test_build_device_discovery_hwmon_all_readonly_warn():
    caps = Capabilities(
        hwmon=HwmonCapability(present=True, pwm_header_count=3, write_support=True),
        features=FeatureFlags(hwmon_write_supported=True),
    )
    vm = ov.build_device_discovery_vm(caps, writable_headers=0)
    assert vm.hwmon_warn is True
    assert "ALL read-only" in vm.hwmon
    assert "0 writable headers" in vm.features


def test_build_device_discovery_none():
    vm = ov.build_device_discovery_vm(None, None)
    assert vm.openfan == "OpenFan: —"
    assert vm.hwmon_warn is False
