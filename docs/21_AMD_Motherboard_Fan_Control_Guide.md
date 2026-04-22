# 21 — AMD Motherboard Fan Control Guide

**Last updated:** 2026-04-22 (v1.5.0)

## Purpose

This guide explains how motherboard fan control works on AMD desktop systems
under Linux, specifically targeting Arch Linux and CachyOS users. It covers
which kernel drivers are involved, vendor-specific quirks, BIOS configuration
requirements, and troubleshooting steps.

This is a user-facing companion to the technical reference in
`19_Hardware_Compatibility.md` and the in-app guidance system implemented in
`hwmon_guidance.py`.

## The key insight: Super I/O controls fans, not the AMD chipset

The most important fact for understanding motherboard fan control on Linux:

> **On desktop AMD motherboards, fan/PWM control is determined by the
> motherboard's Super I/O chip or embedded controller (EC), not by the AMD
> chipset.**

AMD chipset names (X570, B550, X670, B650, X870, B850) identify the board
generation, socket type, and feature tier. They do **not** determine which
Linux driver controls the fans. The Super I/O chip — a separate IC on the
motherboard that handles low-speed I/O — is what actually reads fan RPMs and
writes PWM duty cycles.

This means the correct mental model for fan control is:

1. **Board vendor + model** (from DMI: `board_vendor`, `board_name`)
2. **Super I/O chip / hwmon driver** (`nct6775`, `nct6683`, `it87`, etc.)
3. **Actual read/write behaviour** in `/sys/class/hwmon/`
4. **AMD chipset** as a grouping hint only

### Practical consequence

Do not expect "X670 boards" to all behave the same way. An ASUS X670E with
a Nuvoton NCT6798 and a Gigabyte X670E with an ITE IT8689E use completely
different drivers and have completely different quirks.

---

## AMD platform generation overview

This table maps AMD platform generations to the Linux fan control drivers
you are most likely to encounter. The driver column refers to the actual
fan control path, which is Super I/O / EC based.

| AMD Platform | Typical Boards | Common Linux Driver Path | Notes |
|---|---|---|---|
| **AM4 400-series** (B450, X470) | ASUS ROG Strix B450/X470, PRIME X470-PRO; MSI/Gigabyte/ASRock variants | `nct6775` on Nuvoton boards; `asus_wmi_sensors`/`asus_ec_sensors` for extra ASUS sensors; `it87-dkms-git` on some ITE boards | Generally the easiest generation for Linux fan control |
| **AM4 500-series** (X570, B550, A520) | ASUS X570/B550, MSI B550, Gigabyte X570/B550, ASRock X570/B550 | Mix of `nct6775`, `nct6683`, `it87-dkms-git`; some MSI boards need `nct6687d-dkms-git` | Out-of-tree drivers become common here |
| **AM5 600-series** (X670E, X670, B650E, B650, A620) | ASUS X670/B650, MSI B650, ASRock A620/B650/X670, Gigabyte X670/B650 | `nct6683`, `nct6687d-dkms-git`, `nct6686d`, `it87-dkms-git`; plus ASUS EC/WMI helpers | Monitoring often works before write/control does |
| **AM5 800-series** (X870E, X870, B850, B840) | ASUS X870/B850, MSI X870/B850, Gigabyte X870/B850, ASRock X870/B850 | Commonly the hardest generation; `nct6687d-dkms-git` and `it87-dkms-git` are frequently required | Expect model-specific exceptions; support is still evolving |

---

## Arch/CachyOS setup

### Base packages

Install at minimum:

```bash
sudo pacman -S lm_sensors
```

This provides `sensors`, `sensors-detect`, `pwmconfig`, and `fancontrol`.

References:
- https://archlinux.org/packages/extra/x86_64/lm_sensors/
- https://man.archlinux.org/man/extra/lm_sensors/pwmconfig.8.en
- https://man.archlinux.org/man/extra/lm_sensors/fancontrol.8.en

### DKMS for out-of-tree drivers

If your board needs an out-of-tree driver (`it87-dkms-git`, `nct6687d-dkms-git`),
you also need DKMS and matching kernel headers:

```bash
sudo pacman -S dkms
```

**Kernel headers must match your exact running kernel.** Examples:

```bash
# Arch mainline
sudo pacman -S linux-headers

# Arch LTS
sudo pacman -S linux-lts-headers

# CachyOS (must match your installed kernel flavour)
sudo pacman -S linux-cachyos-headers
# or: linux-cachyos-bore-headers
# or: linux-cachyos-lts-headers
# or: linux-cachyos-deckify-headers
```

