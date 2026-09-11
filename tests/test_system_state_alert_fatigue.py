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
from control_ofc.services.health_ack import Occurrence, build_index, clear_key, is_silenced
from control_ofc.services.system_state_view import (
    SilenceState,
    build_board_notes,
    build_condition_cards,
    build_interference_vm,
    build_safety_gpu_vm,
    build_system_state_vm,
    condition_fingerprint,
    note_ack_key,
    state_rank,
)
from control_ofc.services.verify_view import (
    PWM_EVIDENCE_EFFECTIVE,
    PWM_EVIDENCE_INCONCLUSIVE,
    PWM_EVIDENCE_INEFFECTIVE,
    outcome_for,
    verify_sweep_chip_class,
    verify_sweep_outcome,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.widgets.readiness_report import (
    EVIDENCE_NOT_OBSERVED,
    EVIDENCE_OBSERVED,
    EVIDENCE_UNVERIFIED,
    RECLAIM_HISTORIC_AFTER_MS,
    board_notes,
    evidence_rank,
)
from control_ofc.ui.widgets.readiness_report import evidence_rank as _rank


def silence_index(stored):
    """DEC-358's note-local index, now `health_ack.build_index` (DEC-359).

    Kept as a test-local shim so the Phase-1 assertions still read as they were
    written — they pin a RULE (a silence survives improvement, breaks on
    escalation) that is unchanged, and rewriting them alongside the move would
    have made it impossible to see that the rule did not move with the code.
    """
    return build_index(stored, _rank)


def _silenced(index, key, evidence):
    return is_silenced(index, Occurrence(key, "", evidence), _rank(evidence))


def clear_silence(stored, key):
    return clear_key(stored, key)


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
    assert _silenced(index, "q", EVIDENCE_UNVERIFIED) is True
    assert _silenced(index, "q", EVIDENCE_NOT_OBSERVED) is True, "improvement holds the silence"
    assert _silenced(index, "q", EVIDENCE_OBSERVED) is False, "escalation breaks it"
    assert _silenced(index, "other", EVIDENCE_UNVERIFIED) is False


def test_the_loudest_stored_silence_wins():
    index = silence_index(
        {note_ack_key("q", EVIDENCE_UNVERIFIED), note_ack_key("q", EVIDENCE_OBSERVED)}
    )
    assert _silenced(index, "q", EVIDENCE_OBSERVED) is True


def test_a_quirk_key_containing_an_at_sign_still_parses():
    """`rpartition`, not `split` — evidence never contains "@", a key might."""
    index = silence_index({note_ack_key("vendor@board", EVIDENCE_UNVERIFIED)})
    assert _silenced(index, "vendor@board", EVIDENCE_UNVERIFIED) is True
    assert _silenced(index, "vendor", EVIDENCE_UNVERIFIED) is False


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
    assert _silenced(silence_index(remaining), "q", EVIDENCE_NOT_OBSERVED) is False


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


# ── DEC-359 Phase 2: every health surface gets the same lifecycle ─────────


def _msi_collision():
    """A board with a real, measured condition — a driver-module collision."""
    from control_ofc.api.models import ModuleCollisionInfo

    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="nct6798", expected_driver="nct6775")],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor="Micro-Star International Co., Ltd.", name="MAG B450"),
        module_collisions=[ModuleCollisionInfo(module_a="nct6775", module_b="nct6687")],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )


def _condition(diag, key):
    return next(c for c in build_condition_cards(diag).cards if c.key == key)


def test_a_condition_can_be_dismissed_and_the_pill_still_counts_it():
    """The user's limit, pinned: a health number is never quietened by a button.

    Both halves are the assertion. The card must leave the list (that is the
    request) **and** the count must not move (that is the safety limit), so a
    page can never read SYSTEM READY while fan control is actually broken.
    """
    diag = _msi_collision()
    card = _condition(diag, "module_collision")
    assert card.silence.token, "precondition: the condition must be silenceable"

    loud = build_system_state_vm(diag)
    quiet = build_system_state_vm(
        diag, silence=SilenceState(dismissed=frozenset({card.silence.token}))
    )
    assert "module_collision" not in {c.key for c in quiet.issue_cards}
    assert quiet.conditions_hidden_count == 1
    assert quiet.issues_requiring_attention == loud.issues_requiring_attention
    assert quiet.issue_count_label == loud.issue_count_label


