# 19 — Hardware Compatibility Guide

**Last updated:** 2026-06-01 (v1.15.0 — added Intel platform mapping table, DEC-110)

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
| NCT6797D (chip ID 0xd450) | `nct6775` | Yes (via `nct6775-platform.c`) | linux (built-in) — **see NCT6687-R collision warning below** |
| NCT6795D | `nct6775` | Yes | linux (built-in) — common on MSI AM4 X470 GAMING PRO |
| NCT6792D | `nct6775` | Yes | linux (built-in) — common on ASRock AM4 ITX |
| NCT6779D | `nct6775` | Yes | linux (built-in) — common on ASRock AM4 |
| NCT677x (NCT6775, NCT6776) | `nct6775` | Yes | linux (built-in) |
| NCT6683 (chip ID 0xc730) | `nct6683` | Yes | linux (built-in) |
| NCT6686D (chip ID 0xd440) | `nct6683` | Yes | linux (built-in) — monitoring; PWM writes may not work on all boards |
| NCT6687-R (chip ID 0xd590) | `nct6687` | **No** | `nct6687d-dkms-git` (AUR) — **legacy 0xd450 claim removed by PR #164, see warning below** |

**NCT6683 / NCT6686 / NCT6687 family chip-ID table.** Sourced from the
out-of-tree driver register definitions
([Fred78290/nct6687d/src/nct6687.c](https://github.com/Fred78290/nct6687d/blob/main/src/nct6687.c))
and the mainline `nct6683` driver
([drivers/hwmon/nct6683.c](https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6683.c)):

| Chip      | Chip ID | Driver          | Boards seen                                   |
|-----------|---------|-----------------|-----------------------------------------------|
| NCT6683   | `0xc730` | `nct6683` (mainline) | older MSI / ASRock |
| NCT6686D  | `0xd440` | `nct6683` (mainline; read-only) or `nct6686d` (out-of-tree) | ASRock B650 Steel Legend WiFi, BC-250 |
| NCT6687-R | `0xd590` | `nct6687d-dkms-git` (out-of-tree only) | MSI MAG B550 TOMAHAWK, MAG B650 TOMAHAWK, MPG/MEG X670E |

(NCT6797D = `0xd450` and NCT6798D = `0xd428` are separate
`nct6775`-driven chips — see the `nct6775-platform.c` constants in the
mainline driver source for cross-reference.)

**NCT6797D / NCT6798D vs out-of-tree `nct6687` — chip-ID collision (DEC-104):**
Older builds of the out-of-tree `nct6687` driver declare chip ID `0xd450` —
the same chip ID assigned to the legitimate NCT6797D in
[`nct6775-platform.c`](https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6775-platform.c)
(`#define SIO_NCT6797_ID 0xd450`). When both `nct6687` and `nct6775` are
loaded simultaneously the wrong driver can bind to the chip and write into
the wrong registers — the
[Bazzite report (ublue-os/bazzite #4498)](https://github.com/ublue-os/bazzite/issues/4498)
documents a permanently bricked CPU_FAN header on an MSI MAG X570 TOMAHAWK
WIFI from this exact race (the board's chip reads `0xd451`, which masks to
`0xd450`). The same chip family is common on AM4 400-series MSI boards
(e.g. B450M MORTAR uses NCT6797D, per its upstream lm-sensors config).
**The `0xd450` claim was removed upstream in
[Fred78290/nct6687d PR #164](https://github.com/Fred78290/nct6687d/pull/164)
(2026)**, so a current `nct6687d` build no longer collides; the risk remains
for already-loaded modules and not-yet-updated distro packages. Note that
Bazzite #4498 *requests* a default `nct6687` blacklist but, as of writing,
does not ship one — do not assume your distro handles this. See
`21_AMD_Motherboard_Fan_Control_Guide.md` § AM4 400-series specifics for the
full remediation. The daemon detects the collision in `/diagnostics/hardware`
→ `module_collisions` and the GUI renders a CRITICAL banner discouraging PWM
writes until resolved.

### ITE

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| IT8603–IT87952 (older models) | `it87` | Yes | linux (built-in) |
| IT8625E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8686E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8688E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8689E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8696E | `it87` | **No** | `it87-dkms-git` (AUR) — primary on AM5 800-series Gigabyte boards |
| IT87952E | `it87` | **No** | `it87-dkms-git` (AUR) — secondary chip on dual-IO Gigabyte boards (e.g. X870E AORUS MASTER) |

The out-of-tree `it87` driver is maintained by Frank Crawford:
https://github.com/frankcrawford/it87

Recent Gigabyte AORUS boards (X870/X870E and several Z690/Z790/X670E
SKUs) pair a primary ITE Super-I/O (typically **IT8696E** on AM5
800-series, **IT8689E** on Z690/Z790/X670E) with an **IT87952E**
secondary that exposes additional SYS_FAN4 / FAN5_PUMP / FAN6_PUMP
headers. Both chips ship in the same `it87` driver, but the secondary
chip's enumeration depends on a healthy SuperIO bridge state at boot.

**Known issue — secondary chip not enumerated.** On some systems only
the primary chip appears in `sensors` output (5 of 8 fan headers
visible on X870E AORUS MASTER, etc.). The secondary chip's DEVID
read can fail when the SuperIO bridge has been left in configuration
mode by an earlier process — most commonly a previous run of
`sensors-detect`, but also some early-boot kernel modules or BIOS
quirks. The frankcrawford/it87 issue
[#70](https://github.com/frankcrawford/it87/issues/70) (Gigabyte X870E
AORUS PRO, missing SYS_FAN4/5/6) discusses this config-mode cause —
though in that specific report a clean power-cycle did **not** restore
the second chip, pointing at a stuck SuperIO bridge rather than
`sensors-detect`.

**Workaround:** create `/etc/modprobe.d/it87.conf` with
`options it87 mmio=on` and reboot. Avoid running `sensors-detect`
after boot — it can disturb the SuperIO state mid-session. (Where the
second chip is an undriveable IT8883, MMIO cannot help — see issue
[#81](https://github.com/frankcrawford/it87/issues/81).)

The control-ofc daemon detects this case (DEC-101): when DMI matches
a known dual-chip board but only one ITE chip enumerated, the
Diagnostics → Troubleshooting tab surfaces a warning banner with the exact
remediation steps. See `21_AMD_Motherboard_Fan_Control_Guide.md` §
Gigabyte → Reported examples for the X870E AORUS MASTER worked
example.

### Other ASUS sensor-only drivers

| Driver | Mainline | Function |
|---|---|---|
| `asus_wmi_sensors` | Yes | Read-only board sensor enrichment via WMI. The [kernel doc](https://docs.kernel.org/hwmon/asus_wmi_sensors.html) warns that buggy ASUS BIOS WMI can stop fans or pin them at maximum the more frequently it is polled — it singles out the **PRIME X470-PRO** as "particularly bad" (a BIOS with WMI method version ≥ 2 fixes it). Never the PWM write path. |
| `asus_ec_sensors` | Yes | Read-only EC sensor enrichment. The AM4 400-series boards in the [kernel allowlist](https://docs.kernel.org/hwmon/asus_ec_sensors.html) are the PRIME X470-PRO **and** the ROG STRIX X470-F / X470-I GAMING; everything else in the list is X570+ / Intel territory. |
| `asus_atk0110` | Yes | Read-only ACPI ATK0110 hwmon. Auto-loaded on a wide range of ASUS boards. If you see this driver loaded but no controllable headers, look for `nct6775` or `it87` as the PWM path. |

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

## AMD platform → typical chip mapping

This table summarises what hwmon chip(s) you are likely to find on each
AMD platform generation. Use it as a starting point for diagnostics —
it is not exhaustive, but every entry is cross-referenced against the
frankcrawford/it87 DMI table, the upstream lm-sensors `configs/`
directory, or kernel `asus_*` driver allowlists.

| Generation | Typical Vendors | Typical Hwmon Chip(s) | Driver Path |
|---|---|---|---|
| **AM4 400-series** (B450 / X470) | ASUS (PRIME X470-PRO, ROG STRIX B450/X470 -E/-F/-I) | NCT6798D + `asus_wmi_sensors` / `asus_ec_sensors` / `asus_atk0110` | mainline `nct6775` + sensor-only ASUS drivers |
| | MSI (B450M MORTAR, X470 GAMING PRO, MAG B450 TOMAHAWK MAX) | NCT6797D / NCT6795D | mainline `nct6775` — **never load `nct6687` here unless the chip is genuinely NCT6687-R** |
| | Gigabyte (X470 AORUS ULTRA/GAMING 5/7, B450 AORUS PRO/PRO-CF) | IT8686E + IT8792E (dual-chip) | out-of-tree `it87-dkms-git` |
| | ASRock (B450 Gaming ITX/AC, B450 Pro4, X470 Taichi) | NCT6779D or NCT6792D | mainline `nct6775` |
| **AM4 500-series** (X570 / B550 / A520) | ASUS (TUF GAMING X570-PLUS, ROG STRIX X570/B550, PRIME X570-PRO) | NCT6798D + asus_ec_sensors enrichment | mainline `nct6775` |
| | MSI **NCT6687-R camp** (MAG B550 TOMAHAWK, MAG B550 A-PRO, MPG X570 variants) | NCT6687-R (chip ID 0xd590) | out-of-tree `nct6687d-dkms-git` |
| | MSI **NCT6797D camp** (X570-A PRO, X570 GAMING PRO CARBON, X570 GAMING PLUS/EDGE) | NCT6797D (chip ID 0xd450) | mainline `nct6775` — **must not load nct6687d here** (DEC-105 brick risk) |
| | Gigabyte AORUS (X570 AORUS MASTER/PRO/PRO WIFI/ULTRA, B550 VISION D) | IT8688E + IT8792E (dual-chip) | out-of-tree `it87-dkms-git` |
| | Gigabyte AORUS single-chip (B550M AORUS PRO) | IT8688E only | out-of-tree `it87-dkms-git` |
| | ASRock (B550 Steel Legend, X570 Taichi non-Razer, B550 PG Velocita) | NCT6798D | mainline `nct6775` |
| | ASRock B550 Taichi Razer Edition | NCT6683 family | `asrock-nct6683` (out-of-tree, board-specific) |
| **AM5 600-series** (B650 / X670 / A620) | ASUS (ROG STRIX X670E, ROG CROSSHAIR X670E EXTREME, PRIME X670) | NCT6798D + `asus_ec_sensors` (expanded allowlist) | mainline `nct6775` for PWM; `asus_ec_sensors` for sensor enrichment |
| | MSI (MAG B650 TOMAHAWK, MAG X670E TOMAHAWK, MPG X670E CARBON, MEG X670E ACE) | NCT6687-R variants | out-of-tree `nct6687d-dkms-git` |
| | Gigabyte AORUS (X670E AORUS MASTER, X670E AORUS PRO X) | **IT8689E** + IT87952E dual-chip — **Rev 1 silently ignores PWM writes** (issue #96) | out-of-tree `it87-dkms-git`; Rev 1 hardware has no software fix |
| | Gigabyte AORUS newer (X670 AORUS / B650 boards) | IT8696E (+ optional IT87952E dual) | out-of-tree `it87-dkms-git` |
| | ASRock A620/B650/X670 NCT6686D boards | NCT6686D | `nct6686d` or `asrock-nct6683` or `nct6687d` (board-specific — test before relying) |
| **AM5 800-series** (B850 / X870 / B840) | ASUS (X870E variants, X870 series) | NCT6798D + expanded `asus_ec_sensors` allowlist | mainline `nct6775` + sensor enrichment |
| | MSI **auto-allowlist** (33 boards across B840/B850/X870/Z890) | NCT6687-R variants with msi_alt1 auto-enabled | out-of-tree `nct6687d-dkms-git` v2.x |
| | MSI boards NOT on the allowlist | NCT6687-R variants | `nct6687d` + `msi_alt1=1` or `msi_fan_brute_force=1` |
| | Gigabyte X870E AORUS MASTER / X870E AORUS PRO / B850-AI-TOP | IT8696E + IT87952E (dual-chip) | out-of-tree `it87-dkms-git`; needs `mmio=on` |
| | **Gigabyte X870 AORUS STEALTH ICE** | IT8696E + **IT8883** | IT8696E via `it87-dkms-git`; **IT8883 has no working fan-control support** — it can be force-loaded but its fan headers don't respond ([issue #81](https://github.com/frankcrawford/it87/issues/81)) |
| | ASRock X870 Nova | **NCT6796D-S** | mainline `nct6775` |
| | **ASRock X870E Taichi Lite — dual-Nuvoton** | NCT6686 + NCT6799 (separate chips) | `nct6687d` + mainline `nct6775` (DEC-106 collision-detector exemption) |

The Diagnostics page (`/diagnostics/hardware`) reports the actual loaded
modules and detected chips, so users should always cross-reference this
generic table against their own system's output.

## Intel platform → typical chip mapping

Parallel table for Intel LGA1700 (12th–14th Gen Core) and LGA1851 (Core
Ultra) platforms. Added in DEC-110 alongside the GUI's Intel vendor
quirks and the daemon's CPU vendor detection. As with the AMD table,
every entry is cross-referenced against a verifiable upstream source
(kernel allowlists, lm-sensors `configs/`, Fred78290/nct6687d source,
or frankcrawford/it87 DMI table).

| Generation | Typical Vendors | Typical Hwmon Chip(s) | Driver Path |
|---|---|---|---|
| **LGA1700 600-series** (Z690 / B660 / H670) | ASUS (ROG MAXIMUS Z690 FORMULA, ROG STRIX Z690-A/E GAMING WIFI, TUF GAMING Z690-PLUS) | NCT6798D + `asus_ec_sensors` enrichment on allowlisted ROG boards | mainline `nct6775` for PWM; `asus_ec_sensors` for sensor enrichment |
| | MSI (MAG Z690 TOMAHAWK, MPG Z690 EDGE) | NCT6687D (plain — **no `msi_alt1` needed**; selected via the driver's DMI table, not a distinct published chip ID) | out-of-tree `nct6687d-dkms-git` (auto-detected register layout) |
| | Gigabyte Z690 AORUS (PRO, ELITE AX, MASTER, XTREME) | **IT8689E + IT87952E** (dual-chip) | out-of-tree `it87-dkms-git`; needs `mmio=on` |
| | ASRock (Z690 Steel Legend, Z690 Taichi, **Z690 Extreme** — upstream lm-sensors config) | NCT6798D (Z690 Extreme reports NCT6796D-E as `nct6798-isa-02a0`) | mainline `nct6775` |
| **LGA1700 700-series** (Z790 / B760 / H770) | ASUS (ROG STRIX Z790-E/-H/-I GAMING WIFI II — kernel `asus_ec_sensors` allowlist) | NCT6798D + `asus_ec_sensors` enrichment | mainline `nct6775` + sensor enrichment |
| | MSI (MAG Z790 TOMAHAWK WIFI, MPG Z790 EDGE WIFI, MEG Z790 ACE) | NCT6687D (plain — same register layout as Z690) | out-of-tree `nct6687d-dkms-git`; no `msi_alt1` |
| | Gigabyte Z790 AORUS (ELITE AX, MASTER, XTREME) | IT8689E + IT87952E (dual-chip — same as Z690 AORUS) | out-of-tree `it87-dkms-git`; needs `mmio=on` |
| | ASRock (Z790 Steel Legend WIFI, Z790 Taichi) | NCT6798D | mainline `nct6775` |
| **LGA1851 800-series** (Z890 / B860 / H810) | MSI (MAG/MEG/MPG Z890) | **NCT6687DR** (NCT6687D-Refresh; **requires `msi_alt1=1`**, selected via the driver's `msi_alt1_dmi_table`, not a distinct published chip ID) | out-of-tree `nct6687d-dkms-git` v2.x (auto-allowlist) or manual `msi_alt1=1` |
| | ASUS (ROG STRIX Z890 — not yet on kernel `asus_ec_sensors` allowlist as of 2026-Q2) | NCT6798D / NCT6799D | mainline `nct6775` |
| | Gigabyte Z890 AORUS | **IT8696E + IT87952E** (same as AMD X870E AORUS MASTER generation) | out-of-tree `it87-dkms-git`; same `mmio=on` remediation |
| | ASRock Z890 (Steel Legend, Taichi) | NCT6798D / NCT6799D | mainline `nct6775` |

Notes:

- The daemon's `/diagnostics/hardware` response now includes a `cpu_vendor`
  field (`"Intel"` / `"AMD"` / `""`) populated from `/proc/cpuinfo`. The
  GUI uses this to scope DEC-110 platform-specific quirks (the same
  chip can ship on different vendors' Intel boards and AMD boards with
  different quirks — e.g. NCT6687DR on MSI Z890 vs MSI X870E).
- Intel CPU temperature is always provided by the `coretemp` mainline
  driver (per-core + `Package id 0`). It is not a fan-control driver.
- `intel_pch_thermal` registers a sensor-only hwmon device for the PCH
  temperature; it is enrichment, not control.
- For end-user setup, BIOS tips, and troubleshooting, see
  `23_Intel_Motherboard_Fan_Control_Guide.md`.

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

- **RDNA3+ (RX 7000 / RX 9000 series):** Fan control uses PMFW `fan_curve`
  sysfs interface. Requires `amdgpu.ppfeaturemask` kernel parameter with
  bit 14 set (e.g., `amdgpu.ppfeaturemask=0xffffffff`).
- **RDNA2 and older (RX 6000, RX 5000, Vega, Polaris):** Use the
  traditional `pwm1_enable=1` + `pwm1` control path. RX 6000 = RDNA2,
  RX 5000 = RDNA1; RX 7000 is the first generation where the legacy
  path was removed in favour of PMFW `fan_curve`.
  AMD GPU family map: [AMD `amdgpu.ids` (libdrm)](https://gitlab.freedesktop.org/mesa/drm/-/blob/main/data/amdgpu.ids),
  cross-checked against the kernel
  [`amd_shared.h`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/include/amd_shared.h)
  family constants.

### ppfeaturemask

The `ppfeaturemask` parameter enables AMD GPU power management features.
Bit 14 (`0x4000`) is `PP_OVERDRIVE_MASK` in the kernel header
[`amd_shared.h`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/include/amd_shared.h);
it enables OverDrive, which is what exposes the PMFW fan-curve sysfs tree.

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

## Intel discrete GPU (Arc) monitoring

Intel **discrete** GPUs (Arc) are supported for **read-only** monitoring
only — temperatures and fan RPM. Added in GUI v1.24.0 / daemon v1.12.0
(DEC-121).

### Drivers

| GPU family | Kernel driver | Hwmon chip name |
|---|---|---|
| Arc B-series "Battlemage" (and later Xe2) | `xe` | `xe` |
| Arc A-series "Alchemist" | `i915` | `i915` |

Both drivers register their hwmon node **only for discrete GPUs** (the
kernel gates it on `IS_DGFX(...)` in
[`drivers/gpu/drm/xe/xe_hwmon.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_hwmon.c)
and the equivalent `i915_hwmon.c`). The daemon therefore treats an hwmon
chip named `xe` or `i915` as unambiguously a discrete Intel GPU.
**Integrated** Intel graphics (Xe / UHD) expose no such hwmon node and are
not affected by this feature.

### Supported (read-only)

- **Temperature monitoring** — surfaced as sensor source `"intel_gpu"`,
  kind `gpu_temp`. See `20_Sensor_Interpretation_Guide.md` for the
  per-index meaning of the `xe` / `i915` channels.
- **Fan RPM monitoring** — the `xe` driver exposes `fan1_input`,
  `fan2_input`, `fan3_input` (all read-only RPM); the GUI surfaces one fan
  entity per GPU (`fan1`) as fan ID `intel_gpu:{pci_bdf}`. `i915` fan RPM
  reporting (`fan1_input`) was added in Linux **6.12**.

### Not supported — fan control

There is **no user-controllable fan interface** for Intel GPUs on Linux.
Neither `xe` nor `i915` exposes a `pwm` attribute or a fan-write callback —
Intel GPU fan speed is managed autonomously by on-card firmware (a
linux-firmware blob, e.g. `fan_control_8086_e20b_8086_1100.bin` for the Arc
B580; fan reads go through a firmware "pcode" mailbox). Userspace cannot
override it. This is a kernel/firmware reality, not a daemon limitation.

Consequently the daemon always reports `fan_control_method` of `"read_only"`
(fan present) or `"none"`. Intel GPU fans are never writable and are never
offered as controllable curve members. The GPU's **temperatures** remain
usable as curve *sensors* to drive other fans.

There is no Intel kernel parameter to enable fan control — note that
`amdgpu.ppfeaturemask` is an AMD-only concept and does not apply to Intel.

### Model names

Only the Arc **B580** (PCI device ID `0xE20B`, vendor `0x8086`) is
authoritatively name-mapped. All other Intel discrete GPUs display as the
generic "Intel D-GPU" until an authoritative device-ID → name mapping is
confirmed.

### Sources

- [intel-xe-hwmon sysfs ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-driver-intel-xe-hwmon)
- [intel-i915-hwmon sysfs ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-driver-intel-i915-hwmon)
- [`drivers/gpu/drm/xe/xe_hwmon.c`](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_hwmon.c)
- [`include/drm/intel/pciids.h`](https://github.com/torvalds/linux/blob/master/include/drm/intel/pciids.h)

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
3. **nct6775 (kernel >= 5.16, ASUS boards):** Since Linux 5.16 the driver can
   read the chip through an ASUS WMI access path (`access_asuswmi`) that
   sidesteps the ACPI Super-I/O port reservation on supported ASUS boards,
   avoiding the need for `acpi_enforce_resources=lax` there. (This is a WMI
   sensor-read path, not a general "ACPI mutex" lock — the separately-proposed
   ACPI-mutex patch was never merged upstream.) The mechanism is implemented
   in [`drivers/hwmon/nct6775-platform.c`](https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6775-platform.c)
   — see `enum sensor_access` (`access_direct` / `access_asuswmi`) and the
   `asus_wmi_boards[]` allowlist of supported boards. The kernel-level
   user-facing [nct6775 hwmon doc](https://docs.kernel.org/hwmon/nct6775.html)
   covers the driver's sensor schema but does not document this access path.

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
- **Emergency:** hottest CPU temperature >= 105°C → force all fans to 100% PWM
- **Release:** hottest CPU temperature drops below 80°C → exit emergency
- **Recovery:** apply a 60% PWM recovery floor for one cycle, then resume active profile control
- **Failsafe:** if no CPU sensor is reachable for 5 consecutive cycles → force 40%

The thermal safety state is reported in the hardware diagnostics response.

## Known kernel-version regressions

The daemon ships a curated catalogue (`hwmon/kernel_warnings.rs`,
DEC-098) that matches the running kernel against published amdgpu
regressions and surfaces matches via
`GET /capabilities` (`devices.amd_gpu.kernel_warnings`). The GUI raises a
one-time `QMessageBox` when a high- or critical-severity warning fires,
and lists every match on the Diagnostics page. Acknowledged warnings are
remembered in `app_settings.acknowledged_kernel_warnings` so the popup
does not re-fire on every reconnect.

Currently catalogued (severity in parentheses):

| `id` | Affected kernels | Affected hardware | Severity | Symptom |
|---|---|---|---|---|
| `rdna_hang_kernel_6_18_6_19` | 6.18.x **and** 6.19.x | RDNA3 (RX 7000) and RDNA4 (RX 9000) | Critical | Hard hang under load ([Phoronix, EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025)). Not bisected; **no** AMD revert or fix confirmed. [ROCm #6101](https://github.com/ROCm/ROCm/issues/6101) reports kernel panics on both 6.18.20 and 6.19.10. Pre-RDNA3 GPUs are unaffected. |
| `smu_mismatch_navi48_r9700` | all current kernels (6.14 → 7.0 tested) | R9700 only (PCI `0x7551`) | Critical | No working PMFW fan-control path: an SMU interface-version mismatch (firmware iface v50 vs driver v46, [ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)) leaves `pwm1` read-only and commanded fan changes ineffective; the GPU can reach 109 °C with no dmesg error. Scoped to `0x7551` — the RX 9070 XT (`0x7550`) is **not** affected. |

**Mitigations:**

- For `rdna_hang_kernel_6_18_6_19`: pin to a **6.15–6.17** longterm kernel.
  **Do NOT roll back to 6.18 — it is also affected.** No known-good fix has
  landed upstream as of writing, so avoid both 6.18.x and 6.19.x on RDNA3/4
  until a stable point release is confirmed.
- For `smu_mismatch_navi48_r9700`: there is no working fan-control path on the
  R9700 until the amdgpu driver ships the matching SMU interface (v50). Use
  automatic mode (`POST /gpu/{bdf}/fan/reset`). The mismatch is device-scoped,
  not kernel-7.0-specific — RX 9070 XT (`0x7550`) users are unaffected on any
  kernel.

The catalogue is data-only — adding a new entry takes a 30-line PR
against `kernel_warnings.rs`. If your kernel/hardware combination is
behaving badly and the daemon is silent about it, file an issue with
your `uname -a`, GPU PCI ID, and a short failure description.

## Sources

Primary sources for the externally-verifiable claims in this document
(re-verified during the DEC-114 audit; the AMD/Intel board-mapping tables
are additionally cross-referenced against the upstream lm-sensors `configs/`
directory and the driver DMI tables cited inline above):

**Kernel drivers & docs**
- [nct6775 `nct6775-platform.c`](https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6775-platform.c) — `SIO_NCT6797_ID 0xd450`, `SIO_NCT6798_ID 0xd428`; [nct6775 hwmon doc](https://docs.kernel.org/hwmon/nct6775.html)
- [it87 hwmon doc](https://docs.kernel.org/hwmon/it87.html)
- [asus_wmi_sensors hwmon doc](https://docs.kernel.org/hwmon/asus_wmi_sensors.html) — buggy-BIOS fan-stop warning + supported-board list
- [asus_ec_sensors hwmon doc](https://docs.kernel.org/hwmon/asus_ec_sensors.html) — EC allowlist
- [amdgpu `PP_OVERDRIVE_MASK` (`amd_shared.h`)](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/include/amd_shared.h) — bit 14 = `0x4000`

**Out-of-tree drivers**
- [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) — MSI NCT6686D / NCT6687D, `msi_alt1` & `msi_fan_brute_force` params; [PR #164](https://github.com/Fred78290/nct6687d/pull/164) removed the `0xd450` collision
- [frankcrawford/it87](https://github.com/frankcrawford/it87) — IT8625E+ support, `force_id` / `ignore_resource_conflict` / `mmio` params; issues [#70](https://github.com/frankcrawford/it87/issues/70), [#81](https://github.com/frankcrawford/it87/issues/81), [#96](https://github.com/frankcrawford/it87/issues/96)

**GPU device IDs & kernel regressions**
- AMD `amdgpu.ids` (libdrm) and `pci.ids` (hwdata) — Navi 48 `0x7550` (RX 9070 XT rev `0xC0` / RX 9070 rev `0xC3`) and `0x7551` (Radeon AI PRO R9700)
- [Phoronix — RDNA3/RDNA4 hard hang on Linux 6.18/6.19 (EOY 2025)](https://www.phoronix.com/review/old-amdgpu-eoy2025)
- [ROCm Issue #6101](https://github.com/ROCm/ROCm/issues/6101) — R9700 SMU interface-version mismatch; 6.18.20 / 6.19.10 panics
- [Bazzite (ublue-os/bazzite) #4498](https://github.com/ublue-os/bazzite/issues/4498) — nct6687 / nct6775 `0xd450` collision brick report
