# 21 — AMD Motherboard Fan Control Guide

**Status:** Reference guide, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) records release-by-release changes and wins where this document disagrees with it.

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
| **AM4 400-series** (B450, X470) | ASUS PRIME X470-PRO, ROG STRIX B450/X470 -E/-F/-I; MSI B450/X470; Gigabyte AORUS B450/X470; ASRock B450/X470 | `nct6775` (mainline) on NCT6779D / NCT6792D / NCT6795D / NCT6797D / NCT6798D boards; `asus_wmi_sensors` / `asus_ec_sensors` / `asus_atk0110` for extra ASUS sensors (read-only); `it87-dkms-git` on Gigabyte ITE boards (IT8686E + IT8792E pair) | Mainline coverage is generally the strongest here; main hazards are the NCT6797D-vs-`nct6687` driver collision (DEC-104) and the well-documented ASUS WMI polling bug — see "AM4 400-series specifics" below |
| **AM4 500-series** (X570, B550, A520) | ASUS X570/B550, MSI B550, Gigabyte X570/B550, ASRock X570/B550 | Mix of `nct6775`, `nct6683`, `it87-dkms-git`; some MSI boards need `nct6687d-dkms-git` | Out-of-tree drivers become common here |
| **AM5 600-series** (X670E, X670, B650E, B650, A620) | ASUS X670/B650, MSI B650, ASRock A620/B650/X670, Gigabyte X670/B650 | `nct6683`, `nct6687d-dkms-git`, `nct6686d`, `it87-dkms-git`; plus ASUS EC/WMI helpers | Monitoring often works before write/control does |
| **AM5 800-series** (X870E, X870, B850, B840) | ASUS X870/B850, MSI X870/B850, Gigabyte X870/B850, ASRock X870/B850 | Commonly the hardest generation; `nct6687d-dkms-git` and `it87-dkms-git` are frequently required | Expect model-specific exceptions; support is still evolving |

---

## Arch/CachyOS setup

> **New to Linux fan-control drivers?** The user manual ships a
> step-by-step beginner walkthrough — prerequisites, install, verify,
> rollback — at [`manual/driver-setup.md`](../manual/driver-setup.md).
> This section is the condensed reference version.

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

