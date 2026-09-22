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

from dataclasses import replace

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from control_ofc.api.models import (
    BoardInfo,
    DaemonStatus,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ThermalSafetyInfo,
)
from control_ofc.services.duty_drift import NO_DRIFT
from control_ofc.services.health_ack import (
    SILENCE_CAP,
    Occurrence,
    build_index,
    clear_key,
    fingerprint,
    is_silenced,
    occurrence_token,
    parse_token,
    prune,
)
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
from control_ofc.ui.hwmon_guidance import (
    AMD_GPU_GUIDANCE_DB,
    VENDOR_QUIRKS_DB,
    quirk_key,
)
from control_ofc.ui.theme import active_theme
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
        for p in build_system_state_vm(
            _healthy_gigabyte(), pwm_control_verified=True, duty_drift=NO_DRIFT
        ).issue_cards
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

    loud = build_system_state_vm(diag, duty_drift=NO_DRIFT)
    quiet = build_system_state_vm(diag, dismissed_notes=dismissed, duty_drift=NO_DRIFT)
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

    `_render` calls `_populate_verify_combo`, which syncs `_verify_btn`'s
    enabled state. Before DEC-358 every re-render followed a user action; the
    live thermal push can land at any instant, including inside a sweep.

    **The stakes have since dropped and the guard has not.** When this was
    written, `_on_verify_ok` appended *any* result into `_verify_all_results`
    and stepped the queue whenever a sweep was open, so a second concurrent
    verify popped a header the sweep never reported and corrupted the verdict
    the change persists. DEC-364 made attribution provenance-based, so a foreign
    result is now shown and then ignored; DEC-377 closed the second route to one
    (the handlers' own unconditional re-enable). What this still pins is that a
    background re-render cannot offer the user a hardware write the page has
    deliberately withdrawn — which is now the whole of the claim, not a
    convenience on top of a correctness dependency.
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
    return next(c for c in build_condition_cards(diag, duty_drift=NO_DRIFT).cards if c.key == key)


def test_a_condition_can_be_dismissed_and_the_pill_still_counts_it():
    """The user's limit, pinned: a health number is never quietened by a button.

    Both halves are the assertion. The card must leave the list (that is the
    request) **and** the count must not move (that is the safety limit), so a
    page can never read SYSTEM READY while fan control is actually broken.
    """
    diag = _msi_collision()
    card = _condition(diag, "module_collision")
    assert card.silence.token, "precondition: the condition must be silenceable"

    loud = build_system_state_vm(diag, duty_drift=NO_DRIFT)
    quiet = build_system_state_vm(
        diag, silence=SilenceState(dismissed=frozenset({card.silence.token})), duty_drift=NO_DRIFT
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
    assert "acpi" not in {
        c.key for c in build_condition_cards(one, silence=silenced, duty_drift=NO_DRIFT).cards
    }
    assert "acpi" in {
        c.key for c in build_condition_cards(two, silence=silenced, duty_drift=NO_DRIFT).cards
    }, "a condition whose evidence changed must speak again"


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

    def keys(d):
        return {
            c.key for c in build_condition_cards(d, silence=silenced, duty_drift=NO_DRIFT).cards
        }

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
        c.key
        for c in build_condition_cards(
            _with("nct6775", "nct6687"), silence=silenced, duty_drift=NO_DRIFT
        ).cards
    }
    assert "module_collision" in {
        c.key
        for c in build_condition_cards(
            _with("it87", "it87_dkms"), silence=silenced, duty_drift=NO_DRIFT
        ).cards
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

    silenced = build_condition_cards(
        diag, silence=SilenceState(dismissed=frozenset({hostile})), duty_drift=NO_DRIFT
    )
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
    assert "module_conflict" in {
        c.key for c in build_condition_cards(before, duty_drift=NO_DRIFT).cards
    }, "precondition: the fixture must actually raise the conflict"
    token = _condition(before, "module_conflict").silence.token
    silenced = SilenceState(dismissed=frozenset({token}))

    after = _with([*pair, "asus_wmi_sensors"])  # an unrelated sensor driver loads
    assert "module_conflict" not in {
        c.key for c in build_condition_cards(after, silence=silenced, duty_drift=NO_DRIFT).cards
    }, "an unrelated module loading resurrected the dismissal"

    # The arm that discriminates: a DIFFERENT conflicting pair must still speak.
    other = _with(["it87", "it87_dkms"])
    if "module_conflict" in {
        c.key for c in build_condition_cards(other, duty_drift=NO_DRIFT).cards
    }:
        assert "module_conflict" in {
            c.key for c in build_condition_cards(other, silence=silenced, duty_drift=NO_DRIFT).cards
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
    assert "bios_revert" in {
        c.key for c in build_condition_cards(recent, duty_drift=NO_DRIFT).cards
    }
    assert build_interference_vm(recent).severity_state != "neutral"


def test_a_reclaim_nothing_has_repeated_stands_down():
    """The defect: ONE reclaim pinned ACTION REQUIRED for the daemon's uptime.

    `enable_revert_counts` is monotonic with no reset path, so the count alone
    could never clear — `_base_conditions`' own docstring promised "fix it in
    BIOS, refetch, and it is gone", which was false for this entry.
    """
    old = _reclaim_diag(age_ms=RECLAIM_HISTORIC_AFTER_MS + 1)
    assert "bios_revert" not in {
        c.key for c in build_condition_cards(old, duty_drift=NO_DRIFT).cards
    }

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
    assert "bios_revert" in {
        c.key for c in build_condition_cards(undated, duty_drift=NO_DRIFT).cards
    }
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
    assert "bios_revert" in {c.key for c in build_condition_cards(diag, duty_drift=NO_DRIFT).cards}


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
            duty_drift=NO_DRIFT,
        ).cards
    }


# ── `ACK-r`: Acknowledge must DEMOTE a condition, not just relabel a button ──


def test_acknowledging_a_condition_demotes_it_in_the_view_model():
    """Both arms, and both against the silence rather than against a literal.

    The pre-fix code computed `SilenceVM.acknowledged` for every card and then
    only ever wrote it into `silence`, so `severity_state` stayed exactly as
    loud as before — the crit bracket, the glyph colour and the severity word
    all unchanged. A test asserting `severity_state == "neutral"` would be
    satisfied by a card that was neutral for some other reason, so the
    assertion is the RELATIONSHIP `neutral iff quiet` (DEC-324), with the loud
    arm carried alongside to stop a stuck predicate passing.
    """
    diag = _msi_collision()
    loud = _condition(diag, "module_collision")
    assert loud.silence.token, "precondition: the condition must be silenceable"
    assert not loud.silence.quiet, "precondition: it starts un-silenced"
    assert loud.severity_state != "neutral", (
        "precondition: the un-acknowledged card must have something to demote"
    )

    quiet = next(
        c
        for c in build_condition_cards(
            diag,
            silence=SilenceState(acknowledged=frozenset({loud.silence.token})),
            duty_drift=NO_DRIFT,
        ).cards
        if c.key == "module_collision"
    )
    assert quiet.silence.quiet, "precondition: the acknowledgement was applied"
    assert quiet.severity_state == "neutral"
    # The raw severity is untouched, so the card keeps its place in the sort
    # and does not jump under the reader — and the pill above still counts it.
    assert quiet.severity == loud.severity
    assert quiet.severity_word == loud.severity_word


def test_the_acknowledged_condition_card_says_so_on_screen(qtbot):
    """The rendered card, not the VM — the defect was that nothing changed.

    `isVisibleTo(parent)`, never `isVisible()`: under `offscreen` nothing is
    shown, so `isVisible()` is False for every widget and the assertion would
    pass with the whole pill deleted (`CLAUDE.md`, DEC-324).
    """
    from control_ofc.ui.components.cards import BracketCard

    page, _svc = _page(qtbot, _msi_collision())
    key = "module_collision"
    card = page.findChild(BracketCard, f"SystemState_IssueCard_{key}")
    title = page.findChild(QLabel, f"SystemState_IssueTitle_{key}")
    assert card is not None and title is not None
    loud_state = card.property("state")
    loud_style = title.styleSheet()
    assert page.findChild(StatusPill, f"SystemState_IssueAck_{key}") is None, (
        "precondition: an un-acknowledged card carries no Acknowledged marker"
    )

    page.findChild(QPushButton, f"SystemState_IssueCardAckBtn_{key}").click()
    _flush(page)

    acked_card = page.findChild(BracketCard, f"SystemState_IssueCard_{key}")
    acked_title = page.findChild(QLabel, f"SystemState_IssueTitle_{key}")
    marker = page.findChild(StatusPill, f"SystemState_IssueAck_{key}")
    assert marker is not None, "nothing on screen says the card was acknowledged"
    assert marker.isVisibleTo(page)
    assert acked_card.property("state") != loud_state, "the bracket stayed as loud as before"
    assert acked_card.property("state") == "neutral"
    assert acked_title.styleSheet() != loud_style, "the title was not demoted"
    assert active_theme().text_muted in acked_title.styleSheet()
    _flush(page)


# ── `ACK-s`: the guard that refuses to prune off an incomplete snapshot ──


def _no_chips_diag():
    """A valid 200 that enumerated nothing — e.g. a daemon restart mid-session.

    Not an error payload: `/diagnostics/hardware` returns 200 with an empty
    `chips_detected` when no hwmon chip enumerated, and the GUI models that as
    its own `no_chips` condition.
    """
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(chips_detected=[]),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E AORUS MASTER"),
        thermal_safety=ThermalSafetyInfo(
            state="normal", cpu_sensor_found=True, emergency_threshold_c=110.0
        ),
        cpu_vendor="AMD",
    )