References:
- https://archlinux.org/packages/extra/any/dkms/
- https://archlinux.org/packages/core/x86_64/linux-headers/

### Common out-of-tree drivers

| Package | AUR URL | Upstream | Used for |
|---|---|---|---|
| `it87-dkms-git` | https://aur.archlinux.org/packages/it87-dkms-git | https://github.com/frankcrawford/it87 | Gigabyte boards with ITE IT8688E/IT8689E/IT8696E/IT8625E/IT8686E |
| `nct6687d-dkms-git` | https://aur.archlinux.org/packages/nct6687d-dkms-git | https://github.com/Fred78290/nct6687d | MSI boards (and some ASRock/ASUS) with Nuvoton NCT6687-R |

Install via your preferred AUR helper:

```bash
# Gigabyte / newer ITE path
yay -S it87-dkms-git

# MSI / newer NCT6687 path
yay -S nct6687d-dkms-git
```

---

## Vendor-by-vendor guidance

### ASUS

ASUS is one of the easier vendors on Linux because upstream kernel
documentation explicitly lists many supported boards.

#### Sensor enrichment vs PWM control

ASUS boards often expose two separate driver paths:

1. **Sensor enrichment** — `asus_ec_sensors` and/or `asus_wmi_sensors`
   provide extra temperature readings (VRM, T_Sensor, Water In/Out,
   chipset, etc.) via the ASUS embedded controller or WMI interface.
   These are **read-only sensor sources**, not PWM write paths.

2. **PWM control** — the actual fan control path is usually through the
   Super I/O chip, most commonly `nct6775` on ASUS boards.

If your ASUS board exposes both `asus_ec_sensors` and `nct6775` in
`/sys/class/hwmon/`, use `nct6775` for fan control and `asus_ec_sensors`
for enriched sensor data. Do not attempt to write PWM through the EC
or WMI interface.

#### asus_ec_sensors supported boards (AMD, from kernel docs)

The following AMD boards are explicitly listed in the kernel documentation
for `asus_ec_sensors`:

- PRIME X470-PRO, PRIME X570-PRO, PRIME X670E-PRO WIFI
- ProArt X570-CREATOR WIFI, ProArt X670E-CREATOR WIFI, ProArt X870E-CREATOR WIFI
- ROG CROSSHAIR VIII DARK HERO / HERO / FORMULA / IMPACT
- ROG CROSSHAIR X670E HERO / GENE
- ROG STRIX B550-E / B550-I
- ROG STRIX B650E-I, ROG STRIX B850-I GAMING WIFI
- ROG STRIX X570-E / X570-F / X570-I
- ROG STRIX X670E-E / X670E-I
- ROG STRIX X870-F / X870-I / X870E-E / X870E-H
- TUF GAMING X670E PLUS / WIFI

Reference: https://docs.kernel.org/hwmon/asus_ec_sensors.html

#### ASUS WMI polling risk

The kernel documentation for `asus_wmi_sensors` carries a strong warning:
some ASUS BIOS WMI implementations are buggy and frequent polling can cause
**fans stopping**, **fans getting stuck at maximum speed**, or **sensor
readings freezing**. The PRIME X470-PRO is called out as particularly bad.

The daemon polls at 1 Hz, which is within safe limits. However, users running
additional monitoring tools simultaneously should be aware of cumulative
polling frequency.

Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

#### ASUS ACPI resource conflicts

ASUS boards with Nuvoton chips may have ACPI OpRegion conflicts on I/O
ports 0x0290-0x0299. The daemon's diagnostics endpoint detects these
conflicts. Remediation options:

1. **Preferred (kernel >= 5.17):** The `nct6775` driver supports ACPI mutex
   coordination, avoiding the conflict without kernel parameters.
2. **Fallback:** Add `acpi_enforce_resources=lax` to kernel parameters.
3. **BIOS:** Disable "ACPI Hardware Monitor" if the option is available.

Reference: https://docs.kernel.org/hwmon/nct6775.html

---

### MSI

#### Common setup

Many MSI AMD boards from B550 onward use **Nuvoton NCT6687-family**
controllers. The typical Linux pattern:

- The in-kernel `nct6683` driver may load and expose monitoring data.
- But **PWM writes often do not work** through `nct6683` on MSI boards.
- The out-of-tree `nct6687d-dkms-git` driver is usually required for
  actual fan control.

A confirmed success case: **MSI MPG B550i Gaming Edge** with NCT6687-R
works correctly using the out-of-tree `nct6687d` driver.

Reference: https://github.com/Fred78290/nct6687d/issues/3

