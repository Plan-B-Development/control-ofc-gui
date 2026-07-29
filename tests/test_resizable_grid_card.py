"""ResizableGridCard — the shared DEC-128 (font-derived floor) + DEC-129
(per-card user resize via a corner grip) base that ControlCard and CurveCard now
subclass instead of each re-implementing ~150 lines (meta-audit reuse pass).

Headless tests per the GUI component standard: assert the sizing outcomes and
signal wiring, not just that it constructs.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from control_ofc.ui.components.cards import Card
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard
from control_ofc.ui.widgets.resizable_grid_card import ResizableGridCard

_QWIDGETSIZE_MAX = 16777215


class _StubCard(ResizableGridCard):
    """Minimal concrete subclass: a labelled content layout + the base machinery."""

    def __init__(self, item_id: str = "item-1") -> None:
        super().__init__()
        self._init_grid_card(item_id, f"Stub_Grip_{item_id}")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("content"))
        self.apply_card_size(11)


def test_control_and_curve_cards_are_resizable_grid_cards():
    # The extraction's contract: both cards are proper Card subclasses through
    # the shared base — no hand-rolled QFrame + class="Card" twin.
    assert issubclass(ControlCard, ResizableGridCard)
    assert issubclass(CurveCard, ResizableGridCard)
    assert issubclass(ResizableGridCard, Card)


def test_grid_card_is_a_card(qtbot):
    card = _StubCard()
    qtbot.addWidget(card)
    assert card.property("class") == "Card"  # inherited from Card


def test_grip_created_with_object_name(qtbot):
    card = _StubCard("abc")
    qtbot.addWidget(card)
    assert card._grip is not None
    assert card._grip.objectName() == "Stub_Grip_abc"


def test_theme_sizing_fixes_width_and_floors_height(qtbot):
    card = _StubCard()
    qtbot.addWidget(card)
    # Width fixed (grid-aligned); height a floor (min > 0, max unbounded).
    assert card.minimumWidth() == card.maximumWidth()
    assert card.minimumHeight() > 0
    assert card.maximumHeight() == _QWIDGETSIZE_MAX  # no override
    assert card.user_size is None


def test_user_size_override_fixes_both_axes(qtbot):
    card = _StubCard()
    qtbot.addWidget(card)
    applied = card.set_user_size(400, 400)
    assert card.user_size == applied
    assert card.minimumWidth() == card.maximumWidth() == applied[0]
    assert card.minimumHeight() == card.maximumHeight() == applied[1]


def test_clear_user_size_restores_theme_floor(qtbot):
    card = _StubCard()
    qtbot.addWidget(card)
    card.set_user_size(400, 400)
    card.clear_user_size()
    assert card.user_size is None
    assert card.maximumHeight() == _QWIDGETSIZE_MAX  # floor restored, no fixed max


def test_grip_release_signal_emits_resized_with_item_id(qtbot):
    # Fire the grip's OWN signal, not the handler, so the _init_grid_card wiring
    # (resize_finished -> _on_grip_resized -> resized) is exercised — a dropped or
    # reversed connect() would be invisible if we only called the handler.
    card = _StubCard("ctl-9")
    qtbot.addWidget(card)
    seen: list[tuple] = []
    card.resized.connect(lambda cid, w, h: seen.append((cid, w, h)))
    card._grip.resize_finished.emit(320, 288)
    assert seen == [("ctl-9", 320, 288)]


def test_grip_reset_signal_emits_size_reset_and_clears_override(qtbot):
    # Fire reset_requested so the reset_requested -> _on_grip_reset -> size_reset
    # connection is exercised (not just the handler body).
    card = _StubCard("ctl-9")
    qtbot.addWidget(card)
    card.set_user_size(400, 400)
    seen: list[str] = []
    card.size_reset.connect(seen.append)
    card._grip.reset_requested.emit()
    assert seen == ["ctl-9"]
    assert card.user_size is None
