"""Tests for hwmon_label_resolver (A3).

Covers the libsensors mini-parser, the in-repo fallback table seeded for
the X870E AORUS MASTER, and the priority chain that combines them.

Reads only test-fixture files (via the ``sensors_paths`` override) — never
touches ``/etc/sensors.d`` or any system config.
"""

from __future__ import annotations

import pytest

from control_ofc.ui.hwmon_label_resolver import (
    HWMON_LABEL_FALLBACK,
    BoardKey,
    FallbackLabel,
    clear_libsensors_cache,
    parse_libsensors_config,
    resolve_hwmon_header_label,
    resolve_label_from_fallback,
    resolve_label_from_libsensors,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with a fresh module cache so an earlier test's
    libsensors load cannot leak into another's expectations."""
    clear_libsensors_cache()
    yield
    clear_libsensors_cache()


# ─── Parser ────────────────────────────────────────────────────────────


class TestParser:
    def test_simple_chip_block(self):
        text = """
        chip "it8688-isa-0a40"
            label fan1 "CPU_FAN"
            label fan2 "SYS_FAN1"
            label temp3 "VRM"
        """
        result = parse_libsensors_config(text)
        assert len(result) == 1
        assert result[0].chip_glob == "it8688-isa-0a40"
        assert result[0].labels == {
            "fan1": "CPU_FAN",
            "fan2": "SYS_FAN1",
            "temp3": "VRM",
        }
        assert result[0].ignored == set()

    def test_multiple_chip_blocks(self):
        text = """
        chip "it8688-isa-0a40"
            label fan1 "CPU_FAN"

        chip "it8792-isa-0a60"
            label fan1 "SYS_FAN5_PUMP"
        """
        result = parse_libsensors_config(text)
        assert len(result) == 2
        assert result[0].chip_glob == "it8688-isa-0a40"
        assert result[0].labels == {"fan1": "CPU_FAN"}
        assert result[1].chip_glob == "it8792-isa-0a60"
        assert result[1].labels == {"fan1": "SYS_FAN5_PUMP"}

    def test_multiple_globs_one_line(self):
        text = """
        chip "it8688-isa-*" "it8696-isa-*"
            label fan1 "CPU_FAN"
        """
        result = parse_libsensors_config(text)
        # Two chip blocks emitted; each carries the same labels.
        assert len(result) == 2
        assert {c.chip_glob for c in result} == {
            "it8688-isa-*",
            "it8696-isa-*",
        }
        for c in result:
            assert c.labels == {"fan1": "CPU_FAN"}

    def test_ignore_directive(self):
        text = """
        chip "it8688-isa-0a40"
            ignore in0
            ignore in1
            label fan1 "CPU_FAN"
        """
        result = parse_libsensors_config(text)
        assert result[0].ignored == {"in0", "in1"}
        assert result[0].labels == {"fan1": "CPU_FAN"}

    def test_comments_stripped(self):
        text = """
        # Top comment
        chip "it8688-isa-0a40"  # inline chip comment
            label fan1 "CPU_FAN"  # inline label comment
            # full-line comment
            label fan2 "SYS_FAN1"
        """
        result = parse_libsensors_config(text)
        assert result[0].labels == {"fan1": "CPU_FAN", "fan2": "SYS_FAN1"}

    def test_escaped_quotes_in_label(self):
        text = """
        chip "it8688-isa-0a40"
            label fan1 "CPU \\"Stock\\" Fan"
        """
        result = parse_libsensors_config(text)
        assert result[0].labels == {"fan1": 'CPU "Stock" Fan'}

    def test_blank_lines_and_whitespace(self):
        text = """

        chip "it8688-isa-0a40"

            label fan1 "CPU_FAN"



            label fan2 "SYS_FAN1"

        """
        result = parse_libsensors_config(text)
        assert result[0].labels == {"fan1": "CPU_FAN", "fan2": "SYS_FAN1"}

    def test_unrecognised_lines_ignored(self):
        text = """
        chip "it8688-isa-0a40"
            compute in0 @*2, @/2
            set in1_min 1.5
            label fan1 "CPU_FAN"
        """
        result = parse_libsensors_config(text)
        # compute and set are ignored — only the label survives.
        assert result[0].labels == {"fan1": "CPU_FAN"}

    def test_label_outside_chip_block_dropped(self):
        text = """
        label fan1 "Floating"
        chip "it8688-isa-0a40"
            label fan1 "CPU_FAN"
        """
        result = parse_libsensors_config(text)
        # The pre-chip label has nowhere to live — it's discarded.
        assert len(result) == 1
        assert result[0].labels == {"fan1": "CPU_FAN"}

    def test_empty_file(self):
        assert parse_libsensors_config("") == []

    def test_missing_trailing_newline(self):
        text = 'chip "it8688-isa-0a40"\n    label fan1 "CPU_FAN"'
        result = parse_libsensors_config(text)
        assert result[0].labels == {"fan1": "CPU_FAN"}


# ─── Libsensors-config-driven lookup ───────────────────────────────────


class TestLibsensorsLookup:
    def test_label_found_via_chip_name_match(self, tmp_path):
        cfg = tmp_path / "x870e.conf"
        cfg.write_text(
            'chip "it8696-isa-0a40"\n    label fan1 "CPU_FAN"\n    label fan2 "SYS_FAN1"\n'
        )
        label = resolve_label_from_libsensors("it8696", "fan1", paths=[str(cfg)])
        assert label == "CPU_FAN"

    def test_label_not_matched_when_chip_differs(self, tmp_path):
        cfg = tmp_path / "asus.conf"
        cfg.write_text('chip "nct6798-isa-0290"\n    label fan1 "CPU_FAN"\n')
        # Looking up the it8696's fan1 against an nct6798 config returns nothing.
        assert resolve_label_from_libsensors("it8696", "fan1", paths=[str(cfg)]) is None

    def test_ignore_directive_returns_none(self, tmp_path):
        cfg = tmp_path / "ignore.conf"
        cfg.write_text('chip "it8696-isa-0a40"\n    ignore fan1\n    label fan2 "SYS_FAN1"\n')
        # fan1 is ignored — no label.
        assert resolve_label_from_libsensors("it8696", "fan1", paths=[str(cfg)]) is None
        # fan2 still resolves.
        assert resolve_label_from_libsensors("it8696", "fan2", paths=[str(cfg)]) == "SYS_FAN1"

    def test_glob_chip_pattern(self, tmp_path):
        cfg = tmp_path / "glob.conf"
        cfg.write_text('chip "it8696-isa-*"\n    label fan1 "CPU_FAN"\n')
        assert resolve_label_from_libsensors("it8696", "fan1", paths=[str(cfg)]) == "CPU_FAN"

    def test_missing_path_no_crash(self, tmp_path):
        # A path that does not exist must not cause a crash; it is logged
        # and the function returns None.
        nonexistent = tmp_path / "nope.conf"
        assert resolve_label_from_libsensors("it8696", "fan1", paths=[str(nonexistent)]) is None

    def test_multiple_files_concat(self, tmp_path):
        a = tmp_path / "a.conf"
        a.write_text('chip "it8696-isa-0a40"\n    label fan1 "CPU_FAN"\n')
        b = tmp_path / "b.conf"
        b.write_text('chip "it87952-isa-0a60"\n    label fan1 "SYS_FAN4"\n')
        assert resolve_label_from_libsensors("it8696", "fan1", paths=[str(a), str(b)]) == "CPU_FAN"
        assert (
            resolve_label_from_libsensors("it87952", "fan1", paths=[str(a), str(b)]) == "SYS_FAN4"
        )


# ─── Fallback table ────────────────────────────────────────────────────


class TestFallbackTable:
    def test_x870e_aorus_master_it8696_verified(self):
        for sensor, expected in [
            ("pwm1", "CPU_FAN"),
            ("pwm2", "SYS_FAN1"),
            ("pwm3", "SYS_FAN2"),
            ("pwm4", "SYS_FAN3"),
            ("pwm5", "CPU_OPT"),
        ]:
            label = resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="X870E AORUS MASTER",
                chip_name="it8696",
                sensor_name=sensor,
            )
            assert label == expected, f"{sensor}: got {label!r}"

    def test_x870e_aorus_master_it87952_carries_unverified_suffix(self):
        for sensor in ("pwm1", "pwm2", "pwm3"):
            label = resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="X870E AORUS MASTER",
                chip_name="it87952",
                sensor_name=sensor,
            )
            assert label is not None
            assert label.endswith("(unverified)"), (
                f"{sensor}: {label!r} should be marked unverified"
            )

    def test_other_boards_return_none(self):
        # Different vendor.
        assert (
            resolve_label_from_fallback(
                vendor="ASUSTeK COMPUTER INC.",
                board_name="ROG STRIX X870E-E",
                chip_name="it8696",
                sensor_name="pwm1",
            )
            is None
        )
        # Same vendor, different board.
        assert (
            resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="X670E AORUS MASTER",
                chip_name="it8696",
                sensor_name="pwm1",
            )
            is None
        )
        # Same vendor + board, different chip not in our table.
        assert (
            resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="X870E AORUS MASTER",
                chip_name="amdgpu",
                sensor_name="pwm1",
            )
            is None
        )

    def test_unknown_sensor_returns_none(self):
        assert (
            resolve_label_from_fallback(
                vendor="Gigabyte Technology Co., Ltd.",
                board_name="X870E AORUS MASTER",
                chip_name="it8696",
                sensor_name="pwm99",
            )
            is None
        )

    def test_fallback_label_display_unverified_suffix(self):
        verified = FallbackLabel("CPU_FAN", verified=True)
        unverified = FallbackLabel("SYS_FAN4", verified=False)
        assert verified.display() == "CPU_FAN"
        assert unverified.display() == "SYS_FAN4 (unverified)"


