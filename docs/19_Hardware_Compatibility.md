# 19 — Hardware Compatibility Guide

**Last updated:** 2026-04-21 (v1.1.0)

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

**Known affected boards:** X570, B550, X670, B650 series and newer.

### MSI (NCT6687-R)

MSI boards commonly use the NCT6687-R chip, which requires the out-of-tree
`nct6687d` driver.

**BIOS settings required:**
1. Enter BIOS → Hardware Monitor → Smart Fan Mode
2. **Disable** Smart Fan Mode for all headers

Without disabling Smart Fan Mode, all headers report as read-only.

**Known affected boards:** B550, X570, B650, X670 series.

### ASUS (Nuvoton NCT679x)

ASUS boards with Nuvoton chips may have ACPI OpRegion conflicts on I/O
ports used by the `nct6775` driver (commonly 0x0290–0x0299).

**Remediation:**
- Add `acpi_enforce_resources=lax` to kernel parameters, OR
- Disable "ACPI Hardware Monitor" in BIOS (if available)

The daemon's diagnostics endpoint detects these conflicts by parsing
`/proc/ioports` and reports them in the Hardware Readiness section.

### ASRock

ASRock boards generally work out of the box with mainline drivers.
Most use Nuvoton NCT677x/NCT679x chips with standard I/O port mappings.

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

**Fix:** Add `acpi_enforce_resources=lax` to kernel command line.

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
