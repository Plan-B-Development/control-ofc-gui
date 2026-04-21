"""Demo mode — synthetic data generation for testing without hardware.

DemoService produces the same typed models as the real daemon API, allowing
all UI code to work identically in demo and live modes.
"""

from __future__ import annotations

import math
import random
import time

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    FanReading,
    FeatureFlags,
    HwmonCapability,
    HwmonHeader,
    LeaseState,
    OpenfanCapability,
    SafetyLimits,
    SensorReading,
    StatusCounters,
    SubsystemStatus,
    UnsupportedCapability,
)

# ---------------------------------------------------------------------------
# Demo fan / sensor definitions
# ---------------------------------------------------------------------------

_DEMO_FANS: list[dict] = [
    {"id": "openfan:ch00", "source": "openfan", "label": "Front Intake 1"},
    {"id": "openfan:ch01", "source": "openfan", "label": "Front Intake 2"},
    {"id": "openfan:ch02", "source": "openfan", "label": "Rear Exhaust"},
    {"id": "openfan:ch03", "source": "openfan", "label": "Top Exhaust 1"},
    {"id": "openfan:ch04", "source": "openfan", "label": "Top Exhaust 2"},
    {"id": "openfan:ch05", "source": "openfan", "label": "GPU Adjacent Intake"},
    {"id": "openfan:ch06", "source": "openfan", "label": "Radiator Push 1"},
    {"id": "openfan:ch07", "source": "openfan", "label": "Radiator Push 2"},
    {"id": "hwmon:it8696:pci0:pwm1:CHA_FAN1", "source": "hwmon", "label": "CPU Fan"},
    {"id": "hwmon:it8696:pci0:pwm3:CHA_FAN3", "source": "hwmon", "label": "CPU OPT / Pump"},
]

_DEMO_SENSORS: list[dict] = [
    {
        "id": "hwmon:k10temp:0000:00:18.3:Tctl",
        "kind": "CpuTemp",
        "label": "Tctl",
        "source": "hwmon",
        "chip_name": "k10temp",
    },
    {
        "id": "hwmon:k10temp:0000:00:18.3:Tccd1",
        "kind": "CpuTemp",
        "label": "Tccd1",
        "source": "hwmon",
        "chip_name": "k10temp",
    },
    {
        "id": "hwmon:amdgpu:0000:2d:00.0:edge",
        "kind": "GpuTemp",
        "label": "edge",
        "source": "amd_gpu",
        "chip_name": "amdgpu",
    },
    {
        "id": "hwmon:amdgpu:0000:2d:00.0:junction",
        "kind": "GpuTemp",
        "label": "junction",
        "source": "amd_gpu",
        "chip_name": "amdgpu",
    },
    {
        "id": "hwmon:it8696:it87.2624:temp1",
        "kind": "MbTemp",
        "label": "temp1",
        "source": "hwmon",
        "chip_name": "it8696",
    },
    {
        "id": "hwmon:nvme:0000:01:00.0:Composite",
        "kind": "DiskTemp",
        "label": "Composite",
        "source": "hwmon",
        "chip_name": "nvme",
    },
]

_DEMO_HWMON_HEADERS: list[dict] = [
    {
        "id": "hwmon:it8696:pci0:pwm1:CHA_FAN1",
        "label": "CPU Fan",
        "chip_name": "it8696",
        "pwm_index": 1,
        "supports_enable": True,
        "rpm_available": True,
        "min_pwm_percent": 30,
        "max_pwm_percent": 100,
    },
    {
        "id": "hwmon:it8696:pci0:pwm3:CHA_FAN3",
        "label": "CPU OPT / Pump",
        "chip_name": "it8696",
        "pwm_index": 3,
        "supports_enable": True,
        "rpm_available": True,
        "min_pwm_percent": 30,
        "max_pwm_percent": 100,
    },
]

_DEMO_GROUPS: dict[str, list[str]] = {
    "Intake": ["openfan:ch00", "openfan:ch01", "openfan:ch05"],
    "Exhaust": ["openfan:ch02", "openfan:ch03", "openfan:ch04"],
    "CPU": ["hwmon:it8696:pci0:pwm1:CHA_FAN1", "hwmon:it8696:pci0:pwm3:CHA_FAN3"],
    "Radiator": ["openfan:ch06", "openfan:ch07"],
    "Case": [
        "openfan:ch00",
        "openfan:ch01",
        "openfan:ch02",
        "openfan:ch03",
        "openfan:ch04",
        "openfan:ch05",
    ],
}


