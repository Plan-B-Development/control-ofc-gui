"""Tests for Intel LGA1851 platform support (DEC-110).

LGA1851 is the new Arrow Lake / Core Ultra socket shipping on Z890
boards from Q1 2026. We deliberately ship narrow coverage here — only
the MSI Z890 NCT6687DR `msi_alt1` case is well-documented enough to be
testable without speculation. Other Z890 vendor coverage will follow
as upstream allowlists and driver support stabilise.

Sources cited inline are verifiable: Fred78290/nct6687d source
(nct6687.c::msi_alt1_dmi_table), kernel nct6775 docs.
"""

from __future__ import annotations

from control_ofc.ui.hwmon_guidance import lookup_vendor_quirks


class TestMsiZ890Nct6687drQuirk:
    """The MSI Z890 NCT6687DR + msi_alt1 quirk must be Intel-scoped AND
    board-pattern-scoped so it does NOT fire on:
        - MSI Intel Z690/Z790 (plain NCT6687D, no msi_alt1)
        - MSI AMD X870/X870E (same NCT6687DR chip but AMD platform)"""

    def test_msi_z890_intel_emits_msi_alt1_warning(self):
        # MSI Z890 boards need msi_alt1 — the HIGH-severity quirk must
        # fire when cpu_vendor=Intel AND board_name contains "Z890".
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="MEG Z890 ACE",
        )
        z890_quirks = [q for q in quirks if q.board_pattern == "Z890"]
        assert z890_quirks, (
            f"Expected MSI Z890 NCT6687DR msi_alt1 quirk; got: "
            f"{[(q.severity, q.board_pattern, q.summary) for q in quirks]}"
        )
        # The quirk must cite msi_alt1 specifically — that is the
        # actionable module parameter the user needs.
        flat = " ".join(q.summary + " ".join(q.details) for q in z890_quirks)
        assert "msi_alt1" in flat, "Expected MSI Z890 quirk to mention msi_alt1 module parameter"

    def test_msi_z690_intel_does_not_match_z890_quirk(self):
        # MSI Intel Z690 ships plain NCT6687D — must NOT match the Z890
        # board-pattern-scoped quirk. The auto-detect INFO quirk still
        # fires (a separate Intel-scoped entry without board_pattern).
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="MAG Z690 TOMAHAWK WIFI",
        )
        z890_quirks = [q for q in quirks if q.board_pattern == "Z890"]
        assert not z890_quirks, (
            "MSI Z690 must NOT match the Z890 msi_alt1 quirk; "
            f"got Z890-scoped quirks: {[q.summary for q in z890_quirks]}"
        )

    def test_msi_z790_intel_does_not_match_z890_quirk(self):
        # Same as Z690 — Z790 ships plain NCT6687D.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="MEG Z790 ACE",
        )
        z890_quirks = [q for q in quirks if q.board_pattern == "Z890"]
        assert not z890_quirks

    def test_msi_x870e_amd_does_not_match_z890_intel_quirk(self):
        # MSI AMD X870E also ships NCT6687DR, but the Intel-scoped Z890
        # quirk must NOT fire. The existing AM5 800-series MSI quirk
        # covers the AMD case.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="AMD",
            board_name="MEG X870E GODLIKE",
        )
        z890_quirks = [q for q in quirks if q.board_pattern == "Z890"]
        assert not z890_quirks, (
            "MSI AMD X870E must NOT match the Intel-scoped Z890 quirk; "
            f"got: {[q.summary for q in z890_quirks]}"
        )

    def test_msi_z890_quirk_suppressed_when_board_name_empty(self):
        # Empty board_name (older daemon without DMI, or stripped DMI)
        # must suppress board-pattern-scoped quirks rather than firing
        # them indiscriminately on every MSI Intel system.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="",
        )
        z890_quirks = [q for q in quirks if q.board_pattern == "Z890"]
        assert not z890_quirks, (
            "Board-pattern-scoped quirk must NOT fire when board_name is unknown"
        )

    def test_msi_z890_quirk_severity_is_high(self):
        # The msi_alt1 case is a real BIOS-level interference scenario
        # that can leave users with fans stuck at default speed. HIGH
        # severity (not info) so the diagnostics page surfaces it as a
        # warning banner.
        quirks = lookup_vendor_quirks(
            "Micro-Star International Co., Ltd.",
            "nct6687",
            cpu_vendor="Intel",
            board_name="MEG Z890 ACE",
        )
        z890_high = [q for q in quirks if q.board_pattern == "Z890" and q.severity == "high"]
        assert z890_high, (
            f"Expected HIGH severity for MSI Z890 msi_alt1 quirk; "
            f"got: {[(q.severity, q.summary) for q in quirks if q.board_pattern]}"
        )


class TestGigabyteZ890LgaIntelTopology:
    """Gigabyte Z890 AORUS — same dual-chip topology as AMD X870E AORUS
    (IT8696E + IT87952E). The Intel-scoped IT8696E quirk must fire."""

    def test_gigabyte_z890_aorus_matches_intel_it8696_quirk(self):
        quirks = lookup_vendor_quirks(
            "Gigabyte Technology Co., Ltd.",
            "it8696",
            cpu_vendor="Intel",
            board_name="Z890 AORUS MASTER",
        )
        intel_quirks = [q for q in quirks if q.platform == "intel"]
        assert intel_quirks, "Expected Gigabyte Intel IT8696E quirk on Z890 AORUS"
        # The quirk must call out Z890 / LGA1851 specifically so users
        # understand it covers their generation.
        flat = " ".join(q.summary + " ".join(q.details) for q in intel_quirks)
        assert "z890" in flat.lower() or "lga1851" in flat.lower()

    def test_gigabyte_z890_amd_x870e_does_not_match_intel_quirk(self):
        # Same chip on AMD X870E AORUS MASTER — must NOT match the
        # Intel-scoped quirk. The existing AMD coverage handles X870E.
        quirks = lookup_vendor_quirks(
            "Gigabyte Technology Co., Ltd.",
            "it8696",
            cpu_vendor="AMD",
            board_name="X870E AORUS MASTER",
        )
        intel_quirks = [q for q in quirks if q.platform == "intel"]
        assert not intel_quirks
