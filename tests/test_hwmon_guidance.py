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
        # DEC-106 added a narrower `nct6798` entry that takes precedence
        # over the generic `nct679` entry. The user-visible binding
        # (driver name, mainline status) is unchanged.
        g = lookup_chip_guidance("nct6798")
        assert g is not None
        assert g.chip_prefix == "nct6798"
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

    def test_it8665_names_the_driver_update_as_the_fix(self):
        # Curator 2026-07-29: frankcrawford/it87 PR #120 (merged 2026-07-22)
        # removed the MMIO path for IT8665E, so "update the driver" is now the
        # primary fix and mmio=off is only the older-build fallback — guard
        # against a revert to "mmio=off is the remediation".
        g = lookup_chip_guidance("it8665")
        assert g is not None
        assert g.driver_name == "it87"
        assert g.in_mainline is False
        blob = (" ".join(g.known_issues) + " " + g.notes).lower()
        assert "pr #120" in blob  # names the merged driver-side fix
        assert "update" in blob  # update-the-driver is the primary path
        assert "mmio=off" in blob  # fallback still documented
        assert "fallback" in blob  # ...and framed as a fallback, not the fix

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
        # DEC-106 added the narrower `nct6798` entry, so an upper-case
        # NCT6798 query now hits that — still proving the case-insensitive
        # match works.
        g = lookup_chip_guidance("NCT6798")
        assert g is not None
        assert g.chip_prefix == "nct6798"

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

    def test_nct6687_notes_cover_current_gen_boards(self):
        # superio-curator review 2026-07-21: Fred78290/nct6687d added MSI B850
        # GAMING PRO WIFI6E (#182) and MAG B860M Mortar WiFi (#183); the in-app
        # guidance must not lag a board generation behind the current AM5/Intel gens.
        g = lookup_chip_guidance("nct6687")
        assert g is not None
        # Structural facts that actually mislead users if wrong (per test-review):
        # NCT6687-R needs the out-of-tree driver, and this is the actionable remedy.
        assert g.in_mainline is False
        assert g.driver_package == "nct6687d-dkms-git (AUR)"
        # Board-generation currency (the 2026-07 refresh):
        assert "B850" in g.notes or "B860" in g.notes


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

    def test_it8689_documents_temp_flatten_stopgap(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        assert any("90" in tip for tip in g.bios_tips), (
            "IT8689E guidance must document the temps-to-90 BIOS-curve stopgap"
        )

    def test_it8689_has_ignore_resource_conflict_tip(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        assert any("ignore_resource_conflict" in tip for tip in g.bios_tips)


# ── New vendor quirks (v1.3.0) ───────────────────────────────────


class TestNewVendorQuirks:
    def test_asus_wmi_polling_quirk(self):
        # DEC-105 added a second, AM4-specific asus_wmi_sensors quirk that
        # also matches an ASUS+asus_wmi_sensors lookup. Both legitimately
        # carry the polling warning — assert that at least one entry
        # surfaces the polling concern at HIGH severity.
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "asus_wmi_sensors")
        assert quirks, "expected at least one ASUS+asus_wmi_sensors quirk"
        polling_high = [
            q for q in quirks if q.severity == "high" and "polling" in q.summary.lower()
        ]
        assert polling_high, (
            f"expected at least one HIGH polling quirk; got {[q.summary for q in quirks]}"
        )

    def test_msi_x870_brute_force_quirk(self):
        # The HIGH quirk for MSI X870/B850 7-point-write must mention
        # `msi_fan_brute_force`. Asserting on `any(...)` rather than a
        # specific `details[2]` index makes the test robust to detail-
        # list reordering — what matters is that the workaround is
        # documented, not which line carries it.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        high_quirks = [q for q in quirks if q.severity == "high"]
        assert high_quirks, "Expected MSI X870/B850 HIGH quirk to exist"
        assert any("brute_force" in d for q in high_quirks for d in q.details), (
            "Expected the HIGH MSI+nct6687 quirk to document the "
            "`msi_fan_brute_force=1` module parameter as a workaround"
        )

    def test_msi_brute_force_names_the_nct6683_blacklist_prerequisite(self):
        # 2026-08-25 (SIO-c): upstream states blacklisting `nct6683` as a
        # REQUIREMENT for `msi_fan_brute_force` — if nct6683 binds the chip
        # first, nct6687 never claims it and PWM writes fail with EIO. This
        # was the literal resolution of Fred78290/nct6687d issue #202. Our
        # guidance previously gave the modprobe line without the blacklist,
        # so following it verbatim could silently no-op.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        brute = [q for q in quirks if any("brute_force" in d for d in q.details)]
        assert brute, "expected at least one MSI quirk documenting msi_fan_brute_force"
        for q in brute:
            flat = " ".join(q.details)
            assert "nct6683" in flat, (
                "a quirk recommending msi_fan_brute_force must also name the "
                f"required nct6683 blacklist; got: {q.summary}"
            )

    def test_msi_alt1_carries_the_do_not_force_counter_warning(self):
        # 2026-08-25 (SIO-d): upstream warns against forcing
        # `fan_config=msi_alt1` on non-NCT6687DR boards — B650/B660/X670/
        # Z690/Z790 use the default mapping, and forcing alt1 there reads EC
        # offsets that are zero on that silicon, so every SYS_FAN reports
        # 0 RPM. This quirk renders on ANY MSI+nct6687 board, so the
        # counter-warning has to travel with the recommendation.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        alt1 = [q for q in quirks if "msi_alt1" in " ".join(q.details)]
        assert alt1, "expected an MSI quirk mentioning msi_alt1"
        flat = " ".join(d for q in alt1 for d in q.details)
        assert "DO NOT force" in flat, (
            "the msi_alt1 guidance must carry the explicit do-not-force warning"
        )
        # Name the affected families — a generic 'be careful' would not tell
        # a B650 owner that the warning is about them.
        for family in ("B650", "X670", "Z690", "Z790"):
            assert family in flat, (
                f"the counter-warning must name {family} as a non-DR family "
                "that must not have msi_alt1 forced"
            )
        assert "0 RPM" in flat, "the warning must state the observable symptom"

    def test_brute_force_is_not_described_as_an_older_build_alternative(self):
        # 2026-08-25 (SIO-b): `msi_fan_brute_force` is a CURRENT [BETA]
        # parameter and is orthogonal to `fan_config` — upstream lists the
        # same MSI boards for both. Our text used to call it the option for
        # "older driver builds", which sent users to the wrong knob.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        flat = " ".join(d for q in quirks for d in q.details)
        assert "older driver builds" not in flat, (
            "msi_fan_brute_force is a current BETA parameter, not an "
            "older-build alternative to fan_config=msi_alt1"
        )

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
        # DEC-144 re-ordered the remediation: driver update is bullet 1,
        # the driver-local parameter follows it.
        flat = " ".join(quirks[0].details)
        assert "ignore_resource_conflict" in flat
        assert flat.find("it87-dkms-git") < flat.find("ignore_resource_conflict")

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
