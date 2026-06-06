# 23 — Intel Motherboard Fan Control Guide

**Status:** Reference guide, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) records release-by-release changes and wins where this document disagrees with it.

## Purpose

This guide explains how motherboard fan control works on Intel desktop systems
under Linux, specifically targeting Arch Linux and CachyOS users on LGA1700
(12th/13th/14th Gen Core) and LGA1851 (Core Ultra) sockets. It covers which
kernel drivers are involved, vendor-specific quirks, BIOS configuration
requirements, and troubleshooting steps.

This is a user-facing companion to the technical reference in
`19_Hardware_Compatibility.md` and the in-app guidance system implemented in
`hwmon_guidance.py`. The structure intentionally mirrors the AMD companion
(`21_AMD_Motherboard_Fan_Control_Guide.md`) so cross-references are easy.

## The key insight: Super I/O controls fans, not the Intel chipset

The same insight that applies on AMD platforms applies on Intel:

> **On desktop Intel motherboards, fan/PWM control is determined by the
> motherboard's Super I/O chip or embedded controller (EC), not by the Intel
> chipset.**

Intel chipset names (Z690, Z790, Z890, B760, H770, …) identify the board
generation, socket type, and feature tier. They do **not** determine which
Linux driver controls the fans. The Super I/O chip — a separate IC on the
motherboard that handles low-speed I/O — is what actually reads fan RPMs and
writes PWM duty cycles.

This means the correct mental model for fan control is:

1. **Board vendor + model** (from DMI: `board_vendor`, `board_name`)
2. **Super I/O chip / hwmon driver** (`nct6775`, `nct6683`, `it87`, etc.)
3. **Actual read/write behaviour** in `/sys/class/hwmon/`
4. **Intel chipset** as a grouping hint only

### Practical consequence

Do not expect "Z790 boards" to all behave the same way. An ASUS ROG STRIX
Z790-E with a Nuvoton NCT6798D and a Gigabyte Z790 AORUS MASTER with an ITE
IT8689E + IT87952E pair use completely different drivers and have completely
different quirks.

---

## Intel platform generation overview

This table maps Intel platform generations to the Linux fan control drivers
you are most likely to encounter. As with the AMD companion, the driver
column refers to the actual fan control path (Super I/O / EC), not the CPU
temperature path (which is always `coretemp` on Intel).

| Intel Platform | Typical Boards | Common Linux Driver Path | Notes |
|---|---|---|---|
| **LGA1700 600-series** (Z690, B660, H670, H610) | ASUS ROG STRIX / TUF GAMING / PRIME Z690; MSI MAG/MEG/MPG Z690; Gigabyte Z690 AORUS; ASRock Z690 Steel Legend/Taichi/Extreme | `nct6775` (mainline) on NCT6798D / NCT6796D-E boards; `nct6683` then `nct6687d-dkms-git` on MSI Z690 (plain NCT6687D — auto-detect, no module parameter); `it87-dkms-git` on Gigabyte IT8689E + IT87952E dual-chip; `asus_ec_sensors` (mainline) for extra ASUS Z690 sensors | Strong mainline coverage. Z690 has the only LGA1700-era ASRock board with an upstream lm-sensors config (Z690 Extreme). |
| **LGA1700 700-series** (Z790, B760, H770, H610 refresh) | ASUS ROG STRIX Z790-E/-H/-I; MSI MAG Z790 TOMAHAWK / MPG Z790 EDGE WIFI; Gigabyte Z790 AORUS ELITE/MASTER/XTREME; ASRock Z790 Steel Legend/Taichi | Same chip distribution as Z690. MSI Z790 ships plain NCT6687D — auto-detect. Gigabyte Z790 AORUS dual-chip (IT8689E + IT87952E): 2026-03+ `it87-dkms-git` builds work by default (older builds need `mmio=on`). | Kernel `asus_ec_sensors` allowlist extends to ROG STRIX Z790-E/-H/-I WIFI II. |
| **LGA1851 800-series** (Z890, B860, H810) | MSI MAG/MEG/MPG Z890; ASUS ROG STRIX Z890; Gigabyte Z890 AORUS; ASRock Z890 Taichi/Steel Legend | MSI Z890 ships NCT6687DR — **requires `msi_alt1=1`** module parameter (auto-enabled in nct6687d v2.x for boards on the upstream allowlist). Gigabyte Z890 AORUS uses IT8696E + IT87952E (same as AMD X870E generation). | Newest platform — many specifics still settling. Treat upstream support as evolving. |

The `coretemp` mainline driver covers CPU temperature reporting on all of
these generations (per-core + Package id 0). It is **not** a fan-control
driver — it provides the temperature sensors that the fan-control driver's
PWM curves reference.

---

## Arch/CachyOS setup

### Base packages

Install at minimum:

