"""Tests for the sensor reading interpretation knowledge base."""

from control_ofc.ui.sensor_knowledge import (
    classify_sensor,
    format_sensor_tooltip,
    lookup_board_override,
    SensorClassification,
)


class TestK10temp:
    def test_k10temp_tdie_high_confidence(self):
        c = classify_sensor("k10temp", "Tdie")
        assert c.source_class == "cpu_die"
        assert c.confidence == "high"
        assert "die" in c.display_description.lower()

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

    def test_nct6683_virtual_is_low_confidence(self):
        c = classify_sensor("nct6683", "Virtual Temp 1")
        assert c.source_class == "virtual"
        assert c.confidence == "low"
        assert any("not a direct" in n.lower() for n in c.notes)


class TestNct6775:
    def test_nct6775_cputin_bogus_on_asus_nct6776(self):
        c = classify_sensor(
            "nct6776", "CPUTIN", board_vendor="ASUSTeK COMPUTER INC."
        )
        assert c.source_class == "bogus"
        assert c.confidence == "low"
        assert any("unreli" in n.lower() or "not connected" in n.lower() for n in c.notes)

    def test_nct6775_cputin_normal_on_non_asus(self):
        c = classify_sensor("nct6776", "CPUTIN", board_vendor="Gigabyte")
        assert c.source_class == "cpu_board_side"
        assert c.confidence == "medium"


class TestAsusEc:
    def test_asus_ec_tsensor(self):
        c = classify_sensor("asus_ec_sensors", "T_Sensor")
        assert c.source_class == "external_probe"
        assert c.confidence == "high"

    def test_asus_ec_vrm(self):
        c = classify_sensor("asus_ec_sensors", "VRM")
        assert c.source_class == "vrm"
        assert c.confidence == "high"

    def test_asus_ec_water_in_out(self):
        c_in = classify_sensor("asus_ec_sensors", "Water In")
        assert c_in.source_class == "coolant_in"
        assert c_in.confidence == "high"

        c_out = classify_sensor("asus_ec_sensors", "Water Out")
        assert c_out.source_class == "coolant_out"
        assert c_out.confidence == "high"


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
        assert "24-pin" in override.notes[0]

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