def test_a_changed_condition_speaks_again():
    """The occurrence rule for conditions: the fingerprint is the evidence.

    Sampled on `acpi`, whose fingerprint can genuinely move, rather than on a
    binary condition whose fingerprint is constant — a binary one would pass
    with the fingerprint deleted entirely (`CLAUDE.md`: pick the sample that
    can move).
    """
    from control_ofc.api.models import AcpiConflictInfo

    def _with(conflicts):
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="it8696")],
                total_headers=8,
                writable_headers=8,
            ),
            board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
            acpi_conflicts=conflicts,
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            cpu_vendor="AMD",
        )

    one = _with(
        [AcpiConflictInfo(io_range="0x290", claimed_by="ACPI", conflicts_with_driver="it87")]
    )
    two = _with(
        [
            AcpiConflictInfo(io_range="0x290", claimed_by="ACPI", conflicts_with_driver="it87"),
            AcpiConflictInfo(io_range="0x300", claimed_by="ACPI", conflicts_with_driver="it87"),
        ]
    )
    token = _condition(one, "acpi").silence.token
    assert _condition(one, "acpi").silence.token != _condition(two, "acpi").silence.token, (
        "precondition: a second conflict must be a different occurrence"
    )

    silenced = SilenceState(dismissed=frozenset({token}))
    assert "acpi" not in {c.key for c in build_condition_cards(one, silence=silenced).cards}
    assert "acpi" in {c.key for c in build_condition_cards(two, silence=silenced).cards}, (
        "a condition whose evidence changed must speak again"
    )


def test_a_reclaim_count_rising_does_not_break_its_own_dismissal():
    """`bios_revert` fingerprints the BUCKET, never the raw count.

    The count is monotonic within a daemon lifetime (`ACK-d`), so fingerprinting
    it would mint a new occurrence on every reclaim and the dismissal would
    survive exactly one tick — indistinguishable from not having one. The second
    arm proves the bucket still escalates, or this is just a permanent mute.
    """

    def _with(count):
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="it8696")],
                total_headers=8,
                writable_headers=8,
                enable_revert_counts={"pwm1": count},
            ),
            board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            cpu_vendor="AMD",
        )

    token = _condition(_with(1), "bios_revert").silence.token
    silenced = SilenceState(dismissed=frozenset({token}))
    keys = lambda d: {c.key for c in build_condition_cards(d, silence=silenced).cards}  # noqa: E731

    assert "bios_revert" not in keys(_with(2)), "one more reclaim un-silenced the dismissal"
    assert "bios_revert" not in keys(_with(9))
    assert "bios_revert" in keys(_with(20)), "crossing into HIGH must speak again"


def test_the_interference_monitor_is_demoted_never_deleted():
    """A reading, not an alarm: quietening drops the colour, not the number."""
    diag = HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="it8696")],
            total_headers=8,
            writable_headers=8,
            enable_revert_counts={"pwm1": 4},
        ),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    loud = build_interference_vm(diag)
    assert loud.severity_state == "warn"
    quiet = build_interference_vm(
        diag, silence=SilenceState(dismissed=frozenset({loud.silence.token}))
    )
    assert quiet.severity_state == "neutral", "the alarm must stop"
    assert quiet.has_contention is True, "the reading must not vanish"
    assert quiet.highest_count == loud.highest_count
    assert quiet.gauge_fraction == loud.gauge_fraction


