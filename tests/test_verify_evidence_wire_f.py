"""WIRE-f — the evidence behind a fan-control test result (W-DIAGG Run C, G6).

Both verify endpoints return the header's state before and after the test. Until
this change the GUI rendered only the RPM from it and dropped the rest — the
``pwm_enable`` pair that is the direct evidence of a BIOS/EC reclaim, the duty the
header read back, the GPU's applied speed and zero-RPM setting. And the manual
said the result panel showed "the initial → final RPM and ``pwm_enable`` values",
which it did not.

Written against the shared helpers' own constants where the literal would also
be satisfied by the defect, and through the page call sites as well as the
builders (`testing-method.md`: extracting a rule does not test the call site).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    ConnectionState,
    FanReading,
    GpuVerifyResult,
    GpuVerifyState,
    HwmonHeader,
    HwmonVerifyResult,
    HwmonVerifyState,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.fan_mode import (
    LABEL_AUTOMATIC,
    LABEL_FULL_SPEED,
    LABEL_MANUAL,
    pwm_enable_label,
    pwm_enable_with_value,
)
from control_ofc.services.header_inspector_view import build_header_inspector_view
from control_ofc.services.verify_evidence import (
    NOT_MEASURED_TEXT,
    ROW_APPLIED_SPEED,
    ROW_DUTY,
    ROW_FAN_MODE,
    ROW_RPM,
    ROW_ZERO_RPM,
    UNKNOWN_TEXT,
    VerifyEvidence,
    gpu_verify_evidence,
    hwmon_verify_evidence,
)
from control_ofc.services.verify_view import (
    _GPU_OUTCOMES,
    _GPU_ZERO_RPM_UNCONFIRMED,
    build_gpu_verify_result_view,
    build_verify_result_view,
    gpu_result_outcome,
)
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.system_state_page import SystemStatePage
from control_ofc.ui.widgets.verify_evidence_panel import (
    TITLE_HIDE,
    TITLE_SHOW,
    VerifyEvidencePanel,
)

HEADER_ID = "hwmon:nct6798:nct6775.656:pwm2:CHA_FAN1"
GPU_ID = "0000:03:00.0"


def _hwmon_result(**kw) -> HwmonVerifyResult:
    defaults = dict(
        header_id=HEADER_ID,
        result="effective",
        initial_state=HwmonVerifyState(pwm_enable=1, pwm_raw=102, pwm_percent=40, rpm=800),
        final_state=HwmonVerifyState(pwm_enable=1, pwm_raw=255, pwm_percent=100, rpm=1500),
        test_pwm_percent=100,
        wait_seconds=5,
    )
    defaults.update(kw)
    return HwmonVerifyResult(**defaults)


def _pmfw_result(**kw) -> GpuVerifyResult:
    defaults = dict(
        gpu_id=GPU_ID,
        result="effective",
        initial_state=GpuVerifyState(applied_speed_pct=30, rpm=0, zero_rpm_enabled=True),
        final_state=GpuVerifyState(applied_speed_pct=60, rpm=1400, zero_rpm_enabled=True),
        test_speed_pct=60,
        wait_seconds=8,
        fan_control_method="pmfw_curve",
    )
    defaults.update(kw)
    return GpuVerifyResult(**defaults)


def _row(evidence: VerifyEvidence, label: str):
    matches = [r for r in evidence.rows if r.label == label]
    assert len(matches) == 1, f"{label!r} rows: {matches}"
    return matches[0]


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


# ── The shared fan-mode vocabulary ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        (0, LABEL_FULL_SPEED),
        (1, LABEL_MANUAL),
        (2, LABEL_AUTOMATIC),
        # nct6775's Thermal Cruise..Smart Fan IV: every one is automatic, and
        # the label never claims to know which.
        (3, LABEL_AUTOMATIC),
        (5, LABEL_AUTOMATIC),
        (7, LABEL_AUTOMATIC),
    ],
)
def test_the_kernel_vocabulary_labels_every_documented_mode(mode, label):
    assert pwm_enable_label(mode) == label


def test_an_unreported_mode_has_no_label_and_a_negative_one_is_shown_as_itself():
    assert pwm_enable_label(None) is None
    assert pwm_enable_with_value(None) is None
    # Outside the kernel's definition — rendered, never dropped (273-i).
    assert pwm_enable_label(-1) == "mode -1"
    assert pwm_enable_with_value(-1) == "mode -1"


def test_the_evidence_form_carries_the_raw_value_that_tells_automatic_modes_apart():
    assert pwm_enable_with_value(5) == f"{LABEL_AUTOMATIC} — mode 5"
    assert pwm_enable_with_value(2) != pwm_enable_with_value(5)


def test_the_header_inspector_uses_the_shared_vocabulary():
    """The inspector's own table said "Automatic (firmware curve)" for 2-5 and
    nothing at all for 6+. It now reads the shared helper, so a mode the old
    table did not list is still named."""
    header = HwmonHeader(id=HEADER_ID, label="CHA_FAN1", is_writable=True)
    for mode in (0, 1, 2, 6):
        view = build_header_inspector_view(
            header, reading=FanReading(id=HEADER_ID, source="hwmon", pwm_enable_mode=mode)
        )
        rows = {r.label: r.value for r in view.live_rows}
        assert rows["Control mode"] == pwm_enable_label(mode)


# ── hwmon evidence ───────────────────────────────────────────────────────────


def test_a_completed_hwmon_verify_reports_mode_duty_and_rpm_before_and_after():
    ev = hwmon_verify_evidence(_hwmon_result())
    assert [r.label for r in ev.rows] == [ROW_FAN_MODE, ROW_DUTY, ROW_RPM]
    assert _row(ev, ROW_FAN_MODE).before == pwm_enable_with_value(1)
    assert _row(ev, ROW_DUTY).before == "40% (raw 102)"
    assert _row(ev, ROW_DUTY).after == "100% (raw 255)"
    assert (_row(ev, ROW_RPM).before, _row(ev, ROW_RPM).after) == ("800", "1500")
    # The summary says what the test did — the hwmon path used to say nothing.
    assert ev.summary == "Test: set 100% and waited 5 s; RPM 800 → 1500; fan mode stayed manual"


def test_a_reclaim_is_visible_in_both_the_summary_and_the_table():
    """The direct evidence of a BIOS/EC taking the header back: manual → 2+.
    This is the pair the old result dropped."""
    ev = hwmon_verify_evidence(
        _hwmon_result(
            result="pwm_enable_reverted",
            final_state=HwmonVerifyState(pwm_enable=5, pwm_percent=40, rpm=800),
        )
    )
    assert "fan mode ended automatic (firmware), not manual" in ev.summary
    assert _row(ev, ROW_FAN_MODE).after == pwm_enable_with_value(5)
    assert _row(ev, ROW_FAN_MODE).before != _row(ev, ROW_FAN_MODE).after


def test_a_reclaim_of_a_header_that_started_automatic_is_not_called_stayed():
    """Reviewer F1: the test writes manual, so the end mode is judged against
    manual. A header nothing controlled starts automatic; a BIOS that takes it
    back ends it automatic too, and the daemon calls that `pwm_enable_reverted`."""
    ev = hwmon_verify_evidence(
        _hwmon_result(
            result="pwm_enable_reverted",
            initial_state=HwmonVerifyState(pwm_enable=5, pwm_percent=40, rpm=800),
            final_state=HwmonVerifyState(pwm_enable=5, pwm_percent=40, rpm=800),
        )
    )
    assert "stayed" not in ev.summary
    assert "fan mode ended automatic (firmware), not manual" in ev.summary


def test_the_full_speed_alias_under_a_pass_is_not_worded_as_a_reclaim():
    """Reviewer F4 (DEC-326): the daemon passes a 100% test that ends in mode 0
    as `effective`. The summary reads that verdict; the same end mode under a
    reclaim verdict keeps the reclaim wording (the opposite arm)."""
    alias = dict(
        test_pwm_percent=100,
        final_state=HwmonVerifyState(pwm_enable=0, pwm_raw=255, pwm_percent=100, rpm=1500),
    )
    passed = hwmon_verify_evidence(_hwmon_result(result="effective", **alias))
    assert "not a reclaim" in passed.summary
    assert "not manual" not in passed.summary
    reverted = hwmon_verify_evidence(_hwmon_result(result="pwm_enable_reverted", **alias))
    assert "not a reclaim" not in reverted.summary
    assert "fan mode ended full speed (no control), not manual" in reverted.summary


def test_an_unreported_value_is_unknown_never_zero():
    ev = hwmon_verify_evidence(
        _hwmon_result(
            initial_state=HwmonVerifyState(rpm=800),
            final_state=HwmonVerifyState(rpm=1500),
        )
    )
    assert _row(ev, ROW_FAN_MODE).before == UNKNOWN_TEXT
    assert _row(ev, ROW_FAN_MODE).after == UNKNOWN_TEXT
    assert _row(ev, ROW_DUTY).before == UNKNOWN_TEXT
    # An end mode nobody measured is not claimed; the RPM that was measured is.
    assert "fan mode" not in ev.summary
    assert "RPM 800 → 1500" in ev.summary
    # The end mode is what the summary judges (against the manual the test
    # wrote), so an unknown START with a known end still reports it.
    known_end = hwmon_verify_evidence(
        _hwmon_result(
            initial_state=HwmonVerifyState(rpm=800),
            final_state=HwmonVerifyState(pwm_enable=1, rpm=1500),
        )
    )
    assert "fan mode stayed manual" in known_end.summary


def test_a_result_with_no_state_at_all_has_no_table_and_no_summary():
    ev = hwmon_verify_evidence(HwmonVerifyResult(header_id=HEADER_ID, result="effective"))
    assert ev.rows == ()
    assert ev.summary == ""


def test_a_verify_stopped_for_a_pump_claims_no_after_reading():
    """DEC-418: a verify stopped mid-run never finished its settle."""
    ev = hwmon_verify_evidence(_hwmon_result(result="pump_protected_mid_run"))
    assert ev.rows, "the before column is still evidence"
    assert all(r.after == NOT_MEASURED_TEXT for r in ev.rows)
    assert "1500" not in ev.summary
    assert "stopped before it measured anything" in ev.summary
    # Reviewer F2: the daemon's pump check can stop the verify BEFORE its
    # write, so the duty planned for an ordinary fan is never claimed as set.
    assert "set" not in ev.summary
    assert f"{_hwmon_result().test_pwm_percent}%" not in ev.summary


def test_the_hwmon_view_carries_the_evidence_and_shows_its_summary():
    result = _hwmon_result()
    view = build_verify_result_view(result)
    assert view.evidence == hwmon_verify_evidence(result)
    assert view.evidence.summary in view.text.splitlines()
    # One RPM statement, not the old "RPM:" line beside the new summary.
    assert view.text.count("800 → 1500") == 1


# ── GPU evidence ─────────────────────────────────────────────────────────────


def test_a_pmfw_result_has_a_zero_rpm_row_and_no_fan_mode_row():
    ev = gpu_verify_evidence(_pmfw_result())
    labels = [r.label for r in ev.rows]
    assert labels == [ROW_APPLIED_SPEED, ROW_RPM, ROW_ZERO_RPM]
    assert _row(ev, ROW_APPLIED_SPEED).after == "60%"
    assert _row(ev, ROW_ZERO_RPM).after == "enabled"
    assert ev.summary == "Test: set 60% through the PMFW fan curve and waited 8 s; RPM 0 → 1400"


def test_a_legacy_pwm1_result_has_a_fan_mode_row_and_no_zero_rpm_row():
    ev = gpu_verify_evidence(
        _pmfw_result(
            initial_state=GpuVerifyState(applied_speed_pct=30, rpm=900, pwm_enable=2),
            final_state=GpuVerifyState(applied_speed_pct=60, rpm=1400, pwm_enable=1),
            fan_control_method="hwmon_pwm",
        )
    )
    assert ROW_ZERO_RPM not in [r.label for r in ev.rows]
    assert _row(ev, ROW_FAN_MODE).after == pwm_enable_with_value(1)
    assert "through pwm1" in ev.summary


def test_a_rejected_gpu_write_claims_no_set_duty_and_no_rpm_change():
    """Reviewer F3: `write_failed` carries `wait_seconds: 0` and an RPM read
    straight after the rejected write — a reading, not an effect."""
    ev = gpu_verify_evidence(
        _pmfw_result(
            result="write_failed",
            wait_seconds=0,
            final_state=GpuVerifyState(rpm=1400),
        )
    )
    assert ev.summary == (
        "Test: tried to set 60% through the PMFW fan curve; the write was rejected"
    )
    assert "→" not in ev.summary
    # The opposite arm: a completed test keeps its RPM change.
    assert "RPM 0 → 1400" in gpu_verify_evidence(_pmfw_result()).summary


def test_an_unrecognised_gpu_method_is_named_as_itself():
    ev = gpu_verify_evidence(_pmfw_result(fan_control_method="future_path"))
    assert "through future_path" in ev.summary


# ── zero_rpm_suppressed reads the card's own setting ─────────────────────────


def test_zero_rpm_suppressed_is_normal_only_when_the_card_reports_zero_rpm_on():
    on = _pmfw_result(result="zero_rpm_suppressed")
    assert gpu_result_outcome(on) == _GPU_OUTCOMES["zero_rpm_suppressed"]


@pytest.mark.parametrize("zero_rpm", [False, None])
def test_zero_rpm_suppressed_is_not_called_normal_without_the_setting(zero_rpm):
    result = _pmfw_result(
        result="zero_rpm_suppressed",
        final_state=GpuVerifyState(applied_speed_pct=60, rpm=0, zero_rpm_enabled=zero_rpm),
    )
    outcome = gpu_result_outcome(result)
    assert outcome == _GPU_ZERO_RPM_UNCONFIRMED
    assert "normal" not in outcome.summary
    assert outcome.chip_class != _GPU_OUTCOMES["zero_rpm_suppressed"].chip_class


def test_the_gpu_view_orders_result_summary_fixes_and_restore_note():
    result = _pmfw_result(result="no_rpm_effect", restore_failed=True)
    view = build_gpu_verify_result_view(result, fix_lines=["Check the fan cable."])
    lines = view.text.splitlines()
    assert lines[0] == f"Result: {_GPU_OUTCOMES['no_rpm_effect'].summary}"
    assert lines[1] == gpu_verify_evidence(result).summary
    assert lines[2] == "• To fix: Check the fan cable."
    assert lines[3].startswith("Note: the GPU fan could not be restored")
    assert view.chip_class == _GPU_OUTCOMES["no_rpm_effect"].chip_class
    assert view.evidence == gpu_verify_evidence(result)


# ── The panel ────────────────────────────────────────────────────────────────


def _panel(qtbot) -> VerifyEvidencePanel:
    panel = VerifyEvidencePanel("Test_Section_evidence")
    qtbot.addWidget(panel)
    return panel


def test_the_panel_is_hidden_until_it_has_evidence(qtbot):
    panel = _panel(qtbot)
    assert panel.isHidden()
    panel.set_evidence(VerifyEvidence(summary="Test: x"))
    assert panel.isHidden(), "a summary with no rows has nothing to disclose"


def test_the_panel_renders_the_table_collapsed_and_the_header_toggles_it(qtbot):
    panel = _panel(qtbot)
    ev = hwmon_verify_evidence(_hwmon_result())
    panel.set_evidence(ev)
    assert not panel.isHidden()
    assert not panel.is_expanded()
    assert panel.cells()[0] == ["", "Before", "After"]
    assert panel.cells()[1:] == [[r.label, r.before, r.after] for r in ev.rows]

    header = panel.findChild(object, "Test_Section_evidence_Header")
    assert TITLE_SHOW in header.text()
    header.click()
    assert panel.is_expanded()
    assert TITLE_HIDE in header.text()


def test_a_new_result_starts_collapsed_and_replaces_the_old_rows(qtbot):
    panel = _panel(qtbot)
    panel.set_evidence(hwmon_verify_evidence(_hwmon_result()))
    panel.findChild(object, "Test_Section_evidence_Header").click()
    gpu = gpu_verify_evidence(_pmfw_result())
    panel.set_evidence(gpu)
    assert not panel.is_expanded()
    assert TITLE_SHOW in panel.findChild(object, "Test_Section_evidence_Header").text()
    assert [c[0] for c in panel.cells()[1:]] == [r.label for r in gpu.rows]
    panel.set_evidence(None)
    assert panel.isHidden()
    assert panel.cells() == []


def test_every_cell_has_a_unique_object_name(qtbot):
    from PySide6.QtWidgets import QLabel

    panel = _panel(qtbot)
    panel.set_evidence(hwmon_verify_evidence(_hwmon_result()))
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    names = [w.objectName() for w in panel.findChildren(QLabel)]
    assert names and all(names)
    assert len(names) == len(set(names))


# ── The call sites ───────────────────────────────────────────────────────────


def _system_state(qtbot, *, client=None):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    page = SystemStatePage(
        state=state, diagnostics_service=DiagnosticsService(state), client=client
    )
    qtbot.addWidget(page)
    return page


def _shown_under(panel, label) -> bool:
    """Visible beside its result label. System State's panels sit inside a
    section that starts collapsed, so visibility to the PAGE would be false for
    a correctly shown panel; the container the two share is the honest frame."""
    container = label.parentWidget()
    assert panel.parentWidget() is container, "the evidence belongs under its result"
    return panel.isVisibleTo(container)


def test_system_state_shows_the_hwmon_evidence_and_an_error_clears_it(qtbot):
    page = _system_state(qtbot, client=object())
    result = _hwmon_result()
    page._on_verify_ok(result, HEADER_ID)
    panel = page._verify_evidence
    assert _shown_under(panel, page._verify_result_label)
    assert panel.cells()[1:] == [
        [r.label, r.before, r.after] for r in hwmon_verify_evidence(result).rows
    ]
    assert hwmon_verify_evidence(result).summary in page._verify_result_label.text()

    page._on_verify_error("error", "boom", HEADER_ID)
    assert not _shown_under(panel, page._verify_result_label)
    _flush(page)


def test_system_state_clears_the_hwmon_evidence_when_a_new_verify_cannot_start(qtbot):
    page = _system_state(qtbot, client=None)
    page._show_verify_result(_hwmon_result())
    assert _shown_under(page._verify_evidence, page._verify_result_label)
    page._run_pwm_verify()  # no header selected / no client: a message, no result
    assert page._verify_result_label.text()
    assert not _shown_under(page._verify_evidence, page._verify_result_label)
    _flush(page)


def test_system_state_shows_the_gpu_evidence_and_the_zero_rpm_verdict(qtbot):
    page = _system_state(qtbot, client=object())
    result = _pmfw_result(
        result="zero_rpm_suppressed",
        final_state=GpuVerifyState(applied_speed_pct=60, rpm=0, zero_rpm_enabled=False),
    )
    page._on_gpu_verify_ok(result)
    label = page._gpu_verify_result_label
    assert _GPU_ZERO_RPM_UNCONFIRMED.summary in label.text()
    assert label.property("class") == _GPU_ZERO_RPM_UNCONFIRMED.chip_class
    assert _shown_under(page._gpu_verify_evidence, label)
    assert ROW_ZERO_RPM in [c[0] for c in page._gpu_verify_evidence.cells()]
    # And the hwmon panel is untouched by a GPU result.
    assert page._verify_evidence.isHidden()

    page._on_gpu_verify_error("error", "boom")
    assert not _shown_under(page._gpu_verify_evidence, label)
    _flush(page)


def test_the_hardware_page_shows_the_evidence_and_any_other_message_clears_it(qtbot):
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    page = HardwarePage(state=s, diagnostics_service=DiagnosticsService(s), client=None)
    qtbot.addWidget(page)
    result = _hwmon_result()
    page._on_verify_ok(result, HEADER_ID)
    panel = page._diag_evidence
    assert panel.isVisibleTo(page)
    assert panel.cells()[1:] == [
        [r.label, r.before, r.after] for r in hwmon_verify_evidence(result).rows
    ]
    page._show_diag_message("Port probe finished.")
    assert not panel.isVisibleTo(page)
    assert page._diag_result.text() == "Port probe finished."
    _flush(page)
