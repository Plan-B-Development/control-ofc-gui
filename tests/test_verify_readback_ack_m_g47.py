"""G47 / `ACK-m` — a skipped guard is not a passed guard (DEC-373).

The daemon's hwmon verify classifier reached `rpm_unavailable` whose message is
*"PWM values held but RPM sensor unavailable or too low to verify"*. That claim
rests on the reverted-enable and clamped-value guards having **passed** — but
both are `if let Some(..)`, so a post-write readback that failed *skipped* them
instead, and the verdict inherited a pass nothing had established.

Daemon >= 2.48.0 returns a sixth token, `pwm_readback_unavailable`, for exactly
that case. These are the GUI half: the vocabulary row, the advice, and the two
places a new token could quietly change a verdict it must not change.

Written as relationships wherever a literal would also be satisfied by the
defect (`CLAUDE.md § Hard-won lessons`).
"""

from __future__ import annotations

from control_ofc.api.models import HwmonVerifyResult
from control_ofc.services.verify_view import (
    _OUTCOMES,
    PWM_EVIDENCE_INCONCLUSIVE,
    PWM_EVIDENCE_INEFFECTIVE,
    VERDICT_WARN,
    build_verify_result_view,
    outcome_for,
    verify_sweep_chip_class,
    verify_sweep_outcome,
)
from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance

TOKEN = "pwm_readback_unavailable"


# ── the vocabulary row ───────────────────────────────────────────────────


def test_the_new_token_is_known_rather_than_falling_to_the_unknown_arm():
    """The whole reason the GUI half exists.

    Without a row the token still *renders* — 273-i's fallback sees to that —
    so the discriminating assertion is the absence of the version-gap hint, not
    the presence of text. A GUI that merely didn't crash would pass a test that
    only checked the token survived.
    """
    assert TOKEN in _OUTCOMES
    assert "does not recognise" not in outcome_for(TOKEN).summary, (
        "a token this GUI ships a row for must not be reported as a version gap"
    )


def test_the_new_token_tells_the_user_the_opposite_thing_from_rpm_unavailable():
    """The split, asserted as the disagreement itself.

    These two tokens are deliberately identical in every presentation column —
    both neutral, both `CHECK`, both inconclusive — so a test that compared
    those would pass with the row deleted and the fallback answering instead.
    What must differ is the *sentence*, which is the only reason a sixth token
    was minted rather than a flag: `rpm_unavailable` claims the write was
    accepted, and here nothing established that.

    Resolved through `_OUTCOMES` directly rather than `outcome_for`, and the
    version-gap hint is asserted absent. Both are load-bearing: measured, every
    column below is *also* satisfied by the unrecognised-token fallback, so
    without them this test passes with the row deleted (`CLAUDE.md § Hard-won
    lessons`, DEC-340).
    """
    new, old = _OUTCOMES[TOKEN], outcome_for("rpm_unavailable")

    assert "does not recognise" not in new.summary
    assert new.summary != old.summary
    assert "accepted" not in new.summary.lower(), (
        f"the new token must not inherit the claim it exists to retract: {new.summary!r}"
    )

    # ...and the columns that must AGREE, or "different" is being satisfied by
    # a row that simply drifted — e.g. one painted as a hardware fault.
    assert new.verdict == old.verdict == VERDICT_WARN
    assert new.chip_class == old.chip_class
    assert new.evidence == old.evidence == PWM_EVIDENCE_INCONCLUSIVE


def test_a_failed_readback_is_inconclusive_and_never_condemns_a_sweep():
    """DEC-358's rule, applied to the new token before it can break it.

    A sweep of one empty-tach header and one failed readback has demonstrated
    nothing, and must leave a previously recorded verdict alone. If the new row
    were `ineffective` it would mint the unclearable alarm DEC-358 removed —
    from a transient sysfs read error, which is not a finding about the board.

    `_OUTCOMES[TOKEN]` rather than `outcome_for(TOKEN)` for DEC-340's reason: the
    fallback arm is inconclusive too, so reading through it would satisfy every
    assertion here with the row deleted — measured.
    """
    assert _OUTCOMES[TOKEN].evidence == PWM_EVIDENCE_INCONCLUSIVE
    assert _OUTCOMES[TOKEN].evidence != PWM_EVIDENCE_INEFFECTIVE
    assert verify_sweep_outcome([TOKEN, "rpm_unavailable"]) is None
    assert verify_sweep_chip_class([TOKEN, "rpm_unavailable"]) == "CardMeta"

    # The discriminating arm: it must not SUPPRESS a real finding either.
    assert verify_sweep_outcome([TOKEN, "pwm_enable_reverted"]) == PWM_EVIDENCE_INEFFECTIVE
    assert verify_sweep_chip_class([TOKEN, "pwm_enable_reverted"]) == "CriticalChip"


# ── the advice ───────────────────────────────────────────────────────────


def test_the_advice_is_re_run_rather_than_listen_to_the_fan():
    """The two tokens need different next steps, which is the user-visible half.

    `rpm_unavailable` means the write was accepted and only the confirmation is
    missing, so "listen for fan speed changes" closes it. Here nothing about the
    write was established, so that advice would be actively wrong — it asks the
    user to confirm an effect on the strength of a write we did not confirm.
    """
    advice = verification_guidance(TOKEN, "Gigabyte", "it8696")
    assert advice, "a token with a vocabulary row must not fall through to None"
    assert advice != verification_guidance("rpm_unavailable", "Gigabyte", "it8696")
    assert "re-run" in advice.lower()
    assert "listen" not in advice.lower()


def test_a_failed_readback_never_draws_the_dual_chip_hint():
    """A hint about *missing headers* must not fire on a *failed read*.

    The gate is `result not in ("pwm_value_clamped", "no_rpm_effect")`, so this
    holds by construction today — which is exactly why it is pinned: the next
    person to widen that tuple is the one this test is for. Asserted against a
    board that WOULD draw the hint, or it passes for the wrong reason.
    """
    expected, detected = ["it8696", "it87952"], ["it8696"]
    assert dual_chip_verify_hint("pwm_value_clamped", expected, detected), (
        "precondition: this board must draw the hint, or the negative below is vacuous"
    )
    assert dual_chip_verify_hint(TOKEN, expected, detected) is None


# ── the realised artefact ────────────────────────────────────────────────


def test_the_rendered_result_carries_the_daemon_details_and_one_prefix():
    """What the user actually reads, assembled end to end.

    The daemon's `details` is the only place the *reason* for the failed
    readback appears (which file, and that it is not evidence of a fault), so a
    row that rendered only the summary would drop the informative half.
    """
    details = (
        "pwmN could not be read back after the 6s test window, so whether the "
        "written duty held is unknown. This is NOT evidence that the write failed."
    )
    view = build_verify_result_view(HwmonVerifyResult(header_id="h", result=TOKEN, details=details))

    assert view.lines[0] == f"Result: {outcome_for(TOKEN).summary}"
    assert not view.lines[0][len("Result: ") :].startswith("Result: ")
    assert details in view.text
    assert view.chip_class == outcome_for(TOKEN).chip_class
    assert view.verdict == outcome_for(TOKEN).verdict
    assert "Next step:" in view.text
