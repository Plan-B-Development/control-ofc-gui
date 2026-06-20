"""Phase 4 (DEC-179): FanGroupCard / FanTile renderers over the fan_grouping VM.

These assert *outcomes* — rendered text, state-chip class, objectNames, and that
the rename/assign actions drive the canonical AppState flows — not just clicks.
The widgets are pure renderers: every value here is supplied as a view-model.
"""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLabel

from control_ofc.services.app_state import AppState
from control_ofc.services.fan_grouping import FanGroupVM, FanState, FanTileVM
from control_ofc.services.profile_service import CONTROL_ROLE_CPU_PUMP
from control_ofc.ui.widgets.fan_zone_card import FanGroupCard, FanTile


def _tile_vm(
    fan_id: str = "openfan:ch00",
    *,
    state: FanState = FanState.NORMAL,
    rpm: int | None = 1200,
    pwm: int | None = 40,
    source: str = "openfan",
    role: str | None = None,
    controlled: bool = False,
    curve_source: str | None = None,
    age_ms: int | None = 100,
    name: str | None = None,
) -> FanTileVM:
    return FanTileVM(
        fan_id=fan_id,
        display_name=name or fan_id,
        source=source,
        rpm=rpm,
        pwm_pct=pwm,
        state=state,
        age_ms=age_ms,
        role=role,
        controlled_by_daemon=controlled,
        curve_source=curve_source,
    )


def _group_vm(
    key: str = "zone_front",
    label: str = "Front",
    *,
    is_user_zone: bool = True,
    tiles: tuple[FanTileVM, ...] | None = None,
    online: int = 1,
    expected: int = 1,
    avg_rpm: int | None = 1200,
    avg_pwm: int | None = 40,
    state: FanState = FanState.NORMAL,
) -> FanGroupVM:
    tiles = (_tile_vm(),) if tiles is None else tiles
    return FanGroupVM(
        key=key,
        label=label,
        is_user_zone=is_user_zone,
        tiles=tiles,
        fans_online=online,
        fans_expected=expected,
        avg_rpm=avg_rpm,
        avg_pwm_pct=avg_pwm,
        state=state,
    )


class TestFanTileRender:
    def test_objectname_and_face(self, qtbot):
        tile = FanTile(_tile_vm(name="Front Fan"))
        qtbot.addWidget(tile)
        assert tile.objectName() == "FanZone_Tile_openfan_ch00"
        assert tile._name_label.text() == "Front Fan"
        metrics = tile._metrics_label.text()
        assert "1200 rpm" in metrics
        assert "40%" in metrics
        assert "OpenFan" in metrics
        assert "Normal" in tile._status_label.text()

    def test_offline_tile_shows_dashes(self, qtbot):
        tile = FanTile(_tile_vm(state=FanState.OFFLINE, rpm=None, pwm=None))
        qtbot.addWidget(tile)
        assert "—" in tile._metrics_label.text()
        assert "Offline" in tile._status_label.text()

    def test_status_chip_text_and_class_per_state(self, qtbot):
        cases = [
            (FanState.NORMAL, "SuccessChip"),
            (FanState.OVERRIDE, "WarningChip"),
            (FanState.LOW_RPM, "WarningChip"),
            (FanState.STALE, "WarningChip"),
            (FanState.STALL, "CriticalChip"),
            (FanState.OFFLINE, "CriticalChip"),
        ]
        for st, css in cases:
            tile = FanTile(_tile_vm(state=st))
            qtbot.addWidget(tile)
            # Text label present (not colour-only) + the right semantic class.
            assert st.value in tile._status_label.text()
            assert css in tile._status_label.property("class")

    def test_update_vm_updates_in_place(self, qtbot):
        tile = FanTile(_tile_vm(rpm=1000))
        qtbot.addWidget(tile)
        tile.update_vm(_tile_vm(rpm=2000, state=FanState.STALE))
        assert "2000 rpm" in tile._metrics_label.text()
        assert "Stale" in tile._status_label.text()


class TestFanTileDetail:
    def test_detail_text_is_truthful(self, qtbot):
        tile = FanTile(
            _tile_vm(
                role=CONTROL_ROLE_CPU_PUMP,
                controlled=True,
                curve_source="hwmon:k10temp:Tctl",
                name="Pump",
            )
        )
        qtbot.addWidget(tile)
        text = tile.detail_text()
        assert "ID: openfan:ch00" in text
        assert "Source: OpenFan" in text
        assert "Role: CPU / Pump" in text
        assert "Expected RPM range: —" in text  # never invented
        assert "Controlled by daemon: yes" in text
        assert "Curve sensor: hwmon:k10temp:Tctl" in text

    def test_detail_text_offline_and_no_role(self, qtbot):
        tile = FanTile(_tile_vm(state=FanState.OFFLINE, rpm=None, pwm=None, role=None))
        qtbot.addWidget(tile)
        text = tile.detail_text()
        assert "RPM (measured): —" in text
        assert "Role: —" in text

    def test_activation_emits_signal_and_does_not_hang(self, qtbot):
        tile = FanTile(_tile_vm())
        qtbot.addWidget(tile)
        captured: list[str] = []
        tile.activated.connect(captured.append)
        tile._activate()  # opens the (neutralised) detail dialog + emits
        assert captured == ["openfan:ch00"]


