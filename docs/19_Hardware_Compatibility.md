# 19 — Hardware Compatibility Guide

**Status:** Reference guide, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) records release-by-release changes and wins where this document disagrees with it.

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
| NCT6797D (reports `0xd451`; driver class `0xd450`) | `nct6775` | Yes (via `nct6775-platform.c`) | linux (built-in) — **see the `nct6687` collision warning below** |
| NCT6795D | `nct6775` | Yes | linux (built-in) — common on MSI AM4 X470 GAMING PRO and X470 GAMING PRO CARBON |
| NCT6792D | `nct6775` | Yes | linux (built-in) — common on ASRock AM4 ITX |
| NCT6779D | `nct6775` | Yes | linux (built-in) — common on ASRock AM4 300/400-series ATX |
| NCT677x (NCT6775, NCT6776) | `nct6775` | Yes | linux (built-in) |
| NCT6701D (`0xd806`) | `nct6775`, reported as `nct6799` | Yes | linux (built-in) — ASUS AM5 800-series and Z890/B860 boards. `sensors-detect` calls it unknown; fans and voltages read correctly, most temperature channels do not ([lm-sensors #544](https://github.com/lm-sensors/lm-sensors/issues/544)) |
| NCT6683 (chip ID 0xc730) | `nct6683` | Yes — **read-only** | linux (built-in) |
| NCT6686D (reports `0xd441`; driver class `0xd440`) | `nct6683` | Yes — **monitoring only** | linux (built-in). The in-kernel driver publishes `pwmN` read-only on every board except Mitac OEM systems, and has no `pwmN_enable`. PWM needs an out-of-tree driver (`asrock-nct6683`, `nct6687d` or `nct6686d`) |
| NCT6687D (reports `0xd592`; drivers match `0xd590`) | `nct6687` (out-of-tree) — the in-kernel `nct6683` reads it read-only | **No** for control | `nct6687d-dkms-git` (AUR) — **the `0xd450` claim was removed by PR #164; `force=1` still attaches to any Nuvoton ID, see the warning below** |

**Mainline `nct6775` names a register class, not a part.** Its hwmon names cover
several physical chips each (`SIO_ID_MASK 0xFFF8`): `nct6797` is the NCT6797D
(`0xd45x`); `nct6798` is the `0xd428` class, which kernel 7.3 renames
"NCT6798D/NCT5585D" and which also catches the NCT6796D-E and -R; `nct6799` is
the `0xd80x` class ("NCT6796D-S/NCT6799D-R"), which also catches the NCT6701D. So
an ASRock board whose manual says NCT6796D-S reports `nct6799`, and one that says
NCT6796D-E reports `nct6798`.

**NCT6683 / NCT6686 / NCT6687 family chip-ID table.** Sourced from the
out-of-tree driver register definitions
([Fred78290/nct6687d/nct6687.c](https://github.com/Fred78290/nct6687d/blob/main/nct6687.c))
and the mainline `nct6683` driver
([drivers/hwmon/nct6683.c](https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6683.c)).
**The hwmon name does not tell you which driver bound the chip:** mainline
`nct6683` names its devices after the chip kind (`nct6683`, `nct6686`,
`nct6687`) exactly as the out-of-tree driver does. Check the bound driver with
`ls -l /sys/class/hwmon/hwmon*/device/driver` — and if the `pwmN` files are
read-only (`-r--r--r--`), it is the in-kernel `nct6683`.

| Chip      | Chip ID | Driver          | Boards seen                                   |
|-----------|---------|-----------------|-----------------------------------------------|
| NCT6683   | `0xc730` | `nct6683` (mainline, read-only) | older MSI / ASRock; the B550 Taichi's fan EC is an NCT6683D-class part |
| NCT6686D  | `0xd440` (reports `0xd441`) | `nct6683` (mainline; read-only) or out-of-tree `asrock-nct6683` / `nct6687d` / `nct6686d` | ASRock AM5 Steel Legend / LiveMixer / Lightning boards, the AM5 and Z790/Z890 Taichi boards, most ASRock Z890 boards, and the BC-250 (which the kernel names "AMD BC-250") |
| NCT6687D | `0xd590` (reports `0xd592`) | `nct6687d-dkms-git` (out-of-tree) for control | MSI B550 (MAG B550 TOMAHAWK, B550-A PRO), the X570S MPG refresh, B650/X670 (MAG B650 TOMAHAWK WIFI, MAG X670E TOMAHAWK WIFI, MPG X670E CARBON WIFI), Intel 600/700-series; and, with the alternate "msi_alt1" register map, the B840/B850/B860/X870/X870E/Z890 boards (monitoring tools label those *NCT6687DR*) |

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
`0xd450`). The same chip is on MSI AM4 400-series boards (B450M MORTAR per its
upstream lm-sensors config, MAG B450 TOMAHAWK MAX), the X570-A PRO and the
original 2019 MPG X570 boards. **The `0xd450` claim was removed upstream in
[Fred78290/nct6687d PR #164](https://github.com/Fred78290/nct6687d/pull/164)
(merged 2026-05-19)**, so a current `nct6687d` build no longer claims the chip by
default. **Three ways the risk remains:** already-loaded modules,
not-yet-updated packages, and **`force=1`** — since
[PR #174](https://github.com/Fred78290/nct6687d/pull/174) that parameter attaches
to any chip ID from `0xD000` to `0xDFFF`, NCT6797D, NCT6798D and NCT6799D
included. Never load `nct6687` with `force=1` on an NCT679x board. Bazzite #4498
was closed on 2026-09-07 without a distro fix. Since 2026-08-15 Bazzite instead
ships the Terra `nct6687d` kmod, which autoloads `nct6687` and blacklists
`nct6683` on every machine. On an NCT6797D board that is safe only because a
current `nct6687d` no longer claims `0xd450`. See
`21_AMD_Motherboard_Fan_Control_Guide.md` § AM4 400-series specifics for the
full remediation. The daemon detects the collision in `/diagnostics/hardware`
→ `module_collisions` and the GUI renders a CRITICAL banner discouraging PWM
writes until resolved.

### ITE

| Chip Series | Kernel Driver | Mainline | Package |
|-------------|--------------|----------|---------|
| Legacy IT87xx (IT8603E/IT8620E/IT8622E/IT8628E, IT8705F–IT8795E) | `it87` | Yes (per the mainline `enum chips`, verified at the **v7.2** release tag, 2026-08-16, and again at 7.3-rc4) | linux (built-in) |
| IT8613E | `it87` | **Not yet** — queued in hwmon-next, so expected in **7.4** | `it87-dkms-git` (AUR) until then |
| IT8625E | `it87` | **No** — no mainline submission since October 2024, when changes were requested; not in 7.3-rc4 | `it87-dkms-git` (AUR) |
| IT8665E | `it87` | **No** | `it87-dkms-git` (AUR) — ASUS AM4 300/400-series boards (PRIME X470-PRO, ROG STRIX B450-F, X470-F/-I — [issue #27](https://github.com/frankcrawford/it87/issues/27)) and X399-era boards. **Update the driver**: [PR #120](https://github.com/frankcrawford/it87/pull/120) (merged 2026-07-22) removes the MMIO path for IT8665E, fixing the fan-write regression ([issue #106](https://github.com/frankcrawford/it87/issues/106), closed). `mmio=off` is the fallback for builds older than the merge |
| IT8686E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8688E | `it87` | **No** | `it87-dkms-git` (AUR) |
| IT8689E | `it87` | **Yes (control) since 7.1** — six PWM + `FEAT_FANCTL_ONOFF` (commit `66b8eaf`, merged 2026-03-31; 7.1 released 2026-06-14) | `it87-dkms-git` (AUR) still recommended: the 6.12 / 6.18 LTS kernels lack it, and mainline has no handling for the extra curve vectors that override manual mode on some boards (the fork's PR #128 does); the GUI still labels it out-of-tree for this reason (DEC-144) |
| IT8696E | `it87` | **No** | `it87-dkms-git` (AUR) — primary on AM5 800-series and Z890 Gigabyte boards |
| IT87952E | `it87` | **Yes since 6.3 for enumeration** (commit `d44cb4cd7456` — v6.2 lacks it, v6.3 has it; this row said 6.4 until 2026-09-24) — secondary-chip *control* on dual-IO Gigabyte boards needs the DKMS MMIO path | `it87-dkms-git` (AUR) for control — secondary chip on dual-IO Gigabyte boards (e.g. X870E AORUS MASTER, Z790 AORUS MASTER) |
| "IT8883" | *(not a distinct sensor chip)* | — | Device-ID `0x8883` at a secondary Super-I/O address is **not a sensor chip** — it is an ITE eSPI→LPC **bridge** latched in configuration mode, answering in place of the chip behind it ([#64](https://github.com/frankcrawford/it87/issues/64); ITE lists the IT8883 as "3VSB and VBAT Supported", so it keeps standby power). `it87` has no entry for `0x8883` at all, while it *does* support the IT87952E — so while the bridge is latched the secondary is **unreachable, not unsupported**. **It is recoverable** (DEC-332, measured 2026-09-05): the latch is written by `nct6775`/`w83627ehf`, which unlock Super-I/O config mode before reading the DEVID. **`0xFFFF` is not a different fault** (DEC-421): issue [#70](https://github.com/frankcrawford/it87/issues/70) reads `0xFFFF` without the unlock key and `0x8883` with it, on the same blocked chip. Neither value is visible by default either — the driver prints `Unsupported chip (DEVID=…)` with `pr_debug` and exits silently on `0xFFFF`. So the recovery is one ladder: stop the trigger, reboot, and if the chip is still missing, remove mains power. DEC-326 measured the state correctly on 2026-09-04 but concluded "no local fix", which a controlled experiment then disproved; before that this row described it as a stuck-in-config-mode symptom recovered by `mmio=on`, which was wrong in a different way. See the STEALTH ICE row below. *(Corroborating: on the AAEON **Elkhart Lake embedded** board of [issue #117](https://github.com/frankcrawford/it87/issues/117), a contributor identifies `0x8883`/IT8883 as an ITE **LPC↔eSPI bridge chip** — not a Super-I/O sensor chip — with the maintainer investigating the real sensor chip behind it. Confidence C — contributor comment, not maintainer-confirmed.)* |

The out-of-tree `it87` driver is maintained by Frank Crawford:
https://github.com/frankcrawford/it87

Recent Gigabyte AORUS boards pair a primary ITE Super-I/O with a secondary that
exposes additional headers (typically SYS_FAN4 / FAN5_PUMP / FAN6_PUMP). **The
secondary is not the same chip everywhere:** AM5 800-series, Z890 and Z690/Z790
boards use an **IT87952E** (hwmon `it87952`) beside an IT8696E or IT8689E, while
AM5 600-series boards such as the X670E AORUS MASTER pair their IT8689E with an
**IT8792E** (hwmon `it8792`), as do the X570 / B550 / X470 generations with their
IT8688E / IT8686E. Some boards with near-identical names have no secondary at
all — the X870E AORUS ELITE WIFI7, X670 AORUS ELITE AX and Z790 AORUS ELITE AX
have one chip. Both kinds of secondary ship in the same `it87` driver, but the
secondary's enumeration depends on a healthy Super-I/O bridge state at boot.

**Driver state, 2026-09.** The frankcrawford/it87 master branch merged the
ISA-bridge **MMIO/H2RAM access path**
([PR #102](https://github.com/frankcrawford/it87/pull/102), 2026-04), switched
**MMIO to on-by-default** ([PR #95](https://github.com/frankcrawford/it87/pull/95),
2026-03; `mmio=off` is the opt-out), and on 2026-08-24 merged
[PR #128](https://github.com/frankcrawford/it87/pull/128), which handles the
firmware logic that kept or retook headers (IT8689E and IT8688E rev 2 extra curve
vectors, the SmartFan enable on the IT879x secondaries). The MMIO path also
sidesteps the ACPI I/O-port conflicts that previously forced
`acpi_enforce_resources=lax`
([issue #81 discussion](https://github.com/frankcrawford/it87/issues/81)), and
master carries a built-in DMI ACPI-exemption table (`it87_acpi_ignore`) for
known-safe boards. The AUR package is a `-git` build, so a reinstall picks up the
current snapshot — **but see the v2.0 rename below before rebuilding.**

**⚠ it87 v2.0 renames Gigabyte chips (2026-09-09).**
[PR #132](https://github.com/frankcrawford/it87/pull/132) names each chip after
the board's Gigabyte SIV whenever the driver can read it — `it8696_a008090a`
instead of `it8696`. The driver keeps working, but Control-OFC's stable header
ids embed the chip name, so every profile member, fan alias, header-role
assignment (including a user-assigned pump role) and cooling-device member on
those boards points at an id that no longer exists. Until those are re-checked
the profile no longer controls the fans: a control whose members were all on
those chips commands nothing — daemon ≥ 2.55.0 lists it in `skipped_controls[]`
as `backend_unavailable` and the Controls page shows *Not controlled* — and the
fans stay under the BIOS's control. The built-in board labels and the dual-chip
check also stop matching. Until Control-OFC matches the new names
(register row BRD-a), either re-check those settings after the first boot on a
v2.0 build, or build commit `c567739` (2026-08-25). It has the same driver code as
the last build before the rename, including PR #128. The manual's Driver Setup
page has the commands.

**Known issue — secondary chip not enumerated.** On some systems only the primary
chip appears in `sensors` output (5 of 8 fan headers visible on an X870E AORUS
MASTER, etc.). First rule out the v2.0 rename above — suffixed names mean both
chips are present. Otherwise the secondary is blocked: an ITE eSPI→LPC bridge is
latched in configuration mode and answering in place of the chip. The latch is
written by anything that sends the Super-I/O unlock key to port 0x2E/0x4E —
`nct6775` and `w83627ehf` do so before reading the device ID even on boards they
cannot drive, and `sensors-detect` does it too. Measured on an X870E AORUS MASTER,
BIOS F14c, it87 349.c567739 — cause and cure both reproduced 2026-09-05 (DEC-332,
superseding DEC-326's "no local fix"); see the "IT8883" row above. The
frankcrawford/it87 issue [#70](https://github.com/frankcrawford/it87/issues/70)
(Gigabyte X870E AORUS PRO, missing SYS_FAN4/5/6) is the same state seen from the
other side — there a clean power-cycle did **not** restore the chip either.

**Recovery ladder (DEC-421 — the kernel log cannot tell you which reading the
chip gives, so this is the order for both):**

1. **Is the driver loaded?** `sudo dmesg | grep -i it87` should print a
   `Found IT8xxxE chip` line per chip. No lines at all: install or update
   `it87-dkms-git` (older, pre-2026-03 builds also need `options it87 mmio=on`).
2. **Stop the trigger.** Keep `nct6775` and `w83627ehf` from loading — the
   daemon package's modprobe guard does this on every Gigabyte board (DEC-424;
   before that, only on the boards it listed) — and do not run `sensors-detect`.
3. **Reboot**, then rescan.
4. **Still missing: remove mains power** (PSU switch off or unplugged, about ten
   seconds), then boot. The bridge keeps standby power, so a reboot or a normal
   shut-down may not clear it.

`mmio=on` does not help with any of this, because it is already the driver
default, and `force_id` is for testing only. The manual's Hardware
Troubleshooting page has the step-by-step, including the optional dynamic-debug
command that makes the driver print the ID it read.

**IT8665E MMIO — update the driver.** The 2026-03+ MMIO default *broke* PWM writes
on **IT8665E** boards (ASUS AM4 300/400-series and X399-era boards, e.g. ROG Zenith
Extreme): values written were mangled (180 stored as ~4) through a
maintainer-confirmed broken legacy FEAT_MMIO path
([issue #106](https://github.com/frankcrawford/it87/issues/106), closed).
frankcrawford/it87 [PR #120](https://github.com/frankcrawford/it87/pull/120)
(**merged 2026-07-22**) removes the MMIO path for IT8665E at the driver level, so
**rebuilding the DKMS module (`it87-dkms-git`) fixes the fan with no kernel parameter.**
*Fallback for builds older than the merge:* set `options it87 mmio=off` instead.

The control-ofc daemon detects this case (DEC-101): when DMI matches
a known dual-chip board but only one ITE chip enumerated, the
System State page surfaces a warning banner with the exact
remediation steps. See `21_AMD_Motherboard_Fan_Control_Guide.md` §
Gigabyte → Reported examples for the X870E AORUS MASTER worked
example.

### Other ASUS sensor-only drivers

The hwmon device names differ from the module names — `asus_ec_sensors`
registers as **`asusec`** and `asus_atk0110` as **`atk0110`** — which matters when
reading `/sys/class/hwmon/*/name` or matching a board note.

| Driver (hwmon name) | Mainline | Function |
|---|---|---|
| `asus_wmi_sensors` (`asus_wmi_sensors`) | Yes | Read-only board sensor enrichment via WMI, on 16 boards listed by exact name: the X370 / X470 / B450 ROG CROSSHAIR VI / VII, ROG STRIX B450 and X470 boards, PRIME X470-PRO, and the X399 boards. The [kernel doc](https://docs.kernel.org/hwmon/asus_wmi_sensors.html) warns that buggy ASUS BIOS WMI can stop fans, pin them at maximum or freeze readings, more likely the more often it is polled. It singles out the **PRIME X470-PRO** as "particularly bad", names no safe polling rate, advises a soak test, and says a BIOS with WMI method version ≥ 2 should fix it (the driver refuses older versions). Never the PWM write path — on the boards with evidence the fan chip is an ITE IT8665E. |
| `asus_ec_sensors` (`asusec`) | Yes | Read-only EC sensor enrichment on boards listed by exact name — 55 in kernel 7.2, 60 in the 7.3 release candidates; check the [kernel doc](https://docs.kernel.org/hwmon/asus_ec_sensors.html) for the kernel you run. The AM4 400-series entries are the PRIME X470-PRO, ROG STRIX X470-I GAMING (6.19+) and ROG STRIX X470-F GAMING (7.1+). No Z890, B860, B850-A or B850-F board is listed. From 7.3 an unconnected T_Sensor or water-probe socket reads as unavailable instead of a -62/-60/-40 °C placeholder. |
| `asus_atk0110` (`atk0110`) | Yes | Read-only ACPI ATK0110 hwmon on older ASUS boards that publish that ACPI device. If you see this driver loaded but no controllable headers, look for `nct6775` or `it87` as the PWM path. |

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
it is not exhaustive. Entries are cross-referenced against upstream
lm-sensors `configs/`, the kernel `asus_*` driver lists, the
frankcrawford/it87 Gigabyte SIV sensor catalogue, LibreHardwareMonitor board
definitions, ASRock manual block diagrams and exact-board logs (re-checked
board by board on 2026-09-24, DEC-421). The fork's DMI-table *comments* are not
used as evidence — several were found attached to the wrong board.

| Generation | Typical Vendors | Typical Hwmon Chip(s) | Driver Path |
|---|---|---|---|
| **AM4 400-series** (B450 / X470) | ASUS (PRIME X470-PRO, ROG STRIX B450-F, ROG STRIX X470-F / X470-I) | **ITE IT8665E** ([it87 #27](https://github.com/frankcrawford/it87/issues/27): "Chip markings and sensors-detect say that this is IT8665E") + sensor-only `asus_wmi_sensors` / `asus_ec_sensors` | out-of-tree `it87-dkms-git` for fans — mainline has no IT8665E driver; the ASUS drivers are sensors only. (This row said NCT6798D until 2026-09-24.) |
| | MSI (B450M MORTAR, MAG B450 TOMAHAWK MAX; X470 GAMING PRO and X470 GAMING PRO CARBON) | NCT6797D (MORTAR, TOMAHAWK MAX) / NCT6795D (X470 GAMING PRO, and its CARBON) | mainline `nct6775` — **never load `nct6687` here, and never with `force=1`** |
| | Gigabyte (X470 AORUS ULTRA/GAMING 5/7, B450 AORUS PRO/PRO-CF) | IT8686E + IT8792E (dual-chip; the B450 AORUS PRO's IT8792E carries no fan header) | out-of-tree `it87-dkms-git` |
| | ASRock (B450 Gaming-ITX/ac; B450 Pro4, X470 Taichi) | NCT6792D (ITX) or NCT6779D (ATX / micro-ATX) | mainline `nct6775` |
| **AM4 500-series** (X570 / B550 / A520) | ASUS (TUF GAMING X570-PLUS, ROG STRIX X570/B550, PRIME X570-PRO) | NCT6798D + asus_ec_sensors enrichment | mainline `nct6775` |
| | MSI **NCT6687D camp** (MAG B550 TOMAHAWK, B550-A PRO, MPG B550 GAMING PLUS / EDGE; the 2021 MPG X570S EDGE MAX WIFI and CARBON MAX WIFI) | NCT6687D (reports `0xd592`), default register map | out-of-tree `nct6687d-dkms-git`, with `nct6683` blacklisted |
| | MSI **NCT6797D camp** (X570-A PRO, MPG X570 GAMING PRO CARBON WIFI, MPG X570 GAMING PLUS / EDGE WIFI, MAG X570 TOMAHAWK WIFI, MEG X570 ACE / UNIFY / GODLIKE, MAG X570S TOMAHAWK / TORPEDO MAX) | NCT6797D (reports `0xd451`) | mainline `nct6775` — **must not load nct6687d here** (DEC-105 brick risk; never `force=1`) |
| | Gigabyte AORUS (X570 AORUS MASTER / PRO / PRO WIFI / ULTRA / XTREME, B550 AORUS MASTER / PRO, B550 VISION D) | IT8688E + IT8792E (dual-chip) | out-of-tree `it87-dkms-git` |
| | Gigabyte AORUS X570S (X570S AERO G, X570S AORUS MASTER) | IT8689E + IT87952E (dual-chip) | out-of-tree `it87-dkms-git` |
| | Gigabyte AORUS single-chip (B550M AORUS PRO, B550I AORUS PRO AX) | IT8688E only | out-of-tree `it87-dkms-git` |
| | ASRock (B550 Steel Legend — physically an NCT6796D-E — B550 Extreme4, B550 PG Velocita, X570 Taichi, X570 Steel Legend) | NCT6798D class (`nct6798`) | mainline `nct6775` |
| | ASRock B550 Taichi (and Razer Edition) | fans on an NCT6683D-class EC; the NCT6798D beside it has no fans wired | in-kernel `nct6683` reads them read-only; `asrock-nct6683` (out-of-tree, lists both boards) for PWM |
| **AM5 600-series** (B650 / X670 / A620) | ASUS (ROG STRIX X670E, ROG CROSSHAIR X670E, TUF GAMING X670E-PLUS, PRIME B650-PLUS) | **NCT6799D(-R)**, reported as `nct6799` ([zeule/asus-ec-sensors #45](https://github.com/zeule/asus-ec-sensors/issues/45); [lm-sensors #416](https://github.com/lm-sensors/lm-sensors/issues/416)) + `asus_ec_sensors` on listed boards | mainline `nct6775` for PWM; `asus_ec_sensors` for sensor enrichment. (This row said NCT6798D until 2026-09-24.) |
| | MSI (MAG B650 TOMAHAWK WIFI, MAG X670E TOMAHAWK WIFI, MPG X670E CARBON WIFI, MPG B650 CARBON WIFI, PRO X670-P WIFI) | NCT6687D, **default** register map — never `msi_alt1` | out-of-tree `nct6687d-dkms-git`, with `nct6683` blacklisted |
| | Gigabyte AORUS (X670E AORUS MASTER, X670E AORUS PRO X) | **IT8689E + IT8792E** dual-chip — the secondary is an IT8792E/IT8795E (hwmon `it8792`), not an IT87952E ([#96](https://github.com/frankcrawford/it87/issues/96) dmesg). Before PR #128, Rev 1 accepted PWM writes with no effect while a normal BIOS curve was active | out-of-tree `it87-dkms-git`. [PR #128](https://github.com/frankcrawford/it87/pull/128) (merged 2026-08-24) is the driver-side fix, with positive IT8689E reports including Rev 1 — update, then verify. The fork's pre-#128 BIOS stopgap was PWM 40×6/100 at 0,90×6 °C; never a curve with a 0% point |
| | Gigabyte single-chip (X670 AORUS ELITE AX, X670 / B650 GAMING X AX) | IT8689E only (SIV catalogue) | out-of-tree `it87-dkms-git` |
| | ASRock AM5 Steel Legend / LiveMixer / Lightning ITX (B650 / X670E Steel Legend, B650 LiveMixer, A620I / B650I Lightning WiFi) | **NCT6686D only** | in-kernel `nct6683` reads it read-only (some need `force=1`); PWM via `asrock-nct6683` (lists A620I / B650I Lightning WiFi, X670E Steel Legend) or `nct6687d` (MSI labels — check the pump header) — test before relying |
| | ASRock AM5 Pro RS / PG Lightning / HDV (B650 Pro RS, X670E Pro RS, X670E PG Lightning, A620M Pro RS, B650M-HDV/M.2) | NCT6796D-S only, reported as `nct6799` | mainline `nct6775` |
| | ASRock AM5 Taichi (X670E Taichi, B650E Taichi, and their Lite editions) | NCT6686D + NCT6796D-S (+ Fintek eSPI→LPC bridge) | both drivers — see the X870E Taichi Lite row below |
| **AM5 800-series** (B850 / X870 / B840) | ASUS (X870E / X870 / B850) | **NCT6701D** (`0xd806`), reported as `nct6799` — fans and voltages read correctly, most temperatures do not ([lm-sensors #542](https://github.com/lm-sensors/lm-sensors/issues/542), [#544](https://github.com/lm-sensors/lm-sensors/issues/544)); `asus_ec_sensors` per board: ProArt X870E-CREATOR WIFI 6.17; ROG STRIX B850-I / X870-I / X870E-E 6.18; X870E-H / X870-F 6.19; B850-E 7.2; ROG CROSSHAIR X870E HERO and X870E-E WIFI7 R2 7.3 | mainline `nct6775`. X870 / X870E boards reach the chip through the ASUS WMI path from kernel 7.1; **no B850 board** is on that list, so B850 goes through the I/O ports, where ACPI conflicts are likelier. One ROG STRIX X870E-E owner measured the firmware switching a header straight back from manual to full speed ([zeule #100](https://github.com/zeule/asus-ec-sensors/issues/100)) |
| | MSI **auto-allowlist** (B840 / B850 / B860 / X870 / X870E / Z890 boards; `nct6687.c::nct6687_msi_alt_boards[]` is the source of truth and keeps growing) | NCT6687D with the alternate "msi_alt1" EC map, enabled automatically for listed boards | out-of-tree `nct6687d-dkms-git`; add `msi_fan_brute_force=1` (with `nct6683` blacklisted) if system-fan writes do not stick |
| | MSI boards of those series NOT on the list (e.g. MAG B850 TOMAHAWK WIFI, the PZ / WHITE / UNIFY-X editions) | NCT6687D, alternate map | `nct6687d` + `fan_config=msi_alt1`, plus `msi_fan_brute_force=1` if needed |
| | Gigabyte X870E AORUS **PRO** (incl. ICE / X3D) / **ELITE X3D** ([#89](https://github.com/frankcrawford/it87/issues/89)) / XTREME AI TOP / B850 AI TOP / X870 AORUS ELITE WIFI7 (incl. ICE) | IT8696E + IT87952E (dual-chip) | out-of-tree `it87-dkms-git`; 2026-03+ builds work out of the box (older builds need `mmio=on`). The ELITE X3D is owner-confirmed with both chips controllable ([#89](https://github.com/frankcrawford/it87/issues/89)) |
| | Gigabyte X870E AORUS **ELITE WIFI7** | **IT8696E only** (6 fan headers; [PR #131](https://github.com/frankcrawford/it87/pull/131)) | out-of-tree `it87-dkms-git`. Until 2026-09-24 the daemon's board table matched it with the dual-chip X3D and warned about a missing chip that the board does not have |
| | **Gigabyte X870E AORUS MASTER** — split out of the row above 2026-09-04 (DEC-326) | IT8696E + IT87952E, the secondary blocked behind an ITE bridge latched in config mode (answers device-ID `0x8883`) | out-of-tree `it87-dkms-git` drives the **primary only** (5 of 8 headers) while the bridge is latched. **It IS recoverable** — keep `nct6775`/`w83627ehf` from loading, reboot, then a full power cut if needed (DEC-332, 2026-09-05; DEC-421). `mmio` is already the driver default and `force_id` does not help; neither is the remedy. Same nominal pairing as the ELITE X3D above, different outcome, which is why this table is per board and not per family. See the "IT8883" row and [#64](https://github.com/frankcrawford/it87/issues/64) |
| | **Gigabyte X870 AORUS STEALTH ICE** | IT8696E + IT87952E, the secondary reachable only once the eSPI→LPC bridge in front of it is out of config mode | out-of-tree `it87-dkms-git` drives the **primary only** while the bridge is latched; the secondary comes back after suppressing `nct6775`/`w83627ehf`, a reboot and, if needed, a full power cut. **Enrolled in the dual-chip table** (DEC-332) — it had been held out on the premise that nothing local could reach the secondary, which is false, and #81's own thread records its reporter driving `pwmN` on the second chip. `mmio=on` (already the default) and `force_id` are not the remedy; #81's opening post tried both. [#81](https://github.com/frankcrawford/it87/issues/81) · [#64](https://github.com/frankcrawford/it87/issues/64) is the bridge thread |
| | ASRock X870 Nova WiFi | **NCT6796D-S** only (manual block diagram), reported as `nct6799` | mainline `nct6775` |
| | ASRock X870E Nova WiFi | NCT6796D-S + NCT5585D (+ Fintek bridge) | mainline `nct6775` (both chips) |
| | ASRock B850 / X870 Steel Legend WiFi, B850I Lightning WiFi | **NCT6686D only** | in-kernel `nct6683` read-only; out-of-tree driver for PWM |
| | **ASRock X870E Taichi Lite — dual-Nuvoton** | NCT6686D @ 0x0a20 + NCT6796D-S @ 0x0290 (reported as `nct6799`) + Fintek F85227N bridge | `nct6687d` (or read-only `nct6683`) + mainline `nct6775` (DEC-106 collision-detector exemption). On an X870E Taichi an owner measured CHA_FAN1/2, CPU_FAN2 and AIO_PUMP on the `nct6799` chip and CHA_FAN3/4 on the NCT6686D, where `nct6687d` labels one channel "Pump Fan" — so assign the pump role by hand ([nct6687d #155](https://github.com/Fred78290/nct6687d/issues/155)) |

The System State page (`/diagnostics/hardware`) reports the actual loaded
modules and detected chips, so users should always cross-reference this
generic table against their own system's output.

## Intel platform → typical chip mapping

Parallel table for Intel LGA1700 (12th–14th Gen Core) and LGA1851 (Core
Ultra) platforms. Added in DEC-110 alongside the GUI's Intel vendor
quirks and the daemon's CPU vendor detection, and re-checked board by board on
2026-09-24 (DEC-421). As with the AMD table, every entry is cross-referenced
against a verifiable upstream source (kernel lists, lm-sensors `configs/`,
Fred78290/nct6687d source, the it87 SIV catalogue, ASRock manuals).

| Generation | Typical Vendors | Typical Hwmon Chip(s) | Driver Path |
|---|---|---|---|
| **LGA1700 600-series** (Z690 / B660 / H670) | ASUS (ROG MAXIMUS Z690 FORMULA, ROG STRIX Z690-A GAMING WIFI D4 / Z690-E GAMING WIFI, TUF GAMING Z690-PLUS) | NCT6798D (reports `0xd42b`) + `asus_ec_sensors` enrichment on listed ROG boards | mainline `nct6775` for PWM; `asus_ec_sensors` for sensor enrichment |
| | MSI (MAG Z690 TOMAHAWK WIFI, MPG Z690 EDGE WIFI) | NCT6687D, **default** register map — no `msi_alt1` | out-of-tree `nct6687d-dkms-git`, with `nct6683` blacklisted |
| | Gigabyte Z690 AORUS PRO / MASTER | **IT8689E + IT87952E** (dual-chip) | out-of-tree `it87-dkms-git`; 2026-03+ builds default MMIO on (older builds need `mmio=on`) |
| | ASRock (Z690 Steel Legend, **Z690 Extreme** — upstream lm-sensors config) | NCT6798D class (the Z690 Extreme's physical NCT6796D-E reports as `nct6798-isa-02a0`) | mainline `nct6775` |
| | ASRock Z590 Taichi | NCT6686D | in-kernel `nct6683` read-only (customer ID added in 7.0); out-of-tree driver for PWM |
| **LGA1700 700-series** (Z790 / B760 / H770) | ASUS (ROG MAXIMUS Z790 EXTREME, ROG STRIX Z790-E GAMING WIFI II / Z790-H GAMING WIFI / Z790-I GAMING WIFI — kernel `asus_ec_sensors` list; 7.3 adds ROG MAXIMUS Z790 HERO) | NCT6798D + `asus_ec_sensors` enrichment | mainline `nct6775` + sensor enrichment |
| | MSI (MAG Z790 TOMAHAWK WIFI, MPG Z790 EDGE WIFI) | NCT6687D, default register map — same as Z690 | out-of-tree `nct6687d-dkms-git`; no `msi_alt1` |
| | Gigabyte Z790 AORUS MASTER / XTREME / PRO X | IT8689E + IT87952E (dual-chip; the MASTER's secondary sits at `0x0b10`) | out-of-tree `it87-dkms-git`; 2026-03+ builds default MMIO on (older builds need `mmio=on`) |
| | Gigabyte Z790 AORUS ELITE / ELITE AX | **IT8689E only** (SIV catalogue) | out-of-tree `it87-dkms-git` |
| | ASRock Z790 Steel Legend WiFi | NCT6796D-E, reported as `nct6798` | mainline `nct6775` |
| | ASRock Z790 Taichi | **NCT6686D (5 headers) + NCT5585D (3 headers)** (manual block diagram) | NCT5585D: mainline `nct6775` (`nct6798`); NCT6686D: in-kernel `nct6683` read-only, out-of-tree driver for PWM |
| **LGA1851 800-series** (Z890 / B860 / H810) | MSI (MAG/MEG/MPG Z890) | NCT6687D (reports `0xd592`) with the alternate "msi_alt1" EC map (monitoring tools label these boards *NCT6687DR*); **requires `fan_config=msi_alt1`**, set automatically for boards in the driver's `nct6687_msi_alt_boards[]` | out-of-tree `nct6687d-dkms-git` (auto-allowlist) or manual `fan_config=msi_alt1`; several also need `msi_fan_brute_force=1` |
| | ASUS (ROG STRIX Z890 / B860) | **NCT6701D** — by chip ID it binds as `nct6799`; no Linux log for an ASUS Z890 exists yet | mainline `nct6775`. Not on the ASUS WMI access list, and no `asus_ec_sensors` or `asus_wmi_sensors` support |
| | Gigabyte Z890 AORUS MASTER (and, per the SIV catalogue, XTREME AI TOP / PRO ICE / ELITE X ICE) | **IT8696E + IT87952E** | out-of-tree `it87-dkms-git`; same dual-chip remediation |
| | Gigabyte Z890 AORUS ELITE WIFI7 (incl. ICE / PLUS / DUO X) | **IT8696E only** | out-of-tree `it87-dkms-git` |
| | ASRock Z890 (Steel Legend, Lightning, Pro-A, Pro RS, Nova, Taichi) | **NCT6686D** carries the fans; the Nova and Taichi add an NCT6796D-E (reported as `nct6798`) with little or nothing wired | in-kernel `nct6683` reads the NCT6686D read-only (Z890 Pro-A customer ID since 7.2; others may need `force=1`); PWM via `nct6687d`, or `asrock-nct6683` on the Z890 Nova WiFi |

Notes:

- The daemon's `/diagnostics/hardware` response now includes a `cpu_vendor`
  field (`"Intel"` / `"AMD"` / `""`) populated from `/proc/cpuinfo`. The
  GUI uses this to scope DEC-110 platform-specific quirks (the same
  chip can ship on different vendors' Intel boards and AMD boards with
  different quirks — e.g. the msi_alt1 NCT6687D boards on MSI Z890 vs MSI X870E).
- Intel CPU temperature is always provided by the `coretemp` mainline
  driver (per-core + `Package id 0`). It is not a fan-control driver.
- `intel_pch_thermal` registers a sensor-only hwmon device for the PCH
  temperature; it is enrichment, not control.
- For end-user setup, BIOS tips, and troubleshooting, see
  `23_Intel_Motherboard_Fan_Control_Guide.md`.

## Manufacturer Quirks

### Gigabyte (ITE chips)

Most Gigabyte boards use ITE IT8686E / IT8688E / IT8689E / IT8696E chips, which
require (or, for the IT8689E, still benefit from) the out-of-tree `it87` driver.

**BIOS settings.** On a current driver build usually **none** are needed — the
driver takes each header over from Smart Fan. The rules that do apply:

1. **Never give a BIOS fan curve a 0% point.** The curve runs the fans during
   POST, after the daemon hands a header back, when `it87` unloads, across
   suspend, and whenever the daemon is not running — a 0% point stops the fans,
   pump and CPU included, in exactly those windows. No upstream source recommends
   one.
2. For a 4-pin fan, set the header's **FAN Control Mode** (Smart Fan 5/6) to
   **PWM**. *Fan Control Use Temperature Input* only picks the reference sensor;
   no Smart Fan 5 or 6 manual checked (X570, AMD 600, Intel 600/800 BIOS
   manuals) has a "FAN Control by" item, which this section used to name.
3. *Full Speed* in **Fan Speed Control** is a fail-safe, not a fix: the firmware
   runs that fan at 100% whenever it owns it, but on some boards it also locks
   Linux out of the header
   ([#115](https://github.com/frankcrawford/it87/issues/115): "A fixed-speed mode
   rejects `pwm_enable=1`"), and before PR #128 one owner saw Full-Speed fans
   drop to 0% as the module loaded
   ([#79](https://github.com/frankcrawford/it87/issues/79)).

The PWM files are **not** read-only on these chips: `pwmN` and `pwmN_enable` are
0644 in both the fork and mainline. A write to `pwmN` while the header is in
automatic mode returns `EBUSY` by design. What goes wrong on some boards is that
the chip's firmware logic keeps or retakes the fan — extra curve vectors on
IT8689E and IT8688E rev 2, and the SmartFan enable on the IT879x secondaries —
which PR #128 (2026-08-24) addresses.

**MMIO requirement:** The out-of-tree `it87` driver requires MMIO
(Memory-Mapped I/O) for fan control on newer Gigabyte motherboards. MMIO is
enabled by default in current (2026-03+) builds of the `frankcrawford/it87`
driver ([PR #95](https://github.com/frankcrawford/it87/pull/95)); only
pre-2026-03 builds need `options it87 mmio=on`. One counter-case: the
default *broke* **IT8665E** boards
([issue #106](https://github.com/frankcrawford/it87/issues/106), closed),
fixed at the driver level by [PR #120](https://github.com/frankcrawford/it87/pull/120)
(merged 2026-07-22, removes the MMIO path for IT8665E) — so update the DKMS
build; older builds need `options it87 mmio=off`.

**IT8689E manual control limitation (before PR #128):** Some Gigabyte IT8689E
boards (Rev 1 especially, e.g. X670E AORUS MASTER) accepted manual PWM writes with
no effect while a normal BIOS fan curve was active — the chip's extra vector
curves overrode manual mode
([issue #96](https://github.com/frankcrawford/it87/issues/96)). The fork's
documented stopgap (its README, 2026-03-30 to 2026-08-24, IT8689E boards only) was
a BIOS curve of PWM 40,40,40,40,40,40,100 at temperatures 0,90,90,90,90,90,90 —
lowering 90 to the BIOS maximum where it is capped — and it reliably restored only
the CPU-fan header. The README dropped it once PR #128 made it unnecessary.

The driver-side fix landed on 2026-08-24:
[PR #128](https://github.com/frankcrawford/it87/pull/128) fixes manual
mode for IT8688/IT8689/IT8790/IT8792/IT8795/IT87952 and switches the Gigabyte
path from WMI to SMI.

**Hardware reports.** Three users reported working IT8689E manual PWM on
2026-08-23, against the pre-merge head:

| Board | Chip | Result |
|---|---|---|
| Gigabyte Z790 AORUS MASTER rev 1.0, BIOS F19a, kernel 7.1.8 | **IT8689E revision 1** + IT87952E rev 1 | SYS_FAN2 (`pwm3`) duty 30/60/120/180/255 → 0/1227/2368/3183/3276 RPM; previously stuck ~2250 RPM. `pwm3_enable=2` restored firmware control cleanly |
| Gigabyte B650 Eagle AX | IT8689E (single) | Control works, **no BIOS-side tweaks needed** |
| Gigabyte B760M H | IT8689E revision 2 | All 3 PWM headers controllable; previously CPU-fan only. No BIOS adjustments |

Those three tested PR head `429d2b40`, which is not the commit that merged — the
branch was amended to `27319db7`, reworking the Intel H2RAM bridge save/restore
path. Since the merge, a B660M GAMING AC DDR4 (IT8689E rev 1, working after a
reboot) and a B550M DS3H R2 (rev 2,
[#115](https://github.com/frankcrawford/it87/issues/115)) have reported working.
None yet covers a dual-chip IT8689E board or a v2.0 build, so update
`it87-dkms-git`, then **verify writes take effect** with Test PWM Control before
relying on control. The earlier [PR #114](https://github.com/frankcrawford/it87/pull/114)
was **rejected on 2026-08-25** (superseded by #128) — it is not a pending fix.
See doc 21 for details.

**Where a header still cannot be controlled.** The fork's README carries a
September-2024 note about a "new Gigabyte fan control chip". It names no boards
and no series. The causes found since are board-specific: the secondary chip's
SmartFan enable, the IT8689E / IT8688E rev 2 extra vectors, and — on high-end
boards with more than eight headers — an ITE IT57xx embedded controller that
carries two headers and is reachable only on current builds. This section used to
list "X570, B550, X670, B650, X870, B850 series and newer" as affected. Nothing
upstream supports that list, and control is confirmed on the X570 AORUS PRO
([#99](https://github.com/frankcrawford/it87/issues/99)), B550M DS3H R2 (#115) and
B650 EAGLE AX (#128).

### MSI (NCT6687D)

MSI boards from B550 / Intel 600-series on use the NCT6687D, which needs the
out-of-tree `nct6687d` driver for fan control. (MSI AM4 300/400-series boards and
the original 2019 X570 boards use an NCT679x chip and the mainline `nct6775`
instead — never `nct6687` there.)

**Read-only headers are a driver problem, not a BIOS one.** The in-kernel
`nct6683` also binds the NCT6687D, names the device `nct6687` too, and publishes
every `pwmN` read-only on MSI boards. `nct6687d` always makes them writable. So:
blacklist `nct6683` and check with `ls -l /sys/class/hwmon/hwmon*/device/driver`.
This section used to blame the BIOS "Smart Fan Mode" setting for read-only
headers; that was never the cause.

**X870/B850 7-point write quirk:** Newer MSI boards (B840 / B850 / B860 / X870 /
X870E / Z890 — the alt1 register map) often need all 7 BIOS fan-curve points
written, rather than a single PWM value, before a system fan follows. The
`nct6687d` driver provides a `msi_fan_brute_force=1` module parameter (upstream
marks it **BETA**) for this. It is a separate, current parameter, not an
older-driver alternative to `fan_config`. This only affects system fans — CPU
and pump fans use standard PWM writes. Before entering manual control the driver
snapshots all 7 original curve points and restores them when automatic mode is
written, when the module is unloaded, or when the optional fan-control watchdog
expires. Current builds return `EIO` when a write does not stick — on some boards
(e.g. PRO B850M-P WIFI) even for CPU_FAN — until brute force is enabled.

> **`msi_fan_brute_force=1` requires blacklisting `nct6683`.** Upstream
> states this as a prerequisite: with both loaded, the in-kernel `nct6683`
> and `nct6687` can bind the same chip at once — readings garble and PWM
> writes fail, commonly with `EIO`
> ([#204](https://github.com/Fred78290/nct6687d/issues/204)). Set all three:
> ```sh
> echo "blacklist nct6683" | sudo tee /etc/modprobe.d/nct6683_blacklist.conf
> echo "options nct6687 msi_fan_brute_force=1" | sudo tee /etc/modprobe.d/nct6687_msi.conf
> echo "nct6687" | sudo tee /etc/modules-load.d/nct6687.conf
> ```
> Then reboot. This exact sequence resolved
> [issue #202](https://github.com/Fred78290/nct6687d/issues/202) (MSI PRO
> B850M-P WIFI, PWM writes returning `EIO`).

> **Do NOT force `fan_config=msi_alt1` on boards outside the alt1 series.**
> The chip is the same NCT6687D (it reports `0xd592`) on every MSI generation;
> only the B840 / B850 / B860 / X870 / X870E / Z890 boards use the alternate
> EC register layout (monitoring tools label them *NCT6687DR*), and on those
> the driver enables it **automatically** for each board named in
> `nct6687_msi_alt_boards[]` — a list that grows, and whose entries are full
> DMI names, so a PZ / WHITE / MAX edition can be missing while its sibling is
> listed. Earlier MSI series — **B650 / B660 / X670 / Z690 /
> Z790** — use the *default* mapping and are auto-detected correctly.
> Forcing `msi_alt1` there makes the driver read EC offsets `0x154–0x15E`,
> which read zero on those boards, so **every SYS_FAN reports 0 RPM**
> while CPU_FAN keeps working. Confirm the active mapping with `sudo dmesg`:
> ```
> nct6687 …: active fan config=msi_alt1, SYS_FAN reg_rpm=0x015E/0x015C/0x015A
> ```
> That line is always printed and will reveal a stale forced setting at a
> glance. Reference: upstream issue #167 (MSI MPG B650 CARBON WIFI, MS-7D74).

**Other module parameters** (added 2026-08): `fan_mask` (default `0xff`) and
`temp_mask` (default `0x7f`) hide unpopulated channels. Masked channels are
not read from the EC and numbering is never renumbered to fill gaps, so
existing `sensors.d` / `fancontrol` configuration keeps referring to the
same hardware.

**If your MSI fans disappeared after an August 2026 driver update:** ensure
`nct6687d-dkms-git` is at pkgrel **`-2` or newer**. The `r225.4864fd6-1`
package shipped the upstream Kbuild rework, which moved the module install
path and left no module to load — no RPM readings and no control
([issue #198](https://github.com/Fred78290/nct6687d/issues/198)). Fixed by
the AUR rebuild on 2026-08-22. Kernel 7.2 / GCC 16.2 build failures from the
same window are also fixed upstream (issues #199, #201).

Reference: https://github.com/Fred78290/nct6687d

**Known affected boards (7-point quirk):** the B840 / B850 / B860 / X870 / X870E /
Z890 series.

### ASUS (Nuvoton NCT679x / NCT6701D)

ASUS AM4 500-series and Intel 600/700-series boards use an NCT6798D; AM5
600-series boards an NCT6799D(-R); AM5 800-series and Z890 / B860 boards an
NCT6701D that mainline binds as `nct6799`. ASUS AM4 300/400-series boards are
ITE (IT8665E) — see the Gigabyte-style `it87` notes above.

**ACPI conflicts.** ASUS boards may have ACPI OpRegion conflicts on the I/O ports
the `nct6775` driver uses (commonly 0x0290–0x0299). On many supported boards no
workaround is needed: `nct6775` reaches the chip through an ASUS WMI method
instead of the ports, for boards on its exact-name lists — as of 7.3 there is no
B850, B840, B860 or Z890 board on them.

**Remediation (only if the bind fails):**
- Add `acpi_enforce_resources=lax` to kernel parameters — `nct6775` has no
  driver-local escape (its only module parameters are `force_id` and
  `fan_debounce`), so the system-wide parameter is the only option, OR
- Disable "ACPI Hardware Monitor" in BIOS (if available)

**BIOS.** No setting makes the `nct6775` PWM files writable — they already are,
and the daemon switches each header to manual itself. *Q-Fan Tuning* is a
one-shot calibration; the mode item is *&lt;header&gt; Q-Fan Control* (Auto Detect /
DC Mode / PWM Mode — match the fan) and the curve item *&lt;header&gt; Fan Profile*.

The daemon's diagnostics endpoint detects these conflicts by parsing
`/proc/ioports` and reports them on the Hardware page.

### ASRock

**Current state:** ASRock boards mix chips. Most AM5 boards carry an NCT6796D-S
(mainline `nct6775`, reported as `nct6799`). Many boards also — or only — carry an
NCT6686D (or an NCT6683D-class EC on the B550 Taichi) that holds some or all of
the fan headers. The in-kernel `nct6683` driver provides **monitoring**
(temperatures, RPMs) for that chip, but publishes the PWM files **read-only** on
every board except Mitac OEM systems, and has no `pwmN_enable`. So writes are
refused, not accepted and ignored — this section used to say the opposite. Which
chip carries which header is per board: see the per-generation tables above.

**Alternative drivers for ASRock NCT668x boards:**

| Driver | Repository | Supported boards |
|---|---|---|
| `asrock-nct6683` | https://github.com/branchmispredictor/asrock-nct6683 | Enables PWM on the boards it lists by exact DMI name: B550 Taichi, B550 Taichi Razer Edition, A620I Lightning WiFi, B650I Lightning WiFi, X570 Creator, X670E Steel Legend, Z370M Pro4, Z890 Nova WiFi. No AUR package |
| `nct6687d` | https://github.com/Fred78290/nct6687d | Several ASRock NCT6686D boards (e.g. B650 LiveMixer, X870E Taichi Lite). Uses MSI's register map and **MSI's fan labels**, so verify which header is which before trusting a "Pump Fan" label. `nct6687d-dkms-git` on the AUR |
| `nct6686d` | https://github.com/s25g5d4/nct6686d | Tested only on the A620I Lightning WiFi; its author reports it still targets kernel 6.8. No AUR package |

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

## NVIDIA discrete GPU monitoring

NVIDIA **discrete** GPUs are supported for **read-only** monitoring only —
temperatures and fan telemetry. Added in GUI v2.11.0 / daemon v2.8.0 (DEC-204).
The NVML path is **experimental** (built and fake-tested; not yet verified
against NVIDIA hardware).

### Drivers

NVIDIA GPUs live in one of two mutually-exclusive driver worlds:

| Driver | Telemetry path | `driver` field | `chip_name` |
|---|---|---|---|
| `nouveau` (open) | Kernel DRM driver with an hwmon node (`temp1`, and on some cards fan RPM) | `"nouveau"` | `nouveau` |
| Proprietary NVIDIA | No sysfs hwmon; temperature + measured fan **duty %** via the NVML userspace library (`libnvidia-ml.so.1`) | `"nvidia"` | `nvml` |

The `driver` field on the capability/diagnostics is always the **kernel module
name** (`"nouveau"`/`"nvidia"`), never the `nvml` library. The proprietary NVML
backend is **opt-in and off by default** (`[detection] enable_nvidia_telemetry`
in the daemon config) and needs the daemon to reach `/dev/nvidia*` (a packaged
systemd drop-in grants this).

### Supported (read-only)

- **Temperature monitoring** — sensor source `"nvidia_gpu"`, kind `gpu_temp`
  (chip name `nouveau` or `nvml`).
- **Fan telemetry** — one fan entity per GPU as `nvidia_gpu:{pci_bdf}`. RPM when
  the card exposes it; the NVML path additionally reports a **measured fan
  duty %** (`duty_pct`, may exceed 100 — NVML expresses it as a % of the max
  noise tolerance), distinct from a commanded PWM (there is none).

### Not supported — fan control

There is **no user-controllable fan interface** through this daemon. `nouveau`
*does* expose a writable `pwm1`, but the daemon deliberately **excludes** it from
discovery for safety (the GPU subsystem owns it, mirroring the AMD rule); the
NVML backend is telemetry-only (no fan-write symbol is bound). The daemon always
reports `fan_control_method` `"read_only"` (fan present) or `"none"`. NVIDIA GPU
fans are never writable and never offered as controllable curve members; the
GPU's **temperatures** remain usable as curve *sensors*. Fan **write** support is
a deliberately deferred Phase 2, gated on NVIDIA hardware for validation.

### Model names

`model_name` and `driver_version` are available only from the proprietary NVML
driver; the open `nouveau` leg shows the generic "NVIDIA D-GPU" label.

## Liquid cooling (AIO) — hwmon

Phase 1 (DEC-156, daemon ≥ 1.18.0 / GUI ≥ 1.39.0) supports **hwmon-attached** liquid coolers.
Coolers ride the ordinary hwmon path; the daemon classifies coolant sensors as `coolant_temp`,
flags pump/fan headers `is_aio`, and reports a dynamic `aio_hwmon` capability. **Per-driver pump
writability is asymmetric** — the GUI reflects the kernel's per-channel `is_writable` and never
fakes control. There is **no coolant safety rule** (CPU-only thermal safety is unchanged).

| Driver (hwmon `name`) | Devices | Coolant temp | Pump/fan control |
|---|---|---|---|
| `nzxt-kraken3` (`x53`, `z53`, `kraken2023`, `kraken2023elite`; `kraken2024elite` from kernel 7.3) | NZXT Kraken X/Z-series, 2023, 2023 Elite, 2024 Elite | yes | **writable** — `pwm1` pump (+ `pwm2` fan on Z/2023/2024). The driver labels channel 1 "Pump speed", which the daemon's pump rule matches on every model. Since DEC-423 the daemon's cooler list includes `kraken2024elite`, so that model is flagged as an AIO and its radiator fan gets the cooler floor, as on the other models (before, it got the chassis floor; its pump was protected by the label throughout) |
| `nzxt-kraken2` (`kraken2`) | older NZXT Kraken | yes | **monitor-only** (no `pwm` exposed). Note `fan1_input` is the fan and `fan2_input` the pump — the reverse of kraken3 |
| `aquacomputer_d5next` (`d5next`, `highflownext`, `leakshield`, `octo`, `quadro`, `aquaero`, …) | Aquacomputer D5 Next pump; Octo / Quadro / Aquaero fan controllers; flow / leak devices | yes on the liquid devices (labelled channel) | **D5 Next: `pwm1` is the pump duty and `pwm2` the fan, both writable** (`d5next_ctrl_fan_offsets[] = { 0x97, 0x42 } /* Pump and fan speed */`, since 6.0). The daemon maps channel 1 of a cooler chip to the pump role, so the pump floor applies. Octo (`pwm1`–`8`), Quadro and Aquaero (`pwm1`–`4`) are writable fan controllers, not flagged AIO; flow / leak devices expose no `pwm`. (This row said "pump duty monitor-only" until 2026-09-24.) |
| `asus_rog_ryujin` (`rog_ryujin`) | ASUS ROG RYUJIN II / III AIOs | yes (`temp1` labelled "Coolant temp") | **writable** — `pwm1` pump, `pwm2` internal fan, `pwm3` controller fans (RYUJIN II only). Channel 1 is labelled "Pump speed", which the daemon's pump rule matches; not flagged as an AIO. RYUJIN III EXTREME / EVA / WHITE editions added in 7.3 |
| `gigabyte_waterforce` (`waterforce`) | Gigabyte AORUS WATERFORCE X 240 / 280 / 360 | yes | **monitor-only** — fan, pump and coolant readings, all read-only (since 6.8) |
| `arctic_fan_controller` (`arctic_fan`) | ARCTIC Fan Controller (10 channels), kernel 7.2+ | no | **writable, with a driver hazard the daemon works around (DEC-425).** There is no `pwm_enable`, and every command the driver sends carries all ten channels. On 7.2 and 7.3 its duty cache starts at 0 at probe and after resume, so **the first write to one channel would send 0% to the other nine** until each has been written, and reading `pwmN` back returns that cache, not the device. Since DEC-425 the daemon first sets every channel still holding such a 0 to 100%, unless the daemon itself last set that channel to 0 (a curve at 0%, a zero-RPM fan), and does so before the write that would send it. So a profile that controls only some channels leaves the rest at full speed instead of stopping them. It does this again by itself after a resume. The first report of each batch still sends 0 to the channels not yet reached, briefly, for as long as the batch takes (each write waits up to about 0.6 s for the device's reply). If the device stops answering, the daemon stops at the first write that fails and logs one warning, then sets the remaining channels straight after the next write the device accepts. To keep uncontrolled fans quiet, control all ten channels. A change queued for kernel 7.4 starts the cache at 40% instead, and the daemon leaves that alone |
| `corsair-cpro` (`corsaircpro`), `nzxt-smart2` (`nzxtsmart2`) | Commander Pro / RGB & Fan hubs | no (probes are generic) | writable fans, **not** flagged AIO (fan hubs, not coolers). A Commander Pro `pwmN` read returns an error unless that channel was set to a fixed duty |
| USB-only (much Corsair iCUE/Commander Core, some NZXT) | — | — | **out of scope** — no mainline hwmon driver; the daemon never opens USB-HID |

For a coolant sensor the conservative auto-classifier misses, the user can right-click it in
the Overview page's sensor menu → **Treat as coolant**. Empirical write effectiveness is confirmable with
`POST /hwmon/{id}/verify`.

## ACPI Resource Conflicts

Some BIOS implementations claim I/O port ranges used by Super I/O chips
via ACPI OpRegions. When this happens, the kernel refuses to let the hwmon
driver bind to those ports, even though the ACPI claim is cosmetic (the
BIOS firmware doesn't actively use the ports at runtime).

Common conflict ranges:
- `0x0290–0x0299` — Nuvoton NCT6775
- `0x0290–0x029F` — ITE IT87
- `0x04E0–0x04EF` — Nuvoton NCT6775 (secondary)
- `0x0A20–0x0A2F` — ITE IT87 (alternate)
- `0x0A40–0x0A4F` — ITE IT87 (secondary dual-chip)
- `0x0A60–0x0A6F` — ITE IT87 (secondary dual-chip)

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
drivers are loaded at boot. The GUI's Hardware page shows
which modules are currently loaded by reading `/proc/modules`.

## Thermal Safety

The daemon implements a hardware-independent thermal safety rule:
- **Emergency:** hottest CPU temperature >= the trip point → force every OpenFan channel and writable hwmon header the machine has to 100% PWM (GPU fans excluded — PMFW firmware self-protects, DEC-130). The trip point is 105°C, raised per-machine to `min(CPU-reported ceiling + 5, 115)` where the kernel publishes the ceiling (DEC-308)
- **Release:** a fresh reading of the hottest CPU temperature at or below 80°C → exit emergency and resume active profile control at once (no recovery rung since DEC-386). A sensor that goes stale or disappears does not release it
- **Failsafe:** with nothing latched, if no CPU reading is fresh for 5 consecutive cycles → hold the fans the active profile controls at 40% or more (DEC-382), a skipped control's fans at their last duty (DEC-386); fans no profile controls stay under their firmware curve

**Both duties are FLOORS over the active profile's output, not replacements for
it (DEC-307).** Each OpenFan channel and writable hwmon header receives
`max(commanded, forced)`; at 100% an output no control commands still receives the
forced duty — that is what gives the emergency its reach. The 40% floor reaches only
the fans the profile controls, and every other fan the emergency took is given
back when it ends (DEC-382). The ladder can therefore
only ever raise a fan. Before DEC-307 the forced duty replaced the profile's output, so
the 60% and 40% rungs could drive a fan *down* below what its curve was asking for.

The thermal safety state is reported in the hardware diagnostics response.

## Known kernel-version regressions

The daemon ships a curated catalogue (`hwmon/kernel_warnings.rs`,
DEC-098) that matches the running kernel against published amdgpu
regressions and surfaces matches via
`GET /capabilities` (`devices.amd_gpu.kernel_warnings`). The GUI raises a
one-time `QMessageBox` when a high- or critical-severity warning fires,
and lists every match on the System State page. Acknowledged warnings are
remembered in `app_settings.acknowledged_kernel_warnings` so the popup
does not re-fire on every reconnect.

Currently catalogued (DEC-422):

| `id` | Affected kernels | Affected hardware | Severity | Symptom |
|---|---|---|---|---|
| `rdna_mes_hang_drm_amd_4765` | 6.18.0–6.18.6, and 6.17.9–6.17.13 | RDNA3, RDNA3.5 and RDNA4 GPUs: RX 7000 / RX 9000, Radeon Pro W7000 and AI PRO, and the RDNA3 / RDNA3.5 integrated GPUs (780M, 890M, 8060S …) | Critical | A change that entered 6.18 made evicting a process on a MES GPU suspend the whole MES scheduler. That also stops the kernel's own queues, so a compute job running alongside a 3D workload can time out and hang the GPU ([drm/amd #4765](https://gitlab.freedesktop.org/drm/amd/-/issues/4765)). The change was backported into 6.17.9, but the fix never was, and 6.17 is end-of-life. **Fixed in 6.18.7 and 6.19.0** by `3fd20580b96a` ([ChangeLog-6.18.7](https://cdn.kernel.org/pub/linux/kernel/v6.x/ChangeLog-6.18.7)). The 6.12 and 6.6 longterm kernels never had it. Every GC 11.x / 12.x GPU runs MES, hence the hardware scope. The match is on the version number, so a distribution kernel that backported the fix may still be flagged, and one carrying the bug under a `.0` patch level cannot be detected. |

**Retired by DEC-422.** Daemon v2.56.0 and older still raise these two, and the GUI keeps its guidance for both:

- `rdna_hang_kernel_6_18_6_19` flagged every 6.18.x / 6.19.x kernel on RDNA3/RDNA4 as Critical. It advised pinning 6.15–6.17; none of those was ever a longterm kernel, and 6.17.9 onward carries the hang above. Its evidence was an unbisected report ([Phoronix, December 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025)). Phoronix has since published working RX 7000 / RX 9000 results on 6.18 and 7.x. (ROCm #6101, which this row used to cite, carries one unbootable-kernel report on 6.18.20 / 6.19.10 with out-of-tree `amdgpu-dkms`, not hangs under load.)
- `smu_mismatch_navi48_r9700` was keyed on the SMU interface-version message (driver `0x2E`, firmware `0x32` or `0x33`). That message appears on **every** Navi 48 card, the RX 9070 XT included, and is not a fault. The firmware is designed to be backward compatible, and kernel 7.0 removed the message because ["it just leads to user confusion"](https://git.kernel.org/torvalds/c/e471627d56272a791972f25e467348b611c31713). `pwm1` is read-only on every RDNA4 card by driver design; control goes through the PMFW `fan_curve`, which works on at least some R9700s. Separately, a few R9700 owners report the fan not responding under load, one at 109 °C ([ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)). Those reports are per-unit and unresolved; AMD advised an RMA for the original reporter's card.

**Mitigations:**

- For `rdna_mes_hang_drm_amd_4765`, and the retired `rdna_hang_kernel_6_18_6_19`: update to the
  **latest 6.18 longterm** point release or a current stable **7.x** kernel. **Do not**
  move to 6.15, 6.16 or 6.17. None of them was ever a longterm kernel (the kernel.org
  longterm lines are 6.18, 6.12, 6.6, 6.1, 5.15 and 5.10), all three are end-of-life,
  and 6.17.9 onward carries this hang. Until 2026-09-24 this section said the opposite.
- For the retired `smu_mismatch_navi48_r9700`: nothing about the version message needs
  fixing. If an R9700's fan does not follow a curve, return it to automatic mode
  (`POST /gpu/{bdf}/fan/reset`), compare its RPM under load, and consider a warranty
  claim — no pending kernel change addresses it.

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
- [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) — MSI NCT6686D / NCT6687D, `fan_config=msi_alt1` & `msi_fan_brute_force` params (no tagged releases; DKMS off `main`, `MODULE_VERSION` 1.0.0); [PR #164](https://github.com/Fred78290/nct6687d/pull/164) removed the `0xd450` collision (merged 2026-05-19)
- [frankcrawford/it87](https://github.com/frankcrawford/it87) — IT8625E+ support, `force_id` / `ignore_resource_conflict` / `mmio` params; [PR #95](https://github.com/frankcrawford/it87/pull/95) (MMIO default on, 2026-03), [PR #102](https://github.com/frankcrawford/it87/pull/102) (ISA-bridge MMIO/H2RAM merge, 2026-04); issues [#64](https://github.com/frankcrawford/it87/issues/64) (secondary-chip fan control, closed 2025-12), [#70](https://github.com/frankcrawford/it87/issues/70), [#81](https://github.com/frankcrawford/it87/issues/81), [#89](https://github.com/frankcrawford/it87/issues/89) (X870E AORUS ELITE X3D dual-chip report, closed 2026-01-13), [#92](https://github.com/frankcrawford/it87/issues/92) (B650 GAMING X AX V2 ACPI bind failure, closed 2026-02-23), [#94](https://github.com/frankcrawford/it87/issues/94) (DKMS module-path quirk on CachyOS-LTS/Tumbleweed), [#96](https://github.com/frankcrawford/it87/issues/96) (IT8689E Rev 1 — temps-to-90 partial stopgap; driver fix is PR #128, whose own thread carries three IT8689E confirmations from 2026-08-23 while #96 itself has no post-merge report), [#99](https://github.com/frankcrawford/it87/issues/99) (IT8792 suspend/resume, still open), [#103](https://github.com/frankcrawford/it87/issues/103) (X870E AORUS MASTER community label mapping), [#106](https://github.com/frankcrawford/it87/issues/106) (IT8665E mmio-default regression, closed — fixed by PR #120), [#108](https://github.com/frankcrawford/it87/issues/108) (`-Werror=unused-function` build failure); PRs [#114](https://github.com/frankcrawford/it87/pull/114) (IT8689E/IT8696E manual PWM — **closed/REJECTED 2026-08-25**, superseded by #128; never merged), [#128](https://github.com/frankcrawford/it87/pull/128) (fix control issues for many Gigabyte boards — IT8688/8689/8790/8792/8795/87952 manual mode, H2RAM extra fan channels, Gigabyte WMI→SMI, **merged 2026-08-24**; author reports it untested on IT8689 silicon, but the PR thread carries three IT8689E hardware confirmations dated 2026-08-23 — incl. **Rev 1** on a Z790 AORUS MASTER — all against pre-merge head `429d2b40`, which differs from the merged `27319db7` by 267 lines in the Intel H2RAM bridge path), [#129](https://github.com/frankcrawford/it87/pull/129) (follow-up fixes to #128, **merged 2026-08-24**), [#125](https://github.com/frankcrawford/it87/pull/125) (write the updated tachometer-enable mask, **merged 2026-08-25**), [#126](https://github.com/frankcrawford/it87/pull/126) (ISA-bridge MMIO hardening, **open**), [#110](https://github.com/frankcrawford/it87/pull/110) (force_pwm, open), [#120](https://github.com/frankcrawford/it87/pull/120) (remove MMIO for IT8665E — fixes #106, **merged 2026-07-22**), [#119](https://github.com/frankcrawford/it87/pull/119) (GA-X570S-AERO-G sensors config, **merged 2026-07-22**)

**Mainline it87 chip support (cross-checked against the kernel source)**
- [torvalds/linux `drivers/hwmon/it87.c`](https://github.com/torvalds/linux/blob/master/drivers/hwmon/it87.c) — `enum chips` (re-read byte-for-byte at the **v7.2** release tag *and* at `master` on 2026-08-26, kernel 7.2 released 2026-08-16 — the two are identical: includes `it8622`, `it8689`, `it87952`; excludes `it8613`, `it8625`, `it8665`, `it8686`, `it8688`, `it8696`, `it8698`. The only in-tree `it87.c` change since the IT8689E commit is `7f8581c70` "Clamp negative values to zero in set_fan()")
- IT87952E mainline since kernel **6.3** — commit [`d44cb4cd7456`](https://github.com/torvalds/linux/commit/d44cb4cd7456) (2023; v6.2 lacks it, v6.3 has it — this line said 6.4 until 2026-09-24)
- IT8689E mainline from kernel **7.1** — **fan control** (six PWM channels, `FEAT_SIX_PWM` + `FEAT_FANCTL_ONOFF`), not sensors-only; commit [`66b8eaf`](https://github.com/torvalds/linux/commit/66b8eaf) (author 2026-03-22, merged 2026-03-31), released in 7.1 on 2026-06-14. Separately, some Gigabyte **Rev 1** boards have an EC/BIOS quirk that overrides PWM writes ([issue #96](https://github.com/frankcrawford/it87/issues/96)); the maintainer's temps-to-90 stopgap is partial (CPU fan only). The driver-side fix is [PR #128](https://github.com/frankcrawford/it87/pull/128) (merged 2026-08-24; **three IT8689E hardware confirmations incl. Rev 1, but against the pre-merge head** — update `it87-dkms-git` and verify); [PR #114](https://github.com/frankcrawford/it87/pull/114) was rejected 2026-08-25
- IT8613E is queued in hwmon-next, so it is expected in **7.4** ([LWN: it87 IT8613E v4 series](https://lwn.net/Articles/1054427/)). IT8625E has had no mainline submission since the October 2024 v2 round, where changes were requested ([lore thread](https://lore.kernel.org/lkml/b6c2731b-8fac-4e7a-ab0c-2f36e8a64a69@roeck-us.net/T/)). Neither is in 7.3-rc4 (re-checked 2026-09-24)

**GPU device IDs & kernel regressions**
- AMD `amdgpu.ids` (libdrm) and `pci.ids` (hwdata) — Navi 48 `0x7550` (RX 9070 XT rev `0xC0` / RX 9070 rev `0xC3`) and `0x7551` (Radeon AI PRO R9700)
- [Phoronix — RDNA3/RDNA4 hard hang on Linux 6.18/6.19 (EOY 2025)](https://www.phoronix.com/review/old-amdgpu-eoy2025)
- [drm/amd #4765](https://gitlab.freedesktop.org/drm/amd/-/issues/4765) — a bisected hang, reported on RDNA4, in the MES eviction path that every RDNA3 / RDNA4 GPU runs. Introduced by `079ae5118e1f` in 6.18 and backported to 6.17.9 ([ChangeLog-6.17.9](https://cdn.kernel.org/pub/linux/kernel/v6.x/ChangeLog-6.17.9)); fixed by `3fd20580b96a` in 6.18.7 ([ChangeLog-6.18.7](https://cdn.kernel.org/pub/linux/kernel/v6.x/ChangeLog-6.18.7)) and 6.19.0, never on 6.17.y (DEC-422)
- [kernel.org releases](https://www.kernel.org/category/releases.html) — the longterm lines (6.18, 6.12, 6.6, 6.1, 5.15, 5.10); 6.15–6.17 were never longterm
- [Kernel commit e471627d5627](https://git.kernel.org/torvalds/c/e471627d56272a791972f25e467348b611c31713) (v7.0) — "drm/amdgpu/pm: drop SMU driver if version not matched messages — It just leads to user confusion"
- [ROCm Issue #6101](https://github.com/ROCm/ROCm/issues/6101) — per-unit R9700 fan faults; an AMD engineer there confirms the PMFW path works and calls the interface mismatch harmless. **Closed 2026-07-09 as `completed`**, with post-closure reports through 2026-08-28. Its 6.18.20 / 6.19.10 rows are an unbootable-kernel report on out-of-tree `amdgpu-dkms`, not evidence of hangs under load.
- [ROCm Issue #6155](https://github.com/ROCm/ROCm/issues/6155) — RDNA4 / 1002:7551 MES firmware version clarification. **Closed 2026-05-12 as `completed`** (same verification).
- [Bazzite (ublue-os/bazzite) #4498](https://github.com/ublue-os/bazzite/issues/4498) — nct6687 / nct6775 `0xd450` collision brick report; closed 2026-09-07 without a distro fix. Since 2026-08-15 Bazzite ships the Terra `nct6687d` kmod ([`1f234a7e81`](https://github.com/ublue-os/bazzite/commit/1f234a7e81)), which autoloads `nct6687` and blacklists `nct6683`
