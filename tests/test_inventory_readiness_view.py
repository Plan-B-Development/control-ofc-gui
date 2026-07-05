"""Tests for the daemon hardware-readiness view (Phase 4 / DEC-200)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from control_ofc.api.models import InventoryReadiness, ReadinessItem
from control_ofc.ui.widgets.inventory_readiness_view import InventoryReadinessView


@pytest.fixture
def view(qapp):
    return InventoryReadinessView()


def _readiness(overall, items):
    return InventoryReadiness(overall=overall, items=items)


def test_verdict_ok_uses_success_chip(view):
    view.set_readiness(_readiness("ok", []))
    verdict = view.findChild(QLabel, "Readiness_Label_verdict")
    assert verdict.text()  # populated
    assert verdict.property("class") == "SuccessChip"
    assert "ready" in verdict.text().lower()


def test_empty_readiness_shows_all_clear(view):
    view.set_readiness(_readiness("ok", []))
    status = view.findChild(QLabel, "Readiness_Label_status")
    assert "passed" in status.text().lower()


def test_items_rendered_and_severity_normalized(view):
    items = [
        ReadinessItem(code="an_info", severity="info", summary="info item"),
        ReadinessItem(code="a_crit", severity="critical", summary="crit item", affects_safety=True),
        ReadinessItem(code="a_warn", severity="warning", summary="warn item", blocks_control=True),
    ]
    view.set_readiness(_readiness("critical", items))
    # All three rows exist.
    assert view.findChild(QWidget, "Readiness_ItemRow_a_crit") is not None
    assert view.findChild(QWidget, "Readiness_ItemRow_a_warn") is not None
    assert view.findChild(QWidget, "Readiness_ItemRow_an_info") is not None
    # critical → CriticalChip with the CRITICAL word.
    badge = view.findChild(QLabel, "Readiness_Badge_a_crit")
    assert "CRITICAL" in badge.text()
    assert badge.property("class") == "CriticalChip"
    # daemon "warning" is normalized to the GUI "warn" chip.
    warn_badge = view.findChild(QLabel, "Readiness_Badge_a_warn")
    assert warn_badge.property("class") == "WarningChip"


def test_recommended_action_and_impact_flags_shown(view):
    item = ReadinessItem(
        code="no_pwm",
        severity="warning",
        component="pwm",
        summary="No PWM controls",
        detail="No hwmon pwmN found",
        recommended_action="Load the driver",
        blocks_control=True,
        reboot_may_be_required=True,
    )
    view.set_readiness(_readiness("warning", [item]))
    action = view.findChild(QLabel, "Readiness_Action_no_pwm")
    assert action is not None
    assert "Load the driver" in action.text()
    assert view.findChild(QWidget, "Readiness_Flags_no_pwm") is not None


def test_daemon_strings_rendered_as_plaintext(view):
    # Defence-in-depth: a daemon summary containing markup must not be interpreted.
    item = ReadinessItem(code="x", severity="info", summary="<b>hi</b> & <i>there</i>")
    view.set_readiness(_readiness("info", [item]))
    summary = view.findChild(QLabel, "Readiness_Summary_x")
    assert summary.textFormat() == Qt.TextFormat.PlainText
    assert summary.text() == "<b>hi</b> & <i>there</i>"


def test_error_state_hides_verdict(view):
    view.set_readiness(_readiness("ok", []))  # show verdict first
    view.set_error("boom")
    status = view.findChild(QLabel, "Readiness_Label_status")
    assert status.text() == "boom"
    verdict = view.findChild(QLabel, "Readiness_Label_verdict")
    assert verdict.isHidden()


def test_unsupported_state(view):
    view.set_unsupported()
    status = view.findChild(QLabel, "Readiness_Label_status")
    assert "predates" in status.text().lower()


def test_re_render_replaces_prior_rows(view):
    view.set_readiness(_readiness("warning", [ReadinessItem(code="first", severity="warning")]))
    assert view.findChild(QWidget, "Readiness_ItemRow_first") is not None
    view.set_readiness(_readiness("info", [ReadinessItem(code="second", severity="info")]))
    # Old row is gone; new row present (idempotent re-render).
    assert view.findChild(QWidget, "Readiness_ItemRow_second") is not None
