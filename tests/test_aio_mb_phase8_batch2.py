"""AIO Phase 8 Batch 2 (DEC-334): PWM behaviour characterisation.

The GUI half of `AIO-Phase8-Batch2-PWM-Behaviour-Characterisation.md` §10. Its
daemon half lives in the paired repo, as unit tests beside the code they pin
(`api::characterization::tests::behaviour`, `api::stats`, `pwm_baselines`).

Disciplines from `CLAUDE.md § Hard-won lessons` that run through this file:

* **Assert a RELATIONSHIP, never a literal.** A view-model assertion is written
  against the constant or the sibling value it must agree with, so a threshold
  change cannot silently invert what the test claims.
* **``isVisibleTo(parent)``, never ``isVisible()``.** Under
  ``QT_QPA_PLATFORM=offscreen`` nothing is shown, so ``isVisible()`` is ``False``
  for every widget and an assertion on it passes with ``setVisible(...)``
  deleted.
* **Test the call site, not just the extracted rule.** Every block this batch
  adds to the view model has a paired assertion that the dialog renders it.
* **Absence is not zero.** Several tests exist only to pin that a daemon which
  said nothing produces no claim, rather than a confident ``0``.
"""

from __future__ import annotations

import json

import pyqtgraph as pg
import pytest
from PySide6.QtWidgets import QCheckBox, QLabel

from control_ofc.api.models import (
    VALIDATION_DIAG_BEHAVIOUR,
    VALIDATION_DIAG_CHARACTERIZATION,
    Capabilities,
    CharacterizationRun,
    CharPoint,
    CharSummary,
    ControlCapability,
    EstimatedRpm,
    HwmonHeader,
    PlateauSpan,
    PointStability,
    ValidationEvidence,
    ValidationSession,
    parse_characterization_run,
)
from control_ofc.services.characterization_view import (
    OUTSIDE_LEARNED_RANGE_WARNING,
    build_characterization_view,
)
from control_ofc.services.provenance import classify, from_envelope
from control_ofc.services.validation_export import session_json
from control_ofc.ui.widgets.pwm_characterization_dialog import PwmCharacterizationDialog
from control_ofc.ui.widgets.pwm_response_chart import PwmResponseChart
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog

# ── fixtures ─────────────────────────────────────────────────────────────────


def _caps(**control) -> Capabilities:
    control.setdefault("pwm_characterization", True)
    control.setdefault("pwm_behaviour_characterization", True)
    control.setdefault("validation_sessions", True)
    control.setdefault("diagnostic_preflight", True)
    return Capabilities(control=ControlCapability(**control))


def _header() -> HwmonHeader:
    return HwmonHeader(
        id="hwmon:it8686:isa-0a40:pwm2:AIO_PUMP",
        chip_name="it8686",
        label="AIO_PUMP",
        is_writable=True,
    )


def _point(pct: int, rpm: int | None, direction: str, **kw) -> CharPoint:
    base = dict(
        requested_pct=pct,
        command_accepted=True,
        readback_pct=pct,
        pwm_enable=1,
        rpm_before=rpm,
        rpm_after=rpm,
        settle_ms=6000,
        first_change_ms=1500,
        readback_verdict="match",
        rpm_verdict="changed",
        direction=direction,
    )
    base.update(kw)
    return CharPoint(**base)


def _bidi_run(**summary_kw) -> CharacterizationRun:
    """A completed two-direction sweep with a mild hysteresis gap."""
    points = [
        _point(100, 3000, "ramp", step_index=0),
        _point(60, 2100, "falling", step_index=1),
        _point(30, 950, "falling", step_index=2),
        _point(60, 1900, "rising", step_index=3),
        _point(100, 3000, "rising", step_index=4),
    ]
    summary = CharSummary(
        command_acceptance="pass",
        pwm_readback="pass",
        rpm_response="responsive",
        min_tested_pct=30,
        max_tested_pct=100,
        min_rpm=950,
        max_rpm=3000,
        hysteresis_verdict="present",
        hysteresis_pct=9.8,
        hysteresis_worst_duty_pct=60,
        hysteresis_worst_delta_rpm=200,
        hysteresis_compared_points=2,
        min_responsive_pct=30,
        max_responsive_pct=100,
        stability_verdict="stable",
        worst_cv_pct=1.2,
        measurement_resolution_ms=500,
        typical_response_ms=1500,
        typical_settling_ms=3000,
    )
    for key, value in summary_kw.items():
        setattr(summary, key, value)
    return CharacterizationRun(
        run_id="char-42",
        header_id=_header().id,
        state="complete",
        requested_points_pct=[100, 60, 30, 60, 100],
        settle_seconds=6,
        points=points,
        summary=summary,
        original_pct=42,
        restore_outcome="restored",
        bidirectional=True,
    )


# ── §1/§9 wire parsing ───────────────────────────────────────────────────────


