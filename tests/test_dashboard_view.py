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
    OpenfanCapability,
    SensorReading,
)
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.dashboard_view import (
    build_capabilities_vm,
    cpu_values_for_display,
    safety_detail_text,
)


def _sensor(id="s", kind="cpu_temp", value_c=50.0, age_ms=100, rate=None):
    return SensorReading(id=id, kind=kind, value_c=value_c, age_ms=age_ms, rate_c_per_s=rate)


def _fan(id="f", source="openfan", rpm=1000, pwm=50, age_ms=100):
    return FanReading(id=id, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=age_ms)


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
        text = safety_detail_text("emergency", "Emergency", [], 0, cpu_reading_is_stale=False)
        assert "State: Emergency" in text
        assert "critical temperature" in text.lower()

    def test_hottest_cpu_surfaced(self):
        text = safety_detail_text(
            "normal", "Normal", [40.0, 92.3, 55.0], 0, cpu_reading_is_stale=False
        )
        assert "92.3" in text

    def test_override_count_pluralization(self):
        assert "1 manual override active" in safety_detail_text(
            "normal", "Normal", [], 1, cpu_reading_is_stale=False
        )
        assert "2 manual overrides active" in safety_detail_text(
            "normal", "Normal", [], 2, cpu_reading_is_stale=False
        )

    def test_no_cpu_no_override_omits_those_lines(self):
        text = safety_detail_text("normal", "Normal", [], 0, cpu_reading_is_stale=False)
        assert "Hottest CPU" not in text
        assert "manual override" not in text


# ─── DEC-269: the no-sensor copy must survive the stale trigger ──────────


def test_no_sensor_copy_does_not_claim_the_sensor_is_gone():
    """DEC-267 gave `no_sensor_fallback` a second trigger: a sensor that is still
    listed but has stopped updating. The old wording ("No CPU temperature sensor
    is reachable") then appeared directly above a "Hottest CPU sensor: 62.0°C"
    line drawn from the very list it denied — the GUI contradicting itself in one
    dialog."""
    text = safety_detail_text(
        "no_sensor_fallback", "No CPU sensor", [62.0], 0, cpu_reading_is_stale=True
    )

    assert "reachable" not in text, (
        "the copy still asserts the sensor is unreachable, which is false for the "
        "stale trigger — the reading is right there in the same dialog"
    )
    assert "stopped updating" in text, "the actual condition should be named"
    assert "Last known CPU sensor: 62.0" in text, (
        "a bare 'Hottest CPU sensor' beside 'no current reading' reads as a "
        "contradiction; the value is the stale one the daemon stopped trusting"
    )


def test_a_fresh_reading_is_never_hedged_whatever_the_state():
    """The relabel keys on the reading's age, not on the state. A live emergency
    with a current reading must show it plainly."""
    text = safety_detail_text("emergency", "Emergency", [95.0], 0, cpu_reading_is_stale=False)

    assert "Hottest CPU sensor: 95.0" in text
    assert "Last known" not in text


def test_a_stale_reading_is_hedged_even_during_an_emergency():
    """DEC-269 round 2: a latched emergency running on a stale reading reports
    `emergency`, not `no_sensor_fallback` — so keying the hedge on the state left
    it un-hedged in exactly the case where the value is guaranteed stale."""
    text = safety_detail_text("emergency", "Emergency", [95.0], 0, cpu_reading_is_stale=True)

    assert "Last known CPU sensor: 95.0" in text
    assert "Hottest CPU sensor" not in text


# ─── DEC-269: the printed value and its label are resolved together ──────


class TestCpuValuesForDisplay:
    """`max()` over every CPU sensor while the staleness flag required *all* of
    them to be stale meant the two could disagree: on a multi-CCD Ryzen a stale
    hottest die printed under the confident "Hottest CPU sensor" label. The
    resolver mirrors the daemon's `hottest_cpu_reading` instead — fresh wins
    outright, stale stands in only when nothing is fresh."""

    def test_a_stale_hotter_sensor_cannot_raise_a_fresh_reading(self):
        # The exact multi-CCD shape: die 2 is hotter but has stopped updating.
        values, stale = cpu_values_for_display([(61.0, True), (94.0, False)])

        assert stale is False, "a fresh reading is present, so nothing is hedged"
        assert max(values) == 61.0, (
            "the stale 94.0 must not be printed under the confident label — it is "
            "not what the daemon is acting on"
        )

    def test_all_stale_falls_back_to_the_hottest_stale_value(self):
        values, stale = cpu_values_for_display([(61.0, False), (94.0, False)])

        assert stale is True
        assert max(values) == 94.0

    def test_all_fresh_reports_the_hottest(self):
        values, stale = cpu_values_for_display([(61.0, True), (94.0, True)])

        assert stale is False
        assert max(values) == 94.0

    def test_no_sensors_is_not_stale(self):
        assert cpu_values_for_display([]) == ([], False)

    def test_accepts_a_generator(self):
        """The call site passes a generator expression. Scanning it twice without
        materialising would find it empty on the second pass and report every
        sensor stale."""
        values, stale = cpu_values_for_display((v, f) for v, f in [(61.0, True)])

        assert (values, stale) == ([61.0], False)