def test_the_thermal_row_is_demoted_never_deleted_and_escalation_wins():
    """The row the user put in scope over my recommendation.

    The second arm is what makes that safe: a silence taken at `normal` is
    outranked by a live escalation to `emergency`, which DEC-358's live-poll
    wiring delivers within a second.
    """
    diag = _healthy_gigabyte()
    loud = build_safety_gpu_vm(diag)
    silenced = SilenceState(dismissed=frozenset({loud.thermal_silence.token}))

    quiet = build_safety_gpu_vm(diag, silence=silenced)
    assert quiet.thermal_state == "neutral"
    assert quiet.thermal_text == loud.thermal_text, "the reading must not vanish"
    assert quiet.thermal_limit_text == loud.thermal_limit_text

    hot = build_safety_gpu_vm(diag, live_thermal_state="emergency", silence=silenced)
    assert hot.thermal_state == "crit", "an emergency must outrank a silence taken at normal"


def test_a_dismissed_kernel_advisory_is_honoured_on_the_page(qtbot):
    """`ACK-h`: "Don't show again" silenced the popup and nothing else."""
    from control_ofc.api.models import GpuDiagnosticsInfo, KernelWarning

    del qtbot
    diag = HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(chips_detected=[HwmonChipInfo(chip_name="it8696")]),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
        gpu=GpuDiagnosticsInfo(
            model_name="RX 7900 XTX",
            fan_control_method="pmfw_curve",
            kernel_warnings=[KernelWarning(id="kw-1", severity="high", message="SMU regression")],
        ),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    loud = build_safety_gpu_vm(diag)
    row = next(r for r in loud.gpu_rows if r.label.startswith("Advisory"))
    assert row.state == "warn", "precondition: the advisory must be an alarm to begin with"

    quiet = build_safety_gpu_vm(diag, silence=SilenceState(kernel_warnings=frozenset({"kw-1"})))
    quiet_row = next(r for r in quiet.gpu_rows if r.label.startswith("Advisory"))
    assert quiet_row.state == "neutral", "the startup dismissal was ignored on this page"
    assert quiet_row.value == row.value, "the advisory text must still be readable"


def test_an_unrelated_dismissal_does_not_silence_the_advisory():
    """The opposite arm — the filter must key on THIS warning's id."""
    from control_ofc.api.models import GpuDiagnosticsInfo, KernelWarning

    diag = HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(chips_detected=[HwmonChipInfo(chip_name="it8696")]),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
        gpu=GpuDiagnosticsInfo(
            model_name="RX 7900 XTX",
            fan_control_method="pmfw_curve",
            kernel_warnings=[KernelWarning(id="kw-1", severity="high", message="SMU regression")],
        ),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    vm = build_safety_gpu_vm(diag, silence=SilenceState(kernel_warnings=frozenset({"kw-OTHER"})))
    row = next(r for r in vm.gpu_rows if r.label.startswith("Advisory"))
    assert row.state == "warn"


def test_the_collision_fingerprint_reads_the_fields_that_exist():
    """A different pair of colliding modules is a different occurrence.

    The first draft read `driver_a`/`driver_b` through `getattr(..., "")`.
    Those attributes do not exist on `ModuleCollisionInfo` — and `getattr` with
    a default does not raise, so every collision fingerprinted identically and a
    dismissal taken on one pair would have silenced a completely different pair.
    Asserted as "these two differ" rather than against a literal digest, so it
    still holds if the hash or the separator changes.
    """
    from control_ofc.api.models import ModuleCollisionInfo

    def _with(a, b):
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="nct6798")],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor="Micro-Star International Co., Ltd.", name="MAG B450"),
            module_collisions=[ModuleCollisionInfo(module_a=a, module_b=b)],
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            cpu_vendor="AMD",
        )

    one = condition_fingerprint(_with("nct6775", "nct6687"), "module_collision")
    two = condition_fingerprint(_with("it87", "it87_dkms"), "module_collision")
    assert one and two, "a collision must produce a non-empty fingerprint"
    assert one != two, "two different collisions fingerprinted identically"

    token = _condition(_with("nct6775", "nct6687"), "module_collision").silence.token
    silenced = SilenceState(dismissed=frozenset({token}))
    assert "module_collision" not in {
        c.key for c in build_condition_cards(_with("nct6775", "nct6687"), silence=silenced).cards
    }
    assert "module_collision" in {
        c.key for c in build_condition_cards(_with("it87", "it87_dkms"), silence=silenced).cards
    }, "a dismissal on one module pair silenced a different pair"