class TestModelParsing:
    def test_the_nested_stability_block_is_parsed(self):
        run = parse_characterization_run(
            {
                "run_id": "c1",
                "state": "complete",
                "points": [
                    {
                        "requested_pct": 60,
                        "direction": "falling",
                        "step_index": 3,
                        "settled_ms": 2500,
                        "stability": {
                            "samples": 12,
                            "usable": 11,
                            "dropouts": 1,
                            "cv_pct": 2.5,
                            "verdict": "stable",
                            "sample_interval_ms": 500,
                        },
                    }
                ],
            }
        )
        point = run.points[0]
        assert point.direction == "falling"
        assert point.step_index == 3
        assert point.settled_ms == 2500
        assert point.stability is not None
        assert point.stability.dropouts == 1
        assert point.stability.verdict == "stable"

    def test_an_absent_stability_block_stays_none_rather_than_becoming_zeroes(self):
        """A zeroed record would render as "0 samples, 0 dropouts, stable" — a
        claim about hardware from a daemon that said nothing."""
        run = parse_characterization_run(
            {"run_id": "c1", "state": "complete", "points": [{"requested_pct": 60}]}
        )
        assert run.points[0].stability is None
        assert run.points[0].estimated_physical_rpm is None

    def test_the_rpm_provenance_envelope_is_parsed(self):
        run = parse_characterization_run(
            {
                "run_id": "c1",
                "state": "complete",
                "points": [
                    {
                        "requested_pct": 60,
                        "rpm_after": 1500,
                        "estimated_physical_rpm": {
                            "value": 1000,
                            "provenance": "DERIVED",
                            "correction_factor": 0.667,
                            "correction_source": "validated cooler",
                        },
                    }
                ],
            }
        )
        point = run.points[0]
        assert point.rpm_after == 1500, "§7: the raw reported value is never overwritten"
        assert point.estimated_physical_rpm == EstimatedRpm(
            value=1000,
            provenance="DERIVED",
            correction_factor=0.667,
            correction_source="validated cooler",
        )

    def test_the_provenance_sidecar_and_run_fields_are_parsed(self):
        run = parse_characterization_run(
            {
                "run_id": "c1",
                "state": "complete",
                "bidirectional": True,
                "stability_seconds": 20,
                "completed_unix_ms": 1_700_000_000_000,
                "provenance": {"rpm_after": "OBSERVED", "hysteresis_pct": "DERIVED"},
                "summary": {
                    "plateaus": [{"from_pct": 30, "to_pct": 45, "rpm_min": 900, "rpm_max": 930}],
                    "interpretation_states": ["DEVICE_OVERRIDE_POSSIBLE"],
                },
            }
        )
        assert run.bidirectional is True
        assert run.stability_seconds == 20
        assert run.provenance["rpm_after"] == "OBSERVED"
        assert run.summary is not None
        assert run.summary.plateaus == [PlateauSpan(30, 45, 900, 930)]
        assert run.summary.interpretation_states == ["DEVICE_OVERRIDE_POSSIBLE"]

    def test_an_older_daemons_run_parses_with_the_new_fields_absent(self):
        run = parse_characterization_run(
            {"run_id": "c1", "state": "complete", "points": [{"requested_pct": 60}]}
        )
        assert run.bidirectional is False
        assert run.provenance == {}
        assert run.summary is None


# ── §2/§3/§4/§5/§6 view model ────────────────────────────────────────────────


