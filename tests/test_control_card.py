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


class TestControlCardCompact:
    """DEC-214: compact mockup card — role dot, N-Fans pill, per-member RPM rows,
    and details revealed only when the card is selected."""

    def _role_with_members(self, qtbot, curves):
        from control_ofc.services.profile_service import ControlMember

        control = LogicalControl(
            id="r9",
            name="Intake",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[
                ControlMember(source="openfan", member_id="openfan:ch00", member_label="Front-Top"),
                ControlMember(source="openfan", member_id="openfan:ch01", member_label="Front-Bot"),
            ],
        )
        c = ControlCard(control, curves)
        qtbot.addWidget(c)
        return c

    def test_fan_count_pill(self, qtbot, curves):
        c = self._role_with_members(qtbot, curves)
        assert c._fan_count_label.text() == "2 Fans"

    def test_fan_count_singular(self, qtbot, curves):
        c = _card_with_member(qtbot, curves)
        assert c._fan_count_label.text() == "1 Fan"

    def test_member_rows_show_per_fan_rpm(self, qtbot, curves):
        c = self._role_with_members(qtbot, curves)
        c.set_member_rpms({"openfan:ch00": 1151, "openfan:ch01": 940})
        assert c._member_row_rpm["openfan:ch00"].text() == "1151 RPM"
        assert c._member_row_rpm["openfan:ch01"].text() == "940 RPM"

    def test_member_rpm_unknown_stays_blank(self, qtbot, curves):
        """No fabricated values — an unknown RPM shows an empty column."""
        c = self._role_with_members(qtbot, curves)
        c.set_member_rpms({"openfan:ch00": 1151})  # ch01 omitted
        assert c._member_row_rpm["openfan:ch01"].text() == ""

    def test_details_default_expanded(self, qtbot, curves):
        """A standalone card is expanded so every widget is reachable (tests)."""
        c = self._role_with_members(qtbot, curves)
        assert c._expanded is True
        assert not c._details.isHidden()

    def test_set_selected_collapses_and_reveals_details(self, qtbot, curves):
        c = self._role_with_members(qtbot, curves)
        c.set_selected(False)
        assert c._expanded is False
        assert c._details.isHidden()
        assert c.property("selected") is False
        assert c._link_nub.isHidden()
        c.set_selected(True)
        assert not c._details.isHidden()
        assert c.property("selected") is True
        assert not c._link_nub.isHidden()


class TestCurveLabelElision:
    """DEC-258: the curve line must not widen its card.

    The label was a plain `QLabel`, whose `minimumSizeHint` refuses to shrink
    below the full text — so one profile with a long curve name stretched one
    tile in an otherwise uniform grid, and the fixed-width card clipped it. The
    swap to `ElidedLabel` had no test: every existing card test uses short names
    like "Balanced", which fit either way.
    """

    def test_a_long_curve_name_does_not_force_the_card_wider(self, qtbot, curves):
        from control_ofc.ui.components.labels import ElidedLabel

        long_name = "Aggressive Cooling For Sustained All-Core Workloads"
        curves = [*curves, CurveConfig(id="c3", name=long_name, type=CurveType.GRAPH)]
        ctrl = LogicalControl(
            id="wide_ctrl", name="Test Role", mode=ControlMode.CURVE, curve_id="c3"
        )
        card = ControlCard(ctrl, curves)
        qtbot.addWidget(card)

        label = card._curve_label
        assert isinstance(label, ElidedLabel), (
            "a plain QLabel refuses to shrink below its full text, so a long "
            "curve name widens the card it sits in"
        )
        assert label.minimumSizeHint().width() < label.sizeHint().width(), (
            "the label must be free to shrink below its natural width, or elision cannot happen"
        )

    def test_the_full_curve_name_is_still_stored_verbatim(self, qtbot, curves):
        """Elision is a paint-time effect (DEC-231): `text()` must not be rewritten,
        because the card's XSS guard asserts the label holds exactly what it was
        handed."""
        long_name = "Aggressive Cooling For Sustained All-Core Workloads"
        curves = [*curves, CurveConfig(id="c3", name=long_name, type=CurveType.GRAPH)]
        ctrl = LogicalControl(
            id="wide_ctrl", name="Test Role", mode=ControlMode.CURVE, curve_id="c3"
        )
        card = ControlCard(ctrl, curves)
        qtbot.addWidget(card)
        card.resize(120, card.height())
        card.show()

        assert long_name in card._curve_label.text()
        assert "…" not in card._curve_label.text()