#### BIOS requirements

1. Enter BIOS -> Hardware Monitor -> Smart Fan Mode
2. **Disable** Smart Fan Mode for all headers you want to control

Without disabling Smart Fan Mode, headers appear read-only even with the
correct driver loaded.

#### X870/B850 7-point write quirk

Newer MSI boards (X870, B850, and some Z890) have a specific quirk: single
PWM register writes may not change system fan speeds. The root cause is that
these boards use different control registers for system fans vs CPU/pump fans,
and system fans require writing to all 7 BIOS fan curve points rather than a
single PWM value.

The `nct6687d` driver added the `msi_fan_brute_force` parameter for this:

```bash
modprobe nct6687 msi_fan_brute_force=1
```

This writes PWM values to all 7 fan curve control points simultaneously.
It only affects system fans controlled by BIOS, not CPU or pump fans.

The driver auto-enables the `msi_alt1` configuration for 36+ supported MSI
boards across B850, X870/X870E, and Z890 chipset families.

References:
- https://github.com/Fred78290/nct6687d
- https://github.com/Fred78290/nct6687d/blob/main/TESTING_RESULTS.md

#### Known limitations on MSI boards

- **CPU_FAN** and **PUMP_FAN** headers typically work first; **SYS_FAN**
  support may lag behind on newer boards.
- **3-pin DC chassis fans** may remain uncontrollable even when 4-pin PWM
  fans work correctly.
- On some systems, the in-kernel `nct6683` gives sensor readouts but not
  working PWM writes, while `nct6687d` gives better control.

#### Module conflict

If you switch from the in-kernel `nct6683` to the out-of-tree `nct6687d`,
you must blacklist `nct6683` to prevent it from loading first and claiming
the hwmon device:

```bash
echo "blacklist nct6683" | sudo tee /etc/modprobe.d/blacklist-nct6683.conf
```

Reference: https://forums.unraid.net/topic/190117-solved-blacklist-nct6683/

---

### ASRock

#### Current state

Newer ASRock AMD boards (A620, B650, X670, X870 era) commonly use
**Nuvoton NCT6686D** or related chips. The in-kernel `nct6683` driver
nominally supports NCT6686D, but real-world Linux fan control experience
is mixed:

- **Monitoring (read) usually works** — temperatures, fan RPMs, and
  voltages are visible.
- **PWM writes often do not work** — the driver accepts write commands
  without error, but the fans do not respond.

This is a significant gap. Do not assume that sensor visibility means
fan control is functional.

#### Alternative drivers

Two community projects target ASRock boards specifically:

1. **nct6686d** — Linux kernel module for the Nuvoton NCT6686D chipset,
   based on the NCT6687D driver. Specifically supports newer ASRock AMD
   boards including the A620I Lightning WiFi. Provides PWM write support.

   Repository: https://github.com/s25g5d4/nct6686d

2. **asrock-nct6683** — Updated `nct6683` driver that adds PWM write
   support for specific ASRock boards:
   - ASRock B550 Taichi Razer Edition
   - ASRock B650I Lightning WiFi
   - ASRock A620I Lightning WiFi
   - ASRock X570 Creator

   Repository: https://github.com/branchmispredictor/asrock-nct6683

3. **nct6687d** — In some cases, the MSI-oriented `nct6687d` driver also
   works on ASRock boards. For example, the ASRock B650 LiveMixer has been
   reported to work better with `nct6687d` than the default `nct6683`.

   Reference: https://github.com/Fred78290/nct6687d/issues/103

#### sensors-detect gaps

Some ASRock boards expose Nuvoton chip IDs that `sensors-detect` does not
recognize, even though manually loading `nct6683` works. For example, the
ASRock X670E Steel Legend has been reported with unknown Nuvoton IDs in
`sensors-detect`.

Reference: https://github.com/lm-sensors/lm-sensors/issues/499

#### Troubleshooting steps

1. Load `nct6683` — check if sensors appear.
2. Test a PWM write on a non-critical chassis fan header.
3. If writes have no RPM effect, the header is effectively read-only.
4. Try one of the alternative drivers above, matching your board model.
5. Report your findings — ASRock driver support is actively evolving.

---

### Gigabyte

Gigabyte is the vendor where ITE chip quirks matter most and where the gap
between "sensors visible" and "fans controllable" is widest.

#### Common setup

Gigabyte AMD boards typically use ITE chips (IT8688E, IT8689E, IT8696E,
IT8686E, IT8625E). The upstream in-kernel `it87` driver supports older
models but **not** most of the chips used on recent Gigabyte boards.