> **CachyOS-LTS / non-standard kernel paths:** the it87 DKMS config has a
> known module-install-path quirk on CachyOS-LTS and openSUSE Tumbleweed —
> the module builds but lands in a directory the kernel does not search
> (frankcrawford/it87
> [issue #94](https://github.com/frankcrawford/it87/issues/94)). If
> `modprobe it87` reports "module not found" right after a successful
> DKMS build, check `dkms status` and compare the install path against
> `/lib/modules/$(uname -r)/`.

> **Update before troubleshooting:** both `it87-dkms-git` and
> `nct6687d-dkms-git` are `-git` packages — every reinstall builds the
> current upstream snapshot. A large share of historical workarounds
> (e.g. `mmio=on`, the 0xd450 collision) are already fixed upstream, so
> updating the driver is the first remediation, not the last. Note the
> AUR page's displayed version string is stale `-git` metadata; what
> installs is the current upstream HEAD at build time.

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

## AM4 400-series specifics

The B450 / X470 generation is mostly straightforward on Linux, but four
generation-specific hazards are worth calling out before you wade into
the per-vendor guidance below. All four are addressed by the daemon's
`/diagnostics/hardware` endpoint and the GUI's Diagnostics page (DEC-104).

### 1. NCT6797D vs the out-of-tree `nct6687` driver

Older builds of the out-of-tree `nct6687` driver (`nct6687d-dkms-git` on
the AUR) declare chip ID `0xd450`. This is the **legitimate chip ID
assigned to NCT6797D**, a chip that AM4 400-series MSI boards (B450M
MORTAR, X470 GAMING PRO CARBON, MAG B450 TOMAHAWK MAX) actually ship with.
When both `nct6687` and `nct6775` are loaded at the same time, whichever
driver binds first claims the chip — and the other may scribble into the
wrong registers, which in at least one upstream report **bricked the
CPU_FAN header on an MSI MAG X570 TOMAHAWK WIFI**
([ublue-os/bazzite #4498](https://github.com/ublue-os/bazzite/issues/4498)).
The same chip family is used on AM4 400-series MSI boards, so the trap is
not 500-series-only. **The `0xd450` claim was removed upstream in
[Fred78290/nct6687d PR #164](https://github.com/Fred78290/nct6687d/pull/164)
(2026)** — a current `nct6687d` build no longer collides — but already-loaded
modules and not-yet-updated AUR/distro packages remain at risk, so the
remediation below still applies.

**Remediation:**

- Confirm your chip by reading `cat /sys/class/hwmon/hwmon*/name` on a
  known-good kernel boot.
- If your board has **NCT6797D / NCT6798D**, blacklist `nct6687`:

  ```bash
  echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf
  sudo update-initramfs -u  # Debian/Ubuntu — not needed on Arch
  ```

- If your board genuinely has **NCT6687-R** (rare on AM4 400-series),
  blacklist `nct6775` instead.
- Bazzite #4498 *requests* a default `nct6687` blacklist but, as of
  writing, does not ship one — do not assume your distro handles this
  for you.

The Diagnostics page surfaces this as a CRITICAL `module_collisions`
banner when both modules are loaded simultaneously, and discourages PWM
writes until the user resolves the load ordering.

### 2. ASUS WMI polling firmware bug

The mainline `asus_wmi_sensors` driver explicitly lists these AM4
400-series boards as supported:

- PRIME X470-PRO
- ROG STRIX B450-E GAMING
- ROG STRIX B450-F GAMING
- ROG STRIX B450-I GAMING
- ROG STRIX X470-F GAMING
- ROG STRIX X470-I GAMING

Upstream kernel docs **specifically warn** that some ASUS BIOSes
implement the WMI interface badly: high-frequency polling can stop
fans, pin them at maximum, or freeze sensor readings. PRIME X470-PRO
is called out by name as particularly affected. The Control-OFC daemon
polls at 1 Hz, which is within the safe band; the practical advice is
**do not run additional sensor-polling tools (Open Hardware Monitor,
lm-sensors GUIs, fan-control daemons) against these sensors at the
same time** — the cumulative request rate is what triggers the bug.

Note that `asus_wmi_sensors` / `asus_ec_sensors` / `asus_atk0110` are
**sensor-read drivers**. The actual PWM control path on these boards is
the Super I/O chip (typically NCT6798D), bound by the in-kernel
`nct6775` driver.

### 3. Gigabyte AM4 AORUS dual-chip topology

AM4 400-series Gigabyte AORUS boards (X470 AORUS ULTRA GAMING, X470
AORUS GAMING 5/7 WIFI, B450 AORUS PRO / PRO WIFI / PRO-CF) use the
same two-chip topology as the AM5 boards: a primary **IT8686E** at
I/O 0x0a40 and a secondary **IT8792E** at 0x0a60. Both require the
out-of-tree `it87-dkms-git` driver.

If only N of the expected fan headers appear in `sensors`, the
diagnostics page reports a dual-chip enumeration gap. The standard
remediation (DEC-101, re-ordered by DEC-144 now that current driver
builds default MMIO on) applies here too:

```bash
# 1. Update the driver — 2026-03+ snapshots default mmio=on and merge
#    the ISA-bridge MMIO path that fixes secondary-chip enumeration:
yay -S it87-dkms-git

# 2. Only on older (pre-2026-03) builds:
sudo tee /etc/modprobe.d/it87.conf <<<'options it87 mmio=on'
```

Avoid running `sensors-detect` after boot — it can leave the SuperIO
bridge in configuration mode and cause the secondary chip's DEVID read
to return `0xFFFF`, after which the `it87` driver silently skips it.

The historic note that the secondary IT8792E was read-only on some AM4
Gigabyte boards still applies; verify per-header writability via the
PWM Verify action on the Diagnostics → Troubleshooting tab before assigning fans
to it in a profile.

### 4. ASRock AM4 — generally smooth

AM4 ASRock boards typically use NCT6779D (full ATX) or NCT6792D
(B450 Gaming ITX/AC and similar). Both are covered by mainline
`nct6775`. The most common problem is BIOS "Smart Fan" overrides
silently rewriting PWM after the kernel writes it; disable Smart Fan
for the affected header in BIOS.

ASRock AM4 boards rarely need any of the out-of-tree drivers that the
AM5 generation forces on you. If you're considering installing
`nct6686d` or `nct6687d` "to be safe", **don't** — see hazard #1.

---

## AM4 500-series specifics

The B550 / X570 / A520 generation is the most heterogeneous on Linux —
all four Super-I/O chip families ship in this generation and each
vendor uses a different one. Coverage was hardened in DEC-106; the
practical hazards by vendor:

### MSI (NCT6687-R or NCT6797D)

MSI's AM4 500-series boards split into two non-interchangeable camps:

- **MAG B550 TOMAHAWK / MAG B550 A-PRO / MPG X570 variants** ship the
  **NCT6687-R** chip (chip ID `0xd590`). The in-kernel `nct6683`
  driver can show monitoring but PWM writes typically do not take
  effect. Use the out-of-tree `nct6687d-dkms-git` driver. Disable
  "Smart Fan Mode" in BIOS → Hardware Monitor or headers report as
  read-only.
- **X570-A PRO / X570 GAMING PRO CARBON / X570 GAMING PLUS / X570
  GAMING EDGE** ship the **NCT6797D** chip (chip ID `0xd450`). The
  in-kernel `nct6775` driver supports them out of the box — DO NOT
  install `nct6687d` here, the DEC-105 chip-ID overlap can corrupt
  non-volatile fan registers.

To identify which camp your board is in, run:

```bash
cat /sys/class/hwmon/hwmon*/name
```

If the bound driver reports `nct6687-r` or `nct6686` family names, you
have the out-of-tree camp. If it reports `nct6797`, you have the
mainline camp.

### Gigabyte (IT8688E + IT8792E dual-chip)

Most AM4 500-series AORUS boards (X570 AORUS MASTER / PRO / PRO WIFI
/ ULTRA, B550 VISION D) pair the primary **IT8688E** with a secondary
**IT8792E**. Single-chip variants (B550M AORUS PRO) ship only the
IT8688E. Both require the out-of-tree `it87-dkms-git` driver.

The well-documented dual-chip enumeration trap applies here too: if
only N of the expected headers appear in `sensors`, update
`it87-dkms-git` first (2026-03+ builds default `mmio=on` and merge the
ISA-bridge MMIO path); on older builds set `options it87 mmio=on` in
`/etc/modprobe.d/it87.conf`. Then reboot. Do not run `sensors-detect`
after boot (frankcrawford/it87 issue #70).

X570-generation boards can also lose **IT8792E fan control after
suspend/resume** (frankcrawford/it87 issue #99) — still reproducible
on current driver builds as of 2026-05, with **no confirmed upstream
fix** (an earlier revision of this guide claimed `mmio=on` resolves
it; the issue thread now contradicts that). The daemon re-asserts
`pwm_enable` after resume; if headers stay stuck, a reboot is the
reliable reset.

### ASRock (mostly NCT6798D)

ASRock AM4 500-series boards with NCT6798D (B550 Steel Legend, X570
Taichi non-Razer-Edition, B550 PG Velocita) are covered by the
in-kernel `nct6775` driver. No out-of-tree driver needed.

The exception is **B550 Taichi Razer Edition** which ships an
NCT6683 family chip and may need `branchmispredictor/asrock-nct6683`
or `s25g5d4/nct6686d` for working PWM writes — see the ASRock
section for the alternative-drivers table.

### ASUS (mostly NCT6798D + extra sensor drivers)

AM4 500-series ASUS boards (TUF GAMING X570-PLUS, ROG STRIX X570 /
B550 series, PRIME X570-PRO) ship **NCT6798D** covered by mainline
`nct6775`. The PWM control path is `nct6775`. The `asus_ec_sensors`,
`asus_wmi_sensors`, and `asus_atk0110` drivers (when they bind) are
sensor enrichment ONLY — they never provide PWM writes.

If `nct6775` fails to bind because of an ACPI conflict on I/O ports
`0x0290-0x0299`, add `acpi_enforce_resources=lax` to kernel boot
parameters or disable "ACPI Hardware Monitor" in BIOS. On kernel
5.17+ the driver supports ACPI mutex access that avoids this.

---

## AM5 600-series specifics

The B650 / X670 / A620 generation is where the out-of-tree drivers
become near-mandatory and chip variation between SKUs is high.

### MSI (NCT6687-R variants)

AM5 MSI boards (MAG B650 TOMAHAWK / MAG X670E TOMAHAWK / MPG X670E
CARBON / MEG X670E ACE) ship NCT6687-R variants. Use
`nct6687d-dkms-git`. The DEC-105 chip-ID overlap does **not** apply
to these boards because their chip ID is `0xd590`, not `0xd450` — the
brick scenario requires a single chip at the contested address. See
the MSI section for the auto-allowlist details.

### Gigabyte (IT8689E or IT8696E + IT87952E)

Most AM5 600-series Gigabyte AORUS boards ship the **IT8689E** (X670E
AORUS MASTER, X670E AORUS PRO X) or the **IT8696E** (newer X670 /
B650 boards). Many pair with a secondary **IT87952E**.

**Critical: IT8689E Rev 1 — EC override, BIOS flat-curve fix.** The
IT8689E silicon shipped on X670E AORUS MASTER (and some other AM5
600-series Gigabyte boards) silently ACCEPTS PWM writes with zero
effect on fan speed **while a normal BIOS fan curve is active** — the
EC's vector-curve control overrides the chip's manual-mode register.
This was long believed to be a hard dead end; the issue #96 thread
(2026-03) and the driver README now document a working fix:
**configure a FLAT 7-point BIOS curve (PWM 40/40/40/40/40/40 with the
final point at 100)**. With the EC curve degenerate, driver manual
control works again. If the flat-curve workaround is not viable on
your BIOS revision, fallback options remain:

- Use a different fan header on the secondary IT87952E if your board
  has one.
- Attach affected fans to an external controller (OpenFanController).
- Hold fans at a fixed speed via the BIOS curve itself.

IT8689E Rev 2 (B650 Eagle AX, etc.) is OK as long as a degenerate
fan curve disables the EC's own evaluation.

### ASUS (NCT6798D + asus_ec_sensors)

AM5 600-series ASUS boards (ROG STRIX X670E series, ROG CROSSHAIR X670E
EXTREME, PRIME X670 series) ship **NCT6798D** for PWM control plus a
growing `asus_ec_sensors` allowlist for board-level temperature /
voltage enrichment. The kernel `asus_ec_sensors` AMD allowlist (as of
2026-Q2) covers many more X670E / B650 boards than the AM4 list —
check `docs.kernel.org/hwmon/asus_ec_sensors.html` for your specific
SKU.

The PWM control path is `nct6775`. `asus_ec_sensors` provides
extra-detail temperatures only.

### ASRock (NCT6686D or NCT6798D, often needs alt drivers)

ASRock A620 / B650 / X670 boards commonly ship NCT6686D (where
in-kernel `nct6683` may show sensors but PWM writes are silently
ignored). Specific boards work better with one of three alternative
drivers — pick by SKU:

- `nct6686d` (github.com/s25g5d4/nct6686d) — A620I Lightning WiFi,
  other NCT6686D boards
- `asrock-nct6683` (github.com/branchmispredictor/asrock-nct6683) —
  B650I Lightning WiFi, A620I Lightning WiFi
- `nct6687d` (Fred78290/nct6687d) — some ASRock B650 LiveMixer
  variants

Test PWM write capability on a non-critical chassis fan header
first. ASRock boards are a strong candidate for per-model driver
selection rather than a single rule.

### ASRock X870E Taichi Lite — legitimate dual-Nuvoton

The **ASRock X870E Taichi Lite** (AM5 800-series, included here for
contiguity) ships TWO Super-I/O chips: **NCT6686** at I/O `0x0a20`
(bound by `nct6687d`) plus **NCT6799** at I/O `0x0290` (bound by
mainline `nct6775`). Both drivers MUST be loaded to control all fans.

The DEC-106 collision-detector refinement recognises this
configuration and does NOT emit the CRITICAL banner for this board
even though both `nct6687` and `nct6775` modules are present. If a
collision banner DOES appear, verify both chips enumerated:

```bash
cat /sys/class/hwmon/hwmon*/name
```

If only one nct6 chip name is visible, follow the DEC-105 / collision
remediation BEFORE touching modules. References:
Fred78290/nct6687d issue #155, Level1Techs ASRock Taichi X870E thread.

---

## AM5 800-series specifics

The B850 / X870 generation is currently the hardest on Linux. The
patterns from AM5 600-series carry over (Gigabyte dual-chip IT8696E +
IT87952E, ASRock NCT6686D/NCT6798D, ASUS NCT6798D + asus_ec_sensors,
MSI NCT6687-R variants).

The notable additions specific to this generation:

### MSI nct6687d auto-allowlist

The `nct6687d` driver (v2.x) ships an **auto-enabled board allowlist
of AM5 800-series MSI boards** across B840 / B850 / X870 / Z890
chipsets. On listed boards, the `msi_alt1` register layout is selected
automatically without a module parameter. The list keeps growing
(B850 GAMING PRO WIFI6E and MAG B860M Mortar WiFi were added in
2026-05/06) — the source of truth is `nct6687.c::msi_alt1_dmi_table`
in the Fred78290/nct6687d repository; check it rather than trusting
any point-in-time count.

If your MSI X870 / B850 board is NOT on the allowlist and system fans
don't respond to PWM writes, manually enable:

```bash
sudo modprobe -r nct6687
sudo modprobe nct6687 msi_alt1=1
# persist:
sudo tee /etc/modprobe.d/nct6687.conf <<<'options nct6687 msi_alt1=1'
```

The legacy `msi_fan_brute_force=1` parameter remains a manual override
for boards needing it.

### ASRock X870 Nova — NCT6796D-S

The ASRock X870 Nova ships **NCT6796D-S** as its primary chip
(per Fred78290/nct6687d issue #153). Mainline `nct6775` binds it
cleanly; do not load `nct6687d` here. The DEC-105 collision logic
applies.

### Gigabyte X870 AORUS STEALTH ICE — IT8883 secondary unsupported

X870 AORUS STEALTH ICE pairs the primary **IT8696E** (controllable
via `it87-dkms-git`) with a SECONDARY **IT8883** chip that has NO
Linux driver as of 2026-06 (frankcrawford/it87 issue #81, still open;
dmesg on this board shows DEVIDs `0x8696` + `0x8883`). Fans wired
through IT8883 — including the water-pump header — are uncontrollable
from Linux. On current (2026-04+) driver builds the primary IT8696E
headers, including ones that previously refused control on this
board, are fully controllable. Use only the primary-chip fan headers
or move IT8883-attached fans to an external controller.

The daemon does NOT enroll this board in the dual-chip warning table
(it would always fire a permanent "missing chip" banner with no
remediation possible). The chip is named in the GUI's chip-guidance
database with a "no driver available" note so users searching for
IT8883 see the explanation.

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

The driver maintains an in-tree allowlist of MSI boards across B850,
X870 / X870E, Z890, and adjacent chipset families that auto-enable the
`msi_alt1` configuration. The exact list grows release-by-release as
contributors add tested boards — consult the upstream
[`TESTING_RESULTS.md`](https://github.com/Fred78290/nct6687d/blob/main/TESTING_RESULTS.md)
for the current matrix rather than relying on a count cited here. If
your board is not on the allowlist but exhibits the same symptom, the
`msi_fan_brute_force=1` modprobe parameter still works.

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

The out-of-tree `it87` driver enables MMIO (Memory-Mapped I/O) by default
since the 2026-03 builds ([PR #95](https://github.com/frankcrawford/it87/pull/95);
`mmio=off` is the opt-out), which is necessary for fan control on newer
Gigabyte motherboards. If you are using an older version of the driver,
ensure MMIO is enabled with `options it87 mmio=on`. Do not disable it
unless you have a specific reason — the one documented reason is the
**IT8665E** (X399-era, e.g. ASUS ROG Zenith Extreme): the MMIO default
breaks its PWM writes
([issue #106](https://github.com/frankcrawford/it87/issues/106)), and
`options it87 mmio=off` is the remediation there.

#### IT8689E manual control limitation

Some Gigabyte boards with IT8689E chips (Rev 1 silicon) do not respond to
manual PWM control while a normal BIOS fan curve is active, even though
fan RPMs are visible — the EC's vector-curve control overrides the chip's
manual-mode register. This is documented by the `it87` project maintainer
(issue #96 and the driver README).

For affected boards, the BIOS flat-curve workaround **restores driver
manual control**:

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

- **Gigabyte X670E Aorus Master (IT8689E rev 1):** PWM writes have no
  effect on some systems even though Windows tools can control the fans.
  This is the IT8689E manual-control limitation — a different chip and
  different problem from the X870E AORUS MASTER below. As of 2026-03 the
  upstream thread documents the BIOS flat-curve workaround (7 points:
  PWM 40×6, final 100) as restoring driver manual control.
  Reference: https://github.com/frankcrawford/it87/issues/96

- **Gigabyte X870E AORUS MASTER (IT8696E rev 0 + IT87952E):** PWM fan
  control **works** on `it87-dkms-git` 332.20f2f2f+ and BIOS F13a
  (2026-03 onwards). Distinct from the X670E AORUS MASTER case above
  — that one is IT8689E rev 1 with a manual-control limitation; this
  is IT8696E rev 0 (primary) plus IT87952E (secondary), both
  controllable. The board exposes **8 writable PWM headers total**:
  5 on IT8696E (CPU_FAN, SYS_FAN1, SYS_FAN2, SYS_FAN3, CPU_OPT) and
  3 on IT87952E (community-reported as SYS_FAN5_PUMP, SYS_FAN6_PUMP,
  SYS_FAN4 in that pwm order — frankcrawford/it87
  [issue #103](https://github.com/frankcrawford/it87/issues/103),
  single-owner report; the GUI marks these labels with `(unverified)`
  until silkscreen tracing confirms).

  **Secondary chip enumeration:** on some boots only the IT8696E
  primary chip appears (5 of 8 headers). When this happens, update
  `it87-dkms-git` first — 2026-03+ builds default `mmio=on` and merge
  the ISA-bridge MMIO path (PR #95/#102) that fixes secondary-chip
  enumeration *and control*. On older builds create
  `/etc/modprobe.d/it87.conf` with `options it87 mmio=on`. Reboot and
  verify both chips are listed under `/sys/class/hwmon/`. Avoid
  running `sensors-detect` afterwards — it can disturb the SuperIO
  state and cause the secondary chip to fail enumeration on the next
  reboot. See frankcrawford/it87 issue
  [#70](https://github.com/frankcrawford/it87/issues/70) and DEC-101
  for the diagnostics surfaced by the GUI.

  BIOS Smart Fan 6 reclaims `pwm_enable` on CPU_FAN at ~1 Hz; the
  daemon's `pwm_enable` watchdog (v1.3.0+) handles this transparently.
  To eliminate the reclaim entirely set Smart Fan 6 to
  *Manual / Full Speed* for every header in BIOS. No upstream
  `lm_sensors` config exists for this board (the upstream `configs/`
  tree is unchanged since 2023); the GUI ships a fallback label table
  aligned with the issue #103 community mapping (GUI v1.8.0,
  re-aligned v1.32.0).

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
   - Update the out-of-tree driver first — `-git` packages rebuild the
     current upstream snapshot, and many historical write failures are
     fixed there.
   - Check for module conflicts (two drivers claiming the same device).
   - For MSI X870/B850: try `msi_fan_brute_force=1`.
   - For Gigabyte IT8689E: apply the BIOS flat-curve workaround
     (restores driver manual control on Rev 1 silicon).
   - For IT8665E (X399-era): current builds break PWM writes with MMIO
     on — set `options it87 mmio=off` (frankcrawford/it87 issue #106).
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

## Known kernel-version regressions

If you have an AMD discrete GPU paired with one of the boards in this
guide, also check the daemon's kernel-warning catalogue. Two regressions
are currently flagged:

- **`rdna_hang_kernel_6_18_6_19` (Critical):** Linux **6.18.x and 6.19.x** hard-hang RDNA3/RDNA4 GPUs (RX 7000 / 9000 series) under load. Pin to a **6.15–6.17** longterm kernel — **do not roll back to 6.18, which is also affected** ([Phoronix EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025); [ROCm #6101](https://github.com/ROCm/ROCm/issues/6101) reports panics on 6.18.20 and 6.19.10).
- **`smu_mismatch_navi48_r9700` (Critical):** the AMD R9700 (PCI `0x7551`) has no working `fan_curve` path on current kernels — an SMU interface-version mismatch ([ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)) leaves `pwm1` read-only and commanded fan changes ineffective. Device-scoped, not 7.0-specific; the RX 9070 XT (`0x7550`) is **not** affected.

The GUI raises a one-time popup when these match your hardware; the
catalogue is curated in `hwmon/kernel_warnings.rs` (daemon, DEC-098) and
surfaced via `GET /capabilities`. See
`docs/19_Hardware_Compatibility.md` § Known kernel-version regressions
for the full table and mitigation guidance.

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
  - PR #95 (MMIO default on, 2026-03): https://github.com/frankcrawford/it87/pull/95
  - PR #102 (ISA-bridge MMIO/H2RAM merge, 2026-04): https://github.com/frankcrawford/it87/pull/102
  - issue #64 (secondary-chip fan control, closed 2025-12): https://github.com/frankcrawford/it87/issues/64
  - issue #89 (X870E AORUS ELITE X3D dual-chip report): https://github.com/frankcrawford/it87/issues/89
  - issue #92 (B650 GAMING X AX V2 ACPI bind failure): https://github.com/frankcrawford/it87/issues/92
  - issue #94 (DKMS module-path quirk, CachyOS-LTS/Tumbleweed): https://github.com/frankcrawford/it87/issues/94
  - issue #96 (IT8689E Rev 1 + BIOS flat-curve fix): https://github.com/frankcrawford/it87/issues/96
  - issue #99 (IT8792 suspend/resume, open): https://github.com/frankcrawford/it87/issues/99
  - issue #103 (X870E AORUS MASTER label mapping): https://github.com/frankcrawford/it87/issues/103
  - issue #106 (IT8665E mmio-default regression): https://github.com/frankcrawford/it87/issues/106
  - issue #108 (`-Werror=unused-function` build failure): https://github.com/frankcrawford/it87/issues/108
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
