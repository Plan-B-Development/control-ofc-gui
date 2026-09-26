"""Tests for the sensor reading interpretation knowledge base."""

import pytest

from control_ofc.knowledge.sensor_knowledge import (
    SensorClassification,
    classify_sensor,
    format_sensor_tooltip,
    lookup_board_override,
)


class TestK10temp:
    def test_k10temp_tdie_high_confidence(self):
        c = classify_sensor("k10temp", "Tdie")
        assert c.source_class == "cpu_die"
        assert c.confidence == "high"
        assert "die" in c.display_description.lower()

    def test_the_tdie_note_does_not_contradict_the_recommendation(self):
        """`DC-i`: the preferred-CPU recommendation is the daemon's Tctl-first
        `default_cpu`, so the Tdie tooltip describes the reading, never "prefer"."""
        c = classify_sensor("k10temp", "Tdie")
        assert c.notes
        assert not any("prefer" in n.lower() for n in c.notes)
        assert any("offset" in n.lower() for n in c.notes)

    def test_k10temp_tctl_is_control_temp(self):
        c = classify_sensor("k10temp", "Tctl")
        assert c.source_class == "cpu_control"
        assert c.confidence == "high"
        assert "control" in c.display_description.lower()
        assert any("firmware" in n.lower() for n in c.notes)

    def test_k10temp_tccd_is_ccd(self):
        c = classify_sensor("k10temp", "Tccd1")
        assert c.source_class == "cpu_ccd"
        assert c.confidence == "high"
        assert "Tccd1" in c.display_description


class TestSbTsi:
    def test_sbtsi_temp_is_amd_tsi(self):
        c = classify_sensor("sbtsi_temp", "SB-TSI")
        assert c.source_class == "amd_tsi"
        assert c.confidence == "medium_high"
        assert "SB-TSI" in c.display_description
        assert len(c.notes) >= 1


class TestAmdGpu:
    def test_amdgpu_edge_junction_mem(self):
        edge = classify_sensor("amdgpu", "edge")
        assert edge.source_class == "gpu_edge"
        assert edge.confidence == "high"

        junction = classify_sensor("amdgpu", "junction")
        assert junction.source_class == "gpu_junction"
        assert junction.confidence == "high"
        assert any("hotspot" in n.lower() or "hottest" in n.lower() for n in junction.notes)

        mem = classify_sensor("amdgpu", "mem")
        assert mem.source_class == "gpu_memory"
        assert mem.confidence == "high"


class TestNct6683:
    def test_nct6683_amd_tsi_by_label(self):
        c = classify_sensor("nct6683", "AMD TSI Addr 98h")
        assert c.source_class == "amd_tsi"
        assert c.confidence == "medium_high"
        assert "AMD TSI Addr 98h" in c.display_description

    def test_nct6683_amd_tsi_by_temp_type(self):
        c = classify_sensor("nct6683", "temp7", temp_type=5)
        assert c.source_class == "amd_tsi"
        assert c.confidence == "medium_high"

    def test_nct6683_thermistor_by_type(self):
        c = classify_sensor("nct6683", "temp3", temp_type=4)
        assert c.source_class == "board_thermistor"
        assert c.confidence == "medium"

    def test_nct6683_diode_by_type(self):
        c = classify_sensor("nct6683", "temp2", temp_type=3)
        assert c.source_class == "thermal_diode"
        assert c.confidence == "medium"

    @pytest.mark.parametrize("chip", ["nct6683", "nct6686", "nct6687"])
    def test_a_peci_dimm_is_memory_not_the_cpu(self, chip):
        """`DC-f`: the kernel reports `PECI DIMM n` with temp_type 6, the PECI code."""
        c = classify_sensor(chip, "PECI DIMM 0", temp_type=6)
        assert c.source_class == "memory_dimm"
        # Presence: the CPU's own PECI channel, same type code, is still the CPU.
        assert classify_sensor(chip, "PECI 0.0", temp_type=6).source_class == "cpu_peci"

    def test_nct6683_virtual_is_low_confidence(self):
        c = classify_sensor("nct6683", "Virtual Temp 1")
        assert c.source_class == "virtual"
        assert c.confidence == "low"
        assert any("not a direct" in n.lower() for n in c.notes)