The out-of-tree `it87-dkms-git` (from frankcrawford/it87) is usually
required:

```bash
yay -S it87-dkms-git
```

Reference: https://github.com/frankcrawford/it87

#### BIOS requirements

1. Enter BIOS -> Smart Fan 5 / Smart Fan 6 (or equivalent)
2. Set all fan headers to **Full Speed** mode
3. Ensure "FAN Control by" is NOT set to "Temperature"

Without these settings, the ITE chip's configuration registers remain locked
by the BIOS fan controller firmware, and headers appear read-only even with
the correct driver loaded.

#### MMIO requirement

The out-of-tree `it87` driver enables MMIO (Memory-Mapped I/O) by default,
which is necessary for fan control on newer Gigabyte motherboards. If you
are using an older version of the driver, ensure MMIO is enabled. Do not
disable it unless you have a specific reason.

#### IT8689E manual control limitation

Some Gigabyte boards with IT8689E chips do not allow manual PWM control
at all, even though fan RPMs are visible. This is a known issue documented
by the `it87` project maintainer.

For affected boards, a BIOS flat-curve workaround is documented:

| Point | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| PWM | 40 | 40 | 40 | 40 | 40 | 40 | 100 |
| Temp (C) | 0 | 90 | 90 | 90 | 90 | 90 | 90 |

This creates a degenerate BIOS fan curve that effectively disables the EC's
own curve evaluation, allowing the Linux driver to control the fans via PWM
writes. Set point 7 to 100% at 90C as a safety backstop.

Some boards do not allow setting temperatures above 90C in the BIOS fan
curve editor, which means manual control may stop functioning above that
threshold.

Reference: https://github.com/frankcrawford/it87

#### Separate fan-control chip

Some newer Gigabyte boards appear to have a separate dedicated fan-control
chip in addition to the ITE Super I/O. On these boards, Linux can read
RPMs through the ITE chip but cannot change fan speeds because the actual
PWM output is routed through the separate chip, which has no Linux driver
support.

This is distinct from the IT8689E manual control limitation above — in this
case, the hardware architecture itself prevents Linux-side control.

#### ACPI resource conflicts

The in-kernel `it87` driver may refuse to load due to ACPI I/O port
conflicts. Two options:

1. **Preferred (driver-local):** Use `ignore_resource_conflict=1` when
   loading the module:
   ```bash
   modprobe it87 ignore_resource_conflict=1
   ```
   This is driver-local and does not affect other kernel subsystems.

2. **Fallback (system-wide):** Add `acpi_enforce_resources=lax` to kernel
   parameters. This is a system-wide change that affects all ACPI resource
   enforcement.

**Warning:** Both options carry inherent risk because ACPI and the driver
may access the Super I/O chip concurrently. This can cause race conditions
and in rare cases unexpected reboots. Use only when necessary.

Reference: https://github.com/frankcrawford/it87

#### Reported examples

- **Gigabyte X670E Aorus Master:** PWM writes have no effect on some
  systems even though Windows tools can control the fans.
  Reference: https://github.com/frankcrawford/it87/issues/96

- **Gigabyte B550M DS3H:** `gigabyte_wmi` driver provides temp1-temp6
  with no semantic labels.
  Reference: https://github.com/t-8ch/linux-gigabyte-wmi-driver/issues/19

#### Expected outcomes

When working with Gigabyte boards, expect one of these outcomes:

1. Full control works (driver loaded, BIOS configured correctly)
2. RPM reads work but PWM writes have no effect
3. Only some fan headers are controllable
4. BIOS flat-curve workaround is required
5. No Linux fan control possible (hardware limitation)

The daemon's PWM verification test (`POST /hwmon/{header_id}/verify`)
detects which outcome applies to each header.

---

## Module conflict detection

When switching between in-kernel and out-of-tree drivers, ensure only one
driver claims each hwmon device. Common conflicts:

| Driver A | Driver B | Problem |
|---|---|---|
| `nct6683` (in-kernel) | `nct6687` (out-of-tree) | Both target NCT6687-R; if both load, PWM files may be unusable |
| `it87` (in-kernel) | `it87` (out-of-tree) | In-kernel version may load first with incomplete support |

To blacklist a conflicting module:

```bash
echo "blacklist <module_name>" | sudo tee /etc/modprobe.d/blacklist-<module_name>.conf
sudo depmod -a
```

The daemon's hardware diagnostics endpoint (`GET /diagnostics/hardware`)
reports loaded modules and can detect known conflicts.

---

## force_id: testing only

