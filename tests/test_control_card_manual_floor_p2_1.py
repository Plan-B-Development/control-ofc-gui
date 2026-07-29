"""P2-1 (audit 2026-07-29): the manual-override slider must not request or display
a value below the daemon-enforced floor, and must reflect the value the daemon
actually applied.

The reported bug: on a CPU/pump control (30% floor), dragging the slider to 10%
showed "10%" on the slider + label while the daemon floor-clamped the override and
the fan ran at 30% — right beside the card's own "Min: 30%" badge.
"""

from __future__ import annotations

from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    CurveType,
    LogicalControl,
)
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


def test_manual_slider_min_clamps_to_floor(qtbot):
    card = _floored_card(qtbot)
    card._manual_btn.setChecked(True)  # enter manual
    assert card._manual_slider.minimum() == 30
    # A sub-floor request is impossible — Qt clamps setValue up to the minimum.
    card._manual_slider.setValue(10)
    assert card._manual_slider.value() == 30
    assert card._manual_pct_label.text() == "30%"


def test_reflect_manual_applied_shows_granted_value(qtbot):
    card = _floored_card(qtbot)
    card._manual_btn.setChecked(True)
    card.reflect_manual_applied(30)  # daemon granted 30 for a clamped request
    assert card._manual_slider.value() == 30
    assert card._manual_pct_label.text() == "30%"


def test_reflect_manual_applied_is_noop_when_not_manual(qtbot):
    card = _floored_card(qtbot)
    before = card._manual_slider.value()
    card.reflect_manual_applied(80)  # not in manual → ignored, no phantom override
    assert card._manual_slider.value() == before


def test_slider_min_tracks_role_derived_floor(qtbot):
    # With no user floor, the clamp still tracks the role-derived floor
    # (control_minimum_pct is 20% for a chassis/openfan member) rather than
    # hardcoding 30 — the clamp follows the same effective floor as the Min badge.
    card = _floored_card(qtbot, floor=0.0)
    card._manual_btn.setChecked(True)
    assert card._manual_slider.minimum() == 20