def test_an_empty_chip_snapshot_must_not_delete_the_users_dismissals(qtbot):
    """`ACK-s`: pruning is an optimisation; losing a preference is not.

    Two-armed, and the second arm is the one that makes the first mean
    something: an assertion that *nothing was pruned* is satisfied just as well
    by pruning being dead altogether, which is exactly the state this test was
    written to escape — the guard had no test, and deleting its two lines left
    the whole suite green.
    """
    page, svc = _page(qtbot, _reclaim_diag(age_ms=60_000))
    btn = page.findChild(QPushButton, "SystemState_IssueCardDismissBtn_bios_revert")
    assert btn is not None, "precondition: an active reclaim must be dismissable"
    btn.click()
    stored = list(svc.settings.dismissed_health_items)
    assert stored, "precondition: the dismissal was stored"

    # Arm 1 — the guard. The reclaim key vanishes from the live set along with
    # every other chip-scoped key, and `_prune_silences` PERSISTS its result, so
    # without the guard this render deletes the dismissal irreversibly.
    page._render(_no_chips_diag())
    assert svc.settings.dismissed_health_items == stored, (
        "an empty-but-valid hardware snapshot deleted a persisted dismissal"
    )

    # Arm 2 — the discriminator. A COMPLETE snapshot that no longer raises the
    # key still prunes, so arm 1 is the guard firing rather than pruning being
    # broken or unreachable.
    page._render(_reclaim_diag(age_ms=RECLAIM_HISTORIC_AFTER_MS * 5))
    assert svc.settings.dismissed_health_items == [], (
        "pruning never resumed once the snapshot was complete again"
    )
    _flush(page)


