"""AIO Phase 8 Batch 3a — thermal observation, steady state, startup fingerprint.

DEC-335. Discharges the §12 bullets that belong to 3a, plus the project's own
standing test rules:

* call-site tests assert a **relationship**, never a literal (DEC-324) — and the
  right-hand side is chosen so the defect cannot satisfy it;
* ``isVisibleTo(parent)``, never ``isVisible()``, under ``offscreen`` (DEC-324);
* presence before absence (DEC-272);
* unknown is never rendered as zero, which is this batch's own headline rule.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import (
    STARTUP_COMMAND_MISMATCH,
    STARTUP_DEVICE_OVERRIDE,
    STARTUP_NONE_OBSERVED,
    STEADY_STATE_DETECTED,
    STEADY_STATE_INSUFFICIENT,
    STEADY_STATE_NOT_ESTABLISHED,
    VALIDATION_KIND_THERMAL,
    VALIDATION_KIND_VALIDATION,
    VALIDATION_STATE_RECORDING,
    Capabilities,
    ConnectionState,
    ControlCapability,
    ValidationMemberSample,
    ValidationSample,
    ValidationSession,
    ValidationStartupFingerprint,
    ValidationSteadyState,
    parse_validation_session,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_features import daemon_supports
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.thermal_view import (
    ISOLATION_TEMPLATES,
    TONE_WARN,
    UNKNOWN_TEXT,
    SessionTrace,
    build_live_summary,
    build_session_trace,
    build_startup_views,
    build_steady_state_view,
    isolation_stage_gate,
    startup_tone,
    steady_state_label,
    steady_state_tone,
    watts_text,
)
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.widgets.session_timeline_chart import SessionTimelineChart
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_ofc"


# ── fixtures ─────────────────────────────────────────────────────────────────


def _caps(**control) -> Capabilities:
    control.setdefault("validation_sessions", True)
    control.setdefault("thermal_observation", True)
    return Capabilities(control=ControlCapability(**control))


def _session(*, samples=None, steady=None, fingerprints=None) -> ValidationSession:
    s = ValidationSession(state=VALIDATION_STATE_RECORDING)
    s.metadata.pump_member = "pump1"
    s.metadata.radiator_members = ["rad1"]
    s.samples = samples if samples is not None else []
    s.steady_state = steady
    s.startup_fingerprints = fingerprints or []
    return s


def _sample(i: int, *, temp=None, power=None, rpm=None) -> ValidationSample:
    return ValidationSample(
        elapsed_ms=i * 1000,
        temperature_c=temp,
        package_power_w=power,
        members=[ValidationMemberSample(member_id="pump1", requested_pct=45, rpm=rpm)],
    )


# ── §12: model parsing ───────────────────────────────────────────────────────


class TestModelParsing:
    def test_the_power_fields_round_trip_and_absence_stays_none(self):
        parsed = parse_validation_session(
            {"samples": [{"package_power_w": 198.5, "gpu_power_w": 33.0}, {}]}
        )
        assert parsed.samples[0].package_power_w == 198.5
        assert parsed.samples[0].gpu_power_w == 33.0
        # Presence before absence: the second sample proves absence stays None
        # only because the first proved the field parses at all.
        assert parsed.samples[1].package_power_w is None

    def test_steady_state_and_fingerprints_parse(self):
        parsed = parse_validation_session(
            {
                "steady_state": {"verdict": "detected", "mean_c": 52.0, "criterion": "x"},
                "startup_fingerprints": [
                    {"member_id": "pump1", "interpretation": "device_startup_override"}
                ],
            }
        )
        assert parsed.steady_state is not None
        assert parsed.steady_state.verdict == STEADY_STATE_DETECTED
        assert parsed.startup_fingerprints[0].interpretation == STARTUP_DEVICE_OVERRIDE

    def test_an_older_daemon_sends_neither_and_that_is_not_a_negative_result(self):
        parsed = parse_validation_session({})
        assert parsed.steady_state is None, "absent means 'this daemon does not derive it'"
        assert parsed.startup_fingerprints == []


# ── §12: unknown is never zero ───────────────────────────────────────────────


class TestUnknownIsNotZero:
    def test_an_unknown_power_renders_as_unknown_not_as_zero_watts(self):
        # The batch's headline honesty rule, and the common case on this host:
        # `k10temp` publishes no power attribute at all.
        assert watts_text(None) == UNKNOWN_TEXT
        # The opposite branch — a real zero must still render as zero, or the
        # rule would be "never show zero", which is a different and wrong rule.
        assert watts_text(0.0) == "0 W"
        assert watts_text(198.4) == "198 W"

    def test_a_missing_reading_makes_a_gap_in_the_trace_not_a_zero_point(self):
        samples = [_sample(0, temp=40.0, power=None), _sample(1, temp=41.0, power=120.0)]
        trace = build_session_trace(_session(samples=samples))
        assert len(trace.temperature) == 2, "precondition: both samples carry a temperature"
        assert len(trace.package_power) == 1, "the unknown power must produce NO point"
        assert trace.package_power[0].value == 120.0

    def test_the_live_block_reports_unknown_rather_than_an_idle_machine(self):
        live = build_live_summary(_session(samples=[_sample(0, temp=None, power=None)]))
        assert live.package_power_text == UNKNOWN_TEXT
        assert live.temperature_text == UNKNOWN_TEXT


# ── §12: steady-state detection is observational ─────────────────────────────


class TestSteadyStateRendering:
    @pytest.mark.parametrize(
        "verdict",
        [STEADY_STATE_DETECTED, STEADY_STATE_NOT_ESTABLISHED, STEADY_STATE_INSUFFICIENT],
    )
    def test_no_verdict_renders_as_a_failure(self, verdict):
        """§3 forbids turning 'did not settle' into a verdict about the cooler.

        A run stopped early and a loop that genuinely cannot stabilise produce
        the same token, so nothing here may reach a failure tone.
        """
        assert steady_state_tone(verdict) != "bad"
        assert steady_state_tone(verdict) != "critical"

    def test_the_three_verdicts_are_distinguishable(self):
        """Otherwise a renderer that returned one label for everything passes."""
        labels = {
            steady_state_label(v)
            for v in (
                STEADY_STATE_DETECTED,
                STEADY_STATE_NOT_ESTABLISHED,
                STEADY_STATE_INSUFFICIENT,
            )
        }
        assert len(labels) == 3

    def test_an_unrecognised_verdict_is_rendered_not_dropped(self):
        """273-i: a newer daemon must not be able to make a result vanish."""
        text = steady_state_label("some_future_verdict")
        assert text and text != UNKNOWN_TEXT
        assert "future" in text.lower()

    def test_the_criterion_is_carried_through_verbatim(self):
        """§3 requires the exact rule be reported alongside the verdict.

        Asserted as pass-through, not against a literal: the thresholds live in
        the daemon's constants, and a GUI-side copy would be falsified the
        moment either moved.
        """
        # Content is arbitrary: this asserts PASS-THROUGH, and using the
        # daemon's exact wording here would be the restatement the test
        # exists to prevent.
        criterion = "slope and spread thresholds, held over N windows"
        view = build_steady_state_view(
            ValidationSteadyState(verdict=STEADY_STATE_DETECTED, criterion=criterion)
        )
        assert view is not None
        assert view.criterion == criterion

    def test_no_temperature_recorded_yields_no_view_at_all(self):
        assert build_steady_state_view(None) is None


# ── §12: a startup override is not a control failure ─────────────────────────


class TestStartupFingerprintRendering:
    def test_a_honoured_duty_never_renders_as_a_failure(self):
        """§1's prohibition, asserted directly."""
        views = build_startup_views(
            [
                ValidationStartupFingerprint(
                    member_id="pump1",
                    override_observed=True,
                    peak_rpm=3400,
                    post_override_rpm=1040,
                    requested_pct_during=35,
                    readback_pct_during=35,
                    interpretation=STARTUP_DEVICE_OVERRIDE,
                )
            ]
        )
        assert views[0].tone not in ("bad", "critical")
        # The duty evidence must reach the reader: the two agreeing is exactly
        # what rules the control fault out, so a peak RPM shown alone would
        # invite the reading §1 forbids.
        duty = [r.value for r in views[0].rows if r.label.startswith("Duty")]
        assert duty and "35%" in duty[0] and "readback" in duty[0]

    def test_even_a_mismatch_is_not_a_failure_tone(self):
        """The strongest available reading is `warn`, never a fault."""
        assert startup_tone(STARTUP_COMMAND_MISMATCH) == TONE_WARN
        assert startup_tone(STARTUP_COMMAND_MISMATCH) not in ("bad", "critical")

    def test_the_interpretations_are_distinguishable(self):
        assert startup_tone(STARTUP_DEVICE_OVERRIDE) != startup_tone(STARTUP_COMMAND_MISMATCH)
        assert startup_tone(STARTUP_NONE_OBSERVED) != startup_tone(STARTUP_COMMAND_MISMATCH)


