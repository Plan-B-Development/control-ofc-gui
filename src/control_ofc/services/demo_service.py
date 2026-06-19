"""Demo mode — synthetic data generation for testing without hardware.

DemoService produces the same typed models as the real daemon API, allowing
all UI code to work identically in demo and live modes.
"""

from __future__ import annotations

import math
import random
import time

from control_ofc.api.models import (
    AioHwmonCapability,
    AmdGpuCapability,
    BoardInfo,
    Capabilities,
    ControlCapability,
    DaemonStatus,
    FanReading,
    FeatureFlags,
    GpuDiagnosticsInfo,
    GpuFanResetResult,
    GpuVerifyResult,
    GpuVerifyState,
    HardwareDiagnosticsResult,
    HwmonCapability,
    HwmonChipInfo,
    HwmonDiagnostics,
    HwmonHeader,
    IntelGpuCapability,
    IntelGpuDiagnosticsInfo,
    KernelModuleInfo,
    LeaseState,
    OpenfanCapability,
    SafetyLimits,
    SensorReading,
    SubsystemStatus,
    ThermalSafetyInfo,
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
    {"id": "amd_gpu:0000:2d:00.0", "source": "amd_gpu", "label": "RX 7900 XTX Fan"},
    # Intel discrete GPU (DEC-121) — read-only fan; demonstrates the
    # "(read-only)" treatment and firmware-managed messaging.
    {"id": "intel_gpu:0000:03:00.0", "source": "intel_gpu", "label": "Arc B580 Fan"},
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
    # Intel Arc (Battlemage) via the xe driver — temps start at temp2 (DEC-121).
    {
        "id": "hwmon:xe:0000:03:00.0:temp2",
        "kind": "GpuTemp",
        "label": "temp2",
        "source": "intel_gpu",
        "chip_name": "xe",
    },
    {
        "id": "hwmon:xe:0000:03:00.0:temp3",
        "kind": "GpuTemp",
        "label": "temp3",
        "source": "intel_gpu",
        "chip_name": "xe",
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
    # NZXT Kraken AIO coolant temperature (DEC-156) — classifies as Liquid.
    {
        "id": "hwmon:z53:usb-3-2:Coolant",
        "kind": "CoolantTemp",
        "label": "Coolant",
        "source": "hwmon",
        "chip_name": "z53",
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
        "min_pwm_percent": 0,
        "max_pwm_percent": 100,
    },
    {
        "id": "hwmon:it8696:pci0:pwm3:CHA_FAN3",
        "label": "CPU OPT / Pump",
        "chip_name": "it8696",
        "pwm_index": 3,
        "supports_enable": True,
        "rpm_available": True,
        "min_pwm_percent": 0,
        "max_pwm_percent": 100,
    },
    # NZXT Kraken pump — liquid-cooler header (DEC-156): is_aio + writable.
    {
        "id": "hwmon:z53:usb-3-2:pwm1:Pump",
        "label": "Pump",
        "chip_name": "z53",
        "pwm_index": 1,
        "supports_enable": True,
        "rpm_available": True,
        "min_pwm_percent": 0,
        "max_pwm_percent": 100,
        "is_aio": True,
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
        self._base_coolant_temp = 34.0
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
            daemon_version="2.0.0-demo",
            ipc_transport="demo",
            openfan=OpenfanCapability(
                present=True, channels=8, rpm_support=True, write_support=True
            ),
            hwmon=HwmonCapability(present=True, pwm_header_count=2, write_support=True),
            amd_gpu=AmdGpuCapability(
                present=True,
                model_name="Radeon RX 7900 XTX",
                display_label="RX 7900 XTX",
                pci_id="0000:2d:00.0",
                fan_control_method="pmfw",
                pmfw_supported=True,
                fan_rpm_available=True,
                fan_write_supported=True,
                is_discrete=True,
                overdrive_enabled=True,
                gpu_zero_rpm_available=True,
            ),
            intel_gpu=IntelGpuCapability(
                present=True,
                model_name="Intel Arc B580",
                display_label="Arc B580",
                pci_id="0000:03:00.0",
                pci_device_id=0xE20B,
                driver="xe",
                fan_control_method="read_only",
                fan_rpm_available=True,
                is_discrete=True,
            ),
            aio_hwmon=AioHwmonCapability(
                present=True,
                status="supported",
                pump_writable=True,
                coolant_available=True,
            ),
            aio_usb=UnsupportedCapability(present=False, status="unsupported"),
            features=FeatureFlags(
                openfan_write_supported=True,
                hwmon_write_supported=True,
            ),
            limits=SafetyLimits(),
            # Demo simulates a modern, autonomous 2.0.0+ daemon (the sole fan
            # writer), so the Controls override cards stay live — the card gate now
            # requires autonomous_control (see controls_page._on_capabilities_updated
            # and the main-window control gate).
            control=ControlCapability(autonomous_control=True, min_supported_gui="2.0.0"),
        )

    def status(self) -> DaemonStatus:
        return DaemonStatus(
            api_version=1,
            daemon_version="2.0.0-demo",
            overall_status="healthy",
            subsystems=[
                SubsystemStatus(name="openfan", status="ok", age_ms=500, reason=""),
                SubsystemStatus(name="hwmon_sensors", status="ok", age_ms=500, reason=""),
                SubsystemStatus(name="hwmon_pwm", status="ok", age_ms=500, reason=""),
            ],
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
            elif s["kind"] == "CoolantTemp":
                val = self._drift(self._base_coolant_temp, 5.0, 60.0)
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
            # Intel discrete GPU fans are read-only (DEC-121): the daemon never
            # commands them, so report no last_commanded_pwm (matches reality).
            read_only = f["source"] == "intel_gpu"
            fans.append(
                FanReading(
                    id=f["id"],
                    source=f["source"],
                    rpm=max(0, base_rpm),
                    last_commanded_pwm=None if read_only else pwm,
                    age_ms=random.randint(100, 800),
                )
            )
        return fans

    def hwmon_headers(self) -> list[HwmonHeader]:
        return [HwmonHeader(**h) for h in _DEMO_HWMON_HEADERS]

    def hardware_diagnostics(self) -> HardwareDiagnosticsResult:
        """Synthetic /diagnostics/hardware payload for demo / screenshot use.

        Models a realistic Gigabyte X870E AORUS MASTER reading: IT8696E primary
        chip via the out-of-tree it87 driver, k10temp and amdgpu mainline,
        no ACPI conflicts, healthy thermal safety, and a discrete RDNA3 GPU
        with PMFW fan curves available.
        """
        return HardwareDiagnosticsResult(
            api_version=1,
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="it8696",
                        device_id="ITE IT8696E",
                        expected_driver="it87",
                        in_mainline_kernel=False,
                        header_count=5,
                    ),
                ],
                total_headers=5,
                writable_headers=2,
                enable_revert_counts={
                    "hwmon:it8696:pci0:pwm1:CHA_FAN1": 0,
                    "hwmon:it8696:pci0:pwm3:CHA_FAN3": 0,
                },
            ),
            gpu=GpuDiagnosticsInfo(
                pci_bdf="0000:2d:00.0",
                pci_device_id=0x744C,
                pci_revision=0xC8,
                model_name="Radeon RX 7900 XTX",
                fan_control_method="pmfw",
                overdrive_enabled=True,
                ppfeaturemask="0xffffffff",
                ppfeaturemask_bit14_set=True,
                zero_rpm_available=True,
            ),
            intel_gpu=IntelGpuDiagnosticsInfo(
                pci_bdf="0000:03:00.0",
                pci_device_id=0xE20B,
                pci_revision=0x00,
                model_name="Intel Arc B580",
                driver="xe",
                fan_control_method="read_only",
                fan_rpm_available=True,
                fan_control_note=(
                    "Intel GPU fan control is managed autonomously by on-card firmware and is "
                    "not exposed to Linux userspace (the xe/i915 drivers register no PWM "
                    "interface). Temperature and fan RPM are read-only."
                ),
            ),
            thermal_safety=ThermalSafetyInfo(
                state="normal",
                cpu_sensor_found=True,
                emergency_threshold_c=105.0,
                release_threshold_c=80.0,
            ),
            kernel_modules=[
                KernelModuleInfo(name="it87", loaded=True, in_mainline=False),
                KernelModuleInfo(name="k10temp", loaded=True, in_mainline=True),
                KernelModuleInfo(name="amdgpu", loaded=True, in_mainline=True),
                KernelModuleInfo(name="nvme", loaded=True, in_mainline=True),
            ],
            acpi_conflicts=[],
            board=BoardInfo(
                vendor="Gigabyte",
                name="X870E AORUS MASTER",
                bios_version="F4 (demo)",
            ),
        )

    def lease_status(self) -> LeaseState:
        return LeaseState(lease_required=True, held=True, lease_id="demo-lease")

    def set_fan_pwm(self, fan_id: str, pwm_percent: int) -> None:
        """Simulate a PWM write in demo mode."""
        if fan_id in self._fan_pwm:
            self._fan_pwm[fan_id] = max(0, min(100, pwm_percent))

    def reset_gpu_fan(self, gpu_id: str) -> GpuFanResetResult:
        """Synthetic GPU restore-to-automatic (DEC-147) for demo / screenshot
        use — never touches hardware. Always reports a successful reset."""
        return GpuFanResetResult(gpu_id=gpu_id, reset=True)

    def hwmon_rescan(self) -> list[HwmonHeader]:
        """Synthetic hwmon rescan (DEC-147) — returns the static demo header
        set, mirroring a daemon whose re-enumeration found nothing new."""
        return self.hwmon_headers()

    def verify_gpu_fan(self, gpu_id: str) -> GpuVerifyResult:
        """Synthetic GPU fan verify (DEC-120) for demo / screenshot use — never
        touches hardware. Reports a healthy ``effective`` outcome: an idle
        zero-RPM fan that spins up when the test curve is applied."""
        return GpuVerifyResult(
            gpu_id=gpu_id,
            result="effective",
            initial_state=GpuVerifyState(applied_speed_pct=None, rpm=0, zero_rpm_enabled=True),
            final_state=GpuVerifyState(applied_speed_pct=75, rpm=1650, zero_rpm_enabled=False),
            test_speed_pct=75,
            wait_seconds=6,
            fan_control_method="pmfw_curve",
            details="GPU fan control verified (demo).",
            restore_failed=False,
        )

    @staticmethod
    def fan_aliases() -> dict[str, str]:
        """Return demo-friendly display names for fans."""
        return {f["id"]: f["label"] for f in _DEMO_FANS}
