"""Tests for Refinement 23: Controls page splitter, fixed cards, drag reorder, search removal."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QLineEdit, QSplitter

from onlyfans.services.profile_service import CurveConfig, CurveType, Profile
from onlyfans.ui.pages.controls_page import ControlsPage


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
    """D. Curve cards have consistent fixed size."""

    def test_card_fixed_max_and_min(self, qtbot):
        from onlyfans.ui.widgets.curve_card import CurveCard

        curve = CurveConfig(id="test", name="Test", type=CurveType.FLAT)
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card.maximumSize() == QSize(220, 160)
        assert card.minimumSize() == QSize(220, 160)


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