class TestViewModel:
    def test_bidirectional_is_derived_from_the_points_not_a_request_flag(self):
        """DEC-325: a flag describing a value must come from that value. A run
        that aborted before its second leg is honestly unidirectional."""
        run = _bidi_run()
        run.points = [p for p in run.points if p.direction != "falling"]
        view = build_characterization_view(run, header_label="Pump")
        assert view.bidirectional is False
        assert build_characterization_view(_bidi_run(), header_label="Pump").bidirectional is True

    def test_each_row_names_the_leg_that_produced_it(self):
        view = build_characterization_view(_bidi_run(), header_label="Pump")
        directions = [r.direction for r in view.rows]
        assert directions == ["Start", "Falling", "Falling", "Rising", "Rising"]

    def test_the_curve_splits_by_the_daemons_direction_not_by_arrival_order(self):
        curve = build_characterization_view(_bidi_run(), header_label="Pump").curve
        assert curve.has_data
        assert [p.duty_pct for p in curve.falling] == [30, 60]
        assert [p.duty_pct for p in curve.rising] == [60, 100]
        assert all(p.duty_pct != 100 or p.rpm == 3000 for p in curve.rising)

    def test_an_older_daemons_undirected_points_become_one_rising_series(self):
        """A pre-DEC-334 daemon sends no direction and one ascending sweep, and
        calling that "rising" is what it actually was.

        Every reading is kept rather than folded by duty: a repeated duty is a
        second observation, and dropping one to tidy the series would discard
        measured data to make a chart look neater.
        """
        run = _bidi_run()
        for p in run.points:
            p.direction = ""
        curve = build_characterization_view(run, header_label="Pump").curve
        assert curve.falling == []
        assert len(curve.rising) == len([p for p in run.points if p.rpm_after is not None])
        assert [p.duty_pct for p in curve.rising] == sorted(p.duty_pct for p in curve.rising)

    def test_a_run_with_no_tach_readings_yields_no_curve_to_draw(self):
        """Empty axes read as "we measured and found zero"."""
        run = _bidi_run()
        for p in run.points:
            p.rpm_after = None
        assert build_characterization_view(run, header_label="Pump").curve.has_data is False

    def test_the_summary_block_reports_range_hysteresis_stability_and_timing(self):
        view = build_characterization_view(_bidi_run(), header_label="Pump")
        labels = {row.label: row.value for row in view.summary_rows}
        assert labels["Safe tested range"] == "30-100%"
        assert labels["Effective range"] == "30-100%"
        assert labels["Reported RPM range"] == "950-3000"
        assert "Observed" in labels["Hysteresis"]
        assert labels["RPM stability"] == "Stable"
        # `P8-x`: §5's requirement is that the timing APPEARS, not where. The
        # daemon owns the derivation, so on a daemon that publishes it the row
        # is "Response latency (median)" in the detail block, and the client's
        # own recomputation is deliberately suppressed rather than shown beside
        # it in a second unit. Asserted against the daemon's field so this
        # cannot pass on a view that reports no timing at all.
        detail = {row.label: row.value for row in view.detail_rows}
        run_summary = _bidi_run().summary
        assert run_summary is not None and run_summary.typical_response_ms is not None
        assert detail["Response latency (median)"] == f"{run_summary.typical_response_ms} ms"
        assert "Response time" not in labels, (
            "the client recomputation must not be rendered alongside the daemon's"
        )

    def test_an_unlearned_header_is_reported_as_not_established_not_as_agreement(self):
        """§6 three-state. The Overview: "Do not turn lack of evidence into PASS.\""""
        view = build_characterization_view(_bidi_run(), header_label="Pump")
        labels = {row.label: row.value for row in view.summary_rows}
        assert labels["Learned range"] == "not established yet"
        inside = build_characterization_view(
            _bidi_run(outside_learned_range=False), header_label="Pump"
        )
        assert {r.label: r.value for r in inside.summary_rows}["Learned range"] == (
            "reading inside it"
        )

    def test_a_fully_plateaued_sweep_reports_no_responsive_band_rather_than_omitting_it(self):
        run = _bidi_run(
            min_responsive_pct=None,
            max_responsive_pct=None,
            plateaus=[PlateauSpan(30, 100, 2000, 2010)],
        )
        labels = {
            r.label: r.value
            for r in build_characterization_view(run, header_label="P").summary_rows
        }
        assert labels["Effective range"] == "no responsive band"

    def test_the_detail_block_publishes_the_resolution_the_timings_were_measured_at(self):
        """§5: "Do not publish unrealistic millisecond precision.\""""
        run = _bidi_run()
        run.points[0].stability = PointStability(
            samples=12, usable=12, verdict="stable", sample_interval_ms=500
        )
        rows = {
            r.label: r.value for r in build_characterization_view(run, header_label="P").detail_rows
        }
        assert rows["Measurement resolution"] == "500 ms"
        assert rows["Sample interval"] == "500 ms"
        assert "Settling criterion" in rows

    def test_dropouts_are_only_reported_when_something_measured_them(self):
        """Absence is not zero: "0 dropouts" from a daemon that measured nothing
        reads as a clean tach."""
        run = _bidi_run()
        for p in run.points:
            p.stability = None
        rows = {r.label for r in build_characterization_view(run, header_label="P").detail_rows}
        assert "Tach dropouts" not in rows

        run2 = _bidi_run()
        run2.points[0].stability = PointStability(samples=12, usable=12, verdict="stable")
        rows2 = {
            r.label: r.value
            for r in build_characterization_view(run2, header_label="P").detail_rows
        }
        assert rows2["Tach dropouts"] == "0"


class TestOverrideWarning:
    def test_the_cautious_wording_appears_only_when_command_and_readback_both_passed(self):
        """§10: "override detection requires command/readback success before
        suggesting device override.\""""
        outside = _bidi_run(outside_learned_range=True)
        assert (
            OUTSIDE_LEARNED_RANGE_WARNING
            in build_characterization_view(outside, header_label="P").override_warning
        )

        bad_readback = _bidi_run(outside_learned_range=True, pwm_readback="clamped")
        assert build_characterization_view(bad_readback, header_label="P").override_warning == ""

        bad_command = _bidi_run(outside_learned_range=True, command_acceptance="partial")
        assert build_characterization_view(bad_command, header_label="P").override_warning == ""

    def test_the_warning_never_calls_it_a_hardware_failure(self):
        """§8.5 forbids a generic red "hardware failed" for this condition."""
        text = build_characterization_view(
            _bidi_run(outside_learned_range=True), header_label="P"
        ).override_warning.lower()
        for forbidden in ("failed", "failure", "faulty", "broken"):
            assert forbidden not in text, f"§8.5 wording must stay cautious, found {forbidden!r}"
        assert "may be applying internal control" in text

    def test_interpretation_states_are_offered_as_possibilities(self):
        run = _bidi_run(
            outside_learned_range=True,
            interpretation_states=["DEVICE_OVERRIDE_POSSIBLE", "PWM_CLAMP_POSSIBLE"],
        )
        text = build_characterization_view(run, header_label="P").override_warning
        assert "Possible explanations" in text
        assert "Device Override Possible" in text or "device override possible" in text.lower()


