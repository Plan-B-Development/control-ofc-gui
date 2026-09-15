"""G40 — the verify vocabulary and who a verify result belongs to.

Three register rows, one change set (DEC-364):

* `ACK-j` — an unrecognised token rendered as ``Result: Result: <token>``,
  because `outcome_for`'s fallback arms baked in a prefix the one caller adds.
* `ACK-k` — the GPU verify vocabulary was inlined in `system_state_page` while
  sharing four token names with the hwmon table and disagreeing with it on two.
* `ACK-n` — a verify result was attributed to an open sweep by "is a sweep
  running", not by "did the sweep ask for this".

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
    """A page with a real two-header sweep open, waiting on its first header."""
    page = _page(
        qtbot,
        headers=[
            HwmonHeader(id="pwm1", is_writable=True),
            HwmonHeader(id="pwm2", is_writable=True),
        ],
    )
    page._ensure_verify_worker = lambda: True  # type: ignore[method-assign]  # no real thread
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


def test_the_single_header_button_is_live_mid_sweep(qtbot):
    """Why `ACK-n` was LIVE rather than latent, pinned rather than asserted.

    `ACK-n` recorded that DEC-358 had closed the reachable path to a foreign
    result, by stopping a background re-render from re-enabling the button. That
    is one route and there is a second, older one the row did not account for:
    `_on_verify_ok` re-enables `_verify_btn` **unconditionally**, and
    `_step_pwm_verify_all` never disables it again — so from the moment the first
    header reports until the sweep ends, *Test PWM Control* is clickable, which
    is exactly the concurrent verify the attribution fix exists to survive.

    Sampled after the first result and before the last, because the button is
    legitimately enabled once the sweep has finished — a test that looked at the
    end state would pass against a sweep that kept it disabled throughout.
    """
    page, emitted = _sweep(qtbot)
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"), "pwm1")

    assert page._verify_all_total > 0, "precondition: the sweep must still be running"
    assert emitted == ["pwm1", "pwm2"], "precondition: it must have advanced, not finished"
    assert page._verify_btn.isEnabled() is True, (
        "if this fails the second route has been closed — good, but then `ACK-z` "
        "is fixed and this test and the severity claims that cite it must be re-read"
    )
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