class TestNct6775:
    def test_nct6775_cputin_bogus_on_asus_nct6776(self):
        c = classify_sensor("nct6776", "CPUTIN", board_vendor="ASUSTeK COMPUTER INC.")
        assert c.source_class == "bogus"
        assert c.confidence == "low"
        assert any("unreli" in n.lower() or "not connected" in n.lower() for n in c.notes)

    def test_nct6775_cputin_normal_on_non_asus(self):
        c = classify_sensor("nct6776", "CPUTIN", board_vendor="Gigabyte")
        assert c.source_class == "cpu_board_side"
        assert c.confidence == "medium"

    @pytest.mark.parametrize(
        "chip",
        [
            "nct6775",
            "nct6779",
            "nct6791",
            "nct6792",
            "nct6793",
            "nct6795",
            "nct6796",
            "nct6797",
            "nct6798",
            "nct6799",
        ],
    )
    def test_every_nct67xx_sibling_is_bogus_on_asus(self, chip):
        """AUD-x: the quirk covers the whole nct67xx family, not just nct6776.

        The kernel's remedy is scoped to the BOARD ("The CPU temperature on ASUS
        boards is reported from PECI 0 or TSI 0") while the gate was scoped to
        one chip, so every sibling fell through and CPUTIN was presented as a
        trustworthy CPU reading. lm-sensors#283 is the nct6775 instance: CPUTIN
        123.5 C beside a coretemp Package id 0 of 42.0 C.

        This must stay in step with the daemon's `ASUS_CPUTIN_BOGUS_CHIPS` — a
        chip listed here alone gets a warning and no protection; one listed there
        alone is protected while this GUI still calls it trustworthy.
        """
        c = classify_sensor(chip, "CPUTIN", board_vendor="ASUSTeK COMPUTER INC.")
        assert c.source_class == "bogus"
        assert c.confidence == "low"

    @pytest.mark.parametrize("chip", ["nct6775", "nct6799"])
    def test_the_vendor_gate_survives_the_widening(self, chip):
        """Widening the CHIP set must not widen the BOARD set.

        Asserts the presence of the gate, not merely the absence of the quirk:
        on a non-ASUS board these chips keep their normal CPU classification, so
        a future edit that dropped the vendor condition would red here rather
        than silently discarding a real CPU sensor on every Gigabyte board.
        """
        c = classify_sensor(chip, "CPUTIN", board_vendor="Gigabyte")
        assert c.source_class == "cpu_board_side"


class TestAsusEc:
    def test_asus_ec_tsensor(self):
        c = classify_sensor("asus_ec_sensors", "T_Sensor")
        assert c.source_class == "external_probe"
        assert c.confidence == "high"

    def test_asus_ec_vrm(self):
        c = classify_sensor("asus_ec_sensors", "VRM")
        assert c.source_class == "vrm"
        assert c.confidence == "high"

    # `DC-e`: asus-ec-sensors.c names its water channels with underscores, and
    # the hwmon device is `asusec`. The old test pinned "Water In" on the module
    # name — a label and a chip the driver never emits — so the classifier's
    # spaced-only match passed while every real label fell to the medium hint.
    @pytest.mark.parametrize(
        ("label", "source_class", "place"),
        [
            ("Water_In", "coolant_in", "coolant inlet"),
            ("Water_Out", "coolant_out", "coolant outlet"),
            ("Water_Block_In", "coolant_in", "block inlet"),
            ("Water_Block_Out", "coolant_out", "block outlet"),
        ],
    )
    def test_the_driver_water_labels_classify_by_side(self, label, source_class, place):
        c = classify_sensor("asusec", label)
        assert c.source_class == source_class
        assert c.confidence == "high"
        assert place in c.display_description.lower()

    def test_block_and_loop_channels_read_differently(self):
        loop = classify_sensor("asusec", "Water_In")
        block = classify_sensor("asusec", "Water_Block_In")
        assert loop.source_class == block.source_class
        assert loop.display_description != block.display_description


