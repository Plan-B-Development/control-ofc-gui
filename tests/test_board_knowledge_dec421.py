"""Regression fixtures for the 2026-09-24 all-boards knowledge review (DEC-421).

Each class pins one correction the review applied to the knowledge tables, and
each is written so that reverting the correction turns it red. Where a table
feeds a rendered surface, the assertion is made on what the user sees, through
the production entry point — the review found four tables whose entries were
correct in isolation and unreachable in practice (keys that never matched a
real hwmon name or DMI board string).
"""

from __future__ import annotations

import re

import pytest

from control_ofc.knowledge.hwmon_label_resolver import (
    clear_libsensors_cache,
    resolve_hwmon_header_label,
)
from control_ofc.knowledge.sensor_knowledge import (
    classify_sensor,
    kernel_doc_url_for_chip,
    temp_type_label,
)
from control_ofc.ui.hwmon_guidance import (
    AMD_GPU_GUIDANCE_DB,
    CHIP_GUIDANCE_DB,
    CONFLICTING_MODULE_SETS,
    VENDOR_QUIRKS_DB,
    dual_chip_warning_html,
    lookup_amd_gpu_guidance,
    lookup_chip_guidance,
    lookup_vendor_quirks,
    verification_guidance,
)

ASUS = "ASUSTeK COMPUTER INC."
GIGABYTE = "Gigabyte Technology Co., Ltd."
MSI = "Micro-Star International Co., Ltd."


# ── Keys that never matched real hardware ─────────────────────────────────


class TestRealHwmonNamesReachTheTables:
    """The kernel names these hwmon devices `asusec`, `atk0110` and `sbtsi`.

    The tables were keyed on the MODULE names (`asus_ec_sensors`,
    `asus_atk0110`, `sbtsi_temp`), which no daemon ever reports, so the entries
    were dead. Each test asserts on the real name.
    """

    def test_asusec_fires_the_intel_quirk(self):
        quirks = lookup_vendor_quirks(
            ASUS, "asusec", cpu_vendor="Intel", board_name="ROG STRIX Z790-E GAMING WIFI II"
        )
        assert "asus-asusecsensors-intel-kernel-documented-allowlist" in {q.id for q in quirks}

    def test_asusec_fires_the_amd_x470_quirk_and_not_the_intel_one(self):
        quirks = lookup_vendor_quirks(ASUS, "asusec", cpu_vendor="AMD", board_name="PRIME X470-PRO")
        ids = {q.id for q in quirks}
        # Presence first: the lookup reached the table, so the absence below
        # is a real suppression and not an empty result.
        assert "asus-asusecsensors-prime-x470-pro" in ids
        assert "asus-asusecsensors-intel-kernel-documented-allowlist" not in ids

    def test_atk0110_fires_its_quirk(self):
        quirks = lookup_vendor_quirks(ASUS, "atk0110")
        assert "asus-asusatk0110-acpi-sensor-read" in {q.id for q in quirks}

    @pytest.mark.parametrize("name", ["asusec", "asus_ec_sensors", "atk0110", "asus_atk0110"])
    def test_chip_guidance_resolves_both_spellings(self, name):
        g = lookup_chip_guidance(name)
        assert g is not None, f"{name} falls through to 'Unknown chip'"
        assert g.driver_name in ("asus_ec_sensors", "asus_atk0110")

    def test_asusec_sensor_classifies_like_the_module_name_did(self):
        real = classify_sensor("asusec", "T_Sensor")
        legacy = classify_sensor("asus_ec_sensors", "T_Sensor")
        assert real.source_class == legacy.source_class
        assert real.source_class != classify_sensor("no_such_chip", "T_Sensor").source_class

    def test_sbtsi_classifies_as_amd_tsi(self):
        assert classify_sensor("sbtsi", "temp1").source_class == "amd_tsi"
        assert classify_sensor("sbtsi_temp", "temp1").source_class == "amd_tsi"

    @pytest.mark.parametrize("name", ["asusec", "atk0110", "sbtsi"])
    def test_doc_link_resolves_for_the_real_name(self, name):
        assert kernel_doc_url_for_chip(name) is not None


class TestTempTypeAbi:
    def test_seven_is_not_an_abi_value(self):
        # sysfs-class-hwmon defines 1-6 only; AMD TSI is 5. There is no 7.
        assert temp_type_label(7) == "unknown (7)"
        assert "AMD TSI" in temp_type_label(5)


