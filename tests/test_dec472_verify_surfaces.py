"""DEC-472: System State's verify surfaces, the GPU Fan Control row, demo chips.

`PTA-r` — a verify message that is not a result sets its own colour.
`PTA-s` — a sweep names the headers whose restore failed.
`GPU-d` — a legacy card's method says "verify only", as the Overview does.
`VOLT-e` — the demo's chip list and counts derive from its headers.
"""

from __future__ import annotations

from collections import Counter

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    ConnectionState,
    GpuDiagnosticsInfo,
    GpuVerifyResult,
    HardwareDiagnosticsResult,
    HwmonHeader,
    HwmonVerifyResult,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import _DEMO_VOLTAGES, DemoService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.system_state_view import build_safety_gpu_vm
from control_ofc.services.verify_view import (
    SWEEP_NOT_RESTORED,
    gpu_outcome_for,
    outcome_for,
    sweep_restore_failed_note,
    verify_sweep_chip_class,
)
from control_ofc.ui.pages.system_state_page import SystemStatePage


def _page(qtbot, headers=()):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    if headers:
        state.set_hwmon_headers(list(headers))
    page = SystemStatePage(
        state=state, diagnostics_service=DiagnosticsService(state), client=object()
    )
    qtbot.addWidget(page)
    return page


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


# ── PTA-r ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("category", "expected"), [("error", "CriticalChip"), ("unavailable", "")])
def test_a_verify_error_does_not_inherit_the_last_results_colour(qtbot, category, expected):
    page = _page(qtbot)
    label = page._verify_result_label
    page._show_verify_result(HwmonVerifyResult(header_id="h", result="effective"))
    assert label.property("class") == outcome_for("effective").chip_class == "SuccessChip", (
        "precondition: the previous result painted the label green"
    )

    page._on_verify_error(category, "boom", "h")

    assert label.property("class") == expected
    _flush(page)


@pytest.mark.parametrize(("category", "expected"), [("error", "CriticalChip"), ("unavailable", "")])
def test_a_gpu_verify_error_does_not_inherit_the_last_results_colour(qtbot, category, expected):
    page = _page(qtbot)
    label = page._gpu_verify_result_label
    page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result="effective"))
    assert label.property("class") == gpu_outcome_for("effective").chip_class == "SuccessChip"

    page._on_gpu_verify_error(category, "boom")

    assert label.property("class") == expected
    _flush(page)


def test_a_refusal_before_the_call_resets_the_colour_too(qtbot):
    """Same label, same defect: "No writable header selected" was green too."""
    page = _page(qtbot)
    label = page._verify_result_label
    page._show_verify_result(HwmonVerifyResult(header_id="h", result="effective"))
    assert label.property("class") == "SuccessChip", "precondition"

    page._run_pwm_verify()  # the combo is empty, so it refuses

    assert label.text() == "No writable header selected"
    assert label.property("class") == ""
    _flush(page)


# ── PTA-s ────────────────────────────────────────────────────────────────────


def test_a_failed_restore_raises_a_clean_sweep_to_a_warning():
    assert verify_sweep_chip_class(["effective"]) == "SuccessChip", "precondition"
    assert verify_sweep_chip_class(["effective"], restore_failed=True) == "WarningChip"
    assert verify_sweep_chip_class(["rpm_unavailable"], restore_failed=True) == "WarningChip"
    # Never lowers a louder verdict.
    assert verify_sweep_chip_class(["pwm_enable_reverted"], restore_failed=True) == "CriticalChip"


def test_the_closing_note_counts_the_headers():
    assert sweep_restore_failed_note(0) == ""
    assert sweep_restore_failed_note(1).startswith("1 header could not be put back after its test")
    assert sweep_restore_failed_note(2).startswith("2 headers could not be put back after their")
    assert "re-activate your profile" in sweep_restore_failed_note(2)