class TestAsusWmiWater:
    def test_the_bios_spaced_water_labels_still_classify(self):
        """asus_wmi_sensors passes the BIOS name through, which is spaced."""
        assert classify_sensor("asus_wmi_sensors", "Water In").source_class == "coolant_in"
        assert classify_sensor("asus_wmi_sensors", "Water Out").source_class == "coolant_out"


class TestAsusWmi:
    def test_asus_wmi_with_polling_caveat(self):
        c = classify_sensor("asus_wmi_sensors", "CPU Temperature")
        assert c.source_class == "cpu_board_side"
        assert c.confidence == "medium_high"
        assert any("polling" in n.lower() for n in c.notes)


class TestGigabyteWmi:
    def test_gigabyte_wmi_low_confidence(self):
        c = classify_sensor("gigabyte_wmi", "temp1")
        assert c.source_class == "vendor_wmi_unlabeled"
        assert c.confidence == "low"
        assert any("label" in n.lower() for n in c.notes)


class TestIt87:
    def test_it87_generic_is_low_confidence(self):
        c = classify_sensor("it8688", "temp1")
        assert c.source_class == "super_io_channel"
        assert c.confidence == "low"
        assert any("placement" in n.lower() for n in c.notes)


class TestUnknownDriver:
    def test_unknown_driver_fallback(self):
        c = classify_sensor("some_mystery_chip", "SomeLabel")
        assert c.source_class == "unknown"
        assert c.confidence == "low"
        assert any("some_mystery_chip" in n for n in c.notes)


class TestBoardOverride:
    def test_board_override_lookup_match(self):
        override = lookup_board_override(
            "ASUSTeK COMPUTER INC.",
            "ROG CROSSHAIR VIII HERO",
            "T_Sensor",
        )
        assert override is not None
        assert override.source_class == "external_probe"
        # The Crosshair VIII T_Sensor override was softened by the
        # 2026-06-03 docs-correctness audit: the kernel asus_ec_sensors
        # doc confirms the channel exists but does NOT specify physical
        # pin location, and the location varies by SKU. The notes now
        # cite the ASUS board manual + the kernel allowlist URL instead
        # of claiming a specific header position.
        joined_notes = " ".join(override.notes).lower()
        assert "board manual" in joined_notes
        assert "asus_ec_sensors" in joined_notes

    def test_board_override_lookup_no_match(self):
        override = lookup_board_override(
            "Unknown Vendor",
            "Unknown Model",
            "temp1",
        )
        assert override is None


class TestFormatSensorTooltip:
    def test_format_sensor_tooltip_with_stats(self):
        c = SensorClassification(
            source_class="cpu_die",
            display_description="CPU die temperature (internal sensor)",
            confidence="high",
            notes=["Primary CPU temperature"],
        )
        tooltip = format_sensor_tooltip(
            c,
            sensor_id="k10temp-Tdie",
            chip_name="k10temp",
            session_min=35.0,
            session_max=72.5,
            rate_c_per_s=1.2,
        )
        assert "CPU die temperature" in tooltip
        assert "35.0" in tooltip
        assert "72.5" in tooltip
        assert "+1.2" in tooltip
        assert "Driver: k10temp" in tooltip
        assert "Confidence: High" in tooltip
        assert "Primary CPU temperature" in tooltip
        assert "k10temp-Tdie" in tooltip

    def test_format_sensor_tooltip_minimal(self):
        c = SensorClassification(
            source_class="unknown",
            display_description="Temperature sensor (temp1)",
            confidence="low",
        )
        tooltip = format_sensor_tooltip(c)
        assert "Temperature sensor (temp1)" in tooltip
        assert "Confidence: Low" in tooltip
        # No stats, no driver, no notes, no ID
        assert "Session:" not in tooltip
        assert "Rate:" not in tooltip
        assert "Driver:" not in tooltip
        assert "ID:" not in tooltip