def test_prune_caps_by_keeping_the_most_recent_silences():
    """`ACK-s`: assert the WINDOW the cap keeps, not merely the length.

    A length-only assertion passes with `kept[:cap]`, which keeps the oldest
    entries and drops the newest — the worse of the two failures, since the
    newest silence is the one the user just took and the only one they would
    notice going missing.
    """
    over = [
        occurrence_token(Occurrence(key="k", fingerprint=f"{i:012x}", level="warn"))
        for i in range(SILENCE_CAP + 17)
    ]
    kept = prune(over, {"k"})
    assert len(kept) == SILENCE_CAP
    assert kept == over[-SILENCE_CAP:], "the cap dropped the newest entries, not the oldest"

    # The opposite arm: below the cap nothing is dropped, so the slice above is
    # the cap firing and not a truncation that runs unconditionally.
    under = over[: SILENCE_CAP - 1]
    assert prune(under, {"k"}) == under


# ── `ACK-u`: no persisted key may contain the fingerprint separator ──


def _data_driven_silence_keys():
    """Every silenceable key that comes from a DATA TABLE rather than a literal.

    Scope, stated because it is narrower than "every key": these are the sites
    where a new key is added by appending a row, touching no parsing code at
    all, which is the way the constraint gets broken by accident. The
    hand-written condition literals in `readiness_report.py` are not swept —
    there is no registry to sweep them from, and each sits beside the code that
    raises it. `AMD_GPU_GUIDANCE_DB` is the in-repo mirror of the daemon's
    `KernelWarning.id` vocabulary, so it is the enumerable half of that one.
    """
    keys: list[str] = []
    for quirk in VENDOR_QUIRKS_DB:
        keys.append(quirk_key(quirk))  # the board-note occurrence
        keys.append(f"quirk_{quirk_key(quirk)}")  # the condition promoted from it
    keys += [f"gpu_advisory_{g.warning_id}" for g in AMD_GPU_GUIDANCE_DB]
    keys += ["interference", "thermal"]  # the two fixed keys built in view code
    # `ACK-w`: harvested from the builder rather than listed, so they cannot
    # drift, and because one of them is the only key in the codebase that
    # embeds wire-supplied text — a PCI BDF, hence the only key containing `:`
    # and `.`. A literal list here would have been written from the same mental
    # model as the keys themselves and would have missed exactly that one.
    for _, _diag in _alarm_scenarios():
        keys += [r.key for r in build_safety_gpu_vm(_diag).gpu_rows if r.key]
    return keys


