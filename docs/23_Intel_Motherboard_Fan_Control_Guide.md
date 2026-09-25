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
| **LGA1700 600-series** (Z690, B660, H670, H610) | ASUS ROG STRIX / TUF GAMING / PRIME Z690; MSI MAG/MEG/MPG Z690; Gigabyte Z690 AORUS; ASRock Z690 Steel Legend/Extreme | `nct6775` (mainline) on NCT6798D / NCT6796D-E boards; `nct6687d-dkms-git` on MSI Z690 (NCT6687D, default register map — no module parameter; blacklist the read-only in-kernel `nct6683`); `it87-dkms-git` on Gigabyte IT8689E (+ IT87952E on the PRO / MASTER); `asus_ec_sensors` (mainline) for extra ASUS Z690 sensors | Strong mainline coverage. Z690 has the only LGA1700-era ASRock board with an upstream lm-sensors config (Z690 Extreme). |
| **LGA1700 700-series** (Z790, B760, H770, H610 refresh) | ASUS ROG STRIX Z790-E/-H/-I; MSI MAG Z790 TOMAHAWK / MPG Z790 EDGE WIFI; Gigabyte Z790 AORUS ELITE/MASTER/XTREME; ASRock Z790 Steel Legend/Taichi | Similar to Z690. MSI Z790 ships the NCT6687D with the default map. Gigabyte Z790 AORUS MASTER / XTREME / PRO X are dual-chip (IT8689E + IT87952E); the Z790 AORUS ELITE / ELITE AX has the IT8689E only. ASRock's Z790 Taichi puts five headers on an NCT6686D, read-only in the kernel driver. | The kernel `asus_ec_sensors` list includes ROG STRIX Z790-E GAMING WIFI II, Z790-H and Z790-I GAMING WIFI and ROG MAXIMUS Z790 EXTREME (7.3 adds Z790 HERO). |
| **LGA1851 800-series** (Z890, B860, H810) | MSI MAG/MEG/MPG Z890; ASUS ROG STRIX Z890; Gigabyte Z890 AORUS; ASRock Z890 Taichi/Steel Legend | MSI Z890 ships the NCT6687D with the alternate "msi_alt1" EC map (monitoring tools call it *NCT6687DR*) — **requires `fan_config=msi_alt1`**, set automatically for boards on the upstream list. ASUS Z890 / B860 carry an NCT6701D, bound as `nct6799`. Gigabyte Z890 AORUS MASTER uses IT8696E + IT87952E; the Z890 AORUS ELITE WIFI7 the IT8696E only. ASRock Z890 fans sit on an NCT6686D, read-only in the kernel driver. | Newest platform — many specifics still settling. Treat upstream support as evolving. |

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
> `sensors-detect` after boot. It writes the Super-I/O unlock key and can latch
> the bridge in front of the secondary chip in configuration mode, so the
> secondary silently fails to enumerate — and the latch can survive a reboot.
> The remedy is not a driver update or `mmio=on` (already the default): keep
> `sensors-detect`, `nct6775` and `w83627ehf` away from the Super-I/O, reboot,
> and if the chip is still missing, remove mains power. See doc 19 § ITE and
> frankcrawford/it87 issue #70.

### DKMS for out-of-tree drivers

If your board needs an out-of-tree driver (`it87-dkms-git` for Gigabyte,
`nct6687d-dkms-git` for MSI), you also need DKMS and matching kernel headers:

```bash
sudo pacman -S dkms linux-headers
# Or, if you run a CachyOS kernel:
sudo pacman -S dkms linux-cachyos-headers
```

> **Z890 caveat:** the upstream nct6687d driver's board list is still
> filling in for MSI Z890 SKUs, and each entry is a full DMI name, so a WHITE /
> PZ edition can be missing while its sibling is listed. If your board is not
> on it, load with `fan_config=msi_alt1`:
>
> ```bash
> echo 'options nct6687 fan_config=msi_alt1' | sudo tee /etc/modprobe.d/nct6687.conf
> sudo modprobe -r nct6687 && sudo modprobe nct6687
> ```

