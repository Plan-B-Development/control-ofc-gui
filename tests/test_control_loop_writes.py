"""Phase 3 — Control loop → daemon write integration tests.

Verifies that _cycle() correctly dispatches PWM writes to the daemon API
via FakeDaemonClient. Covers: OpenFan writes, hwmon lease-gated writes,
channel parsing, PWM rounding, write suppression, error handling,
multi-member controls, signal emission, and status counters.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError
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
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    Profile,
    ProfileService,
)
from tests.conftest import FakeDaemonClient

# ---------------------------------------------------------------------------
# Helpers (shared with test_control_loop.py pattern)
# ---------------------------------------------------------------------------


def _make_profile(
    temp_points,
    sensor_id="cpu_temp",
    target_id="openfan:ch00",
    curve_type=CurveType.GRAPH,
    members=None,
):
    """Create a profile with one logical control and curve."""
    points = [CurvePoint(t, p) for t, p in temp_points]
    curve = CurveConfig(
        id="test_curve",
        name="Test Curve",
        type=curve_type,
        sensor_id=sensor_id,
        points=points,
    )
    source = "openfan" if target_id.startswith("openfan") else "hwmon"
    if members is None:
        members = [ControlMember(source=source, member_id=target_id)]
    control = LogicalControl(
        id="test_control",
        name="Test Control",
        mode=ControlMode.CURVE,
        curve_id="test_curve",
        members=members,
    )
    return Profile(id="test", name="Test", controls=[control], curves=[curve])


def _openfan_calls(client):
    """Extract set_openfan_pwm calls from FakeDaemonClient."""
    return [(a, k) for m, a, k in client.calls if m == "set_openfan_pwm"]


def _hwmon_calls(client):
    """Extract set_hwmon_pwm calls from FakeDaemonClient."""
    return [(a, k) for m, a, k in client.calls if m == "set_hwmon_pwm"]


def _write_calls(client):
    """Extract all write-type calls."""
    write_methods = {"set_openfan_pwm", "set_hwmon_pwm", "set_openfan_all_pwm"}
    return [(m, a, k) for m, a, k in client.calls if m in write_methods]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state(qtbot):
    s = AppState()
    s.connection = ConnectionState.CONNECTED
    s.mode = OperationMode.AUTOMATIC
    s.sensors = [
        SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=50.0, age_ms=500),
    ]
    s.fans = [
        FanReading(
            id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
        ),
    ]
    return s


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def fake_client():
    return FakeDaemonClient()


@pytest.fixture()
def fake_lease():
    mock = MagicMock()
    mock.is_held = True
    mock.lease_id = "test-lease"
    return mock


@pytest.fixture()
def fake_lease_not_held():
    mock = MagicMock()
    mock.is_held = False
    mock.lease_id = None
    mock.acquire = MagicMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# A. OpenFan write path
# ---------------------------------------------------------------------------


class TestOpenfanWritePath:
    """Verify set_openfan_pwm is called with correct channel and PWM."""

    def test_cycle_calls_set_openfan_pwm(self, state, profile_service, fake_client, qtbot):
        """50°C on (30→20, 70→80) curve → set_openfan_pwm(0, 50)."""
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        calls = _openfan_calls(fake_client)
        assert len(calls) == 1
        args, _kw = calls[0]
        assert args == (0, 50)  # channel=0, pwm=50%

    def test_channel_parsed_from_target_id(self, state, profile_service, fake_client, qtbot):
        """openfan:ch03 → channel=3 extracted correctly."""
        state.fans = [
            FanReading(
                id="openfan:ch03", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="openfan:ch03")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        calls = _openfan_calls(fake_client)
        assert len(calls) == 1
        assert calls[0][0][0] == 3  # first positional arg = channel

    def test_pwm_rounded_to_int(self, state, profile_service, fake_client, qtbot):
        """Graph curve producing 51.5% → set_openfan_pwm(_, 52) after round()."""
        # At 51°C on (30→20, 70→80): 20 + (51-30)/(70-30)*60 = 51.5
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=51.0, age_ms=500),
        ]
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        calls = _openfan_calls(fake_client)
        assert len(calls) == 1
        assert calls[0][0][1] == 52  # round(51.5) = 52

    def test_suppressed_write_no_call(self, state, profile_service, fake_client, qtbot):
        """PWM delta < 1% → no client call."""
        state.fans = [
            FanReading(
                id="openfan:ch00",
                source="openfan",
                rpm=800,
                last_commanded_pwm=50,  # current = 50%
                age_ms=500,
            ),
        ]
        # Curve evaluates to exactly 50% at 50°C on (30→20, 70→80)
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        # Delta is 0% — should be suppressed
        calls = _openfan_calls(fake_client)
        assert len(calls) == 0

    def test_multi_member_writes_each(self, state, profile_service, fake_client, qtbot):
        """Control with 2 members → 2 set_openfan_pwm calls."""
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
            FanReading(
                id="openfan:ch01", source="openfan", rpm=900, last_commanded_pwm=None, age_ms=500
            ),
        ]
        members = [
            ControlMember(source="openfan", member_id="openfan:ch00"),
            ControlMember(source="openfan", member_id="openfan:ch01"),
        ]
        profile = _make_profile([(30, 20), (70, 80)], members=members)
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        calls = _openfan_calls(fake_client)
        assert len(calls) == 2
        channels = sorted(c[0][0] for c in calls)
        assert channels == [0, 1]

    def test_malformed_channel_id_handled(self, state, profile_service, fake_client, qtbot):
        """Malformed openfan target ID is skipped without crashing."""
        state.fans = [
            FanReading(
                id="openfan:channel01",
                source="openfan",
                rpm=800,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="openfan:channel01")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()  # must not raise

        assert len(_openfan_calls(fake_client)) == 0


# ---------------------------------------------------------------------------
# B. Hwmon write path (lease-gated)
# ---------------------------------------------------------------------------


class TestHwmonWritePath:
    """Verify set_hwmon_pwm is called with correct header_id and lease_id."""

    def test_hwmon_write_with_lease(self, state, profile_service, fake_client, fake_lease, qtbot):
        """With lease held, set_hwmon_pwm called with correct args."""
        state.fans = [
            FanReading(
                id="hwmon:nct6775:pwm1",
                source="hwmon",
                rpm=900,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="hwmon:nct6775:pwm1")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(
            state, profile_service, client=fake_client, lease_service=fake_lease
        )
        loop._cycle()

        calls = _hwmon_calls(fake_client)
        assert len(calls) == 1
        args, _kw = calls[0]
        assert args[0] == "hwmon:nct6775:pwm1"  # header_id
        assert args[1] == 50  # pwm
        assert args[2] == "test-lease"  # lease_id

    def test_hwmon_write_without_lease_skipped(
        self, state, profile_service, fake_client, fake_lease_not_held, qtbot
    ):
        """Without lease, set_hwmon_pwm is NOT called."""
        state.fans = [
            FanReading(
                id="hwmon:nct6775:pwm1",
                source="hwmon",
                rpm=900,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="hwmon:nct6775:pwm1")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(
            state, profile_service, client=fake_client, lease_service=fake_lease_not_held
        )
        loop._cycle()

        calls = _hwmon_calls(fake_client)
        assert len(calls) == 0

    def test_hwmon_lease_id_passed_correctly(self, state, profile_service, fake_client, qtbot):
        """Lease ID string passed through exactly."""
        lease = MagicMock()
        lease.is_held = True
        lease.lease_id = "unique-lease-abc-123"

        state.fans = [
            FanReading(
                id="hwmon:nct6775:pwm1",
                source="hwmon",
                rpm=900,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="hwmon:nct6775:pwm1")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client, lease_service=lease)
        loop._cycle()

        calls = _hwmon_calls(fake_client)
        assert len(calls) == 1
        assert calls[0][0][2] == "unique-lease-abc-123"

    def test_unknown_target_skipped(self, state, profile_service, fake_client, qtbot):
        """Unknown target format → no write calls."""
        state.fans = [
            FanReading(
                id="unknown:device", source="unknown", rpm=0, last_commanded_pwm=None, age_ms=500
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="unknown:device")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        assert len(_write_calls(fake_client)) == 0


# ---------------------------------------------------------------------------
# C. Write failure handling
# ---------------------------------------------------------------------------


class TestWriteFailureHandling:
    """Verify errors are caught, logged, and don't crash the loop."""

    def test_daemon_error_caught(self, state, profile_service, fake_client, qtbot):
        """DaemonError on write → loop continues, no crash."""
        fake_client.simulate_error(
            "set_openfan_pwm",
            DaemonError(
                code="hardware_error",
                message="serial timeout",
                retryable=True,
                source="serial",
                status=500,
            ),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        # Must not raise
        loop._cycle()

    def test_no_client_skips_write(self, state, profile_service, qtbot):
        """client=None → write skipped, no crash."""
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=None)
        loop._cycle()  # must not raise

    def test_error_on_first_member_writes_second(self, state, profile_service, fake_client, qtbot):
        """Error on ch00 does not prevent ch01 from being written."""
        fake_client.simulate_error(
            "set_openfan_pwm",
            DaemonError(
                code="hardware_error",
                message="serial timeout",
                retryable=True,
                source="serial",
                status=500,
            ),
        )
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
            FanReading(
                id="openfan:ch01", source="openfan", rpm=900, last_commanded_pwm=None, age_ms=500
            ),
        ]
        members = [
            ControlMember(source="openfan", member_id="openfan:ch00"),
            ControlMember(source="openfan", member_id="openfan:ch01"),
        ]
        profile = _make_profile([(30, 20), (70, 80)], members=members)
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        loop._cycle()

        # The error is raised for EVERY call to set_openfan_pwm, so both fail.
        # But the loop must not crash — it catches DaemonError per-member.
        # Both members attempted (both recorded as calls even though they raise)
        all_calls = [(m, a, k) for m, a, k in fake_client.calls if m == "set_openfan_pwm"]
        assert len(all_calls) == 2  # both members attempted


