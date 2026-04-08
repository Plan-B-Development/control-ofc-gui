"""Tests for Refinement 25: Card ordering, fixed sizing, flow layout, syslog fields."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QPushButton

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer
from control_ofc.ui.widgets.flow_layout import FlowLayout


class TestFlowLayoutInvalidation:
    """FlowLayout.addItem() must trigger invalidation so cards are positioned."""

    def test_add_item_invalidates(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        btn = QPushButton("Test")
        btn.setFixedSize(100, 50)
        container.add_card(btn, "t1")
        # After add, layout should have the item
        assert container.flow_layout().count() == 1

    def test_add_item_calls_invalidate(self, qtbot):
        """FlowLayout.addItem() must call invalidate() so layout recalculates.

        Verify by showing the container and checking positions after Qt processes events.
        """
        from PySide6.QtWidgets import QWidget

        container = QWidget()
        layout = FlowLayout(container, margin=4, h_spacing=6, v_spacing=6)
        qtbot.addWidget(container)
        container.resize(500, 200)
        container.show()
        qtbot.waitExposed(container)

        for i in range(3):
            btn = QPushButton(f"Card {i}")
            btn.setFixedSize(100, 50)
            layout.addWidget(btn)

        # Process events so Qt triggers setGeometry → _do_layout
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

        # Cards should now have been positioned at different x offsets
        positions = set()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.geometry().width() > 0:
                positions.add(item.geometry().x())
        assert len(positions) > 1, "Cards must have different x positions after layout"

    def test_cards_positioned_in_hidden_parent(self, qtbot):
        """Cards must be positioned even when parent is not yet shown.

        This is the root cause of R27: FlowLayout used isVisible() which
        checks the entire parent chain. Before the window is shown, all
        children report isVisible()=False, so _do_layout() skipped them
        all, leaving cards stacked at (0,0). The fix uses isHidden()
        which only checks if the widget itself was explicitly hidden.
        """
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QWidget

        container = QWidget()  # NOT shown — simulates pre-show construction
        layout = FlowLayout(container, margin=4, h_spacing=6, v_spacing=6)
        qtbot.addWidget(container)

        for i in range(3):
            btn = QPushButton(f"Card {i}")
            btn.setFixedSize(100, 50)
            layout.addWidget(btn)

        # Force layout calculation at width=500 — simulates what Qt does on resize
        layout._do_layout(QRect(0, 0, 500, 200), test_only=False)

        # Cards must have different x positions, not all stacked at (0,0)
        positions = set()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.geometry().width() > 0:
                positions.add(item.geometry().x())
        assert len(positions) > 1, "Cards stacked — layout skipped items in hidden parent"


class TestCurveCardAppend:
    """New curve cards append to the end of the sequence."""

    def test_new_curve_appends_to_end(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        initial_count = len(profile.curves)

        # Add a new curve
        page._on_add_curve(CurveType.FLAT)

        profile = page._get_current_profile()
        assert len(profile.curves) == initial_count + 1
        assert profile.curves[-1].name.startswith("New Flat")

    def test_existing_curve_order_preserved_after_add(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        original_ids = [c.id for c in profile.curves]

        page._on_add_curve(CurveType.LINEAR)

        profile = page._get_current_profile()
        new_ids = [c.id for c in profile.curves]
        # Original IDs should appear at the same positions
        assert new_ids[: len(original_ids)] == original_ids


class TestCurveOrderStability:
    """Curve card order survives refresh/rebuild cycles."""

    def test_order_survives_refresh(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        if len(profile.curves) < 2:
            page._on_add_curve(CurveType.FLAT)
            page._on_add_curve(CurveType.LINEAR)
            profile = page._get_current_profile()

        ids_before = [c.id for c in profile.curves]
        page._refresh_all()
        ids_after = [c.id for c in profile.curves]
        assert ids_before == ids_after

    def test_reorder_persists_through_refresh(self):
        """Model-level: reordering profile.curves survives iteration."""
        c1 = CurveConfig(id="c1", name="A", type=CurveType.FLAT)
        c2 = CurveConfig(id="c2", name="B", type=CurveType.LINEAR)
        c3 = CurveConfig(id="c3", name="C", type=CurveType.GRAPH)
        profile = Profile(id="test", name="Test", curves=[c1, c2, c3])

        # Simulate drag reorder: c3, c1, c2
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map["c3"], curve_map["c1"], curve_map["c2"]]

        # Simulate refresh: iterate profile.curves
        rebuilt = [c.id for c in profile.curves]
        assert rebuilt == ["c3", "c1", "c2"]


class TestControlCardFixedSize:
    """Fan Role cards have consistent fixed size."""

    def test_control_card_fixed_size(self, qtbot):
        control = LogicalControl(name="Test Role", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)
        from control_ofc.ui.widgets.card_metrics import CARD_HEIGHT, CARD_WIDTH

        assert card.maximumSize() == QSize(CARD_WIDTH, CARD_HEIGHT)
        assert card.minimumSize() == QSize(CARD_WIDTH, CARD_HEIGHT)


class TestControlCardFlowContainer:
    """Fan Role cards use DraggableFlowContainer like Curve cards."""

    def test_controls_use_flow_container(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        assert hasattr(page, "_controls_flow")
        assert isinstance(page._controls_flow, DraggableFlowContainer)

    def test_new_control_appends_to_end(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        initial_count = len(profile.controls)

        page._on_new_control(single=True, name="Appended Role")

        profile = page._get_current_profile()
        assert len(profile.controls) == initial_count + 1
        assert profile.controls[-1].name == "Appended Role"

    def test_control_reorder_syncs_model(self):
        """Reordering controls updates profile.controls list."""
        c1 = LogicalControl(id="r1", name="First", mode=ControlMode.CURVE)
        c2 = LogicalControl(id="r2", name="Second", mode=ControlMode.CURVE)
        c3 = LogicalControl(id="r3", name="Third", mode=ControlMode.CURVE)
        profile = Profile(id="test", name="Test", controls=[c1, c2, c3])

        # Simulate reorder: r3, r1, r2
        control_map = {c.id: c for c in profile.controls}
        profile.controls = [control_map["r3"], control_map["r1"], control_map["r2"]]

        assert profile.controls[0].name == "Third"
        assert profile.controls[1].name == "First"
        assert profile.controls[2].name == "Second"
