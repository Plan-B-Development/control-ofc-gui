"""Tests for Intel LGA1700 platform support (DEC-110).

Mirrors the AM4/AM5 quirk-test structure. Covers the daemon-supplied
`cpu_vendor` field, GUI VendorQuirk platform / board_pattern scoping,
Intel PECI classification widening, asus_ec_sensors Intel allowlist
overrides, and ASRock Z690 Extreme label fallback.

All cited sources are verifiable: kernel asus_ec_sensors docs, kernel
nct6775 docs, lm-sensors upstream configs, Fred78290/nct6687d source.
"""

from __future__ import annotations

from control_ofc.api.models import HardwareDiagnosticsResult, parse_hardware_diagnostics
from control_ofc.ui.hwmon_guidance import lookup_vendor_quirks
from control_ofc.ui.hwmon_label_resolver import resolve_label_from_fallback
from control_ofc.ui.sensor_knowledge import classify_sensor, lookup_board_override


class TestDaemonCpuVendorRoundTrip:
    """The new daemon `cpu_vendor` field must round-trip through the GUI parser."""

    def test_intel_cpu_vendor_parsed(self):
        # The daemon emits cpu_vendor when /proc/cpuinfo vendor_id is
        # recognised. Older daemons omit the field entirely.
        payload = {"cpu_vendor": "Intel"}
        result = parse_hardware_diagnostics(payload)
        assert isinstance(result, HardwareDiagnosticsResult)
        assert result.cpu_vendor == "Intel"

    def test_amd_cpu_vendor_parsed(self):
        payload = {"cpu_vendor": "AMD"}
        result = parse_hardware_diagnostics(payload)
        assert result.cpu_vendor == "AMD"

    def test_missing_cpu_vendor_defaults_to_empty(self):
        # Older daemon (no DEC-110 support) emits no key — GUI must default
        # to empty string so platform-scoped quirks suppress rather than
        # fire indiscriminately.
        result = parse_hardware_diagnostics({})
        assert result.cpu_vendor == ""

    def test_null_cpu_vendor_defaults_to_empty(self):
        # Defensive: null on the wire is coerced to empty so platform
        # filtering treats it as "unknown" rather than crashing on the
        # ``.lower()`` call later.
        result = parse_hardware_diagnostics({"cpu_vendor": None})
        assert result.cpu_vendor == ""


class TestVendorQuirkPlatformScoping:
    """The `platform` field on VendorQuirk must scope quirks to one CPU vendor."""

    def test_intel_quirk_fires_with_intel_cpu_vendor(self):
        # The new ASUS Intel asus_ec_sensors quirk has platform="intel".
        # It must surface when the caller passes cpu_vendor="Intel".
        quirks = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.",
            "asus_ec_sensors",
            cpu_vendor="Intel",
            board_name="ROG STRIX Z790-E GAMING WIFI II",
        )
        intel_specific = [q for q in quirks if q.platform == "intel"]
        assert intel_specific, (
            "ASUS Intel asus_ec_sensors quirk must fire when cpu_vendor=Intel; "
            f"got platforms: {[q.platform for q in quirks]}"
        )

    def test_intel_quirk_suppressed_with_amd_cpu_vendor(self):
        # The same ASUS asus_ec_sensors lookup with cpu_vendor=AMD must
        # suppress the Intel-only quirk so it doesn't show up on AMD
        # AM5 ASUS boards (which use the same driver legitimately).
        quirks = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.",
            "asus_ec_sensors",
            cpu_vendor="AMD",
            board_name="ProArt X670E-CREATOR WIFI",
        )
        intel_specific = [q for q in quirks if q.platform == "intel"]
        assert not intel_specific, (
            "Intel-scoped quirk must NOT fire on AMD systems; "
            f"got: {[(q.summary, q.platform) for q in intel_specific]}"
        )

    def test_intel_quirk_suppressed_when_cpu_vendor_unknown(self):
        # Empty cpu_vendor (older daemon, hypervisor with unknown vendor_id)
        # must suppress platform-scoped quirks. Truthful direction:
        # "we don't know, so don't claim".
        quirks = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.",
            "asus_ec_sensors",
            cpu_vendor="",
            board_name="ROG STRIX Z790-E GAMING WIFI II",
        )
        intel_specific = [q for q in quirks if q.platform == "intel"]
        assert not intel_specific, "Intel-scoped quirk must NOT fire when cpu_vendor is unknown"

    def test_unscoped_quirk_fires_regardless_of_cpu_vendor(self):
        # Pre-DEC-110 quirks have platform=None and must continue to match
        # on vendor + chip alone — the new keyword args must be backward
        # compatible.
        # ASUS + nct679 has an existing ACPI conflict quirk (platform=None)
        # that must keep firing on both Intel and AMD.
        intel = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.", "nct6798", cpu_vendor="Intel", board_name=""
        )
        amd = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.", "nct6798", cpu_vendor="AMD", board_name=""
        )
        # Both calls return some unscoped (platform=None) quirks.
        intel_unscoped = [q for q in intel if q.platform is None]
        amd_unscoped = [q for q in amd if q.platform is None]
        assert intel_unscoped, "Unscoped quirks must fire on Intel"
        assert amd_unscoped, "Unscoped quirks must fire on AMD"

    def test_default_call_signature_remains_backward_compatible(self):
        # The pre-DEC-110 two-positional-arg form must still work — no
        # keyword args required. This is what callers in tests/test_v1_2_
        # diagnostics.py rely on.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8689")
        assert quirks, "Backward-compatible call must still return quirks"