```bash
sudo pacman -S lm_sensors
```

This provides `sensors`, `sensors-detect`, `pwmconfig`, and `fancontrol`.

> **Heads-up — `sensors-detect`:** On Gigabyte dual-chip boards, do not run
> `sensors-detect` after boot. It can leave the SuperIO bridge in
> configuration mode and the secondary chip will silently fail to
> enumerate on the next driver reload. The remedy is updating
> `it87-dkms-git` (2026-03+ builds default `mmio=on`) — or, on older
> builds, the `options it87 mmio=on` modprobe.d line — plus a reboot.
> See frankcrawford/it87 issue #70.

### DKMS for out-of-tree drivers

If your board needs an out-of-tree driver (`it87-dkms-git` for Gigabyte,
`nct6687d-dkms-git` for MSI), you also need DKMS and matching kernel headers:

```bash
sudo pacman -S dkms linux-headers
# Or, if you run a CachyOS kernel:
sudo pacman -S dkms linux-cachyos-headers
```

> **Z890 caveat:** the upstream nct6687d driver's auto-allowlist is still
> filling in for new MSI Z890 SKUs. If your board is not yet on it,
> load with `msi_alt1=1`:
>
> ```bash
> echo 'options nct6687 msi_alt1=1' | sudo tee /etc/modprobe.d/nct6687.conf
> sudo modprobe -r nct6687 && sudo modprobe nct6687
> ```

---

## Per-vendor guide

### ASUS LGA1700 (Z690 / Z790)

**Primary chip:** Almost always Nuvoton NCT6798D (chip ID 0xd428) on
modern ASUS Intel boards. The in-kernel `nct6775` driver supports
monitoring and PWM writes out of the box.