def test_every_data_driven_key_survives_being_stored_as_a_silence():
    """`ACK-u`: a key containing `#` is silently truncated when it is read back.

    Asserted as a round trip rather than as a character-class check, because the
    round trip is the property that actually matters and it stays true if the
    token grammar ever moves. Both fingerprint shapes, because they leave
    `parse_token` by different branches — a board note stores `fingerprint=""`
    and emits no `#` at all, a condition stores a digest and does.

    The failure this prevents is silent, which is why it is worth a test over an
    inert constraint: a board note's occurrence carries `fingerprint=""`, so a
    `#` in its key means it can never match its own stored token — Dismiss would
    appear to do nothing — while `prune`, keyed on the truncated half, would
    delete the entry on the next render.
    """
    assert VENDOR_QUIRKS_DB and AMD_GPU_GUIDANCE_DB, "precondition: the id tables are populated"
    keys = _data_driven_silence_keys()

    # The precondition names each of the four contributions rather than bounding
    # the total, because a lower bound cannot discriminate one going missing:
    # the sweep yields ~76 keys against a bound of 38, so deleting the whole
    # `quirk_`-prefixed vocabulary would still satisfy it and the round trip
    # below would simply iterate fewer keys, all passing. Samples are read from
    # the tables at runtime, so this stays a relationship rather than a literal.
    a_quirk = quirk_key(VENDOR_QUIRKS_DB[0])
    an_advisory = AMD_GPU_GUIDANCE_DB[0].warning_id
    for expected in (
        a_quirk,  # the board-note occurrence
        f"quirk_{a_quirk}",  # the condition promoted from it
        f"gpu_advisory_{an_advisory}",  # the GPU advisory row
        "interference",
        "thermal",
        "gpu_row_amd_device_0000:03:00.0",  # the only key carrying a PCI BDF
        "gpu_row_fan_control",
    ):
        assert expected in keys, f"the sweep lost the vocabulary {expected!r} belongs to"

    digest = fingerprint(["evidence"])
    assert digest and len(digest) == 12, "precondition: a real digest, not the empty one"

    for key in keys:
        for shape in ("", digest):
            occ = Occurrence(key=key, fingerprint=shape, level="warn")
            assert parse_token(occurrence_token(occ)) == occ, (
                f"{key!r} does not survive a store/read round trip"
            )


# ---------------------------------------------------------------------------
# `ACK-w` — every GPU constraint row that raises an alarm can be silenced
# ---------------------------------------------------------------------------


def _gpu_diag(**gpu_kw) -> HardwareDiagnosticsResult:
    """`_healthy_gigabyte` with a GPU whose constraint rows the caller picks."""
    from control_ofc.api.models import GpuDiagnosticsInfo

    diag = _healthy_gigabyte()
    devices = gpu_kw.pop("amd_pci_devices", [])
    module_loaded = gpu_kw.pop("amdgpu_module_loaded", True)
    return replace(
        diag,
        gpu=GpuDiagnosticsInfo(**{"model_name": "RX 7900 XTX", **gpu_kw}),
        amd_pci_devices=devices,
        amdgpu_module_loaded=module_loaded,
    )


def _alarm_scenarios() -> list[tuple[str, HardwareDiagnosticsResult]]:
    """Every shape that can paint a `warn` row, as a registry.

    Two diags rather than one because the two `ppfeaturemask` branches are
    mutually exclusive — `gpu.ppfeaturemask` being truthy is what chooses
    between them, so no single machine can show both.
    """
    from control_ofc.api.models import AmdPciDeviceInfo

    return [
        (
            "read_only, nothing on the kernel command line",
            _gpu_diag(
                fan_control_method="read_only",
                overdrive_enabled=False,
                ppfeaturemask=None,
                amdgpu_driver_bound=False,
                amd_pci_devices=[
                    AmdPciDeviceInfo(pci_bdf="0000:03:00.0", driver="vfio-pci", amdgpu_bound=False)
                ],
            ),
        ),
        (
            "ppfeaturemask set but bit 14 clear",
            _gpu_diag(
                fan_control_method="none",
                overdrive_enabled=False,
                ppfeaturemask="0xfff7ffff",
                ppfeaturemask_bit14_set=False,
            ),
        ),
    ]