# ── §12: the chart ───────────────────────────────────────────────────────────


class TestSessionChart:
    def test_it_draws_nothing_when_there_is_nothing_to_draw(self, qtbot):
        """Empty axes read as 'we measured and found zero'."""
        chart = SessionTimelineChart()
        qtbot.addWidget(chart)
        chart.set_trace(SessionTrace())
        assert chart.has_data is False

    def test_it_draws_when_there_is_data(self, qtbot):
        """The opposite branch — a chart that never has data passes the above."""
        chart = SessionTimelineChart()
        qtbot.addWidget(chart)
        chart.set_trace(build_session_trace(_session(samples=[_sample(0, temp=40.0)])))
        assert chart.has_data is True

    def test_the_power_strip_hides_when_the_host_exposes_no_power(self, qtbot):
        """`isVisibleTo`, never `isVisible`: under offscreen nothing is shown, so
        `isVisible()` is False for every widget and this would pass with the
        `setVisible` call deleted (DEC-324)."""
        chart = SessionTimelineChart()
        qtbot.addWidget(chart)

        with_power = build_session_trace(_session(samples=[_sample(0, temp=40.0, power=120.0)]))
        chart.set_trace(with_power)
        assert chart._power.isVisibleTo(chart) is True, "precondition: it shows when there IS power"

        without = build_session_trace(_session(samples=[_sample(0, temp=40.0, power=None)]))
        chart.set_trace(without)
        assert chart._power.isVisibleTo(chart) is False

    def test_the_steady_band_spans_from_the_daemons_start_to_the_recording_end(self):
        samples = [_sample(i, temp=40.0) for i in range(10)]
        steady = ValidationSteadyState(verdict=STEADY_STATE_DETECTED, start_ms=4000)
        trace = build_session_trace(_session(samples=samples), steady)
        assert trace.steady_from_s == 4.0
        assert trace.steady_to_s == 9.0

    def test_no_steady_state_means_no_band(self):
        trace = build_session_trace(_session(samples=[_sample(0, temp=40.0)]), None)
        assert trace.steady_from_s is None and trace.steady_to_s is None


