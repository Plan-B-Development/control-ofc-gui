"""Tests for ControlCard widget — compact fan role card."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from onlyfans.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from onlyfans.ui.widgets.control_card import ControlCard


@pytest.fixture()
def curves():
    return [
        CurveConfig(id="c1", name="Balanced", type=CurveType.GRAPH),
        CurveConfig(id="c2", name="Quiet", type=CurveType.FLAT, flat_output_pct=30.0),
    ]


@pytest.fixture()
def control():
    return LogicalControl(
        id="test_ctrl",
        name="Test Role",
        mode=ControlMode.CURVE,
        curve_id="c1",
        manual_output_pct=50.0,
    )


@pytest.fixture()
def card(qtbot, control, curves):
    c = ControlCard(control, curves)
    qtbot.addWidget(c)
    return c


class TestControlCardNoTuning:
    def test_no_tuning_button(self, card):
        """Tuning UI has been removed from ControlCard (R2-005)."""
        assert not hasattr(card, "_tuning_btn")
        assert not hasattr(card, "_tuning_frame")
        assert not hasattr(card, "_tuning_spins")


class TestControlCardContent:
    def test_card_shows_name(self, card, control):
        assert card._name_label.text() == "Test Role"

    def test_card_shows_members_text(self, card):
        assert "No outputs assigned" in card._members_label.text()

    def test_card_shows_curve(self, card):
        assert "Balanced" in card._curve_label.text()

    def test_set_output_updates_display(self, qtbot, curves):
        from onlyfans.services.profile_service import ControlMember

        ctrl_with_members = LogicalControl(
            id="with_m",
            name="WithMembers",
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        c = ControlCard(ctrl_with_members, curves)
        qtbot.addWidget(c)
        c.set_output(67.0, sensor_name="CPU", sensor_value=42.0)
        assert "67" in c._output_label.text()
        assert "CPU" in c._output_label.text()

    def test_set_output_sets_applied(self, qtbot, curves):
        from onlyfans.services.profile_service import ControlMember

        ctrl_with_members = LogicalControl(
            id="with_m2",
            name="WithMembers2",
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        c = ControlCard(ctrl_with_members, curves)
        qtbot.addWidget(c)
        c.set_output(50.0)
        assert "Applied" in c._status_chip.text()

    def test_set_output_blocked_no_members(self, card):
        """R7-005: set_output is ignored when control has no members."""
        card.set_output(50.0)
        assert "No members" in card._status_chip.text()

    def test_no_members_shows_status(self, card):
        assert "No members" in card._status_chip.text()


class TestControlCardDelete:
    def test_delete_signal_emitted(self, qtbot, card, control):
        """Delete button emits delete_requested signal with control id."""
        with qtbot.waitSignal(card.delete_requested, timeout=1000) as blocker:
            from PySide6.QtWidgets import QPushButton

            del_btn = card.findChild(QPushButton, f"ControlCard_Btn_delete_{control.id}")
            assert del_btn is not None
            qtbot.mouseClick(del_btn, Qt.MouseButton.LeftButton)
        assert blocker.args == [control.id]


class TestControlCardEditRole:
    def test_edit_signal_emitted(self, qtbot, card, control):
        """Edit button emits edit_role_requested signal."""
        with qtbot.waitSignal(card.edit_role_requested, timeout=1000) as blocker:
            from PySide6.QtWidgets import QPushButton

            edit_btn = card.findChild(QPushButton, f"ControlCard_Btn_edit_{control.id}")
            assert edit_btn is not None
            qtbot.mouseClick(edit_btn, Qt.MouseButton.LeftButton)
        assert blocker.args == [control.id]


class TestControlCardUpdate:
    def test_update_control_changes_display(self, card, control, curves):
        control.name = "Updated Name"
        control.curve_id = "c2"
        card.update_control(control, curves)
        assert card._name_label.text() == "Updated Name"
        assert "Quiet" in card._curve_label.text()

    def test_update_output_preview(self, card):
        card.update_output_preview("Balanced", "CPU", 45.0, 55.0)
        assert "Preview" in card._output_label.text()
        assert "55" in card._output_label.text()
