"""Shared fan displayability filter (DEC-047).

Used by DashboardPage and SensorSeriesPanel to decide which fans appear in the UI.
GPU fans are always visible because zero-RPM idle is normal behavior.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading


def filter_displayable_fans(
    fans: list[FanReading],
    aliases: dict[str, str],
    hide_unused: bool,
) -> list[FanReading]:
    """Filter fans to only those that should be displayed.

    GPU fans are always visible (zero-RPM idle is normal, DEC-047).
    When hide_unused is True, non-GPU fans need evidence of activity.
    """

    def is_displayable(fan: FanReading) -> bool:
        # GPU fans are always displayable -- zero-RPM idle is normal behavior
        if fan.source == "amd_gpu":
            return True
        # When auto-hide is disabled, show all fans
        if not hide_unused:
            return True
        # Real RPM evidence (spinning fan)
        if fan.rpm is not None and fan.rpm > 0:
            return True
        # User explicitly labelled it (they want to see it)
        if fan.id in aliases:
            return True
        # Actively being controlled (PWM commanded above 0)
        return bool(fan.last_commanded_pwm is not None and fan.last_commanded_pwm > 0)

    displayable = [f for f in fans if is_displayable(f)]

    # De-duplicate: suppress hwmon fans already represented by amd_gpu.
    # Identity link: PCI BDF embedded in the amd_gpu fan ID (e.g.
    # "amd_gpu:0000:03:00.0") also appears in the hwmon fan ID.
    gpu_bdfs = {f.id.removeprefix("amd_gpu:") for f in displayable if f.source == "amd_gpu"}
    if gpu_bdfs:
        displayable = [
            f
            for f in displayable
            if f.source != "hwmon" or not any(bdf in f.id for bdf in gpu_bdfs)
        ]

    return displayable
