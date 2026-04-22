# 19 — Hardware Compatibility Guide

**Last updated:** 2026-04-22 (v1.5.0)

**See also:** `21_AMD_Motherboard_Fan_Control_Guide.md` for comprehensive
vendor-by-vendor setup and troubleshooting guidance.

## Purpose

This document describes the Super I/O (SIO) chips, kernel drivers, and
manufacturer-specific quirks that affect motherboard fan header control
under Linux. It serves as a reference for both users and the in-app
guidance system (`hwmon_guidance.py`).

## Supported Chip Families

### Nuvoton

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| NCT679x (NCT6798, NCT6799) | `nct6775` | Yes | linux (built-in) |
| NCT677x (NCT6775, NCT6776) | `nct6775` | Yes | linux (built-in) |
| NCT6683 | `nct6683` | Yes | linux (built-in) |
| NCT6686D | `nct6683` | Yes | linux (built-in) — monitoring; PWM writes may not work on all boards |
| NCT6687-R | `nct6687` | **No** | `nct6687d-dkms-git` (AUR) |

### ITE

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| IT8603–IT87952 (older models) | `it87` | Yes | linux (built-in) |
| IT8625E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8686E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8688E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8689E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8696E | `it87` | **No** | `it87-dkms-git` (AUR) |

The out-of-tree `it87` driver is maintained by Frank Crawford:
https://github.com/frankcrawford/it87

### Fintek

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| F71882FG | `f71882fg` | Yes | linux (built-in) |
| F718xx series | `f71882fg` | Yes | linux (built-in) |

### SMSC

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| SCH5627 | `sch5627` | Yes | linux (built-in) |
| SCH5636 | `sch5636` | Yes | linux (built-in) |

## Manufacturer Quirks

### Gigabyte (ITE chips)

Most Gigabyte boards use ITE IT8688E or IT8689E chips, which require the
out-of-tree `it87` driver.

**BIOS settings required:**
1. Enter BIOS → Smart Fan 5 (or equivalent)
2. Set all fan headers to **Full Speed** mode
3. Ensure "FAN Control by" is NOT set to "Temperature"

Without these settings, headers appear read-only even with the correct driver
loaded. This is because the ITE chip's configuration registers are locked by
the BIOS fan controller firmware.

**MMIO requirement:** The out-of-tree `it87` driver requires MMIO
(Memory-Mapped I/O) for fan control on newer Gigabyte motherboards. MMIO is
enabled by default in current versions of the `frankcrawford/it87` driver.

**IT8689E manual control limitation:** Some Gigabyte IT8689E boards do not
allow manual PWM control at all, even with the correct driver. A BIOS
flat-curve workaround is documented — see doc 21 for details.

**Separate fan-control chip:** Some newer Gigabyte boards use a dedicated
fan-control chip in addition to the ITE Super I/O. On these boards, Linux
can read RPMs but cannot change fan speeds through the ITE chip.

**Known affected boards:** X570, B550, X670, B650, X870, B850 series and newer.

### MSI (NCT6687-R)

MSI boards commonly use the NCT6687-R chip, which requires the out-of-tree
`nct6687d` driver.

**BIOS settings required:**
1. Enter BIOS → Hardware Monitor → Smart Fan Mode
2. **Disable** Smart Fan Mode for all headers

Without disabling Smart Fan Mode, all headers report as read-only.

**X870/B850 7-point write quirk:** Newer MSI boards (X870, B850, Z890)
require writing all 7 BIOS fan curve points rather than a single PWM value
for system fan control. The `nct6687d` driver provides a
`msi_fan_brute_force=1` module parameter for this. This only affects system
fans — CPU and pump fans use standard PWM writes.

Reference: https://github.com/Fred78290/nct6687d

**Known affected boards:** B550, X570, B650, X670, X870, B850 series.

### ASUS (Nuvoton NCT679x)

ASUS boards with Nuvoton chips may have ACPI OpRegion conflicts on I/O
ports used by the `nct6775` driver (commonly 0x0290–0x0299).

**Remediation:**
- Add `acpi_enforce_resources=lax` to kernel parameters, OR
- Disable "ACPI Hardware Monitor" in BIOS (if available)