def _sweep(qtbot):
    page = _page(
        qtbot,
        headers=[
            HwmonHeader(id="pwm1", is_writable=True),
            HwmonHeader(id="pwm2", is_writable=True),
        ],
    )
    page._ensure_verify_worker = lambda: True  # type: ignore[method-assign]  # no real thread
    emitted: list[str] = []
    page._verify_request.connect(emitted.append)
    page._run_pwm_verify_all()
    assert emitted == ["pwm1"], "precondition: the sweep must actually be running"
    return page


def test_the_sweep_summary_names_a_header_left_at_its_test_duty(qtbot):
    page = _sweep(qtbot)
    label = page._verify_all_progress_label

    page._on_verify_ok(
        HwmonVerifyResult(header_id="pwm1", result="effective", restore_failed=True), "pwm1"
    )
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="effective"), "pwm2")

    assert page._verify_all_total == 0, "precondition: the sweep finished"
    lines = label.text().splitlines()
    assert f"  • pwm1: OK — {SWEEP_NOT_RESTORED}" in lines
    assert "  • pwm2: OK" in lines, "a header that was put back carries no marker"
    assert lines[-1] == sweep_restore_failed_note(1)
    assert label.property("class") == "WarningChip"
    _flush(page)


def test_the_next_sweep_does_not_carry_the_last_ones_failure(qtbot):
    page = _sweep(qtbot)
    page._on_verify_ok(
        HwmonVerifyResult(header_id="pwm1", result="effective", restore_failed=True), "pwm1"
    )
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="effective"), "pwm2")
    assert SWEEP_NOT_RESTORED in page._verify_all_progress_label.text(), "precondition"

    page._run_pwm_verify_all()
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="effective"), "pwm2")

    assert SWEEP_NOT_RESTORED not in page._verify_all_progress_label.text()
    assert page._verify_all_progress_label.property("class") == "SuccessChip"
    _flush(page)


# ── GPU-d ────────────────────────────────────────────────────────────────────


def _gpu_row(method: str):
    diag = HardwareDiagnosticsResult(
        gpu=GpuDiagnosticsInfo(model_name="RX 6800", fan_control_method=method)
    )
    return next(r for r in build_safety_gpu_vm(diag).gpu_rows if r.label == "Fan Control")


def test_a_legacy_cards_method_says_verify_only_and_stays_ok():
    row = _gpu_row("hwmon_pwm")
    assert row.value == "hwmon_pwm (verify only)"
    assert row.state == "ok", "verify and reset do work there"


@pytest.mark.parametrize("method", ["pmfw_curve", "read_only", "none"])
def test_every_other_method_is_shown_as_the_wire_sends_it(method):
    assert _gpu_row(method).value == method


# ── VOLT-e ───────────────────────────────────────────────────────────────────


def test_the_demo_chip_list_is_the_daemons_derivation_of_its_headers():
    """As `hw_diagnostics.rs::chips_detected`: one entry per chip with headers,
    each counting them, and both totals over the same list."""
    demo = DemoService()
    hwmon = demo.hardware_diagnostics().hwmon
    headers = demo.hwmon_headers()
    per_chip = Counter(h.chip_name for h in headers)

    assert {c.chip_name: c.header_count for c in hwmon.chips_detected} == dict(per_chip)
    assert hwmon.total_headers == len(headers)
    assert hwmon.writable_headers == sum(1 for h in headers if h.is_writable)


def test_every_chip_the_voltages_panel_shows_is_in_the_chip_list():
    """`VOLT-e`'s symptom: the it87952's rails beside a list naming only the it8696."""
    detected = {c.chip_name for c in DemoService().hardware_diagnostics().hwmon.chips_detected}
    rail_chips = {chip for chip, *_ in _DEMO_VOLTAGES}
    assert "it87952" in rail_chips, "precondition: the demo shows the second chip's rails"
    assert rail_chips <= detected


def test_the_second_chips_headers_have_fans_and_no_pump_label():
    demo = DemoService()
    ids = {h.id for h in demo.hwmon_headers() if h.chip_name == "it87952"}
    assert len(ids) == 3
    assert ids <= {f.id for f in demo.fans()}, "a demo header with no reading looks dead"
    assert not any("PUMP" in i.upper() for i in ids), "the demo's one pump is the Kraken"