def test_every_alarm_raising_gpu_row_is_silenceable():
    """`ACK-w`, the registry sweep — classify by default, not by opting in.

    DEC-367's lesson in the shape that produced this defect: the enumeration
    that recorded it was derived by listing the `silence=` sites, which is the
    *silenceable* side, so it could never discover an alarm row that had no
    `silence=` to list. It found three of six. This asserts the property from
    the other direction — every rendered row that alarms must carry a token —
    which is the only direction a seventh row can fail.
    """
    seen: set[str] = set()
    for name, diag in _alarm_scenarios():
        rows = build_safety_gpu_vm(diag).gpu_rows
        alarms = [r for r in rows if r.state in ("warn", "crit")]
        assert alarms, f"precondition: {name} painted no alarm row at all"
        for r in alarms:
            assert r.silence.token, f"{name}: {r.label!r} alarms with no way to silence it"
            assert r.silence.can_acknowledge and r.silence.can_dismiss
            seen.add(parse_token(r.silence.token).key)

    assert seen == {
        "gpu_row_fan_control",
        "gpu_row_overdrive",
        "gpu_row_ppfeaturemask",
        "gpu_row_amdgpu_binding",
        "gpu_row_amd_device_0000:03:00.0",
    }, "the six alarm-raising construction sites, by the five keys they use"


def test_the_builder_constructs_no_gpu_row_directly():
    """The bypass guard: a new row must go through the silencer.

    The `key` argument has no default, so a call that forgets one is a
    `TypeError` — but that only binds a call site that uses the silencer at
    all. This is what stops the next row being appended the old way, which is
    exactly how all six of these came to exist.

    Matched against `inspect.getsource` of the builder alone, not the module:
    `_GpuRowSilencer` constructs rows legitimately and must not trip it. The
    bare annotation `rows: list[GpuConstraintRowVM] = []` has no `(` after the
    name, so it does not match either.
    """
    import inspect

    from control_ofc.services import system_state_view

    body = inspect.getsource(system_state_view.build_safety_gpu_vm)
    assert "GpuConstraintRowVM(" not in body, (
        "build_safety_gpu_vm builds a row directly — route it through `silencer`"
    )
    assert body.count("silencer.constraint(") + body.count("silencer.advisory(") == 11, (
        "a row was added or removed; confirm it picked a key and update this count"
    )


def test_silencing_one_gpu_row_leaves_its_neighbours_alone():
    """Both arms, per row identity — a silence is keyed on the item.

    The right-hand side is the *other* rows rather than a literal state, so a
    fix that silenced everything at once would fail here even though the row
    under test looked correct.
    """
    _, diag = _alarm_scenarios()[0]
    loud = build_safety_gpu_vm(diag)
    target = next(r for r in loud.gpu_rows if r.label == "Fan Control")
    assert target.state == "warn", "precondition: read_only must alarm to begin with"

    quiet = build_safety_gpu_vm(
        diag, silence=SilenceState(dismissed=frozenset({target.silence.token}))
    )
    by_label = {r.label: r for r in quiet.gpu_rows}
    assert by_label["Fan Control"].state == "neutral"
    assert by_label["Fan Control"].value == target.value, "demote, never delete"
    assert by_label["Fan Control"].silence.dismissed
    assert by_label["Overdrive"].state == "warn", "a neighbour must not be silenced with it"
    assert by_label["ppfeaturemask"].state == "warn"
    assert by_label["amdgpu binding"].state == "warn"


