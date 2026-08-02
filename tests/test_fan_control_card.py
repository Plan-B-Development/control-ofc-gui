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
from control_ofc.ui.components.cards import Card
from control_ofc.ui.theme import ThemeTokens, active_theme, build_stylesheet, default_dark_theme
from control_ofc.ui.widgets.card_metrics import fan_tile_width
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


class TestTileDensity:
    """DEC-238: the tile was 267x244 at 10pt, ~29% of its height whitespace.

    These pin the structural causes rather than a pixel count, so a regression is
    named ("the padding is being charged twice again") instead of just "taller".
    """

    def test_qss_padding_is_not_charged_on_top_of_the_layout_inset(self, qtbot):
        """The old tile paid ``.Card``'s 12px QSS padding *and* a 12/10 layout
        margin, putting 25px between the border and the text. The tile opts out
        of the QSS padding via density="tile" and owns its inset once, so the
        widget's own margins must be the 1px border and nothing more.

        The stylesheet is applied **to the widgets**, not to the QApplication:
        nothing else in the suite installs it, so without this the margins are
        (0,0,0,0) and the assertion is inert — and a global ``apply_theme`` here
        would mutate Qt state for every later test (it made a shiboken teardown
        UAF deterministic once already, DEC-236).
        """
        qss = build_stylesheet(default_dark_theme())
        card = FanControlCard(_vm())
        plain = Card()
        qtbot.addWidget(card)
        qtbot.addWidget(plain)
        card.setStyleSheet(qss)
        plain.setStyleSheet(qss)
        assert card.property("density") == "tile"
        # Exactly the 1px border — not "at most 1", which 0 also satisfies.
        margins = card.contentsMargins()
        for side in (margins.left(), margins.top(), margins.right(), margins.bottom()):
            assert side == 1, f"tile is charging QSS padding again: {margins}"
        # And prove the rule is what's doing it: an ordinary Card under the same
        # sheet still pays the full 12px padding + 1px border.
        assert plain.contentsMargins().left() == 13

    def test_tile_height_stays_off_the_old_budget(self, qtbot):
        """A backstop on the whole composition.

        The bound is calibrated **unstyled**, which is how the suite runs: the
        full pre-DEC-238 structure (Edit in its own row + a stretch-taking
        CurvePreview) measures 188px here, against ~139 now. The 244px figure in
        the ADR is the *styled* number and does not apply to this assertion.

        It is a backstop for a wholesale revert only — a partial one (just the
        Edit row back, or just the band back) lands near 150-155 and clears this.
        Those are caught individually by ``test_edit_is_a_ghost_button_beside_the_name``
        and ``test_curve_and_placeholder_tiles_are_the_same_height``.
        """
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        card.show()
        assert card.height() < 180

    def test_curve_and_placeholder_tiles_are_the_same_height(self, qtbot):
        """The band is a one-slot stack precisely so a tile whose curve paints a
        sparkline and one showing "No curve assigned" cannot differ — otherwise
        every tile in the flow row inherits the tallest one's slack."""
        curve = CurveConfig(id="cv", name="C", sensor_id="s", points=[CurvePoint(30, 20)])
        with_curve = FanControlCard(_vm(curve=curve))
        without = FanControlCard(_vm(curve=None))
        qtbot.addWidget(with_curve)
        qtbot.addWidget(without)
        with_curve.show()
        without.show()
        assert with_curve.height() == without.height()

    def test_read_only_tile_matches_a_normal_one(self, qtbot):
        """Read-only hides Edit, and Edit is the tallest thing in the title row,
        so hiding it used to collapse the row and leave read-only tiles short."""
        normal = FanControlCard(_vm())
        read_only = FanControlCard(_vm(is_read_only=True))
        qtbot.addWidget(normal)
        qtbot.addWidget(read_only)
        normal.show()
        read_only.show()
        assert not read_only._edit_btn.isVisible()
        assert read_only.height() == normal.height()

    def test_hidden_edit_gives_its_width_back_to_the_name(self, qtbot):
        """The row height is stabilised by the name's minimum height, not by
        retaining the hidden button's size — ``retainSizeWhenHidden`` would hold
        both axes, and read-only tiles are exactly the ones with the longest
        labels (GPU model names), so the reserved width ate the model number."""
        normal = FanControlCard(_vm(label="NVIDIA GeForce RTX 4080 Fan"))
        read_only = FanControlCard(_vm(label="NVIDIA GeForce RTX 4080 Fan", is_read_only=True))
        qtbot.addWidget(normal)
        qtbot.addWidget(read_only)
        normal.show()
        read_only.show()
        # Only the name width is asserted: both tiles are setFixedWidth to the
        # same value unconditionally, so comparing tile widths here would restate
        # test_tiles_share_one_width and could not fail for this regression.
        assert read_only._name.width() > normal._name.width()

    def test_tiles_share_one_width(self, qtbot):
        """Content-sized hints produced a ragged run (267/267/267/251 measured);
        a fixed width makes the flow grid form real columns."""
        a = FanControlCard(_vm(label="CPU", control_id="c1"))
        b = FanControlCard(_vm(label="A Much Longer Control Name", control_id="c2"))
        qtbot.addWidget(a)
        qtbot.addWidget(b)
        assert a.width() == b.width() == fan_tile_width(active_theme().base_font_size_pt)

    def test_width_follows_a_theme_font_change(self, qtbot):
        """A theme can carry a new base font size; a tile still sized for the old
        one would clip its readings or sit out of column."""
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        card.set_theme(ThemeTokens(name="big", base_font_size_pt=16))
        assert card.width() == fan_tile_width(16)
        assert card.width() > fan_tile_width(10)

    def test_metric_columns_split_the_row_equally(self, qtbot):
        """The three columns take a third of the row each, not their content
        width. The old layout sized them to content and let a trailing spacer
        swallow the surplus, so the two hairlines landed at a different x on
        every tile and the grid read as ragged rather than columnar.

        Pins the outcome, not the mechanism: Qt's surplus distribution already
        equalises columns whenever nothing else competes for the space, so this
        stays green for any layout that produces equal columns and fails for the
        pre-DEC-238 one (verified by mutation).
        """
        a = FanControlCard(_vm(rpm=1, pwm_pct=1, temp_c=1.0, control_id="c1"))
        b = FanControlCard(_vm(rpm=10000, pwm_pct=100, temp_c=-40.0, control_id="c2"))
        qtbot.addWidget(a)
        qtbot.addWidget(b)
        a.show()
        b.show()
        for card in (a, b):
            widths = [
                card._rpm_value.parentWidget().width(),
                card._speed_value.parentWidget().width(),
                card._temp_value.parentWidget().width(),
            ]
            # <=1px apart: the row rarely divides by three exactly, and Qt hands
            # the remainder to one column. Content-sized columns differ by tens.
            assert max(widths) - min(widths) <= 1, f"metric columns are content-sized: {widths}"
        a_x = sorted(d.x() for d in TestMetricDividers._dividers(a))
        b_x = sorted(d.x() for d in TestMetricDividers._dividers(b))
        assert a_x == b_x, "divider positions drift with content"