class TestProvenanceRows:
    def test_reported_rpm_is_labelled_observed(self):
        rows = build_characterization_view(_bidi_run(), header_label="P").provenance_rows
        assert rows[0].label == "Reported RPM"
        assert rows[0].provenance == classify("rpm_after")

    def test_no_estimate_row_exists_without_trusted_metadata(self):
        """§7: with no correction there is NO estimate — never a relabelled copy
        of the reported figure."""
        rows = build_characterization_view(_bidi_run(), header_label="P").provenance_rows
        assert all(r.label != "Estimated physical RPM" for r in rows)

    def test_an_estimate_is_labelled_derived_and_names_its_source(self):
        run = _bidi_run()
        run.points[-1].estimated_physical_rpm = EstimatedRpm(
            value=2000, provenance="DERIVED", correction_factor=0.667, correction_source="cooler X"
        )
        rows = {
            r.label: r for r in build_characterization_view(run, header_label="P").provenance_rows
        }
        assert rows["Estimated physical RPM"].provenance == "DERIVED"
        assert "cooler X" in rows["Correction"].value
        assert rows["Correction"].provenance == classify("correction_source")

    def test_the_daemons_sidecar_wins_over_the_static_table(self):
        """One source of truth per field, and the daemon's is the one that
        produced the value."""
        run = _bidi_run()
        run.provenance = {"rpm_after": "UNVERIFIED"}
        rows = build_characterization_view(run, header_label="P").provenance_rows
        assert rows[0].provenance == "UNVERIFIED"
        assert from_envelope({"value": 1, "provenance": "DERIVED"}) is not None


# ── §8 GUI: the call sites that render the view model ────────────────────────


def _dialog(qtbot, *, caps=None, header=None) -> PwmCharacterizationDialog:
    dialog = PwmCharacterizationDialog(
        _header().id,
        "AIO Pump",
        is_pump=True,
        header=header if header is not None else _header(),
        capabilities=caps if caps is not None else _caps(),
    )
    qtbot.addWidget(dialog)
    return dialog


def _preflight_payload(**over) -> dict:
    payload = {
        "header_id": _header().id,
        "diagnostic": "pwm_characterization",
        "verdict": "ready",
        "checks": [{"check_id": "pwm_writable", "state": "pass", "detail": "writable"}],
        "blocking": [],
    }
    payload.update(over)
    return payload


class TestDialogRendersTheNewBlocks:
    """§10: "GUI displays bidirectional results", "GUI displays effective
    range/stability/timing", "GUI displays cautious override warning".

    Every assertion uses ``isVisibleTo(parent)``: under ``offscreen`` nothing is
    shown, so ``isVisible()`` is ``False`` everywhere and these would pass with
    the ``setVisible`` calls deleted.
    """

    def test_the_table_shows_each_readings_leg(self, qtbot):
        dialog = _dialog(qtbot)
        dialog.apply_run(_bidi_run())
        table = dialog._table
        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        assert "Direction" in headers
        col = headers.index("Direction")
        rendered = [table.item(r, col).text() for r in range(table.rowCount())]
        # A RELATIONSHIP, not a literal: whatever the view model derived is what
        # the table must show, so a renderer reading the wrong field fails here.
        view = build_characterization_view(_bidi_run(), header_label="AIO Pump")
        assert rendered == [row.direction for row in view.rows]

    def test_the_table_shows_each_readings_stability(self, qtbot):
        dialog = _dialog(qtbot)
        run = _bidi_run()
        run.points[1].stability = PointStability(
            samples=12, usable=12, cv_pct=1.4, verdict="stable", sample_interval_ms=500
        )
        dialog.apply_run(run)
        headers = [
            dialog._table.horizontalHeaderItem(c).text() for c in range(dialog._table.columnCount())
        ]
        col = headers.index("Stability")
        assert "Stable" in dialog._table.item(1, col).text()

    def test_the_summary_block_appears_once_a_run_has_a_summary(self, qtbot):
        dialog = _dialog(qtbot)
        assert dialog._summary_holder.isVisibleTo(dialog) is False
        dialog.apply_run(_bidi_run())
        assert dialog._summary_holder.isVisibleTo(dialog) is True
        labels = [
            w.text()
            for w in dialog._summary_holder.findChildren(QLabel)
            if w.objectName().startswith("Char_SummaryLabel")
        ]
        assert "Hysteresis" in labels
        assert "RPM stability" in labels

    def test_the_chart_appears_only_when_there_is_something_to_plot(self, qtbot):
        dialog = _dialog(qtbot)
        assert dialog._chart.isVisibleTo(dialog) is False
        run = _bidi_run()
        for p in run.points:
            p.rpm_after = None
        dialog.apply_run(run)
        assert dialog._chart.isVisibleTo(dialog) is False, "empty axes read as a measured zero"
        dialog.apply_run(_bidi_run())
        assert dialog._chart.isVisibleTo(dialog) is True

    def test_the_override_warning_is_shown_only_when_the_view_model_says_so(self, qtbot):
        dialog = _dialog(qtbot)
        dialog.apply_run(_bidi_run())
        assert dialog._override_lbl.isVisibleTo(dialog) is False
        dialog.apply_run(_bidi_run(outside_learned_range=True))
        assert dialog._override_lbl.isVisibleTo(dialog) is True
        # The relationship again: the label shows the view model's text, so a
        # dialog that re-derived the condition itself would diverge here.
        view = build_characterization_view(
            _bidi_run(outside_learned_range=True), header_label="AIO Pump"
        )
        assert dialog._override_lbl.text() == view.override_warning

    def test_the_detail_section_carries_the_engineering_rows_and_provenance(self, qtbot):
        dialog = _dialog(qtbot)
        assert dialog._detail_section.isVisibleTo(dialog) is False
        run = _bidi_run()
        run.points[0].stability = PointStability(
            samples=12, usable=12, verdict="stable", sample_interval_ms=500
        )
        dialog.apply_run(run)
        assert dialog._detail_section.isVisibleTo(dialog) is True
        prov_labels = [
            w.text()
            for w in dialog.findChildren(QLabel)
            if w.objectName().startswith("Char_ProvLabel")
        ]
        assert "Reported RPM" in prov_labels


