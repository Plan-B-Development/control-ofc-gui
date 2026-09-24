"""G99 / `TS-aw` — a verify stopped because the header became a pump (DEC-418).

Daemon verify decides the header's pump floor when the test is planned. Since
DEC-418 it re-reads the pump-protection union during the settle, and when a
profile naming the header a pump is activated — or the header is assigned the
pump role — mid-test, it stops the test, floors the restore at the pump floor
and returns a seventh `result` token, `pump_protected_mid_run`.

These are the GUI half: the vocabulary row, the report finding, the advice, and
the places a new token could quietly change a verdict it must not change.
Resolved through `_OUTCOMES` rather than `outcome_for` wherever the unknown-token
fallback would satisfy the assertion too (DEC-340).
"""

from __future__ import annotations

from control_ofc.api.models import HwmonVerifyResult, HwmonVerifyState
from control_ofc.services.verify_view import (
    _OUTCOMES,
    PWM_EVIDENCE_INCONCLUSIVE,
    PWM_EVIDENCE_INEFFECTIVE,
    build_verify_result_view,
    outcome_for,
    verify_sweep_chip_class,
    verify_sweep_outcome,
)
from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance

TOKEN = "pump_protected_mid_run"


def test_the_token_is_known_rather_than_falling_to_the_unknown_arm():
    """Without a row the token still renders (273-i), so the discriminating
    assertion is the absence of the version-gap hint."""
    assert TOKEN in _OUTCOMES
    assert "does not recognise" not in outcome_for(TOKEN).summary


def test_it_is_neutral_and_inconclusive_like_the_other_unmeasured_results():
    """Nothing was measured, so it must look exactly as loud as the other
    token for an unmeasured test — and say something different, or it is a
    duplicate row."""
    new, peer = _OUTCOMES[TOKEN], _OUTCOMES["pwm_readback_unavailable"]
    assert (new.chip_class, new.evidence) == (peer.chip_class, peer.evidence)
    assert new.evidence == PWM_EVIDENCE_INCONCLUSIVE
    assert new.summary != peer.summary
    assert "pump" in new.summary.lower()


def test_a_stopped_verify_never_condemns_a_sweep_nor_suppresses_a_real_finding():
    """DEC-358's rule: an inconclusive result leaves a recorded verdict alone.
    The opposite arm: it must not hide a real finding beside it either."""
    assert verify_sweep_outcome([TOKEN, "rpm_unavailable"]) is None
    assert verify_sweep_chip_class([TOKEN, "rpm_unavailable"]) == "CardMeta"
    assert verify_sweep_outcome([TOKEN, "pwm_enable_reverted"]) == PWM_EVIDENCE_INEFFECTIVE
    assert verify_sweep_chip_class([TOKEN, "pwm_enable_reverted"]) == "CriticalChip"


def test_the_advice_is_to_re_run_and_names_why():
    advice = verification_guidance(TOKEN, "Gigabyte", "it8696")
    assert advice, "a token with a vocabulary row must not fall through to None"
    assert advice != verification_guidance("pwm_readback_unavailable", "Gigabyte", "it8696")
    assert "re-run" in advice.lower()
    assert "pump" in advice.lower()
    # Not a board fault: none of the BIOS/driver remedies belongs here.
    assert "bios" not in advice.lower()


def test_a_stopped_verify_never_draws_the_dual_chip_hint():
    expected, detected = ["it8696", "it87952"], ["it8696"]
    assert dual_chip_verify_hint("pwm_value_clamped", expected, detected), (
        "precondition: this board must draw the hint, or the negative below is vacuous"
    )
    assert dual_chip_verify_hint(TOKEN, expected, detected) is None


def test_the_sentence_makes_no_claim_about_the_restore():
    """DEC-418 review `C1`: the daemon can return this token with
    `restore_failed: true`, and no hwmon verify surface renders that field, so a
    sentence claiming the header was restored would be the only restore claim
    on screen — false exactly when it matters."""
    assert "restor" not in _OUTCOMES[TOKEN].summary.lower()


def test_a_cut_short_settle_shows_no_rpm_change_but_a_completed_one_does():
    """DEC-418 review `C2`: the before/after RPM of a stopped verify is not a
    measurement. The opposite arm: a completed verify keeps its RPM line."""
    stopped = build_verify_result_view(
        HwmonVerifyResult(
            header_id="h",
            result=TOKEN,
            initial_state=HwmonVerifyState(rpm=1200),
            final_state=HwmonVerifyState(rpm=900),
        )
    )
    assert "RPM:" not in stopped.text
    completed = build_verify_result_view(
        HwmonVerifyResult(
            header_id="h",
            result="effective",
            initial_state=HwmonVerifyState(rpm=1200),
            final_state=HwmonVerifyState(rpm=900),
        )
    )
    assert "RPM: 1200 → 900" in completed.text


def test_the_rendered_result_carries_the_daemon_details_and_one_prefix():
    """The daemon's `details` is where the floor figure it applied appears, so a
    row that rendered only the summary would drop it."""
    details = (
        "the header became pump-protected during the verify (a profile naming it a pump "
        "was activated, or it was assigned the pump role), so the verify stopped and its "
        "restore is floored at the 30% pump floor."
    )
    view = build_verify_result_view(HwmonVerifyResult(header_id="h", result=TOKEN, details=details))
    assert view.lines[0] == f"Result: {_OUTCOMES[TOKEN].summary}"
    assert details in view.text
    assert view.chip_class == _OUTCOMES[TOKEN].chip_class
    assert "Next step:" in view.text