class TestMsiNct6687PlatformDisambiguation:
    """The MSI NCT6687 chip ships on both Intel (Z690/Z790/Z890) and AMD
    (X870/X870E) platforms. Two distinct platform-scoped quirks must
    isolate the Intel auto-detect (Z690/Z790) and Intel msi_alt1 (Z890)
    cases without bleeding into AMD."""

    def test_msi_intel_z790_uses_plain_nct6687d(self):
        # MSI Z690/Z790 ship plain NCT6687D — auto-detected, no msi_alt1.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="MAG Z790 TOMAHAWK MAX WIFI",
        )
        intel_z690_z790 = [q for q in quirks if q.platform == "intel" and q.severity == "info"]
        assert intel_z690_z790, (
            "Expected an INFO quirk for MSI Intel Z690/Z790 + NCT6687D "
            "plain (auto-detect, no msi_alt1)"
        )
        # The quirk text must mention NCT6687D auto-detect to anchor the
        # distinction from Z890 NCT6687DR.
        flat = " ".join(q.summary + " ".join(q.details) for q in intel_z690_z790)
        assert "auto" in flat.lower() or "plain" in flat.lower(), (
            "Expected MSI Intel Z690/Z790 quirk to call out auto-detect / plain NCT6687D"
        )

    def test_msi_amd_x870e_does_not_match_intel_z790_quirk(self):
        # Same chip, different platform: MSI AMD X870E must NOT match the
        # Intel-scoped Z690/Z790 quirk.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="AMD",
            board_name="MEG X870E ACE",
        )
        intel_only = [q for q in quirks if q.platform == "intel"]
        assert not intel_only, (
            "Intel-scoped MSI quirks must NOT fire on AMD MSI boards; "
            f"got: {[q.summary for q in intel_only]}"
        )


class TestAsusIntelLga1700Quirks:
    """ASUS ec_sensors / NCT6798D coverage for LGA1700 boards."""

    def test_asus_ec_sensors_intel_quirk_mentions_kernel_allowlist(self):
        # The Intel asus_ec_sensors quirk must cite the kernel-documented
        # allowlist so users on those exact boards have confidence they
        # are on a supported path.
        quirks = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.",
            "asus_ec_sensors",
            cpu_vendor="Intel",
            board_name="ROG STRIX Z790-E GAMING WIFI II",
        )
        flat = " ".join(q.summary + " ".join(q.details) for q in quirks)
        for board in [
            "MAXIMUS Z690 FORMULA",
            "STRIX Z690-A GAMING WIFI D4",
            "STRIX Z690-E GAMING WIFI",
            "STRIX Z790-E GAMING WIFI II",
            "STRIX Z790-H GAMING WIFI",
            "STRIX Z790-I GAMING WIFI",
        ]:
            assert board in flat, (
                f"Intel asus_ec_sensors quirk must cite kernel allowlist "
                f"board {board!r}; missing from quirk text"
            )

    def test_asus_intel_nct6798_marked_as_supported(self):
        # ASUS LGA1700 boards with NCT6798D: surface an INFO quirk so users
        # see "this is supported, no out-of-tree driver needed".
        quirks = lookup_vendor_quirks(
            "ASUSTeK COMPUTER INC.",
            "nct6798",
            cpu_vendor="Intel",
            board_name="ROG STRIX Z790-E GAMING WIFI II",
        )
        intel_info = [q for q in quirks if q.platform == "intel" and q.severity == "info"]
        assert intel_info, (
            "Expected an INFO ASUS Intel NCT6798D guidance entry; "
            f"got: {[(q.severity, q.platform) for q in quirks]}"
        )
        # Must call out that the DEC-105 brick warning does NOT apply.
        flat = " ".join(q.summary + " ".join(q.details) for q in intel_info)
        assert "dec-105" in flat.lower() or "0xd428" in flat or "0xd450" in flat, (
            "Expected ASUS Intel NCT6798D quirk to reference the DEC-105 "
            "chip-ID distinction so users know the brick risk does not apply"
        )