# ── §12: the GUI exposes thermal observation ─────────────────────────────────


class TestDialogExposesTheThermalBlocks:
    @staticmethod
    def _dialog(qtbot, kind, session):
        d = ValidationSessionDialog("aio0", "Test AIO", kind=kind, members=[])
        qtbot.addWidget(d)
        d.apply_session(session)
        return d

    def test_the_thermal_kind_gets_its_own_title_and_workload_notice(self, qtbot):
        d = self._dialog(qtbot, VALIDATION_KIND_THERMAL, _session())
        assert d.windowTitle() == "Thermal Observation"
        # §2: the UI must make clear Control-OFC is not launching the workload.
        assert d._workload_lbl.isVisibleTo(d)
        assert "not" in d._workload_lbl.text().lower()
        assert "workload" in d._workload_lbl.text().lower()

    def test_the_live_block_is_thermal_only(self, qtbot):
        thermal = self._dialog(qtbot, VALIDATION_KIND_THERMAL, _session())
        assert thermal._live_box.isVisibleTo(thermal) is True
        plain = self._dialog(qtbot, VALIDATION_KIND_VALIDATION, _session())
        assert plain._live_box.isVisibleTo(plain) is False

    def test_the_blocks_appear_only_when_the_session_carries_the_material(self, qtbot):
        rich = self._dialog(
            qtbot,
            VALIDATION_KIND_THERMAL,
            _session(
                samples=[_sample(0, temp=52.0, power=198.0, rpm=1620)],
                steady=ValidationSteadyState(verdict=STEADY_STATE_DETECTED, mean_c=52.0),
                fingerprints=[
                    ValidationStartupFingerprint(
                        member_id="pump1", interpretation=STARTUP_DEVICE_OVERRIDE
                    )
                ],
            ),
        )
        assert rich._steady_section.isVisibleTo(rich) is True
        assert rich._startup_section.isVisibleTo(rich) is True
        assert rich._chart_section.isVisibleTo(rich) is True

        # An older daemon sends neither. A visible-but-empty panel would report a
        # measurement that came back blank rather than one never taken.
        bare = self._dialog(qtbot, VALIDATION_KIND_THERMAL, _session())
        assert bare._steady_section.isVisibleTo(bare) is False
        assert bare._startup_section.isVisibleTo(bare) is False
        assert bare._chart_section.isVisibleTo(bare) is False


