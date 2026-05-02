"""Tests for ControlCard widget — compact fan role card."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.widgets.control_card import ControlCard


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
        from control_ofc.services.profile_service import ControlMember

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
        from control_ofc.services.profile_service import ControlMember

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


class TestControlCardMinPwmBadge:
    """Minimum-PWM badge surfaces the role-derived floor (DEC-095)."""

    def test_chassis_default_when_no_members(self, card):
        # Empty control defaults to the chassis role (the safer default for
        # a brand-new control), so the badge surfaces 20% — same as a
        # chassis-only role with members. Users see the floor will become
        # active as soon as they add openfan/chassis members.
        assert "20" in card._min_pwm_label.text()

    def test_badge_shows_explicit_minimum(self, qtbot, curves):
        ctrl = LogicalControl(
            id="explicit",
            name="Explicit",
            curve_id="c1",
            minimum_pct=20.0,
        )
        c = ControlCard(ctrl, curves)
        qtbot.addWidget(c)
        assert "20" in c._min_pwm_label.text()
        assert "%" in c._min_pwm_label.text()

    def test_badge_shows_role_derived_minimum_for_cpu_pump(self, qtbot, curves):
        from control_ofc.services.profile_service import ControlMember

        ctrl = LogicalControl(
            id="pump",
            name="Pump",
            curve_id="c1",
            members=[
                ControlMember(
                    source="hwmon",
                    member_id="hwmon:nct6775:pwm1",
                    member_label="AIO_PUMP",
                )
            ],
            minimum_pct=0.0,
        )
        c = ControlCard(ctrl, curves)
        qtbot.addWidget(c)
        # Even with explicit minimum_pct=0, the role policy yields 30% so
        # the badge surfaces the effective clamp.
        assert "30" in c._min_pwm_label.text()
        assert "pump" in c._min_pwm_label.toolTip().lower()

    def test_update_control_refreshes_badge(self, qtbot, control, curves):
        from control_ofc.services.profile_service import ControlMember

        c = ControlCard(control, curves)
        qtbot.addWidget(c)
        # Add a CPU member — badge should refresh on update_control.
        control.members.append(
            ControlMember(
                source="hwmon",
                member_id="hwmon:nct6775:pwm1",
                member_label="CPU_FAN",
            )
        )
        c.update_control(control, curves)
        assert "30" in c._min_pwm_label.text()

    def test_update_output_preview(self, card):
        card.update_output_preview("Balanced", "CPU", 45.0, 55.0)
        assert "Preview" in card._output_label.text()
        assert "55" in card._output_label.text()
