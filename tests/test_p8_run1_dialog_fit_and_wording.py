"""Run 1 (`G20`): the session dialog fits the screen and says what it is doing.

Covers `P8-ba` (ModalDialog had no scroll area and no screen clamp, so the Stop
button went off the bottom of the display), `P8-bb` (the evidence rows and the
poll summary were computed and rendered nowhere), `P8-bc` (Stop/Cancel wording),
`P8-bd` (the dialog blocked the page its own instructions send you to) and
`P8-w` (no single-poll-in-flight guard).

**Every size assertion here is a RELATIONSHIP measured at runtime, never a pixel
count.** A literal derived on one machine reddened all three CI legs once
(`CLAUDE.md § Hard-won lessons`), because CI's font set differs and every derived
metric moves with it. `tests/conftest.py` registers the bundled DM Sans, so the
metrics are at least stable — but a constant would still be a constant, and the
thing under test is "smaller than its content" / "fits the screen", which is a
relationship in the first place.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from control_ofc.api.models import (
    VALIDATION_KIND_LIFECYCLE,
    VALIDATION_KIND_THERMAL,
    VALIDATION_KIND_VALIDATION,
    VALIDATION_STATE_COMPLETED,
    VALIDATION_STATE_RECORDING,
    ValidationEvidence,
    ValidationSessionSummary,
)
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.footer import StatusFooter
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog
from tests.test_aio_mb_phase6 import _session

#: Far taller than any screen a test can run on, so "does it scroll" is not a
#: question about this machine's resolution.
_TALLER_THAN_ANY_SCREEN = 6000


def _tall_dialog(qtbot, **kw) -> tuple[ModalDialog, QWidget, QPushButton]:
    dlg = ModalDialog("Tall", **kw)
    qtbot.addWidget(dlg)
    tall = QLabel("content")
    tall.setMinimumHeight(_TALLER_THAN_ANY_SCREEN)
    dlg.body_layout().addWidget(tall)
    stop = dlg.add_footer_button("Stop", "secondary", object_name="Test_Btn_stop")
    return dlg, tall, stop


class TestModalDialogFitsTheScreen:
    """`P8-ba`. The defect: the layout minimum WAS the window minimum."""

    def test_content_taller_than_the_screen_does_not_become_a_window_minimum(self, qtbot):
        """The relationship that was inverted before the fix.

        Asserted against the body's own minimum rather than a number, so it means
        the same thing in any font: the window must be allowed to be smaller than
        its content, which is precisely what a scroll area buys and what the bare
        `QVBoxLayout` forbade.
        """
        dlg, tall, _ = _tall_dialog(qtbot)
        assert tall.minimumSizeHint().height() >= _TALLER_THAN_ANY_SCREEN
        assert dlg.minimumSizeHint().height() < tall.minimumSizeHint().height(), (
            "the dialog's minimum still tracks its content — the scroll area is "
            "not taking effect, and tall content is again a floor the window "
            "cannot go below"
        )

    def test_a_resize_smaller_than_the_content_is_honoured(self, qtbot):
        """Before the fix `resize(700, 400)` returned 767x2496 — measured.

        This is the user-visible half: the window could not be shrunk, so the
        footer stayed off-screen no matter what the user did.
        """
        dlg, tall, _ = _tall_dialog(qtbot)
        dlg.show()
        qtbot.waitExposed(dlg)
        target = dlg.minimumSizeHint().height() + 40
        dlg.resize(dlg.width(), target)
        assert dlg.height() == target
        assert dlg.height() < tall.minimumSizeHint().height()

    def test_the_shown_dialog_fits_the_screen_it_opened_on(self, qtbot):
        """`availableGeometry`, so a panel or taskbar is excluded.

        The realised height of a shown widget, not `sizeHint()` — those differ,
        and only the realised one is what the user actually sees.
        """
        dlg, _, _ = _tall_dialog(qtbot)
        dlg.show()
        qtbot.waitExposed(dlg)
        avail = dlg.screen().availableGeometry()
        assert dlg.height() <= avail.height()
        assert dlg.width() <= avail.width()

    def test_an_oversized_explicit_resize_is_clamped_on_show(self, qtbot):
        """A subclass may `resize()` itself past the screen (alert_center does).

        The scroll area alone does not cover this: it removes the *minimum*, and
        an explicit oversized `resize` is neither a minimum nor a size hint.
        """
        dlg, _, _ = _tall_dialog(qtbot)
        avail = dlg.screen().availableGeometry()
        dlg.resize(avail.width() * 3, avail.height() * 3)
        dlg.show()
        qtbot.waitExposed(dlg)
        assert dlg.height() <= avail.height()
        assert dlg.width() <= avail.width()

    def test_the_footer_is_outside_the_scroll_area_so_stop_cannot_scroll_away(self, qtbot):
        """Structural, and the reason the fix is not "wrap everything".

        A footer INSIDE the scroll area would satisfy every size assertion above
        and still hide Stop below the fold — the exact failure being fixed, one
        layer in. So this asserts ancestry, which is the property that makes the
        buttons unconditionally reachable.
        """
        dlg, _, stop = _tall_dialog(qtbot)
        scrolled = dlg._scroll.widget()
        assert dlg._body is scrolled, "the body must be the scrolled widget"
        assert not scrolled.isAncestorOf(stop), (
            "a footer button is inside the scroll area — it will scroll out of "
            "reach exactly like the body content"
        )
        assert not scrolled.isAncestorOf(dlg._title), "the header must not scroll either"

    def test_the_body_really_scrolls_rather_than_clipping(self, qtbot):
        """A scroll area that cannot scroll would hide the content instead."""
        dlg, _, _ = _tall_dialog(qtbot)
        dlg.show()
        qtbot.waitExposed(dlg)
        assert dlg._scroll.verticalScrollBar().maximum() > 0


class TestModalityIsOptional:
    """`P8-bd`. The scrim and the modality are one decision, not two."""

    def test_a_modeless_dialog_is_not_modal_and_paints_no_scrim(self, qtbot):
        host = QWidget()
        host.resize(400, 300)
        qtbot.addWidget(host)
        dlg = ModalDialog("Modeless", parent=host, modal=False)
        qtbot.addWidget(dlg)
        assert dlg.isModal() is False
        dlg._ensure_scrim()
        assert dlg._scrim is None, (
            "a veil over a window the user can still click is a lie about what is reachable"
        )

    def test_the_default_is_still_modal_with_a_scrim(self, qtbot):
        """The opposite branch, or the assertion above passes vacuously."""
        host = QWidget()
        host.resize(400, 300)
        qtbot.addWidget(host)
        dlg = ModalDialog("Modal", parent=host)
        qtbot.addWidget(dlg)
        assert dlg.isModal() is True
        dlg._ensure_scrim()
        assert dlg._scrim is not None
        dlg._remove_scrim()

    def test_the_session_dialog_chose_modeless(self, qtbot):
        """The call site, not just the capability — `CLAUDE.md`'s recurring trap.

        Asserted as a relationship against `ModalDialog`'s own default rather than
        the literal `False`, so it cannot pass if someone flips the base class.
        """
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        plain = ModalDialog("plain")
        qtbot.addWidget(plain)
        assert dlg.isModal() != plain.isModal()
        assert dlg.isModal() is False


class TestEndConditionIsStated:
    """`P8-az` (GUI half) / `P8-be`. The dialog never said when it stops."""

    @pytest.mark.parametrize(
        "kind",
        [VALIDATION_KIND_VALIDATION, VALIDATION_KIND_LIFECYCLE, VALIDATION_KIND_THERMAL],
    )
    def test_every_kind_states_that_it_records_until_stopped(self, qtbot, kind):
        dlg = ValidationSessionDialog("aio0", "AIO", kind=kind, members=[])
        qtbot.addWidget(dlg)
        text = dlg._intro.text().lower()
        # A RELATIONSHIP, not the literal "stop": the intro must name the button
        # the user is actually looking for, so renaming one and not the other
        # fails here. Asserting the literal is how this test broke when the
        # button became "Stop & Save" — the wording drifted and the test only
        # noticed because it happened to be checking the same words.
        assert dlg._stop_btn.text().lower() in text
        # The two things a user actually gets wrong, per the bug report.
        assert "closing this window" in text
        assert "diagnostics below does not end it" in text

    @pytest.mark.parametrize(
        "kind",
        [VALIDATION_KIND_VALIDATION, VALIDATION_KIND_LIFECYCLE, VALIDATION_KIND_THERMAL],
    )
    def test_every_kind_keeps_its_own_lead_in(self, qtbot, kind):
        """The shared sentence is APPENDED, not substituted for the kind copy."""
        from control_ofc.ui.widgets.validation_session_dialog import _KIND_INTROS

        dlg = ValidationSessionDialog("aio0", "AIO", kind=kind, members=[])
        qtbot.addWidget(dlg)
        assert _KIND_INTROS[kind] in dlg._intro.text()

    def test_the_duration_hints_are_scoped_per_member(self, qtbot):
        """They read as the session's duration before, which is the misreading."""
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        labels = [box.text() for _, box in dlg._diag_boxes]
        assert labels, "the fixture must offer at least one diagnostic"
        for text in labels:
            assert "per member" in text, f"unscoped duration hint: {text!r}"


