"""DEC-144 — 2026-Q2 it87/SIO knowledgebase refresh regression tests.

Locks in the four behavioural commitments of the refresh:

1. Remediation ordering: every dual-chip remediation surface recommends
   updating ``it87-dkms-git`` BEFORE the legacy ``mmio=on`` modparam
   (current driver builds default MMIO on — frankcrawford/it87 PR #95).
2. New escape hatch: IT8665E guidance carries the ``mmio=off``
   remediation for the maintainer-confirmed MMIO regression (issue #106).
3. New/updated chip knowledge: IT8622E (mainline), IT87952E (mainline
   enumeration ≥ 6.4, DKMS for dual-chip control), IT8689E Rev 1
   (control mainline since 7.1; temps-to-90 partial stopgap. Candidate
   fix is now frankcrawford/it87 PR #128, merged 2026-08-24 but
   unverified on IT8689E silicon; PR #114 was rejected 2026-08-25).
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
    def test_dual_chip_warning_names_the_driver_package_where_it_helps(self):
        """Rewritten by `UDOC-h` — the ordering it asserted is no longer meaningful.

        This was the THIRD test pinning the retracted remedy: it required that
        an `mmio=on` step "remain for old builds" and merely be ordered after
        the driver update. Ordering advice that cannot work does not make it
        work. Together with the two below, these guards are why the DEC-326
        correction landed in `docs/` and in `lookup_vendor_quirks` but not in
        this string — the wrong behaviour had test cover.

        What survives is the part that is still true: installing
        `it87-dkms-git` is a real remedy for the two cases where the driver is
        absent or the bridge is merely stuck, so the package must still be
        named.
        """
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        assert "it87-dkms-git" in html, "the package must still be named where it genuinely helps"
        # ...but never as the answer to the unreachable-bridge case.
        assert "no local fix" in html.lower()

    def test_dual_chip_warning_does_not_offer_mmio_as_a_remedy(self):
        """Rewritten by `UDOC-h` — it used to pin the claim that was wrong.

        The original asserted `mmio=on` was "presented as an older-build-only
        step", which is a scoping requirement on advice that should never have
        been given: `mmio` has been the driver default since it87's PR #95, so
        on the X870E AORUS MASTER measured for DEC-326 the step names a state
        already in effect and cannot help. This test was a live guard holding
        the retracted wording in place — worth recording, because it is why the
        DEC-326 correction reached the docs and the vendor-quirk table but not
        this string.
        """
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        lowered = html.lower()
        assert "no local fix" in lowered
        if "mmio" in lowered:
            assert "already the driver default" in lowered

    def test_readiness_dual_chip_fix_gives_the_discriminator(self):
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
        fix = problems["dual_chip"]["fix"].lower()
        # `UDOC-h`: this used to assert the ORDER of two remedies ("update
        # before mmio=on"). Ordering futile advice does not make it useful —
        # on a 0x8883 board neither step can work. The line now hands over the
        # discriminator instead, so the user finds out which fault they have
        # before changing anything.
        assert "dmesg" in fix
        assert "no local fix" in fix
        assert "mmio=on" not in fix

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
        assert "No known software workaround" not in flat, "the Rev 1 dead-end framing is obsolete"
        # Corrected 2026-07: mainline 7.1 gives IT8689E fan *control*; the
        # Rev 1 stopgap is partial (temps-to-90, CPU fan only).
        # Corrected 2026-08-25: PR #114 was REJECTED, superseded by PR #128
        # (merged 2026-08-24). #128 is a *candidate* fix — its own author
        # could not test it on IT8689 silicon — so the entry must name it
        # without promising working control.
        assert "control" in flat
        assert "PR #128" in flat, "must name the current candidate driver-side fix"
        # Guard the retraction: #114 may still be mentioned, but never as the
        # fix that is still coming. A bare "PR #114 in flat" would now pass
        # vacuously against the text that records its rejection.
        assert "pending driver-side fix (frankcrawford/it87 PR #114)" not in flat, (
            "PR #114 was rejected 2026-08-25 — it must not be framed as pending"
        )
        assert "rejected" in flat.lower(), (
            "the #114 retraction must be stated, not silently dropped"
        )
        # Truthfulness, 2026-08-26: #128 now HAS three IT8689E hardware reports
        # (incl. Rev 1 on a Z790 AORUS MASTER), so "unconfirmed" would assert the
        # opposite of the truth. But all three tested pre-merge head 429d2b40,
        # and the merged 27319db7 reworked 267 lines of the same bridge path —
        # so the entry must still tell the user to verify rather than assume.
        assert "unconfirmed on it8689e" not in flat.lower(), (
            "the retracted 'unconfirmed on IT8689E' claim must not return — "
            "three hardware reports exist (2026-08-23)"
        )
        assert "verify" in flat.lower(), (
            "the reports tested pre-merge code; the update-then-verify instruction must survive"
        )

    def test_it8689_quirk_documents_partial_stopgap(self):
        quirks = lookup_vendor_quirks(VENDOR_GB, "it8689")
        assert len(quirks) == 1
        flat = " ".join(quirks[0].details)
        assert "No known software workaround" not in flat
        assert "temperature" in flat.lower(), "quirk must document the temps-to-90 stopgap"
        # 2026-08-25: PR #114 rejected, superseded by PR #128. 2026-08-26: #128
        # gained three IT8689E hardware reports, so the quirk must name it as the
        # fix and lead with "update the driver" — while keeping the verify step,
        # because those reports tested the pre-merge patch.
        assert "PR #128" in flat, "quirk must point to the current driver-side fix"
        assert "unconfirmed on it8689e" not in flat.lower(), (
            "the retracted 'unconfirmed on IT8689E' claim must not return"
        )
        assert "it87-dkms-git" in flat, "quirk must tell the user which package to update"
        assert "verify" in flat.lower(), "the update-then-verify instruction must survive"

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