# ── The call sites: a real click, through the real signal, on each surface ──
#
# `CLAUDE.md`'s most-repeated lesson, and the reviewer's P2: every assertion
# above this line calls a view-model builder, which proves the RULE and says
# nothing about the WIRING. This change connected new signals on two cards and
# passed two bound emitters into a third builder; those connections are the part
# most likely to be broken, and nothing was exercising them.


def _interference_diag(count=4):
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="it8696", expected_driver="it87")],
            total_headers=8,
            writable_headers=8,
            enable_revert_counts={"pwm1": count},
        ),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E AORUS MASTER"),
        thermal_safety=ThermalSafetyInfo(
            state="normal", cpu_sensor_found=True, emergency_threshold_c=110.0
        ),
        cpu_vendor="AMD",
    )


def test_clicking_dismiss_on_the_interference_monitor_persists_it(qtbot):
    """InterferenceCard.note_dismissed → page handler → settings → re-render."""
    page, svc = _page(qtbot, _interference_diag())
    btn = page.findChild(QPushButton, "SystemState_InterferenceDismissBtn_monitor")
    assert btn is not None, "the Interference Monitor offered no Dismiss button"

    btn.click()
    assert svc.settings.dismissed_health_items, "the click never reached the page handler"
    assert page._interference_card is not None
    _flush(page)

    # The reading survives; only the alarm stops. Asserted on the re-rendered
    # view-model rather than on the click's return value.
    vm = build_interference_vm(
        _interference_diag(),
        silence=SilenceState(dismissed=frozenset(svc.settings.dismissed_health_items)),
    )
    assert vm.severity_state == "neutral"
    assert vm.highest_count == 4


def test_clicking_dismiss_on_the_thermal_row_persists_it(qtbot):
    """SafetyCard.note_dismissed → page handler → settings."""
    page, svc = _page(qtbot, _interference_diag())
    btn = page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu")
    assert btn is not None, "the thermal row offered no Dismiss button"
    btn.click()
    assert any(t.startswith("thermal@") for t in svc.settings.dismissed_health_items), (
        "the thermal row's click never reached the page handler"
    )
    assert page._safety_card is not None
    _flush(page)


def test_the_thermal_row_offers_no_dismiss_while_the_alarm_is_firing(qtbot):
    """You cannot permanently mute an alarm while it is firing.

    A dismissal is stored at the level it was taken and this row has a fixed
    empty fingerprint, so one taken at `crit` would satisfy every future state —
    permanently, across restarts. Both arms: the button is absent while critical
    and present when it is not, or the guard has simply deleted the feature.
    """
    page, _svc = _page(qtbot, _interference_diag())
    assert page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu") is not None, (
        "precondition: Dismiss must be offered while the row is calm"
    )

    page.set_thermal_state("emergency")
    # The old row was removed with deleteLater(); without dispatching those,
    # findChild returns the corpse and this assertion is about a dead widget.
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu") is None, (
        "a firing thermal alarm could be muted permanently"
    )
    # Acknowledge stays — session-only, which is what ISA-18.2 says an active
    # alarm should accept. Without this arm the guard could have deleted the
    # whole affordance rather than just the permanent half.
    assert page.findChild(QPushButton, "SystemState_ThermalAckBtn_cpu") is not None

    page.set_thermal_state("normal")
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu") is not None, (
        "Dismiss must come back once the alarm clears"
    )


def test_clicking_dismiss_on_a_condition_card_removes_it_and_keeps_the_count(qtbot):
    """The condition card's new action row, driven through the real button."""
    from PySide6.QtWidgets import QLabel

    diag = _msi_collision()
    page, svc = _page(qtbot, diag)
    pill_before = page.findChild(StatusPill, "SystemState_Pill_issueCount").text()
    btn = page.findChild(QPushButton, "SystemState_IssueCardDismissBtn_module_collision")
    assert btn is not None, "the condition card offered no Dismiss button"

    btn.click()
    assert svc.settings.dismissed_health_items, "the click never reached the page handler"
    _flush(page)

    assert page.findChild(QPushButton, "SystemState_IssueCardDismissBtn_module_collision") is None
    after = page.findChild(StatusPill, "SystemState_Pill_issueCount")
    assert after.text() == pill_before, "dismissing a condition changed the health count"
    hidden = page.findChild(QLabel, "SystemState_Label_hiddenConditions")
    assert hidden is not None and "1 dismissed" in hidden.text(), (
        "a shorter list and an unchanged pill must be reconcilable by the reader"
    )


