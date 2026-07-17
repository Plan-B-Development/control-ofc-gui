"""Unit tests for the Qt-free Dashboard view-model (DEC-219, Phase 7.2).

Direct tests of the pure compute carved out of DashboardPage — no widgets, no
Qt. The widget-level rendering is covered by test_summary_cards / test_dashboard;
these pin the logic itself so a future card refactor can't silently change it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    FanReading,
    HwmonCapability,
    HwmonHeader,
    IntelGpuCapability,
    NvidiaGpuCapability,
    OpenfanCapability,
    SensorReading,
)
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.dashboard_view import (
    absent_member_ids,
    build_capabilities_vm,
    build_fans_card_vm,
    build_summary_card_vm,
    curated_chart_keys,
    fan_tooltip,
    resolve_card_sensor,
    safety_detail_text,
    trend_from_rate,
)
from control_ofc.services.session_stats import SessionStatsTracker


def _sensor(id="s", kind="cpu_temp", value_c=50.0, age_ms=100, rate=None):
    return SensorReading(id=id, kind=kind, value_c=value_c, age_ms=age_ms, rate_c_per_s=rate)


def _fan(id="f", source="openfan", rpm=1000, pwm=50, age_ms=100):
    return FanReading(id=id, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=age_ms)


# ─── trend_from_rate ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(None, ""), (0.06, "up"), (-0.06, "down"), (0.0, "flat"), (0.04, "flat")],
)
def test_trend_from_rate(rate, expected):
    assert trend_from_rate(rate) == expected


# ─── resolve_card_sensor / curated_chart_keys ────────────────────────────


class TestSensorResolution:
    def test_binding_takes_priority_over_kind(self):
        s1, s2 = _sensor(id="a", kind="cpu_temp"), _sensor(id="b", kind="cpu_temp")
        sensors = [s1, s2]
        got = resolve_card_sensor(
            "cpu_temp", ("cpu_temp",), sensors, {s.id: s for s in sensors}, {"cpu_temp": "b"}
        )
        assert got is s2

    def test_kind_fallback_when_no_binding(self):
        s = _sensor(id="a", kind="cpu_temp")
        assert resolve_card_sensor("cpu_temp", ("CpuTemp", "cpu_temp"), [s], {"a": s}, {}) is s

    def test_absent_binding_falls_through_to_kind(self):
        s = _sensor(id="a", kind="cpu_temp")
        got = resolve_card_sensor("cpu_temp", ("cpu_temp",), [s], {"a": s}, {"cpu_temp": "gone"})
        assert got is s

    def test_no_match_returns_none(self):
        assert resolve_card_sensor("cpu_temp", ("cpu_temp",), [], {}, {}) is None

    def test_curated_keys_kind_aware_and_drops_missing_slots(self):
        sensors = [_sensor(id="cpu0", kind="cpu_temp"), _sensor(id="gpu0", kind="GpuTemp")]
        # No mobo/case sensor present → that slot is simply dropped.
        assert curated_chart_keys(sensors, {}) == {"sensor:cpu0", "sensor:gpu0"}


# ─── absent_member_ids ───────────────────────────────────────────────────


class TestAbsentMembers:
    def test_none_profile_is_empty(self):
        assert absent_member_ids(None, {"f1"}) == set()

    def test_expected_minus_present(self):
        profile = SimpleNamespace(
            controls=[
                SimpleNamespace(
                    members=[
                        SimpleNamespace(member_id="f1"),
                        SimpleNamespace(member_id="f2"),
                    ]
                )
            ]
        )
        assert absent_member_ids(profile, {"f1"}) == {"f2"}

    def test_present_idle_member_not_flagged(self):
        profile = SimpleNamespace(
            controls=[SimpleNamespace(members=[SimpleNamespace(member_id="f1")])]
        )
        assert absent_member_ids(profile, {"f1"}) == set()


# ─── fan_tooltip ─────────────────────────────────────────────────────────


class TestFanTooltip:
    def test_non_hwmon_is_id_only(self):
        assert fan_tooltip(_fan(id="ch1", source="openfan"), []) == "ID: ch1"

    def test_hwmon_read_only_is_annotated(self):
        header = HwmonHeader(id="hwmon:x:1", chip_name="nct6799", pwm_mode=1, is_writable=False)
        tip = fan_tooltip(_fan(id="hwmon:x:1", source="hwmon"), [header])
        assert "Chip: nct6799" in tip
        assert "Mode: PWM" in tip
        assert "Status: read-only" in tip

    def test_hwmon_without_matching_header_is_id_only(self):
        assert fan_tooltip(_fan(id="hwmon:x:1", source="hwmon"), []) == "ID: hwmon:x:1"

    def test_hwmon_unknown_chip_omits_driver_line(self):
        # A chip with no guidance entry still shows Chip + mode, but no Driver line.
        header = HwmonHeader(id="hwmon:x:1", chip_name="totallyunknownchip", pwm_mode=0)
        tip = fan_tooltip(_fan(id="hwmon:x:1", source="hwmon"), [header])
        assert "Chip: totallyunknownchip" in tip
        assert "Driver:" not in tip
        assert "Mode: DC" in tip

    def test_hwmon_unexposed_pwm_mode_omits_mode_line(self):
        # pwm_mode=None (not exposed by the chip) → no Mode line.
        header = HwmonHeader(id="hwmon:x:1", chip_name="nct6799", pwm_mode=None)
        tip = fan_tooltip(_fan(id="hwmon:x:1", source="hwmon"), [header])
        assert "Chip: nct6799" in tip
        assert "Mode:" not in tip


# ─── build_summary_card_vm ───────────────────────────────────────────────


class TestSummaryCardVM:
    def test_no_match_returns_none(self):
        vm = build_summary_card_vm("cpu_temp", ("cpu_temp",), [], {}, {}, SessionStatsTracker())
        assert vm is None

    def test_fresh_value_trend_and_range(self):
        s = _sensor(id="cpu0", kind="cpu_temp", value_c=55.0, age_ms=100, rate=0.5)
        st = SessionStatsTracker()
        st.update_batch([("cpu0", 40.0), ("cpu0", 60.0)])
        vm = build_summary_card_vm("cpu_temp", ("cpu_temp",), [s], {"cpu0": s}, {}, st)
        assert vm.value_text == "55.0°C"
        assert vm.trend == "up"
        assert vm.status_class == ""
        assert (vm.range_min, vm.range_max) == (40.0, 60.0)

    def test_warn_then_crit_thresholds(self):
        s = _sensor(id="cpu0", kind="cpu_temp", value_c=80.0, age_ms=100)
        vm = build_summary_card_vm(
            "cpu_temp", ("cpu_temp",), [s], {"cpu0": s}, {}, SessionStatsTracker(), warn=75, crit=85
        )
        assert vm.status_class == "WarningChip"
        s2 = _sensor(id="cpu0", kind="cpu_temp", value_c=90.0, age_ms=100)
        vm2 = build_summary_card_vm(
            "cpu_temp",
            ("cpu_temp",),
            [s2],
            {"cpu0": s2},
            {},
            SessionStatsTracker(),
            warn=75,
            crit=85,
        )
        assert vm2.status_class == "CriticalChip"

    def test_stale_reading_marks_and_drops_trend(self):
        s = _sensor(id="cpu0", kind="cpu_temp", value_c=50.0, age_ms=5000, rate=0.5)
        vm = build_summary_card_vm(
            "cpu_temp", ("cpu_temp",), [s], {"cpu0": s}, {}, SessionStatsTracker()
        )
        assert "⏱" in vm.value_text
        assert vm.status_class == "WarningChip"
        assert vm.trend == ""  # a stale rate is not trusted

    def test_invalid_reading_is_critical(self):
        s = _sensor(id="cpu0", kind="cpu_temp", value_c=50.0, age_ms=15000)
        vm = build_summary_card_vm(
            "cpu_temp", ("cpu_temp",), [s], {"cpu0": s}, {}, SessionStatsTracker()
        )
        assert "⚠" in vm.value_text
        assert vm.status_class == "CriticalChip"

    def test_absent_binding_blanks_without_reclassifying(self):
        # A bound-but-missing sensor blanks the face but must NOT touch the status
        # class (status_class is None → the page leaves it as-is).
        vm = build_summary_card_vm(
            "cpu_temp", ("cpu_temp",), [], {}, {"cpu_temp": "gone"}, SessionStatsTracker()
        )
        assert vm.value_text == "—"
        assert vm.status_class is None
        assert (vm.range_min, vm.range_max) == (None, None)


# ─── build_fans_card_vm ──────────────────────────────────────────────────


class TestFansCardVM:
    def test_all_online_with_averages(self):
        vm = build_fans_card_vm([_fan(id="a", pwm=50, rpm=1200), _fan(id="b", pwm=40, rpm=1000)])
        assert vm.value_text == "2/2"
        assert vm.status_class == ""
        assert "avg 45% PWM" in vm.detail_text
        assert "1100 rpm" in vm.detail_text

    def test_shortfall_warns(self):
        vm = build_fans_card_vm([_fan(id="a", age_ms=100), _fan(id="b", age_ms=5000)])
        assert vm.value_text == "1/2"
        assert vm.status_class == "WarningChip"

    def test_empty(self):
        vm = build_fans_card_vm([])
        assert vm.value_text == "0/0"
        assert vm.detail_text == ""


# ─── build_capabilities_vm ───────────────────────────────────────────────


class TestCapabilitiesVM:
    def test_absent_devices_and_hwmon_info_banner(self):
        vm = build_capabilities_vm(Capabilities())
        assert vm.openfan.text == "OpenFan: not detected"
        assert vm.openfan.css_class == "PageSubtitle"
        assert vm.hwmon.text == "hwmon: not detected"
        assert vm.gpu_title is None
        assert vm.hwmon_banner is not None and vm.hwmon_banner.kind == "info"

    def test_present_writable_devices_no_banner(self):
        caps = Capabilities(
            openfan=OpenfanCapability(present=True, channels=4),
            hwmon=HwmonCapability(present=True, pwm_header_count=6, write_support=True),
        )
        vm = build_capabilities_vm(caps)
        assert vm.openfan.text == "OpenFan: detected (4 ch)"
        assert vm.openfan.css_class == "SuccessChip"
        assert vm.hwmon.text == "hwmon: detected (6 headers)"
        assert vm.hwmon_banner is None

    def test_hwmon_present_readonly_warns(self):
        caps = Capabilities(
            hwmon=HwmonCapability(present=True, pwm_header_count=3, write_support=False)
        )
        vm = build_capabilities_vm(caps)
        assert vm.hwmon_banner is not None and vm.hwmon_banner.kind == "warning"

    def test_gpu_priority_amd_over_intel_over_nvidia(self):
        caps = Capabilities(
            amd_gpu=AmdGpuCapability(present=True, display_label="9070XT"),
            intel_gpu=IntelGpuCapability(present=True, display_label="Arc"),
            nvidia_gpu=NvidiaGpuCapability(present=True, display_label="RTX"),
        )
        assert build_capabilities_vm(caps).gpu_title == "9070XT Temp"

    def test_gpu_intel_when_no_amd(self):
        caps = Capabilities(intel_gpu=IntelGpuCapability(present=True, display_label="Arc"))
        assert build_capabilities_vm(caps).gpu_title == "Arc Temp"

    def test_gpu_nvidia_when_no_amd_or_intel(self):
        caps = Capabilities(nvidia_gpu=NvidiaGpuCapability(present=True, display_label="RTX"))
        assert build_capabilities_vm(caps).gpu_title == "RTX Temp"

    def test_api_skew_message_when_mismatched(self):
        vm = build_capabilities_vm(Capabilities(api_version=EXPECTED_API_VERSION + 1))
        assert vm.api_skew_message is not None
        assert f"v{EXPECTED_API_VERSION + 1}" in vm.api_skew_message

    def test_no_skew_when_matching(self):
        vm = build_capabilities_vm(Capabilities(api_version=EXPECTED_API_VERSION))
        assert vm.api_skew_message is None


# ─── safety_detail_text ──────────────────────────────────────────────────


class TestSafetyDetailText:
    def test_state_and_reason(self):
        text = safety_detail_text("emergency", "Emergency", [], 0)
        assert "State: Emergency" in text
        assert "critical temperature" in text.lower()

    def test_hottest_cpu_surfaced(self):
        text = safety_detail_text("normal", "Normal", [40.0, 92.3, 55.0], 0)
        assert "92.3" in text

    def test_override_count_pluralization(self):
        assert "1 manual override active" in safety_detail_text("normal", "Normal", [], 1)
        assert "2 manual overrides active" in safety_detail_text("normal", "Normal", [], 2)

    def test_no_cpu_no_override_omits_those_lines(self):
        text = safety_detail_text("normal", "Normal", [], 0)
        assert "Hottest CPU" not in text
        assert "manual override" not in text
