"""Tests for AM4 500-series, AM5 600-series, and AM5 800-series support (DEC-106).

This pass extends DEC-105 (AM4 400-series) to the rest of the AMD x500
(AM4 500-series), x600 (AM5 600-series), and the directly-shipping
x800 (AM5 800-series) chip topologies. Adds narrower chip-prefix entries
for NCT6796D / NCT6798D / NCT6799D / IT8883, vendor quirks covering the
ASRock dual-Nuvoton legitimate config (X870E Taichi Lite), MSI
auto-allowlist for nct6687d v2.x, IT8689E Rev 1 silent-writes dead end,
and IT8883 unsupported-secondary-chip note. Also exercises the refined
daemon `module_collisions` detector behaviour through the GUI parser.

All sources cited are verifiable: kernel hwmon docs, frankcrawford/it87
issue tracker, Fred78290/nct6687d issue tracker, lm-sensors `configs/`
upstream repository.
"""

from __future__ import annotations

from control_ofc.api.models import (
    HardwareDiagnosticsResult,
    ModuleCollisionInfo,
    parse_hardware_diagnostics,
)
from control_ofc.ui.hwmon_guidance import (
    lookup_chip_guidance,
    lookup_vendor_quirks,
)
from control_ofc.ui.hwmon_label_resolver import resolve_label_from_fallback


class TestNarrowerChipGuidance:
    """Narrower chip-prefix entries take precedence over the generic
    `nct679` fallthrough via longest-prefix matching."""

    def test_nct6798_picks_specific_entry_not_generic(self):
        g = lookup_chip_guidance("nct6798")
        assert g is not None
        assert g.chip_prefix == "nct6798", (
            f"Expected most-specific match `nct6798`; got {g.chip_prefix}"
        )
        # The whole point of having a chip-specific entry (instead of
        # falling through to the generic `nct679` row) is that users on
        # NCT6798D should see why the DEC-105 brick risk does NOT apply
        # to them. The text must mention the specific NCT6798D chip ID
        # 0xd428 to anchor the distinction from NCT6797D's 0xd450.
        notes_plus_issues = " ".join([g.notes or "", *g.known_issues])
        assert "0xd428" in notes_plus_issues, (
            f"NCT6798D entry must cite chip ID 0xd428 to anchor the "
            f"distinction from NCT6797D's overlapped 0xd450; got "
            f"notes/issues: {notes_plus_issues!r}"
        )

    def test_nct6799_picks_specific_entry_with_taichi_reference(self):
        g = lookup_chip_guidance("nct6799")
        assert g is not None
        assert g.chip_prefix == "nct6799"
        # The entry must point users at the ASRock Taichi Lite case so
        # they don't blindly follow generic NCT679x guidance.
        flat = " ".join([g.notes or "", *g.known_issues]).lower()
        assert "taichi" in flat, (
            "NCT6799D entry should reference the ASRock X870E Taichi Lite "
            "legitimate dual-Nuvoton case"
        )

    def test_nct6796_picks_specific_entry(self):
        g = lookup_chip_guidance("nct6796")
        assert g is not None
        assert g.chip_prefix == "nct6796"
        # X870 Nova reference is the point of having a narrower entry.
        flat = " ".join([g.notes or "", *g.known_issues]).lower()
        assert "x870 nova" in flat or "nct6796d-s" in flat

    def test_generic_nct679_still_matches_nct6797(self):
        # The narrower entries shouldn't break the generic match for
        # chips that don't have their own row (e.g. NCT6797D).
        g = lookup_chip_guidance("nct6797")
        assert g is not None
        # NCT6797D doesn't have a chip-specific entry yet; it falls back
        # to the generic `nct679` entry.
        assert g.chip_prefix == "nct679"

    def test_it8883_preliminary_entry_marked_unsupported(self):
        # D4.A: ship a preliminary entry for IT8883 so users see a chip
        # name rather than "Unknown chip", AND the entry must make the
        # no-driver situation unambiguous.
        g = lookup_chip_guidance("it8883")
        assert g is not None
        assert g.chip_prefix == "it8883"
        flat = " ".join([g.notes or "", *g.known_issues]).lower()
        assert "no" in flat and "driver" in flat, (
            "IT8883 guidance must state no Linux driver is available"
        )
        # Cite the upstream tracking issue so the entry can be revisited
        # when a driver ships.
        assert "frankcrawford/it87" in g.driver_url or "issue" in g.driver_url