class TestStopAndCancelTellTheTruth:
    """`P8-bc`. Both finalise and persist; only the state token differs."""

    def test_neither_button_is_styled_as_destructive(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        assert dlg._cancel_btn.property("variant") != "danger", (
            "the red styling said this button throws the recording away; it "
            "finalises and persists exactly as Stop does"
        )
        assert dlg._stop_btn.property("variant") == dlg._cancel_btn.property("variant")

    def test_both_buttons_say_they_save(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        assert "save" in dlg._stop_btn.text().lower()
        assert "cancelled" in dlg._cancel_btn.text().lower()
        # The tooltip carries the part that matters and does not fit on a button.
        assert "nothing is discarded" in dlg._cancel_btn.toolTip().lower()


class TestEvidenceRendersAsDiagnosticsFinish:
    """`P8-bb`. The only surface that changes DURING an orchestrated run."""

    def _evidence(self, **kw) -> ValidationEvidence:
        base = {
            "kind": "pwm_verify",
            "member_id": "hwmon:nct:dev:pwm1:AIO_PUMP",
            "outcome": "pass",
            "detail": "responded at 40%",
            "started_unix_ms": 1,
            "completed_unix_ms": 2,
        }
        base.update(kw)
        return ValidationEvidence(**base)

    def test_a_finished_diagnostic_appears_in_the_table(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        dlg.apply_session(_session(evidence=[self._evidence()]))
        assert dlg._evidence_table.rowCount() == 1
        # `isVisibleTo`, never `isVisible`: under the offscreen platform nothing
        # is shown, so `isVisible()` is False for every widget and this assertion
        # would pass with the `setVisible` call deleted.
        assert dlg._evidence_table.isVisibleTo(dlg)
        assert dlg._evidence_table.item(0, 0).text()
        assert "responded at 40%" in dlg._evidence_table.item(0, 3).text()

    def test_a_second_diagnostic_changes_what_is_on_screen(self, qtbot):
        """The bug was that NOTHING changed as diagnostics completed.

        Asserted as a change across the window rather than an end state, because
        an end-state snapshot cannot distinguish "rows appeared as they finished"
        from "rows appeared all at once at the end".
        """
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        dlg.apply_session(_session(evidence=[self._evidence()]))
        first = dlg._evidence_table.rowCount()
        dlg.apply_session(
            _session(evidence=[self._evidence(), self._evidence(kind="pwm_characterization")])
        )
        assert dlg._evidence_table.rowCount() > first

    def test_a_running_session_with_no_result_yet_says_so(self, qtbot):
        """Otherwise the first 2½ minutes are indistinguishable from a stall."""
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        dlg.apply_session(_session(evidence=[], state=VALIDATION_STATE_RECORDING))
        assert dlg._evidence_caption.isVisibleTo(dlg)
        assert "none has finished yet" in dlg._evidence_caption.text().lower()

    def test_a_passive_session_is_not_told_its_diagnostics_are_running(self, qtbot):
        """The opposite branch, and the defect self-review found.

        `view.diagnostics_note` is NEVER empty — a passive session gets
        "Recording only — no diagnostics were run." — so the caption predicate
        cannot key on it. Without this test the caption told a user who asked for
        no diagnostics that their diagnostics were running.
        """
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        dlg.apply_session(
            _session(evidence=[], state=VALIDATION_STATE_RECORDING, requested_diagnostics=[])
        )
        assert "none has finished yet" not in dlg._evidence_caption.text().lower()
        assert not dlg._evidence_caption.isVisibleTo(dlg)

    def test_no_session_hides_both_surfaces(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        dlg.apply_session(_session(evidence=[self._evidence()]))
        assert dlg._evidence_table.isVisibleTo(dlg)
        dlg.apply_session(None)
        assert not dlg._evidence_table.isVisibleTo(dlg)
        assert not dlg._evidence_caption.isVisibleTo(dlg)


class TestPollInFlightGuard:
    """`P8-w`. Both sibling dialogs already carried this."""

    def test_a_second_timer_fire_does_not_queue_a_second_poll(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        seen = []
        dlg.poll_requested.connect(lambda: seen.append(1))
        dlg._request_poll()
        dlg._request_poll()
        dlg._request_poll()
        assert len(seen) == 1

    def test_a_reply_re_arms_it(self, qtbot):
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        seen = []
        dlg.poll_requested.connect(lambda: seen.append(1))
        dlg._request_poll()
        dlg.apply_session(_session())
        dlg._request_poll()
        assert len(seen) == 2

    def test_a_failed_reply_also_re_arms_it(self, qtbot):
        """A guard that latches on the first socket error wedges what it guards."""
        dlg = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(dlg)
        seen = []
        dlg.poll_requested.connect(lambda: seen.append(1))
        dlg._request_poll()
        dlg.apply_error("error", "socket closed")
        dlg._request_poll()
        assert len(seen) == 2


class TestFooterAnnouncesARecordingSession:
    """`P8-bb` second half: the session was invisible once the dialog closed."""

    def _summary(self, **kw) -> ValidationSessionSummary:
        base = {
            "session_id": "vs-1",
            "kind": VALIDATION_KIND_THERMAL,
            "state": VALIDATION_STATE_RECORDING,
            "sample_count": 42,
        }
        base.update(kw)
        return ValidationSessionSummary(**base)

    def test_a_recording_session_is_announced(self, qtbot):
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_validation_session(self._summary())
        assert footer._session_btn.isVisibleTo(footer)
        assert "thermal observation" in footer._session_btn.text().lower()
        assert "42" in footer._session_btn.text()

    def test_a_finished_session_is_not(self, qtbot):
        """The daemon serves its most recent COMPLETED session indefinitely, so
        keying on presence would pin a permanent chip after the first run."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_validation_session(self._summary())
        assert footer._session_btn.isVisibleTo(footer)
        footer.set_validation_session(self._summary(state=VALIDATION_STATE_COMPLETED))
        assert not footer._session_btn.isVisibleTo(footer)

    def test_no_session_at_all_is_not_announced(self, qtbot):
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_validation_session(None)
        assert not footer._session_btn.isVisibleTo(footer)

    def test_losing_the_daemon_retracts_the_claim(self, qtbot):
        """A stale "Recording" would otherwise follow the user across every page."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_validation_session(self._summary())
        footer.set_live(False)
        assert not footer._session_btn.isVisibleTo(footer)

    def test_an_unknown_kind_is_rendered_rather_than_dropped(self, qtbot):
        """The 273-i rule: a newer daemon must not make a state vanish."""
        footer = StatusFooter()
        qtbot.addWidget(footer)
        footer.set_validation_session(self._summary(kind="some_future_kind"))
        assert footer._session_btn.isVisibleTo(footer)
        assert footer._session_btn.text().strip()


class TestModelessCallSite:
    """`P8-bd` at the call site — the half a dialog-only test cannot reach."""

    def _page(self, qtbot, monkeypatch):
        from tests.test_aio_mb_phase6 import _device, _page, _stub_workers

        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        return page

    def test_the_page_shows_it_without_blocking_and_keeps_the_reference(self, qtbot, monkeypatch):
        """`exec()` blocked every other window; `show()` does not.

        The reference matters as much as the call: with `exec()` the old
        `try/finally` cleared `_validation_dialog` the moment the call returned,
        so a modeless dialog left on that path would have been shown and then
        immediately orphaned from every poll route.
        """
        page = self._page(qtbot, monkeypatch)
        shown = []
        monkeypatch.setattr(
            ValidationSessionDialog, "show", lambda d: shown.append(d), raising=True
        )
        monkeypatch.setattr(
            ValidationSessionDialog,
            "exec",
            lambda d: pytest.fail("the session dialog must not block the main window"),
            raising=True,
        )
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        assert len(shown) == 1
        assert page._validation_dialog is shown[0]

    def test_closing_it_stops_polling_and_releases_the_reference(self, qtbot, monkeypatch):
        page = self._page(qtbot, monkeypatch)
        monkeypatch.setattr(ValidationSessionDialog, "show", lambda d: None, raising=True)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        dialog = page._validation_dialog
        assert dialog is not None
        assert dialog._timer.isActive(), "the dialog must be polling while open"
        dialog.reject()
        assert not dialog._timer.isActive()
        assert page._validation_dialog is None

    def test_a_stale_close_cannot_unhook_the_dialog_that_replaced_it(self, qtbot, monkeypatch):
        """The identity fence in `_on_validation_closed`, tested in isolation.

        **This test used to open the dialog twice**, which certified the fence
        while quietly depending on the reentrancy defect it sat next to — the
        reviewer's P2. Now that a second open raises the existing dialog instead
        of building a rival, the fence is exercised by handing
        `_on_validation_closed` a dialog that is genuinely not the current one,
        which is what the guard actually protects against: a `finished` signal
        from a previously-closed dialog arriving late.
        """
        page = self._page(qtbot, monkeypatch)
        monkeypatch.setattr(ValidationSessionDialog, "show", lambda d: None, raising=True)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        live = page._validation_dialog
        assert live is not None

        stale = ValidationSessionDialog("aio0", "AIO", members=[])
        qtbot.addWidget(stale)
        assert stale is not live

        page._on_validation_closed(stale)

        assert page._validation_dialog is live, (
            "a late `finished` from an older dialog cleared the live dialog's "
            "reference — poll replies would stop reaching the open window"
        )


class TestSessionDialogIsNotReentrant:
    """The reviewer's P1: `exec()` enforced this structurally, `show()` does not.

    A second dialog is not cosmetic. The daemon holds ONE session slot and poll
    replies route only to `page._validation_dialog`, so an orphan sits
    un-updated but fully interactive — and its Stop/Cancel/Start are forwarded to
    the daemon regardless of which dialog is current. Clicking Stop on the stale
    window would stop the live session while that window never showed the result.
    """

    def _page(self, qtbot, monkeypatch):
        from tests.test_aio_mb_phase6 import _device, _page, _stub_workers

        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        monkeypatch.setattr(ValidationSessionDialog, "show", lambda d: None, raising=True)
        return page

    def test_a_second_open_reuses_the_dialog_instead_of_building_a_rival(self, qtbot, monkeypatch):
        page = self._page(qtbot, monkeypatch)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        first = page._validation_dialog
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        assert page._validation_dialog is first

    def test_no_orphan_is_left_polling(self, qtbot, monkeypatch):
        """The measured symptom: two live 1 Hz timers, two polls per tick.

        Counts the polls actually emitted rather than the dialogs constructed —
        a guard that built a second dialog but silently stopped its timer would
        satisfy an object-identity assertion and still leave a rogue Stop button
        on screen, so the observable cost is what is asserted.
        """
        page = self._page(qtbot, monkeypatch)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)

        live = [d for d in page.findChildren(ValidationSessionDialog) if d._timer.isActive()]
        assert len(live) == 1, f"{len(live)} session dialogs are polling at once"

        polls = []
        page._validation_poll_request.connect(lambda: polls.append(1))
        for dialog in page.findChildren(ValidationSessionDialog):
            dialog._timer.timeout.emit()
        assert len(polls) == 1, f"{len(polls)} polls per tick — an orphan is still running"

    def test_asking_for_a_different_kind_says_why_it_did_not_switch(self, qtbot, monkeypatch):
        """Silently reusing a Validation window for a Thermal click would lie."""
        page = self._page(qtbot, monkeypatch)
        msgs = []
        page._show_diag_message = lambda m: msgs.append(m)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        page._open_validation(kind=VALIDATION_KIND_THERMAL)
        assert page._validation_dialog.kind() == VALIDATION_KIND_VALIDATION
        assert msgs and "one session at a time" in msgs[0]

    def test_closing_it_allows_a_fresh_open(self, qtbot, monkeypatch):
        """The guard must not become a one-session-per-app-launch trap."""
        page = self._page(qtbot, monkeypatch)
        page._open_validation(kind=VALIDATION_KIND_VALIDATION)
        first = page._validation_dialog
        first.reject()
        assert page._validation_dialog is None
        page._open_validation(kind=VALIDATION_KIND_THERMAL)
        assert page._validation_dialog is not None
        assert page._validation_dialog is not first
        assert page._validation_dialog.kind() == VALIDATION_KIND_THERMAL


class TestExportIsOneButtonWithAMenu:
    """`P8-bh`. The footer, not the content, was setting the minimum WIDTH.

    Measured before this change: dialog minimum width 840px against a body
    needing 184. Two export buttons were 214px of that, and 73px more came from
    DEC-337's own truthfulness rename (Stop/Cancel Session -> Stop & Save /
    Stop & Mark Cancelled), which took the footer from 767 to 840. So part of
    this row is a regression that change introduced, not merely old untidiness.
    """

    def _dialog(self, qtbot) -> ValidationSessionDialog:
        dlg = ValidationSessionDialog("aio0", "AIO Cooling System", members=[])
        qtbot.addWidget(dlg)
        return dlg

    def test_one_export_button_is_narrower_than_the_two_it_replaced(self, qtbot):
        """A RELATIONSHIP measured at runtime, never a pixel count.

        The two replaced buttons are rebuilt here in the same widget tree, so
        they carry the same font and style as the real one — which is what makes
        this portable. A literal (`< 767`) would be a font metric, and a font
        metric measured on one machine has reddened all three CI legs of this
        project before.
        """
        dlg = self._dialog(qtbot)
        old_csv = make_button("Export CSV", "secondary", parent=dlg)
        old_json = make_button("Export JSON", "secondary", parent=dlg)
        replaced = old_csv.minimumSizeHint().width() + old_json.minimumSizeHint().width()

        assert dlg._export_btn.minimumSizeHint().width() < replaced, (
            "the merged button is no narrower than the pair it replaced"
        )

    def test_the_footer_no_longer_needs_far_more_width_than_the_body(self, qtbot):
        """The defect in one line: the footer, not the content, set the minimum.

        Asserted as a ratio against the body measured in the same tree rather
        than a pixel figure. Still generous — the footer legitimately holds six
        buttons — but it fails if the footer regains a seventh.
        """
        dlg = self._dialog(qtbot)
        footer = dlg._footer.minimumSizeHint().width()
        body = dlg._body.minimumSizeHint().width()
        assert body > 0
        assert footer == dlg.minimumSizeHint().width(), (
            "the premise of this test has changed — the footer is no longer what "
            "sets the dialog's minimum width, so re-derive the bound"
        )
        assert footer < 4.5 * body, f"footer {footer} vs body {body}"

    def test_the_menu_is_actually_attached_to_the_button(self, qtbot):
        """Found by the fix-out check, not by reading the code.

        Every other test here triggers the QActions directly, which proves the
        connections work and proves nothing about whether the user can REACH
        them. With `setMenu` deleted the whole suite stayed green while the
        button became inert — the project's "the unit test proves you answered,
        never that you were asked" trap, in a menu coat.
        """
        dlg = self._dialog(qtbot)
        assert dlg._export_btn.menu() is dlg._export_menu
        assert set(dlg._export_btn.menu().actions()) == {dlg._csv_action, dlg._json_action}

    def test_the_menu_offers_both_formats(self, qtbot):
        dlg = self._dialog(qtbot)
        labels = [a.text() for a in dlg._export_menu.actions()]
        assert len(labels) == 2
        assert any("csv" in t.lower() for t in labels)
        assert any("json" in t.lower() for t in labels)

    @pytest.mark.parametrize("fmt", ["csv", "json"])
    def test_triggering_an_action_emits_the_wire_token(self, qtbot, fmt):
        """The ACTION, not a hand-called handler — the connection is the thing
        most likely to be wrong, and `hardware_page._export_session` switches on
        exactly these two tokens."""
        dlg = self._dialog(qtbot)
        seen = []
        dlg.export_requested.connect(seen.append)
        {"csv": dlg._csv_action, "json": dlg._json_action}[fmt].trigger()
        assert seen == [fmt]

    def test_export_follows_the_same_enablement_rule_as_the_pair_it_replaced(self, qtbot):
        """Both branches, so it cannot pass with `setEnabled` stuck either way.

        The rule is unchanged from the two-button version: exportable exactly
        when a session of ours is recording or finished, because a session that
        never started would produce an empty file the user reads as a failure.
        """
        dlg = self._dialog(qtbot)
        assert dlg._export_btn.isEnabled() is False, "nothing to export before a start"

        dlg.apply_session(_session(state=VALIDATION_STATE_RECORDING))
        assert dlg._export_btn.isEnabled() is True, "a recording session is exportable"

        dlg.apply_session(_session(state=VALIDATION_STATE_COMPLETED))
        assert dlg._export_btn.isEnabled() is True, "a finished session is exportable"

        dlg.apply_session(None)
        assert dlg._export_btn.isEnabled() is False, "no session, nothing to export"
