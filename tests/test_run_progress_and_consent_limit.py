"""R2 of the 2026-09-23 release batch: diagnostics on the wire.

* ``P8-bg`` — a running characterisation or discovery run now publishes its
  ``current_step`` (daemon >= 2.55.0), and both progress dialogs say what is
  being held instead of only the worst-case silence.
* ``PTA-i`` — the PWM Test Report's consent page interpolates the daemon's
  ``limits.diagnostic_max_temp_c`` rather than restating 85 °C.

Every assertion is a relationship to the wire value, never a restated literal,
so moving a daemon constant cannot leave a test asserting a stale number.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.api.models import (
    CharacterizationRun,
    ControlPathRun,
    RunStep,
    parse_capabilities,
    parse_characterization_run,
    parse_control_path_run,
)
from control_ofc.services.characterization_view import build_characterization_view
from control_ofc.services.control_path_view import build_control_path_view
from control_ofc.services.pwm_report import catalog as cat
from control_ofc.services.run_step_view import step_timing

NOW = 1_800_000_000_000


def _step(phase: str, *, index: int = 2, duty: int = 60, elapsed_ms: int = 4_200, max_ms=12_000):
    return RunStep(
        phase=phase,
        index=index,
        duty_pct=duty,
        started_unix_ms=NOW - elapsed_ms,
        max_ms=max_ms,
    )


# ── Parsing ──────────────────────────────────────────────────────────────────


def _wire_step() -> dict:
    return {
        "phase": "dwell",
        "index": 3,
        "duty_pct": 55,
        "started_unix_ms": NOW,
        "max_ms": 20_000,
    }


@pytest.mark.parametrize(
    "parse", [parse_characterization_run, parse_control_path_run], ids=["sweep", "discovery"]
)
def test_current_step_is_parsed_from_the_wire_on_both_runs(parse):
    wire = _wire_step()
    run = parse({"run_id": "r", "state": "running", "current_step": wire})
    step = run.current_step
    assert step is not None
    assert (step.phase, step.index, step.duty_pct, step.started_unix_ms, step.max_ms) == (
        wire["phase"],
        wire["index"],
        wire["duty_pct"],
        wire["started_unix_ms"],
        wire["max_ms"],
    )


@pytest.mark.parametrize(
    "parse", [parse_characterization_run, parse_control_path_run], ids=["sweep", "discovery"]
)
@pytest.mark.parametrize("raw", [None, "running", 7, []])
def test_an_absent_or_malformed_step_is_none_not_a_zeroed_step(parse, raw):
    """A zeroed step would render "step 1 of N, 0 %" about a run the daemon said
    nothing about — and a pre-2.55 daemon omits the field entirely."""
    body = {"run_id": "r", "state": "running"}
    if raw is not None:
        body["current_step"] = raw
    assert parse(body).current_step is None


def test_non_numeric_step_fields_fall_back_to_zero_rather_than_raising():
    run = parse_control_path_run(
        {"state": "running", "current_step": {"phase": "baseline", "index": "x", "max_ms": True}}
    )
    assert run.current_step is not None
    assert run.current_step.index == 0 and run.current_step.max_ms == 0


def test_the_diagnostic_limit_is_parsed_from_capabilities():
    caps = parse_capabilities({"limits": {"diagnostic_max_temp_c": 91.5}})
    assert caps.limits.diagnostic_max_temp_c == 91.5
    assert parse_capabilities({"limits": {}}).limits.diagnostic_max_temp_c is None
    assert parse_capabilities({}).limits.diagnostic_max_temp_c is None
    # A bool is an int in Python; it is not a temperature.
    garbage = parse_capabilities({"limits": {"diagnostic_max_temp_c": True}})
    assert garbage.limits.diagnostic_max_temp_c is None


# ── Timing words ─────────────────────────────────────────────────────────────


def test_timing_reports_elapsed_of_the_bound():
    step = _step("baseline", elapsed_ms=4_200, max_ms=12_000)
    assert step_timing(step, NOW) == "4s of up to 12s"


def test_timing_clamps_an_overrun_to_the_bound():
    """A phase can overrun its bound by I/O; "13s of up to 12s" reads as a stall."""
    step = _step("baseline", elapsed_ms=13_500, max_ms=12_000)
    assert step_timing(step, NOW) == "12s of up to 12s"


def test_timing_never_goes_negative_on_clock_skew():
    step = _step("baseline", elapsed_ms=-3_000)
    assert step_timing(step, NOW).startswith("0s of")


def test_a_sub_second_bound_rounds_up_rather_than_reading_zero():
    step = RunStep(phase="settle", max_ms=300)
    assert step_timing(step, NOW) == "up to 1s"


# ── Characterisation dialog ──────────────────────────────────────────────────


def _char_run(step: RunStep | None) -> CharacterizationRun:
    return CharacterizationRun(
        run_id="c",
        state="running",
        requested_points_pct=[30, 60, 100, 60, 30],
        settle_seconds=6,
        bidirectional=True,
        stability_seconds=20,
        current_step=step,
    )


@pytest.mark.parametrize("phase", ["settle", "dwell"])
def test_a_sweep_says_which_step_it_is_holding(phase):
    step = _step(phase, index=2, duty=100)
    run = _char_run(step)
    view = build_characterization_view(run, header_label="Pump", now_ms=NOW)
    text = view.status_text
    total = len(run.requested_points_pct)
    # The daemon's 0-based index is shown 1-based, against the plan length.
    assert f"step {step.index + 1} of {total}" in text
    assert f"{step.duty_pct}%" in text
    assert step_timing(step, NOW) in text
    # The worst-case explanation is for a daemon that cannot say what it is doing.
    assert "a pause that long is normal" not in text


def test_the_two_sweep_phases_are_worded_differently():
    words = {
        phase: build_characterization_view(
            _char_run(_step(phase)), header_label="P", now_ms=NOW
        ).status_text
        for phase in ("settle", "dwell")
    }
    assert words["settle"] != words["dwell"]
    assert "stability" in words["dwell"]


def test_an_unrecognised_sweep_phase_is_rendered_not_dropped():
    view = build_characterization_view(
        _char_run(_step("spin_down_check")), header_label="P", now_ms=NOW
    )
    assert "spin down check" in view.status_text


def test_an_older_daemon_keeps_the_worst_case_explanation():
    """DEC-337's client-side mitigation is still what a pre-2.55 daemon gets."""
    run = _char_run(None)
    text = build_characterization_view(run, header_label="P").status_text
    assert f"{run.settle_seconds + run.stability_seconds}s apart" in text