class TestX500X600X800VendorQuirks:
    def test_msi_nct6687_auto_allowlist_quirk(self):
        # MSI AM5 800-series: the new INFO entry must SPECIFICALLY mention
        # `msi_alt1` (the auto-enabled module parameter) AND the 33-board
        # allowlist count. The existing HIGH entry contains "auto" and
        # "allowlist" via other phrasing, so a substring-only check would
        # accidentally pass even if the new INFO entry were deleted —
        # this tighter assertion isolates the DEC-106 contribution.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        info_quirks = [q for q in quirks if q.severity == "info"]
        assert info_quirks, (
            f"Expected a new INFO-severity MSI+nct6687 quirk for the v2.x "
            f"auto-allowlist; got: {[(q.severity, q.summary) for q in quirks]}"
        )
        msi_alt1 = [
            q for q in info_quirks if "msi_alt1" in (q.summary + " ".join(q.details)).lower()
        ]
        assert msi_alt1, (
            "Expected the INFO MSI+nct6687 quirk to mention msi_alt1 "
            "specifically — that is the v2.x auto-enabled module "
            "parameter the user needs to know about"
        )
        # And the 33-board count anchors the upstream source we cited
        # (`nct6687.c::msi_alt1_dmi_table`).
        flat_info = " ".join(q.summary + " ".join(q.details) for q in info_quirks)
        assert "33" in flat_info, (
            "Expected the INFO MSI+nct6687 quirk to cite the 33-board "
            "allowlist count from Fred78290/nct6687d"
        )

    def test_asrock_dual_nuvoton_taichi_lite_quirk(self):
        # The Taichi Lite legitimate dual-Nuvoton case must be findable
        # via ASRock + nct6799 lookup, and must explicitly mention the
        # DEC-106 refinement so users don't panic at any residual banner.
        quirks = lookup_vendor_quirks("ASRock", "nct6799")
        flat = " ".join(q.summary + " ".join(q.details) for q in quirks)
        assert "taichi lite" in flat.lower()
        assert "dec-106" in flat.lower() or "refines" in flat.lower(), (
            "Expected ASRock+NCT6799 quirk to reference DEC-106 / the "
            "collision-detector refinement so users understand why the "
            "CRITICAL banner is suppressed"
        )

    def test_gigabyte_it8689_rev1_dead_end(self):
        # X670E AORUS MASTER IT8689E Rev 1 dead-end (frankcrawford/it87
        # issue #96) must surface a CRITICAL quirk so users don't waste
        # time chasing a software fix.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8689")
        critical = [q for q in quirks if q.severity == "critical"]
        assert critical, "Expected CRITICAL Gigabyte+IT8689E quirk"
        # The quirk must mention "Rev 1" so users understand it is a
        # hardware-revision-specific dead end, not a board-name match.
        matched = [q for q in critical if "rev 1" in (q.summary + " ".join(q.details)).lower()]
        assert matched, (
            "Expected the CRITICAL IT8689E quirk to call out Rev 1 "
            f"specifically; got: {[q.summary for q in critical]}"
        )

    def test_gigabyte_it8883_unsupported_quirk(self):
        # X870 AORUS STEALTH ICE secondary IT8883 has no driver — the
        # IT8696E primary works fine, so the quirk fires on the primary
        # chip lookup so users see the warning when checking that chip.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        flat = " ".join(q.summary + " ".join(q.details) for q in quirks).lower()
        assert "stealth ice" in flat or "it8883" in flat, (
            "Expected Gigabyte+IT8696E quirks to mention the IT8883 "
            "secondary chip / STEALTH ICE board"
        )

    def test_asrock_nct6798_supported_info(self):
        # AM4 500-series ASRock NCT6798D boards: mainline coverage is
        # solid. Surface an INFO entry so users see "this is supported"
        # rather than silence.
        quirks = lookup_vendor_quirks("ASRock", "nct6798")
        info = [q for q in quirks if q.severity == "info"]
        assert info, "Expected an INFO ASRock+NCT6798D guidance entry"

    def test_asrock_nct6796_supported_info(self):
        quirks = lookup_vendor_quirks("ASRock", "nct6796")
        info = [q for q in quirks if q.severity == "info"]
        assert info, "Expected an INFO ASRock+NCT6796D-S guidance entry"

    def test_asus_nct6798_am4_500_am5_600(self):
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "nct6798")
        info = [q for q in quirks if q.severity == "info"]
        assert info, "Expected an INFO ASUS+NCT6798D guidance entry"

    def test_msi_500_series_nct6687r_guidance(self):
        # AM4 500-series MSI NCT6687-R boards (MAG B550 TOMAHAWK etc.):
        # the quirk must point at the out-of-tree driver AND clarify
        # that DEC-105's brick risk does NOT apply to a genuine
        # NCT6687-R chip (0xd590) — only to NCT6797D mis-claim.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        flat = " ".join(q.summary + " ".join(q.details) for q in quirks).lower()
        assert "nct6687-r" in flat or "0xd590" in flat, (
            "Expected MSI+nct6687 guidance to reference the genuine "
            "NCT6687-R chip ID 0xd590 so users on real NCT6687-R boards "
            "are not panicked by the DEC-105 collision warning"
        )


