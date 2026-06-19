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


def _card_with_member(qtbot, curves):
    from control_ofc.services.profile_service import ControlMember

    ctrl = LogicalControl(
        id="m_ctrl",
        name="Manual Role",
        curve_id="c1",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    c = ControlCard(ctrl, curves)
    qtbot.addWidget(c)
    return c


class TestControlCardManual:
    """Inline per-card transient manual override (Decision 1A)."""

    def test_toggle_reveals_slider_and_emits(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        toggles: list[tuple[str, bool, int]] = []
        c.manual_toggled.connect(lambda cid, active, pct: toggles.append((cid, active, pct)))

        c._manual_btn.setChecked(True)

        assert not c._manual_slider.isHidden()
        assert c._output_label.isHidden()
        assert c._status_chip.text() == "Manual"
        assert toggles and toggles[-1][0] == "m_ctrl" and toggles[-1][1] is True

    def test_toggle_off_restores_output(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        toggles: list[tuple[str, bool, int]] = []
        c.manual_toggled.connect(lambda cid, active, pct: toggles.append((cid, active, pct)))

        c._manual_btn.setChecked(True)
        c._manual_btn.setChecked(False)

        assert c._manual_slider.isHidden()
        assert not c._output_label.isHidden()
        assert toggles[-1][1] is False

    def test_clear_manual_exits_without_emitting(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        toggles: list[tuple[str, bool, int]] = []
        c.manual_toggled.connect(lambda cid, active, pct: toggles.append((cid, active, pct)))

        c._manual_btn.setChecked(True)
        toggles.clear()
        c.clear_manual()

        # A programmatic clear (override lapsed, DEC-163) must NOT emit
        # manual_toggled — otherwise the page would try to release the
        # already-gone override.
        assert not c._manual_btn.isChecked()
        assert c._manual_slider.isHidden()
        assert not c._output_label.isHidden()
        assert toggles == []

    def test_clear_manual_noop_when_not_manual(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        toggles: list = []
        c.manual_toggled.connect(lambda *a: toggles.append(a))

        c.clear_manual()  # not in manual mode → no-op, no emit

        assert toggles == []
        assert not c._manual_btn.isChecked()

    def test_slider_drag_emits_value(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c._manual_btn.setChecked(True)
        values: list[tuple[str, int]] = []
        c.manual_value_changed.connect(lambda cid, pct: values.append((cid, pct)))

        c._manual_slider.setValue(73)

        assert ("m_ctrl", 73) in values
        assert c._manual_pct_label.text() == "73%"

    def test_toggle_seeds_slider_from_last_output(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_output(62.0, sensor_name="CPU", sensor_value=40.0)

        c._manual_btn.setChecked(True)

        # Manual starts at the current speed so the fan doesn't jump.
        assert c._manual_slider.value() == 62

    def test_manual_chip_survives_status_update(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c._manual_btn.setChecked(True)

        # A loop status tick must not overwrite the Manual chip with "Applied".
        c.set_output(20.0, sensor_name="CPU", sensor_value=40.0)

        assert c._status_chip.text() == "Manual"

    def test_manual_button_disabled_without_members(self, card):
        # The default `card` fixture has no members.
        assert not card._manual_btn.isEnabled()


class TestControlCardExternalOverride:
    """DEC-169: read-only display of a daemon-held override this GUI session does
    not own (no fencing token). Driven by the Controls page's /status reconcile."""

    def test_set_external_shows_readonly_chip(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_external_override(45)
        assert c._status_chip.text() == "External 45%"
        # The Manual button stays UNchecked: clicking it is a deliberate take-over.
        assert not c._manual_btn.isChecked()

    def test_external_chip_survives_status_update(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_external_override(45)

        # A 1 Hz loop tick must not repaint "Applied" over the External chip,
        # but the live output value must still update.
        c.set_output(45.0, sensor_name="CPU", sensor_value=40.0)

        assert c._status_chip.text() == "External 45%"
        assert "45" in c._output_label.text()

    def test_clear_external_then_status_repaints_applied(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_external_override(45)
        c.clear_external_override()

        assert c._external_pct is None
        # Next loop tick repaints the normal Applied chip.
        c.set_output(50.0)
        assert "Applied" in c._status_chip.text()

    def test_clear_external_noop_when_none(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_output(50.0)
        assert "Applied" in c._status_chip.text()
        c.clear_external_override()  # nothing external → leave the chip alone
        assert "Applied" in c._status_chip.text()

    def test_manual_takeover_supersedes_external(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_external_override(45)

        # The user takes ownership: Manual wins and the external display is dropped.
        c._manual_btn.setChecked(True)

        assert c._status_chip.text() == "Manual"
        assert c._external_pct is None

    def test_set_external_while_manual_leaves_manual_chip(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c._manual_btn.setChecked(True)

        # Reconcile may report this control (the GUI's own override shows in
        # /status too); a stray external set must not clobber the Manual chip.
        c.set_external_override(45)

        assert c._status_chip.text() == "Manual"


class TestControlCardGpuOutput:
    """DEC-119: card surfaces a divergent GPU member output."""

    def test_gpu_suffix_shown_when_divergent(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_output(20.0, sensor_name="CPU", sensor_value=40.0, gpu_output_pct=10.0)
        text = c._output_label.text()
        assert "GPU 10%" in text
        assert "20%" in text

    def test_no_gpu_suffix_when_absent(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        c.set_output(20.0, sensor_name="CPU", sensor_value=40.0)
        assert "GPU" not in c._output_label.text()