class TestDialogPreflight:
    """Batch 1 §6.1, wired to this dialog by DEC-334."""

    def test_the_dialog_asks_for_a_preflight_for_its_own_diagnostic(self, qtbot):
        dialog = _dialog(qtbot)
        seen: list[tuple[str, str]] = []
        dialog.preflight_requested.connect(lambda h, d: seen.append((h, d)))
        dialog.request_preflight()
        assert seen == [(_header().id, "pwm_characterization")]

    def test_a_blocked_preflight_disables_start_and_says_why(self, qtbot):
        from control_ofc.api.models import parse_preflight_report

        dialog = _dialog(qtbot)
        dialog.apply_preflight(
            parse_preflight_report(
                _preflight_payload(
                    verdict="blocked",
                    checks=[
                        {
                            "check_id": "thermal_state",
                            "state": "fail",
                            "detail": "Thermal safety is forcing fan output",
                        }
                    ],
                    blocking=["thermal_state"],
                )
            )
        )
        assert dialog._start_btn.isEnabled() is False
        assert dialog._blocked_lbl.isVisibleTo(dialog) is True

    def test_a_ready_preflight_leaves_start_available(self, qtbot):
        from control_ofc.api.models import parse_preflight_report

        dialog = _dialog(qtbot)
        dialog.apply_preflight(parse_preflight_report(_preflight_payload()))
        assert dialog._start_btn.isEnabled() is True
        assert dialog._blocked_lbl.isVisibleTo(dialog) is False

    def test_an_unavailable_preflight_never_blocks_start(self, qtbot):
        """Advisory only. An older daemon serves no preflight, and the POST still
        runs every guard the preflight merely reports."""
        dialog = _dialog(qtbot)
        dialog.apply_preflight_error("unavailable", "not supported by this daemon")
        assert dialog._start_btn.isEnabled() is True


class TestPollGuard:
    """P8-e, fixed in BOTH dialogs in one change as the register row requires."""

    def test_the_characterisation_dialog_keeps_one_poll_in_flight(self, qtbot):
        dialog = _dialog(qtbot)
        polls: list[int] = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog._on_poll_tick()
        dialog._on_poll_tick()
        dialog._on_poll_tick()
        assert len(polls) == 1, "the timer must not queue polls behind an unanswered one"
        dialog.apply_run(_bidi_run())
        dialog._on_poll_tick()
        assert len(polls) == 2, "and a reply must release it"

    def test_an_error_reply_also_releases_the_guard(self, qtbot):
        """Otherwise a single failed poll wedges polling for the rest of the run.

        The first draft did tick -> error -> tick and asserted 2, which **passed
        with the guard deleted** because two unguarded ticks are also two polls.
        It has to exercise the HOLD before the release, or it proves nothing —
        the same trap as picking a sample that cannot move.
        """
        dialog = _dialog(qtbot)
        polls: list[int] = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog._on_poll_tick()
        dialog._on_poll_tick()
        assert len(polls) == 1, "precondition: the guard must be holding"
        dialog.apply_error("unavailable", "daemon busy")
        dialog._on_poll_tick()
        assert len(polls) == 2

    def test_the_control_path_dialog_carries_the_identical_guard(self, qtbot):
        from control_ofc.ui.widgets.control_path_dialog import ControlPathDiscoveryDialog

        dialog = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        qtbot.addWidget(dialog)
        polls: list[int] = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog._on_poll_tick()
        dialog._on_poll_tick()
        assert len(polls) == 1
        dialog.apply_error("unavailable", "daemon busy")
        dialog._on_poll_tick()
        assert len(polls) == 2


class TestCapabilityGating:
    def test_the_behaviour_inputs_are_sent_only_when_the_daemon_has_the_flag(self, qtbot):
        dialog = _dialog(qtbot)
        sent: list[tuple] = []
        dialog.start_requested.connect(lambda *a: sent.append(a))
        dialog._on_start()
        assert sent[0][3] is True, "bidirectional is requested when supported"

    def test_an_older_daemon_receives_the_request_it_has_always_received(self, qtbot):
        """Gating on `pwm_characterization` would be wrong: an older daemon HAS
        that flag and would silently ignore the new fields, leaving the UI
        promising hysteresis it never measured."""
        dialog = _dialog(qtbot, caps=_caps(pwm_behaviour_characterization=False))
        sent: list[tuple] = []
        dialog.start_requested.connect(lambda *a: sent.append(a))
        dialog._on_start()
        assert sent[0][3] is None
        assert sent[0][4] is None

    def test_no_capabilities_at_all_is_treated_as_unsupported(self, qtbot):
        dialog = _dialog(qtbot, caps=None)
        dialog._behaviour_supported = False
        sent: list[tuple] = []
        dialog.start_requested.connect(lambda *a: sent.append(a))
        dialog._on_start()
        assert sent[0][3] is None


