"""Pruning chart settings for hardware the daemon no longer reports (DEC-246).

The headless rule plus the Settings button that drives it. The dangerous
direction here is deleting too much, not too little, so most of these pin
things the prune must *refuse* to do.
"""

from __future__ import annotations

import json

import pytest

from control_ofc.services.orphan_prune import (
    OrphanReport,
    find_orphans,
    live_series_keys,
)

REAL_FAN = "fan:openfan:ch00:rpm"
REAL_SENSOR = "sensor:hwmon:k10temp:0000:00:18.3:Tccd1"
DEAD_GPU = "fan:amd_gpu:0000:2d:00.0:rpm"  # a demo id, wrong PCI address
DEAD_SENSOR = "sensor:hwmon:z53:usb-3-2:Coolant"


class TestFindOrphans:
    def test_empty_known_set_prunes_nothing(self):
        """The single most important case. A disconnected GUI, or one asked
        before its first poll, knows of no hardware — and "remove everything the
        daemon did not mention" would then wipe the entire chart configuration."""
        report = find_orphans([REAL_FAN, DEAD_GPU], {REAL_SENSOR: "#ff0000"}, set())
        assert report.total == 0
        assert not report

    def test_separates_live_keys_from_dead_ones(self):
        known = live_series_keys(["openfan:ch00"], ["hwmon:k10temp:0000:00:18.3:Tccd1"])
        report = find_orphans(
            [REAL_FAN, DEAD_GPU, DEAD_SENSOR],
            {REAL_SENSOR: "#ff0000", DEAD_SENSOR: "#00ff00"},
            known,
        )
        assert report.hidden_series == sorted([DEAD_GPU, DEAD_SENSOR])
        assert report.series_colors == [DEAD_SENSOR]
        assert report.total == 3

    def test_quarantined_sensors_are_not_orphans(self):
        """DEC-193 evicts a sensor that fails every read from the live list. A
        known-set built from `sensors` alone would drop the colour of a WiFi
        temperature every time the radio is off."""
        wifi = "hwmon:ath12k:0000:03:00.0:temp1"
        known = live_series_keys(["openfan:ch00"], [], unavailable_sensor_ids=[wifi])
        report = find_orphans([f"sensor:{wifi}"], {f"sensor:{wifi}": "#ff0000"}, known)
        assert report.total == 0

    def test_key_shapes_match_the_series_model(self):
        """A mismatch here would report live hardware as orphaned — the failure
        mode is silent deletion, so the shapes are pinned explicitly."""
        assert live_series_keys(["openfan:ch00"], ["hwmon:k10temp:0:Tctl"]) == {
            "fan:openfan:ch00:rpm",
            "sensor:hwmon:k10temp:0:Tctl",
        }

    def test_report_is_falsy_when_empty(self):
        assert not OrphanReport()
        assert OrphanReport(hidden_series=["x"])


class TestSettingsPruneButton:
    @pytest.fixture()
    def page(self, qtbot, app_state, settings_service):
        from control_ofc.api.models import ConnectionState, DaemonStatus, FanReading, SensorReading
        from control_ofc.ui.pages.settings_page import SettingsPage

        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(DaemonStatus(overall_status="ok"))
        app_state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=10)])
        app_state.set_sensors(
            [
                SensorReading(
                    id="hwmon:k10temp:0000:00:18.3:Tccd1",
                    kind="CpuTemp",
                    label="Tccd1",
                    value_c=45.0,
                    source="hwmon",
                    age_ms=10,
                )
            ]
        )
        p = SettingsPage(state=app_state, settings_service=settings_service)
        qtbot.addWidget(p)
        return p

    def test_button_counts_orphans_and_disables_when_clean(self, page, settings_service):
        settings_service.update(hidden_chart_series=[REAL_FAN], series_colors={})
        page._refresh_reset_buttons()
        assert not page._prune_orphans_btn.isEnabled()
        assert page._prune_orphans_btn.text() == "Remove"

        settings_service.update(hidden_chart_series=[REAL_FAN, DEAD_GPU])
        page._refresh_reset_buttons()
        assert page._prune_orphans_btn.isEnabled()
        assert page._prune_orphans_btn.text() == "Remove (1)"

    def test_clicking_removes_only_the_dead_entries(self, page, settings_service, tmp_path):
        settings_service.update(
            hidden_chart_series=[REAL_FAN, DEAD_GPU, DEAD_SENSOR],
            series_colors={REAL_SENSOR: "#ff0000", DEAD_SENSOR: "#00ff00"},
        )
        page._refresh_reset_buttons()
        page._prune_orphans_btn.click()

        s = settings_service.settings
        assert s.hidden_chart_series == [REAL_FAN]
        assert s.series_colors == {REAL_SENSOR: "#ff0000"}
        # …and it reached disk, not just memory.
        on_disk = json.loads((tmp_path / "control-ofc" / "app_settings.json").read_text())
        assert on_disk["hidden_chart_series"] == [REAL_FAN]

    def test_disconnected_page_never_prunes(self, qtbot, app_state, settings_service):
        """No poll yet means no known hardware, which must not read as
        'everything is orphaned'."""
        from control_ofc.ui.pages.settings_page import SettingsPage

        settings_service.update(hidden_chart_series=[REAL_FAN, DEAD_GPU])
        p = SettingsPage(state=app_state, settings_service=settings_service)
        qtbot.addWidget(p)

        assert not p._prune_orphans_btn.isEnabled()
        p._prune_chart_orphans()
        assert settings_service.settings.hidden_chart_series == [REAL_FAN, DEAD_GPU]

    def test_fan_aliases_are_never_touched(self, page, settings_service):
        """DEC-237's Fan Names card lists an alias whose fan is absent precisely
        so the user can clear it, and the alias becomes ControlMember.member_label,
        which selects the 30% CPU/pump floor. Not this button's business."""
        settings_service.update(
            fan_aliases={"openfan:ch00": "Front", "openfan:ch99": "Long Gone"},
            hidden_chart_series=[DEAD_GPU],
        )
        page._refresh_reset_buttons()
        page._prune_orphans_btn.click()

        assert settings_service.settings.fan_aliases == {
            "openfan:ch00": "Front",
            "openfan:ch99": "Long Gone",
        }

    def test_prune_syncs_the_live_selection_model(self, qtbot, app_state, settings_service):
        """Without this the chart keeps hiding a series whose saved entry was just
        removed, and the next selection_changed writes it straight back."""
        from control_ofc.api.models import ConnectionState, DaemonStatus, FanReading
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.pages.settings_page import SettingsPage

        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(DaemonStatus(overall_status="ok"))
        app_state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=10)])

        settings_service.update(hidden_chart_series=[REAL_FAN, DEAD_GPU])
        model = SeriesSelectionModel()
        model.load_hidden([REAL_FAN, DEAD_GPU])
        p = SettingsPage(state=app_state, settings_service=settings_service, series_selection=model)
        qtbot.addWidget(p)
        p._prune_chart_orphans()

        assert DEAD_GPU not in model.to_dict()["hidden_keys"]
