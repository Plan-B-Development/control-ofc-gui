"""Pre-release review follow-ups for the audit-2026-07-29 remediation (v2.32.2).

Closes the small P3 gaps the 4-reviewer release pass surfaced:
- security: the escape sweep missed the `notes` path inside its own tooltip
  function (unknown-driver fallback embeds the raw daemon chip name);
- security: `grant.pwm_percent` was not int-coerced before `QSlider.setValue`;
- gui-finesse: the manual slider min was not re-synced on an in-place floor
  change in `update_control`;
- test-reviewer: the dashboard apply path was not verified to arm the *owned*
  reset timer (a regression to `QTimer.singleShot` would slip through).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.knowledge.sensor_knowledge import classify_sensor, format_sensor_tooltip
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.control_card import ControlCard


def _floored_card(qtbot, floor: float = 30.0) -> ControlCard:
    control = LogicalControl(
        id="pump",
        name="Pump",
        curve_id="c1",
        minimum_pct=floor,
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    curves = [CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)]
    card = ControlCard(control, curves)
    qtbot.addWidget(card)
    return card


def test_sensor_tooltip_escapes_the_notes_path():
    # The unknown-driver fallback embeds the raw daemon chip name in `notes`; the
    # tooltip auto-detects rich text, so that must be escaped like the sibling
    # Driver/ID lines (security P3 — the sweep missed its own function).
    c = classify_sensor(chip_name="evil<b>chip", label="", temp_type=None)
    assert any("<b>" in n for n in c.notes)  # the raw name reaches notes
    tip = format_sensor_tooltip(c)
    assert "<b>" not in tip  # no raw tag survives into the tooltip
    assert "&lt;b&gt;" in tip  # it is escaped, not dropped


def test_reflect_manual_applied_coerces_a_non_int_grant(qtbot):
    # A non-conforming daemon could send pwm_percent as a str; QSlider.setValue
    # needs an int. PySide6 accepts a Python float natively, so a str is the real
    # trigger — the int() coercion prevents a TypeError in the _on_take_result slot
    # (security P3; a float arg would pass even without the fix — test-tests catch).
    card = _floored_card(qtbot)
    card._manual_btn.setChecked(True)
    card.reflect_manual_applied("30")  # a str — must not raise (int-coerced)
    assert card._manual_slider.value() == 30


def test_update_control_resyncs_slider_min_while_manual(qtbot):
    # Belt-and-suspenders (gui-finesse P3): if a control's floor ever changed in
    # place while manual is active, the slider minimum tracks it.
    card = _floored_card(qtbot, floor=20.0)
    card._manual_btn.setChecked(True)
    assert card._manual_slider.minimum() == 20
    higher = LogicalControl(
        id="pump",
        name="Pump",
        curve_id="c1",
        minimum_pct=40.0,
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    card.update_control(higher, [])
    assert card._manual_slider.minimum() == 40


def test_apply_path_arms_the_owned_reset_timer(qtbot):
    # A regression from the owned timer back to QTimer.singleShot would leave
    # _reset_apply_timer unused (and uncancellable). Prove the apply path arms it
    # (test-reviewer gap — cleanup can only cancel a timer the path actually uses).
    ps = MagicMock()
    ps.active_id = "p0"
    ps.activate.return_value = MagicMock(activated=False, error="nope")
    page = DashboardPage(state=AppState(), profile_service=ps)
    qtbot.addWidget(page)
    assert not page._reset_apply_timer.isActive()

    page._activate_profile_by_id("p1")  # failure branch → "Failed" + timer.start()

    assert page._reset_apply_timer.isActive()
    assert page._reset_apply_timer.isSingleShot()
    assert page._apply_btn.text() == "Failed"
