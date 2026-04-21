"""Tests for SummaryCard session range display."""

import pytest
from PySide6.QtWidgets import QApplication

from control_ofc.ui.widgets.summary_card import SummaryCard

_app = None


@pytest.fixture(autouse=True)
def _ensure_qapp():
    global _app
    if QApplication.instance() is None:
        _app = QApplication([])
    yield


def _is_visible(card: SummaryCard) -> bool:
    """Check visibility intent — not isVisible() which requires parent shown."""
    return not card._range_label.isHidden()


class TestSummaryCardRange:
    def test_range_label_hidden_by_default(self):
        card = SummaryCard("CPU", "45.0°C")
        assert card._range_label.isHidden()

    def test_set_range_shows_label(self):
        card = SummaryCard("CPU", "55.0°C")
        card.set_range(32.0, 78.5)
        assert _is_visible(card)
        assert "32.0" in card._range_label.text()
        assert "78.5" in card._range_label.text()

    def test_set_range_none_hides_label(self):
        card = SummaryCard("CPU", "55.0°C")
        card.set_range(32.0, 78.5)
        assert _is_visible(card)
        card.set_range(None, None)
        assert not _is_visible(card)

    def test_set_range_partial_none_hides_label(self):
        card = SummaryCard("CPU", "55.0°C")
        card.set_range(32.0, None)
        assert not _is_visible(card)

    def test_range_updates_text(self):
        card = SummaryCard("CPU", "55.0°C")
        card.set_range(30.0, 60.0)
        text1 = card._range_label.text()
        card.set_range(25.0, 85.0)
        text2 = card._range_label.text()
        assert text1 != text2
        assert "25.0" in text2
        assert "85.0" in text2

    def test_range_arrows_present(self):
        card = SummaryCard("CPU", "55.0°C")
        card.set_range(30.0, 80.0)
        text = card._range_label.text()
        assert "↓" in text  # down arrow
        assert "↑" in text  # up arrow