def test_a_quiet_gpu_row_keeps_its_token_so_the_prune_cannot_eat_the_silence():
    """The demote-order trap, asserted rather than trusted.

    Neutralising `state` before building the occurrence renders identically and
    is wrong twice: the row loses its Unacknowledge button, and
    `_live_silence_keys` harvests keys from *rendered* tokens — so the next
    prune would delete the dismissal the user had just taken.
    """
    _, diag = _alarm_scenarios()[0]
    target = next(r for r in build_safety_gpu_vm(diag).gpu_rows if r.label == "Overdrive")

    # Asserted on the LOUD render, before any silencing is in play. The level
    # the builder publishes is observable on its own, so this fails where the
    # order is wrong rather than at a precondition further down — which is the
    # difference between a maintainer reading "the token carries the demoted
    # level" and reading "the row was not quiet", a true statement that points
    # at the harness.
    assert target.state == "warn", "precondition: the row must alarm to begin with"
    assert parse_token(target.silence.token).level == "warn", (
        "the token must carry the row's REAL level, not a demoted one"
    )

    quiet = build_safety_gpu_vm(
        diag, silence=SilenceState(dismissed=frozenset({target.silence.token}))
    )
    row = next(r for r in quiet.gpu_rows if r.label == "Overdrive")
    assert row.state == "neutral", "the dismissal must actually take effect"
    assert row.silence.token == target.silence.token, (
        "a quiet row must still report the token that silenced it, or the next "
        "prune deletes the dismissal the user just took"
    )


def test_a_gpu_row_speaks_again_when_its_reading_changes():
    """Fingerprint-on-value: a different readout is a different occurrence.

    This is also why these rows need no `crit`-withholding of Dismiss the way
    the thermal row does. The thermal row's fingerprint is fixed empty, so a
    dismissal taken at the top covers every future state forever; here the
    state is a pure function of the value the fingerprint is taken from, so
    anything that could escalate the row voids the silence by construction.
    """
    diag_none = _gpu_diag(fan_control_method="none")
    token = next(
        r for r in build_safety_gpu_vm(diag_none).gpu_rows if r.label == "Fan Control"
    ).silence.token
    silence = SilenceState(dismissed=frozenset({token}))

    still_none = next(
        r
        for r in build_safety_gpu_vm(diag_none, silence=silence).gpu_rows
        if r.label == "Fan Control"
    )
    assert still_none.state == "neutral", "precondition: the silence must hold on its own reading"

    changed = next(
        r
        for r in build_safety_gpu_vm(
            _gpu_diag(fan_control_method="read_only"), silence=silence
        ).gpu_rows
        if r.label == "Fan Control"
    )
    assert changed.state == "warn", "a different reading is a new occurrence and must speak again"


def test_the_kernel_advisory_token_is_unchanged_byte_for_byte():
    """DEC-359's no-migration property — the one thing this change must not move.

    Asserted as a literal, not against `occurrence_token`: re-deriving it here
    would share any defect the builder has, and the whole point is that a
    silence stored by v2.73.0 still matches.
    """
    from control_ofc.api.models import KernelWarning

    diag = _gpu_diag(
        fan_control_method="pmfw_curve",
        kernel_warnings=[KernelWarning(id="kw-1", severity="high", message="SMU regression")],
    )
    row = next(r for r in build_safety_gpu_vm(diag).gpu_rows if r.label.startswith("Advisory"))
    assert row.silence.token == "gpu_advisory_kw-1@warn"


def test_a_neutral_gpu_row_offers_nothing_to_press():
    """The opposite arm of the rule — `Zero-RPM: available` gets no buttons."""
    rows = build_safety_gpu_vm(_gpu_diag(fan_control_method="pmfw_curve")).gpu_rows
    by_label = {r.label: r for r in rows}
    assert by_label["Zero-RPM"].state == "neutral"
    assert by_label["Zero-RPM"].silence.token == ""
    assert by_label["Fan Control"].state == "ok", "precondition: a healthy row, not an alarm"
    assert by_label["Fan Control"].silence.token == "", "an `ok` row has nothing to quieten"


def test_gpu_row_silence_keys_do_not_collide_with_the_condition_cards():
    """The user's decision (2026-09-17): these are two different items.

    A shared key would mean Dismiss on a one-line readout also hiding the
    condition card that carries the fix.
    """
    from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

    _, diag = _alarm_scenarios()[0]
    row_keys = {
        parse_token(r.silence.token).key
        for r in build_safety_gpu_vm(diag).gpu_rows
        if r.silence.token
    }
    condition_keys = {p["key"] for p in detect_readiness_problems(diag, duty_drift=NO_DRIFT)}
    assert row_keys, "precondition: the rows must carry keys at all"
    assert condition_keys & {"gpu_readonly", "gpu_ppfeaturemask"}, (
        "precondition: this fixture must actually raise the GPU conditions"
    )
    assert not (row_keys & condition_keys), "a GPU row must not share a key with a condition card"