class TestSessionDiagnosticChoice:
    def test_the_behaviour_token_is_offered_when_supported(self, qtbot):
        dialog = ValidationSessionDialog(
            device_id="dev-1",
            device_name="Test AIO",
            supported_diagnostics={
                VALIDATION_DIAG_CHARACTERIZATION,
                VALIDATION_DIAG_BEHAVIOUR,
            },
        )
        qtbot.addWidget(dialog)
        texts = " ".join(cb.text() for cb in dialog.findChildren(QCheckBox))
        assert "behaviour characterisation" in texts.lower()

    def test_it_is_filtered_out_against_a_daemon_without_the_flag(self, qtbot):
        """An unknown token in `diagnostics[]` makes the daemon reject the WHOLE
        session, so the gate is at the checkbox, never at submit."""
        dialog = ValidationSessionDialog(
            device_id="dev-1",
            device_name="Test AIO",
            supported_diagnostics={VALIDATION_DIAG_CHARACTERIZATION},
        )
        qtbot.addWidget(dialog)
        texts = " ".join(cb.text() for cb in dialog.findChildren(QCheckBox))
        assert "behaviour characterisation" not in texts.lower()


class TestSessionDiagnosticGatingCallSite:
    """The CALL SITE — `HardwarePage._supported_session_diagnostics()`.

    `TestSessionDiagnosticChoice` above hands the dialog `supported_diagnostics`
    as a **literal set**, so it proves the dialog honours the set and never that
    the page produces it. That is the extracted-rule trap (DEC-324), and it is
    exactly how DEC-334 shipped with `pwm_behaviour_characterization` missing
    from `daemon_features`: `daemon_supports` returns `None` for an unregistered
    id, `None` is falsy, and the token was therefore offered on **no daemon at
    all** while every test stayed green.

    The assertion is a RELATIONSHIP against the wire flag, and the choice of
    right-hand side is the whole point. Asserting against `daemon_supports(...)`
    would be satisfied by the defect itself — delete the registry entry and both
    sides go falsy together, so the test passes with the bug present. The flag on
    `capabilities.control` is the one term that stays true independently.
    """

    @staticmethod
    def _page(qtbot, caps):
        from control_ofc.api.models import ConnectionState
        from control_ofc.services.app_state import AppState
        from control_ofc.services.diagnostics_service import DiagnosticsService
        from control_ofc.ui.pages.hardware_page import HardwarePage

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        if caps is not None:
            state.set_capabilities(caps)
        page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=None)
        qtbot.addWidget(page)
        return page

    def _assert_tracks_the_flag(self, qtbot, *, advertised: bool) -> bool:
        caps = _caps(pwm_behaviour_characterization=advertised)
        page = self._page(qtbot, caps)
        offered = VALIDATION_DIAG_BEHAVIOUR in page._supported_session_diagnostics()
        assert offered == caps.control.pwm_behaviour_characterization, (
            f"the behaviour token must be offered exactly when the daemon "
            f"advertises the flag (advertised={advertised}, offered={offered})"
        )
        return offered

    def test_it_is_offered_when_the_daemon_advertises_the_flag(self, qtbot):
        assert self._assert_tracks_the_flag(qtbot, advertised=True) is True

    def test_it_is_withheld_when_the_daemon_denies_the_flag(self, qtbot):
        assert self._assert_tracks_the_flag(qtbot, advertised=False) is False

    def test_no_capabilities_at_all_withholds_it(self, qtbot):
        """Not connected yet. `None` must read as "do not offer", not as a crash."""
        page = self._page(qtbot, None)
        assert VALIDATION_DIAG_BEHAVIOUR not in page._supported_session_diagnostics()

    def test_the_basic_characterisation_token_is_unaffected(self, qtbot):
        """Precondition: the two tokens are independent.

        Without this, a `_supported_session_diagnostics` that returned the empty
        set would satisfy the withheld case above and look like a passing gate.
        """
        page = self._page(qtbot, _caps(pwm_behaviour_characterization=False))
        assert VALIDATION_DIAG_CHARACTERIZATION in page._supported_session_diagnostics()


class TestClientContract:
    def test_the_behaviour_inputs_reach_the_payload_only_when_given(self):
        from control_ofc.api.client import DaemonClient

        sent: dict = {}

        class _Client(DaemonClient):
            def __init__(self):
                pass

            def _post(self, path, json=None, **kw):
                sent["path"] = path
                sent["body"] = json
                return {"run_id": "c1", "state": "running"}

        client = _Client()
        client.start_characterization("h1")
        assert sent["body"] == {}, "an unqualified call must not invent fields"

        client.start_characterization("h1", bidirectional=True, stability_seconds=20)
        assert sent["body"] == {"bidirectional": True, "stability_seconds": 20}
        assert sent["path"] == "/hwmon/h1/characterize"


class TestExportProvenance:
    def test_characterisation_evidence_carries_provenance_rows(self):
        """§10: "exports include both raw and derived fields with provenance."

        Reading only the control-path summary was correct for Batch 1 and became
        a silent omission the moment a second diagnostic published derived
        values.
        """
        session = ValidationSession(
            session_id="val-1",
            kind="validation",
            state="completed",
            evidence=[
                ValidationEvidence(
                    kind="pwm_behaviour_characterization",
                    member_id=_header().id,
                    characterization=_bidi_run(),
                )
            ],
        )
        doc = json.loads(session_json(session))
        rows = doc["evidence"][0].get("provenance_rows") or []
        fields = {r["field"]: r["provenance"] for r in rows}
        assert fields, "a characterisation summary must be classified"
        assert fields.get("hysteresis_pct") == "DERIVED"

    def test_the_raw_reported_rpm_survives_the_export(self):
        """§9: "Do not overwrite raw evidence with derived values.\""""
        run = _bidi_run()
        run.points[-1].estimated_physical_rpm = EstimatedRpm(
            value=2000, provenance="DERIVED", correction_factor=0.667, correction_source="cooler"
        )
        session = ValidationSession(
            session_id="val-1",
            kind="validation",
            state="completed",
            evidence=[
                ValidationEvidence(
                    kind="pwm_behaviour_characterization",
                    member_id=_header().id,
                    characterization=run,
                )
            ],
        )
        doc = json.loads(session_json(session))
        point = doc["evidence"][0]["characterization"]["points"][-1]
        assert point["rpm_after"] == 3000
        assert point["estimated_physical_rpm"]["value"] == 2000


