"""Run 2 (`G21`): a session can finalise itself when its diagnostics finish.

The GUI half of `P8-az`. The daemon half — the `stop_when_diagnostics_complete`
field, the id-fenced terminal hop in `spawn_orchestration`, the
`control.validation_auto_stop` capability — is tested in the daemon repo
(`daemon/tests/ipc_integration.rs`). What is tested here is everything the GUI
owns: the capability gate, the option's enablement rule, what Start actually
sends, and the one thing this whole feature can most easily get wrong — showing
the user a promise the connected daemon has no intention of keeping.

**Two rules from `CLAUDE.md § Hard-won lessons` shape almost every assertion.**
Relationships, never literals: the capability tests assert against the *wire
flag* rather than against `daemon_supports`, because asserting against the
lookup is satisfied by the defect it guards — delete the registry entry and both
sides go falsy together, which is exactly how DEC-334 shipped a feature
reachable on no daemon at all. And `isVisibleTo(parent)`, never `isVisible()`:
under `QT_QPA_PLATFORM=offscreen` nothing is shown, so an `isVisible()`
assertion passes with the visibility call deleted.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QCheckBox, QLabel

from control_ofc.api.models import (
    VALIDATION_KIND_LIFECYCLE,
    VALIDATION_KIND_THERMAL,
    VALIDATION_KIND_VALIDATION,
    Capabilities,
    ControlCapability,
)
from control_ofc.services.daemon_features import (
    DAEMON_FEATURE_CAPABILITY_FLAGS,
    DAEMON_FEATURE_LABELS,
    DAEMON_FEATURE_MINIMUMS,
    daemon_supports,
)
from control_ofc.ui.widgets.validation_session_dialog import (
    _END_CONDITION,
    _END_CONDITION_AUTO_STOP,
    ValidationSessionDialog,
)
from tests.test_aio_mb_phase6 import _session

_FEATURE = "validation_auto_stop"


def _caps(**control) -> Capabilities:
    control.setdefault("validation_sessions", True)
    return Capabilities(control=ControlCapability(**control))


def _dialog(qtbot, *, supported: bool = True, kind: str = VALIDATION_KIND_VALIDATION):
    dlg = ValidationSessionDialog(
        "aio0", "AIO Cooling System", kind=kind, auto_stop_supported=supported
    )
    qtbot.addWidget(dlg)
    return dlg


# ── The registry (the DEC-334 trap) ──────────────────────────────────────────


def test_the_feature_id_resolves_through_the_registry():
    """`daemon_supports` answers `None` for an unregistered id, and `None` is falsy.

    So an id that was never added to `DAEMON_FEATURE_CAPABILITY_FLAGS` produces
    a gate that is dead on **every** daemon — no exception, no log line, no
    failing test. That is DEC-334 exactly, and the correction it shipped was to
    sweep every entry point into the registry and weight the silent ones
    highest. `daemon_supports` is the silent one.
    """
    assert daemon_supports(_FEATURE, _caps(validation_auto_stop=True)) is True
    assert daemon_supports(_FEATURE, _caps(validation_auto_stop=False)) is False


def test_the_feature_id_is_in_all_three_registries():
    """One flag, one gating shape — and every registry a message may reach for.

    A missing `MINIMUMS` entry makes `minimum_version` raise; a missing
    `LABELS` entry makes `unsupported_feature_message` raise. Both are loud,
    which is why they are less dangerous than the flag map above — but a feature
    whose "requires daemon X" sentence crashes is still a dead end.
    """
    assert DAEMON_FEATURE_CAPABILITY_FLAGS[_FEATURE] == "validation_auto_stop"
    assert DAEMON_FEATURE_MINIMUMS[_FEATURE] == "2.43.0"
    assert _FEATURE in DAEMON_FEATURE_LABELS


# ── The capability gate at the call site ─────────────────────────────────────


@pytest.mark.parametrize("advertised", [True, False])
def test_the_page_offers_the_option_only_when_the_daemon_advertises_it(
    qtbot, monkeypatch, advertised
):
    """A RELATIONSHIP against the WIRE FLAG, not against `daemon_supports`.

    Asserting `dialog._auto_stop_supported == daemon_supports(...)` would be
    satisfied by the very defect this guards: remove the registry entry and both
    sides read falsy together. `capabilities.control.validation_auto_stop` is
    the one term that stays true independently of the lookup.

    Driven through `HardwarePage._open_validation` — the real call site, with
    the real dialog constructed — rather than by passing a literal to the
    constructor. A rule proven in isolation says nothing about whether the page
    passes it the right thing, which is `CLAUDE.md`'s most-recurred lesson.
    """
    from tests.test_aio_mb_phase6 import _device, _no_show, _page, _stub_workers

    page, state = _page(qtbot, devices=[_device()])
    caps = _caps(validation_auto_stop=advertised)
    state.set_capabilities(caps)
    _stub_workers(page)
    built = _no_show(monkeypatch, ValidationSessionDialog)
    page._open_validation(kind=VALIDATION_KIND_VALIDATION)

    assert len(built) == 1, "the dialog must actually have been built"
    box = built[0].findChild(QCheckBox, "Validation_Check_autoStop")
    assert box.isVisibleTo(built[0]) == caps.control.validation_auto_stop


@pytest.mark.parametrize("supported", [True, False])
def test_the_checkbox_is_hidden_rather_than_disabled_without_the_capability(qtbot, supported):
    """Hidden, not greyed: there is nothing the user could do to change it.

    `isVisibleTo(parent)` and not `isVisible()` — offscreen, every widget is
    invisible, so an `isVisible()` assertion here would pass with the
    `setVisible` call deleted.
    """
    dlg = _dialog(qtbot, supported=supported)
    box = dlg.findChild(QCheckBox, "Validation_Check_autoStop")
    assert box is not None, "the option must exist as a widget either way"
    assert box.isVisibleTo(dlg) == supported


def test_an_unsupported_daemon_is_never_sent_the_field(qtbot):
    """The gate is on the REQUEST, not only on the pixels.

    A hidden checkbox that still fed `_emit_start` would put the field on the
    wire against a daemon that drops it — which returns 200, records for two
    hours, and leaves the dialog claiming otherwise. That is `P8-az` reproduced
    inside its own fix.
    """
    dlg = _dialog(qtbot, supported=False)
    dlg._diag_boxes[0][1].setChecked(True)
    dlg._auto_stop_box.setChecked(True)  # force the worst case
    assert dlg._auto_stop_requested() is False


# ── The enablement rule ──────────────────────────────────────────────────────


def test_the_option_needs_a_diagnostic_to_complete(qtbot):
    """Both branches, because a stuck predicate passes a one-sided test.

    The daemon rejects `stop_when_diagnostics_complete` with an empty
    `diagnostics[]` with `400 validation_error`, so an always-enabled box would
    let the user compose a request that can only be refused.
    """
    dlg = _dialog(qtbot)
    box, hint = dlg._auto_stop_box, dlg.findChild(QLabel, "Validation_Label_autoStopHint")

    assert not box.isEnabled(), "no diagnostic is ticked, so there is nothing to complete"
    assert not box.isChecked()
    assert hint.isVisibleTo(dlg), "a control that greys out with no reason reads as broken"
    assert dlg._auto_stop_requested() is False

    dlg._diag_boxes[0][1].setChecked(True)
    assert box.isEnabled()
    assert not hint.isVisibleTo(dlg)

    # ...and back, which is the branch a one-directional implementation misses.
    dlg._diag_boxes[0][1].setChecked(False)
    assert not box.isEnabled()
    assert not box.isChecked()
    assert dlg._auto_stop_requested() is False


def test_a_users_choice_survives_the_box_being_disabled_and_re_enabled(qtbot):
    """Clearing the last diagnostic must not silently discard the user's answer.

    The box is un-ticked programmatically when it becomes meaningless, and the
    guard in `_on_auto_stop_toggled` is what stops that being recorded as the
    user changing their mind. Without it, re-ticking a diagnostic would bring
    the option back off and Start would quietly send `false`.
    """
    dlg = _dialog(qtbot, kind=VALIDATION_KIND_THERMAL)  # defaults OFF
    dlg._diag_boxes[0][1].setChecked(True)
    assert not dlg._auto_stop_box.isChecked()

    dlg._auto_stop_box.setChecked(True)  # the user opts in
    dlg._diag_boxes[0][1].setChecked(False)  # ...and clears the diagnostic
    dlg._diag_boxes[0][1].setChecked(True)  # ...then puts one back
    assert dlg._auto_stop_box.isChecked(), "the user's choice was discarded"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (VALIDATION_KIND_VALIDATION, True),
        (VALIDATION_KIND_LIFECYCLE, False),
        (VALIDATION_KIND_THERMAL, False),
    ],
)
def test_only_a_validation_session_pre_ticks_the_option(qtbot, kind, expected):
    """A validation session exists FOR its diagnostics; the other two are passive.

    All three kinds are asserted, so a default that drifted to "always on" — the
    behaviour change the daemon's own `false` default exists to avoid — cannot
    pass by matching one row.
    """
    dlg = _dialog(qtbot, kind=kind)
    dlg._diag_boxes[0][1].setChecked(True)
    assert dlg._auto_stop_box.isChecked() is expected


# ── What Start sends, through the signal ─────────────────────────────────────


@pytest.mark.parametrize("ticked", [True, False])
def test_start_sends_what_the_box_says(qtbot, ticked):
    """`.click()`, not `_emit_start()`.

    Invoking the handler directly skips the connection, which is the thing most
    likely to be broken (`CLAUDE.md § Hard-won lessons`).
    """
    dlg = _dialog(qtbot)
    dlg._diag_boxes[0][1].setChecked(True)
    dlg._auto_stop_box.setChecked(ticked)

    seen: list[tuple] = []
    dlg.start_requested.connect(lambda *a: seen.append(a))
    dlg._start_btn.click()

    assert len(seen) == 1, "Start must emit exactly once"
    assert seen[0][-1] is ticked, f"the auto-stop flag on the wire was {seen[0][-1]!r}"


def test_the_client_puts_the_field_on_the_wire_only_when_asked(monkeypatch):
    """And under the daemon's own field name.

    Omitted when false rather than sent as `false`: it keeps a gated client's
    request byte-identical to a pre-`P8-az` client's.
    """
    from control_ofc.api.client import DaemonClient

    posted: list[dict] = []

    def fake_post(self, path, json=None, **kw):
        posted.append(dict(json or {}))
        return {"session_id": "vs-1"}

    monkeypatch.setattr(DaemonClient, "_post", fake_post, raising=True)
    client = DaemonClient.__new__(DaemonClient)

    client.start_validation_session("aio0", diagnostics=["pwm_verify"])
    assert "stop_when_diagnostics_complete" not in posted[-1]

    client.start_validation_session(
        "aio0", diagnostics=["pwm_verify"], stop_when_diagnostics_complete=True
    )
    assert posted[-1]["stop_when_diagnostics_complete"] is True


# ── Truthfulness: render the daemon's answer, not our own request ────────────


@pytest.mark.parametrize("echoed", [True, False])
def test_the_end_condition_renders_the_daemons_echo_not_the_checkbox(qtbot, echoed):
    """**The test this feature exists to pass.**

    The dialog is put in the state a client's own request memory would get
    wrong: the checkbox is ticked, and the session the daemon returns says
    otherwise. That is not a contrived shape — it is precisely what a daemon
    older than 2.43.0 produces, because `serde` drops the unknown request field
    and answers `200`. A dialog rendering its own intent would then promise an
    end the daemon will never deliver, which is `P8-az` itself.

    Asserted as a relationship against `session.stop_when_diagnostics_complete`,
    so a call site reading the checkbox cannot satisfy it in either direction.
    """
    dlg = _dialog(qtbot)
    dlg._diag_boxes[0][1].setChecked(True)
    dlg._auto_stop_box.setChecked(True)
    assert dlg._auto_stop_box.isChecked(), "the precondition: the CLIENT asked for auto-stop"

    session = _session(stop_when_diagnostics_complete=echoed)
    dlg.apply_session(session)

    expected = (
        _END_CONDITION_AUTO_STOP if session.stop_when_diagnostics_complete else _END_CONDITION
    )
    other = _END_CONDITION if session.stop_when_diagnostics_complete else _END_CONDITION_AUTO_STOP
    text = dlg._intro.text()
    assert expected in text, "the intro does not describe the end the DAEMON reported"
    assert other not in text, "the intro describes both ends at once"


def test_the_two_end_conditions_are_not_the_same_sentence():
    """The precondition for the test above.

    If the two strings were ever collapsed, every assertion there would hold
    vacuously — `expected in text` and `other not in text` cannot both be
    meaningful when the two are equal.
    """
    assert _END_CONDITION != _END_CONDITION_AUTO_STOP
    # The old sentence promises the cap; the new one promises the diagnostics.
    assert "sample cap" in _END_CONDITION
    assert "diagnostics" in _END_CONDITION_AUTO_STOP


def test_a_finished_session_lets_the_end_condition_follow_the_form_again(qtbot):
    """Once the form is editable, the sentence describes the NEXT session.

    `_apply_enablement` re-enables the options on anything but `recording`, so
    after a session finishes the user is composing the next run — and captioning
    that form with the *previous* run's ending is a different way of saying
    something untrue. The daemon's echo wins only while a session it owns is
    live, which is exactly when the form is locked.
    """
    dlg = _dialog(qtbot)
    dlg.apply_session(_session(state="completed", stop_when_diagnostics_complete=False))
    assert dlg._options_section.isEnabled(), "the precondition: the form is editable again"

    dlg._diag_boxes[0][1].setChecked(True)
    dlg._auto_stop_box.setChecked(True)
    assert _END_CONDITION_AUTO_STOP in dlg._intro.text(), (
        "the finished session's ending is captioning the run being composed now"
    )


def test_the_intro_is_composed_before_the_dialog_is_ever_shown(qtbot):
    """The label is created empty and filled by `_sync_end_condition`.

    One owner for the sentence, rather than the same formatting in `__init__`
    and in the sync — but that only works if the sync really does run during
    construction. An empty intro would be a blank panel above the form, and no
    other assertion in this file would notice.
    """
    dlg = _dialog(qtbot, supported=False)  # the plainest possible construction
    text = dlg._intro.text()
    assert text.strip(), "the intro is empty — `_sync_end_condition` did not run at build time"
    assert _END_CONDITION in text