class TestMainlineItePartsHaveEntries:
    """Mainline it87 parts that used to fall through to 'Unknown chip'."""

    @pytest.mark.parametrize("chip", ["it8603", "it8620", "it8628"])
    def test_entry_exists_and_is_mainline(self, chip):
        g = lookup_chip_guidance(chip)
        assert g is not None
        assert g.chip_prefix == chip, f"{chip} resolved to the broader {g.chip_prefix!r} entry"
        assert g.in_mainline is True


# ── Label table: keys must equal the DMI string the firmware reports ──────


@pytest.fixture
def no_libsensors(tmp_path):
    """A libsensors search path that exists nowhere, so only the in-repo table
    answers. (An empty list is NOT hermetic — the loader treats it as "use the
    system paths".)"""
    clear_libsensors_cache()
    yield [str(tmp_path / "absent.conf")]
    clear_libsensors_cache()


def _label(no_libsensors, vendor, board, chip, idx):
    return resolve_hwmon_header_label(
        sysfs_label=f"pwm{idx}",  # the daemon's synthesised placeholder
        chip_name=chip,
        pwm_index=idx,
        board_vendor=vendor,
        board_name=board,
        sensors_paths=no_libsensors,
    )


class TestLabelTableMatchesRealDmiNames:
    @pytest.mark.parametrize(
        ("vendor", "board", "chip", "idx", "expected"),
        [
            (GIGABYTE, "X470 AORUS ULTRA GAMING-CF", "it8686", 1, "CPU_FAN"),
            (GIGABYTE, "X470 AORUS ULTRA GAMING-CF", "it8792", 1, "SYS_FAN5_PUMP"),
            (MSI, "X470 GAMING PRO (MS-7B79)", "nct6795", 1, "PUMP_FAN1"),
            (MSI, "X470 GAMING PRO (MS-7B79)", "nct6795", 2, "CPU_FAN1"),
            ("ASRock", "B450 Gaming-ITX/ac", "nct6792", 2, "CPU_FAN1"),
            (GIGABYTE, "B550 VISION D-CF", "it8688", 1, "CPU_FAN"),
            (GIGABYTE, "B550 VISION D-CF", "it8792", 2, "SYS_FAN6_PUMP"),
        ],
    )
    def test_real_board_string_resolves(self, no_libsensors, vendor, board, chip, idx, expected):
        assert _label(no_libsensors, vendor, board, chip, idx) == expected

    @pytest.mark.parametrize(
        ("vendor", "board", "chip"),
        [
            # A different board whose name starts the same way: a wildcard key
            # would have handed it the X470 GAMING PRO's pump label.
            (MSI, "X470 GAMING PRO CARBON (MS-7B78)", "nct6795"),
            # Not described by the B550 VISION D config.
            (GIGABYTE, "B550 VISION D-P", "it8688"),
        ],
    )
    def test_neighbouring_boards_do_not_inherit_a_mapping(self, no_libsensors, vendor, board, chip):
        assert _label(no_libsensors, vendor, board, chip, 1) == "pwm1"

    def test_x870e_master_pump_labels_stay_unverified(self, no_libsensors):
        # The it87952 channel order has a conflicting source (it87 PR #100), so
        # the PUMP labels stay flagged until a per-channel test settles it.
        text = _label(no_libsensors, GIGABYTE, "X870E AORUS MASTER", "it87952", 1)
        assert text.startswith("SYS_FAN5_PUMP")
        assert "unverified" in text