The `it87` driver supports a `force_id` parameter to override chip
detection. The upstream project explicitly states this should only be used
for testing. Do not use `force_id` as a normal production workaround — if
the driver does not detect your chip naturally, the chip may not actually
be supported, and forcing it can cause undefined behaviour.

Reference: https://github.com/frankcrawford/it87

---

## Detection and verification logic

The daemon and GUI use this priority order for fan control detection:

### Step 1: Collect board identity

The daemon reads DMI data (`/sys/class/dmi/id/board_vendor`,
`/sys/class/dmi/id/board_name`) and enumerates all hwmon devices under
`/sys/class/hwmon/`.

### Step 2: Identify loaded drivers

Prioritise these hwmon driver names when discovered:

- `nct6775` (Nuvoton, mainline)
- `nct6683` (Nuvoton newer, mainline)
- `nct6687` (Nuvoton, out-of-tree)
- `it87` (ITE, mainline or out-of-tree)
- `asus_ec_sensors` (ASUS EC, read-only sensors)
- `asus_wmi_sensors` (ASUS WMI, read-only sensors)

### Step 3: Test write capability

The daemon's verify endpoint (`POST /hwmon/{header_id}/verify`) performs
a safe write-capability test:

1. Reads the current PWM value
2. Writes a small delta (preferring a non-critical chassis fan header)
3. Waits ~3 seconds and reads the RPM response
4. Restores the original PWM value
5. Reports one of: `effective`, `pwm_enable_reverted`, `pwm_value_clamped`,
   `no_rpm_effect`, `rpm_unavailable`

This test determines the actual write capability regardless of what the
driver claims.

### Step 4: Apply vendor quirk guidance

Based on the board vendor, chip name, and verification result, the GUI
displays context-specific guidance from its quirk database. This includes
BIOS configuration steps, alternative driver suggestions, and known
limitations.

---

## Troubleshooting checklist

1. **No sensors visible:**
   - Run `sensors-detect` and follow its module loading instructions.
   - Check if a DKMS driver is needed for your board's Super I/O chip.
   - Ensure kernel headers match your running kernel (`uname -r`).

2. **Sensors visible but no fan control headers:**
   - Check the daemon's hardware diagnostics for ACPI resource conflicts.
   - Try the appropriate out-of-tree driver for your board vendor/chip.
   - Check BIOS settings (Smart Fan Mode, Fan Control by Temperature).

3. **Fan control headers present but writes have no effect:**
   - Run the PWM verification test from the diagnostics page.
   - Check for module conflicts (two drivers claiming the same device).
   - For MSI X870/B850: try `msi_fan_brute_force=1`.
   - For Gigabyte IT8689E: try the BIOS flat-curve workaround.
   - For ASRock: try nct6686d or asrock-nct6683 alternative drivers.

4. **Fan control works for some headers but not others:**
   - This is common. CPU/pump headers often work before system fan headers.
   - Some boards have a mix of controllable and read-only headers.
   - The daemon reports each header's control capability independently.

5. **Fans behave erratically after resume from suspend:**
   - The daemon detects system resume via CLOCK_BOOTTIME vs CLOCK_MONOTONIC
     gap and signals a manual mode reset.
   - Some boards require re-writing `pwm_enable=1` after resume.

---

## Source references

### Kernel documentation
- nct6775: https://docs.kernel.org/hwmon/nct6775.html
- nct6683: https://docs.kernel.org/hwmon/nct6683.html
- it87: https://docs.kernel.org/hwmon/it87.html
- asus_ec_sensors: https://docs.kernel.org/hwmon/asus_ec_sensors.html
- asus_wmi_sensors: https://docs.kernel.org/hwmon/asus_wmi_sensors.html
- k10temp: https://docs.kernel.org/hwmon/k10temp.html

### Out-of-tree drivers
- frankcrawford/it87: https://github.com/frankcrawford/it87
- Fred78290/nct6687d: https://github.com/Fred78290/nct6687d
- s25g5d4/nct6686d: https://github.com/s25g5d4/nct6686d
- branchmispredictor/asrock-nct6683: https://github.com/branchmispredictor/asrock-nct6683

### AUR packages
- it87-dkms-git: https://aur.archlinux.org/packages/it87-dkms-git
- nct6687d-dkms-git: https://aur.archlinux.org/packages/nct6687d-dkms-git

### Arch packages
- lm_sensors: https://archlinux.org/packages/extra/x86_64/lm_sensors/
- dkms: https://archlinux.org/packages/extra/any/dkms/
- linux-headers: https://archlinux.org/packages/core/x86_64/linux-headers/