The daemon's diagnostics endpoint detects these conflicts by parsing
`/proc/ioports` and reports them in the Hardware Readiness section.

### ASRock

**Current state:** Newer ASRock AMD boards (A620, B650, X670, X870 era)
commonly use Nuvoton NCT6686D or related chips. The in-kernel `nct6683`
driver provides **monitoring** (temperatures, RPMs) but **PWM writes often
do not work** — the driver accepts commands without error, but fans do not
respond.

**Alternative drivers for ASRock boards:**

| Driver | Repository | Supported boards |
|---|---|---|
| `nct6686d` | https://github.com/s25g5d4/nct6686d | ASRock A620I Lightning WiFi, other NCT6686D boards |
| `asrock-nct6683` | https://github.com/branchmispredictor/asrock-nct6683 | B550 Taichi Razer, B650I Lightning WiFi, A620I Lightning WiFi, X570 Creator |
| `nct6687d` | https://github.com/Fred78290/nct6687d | Some ASRock boards (e.g. B650 LiveMixer) |

Older ASRock boards with NCT677x/NCT679x chips generally work with the
mainline `nct6775` driver.

## AMD GPU Fan Control

### Requirements

- **RDNA3+ (RX 7000/9000 series):** Fan control uses PMFW `fan_curve`
  sysfs interface. Requires `amdgpu.ppfeaturemask` kernel parameter with
  bit 14 set (e.g., `amdgpu.ppfeaturemask=0xffffffff`).
- **Pre-RDNA3 (RX 6000 and older):** Uses traditional `pwm1_enable=1`
  + `pwm1` control.

### ppfeaturemask

The `ppfeaturemask` parameter enables AMD GPU power management features.
Bit 14 (`0x4000`) specifically enables overdrive/PMFW fan curve access.

To check current value:
```bash
cat /sys/module/amdgpu/parameters/ppfeaturemask
```

To enable all features (including fan control), add to kernel parameters:
```
amdgpu.ppfeaturemask=0xffffffff
```

The daemon's hardware diagnostics endpoint reports the current
ppfeaturemask value and whether bit 14 is set.

## ACPI Resource Conflicts

Some BIOS implementations claim I/O port ranges used by Super I/O chips
via ACPI OpRegions. When this happens, the kernel refuses to let the hwmon
driver bind to those ports, even though the ACPI claim is cosmetic (the
BIOS firmware doesn't actively use the ports at runtime).

Common conflict ranges:
- `0x0290–0x0299` — Nuvoton NCT6775 and ITE IT87
- `0x04E0–0x04EF` — Nuvoton NCT6775 (secondary)
- `0x0A20–0x0A2F` — ITE IT87 (alternate)

The daemon detects these by comparing `/proc/ioports` ACPI entries against
known SIO I/O ranges.

**Fix options:**
1. **Preferred (driver-local, it87 only):** `modprobe it87 ignore_resource_conflict=1`
2. **System-wide fallback:** Add `acpi_enforce_resources=lax` to kernel command line.
3. **nct6775 (kernel >= 5.17):** The driver supports ACPI mutex coordination
   natively, avoiding the need for kernel parameters on most boards.

## force_id warning

The `it87` driver supports a `force_id` parameter to override chip detection.
The upstream project explicitly states this should only be used for testing.
Do not use `force_id` in production — if the driver does not detect your chip
naturally, the chip may not be supported, and forcing it can cause undefined
behaviour.

Reference: https://github.com/frankcrawford/it87

## Kernel Module Loading

The daemon ships a module load configuration file at
`/etc/modules-load.d/control-ofc.conf` which ensures required hwmon
drivers are loaded at boot. The GUI's Hardware Readiness display shows
which modules are currently loaded by reading `/proc/modules`.

## Thermal Safety

The daemon implements a hardware-independent thermal safety rule:
- **Emergency:** CPU Tctl >= 105°C → force all fans to 100% PWM
- **Release:** CPU Tctl drops below 80°C → exit emergency
- **Recovery:** Resume normal control at 60°C
- **Failsafe:** If no CPU sensor found for 5 consecutive cycles → force 40%

The thermal safety state is reported in the hardware diagnostics response.
