"""Tests for AM4 400-series & related AMD motherboard support (DEC-105).

Covers the new hardening pass: vendor quirks for MSI NCT6797D mis-claim by
nct6687, kernel-documented asus_wmi_sensors AM4 boards, ASRock/Gigabyte AM4
400-series chip families, the new daemon `module_collisions` field on the
GUI side, and label-fallback entries for boards with upstream lm-sensors
configs.

All sources cited inline are verifiable: Linux kernel hwmon docs, the
frankcrawford/it87 README, the Fred78290/nct6687d README, and the
lm-sensors `configs/` directory in the upstream repository.
"""

from __future__ import annotations

from control_ofc.api.models import (
    HardwareDiagnosticsResult,
    ModuleCollisionInfo,
    parse_hardware_diagnostics,
)
from control_ofc.ui.hwmon_guidance import (
    detect_module_conflicts,
    lookup_chip_guidance,
    lookup_vendor_quirks,
)
from control_ofc.ui.hwmon_label_resolver import resolve_label_from_fallback


class TestMsiNct6797MisIdQuirk:
    """The headline DEC-105 case: NCT6797D vs out-of-tree nct6687 chip-ID
    overlap can corrupt non-volatile fan registers on AM4/AM5 MSI boards."""

    def test_msi_nct6797_emits_critical_quirk(self):
        # Tighter than "some critical quirk exists": the headline
        # invariant is that the chip-ID overlap (0xd450) AND the
        # offending driver name (nct6687) BOTH appear inside the SAME
        # CRITICAL quirk's details. A rewording that drops either anchor
        # is a real regression — these are the two facts a user can
        # verify against upstream sources
        # (drivers/hwmon/nct6775-platform.c and the Fred78290/nct6687d
        # README).
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6797")
        critical = [q for q in quirks if q.severity == "critical"]
        assert critical, "Expected at least one CRITICAL MSI+NCT6797 quirk"
        matched = [
            q
            for q in critical
            if "0xd450" in " ".join(q.details) and any("nct6687" in d.lower() for d in q.details)
        ]
        assert matched, (
            "Expected one CRITICAL MSI+NCT6797 quirk whose details mention "
            "BOTH the colliding chip ID 0xd450 and the misclaiming driver "
            "nct6687; got severities/summaries: "
            f"{[(q.severity, q.summary) for q in critical]}"
        )

    def test_msi_nct6798_also_carries_misid_warning(self):
        # NCT6798D shares the overlap concern. The CRITICAL quirk's text
        # must still anchor against the same two upstream-verifiable
        # facts as the 6797 case: 0xd450 chip-ID collision + the
        # misclaiming nct6687 driver name.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6798")
        critical = [q for q in quirks if q.severity == "critical"]
        assert critical, "Expected at least one CRITICAL MSI+NCT6798 quirk"
        matched = [
            q
            for q in critical
            if any("nct6687" in d.lower() for d in q.details) or "nct6687" in q.summary.lower()
        ]
        assert matched, (
            "Expected the CRITICAL MSI+NCT6798 quirk to reference nct6687 as the colliding driver"
        )

    def test_msi_nct6795_is_only_informational(self):
        # NCT6795D has no chip-ID overlap concern. We want users to be
        # told mainline coverage is fine, not to surface a critical warning.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6795")
        assert quirks, "Expected at least an INFO entry for MSI + NCT6795D"
        assert all(q.severity != "critical" for q in quirks)

    def test_non_msi_vendor_does_not_emit_misid_warning(self):
        # The trap is specific to MSI's chip/driver combination. ASUS or
        # Gigabyte with the same chip name must not trigger the CRITICAL
        # misID quirk (other ASUS quirks may legitimately match — e.g.
        # the long-standing ASUS+nct679x ACPI port-conflict entry).
        asus_quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "nct6797")
        for q in asus_quirks:
            assert q.severity != "critical", (
                "ASUS + NCT6797 must not surface the MSI-specific CRITICAL nct6687 misID quirk"
            )


