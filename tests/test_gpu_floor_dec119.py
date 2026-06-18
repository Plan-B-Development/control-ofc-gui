"""DEC-119 — per-member GPU floor removal in the control loop.

GPU fans must never be floored above 0% by the GUI; the GPU's PMFW firmware
owns its OD_RANGE minimum (~15%). For a GPU-only control this was already the
case (role floor 0). DEC-119 extends it to *mixed* controls: a GPU fan grouped
with chassis/CPU fans now idles to its own 0% floor while the non-GPU members
keep their stall-protection floor.

These tests pin the pure helper (`member_minimum_pct`). The control-loop
write-path tests that exercised this floor were retired at the 2.0
control-migration flip together with ``ControlLoopService``; the equivalent
floor behaviour is now covered by ``test_profile_service.py``,
``test_role_classification_parity.py``, and ``test_aio_phase1.py``.
"""

from __future__ import annotations

from control_ofc.services.profile_service import (
    ControlMember,
    LogicalControl,
    member_minimum_pct,
)

GPU_ID = "amd_gpu:0000:03:00.0"
CHASSIS_ID = "openfan:ch00"
CPU_HEADER_ID = "hwmon:nct6799:0000:pwm1:CPU Fan"


def _gpu_member(member_id: str = GPU_ID) -> ControlMember:
    return ControlMember(source="amd_gpu", member_id=member_id, member_label="9070XT Fan")


def _chassis_member(member_id: str = CHASSIS_ID) -> ControlMember:
    return ControlMember(source="openfan", member_id=member_id, member_label="Chassis")


def _cpu_member(member_id: str = CPU_HEADER_ID) -> ControlMember:
    return ControlMember(source="hwmon", member_id=member_id, member_label="CPU Fan")


# ---------------------------------------------------------------------------
# Pure helper: member_minimum_pct
# ---------------------------------------------------------------------------


class TestMemberMinimumPct:
    def test_gpu_member_floor_is_zero_in_gpu_only_control(self):
        ctrl = LogicalControl(members=[_gpu_member()], minimum_pct=0.0)
        assert member_minimum_pct(ctrl, ctrl.members[0]) == 0.0

    def test_gpu_member_floor_is_zero_in_mixed_chassis_control(self):
        # Mixed control's control-wide floor is 20 (chassis role), but the GPU
        # member is still 0 — the whole point of DEC-119.
        ctrl = LogicalControl(members=[_gpu_member(), _chassis_member()], minimum_pct=20.0)
        gpu, chassis = ctrl.members
        assert member_minimum_pct(ctrl, gpu) == 0.0
        assert member_minimum_pct(ctrl, chassis) == 20.0

    def test_gpu_member_floor_is_zero_in_mixed_cpu_control(self):
        ctrl = LogicalControl(members=[_gpu_member(), _cpu_member()], minimum_pct=30.0)
        gpu, cpu = ctrl.members
        assert member_minimum_pct(ctrl, gpu) == 0.0
        assert member_minimum_pct(ctrl, cpu) == 30.0

    def test_non_gpu_member_honours_control_floor(self):
        # A chassis member in a CPU+chassis control keeps the strict 30 floor —
        # unchanged from pre-DEC-119 (non-GPU paths must not regress).
        ctrl = LogicalControl(members=[_cpu_member(), _chassis_member()], minimum_pct=30.0)
        chassis = ctrl.members[1]
        assert member_minimum_pct(ctrl, chassis) == 30.0

    def test_gpu_floor_zero_even_if_control_floor_misconfigured_high(self):
        # Defensive: a GPU member is 0 regardless of the stored control floor.
        ctrl = LogicalControl(members=[_gpu_member(), _chassis_member()], minimum_pct=55.0)
        assert member_minimum_pct(ctrl, ctrl.members[0]) == 0.0
