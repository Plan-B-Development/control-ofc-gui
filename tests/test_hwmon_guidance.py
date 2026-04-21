"""Tests for the chip-family knowledge base and driver guidance module."""

from control_ofc.ui.hwmon_guidance import (
    detect_module_conflicts,
    format_driver_status,
    lookup_chip_guidance,
    lookup_vendor_quirks,
    verification_guidance,
)


class TestLookupChipGuidance:
    def test_nct6687_matches_specific_entry(self):
        g = lookup_chip_guidance("nct6687")
        assert g is not None
        assert g.driver_name == "nct6687"
        assert g.in_mainline is False

    def test_nct6798_matches_nct679x(self):
        g = lookup_chip_guidance("nct6798")
        assert g is not None
        assert g.chip_prefix == "nct679"
        assert g.driver_name == "nct6775"
        assert g.in_mainline is True

    def test_it8688_matches_specific_entry(self):
        g = lookup_chip_guidance("it8688")
        assert g is not None
        assert g.chip_prefix == "it8688"
        assert g.in_mainline is False
        assert "AUR" in g.driver_package

    def test_it8689_matches_specific_entry(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        assert g.chip_prefix == "it8689"

    def test_it8696_matches_specific_entry(self):
        g = lookup_chip_guidance("it8696")
        assert g is not None
        assert g.chip_prefix == "it8696"

    def test_it8720_matches_generic_it87(self):
        g = lookup_chip_guidance("it8720")
        assert g is not None
        assert g.chip_prefix == "it87"
        assert g.in_mainline is True

    def test_f71882_matches(self):
        g = lookup_chip_guidance("f71882fg")
        assert g is not None
        assert g.driver_name == "f71882fg"
        assert g.in_mainline is True

    def test_unknown_chip_returns_none(self):
        assert lookup_chip_guidance("totally_unknown_chip") is None

    def test_case_insensitive(self):
        g = lookup_chip_guidance("NCT6798")
        assert g is not None
        assert g.chip_prefix == "nct679"

    def test_most_specific_prefix_wins(self):
        g = lookup_chip_guidance("it8688E")
        assert g is not None
        assert g.chip_prefix == "it8688"

    def test_nct677x_entry(self):
        g = lookup_chip_guidance("nct6775")
        assert g is not None
        assert g.chip_prefix == "nct677"

    def test_sch5627(self):
        g = lookup_chip_guidance("sch5627")
        assert g is not None
        assert g.driver_name == "sch5627"

    def test_guidance_has_bios_tips(self):
        g = lookup_chip_guidance("nct6687")
        assert g is not None
        assert len(g.bios_tips) > 0

    def test_guidance_has_known_issues(self):
        g = lookup_chip_guidance("nct6687")
        assert g is not None
        assert len(g.known_issues) > 0

    def test_guidance_has_driver_url(self):
        g = lookup_chip_guidance("nct6687")
        assert g is not None
        assert g.driver_url.startswith("http")


class TestFormatDriverStatus:
    def test_loaded_mainline(self):
        result = format_driver_status("nct6798", loaded=True)
        assert "loaded" in result
        assert "mainline" in result

    def test_loaded_out_of_tree(self):
        result = format_driver_status("nct6687", loaded=True)
        assert "loaded" in result
        assert "out-of-tree" in result

    def test_not_loaded_mainline(self):
        result = format_driver_status("nct6798", loaded=False)
        assert "not loaded" in result
        assert "modprobe" in result

    def test_not_loaded_out_of_tree(self):
        result = format_driver_status("nct6687", loaded=False)
        assert "not loaded" in result
        assert "install" in result

    def test_unknown_chip(self):
        result = format_driver_status("totally_unknown", loaded=True)
        assert "Unknown" in result


# ── New chip entries (v1.3.0) ─────────────────────────────────────


class TestNewChipEntries:
    def test_nct6686_matches_specific_entry(self):
        g = lookup_chip_guidance("nct6686d")
        assert g is not None
        assert g.chip_prefix == "nct6686"
        assert g.driver_name == "nct6683"
        assert g.in_mainline is True

    def test_nct6686_has_asrock_known_issues(self):
        g = lookup_chip_guidance("nct6686")
        assert g is not None
        assert any("ASRock" in issue or "asrock" in issue for issue in g.known_issues)

    def test_nct6686_does_not_match_nct6683(self):
        g = lookup_chip_guidance("nct6686d")
        assert g is not None
        assert g.chip_prefix == "nct6686"

    def test_nct6683_enriched_with_msi_tip(self):
        g = lookup_chip_guidance("nct6683")
        assert g is not None
        assert g.chip_prefix == "nct6683"
        assert any("MSI" in tip or "nct6687d" in tip for tip in g.bios_tips)

    def test_nct6683_has_known_issues(self):
        g = lookup_chip_guidance("nct6683")
        assert g is not None
        assert len(g.known_issues) > 0

    def test_asus_ec_sensors_entry(self):
        g = lookup_chip_guidance("asus_ec_sensors")
        assert g is not None
        assert g.driver_name == "asus_ec_sensors"
        assert g.in_mainline is True
        assert any("sensor-enrichment" in issue or "NOT a PWM" in issue for issue in g.known_issues)

    def test_asus_wmi_sensors_entry(self):
        g = lookup_chip_guidance("asus_wmi_sensors")
        assert g is not None
        assert g.driver_name == "asus_wmi_sensors"
        assert g.in_mainline is True
        assert any("poll" in tip.lower() for tip in g.bios_tips)

    def test_asus_wmi_sensors_has_polling_warning(self):
        g = lookup_chip_guidance("asus_wmi_sensors")
        assert g is not None
        assert any("X470" in issue or "stop" in issue for issue in g.known_issues)

    def test_it8689_has_degenerate_curve_values(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        assert any("40" in tip and "90" in tip for tip in g.bios_tips)

    def test_it8689_has_ignore_resource_conflict_tip(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        assert any("ignore_resource_conflict" in tip for tip in g.bios_tips)


# ── New vendor quirks (v1.3.0) ───────────────────────────────────


class TestNewVendorQuirks:
    def test_asus_wmi_polling_quirk(self):
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "asus_wmi_sensors")
        assert len(quirks) == 1
        assert quirks[0].severity == "high"
        assert "polling" in quirks[0].summary.lower()

    def test_msi_x870_brute_force_quirk(self):
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        high_quirks = [q for q in quirks if q.severity == "high"]
        assert len(high_quirks) == 1
        assert "brute_force" in high_quirks[0].details[2]

    def test_asrock_nct6686_quirk(self):
        quirks = lookup_vendor_quirks("ASRock", "nct6686d")
        assert len(quirks) == 1
        assert quirks[0].severity == "medium"
        assert "monitoring" in quirks[0].summary.lower()

    def test_asrock_nct6683_quirk(self):
        quirks = lookup_vendor_quirks("ASRock", "nct6683")
        assert len(quirks) == 1
        assert quirks[0].severity == "medium"

    def test_gigabyte_it87_info_quirk(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8720")
        assert len(quirks) == 1
        assert quirks[0].severity == "info"
        assert "ignore_resource_conflict" in quirks[0].details[0]

    def test_gigabyte_it87_force_id_warning(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8720")
        assert any("force_id" in d for d in quirks[0].details)


# ── Module conflict detection ────────────────────────────────────


class TestModuleConflictDetection:
    def test_nct6683_nct6687_conflict(self):
        conflicts = detect_module_conflicts(["nct6683", "nct6687", "k10temp"])
        assert len(conflicts) == 1
        assert conflicts[0].module_a == "nct6683"
        assert conflicts[0].module_b == "nct6687"
        assert "blacklist" in conflicts[0].explanation.lower()

    def test_no_conflict_single_module(self):
        conflicts = detect_module_conflicts(["nct6687", "k10temp"])
        assert len(conflicts) == 0

    def test_no_conflict_empty(self):
        conflicts = detect_module_conflicts([])
        assert len(conflicts) == 0

    def test_case_insensitive(self):
        conflicts = detect_module_conflicts(["NCT6683", "NCT6687"])
        assert len(conflicts) == 1


# ── Verification guidance ────────────────────────────────────────


class TestVerificationGuidance:
    def test_effective_returns_none(self):
        assert verification_guidance("effective", "Gigabyte", "it8696") is None

    def test_reverted_gigabyte_ite(self):
        result = verification_guidance(
            "pwm_enable_reverted", "Gigabyte Technology Co., Ltd.", "it8696"
        )
        assert result is not None
        assert "Full Speed" in result

    def test_reverted_msi(self):
        result = verification_guidance(
            "pwm_enable_reverted", "Micro-Star International Co., Ltd.", "nct6687"
        )
        assert result is not None
        assert "Smart Fan" in result
        assert "brute_force" in result

    def test_reverted_generic(self):
        result = verification_guidance("pwm_enable_reverted", "Unknown Vendor", "unknown_chip")
        assert result is not None
        assert "BIOS" in result

    def test_no_rpm_effect_gigabyte_it8689(self):
        result = verification_guidance("no_rpm_effect", "Gigabyte Technology Co., Ltd.", "it8689")
        assert result is not None
        assert "Rev 1" in result

    def test_no_rpm_effect_asrock_nct6(self):
        result = verification_guidance("no_rpm_effect", "ASRock", "nct6686d")
        assert result is not None
        assert "out-of-tree" in result

    def test_no_rpm_effect_generic(self):
        result = verification_guidance("no_rpm_effect", "Unknown", "unknown")
        assert result is not None
        assert "fan" in result.lower()

    def test_clamped(self):
        result = verification_guidance("pwm_value_clamped", "Gigabyte", "it8696")
        assert result is not None
        assert "clamping" in result.lower() or "clamp" in result.lower()

    def test_rpm_unavailable(self):
        result = verification_guidance("rpm_unavailable", "Gigabyte", "it8696")
        assert result is not None
        assert "RPM" in result