class TestChart:
    def test_the_chart_reports_whether_it_had_anything_to_draw(self, qtbot):
        chart = PwmResponseChart()
        qtbot.addWidget(chart)
        assert chart.has_data is False
        chart.set_curve(build_characterization_view(_bidi_run(), header_label="P").curve)
        assert chart.has_data is True

    def test_every_widget_carries_a_settable_unique_object_name(self, qtbot):
        first = PwmResponseChart(object_name="Chart_A")
        second = PwmResponseChart(object_name="Chart_B")
        qtbot.addWidget(first)
        qtbot.addWidget(second)
        assert first.objectName() != second.objectName()
        assert first._plot_widget.objectName() != second._plot_widget.objectName()


# ── review remediation ───────────────────────────────────────────────────────


class TestSettlingHonesty:
    """§5: "Do not publish unrealistic millisecond precision" — and its sibling,
    do not publish a figure that is not the thing the label names.

    `settle_ms` is how long the daemon HELD the point; a stability dwell inflates
    it, so a step with a 20 s dwell reports 26 000 ms for a fan that settled in
    3 s. Both the per-row column and the summary line used it.
    """

    def _run_with_dwell(self, settled: int | None) -> CharacterizationRun:
        run = _bidi_run()
        for p in run.points:
            p.settle_ms = 26_000  # 6 s settle + a 20 s dwell
            p.settled_ms = settled
            p.stability = PointStability(
                samples=52, usable=52, verdict="stable", sample_interval_ms=500, dwell_ms=20_000
            )
        return run

    def test_a_measured_settling_time_is_what_is_shown(self):
        view = build_characterization_view(self._run_with_dwell(3000), header_label="P")
        assert view.rows[0].settling == "3.0 s"
        assert view.settling_time == "~3.0 s"

    def test_an_unmeasured_settling_time_is_not_the_length_of_the_wait(self):
        """A current daemon that measured no settling reports nothing, never the
        hold duration — which would report a placeholder as a measurement."""
        view = build_characterization_view(self._run_with_dwell(None), header_label="P")
        assert view.rows[0].settling == "—"
        assert view.settling_time == "", "and the aggregate says nothing rather than ~26.0 s"
        labels = {r.label: r.value for r in view.summary_rows}
        assert "Settling time" not in labels

    def test_an_older_daemon_still_shows_the_hold_it_has_always_shown(self):
        """Detected by the absence of the per-point stability block: an older
        daemon sends neither field, and there `settle_ms` is the only figure
        there has ever been."""
        run = _bidi_run()
        for p in run.points:
            p.settle_ms = 6000
            p.settled_ms = None
            p.stability = None
        view = build_characterization_view(run, header_label="P")
        assert view.rows[0].settling == "6.0 s"
        assert view.settling_time == "~6.0 s"


class TestOneMeasurementOneProducer:
    """`P8-x`: the same measurement must not appear twice in two units."""

    def test_the_daemons_median_displaces_the_client_recomputation(self):
        run = _bidi_run()
        assert run.summary is not None
        assert run.summary.typical_response_ms is not None, (
            "precondition: this fixture's daemon DOES publish the median"
        )
        view = build_characterization_view(run, header_label="P")

        summary_labels = {r.label for r in view.summary_rows}
        detail_labels = {r.label for r in view.detail_rows}

        # The daemon's row is the one that renders...
        assert "Response latency (median)" in detail_labels
        # ...and the client's recomputation of the SAME measurement does not,
        # which is the whole finding: both were shown, in one dialog, in
        # different units, disagreeing on any even sample count.
        assert "Response time" not in summary_labels, (
            "the client-recomputed median must not be rendered alongside the daemon's own"
        )
        assert "Settling time" not in summary_labels
        assert "Settling time (median)" in detail_labels

    def test_an_older_daemon_still_gets_the_client_fallback(self):
        """The discriminating arm. Suppressing the client row unconditionally
        would pass the test above and silently drop the measurement entirely for
        a daemon predating 2.40.0 — so the case that must still render it is the
        one worth asserting."""
        from dataclasses import replace as _replace

        run = _bidi_run()
        assert run.summary is not None
        run = _replace(
            run,
            summary=_replace(run.summary, typical_response_ms=None, typical_settling_ms=None),
        )
        view = build_characterization_view(run, header_label="P")

        summary_labels = {r.label for r in view.summary_rows}
        detail_labels = {r.label for r in view.detail_rows}
        assert "Response latency (median)" not in detail_labels, "precondition: daemon said nothing"
        assert "Response time" in summary_labels, (
            "with no daemon field the client fallback must still report the timing"
        )
        assert "Settling time" in summary_labels