# ── §12: the GUI exposes the entry point, and gates it ───────────────────────


class TestHardwarePageEntryPoint:
    @staticmethod
    def _page(qtbot, caps):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        if caps is not None:
            state.set_capabilities(caps)
        page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=None)
        qtbot.addWidget(page)
        return page

    def test_the_thermal_button_exists_and_is_reachable_from_hardware(self, qtbot):
        page = self._page(qtbot, _caps())
        button = page.findChild(QPushButton, "Hardware_Btn_thermal")
        assert button is not None, "§9: everything in this batch must be reachable from Hardware"
        assert "thermal" in button.text().lower()

    @pytest.mark.parametrize("advertised", [True, False])
    def test_the_button_tracks_the_capability_flag(self, qtbot, advertised):
        """A RELATIONSHIP against the wire flag, not against `daemon_supports`.

        Asserting against `daemon_supports` would be satisfied by the defect it
        guards: drop the registry entry and both sides go falsy together, which
        is precisely how DEC-334 shipped its session diagnostic unreachable. The
        flag on `capabilities.control` is the term that stays true independently.
        """
        caps = _caps(thermal_observation=advertised)
        page = self._page(qtbot, caps)
        # A device must exist or every diagnostic button is disabled for an
        # unrelated reason and the assertion below would be vacuous.
        page._device_cards = {"aio0": object()}
        page._sync_diagnostic_enablement()
        button = page.findChild(QPushButton, "Hardware_Btn_thermal")
        assert button.isEnabled() == caps.control.thermal_observation

    def test_the_feature_id_resolves_through_the_registry(self):
        """The DEC-334 defect, guarded for this batch's own id.

        `daemon_supports` returns `None` for an id absent from
        `DAEMON_FEATURE_CAPABILITY_FLAGS`, and `None` is falsy — so an
        unregistered id makes the gate dead on every daemon, silently.
        """
        assert daemon_supports("thermal_observation", _caps(thermal_observation=True)) is True
        assert daemon_supports("thermal_observation", _caps(thermal_observation=False)) is False


# ── §12: no workload is ever launched ────────────────────────────────────────


def test_nothing_on_the_thermal_path_can_launch_a_workload():
    """§2 and §11: Control-OFC must not start a stress or benchmark tool.

    A source scan rather than a behavioural test, because the property is an
    ABSENCE and the honest way to assert an absence in the shipped tree is to
    look for the thing that would implement it. Matched in call position rather
    than as a bare substring, so a comment explaining the prohibition does not
    trip the guard it explains — the `polling.rs` trap, one language over.
    """
    banned = re.compile(
        r"\b(subprocess\.(run|Popen|call|check_output)|os\.(system|exec\w*|spawn\w*)"
        r"|QProcess\s*\(|\.startDetached\s*\()"
    )
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Scope: the modules this batch added or drives the observation through.
        if not any(
            key in path.name
            for key in ("thermal_view", "session_timeline_chart", "validation_session_dialog")
        ):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not offenders, (
        "the thermal observation path must not be able to launch a workload:\n"
        + "\n".join(offenders)
    )
    # Precondition: the sweep must actually have inspected something, or it
    # asserts nothing at all.
    scanned = [p for p in SRC.rglob("*.py") if "thermal_view" in p.name]
    assert scanned, "precondition: the thermal modules must exist to be scanned"


# ── §12: the isolation workflow respects safety, and blocks on a bad state ────


