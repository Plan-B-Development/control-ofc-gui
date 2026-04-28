"""Tests for fan_presence.classify_fan_presence (A2).

Drives the classifier against synthetic ``(FanReading, HwmonHeader)`` pairs
covering every state. Hardware-facing logic uses fixtures only — no live
sysfs, no daemon.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    PRESENCE_TOOLTIP,
    FanPresence,
    classify_fan_presence,
)


def _hwmon_fan(fan_id: str, rpm: int | None, last_pwm: int | None = None) -> FanReading:
    return FanReading(
        id=fan_id,
        source="hwmon",
        rpm=rpm,
        last_commanded_pwm=last_pwm,
        age_ms=500,
    )


def _header(
    header_id: str,
    *,
    is_writable: bool = True,
    rpm_available: bool = True,
    chip: str = "it8696",
) -> HwmonHeader:
    return HwmonHeader(
        id=header_id,
        label="",
        chip_name=chip,
        device_id="it87.2624",
        pwm_index=1,
        supports_enable=True,
        rpm_available=rpm_available,
        is_writable=is_writable,
    )


class TestPresent:
    def test_spinning_fan_with_writable_header(self) -> None:
        fan = _hwmon_fan("hwmon:it8696:it87.2624:pwm1:pwm1", rpm=994)
        header = _header("hwmon:it8696:it87.2624:pwm1:pwm1")
        assert classify_fan_presence(fan, header) == FanPresence.PRESENT

    def test_spinning_fan_no_header(self) -> None:
        """OpenFan/GPU fans don't have hwmon headers — PRESENT is still
        returnable purely from RPM evidence."""
        fan = FanReading(
            id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
        )
        assert classify_fan_presence(fan, None) == FanPresence.PRESENT

    def test_pwm_only_actively_driven_is_present(self) -> None:
        """A PWM-only header with no tachometer but active commanded PWM
        is PRESENT for display — there's nothing more useful to say."""
        fan = _hwmon_fan("hwmon:foo:bar:pwm1:pwm1", rpm=None, last_pwm=60)
        header = _header("hwmon:foo:bar:pwm1:pwm1", rpm_available=False)
        assert classify_fan_presence(fan, header) == FanPresence.PRESENT

    def test_openfan_with_only_commanded_pwm_is_present(self) -> None:
        fan = FanReading(
            id="openfan:ch00", source="openfan", rpm=0, last_commanded_pwm=40, age_ms=500
        )
        assert classify_fan_presence(fan, None) == FanPresence.PRESENT


class TestEmptyHeader:
    def test_writable_header_with_zero_rpm(self) -> None:
        """The dominant X870E AORUS MASTER case — controllable header with
        a tachometer wire but nothing plugged in."""
        fan = _hwmon_fan("hwmon:it8696:it87.2624:pwm2:pwm2", rpm=0)
        header = _header("hwmon:it8696:it87.2624:pwm2:pwm2")
        assert classify_fan_presence(fan, header) == FanPresence.EMPTY_HEADER

    def test_writable_header_with_no_fan_reading(self) -> None:
        """Header exists but no matching FanReading at all — still empty."""
        header = _header("hwmon:it87952:it87.2656:pwm1:pwm1", chip="it87952")
        assert classify_fan_presence(None, header) == FanPresence.EMPTY_HEADER


class TestReadOnly:
    def test_unwritable_header(self) -> None:
        fan = _hwmon_fan("hwmon:amdgpu:0000:03:00.0:pwm1:pwm1", rpm=1500)
        header = _header(
            "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1",
            is_writable=False,
            chip="amdgpu",
        )
        # Read-only takes precedence over a spinning fan — the user needs to
        # know they can't control it, not just that it's spinning.
        assert classify_fan_presence(fan, header) == FanPresence.READ_ONLY

    def test_unwritable_header_no_fan(self) -> None:
        header = _header("hwmon:foo:bar:pwm1:pwm1", is_writable=False, rpm_available=False)
        assert classify_fan_presence(None, header) == FanPresence.READ_ONLY


class TestPwmOnly:
    def test_writable_header_no_tach_idle(self) -> None:
        """Some MSI boards expose PWM without tachometer wires."""
        fan = _hwmon_fan("hwmon:foo:bar:pwm1:pwm1", rpm=None, last_pwm=0)
        header = _header("hwmon:foo:bar:pwm1:pwm1", rpm_available=False)
        assert classify_fan_presence(fan, header) == FanPresence.PWM_ONLY

    def test_writable_header_no_tach_no_fan_reading(self) -> None:
        header = _header("hwmon:foo:bar:pwm1:pwm1", rpm_available=False)
        assert classify_fan_presence(None, header) == FanPresence.PWM_ONLY


class TestUnknown:
    def test_no_data_at_all(self) -> None:
        assert classify_fan_presence(None, None) == FanPresence.UNKNOWN

    def test_openfan_idle_no_pwm(self) -> None:
        """OpenFan fan with rpm=0 and last_pwm=0 — no positive evidence
        of being PRESENT, no header context. UNKNOWN is safer than a
        guess."""
        fan = FanReading(
            id="openfan:ch00", source="openfan", rpm=0, last_commanded_pwm=0, age_ms=500
        )
        assert classify_fan_presence(fan, None) == FanPresence.UNKNOWN


class TestPresentationData:
    """The PRESENCE_BADGE and PRESENCE_TOOLTIP dicts must cover every state
    so callers can dispatch on them without needing a fallback."""

    def test_every_state_has_badge(self) -> None:
        for state in FanPresence:
            assert state in PRESENCE_BADGE

    def test_every_state_has_tooltip(self) -> None:
        for state in FanPresence:
            assert state in PRESENCE_TOOLTIP
            assert PRESENCE_TOOLTIP[state]  # non-empty

    def test_present_has_empty_badge(self) -> None:
        """PRESENT must produce no on-screen decoration so the default
        case stays uncluttered."""
        assert PRESENCE_BADGE[FanPresence.PRESENT] == ""

    def test_unknown_has_empty_badge(self) -> None:
        """UNKNOWN suppresses the badge so we never decorate fans whose
        state we can't classify confidently."""
        assert PRESENCE_BADGE[FanPresence.UNKNOWN] == ""