class TestFixedKeysReachThePumpFloor:
    """The reason the key fixes matter is the 30% pump/CPU floor, so assert it at
    the call site rather than at the resolver (CLAUDE.md § Hard-won lessons,
    "extracting a rule into a testable function does NOT test the call site").

    `AppState.fan_fallback_name` is the production route that hands the daemon's
    DMI strings to the resolver; `infer_member_role` is what the floor is baked
    from. Before DEC-421 each pump/CPU header below resolved to a bare `pwmN` and
    classified as chassis (20%). The chassis rows are the opposite branch, so a
    classifier stuck on `cpu_or_pump` cannot pass.
    """

    @pytest.fixture
    def hermetic_libsensors(self, tmp_path, monkeypatch):
        # fan_fallback_name passes no sensors_paths, so the resolver falls back to
        # the SYSTEM list; pin that list to a path that exists nowhere, or a
        # developer's own /etc/sensors.d could answer first (register row BRD-p).
        from control_ofc.knowledge import hwmon_label_resolver

        monkeypatch.setattr(
            hwmon_label_resolver, "LIBSENSORS_CONFIG_PATHS", [str(tmp_path / "absent.conf")]
        )
        clear_libsensors_cache()
        yield
        clear_libsensors_cache()

    @pytest.mark.parametrize(
        ("vendor", "board", "chip", "idx", "role"),
        [
            (MSI, "X470 GAMING PRO (MS-7B79)", "nct6795", 1, "cpu_or_pump"),  # PUMP_FAN1
            (MSI, "X470 GAMING PRO (MS-7B79)", "nct6795", 2, "cpu_or_pump"),  # CPU_FAN1
            (MSI, "X470 GAMING PRO (MS-7B79)", "nct6795", 3, "chassis"),  # SYS_FAN1
            (GIGABYTE, "X470 AORUS ULTRA GAMING-CF", "it8792", 1, "cpu_or_pump"),  # SYS_FAN5_PUMP
            (GIGABYTE, "X470 AORUS ULTRA GAMING-CF", "it8792", 3, "chassis"),  # SYS_FAN4
            (GIGABYTE, "B550 VISION D-CF", "it8792", 2, "cpu_or_pump"),  # SYS_FAN6_PUMP
            ("ASRock", "B450 Gaming-ITX/ac", "nct6792", 2, "cpu_or_pump"),  # CPU_FAN1
            ("ASRock", "B450 Gaming-ITX/ac", "nct6792", 1, "chassis"),  # CHA_FAN1
        ],
    )
    def test_real_dmi_string_gives_the_header_its_floor_role(
        self, hermetic_libsensors, vendor, board, chip, idx, role
    ):
        from control_ofc.api.models import BoardInfo, HwmonHeader
        from control_ofc.services.app_state import AppState
        from control_ofc.services.profile_service import ControlMember, infer_member_role

        hid = f"hwmon:{chip}:{chip}.656:pwm{idx}:pwm{idx}"
        state = AppState()
        state.board_info = BoardInfo(vendor=vendor, name=board)
        state.set_hwmon_headers(
            [HwmonHeader(id=hid, label=f"pwm{idx}", chip_name=chip, pwm_index=idx)]
        )
        name = state.fan_fallback_name(hid)
        # Precondition: the board table answered, not the raw-id last resort.
        assert name != f"pwm{idx}", f"{board} {chip} pwm{idx} fell through to the raw id"
        member = ControlMember(source="hwmon", member_id=hid, member_label=name)
        assert infer_member_role(member) == role, f"{board} {chip} pwm{idx} -> {name!r}"


# ── Safety text ───────────────────────────────────────────────────────────


