"""DEC-119 — per-member GPU floor removal in the control loop.

GPU fans must never be floored above 0% by the GUI; the GPU's PMFW firmware
owns its OD_RANGE minimum (~15%). For a GPU-only control this was already the
case (role floor 0). DEC-119 extends it to *mixed* controls: a GPU fan grouped
with chassis/CPU fans now idles to its own 0% floor while the non-GPU members
keep their stall-protection floor.

These tests pin both the pure helper (`member_minimum_pct`) and the observable
write-path behaviour (a mixed manual control writes different PWMs to its GPU
and chassis members in the same cycle).
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    ConnectionState,
    FanReading,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    LogicalControl,
    Profile,
    ProfileService,
    member_minimum_pct,
)
from tests.conftest import FakeDaemonClient

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


# ---------------------------------------------------------------------------
# Control-loop write path: mixed control writes per-member outputs
# ---------------------------------------------------------------------------


@pytest.fixture()
def state(qtbot):
    s = AppState()
    s.connection = ConnectionState.CONNECTED
    s.mode = OperationMode.AUTOMATIC
    s.sensors = [
        SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=40.0, age_ms=200),
    ]
    s.fans = [
        FanReading(id=GPU_ID, source="amd_gpu", rpm=0, last_commanded_pwm=None, age_ms=200),
        FanReading(id=CHASSIS_ID, source="openfan", rpm=600, last_commanded_pwm=None, age_ms=200),
    ]
    return s


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


def _gpu_calls(client):
    return [(a, k) for m, a, k in client.calls if m == "set_gpu_fan_speed"]


def _openfan_calls(client):
    return [(a, k) for m, a, k in client.calls if m == "set_openfan_pwm"]


def _activate(profile_service, profile):
    profile_service._profiles[profile.id] = profile
    profile_service.set_active(profile.id)


def _mixed_manual_profile(manual_pct: float, minimum_pct: float = 20.0) -> Profile:
    control = LogicalControl(
        id="mixed",
        name="GPU + Chassis",
        mode=ControlMode.MANUAL,
        manual_output_pct=manual_pct,
        members=[_gpu_member(), _chassis_member()],
        minimum_pct=minimum_pct,
    )
    return Profile(id="p", name="P", controls=[control], curves=[])


class TestMixedControlPerMemberFloor:
    def test_gpu_idles_below_floor_while_chassis_is_floored(self, state, profile_service, qtbot):
        """Manual 10% on a mixed control: GPU → 10%, chassis → floored 20%."""
        client = FakeDaemonClient()
        _activate(profile_service, _mixed_manual_profile(manual_pct=10.0))
        loop = ControlLoopService(state, profile_service, client=client)

        loop._cycle()

        gpu_calls = _gpu_calls(client)
        of_calls = _openfan_calls(client)
        assert len(gpu_calls) == 1
        assert len(of_calls) == 1
        # GPU member followed the manual value all the way down.
        assert gpu_calls[0][0] == ("0000:03:00.0", 10)
        # Chassis member was floored at the control's 20% role minimum.
        assert of_calls[0][0] == (0, 20)

    def test_gpu_reaches_zero_in_mixed_control(self, state, profile_service, qtbot):
        """Manual 0% → GPU written 0%, chassis still floored at 20%."""
        client = FakeDaemonClient()
        _activate(profile_service, _mixed_manual_profile(manual_pct=0.0))
        loop = ControlLoopService(state, profile_service, client=client)

        loop._cycle()

        assert _gpu_calls(client)[0][0] == ("0000:03:00.0", 0)
        assert _openfan_calls(client)[0][0] == (0, 20)

    def test_non_gpu_members_unchanged_when_above_floor(self, state, profile_service, qtbot):
        """Manual 50% (above floor): both members get 50% — no per-member split."""
        client = FakeDaemonClient()
        _activate(profile_service, _mixed_manual_profile(manual_pct=50.0))
        loop = ControlLoopService(state, profile_service, client=client)

        loop._cycle()

        assert _gpu_calls(client)[0][0] == ("0000:03:00.0", 50)
        assert _openfan_calls(client)[0][0] == (0, 50)

    def test_gpu_only_control_reaches_zero(self, state, profile_service, qtbot):
        """A GPU-only control (floor 0) writes 0% — the pre-DEC-119 baseline."""
        client = FakeDaemonClient()
        control = LogicalControl(
            id="gpu_only",
            name="GPU",
            mode=ControlMode.MANUAL,
            manual_output_pct=0.0,
            members=[_gpu_member()],
            minimum_pct=0.0,
        )
        _activate(profile_service, Profile(id="p", name="P", controls=[control], curves=[]))
        loop = ControlLoopService(state, profile_service, client=client)

        loop._cycle()

        assert _gpu_calls(client)[0][0] == ("0000:03:00.0", 0)

    def test_per_member_step_state_is_independent(self, state, profile_service, qtbot):
        """The GPU branch keeps its own step-rate tracker, namespaced so it
        never collides with the control-wide key."""
        client = FakeDaemonClient()
        _activate(profile_service, _mixed_manual_profile(manual_pct=10.0))
        loop = ControlLoopService(state, profile_service, client=client)

        loop._cycle()

        # Control-wide key tracks the chassis (floored) trajectory at 20…
        assert loop._target_states["mixed"].last_output == 20.0
        # …and the GPU member has its own key at the un-floored 10.
        gpu_key = loop._member_state_key("mixed", GPU_ID)
        assert gpu_key in loop._target_states
        assert loop._target_states[gpu_key].last_output == 10.0