# ─── Resolver priority chain ───────────────────────────────────────────


class TestResolverPriority:
    """Priority: sysfs > libsensors > fallback > raw pwmN."""

    def test_sysfs_label_wins(self, tmp_path):
        cfg = tmp_path / "x870e.conf"
        cfg.write_text('chip "it8696-isa-0a40"\n    label fan1 "FROM_SENSORS_CONF"\n')
        label = resolve_hwmon_header_label(
            sysfs_label="FROM_SYSFS",
            chip_name="it8696",
            pwm_index=1,
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            sensors_paths=[str(cfg)],
        )
        assert label == "FROM_SYSFS"

    def test_libsensors_wins_over_fallback(self, tmp_path):
        cfg = tmp_path / "x870e.conf"
        cfg.write_text('chip "it8696-isa-0a40"\n    label fan1 "USER_OVERRIDE"\n')
        # Empty sysfs label triggers the libsensors lookup. The
        # X870E AORUS MASTER fallback would normally produce "CPU_FAN"
        # for it8696/pwm1 — the user's config wins.
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="it8696",
            pwm_index=1,
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            sensors_paths=[str(cfg)],
        )
        assert label == "USER_OVERRIDE"

    def test_fallback_used_when_no_sysfs_or_libsensors(self):
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="it8696",
            pwm_index=1,
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            sensors_paths=[],  # No libsensors files.
        )
        assert label == "CPU_FAN"

    def test_fallback_unverified_carries_suffix(self):
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="it87952",
            pwm_index=1,
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            sensors_paths=[],
        )
        assert label.startswith("SYS_FAN4")
        assert label.endswith("(unverified)")

    def test_unknown_board_returns_raw_pwm_name(self):
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="nct6798",
            pwm_index=2,
            board_vendor="Some Vendor Inc.",
            board_name="Some Other Board",
            sensors_paths=[],
        )
        assert label == "pwm2"

    def test_libsensors_fan_name_preferred_over_pwm(self, tmp_path):
        """Communities universally write `label fanN` not `label pwmN`,
        so the resolver should accept either. The actual-fan name takes
        precedence over the pwm name."""
        cfg = tmp_path / "x870e.conf"
        cfg.write_text(
            'chip "it8696-isa-0a40"\n    label fan1 "FROM_FAN"\n    label pwm1 "FROM_PWM"\n'
        )
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="it8696",
            pwm_index=1,
            sensors_paths=[str(cfg)],
        )
        assert label == "FROM_FAN"

    def test_libsensors_pwm_name_used_when_fan_absent(self, tmp_path):
        cfg = tmp_path / "x870e.conf"
        cfg.write_text('chip "it8696-isa-0a40"\n    label pwm1 "FROM_PWM"\n')
        label = resolve_hwmon_header_label(
            sysfs_label="",
            chip_name="it8696",
            pwm_index=1,
            sensors_paths=[str(cfg)],
        )
        assert label == "FROM_PWM"