def test_a_terminal_sweep_ignores_a_stale_step():
    run = _char_run(_step("settle"))
    run.state = "complete"
    text = build_characterization_view(run, header_label="P", now_ms=NOW).status_text
    assert "Measuring" not in text


# ── Discovery dialog ─────────────────────────────────────────────────────────


def _discovery_run(step: RunStep | None) -> ControlPathRun:
    return ControlPathRun(
        run_id="d",
        state="running",
        requested_cycles=2,
        window_seconds=12,
        baseline_pct=40,
        perturbed_pct=70,
        current_step=step,
    )


@pytest.mark.parametrize("phase", ["settle_wait", "baseline", "perturbed"])
def test_discovery_says_which_window_it_is_holding(phase):
    step = _step(phase, index=2, duty=70)
    run = _discovery_run(step)
    text = build_control_path_view(run, header_label="Pump", now_ms=NOW).status_text
    # Discovery's cycle number is 1-based on the wire, and shown as-is.
    assert f"cycle {step.index} of {run.requested_cycles}" in text
    assert f"{step.duty_pct}%" in text
    assert step_timing(step, NOW) in text


def test_the_three_discovery_windows_are_worded_differently():
    texts = {
        build_control_path_view(
            _discovery_run(_step(phase)), header_label="P", now_ms=NOW
        ).status_text
        for phase in ("settle_wait", "baseline", "perturbed")
    }
    assert len(texts) == 3


def test_an_unrecognised_discovery_phase_is_rendered_not_dropped():
    text = build_control_path_view(
        _discovery_run(_step("extra_window")), header_label="P", now_ms=NOW
    ).status_text
    assert "extra window" in text


def test_discovery_without_a_step_keeps_its_old_text():
    run = _discovery_run(None)
    text = build_control_path_view(run, header_label="P").status_text
    assert f"{run.window_seconds}s" in text


# ── Consent text (PTA-i) ─────────────────────────────────────────────────────


def test_the_consent_text_interpolates_the_published_limit():
    # Deliberately not 85: a value the old literal cannot satisfy.
    caps = parse_capabilities({"limits": {"diagnostic_max_temp_c": 91.5}})
    text = cat.consent_safety_text(caps)
    assert f"{caps.limits.diagnostic_max_temp_c:g} °C" in text
    assert "85" not in text


def test_the_consent_text_names_no_figure_when_the_daemon_published_none():
    for caps in (parse_capabilities({}), None):
        text = cat.consent_safety_text(caps)
        assert "°C" not in text
        assert "diagnostic limit" in text


def _review_text(qtbot, tmp_path, settings_service, monkeypatch, limits: dict | None) -> tuple:
    """Open the real window, click through to the review page, return the label
    text and the capabilities it was rendered from."""
    from control_ofc.ui.pages.pwm_report_controller import PwmReportController
    from control_ofc.ui.widgets.pwm_report_window import PAGE_REVIEW, PwmReportWindow
    from tests.test_pwm_report_window import _state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = _state()
    if limits is not None:
        caps = parse_capabilities(
            {
                "daemon_version": "2.55.0",
                "api_version": 1,
                "control": {
                    "autonomous_control": True,
                    "pwm_characterization": True,
                    "header_roles": True,
                },
                "limits": limits,
            }
        )
        state.set_capabilities(caps)
    controller = PwmReportController(state, "/tmp/fake.sock", directory=tmp_path / "r")
    window = PwmReportWindow(controller, state, settings_service)
    qtbot.addWidget(window)
    try:
        window.findChild(QPushButton, "PwmReport_Btn_new").click()
        window.findChild(QPushButton, "PwmReport_Btn_next").click()
        window.findChild(QPushButton, "PwmReport_Btn_next").click()
        assert window.current_page() == PAGE_REVIEW
        label = window.findChild(QLabel, "PwmReport_Label_safety")
        return label.text(), state.capabilities
    finally:
        controller.shutdown()


def test_the_review_page_renders_the_limit_the_daemon_published(
    qtbot, tmp_path, settings_service, monkeypatch
):
    """The call site: the window must pass the LIVE capabilities, not ``None``."""
    text, caps = _review_text(
        qtbot, tmp_path, settings_service, monkeypatch, {"diagnostic_max_temp_c": 91.5}
    )
    assert text == cat.consent_safety_text(caps)
    assert f"{caps.limits.diagnostic_max_temp_c:g} °C" in text


def test_the_review_page_names_no_figure_for_an_older_daemon(
    qtbot, tmp_path, settings_service, monkeypatch
):
    text, caps = _review_text(qtbot, tmp_path, settings_service, monkeypatch, None)
    assert caps.limits.diagnostic_max_temp_c is None
    assert "°C" not in text and "diagnostic limit" in text
