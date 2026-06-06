"""DEC-144 — 2026-Q2 it87/SIO knowledgebase refresh regression tests.

Locks in the four behavioural commitments of the refresh:

1. Remediation ordering: every dual-chip remediation surface recommends
   updating ``it87-dkms-git`` BEFORE the legacy ``mmio=on`` modparam
   (current driver builds default MMIO on — frankcrawford/it87 PR #95).
2. New escape hatch: IT8665E guidance carries the ``mmio=off``
   remediation for the maintainer-confirmed MMIO regression (issue #106).
3. New/updated chip knowledge: IT8622E (mainline), IT87952E (mainline
   enumeration ≥ 6.4, DKMS for dual-chip control), IT8689E Rev 1
   (flat-curve fix replaces the "no software workaround" dead end).
4. New vendor quirk: Gigabyte B650 GAMING X AX V2 ACPI bind failure
   (issue #92) — AMD-platform + board-scoped.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ThermalSafetyInfo,
)
from control_ofc.ui.hwmon_guidance import (
    dual_chip_warning_html,
    lookup_chip_guidance,
    lookup_vendor_quirks,
)
from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

VENDOR_GB = "Gigabyte Technology Co., Ltd."


# ── 1. Remediation ordering ────────────────────────────────────────


class TestRemediationOrdering:
    def test_dual_chip_warning_recommends_driver_update_before_mmio(self):
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        update_pos = html.find("it87-dkms-git")
        mmio_pos = html.find("mmio=on")
        assert update_pos != -1, "warning must name the driver package to update"
        assert mmio_pos != -1, "legacy mmio=on step must remain for old builds"
        assert update_pos < mmio_pos, (
            "DEC-144: the driver update must be step 1; mmio=on is the "
            "legacy-build fallback, not the headline remediation"
        )

    def test_dual_chip_warning_scopes_mmio_to_older_builds(self):
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        assert "older" in html.lower(), "mmio=on must be presented as an older-build-only step"

    def test_readiness_dual_chip_fix_orders_update_first(self):
        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="it8696", header_count=5)],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor=VENDOR_GB, name="X870E AORUS MASTER"),
            kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            expected_chips=["it8696", "it87952"],
        )
        problems = {p["key"]: p for p in detect_readiness_problems(diag)}
        assert "dual_chip" in problems
        fix = problems["dual_chip"]["fix"]
        assert fix.find("it87-dkms-git") != -1
        assert fix.find("it87-dkms-git") < fix.find("mmio=on")

    def test_readiness_acpi_fix_orders_update_first_for_it87(self):
        from control_ofc.api.models import AcpiConflictInfo

        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="it8689", header_count=5)],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor=VENDOR_GB, name="Generic"),
            kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            acpi_conflicts=[
                AcpiConflictInfo(
                    io_range="0a40-0a4f", claimed_by="ACPI", conflicts_with_driver="it87"
                )
            ],
        )
        problems = {p["key"]: p for p in detect_readiness_problems(diag)}
        assert "acpi" in problems
        fix = problems["acpi"]["fix"]
        assert fix.find("it87-dkms-git") != -1
        assert fix.find("it87-dkms-git") < fix.find("ignore_resource_conflict")


# ── 2. IT8665E mmio=off escape hatch ───────────────────────────────


class TestIt8665MmioOffEscapeHatch:
    def test_entry_exists_and_is_out_of_tree(self):
        g = lookup_chip_guidance("it8665")
        assert g is not None
        assert g.chip_prefix == "it8665"
        assert g.in_mainline is False

    def test_entry_carries_mmio_off_remediation_and_source(self):
        g = lookup_chip_guidance("it8665")
        assert g is not None
        flat = " ".join(g.known_issues)
        assert "mmio=off" in flat, "DEC-144: IT8665E guidance must carry the mmio=off escape hatch"
        joined = " ".join([flat, g.driver_url, g.notes])
        assert "106" in joined, "must cite frankcrawford/it87 issue #106"


# ── 3. Chip knowledge updates ──────────────────────────────────────


class TestChipKnowledgeUpdates:
    def test_it8622_is_mainline_builtin(self):
        g = lookup_chip_guidance("it8622")
        assert g is not None
        assert g.chip_prefix == "it8622"
        assert g.in_mainline is True
        assert "built-in" in g.driver_package

    def test_it87952_has_dedicated_entry_not_generic_fallthrough(self):
        g = lookup_chip_guidance("it87952")
        assert g is not None
        assert g.chip_prefix == "it87952", (
            "it87952 must hit its own entry (longest-prefix), not the generic it87 fallthrough"
        )

    def test_it87952_mainline_enumeration_but_dkms_control(self):
        g = lookup_chip_guidance("it87952")
        assert g is not None
        # Matches the daemon's chip_driver_in_mainline (kernel ≥ 6.4).
        assert g.in_mainline is True
        flat = " ".join([g.driver_package, *g.known_issues]).lower()
        assert "it87-dkms-git" in flat, "entry must state dual-chip CONTROL needs the DKMS build"

    def test_it8689_rev1_no_longer_a_dead_end(self):
        g = lookup_chip_guidance("it8689")
        assert g is not None
        flat = " ".join(g.known_issues)
        assert "No known software workaround" not in flat, (
            "DEC-144: the Rev 1 dead-end framing is obsolete — issue #96 "
            "documents the BIOS flat-curve fix"
        )
        assert "manual control" in flat, "must state the flat-curve fix outcome"

    def test_it8689_quirk_documents_flat_curve_fix(self):
        quirks = lookup_vendor_quirks(VENDOR_GB, "it8689")
        assert len(quirks) == 1
        flat = " ".join(quirks[0].details)
        assert "No known software workaround" not in flat
        assert "40/40/40/40/40/40" in flat, "quirk must spell out the upstream 7-point flat curve"

    def test_it8883_entry_refreshed_not_stale_dated(self):
        g = lookup_chip_guidance("it8883")
        assert g is not None
        flat = " ".join([g.driver_name, g.notes, *g.known_issues])
        assert "2026-Q2" not in flat, (
            "DEC-144 refreshed the IT8883 entry — the 2026-Q2 stamp must "
            "not linger after the 2026-06 re-check"
        )


# ── 4. B650 GAMING X AX V2 vendor quirk ────────────────────────────


class TestB650GamingXAxV2Quirk:
    def test_fires_on_exact_board_with_amd_platform(self):
        quirks = lookup_vendor_quirks(
            VENDOR_GB,
            "it8689",
            cpu_vendor="AMD",
            board_name="B650 GAMING X AX V2",
        )
        flat = " ".join(q.summary + " " + " ".join(q.details) for q in quirks)
        assert "ignore_resource_conflict=1" in flat
        assert "92" in flat, "must cite frankcrawford/it87 issue #92"

    def test_does_not_fire_on_other_b650_boards(self):
        quirks = lookup_vendor_quirks(
            VENDOR_GB,
            "it8689",
            cpu_vendor="AMD",
            board_name="B650 EAGLE AX",
        )
        assert not any("B650 GAMING X AX V2" in q.summary for q in quirks)

    def test_suppressed_when_platform_unknown(self):
        # Board-pattern + platform scoping: an unknown CPU vendor must
        # suppress the platform-scoped quirk (truthful "we don't know").
        quirks = lookup_vendor_quirks(
            VENDOR_GB,
            "it8689",
            board_name="B650 GAMING X AX V2",
        )
        assert not any("B650 GAMING X AX V2" in q.summary for q in quirks)
