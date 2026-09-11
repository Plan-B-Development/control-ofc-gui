"""DEC-358 — a warning the user dealt with must not come back on its own.

Three defects, all of which made the System State page shout again about
something that had not got worse:

* a dismissal was voided by *any* evidence change, so pressing the page's own
  **Test fan control** button and getting a clean result resurrected every note
  the user had dismissed;
* an `rpm_unavailable` verify result — the daemon's way of saying "the write
  landed, there is just no tach to confirm it with" — was recorded as
  `ineffective`, which promoted every triggerless board note to a condition
  card that re-testing could never clear, while the same sweep showed the user
  a green success chip;
* the page rendered a `/diagnostics/hardware` snapshot it fetched once, with no
  way to retake it and the only link to the full report hidden inside a
  collapsed section.

Two rules govern the tests below. Every silencing test is **two-armed** —
improvement must not break the silence, escalation must — because a one-armed
test passes with the comparison replaced by a constant. And the verify tests
assert the recorded outcome and the on-screen chip **against each other**,
never against two literals: the defect was that those two disagreed, so a test
that pins each separately would have passed while it was live.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from control_ofc.api.models import (
    BoardInfo,
    DaemonStatus,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ThermalSafetyInfo,
)
from control_ofc.services.system_state_view import (
    build_board_notes,
    build_safety_gpu_vm,
    build_system_state_vm,
    clear_silence,
    is_silenced,
    note_ack_key,
    silence_index,
)
from control_ofc.services.verify_view import (
    PWM_EVIDENCE_EFFECTIVE,
    PWM_EVIDENCE_INCONCLUSIVE,
    PWM_EVIDENCE_INEFFECTIVE,
    outcome_for,
    verify_sweep_chip_class,
    verify_sweep_outcome,
)
from control_ofc.ui.widgets.readiness_report import (
    EVIDENCE_NOT_OBSERVED,
    EVIDENCE_OBSERVED,
    EVIDENCE_UNVERIFIED,
    board_notes,
    evidence_rank,
)

_GIGABYTE = "Gigabyte Technology Co., Ltd."


def _healthy_gigabyte() -> HardwareDiagnosticsResult:
    """The live machine this was reproduced on — nothing wrong with it.

    Matches the `gigabyte`/`it8696` quirk, whose trigger is `bios_revert`: no
    reclaim has been counted, and an empty reclaim map is *not* counter-evidence
    (it is equally consistent with "the daemon has never written"), so the note
    sits at `unverified` until a fan-control test settles it. That is the state
    a user dismisses from, which is what makes it the right fixture here.
    """
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(
                    chip_name="it8696",
                    expected_driver="it87",
                    in_mainline_kernel=False,
                    header_count=8,
                )
            ],
            total_headers=8,
            writable_headers=8,
        ),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E AORUS MASTER"),
        kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        # 110, deliberately not the 105 fallback: the trip point is per-machine
        # (DEC-308), so a fixture that happened to match the default would not
        # prove the rendered figure came from the daemon at all.
        thermal_safety=ThermalSafetyInfo(
            state="normal", cpu_sensor_found=True, emergency_threshold_c=110.0
        ),
        cpu_vendor="AMD",
    )


def _bios_revert_note(diag=None, **kw):
    diag = diag if diag is not None else _healthy_gigabyte()
    return next(n for n in board_notes(diag, **kw) if n.quirk.trigger == "bios_revert")


# ── ACK-b: a silence survives improvement and breaks on escalation ────────


def test_evidence_ranks_in_escalation_order():
    """The ordering the whole silencing rule rests on, asserted as an ordering."""
    assert evidence_rank(EVIDENCE_NOT_OBSERVED) < evidence_rank(EVIDENCE_UNVERIFIED)
    assert evidence_rank(EVIDENCE_UNVERIFIED) < evidence_rank(EVIDENCE_OBSERVED)
    # An unrecognised state from a newer build outranks every known one, so it
    # breaks a silence rather than hiding inside it.
    assert evidence_rank("something_new") > evidence_rank(EVIDENCE_OBSERVED)


def test_a_clean_verify_does_not_resurrect_a_dismissed_note():
    """The measured bug: dismiss, press *Test fan control*, get it back.

    The precondition is load-bearing. If the evidence did not actually move
    between the two arms there is nothing for the rank comparison to be right
    *about*, and the test would pass against plain string equality — the very
    thing it exists to reject (``CLAUDE.md § Hard-won lessons``: pick the sample
    that can move).
    """
    diag = _healthy_gigabyte()
    note = _bios_revert_note(diag)
    dismissed = {note_ack_key(note.key, note.evidence)}

    improved = _bios_revert_note(diag, pwm_control_verified=True)
    assert note.evidence == EVIDENCE_UNVERIFIED
    assert improved.evidence == EVIDENCE_NOT_OBSERVED
    assert evidence_rank(improved.evidence) < evidence_rank(note.evidence), (
        "precondition: the clean test must genuinely move the evidence, downward"
    )

    after = build_board_notes(diag, pwm_control_verified=True, dismissed=dismissed)
    assert note.key not in {n.key for n in after.notes}, "a clean result resurrected the note"
    assert after.hidden_count == 1


def test_a_confirmed_note_still_breaks_its_own_dismissal():
    """The arm that discriminates: only escalation may un-silence.

    Without this the change above is indistinguishable from "never re-show a
    dismissed note", which is the permanent suppression DEC-282 removed.
    """
    diag = _healthy_gigabyte()
    note = _bios_revert_note(diag)
    dismissed = {note_ack_key(note.key, note.evidence)}

    promoted = _bios_revert_note(diag, pwm_control_verified=False)
    assert evidence_rank(promoted.evidence) > evidence_rank(note.evidence)

    after = build_board_notes(diag, pwm_control_verified=False, dismissed=dismissed)
    assert note.key in {n.key for n in after.notes}
    assert after.hidden_count == 0


def test_a_silence_is_matched_by_rank_not_by_string_equality():
    """The rule itself, at the unit it is implemented in."""
    index = silence_index({note_ack_key("q", EVIDENCE_UNVERIFIED)})
    assert is_silenced(index, "q", EVIDENCE_UNVERIFIED) is True
    assert is_silenced(index, "q", EVIDENCE_NOT_OBSERVED) is True, "improvement holds the silence"
    assert is_silenced(index, "q", EVIDENCE_OBSERVED) is False, "escalation breaks it"
    assert is_silenced(index, "other", EVIDENCE_UNVERIFIED) is False


def test_the_loudest_stored_silence_wins():
    index = silence_index(
        {note_ack_key("q", EVIDENCE_UNVERIFIED), note_ack_key("q", EVIDENCE_OBSERVED)}
    )
    assert is_silenced(index, "q", EVIDENCE_OBSERVED) is True


def test_a_quirk_key_containing_an_at_sign_still_parses():
    """`rpartition`, not `split` — evidence never contains "@", a key might."""
    index = silence_index({note_ack_key("vendor@board", EVIDENCE_UNVERIFIED)})
    assert is_silenced(index, "vendor@board", EVIDENCE_UNVERIFIED) is True
    assert is_silenced(index, "vendor", EVIDENCE_UNVERIFIED) is False


def test_clearing_a_silence_removes_every_evidence_it_was_stored_at():
    """The edge case rank matching introduces.

    A note acknowledged at `unverified` and since improved is *still*
    acknowledged, but the ack key the row now carries was never stored. Removing
    only that key would have appended it — leaving the note more thoroughly
    silenced than before the user asked to un-silence it.
    """
    stored = [
        note_ack_key("q", EVIDENCE_UNVERIFIED),
        note_ack_key("q", EVIDENCE_OBSERVED),
        note_ack_key("other", EVIDENCE_UNVERIFIED),
    ]
    remaining = clear_silence(stored, "q")
    assert remaining == [note_ack_key("other", EVIDENCE_UNVERIFIED)]
    assert is_silenced(silence_index(remaining), "q", EVIDENCE_NOT_OBSERVED) is False


# ── ACK-a: one verify vocabulary, two consumers that cannot disagree ──────


def test_a_missing_tach_is_inconclusive_not_a_failure():
    """Asserted against the vocabulary, never against the literal token.

    `rpm_unavailable` is the daemon's "PWM values held but RPM sensor
    unavailable" — the write landed. Pinning it as a *relationship* is what
    stops the assertion being satisfied by a call site that re-derives the
    verdict from its own list of tokens, which is what the defect was.
    """
    assert outcome_for("rpm_unavailable").evidence == PWM_EVIDENCE_INCONCLUSIVE
    assert outcome_for("effective").evidence == PWM_EVIDENCE_EFFECTIVE
    assert outcome_for("no_rpm_effect").evidence == PWM_EVIDENCE_INEFFECTIVE
    assert outcome_for("pwm_enable_reverted").evidence == PWM_EVIDENCE_INEFFECTIVE
    # A token this build does not know, and a sweep-level error, are both
    # inconclusive: neither may be invented into a verdict about the hardware.
    assert outcome_for("brand_new_token").evidence == PWM_EVIDENCE_INCONCLUSIVE
    assert outcome_for("error:unavailable").evidence == PWM_EVIDENCE_INCONCLUSIVE


def test_one_tachless_header_does_not_condemn_a_working_board():
    """The measured sweep: two good headers and a fan idling at 0 RPM."""
    results = ["effective", "effective", "rpm_unavailable"]
    assert verify_sweep_outcome(results) == PWM_EVIDENCE_EFFECTIVE
    assert verify_sweep_chip_class(results) == "SuccessChip"


def test_a_genuinely_bad_header_still_condemns_the_sweep():
    """The opposite arm — an inconclusive result must not *mask* a failure."""
    results = ["effective", "rpm_unavailable", "pwm_enable_reverted"]
    assert verify_sweep_outcome(results) == PWM_EVIDENCE_INEFFECTIVE
    assert verify_sweep_chip_class(results) == "CriticalChip"


def test_an_all_inconclusive_sweep_settles_nothing():
    """No tach anywhere: not a pass, not a failure, and not green."""
    results = ["rpm_unavailable", "rpm_unavailable"]
    assert verify_sweep_outcome(results) is None
    assert verify_sweep_chip_class(results) == "CardMeta"
    assert verify_sweep_outcome([]) is None


# ── ACK-a at the call site — the two consumers, driven together ───────────


def _page(qtbot, diag=None):
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    svc = AppSettingsService()
    page = SystemStatePage(diagnostics_service=DiagnosticsService(None), settings_service=svc)
    qtbot.addWidget(page)
    page._render(diag if diag is not None else _healthy_gigabyte())
    return page, svc


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


def _run_sweep(page, results):
    """Drive the real summary path, not `_record_verify_outcome` directly.

    The extracted-rule trap (`CLAUDE.md § Hard-won lessons`): every pre-existing
    test called `_record_verify_outcome` itself, which proves the helper honours
    its input and says nothing about the chip rendered beside it. The defect
    lived precisely in the gap between those two consumers, so the test has to
    run the call site that feeds both.
    """
    page._verify_all_results = [(f"pwm{i + 1}", r) for i, r in enumerate(results)]
    page._verify_all_total = len(results)
    page._show_verify_all_summary()
    return page._verify_all_progress_label.property("class")


def test_the_summary_chip_and_the_recorded_outcome_never_disagree(qtbot):
    """The defect, stated as the invariant it broke.

    Asserted as a relationship between the two consumers rather than as two
    literals: a green chip beside a persisted `ineffective` is the bug, and only
    comparing them can see it.
    """
    page, svc = _page(qtbot)
    assert page._verify_all_progress_label is not None  # DEC-356: bind and use

    chip = _run_sweep(page, ["effective", "effective", "rpm_unavailable"])
    recorded = svc.settings.last_pwm_verify_effective
    assert (chip == "SuccessChip") == (recorded == PWM_EVIDENCE_EFFECTIVE), (
        f"same sweep, opposite verdicts: chip={chip} recorded={recorded}"
    )
    assert recorded == PWM_EVIDENCE_EFFECTIVE

    chip = _run_sweep(page, ["effective", "no_rpm_effect"])
    recorded = svc.settings.last_pwm_verify_effective
    assert (chip == "SuccessChip") == (recorded == PWM_EVIDENCE_EFFECTIVE)
    assert recorded == PWM_EVIDENCE_INEFFECTIVE
    _flush(page)


def test_an_inconclusive_sweep_leaves_a_previous_result_alone(qtbot):
    """`None` means "record nothing", not "record a third verdict"."""
    page, svc = _page(qtbot)
    assert page._verify_all_progress_label is not None  # DEC-356: bind and use
    svc.update(last_pwm_verify_effective=PWM_EVIDENCE_EFFECTIVE)
    chip = _run_sweep(page, ["rpm_unavailable", "rpm_unavailable"])
    assert svc.settings.last_pwm_verify_effective == PWM_EVIDENCE_EFFECTIVE
    assert chip == "CardMeta", "nothing failed, but nothing was demonstrated either"
    _flush(page)


def test_a_tachless_sweep_does_not_mint_a_condition_card(qtbot):
    """The user-visible end of the same defect.

    A false `ineffective` flips every triggerless quirk to *observed*, and a
    promoted quirk becomes a condition card that re-testing can never clear —
    the tach is missing by construction.
    """
    from PySide6.QtWidgets import QFrame

    page, svc = _page(qtbot)
    assert page.findChild(QFrame, "SystemState_Card_health") is not None
    _run_sweep(page, ["effective", "rpm_unavailable"])
    _flush(page)
    assert svc.settings.last_pwm_verify_effective == PWM_EVIDENCE_EFFECTIVE
    assert not [
        p
        for p in build_system_state_vm(_healthy_gigabyte(), pwm_control_verified=True).issue_cards
        if p.key.startswith("quirk_")
    ]


# ── The pill counts conditions, and silencing cannot reach it ─────────────


def test_a_dismissed_note_does_not_change_the_action_required_count():
    """A health number is never quietened by a button.

    Board notes were never counted in the pill, and Phase 2 adds dismissal to
    the cards that *are*. Pinning the invariant now is what makes that a
    deliberate decision rather than an accident nobody notices breaking.
    """
    diag = _healthy_gigabyte()
    note = _bios_revert_note(diag)
    dismissed = {note_ack_key(note.key, note.evidence)}

    loud = build_system_state_vm(diag)
    quiet = build_system_state_vm(diag, dismissed_notes=dismissed)
    assert quiet.board_notes.hidden_count == 1, "precondition: something really was hidden"
    assert quiet.issues_requiring_attention == loud.issues_requiring_attention
    assert quiet.issue_count_label == loud.issue_count_label


# ── The thermal row follows the live poll, not the fetched snapshot ───────


def test_the_thermal_row_prefers_the_live_state_over_the_snapshot():
    """A snapshot fetched once must not be rendered as if it were current."""
    diag = _healthy_gigabyte()
    assert diag.thermal_safety.state == "normal"

    stale = build_safety_gpu_vm(diag)
    live = build_safety_gpu_vm(diag, live_thermal_state="emergency")
    assert stale.thermal_state == "ok"
    assert live.thermal_state == "crit"
    assert live.thermal_text == "Emergency"


def test_the_thermal_row_falls_back_to_the_snapshot_before_the_first_poll():
    """The opposite arm: `None` must not blank the row."""
    diag = _healthy_gigabyte()
    assert build_safety_gpu_vm(diag, live_thermal_state=None).thermal_text == "Normal"
    assert build_safety_gpu_vm(diag, live_thermal_state="").thermal_text == "Normal"


def test_the_thermal_limit_is_interpolated_from_what_the_daemon_reported():
    """DEC-308: the trip point is per-machine. Never a literal, never compared
    to one — this fixture deliberately reports 110, not the 105 floor."""
    vm = build_safety_gpu_vm(_healthy_gigabyte(), live_thermal_state="emergency")
    assert (
        str(int(_healthy_gigabyte().thermal_safety.emergency_threshold_c)) in vm.thermal_limit_text
    )


def test_a_live_thermal_change_re_renders_the_page(qtbot):
    page, _svc = _page(qtbot)
    assert page.findChild(QPushButton, "SystemState_Btn_refresh") is not None
    page.set_thermal_state("emergency")
    assert page._live_thermal_state == "emergency"
    assert page._safety_card is not None
    # An unchanged value must not re-render — this row sits on the 1 Hz path.
    page._last_rendered_diag = None
    page.set_thermal_state("emergency")
    _flush(page)


def test_main_window_feeds_the_page_the_same_field_as_the_ribbon():
    """The call-site test for the wiring, not just for the slot.

    `CLAUDE.md § Hard-won lessons`: a rule with thorough tests and nothing
    asserting the production path calls it is an untested rule.
    """
    import inspect

    from control_ofc.ui.main_window import MainWindow

    src = inspect.getsource(MainWindow._on_status_for_ribbon)
    assert "system_state_page.set_thermal_state" in src
    assert src.count("status.thermal_state") >= 3, (
        "ribbon, footer and System State must read one field, not three sources"
    )


# ── ACK-e / ACK-f: the report and refresh are reachable ───────────────────


def test_the_report_and_refresh_buttons_are_reachable_without_expanding_anything(qtbot):
    """`isVisibleTo(page)`, never `isVisible()`.

    Under `QT_QPA_PLATFORM=offscreen` nothing is shown, so `isVisible()` is
    False for every widget and the assertion would pass with the buttons put
    back inside the collapsed section (DEC-324).
    """
    page, _svc = _page(qtbot)
    report = page.findChild(QPushButton, "SystemState_Btn_openReport")
    refresh = page.findChild(QPushButton, "SystemState_Btn_refresh")
    assert report is not None and refresh is not None
    assert report.isVisibleTo(page) is True
    assert refresh.isVisibleTo(page) is True

    # The discriminating arm: the Advanced actions section really is collapsed,
    # so a button left inside it would NOT be visible to the page.
    verify_all = page.findChild(QPushButton, "SystemState_Btn_verifyAll")
    assert verify_all is not None
    assert verify_all.isVisibleTo(page) is False, (
        "precondition: Advanced actions is collapsed, which is what made the "
        "report unreachable in the first place"
    )
    _flush(page)


def test_refresh_refetches_even_when_the_cache_is_warm(qtbot):
    """`.click()` on the real button — the connection is the likeliest break.

    The page latches `_hw_diag_fetched` after its first attempt and renders from
    the cache forever after, so a refresh that only calls the fetch helper would
    be inert on every visit but the first. That is the bug wearing a button.
    """
    page, _svc = _page(qtbot)
    calls: list[bool] = []
    page._fetch_hardware_diagnostics = lambda: calls.append(True)
    page._hw_diag_fetched = True  # as it is after the first visit

    page.findChild(QPushButton, "SystemState_Btn_refresh").click()
    assert calls == [True], "the refresh button did not reach the fetch path"
    _flush(page)


def test_refresh_does_not_stack_up_while_one_is_in_flight(qtbot):
    """`/diagnostics/hardware` is the expensive call on this page.

    Found in self-review rather than by a reviewer: the first draft of the
    button had no guard at all, so holding it down queued one fetch per click.
    Both arms matter — the guard must also *release*, or the button works once
    and is dead for the rest of the session, which is a worse bug than the one
    it fixes.
    """
    page, _svc = _page(qtbot)
    calls: list[bool] = []
    page._fetch_hardware_diagnostics = lambda: calls.append(True)
    btn = page.findChild(QPushButton, "SystemState_Btn_refresh")

    btn.click()
    assert calls == [True]
    assert btn.isEnabled() is False, "the button must show that a fetch is running"
    btn.click()
    page._force_refresh_diagnostics()  # even bypassing the disabled button
    assert calls == [True], "a second fetch was queued while one was in flight"

    page._on_hw_diag_error("unavailable", "daemon down")
    assert btn.isEnabled() is True, "the guard must release on the error path too"
    btn.click()
    assert calls == [True, True], "the button was dead after one use"
    _flush(page)


def _state_with_headers():
    """Three writable headers, so the verify combo is genuinely non-empty.

    Load-bearing, and the fix-out check is what proved it. With `_page()`'s
    default `_state = None` the combo is empty, so the *ungated*
    `setEnabled(count() > 0)` also evaluates to False — the mid-sweep test
    passed with its own fix deleted, because the enable path could never fire
    (`CLAUDE.md`: the arm where the lookup finds nothing returns the pre-fix
    answer by construction).
    """
    from control_ofc.api.models import HwmonHeader

    class _State:
        def __init__(self):
            self.hwmon_headers = [
                HwmonHeader(id="hwmon:it8696:dev:pwm1:CPU_FAN", label="CPU_FAN", is_writable=True),
                HwmonHeader(id="hwmon:it8696:dev:pwm2:SYS_FAN", label="SYS_FAN", is_writable=True),
                HwmonHeader(id="hwmon:it8696:dev:pwm3:AIO", label="AIO", is_writable=True),
            ]

        @staticmethod
        def fan_display_name(header_id):
            return header_id.rsplit(":", 1)[-1]

    return _State()


def test_a_live_thermal_change_cannot_re_enable_verify_mid_sweep(qtbot):
    """Reviewer P1: the autonomous re-render must not unlock a hardware write.

    `_render` calls `_populate_verify_combo`, which re-enables `_verify_btn`.
    Before DEC-358 every re-render followed a user action; the live thermal push
    can land at any instant, including inside a sweep. `_on_verify_ok` appends
    *any* result into `_verify_all_results` and steps the queue whenever a sweep
    is open, so a second concurrent verify pops a header the sweep never
    reported and corrupts the verdict this change persists.
    """
    page, _svc = _page(qtbot)
    page._state = _state_with_headers()
    page._populate_verify_combo()
    btn = page.findChild(QPushButton, "SystemState_Btn_verifyPwm")
    assert btn is not None
    assert page._verify_combo.count() == 3, (
        "precondition: the combo must be NON-EMPTY, or the ungated "
        "setEnabled(count() > 0) is False anyway and this test proves nothing"
    )

    page._verify_all_total = 3  # a sweep is running
    btn.setEnabled(False)  # as `_run_pwm_verify_all` leaves it
    page.set_thermal_state("emergency")
    assert page._live_thermal_state == "emergency", "precondition: the re-render really fired"
    assert btn.isEnabled() is False, "a background re-render unlocked a hardware write mid-sweep"

    # The opposite arm — with no sweep running the button must come back, or the
    # guard has simply disabled the feature rather than protecting it.
    page._verify_all_total = 0
    page.set_thermal_state("normal")
    assert btn.isEnabled() is True
    _flush(page)


def test_a_re_render_keeps_the_header_the_user_selected(qtbot):
    """Reviewer P2: `clear()` resets `currentIndex`, so the pick was lost.

    Sampled on the header at index 1, never index 0 — index 0 survives a reset
    by accident, so asserting on it would pass with the restore deleted
    (`CLAUDE.md`: pick the sample that can move).

    The first draft of this test set `page._state = None`, which left the combo
    **empty** after the rebuild, so its assertion sat behind `if count():` and
    never ran. It is written against a real header set precisely so it cannot
    pass by asserting nothing.
    """
    page, _svc = _page(qtbot)
    page._state = _state_with_headers()
    page._populate_verify_combo()
    assert page._verify_combo.count() == 3, "precondition: the combo really was populated"

    page._verify_combo.setCurrentIndex(1)
    chosen = page._verify_combo.currentData()
    assert page._verify_combo.currentIndex() != 0, "precondition: a pick a reset would destroy"

    page.set_thermal_state("emergency")  # an autonomous re-render, mid-decision
    assert page._live_thermal_state == "emergency", "precondition: the re-render really fired"
    assert page._verify_combo.count() == 3
    assert page._verify_combo.currentData() == chosen, (
        "a background re-render silently moved the user's selection to another header"
    )
    _flush(page)


def test_the_showevent_fetch_arms_the_same_guard_as_refresh(qtbot):
    """Reviewer P3: the guard was narrower than its own docstring claimed."""
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    svc = AppSettingsService()
    page = SystemStatePage(diagnostics_service=DiagnosticsService(None), settings_service=svc)
    qtbot.addWidget(page)
    calls: list[bool] = []
    page._fetch_hardware_diagnostics = lambda: calls.append(True)

    page.show()  # the real show event, not a hand-rolled call to the handler
    assert calls == [True], "precondition: the first show really did fetch"
    assert page._hw_diag_in_flight is True
    page.findChild(QPushButton, "SystemState_Btn_refresh").click()
    assert calls == [True], "Refresh raced the page's own first fetch"
    _flush(page)


def test_the_report_button_is_disabled_until_there_is_something_to_report(qtbot):
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    svc = AppSettingsService()
    page = SystemStatePage(diagnostics_service=DiagnosticsService(None), settings_service=svc)
    qtbot.addWidget(page)
    report = page.findChild(QPushButton, "SystemState_Btn_openReport")
    assert report.isEnabled() is False
    page._render(_healthy_gigabyte())
    assert report.isEnabled() is True
    _flush(page)


def test_the_poll_status_model_still_carries_the_field_the_page_reads():
    """One wire field, one gating shape (DEC-334)."""
    assert DaemonStatus().thermal_state == "normal"
