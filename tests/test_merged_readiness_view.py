"""Tests for the merged Hardware-readiness view widget (DEC-206) — actionable
cards, the security-boundary render split, the Passed group, and action signals."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from control_ofc.ui.readiness_merge import ACTION_DEEP_LINK, ActionSpec, MergedReadinessItem
from control_ofc.ui.widgets.merged_readiness_view import MergedReadinessView


def _items() -> list[MergedReadinessItem]:
    return [
        MergedReadinessItem(
            code="cpu_sensor_missing",
            severity="critical",
            rank=3,
            headline="No CPU temperature sensor detected",
            component="cpu",
            plain_detail="Thermal safety cannot track CPU heat.",
            affects_safety=True,
            action=ActionSpec(ACTION_DEEP_LINK, "Pick a CPU sensor", "preferred_cpu"),
            source="daemon",
        ),
        MergedReadinessItem(
            code="vendor_quirk",
            severity="warning",
            rank=2,
            headline="Board/chip quirk detected",
            html_detail="Review the quirk <b>notes</b>.",
            doc_url="https://example.test/guide",
            doc_title="Guide",
            source="gui",
        ),
        MergedReadinessItem(
            code="cpu_sensor_present",
            severity="ok",
            rank=0,
            headline="CPU temperature source found",
            is_ok=True,
            source="daemon",
        ),
    ]


def test_actionable_items_are_cards_ok_items_are_in_passed_group(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    assert v.findChild(QFrame, "MergedReadiness_Card_cpu_sensor_missing") is not None
    assert v.findChild(QFrame, "MergedReadiness_Card_vendor_quirk") is not None
    # The ok item is NOT a card — it lives in the collapsed Passed group.
    assert v.findChild(QFrame, "MergedReadiness_Card_cpu_sensor_present") is None
    assert v.findChild(QLabel, "MergedReadiness_Passed_cpu_sensor_present") is not None


def test_daemon_detail_is_plaintext_gui_detail_is_richtext(qtbot):
    """Security boundary: the daemon string renders PlainText, the GUI fix RichText."""
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    plain = v.findChild(QLabel, "MergedReadiness_Plain_cpu_sensor_missing")
    assert plain.textFormat() == Qt.TextFormat.PlainText
    html = v.findChild(QLabel, "MergedReadiness_Html_vendor_quirk")
    assert html.textFormat() == Qt.TextFormat.RichText


def test_headline_is_plaintext(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    headline = v.findChild(QLabel, "MergedReadiness_Headline_cpu_sensor_missing")
    assert headline.textFormat() == Qt.TextFormat.PlainText


def test_action_button_emits_spec(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    btn = v.findChild(QPushButton, "MergedReadiness_Action_cpu_sensor_missing")
    assert btn is not None
    assert btn.text() == "Pick a CPU sensor"
    with qtbot.waitSignal(v.action_requested, timeout=500) as sig:
        btn.click()
    assert sig.args[0].target == "preferred_cpu"


def test_ok_item_has_no_action_button(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    assert v.findChild(QPushButton, "MergedReadiness_Action_cpu_sensor_present") is None


def test_verdict_reflects_worst_severity_and_count(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    text = v._verdict_label.text()
    assert "Not ready" in text  # critical wording
    assert "to fix" in text  # count appended (1 critical + 1 warning = 2)


def test_impact_flag_rendered(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    flags = v.findChild(QLabel, "MergedReadiness_Flag_cpu_sensor_missing_0")
    assert flags is not None
    assert "affects safety" in flags.text()


def test_set_items_replaces_prior_render(qtbot):
    v = MergedReadinessView()
    qtbot.addWidget(v)
    v.set_items(_items())
    v.set_items([])  # empty → no stale cards survive
    assert v.findChild(QFrame, "MergedReadiness_Card_cpu_sensor_missing") is None