def test_acknowledging_a_condition_is_session_only(qtbot):
    """The same wiring, on the Acknowledge half."""
    page, svc = _page(qtbot, _msi_collision())
    btn = page.findChild(QPushButton, "SystemState_IssueCardAckBtn_module_collision")
    assert btn is not None
    btn.click()
    assert page._session_acks, "the acknowledgement did not take effect"
    assert svc.settings.dismissed_health_items == [], "an acknowledgement must not persist"
    _flush(page)


def test_an_unrecognised_stored_level_does_not_silence_anything():
    """Fail loud, not fail open.

    A stored level from another vocabulary — a corrupt token, or one folded in
    from the retired board-note key — used to rank ABOVE every known level and
    therefore silence its item at every severity including `crit`. Measured:
    `state_rank("observed")` is 4 against `state_rank("crit")` of 3.
    """
    diag = _msi_collision()
    card = _condition(diag, "module_collision")
    # Take the REAL token and swap only its level. The first draft built
    # `f"{key}@observed"`, which has an empty fingerprint where the real
    # occurrence has a digest — so it could never match on `(key, fingerprint)`
    # and the test passed with the vocabulary guard deleted. It was proving the
    # fingerprint works, not the guard (`CLAUDE.md`: the arm where the lookup
    # finds nothing returns the pre-fix answer by construction).
    hostile = card.silence.token.rpartition("@")[0] + "@observed"
    assert hostile != card.silence.token
    assert hostile.startswith(card.silence.token.rpartition("@")[0] + "@"), (
        "precondition: the hostile token must share the real occurrence's fingerprint"
    )
    assert state_rank("observed") > state_rank("crit"), "precondition: it really does outrank crit"

    silenced = build_condition_cards(diag, silence=SilenceState(dismissed=frozenset({hostile})))
    assert "module_collision" in {c.key for c in silenced.cards}, (
        "an unrecognised stored level silenced a critical condition"
    )


def test_a_module_conflict_dismissal_survives_an_unrelated_module_loading():
    """The fingerprint is the conflicting PAIR, not every loaded known module.

    `kernel_modules` is the daemon's curated KNOWN_MODULES filtered to loaded —
    Fintek, Winbond, SMSC, three ASUS WMI drivers and more. Fingerprinting all
    of it meant loading any unrelated sensor module resurrected the dismissal,
    and the manual tells users to press *Rescan Hardware* right after doing
    exactly that.
    """
    from control_ofc.api.models import KernelModuleInfo

    def _with(mods):
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="nct6798")],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor="Micro-Star International Co., Ltd.", name="MAG B450"),
            kernel_modules=[KernelModuleInfo(name=m, loaded=True) for m in mods],
            thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
            cpu_vendor="AMD",
        )

    pair = ["nct6775", "nct6687"]
    before = _with(pair)
    assert "module_conflict" in {c.key for c in build_condition_cards(before).cards}, (
        "precondition: the fixture must actually raise the conflict"
    )
    token = _condition(before, "module_conflict").silence.token
    silenced = SilenceState(dismissed=frozenset({token}))

    after = _with([*pair, "asus_wmi_sensors"])  # an unrelated sensor driver loads
    assert "module_conflict" not in {
        c.key for c in build_condition_cards(after, silence=silenced).cards
    }, "an unrelated module loading resurrected the dismissal"

    # The arm that discriminates: a DIFFERENT conflicting pair must still speak.
    other = _with(["it87", "it87_dkms"])
    if "module_conflict" in {c.key for c in build_condition_cards(other).cards}:
        assert "module_conflict" in {
            c.key for c in build_condition_cards(other, silence=silenced).cards
        }, "a dismissal on one pair silenced a different pair"