---

## Per-vendor guide

### ASUS LGA1700 (Z690 / Z790)

**Primary chip:** Nuvoton NCT6798D (it reports `0xd42b`; the driver matches the
`0xd428` class) on modern ASUS LGA1700 boards. The in-kernel `nct6775` driver
supports monitoring and PWM writes out of the box.

**Extra sensors:** Several ASUS Z690 / Z790 ROG boards are on the upstream
`asus_ec_sensors` list (hwmon device name `asusec`). The list grows with every
kernel — 55 boards in 7.2, 60 in the 7.3 release candidates — so check
[docs.kernel.org/hwmon/asus_ec_sensors.html](https://docs.kernel.org/hwmon/asus_ec_sensors.html)
for the kernel you run. The LGA1700 entries in 7.2 are:

- ROG MAXIMUS Z690 FORMULA
- ROG MAXIMUS Z790 EXTREME
- ROG STRIX Z690-A GAMING WIFI D4 (the DDR4 board only — the DDR5 one is not
  listed)
- ROG STRIX Z690-E GAMING WIFI
- ROG STRIX Z790-E GAMING WIFI II
- ROG STRIX Z790-H GAMING WIFI
- ROG STRIX Z790-I GAMING WIFI

The 7.3 release candidates add ROG MAXIMUS Z790 HERO and ProArt Z690-CREATOR
WIFI. (This list said "verbatim, six boards" until 2026-09-24 and had drifted.)

`asus_ec_sensors` provides semantic sensor labels (VRM, T_Sensor,
Water_In/Out, Chipset) but **does not** provide a PWM write path —
it is sensor enrichment only.

**Important Intel/AMD distinction:** the `asus_wmi_sensors` driver (with
its documented polling bugs on PRIME X470-PRO and similar) is
**AMD-only**. Kernel docs explicitly list AM4-era boards; no Intel
allowlist entries exist. Do not extrapolate ASUS WMI advice from the
AMD companion guide to Intel ROG boards.

**BIOS tips:**
- No BIOS setting makes the headers writable — under `nct6775` they already are,
  and the daemon switches each one to manual itself. *Q-Fan Tuning* is a one-shot
  calibration, not a mode; the mode item is *&lt;header&gt; Q-Fan Control* (Auto
  Detect / DC Mode / PWM Mode — match the fan) and the curve item
  *&lt;header&gt; Fan Profile*. (This tip used to say "set Q-Fan Tuning to Manual";
  the ASUS BIOS manuals have no such choice.)
- ACPI I/O port 0x0290-0x0299 conflicts can prevent the `nct6775` driver
  binding. Since Linux 5.16 the driver can read supported ASUS boards
  through an ASUS WMI access path (`access_asuswmi`) that sidesteps the
  port reservation (a WMI sensor-read path, not an "ACPI mutex" — the
  separately-proposed ACPI-mutex patch was never merged); on older
  kernels, add `acpi_enforce_resources=lax` to boot parameters.

### ASUS LGA1851 (Z890 / B860)

LGA1851 is the Core Ultra socket. ASUS Z890 and B860 boards carry an **NCT6701D**
(LibreHardwareMonitor: "Before this change, only the Nuvoton NCT6701D was exposed
on this board", ROG STRIX Z890-E; also a tested ROG STRIX B860-I). By chip ID
mainline `nct6775` binds it as `nct6799`, as it does on ASUS AM5 800-series
boards, where fans and voltages read correctly and most temperatures do not. No
Linux log for an ASUS Z890 exists yet.

There is **no** Linux sensor-enrichment driver for these boards: no Z890 or B860
board is on the `asus_ec_sensors` list, `asus_wmi_sensors` is AMD-only, and the
generic `asus-wmi` platform driver gives at most one `cpu_fan` reading on ASUS
desktops. (This section used to say extra labels came from `asus-wmi`, and that
these boards "ship NCT6798D or NCT6799D"; neither is supported by the evidence.)
Z890 / B860 boards are also not on the ASUS WMI access list, so `nct6775` uses
the I/O ports directly.

### MSI LGA1700 (Z690 / Z790)

**Primary chip:** Nuvoton NCT6687D — the same chip MSI uses on every generation
(it reports `0xd592`), with the **default** EC register map. The in-kernel
`nct6683` driver enumerates the chip and reads sensors, but publishes every
`pwmN` read-only on MSI boards (and names its device `nct6687`, the same as the
out-of-tree driver). Fan control needs `nct6687d-dkms-git`, with `nct6683`
blacklisted.

**Key Z690/Z790 fact:** per
[Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) source
(`nct6687.c::nct6687_msi_alt_boards[]`), MSI Z690 and Z790 boards use the
**default** register layout — the `msi_alt1` module parameter is
**not** required and not auto-enabled. Z890 is the platform that needs
it (see below). Never force `msi_alt1` on Z690/Z790: every SYS_FAN then reads
0 RPM.

**BIOS tips:**
- No BIOS setting makes the headers writable. If they are read-only, the
  in-kernel `nct6683` is bound — check `ls -l /sys/class/hwmon/hwmon*/device/driver`
  and blacklist it. (This tip used to say "disable Smart Fan Mode"; that was
  never the cause.)
- In 2023 the in-kernel `nct6683` found the NCT6687D on an **MPG Z790 EDGE
  WIFI** (MS-7D91) but exposed no sensors, while the out-of-tree `nct6687`
  worked with its default map (lm-sensors issue #446, kernel bug 217591). There
  is no MSI board called "MAG Z790 EDGE WIFI" — this tip used to name one, and
  said the out-of-tree driver misidentified the chip, which the issue does not
  show.

### MSI LGA1851 (Z890)

**Primary chip:** the same Nuvoton **NCT6687D** (it reports `0xd592`), with a
different EC register layout — monitoring tools label these boards *NCT6687DR*;
no Nuvoton or MSI document defines that name or the "NCT6687D-Refresh" expansion
this section used to give. Per the upstream
`nct6687.c::nct6687_msi_alt_boards[]`, these boards need the alt1 register
layout — current nct6687d builds enable it automatically for listed boards; if
your SKU is not listed, load with `fan_config=msi_alt1`. Several Z890 boards (MAG
Z890 TOMAHAWK WIFI, PRO Z890-P WIFI) also needed `msi_fan_brute_force=1`, with
`nct6683` blacklisted, before system-fan writes stuck.

**Symptoms of `msi_alt1` being needed-but-missing:**
- PWM writes are accepted but fan RPM does not change.
- Fan tachometer readings are stuck at 0 or 65535.
- `sudo dmesg | grep 'active fan config'` shows `default` on a Z890 board.

**Workaround:**

```bash
echo 'options nct6687 fan_config=msi_alt1' | sudo tee /etc/modprobe.d/nct6687.conf
sudo modprobe -r nct6687 && sudo modprobe nct6687
```

The control-ofc-gui System State page surfaces this guidance
automatically on MSI boards with `board_name` containing `Z890`.

### Gigabyte LGA1700 (Z690 / Z790 AORUS)

**Primary chip:** ITE **IT8689E** on Z690 / Z790 AORUS boards.
**Out-of-tree recommended** — `it87-dkms-git` (frankcrawford fork).
Mainline `it87` gained IT8689E fan *control* in kernel 7.1 (commit
`66b8eaf`), but the DKMS build stays the safe choice on the 6.12 / 6.18
LTS kernels most distros ship, and only it handles the extra curve vectors
that overrode manual mode before its PR #128.

**Secondary chip:** **IT87952E** on the Z690 AORUS PRO / MASTER and Z790 AORUS
MASTER / XTREME / PRO X (on the Z790 AORUS MASTER at `0x0b10`, not `0x0a60`).
The **Z790 AORUS ELITE / ELITE AX has the IT8689E only** — it was listed as
dual-chip here and in the daemon's `GIGABYTE_DUAL_CHIP_BOARDS` until 2026-09-24,
which raised a false missing-chip warning. (The AMD X670E AORUS boards pair
their IT8689E with an IT8792E, not an IT87952E.)

**Dual-chip remediation (in order):**

```bash
# 1. Update the driver — 2026-03+ builds default mmio=on and merge the
#    ISA-bridge MMIO path that fixes secondary-chip enumeration/control.
#    Builds from 2026-09-09 rename the chips (it8689_900a090a) — see doc 19.
yay -S it87-dkms-git

# 2. Only on older (pre-2026-03) builds:
echo 'options it87 mmio=on' | sudo tee /etc/modprobe.d/it87.conf

sudo systemctl reboot
# Then in the GUI: click Rescan Hardware in the global footer
# Still missing: keep sensors-detect / nct6775 / w83627ehf away and remove
# mains power — the ladder in doc 19 § ITE.
```

**BIOS tips:**
- On a current driver build, usually none are needed. **Never give the BIOS
  curve a 0% point** — it runs the fans at boot and whenever the daemon is not
  controlling them. (An earlier version of this tip published a "degenerate
  curve" with every point at 0% except the last; no upstream source ever
  recommended that, and it would stop the fans in exactly those windows.)
- *Full Speed* in Smart Fan 6 **Fan Speed Control** is a fail-safe, not a fix:
  the firmware runs that fan at 100% whenever it owns it, but on some boards it
  also locks Linux out of the header (frankcrawford/it87 #115).
- For a 4-pin fan set the header's **FAN Control Mode** to PWM. There is no
  "FAN Control by" item in the Smart Fan 6 manuals checked; this tip used to
  name one.

### Gigabyte LGA1851 (Z890 AORUS)

**Primary chip:** ITE **IT8696E** (same as AMD X870E AORUS MASTER
generation). Requires `it87-dkms-git`.

**Secondary chip:** **IT87952E** on the Z890 AORUS MASTER (LibreHardwareMonitor
PR #2512), and per the it87 SIV catalogue on the XTREME AI TOP, PRO ICE and ELITE
X ICE. The **Z890 AORUS ELITE WIFI7** (ICE / PLUS / DUO X) has the IT8696E only.
Same remediation as Z690/Z790.

**BIOS tips:** as for Z690/Z790 — no 0% curve points; Full Speed is a fail-safe,
not a fix.

### ASRock LGA1700 / LGA1851 (Z690 / Z790 / Z890)

ASRock's Intel boards mix chips, per the block diagrams in ASRock's manuals:

- **Z690 Steel Legend, Z690 Extreme** — NCT6798D class (`nct6798`); the Z690
  Extreme's physical part is an NCT6796D-E. Mainline `nct6775`, no out-of-tree
  driver needed.
- **Z790 Steel Legend WiFi** — NCT6796D-E, reported as `nct6798`.
- **Z790 PG Lightning, Z790 Pro RS** — NCT6796D (hwmon `nct6796`).
- **Z790 Taichi** — **NCT6686D (5 headers) + NCT5585D (3 headers)**. The NCT5585D
  is driven by `nct6775` (as `nct6798`); the NCT6686D's headers are read-only
  under the in-kernel `nct6683` and need an out-of-tree driver.
- **Z590 Taichi** — NCT6686D (the in-kernel `nct6683` recognises its customer ID
  from 7.0; read-only).
- **Z890 boards** (Steel Legend, Lightning, Pro-A, Pro RS, Nova, Taichi) — the fans
  are on an **NCT6686D**; the Nova and Taichi add an NCT6796D-E with little or
  nothing wired. PWM needs `nct6687d`, or `asrock-nct6683` on the Z890 Nova WiFi.

(This section used to put an NCT6798D on the Z690 / Z790 Taichi and every Z890
board, which the manuals do not show. The plain Z690 Taichi's chips are still
unverified.)

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
- Headers on the NCT67xx chip are always writable under `nct6775`, and the
  daemon sets manual mode itself — no BIOS setting unlocks them. If a fan does
  not follow, check that the header's BIOS fan type matches the fan (DC for
  3-pin, PWM for 4-pin).
- Headers on an NCT6686D are read-only under the in-kernel `nct6683` whatever
  the BIOS says (`ls -l` shows `-r--r--r--`); writes are refused, not "silently
  ignored" as this tip used to say. Use an out-of-tree driver there.

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
`PECI Agent 0` (`nct6775`) or `PECI 0.0` (`nct6683`). DEC-110 widens the GUI classifier so
these match as `cpu_peci` with `medium_high` confidence and a
truthful "Intel CPU temperature reported via the PECI bus" tooltip.

The `intel_pch_thermal` driver registers a hwmon device exposing the
PCH (Platform Controller Hub) temperature on Intel systems. It is
**sensor enrichment only** — not a fan-control path. The System State
page lists it honestly as "loaded (mainline)" when present.

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
   AORUS boards. First rule out an it87 v2.0 build (suffixed chip names
   such as `it8689_900a090a` mean both chips are present). Otherwise, on a
   current build, a missing IT87952E is a blocked Super-I/O: keep
   `sensors-detect`, `nct6775` and `w83627ehf` away, reboot, then remove mains
   power if needed. Only pre-2026-03 builds also need `mmio=on`.
4. **Watch for BIOS reclaim:** the daemon's pwm_enable watchdog detects EC
   firmware overwriting manual mode and re-asserts it. The remedy is per
   vendor: keep `it87-dkms-git` current on Gigabyte (Full Speed is a fail-safe,
   not a fix); `msi_fan_brute_force=1` on MSI Z890; on ASUS and ASRock no BIOS
   setting is known to stop it. Read-only headers are a different problem — a
   driver, not the BIOS.
5. **Z890 specifically:** if PWM writes are accepted but fans don't
   change speed, try the `fan_config=msi_alt1` workaround before assuming the
   board is unsupported.

---

## What this guide does NOT cover

- **Intel Arc / Xe GPU fan control:** Intel Arc desktop GPUs report fan speed —
  `i915` (Arc A-series) from Linux 6.12, `xe` (Arc B-series) from 6.16 — but no
  writable fan-control path exists as of 7.3. The GUI does not claim Intel GPU
  fan control.
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

- [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) — MSI NCT6687D (default and msi_alt1 register maps)
- [frankcrawford/it87](https://github.com/frankcrawford/it87) — ITE IT8625E+

### Community references cited above

- [petersulyok/asrock_z690_extreme](https://github.com/petersulyok/asrock_z690_extreme) — ASRock Z690 Extreme lm-sensors config
- [lm-sensors/lm-sensors](https://github.com/lm-sensors/lm-sensors) — upstream configs directory
- [LibreHardwareMonitor PR #1621](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/1621) — MSI Z890 NCT6687D documentation
- [LibreHardwareMonitor PR #2526](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/2526) and [#2512](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/2512) — ASUS ROG STRIX Z890-E (NCT6701D); Gigabyte Z890 AORUS MASTER (IT8696E + IT87952E)
- [lm-sensors issue #446](https://github.com/lm-sensors/lm-sensors/issues/446) — MSI MPG Z790 EDGE WIFI (MS-7D91)

### Companion guides in this repo

- `19_Hardware_Compatibility.md` — technical reference (per-driver tables)
- `21_AMD_Motherboard_Fan_Control_Guide.md` — AMD companion
- `22_AMD_Sensor_Interpretation_Deep_Dive.md` — AMD CPU sensor semantics