# ─── Cache behaviour ───────────────────────────────────────────────────


class TestCache:
    def test_default_paths_cached(self, tmp_path, monkeypatch):
        """Calling load_libsensors_configs() with no paths argument caches
        the result. Subsequent calls return the same list without re-reading."""
        from control_ofc.ui import hwmon_label_resolver as r

        cfg = tmp_path / "fake.conf"
        cfg.write_text('chip "it8696-isa-0a40"\n    label fan1 "CACHED_FAN"\n')
        monkeypatch.setattr(r, "LIBSENSORS_CONFIG_PATHS", [str(cfg)])

        first = r.load_libsensors_configs()
        # Replace the file content — without forcing, the cache wins.
        cfg.write_text('chip "it8696-isa-0a40"\n    label fan1 "REWRITTEN"\n')
        second = r.load_libsensors_configs()
        assert first == second  # cached
        assert second[0].labels["fan1"] == "CACHED_FAN"

        # Force re-read picks up the rewrite.
        third = r.load_libsensors_configs(force=True)
        assert third[0].labels["fan1"] == "REWRITTEN"

    def test_explicit_paths_bypass_cache(self, tmp_path):
        """Tests pass ``paths=`` so they never read or pollute the
        process cache."""
        from control_ofc.ui import hwmon_label_resolver as r

        a = tmp_path / "a.conf"
        a.write_text('chip "it8696-isa-0a40"\n    label fan1 "A_LABEL"\n')
        b = tmp_path / "b.conf"
        b.write_text('chip "it8696-isa-0a40"\n    label fan1 "B_LABEL"\n')

        first = r.load_libsensors_configs(paths=[str(a)])
        second = r.load_libsensors_configs(paths=[str(b)])
        assert first[0].labels["fan1"] == "A_LABEL"
        assert second[0].labels["fan1"] == "B_LABEL"