class TestManualHeldWithoutMembers:
    """277-o: stripping a control's members must not trap a live override.

    `_update_no_members_state` disabled the Manual button whenever the control
    had no members. Disabling a *checked* button leaves the user unable to
    un-toggle it — so a control stripped of its members while an override was
    live could not be released until the ~15 s daemon deadman expired, on a card
    whose renew timer was still renewing. Verified identical at v2.43.1, so
    pre-existing rather than introduced.
    """

    def test_the_release_stays_reachable_when_members_vanish(self, qtbot, curves):
        card = _card_with_member(qtbot, curves)
        card._manual_btn.setChecked(True)
        assert card._manual_btn.isEnabled(), "precondition: enabled while it has members"

        stripped = LogicalControl(id="m_ctrl", name="Manual Role", curve_id="c1", members=[])
        card._update_no_members_state(stripped)

        assert card._manual_btn.isEnabled(), (
            "a CHECKED Manual button must stay enabled with no members — otherwise "
            "the user cannot release an override they are holding and must wait "
            "out the daemon deadman"
        )

    def test_taking_manual_still_needs_members(self, qtbot, curves):
        """Guard against over-fixing: the exemption is only for a HELD override."""
        card = _card_with_member(qtbot, curves)
        stripped = LogicalControl(id="m_ctrl", name="Manual Role", curve_id="c1", members=[])
        card._update_no_members_state(stripped)

        assert not card._manual_btn.isEnabled(), (
            "an UNchecked Manual button with no members must stay disabled — "
            "there is nothing to drive"
        )

    def test_releasing_on_an_emptied_card_re_disables_the_button(self, qtbot, curves):
        """The exemption must not outlive the override that justified it.

        `setEnabled(members or checked)` is recomputed only in
        `_update_no_members_state`, and nothing re-ran it on the way out — so
        after releasing an override on a card whose members were stripped in
        place, the button stayed ENABLED on a 0-member control and the user could
        take a FRESH override with no fans. That is the exact invariant
        `test_taking_manual_still_needs_members` claims to guard, defeated by the
        interleaving rather than by the static state.
        """
        card = _card_with_member(qtbot, curves)
        card._manual_btn.setChecked(True)
        stripped = LogicalControl(id="m_ctrl", name="Manual Role", curve_id="c1", members=[])
        card._update_no_members_state(stripped)
        card._control = stripped
        assert card._manual_btn.isEnabled(), "precondition: enabled while held"

        card.clear_manual()

        assert not card._manual_btn.isEnabled(), (
            "once the override is released the exemption lapses — a 0-member "
            "control must not offer a fresh Manual take"
        )

    def test_clicking_the_toggle_off_re_disables_it_too(self, qtbot, curves):
        """The INTERACTIVE exit, driven by `.click()` — not by calling a handler.

        Round 2 of this fix recomputed the enable rule only in `clear_manual`,
        the *programmatic* exit taken when an override lapses or is rejected. The
        exit a user actually takes is `_on_manual_toggled(False)`, and it was
        left out — so un-toggling on a member-stripped card left an enabled
        button on a 0-member control and the next click took a fresh override
        that commands nothing and renews it every ~5 s.

        **Its round-2 tests could not have caught this**: they called
        `clear_manual()` and `_update_no_members_state()` directly, so they never
        crossed the signal connection where the bug lived. `.click()`, not
        `_handler()` (CLAUDE.md § Hard-won lessons) — this test exists in that
        form deliberately.
        """
        card = _card_with_member(qtbot, curves)
        card._manual_btn.click()
        assert card._manual_btn.isChecked(), "precondition: the click took Manual"

        stripped = LogicalControl(id="m_ctrl", name="Manual Role", curve_id="c1", members=[])
        card.update_control(stripped, curves)
        assert card._manual_btn.isEnabled(), "precondition: the exemption holds while held"

        card._manual_btn.click()

        assert not card._manual_btn.isChecked(), "the click released Manual"
        assert not card._manual_btn.isEnabled(), (
            "the exemption must lapse on the INTERACTIVE exit as well — otherwise "
            "the very next click takes a fresh override on a control with no fans"
        )

    def test_a_held_manual_is_not_relabelled_no_members(self, qtbot, curves):
        card = _card_with_member(qtbot, curves)
        card._manual_btn.setChecked(True)
        stripped = LogicalControl(id="m_ctrl", name="Manual Role", curve_id="c1", members=[])
        card._update_no_members_state(stripped)

        assert card._status_chip.text() != "No members", (
            "the user is commanding these fans right now, so 'No members' is both "
            "wrong and — via _apply_chip — announced as the card's description"
        )


class TestSkipReasonIsAccessible:
    """277-n: the skip reason must reach a keyboard-only user.

    It existed only as a tooltip on `_status_chip`, a non-focusable QLabel. Qt
    maps `toolTip` to `QAccessible::Text::Description`, which is what that relied
    on — but browsing by focus never lands on a label, so a screen-reader user
    got "Not controlled" with no cause while a mouse user got the explanation.
    """

    def test_the_card_carries_the_reason_as_its_description(self, qtbot, curves):
        card = _card_with_member(qtbot, curves)

        card.set_skipped("curve_not_found")

        assert card._status_chip.text() == "Not controlled"
        assert card._status_chip.toolTip() != "", "precondition: the chip still explains itself"
        assert card.accessibleDescription() == card._status_chip.toolTip(), (
            "the reason must also be on the CARD, which is focusable — the chip "
            "is a QLabel a keyboard user can never reach"
        )


class TestClearOutputRespectsTheMemberGuard:
    """277-k round 3: `clear_output` must suppress where `set_output` suppresses.

    Its docstring promised exactly that; the code omitted the member guard. On a
    member-less control the 1 Hz reset loop therefore replaced
    `_update_no_members_state`'s "Assign outputs to enable" guidance with an em
    dash on every poll, and nothing put it back for the rest of the session.
    """

    def test_a_member_less_card_keeps_its_guidance(self, qtbot, curves):
        from control_ofc.services.profile_service import LogicalControl
        from control_ofc.ui.widgets.control_card import ControlCard

        card = ControlCard(
            LogicalControl(id="empty", name="Empty", curve_id="c1", members=[]), curves
        )
        qtbot.addWidget(card)
        assert card._output_label.text() == "Assign outputs to enable", "precondition"

        card.clear_output()

        assert card._output_label.text() == "Assign outputs to enable", (
            "blanking this replaces actionable guidance with nothing, and nothing "
            "restores it for the rest of the session"
        )

    def test_a_member_bearing_card_still_clears(self, qtbot, curves):
        """Guard against over-fixing: the guard is for member-LESS controls only."""
        card = _card_with_member(qtbot, curves)
        card.set_output(42.0)
        assert "42%" in card._output_label.text(), "precondition"

        card.clear_output()

        assert card._output_label.text() == "—"