# ---------------------------------------------------------------------------
# Demo service
# ---------------------------------------------------------------------------


class DemoService:
    """Generates plausible synthetic sensor/fan data for demo mode.

    Call tick() on each polling cycle to advance the simulation.
    """

    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._base_cpu_temp = 45.0
        self._base_gpu_temp = 38.0
        self._base_mb_temp = 32.0
        self._base_disk_temp = 35.0
        self._fan_pwm: dict[str, int] = {f["id"]: 40 for f in _DEMO_FANS}

    @property
    def _elapsed(self) -> float:
        return time.monotonic() - self._start_time

    def _drift(self, base: float, amplitude: float = 5.0, period: float = 120.0) -> float:
        """Sinusoidal drift with small random noise."""
        t = self._elapsed
        wave = amplitude * math.sin(2 * math.pi * t / period)
        noise = random.gauss(0, 0.3)
        return round(base + wave + noise, 1)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            api_version=1,
            daemon_version="0.1.0-demo",
            ipc_transport="demo",
            openfan=OpenfanCapability(
                present=True, channels=8, rpm_support=True, write_support=True
            ),
            hwmon=HwmonCapability(
                present=True, pwm_header_count=2, lease_required=True, write_support=True
            ),
            aio_hwmon=UnsupportedCapability(present=False, status="unsupported"),
            aio_usb=UnsupportedCapability(present=False, status="unsupported"),
            features=FeatureFlags(
                openfan_write_supported=True,
                hwmon_write_supported=True,
                lease_required_for_hwmon_writes=True,
            ),
            limits=SafetyLimits(),
        )

    def status(self) -> DaemonStatus:
        return DaemonStatus(
            api_version=1,
            daemon_version="0.1.0-demo",
            overall_status="healthy",
            subsystems=[
                SubsystemStatus(name="openfan", status="ok", age_ms=500, reason=""),
                SubsystemStatus(name="hwmon_sensors", status="ok", age_ms=500, reason=""),
                SubsystemStatus(name="hwmon_pwm", status="ok", age_ms=500, reason=""),
            ],
            counters=StatusCounters(),
        )

    def sensors(self) -> list[SensorReading]:
        readings = []
        for s in _DEMO_SENSORS:
            if s["kind"] == "CpuTemp":
                val = self._drift(self._base_cpu_temp, 8.0, 90.0)
            elif s["kind"] == "GpuTemp":
                val = self._drift(self._base_gpu_temp, 12.0, 150.0)
            elif s["kind"] == "MbTemp":
                val = self._drift(self._base_mb_temp, 3.0, 200.0)
            else:
                val = self._drift(self._base_disk_temp, 2.0, 300.0)

            readings.append(
                SensorReading(
                    id=s["id"],
                    kind=s["kind"],
                    label=s["label"],
                    value_c=val,
                    source=s["source"],
                    age_ms=random.randint(100, 800),
                    chip_name=s.get("chip_name", ""),
                )
            )
        return readings

    def fans(self) -> list[FanReading]:
        fans = []
        for f in _DEMO_FANS:
            pwm = self._fan_pwm.get(f["id"], 40)
            base_rpm = int(pwm * 18 + random.gauss(0, 15))
            fans.append(
                FanReading(
                    id=f["id"],
                    source=f["source"],
                    rpm=max(0, base_rpm),
                    last_commanded_pwm=pwm,
                    age_ms=random.randint(100, 800),
                )
            )
        return fans

    def hwmon_headers(self) -> list[HwmonHeader]:
        return [HwmonHeader(**h) for h in _DEMO_HWMON_HEADERS]

    def lease_status(self) -> LeaseState:
        return LeaseState(lease_required=True, held=True, lease_id="demo-lease")

    def set_fan_pwm(self, fan_id: str, pwm_percent: int) -> None:
        """Simulate a PWM write in demo mode."""
        if fan_id in self._fan_pwm:
            self._fan_pwm[fan_id] = max(0, min(100, pwm_percent))

    @staticmethod
    def fan_aliases() -> dict[str, str]:
        """Return demo-friendly display names for fans."""
        return {f["id"]: f["label"] for f in _DEMO_FANS}
