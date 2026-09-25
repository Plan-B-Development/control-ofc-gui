"""DEC-422: the AMD GPU advisory a current daemon raises, and the two it retired.

The daemon now raises one advisory, drm/amd #4765 (the MES eviction hang on
RDNA3/RDNA4, on 6.17.9-6.17.13 and 6.18.0-6.18.6), under a new id. It retired
`rdna_hang_kernel_6_18_6_19` (it sent users to 6.15-6.17, never longterm, and
6.17.9 onward carries this very bug) and `smu_mismatch_navi48_r9700` (a benign
message treated as a fault). The GUI keeps guidance for the retired ids because
older daemons still emit them.
"""

from __future__ import annotations

from control_ofc.ui.hwmon_guidance import AMD_GPU_GUIDANCE_DB, lookup_amd_gpu_guidance

# The daemon's `hwmon::kernel_warnings::MES_HANG_4765_ID`. The two repos share
# no code, so the id is restated here; `docs/08` names it for both sides.
CURRENT_ID = "rdna_mes_hang_drm_amd_4765"
RETIRED_IDS = ("rdna_hang_kernel_6_18_6_19", "smu_mismatch_navi48_r9700")


def _flat(warning_id: str) -> str:
    g = lookup_amd_gpu_guidance(warning_id)
    assert g is not None, f"no guidance entry for {warning_id}"
    return " ".join([g.summary, *g.details])


def test_the_current_advisory_has_guidance_with_the_fixed_and_affected_releases():
    text = _flat(CURRENT_ID)
    assert "#4765" in text
    assert "6.18.7" in text and "6.19" in text, "the fixed releases"
    assert "6.17.9" in text, "the 6.17 backport is affected too"
    g = lookup_amd_gpu_guidance(CURRENT_ID)
    assert g is not None
    assert any("issues/4765" in ref for ref in g.references)


def test_the_advice_never_sends_users_to_a_kernel_that_carries_the_bug():
    text = _flat(CURRENT_ID)
    # The retired rule's advice was to pin 6.15-6.17. 6.17.9 onward carries this
    # hang, and none of the three was ever a longterm kernel.
    assert "Do NOT move to 6.15, 6.16 or 6.17" in text
    assert "pin to" not in text.lower()


def test_retired_ids_keep_their_guidance_for_older_daemons():
    for warning_id in RETIRED_IDS:
        assert lookup_amd_gpu_guidance(warning_id) is not None, warning_id


def test_each_advisory_id_has_exactly_one_entry():
    ids = [g.warning_id for g in AMD_GPU_GUIDANCE_DB]
    assert len(ids) == len(set(ids)), ids
    # Presence first, so the uniqueness check cannot pass on an empty table.
    assert CURRENT_ID in ids
