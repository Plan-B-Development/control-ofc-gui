"""Qt-free view-models for the alert surfaces (DEC-282).

``next_action_for_warning``'s tests moved here with the function. It lived in
``ui/widgets/warnings_view.py`` until three surfaces needed it; the rule now sits in
``services/alerts_view.py`` and these are its tests.
"""

from __future__ import annotations

import pytest

from control_ofc.services.alerts import AlertCondition, AlertLedger
from control_ofc.services.alerts_view import (
    build_active_cards,
    build_recovered_rows,
    build_status_vm,
    level_glyph,
    next_action_for_warning,
)


def _cond(key: str, level: str = "warning", title: str = "t", detail: str = "d") -> AlertCondition:
    return AlertCondition(
        key=key, level=level, source="fan", component="cpu_fan", title=title, detail=detail
    )


class TestNextActionForWarning:
    @pytest.mark.parametrize(
        "warning, has_action",
        [
            ({"_key": "sensor_stale:s1", "source": "sensor"}, True),
            ({"_key": "fan_stale:f1", "source": "fan"}, True),
            ({"_key": "fan_stall:f1", "source": "fan"}, True),
            ({"_key": "api_version_skew", "source": "api"}, True),
            # Source-only fallback (key prefix unrecognised) still yields an action.
            ({"_key": "weird:1", "source": "fan"}, True),
            ({"_key": "weird:1", "source": "sensor"}, True),
            # Outside the taxonomy entirely — no advice is better than invented advice.
            ({"_key": "mystery:1", "source": "mystery"}, False),
            ({}, False),
        ],
    )
    def test_action_presence(self, warning, has_action):
        assert (next_action_for_warning(warning) is not None) is has_action

    def test_stall_action_is_fan_specific_not_generic_stale(self):
        """A stall and a stale fan share source="fan"; the key must win."""
        stall = next_action_for_warning({"_key": "fan_stall:f1", "source": "fan"})
        stale = next_action_for_warning({"_key": "fan_stale:f1", "source": "fan"})
        assert stall != stale
        assert "0 RPM" in stall


class TestStatusVM:
    def test_all_clear_when_nothing_is_present(self):
        vm = build_status_vm([], [])
        assert vm.has_active is False
        assert vm.summary == "✓  No active alerts"
        assert vm.state == "ok"
        assert vm.recent_note == ""

    def test_counts_split_critical_from_warning(self):
        led = AlertLedger()
        led.reconcile([_cond("a", "error"), _cond("b", "warning"), _cond("c", "warning")], now=1.0)
        vm = build_status_vm(led.present(), [])

        assert (vm.critical_count, vm.warning_count) == (1, 2)
        assert vm.summary == "✖ 1 critical   ⚠ 2 warnings"
        assert vm.state == "crit", "the worst severity drives the treatment"

    def test_singular_wording_for_one_warning(self):
        led = AlertLedger()
        led.reconcile([_cond("b", "warning")], now=1.0)
        assert build_status_vm(led.present(), []).summary == "⚠ 1 warning"

    def test_headline_is_the_most_severe_alert(self):
        led = AlertLedger()
        led.reconcile(
            [
                _cond("w", "warning", detail="just a warning"),
                _cond("e", "error", detail="the bad one"),
            ],
            now=1.0,
        )
        assert build_status_vm(led.present(), []).headline == "the bad one"

    def test_recent_note_appears_only_when_nothing_is_active(self):
        """Brief §24: "✓ No active alerts / Recent alert: … recovered at …"."""
        led = AlertLedger()
        led.reconcile([_cond("k", title="CPU_FAN stall")], now=100.0)
        led.reconcile([], now=101.0)
        vm = build_status_vm(led.present(), led.recovered())

        assert vm.has_active is False
        assert "CPU_FAN stall" in vm.recent_note
        assert "recovered at" in vm.recent_note

    def test_recent_note_is_suppressed_while_something_is_still_wrong(self):
        """A live alert is the thing to read; history would only compete with it."""
        led = AlertLedger()
        led.reconcile([_cond("old")], now=100.0)
        led.reconcile([_cond("new")], now=101.0)
        vm = build_status_vm(led.present(), led.recovered())

        assert vm.has_active is True
        assert vm.recent_note == ""


class TestAlertCards:
    def test_active_card_carries_everything_the_model_knows(self):
        led = AlertLedger()
        led.reconcile([_cond("fan_stall:cpu_fan", "error", title="cpu_fan stall")], now=100.0)
        led.reconcile([_cond("fan_stall:cpu_fan", "error", title="cpu_fan stall")], now=160.0)
        card = build_active_cards(led.present())[0]

        assert card.state_label == "ACTIVE"
        assert card.pill_state == "crit"
        assert card.glyph == level_glyph("error")
        assert card.component == "cpu_fan"
        assert card.first_detected != card.last_detected, "both ends are shown separately"
        assert card.suggested_action is not None
        assert card.is_present is True

    def test_acknowledged_active_card_is_muted_but_still_present(self):
        led = AlertLedger()
        led.reconcile([_cond("k", "error")], now=100.0)
        led.acknowledge("k", now=101.0)
        card = build_active_cards(led.present())[0]

        assert card.state_label == "ACKNOWLEDGED"
        assert card.pill_state == "neutral", "seen, so it stops shouting"
        assert card.is_present is True, "but the condition has not gone anywhere"
        assert card.acknowledged is True

    def test_recovered_rows_are_compact_and_newest_first(self):
        led = AlertLedger()
        for i, title in enumerate(["first", "second"]):
            led.reconcile([_cond(f"k{i}", title=title)], now=float(i))
            led.reconcile([], now=float(i) + 0.5)
        rows = build_recovered_rows(led.recovered())

        assert [r.title for r in rows] == ["second", "first"]
        assert rows[0].text == "✓  RECOVERED   second"
        assert rows[0].recovered_at != "—"