class TestFanTileActions:
    def test_apply_rename_sets_alias(self, qtbot):
        state = AppState()
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile.apply_rename("  My Fan  ")
        assert state.fan_aliases["openfan:ch00"] == "My Fan"

    def test_apply_rename_empty_is_noop(self, qtbot):
        state = AppState()
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile.apply_rename("   ")
        assert "openfan:ch00" not in state.fan_aliases

    def test_apply_assign_sets_zone(self, qtbot):
        state = AppState()
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile.apply_assign("Front Intake")
        assert state.fan_zones["openfan:ch00"] == "Front Intake"

    def test_apply_assign_empty_unassigns(self, qtbot):
        state = AppState()
        state.set_fan_zone("openfan:ch00", "Front")
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile.apply_assign("")
        assert "openfan:ch00" not in state.fan_zones

    def test_prompt_rename_accept_path(self, qtbot, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))
        state = AppState()
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile._prompt_rename()
        assert state.fan_aliases["openfan:ch00"] == "Renamed"

    def test_prompt_assign_accept_path(self, qtbot, monkeypatch):
        monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Exhaust", True))
        state = AppState()
        tile = FanTile(_tile_vm(), state=state)
        qtbot.addWidget(tile)
        tile._prompt_assign()
        assert state.fan_zones["openfan:ch00"] == "Exhaust"


class TestFanGroupCard:
    def test_user_zone_header(self, qtbot):
        card = FanGroupCard(
            _group_vm(label="Front Intake", is_user_zone=True, online=2, expected=3)
        )
        qtbot.addWidget(card)
        assert card.objectName() == "FanZone_Card_zone_front"
        assert card._label.text() == "Front Intake"
        assert card._marker.text() == "★"  # user-zone marker
        assert "2/3 online" in card._counts.text()

    def test_fallback_bucket_marker_differs(self, qtbot):
        card = FanGroupCard(_group_vm(key="bucket_openfan", label="OpenFan", is_user_zone=False))
        qtbot.addWidget(card)
        assert card._marker.text() == "○"  # auto-grouped marker, distinct from ★

    def test_state_chip_objectname_text_and_class(self, qtbot):
        card = FanGroupCard(_group_vm(key="zone_z", state=FanState.STALL))
        qtbot.addWidget(card)
        chip = card.findChild(QLabel, "FanZone_Chip_state_zone_z")
        assert chip is not None
        assert "Stall" in chip.text()
        assert "CriticalChip" in chip.property("class")

    def test_average_text(self, qtbot):
        card = FanGroupCard(_group_vm(avg_rpm=1500, avg_pwm=55))
        qtbot.addWidget(card)
        assert "55% PWM" in card._avgs.text()
        assert "1500 rpm" in card._avgs.text()

    def test_one_tile_per_vm(self, qtbot):
        tiles = (_tile_vm("openfan:ch00"), _tile_vm("openfan:ch01"))
        card = FanGroupCard(_group_vm(tiles=tiles, online=2, expected=2))
        qtbot.addWidget(card)
        assert len(card._tiles) == 2
        assert card.findChild(FanTile, "FanZone_Tile_openfan_ch01") is not None

    def test_many_tiles_collapse_by_default(self, qtbot):
        tiles = tuple(_tile_vm(f"openfan:ch{i:02d}") for i in range(10))
        card = FanGroupCard(_group_vm(tiles=tiles, online=10, expected=10))
        qtbot.addWidget(card)
        assert card._toggle.isHidden() is False  # toggle offered
        assert card._tiles_container.isHidden() is True  # collapsed when many

    def test_few_tiles_no_toggle(self, qtbot):
        card = FanGroupCard(_group_vm(tiles=(_tile_vm(),), online=1, expected=1))
        qtbot.addWidget(card)
        assert card._toggle.isHidden() is True
        assert card._tiles_container.isHidden() is False

    def test_tile_activation_bubbles_up(self, qtbot):
        card = FanGroupCard(_group_vm())
        qtbot.addWidget(card)
        captured: list[str] = []
        card.tile_activated.connect(captured.append)
        next(iter(card._tiles.values()))._activate()
        assert captured == ["openfan:ch00"]

    def test_update_vm_reconciles_tiles(self, qtbot):
        card = FanGroupCard(_group_vm(tiles=(_tile_vm("openfan:ch00"),)))
        qtbot.addWidget(card)
        card.update_vm(_group_vm(tiles=(_tile_vm("openfan:ch01"),)))
        assert "openfan:ch00" not in card._tiles
        assert "openfan:ch01" in card._tiles