def test_the_retired_keys_stay_fenced_out_of_an_imported_settings_file():
    """`MACHINE_SPECIFIC_KEYS` has TWO consumers, and only one was considered.

    It filters what LEAVES (`portable_dict`) and what is allowed IN
    (`SettingsPage._import_settings`). Dropping the two retired names cost
    nothing on the export side — they are no longer fields — but opened the
    import side, because `from_dict` still gives them a live effect by folding
    them into `dismissed_health_items`. A foreign file could then hide health
    conditions on hardware that had never been reviewed.
    """
    from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS, AppSettings

    for retired in ("acknowledged_board_notes", "dismissed_board_notes"):
        assert retired in MACHINE_SPECIFIC_KEYS, f"{retired} is no longer fenced from imports"
    assert "dismissed_health_items" in MACHINE_SPECIFIC_KEYS

    # The import filter, reproduced exactly as `settings_page` applies it.
    incoming = {
        "dismissed_board_notes": ["all_readonly@observed"],
        "acknowledged_board_notes": ["module_collision@observed"],
        "dismissed_health_items": ["acpi#deadbeef@warn"],
        "card_size": "compact",  # a portable key, to prove the filter still lets one through
    }
    merged = {k: v for k, v in incoming.items() if k not in MACHINE_SPECIFIC_KEYS}
    assert merged == {"card_size": "compact"}, "a foreign file could seed a local silence"

    # And the fold itself still works for a LOCAL file, which is the whole point
    # of keeping `from_dict`'s migration.
    local = AppSettings.from_dict({"dismissed_board_notes": ["all_readonly@observed"]})
    assert local.dismissed_health_items == ["all_readonly@observed"]


# ── DEC-360 Phase 3: a reclaim the watchdog fixed hours ago is history ────


def _reclaim_diag(count=3, age_ms=None):
    """A board with a counted BIOS reclaim, optionally dated."""
    hw = HwmonDiagnostics(
        chips_detected=[HwmonChipInfo(chip_name="it8696", expected_driver="it87")],
        total_headers=8,
        writable_headers=8,
        enable_revert_counts={"pwm1": count},
    )
    if age_ms is not None:
        hw.enable_revert_last_seen_ms = {"pwm1": age_ms}
    return HardwareDiagnosticsResult(
        hwmon=hw,
        board=BoardInfo(vendor=_GIGABYTE, name="X870E AORUS MASTER"),
        thermal_safety=ThermalSafetyInfo(
            state="normal", cpu_sensor_found=True, emergency_threshold_c=110.0
        ),
        cpu_vendor="AMD",
    )


def test_a_recent_reclaim_is_still_a_condition():
    """The arm that discriminates. Without it, "historic" is indistinguishable
    from having deleted the condition outright."""
    recent = _reclaim_diag(age_ms=60_000)  # a minute ago
    assert "bios_revert" in {c.key for c in build_condition_cards(recent).cards}
    assert build_interference_vm(recent).severity_state != "neutral"


def test_a_reclaim_nothing_has_repeated_stands_down():
    """The defect: ONE reclaim pinned ACTION REQUIRED for the daemon's uptime.

    `enable_revert_counts` is monotonic with no reset path, so the count alone
    could never clear — `_base_conditions`' own docstring promised "fix it in
    BIOS, refetch, and it is gone", which was false for this entry.
    """
    old = _reclaim_diag(age_ms=RECLAIM_HISTORIC_AFTER_MS + 1)
    assert "bios_revert" not in {c.key for c in build_condition_cards(old).cards}

    # Dated, not discarded: the monitor keeps the count and says what it is.
    vm = build_interference_vm(old)
    assert vm.has_contention is True
    assert vm.highest_count == 3, "the evidence must survive standing down"
    assert vm.severity_state == "neutral"
    assert vm.title == "Past Interference"


