"""DEC-492: memory-module sensors keep their id when the i2c bus renumbers.

The daemon names a memory module by its SMBus controller, port and SPD address
instead of the kernel's dynamic bus number. Tests here pin:

* the GUI's parser and cross-form rule agree with the daemon's, on the shared oracle;
* ids saved against the old form follow the module on the first poll — every
  sensor-keyed store, and profile curves — and only to a single other-form match;
* two modules at one address on different segments get different names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.api.models import SensorReading
from control_ofc.knowledge.memory_sensor_id import (
    parse_memory_sensor_id,
    resolve_memory_sensor_id,
)
from control_ofc.knowledge.sensor_knowledge import sensor_display_name
from control_ofc.services.memory_id_migration import (
    find_memory_id_moves,
    rekey_list,
    rekey_mapping,
)
from control_ofc.services.profile_service import CurveConfig, Profile

FIXTURE = Path(__file__).parent / "fixtures" / "memory_sensor_ids.json"
_ORACLE = json.loads(FIXTURE.read_text())
_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "memory_sensor_ids.json"
)

LEGACY = "hwmon:spd5118:21-0051:temp1"
STABLE = "hwmon:spd5118:0000:00:14.0-p0-0051:temp1"
STABLE_P2 = "hwmon:spd5118:0000:00:14.0-p2-0051:temp1"
CPU = "hwmon:k10temp:0000:00:18.3:Tctl"


def _dimm(sid: str) -> SensorReading:
    return SensorReading(
        id=sid, kind="mb_temp", label="temp1", value_c=40.0, source="hwmon", chip_name="spd5118"
    )


def _cpu() -> SensorReading:
    return SensorReading(
        id=CPU, kind="cpu_temp", label="Tctl", value_c=50.0, source="hwmon", chip_name="k10temp"
    )


# ── The shared oracle ────────────────────────────────────────────────────


@pytest.mark.parametrize("case", _ORACLE["parse"], ids=lambda c: c["id"])
def test_parse_matches_the_cross_stack_oracle(case):
    got = parse_memory_sensor_id(case["id"])
    want = case["expect"]
    if want is None:
        assert got is None
        return
    assert got is not None
    assert got.chip == want["chip"]
    assert got.legacy == want["legacy"]
    assert got.controller == want["controller"]
    assert list(got.segments) == want["segments"]
    assert got.address == want["address"]
    assert got.label == want["label"]


@pytest.mark.parametrize("case", _ORACLE["resolve"], ids=lambda c: c["why"])
def test_resolve_matches_the_cross_stack_oracle(case):
    got = resolve_memory_sensor_id(case["saved"], case["live"], case.get("unavailable", []))
    assert got == case["expect"]


def test_the_oracle_contains_real_moves_and_refusals():
    """Presence before absence: an oracle of ids that stay put proves nothing."""
    resolves = _ORACLE["resolve"]
    assert sum(1 for c in resolves if c["expect"] not in (None, c["saved"])) >= 2
    assert sum(1 for c in resolves if c["expect"] is None) >= 2


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_memory_sensor_id_fixture_copies_are_byte_identical():
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "memory_sensor_ids.json drifted between the GUI and daemon copies"
    )


# ── Naming ───────────────────────────────────────────────────────────────


class TestDisplayName:
    def test_both_forms_name_the_module_by_address(self):
        assert sensor_display_name(LEGACY, "temp1", peers=[LEGACY]) == "DIMM 0x51"
        assert sensor_display_name(STABLE, "temp1", peers=[STABLE]) == "DIMM 0x51"

    def test_same_address_on_two_segments_is_told_apart(self):
        peers = [STABLE, STABLE_P2]
        assert sensor_display_name(STABLE, "temp1", peers=peers) == "DIMM 0x51 (p0)"
        assert sensor_display_name(STABLE_P2, "temp1", peers=peers) == "DIMM 0x51 (p2)"

    def test_legacy_twins_are_told_apart_by_bus(self):
        a, b = "hwmon:spd5118:21-0051:temp1", "hwmon:spd5118:22-0051:temp1"
        assert sensor_display_name(a, "temp1", peers=[a, b]) == "DIMM 0x51 (bus 21)"

    def test_a_different_chip_at_the_same_address_is_not_a_twin(self):
        jc = "hwmon:jc42:0000:00:14.0-p2-0051:temp1"
        assert sensor_display_name(STABLE, "temp1", peers=[STABLE, jc]) == "DIMM 0x51"

    def test_a_mux_channel_is_named(self):
        a = "hwmon:spd5118:0000:00:1f.4-ch0-0050:temp1"
        b = "hwmon:spd5118:0000:00:1f.4-ch1-0050:temp1"
        assert sensor_display_name(b, "temp1", peers=[a, b]) == "DIMM 0x50 (ch1)"

    def test_the_series_panel_lists_twins_apart(self, qtbot):
        """The call site, not just the helper: a surface passes its peers."""
        from control_ofc.services.app_state import AppState
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=AppState())
        qtbot.addWidget(panel)
        panel.update_sensors([_dimm(STABLE), _dimm(STABLE_P2)])
        memory = panel._group_items["memory"]
        labels = sorted(memory.child(i).text(0) for i in range(memory.childCount()))
        assert labels == ["DIMM 0x51 (p0)", "DIMM 0x51 (p2)"]


# ── Re-keying the stores ─────────────────────────────────────────────────


class TestMoves:
    def test_a_legacy_id_moves_to_its_single_stable_match(self):
        assert find_memory_id_moves([LEGACY, CPU], [STABLE, CPU], []) == {LEGACY: STABLE}

    def test_ambiguity_moves_nothing(self):
        assert find_memory_id_moves([LEGACY], [STABLE, STABLE_P2], []) == {}

    def test_a_missing_stable_module_is_never_rebound_to_its_twin(self):
        assert find_memory_id_moves([STABLE_P2], [STABLE], []) == {}

    def test_a_quarantined_twin_blocks_the_move(self):
        """Review P2: the saved module (p0) quarantined must not hand its curve to
        the other DIMM at the same address. Opposite branch first: without the twin
        it moves."""
        assert find_memory_id_moves([LEGACY], [STABLE_P2], []) == {LEGACY: STABLE_P2}
        assert find_memory_id_moves([LEGACY], [STABLE_P2], [STABLE]) == {}

    def test_mapping_rekey_keeps_an_entry_already_at_the_live_key(self):
        colours = {f"sensor:{LEGACY}": "#111111", f"sensor:{STABLE}": "#222222", "fan:x": "#333"}
        out = rekey_mapping(colours, {LEGACY: STABLE}, series=True)
        assert out == {f"sensor:{STABLE}": "#222222", "fan:x": "#333"}

    def test_mapping_rekey_moves_a_lone_entry(self):
        out = rekey_mapping({LEGACY: "coolant"}, {LEGACY: STABLE}, series=False)
        assert out == {STABLE: "coolant"}

    def test_list_rekey_keeps_order_and_dedupes(self):
        out = rekey_list(
            [f"sensor:{LEGACY}", "fan:x", f"sensor:{STABLE}"], {LEGACY: STABLE}, series=True
        )
        assert out == [f"sensor:{STABLE}", "fan:x"]


class TestMainWindowHook:
    def _seed(self, settings_service, profile_service):
        settings_service.update(
            series_colors={f"sensor:{LEGACY}": "#123456"},
            hidden_chart_series=[f"sensor:{LEGACY}"],
            diagnostics_hidden_sensor_ids=[LEGACY],
            sensor_class_overrides={LEGACY: "coolant"},
        )
        profile = Profile(
            id="mem",
            name="Mem",
            curves=[CurveConfig(id="c", name="c", sensor_id=LEGACY)],
        )
        profile_service._profiles[profile.id] = profile
        profile_service.save_profile(profile)
        return profile

    def _window(self, qtbot, settings_service, profile_service, **kw):
        from control_ofc.ui.main_window import MainWindow

        w = MainWindow(settings_service=settings_service, profile_service=profile_service, **kw)
        qtbot.addWidget(w)
        return w

    def test_first_poll_re_keys_every_store_and_the_curve(
        self, qtbot, settings_service, profile_service
    ):
        self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service)
        w._state.set_sensors([_dimm(STABLE), _cpu()])

        s = settings_service.settings
        assert s.series_colors == {f"sensor:{STABLE}": "#123456"}
        assert s.hidden_chart_series == [f"sensor:{STABLE}"]
        assert s.diagnostics_hidden_sensor_ids == [STABLE]
        assert s.sensor_class_overrides == {STABLE: "coolant"}
        assert w._state.sensor_class_overrides == {STABLE: "coolant"}
        # Persisted, not just mutated: the reloaded profile carries the live id.
        assert profile_service.reload_profile("mem").curves[0].sensor_id == STABLE
        assert any("controller and address" in e.message for e in w._diag.events)

    def test_ambiguity_leaves_everything_alone(self, qtbot, settings_service, profile_service):
        self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service)
        w._state.set_sensors([_dimm(STABLE), _dimm(STABLE_P2), _cpu()])

        assert settings_service.settings.series_colors == {f"sensor:{LEGACY}": "#123456"}
        assert profile_service.get_profile("mem").curves[0].sensor_id == LEGACY

    def test_a_quarantined_twin_in_status_blocks_the_re_key(
        self, qtbot, settings_service, profile_service
    ):
        """The wiring of the P2 fix: the hook reads the daemon's
        ``unavailable_sensors[]``, not only the live set."""
        from control_ofc.api.models import DaemonStatus, UnavailableSensor

        self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service)
        w._state.set_status(
            DaemonStatus(
                unavailable_sensors=[
                    UnavailableSensor(id=STABLE, label="temp1", reason="read_error")
                ]
            )
        )
        w._state.set_sensors([_dimm(STABLE_P2), _cpu()])

        assert settings_service.settings.series_colors == {f"sensor:{LEGACY}": "#123456"}
        assert profile_service.get_profile("mem").curves[0].sensor_id == LEGACY

    def test_an_unpublished_draft_is_re_keyed_but_not_uploaded(
        self, qtbot, settings_service, profile_service, monkeypatch
    ):
        """Review P3: ProfileService promises no background sync of drafts."""
        profile = self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service)
        # A daemon-backed service holding this profile as an unpublished draft.
        monkeypatch.setattr(type(profile_service), "daemon_backed", property(lambda _s: True))
        monkeypatch.setattr(profile_service, "is_published", lambda _pid: False)
        saved: list[str] = []
        monkeypatch.setattr(profile_service, "save_profile", lambda p: saved.append(p.id))
        w._state.set_sensors([_dimm(STABLE), _cpu()])

        assert saved == []
        assert profile.curves[0].sensor_id == STABLE

    def test_demo_mode_never_re_keys(self, qtbot, settings_service, profile_service):
        self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service, demo_mode=True)
        w._state.set_sensors([_dimm(STABLE), _cpu()])

        assert settings_service.settings.series_colors == {f"sensor:{LEGACY}": "#123456"}

    def test_a_profile_with_unsaved_edits_is_not_published(
        self, qtbot, settings_service, profile_service, monkeypatch
    ):
        """Saving the profile the Controls page is editing would publish the user's
        unsaved edits with the re-key; it is re-keyed in memory only."""
        profile = self._seed(settings_service, profile_service)
        w = self._window(qtbot, settings_service, profile_service)
        monkeypatch.setattr(w.controls_page, "has_unsaved_changes", lambda: True)
        monkeypatch.setattr(type(w.controls_page), "viewed_profile_id", property(lambda _s: "mem"))
        saved: list[str] = []
        monkeypatch.setattr(profile_service, "save_profile", lambda p: saved.append(p.id))
        w._state.set_sensors([_dimm(STABLE), _cpu()])

        assert saved == []
        assert profile.curves[0].sensor_id == STABLE
