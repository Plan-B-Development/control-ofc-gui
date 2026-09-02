"""AIO-MB Phase 3 — PWM/RPM response characterisation (GUI side).

Covers the three things the brief names as correctness requirements, not taste:

1. **The three axes stay separate.** Command acceptance, PWM readback and RPM
   response are reported independently; collapsing them into one pass/fail is a
   defect the brief calls out by name.
2. **A device that does not follow PWM is not a write failure.** A non-monotonic
   or motionless response must never render as "PWM writes failed".
3. **The safety decision reads the UNION, not the wire ``role``** (DEC-312).

Plus the client contract (no client-side clamp — the daemon owns the floor), the
capability gate, and unknown-token tolerance (the 273-i rule).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import (
    Capabilities,
    CharacterizationRun,
    ConnectionState,
    ControlCapability,
    HwmonHeader,
    OperationMode,
    parse_characterization_run,
)
from control_ofc.services.characterization_view import (
    PUMP_STARTUP_WARNING,
    build_characterization_view,
    pre_run_warnings,
)
from control_ofc.services.pump_protection import header_is_pump_protected
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.widgets.pwm_characterization_dialog import PwmCharacterizationDialog


def _caps(*, characterization: bool = True, roles: bool = True) -> Capabilities:
    return Capabilities(
        daemon_version="2.29.0",
        control=ControlCapability(
            autonomous_control=True,
            header_roles=roles,
            pwm_characterization=characterization,
        ),
    )


def _header(**kw) -> HwmonHeader:
    base = dict(
        id="hwmon:nct6798:dev:pwm1:AIO_PUMP",
        label="AIO_PUMP",
        chip_name="nct6798",
        device_id="dev",
        pwm_index=1,
        is_writable=True,
    )
    base.update(kw)
    return HwmonHeader(**base)


def _run(**kw) -> CharacterizationRun:
    data = {
        "run_id": "char-1",
        "header_id": "h1",
        "state": "complete",
        "requested_points_pct": [30, 60, 100],
        "settle_seconds": 6,
        "points": [],
        "summary": None,
    }
    data.update(kw)
    return parse_characterization_run(data)


def _point(pct, readback=None, rpm=None, accepted=True, rv="match", rpmv="changed"):
    return {
        "requested_pct": pct,
        "command_accepted": accepted,
        "readback_pct": pct if readback is None else readback,
        "rpm_after": rpm,
        "readback_verdict": rv,
        "rpm_verdict": rpmv,
    }


# ── Model parsing ────────────────────────────────────────────────────


class TestModelParsing:
    def test_unknown_tokens_and_fields_are_kept_not_dropped(self):
        """273-i: render what a newer daemon sends; never drop the row."""
        run = parse_characterization_run(
            {
                "state": "some_future_state",
                "requested_points_pct": [30],
                "points": [
                    {
                        "requested_pct": 30,
                        "command_accepted": True,
                        "readback_verdict": "brand_new_verdict",
                        "rpm_verdict": "also_new",
                        "a_field_this_build_has_never_heard_of": 7,
                    }
                ],
                "summary": {"command_acceptance": "pass", "new_axis": "x"},
            }
        )
        assert len(run.points) == 1, "an unknown verdict must not drop the point"
        assert run.points[0].readback_verdict == "brand_new_verdict"
        assert run.state == "some_future_state"
        assert run.summary is not None

    def test_missing_summary_and_points_parse_to_safe_defaults(self):
        run = parse_characterization_run({})
        assert run.points == []
        assert run.summary is None
        assert run.requested_points_pct == []
        assert not run.is_running

    def test_capability_defaults_false_on_an_older_daemon(self):
        assert ControlCapability().pwm_characterization is False


# ── View-model ───────────────────────────────────────────────────────


class TestViewModel:
    def test_the_three_axes_are_reported_independently(self):
        run = _run(
            points=[_point(30, rpm=900), _point(100, rpm=3300)],
            summary={
                "command_acceptance": "pass",
                "pwm_readback": "clamped",
                "rpm_response": "responsive",
            },
        )
        view = build_characterization_view(run, header_label="AIO_PUMP")
        labels = {c.label: (c.value, c.state) for c in view.verdicts}
        assert labels["PWM command"] == ("Accepted", "ok")
        assert labels["PWM readback"] == ("Clamped", "warn")
        assert labels["RPM response"] == ("Responds", "ok")
        assert len(view.verdicts) == 3, "the axes must not be collapsed into one"

    def test_a_motionless_fan_with_a_good_readback_is_an_override_not_a_failure(self):
        run = _run(
            points=[_point(30, rpm=2800, rpmv="unchanged")],
            summary={
                "command_acceptance": "pass",
                "pwm_readback": "pass",
                "rpm_response": "no_response",
                "possible_device_override": True,
            },
        )
        view = build_characterization_view(run, header_label="AIO_PUMP")
        blob = " ".join(view.notes)
        assert PUMP_STARTUP_WARNING in blob
        assert "fail" not in blob.lower()
        labels = {c.label: c.value for c in view.verdicts}
        assert labels["PWM command"] == "Accepted"
        assert labels["PWM readback"] == "Correct"

    def test_a_non_monotonic_run_never_renders_as_a_pwm_write_failure(self):
        run = _run(
            points=[_point(30, rpm=900), _point(60, rpm=1800), _point(100, rpm=1200)],
            summary={
                "command_acceptance": "pass",
                "pwm_readback": "pass",
                "rpm_response": "responsive",
                "monotonic": False,
            },
        )
        view = build_characterization_view(run, header_label="AIO_PUMP")
        assert all(row.result != "Write failed" for row in view.rows)
        assert {c.label: c.value for c in view.verdicts}["PWM command"] == "Accepted"
        assert any("not a fault" in n for n in view.notes)

    def test_a_genuine_write_failure_is_reported_as_one(self):
        run = _run(
            state="failed",
            points=[_point(30, rpm=900), _point(60, accepted=False, rv="unavailable")],
            summary={
                "command_acceptance": "partial",
                "pwm_readback": "pass",
                "rpm_response": "unavailable",
            },
        )
        view = build_characterization_view(run, header_label="AIO_PUMP")
        assert view.rows[1].result == "Write failed"
        assert view.rows[1].state == "critical"
        assert {c.label: c.value for c in view.verdicts}["PWM command"] == "Partly accepted"

    def test_an_unknown_verdict_token_is_rendered_rather_than_dropped(self):
        run = _run(
            points=[_point(30, rpm=900, rv="quantum_flux")],
            summary={
                "command_acceptance": "pass",
                "pwm_readback": "quantum_flux",
                "rpm_response": "responsive",
            },
        )
        view = build_characterization_view(run, header_label="AIO_PUMP")
        assert len(view.rows) == 1
        assert view.rows[0].result == "Quantum flux"
        assert {c.label: c.value for c in view.verdicts}["PWM readback"] == "Quantum flux"

    def test_progress_and_running_state_drive_the_cancel_affordance(self):
        running = _run(state="running", points=[_point(30, rpm=900)])
        view = build_characterization_view(running, header_label="AIO_PUMP")
        assert view.running and view.can_cancel
        assert view.progress_text == "1 of 3 points"
        done = _run(state="complete", points=[_point(30, rpm=900)])
        assert not build_characterization_view(done, header_label="x").can_cancel

    def test_a_failed_restore_is_surfaced(self):
        run = _run(restore_failed=True, points=[_point(30, rpm=900)])
        view = build_characterization_view(run, header_label="AIO_PUMP")
        assert any("Restoring the original speed failed" in n for n in view.notes)

    def test_no_run_yet_renders_without_raising(self):
        view = build_characterization_view(None, header_label="AIO_PUMP")
        assert view.rows == [] and not view.running and view.status_text


# ── Pump protection: the UNION, not the wire role ────────────────────


class TestPumpProtectionUnion:
    def test_a_downgraded_pump_label_is_still_protected(self):
        """DEC-312: assigning chassis_fan to a PUMP-labelled header does NOT
        remove the daemon's protection, so the copy must not claim it did."""
        header = _header(label="AIO_PUMP", role="chassis_fan", role_source="user_assigned")
        assert header_is_pump_protected(header, _caps()) is True

    def test_a_user_assigned_pump_on_an_unlabelled_header_is_protected(self):
        header = _header(label="pwm1", role="pump", role_source="user_assigned")
        assert header_is_pump_protected(header, _caps()) is True

    def test_a_plain_chassis_header_is_not_protected(self):
        header = _header(label="CHA_FAN1", role="chassis_fan")
        assert header_is_pump_protected(header, _caps()) is False

    def test_a_cooler_channel_one_labelled_cpu_fan_is_not_promoted(self):
        header = _header(label="CPU_FAN1", role="cpu_fan", is_aio=True, pwm_index=1)
        assert header_is_pump_protected(header, _caps()) is False

    def test_nothing_is_protected_without_the_role_capability(self):
        header = _header(label="AIO_PUMP", role="pump")
        assert header_is_pump_protected(header, _caps(roles=False)) is False

    def test_the_pump_warning_only_appears_for_a_protected_header(self):
        assert PUMP_STARTUP_WARNING in " ".join(pre_run_warnings(_header(), is_pump=True))
        assert PUMP_STARTUP_WARNING not in " ".join(pre_run_warnings(_header(), is_pump=False))

    def test_every_run_warns_that_curve_control_pauses(self):
        for is_pump in (True, False):
            assert any("paused" in w for w in pre_run_warnings(_header(), is_pump=is_pump)), (
                "the user must be told all fans hold their duty during the sweep"
            )