class TestGigabyteIntelLga1700Quirks:
    """Gigabyte AORUS Z690/Z790 dual-chip coverage (IT8689E + IT87952E)."""

    def test_gigabyte_intel_z790_aorus_marked_high_severity(self):
        # The dual-chip Z790 AORUS quirk must surface HIGH severity (BIOS
        # interference is real on these boards) and reference the
        # dual-chip mmio=on remediation.
        quirks = lookup_vendor_quirks(
            "Gigabyte Technology Co., Ltd.",
            "it8689",
            cpu_vendor="Intel",
            board_name="Z790 AORUS MASTER",
        )
        intel_high = [q for q in quirks if q.platform == "intel" and q.severity == "high"]
        assert intel_high, "Expected HIGH-severity Gigabyte Intel IT8689E quirk"
        flat = " ".join(q.summary + " ".join(q.details) for q in intel_high)
        assert "mmio=on" in flat, (
            "Expected Gigabyte Intel IT8689E quirk to cite the mmio=on dual-chip remediation"
        )

    def test_gigabyte_intel_z890_aorus_uses_it8696(self):
        # Z890 AORUS uses the newer IT8696E + IT87952E topology, same as
        # AMD X870E AORUS MASTER.
        quirks = lookup_vendor_quirks(
            "Gigabyte Technology Co., Ltd.",
            "it8696",
            cpu_vendor="Intel",
            board_name="Z890 AORUS MASTER",
        )
        intel_quirks = [q for q in quirks if q.platform == "intel"]
        assert intel_quirks, "Expected at least one Gigabyte Intel IT8696E quirk"
        flat = " ".join(q.summary + " ".join(q.details) for q in intel_quirks)
        assert "z890" in flat.lower() or "lga1851" in flat.lower(), (
            "Expected Gigabyte Intel IT8696E quirk to mention Z890 / LGA1851"
        )


class TestAsrockIntelLga1700Quirks:
    """ASRock Z690/Z790 with NCT6798D — mainline kernel coverage."""

    def test_asrock_intel_nct6798_marked_as_supported(self):
        quirks = lookup_vendor_quirks(
            "ASRock",
            "nct6798",
            cpu_vendor="Intel",
            board_name="Z690 Steel Legend",
        )
        intel_info = [q for q in quirks if q.platform == "intel" and q.severity == "info"]
        assert intel_info, "Expected INFO ASRock Intel NCT6798D guidance entry"


class TestIntelSensorClassification:
    """Intel CPU sensors (coretemp Package, NCT PECI) must classify correctly."""

    def test_coretemp_package_id_classifies_as_cpu_die(self):
        # The existing coretemp handler already maps "Package id 0" to
        # cpu_die high-confidence. Locks the invariant.
        c = classify_sensor("coretemp", "Package id 0")
        assert c.source_class == "cpu_die"
        assert c.confidence == "high"

    def test_coretemp_core_n_classifies_as_cpu_die(self):
        c = classify_sensor("coretemp", "Core 3")
        assert c.source_class == "cpu_die"
        assert c.confidence == "high"

    def test_nct6798_peci_agent_0_classifies_as_cpu_peci(self):
        # DEC-110 widening: Intel PECI on nct6775-family chips must
        # classify even without "CPU" in the label. The kernel driver
        # commonly exposes Intel PECI as "PECI Agent 0" or "PECI 0".
        c = classify_sensor("nct6798", "PECI Agent 0")
        assert c.source_class == "cpu_peci", (
            f"Intel PECI label must classify as cpu_peci; got {c.source_class}"
        )
        assert c.confidence == "medium_high"
        # The display text must tag it as Intel PECI so the tooltip is
        # truthful about which CPU vendor PECI is reporting.
        assert "intel" in c.display_description.lower(), (
            f"Intel PECI tooltip should mention Intel; got: {c.display_description}"
        )

    def test_nct6798_peci_0_classifies_as_cpu_peci(self):
        # Some firmwares omit the "Agent" word — "PECI 0" must still match.
        c = classify_sensor("nct6798", "PECI 0")
        assert c.source_class == "cpu_peci"

    def test_nct6798_amd_tsi_still_classifies_as_amd_tsi(self):
        # The Intel PECI widening must NOT regress AMD TSI handling on
        # the same chip family.
        c = classify_sensor("nct6798", "AMD TSI Addr 98h")
        assert c.source_class == "amd_tsi"