class TestTitleRow:
    """DEC-238 moved Edit into the title row and the state chip onto the meta row."""

    def test_edit_is_a_ghost_button_beside_the_name(self, qtbot):
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        card.show()
        assert card._edit_btn.property("variant") == "ghost"
        # Same row as the name, not a row of its own below the card body.
        assert card._edit_btn.y() == card._name.y()
        assert card._edit_btn.x() > card._name.x()

    def test_edit_reads_as_an_action_not_as_body_text(self, qtbot):
        """A ghost button is borderless, so colour is the only thing separating
        the tile's one action from the muted metadata beside it. At the secondary
        tone Edit was the same colour and weight as the fan count one row below
        and did not read as a control at all.

        Widget-scoped stylesheet, as in the padding guard — the suite installs no
        app sheet, so an unstyled palette check would pass either way."""
        tokens = default_dark_theme()
        card = FanControlCard(_vm())
        qtbot.addWidget(card)
        card.setStyleSheet(build_stylesheet(tokens))
        card.show()
        edit_colour = card._edit_btn.palette().color(card._edit_btn.foregroundRole()).name()
        count_colour = card._count.palette().color(card._count.foregroundRole()).name()
        assert edit_colour.lower() == tokens.text_primary.lower()
        assert edit_colour.lower() != count_colour.lower()

    def test_edit_still_emits_from_the_title_row(self, qtbot):
        """The relocation must not have cost the deep-link."""
        card = FanControlCard(_vm(control_id="c9"))
        qtbot.addWidget(card)
        seen: list[str] = []
        card.edit_requested.connect(seen.append)
        card._edit_btn.click()
        assert seen == ["c9"]

    def test_state_chip_sits_below_the_name_not_beside_it(self, qtbot):
        """Name + chip + Edit all in one row left the worst case ("Unassigned" +
        "Not controlled" + "Assign…") 62px for the name and elided it to
        "Unas…". The chip rides the meta row, which was holding one short token
        across ~180px, so it costs no height."""
        card = FanControlCard(_vm(is_unassigned=True, control_id="", label="Unassigned"))
        qtbot.addWidget(card)
        card.show()
        assert card._state_chip.y() > card._name.y()
        assert card._state_chip.y() == card._count.y()
        # The name is what identifies the tile: it must not be elided here.
        assert card._name.elided_text() == "Unassigned"

    def test_long_name_elides_but_keeps_its_full_text_and_tooltip(self, qtbot):
        long_name = "Front Radiator Intake Trio (push configuration)"
        card = FanControlCard(_vm(label=long_name))
        qtbot.addWidget(card)
        card.show()
        card.update_vm(_vm(label=long_name))  # tooltip is decided at the live width
        assert card._name.text() == long_name  # data intact
        assert card._name.elided_text() != long_name  # display truncated
        assert long_name in card._name.toolTip()

    def test_name_that_fits_carries_no_tooltip(self, qtbot):
        """A tooltip repeating a name already fully visible is noise; it exists
        only to recover what elision took away."""
        card = FanControlCard(_vm(label="CPU"))
        qtbot.addWidget(card)
        card.show()
        card.update_vm(_vm(label="CPU"))
        assert card._name.toolTip() == ""

    def test_name_tooltip_escapes_untrusted_markup(self, qtbot):
        """Qt renders a tooltip as rich text when it looks like markup, and the
        label is fed profile/alias text."""
        card = FanControlCard(_vm(label="<b>PWNED</b>" * 6))
        qtbot.addWidget(card)
        card.show()
        card.update_vm(_vm(label="<b>PWNED</b>" * 6))
        assert "&lt;b&gt;PWNED&lt;/b&gt;" in card._name.toolTip()
        assert "<b>PWNED" not in card._name.toolTip()

    def test_tooltips_do_not_leak_html_entities(self, qtbot):
        """Escaping alone renders *plain* — Qt's mightBeRichText() looks for a
        '<' and escaping removes every one, so `CPU & AIO` displayed as
        `CPU &amp;amp; AIO`. '&' is ordinary in fan names; '<' is not, so this
        was the common case failing, not the adversarial one."""
        from PySide6.QtGui import Qt as QtGui_Qt

        long_amp = "Front & Top & Rear & Side Radiator Fans"
        card = FanControlCard(_vm(label=long_amp))
        qtbot.addWidget(card)
        card.show()
        card.update_vm(_vm(label=long_amp))
        for tip in (card._name.toolTip(), card._edit_btn.toolTip()):
            assert "&amp;amp;" not in tip
            assert QtGui_Qt.mightBeRichText(tip), f"would render as plain text: {tip!r}"

    def test_tooltip_stays_one_line_and_keeps_its_whitespace(self, qtbot):
        """The rich-text path is what makes the escape decode, but Qt ties word
        wrap to it (``QTipLabel`` calls ``setWordWrap(mightBeRichText(text))``)
        and its parser collapses whitespace. Unguarded, the fix for the entity
        leak reshaped a 442x40 tooltip into a 145x94 block and single-spaced any
        name with a double space — in the one surface meant to reproduce a name
        the tile had to elide."""
        from PySide6.QtGui import QTextDocument

        spaced = "Front  Double  Space"
        card = FanControlCard(_vm(label=spaced))
        qtbot.addWidget(card)
        card.show()
        card.update_vm(_vm(label=spaced))
        tip = card._edit_btn.toolTip()  # always set, unlike the name's
        assert "white-space" in tip, "no wrap/whitespace guard on the tooltip"
        doc = QTextDocument()
        doc.setHtml(tip)
        assert spaced in doc.toPlainText(), f"whitespace collapsed: {doc.toPlainText()!r}"