def test_an_unknown_reclaim_age_does_not_suppress_the_condition():
    """An older daemon omits the field. Absence of a measurement is not
    evidence of age, and the safe direction for a warning is to keep it."""
    undated = _reclaim_diag(age_ms=None)
    assert undated.hwmon.enable_revert_last_seen_ms == {}
    assert "bios_revert" in {c.key for c in build_condition_cards(undated).cards}
    assert build_interference_vm(undated).severity_state != "neutral"


def test_one_active_header_keeps_the_condition_up_for_all_of_them():
    """`all`, not `any`: a single header still being fought over is active,
    however quiet the rest have gone."""
    hw = HwmonDiagnostics(
        chips_detected=[HwmonChipInfo(chip_name="it8696")],
        total_headers=8,
        writable_headers=8,
        enable_revert_counts={"pwm1": 5, "pwm2": 2},
    )
    hw.enable_revert_last_seen_ms = {"pwm1": RECLAIM_HISTORIC_AFTER_MS * 10, "pwm2": 5_000}
    diag = HardwareDiagnosticsResult(
        hwmon=hw,
        board=BoardInfo(vendor=_GIGABYTE, name="X870E"),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    assert "bios_revert" in {c.key for c in build_condition_cards(diag).cards}


def test_the_historic_wording_interpolates_the_reported_age():
    """DEC-292: a threshold spelled into a string drifts the moment it moves."""
    vm = build_interference_vm(_reclaim_diag(age_ms=5 * 3_600_000))
    assert "5 hour" in vm.explanation
    vm2 = build_interference_vm(_reclaim_diag(age_ms=2 * 3_600_000))
    assert "2 hour" in vm2.explanation
    assert vm.explanation != vm2.explanation, "the figure must come from the wire, not a literal"


def test_the_wire_field_defaults_empty_on_an_older_daemon():
    """One wire field, one gating shape (DEC-334)."""
    from control_ofc.api.models import parse_hardware_diagnostics

    older = parse_hardware_diagnostics({"hwmon": {"enable_revert_counts": {"pwm1": 2}}})
    assert older.hwmon.enable_revert_counts == {"pwm1": 2}
    assert older.hwmon.enable_revert_last_seen_ms == {}, "an older daemon must not imply an age"

    # And the opposite arm — the field is genuinely read when the daemon sends it.
    newer = parse_hardware_diagnostics(
        {
            "hwmon": {
                "enable_revert_counts": {"pwm1": 2},
                "enable_revert_last_seen_ms": {"pwm1": 7_200_000},
            }
        }
    )
    assert newer.hwmon.enable_revert_last_seen_ms == {"pwm1": 7_200_000}


def test_a_dismissal_expires_when_the_reclaim_episode_ends(qtbot):
    """Dismiss an active reclaim, let it go historic, and the silence goes too.

    Raised by `ofc:contract-reviewer` as a defect and **refuted by measurement**:
    DEC-360 makes the condition vanish and later return, which before this change
    was structurally impossible, so a stale dismissal could in principle cover a
    genuinely new BIOS fight at the same severity bucket. It does not, because
    Phase 2's pruning removes a silence whose key the hardware is no longer
    raising — ISA-18.2's RECOVERED semantics, arrived at from the other
    direction. This test exists so that stays true: it is the only thing
    connecting the two halves.
    """
    page, svc = _page(qtbot, _reclaim_diag(age_ms=60_000))
    btn = page.findChild(QPushButton, "SystemState_IssueCardDismissBtn_bios_revert")
    assert btn is not None, "precondition: an active reclaim must be dismissable"
    btn.click()
    assert svc.settings.dismissed_health_items, "precondition: the dismissal was stored"
    _flush(page)

    # The episode ends. Rendering while historic prunes the now-dead silence.
    page._render(_reclaim_diag(age_ms=RECLAIM_HISTORIC_AFTER_MS * 5))
    assert svc.settings.dismissed_health_items == [], (
        "the silence outlived the occurrence it was taken against"
    )

    # A NEW fight at the same severity bucket must speak again.
    assert "bios_revert" in {
        c.key
        for c in build_condition_cards(
            _reclaim_diag(age_ms=30_000),
            silence=SilenceState(dismissed=frozenset(svc.settings.dismissed_health_items)),
        ).cards
    }