class TestChartMarkers:
    def test_plateaus_and_saturation_are_drawn_without_error(self, qtbot):
        """§8.3's "clearly mark plateaus/saturation". Exercises the marker path,
        which no other test reached."""
        from control_ofc.services.characterization_view import ResponseCurve, SeriesPoint

        chart = PwmResponseChart()
        qtbot.addWidget(chart)
        chart.set_curve(
            ResponseCurve(
                rising=[SeriesPoint(30, 900), SeriesPoint(60, 2000), SeriesPoint(100, 3300)],
                falling=[SeriesPoint(30, 950), SeriesPoint(60, 2100)],
                plateaus=[(30, 45), (90, 100)],
                low_plateau_to_pct=45,
                saturation_from_pct=90,
                has_data=True,
            )
        )
        plot = chart._plot_widget.getPlotItem()
        assert plot is not None

        # `P8-ad`: assert the REALISED positions, not the item count. `>= 5` was
        # satisfied by `set_curve` construction (2 series + 2 regions + 1 line),
        # so drawing the saturation line at `low_plateau_to_pct` (45) instead of
        # `saturation_from_pct` (90) passed, and so did a zero-width region. The
        # fixture already makes those values distinct — "something changed" is
        # not evidence a rule fired.
        lines = [i for i in plot.items if isinstance(i, pg.InfiniteLine)]
        assert len(lines) == 1, f"expected one saturation line, got {lines}"
        assert lines[0].value() == pytest.approx(90.0), (
            "the saturation line must sit at saturation_from_pct, not at some "
            f"other distinct value in the fixture; got {lines[0].value()}"
        )

        regions = [i for i in plot.items if isinstance(i, pg.LinearRegionItem)]
        drawn = sorted(tuple(round(v, 6) for v in r.getRegion()) for r in regions)
        assert drawn == [(30.0, 45.0), (90.0, 100.0)], (
            f"plateau regions must match curve.plateaus; got {drawn}"
        )
        assert all(lo < hi for lo, hi in drawn), "a zero-width plateau marks nothing"

    def test_a_redraw_replaces_the_previous_run_rather_than_stacking_on_it(self, qtbot):
        """The dialog calls `set_curve` on every poll while the sweep runs."""
        chart = PwmResponseChart()
        qtbot.addWidget(chart)
        curve = build_characterization_view(_bidi_run(), header_label="P").curve
        chart.set_curve(curve)
        first = len(chart._plot_widget.getPlotItem().items)
        # `P8-aq`: presence BEFORE absence. `set_curve` opens with `plot.clear()`
        # and early-returns when `not curve.has_data`, so without this a draw
        # that produced nothing at all gave `0 == 0` and passed — the test would
        # have been green over a chart that had stopped rendering entirely.
        assert first > 0, "the first draw must actually put items on the plot"
        chart.set_curve(curve)
        assert len(chart._plot_widget.getPlotItem().items) == first


class TestExportSidecarPrecedence:
    """The daemon's own legend must win over the GUI's static table.

    One source of truth per field, and the daemon's is the one that produced the
    value. This rule had no test — a rule with none at its call site is the
    family CLAUDE.md records twelve times.
    """

    def _doc(self, provenance: dict) -> dict:
        run = _bidi_run()
        run.provenance = provenance
        session = ValidationSession(
            session_id="val-1",
            kind="validation",
            state="completed",
            evidence=[
                ValidationEvidence(
                    kind="pwm_behaviour_characterization",
                    member_id=_header().id,
                    characterization=run,
                )
            ],
        )
        return json.loads(session_json(session))

    def test_the_static_table_applies_when_the_daemon_sent_no_sidecar(self):
        rows = {
            r["field"]: r["provenance"] for r in self._doc({})["evidence"][0]["provenance_rows"]
        }
        assert rows["hysteresis_pct"] == classify("hysteresis_pct")

    def test_a_sidecar_entry_overrides_the_static_table(self):
        doc = self._doc({"hysteresis_pct": "UNVERIFIED"})
        rows = {r["field"]: r["provenance"] for r in doc["evidence"][0]["provenance_rows"]}
        assert rows["hysteresis_pct"] == "UNVERIFIED"
        # A RELATIONSHIP: the override must differ from what the table would say,
        # or this passes against an implementation that ignores the sidecar.
        assert rows["hysteresis_pct"] != classify("hysteresis_pct")

    def test_fields_the_sidecar_omits_keep_their_static_classification(self):
        doc = self._doc({"hysteresis_pct": "UNVERIFIED"})
        rows = {r["field"]: r["provenance"] for r in doc["evidence"][0]["provenance_rows"]}
        assert rows["stability_verdict"] == classify("stability_verdict")


class TestPollGuardGuarantee:
    """What P8-e's closure actually promises, stated as a test.

    The guard bounds the timer: it cannot queue a second poll while one is
    unanswered. It does **not** bound the user — a start or cancel reply also
    clears it, because any reply proves the single worker thread is draining, so
    a click during an outstanding poll can let one extra through. That is bounded
    by user actions rather than by time, which is the unbounded backlog the row
    was actually about. Recorded in the register row as well.
    """

    def test_the_timer_alone_can_never_get_ahead_of_the_worker(self, qtbot):
        dialog = _dialog(qtbot)
        polls: list[int] = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        for _ in range(20):
            dialog._on_poll_tick()
        assert len(polls) == 1, "twenty timer ticks with no reply must produce one poll"
