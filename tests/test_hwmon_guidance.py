"""Tests for the chip-family knowledge base and driver guidance module."""

from control_ofc.ui.hwmon_guidance import (
    format_driver_status,
    lookup_chip_guidance,
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