class TestNewLabelFallbacks:
    def test_b550_vision_d_primary_chip(self):
        # Source: configs/Gigabyte/GA-B550-VISION-D.conf
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="B550 VISION D",
            chip_name="it8688",
            sensor_name="pwm1",
        )
        assert label == "CPU_FAN"

    def test_b550_vision_d_secondary_chip(self):
        # Same config — secondary IT8792E at 0x0a60.
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="B550 VISION D",
            chip_name="it8792",
            sensor_name="pwm3",
        )
        assert label == "SYS_FAN4"

    def test_b550m_aorus_pro_single_chip(self):
        # Source: configs/Gigabyte/GA-B550M-AORUS-PRO.conf
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="B550M AORUS PRO",
            chip_name="it8688",
            sensor_name="pwm5",
        )
        assert label == "CPU_OPT"

    def test_b550m_aorus_pro_wifi_variant_matches_via_glob(self):
        # The board_glob `B550M AORUS PRO*` must cover the WIFI SKU.
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="B550M AORUS PRO AX",
            chip_name="it8688",
            sensor_name="pwm1",
        )
        assert label == "CPU_FAN"

    def test_msi_x570_a_pro_labels(self):
        # Source: configs/MSI/X570-A-Pro.conf — chip nct6797.
        label = resolve_label_from_fallback(
            vendor="Micro-Star International Co., Ltd.",
            board_name="X570-A PRO",
            chip_name="nct6797",
            sensor_name="pwm2",
        )
        assert label == "CPU Fan"

    def test_msi_x570_a_pro_pump_label(self):
        # fan1 is labelled "Pump" upstream — important to surface so
        # users don't assign their CPU fan to the AIO pump curve.
        label = resolve_label_from_fallback(
            vendor="Micro-Star International Co., Ltd.",
            board_name="X570-A PRO",
            chip_name="nct6797",
            sensor_name="pwm1",
        )
        assert label == "Pump"

    def test_wrong_chip_returns_none(self):
        # The same B550 VISION D board with the wrong chip name (e.g.
        # the user has both chips and asks about a non-existent
        # nct6798) must return None, not the it8688 mapping.
        assert (
            resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="B550 VISION D",
                chip_name="nct6798",
                sensor_name="pwm1",
            )
            is None
        )

    def test_wrong_vendor_returns_none(self):
        # ASRock + B550 VISION D board_name must NOT match a Gigabyte
        # entry — vendor equality is required.
        assert (
            resolve_label_from_fallback(
                vendor="ASRock",
                board_name="B550 VISION D",
                chip_name="it8688",
                sensor_name="pwm1",
            )
            is None
        )

    def test_unverified_entry_gets_unverified_suffix(self):
        # The X870E AORUS MASTER it87952 secondary-chip mapping ships
        # with `verified=False` (silkscreen tracing not yet done). The
        # resolver must append `(unverified)` so users can tell which
        # labels are trustworthy. Without this test, an accidental edit
        # that drops the suffix logic on `FallbackLabel.display()` would
        # silently mislabel unverified headers as if they were verified.
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            chip_name="it87952",
            sensor_name="pwm2",
        )
        assert label is not None
        assert label.endswith("(unverified)"), (
            f"Unverified entries must surface the `(unverified)` suffix; got: {label!r}"
        )