def _every_guidance_string() -> list[str]:
    out: list[str] = []
    for g in CHIP_GUIDANCE_DB:
        out += [*g.bios_tips, *g.known_issues, g.notes]
    for q in VENDOR_QUIRKS_DB:
        out += [q.summary, *q.details]
    for _a, _b, text in CONFLICTING_MODULE_SETS:
        out.append(text)
    for g in AMD_GPU_GUIDANCE_DB:
        out += [g.summary, *g.details]
    for result in ("pwm_enable_reverted", "no_rpm_effect", "pwm_value_clamped"):
        for vendor, chip in ((GIGABYTE, "it8696"), (GIGABYTE, "it8689"), (MSI, "nct6687")):
            out.append(verification_guidance(result, vendor, chip) or "")
    out.append(dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"]))
    return out


class TestNoZeroPercentCurveRecipe:
    """A BIOS curve runs the fans at boot and whenever the daemon is not in
    control, so no guidance may publish one with a 0% point (DEC-421)."""

    RECIPES = (
        re.compile(r"0% pwm except", re.I),
        re.compile(r"degenerate (fan )?curve", re.I),
        re.compile(r"all temperature points identical", re.I),
    )

    def test_sweep_is_not_vacuous(self):
        strings = _every_guidance_string()
        assert len(strings) > 200
        # The one surviving recipe (the pre-PR #128 stopgap) must still be
        # findable, so the sweep below is reading the right text.
        assert any("40,40,40,40,40,40,100" in s for s in strings)

    def test_no_zero_percent_recipe_anywhere(self):
        for text in _every_guidance_string():
            for pattern in self.RECIPES:
                if pattern.search(text):
                    # Only as a thing NOT to do.
                    assert re.search(r"never|not|no longer|gone", text, re.I), (
                        f"recipe {pattern.pattern!r} published as advice: {text[:160]!r}"
                    )


class TestFullSpeedIsAFailSafeNotAFix:
    """BRD-16: a header's BIOS 'Full Speed' makes the firmware run it at 100%
    while it owns the fan. It is not a way to make Linux control work, and on
    some boards it locks manual mode out (it87 #115). Every string that names it
    must carry that caveat."""

    CAVEAT = re.compile(r"fail-safe|not a fix|can also stop linux", re.I)

    def test_every_full_speed_mention_carries_the_caveat(self):
        mentions = [s for s in _every_guidance_string() if re.search(r"full speed", s, re.I)]
        # Presence first: the Gigabyte entries still name it.
        assert len(mentions) >= 3
        for text in mentions:
            # The ASUS note quotes firmware switching a header *to* full speed —
            # an observation, not advice.
            if "pwm_enable 1) to full speed" in text:
                continue
            assert self.CAVEAT.search(text), f"Full Speed offered as a fix: {text[:160]!r}"


class TestForceOneIsNamed:
    """nct6687d PR #174: `force=1` attaches to any chip in 0xD000-0xDFFF, which
    re-opens the NCT679x collision that PR #164 closed by default."""

    @pytest.mark.parametrize("chip", ["nct6797", "nct6798"])
    def test_collision_quirk_names_force(self, chip):
        crit = [q for q in lookup_vendor_quirks(MSI, chip) if q.consequence == "hardware_damage"]
        assert crit, f"precondition: the {chip} collision quirk fires"
        assert all("force=1" in " ".join(q.details) for q in crit)

    def test_gui_fallback_collision_text_names_force(self):
        text = next(t for a, b, t in CONFLICTING_MODULE_SETS if {a, b} == {"nct6687", "nct6775"})
        assert "force=1" in text
        # The board it used to cite is an NCT6795D, not an NCT6797D.
        assert "X470 GAMING PRO CARBON" not in text


class TestDualChipAlert:
    def test_single_chip_board_is_not_called_dual_chip(self):
        html = dual_chip_warning_html("X870E AORUS ELITE WIFI7", ["it8696"], [])
        assert html is not None
        assert "Dual-chip" not in html
        assert "1 ITE Super-IO chip" in html

    def test_dual_chip_board_keeps_its_heading(self):
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None and "Dual-chip" in html

    def test_renamed_v2_chips_are_explained_as_a_false_alarm(self):
        html = dual_chip_warning_html(
            "X870E AORUS MASTER", ["it8696", "it87952"], ["it8696_a008090a", "it87952_a008090a"]
        )
        # The exact comparison still reports them missing (BRD-a) — so the copy
        # must say what a suffixed name means.
        assert html is not None
        assert "it8696_a008090a" in html
        assert "2026-09-09" in html


class TestGpuAdvisoryGuidance:
    def test_rdna_hang_does_not_send_users_to_eol_kernels(self):
        g = lookup_amd_gpu_guidance("rdna_hang_kernel_6_18_6_19")
        assert g is not None
        flat = " ".join([g.summary, *g.details])
        assert "never" in flat and "longterm" in flat
        assert not re.search(r"(pin|roll back) to a 6\.15", flat, re.I)
        assert "6.18.7" in flat, "the bisected fix must be named"

    def test_smu_message_is_not_presented_as_the_fault(self):
        g = lookup_amd_gpu_guidance("smu_mismatch_navi48_r9700")
        assert g is not None
        flat = " ".join([g.summary, *g.details])
        assert "not a fault" in flat
        assert "e471627d5627" in flat


class TestNewQuirkEntries:
    def test_asus_nct6799_quirk_is_promotable_by_a_reclaim(self):
        q = next(q for q in VENDOR_QUIRKS_DB if q.id == "asus-nct6799-am5-and-z890")
        assert q.consequence == "control_loss" and q.trigger == "bios_revert"
        assert q in lookup_vendor_quirks(ASUS, "nct6799")
        assert "NCT6701D" in " ".join(q.details)