def test_the_gpu_row_buttons_are_named_by_key_not_by_row_index(qtbot):
    """`ACK-w`/D — an index shifts when a row above appears or disappears."""
    from control_ofc.ui.widgets.system_state_cards import SafetyCard

    card = SafetyCard()
    qtbot.addWidget(card)

    _, with_binding_row = _alarm_scenarios()[0]
    card.render(build_safety_gpu_vm(with_binding_row))
    named = card.findChild(QPushButton, "SystemState_GpuRowAckBtn_gpu_row_overdrive")
    assert named is not None, "the Acknowledge button must be addressable by the row's key"
    assert card.findChild(QPushButton, "SystemState_GpuRowAckBtn_0") is None

    # The same row, with an earlier row gone: `amdgpu binding` disappears when
    # the driver binds, so every index below it shifts by one. The name must not.
    fewer = replace(with_binding_row, gpu=replace(with_binding_row.gpu, amdgpu_driver_bound=True))
    card.render(build_safety_gpu_vm(fewer))
    assert card.findChild(QPushButton, "SystemState_GpuRowAckBtn_gpu_row_overdrive") is not None


def test_the_settings_toggles_govern_the_new_gpu_rows_too():
    """Settings says these buttons are governed by its two toggles — prove it.

    `settings_page.py` tells the user that "Acknowledge health items" and
    "Dismiss health items" cover the GPU rows, and `manual/settings.md` repeats
    it. That is a claim about wiring, and the wiring is `SilenceState.allow_*`
    reaching `_GpuRowSilencer` — which nothing else in this file exercises for
    a constraint row. Asserted per toggle, and against the row still carrying
    its token either way: turning the buttons off must not un-silence anything.
    """
    _, diag = _alarm_scenarios()[0]

    def overdrive(**kw):
        return next(
            r
            for r in build_safety_gpu_vm(diag, silence=SilenceState(**kw)).gpu_rows
            if r.label == "Overdrive"
        )

    both = overdrive()
    assert both.state == "warn", "precondition: the row must alarm to begin with"
    assert both.silence.can_acknowledge and both.silence.can_dismiss

    no_ack = overdrive(allow_acknowledge=False)
    assert not no_ack.silence.can_acknowledge
    assert no_ack.silence.can_dismiss, "one toggle must not disable the other"

    no_dismiss = overdrive(allow_dismiss=False)
    assert no_dismiss.silence.can_acknowledge
    assert not no_dismiss.silence.can_dismiss
    assert no_dismiss.silence.token == both.silence.token, (
        "withholding the buttons must not change the row's identity"
    )


def test_no_gpu_constraint_row_can_paint_crit_and_state_follows_the_value():
    """The two properties that let these rows skip the thermal row's `crit` guard.

    The thermal row withholds Dismiss while it is critical because its
    fingerprint is fixed empty, so a dismissal taken at the top rank satisfies
    `rank <= stored` for every future state — a permanent mute. A constraint row
    needs no such guard for two reasons, and **the reachability one is the load
    bearing half**: no constraint row can reach `crit` at all, so the dangerous
    case cannot arise. (The `constraint` docstring led with the fingerprint
    argument until this test was written and mutated; that argument is real but
    secondary, because escalation below `crit` is already broken by the rank
    check in `is_silenced` without any help from the fingerprint.)

    Both are asserted over a matrix, so a future row that paints `crit`, or
    derives its state from a second field, fails here and forces the author to
    decide rather than inheriting a guarantee nobody re-checked.

    The purity half collects **every** row, not just the silenceable ones. The
    first draft skipped rows with no token and therefore **passed with
    `Overdrive`'s state re-pointed at `amdgpu_driver_bound`** — the mutation
    turns the bad pairing `ok`, an `ok` row carries no token, and the collector
    threw away the very observation that proved the defect.
    """
    import itertools

    seen: dict[tuple[str, str], set[str]] = {}
    crit_rows: list[str] = []
    rows_seen = 0
    for method, overdrive, mask, bit14, bound in itertools.product(
        ("pmfw_curve", "hwmon_pwm", "read_only", "none", ""),
        (True, False),
        (None, "", "0xfff7ffff"),
        (True, False),
        (True, False),
    ):
        diag = _gpu_diag(
            fan_control_method=method,
            overdrive_enabled=overdrive,
            ppfeaturemask=mask,
            ppfeaturemask_bit14_set=bit14,
            amdgpu_driver_bound=bound,
        )
        for r in build_safety_gpu_vm(diag).gpu_rows:
            rows_seen += 1
            if r.state == "crit":
                crit_rows.append(f"{r.label}: {r.value}")
            seen.setdefault((r.label, r.value), set()).add(r.state)

    assert rows_seen > 400, "precondition: the matrix must actually build rows"
    assert len({label for label, _ in seen}) >= 4, "precondition: several row kinds, not one"
    assert {s for states in seen.values() for s in states} >= {"ok", "warn", "neutral"}, (
        "precondition: the matrix must reach both the alarming and the healthy arms"
    )

    assert not crit_rows, (
        "a constraint row reached `crit`: a dismissal taken there is stored at the top "
        "rank and covers every future state, which is the permanent mute the thermal "
        f"row withholds Dismiss to prevent. Decide before shipping it: {crit_rows}"
    )
    ambiguous = {pair: states for pair, states in seen.items() if len(states) > 1}
    assert not ambiguous, (
        "a row's state must depend only on the value it fingerprints, or an escalation "
        f"can hide inside an existing silence: {ambiguous}"
    )


