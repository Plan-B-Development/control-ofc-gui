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
| **AM4 400-series** (B450, X470) | ASUS PRIME X470-PRO, ROG STRIX B450/X470 -E/-F/-I; MSI B450/X470; Gigabyte AORUS B450/X470; ASRock B450/X470 | `nct6775` (mainline) on the MSI and ASRock boards (NCT6779D / NCT6792D / NCT6795D / NCT6797D); `it87-dkms-git` on Gigabyte (IT8686E + IT8792E) **and on ASUS**, whose AM4 300/400-series boards carry an ITE IT8665E; `asus_wmi_sensors` / `asus_ec_sensors` for extra ASUS sensors (read-only) | Main hazards are the NCT6797D-vs-`nct6687` driver collision (DEC-104) and the ASUS WMI firmware bug — see "AM4 400-series specifics" below |
| **AM4 500-series** (X570, B550, A520) | ASUS X570/B550, MSI B550/X570, Gigabyte X570/B550, ASRock X570/B550 | `nct6775` (ASUS, ASRock, MSI's original X570 boards); `nct6687d-dkms-git` on MSI B550 and X570S boards; `it87-dkms-git` on Gigabyte; `nct6683` (read-only) for the B550 Taichi's EC | Out-of-tree drivers become common here |
| **AM5 600-series** (X670E, X670, B650E, B650, A620) | ASUS X670/B650, MSI B650/X670, ASRock A620/B650/X670, Gigabyte X670/B650 | `nct6775` (ASUS NCT6799D, ASRock NCT6796D-S); `nct6687d-dkms-git` (MSI); `it87-dkms-git` (Gigabyte); ASRock NCT6686D boards need `asrock-nct6683` or `nct6687d` for PWM; plus ASUS EC helpers | Monitoring often works before write/control does |
| **AM5 800-series** (X870E, X870, B850, B840) | ASUS X870/B850, MSI X870/B850, Gigabyte X870/B850, ASRock X870/B850 | `nct6775` (ASUS NCT6701D, reported as `nct6799`); `nct6687d-dkms-git` with the msi_alt1 map (MSI); `it87-dkms-git` (Gigabyte); ASRock mixes NCT6796D-S and NCT6686D | Expect model-specific exceptions; support is still evolving |

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
| `it87-dkms-git` | https://aur.archlinux.org/packages/it87-dkms-git | https://github.com/frankcrawford/it87 | Gigabyte boards with ITE IT8688E/IT8689E/IT8696E/IT8625E/IT8686E; ASUS AM4 300/400-series boards with ITE IT8665E. **Builds from 2026-09-09 rename Gigabyte chips** (`it8696_a008090a`) — see doc 19 before rebuilding |
| `nct6687d-dkms-git` | https://aur.archlinux.org/packages/nct6687d-dkms-git | https://github.com/Fred78290/nct6687d | MSI boards with the Nuvoton NCT6687D, and some ASRock NCT6686D boards (with MSI's fan labels). No ASUS board is known to use it — ASUS boards that tried report "No such device" ([nct6687d #104](https://github.com/Fred78290/nct6687d/issues/104)) |

There is **no** AUR package for the ASRock-specific `asrock-nct6683` or
`nct6686d` sources — build those from their repositories if your board needs
them.

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
`/diagnostics/hardware` endpoint and the GUI's System State page (DEC-104).

### 1. NCT6797D vs the out-of-tree `nct6687` driver

Older builds of the out-of-tree `nct6687` driver (`nct6687d-dkms-git` on
the AUR) declare chip ID `0xd450`. This is the **legitimate chip ID
assigned to NCT6797D**, the chip on AM4 MSI boards such as the B450M
MORTAR and MAG B450 TOMAHAWK MAX (the X470 GAMING PRO and X470 GAMING PRO
CARBON are NCT6795D). When both `nct6687` and `nct6775` are loaded at the
same time, whichever driver binds first claims the chip — and the other may
scribble into the wrong registers, which in at least one upstream report
**bricked the CPU_FAN header on an MSI MAG X570 TOMAHAWK WIFI**
([ublue-os/bazzite #4498](https://github.com/ublue-os/bazzite/issues/4498);
the chip reads `0xd451`, which masks to `0xd450`). **The `0xd450` claim was
removed upstream in
[Fred78290/nct6687d PR #164](https://github.com/Fred78290/nct6687d/pull/164)
(merged 2026-05-19)**, so a current `nct6687d` build no longer claims the chip
by default. The risk remains three ways: already-loaded modules,
not-yet-updated packages, and **`force=1`**, which since
[PR #174](https://github.com/Fred78290/nct6687d/pull/174) attaches to any
Nuvoton chip ID from `0xD000` to `0xDFFF` — NCT6795D/6797D/6798D/6799D
included.

**Remediation:**

- Identify the chip first. `sudo dmesg | grep -i 'found nct'` shows which
  driver found which chip at which address; two drivers reporting a chip at the
  same address means both claimed it.
- If your board has **NCT6795D / NCT6797D / NCT6798D**, blacklist `nct6687`,
  and never load it with `force=1`:

  ```bash
  echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf
  sudo update-initramfs -u  # Debian/Ubuntu — not needed on Arch
  ```

- AM4 400-series MSI boards do not carry an NCT6687D at all; MSI moved to it
  with B550.
- Bazzite #4498 was closed on 2026-09-07 without a distro fix. Since
  2026-08-15 Bazzite instead ships the Terra `nct6687d` kmod, which autoloads
  `nct6687` and blacklists `nct6683` on every machine. That is safe on an
  NCT6797D board only because a current `nct6687d` no longer claims `0xd450`.

The System State page surfaces this as a CRITICAL `module_collisions`
banner when both modules are loaded simultaneously, and discourages PWM
writes until the user resolves the load ordering.

### 2. ASUS: an ITE chip, and a WMI firmware bug

**The fan chip on ASUS AM4 300/400-series boards is an ITE IT8665E, not a
Nuvoton part** — "Chip markings and sensors-detect say that this is IT8665E"
on a PRIME X470-PRO
([frankcrawford/it87 #27](https://github.com/frankcrawford/it87/issues/27)), and
the same on the ROG STRIX X470-F / X470-I and B450-F. Mainline has no IT8665E
driver, so fan control needs the out-of-tree `it87-dkms-git`; use a build from
2026-07-22 or later (older builds mangled IT8665E writes under the MMIO default,
[issue #106](https://github.com/frankcrawford/it87/issues/106)). This section
said NCT6798D until 2026-09-24.

The mainline `asus_wmi_sensors` driver supports 16 boards by exact name,
including these AM4 400-series ones:

- PRIME X470-PRO
- ROG CROSSHAIR VII HERO
- ROG CROSSHAIR VII HERO (WI-FI)
- ROG STRIX B450-E GAMING
- ROG STRIX B450-F GAMING (and B450-F GAMING II)
- ROG STRIX B450-I GAMING
- ROG STRIX X470-F GAMING
- ROG STRIX X470-I GAMING

Upstream kernel docs **specifically warn** that some ASUS BIOSes
implement the WMI interface badly: fans stopping, fans pinned at
maximum, or frozen readings, more likely the more often it is polled.
PRIME X470-PRO is called out by name as particularly affected. The kernel
names **no safe polling rate**; its advice is a soak test while polling
before leaving the machine unattended, and a BIOS whose WMI method version is
2 or later (the driver refuses older versions). The driver itself calls the
BIOS at most about once a second per sensor group, however many programs read
its files — so extra readers do not multiply WMI calls, and this guide's
earlier claim that the daemon's 1 Hz poll sat "within the safe band" had no
source.

Note that `asus_wmi_sensors` (hwmon name `asus_wmi_sensors`) and
`asus_ec_sensors` (hwmon name `asusec`) are **sensor-read drivers**. On these
boards the PWM control path is the IT8665E, through `it87-dkms-git`.

### 3. Gigabyte AM4 AORUS dual-chip topology

AM4 400-series Gigabyte AORUS boards (X470 AORUS ULTRA GAMING, X470
AORUS GAMING 5/7 WIFI, B450 AORUS PRO / PRO WIFI / PRO-CF) use a two-chip
topology: a primary **IT8686E** at I/O 0x0a40 and a secondary **IT8792E** at
0x0a60. Both require the out-of-tree `it87-dkms-git` driver. On the B450 AORUS
PRO the IT8792E carries no fan header at all — all five are on the IT8686E — so
a missing secondary there costs temperatures and voltages, not fans.

If only N of the expected fan headers appear in `sensors`, the
System State page reports a dual-chip enumeration gap. Update the driver
first (2026-03+ snapshots default `mmio=on` and carry the ISA-bridge MMIO
path; older builds need `options it87 mmio=on`), then follow the recovery
ladder in doc 19 § ITE: keep `nct6775` / `w83627ehf` and `sensors-detect` away
from the Super-I/O, reboot, and if the chip is still missing, remove mains
power.

```bash
# 1. Update the driver — 2026-03+ snapshots default mmio=on.
#    (Builds from 2026-09-09 rename Gigabyte chips — see doc 19 first.)
yay -S it87-dkms-git

# 2. Only on older (pre-2026-03) builds:
sudo tee /etc/modprobe.d/it87.conf <<<'options it87 mmio=on'
```

The historic note that the secondary IT8792E was read-only on some AM4
Gigabyte boards still applies; verify per-header writability via the
PWM Verify action on the System State page before assigning fans
to it in a profile.

### 4. ASRock AM4 — generally smooth

AM4 ASRock boards use NCT6779D (300/400-series ATX and micro-ATX: B450 Pro4,
B450 Steel Legend, X470 Taichi), NCT6792D (the ITX boards: B450 / X470 / B550M
Gaming-ITX/ac) or NCT6793D (the HDV R4.0 boards). All are covered by mainline
`nct6775`, whose PWM files are always writable — the daemon switches a header to
manual itself. If a fan does not follow, check that the header's BIOS fan type
matches the fan (DC for 3-pin, PWM for 4-pin).

ASRock AM4 boards rarely need any of the out-of-tree drivers that the
AM5 generation forces on you. If you're considering installing
`nct6686d` or `nct6687d` "to be safe", **don't** — see hazard #1. (The B550
Taichi is the exception — see below.)

---

## AM4 500-series specifics

The B550 / X570 / A520 generation is the most heterogeneous on Linux —
all four Super-I/O chip families ship in this generation and each
vendor uses a different one. Coverage was hardened in DEC-106 and re-checked
board by board in DEC-421; the practical hazards by vendor:

### MSI (NCT6687D or NCT6797D)

MSI's AM4 500-series boards split into two non-interchangeable camps:

- **MAG B550 TOMAHAWK / B550-A PRO / MPG B550 GAMING PLUS / MPG B550 GAMING
  EDGE, and the 2021 MPG X570S EDGE MAX WIFI / CARBON MAX WIFI** ship the
  **NCT6687D** (it reports `0xd592`; both drivers match it as `0xd590`). The
  in-kernel `nct6683` reads it but publishes the PWM files read-only. Use the
  out-of-tree `nct6687d-dkms-git` driver and blacklist `nct6683` — with both
  loaded they can bind the same chip and garble readings
  ([nct6687d #204](https://github.com/Fred78290/nct6687d/issues/204), B550-A
  PRO). No BIOS setting is involved; this section used to blame "Smart Fan
  Mode".
- **X570-A PRO, the original 2019 MPG X570 GAMING PRO CARBON WIFI / GAMING
  PLUS / GAMING EDGE WIFI, MAG X570 TOMAHAWK WIFI, MEG X570 ACE / UNIFY /
  GODLIKE, and MAG X570S TOMAHAWK / TORPEDO MAX** ship the **NCT6797D**
  (reports `0xd451`). The in-kernel `nct6775` driver supports them out of the
  box — DO NOT install `nct6687d` here, and never load it with `force=1`: the
  DEC-105 chip-ID overlap can corrupt non-volatile fan registers.

To identify which camp your board is in:

```bash
sudo dmesg | grep -i 'found nct'
ls -l /sys/class/hwmon/hwmon*/device/driver
```

`nct6775: Found NCT6797D …` means the mainline camp. `Found NCT6687D` from
either `nct6683` or `nct6687` means the out-of-tree camp — and note the hwmon
**name** alone cannot tell the two drivers apart, because mainline `nct6683`
also names the device `nct6687`.

### Gigabyte (IT8688E + IT8792E dual-chip)

Most AM4 500-series AORUS boards (X570 AORUS MASTER / PRO / PRO WIFI
/ ULTRA / XTREME, B550 AORUS MASTER / PRO, B550 VISION D) pair the primary
**IT8688E** with a secondary **IT8792E**. Single-chip variants (B550M AORUS
PRO, B550I AORUS PRO AX) ship only the IT8688E. The X570S refresh (X570S AERO
G, X570S AORUS MASTER) pairs an IT8689E with an IT87952E. All require the
out-of-tree `it87-dkms-git` driver.

The well-documented dual-chip enumeration trap applies here too: if
only N of the expected headers appear in `sensors`, update
`it87-dkms-git` first (2026-03+ builds default `mmio=on` and merge the
ISA-bridge MMIO path); on older builds set `options it87 mmio=on` in
`/etc/modprobe.d/it87.conf`. Then follow the doc 19 recovery ladder. Do not
run `sensors-detect` after boot (frankcrawford/it87 issue #70).

X570-generation boards can also lose **IT8792E fan control after
suspend/resume** (frankcrawford/it87 issue #99). The reporter found it working
after rebuilding from master in September 2026, and the author of PR #128
credits that patch's sleep/suspend changes, but the issue is still open. The
daemon re-asserts `pwm_enable` after resume; if headers stay stuck, a reboot is
the reliable reset.

### ASRock (mostly NCT6798D)

ASRock AM4 500-series ATX boards report an NCT6798D-class chip (B550 Steel
Legend — physically an NCT6796D-E — B550 Extreme4, B550 PG Velocita, X570
Taichi, X570 Steel Legend) and are covered by the in-kernel `nct6775` driver.
No out-of-tree driver needed.

The exception is the **B550 Taichi and B550 Taichi Razer Edition**: their fans
are on an NCT6683D-class EC (hwmon `nct6683`), and the NCT6798D beside it has no
fans wired. The in-kernel `nct6683` reads that EC but publishes PWM read-only;
`branchmispredictor/asrock-nct6683` lists both boards and makes it writable.

### ASUS (mostly NCT6798D + extra sensor drivers)

AM4 500-series ASUS boards (TUF GAMING X570-PLUS, ROG STRIX X570 /
B550 series, PRIME X570-PRO) ship **NCT6798D** covered by mainline
`nct6775`. The PWM control path is `nct6775`. The `asus_ec_sensors`
(`asusec`) and `asus_wmi_sensors` drivers (when they bind) are sensor
enrichment ONLY — they never provide PWM writes.

If `nct6775` fails to bind because of an ACPI conflict on I/O ports
`0x0290-0x0299`, add `acpi_enforce_resources=lax` to kernel boot
parameters or disable "ACPI Hardware Monitor" in BIOS. Since Linux
5.16 the driver can read supported ASUS boards through an ASUS WMI
access path (`access_asuswmi`) that sidesteps the port reservation,
often removing the need for `acpi_enforce_resources=lax` (this is a WMI
sensor-read path, not an "ACPI mutex" — the separately-proposed
ACPI-mutex patch was never merged; see doc 19 for the source).

---

## AM5 600-series specifics

The B650 / X670 / A620 generation is where the out-of-tree drivers
become near-mandatory and chip variation between SKUs is high.

### MSI (NCT6687D, default register map)

AM5 600-series MSI boards (MAG B650 TOMAHAWK WIFI, MAG X670E TOMAHAWK WIFI,
MPG X670E CARBON WIFI, MPG B650 CARBON WIFI, PRO X670-P WIFI) ship the
NCT6687D with the **default** register map. Use `nct6687d-dkms-git`, blacklist
`nct6683`, and **never** force `fan_config=msi_alt1` here — it makes every
SYS_FAN read 0 RPM
([nct6687d #167](https://github.com/Fred78290/nct6687d/issues/167)). The
DEC-105 chip-ID overlap does **not** apply to these boards, because their chip
reports `0xd592`, not `0xd451` — the brick scenario needs `nct6687` claiming an
NCT679x. See the MSI section for the auto-allowlist details.

### Gigabyte (IT8689E, dual boards + IT8792E)

AM5 600-series Gigabyte boards ship the **IT8689E**. The dual-chip ones (X670E
AORUS MASTER, X670E AORUS PRO X) pair it with an **IT8792E** — hwmon `it8792`,
not `it87952` ([#96](https://github.com/frankcrawford/it87/issues/96) dmesg:
"Found IT8792E/IT8795E chip at 0xa60, revision 3"). The X670 AORUS ELITE AX and
the X670 / B650 GAMING X AX boards are single-chip (IT8689E only), according to
the it87 SIV catalogue. The daemon's board table said otherwise for the X670E
AORUS MASTER and X670 AORUS ELITE AX until 2026-09-24, and warned about a
missing chip on working boards.

**IT8689E before PR #128 — EC override, partial BIOS-curve stopgap.** On driver
builds older than 2026-08-24, IT8689E boards (Rev 1 especially, e.g. X670E
AORUS MASTER) ACCEPTED PWM writes with zero effect on fan speed **while a
normal BIOS fan curve was active** — the chip's extra vector curves overrode
manual mode ([issue #96](https://github.com/frankcrawford/it87/issues/96)). The
fork's documented stopgap (its README, 2026-03-30 to 2026-08-24, IT8689E boards
only) was a BIOS curve of **PWM 40,40,40,40,40,40,100 at temperatures
0,90,90,90,90,90,90** — lowering 90 to the BIOS maximum where it is capped. The
#96 thread states it as "set every vector's temperature to 90", but every
published recipe keeps point 1 at 0 °C. On the reporter's board it reliably
restored only the **CPU-fan** header. **No upstream source recommends a curve
with a 0% point, and you must never use one**: the BIOS curve runs the fans at
boot, after the daemon hands a header back, when `it87` unloads and across
suspend.

**Driver-side fix: PR #128, merged 2026-08-24.**
[PR #128](https://github.com/frankcrawford/it87/pull/128) fixes manual mode for
IT8688, IT8689, IT8790, IT8792/IT8795 and IT87952 (it disables the extra
vectors itself), adds the H2RAM extra fan channels found on high-end Gigabyte
boards, and moves the Gigabyte path from WMI to SMI.

**Reports.** Three users confirmed working IT8689E manual PWM on 2026-08-23 —
on a Z790 AORUS MASTER rev 1.0 carrying **IT8689E revision 1** (with a measured
duty→RPM response across five duty steps, and clean restore to firmware
control), on a single-IT8689 B650 Eagle AX, and on a B760M H — none of them
needing BIOS-side tweaks. They tested PR head `429d2b40`, which was amended to
`27319db7` before merging, reworking the Intel H2RAM bridge error handling.
Since the merge, a B660M GAMING AC DDR4 (rev 1, working after a reboot) and a
B550M DS3H R2 (rev 2, [#115](https://github.com/frankcrawford/it87/issues/115))
have reported working. None yet covers a dual-chip IT8689E board or an it87
v2.0 build. The earlier [PR #114](https://github.com/frankcrawford/it87/pull/114)
was **rejected on 2026-08-25** and is not a pending fix.

So: update `it87-dkms-git` (reading doc 19's v2.0 rename note first), then
verify with Test PWM Control. If a header still cannot be controlled on a
current build:

- Use a different fan header if your board has one.
- Attach affected fans to an external controller (OpenFanController).
- Let the BIOS curve drive that header — and keep that curve free of 0% points.

### ASUS (NCT6799D + asus_ec_sensors)

AM5 600-series ASUS boards (ROG STRIX X670E series, ROG CROSSHAIR X670E,
TUF GAMING X670E-PLUS, PRIME B650-PLUS) ship an **NCT6799D(-R)**, which mainline
`nct6775` reports as `nct6799`
([zeule/asus-ec-sensors #45](https://github.com/zeule/asus-ec-sensors/issues/45);
[lm-sensors #416](https://github.com/lm-sensors/lm-sensors/issues/416): "this
board (ASUS TUF GAMING X670E-PLUS WIFI) has indeed a Nuvoton NCT6799D-R chip").
This section said NCT6798D until 2026-09-24. Many also get board-level
temperature / voltage enrichment from `asus_ec_sensors` (hwmon `asusec`) — check
`docs.kernel.org/hwmon/asus_ec_sensors.html` for your SKU and kernel.

The PWM control path is `nct6775`. `asus_ec_sensors` provides
extra-detail temperatures only.

### ASRock (NCT6686D or NCT6796D-S — check your board)

ASRock's AM5 boards split by family, per the block diagrams in ASRock's own
manuals:

- **Steel Legend / LiveMixer / Lightning ITX** (B650 / X670E Steel Legend,
  B650 LiveMixer, A620I / B650I Lightning WiFi): **NCT6686D only**. The
  in-kernel `nct6683` reads it (some need `force=1`) but publishes PWM
  **read-only** — writes are refused, not "silently ignored" as this section
  used to say. PWM needs an out-of-tree driver:
  - `asrock-nct6683` (github.com/branchmispredictor/asrock-nct6683) — lists
    A620I / B650I Lightning WiFi and X670E Steel Legend by exact name;
  - `nct6687d` (Fred78290/nct6687d) — works on the B650 LiveMixer and others,
    with MSI's register map and fan labels;
  - `nct6686d` (github.com/s25g5d4/nct6686d) — tested only on the A620I
    Lightning WiFi.
- **Pro RS / PG Lightning / HDV** (B650 Pro RS, X670E Pro RS, X670E PG
  Lightning, A620M Pro RS, B650M-HDV/M.2): **NCT6796D-S only**, reported as
  `nct6799` — mainline `nct6775`, nothing else needed.
- **Taichi** (X670E Taichi, B650E Taichi and their Lite editions): both chips —
  see the X870E Taichi Lite entry below.

Test PWM write capability on a non-critical chassis fan header
first. ASRock boards are a strong candidate for per-model driver
selection rather than a single rule.

### ASRock X870E Taichi Lite — legitimate dual-Nuvoton

The **ASRock X870E Taichi Lite** (AM5 800-series, included here for
contiguity) ships TWO Super-I/O chips: an **NCT6686D** at I/O `0x0a20` (bound
by `nct6687d`, or read-only by the in-kernel `nct6683`) plus an **NCT6796D-S**
at I/O `0x0290`, which mainline `nct6775` reports as "NCT6796D-S/NCT6799D-R"
(hwmon `nct6799`), behind a Fintek F85227N eSPI→LPC bridge. Both drivers MUST
be loaded to control all fans. The same pair is on the X670E / X870E Taichi and
B650E Taichi (Lite).

**Pump label hazard.** On an X870E Taichi an owner measured CHA_FAN1/2, CPU_FAN2
and AIO_PUMP on the `nct6799` chip and CHA_FAN3/4 on the NCT6686D
([nct6687d #155](https://github.com/Fred78290/nct6687d/issues/155)). `nct6687d`
names the NCT6686D's channels with MSI's labels, so its "Pump Fan" is a chassis
header there, while the real pump header is unlabelled. Assign the pump role to
the real pump header in the fan wizard so the pump floor covers it.

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

The B850 / X870 generation is currently the hardest on Linux. Per vendor:
Gigabyte dual-chip IT8696E + IT87952E (with some single-chip ELITE boards);
ASRock NCT6796D-S and/or NCT6686D; ASUS **NCT6701D** (reported as `nct6799`)
+ `asus_ec_sensors`; MSI NCT6687D with the alternate "msi_alt1" register map.

The notable additions specific to this generation:

### ASUS — NCT6701D

ASUS 800-series AM5 boards (and Intel Z890 / B860) carry an **NCT6701D**,
chip ID `0xd806`. `sensors-detect` calls it an unknown chip; mainline `nct6775`
binds it as `nct6799` ("NCT6796D-S/NCT6799D-R or compatible"). Fans and voltages
read correctly, but most temperature channels are not meaningful
([lm-sensors #542](https://github.com/lm-sensors/lm-sensors/issues/542),
[#544](https://github.com/lm-sensors/lm-sensors/issues/544)) — drive curves from
`k10temp` Tctl or another sensor you can verify. X870 / X870E boards reach the
chip through the ASUS WMI path from kernel 7.1; **no B850 board is on that list**,
so B850 boards use the I/O ports directly and are likelier to hit an ACPI
conflict. One ROG STRIX X870E-E owner measured the firmware switching a header
straight back from manual to full speed
([zeule/asus-ec-sensors #100](https://github.com/zeule/asus-ec-sensors/issues/100),
before 7.1); whether the WMI path changes that is unverified. Run Test PWM
Control. `asus_ec_sensors` coverage arrived board by board: ProArt
X870E-CREATOR WIFI 6.17; ROG STRIX B850-I / X870-I / X870E-E 6.18; X870E-H /
X870-F 6.19; B850-E 7.2; ROG CROSSHAIR X870E HERO and X870E-E WIFI7 R2 7.3.

### MSI nct6687d auto-allowlist

The `nct6687d` driver ships an **auto-enabled board list** of MSI B840 / B850 /
B860 / X870 / X870E / Z890 boards. On listed boards the `msi_alt1` register
layout is selected automatically, without a module parameter. The chip is the
same NCT6687D as earlier MSI boards (it reports `0xd592`); only the EC register
layout differs, and monitoring tools label these boards *NCT6687DR*. The list
keeps growing, and each entry is a full DMI board name with its MS-number — so a
PZ / WHITE / MAX edition can be missing while its sibling is listed. The source
of truth is `nct6687.c::nct6687_msi_alt_boards[]` in the Fred78290/nct6687d
repository; check it rather than trusting any point-in-time count.

If your MSI board of those series is NOT on the list and system fans
don't respond to PWM writes, manually enable:

```bash
sudo modprobe -r nct6687
sudo modprobe nct6687 fan_config=msi_alt1
# persist:
sudo tee /etc/modprobe.d/nct6687.conf <<<'options nct6687 fan_config=msi_alt1'
```

`msi_fan_brute_force=1` is a **separate, current** parameter (upstream marks it
BETA) — not a legacy alternative to `fan_config`. It writes the duty into all 7
BIOS curve points for system fans (saving and restoring the originals), and it
requires `nct6683` to be blacklisted. Several boards needed it before
system-fan writes stuck — PRO X870E-P WIFI, MAG X870E TOMAHAWK WIFI, PRO Z890-P
WIFI, MAG Z890 TOMAHAWK WIFI, and PRO B850M-P WIFI, where current builds
returned `EIO` even for CPU_FAN until it was on
([nct6687d #107](https://github.com/Fred78290/nct6687d/issues/107),
[#148](https://github.com/Fred78290/nct6687d/issues/148),
[#185](https://github.com/Fred78290/nct6687d/issues/185),
[#202](https://github.com/Fred78290/nct6687d/issues/202)).

### ASRock X870 Nova WiFi — NCT6796D-S

The ASRock X870 Nova WiFi ships a single **NCT6796D-S** (its manual's block
diagram), which mainline `nct6775` reports as `nct6799`; nothing else is needed,
and do not load `nct6687d` here. (Fred78290/nct6687d issue #153, which this
section used to cite, matches the two-chip X870E Nova — NCT6796D-S + NCT5585D —
rather than this board.) ASRock's B850 / X870 Steel Legend WiFi and B850I
Lightning WiFi carry an **NCT6686D only** — read-only in the kernel driver, as on
the 600-series Steel Legend boards.

### A secondary Super-I/O that will not enumerate — a latched bridge, and how to clear it

**Rewritten three times. 2026-09-04 (DEC-326) withdrew a "use `mmio=on`" account
that was wrong. 2026-09-05 (DEC-332) withdrew that rewrite's own conclusion —
"there is no local fix" — after a controlled experiment produced both the cause
and the cure. 2026-09-24 (DEC-421) withdrew the "`0x8883` and `0xFFFF` are
different faults" split: they are two readings of one blocked state, and neither
is visible in the kernel log by default. The measurements below are unchanged.**

An ITE eSPI-to-LPC **bridge** latched in configuration mode answers in place of
the chip behind it. It answers `0x8883` to a read with the Super-I/O unlock key,
and `0xFFFF` to one without it ([#70](https://github.com/frankcrawford/it87/issues/70)).
The driver finds the primary IT8696E over MMIO and then cannot find the
secondary; one hwmon device enumerates instead of two, costing 3 of 8 fan headers
and 3 of 9 temperatures on an X870E AORUS MASTER. The driver prints the failing
ID only at debug level (`pr_debug`), and a `0xFFFF` read makes it give up
without printing anything. So there is no log line to choose a remedy from, and
the remedy is one ladder. **Recoverable — see below.**

#### What latches it, measured

`nct6775` and `w83627ehf` share one `superio_enter()`:

```c
static inline int superio_enter(int ioreg) {
        if (!request_muxed_region(ioreg, 2, DRVNAME)) return -EBUSY;
        outb(0x87, ioreg);      /* unconditional */
        outb(0x87, ioreg);
        return 0;
}
```

Both call it and *then* read `SIO_REG_DEVID`, on both 0x2E and 0x4E. That
unconditional write is what puts the bridge into config mode. `it87` does the
opposite — `superio_enter(sioaddr, /*noentry=*/true)` reads the DEVID with no
unlock and unlocks only if that returns `0xffff` — which is why `it87` is safe
and the other two are not.

Measured on an X870E AORUS MASTER, 2026-09-05, within a single boot:

| Step | Result |
| --- | --- |
| Control: reload `it87` alone | `it87952` stays bound — a reload writes nothing |
| Load `nct6775`, reload `it87` | `it87952` gone (with dynamic debug on: `Unsupported chip (DEVID=0x8883)`) |

`nct6775` **fails to load** on this board (`could not insert 'nct6775': No such
device`) and does the damage anyway, because the port write happens before the
`-ENODEV`. `w83627ehf` was not tested individually; it is implicated by having
byte-identical unlock behaviour in the same function, not by measurement.

#### Clearing it

1. Stop `nct6775` and `w83627ehf` loading, and do not run `sensors-detect`. The
   `control-ofc-daemon` package ships
   `/usr/lib/modprobe.d/control-ofc-superio.conf`, which suppresses both
   automatically on the Gigabyte boards it lists. To do it by hand, use
   `install <mod> /bin/true` — **not** `blacklist`, which the explicit `modprobe`
   issued by systemd ignores.
2. **Reboot** and rescan. Upstream's first step, and enough when nothing re-arms
   the latch at boot.
3. **Still missing: power the machine down at the wall.** On the X870E AORUS
   MASTER the latch survived a warm reboot *and* a soft power-off, because the
   bridge keeps standby power (ITE lists the IT8883 as "3VSB and VBAT
   Supported"). This is the step people skip, and skipping it produces a false
   negative.
4. Verify: `sudo dmesg | grep -i it87` should show a second `Found IT...E chip`
   line, and `sensors` two `it8*` chips.

Why the *old* advice still cannot work, measured rather than argued:

| Old advice | Why it fails |
| --- | --- |
| "load with `mmio=on`" | `mmio` already defaults to `true` (`it87.c:314`). The test host passes the module no parameters at all, so this named a state already in effect. |
| "update to a current build" | The failure reproduces at upstream HEAD. It is not a driver bug. (And a build from 2026-09-09 on renames the chips — doc 19.) |
| "read the DEVID in `dmesg` and pick a fix" | The ID is printed only with dynamic debug on, `0xFFFF` never, and both readings are cleared by the same ladder. |
| "per issue #81" | #81's *opening post* forced the ID and set `mmio=on` and still lost three fans and a pump. Its later comments record the reporter getting the second chip working — the opposite of what the opening post shows. |

The driver contains **no** case, constant or comment for `0x8883` anywhere,
while `IT87952E_DEVID 0x8695` **is** defined and handled — so while the bridge
is latched the secondary is **unreachable, not unsupported**.

**Bounded by pairing, not by family.** Other boards with the same IT8696E +
IT87952E pairing work out of the box — the X870E AORUS ELITE X3D is
owner-confirmed with both chips controllable
([#89](https://github.com/frankcrawford/it87/issues/89)). Do not generalise this
to "X870E" or to "dual-ITE".

The GUI's chip-guidance database reports this honestly, so a user who searches
for "IT8883" is told what they are looking at and how to clear it, rather than
being sent round a loop or told to give up.

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

2. **PWM control** — the actual fan control path is the Super I/O chip:
   `nct6775` on AM4 500-series and all AM5 ASUS boards (NCT6798D, NCT6799D,
   NCT6701D), but the out-of-tree `it87` on AM4 300/400-series boards, which
   carry an ITE IT8665E.

If your ASUS board exposes both `asusec` (the `asus_ec_sensors` hwmon device)
and `nct6799`/`nct6798` in `/sys/class/hwmon/*/name`, use the Nuvoton device for
fan control and `asusec` for enriched sensor data. Do not attempt to write PWM
through the EC or WMI interface.

#### asus_ec_sensors supported boards (AMD, from the kernel driver)

The driver lists boards by exact DMI name, and the list grows with every
kernel — 55 boards in 7.2, 60 in the 7.3 release candidates. A board added in a
recent kernel is **not** in the 6.12 / 6.18 LTS kernels. AMD boards in the list
as of the 7.3 release candidates, with the kernel that added the newer ones:

- PRIME X470-PRO, PRIME X570-PRO, PRIME X670E-PRO WIFI
- ProArt B550-CREATOR, ProArt X570-CREATOR WIFI, ProArt X670E-CREATOR WIFI,
  ProArt X870E-CREATOR WIFI (6.17)
- Pro WS X570-ACE; Pro WS TRX50-SAGE WIFI (and A); Pro WS WRX90E-SAGE SE
- ROG CROSSHAIR VIII DARK HERO / HERO (and WI-FI) / FORMULA / IMPACT
- ROG CROSSHAIR X670E EXTREME / HERO / GENE; ROG CROSSHAIR X870E HERO (7.3)
- ROG STRIX B550-E / B550-I; ROG STRIX B650E-E / B650E-I
- ROG STRIX B850-I GAMING WIFI (6.18); ROG STRIX B850-E GAMING WIFI (7.2)
- ROG STRIX X470-I GAMING (6.19); ROG STRIX X470-F GAMING (7.1)
- ROG STRIX X570-E (and WIFI II) / X570-F / X570-I
- ROG STRIX X670E-E / X670E-I
- ROG STRIX X870-I / X870E-E (6.18); X870-F / X870E-H (6.19); X870E-E GAMING
  WIFI7 R2 (7.3)
- ROG ZENITH II EXTREME (and ALPHA)
- TUF GAMING X670E-PLUS (and WIFI)

No B850-A, B850-F, B840 or other B850 STRIX board is listed. The device's hwmon
name is `asusec`. From 7.3 an unconnected T_Sensor or water-probe socket reads
as unavailable rather than as a -62/-60/-40 °C placeholder.

Reference: https://docs.kernel.org/hwmon/asus_ec_sensors.html

#### ASUS WMI polling risk

The kernel documentation for `asus_wmi_sensors` carries a strong warning:
some ASUS BIOS WMI implementations are buggy and frequent polling can cause
**fans stopping**, **fans getting stuck at maximum speed**, or **sensor
readings freezing**. The PRIME X470-PRO is called out as particularly bad.

The kernel names **no safe polling rate**. Its advice is a soak test while
polling before leaving the machine unattended, and a BIOS whose WMI method
version is 2 or later — the driver refuses older versions. The driver calls the
BIOS at most about once a second per sensor group, however many programs read
its files. (This section used to say the daemon's 1 Hz poll was "within safe
limits"; there is no such limit to be within.) The boards it supports are X370
/ X470 / B450 / X399 boards whose fan chip is an ITE IT8665E where it has been
checked — see "AM4 400-series specifics".

Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

#### ASUS ACPI resource conflicts

ASUS boards with Nuvoton chips may have ACPI OpRegion conflicts on I/O
ports 0x0290-0x0299. The daemon's diagnostics endpoint detects these
conflicts. Remediation options:

1. **Preferred (kernel >= 5.16, ASUS boards):** The `nct6775` driver can read
   the chip through an ASUS WMI access path (`access_asuswmi`), sidestepping the
   conflict without kernel parameters. (A WMI sensor-read path, not an "ACPI
   mutex" — the separately-proposed ACPI-mutex patch was never merged.)
2. **Fallback:** Add `acpi_enforce_resources=lax` to kernel parameters.
3. **BIOS:** Disable "ACPI Hardware Monitor" if the option is available.

Reference: https://docs.kernel.org/hwmon/nct6775.html

---

### MSI

#### Common setup

MSI AMD boards from B550 onward (except the MAG X570S TOMAHAWK / TORPEDO MAX,
which kept the NCT6797D) use the **Nuvoton NCT6687D**, which reports `0xd592`.
The typical Linux pattern:

- The in-kernel `nct6683` driver may load and expose monitoring data.
- But it publishes every `pwmN` **read-only** on MSI boards — it enables PWM
  writes only on Mitac OEM systems — and it names its device `nct6687` too, so
  the hwmon name does not tell you which driver is bound.
- The out-of-tree `nct6687d-dkms-git` driver is required for actual fan
  control, with `nct6683` blacklisted.

A confirmed success case: **MSI MPG B550I GAMING EDGE (MAX) WIFI** works with
the out-of-tree `nct6687d` driver's default register map.

Reference: https://github.com/Fred78290/nct6687d/issues/3

#### BIOS requirements

None for writability. This section used to say "disable Smart Fan Mode or
headers appear read-only"; that was never the cause. Read-only headers mean the
in-kernel `nct6683` is bound instead of `nct6687d` — check with
`ls -l /sys/class/hwmon/hwmon*/device/driver` and blacklist `nct6683`. For 3-pin
fans, set the header's fan type to DC in the BIOS.

#### X870/B850 7-point write quirk

Newer MSI boards (B840 / B850 / B860 / X870 / X870E / Z890 — the "msi_alt1"
register map) have a specific quirk: single PWM register writes may not change
system fan speeds, because the EC's own curve keeps driving them. Writing the
duty into all 7 BIOS fan-curve points does.

The `nct6687d` driver's `msi_fan_brute_force` parameter (upstream marks it
**BETA**) does this. It is a separate, current parameter — not a legacy
alternative to `fan_config` — and it **requires `nct6683` to be blacklisted**:

```bash
echo "blacklist nct6683" | sudo tee /etc/modprobe.d/nct6683_blacklist.conf
echo "options nct6687 msi_fan_brute_force=1" | sudo tee /etc/modprobe.d/nct6687_msi.conf
echo "nct6687" | sudo tee /etc/modules-load.d/nct6687.conf
# then reboot
```

It only affects system fans, never CPU or pump fans. The driver snapshots the
original curve points before entering manual control and restores them when
automatic mode is written, when the module unloads, or when its fan-control
watchdog expires.

The driver keeps a board list, `nct6687.c::nct6687_msi_alt_boards[]`, that
auto-enables the `msi_alt1` map; it grows release by release and is the source
of truth. (Upstream's `TESTING_RESULTS.md`, which this section used to point at,
records one board's 2025-10 tests — an MAG X870E TOMAHAWK WIFI — rather than a
board matrix.) If your board is not on
the list but shows the same symptom, set `fan_config=msi_alt1` yourself, and add
`msi_fan_brute_force=1` if writes still do not stick.

References:
- https://github.com/Fred78290/nct6687d

#### Known limitations on MSI boards

- Historically **CPU_FAN** and **PUMP_FAN** worked before **SYS_FAN** did on
  the msi_alt1 boards. That is not guaranteed on current builds: on a PRO
  B850M-P WIFI even CPU_FAN returned `EIO` until brute force was enabled
  ([nct6687d #202](https://github.com/Fred78290/nct6687d/issues/202)).
- **3-pin DC fans** need the header's fan type set to DC in the BIOS. No report
  since brute force arrived isolates a DC-specific failure.
- The in-kernel `nct6683` gives sensor readouts with read-only PWM files;
  `nct6687d` is the control path.

#### Module conflict

If you switch from the in-kernel `nct6683` to the out-of-tree `nct6687d`,
you must blacklist `nct6683`: with both loaded they can bind the same chip at
once, which garbles readings and makes writes fail
([nct6687d #204](https://github.com/Fred78290/nct6687d/issues/204)):

```bash
echo "blacklist nct6683" | sudo tee /etc/modprobe.d/blacklist-nct6683.conf
```

Reference: https://forums.unraid.net/topic/190117-solved-blacklist-nct6683/

---

### ASRock

#### Current state

Many ASRock AMD boards carry a **Nuvoton NCT6686D** (the AM5 Steel Legend /
LiveMixer / Lightning boards, the Taichi boards) or an NCT6683D-class EC (B550
Taichi), sometimes beside an NCT67xx chip that `nct6775` drives. The in-kernel
`nct6683` driver supports the NCT668x part for monitoring only:

- **Monitoring (read) works** — temperatures, fan RPMs, and voltages are
  visible (some boards need `force=1`, because the driver binds only known
  customer IDs).
- **PWM is read-only** — the driver makes `pwmN` writable only on Mitac OEM
  systems and has no `pwmN_enable`. Writes are refused, not accepted and
  ignored; this section used to say the latter.

Do not assume that sensor visibility means fan control is functional.

#### Alternative drivers

Two community projects target ASRock boards specifically:

1. **asrock-nct6683** — an updated `nct6683` that enables PWM writes on the
   ASRock boards it lists by exact DMI name:
   - ASRock B550 Taichi and B550 Taichi Razer Edition
   - ASRock A620I Lightning WiFi and B650I Lightning WiFi
   - ASRock X570 Creator
   - ASRock X670E Steel Legend (added 2026-08)
   - ASRock Z370M Pro4
   - ASRock Z890 Nova WiFi

   Repository: https://github.com/branchmispredictor/asrock-nct6683 (no AUR
   package)

2. **nct6687d** — the MSI-oriented driver also drives several ASRock NCT6686D
   boards, e.g. the B650 LiveMixer
   ([#103](https://github.com/Fred78290/nct6687d/issues/103)) and the X870E
   Taichi Lite. It applies **MSI's register map and MSI's fan labels**, so
   voltages can be wrong and a channel labelled "Pump Fan" may be a chassis
   header — check before trusting it. `nct6687d-dkms-git` on the AUR.

3. **nct6686d** — a kernel module for the NCT6686D based on the NCT6687D
   driver, tested only on the A620I Lightning WiFi; its author reports it
   still targets kernel 6.8. No AUR package.

   Repository: https://github.com/s25g5d4/nct6686d

#### sensors-detect gaps

`sensors-detect` reports "Found unknown chip with ID 0xd441" for the NCT6686D
on ASRock boards such as the X670E Steel Legend, Z890I Nova, Z890 Lightning and
X870E Taichi, although loading `nct6683` with `force=1` works (read-only).

Reference: https://github.com/lm-sensors/lm-sensors/issues/499

#### Troubleshooting steps

1. Check your board's chips (the manual's block diagram names them, and
   `cat /sys/class/hwmon/hwmon*/name` shows what bound).
2. Headers on the NCT67xx chip (`nct6798` / `nct6799` / `nct6792` / `nct6779`)
   are writable under `nct6775` — test one on a non-critical chassis fan.
3. Headers on the NCT6686D / NCT6683D are read-only under the in-kernel
   `nct6683` (`ls -l` shows `-r--r--r--`). Try one of the alternative drivers
   above, matching your board model.
4. Report your findings — ASRock driver support is actively evolving.

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

On a current driver build usually **none** — two boards were reported working
with no BIOS change at all. The rules that do apply:

1. **Never give the BIOS curve a 0% point.** It runs the fans during POST, after
   the daemon hands a header back, when `it87` unloads, across suspend and
   whenever the daemon is not running.
2. For a 4-pin fan set the header's **FAN Control Mode** to **PWM**. There is
   no "FAN Control by … Temperature" item in the Smart Fan 5/6 manuals checked
   (this section used to name one); *Fan Control Use Temperature Input* only
   picks the reference sensor.
3. *Full Speed* in **Fan Speed Control** is a fail-safe, not a fix: the
   firmware runs the fan at 100% whenever it owns it, but on some boards it
   also locks Linux out of the header
   ([#115](https://github.com/frankcrawford/it87/issues/115)), and before PR
   #128 one owner saw Full-Speed fans drop to 0% as the module loaded
   ([#79](https://github.com/frankcrawford/it87/issues/79)).

The headers are **not** read-only: `pwmN` and `pwmN_enable` are 0644 in both the
fork and mainline, and a write while the header is in automatic mode returns
`EBUSY` by design. What goes wrong on some boards is that the chip's firmware
logic keeps or retakes the fan, which PR #128 (2026-08-24) addresses. This
section used to say the registers were "locked by the BIOS"; no source supports
that.

#### MMIO requirement

The out-of-tree `it87` driver enables MMIO (Memory-Mapped I/O) by default
since the 2026-03 builds ([PR #95](https://github.com/frankcrawford/it87/pull/95);
`mmio=off` is the opt-out), which is necessary for fan control on newer
Gigabyte motherboards. If you are using an older version of the driver,
ensure MMIO is enabled with `options it87 mmio=on`. Do not disable it
unless you have a specific reason — the one documented reason is the
**IT8665E** (ASUS AM4 300/400-series boards and X399-era boards such as the ROG
Zenith Extreme): the 2026-03+ MMIO
default *broke* its PWM writes
([issue #106](https://github.com/frankcrawford/it87/issues/106), closed),
fixed at the driver level by [PR #120](https://github.com/frankcrawford/it87/pull/120)
(merged 2026-07-22, removes the MMIO path for IT8665E) — so **update the DKMS
build** (`it87-dkms-git`); `options it87 mmio=off` is the fallback for builds
older than the merge.

#### IT8689E manual control limitation (builds before 2026-08-24)

On driver builds older than PR #128, some Gigabyte boards with IT8689E chips
(Rev 1 especially) did not respond to manual PWM control while a normal BIOS fan
curve was active, even though fan RPMs were visible — the chip's extra vector
curves overrode manual mode (issue #96).

The fork's README carried a stopgap for IT8689E boards only, from 2026-03-30
until PR #128 made it unnecessary: a BIOS curve with every point but the last at
40% and temperature points 1–6 set to 0, 90, 90, 90, 90, 90 — point 1 is **0 °C**,
not 90 (the #96 thread's "every vector's temperature to 90" wording drops it).
Lower 90 to your BIOS maximum where the editor caps it; one owner's BIOS capped at
65 ([#115](https://github.com/frankcrawford/it87/issues/115)). On the #96
reporter's board it reliably restored only the **CPU-fan** header. Treat it as a
partial workaround for old builds, and use it only there:

| Point | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Temp (C) | 0 | 90 | 90 | 90 | 90 | 90 | 90 |
| PWM | 40 | 40 | 40 | 40 | 40 | 40 | 100 |

**No upstream source recommends a curve with a 0% point, and you must never use
one** — the BIOS curve is what runs the fans whenever the daemon is not in
control.

A driver-side fix, [PR #128](https://github.com/frankcrawford/it87/pull/128),
merged on 2026-08-24. It has three positive IT8689E hardware reports from
2026-08-23, including on revision 1, taken against the pre-merge PR head, and
post-merge reports from a B660M GAMING AC DDR4 (rev 1) and a B550M DS3H R2
(rev 2). [PR #114](https://github.com/frankcrawford/it87/pull/114),
previously cited here as the pending fix, was **rejected on 2026-08-25**. See
the *Gigabyte (IT8689E, dual boards + IT8792E)* section above for the full
status.

Reference: https://github.com/frankcrawford/it87

#### Where a header still cannot be controlled

The fork's README carries a September-2024 note about a "new Gigabyte fan
control chip". It names no boards. The causes found since are specific and
handled on current builds: the secondary chip's SmartFan enable, the
IT8689E / IT8688E rev 2 extra vectors, and — on high-end boards with more than
eight headers — an ITE IT57xx embedded controller carrying two headers that only
current builds reach. This section used to describe a separate fan-control chip
with no Linux driver on unnamed "newer" boards; no source supports that, and
control is confirmed on the X570 AORUS PRO (#99), B550M DS3H R2 (#115) and B650
EAGLE AX (#128).

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

- **Gigabyte X670E Aorus Master (IT8689E rev 1 + IT8792E):** on builds
  before PR #128, PWM writes had no effect even though Windows tools could
  control the fans. This is the IT8689E manual-control limitation — a
  different chip and different problem from the X870E AORUS MASTER below.
  Its secondary is an IT8792E (#96 dmesg: "Found IT8792E/IT8795E chip at
  0xa60, revision 3"), not an IT87952E. As of 2026-03 the upstream thread
  documented the BIOS flat-curve workaround (PWM 40×6 then 100, temperatures
  0 then 90×6) as restoring driver manual control (CPU-fan header only, per
  the reporter). **Status 2026-09-24:** #96 itself has no post-merge report,
  but PR #128's thread carries three IT8689E confirmations from 2026-08-23
  (including revision 1 on a Z790 AORUS MASTER), and two more boards have
  reported working since the merge. None of them is a dual-chip IT8689E
  board. References: https://github.com/frankcrawford/it87/issues/96 and
  https://github.com/frankcrawford/it87/pull/128

- **Gigabyte X870E AORUS MASTER (IT8696E rev 0 + IT87952E):** PWM fan
  control on the **primary** works. Distinct from the X670E AORUS MASTER
  case above — that one is IT8689E rev 1 with a manual-control
  limitation; this is IT8696E rev 0 (primary) plus a secondary.

  **⚠ The secondary can be masked by a latched bridge — measured, and
  recoverable (DEC-332, 2026-09-05).** On BIOS **F14c** with `it87-dkms-git`
  **349.c567739** (upstream HEAD), the kernel finds
  `IT8696E chip at 0xa40 [MMIO at 0xfe100000]` and then gets device-ID
  `0x8883` from the secondary address, which the driver does not recognise
  (visible only with it87 dynamic debug on).
  One `it87` hwmon device enumerates and exactly **five** `pwm` files exist
  — the primary's. `0x8883` is an ITE eSPI→LPC bridge in configuration mode
  ([#64](https://github.com/frankcrawford/it87/issues/64)), put there by
  `nct6775`/`w83627ehf` unlocking Super-I/O config space before reading the
  DEVID. Suppressing those two and then cutting mains power restored the
  secondary as `it87952-isa-0a60` — 3 fans, 3 PWMs, 3 thermistor temps, 8 of
  8 headers. Neither `mmio` (already the driver default) nor `force_id` is
  the remedy; DEC-326 recorded this state as having "no local fix" on
  2026-09-04, which the experiment disproved the next day.

  This **does not retract** the earlier report below, which was made on
  `it87-dkms-git` 332.20f2f2f+ and BIOS **F13a** (2026-03). Both
  observations are recorded because they differ in board firmware *and*
  driver revision and only one of them has been reproduced here; nobody has
  bisected which change matters. Treat the 8-header figure as the board's
  **physical** layout, not as what Linux currently reaches.

  Physically the board exposes **8 PWM headers**:
  5 on IT8696E (CPU_FAN, SYS_FAN1, SYS_FAN2, SYS_FAN3, CPU_OPT) and
  3 on IT87952E — SYS_FAN5_PUMP, SYS_FAN6_PUMP, SYS_FAN4 in that pwm order
  per two owners' configs in frankcrawford/it87
  [issue #103](https://github.com/frankcrawford/it87/issues/103) and the it87
  project's own Gigabyte sensor catalogue (SIV `A008090A`, which this board
  reports). An annotation in it87 PR #100 orders them SYS_FAN4 / FAN5_PUMP /
  FAN6_PUMP instead, read with three identical fans, so it does not isolate a
  channel. Because the order decides which header gets the pump floor, the GUI
  keeps these labels marked `(unverified)` until a per-channel test settles it
  (register row BRD-i).

  **Secondary chip enumeration:** on some boots only the IT8696E primary chip
  appears (5 of 8 headers). Keep `nct6775` / `w83627ehf` and `sensors-detect`
  away from the Super-I/O, reboot, and if the chip is still missing remove mains
  power — the ladder in doc 19 § ITE. (Older, pre-2026-03 builds also need
  `options it87 mmio=on`.) See frankcrawford/it87 issue
  [#70](https://github.com/frankcrawford/it87/issues/70) and DEC-101 for the
  diagnostics surfaced by the GUI. **it87 builds from 2026-09-09 rename this
  board's chips to `it8696_a008090a` / `it87952_a008090a`**, which changes every
  header id — see doc 19.

  BIOS Smart Fan 6 reclaims `pwm_enable` on CPU_FAN at ~1 Hz; the
  daemon's `pwm_enable` watchdog (v1.3.0+) handles this transparently.
  Setting a header to *Full Speed* in BIOS is a fail-safe, not a fix — on some
  boards it locks Linux out of the header entirely. No upstream `lm_sensors`
  config exists for this board (the upstream `configs/` tree is unchanged
  since 2023); the GUI ships a fallback label table aligned with the issue #103
  mapping (GUI v1.8.0, re-aligned v1.32.0).

- **Gigabyte B550M DS3H:** `gigabyte_wmi` driver provides temp1-temp6
  with no semantic labels (reported on a B550M DS3H AC; the issue was closed
  by its reporter).
  Reference: https://github.com/t-8ch/linux-gigabyte-wmi-driver/issues/19

#### Expected outcomes

When working with Gigabyte boards, expect one of these outcomes:

1. Full control works (current driver loaded — usually no BIOS change needed)
2. RPM reads work but PWM writes have no effect (on IT8689E boards: a driver
   build older than PR #128)
3. Only some fan headers are controllable (a blocked secondary chip, or
   headers on an IT57xx EC that only current builds reach)
4. The firmware keeps retaking a header (the daemon's watchdog re-asserts
   manual mode; update the driver)

The daemon's PWM verification test (`POST /hwmon/{header_id}/verify`)
detects which outcome applies to each header.

---

## Module conflict detection

When switching between in-kernel and out-of-tree drivers, ensure only one
driver claims each hwmon device. Common conflicts:

| Driver A | Driver B | Problem |
|---|---|---|
| `nct6683` (in-kernel) | `nct6687` (out-of-tree) | Both bind the NCT6687D (and the NCT6686D); with both loaded they can claim the same chip at once — readings garble and PWM writes fail ([nct6687d #204](https://github.com/Fred78290/nct6687d/issues/204)). Blacklist `nct6683` |
| `nct6687` (out-of-tree) | `nct6775` (in-kernel) | On an NCT679x board, an `nct6687` that claims the chip (old builds by default; any build with `force=1`) can corrupt non-volatile fan registers — the DEC-105 collision |

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

- `nct6775` (Nuvoton, mainline — hwmon names `nct6775`…`nct6799`)
- `nct6683` (Nuvoton EC, mainline, read-only PWM — hwmon names `nct6683`,
  `nct6686`, `nct6687`)
- `nct6687` (Nuvoton, out-of-tree — hwmon names `nct6686`, `nct6687`; the same
  names as the in-kernel driver, so check the bound driver)
- `it87` (ITE, mainline or out-of-tree — hwmon names `it86xx` / `it87xx`, with
  a board suffix such as `it8696_a008090a` on builds from 2026-09-09)
- `asus_ec_sensors` (ASUS EC, read-only sensors — hwmon name `asusec`)
- `asus_wmi_sensors` (ASUS WMI, read-only sensors)

### Step 3: Test write capability

The daemon's verify endpoint (`POST /hwmon/{header_id}/verify`) performs
a safe write-capability test:

1. Reads the current PWM value
2. Writes a known-distinct duty (run it on a non-critical chassis fan header
   first)
3. Waits ~6 seconds and reads the RPM response
4. Restores the original PWM value
5. Reports one of: `effective`, `pwm_enable_reverted`, `pwm_value_clamped`,
   `no_rpm_effect`, `rpm_unavailable`, or — daemon ≥ 2.48.0 —
   `pwm_readback_unavailable` (the post-write readback produced nothing, so
   nothing about the write was established; re-run), or — daemon ≥ 2.56.0 —
   `pump_protected_mid_run` (the header became pump-protected during the test,
   so it stopped and restored no lower than the pump floor; re-run). See
   `docs/08` for how these differ.

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
   - Check the System State page's readiness report first — it identifies the
     board's chips without probing the hardware. Treat `sensors-detect` as a
     last resort, and **never** run it after boot on a dual-chip Gigabyte board:
     it writes the Super-I/O unlock key and can latch the bridge in front of the
     secondary chip. It also cannot identify the IT8688E / IT8689E / IT8696E /
     IT8698E or the NCT6686D.
   - Check if a DKMS driver is needed for your board's Super I/O chip.
   - Ensure kernel headers match your running kernel (`uname -r`).

2. **Sensors visible but no fan control headers:**
   - Check the daemon's hardware diagnostics for ACPI resource conflicts.
   - Try the appropriate out-of-tree driver for your board vendor/chip.
   - If the `pwmN` files exist but are read-only (`-r--r--r--`), the in-kernel
     `nct6683` is bound — it never makes PWM writable outside Mitac OEM systems.
     No BIOS setting changes that.

3. **Fan control headers present but writes have no effect:**
   - Run the PWM verification test from the System State page.
   - Update the out-of-tree driver first — `-git` packages rebuild the
     current upstream snapshot, and many historical write failures are
     fixed there.
   - Check for module conflicts (two drivers claiming the same device).
   - For MSI X870/B850: try `msi_fan_brute_force=1` — and **blacklist
     `nct6683` at the same time**, which upstream requires for it to work
     (see doc 19 § MSI for the three-file setup). Do **not** force
     `fan_config=msi_alt1` on B650/B660/X670/Z690/Z790 boards: they use the
     default mapping and forcing alt1 zeroes every SYS_FAN reading.
   - For Gigabyte IT8689E (Rev 1): **first update `it87-dkms-git`** — the
     driver fix merged 2026-08-24 (frankcrawford/it87 PR #128) has positive
     IT8689E reports including Rev 1, and more since the merge (read doc 19's
     v2.0 rename note before rebuilding). Verify with Test PWM Control. The old
     BIOS-curve stopgap (PWM 40×6 then 100; temperatures 0 then 90×6) was only
     for builds older than the fix, restored only the CPU-fan header, and must
     never be replaced by a curve with a 0% point. PR #114 was rejected
     2026-08-25.
   - For IT8665E (ASUS AM4 300/400-series, X399-era boards): the 2026-03+ MMIO
     default broke PWM writes;
     [PR #120](https://github.com/frankcrawford/it87/pull/120) (merged
     2026-07-22) fixes it — **update `it87-dkms-git`**; older builds need
     `options it87 mmio=off` (frankcrawford/it87 issue #106, closed).
   - For ASRock: headers on an NCT6686D / NCT6683D are read-only in the kernel
     driver — try `asrock-nct6683` (if your board is on its list) or `nct6687d`.

4. **Fan control works for some headers but not others:**
   - This is common. Boards often split headers across two chips (a Gigabyte
     secondary ITE chip; an ASRock NCT6686D beside an NCT67xx), and the two may
     be in different states.
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

- **`rdna_hang_kernel_6_18_6_19` (Critical):** raised on Linux **6.18.x and 6.19.x** with an RDNA3/RDNA4 GPU (RX 7000 / 9000 series), after hard hangs under benchmark load were reported on both ([Phoronix EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025)). One bisected RDNA4 hang was fixed in **6.18.7** ([drm/amd #4765](https://gitlab.freedesktop.org/drm/amd/-/issues/4765)). If you see hangs, move to the latest 6.18 longterm point release or a current 7.x kernel — **not** to 6.15–6.17, which were never longterm and are end-of-life. Daemons up to v2.56 word this advisory the opposite way.
- **`smu_mismatch_navi48_r9700` (Critical):** raised for an AMD R9700 (PCI `0x7551`). The SMU interface-version message it is keyed on appears on every Navi 48 card, the RX 9070 XT included, and is not a fault (kernel 7.0 removed it as confusing). `pwm1` is read-only on every RDNA4 card by design; the PMFW `fan_curve` path works on at least some R9700s. A few R9700 units have unresolved per-unit fan faults ([ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)).

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
  - PR #110 (force_pwm parameter, open): https://github.com/frankcrawford/it87/pull/110
  - PR #114 (IT8689E/IT8696E manual PWM — **closed/REJECTED 2026-08-25**, superseded by #128): https://github.com/frankcrawford/it87/pull/114
  - PR #128 (fix control issues for many Gigabyte boards — IT8688/8689/8790/8792/8795/87952 manual mode, H2RAM channels, WMI→SMI; merged 2026-08-24; its author could not test IT8689 silicon, but its thread carries three IT8689E confirmations): https://github.com/frankcrawford/it87/pull/128
  - PR #132 (v2.0, 2026-09-09 — names Gigabyte chips after the board's SIV, e.g. `it8696_a008090a`): https://github.com/frankcrawford/it87/pull/132
  - PR #129 (follow-up fixes to #128, merged 2026-08-24): https://github.com/frankcrawford/it87/pull/129
  - PR #126 (ISA-bridge MMIO hardening, open): https://github.com/frankcrawford/it87/pull/126
  - PR #120 (remove MMIO path for IT8665E — fixes #106, merged 2026-07-22): https://github.com/frankcrawford/it87/pull/120
  - issue #64 (secondary-chip fan control, closed 2025-12): https://github.com/frankcrawford/it87/issues/64
  - issue #89 (X870E AORUS ELITE X3D dual-chip report, closed 2026-01-13): https://github.com/frankcrawford/it87/issues/89
  - issue #92 (B650 GAMING X AX V2 ACPI bind failure, closed 2026-02-23): https://github.com/frankcrawford/it87/issues/92
  - issue #94 (DKMS module-path quirk, CachyOS-LTS/Tumbleweed): https://github.com/frankcrawford/it87/issues/94
  - issue #96 (IT8689E Rev 1 — temps-to-90 partial stopgap): https://github.com/frankcrawford/it87/issues/96
  - issue #99 (IT8792 suspend/resume, open; reported working after a September 2026 rebuild): https://github.com/frankcrawford/it87/issues/99
  - issue #27 (ASUS PRIME X470-PRO is an IT8665E): https://github.com/frankcrawford/it87/issues/27
  - issue #79 (BIOS Full Speed dropped fans to 0% on a pre-#128 build) and #115 (fixed-speed mode locks out manual): https://github.com/frankcrawford/it87/issues/79, https://github.com/frankcrawford/it87/issues/115
  - issue #103 (X870E AORUS MASTER label mapping): https://github.com/frankcrawford/it87/issues/103
  - issue #106 (IT8665E mmio-default regression, closed — fixed by PR #120): https://github.com/frankcrawford/it87/issues/106
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