class TestIsolationTemplateGate:
    """§12 asks that an active isolation workflow respect the safety preflight
    and that an unsafe rising temperature block progression.

    Under Q3-A there is no daemon-driven stepping — the workflow is guided — so
    these become client-side stage gates. That is recorded in §3.3 of the agreed
    scope, and the tests survive the change of mechanism rather than vanishing
    with it.
    """

    def _recording(self, **sample_kw) -> ValidationSession:
        s = _session(samples=[ValidationSample(elapsed_ms=0, **sample_kw)])
        s.state = VALIDATION_STATE_RECORDING
        return s

    def test_a_healthy_recording_may_advance(self):
        """Presence before absence: without this, a gate that refused
        everything would satisfy every refusal test below."""
        gate = isolation_stage_gate(self._recording(temperature_c=45.0, thermal_state="normal"))
        assert gate.can_advance is True

    def test_an_active_thermal_ladder_blocks_progression(self):
        gate = isolation_stage_gate(self._recording(temperature_c=95.0, thermal_state="emergency"))
        assert gate.can_advance is False
        assert "thermal" in gate.reason.lower()

    def test_no_temperature_reading_blocks_progression(self):
        """Lack of evidence must not read as a pass: a gate that cannot see the
        temperature cannot claim it is safe to continue."""
        gate = isolation_stage_gate(self._recording(temperature_c=None, thermal_state="normal"))
        assert gate.can_advance is False

    def test_a_session_that_is_not_recording_cannot_advance(self):
        assert isolation_stage_gate(None).can_advance is False
        assert isolation_stage_gate(ValidationSession()).can_advance is False

    def test_the_templates_hold_the_supporting_device_at_a_fixed_level(self):
        """The Overview's supporting-device rule: while one component is varied
        the other is held, and two unknown components are never varied at once."""
        for _key, (_label, stages) in ISOLATION_TEMPLATES.items():
            assert stages, "a template with no stages guides nothing"
            assert "hold" in stages[0].lower(), (
                f"the first stage must pin the supporting device, got: {stages[0]!r}"
            )

    def test_the_dialog_refuses_to_advance_and_says_why(self, qtbot):
        """`.click()`, not the handler: invoking the handler directly would skip
        the connection, which is the thing most likely to be broken."""
        d = ValidationSessionDialog("aio0", "Test AIO", kind=VALIDATION_KIND_THERMAL, members=[])
        qtbot.addWidget(d)
        d.apply_session(self._recording(temperature_c=95.0, thermal_state="emergency"))
        d._template_combo.setCurrentIndex(1)

        emitted: list[tuple] = []
        d.marker_requested.connect(lambda *a: emitted.append(a))
        d._stage_btn.click()

        # The refusal is expressed by DISABLING the control and stating why, not
        # by letting the click through to a handler that then declines. Asserted
        # the way it actually works: a disabled button swallows `.click()`, so a
        # test expecting a status message here would be asserting a mechanism
        # that does not exist.
        assert emitted == [], "an unsafe state must not record a stage marker"
        assert d._stage_btn.isEnabled() is False
        assert "thermal" in d._stage_lbl.text().lower(), (
            f"the reason must be on screen, got: {d._stage_lbl.text()!r}"
        )

        # And the handler itself refuses too, so the gate does not depend on the
        # button's enabled state alone — belt and braces on a safety refusal.
        d._on_next_stage()
        assert emitted == []
        assert "thermal" in d._status_lbl.text().lower()

    def test_the_dialog_advances_and_marks_the_timeline_when_it_is_safe(self, qtbot):
        """The opposite branch — otherwise a button that never emits passes."""
        d = ValidationSessionDialog("aio0", "Test AIO", kind=VALIDATION_KIND_THERMAL, members=[])
        qtbot.addWidget(d)
        d.apply_session(self._recording(temperature_c=45.0, thermal_state="normal"))
        d._template_combo.setCurrentIndex(1)

        emitted: list[tuple] = []
        d.marker_requested.connect(lambda *a: emitted.append(a))
        d._stage_btn.click()

        assert len(emitted) == 1, "a safe advance must record a marker"
        assert "stage 1" in emitted[0][0].lower()

    def test_the_template_section_is_thermal_only(self, qtbot):
        thermal = ValidationSessionDialog("aio0", "AIO", kind=VALIDATION_KIND_THERMAL, members=[])
        qtbot.addWidget(thermal)
        thermal.apply_session(self._recording(temperature_c=45.0))
        assert thermal._template_section.isVisibleTo(thermal) is True

        plain = ValidationSessionDialog("aio0", "AIO", kind=VALIDATION_KIND_VALIDATION, members=[])
        qtbot.addWidget(plain)
        plain.apply_session(self._recording(temperature_c=45.0))
        assert plain._template_section.isVisibleTo(plain) is False
