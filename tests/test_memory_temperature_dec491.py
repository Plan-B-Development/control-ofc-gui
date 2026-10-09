"""DEC-491: memory (DIMM) temperatures are recognised, named and grouped.

Two DDR5 modules publish `hwmon:spd5118:<bus>-<addr>:temp1` with the label
`temp1`, `kind: mb_temp`. Before DEC-491 the GUI filed them under Motherboard as
two identical low-confidence `? temp1` rows, and could pick one as the default
"motherboard" chart line.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    DaemonStatus,
    SensorReading,
    SensorThresholds,
    UnavailableSensor,
)
from control_ofc.knowledge.sensor_knowledge import (
    MEMORY_SOURCE_CLASSES,
    classify_sensor,
    kernel_doc_url_for_chip,
    sensor_display_name,
    sensor_is_memory,
    trusted_crit_c,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.controls_view import build_sensor_choices, sensor_combo_label
from control_ofc.services.demo_service import DemoService
from control_ofc.services.overview_view import (
    build_sensor_rows,
    build_sensor_summary,
    is_alarm_active,
)
from control_ofc.services.series_selection import SeriesSelectionModel, default_series_keys
from control_ofc.ui.widgets.sensor_detail_dialog import build_sensor_detail_html
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from tests.test_overview_page import _page

DIMM_A = "hwmon:spd5118:21-0051:temp1"
DIMM_B = "hwmon:spd5118:21-0053:temp1"
BOARD = "hwmon:nct6798:nct6775.656:SYSTIN"


def _dimm(sid: str = DIMM_A, *, value_c: float = 37.5, **kw) -> SensorReading:
    return SensorReading(
        id=sid,
        kind="mb_temp",
        label="temp1",
        value_c=value_c,
        source="hwmon",
        age_ms=kw.get("age_ms", 300),
        chip_name=kw.get("chip_name", "spd5118"),
        thresholds=kw.get("thresholds"),
    )


def _board(sid: str = BOARD, *, label: str = "SYSTIN", chip: str = "nct6798") -> SensorReading:
    return SensorReading(
        id=sid, kind="mb_temp", label=label, value_c=31.0, source="hwmon", chip_name=chip
    )


def _cpu() -> SensorReading:
    return SensorReading(
        id="hwmon:k10temp:0000:00:18.3:Tctl",
        kind="cpu_temp",
        label="Tctl",
        value_c=48.0,
        source="hwmon",
        chip_name="k10temp",
    )


# ─── classification ─────────────────────────────────────────────────────


class TestClassification:
    @pytest.mark.parametrize(("chip", "confidence"), [("spd5118", "high"), ("jc42", "medium")])
    def test_memory_module_chips_are_memory(self, chip, confidence):
        # Pre-fix: the unknown-driver fallback, confidence "low" (the `?` row).
        # jc42 is medium: its driver also binds standalone thermometers.
        c = classify_sensor(chip, "temp1")
        assert c.source_class == "memory_dimm"
        assert c.confidence == confidence

    @pytest.mark.parametrize(
        ("chip", "label"),
        [
            ("nct6683", "PECI DIMM 0"),
            ("nct6686", "DIMM 1"),
            # Pre-fix: the nct6775 family's generic `super_io_channel`.
            ("nct6779", "PCH_DIM0_TEMP"),
            ("nct6792", "PCH_DIM3_TEMP"),
            ("nct6793", "Agent0 Dimm0 "),  # the driver's trailing space
            ("nct6798", "Agent1 Dimm1"),
            # Pre-fix: the unknown-driver fallback.
            ("dell_smm", "SODIMM"),
        ],
    )
    def test_board_memory_channels_are_memory_by_label(self, chip, label):
        c = classify_sensor(chip, label)
        assert c.source_class == "memory_dimm"
        assert c.confidence == "medium"

    @pytest.mark.parametrize(
        ("chip", "label"),
        [("nct6779", "PCH_CHIP_TEMP"), ("nct6798", "SYSTIN"), ("dell_smm", "Ambient")],
    )
    def test_non_memory_channels_are_not_memory(self, chip, label):
        assert classify_sensor(chip, label).source_class not in MEMORY_SOURCE_CLASSES

    def test_sensor_is_memory_follows_the_classifier(self):
        assert sensor_is_memory(_dimm())
        assert not sensor_is_memory(_board())
        assert not sensor_is_memory(None)

    @pytest.mark.parametrize(
        ("chip", "page"),
        [("spd5118", "spd5118.html"), ("jc42", "jc42.html"), ("dell_smm", "dell-smm-hwmon.html")],
    )
    def test_driver_doc_links(self, chip, page):
        assert kernel_doc_url_for_chip(chip) == f"https://docs.kernel.org/hwmon/{page}"


# ─── naming ─────────────────────────────────────────────────────────────


class TestDisplayName:
    def test_two_dimms_get_different_names_from_their_spd_address(self):
        assert sensor_display_name(DIMM_A, "temp1", peers=[]) == "DIMM 0x51"
        assert sensor_display_name(DIMM_B, "temp1", peers=[]) == "DIMM 0x53"

    def test_ddr4_jc42_address(self):
        assert sensor_display_name("hwmon:jc42:0-0018:temp1", "temp1", peers=[]) == "DIMM 0x18"

    def test_the_bus_number_is_not_part_of_the_name(self):
        # The kernel numbers i2c buses dynamically; the address is what stays.
        assert sensor_display_name("hwmon:spd5118:3-0051:temp1", "temp1", peers=[]) == (
            sensor_display_name(DIMM_A, "temp1", peers=[])
        )

    @pytest.mark.parametrize(
        "sid",
        [
            "hwmon:spd5118:nodev:temp1",  # no i2c device id
            "hwmon:spd5118:21-zz:temp1",  # unparsable address
            "spd5118:21-0051:temp1",  # not the documented shape
        ],
    )
    def test_unparsable_memory_id_keeps_the_plain_name(self, sid):
        assert sensor_display_name(sid, "temp1", peers=[]) == "temp1"

    def test_every_other_sensor_keeps_label_or_id(self):
        # The whole demo population (DIMMs excepted) plus labelless sensors: the
        # helper must be `label or id` for each, byte for byte.
        demo = [s for s in DemoService().sensors() if s.chip_name not in ("spd5118", "jc42")]
        assert demo
        others = [
            *demo,
            SensorReading(id="hwmon:nct6683:nct6683.2592:DIMM 0", label="DIMM 0"),
            SensorReading(id="hwmon:amdgpu:0000:03:00.0:edge", label=""),
            SensorReading(id="", label=""),
        ]
        for s in others:
            assert sensor_display_name(s.id, s.label, peers=[]) == (s.label or s.id), s.id


# ─── surfaces ───────────────────────────────────────────────────────────


class TestSeriesPanel:
    def test_dimms_file_under_memory_with_distinct_names(self, qtbot):
        panel = SensorSeriesPanel(SeriesSelectionModel(), state=AppState())
        qtbot.addWidget(panel)
        panel.update_sensors([_board(), _dimm(DIMM_A), _dimm(DIMM_B)])

        memory = panel._group_items["memory"]
        names = sorted(memory.child(i).text(0) for i in range(memory.childCount()))
        assert names == ["DIMM 0x51", "DIMM 0x53"]
        assert memory.text(0) == "Memory (2)"
        mb = panel._group_items["mb"]
        assert [mb.child(i).text(0) for i in range(mb.childCount())] == ["SYSTIN"]
        assert mb.text(0) == "Motherboard (1)"

    def test_memory_group_sits_after_motherboard(self, qtbot):
        panel = SensorSeriesPanel(SeriesSelectionModel(), state=AppState())
        qtbot.addWidget(panel)
        panel.update_sensors([_dimm(DIMM_A), _board()])
        tree = panel._tree
        order = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        assert order.index(panel._group_items["mb"]) < order.index(panel._group_items["memory"])


class TestDefaultSeries:
    def test_a_dimm_listed_first_never_fills_the_motherboard_slot(self):
        keys = default_series_keys([_cpu(), _dimm(DIMM_A), _board()])
        assert f"sensor:{BOARD}" in keys
        assert f"sensor:{DIMM_A}" not in keys

    def test_only_dimms_leaves_the_motherboard_slot_empty(self):
        keys = default_series_keys([_cpu(), _dimm(DIMM_A), _dimm(DIMM_B)])
        assert keys == {f"sensor:{_cpu().id}"}


class TestOverview:
    def test_rows_name_each_dimm_without_the_low_confidence_mark(self):
        rows = build_sensor_rows(
            [_dimm(DIMM_A), _dimm(DIMM_B)], classify=AppState().classify_sensor
        )
        assert [r.label for r in rows] == ["DIMM 0x51", "DIMM 0x53"]
        assert not any(r.is_low_confidence for r in rows)

    def test_summary_counts_memory_apart_from_the_board(self):
        text = build_sensor_summary(
            [_cpu(), _board(), _dimm(DIMM_A), _dimm(DIMM_B)],
            hidden_count=0,
            unavailable_count=0,
            classify=AppState().classify_sensor,
        )
        assert "1 board" in text
        assert "2 memory" in text

    def test_unavailable_dimm_is_listed_by_its_name(self, qtbot):
        page, _ = _page(qtbot)
        page._on_status(
            DaemonStatus(
                unavailable_sensors=[
                    UnavailableSensor(
                        id=DIMM_A,
                        label="temp1",
                        reason="temperature sensor disabled",
                        unavailable_for_ms=5000,
                    )
                ]
            )
        )
        assert "DIMM 0x51 — temperature sensor disabled" in page._unavailable_label.text()


class TestControlsPicker:
    def test_combo_label_names_the_dimm_and_says_memory(self):
        assert sensor_combo_label(_dimm(DIMM_A), {}, peers=[]).startswith("DIMM 0x51 (memory)")
        assert sensor_combo_label(_board(), {}, peers=[]).startswith("SYSTIN (board)")
        assert sensor_combo_label(_cpu(), {}, peers=[]).startswith("★ Tctl (CPU)")

    def test_an_unlisted_kind_shows_as_sent(self):
        s = SensorReading(id="x", kind="future_temp", label="X", value_c=None)
        assert sensor_combo_label(s, {}, peers=[]) == "X (future_temp)"

    def test_aio_wizard_choices_carry_the_name(self):
        labels = [c["label"] for c in build_sensor_choices([_dimm(DIMM_A), _board()], {})]
        assert labels == ["DIMM 0x51", "SYSTIN"]


class TestAlertName:
    def test_stale_dimm_alert_names_the_module(self):
        state = AppState()
        state.set_sensors([_dimm(DIMM_A, age_ms=60_000)])
        titles = [o.title for o in state.alerts.present()]
        assert any("DIMM 0x51" in t for t in titles), titles


# ─── the crit guard (Q-f) ───────────────────────────────────────────────


class TestCritGuard:
    @pytest.mark.parametrize(
        "thresholds",
        [
            SensorThresholds(max_c=55.0, crit_c=-0.25),  # an all-ones read
            SensorThresholds(max_c=55.0, crit_c=0.0),
            SensorThresholds(max_c=55.0, crit_c=30.0),  # below its own max
        ],
    )
    def test_an_implausible_module_crit_latches_no_alarm(self, thresholds):
        s = _dimm(value_c=37.5, thresholds=thresholds)
        assert trusted_crit_c(s) is None
        assert not is_alarm_active(s)
        html = build_sensor_detail_html(
            s, None, classification=AppState().classify_sensor(s), peers=[s.id]
        )
        assert "exceeds crit" not in html
        assert "not a plausible module limit" in html

    def test_the_same_limit_on_a_board_chip_is_used_as_reported(self):
        # Opposite branch: the guard is for memory modules only.
        s = _dimm(value_c=37.5, chip_name="nct6798", thresholds=SensorThresholds(crit_c=-0.25))
        assert trusted_crit_c(s) == -0.25
        assert is_alarm_active(s)

    def test_a_real_module_crit_still_alarms(self):
        s = _dimm(value_c=90.0, thresholds=SensorThresholds(max_c=55.0, crit_c=85.0))
        assert trusted_crit_c(s) == 85.0
        assert is_alarm_active(s)
        html = build_sensor_detail_html(
            s, None, classification=AppState().classify_sensor(s), peers=[s.id]
        )
        assert "exceeds crit" in html


# ─── demo mode ──────────────────────────────────────────────────────────


def test_demo_mode_shows_a_ddr5_pair_under_memory():
    sensors = DemoService().sensors()
    memory = [s for s in sensors if sensor_is_memory(s)]
    assert sorted(sensor_display_name(s.id, s.label, peers=[]) for s in memory) == [
        "DIMM 0x51",
        "DIMM 0x53",
    ]
    # And the demo's default chart keeps its board line.
    keys = default_series_keys(sensors)
    assert not any(f"sensor:{s.id}" in keys for s in memory)
    assert any(s.kind == "mb_temp" and f"sensor:{s.id}" in keys for s in sensors if s not in memory)
