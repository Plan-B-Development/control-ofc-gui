"""Phase 4 (DEC-179): the zone editor — assign / unassign / rename via the
per-tile action — plus FanZoneGrid reconciliation and the demo seed.

Assignment persists through AppState (Phase 1, DEC-176); these tests verify the
*editor outcomes*: a fan moves into a user zone, unassign returns it to the
role/source fallback, renaming (by reassigning members) moves the whole group,
unassigned fans are never hidden, and the demo seed populates user zones.
"""

from __future__ import annotations

from control_ofc.api.models import ConnectionState, FanReading
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.services.fan_grouping import build_fan_groups
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.fan_zone_card import FanGroupCard, FanTile, FanZoneGrid


def _fans() -> list[FanReading]:
    return [
        FanReading(
            id="openfan:ch00", source="openfan", rpm=1200, last_commanded_pwm=40, age_ms=100
        ),
        FanReading(
            id="openfan:ch01", source="openfan", rpm=1100, last_commanded_pwm=40, age_ms=100
        ),
    ]


def _groups(state: AppState, fans: list[FanReading]):
    return build_fan_groups(
        fans,
        fan_zones=state.fan_zones,
        display_name=state.fan_display_name,
        active_profile=None,
        overrides=[],
    )


class TestZoneGridReconciliation:
    def test_assign_moves_fan_into_user_zone(self, qtbot):
        state = AppState()
        fans = _fans()
        grid = FanZoneGrid(state=state)
        qtbot.addWidget(grid)
        grid.set_groups(_groups(state, fans))
        # Out of the box both fans sit in the auto source/role fallback bucket.
        assert grid.findChild(FanGroupCard, "FanZone_Card_bucket_openfan") is not None
        assert grid.findChild(FanGroupCard, "FanZone_Card_zone_front_intake") is None

        state.set_fan_zone("openfan:ch00", "Front Intake")
        grid.set_groups(_groups(state, fans))
        zone_card = grid.findChild(FanGroupCard, "FanZone_Card_zone_front_intake")
        assert zone_card is not None
        assert zone_card._marker.text() == "★"  # rendered as a user zone

    def test_unassign_returns_fan_to_fallback(self, qtbot):
        state = AppState()
        fans = _fans()
        state.set_fan_zone("openfan:ch00", "Front Intake")
        grid = FanZoneGrid(state=state)
        qtbot.addWidget(grid)
        grid.set_groups(_groups(state, fans))
        assert grid.findChild(FanGroupCard, "FanZone_Card_zone_front_intake") is not None

        state.set_fan_zone("openfan:ch00", "")  # unassign
        grid.set_groups(_groups(state, fans))
        assert grid.findChild(FanGroupCard, "FanZone_Card_zone_front_intake") is None
        assert grid.findChild(FanGroupCard, "FanZone_Card_bucket_openfan") is not None

    def test_rename_zone_by_reassigning_members(self, qtbot):
        state = AppState()
        fans = _fans()
        for f in fans:
            state.set_fan_zone(f.id, "Old")
        grid = FanZoneGrid(state=state)
        qtbot.addWidget(grid)
        grid.set_groups(_groups(state, fans))
        old = grid.findChild(FanGroupCard, "FanZone_Card_zone_old")
        assert old is not None
        assert len(old._tiles) == 2

        for f in fans:  # "rename" = reassign every member to the new name
            state.set_fan_zone(f.id, "New")
        grid.set_groups(_groups(state, fans))
        assert grid.findChild(FanGroupCard, "FanZone_Card_zone_old") is None
        new = grid.findChild(FanGroupCard, "FanZone_Card_zone_new")
        assert new is not None
        assert len(new._tiles) == 2

    def test_unassigned_fans_always_visible(self, qtbot):
        state = AppState()
        fans = _fans()
        state.set_fan_zone("openfan:ch00", "Front Intake")  # one assigned, one not
        grid = FanZoneGrid(state=state)
        qtbot.addWidget(grid)
        grid.set_groups(_groups(state, fans))
        # The unassigned fan is never hidden — it stays in the fallback group.
        assert grid.findChild(FanGroupCard, "FanZone_Card_zone_front_intake") is not None
        assert grid.findChild(FanGroupCard, "FanZone_Card_bucket_openfan") is not None

    def test_empty_grid_shows_placeholder(self, qtbot):
        grid = FanZoneGrid(state=AppState())
        qtbot.addWidget(grid)
        grid.set_groups([])
        assert grid._empty.isHidden() is False


class TestEditorThroughDashboard:
    """The per-tile action drives AppState, which the dashboard observes and
    re-groups live — the full editor loop."""

    def _page(self, qtbot):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        state.set_fans(_fans())  # populates the grid via fans_updated
        return page, state

    def test_tile_assign_regroups_dashboard_live(self, qtbot):
        page, _state = self._page(qtbot)
        tile = page._fan_zone_grid.findChild(FanTile, "FanZone_Tile_openfan_ch00")
        assert tile is not None
        tile.apply_assign("Exhaust")  # editor action → set_fan_zone → live regroup
        assert page.findChild(FanGroupCard, "FanZone_Card_zone_exhaust") is not None

    def test_tile_rename_updates_alias_and_tile_live(self, qtbot):
        page, state = self._page(qtbot)
        tile = page._fan_zone_grid.findChild(FanTile, "FanZone_Tile_openfan_ch00")
        tile.apply_rename("Big Fan")
        assert state.fan_aliases["openfan:ch00"] == "Big Fan"
        refreshed = page._fan_zone_grid.findChild(FanTile, "FanZone_Tile_openfan_ch00")
        assert refreshed._name_label.text() == "Big Fan"


class TestDemoSeed:
    def test_demo_zones_populate_user_zones(self):
        state = AppState()
        state.fan_zones = dict(DemoService.fan_zones())
        fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=1000, last_commanded_pwm=40, age_ms=50
            ),
            FanReading(
                id="openfan:ch02", source="openfan", rpm=1000, last_commanded_pwm=40, age_ms=50
            ),
            FanReading(
                id="amd_gpu:0000:03:00.0",
                source="amd_gpu",
                rpm=0,
                last_commanded_pwm=None,
                age_ms=50,
            ),
        ]
        groups = build_fan_groups(
            fans,
            fan_zones=state.fan_zones,
            display_name=state.fan_display_name,
            active_profile=None,
            overrides=[],
        )
        labels = {g.label: g.is_user_zone for g in groups}
        assert labels.get("Front Intake") is True  # ch00 (demo seed)
        assert labels.get("Exhaust") is True  # ch02 (demo seed)
        # The GPU fan is intentionally unzoned → role/source fallback, not hidden.
        assert any(g.label == "AMD GPU" and not g.is_user_zone for g in groups)

    def test_cleared_zones_fall_back_to_source(self):
        fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=1000, last_commanded_pwm=40, age_ms=50
            )
        ]
        groups = build_fan_groups(
            fans,
            fan_zones={},
            display_name=lambda i: i,
            active_profile=None,
            overrides=[],
        )
        assert all(not g.is_user_zone for g in groups)
        assert any(g.label == "OpenFan" for g in groups)
