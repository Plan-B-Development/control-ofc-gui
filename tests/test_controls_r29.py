"""R29: Controls page card polish, section resizing, and alignment tests.

Covers: curves/editor splitter, shared card sizing, bottom row order,
transparent label backgrounds, cross-section alignment.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QSplitter

from onlyfans.services.profile_service import ControlMode, CurveConfig, CurveType, LogicalControl
from onlyfans.ui.pages.controls_page import ControlsPage
from onlyfans.ui.widgets.card_metrics import CARD_HEIGHT, CARD_WIDTH
from onlyfans.ui.widgets.control_card import ControlCard
from onlyfans.ui.widgets.curve_card import CurveCard


class TestCurvesEditorSplitter:
    """Curves grid and editor are separated by a user-draggable splitter."""

    def test_splitter_exists(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_curvesEditor")
        assert splitter is not None
        assert splitter.orientation() == Qt.Orientation.Vertical

    def test_splitter_has_two_children(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_curvesEditor")
        assert splitter.count() == 2

    def test_splitter_not_collapsible(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_curvesEditor")
        assert not splitter.isCollapsible(0)
        assert not splitter.isCollapsible(1)


class TestSharedCardSizing:
    """CurveCard and ControlCard use the same shared dimensions."""

    def test_curve_card_uses_shared_size(self, qtbot):
        curve = CurveConfig(id="c1", name="Test", type=CurveType.FLAT)
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card.size() == QSize(CARD_WIDTH, CARD_HEIGHT)

    def test_control_card_uses_shared_size(self, qtbot):
        control = LogicalControl(name="Role", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)
        assert card.size() == QSize(CARD_WIDTH, CARD_HEIGHT)

    def test_cards_are_same_size(self, qtbot):
        curve_card = CurveCard(CurveConfig(id="c", name="C", type=CurveType.FLAT))
        control_card = ControlCard(LogicalControl(name="R", mode=ControlMode.CURVE), [])
        qtbot.addWidget(curve_card)
        qtbot.addWidget(control_card)
        assert curve_card.size() == control_card.size()


class TestControlCardBottomRow:
    """Fan Role bottom row: RPM left, Delete left-of-Edit, Edit far right."""

    def test_bottom_row_widget_order(self, qtbot):
        control = LogicalControl(id="r1", name="Test", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)

        # The last layout in the card's main QVBoxLayout is the actions row
        main_layout = card.layout()
        actions_layout = main_layout.itemAt(main_layout.count() - 1).layout()

        # Collect widget names in order (skip stretches)
        widgets = []
        for i in range(actions_layout.count()):
            item = actions_layout.itemAt(i)
            if item.widget():
                widgets.append(item.widget().objectName())

        # RPM label first, then Delete, then Edit
        assert widgets[0] == "ControlCard_Label_rpm_r1"
        assert widgets[1] == "ControlCard_Btn_delete_r1"
        assert widgets[2] == "ControlCard_Btn_edit_r1"


class TestCardMetaTypography:
    """R33: Card metadata labels use CardMeta class (small role), not PageSubtitle."""

    def test_curve_card_metadata_uses_card_meta(self, qtbot):
        curve = CurveConfig(id="c1", name="Test", type=CurveType.FLAT)
        card = CurveCard(curve)
        qtbot.addWidget(card)
        sensor_label = card.findChild(type(card._name_label), "CurveCard_Label_sensor_c1")
        assert sensor_label.property("class") == "CardMeta"

    def test_control_card_metadata_uses_card_meta(self, qtbot):
        control = LogicalControl(id="r1", name="Test", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)
        members_label = card.findChild(type(card._name_label), "ControlCard_Label_members_r1")
        assert members_label.property("class") == "CardMeta"

    def test_stylesheet_has_card_meta_class(self):
        from onlyfans.ui.theme import build_stylesheet, default_dark_theme

        css = build_stylesheet(default_dark_theme())
        assert ".CardMeta" in css

    def test_stylesheet_has_card_button_padding(self):
        from onlyfans.ui.theme import build_stylesheet, default_dark_theme

        css = build_stylesheet(default_dark_theme())
        assert ".Card QPushButton" in css
        assert "padding" in css


class TestTransparentLabelBackgrounds:
    """Card labels use transparent background so the Card class background shows through."""

    def test_curve_card_labels_transparent(self, qtbot):
        curve = CurveConfig(id="c1", name="Test", type=CurveType.FLAT)
        card = CurveCard(curve)
        qtbot.addWidget(card)

        # Check all labels with inline stylesheets contain 'transparent'
        for label_name in [
            "CurveCard_Label_c1",
            "CurveCard_Label_sensor_c1",
            "CurveCard_Label_usedBy_c1",
            "CurveCard_Label_status_c1",
        ]:
            label = card.findChild(type(card._name_label), label_name)
            if label:
                assert "transparent" in label.styleSheet().lower(), (
                    f"{label_name} missing transparent background"
                )

    def test_control_card_labels_transparent(self, qtbot):
        control = LogicalControl(id="r1", name="Test", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)

        for label_name in [
            "ControlCard_Label_r1",
            "ControlCard_Label_status_r1",
            "ControlCard_Label_members_r1",
            "ControlCard_Label_curve_r1",
            "ControlCard_Label_output_r1",
            "ControlCard_Label_rpm_r1",
        ]:
            label = card.findChild(type(card._name_label), label_name)
            if label:
                assert "transparent" in label.styleSheet().lower(), (
                    f"{label_name} missing transparent background"
                )
