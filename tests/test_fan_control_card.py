"""Tests for the Dashboard fan card widget (DEC-222).

The card is deliberately read-only: it reflects daemon state and deep-links to
the Controls page, which owns every write. These pin what it renders for each
state, and that it never grows a control affordance.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFrame

from control_ofc.services.fan_cards_view import FanCardVM, FanState
from control_ofc.services.profile_service import CurveConfig, CurvePoint
from control_ofc.ui.widgets.fan_control_card import FanControlCard


def _vm(**kw):
    base = dict(
        control_id="c1",
        card_key="c1",
        label="CPU Fans",
        is_unassigned=False,
        is_read_only=False,
        fan_count=2,
        member_fan_ids=("f1", "f2"),
        rpm=1200,
        pwm_pct=45,
        duty_pct=None,
        temp_c=61.4,
        state=FanState.NORMAL,
        overridden=False,
        curve=None,
    )
    base.update(kw)
    # Unless a test is specifically exercising a key clash, the card key tracks
    # the control id (which is what build_fan_card_vms produces normally).
    if "control_id" in kw and "card_key" not in kw:
        base["card_key"] = kw["control_id"]
    return FanCardVM(**base)


class TestRendering:
    def test_renders_the_metric_triple(self, qtbot):
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        assert card._rpm_value.text() == "1200"
        assert card._speed_value.text() == "45%"
        assert card._temp_value.text() == "61°C"

    def test_missing_values_render_as_em_dash_not_zero(self, qtbot):
        """An unknown reading must never be presented as a real 0."""
        card = FanControlCard(_vm(rpm=None, pwm_pct=None, temp_c=None))
        qtbot.addWidget(card)
        assert card._rpm_value.text() == "—"
        assert card._speed_value.text() == "—"
        assert card._temp_value.text() == "—"

    def test_zero_is_rendered_not_dropped(self, qtbot):
        """The 0-vs-None falsy trap: a genuinely stopped fan reads 0."""
        card = FanControlCard(_vm(rpm=0, pwm_pct=0, temp_c=0.0))
        qtbot.addWidget(card)
        assert card._rpm_value.text() == "0"
        assert card._speed_value.text() == "0%"
        assert card._temp_value.text() == "0°C"

    def test_fan_count_names_the_blast_radius(self, qtbot):
        """The card acts on a whole control, so it must say how many fans that is."""
        card = FanControlCard(_vm(fan_count=3))
        qtbot.addWidget(card)
        assert card._count.text() == "3 fans"

    def test_fan_count_singular(self, qtbot):
        card = FanControlCard(_vm(fan_count=1))
        qtbot.addWidget(card)
        assert card._count.text() == "1 fan"

    def test_object_names_are_unique_per_control(self, qtbot):
        a = FanControlCard(_vm(control_id="c1"))
        b = FanControlCard(_vm(control_id="c2"))
        qtbot.addWidget(a)
        qtbot.addWidget(b)
        assert a.objectName() == "FanCard_Root_c1"
        assert b.objectName() == "FanCard_Root_c2"
        assert a._rpm_value.objectName() != b._rpm_value.objectName()

    def test_read_only_card_id_is_sanitised_into_the_object_name(self, qtbot):
        """Fan ids carry ':' separators; a raw one would make a malformed name."""
        card = FanControlCard(_vm(control_id="readonly:nvidia_gpu:0000:01:00.0"))
        qtbot.addWidget(card)
        assert card.objectName() == "FanCard_Root_readonly_nvidia_gpu_0000_01_00_0"


class TestStateChip:
    @pytest.mark.parametrize(
        ("state", "text", "css"),
        [
            (FanState.NORMAL, "Auto", "SuccessChip"),
            (FanState.OVERRIDE, "Override active", "WarningChip"),
            (FanState.LOW_RPM, "Low RPM", "WarningChip"),
            (FanState.STALE, "Stale", "WarningChip"),
            (FanState.STALL, "Stall", "CriticalChip"),
            (FanState.OFFLINE, "Offline", "CriticalChip"),
        ],
    )
    def test_chip_pairs_a_word_with_the_colour(self, qtbot, state, text, css):
        """Text alongside colour — the state is never colour-only (WCAG 1.4.1)."""
        card = FanControlCard(_vm(state=state))
        qtbot.addWidget(card)
        assert card._state_chip.text() == text
        assert card._state_chip.property("class") == css

    def test_unassigned_card_does_not_claim_auto(self, qtbot):
        """Nothing is driving it, so "Auto" would be a lie."""
        card = FanControlCard(_vm(is_unassigned=True, state=FanState.NORMAL))
        qtbot.addWidget(card)
        assert card._state_chip.text() == "Not controlled"
        assert card._state_chip.property("class") == "InfoChip"

    def test_unassigned_card_still_shows_a_real_fault(self, qtbot):
        """The informational relabel must not mask a stall."""
        card = FanControlCard(_vm(is_unassigned=True, state=FanState.STALL))
        qtbot.addWidget(card)
        assert card._state_chip.text() == "Stall"


class TestCurvePreview:
    def test_curve_is_handed_to_the_preview(self, qtbot):
        curve = CurveConfig(id="cv", name="C", sensor_id="s", points=[CurvePoint(30, 20)])
        card = FanControlCard(_vm(curve=curve))
        qtbot.addWidget(card)
        card.show()
        assert card._preview.curve is curve
        assert card._preview.isVisible()
        assert not card._no_curve.isVisible()

    def test_no_curve_shows_a_placeholder_instead(self, qtbot):
        card = FanControlCard(_vm(curve=None))
        qtbot.addWidget(card)
        card.show()
        assert not card._preview.isVisible()
        assert card._no_curve.isVisible()
        assert card._no_curve.text() == "No curve assigned"

    def test_placeholder_text_is_specific_to_why(self, qtbot):
        unassigned = FanControlCard(_vm(is_unassigned=True, curve=None))
        read_only = FanControlCard(_vm(is_read_only=True, curve=None))
        qtbot.addWidget(unassigned)
        qtbot.addWidget(read_only)
        assert unassigned._no_curve.text() == "Not assigned to a control"
        assert read_only._no_curve.text() == "No fan control available for this device"


class TestEditAffordance:
    def test_edit_emits_the_control_id(self, qtbot):
        card = FanControlCard(_vm(control_id="c7"))
        qtbot.addWidget(card)
        seen: list[str] = []
        card.edit_requested.connect(seen.append)
        card._edit_btn.click()
        assert seen == ["c7"]

    def test_unassigned_card_offers_assign(self, qtbot):
        card = FanControlCard(_vm(is_unassigned=True, control_id=""))
        qtbot.addWidget(card)
        card.show()
        assert card._edit_btn.text() == "Assign…"
        assert card._edit_btn.isVisible()

    def test_read_only_card_hides_edit(self, qtbot):
        """A read-only fan cannot be assigned to a control (DEC-102), so an Edit
        button would be a dead control."""
        card = FanControlCard(_vm(is_read_only=True))
        qtbot.addWidget(card)
        card.show()
        assert not card._edit_btn.isVisible()

    def test_card_exposes_no_write_affordance(self, qtbot):
        """The card must stay read-only: the override session lives on the Controls
        page, and a second one here would race it (DEC-163). Checks every input
        widget class, not just sliders — a spin box would be just as much of a
        second writer."""
        from PySide6.QtWidgets import (
            QAbstractSlider,
            QAbstractSpinBox,
            QCheckBox,
            QComboBox,
            QLineEdit,
        )

        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        for widget_cls in (
            QAbstractSlider,
            QAbstractSpinBox,
            QCheckBox,
            QComboBox,
            QLineEdit,
        ):
            assert card.findChildren(widget_cls) == [], widget_cls.__name__
        # The only signal it may expose is the navigation one.
        assert not hasattr(card, "manual_toggled")
        assert not hasattr(card, "manual_value_changed")


class TestUpdateInPlace:
    def test_update_vm_rerenders_without_rebuilding(self, qtbot):
        """Cards are reconciled at 1 Hz, so updating must not recreate widgets."""
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        rpm_label = card._rpm_value
        card.update_vm(_vm(rpm=900, pwm_pct=30, state=FanState.OVERRIDE))
        assert card._rpm_value is rpm_label  # same widget, new text
        assert rpm_label.text() == "900"
        assert card._speed_value.text() == "30%"
        assert card._state_chip.text() == "Override active"

    def test_update_vm_is_idempotent(self, qtbot):
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        card.update_vm(_vm())
        card.update_vm(_vm())
        assert card._rpm_value.text() == "1200"
        assert card._state_chip.property("class") == "SuccessChip"

    def test_edit_signal_follows_a_changed_control_id(self, qtbot):
        card = FanControlCard(_vm(control_id="c1"))
        qtbot.addWidget(card)
        seen: list[str] = []
        card.edit_requested.connect(seen.append)
        card.update_vm(_vm(control_id="c2"))
        card._edit_btn.click()
        assert seen == ["c2"]


class TestMemberlessControl:
    def test_no_fans_reads_as_unconfigured_not_faulted(self, qtbot):
        """A control the user just created has no members yet. Painting a red
        critical "Offline" chip there would both alarm the user mid-setup and make
        a genuine OFFLINE (an expected fan reporting nothing) indistinguishable."""
        card = FanControlCard(_vm(fan_count=0, state=FanState.NORMAL))
        qtbot.addWidget(card)
        assert card._state_chip.text() == "No fans"
        assert card._state_chip.property("class") == "InfoChip"
        assert card._count.text() == "No fans assigned"

    def test_a_real_fault_still_wins_over_the_unconfigured_label(self, qtbot):
        card = FanControlCard(_vm(fan_count=0, state=FanState.STALL))
        qtbot.addWidget(card)
        assert card._state_chip.text() == "Stall"


class TestUntrustedText:
    """Control names come from the profile and fan labels from user aliases. The
    footer and warnings view already treat such strings as untrusted markup; the
    card must not be the one widget that does not."""

    def test_label_is_rendered_as_plain_text(self, qtbot):
        from PySide6.QtCore import Qt

        card = FanControlCard(_vm(label="<b>PWNED</b>"))
        qtbot.addWidget(card)
        assert card._name.textFormat() == Qt.TextFormat.PlainText
        assert card._name.text() == "<b>PWNED</b>"

    def test_edit_tooltip_escapes_the_label(self, qtbot):
        card = FanControlCard(_vm(label="<b>PWNED</b>"))
        qtbot.addWidget(card)
        assert "&lt;b&gt;PWNED&lt;/b&gt;" in card._edit_btn.toolTip()
        assert "<b>" not in card._edit_btn.toolTip()


class TestMetricDividers:
    """DEC-225: the RPM/SPEED/TEMP trio sits flush on the card surface, separated
    by 1px hairline rules rather than an inset panel of a different tone (the
    app_bg blocks that used to sit behind the values)."""

    @staticmethod
    def _dividers(card):
        return [f for f in card.findChildren(QFrame) if f.property("class") == "CardDivider"]

    def test_two_hairline_dividers_between_the_three_metrics(self, qtbot):
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        dividers = self._dividers(card)
        assert len(dividers) == 2
        # A rule, not a block: pinned to 1px wide.
        for d in dividers:
            assert d.minimumWidth() == 1
            assert d.maximumWidth() == 1

    def test_divider_object_names_are_unique_per_control(self, qtbot):
        a = FanControlCard(_vm(control_id="c1"))
        b = FanControlCard(_vm(control_id="c2"))
        qtbot.addWidget(a)
        qtbot.addWidget(b)
        a_names = {d.objectName() for d in self._dividers(a)}
        assert a_names == {"FanCard_Divider_rpmSpeed_c1", "FanCard_Divider_speedTemp_c1"}
        assert a_names.isdisjoint({d.objectName() for d in self._dividers(b)})

    def test_metric_area_declares_no_opaque_fill(self, qtbot):
        """No metric label or its column wrapper may hard-code an opaque inline
        background: they must inherit the card's ``.Card`` surface so the values
        sit flush on it. (This guards the inline-stylesheet side only; the global
        blanket-``QWidget`` leak itself is guarded by
        ``test_theme_system.TestBackgroundLeakGuard``.)"""
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        for label in (card._rpm_value, card._speed_value, card._temp_value):
            for widget in (label, label.parentWidget()):
                sheet = widget.styleSheet()
                assert "background-color" not in sheet
                if "background:" in sheet:
                    assert "transparent" in sheet
