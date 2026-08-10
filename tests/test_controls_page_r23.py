"""Tests for Refinement 23: Controls page splitter, fixed cards, drag reorder, search removal."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit, QSplitter

from control_ofc.services.profile_service import CurveConfig, CurveType, Profile
from control_ofc.ui.pages.controls_page import ControlsPage


class TestSplitter:
    """A. Splitter between Fan Roles and Curves sections."""

    def test_splitter_exists(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_sections")
        assert splitter is not None

    def test_splitter_has_two_panes(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_sections")
        assert splitter.count() == 2


class TestSearchRemoval:
    """C. Search function fully removed."""

    def test_no_search_widget(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        search = page.findChild(QLineEdit, "Controls_Edit_curveSearch")
        assert search is None

    def test_no_filter_widget(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        filt = page.findChild(QComboBox, "Controls_Combo_curveFilter")
        assert filt is None


class TestFixedSizeCards:
    """D. Curve cards have a fixed width and a minimum-height floor (DEC-128)."""

    def test_card_fixed_width_and_min_height(self, qtbot):
        from control_ofc.ui.theme import active_theme
        from control_ofc.ui.widgets.card_metrics import DEFAULT_CARD_SIZE, card_dimensions
        from control_ofc.ui.widgets.curve_card import CurveCard

        curve = CurveConfig(id="test", name="Test", type=CurveType.FLAT)
        card = CurveCard(curve)
        qtbot.addWidget(card)
        w, h = card_dimensions(active_theme().base_font_size_pt, DEFAULT_CARD_SIZE)
        # Width is fixed (aligned columns); height is a floor content can grow
        # past, never a hard cap (DEC-128).
        assert card.minimumWidth() == w
        assert card.maximumWidth() == w
        assert card.minimumHeight() == h
        assert card.maximumHeight() > h


class TestCurveReorder:
    """B. Curve card drag-to-reorder (model-level tests)."""

    def test_reorder_curves_in_profile(self):
        c1 = CurveConfig(id="c1", name="First", type=CurveType.FLAT)
        c2 = CurveConfig(id="c2", name="Second", type=CurveType.LINEAR)
        c3 = CurveConfig(id="c3", name="Third", type=CurveType.GRAPH)
        profile = Profile(id="test", name="Test", curves=[c1, c2, c3])

        new_order = ["c3", "c1", "c2"]
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map[cid] for cid in new_order]

        assert profile.curves[0].name == "Third"
        assert profile.curves[1].name == "First"
        assert profile.curves[2].name == "Second"

    def test_reorder_preserves_curve_data(self):
        c1 = CurveConfig(id="c1", name="Graph Curve", type=CurveType.GRAPH, sensor_id="cpu_sensor")
        c2 = CurveConfig(id="c2", name="Flat", type=CurveType.FLAT)
        profile = Profile(id="test", name="Test", curves=[c1, c2])

        new_order = ["c2", "c1"]
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map[cid] for cid in new_order]

        assert profile.curves[1].sensor_id == "cpu_sensor"
        assert profile.curves[1].type == CurveType.GRAPH

    def test_reorder_single_curve_noop(self):
        c1 = CurveConfig(id="c1", name="Only", type=CurveType.FLAT)
        profile = Profile(id="test", name="Test", curves=[c1])

        new_order = ["c1"]
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map[cid] for cid in new_order]

        assert len(profile.curves) == 1
        assert profile.curves[0].name == "Only"


class TestPaneMinimumTracksTheCardMetric:
    """Release review round 2, 2026-08-10 — a P2 in the round-1 DEC-260 fix.

    `pane_min` was derived from the card metric (correct) but computed once in
    `_build_ui` and never again. `set_theme` re-derives every card's width from
    the new base font and density tier, so raising the live base font grew the
    cards while the panes stayed pinned to the startup value — putting the flow
    container's minimum past the viewport and restoring exactly the permanent
    horizontal scrollbar and clipped resize grip DEC-260 removed.
    """

    def _tokens(self, base_pt: int):
        from control_ofc.ui.theme import default_dark_theme

        t = default_dark_theme()
        t.base_font_size_pt = base_pt
        return t

    def test_raising_the_base_font_widens_the_panes(self, qtbot, app_state, profile_service):
        from control_ofc.ui.widgets.card_metrics import card_pane_min_width

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        before = [p.minimumWidth() for p in page._card_panes]

        page.set_theme(self._tokens(16))

        expected = card_pane_min_width(16, page._card_size_tier())
        after = [p.minimumWidth() for p in page._card_panes]
        assert after == [expected] * len(after), (
            f"panes stayed at {before} while cards re-derived to a 16pt width — "
            "the flow container no longer fits, which is the DEC-260 overflow"
        )
        assert all(a > b for a, b in zip(after, before, strict=True)), (
            "16pt must be wider than the default"
        )

    def test_every_pane_holds_at_least_one_card(self, qtbot, app_state, profile_service):
        """The property that actually matters, across the whole live font range."""
        from control_ofc.ui.widgets.card_metrics import card_dimensions

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        tier = page._card_size_tier()

        for pt in (7, 10, 13, 16):
            page.set_theme(self._tokens(pt))
            card_w = card_dimensions(pt, tier)[0]
            for pane in page._card_panes:
                assert pane.minimumWidth() >= card_w, (
                    f"at {pt}pt a card is {card_w}px but its pane bottoms out at "
                    f"{pane.minimumWidth()}px — one card cannot fit"
                )