# ─── Defensive/structural ──────────────────────────────────────────────


class TestStructural:
    def test_fallback_table_has_x870e_master_keys(self):
        """Stable contract: the X870E AORUS MASTER seed must remain in
        the table — this is the primary user case. Catch accidental
        regression of the table during unrelated edits."""
        keys = list(HWMON_LABEL_FALLBACK)
        assert (
            BoardKey(
                vendor="Gigabyte Technology Co., Ltd.",
                board_glob="X870E AORUS MASTER",
                chip="it8696",
            )
            in keys
        )
        assert (
            BoardKey(
                vendor="Gigabyte Technology Co., Ltd.",
                board_glob="X870E AORUS MASTER",
                chip="it87952",
            )
            in keys
        )

    def test_x870e_master_it8696_full_set(self):
        """All 5 IT8696E PWM channels on X870E AORUS MASTER must have
        verified labels — no gaps in the silkscreen mapping."""
        key = BoardKey(
            vendor="Gigabyte Technology Co., Ltd.",
            board_glob="X870E AORUS MASTER",
            chip="it8696",
        )
        mapping = HWMON_LABEL_FALLBACK[key]
        assert set(mapping.keys()) == {"pwm1", "pwm2", "pwm3", "pwm4", "pwm5"}
        for entry in mapping.values():
            assert entry.verified, "IT8696E mappings on X870E MASTER are all verified"

    def test_x870e_master_it87952_full_set_unverified(self):
        key = BoardKey(
            vendor="Gigabyte Technology Co., Ltd.",
            board_glob="X870E AORUS MASTER",
            chip="it87952",
        )
        mapping = HWMON_LABEL_FALLBACK[key]
        assert set(mapping.keys()) == {"pwm1", "pwm2", "pwm3"}
        for entry in mapping.values():
            assert not entry.verified, "IT87952E mappings are best-guess"