# ---------------------------------------------------------------------------
# D. Status counters
# ---------------------------------------------------------------------------


class TestStatusCounters:
    """Verify targets_active and targets_skipped are correctly tracked."""

    def test_active_on_successful_write(self, state, profile_service, fake_client, qtbot):
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()

        assert len(statuses) == 1
        assert statuses[0].targets_active >= 1

    def test_skipped_on_failure(self, state, profile_service, fake_client, qtbot):
        fake_client.simulate_error(
            "set_openfan_pwm",
            DaemonError(code="hw", message="fail", retryable=False, source="serial", status=500),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()

        assert len(statuses) == 1
        assert statuses[0].targets_skipped >= 1

    def test_suppressed_counts_as_active(self, state, profile_service, fake_client, qtbot):
        """Write suppressed (delta < 1%) → still counts as active, zero client calls."""
        state.fans = [
            FanReading(
                id="openfan:ch00",
                source="openfan",
                rpm=800,
                last_commanded_pwm=50,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()

        assert len(statuses) == 1
        assert statuses[0].targets_active >= 1
        assert len(_openfan_calls(fake_client)) == 0


# ---------------------------------------------------------------------------
# E. DaemonUnavailable and failure accumulation
# ---------------------------------------------------------------------------


class TestDaemonUnavailableHandling:
    """Verify DaemonUnavailable (subclass of DaemonError) is caught properly."""

    def test_daemon_unavailable_caught_in_write(self, state, profile_service, fake_client, qtbot):
        """DaemonUnavailable on write → loop continues, targets_skipped incremented."""
        from control_ofc.api.errors import DaemonUnavailable

        fake_client.simulate_error("set_openfan_pwm", DaemonUnavailable())
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()  # must not raise

        assert len(statuses) == 1
        assert statuses[0].targets_skipped >= 1

    def test_write_failure_accumulation_triggers_warning(
        self, state, profile_service, fake_client, qtbot
    ):
        """3 consecutive write failures on same target → state warning added."""
        fake_client.simulate_persistent_error(
            "set_openfan_pwm",
            DaemonError(code="hw", message="fail", retryable=True, source="serial", status=500),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        # Run 3 cycles — each fails, counter increments
        loop._cycle()
        loop._cycle()
        loop._cycle()

        # After 3 failures, ad-hoc warning should be added to state
        ext = [w for w in state._external_warnings if "openfan:ch00" in w.get("message", "")]
        assert len(ext) == 1
        assert "3 times" in ext[0]["message"]

    def test_hwmon_write_with_stale_lease_handled(
        self, state, profile_service, fake_client, fake_lease, qtbot
    ):
        """Daemon rejects stale lease_id → error caught, failure counted."""
        fake_client.simulate_error(
            "set_hwmon_pwm",
            DaemonError(
                code="lease_not_found",
                message="lease expired",
                retryable=False,
                source="validation",
                status=403,
            ),
        )
        state.fans = [
            FanReading(
                id="hwmon:it8696:pwm1:CHA_FAN1",
                source="hwmon",
                rpm=1200,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="hwmon:it8696:pwm1:CHA_FAN1")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(
            state, profile_service, client=fake_client, lease_service=fake_lease
        )

        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()  # must not raise

        assert statuses[0].targets_skipped >= 1


# ---------------------------------------------------------------------------
# F. Write failure recovery (T5 audit finding)
# ---------------------------------------------------------------------------


class TestWriteFailureRecovery:
    """Verify failure count recovers after successful writes."""

    def test_write_failure_recovery_clears_warning_after_successes(
        self, state, profile_service, fake_client, qtbot
    ):
        """After 3 failures trigger a warning, 3 successes clear it."""
        fake_client.simulate_persistent_error(
            "set_openfan_pwm",
            DaemonError(code="hw", message="fail", retryable=True, source="serial", status=500),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        # 3 failures → warning added
        loop._cycle()
        loop._cycle()
        loop._cycle()
        ext = [w for w in state._external_warnings if "openfan:ch00" in w.get("message", "")]
        assert len(ext) == 1, "expected warning after 3 failures"

        # Clear error injection and run 3 successful cycles
        fake_client.clear_errors()
        # Reset last_commanded_pwm so writes aren't suppressed
        state.fans[0].last_commanded_pwm = None
        loop._cycle()
        state.fans[0].last_commanded_pwm = None
        loop._cycle()
        state.fans[0].last_commanded_pwm = None
        loop._cycle()

        # Warning should be cleared (count decremented below 3)
        ext = [w for w in state._external_warnings if "openfan:ch00" in w.get("message", "")]
        assert len(ext) == 0, "warning should be cleared after 3 successes"

    def test_hwmon_write_attempts_lease_acquire_when_not_held(
        self, state, profile_service, fake_client, fake_lease_not_held, qtbot
    ):
        """When hwmon lease is not held, _write_target attempts acquire() before skipping."""
        state.fans = [
            FanReading(
                id="hwmon:it8696:pwm1:CHA_FAN1",
                source="hwmon",
                rpm=1200,
                last_commanded_pwm=None,
                age_ms=500,
            ),
        ]
        profile = _make_profile([(30, 20), (70, 80)], target_id="hwmon:it8696:pwm1:CHA_FAN1")
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(
            state, profile_service, client=fake_client, lease_service=fake_lease_not_held
        )
        loop._cycle()

        # acquire() was called because is_held was False
        fake_lease_not_held.acquire.assert_called()
        # Write was skipped (no set_hwmon_pwm calls)
        hwmon_calls = [(m, a, k) for m, a, k in fake_client.calls if m == "set_hwmon_pwm"]
        assert len(hwmon_calls) == 0


# ---------------------------------------------------------------------------
# G. Outcome state assertions (T7 audit finding)
# ---------------------------------------------------------------------------


class TestOutcomeState:
    """Verify _write_failure_counts state reflects write outcomes."""

    def test_successful_sync_write_decrements_failure_count(
        self, state, profile_service, fake_client, qtbot
    ):
        """After a successful write, pre-existing failure count is decremented."""
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)
        # Pre-seed failure count
        loop._write_failure_counts["openfan:ch00"] = 2

        loop._cycle()

        # Success decrements count from 2 → 1
        assert loop._write_failure_counts.get("openfan:ch00", 0) == 1

    def test_failed_sync_write_increments_failure_count(
        self, state, profile_service, fake_client, qtbot
    ):
        """Failed writes increment the failure counter for that target."""
        fake_client.simulate_persistent_error(
            "set_openfan_pwm",
            DaemonError(code="hw", message="fail", retryable=True, source="serial", status=500),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service, client=fake_client)

        loop._cycle()
        assert loop._write_failure_counts.get("openfan:ch00", 0) == 1

        state.fans[0].last_commanded_pwm = None  # prevent write suppression
        loop._cycle()
        assert loop._write_failure_counts.get("openfan:ch00", 0) == 2
