"""Unit tests for the Qt-free Dashboard view-model (DEC-219, Phase 7.2).

Direct tests of the pure compute carved out of DashboardPage — no widgets, no Qt.
DEC-222 removed the summary/fans card builders and moved chart-series curation to
``series_selection.default_series_keys`` (covered in test_series_selection), so
what remains here is the fan tooltip, the capability chips/banners and the
thermal-safety detail text.
"""

from __future__ import annotations

from control_ofc.api.models import (
    Capabilities,
    FanReading,
    HwmonCapability,
    HwmonHeader,
    OpenfanCapability,
    SensorReading,
)
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.dashboard_view import (
    build_capabilities_vm,
    fan_tooltip,
    safety_detail_text,
)


def _sensor(id="s", kind="cpu_temp", value_c=50.0, age_ms=100, rate=None):
    return SensorReading(id=id, kind=kind, value_c=value_c, age_ms=age_ms, rate_c_per_s=rate)


def _fan(id="f", source="openfan", rpm=1000, pwm=50, age_ms=100):
    return FanReading(id=id, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=age_ms)


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


# ─── build_capabilities_vm ───────────────────────────────────────────────


class TestCapabilitiesVM:
    def test_absent_devices_and_hwmon_info_banner(self):
        vm = build_capabilities_vm(Capabilities())
        assert vm.openfan.text == "OpenFan: not detected"
        assert vm.openfan.css_class == "PageSubtitle"
        assert vm.hwmon.text == "hwmon: not detected"
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