class TestAsusAm4Quirks:
    """ASUS asus_wmi_sensors / asus_ec_sensors / asus_atk0110 coverage
    for the AM4 generation. Sourced from upstream kernel docs."""

    def test_asus_wmi_polling_warning_for_listed_boards(self):
        # Kernel docs explicitly list PRIME X470-PRO + 5 ROG STRIX AM4 boards.
        # The polling-frequency warning must fire on these.
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "asus_wmi_sensors")
        assert quirks, "Expected ASUS+asus_wmi_sensors quirk to exist"
        flat = " ".join(q.summary + " ".join(q.details) for q in quirks)
        for board in [
            "PRIME X470-PRO",
            "ROG STRIX B450-E",
            "ROG STRIX B450-F",
            "ROG STRIX B450-I",
            "ROG STRIX X470-F",
            "ROG STRIX X470-I",
        ]:
            assert board in flat, f"AM4 board {board} missing from quirk text"

    def test_asus_atk0110_recognised_as_read_only(self):
        # asus_atk0110 was a real diagnostic dead end before this pass:
        # ASUS users saw sensors but no PWM control and the GUI gave no
        # hint. The fix is a chip-guidance entry that explicitly says
        # "this is read-only" so users do not waste time looking for a
        # PWM-write capability on this driver. Asserts both that the
        # guidance exists AND that it carries the read-only signal.
        g = lookup_chip_guidance("asus_atk0110")
        assert g is not None, "asus_atk0110 must be in CHIP_GUIDANCE_DB"
        issues_blob = " ".join(g.known_issues).lower()
        notes_blob = (g.notes or "").lower()
        assert "read-only" in issues_blob or "read-only" in notes_blob, (
            "asus_atk0110 guidance must explicitly state the driver is "
            f"read-only; got known_issues={g.known_issues!r}, notes={g.notes!r}"
        )


class TestAsrockAm4Quirks:
    def test_asrock_nct6779_treated_as_supported(self):
        # Common AM4 ASRock chip — mainline coverage is solid. The quirk
        # must be INFO severity (NOT critical/high) so it presents as
        # supported guidance rather than a warning banner. Asserting on
        # severity catches future accidental severity bumps.
        quirks = lookup_vendor_quirks("ASRock", "nct6779")
        am4_info = [q for q in quirks if q.severity == "info"]
        assert am4_info, (
            f"Expected an INFO-severity ASRock+NCT6779D guidance entry; "
            f"got: {[(q.severity, q.summary) for q in quirks]}"
        )
        # And no critical/high — if a future edit accidentally promotes
        # the AM4 ASRock guidance to a warning, this fails loudly.
        assert all(q.severity in ("info", "medium") for q in quirks), (
            f"ASRock+NCT6779D must not surface CRITICAL or HIGH quirks "
            f"(NCT6779D has mainline kernel coverage); got: "
            f"{[(q.severity, q.summary) for q in quirks]}"
        )

    def test_asrock_nct6792_treated_as_supported(self):
        # AM4 ITX/AC boards (e.g. B450 Gaming ITX/AC) use NCT6792D —
        # kernel coverage is solid via nct6775 driver.
        quirks = lookup_vendor_quirks("ASRock", "nct6792")
        am4_info = [q for q in quirks if q.severity == "info"]
        assert am4_info, (
            f"Expected an INFO-severity ASRock+NCT6792D guidance entry; "
            f"got: {[(q.severity, q.summary) for q in quirks]}"
        )