class TestModuleCollisionRefinementGuiSide:
    """The DEC-106 refinement happens daemon-side, but the GUI must round-
    trip the empty `module_collisions` field cleanly when the daemon
    suppresses on a legitimate dual-Nuvoton board."""

    def test_empty_module_collisions_from_dual_nuvoton_daemon(self):
        # Simulating the daemon's output on an ASRock X870E Taichi Lite
        # after DEC-106 refinement: both modules loaded but two distinct
        # nct6 chips detected → daemon emits no collision entries (field
        # omitted from the wire entirely).
        payload = {
            "api_version": 1,
            "hwmon": {
                "chips_detected": [
                    {
                        "chip_name": "nct6686",
                        "device_id": "isa-0a20",
                        "expected_driver": "nct6687",
                        "in_mainline_kernel": False,
                        "header_count": 5,
                    },
                    {
                        "chip_name": "nct6799",
                        "device_id": "isa-0290",
                        "expected_driver": "nct6775",
                        "in_mainline_kernel": True,
                        "header_count": 3,
                    },
                ],
                "total_headers": 8,
                "writable_headers": 8,
                "enable_revert_counts": {},
            },
            "kernel_modules": [
                {"name": "nct6687", "loaded": True, "in_mainline": False},
                {"name": "nct6775", "loaded": True, "in_mainline": True},
            ],
            "acpi_conflicts": [],
            "board": {
                "vendor": "ASRock",
                "name": "X870E Taichi Lite",
                "bios_version": "1.0",
            },
            # No `module_collisions` key — daemon suppressed it.
        }
        result = parse_hardware_diagnostics(payload)
        assert isinstance(result, HardwareDiagnosticsResult)
        assert result.module_collisions == [], (
            "Legitimate dual-Nuvoton board must surface no collision entries through the GUI parser"
        )
        # Sanity: the chip list is still surfaced so the GUI can render
        # the supported-chips view normally.
        assert len(result.hwmon.chips_detected) == 2

    def test_single_chip_collision_still_critical_through_parser(self):
        # The brick scenario from DEC-105: single chip + both modules
        # loaded → CRITICAL collision survives the parser.
        payload = {
            "module_collisions": [
                {
                    "module_a": "nct6687",
                    "module_b": "nct6775",
                    "severity": "critical",
                    "summary": "nct6687 (out-of-tree) and nct6775 ...",
                    "remediation": "(1) Identify the chip FIRST: ...",
                }
            ],
        }
        result = parse_hardware_diagnostics(payload)
        assert len(result.module_collisions) == 1
        assert isinstance(result.module_collisions[0], ModuleCollisionInfo)
        assert result.module_collisions[0].severity == "critical"