def test_a_dismissed_gpu_row_survives_the_problem_being_fixed_and_recurring():
    """The resolve -> prune -> recur round trip (`ofc:python-gui-reviewer`, P2).

    `health_ack.is_silenced` promises that a silence survives things improving,
    and `manual/diagnostics.md` promises it in the same words. This change's
    first draft broke that promise for the GPU rows without touching either:
    `_live_silence_keys` read each row's key off its **token**, an `ok` row
    carries no token, so one diagnostics fetch taken while the problem was fixed
    dropped the key from the live set and `prune` deleted the dismissal.

    The middle step is the one that matters and it is asserted directly — that
    `prune` KEEPS the token while the row is healthy. Asserting only the final
    state would pass on a build that never pruned at all, which is not the
    property under test.
    """
    from control_ofc.services.system_state_view import build_system_state_vm
    from control_ofc.ui.pages.system_state_page import _live_silence_keys

    broken = _gpu_diag(fan_control_method="read_only", overdrive_enabled=False)
    fixed = _gpu_diag(fan_control_method="read_only", overdrive_enabled=True)

    def row(diag, **kw):
        return next(
            r
            for r in build_safety_gpu_vm(diag, silence=SilenceState(**kw)).gpu_rows
            if r.label == "Overdrive"
        )

    token = row(broken).silence.token
    assert row(broken).state == "warn", "precondition: the row alarms before the dismissal"
    assert row(broken, dismissed=frozenset({token})).state == "neutral", (
        "precondition: the dismissal takes effect"
    )

    # The page prunes against an UNSILENCED probe, exactly as `_prune_silences`
    # builds it — so this is the real predicate, not a re-derivation of it.
    healthy_probe = build_system_state_vm(fixed, duty_drift=NO_DRIFT)
    assert row(fixed).state == "ok", "precondition: the problem really is fixed"
    assert row(fixed).silence.token == "", "precondition: a healthy row carries no token"
    kept = prune([token], _live_silence_keys(healthy_probe))
    assert kept == [token], (
        "the prune deleted a dismissal while the problem was merely fixed; "
        "a silence must survive things improving"
    )

    # ... and the row is still quiet when the same problem comes back.
    assert row(broken, dismissed=frozenset(kept)).state == "neutral"


def test_a_gpu_row_the_hardware_can_no_longer_raise_is_still_pruned():
    """The opposite arm — `ACK-g` must keep working, or the fix above overshoots.

    `amdgpu binding` disappears from the list when the driver binds, which is a
    genuinely different case from a row that stays visible and goes quiet: the
    item cannot be raised by this hardware at all any more, and keeping its
    dismissal would inflate the Settings restore counter with something that can
    never come back.
    """
    from control_ofc.services.system_state_view import build_system_state_vm
    from control_ofc.ui.pages.system_state_page import _live_silence_keys

    unbound = _gpu_diag(fan_control_method="read_only", amdgpu_driver_bound=False)
    token = next(
        r for r in build_safety_gpu_vm(unbound).gpu_rows if r.label == "amdgpu binding"
    ).silence.token
    assert token, "precondition: the row must be silenceable while it is raised"

    bound = _gpu_diag(fan_control_method="read_only", amdgpu_driver_bound=True)
    assert not any(r.label == "amdgpu binding" for r in build_safety_gpu_vm(bound).gpu_rows), (
        "precondition: the row must genuinely vanish, not merely go quiet"
    )
    assert (
        prune([token], _live_silence_keys(build_system_state_vm(bound, duty_drift=NO_DRIFT))) == []
    )