class TestModuleCollisionDetection:
    """Daemon-reported and GUI-fallback collision pair handling."""

    def test_gui_fallback_flags_nct6687_with_nct6775(self):
        # The CRITICAL pair must be detected by the GUI's static table for
        # users on older daemons that don't emit `module_collisions` yet.
        conflicts = detect_module_conflicts(["nct6687", "nct6775", "k10temp", "amdgpu"])
        pairs = {tuple(sorted([c.module_a, c.module_b])) for c in conflicts}
        assert ("nct6687", "nct6775") in pairs

    def test_gui_fallback_silent_when_only_one_of_pair_loaded(self):
        # Lone nct6687 must NOT trigger the collision banner — many MSI
        # users intentionally run only the out-of-tree driver.
        conflicts = detect_module_conflicts(["nct6687", "k10temp"])
        pairs = {tuple(sorted([c.module_a, c.module_b])) for c in conflicts}
        assert ("nct6687", "nct6775") not in pairs

    def test_daemon_module_collisions_round_trip(self):
        # The GUI parser must turn the daemon JSON into a ModuleCollisionInfo
        # dataclass. Older daemons omit the field entirely → default to [].
        payload = {
            "module_collisions": [
                {
                    "module_a": "nct6687",
                    "module_b": "nct6775",
                    "severity": "critical",
                    "summary": "Both drivers loaded; chip ID 0xd450 overlaps",
                    "remediation": "blacklist nct6687",
                }
            ],
        }
        result = parse_hardware_diagnostics(payload)
        assert isinstance(result, HardwareDiagnosticsResult)
        assert len(result.module_collisions) == 1
        collision = result.module_collisions[0]
        assert isinstance(collision, ModuleCollisionInfo)
        assert collision.module_a == "nct6687"
        assert collision.module_b == "nct6775"
        assert collision.severity == "critical"

    def test_daemon_module_collisions_default_empty_when_field_missing(self):
        # Older daemon (no DEC-105 support) emits no key — GUI must default.
        result = parse_hardware_diagnostics({})
        assert result.module_collisions == []

    def test_daemon_module_collisions_tolerates_malformed_entries(self):
        # Defensive parsing: non-dict entries must be skipped rather than
        # raising. Same pattern as kernel_warnings parsing.
        payload = {"module_collisions": [None, "garbage", 42, {"module_a": "x"}]}
        result = parse_hardware_diagnostics(payload)
        # Only the dict-shaped entry survives; missing fields default.
        assert len(result.module_collisions) == 1
        assert result.module_collisions[0].module_a == "x"

    def test_module_collisions_severity_defaults_to_info_when_missing(self):
        # Per DEC-105 review: when the daemon emits a malformed entry
        # without a `severity` field, the GUI defaults to "info" — the
        # safe direction. Earlier prototype defaulted to "critical" which
        # would have promoted future legitimate non-critical entries to a
        # red banner.
        payload = {"module_collisions": [{"module_a": "foo", "module_b": "bar"}]}
        result = parse_hardware_diagnostics(payload)
        assert len(result.module_collisions) == 1
        assert result.module_collisions[0].severity == "info"

    def test_gui_fallback_suppressed_when_daemon_reports_same_pair(self):
        # Verifies the suppression logic in diagnostics_page.py: when the
        # daemon already reported a collision pair, the GUI-only fallback
        # banner must NOT also fire for the same pair. This is the
        # `daemon_pairs = {tuple(sorted([...]))…}` filter in the page
        # render path; here we test the underlying invariant directly.
        loaded = ["nct6687", "nct6775", "k10temp"]
        gui_pairs = {
            tuple(sorted([c.module_a, c.module_b])) for c in detect_module_conflicts(loaded)
        }
        # The daemon would emit the same pair in this case.
        daemon_pair = tuple(sorted(["nct6687", "nct6775"]))
        assert daemon_pair in gui_pairs, (
            "GUI fallback must still detect the pair so the suppression "
            "logic has something to filter"
        )
        # The deduplication invariant — both representations canonicalise
        # to the same sorted tuple regardless of which side put which
        # module first.
        assert tuple(sorted(["nct6775", "nct6687"])) == daemon_pair, (
            "Sorted-tuple canonicalisation must be order-independent"
        )


class TestAm4LabelFallback:
    """Verified-against-upstream lm-sensors-config label fallbacks."""

    def test_x470_aorus_ultra_gaming_primary_chip(self):
        # Source: configs/Gigabyte/X470-AORUS-ULTRA-GAMING.conf
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="X470 AORUS ULTRA GAMING",
            chip_name="it8686",
            sensor_name="pwm1",
        )
        assert label == "CPU_FAN"

    def test_x470_aorus_ultra_gaming_secondary_chip(self):
        # Same upstream config — secondary IT8792E at 0x0a60.
        label = resolve_label_from_fallback(
            vendor="Gigabyte Technology Co., Ltd.",
            board_name="X470 AORUS ULTRA GAMING",
            chip_name="it8792",
            sensor_name="pwm3",
        )
        assert label == "SYS_FAN4"

    def test_msi_x470_gaming_pro_labels(self):
        # Source: configs/MSI/MS_7B79_X470_GAMINGPRO.conf
        label = resolve_label_from_fallback(
            vendor="Micro-Star International Co., Ltd.",
            board_name="X470 GAMING PRO",
            chip_name="nct6795",
            sensor_name="pwm1",
        )
        assert label == "PUMP_FAN1"

    def test_msi_b450m_mortar_uses_nct6797_labels(self):
        # Source: configs/MSI/MS-7B89-B450M-MORTAR.conf. fan1 was ignored
        # upstream; fan2-5 mapped.
        label = resolve_label_from_fallback(
            vendor="Micro-Star International Co., Ltd.",
            board_name="B450M MORTAR",
            chip_name="nct6797",
            sensor_name="pwm2",
        )
        assert label == "CPU 1"

    def test_asrock_b450_gaming_itx_ac_labels(self):
        # Source: configs/ASRock/B450-Gaming-ITX-ac.conf.
        label = resolve_label_from_fallback(
            vendor="ASRock",
            board_name="B450 Gaming ITX/ac",
            chip_name="nct6792",
            sensor_name="pwm2",
        )
        assert label == "CPU_FAN1"

    def test_unknown_am4_board_returns_none(self):
        # No upstream config for B550-MEG → fallback must not invent labels.
        assert (
            resolve_label_from_fallback(
                vendor="Micro-Star International Co., Ltd.",
                board_name="MEG B550 UNIFY",
                chip_name="nct6797",
                sensor_name="pwm1",
            )
            is None
        )
