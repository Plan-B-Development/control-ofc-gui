"""`SSN-l` / `SSN-i` / `ACK-i` / `ACK-y` — the Full Report is the System State
page's answer at length, never a second opinion (DEC-379, GUI v2.76.5).

`SSN-i` was answered by the user as option **C**: neither surface owns a global
verdict, and each answers its own narrower question. The Hardware page answers
"is this machine's hardware/driver stack set up for fan control?" from the
daemon's evidence-based `GET /inventory/hardware-readiness`; the System State
page answers "what on this machine needs a response right now?" from
observation, in ISA-18.2 vocabulary. The pop-out report is **not** a third
surface — it is the System State page's answer at length, so it must share that
page's derivation.

It did not. DEC-357 gave `detect_readiness_problems` a `pwm_control_verified`
argument and threaded it through two of its four call sites, so the report
opened with "✓ System ready" on a machine whose own pill above it read
"1 ACTION REQUIRED", with an empty "To fix" block — reading *healthier* than
the machine is, on a board with a documented matching quirk and a failed PWM
test.

Every assertion below is a **relationship** between the two consumers, never a
literal: a green banner beside a `1 ACTION REQUIRED` pill is the defect, and
only comparing them can see it (`CLAUDE.md § Hard-won lessons`, DEC-324).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.services.duty_drift import NO_DRIFT
from control_ofc.services.system_state_view import _STATE_BY_CSS, build_system_state_vm
from control_ofc.ui.widgets.readiness_report import (
    build_fix_guidance_html,
    build_readiness_report_html,
    detect_readiness_problems,
    readiness_verdict,
)
from tests.test_system_state_health_noise import _healthy_gigabyte

#: The recorded verify outcome that makes the two derivations diverge. Per
#: DEC-340, this is the ONLY arm that discriminates: `None` and `True` promote
#: nothing, so they return the pre-fix answer by construction, and a test
#: written on either would pass with the whole fix deleted.
_DISCRIMINATING = False


def _diag():
    """A machine whose board note is promoted by a failed PWM test.

    The live reproduction: a Gigabyte AORUS MASTER on an `it87`-family chip,
    8/8 headers writable, no collision, no ACPI conflict, no reclaim — healthy
    on `GET /diagnostics/hardware` alone, and matching the vendor quirk whose
    mechanism is "the BIOS accepts PWM writes and silently overrides them".
    """
    return _healthy_gigabyte()


def test_the_fixture_still_diverges_across_the_flag():
    """The precondition, or everything below asserts nothing.

    Stated on a quantity fixed BEFORE the defect can act — whether this fixture
    still matches a promotable quirk — rather than on anything the fix produces.
    A precondition derived from the defect becomes unsatisfiable when the fix is
    removed, and the test then fails saying "the window never opened", which
    points a maintainer at the harness instead of at the missing argument
    (DEC-348).
    """
    assert detect_readiness_problems(_diag(), pwm_control_verified=True, duty_drift=NO_DRIFT) == []
    promoted = detect_readiness_problems(
        _diag(), pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT
    )
    assert promoted, "fixture no longer matches a promotable quirk"


@pytest.mark.parametrize("verified", [None, True, _DISCRIMINATING])
def test_the_report_banner_and_the_page_pill_never_disagree(verified):
    """`SSN-l`, stated as the invariant it broke.

    Both sides are asked the same question with the same argument, and the
    answer is compared through `_STATE_BY_CSS` — the map the page itself used
    to translate one into the other, so neither side of this assertion is a
    re-derivation of the other's arithmetic.
    """
    diag = _diag()
    _text, css = readiness_verdict(diag, pwm_control_verified=verified, duty_drift=NO_DRIFT)
    vm = build_system_state_vm(diag, pwm_control_verified=verified, duty_drift=NO_DRIFT)
    assert _STATE_BY_CSS[css] == vm.issue_count_state, (
        f"verify={verified}: report banner {css} vs page pill {vm.issue_count_state}"
    )


def test_the_to_fix_block_appears_exactly_when_the_pill_is_counting():
    """Presence before absence, both arms.

    "The To fix block is populated" proves nothing on its own — a block that is
    always populated satisfies it. The `True` arm establishes that this fixture
    can produce an empty one, so the `False` arm is a change rather than a
    constant.
    """
    diag = _diag()
    quiet = build_fix_guidance_html(diag, pwm_control_verified=True, duty_drift=NO_DRIFT)
    loud = build_fix_guidance_html(diag, pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT)

    assert quiet is None
    assert loud is not None
    assert (
        build_system_state_vm(
            diag, pwm_control_verified=True, duty_drift=NO_DRIFT
        ).issues_requiring_attention
        == 0
    )
    vm = build_system_state_vm(diag, pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT)
    assert vm.issues_requiring_attention > 0
    for problem in detect_readiness_problems(
        diag, pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT
    ):
        assert problem["label"] in loud, f"{problem['key']} is counted but not in 'To fix'"


def test_the_report_html_never_opens_healthier_than_the_page():
    """The user-visible end: the rendered artefact, not an intermediate.

    `readiness_verdict`'s healthy sentence is the one that shipped over a
    counted condition, so this asserts on the HTML the dialog actually receives
    rather than on the tuple that feeds it (DEC-320: assert the realised
    artefact).
    """
    diag = _diag()
    healthy_text = readiness_verdict(diag, pwm_control_verified=True, duty_drift=NO_DRIFT)[0]
    assert healthy_text in build_readiness_report_html(
        diag, pwm_control_verified=True, duty_drift=NO_DRIFT
    )
    assert healthy_text not in build_readiness_report_html(
        diag, pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT
    )


# ── The call sites — an argument threaded into a helper is not an argument ───
# the page passes. This is `CLAUDE.md § Hard-won lessons`' first entry, which
# has now recurred fourteen times: a pure helper with thorough unit tests, and
# nothing asserting that the production path calls it. `SSN-l` IS that defect —
# `detect_readiness_problems` had the argument and two of four callers used it.


def _page(qtbot, *, recorded: str):
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    svc = AppSettingsService()
    svc.update(last_pwm_verify_effective=recorded)
    diag_svc = DiagnosticsService(None)
    diag_svc.set_hw_diagnostics(_diag())
    page = SystemStatePage(diagnostics_service=diag_svc, settings_service=svc)
    qtbot.addWidget(page)
    return page, svc


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


def test_open_full_report_passes_the_recorded_verify_outcome(qtbot):
    """The `Open Full Report` button's call site (`system_state_page:1435`).

    The right-hand side is built from the SETTINGS, which is the one term that
    stays true independently of the call site under test (DEC-334): asserting
    against what the page computed would be satisfied by the defect itself.
    """
    page, _svc = _page(qtbot, recorded="ineffective")
    assert page._pwm_verified() is _DISCRIMINATING  # DEC-356: bind and use

    diag = _diag()
    expected = build_readiness_report_html(
        diag, pwm_control_verified=_DISCRIMINATING, duty_drift=NO_DRIFT
    )
    stale = build_readiness_report_html(diag, pwm_control_verified=None, duty_drift=NO_DRIFT)
    assert expected != stale, "the two derivations must differ, or this asserts nothing"

    page._open_readiness_report()
    assert page._report_dialog is not None
    # Read back what the dialog is RENDERING, not the string handed to it:
    # `QTextBrowser` re-serialises the markup, so the HTML in equals nothing
    # comparable coming out, while the text the user reads survives intact.
    shown = page._report_dialog._browser.toPlainText()
    assert readiness_verdict(diag, pwm_control_verified=True, duty_drift=NO_DRIFT)[0] not in shown
    assert "To fix" in shown
    _flush(page)


def test_a_render_refreshes_an_open_report_with_the_same_flag(qtbot):
    """The second call site (`system_state_page:628`), which repaints a report
    that is already open when a fresh payload arrives.

    Both call sites are asserted because `SSN-l` is precisely the failure mode
    of testing one caller of a threaded argument and assuming the rest.

    **The direction is load-bearing.** The first draft opened on `ineffective`
    and flipped to `effective`, asserting the report went quiet — and it
    **passed with the call site's argument deleted**, because a call site that
    passes nothing evaluates as `None`, and `None` is quiet too (DEC-340: the
    arm where the new lookup finds nothing returns the pre-fix answer by
    construction). Measured, not reasoned about. Flipping the other way is the
    arm that discriminates: only a report built with the flag the page actually
    holds can go LOUD, an answer the pre-fix path could not produce.
    """
    page, svc = _page(qtbot, recorded="effective")
    page._open_readiness_report()
    assert page._report_dialog is not None
    assert page._report_dialog.isVisible(), "the refresh branch is gated on visibility"
    quiet = page._report_dialog._browser.toPlainText()
    assert "To fix" not in quiet, "precondition: the report starts quiet"

    # Record a failed fan-control test and re-render. The report is open, so
    # the refresh branch fires — and it must carry the new outcome.
    svc.update(last_pwm_verify_effective="ineffective")
    assert page._pwm_verified() is _DISCRIMINATING
    page._render(_diag())
    shown = page._report_dialog._browser.toPlainText()
    assert (
        readiness_verdict(_diag(), pwm_control_verified=True, duty_drift=NO_DRIFT)[0] not in shown
    )
    assert "To fix" in shown
    _flush(page)


# ── `SSN-i`'s answer C, pinned so the rename cannot silently regress ─────────


def test_the_two_health_surfaces_do_not_share_a_verdict_word():
    """Answer C in executable form.

    The Hardware page said `READY` and System State said `SYSTEM READY` — two
    different questions a glance apart, in near-identical words. Asserted as
    disjointness rather than against two literals, so the check survives either
    side re-wording and fails only on a genuine collision.
    """
    from control_ofc.services.hardware_view import _VERDICT

    hardware_words = {word for word, _state in _VERDICT.values()}
    diag = _diag()
    system_state_words = {
        build_system_state_vm(diag, pwm_control_verified=v, duty_drift=NO_DRIFT).issue_count_label
        for v in (None, True, _DISCRIMINATING)
    }
    assert system_state_words, "fixture must produce both a healthy and a loud label"
    assert len(system_state_words) > 1, "both arms of the page pill must be sampled"

    for hw in hardware_words:
        for ss in system_state_words:
            assert hw != ss, f"the two surfaces both say {hw!r}"
            assert not (hw in ss or ss in hw), (
                f"one surface's verdict contains the other's: {hw!r} vs {ss!r}"
            )