# ── Dialog ───────────────────────────────────────────────────────────


class TestDialog:
    def test_the_table_fills_progressively_across_snapshots(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._started = True
        for n in (1, 2, 3):
            dlg.apply_run(
                _run(
                    state="running",
                    points=[_point(p, rpm=900 * p) for p in [30, 60, 100][:n]],
                )
            )
            assert dlg._table.rowCount() == n, "each poll must add the new rows"
        assert dlg._table.item(0, 0).text() == "30%"

    def test_cancel_emits_and_start_emits_no_client_side_point_list(self, qtbot):
        """The DAEMON owns the point list and the pump floor. A client-side list
        would be a second copy of a safety rule, and the two would drift."""
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        started: list[tuple] = []
        cancelled: list[int] = []
        dlg.start_requested.connect(lambda h, p, s: started.append((h, p, s)))
        dlg.cancel_requested.connect(lambda: cancelled.append(1))
        dlg._start_btn.click()
        assert started == [("h1", None, None)]
        dlg._cancel_btn.click()
        assert cancelled == [1]

    def test_a_terminal_state_stops_polling_and_re_enables_start(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._start_btn.click()
        assert dlg._timer.isActive()
        dlg.apply_run(_run(state="complete", points=[_point(30, rpm=900)]))
        assert not dlg._timer.isActive(), "a finished run must stop the poll timer"
        assert dlg._start_btn.isEnabled()
        assert not dlg._cancel_btn.isEnabled()

    def test_a_lost_run_stops_polling_instead_of_stalling_silently(self, qtbot):
        """A daemon restart mid-sweep makes GET 404 -> None. That is terminal, not
        "not started yet" — otherwise the timer runs forever behind a dialog that
        reads "Ready to start."."""
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._start_btn.click()
        assert dlg._timer.isActive()
        dlg.apply_run(None)
        assert not dlg._timer.isActive(), "a lost run must stop the poll timer"
        assert dlg._start_btn.isEnabled()
        assert "no longer has this run" in dlg._status_lbl.text()

    def test_a_none_before_starting_is_not_treated_as_a_lost_run(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg.apply_run(None)
        assert dlg._status_lbl.text() == "Ready to start."
        assert dlg._start_btn.isEnabled()

    def test_a_soft_safety_refusal_is_shown_verbatim_not_as_an_error(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._start_btn.click()
        dlg.apply_error("unavailable", "Cannot run while hot: Tctl at 91.0°C")
        assert dlg._status_lbl.text() == "Cannot run while hot: Tctl at 91.0°C"
        assert not dlg._timer.isActive()
        dlg.apply_error("error", "boom")
        assert "Characterisation error" in dlg._status_lbl.text()

    def test_the_pump_copy_is_shown_only_for_a_protected_header(self, qtbot):
        pump = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        fan = PwmCharacterizationDialog("h2", "CHA_FAN1", is_pump=False)
        qtbot.addWidget(pump)
        qtbot.addWidget(fan)
        assert "never stopped" in pump._warnings.text()
        assert "never stopped" not in fan._warnings.text()

    def test_verdict_pills_carry_accessible_names(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._started = True
        dlg.apply_run(
            _run(
                points=[_point(30, rpm=900)],
                summary={
                    "command_acceptance": "pass",
                    "pwm_readback": "pass",
                    "rpm_response": "responsive",
                },
            )
        )
        pills = dlg.findChildren(StatusPill)
        assert len(pills) == 3
        for pill in pills:
            assert pill.accessibleName(), "a glyph-free pill still needs a name (DEC-251)"


# ── Capability gate on the page ──────────────────────────────────────


class TestPageGate:
    @staticmethod
    def _page(qtbot, caps):
        from control_ofc.services.app_state import AppState
        from control_ofc.ui.pages.system_state_page import SystemStatePage

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        state.set_capabilities(caps)
        state.set_hwmon_headers([_header()])
        page = SystemStatePage(state, None)
        qtbot.addWidget(page)
        page._populate_verify_combo()
        return page

    def _button(self, page) -> QPushButton:
        btn = page.findChild(QPushButton, "SystemState_Btn_characterize")
        assert btn is not None
        return btn

    def test_the_button_is_hidden_against_a_daemon_without_the_capability(self, qtbot):
        page = self._page(qtbot, _caps(characterization=False))
        btn = self._button(page)
        assert btn.isHidden(), "an older daemon 404s the route — never offer the button"
        assert not btn.isEnabled()

    def test_the_button_is_offered_when_the_capability_is_advertised(self, qtbot):
        page = self._page(qtbot, _caps())
        btn = self._button(page)
        assert not btn.isHidden()
        assert btn.isEnabled()

    def test_the_quick_verify_button_is_untouched_by_the_new_one(self, qtbot):
        """The brief: 'Do not replace the existing quick PWM verification test.'"""
        page = self._page(qtbot, _caps())
        verify = page.findChild(QPushButton, "SystemState_Btn_verifyPwm")
        assert verify is not None
        assert verify.text() == "Test PWM Control"
        assert verify.isEnabled()


# ── Client contract ──────────────────────────────────────────────────


class TestClientContract:
    def test_start_sends_only_what_the_caller_supplied(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        sent: dict = {}

        def fake_post(self, path, json=None, *, params=None, timeout=None):
            sent["path"] = path
            sent["json"] = json
            return {"run_id": "char-1", "header_id": "h1", "state": "running"}

        monkeypatch.setattr(DaemonClient, "_post", fake_post)
        client = DaemonClient.__new__(DaemonClient)
        run = client.start_characterization("h1")
        assert sent["path"] == "/hwmon/h1/characterize"
        assert sent["json"] == {}, "no client-side clamp — the daemon owns the floor"
        assert run.is_running

        client.start_characterization("h1", points_pct=[40, 80], settle_seconds=4)
        assert sent["json"] == {"points_pct": [40, 80], "settle_seconds": 4}

    def test_status_returns_none_on_404_and_reraises_everything_else(self, monkeypatch):
        from control_ofc.api.client import DaemonClient
        from control_ofc.api.errors import DaemonError

        def not_found(self, path, *, params=None, timeout=None):
            raise DaemonError(
                code="validation_error",
                message="none yet",
                retryable=False,
                source="validation",
                status=404,
                details=None,
                endpoint=path,
            )

        monkeypatch.setattr(DaemonClient, "_get", not_found)
        client = DaemonClient.__new__(DaemonClient)
        assert client.characterization_status() is None

        def boom(self, path, *, params=None, timeout=None):
            raise DaemonError(
                code="internal_error",
                message="boom",
                retryable=False,
                source="internal",
                status=500,
                details=None,
                endpoint=path,
            )

        monkeypatch.setattr(DaemonClient, "_get", boom)
        with pytest.raises(DaemonError):
            client.characterization_status()

    def test_cancel_targets_the_diagnostics_route(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        seen: dict = {}

        def fake_delete(self, path, json=None, *, timeout=None):
            seen["path"] = path
            return {"run_id": "char-1", "state": "cancelled"}

        monkeypatch.setattr(DaemonClient, "_delete", fake_delete)
        client = DaemonClient.__new__(DaemonClient)
        assert client.cancel_characterization().state == "cancelled"
        assert seen["path"] == "/diagnostics/characterization"


class TestForeignRunSnapshots:
    """`AUD2-a`: `GET /diagnostics/characterization` serves ONE process-global
    slot, so a snapshot can legitimately be about a different header — a poll
    queued behind our own blocking POST returns the *previous* run, and a second
    client owning the slot does the same. Rendering it under this dialog's label
    attributes another header's measurements to this one, in the one feature
    whose entire purpose is a per-header verdict.
    """

    def test_a_snapshot_for_another_header_is_not_rendered(self, qtbot):
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._started = True

        dlg.apply_run(_run(header_id="h1", state="running", points=[_point(30, rpm=900)]))
        assert dlg._table.rowCount() == 1, "precondition: our own run renders"

        dlg.apply_run(
            _run(
                header_id="h2",
                state="complete",
                points=[_point(p, rpm=100 * p) for p in (30, 60, 100)],
            )
        )
        assert dlg._table.rowCount() == 1, (
            "a completed run on h2 was rendered as this dialog's result for h1"
        )

    def test_a_foreign_terminal_snapshot_does_not_end_our_run(self, qtbot):
        """The damage is not only the table: a foreign *terminal* snapshot also
        stops the poll timer and relabels Start as "Run again", so our own live
        sweep goes unwatched and reads as finished."""
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._start_btn.click()
        assert dlg._timer.isActive(), "precondition: our run is being polled"

        dlg.apply_run(_run(header_id="h2", state="complete", points=[_point(30, rpm=900)]))

        assert dlg._timer.isActive(), "another header's finished run stopped our polling"
        assert not dlg._finished

    def test_a_snapshot_without_a_header_id_is_still_rendered(self, qtbot):
        """A daemon too old to send one must not blank the dialog — refusing an
        empty id would trade a wrong reading for no reading."""
        dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
        qtbot.addWidget(dlg)
        dlg._started = True

        dlg.apply_run(_run(header_id="", state="running", points=[_point(30, rpm=900)]))

        assert dlg._table.rowCount() == 1


class TestRestoreOutcomeNotes:
    """`AUD2-c`: the daemon used to publish `restore_failed: false` on three
    exits that deliberately did NOT restore, so the GUI said nothing about a
    header parked at a test duty. It now says which — and the wording has to
    differ per reason, because the single old note's advice ("re-activate your
    profile") is the one thing a user must not act on under a thermal force.
    """

    def _notes(self, **kw) -> str:
        run = _run(state="aborted", points=[_point(30, rpm=900)], **kw)
        return "\n".join(build_characterization_view(run, header_label="AIO_PUMP").notes)

    def test_a_restored_header_says_nothing_about_the_restore(self):
        assert "restor" not in self._notes(restore_failed=False, restore_outcome="restored").lower()

    def test_a_failed_restore_still_tells_the_user_to_retake_control(self):
        notes = self._notes(restore_failed=True, restore_outcome="write_failed")
        assert "Re-activate your profile" in notes

    def test_a_thermal_skip_does_not_tell_the_user_to_retake_control(self):
        """The wrong-advice case, stated as an absence AND a presence — an
        absence assertion on its own passes vacuously (CLAUDE.md § Hard-won
        lessons)."""
        notes = self._notes(restore_failed=True, restore_outcome="skipped_thermal_force")
        assert "Thermal safety" in notes, "the reason must be surfaced at all"
        assert "Re-activate your profile" not in notes, (
            "telling the user to retake control while the ladder is forcing is "
            "exactly the action the reason token exists to prevent"
        )

    def test_a_shutdown_skip_is_explained_rather_than_called_a_failure(self):
        notes = self._notes(restore_failed=True, restore_outcome="skipped_shutting_down")
        assert "shutting down" in notes
        assert "failed" not in notes.lower()

    def test_an_unreadable_pre_sweep_duty_is_explained(self):
        notes = self._notes(restore_failed=True, restore_outcome="no_original_duty")
        assert "could not be read" in notes

    def test_an_unrecognised_reason_is_rendered_not_dropped(self):
        """273-i: the client owns the wording, so a token this build has never
        seen must still produce a note rather than silence."""
        notes = self._notes(restore_failed=True, restore_outcome="seized_by_firmware")
        assert "Seized by firmware" in notes

    def test_an_older_daemon_that_sends_no_reason_keeps_the_failure_note(self):
        """Pre-2.30.0 `restore_failed: true` could only mean the write failed."""
        notes = self._notes(restore_failed=True)
        assert "Re-activate your profile" in notes


class TestRestoreNoteDoesNotTrustOneField:
    """`restore_note` reconstructs "was it put back?" from both fields.

    The daemon derives one from the other, so they cannot disagree today. Taking
    its word for it is the thing `AUD2-c` was, though: a version-skewed or
    partial response naming a skip while reporting `restore_failed: false` would
    fall silent in exactly the old way.
    """

    def _notes(self, **kw) -> str:
        run = _run(state="aborted", points=[_point(30, rpm=900)], **kw)
        return "\n".join(build_characterization_view(run, header_label="AIO_PUMP").notes)

    def test_a_skip_reason_is_surfaced_even_if_the_boolean_says_otherwise(self):
        notes = self._notes(restore_failed=False, restore_outcome="skipped_thermal_force")
        assert "Thermal safety" in notes

    def test_a_running_or_absent_outcome_with_a_false_boolean_stays_silent(self):
        for outcome in ("", "pending", "restored"):
            assert (
                "restor" not in self._notes(restore_failed=False, restore_outcome=outcome).lower()
            ), f"{outcome!r} must not produce a note"