class TestIntelBoardSensorOverrides:
    """Intel asus_ec_sensors allowlist BoardSensorOverride entries."""

    def test_maximus_z690_formula_vrm_override(self):
        ov = lookup_board_override(
            board_vendor="ASUSTeK COMPUTER INC.",
            board_model="ROG MAXIMUS Z690 FORMULA",
            label="VRM",
        )
        assert ov is not None, "MAXIMUS Z690 FORMULA VRM override must be present"
        assert ov.source_class == "vrm"
        assert ov.confidence == "high"

    def test_strix_z790_e_gaming_wifi_ii_vrm_override(self):
        ov = lookup_board_override(
            board_vendor="ASUSTeK COMPUTER INC.",
            board_model="ROG STRIX Z790-E GAMING WIFI II",
            label="VRM",
        )
        assert ov is not None
        assert ov.source_class == "vrm"

    def test_strix_z690_a_gaming_wifi_t_sensor_override(self):
        ov = lookup_board_override(
            board_vendor="ASUSTeK COMPUTER INC.",
            board_model="ROG STRIX Z690-A GAMING WIFI D4",
            label="T_Sensor",
        )
        assert ov is not None
        assert ov.source_class == "external_probe"

    def test_unknown_intel_board_returns_none(self):
        # Z890 boards not yet in kernel allowlist must return None.
        assert (
            lookup_board_override(
                board_vendor="ASUSTeK COMPUTER INC.",
                board_model="ROG STRIX Z890-E GAMING WIFI",
                label="VRM",
            )
            is None
        )


class TestIntelLabelFallback:
    """ASRock Z690 Extreme — only LGA1700-era board with upstream lm-sensors config."""

    def test_asrock_z690_extreme_pwm2_is_cpu_fan1(self):
        # Source: configs/ASRock/Z690_Extreme.conf — fan2 = "CPU fan1".
        # The libsensors convention is fanN and pwmN map to the same
        # physical fan, so pwm2 must resolve to "CPU fan1".
        label = resolve_label_from_fallback(
            vendor="ASRock",
            board_name="Z690 Extreme",
            chip_name="nct6798",
            sensor_name="pwm2",
        )
        assert label == "CPU fan1"

    def test_asrock_z690_extreme_pwm4_is_chassis_fan1(self):
        # Source: same config — fan4 = "Chassis fan1".
        label = resolve_label_from_fallback(
            vendor="ASRock",
            board_name="Z690 Extreme",
            chip_name="nct6798",
            sensor_name="pwm4",
        )
        assert label == "Chassis fan1"

    def test_asrock_z690_extreme_wrong_chip_returns_none(self):
        # Same board with the wrong chip (e.g. user has a sibling chip
        # that's not the primary) must return None.
        assert (
            resolve_label_from_fallback(
                vendor="ASRock",
                board_name="Z690 Extreme",
                chip_name="nct6687",  # wrong chip family
                sensor_name="pwm1",
            )
            is None
        )

    def test_unknown_intel_board_returns_none(self):
        # Boards without upstream lm-sensors configs must not fabricate
        # labels — they fall through to the raw pwmN identifier.
        assert (
            resolve_label_from_fallback(
                vendor="ASUSTeK COMPUTER INC.",
                board_name="ROG STRIX Z790-E GAMING WIFI II",
                chip_name="nct6798",
                sensor_name="pwm1",
            )
            is None
        )
