"""G40 — the verify vocabulary and who a verify result belongs to.

Three register rows, one change set (DEC-364):

* `ACK-j` — an unrecognised token rendered as ``Result: Result: <token>``,
  because `outcome_for`'s fallback arms baked in a prefix the one caller adds.
* `ACK-k` — the GPU verify vocabulary was inlined in `system_state_page` while
  sharing four token names with the hwmon table and disagreeing with it on two.
* `ACK-n` — a verify result was attributed to an open sweep by "is a sweep
  running", not by "did the sweep ask for this".

`ACK-z` was opened by that change and closed later, in the same file: the
sweep disabled *Test PWM Control* and both result handlers switched it back on
again. Its tests sit with `ACK-n`'s because they share the `_sweep` fixture and
because the two rows are one story — `ACK-z` is the route that made `ACK-n`
live.

Written as relationships rather than literals wherever the literal would also
be satisfied by the defect (`CLAUDE.md § Hard-won lessons`).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    ConnectionState,
    GpuVerifyResult,
    HwmonHeader,
    HwmonVerifyResult,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.verify_view import (
    _GPU_OUTCOMES,
    _OUTCOMES,
    build_verify_result_view,
    gpu_outcome_for,
    outcome_for,
)
from control_ofc.ui.pages.system_state_page import SystemStatePage

PREFIX = "Result: "


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


def _page(qtbot, headers=()):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    if headers:
        state.set_hwmon_headers(list(headers))
    page = SystemStatePage(
        state=state,
        diagnostics_service=DiagnosticsService(state),
        client=object(),
    )
    qtbot.addWidget(page)
    return page


# ── ACK-j — one prefix, one owner ────────────────────────────────────────


def test_no_arm_of_the_vocabulary_carries_the_result_prefix():
    """The invariant, stated over every arm rather than over the bug's example.

    `build_verify_result_view` owns ``Result: ``. A summary that carries it too
    is the defect, whichever arm produced it — so this asserts the rule, not the
    one token that happened to trip it.
    """
    arms = [
        *_OUTCOMES,  # every known token
        "future_token",  # a daemon newer than this GUI
        "error:unavailable",  # the sweep's own synthesised failure
    ]
    offenders = [tok for tok in arms if outcome_for(tok).summary.startswith(PREFIX)]
    assert offenders == [], f"these arms prefix their own summary: {offenders}"


def test_an_unrecognised_token_renders_with_exactly_one_prefix():
    """The rendered artefact, not a re-derivation of the rule above.

    Asserted on `view.text` — what the user reads — because the doubling was
    produced by the *caller* adding a prefix the callee had already added, and
    only the composed result can see that.
    """
    view = build_verify_result_view(HwmonVerifyResult(header_id="h", result="future_token"))
    first = view.lines[0]
    assert first.startswith(PREFIX), "273-i: an unknown token must still render"
    assert not first[len(PREFIX) :].startswith(PREFIX), f"doubled prefix: {first!r}"
    assert "future_token" in first, "the token itself must survive verbatim"

    # The opposite arm, or a fix that simply stopped prefixing anything passes.
    known = build_verify_result_view(HwmonVerifyResult(header_id="h", result="effective"))
    assert known.lines[0].startswith(PREFIX)
    assert not known.lines[0][len(PREFIX) :].startswith(PREFIX)


def test_an_unrecognised_token_says_it_is_a_version_gap():
    """The only way to reach this arm is a daemon newer than this GUI.

    Without the hint the user reads an opaque token as a hardware fault. The
    `error:` arm is the discriminating case: this GUI synthesises that token
    itself, so telling the user to upgrade would be a lie.
    """
    unknown = outcome_for("future_token").summary
    assert "does not recognise" in unknown, f"no version-gap hint: {unknown!r}"
    assert "does not recognise" not in outcome_for("error:unavailable").summary, (
        "an `error:` token is this GUI's own synthesis, not a version gap"
    )
    assert "does not recognise" not in outcome_for("effective").summary


# ── ACK-k — two vocabularies, deliberately not one ───────────────────────


def test_the_gpu_and_hwmon_vocabularies_disagree_where_they_are_documented_to():
    """The reason these are two tables, asserted as the disagreement itself.

    Four token names are shared. If a future change merges the tables on the
    strength of those names, these two rows are what it silently loses: a GPU
    with no fan-RPM sensor is unexpected (Warning) where a header with no tach
    is ordinary (neutral), and a GPU that ignored an applied curve is Critical
    where a case fan behind a splitter is not.
    """
    shared = set(_GPU_OUTCOMES) & set(_OUTCOMES)
    assert {"effective", "no_rpm_effect", "pwm_enable_reverted", "rpm_unavailable"} <= shared

    assert (
        gpu_outcome_for("rpm_unavailable").chip_class != outcome_for("rpm_unavailable").chip_class
    )
    assert gpu_outcome_for("no_rpm_effect").chip_class != outcome_for("no_rpm_effect").chip_class
    # ...and they must still agree where agreement is correct, or "different"
    # is being satisfied by two tables that have simply drifted apart.
    assert gpu_outcome_for("effective").chip_class == outcome_for("effective").chip_class


def test_every_token_the_contract_documents_has_a_gpu_row():
    """`docs/08` § GPU fan verify — the wire's seven tokens.

    Pins the vocabulary across the move out of the page: an extraction that
    dropped a row would otherwise degrade that token to the unknown arm, which
    still renders and so would not fail anything else.
    """
    documented = {
        "effective",
        "curve_not_applied",
        "no_rpm_effect",
        "zero_rpm_suppressed",
        "rpm_unavailable",
        "write_failed",
        "pwm_enable_reverted",
    }
    assert set(_GPU_OUTCOMES) == documented


def test_the_gpu_page_renders_the_gpu_table_and_not_the_hwmon_one(qtbot):
    """The call site, not the extracted rule (`CLAUDE.md § Hard-won lessons`).

    Asserted against `gpu_outcome_for`, so a page that kept its own copy fails
    as soon as the table moves — and on `rpm_unavailable`, the token where an
    accidental merge would be visible, because the two tables answer it
    differently.
    """
    page = _page(qtbot)
    label = page._gpu_verify_result_label

    for token in ("rpm_unavailable", "effective", "write_failed"):
        page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result=token))
        expected = gpu_outcome_for(token)
        assert expected.summary in label.text(), f"{token}: page wording is not the table's"
        assert label.property("class") == expected.chip_class

    # The discriminating arm: on `rpm_unavailable` the two tables disagree, so
    # only a call site reading the GPU one can produce this. Matching the
    # extracted table on a token both tables answer identically would be
    # satisfied by a page still pointed at `outcome_for`.
    page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result="rpm_unavailable"))
    assert label.property("class") == gpu_outcome_for("rpm_unavailable").chip_class
    assert label.property("class") != outcome_for("rpm_unavailable").chip_class

    # An unknown GPU token follows the same prefix rule as the hwmon side.
    page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result="future_gpu_token"))
    first = label.text().splitlines()[0]
    assert first.startswith(PREFIX)
    assert not first[len(PREFIX) :].startswith(PREFIX)
    assert "future_gpu_token" in first
    _flush(page)


# ── ACK-n — a result belongs to the request that asked for it ────────────


def _sweep(qtbot):
    """A page with a real two-header sweep open, waiting on its first header.

    The combo is filled through `_populate_verify_combo` — the same method
    `_render` calls — because the page only builds it when hardware diagnostics
    arrive, and `_verify_btn`'s gate reads its count (row `ACK-z`). Asserted as
    a precondition below: an empty combo would keep the button disabled for a
    reason that has nothing to do with the sweep, and every assertion about it
    would pass for the wrong one.
    """
    page = _page(
        qtbot,
        headers=[
            HwmonHeader(id="pwm1", is_writable=True),
            HwmonHeader(id="pwm2", is_writable=True),
        ],
    )
    page._ensure_verify_worker = lambda: True  # type: ignore[method-assign]  # no real thread
    page._populate_verify_combo()
    assert page._verify_combo.count() == 2, "precondition: the header combo must be populated"
    assert page._verify_btn.isEnabled() is True, (
        "precondition: the button must start enabled, or 'disabled mid-sweep' proves nothing"
    )
    emitted: list[str] = []
    page._verify_request.connect(emitted.append)
    page._run_pwm_verify_all()
    assert emitted == ["pwm1"], "precondition: the sweep must actually be running"
    assert page._verify_all_pending == "pwm1"
    return page, emitted


def test_a_foreign_result_is_not_absorbed_into_an_open_sweep(qtbot):
    """The defect: any result popped the queue and joined the recorded set.

    The foreign header is `pwm9`, which the sweep never queued — asserted as a
    precondition, because a sample that matched the pending header would be
    absorbed correctly and prove nothing.
    """
    page, emitted = _sweep(qtbot)
    assert page._verify_all_pending != "pwm9", (
        "precondition: the foreign header must NOT be the one the sweep is waiting on, "
        "or it would be absorbed correctly and this test would prove nothing"
    )

    page._on_verify_ok(HwmonVerifyResult(header_id="pwm9", result="no_rpm_effect"), "pwm9")

    assert page._verify_all_results == [], "a foreign result joined the sweep's evidence"
    assert emitted == ["pwm1"], "a foreign result popped a header off the sweep's queue"
    assert page._verify_all_pending == "pwm1", "the sweep stopped waiting for its own header"
    # It is still SHOWN — not absorbed is not the same as not rendered.
    assert page._verify_result_label.text(), "the user was told nothing about their own verify"
    _flush(page)


def test_a_foreign_error_is_not_absorbed_into_an_open_sweep(qtbot):
    """The error arm had no header at all — it read `_verify_active_header`.

    That field is overwritten by whichever path requested last, so a foreign
    failure was recorded against whatever the sweep happened to be testing.
    """
    page, emitted = _sweep(qtbot)
    assert page._verify_all_pending != "pwm9", (
        "precondition: the foreign header must NOT be the one the sweep is waiting on, "
        "or it would be absorbed correctly and this test would prove nothing"
    )

    page._on_verify_error("error", "the other one exploded", "pwm9")

    assert page._verify_all_results == [], "a foreign failure was recorded against the sweep"
    assert emitted == ["pwm1"]
    assert page._verify_all_pending == "pwm1"
    _flush(page)


def test_the_sweeps_own_result_is_absorbed_and_advances_it(qtbot):
    """The arm where the new lookup FINDS something (DEC-340).

    The rejecting arm above returns the pre-fix answer for a sweep that records
    nothing either way; only this one can distinguish a matching request from a
    guard that rejects everything.
    """
    page, emitted = _sweep(qtbot)

    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")
    assert page._verify_all_results == [("pwm1", "effective")]
    assert emitted == ["pwm1", "pwm2"], "the sweep did not advance to its next header"
    assert page._verify_all_pending == "pwm2"

    page._on_verify_error("unavailable", "gone", "pwm2")
    assert page._verify_all_results == [("pwm1", "effective"), ("pwm2", "error:unavailable")]
    assert page._verify_all_total == 0, "the sweep did not finish"
    assert page._verify_all_pending is None
    _flush(page)


def test_a_foreign_result_does_not_wedge_the_sweep(qtbot):
    """Rejecting is not dropping: the sweep's own answer still arrives.

    Every request the sweep emits produces exactly one signal carrying that same
    requested header, so ignoring a foreign one cannot leave it waiting forever
    — which is the regression a stricter guard could plausibly introduce.
    """
    page, emitted = _sweep(qtbot)

    page._on_verify_ok(HwmonVerifyResult(header_id="pwm9", result="effective"), "pwm9")
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")

    assert emitted == ["pwm1", "pwm2"], "the sweep stalled after ignoring a foreign result"
    assert [h for h, _ in page._verify_all_results] == ["pwm1"]
    _flush(page)


# ── ACK-z — the sweep's intent is not overridden by its own results ──────


def test_the_single_header_button_stays_dead_for_the_whole_sweep(qtbot):
    """`ACK-z`: the second route, and the one that made `ACK-n` live.

    `ACK-n` recorded that DEC-358 had closed the reachable path to a foreign
    result, by stopping a background re-render from re-enabling the button. That
    is one route and there was a second, older one the row did not account for:
    `_on_verify_ok` re-enabled `_verify_btn` **unconditionally** and
    `_step_pwm_verify_all` never disabled it again — so from the moment the
    first header reported until the sweep ended, *Test PWM Control* was
    clickable, which is exactly the concurrent verify the attribution fix exists
    to survive.

    **Both arms are asserted in one test on purpose.** Sampled only mid-sweep,
    this passes against a fix that disables the button forever; sampled only at
    the end, it passes against a sweep that never disabled it at all. Neither
    half is evidence without the other, which is why the closing arm is here
    rather than in a sibling test that could be deleted on its own.
    """
    page, emitted = _sweep(qtbot)
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")

    assert page._verify_all_total > 0, "precondition: the sweep must still be running"
    assert emitted == ["pwm1", "pwm2"], "precondition: it must have advanced, not finished"
    assert page._verify_btn.isEnabled() is False, (
        "the sweep disabled *Test PWM Control* and its own first result switched it "
        "back on — the button now offers an action the page will not honour"
    )

    # Closing arm: it must come back, or "fixed" means "disabled forever".
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="effective"), "pwm2")
    assert page._verify_all_total == 0, "precondition: the sweep must have finished"
    assert page._verify_btn.isEnabled() is True, "the sweep ended and the button never came back"
    _flush(page)


def test_the_error_arm_also_leaves_the_button_dead_mid_sweep(qtbot):
    """`_on_verify_error` carried the identical unconditional re-enable.

    The success arm above cannot see it: a sweep whose headers all *fail* never
    runs `_on_verify_ok` at all, so a fix applied to one handler and not the
    other leaves the defect fully reachable on every board where the verify
    errors — which, for a header the daemon cannot write, is every board.
    """
    page, emitted = _sweep(qtbot)
    page._on_verify_error("unavailable", "gone", "pwm1")

    assert page._verify_all_total > 0, "precondition: the sweep must still be running"
    assert emitted == ["pwm1", "pwm2"], "precondition: it must have advanced, not finished"
    assert page._verify_btn.isEnabled() is False, (
        "the error arm re-enabled the button the sweep had disabled"
    )

    page._on_verify_error("unavailable", "gone", "pwm2")
    assert page._verify_all_total == 0, "precondition: the sweep must have finished"
    assert page._verify_btn.isEnabled() is True, "the sweep ended and the button never came back"
    _flush(page)


def _single_verify(qtbot):
    """An ORDINARY single verify, driven through the real entry point.

    Not a direct slot call. `_run_pwm_verify` is what sets
    `_verify_active_header`, and that field is the whole trap: it is still set
    when the result handler runs, so a re-enable gated on `_verify_in_flight()`
    alone disables the button permanently. A fixture that calls the slot without
    it cannot see that — the extracted-rule trap, one field over.
    """
    page = _page(
        qtbot,
        headers=[
            HwmonHeader(id="pwm1", is_writable=True),
            HwmonHeader(id="pwm2", is_writable=True),
        ],
    )
    page._ensure_verify_worker = lambda: True  # type: ignore[method-assign]  # no real thread
    page._populate_verify_combo()  # as `_render` does — see `_sweep`
    assert page._verify_combo.count() == 2, "precondition: the header combo must be populated"
    emitted: list[str] = []
    page._verify_request.connect(emitted.append)
    page._run_pwm_verify()
    assert emitted, "precondition: the verify must actually have been requested"
    assert page._verify_active_header == emitted[0], (
        "precondition: the field that springs the trap must really be set, or this "
        "test passes against the gate it exists to check"
    )
    assert page._verify_btn.isEnabled() is False, "precondition: the button must be disabled"
    assert page._verify_all_total == 0, "precondition: no sweep is open"
    return page, emitted[0]


def test_an_ordinary_single_verify_gives_the_button_back(qtbot):
    """The trap: gating the re-enable without clearing the state first.

    `_verify_in_flight()` reads `_verify_active_header`, which `_on_verify_ok`
    clears two lines *after* the old re-enable sat. Gate the re-enable there and
    every ordinary verify disables *Test PWM Control* for the rest of the
    session — no error, no log line, and the pre-existing slot-level test cannot
    see it because its page never set the field.
    """
    page, header_id = _single_verify(qtbot)

    page._on_verify_ok(HwmonVerifyResult(header_id=header_id, result="effective"), header_id)

    assert page._verify_active_header is None, "the in-flight marker was not cleared"
    assert page._verify_btn.isEnabled() is True, (
        "an ordinary single verify left the button disabled — the gate read "
        "`_verify_active_header` before the handler cleared it"
    )
    _flush(page)


def test_an_ordinary_single_verify_that_FAILS_gives_the_button_back(qtbot):
    """The same trap on the error arm, which has the same two lines."""
    page, header_id = _single_verify(qtbot)

    page._on_verify_error("unavailable", "gone", header_id)

    assert page._verify_active_header is None, "the in-flight marker was not cleared"
    assert page._verify_btn.isEnabled() is True, (
        "a failed single verify left the button disabled for the rest of the session"
    )
    _flush(page)


def test_the_button_gate_is_one_shape_wherever_it_is_read(qtbot):
    """A RELATIONSHIP, not a literal — the four call sites must agree.

    Every assertion above states an expected boolean, each of which a
    single-site fix could satisfy while the other sites kept their own
    expressions (DEC-334: one flag, one gating shape). This asserts the button
    matches `_sync_verify_button_enabled`'s own predicate after each handler
    runs, so a call site that stops routing through the helper fails here even
    when its answer happens to be right.
    """
    page, emitted = _sweep(qtbot)

    def expected() -> bool:
        return not page._verify_in_flight() and page._verify_combo.count() > 0

    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")
    # `emitted` is asserted, not dropped: satisfying RUF059 by renaming it to a
    # dummy would delete the only evidence that the sample below is mid-sweep.
    assert emitted == ["pwm1", "pwm2"], "precondition: the sweep advanced rather than finishing"
    assert expected() is False, "precondition: mid-sweep must be an in-flight sample"
    assert page._verify_btn.isEnabled() == expected()

    page._populate_verify_combo()  # the autonomous 1 Hz re-render, mid-sweep
    assert page._verify_btn.isEnabled() == expected(), "a re-render disagreed with the gate"

    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="effective"), "pwm2")
    assert expected() is True, "precondition: the finished sample must differ from the first"
    assert page._verify_btn.isEnabled() == expected()
    _flush(page)


def test_the_worker_emits_the_requested_header_on_both_arms(qtbot):
    """The signal's arity, exercised through a real emit rather than a slot call.

    Every other test in this file calls the page's slots directly, which proves
    the slot bodies and says nothing about whether the worker actually emits what
    they expect. `verify_ok` gained an argument; a mismatch between signal and
    slot would reach the running app with the suite still green. Mirrors the
    existing `_GpuVerifyWorker` success-path test.
    """
    from unittest.mock import MagicMock

    from control_ofc.api.errors import DaemonUnavailable
    from control_ofc.ui.pages.diagnostics_workers import _VerifyWorker

    worker = _VerifyWorker("/tmp/x.sock")
    result = MagicMock(name="HwmonVerifyResult")
    worker._ensure_client = MagicMock(return_value=MagicMock(verify_hwmon_pwm=lambda h: result))
    ok: list[tuple] = []
    worker.verify_ok.connect(lambda r, h: ok.append((r, h)))
    worker.do_verify("hwmon:nct6798:pwm3")
    assert ok == [(result, "hwmon:nct6798:pwm3")], (
        "verify_ok did not carry the REQUESTED header — the sweep would absorb nothing"
    )

    worker._ensure_client = MagicMock(
        return_value=MagicMock(verify_hwmon_pwm=MagicMock(side_effect=DaemonUnavailable("gone")))
    )
    errors: list[tuple] = []
    worker.verify_error.connect(lambda c, m, h: errors.append((c, h)))
    worker.do_verify("hwmon:nct6798:pwm3")
    assert errors == [("unavailable", "hwmon:nct6798:pwm3")]