class TestSpeedCaption:
    """DEC-204's "duty is never misread as commanded PWM" moved from the value
    into the caption (DEC-238) — the column header is what names the quantity."""

    def test_commanded_pwm_reads_speed(self, qtbot):
        card = FanControlCard(_vm(pwm_pct=45, duty_pct=None))
        qtbot.addWidget(card)
        assert card._speed_caption.text() == "SPEED"
        assert card._speed_value.text() == "45%"

    def test_measured_duty_relabels_the_caption(self, qtbot):
        card = FanControlCard(_vm(pwm_pct=None, duty_pct=55))
        qtbot.addWidget(card)
        assert card._speed_caption.text() == "DUTY"
        assert card._speed_value.text() == "55%"
        assert "not a value the daemon commanded" in card._speed_caption.toolTip()

    def test_caption_resets_when_a_commanded_value_arrives(self, qtbot):
        """Cards are reconciled in place at 1 Hz. A tile that showed duty and then
        gained a commanded PWM must stop calling it DUTY — a stale caption would
        label a commanded value as a measurement."""
        card = FanControlCard(_vm(pwm_pct=None, duty_pct=55))
        qtbot.addWidget(card)
        card.update_vm(_vm(pwm_pct=45, duty_pct=None))
        assert card._speed_caption.text() == "SPEED"
        assert card._speed_caption.toolTip() == ""

    def test_unknown_speed_keeps_the_neutral_caption(self, qtbot):
        card = FanControlCard(_vm(pwm_pct=None, duty_pct=None))
        qtbot.addWidget(card)
        assert card._speed_caption.text() == "SPEED"
        assert card._speed_value.text() == "—"