**Extra sensors:** Many ASUS Z690 / Z790 ROG STRIX and MAXIMUS boards are
on the upstream `asus_ec_sensors` allowlist. As of Linux 6.x the
allowlist covers (verbatim from
[docs.kernel.org/hwmon/asus_ec_sensors.html](https://docs.kernel.org/hwmon/asus_ec_sensors.html)):

- ROG MAXIMUS Z690 FORMULA
- ROG STRIX Z690-A GAMING WIFI D4
- ROG STRIX Z690-E GAMING WIFI
- ROG STRIX Z790-E GAMING WIFI II
- ROG STRIX Z790-H GAMING WIFI
- ROG STRIX Z790-I GAMING WIFI

`asus_ec_sensors` provides semantic sensor labels (VRM, T_Sensor,
Water_In/Out, Chipset) but **does not** provide a PWM write path —
it is sensor enrichment only.

**Important Intel/AMD distinction:** the `asus_wmi_sensors` driver (with
its documented polling bugs on PRIME X470-PRO and similar) is
**AMD-only**. Kernel docs explicitly list AM4-era boards; no Intel
allowlist entries exist. Do not extrapolate ASUS WMI advice from the
AMD companion guide to Intel ROG boards.

**BIOS tips:**
- If headers appear read-only, check that "Q-Fan Tuning" / "Fan Tuning"
  is set to **Manual** rather than **Auto** / **PWM** / **DC**.
- ACPI I/O port 0x0290-0x0299 conflicts can prevent the `nct6775` driver
  binding. Newer kernels (5.17+) mitigate this with ACPI mutex-based
  access; on older kernels, add `acpi_enforce_resources=lax` to boot
  parameters.

### ASUS LGA1851 (Z890)

LGA1851 is the new Core Ultra socket. As of 2026-Q2 the `asus_ec_sensors`
kernel allowlist has not yet added Z890 boards, so extra sensor labels
come from `asus_wmi` (read-only) or the raw `nct6798` / `nct6799`
labels. The Super I/O chip distribution is still settling — many SKUs
ship NCT6798D or NCT6799D.

### MSI LGA1700 (Z690 / Z790)

**Primary chip:** Nuvoton NCT6687D (the *plain* part, not the -R or -DR
variants). The in-kernel `nct6683` driver enumerates the chip and
reads sensors; PWM writes commonly require the out-of-tree
`nct6687d-dkms-git`.

**Key Z690/Z790 fact:** per
[Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) source
(`nct6687.c::msi_alt1_dmi_table`), MSI Z690 and Z790 boards use the
**default** register layout — the `msi_alt1` module parameter is
**not** required and not auto-enabled. Z890 is the platform that needs
it (see below).

**BIOS tips:**
- Disable "Smart Fan Mode" in BIOS → Hardware Monitor before relying on
  manual control from Linux.
- Some MSI Z790 boards (notably MAG Z790 EDGE WIFI) had their
  NCT6687D variant misidentified by earlier nct6687d versions — keep
  the DKMS build current. See lm-sensors issue #446.

### MSI LGA1851 (Z890)

**Primary chip:** Nuvoton **NCT6687DR**, distinct from the Z690/Z790
NCT6687D. Per the upstream
`nct6687.c::msi_alt1_dmi_table`, NCT6687DR requires the alt1 register
layout — the v2.x driver auto-enables it on the Z890 allowlist; if your
SKU is not yet listed, load with `msi_alt1=1`.

**Symptoms of `msi_alt1` being needed-but-missing:**
- PWM writes are accepted but fan RPM does not change.
- Fan tachometer readings are stuck at 0 or 65535.
- `dmesg | grep nct6687` shows the driver loaded but reports the wrong
  chip variant.

**Workaround:**

```bash
echo 'options nct6687 msi_alt1=1' | sudo tee /etc/modprobe.d/nct6687.conf
sudo modprobe -r nct6687 && sudo modprobe nct6687
```

The control-ofc-gui Diagnostics page surfaces this guidance
automatically on MSI boards with `board_name` containing `Z890`.

### Gigabyte LGA1700 (Z690 / Z790 AORUS)

**Primary chip:** ITE **IT8689E** on Z690 / Z790 AORUS boards. **Always
out-of-tree** — `it87-dkms-git` (frankcrawford fork) is required. The
in-kernel `it87` driver does not support IT8689E.

**Secondary chip:** **IT87952E** on most AORUS tiers (PRO, ELITE AX,
MASTER, XTREME). The dual-chip topology matches the AMD X670E AORUS
MASTER family. Boards in the daemon's `GIGABYTE_DUAL_CHIP_BOARDS` table
that are LGA1700: Z690 AORUS PRO, Z790 AORUS ELITE AX, Z790 AORUS
MASTER, Z790 AORUS XTREME.

**Dual-chip remediation (in order):**

```bash
# 1. Update the driver — 2026-03+ builds default mmio=on and merge the
#    ISA-bridge MMIO path that fixes secondary-chip enumeration/control:
yay -S it87-dkms-git

# 2. Only on older (pre-2026-03) builds:
echo 'options it87 mmio=on' | sudo tee /etc/modprobe.d/it87.conf

sudo systemctl reboot
# Then in the GUI: Diagnostics → Refresh Hardware Diagnostics
```

**BIOS tips:**
- **SmartFan 6** actively overrides PWM unless fan mode is set to
  **Full Speed** in BIOS → Smart Fan 6 settings.
- If "Full Speed" is unavailable in your BIOS revision, configure a
  degenerate fan curve: set all 7 temperature points to identical
  values, PWM 0% except the final point at 100%. This disables the EC's
  own curve evaluation.
- Ensure **FAN Control by** is NOT set to **Temperature**.

### Gigabyte LGA1851 (Z890 AORUS)

**Primary chip:** ITE **IT8696E** (same as AMD X870E AORUS MASTER
generation). Requires `it87-dkms-git`.

**Secondary chip:** **IT87952E** on the higher-tier Z890 AORUS SKUs.
Same dual-chip topology and same remediation as Z690/Z790 (driver
update first; `mmio=on` only on pre-2026-03 builds).

**BIOS tips:** SmartFan 6 "Full Speed" + degenerate-curve fallback.

### ASRock LGA1700 (Z690 / Z790)

**Primary chip:** Nuvoton NCT6798D on most LGA1700 ASRock boards
(Z690 Steel Legend, Z690 Taichi, Z790 Steel Legend WIFI, Z790 Taichi).
The in-kernel `nct6775` driver supports monitoring and PWM writes —
no out-of-tree driver needed.

**Worked example — ASRock Z690 Extreme (upstream lm-sensors config):**
The community
[petersulyok/asrock_z690_extreme](https://github.com/petersulyok/asrock_z690_extreme)
config is included in the upstream lm-sensors repository
(`configs/ASRock/Z690_Extreme.conf`). The chip enumerates as
`nct6798-isa-02a0` even though the physical part is NCT6796D-E. The
control-ofc-gui ships `verified=True` fallback labels for this board:

| `pwmN` | Label |
|---|---|
| `pwm1` | Chassis fan3 |
| `pwm2` | CPU fan1 |
| `pwm3` | CPU fan2 |
| `pwm4` | Chassis fan1 |
| `pwm5` | Chassis fan2 |
| `pwm6` | Chassis fan4 |
| `pwm7` | Chassis fan5 |

**BIOS tips:**
- If headers appear read-only, the typical cause is BIOS "Smart Fan"
  overriding manual mode. Disable Smart Fan for the affected header
  or set fan mode to **Full Speed** / **Performance**.
- Some Z690 Taichi-class boards expose monitoring but not PWM writes
  via the in-kernel driver. If writes are silently ignored, the GUI's
  Diagnostics → Verify PWM result will report `no_rpm_effect` — at
  that point an out-of-tree driver attempt is the right diagnostic
  direction.

---

## Sensors (CPU temperature on Intel)

Intel CPU temperature comes from the mainline `coretemp` driver, which
exposes:

- `Package id 0` — overall package temperature (use this for thermal
  safety and fan curves)
- `Core 0` / `Core 1` / … — per-physical-core temperature

The daemon classifies `coretemp` sensors as `CpuTemp` automatically,
and the GUI's sensor knowledge surfaces them as **high-confidence**
CPU die temperatures.

On Super I/O chips that also expose Intel PECI (e.g. NCT6798D wired
through the LPC bus to the CPU), the kernel exposes labels like
`PECI Agent 0` or `PECI 0`. DEC-110 widens the GUI classifier so
these match as `cpu_peci` with `medium_high` confidence and a
truthful "Intel CPU temperature reported via the PECI bus" tooltip.

The `intel_pch_thermal` driver registers a hwmon device exposing the
PCH (Platform Controller Hub) temperature on Intel systems. It is
**sensor enrichment only** — not a fan-control path. The daemon
diagnostics page lists it honestly as "loaded (mainline)" when present.

The kernel `x86_pkg_temp_thermal` driver covers the same physical
sensor as `coretemp` but registers with `.no_hwmon = true` — it only
appears as a thermal zone, never under `/sys/class/hwmon`. The control
plane uses `coretemp` exclusively.

---

## Troubleshooting checklist

1. **Identify the chip first:** `cat /sys/class/hwmon/hwmon*/name` shows
   what actually bound. Compare with the per-vendor section above.
2. **Verify CPU vendor:** the daemon's `/diagnostics/hardware` response
   now includes `cpu_vendor` (`"Intel"` / `"AMD"`). The GUI uses this to
   scope platform-specific guidance — if your CPU vendor is reported as
   empty, the platform-scoped quirks won't fire.
3. **Look for the dual-chip warning** on Gigabyte LGA1700/LGA1851
   AORUS boards — a missing IT87952E almost always points at an
   outdated `it87-dkms-git` build (2026-03+ builds default `mmio=on`;
   older builds need the modparam set explicitly).
4. **Watch for BIOS reclaim:** the GUI's pwm_enable watchdog will
   detect EC firmware overwriting manual mode. The fix is BIOS-side:
   Full Speed (Gigabyte), Disable Smart Fan (MSI, ASRock), Q-Fan
   Manual (ASUS).
5. **Z890 specifically:** if PWM writes are accepted but fans don't
   change speed, try the `msi_alt1=1` workaround before assuming the
   board is unsupported.

---

## What this guide does NOT cover

- **Intel Arc / Xe GPU fan control:** Intel Arc desktop GPUs gained
  fan-speed *reporting* in Linux 6.12 but no writable fan-control path
  exists as of 2026-Q2. The GUI does not claim Intel GPU fan control.
- **`thermald`:** the Intel thermal daemon manipulates CPU frequency
  and turbo state via the thermal-zone API, not the hwmon API. It is
  orthogonal to motherboard fan control and out of scope here.
- **Hybrid P-core / E-core differentiation:** `coretemp` exposes both
  P-cores and E-cores as `Core N` / `Package id 0`. The fan-control
  path treats them identically — no special handling required for
  Alder Lake / Raptor Lake / Arrow Lake.

---

## References

### Kernel documentation

- [Intel coretemp](https://docs.kernel.org/hwmon/coretemp.html)
- [Nuvoton nct6775](https://docs.kernel.org/hwmon/nct6775.html)
- [Nuvoton nct6683 family](https://docs.kernel.org/hwmon/nct6683.html)
- [ITE it87](https://docs.kernel.org/hwmon/it87.html)
- [ASUS EC sensors](https://docs.kernel.org/hwmon/asus_ec_sensors.html)
- [ASUS WMI sensors (AMD-only)](https://docs.kernel.org/hwmon/asus_wmi_sensors.html)

### Out-of-tree drivers

- [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) — MSI NCT6687D/-R/-DR
- [frankcrawford/it87](https://github.com/frankcrawford/it87) — ITE IT8625E+

### Community references cited above

- [petersulyok/asrock_z690_extreme](https://github.com/petersulyok/asrock_z690_extreme) — ASRock Z690 Extreme lm-sensors config
- [lm-sensors/lm-sensors](https://github.com/lm-sensors/lm-sensors) — upstream configs directory
- [LibreHardwareMonitor PR #1621](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/1621) — MSI Z890 NCT6687D documentation

### Companion guides in this repo

- `19_Hardware_Compatibility.md` — technical reference (per-driver tables)
- `21_AMD_Motherboard_Fan_Control_Guide.md` — AMD companion
- `22_AMD_Sensor_Interpretation_Deep_Dive.md` — AMD CPU sensor semantics
