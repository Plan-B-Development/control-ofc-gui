# Cooling Hardware Readiness — user guide

The **Hardware** page (the *Cooling Hardware Readiness* page) is a
read-only, plain-language assessment of your cooling hardware: what is ready, what
needs attention, and the recommended next step. Opening or refreshing it **does not
change your system** — it never loads kernel modules, installs packages, writes fan
speeds, probes hardware ports, or changes your sensor selection. Every "Learn how"
link on that page points here.

> Kernel-module and hardware-access changes can affect system stability. Review the
> guidance for your hardware before proceeding. Control-OFC does not apply these
> changes automatically.

Each finding distinguishes four different things, in order of certainty — do not
conflate them:

- **detected** — a chip or PWM attribute is present;
- **writable** — the daemon can write the attribute;
- **driver bound** — a kernel driver is actually bound and exposing the chip;
- **control verified** — writing the value was observed to move the intended fan.

Detecting a writable PWM attribute is **not** proof that changing it controls the
fan you expect. When in doubt, use the fan-control verification workflow below.

For any command shown below: it is provided **for you to review and run yourself**.
Control-OFC never runs it for you. Where a change is temporary vs. persistent, or may
need a reboot, that is called out. Always check the command against your motherboard
and kernel version first.

---

## No usable CPU temperature source

The daemon's thermal safety reads the hottest CPU temperature sensor — `k10temp` on
AMD, `coretemp` on Intel. With none, there is no thermal emergency to trip, and once
no CPU reading has been fresh for five seconds the fans your **active profile**
controls are held at 40 % or more. Fans no profile controls are not touched, and with
no profile active nothing is forced.

**What to do:** both modules are mainline and load on their own on almost every
system, so check that yours did:

```bash
lsmod | grep -E 'k10temp|coretemp'
```

If neither is listed, load the one for your CPU (`sudo modprobe k10temp` or
`sudo modprobe coretemp`) and click **Rescan Hardware** in the footer. To load it at
every boot, put its name on a line of its own in a file under `/etc/modules-load.d/`.
If the module loads but reports no temperature, your kernel may predate your CPU —
update the kernel. On a board with a Nuvoton chip, the chip's CPU channels (`PECI`,
`TSI`, and `CPUTIN` except on ASUS boards, where it is often unconnected) count as
CPU sensors too, so loading the board's driver can also supply one — see *Loading an
in-kernel Super-I/O driver*. Picking a preferred CPU sensor (below) changes none of
this: thermal safety always uses the hottest CPU sensor.

## Selecting a preferred sensor

The daemon auto-picks a CPU (and motherboard) temperature sensor, but you can choose
a specific one. The **Pick a CPU sensor** / **Pick a motherboard sensor** action
opens the **Settings** page (Preferred Sensors card). Your choice is persisted by the daemon and is
advisory — it never silently replaces a working sensor, and a selection that later
disappears (a chip that stopped being detected) is flagged as *stale* here so you can
re-pick. This changes only the daemon's own configuration file; no hardware is touched.

## Read-only PWM headers

The daemon found PWM (`pwmN`) attributes that the kernel publishes read-only, so the
fans on those headers can be watched but not controlled. Nothing can be written to
them, so fan-control verification cannot help. The cause is almost always the
driver, not the board:

- **The bound driver is monitoring-only for the chip.** The usual case is the
  in-kernel `nct6683`, which covers the NCT6683D, NCT6686D and NCT6687D on many MSI
  and ASRock boards. It publishes `pwmN` read-only on every board except Mitac OEM
  systems and has no `pwmN_enable` at all, so no BIOS setting unlocks it.
  `ls -l /sys/class/hwmon/hwmon*/device/driver` shows which driver is bound; the
  device name does not, because the out-of-tree drivers use the same names.
  - **MSI (NCT6687D):** install `nct6687d-dkms-git` and blacklist `nct6683`, then
    reboot. With both loaded they can bind the same chip and garble readings.
  - **ASRock (NCT6686D / NCT6683D):** the fans on that chip need a board-specific
    out-of-tree driver — for example `asrock-nct6683` for the boards it lists, or
    `nct6687d` on some boards (its fan labels are MSI's, so check which header is
    which).

  Installing the wrong out-of-tree driver can do harm. Read the board's row in
  *Driver Setup* (in the user manual) before changing anything.
- **The channel is not controllable.** Some chips report a duty for a channel they
  cannot drive. No driver change helps there.

The **Open System State** action opens the System State page. There, hovering your
chip's row in the **Hardware Registry** table shows its known issues, and the
**Board notes** section lists any vendor quirk that matches your board.

## PWM detected but not verified

The daemon found writable PWM (`pwmN`) attributes, but has not confirmed that writing
them moves a fan. Writable ≠ controllable: routed PWM pins, BIOS/EC behaviour, and
ACPI can all override the chip. Use the fan-control verification workflow to confirm.

## Fan-control verification

The **Test PWM control** action scrolls to this page's own **Hardware Diagnostics**
section, whose **PWM Test Report…** tests the headers you choose in one run. Each
header's card under **Cooling Hardware** also has its own **Test Control** button, and
the **System State** page keeps the same test as an advanced shortcut. It briefly nudges a fan and observes the RPM
response to confirm the control path actually works — the honest way to turn
"detected/writable" into "control verified". It is thermally guarded and reverts
after the test.

## Loading an in-kernel Super-I/O driver

Most motherboards expose their fan/temperature sensors through a **Super-I/O** chip
(ITE `it87`, Nuvoton `nct6775`, …). If the chip is detected but its driver is not
loaded, the sensors and fan controls are invisible. The Super-I/O details section
shows the exact module and a copy-paste command, for example:

```
sudo modprobe nct6775
```

- **What it changes:** loads a kernel module so the chip's hwmon device appears. It
  does not change fan speeds. Its temperatures and fan speeds appear after **Rescan
  Hardware**, but the daemon controls a fan header only once it has been restarted
  with that header present: `sudo systemctl restart control-ofc-daemon`. The same
  applies after reloading a driver.
- **On a Gigabyte board, `nct6775` and `w83627ehf` do not load.** The daemon
  package's Super-I/O guard declines both there — Gigabyte boards use ITE chips,
  which those drivers cannot bind, and their probe can hide a second chip — so the
  command reports success and nothing appears. `sudo journalctl -b -t
  control-ofc-superio-guard` shows each module it declined. You need `it87` there.
- **Temporary vs. persistent:** `modprobe` lasts until reboot. The daemon package's
  `/etc/modules-load.d/control-ofc.conf` already loads `nct6775`, `it87`,
  `w83627ehf` and `drivetemp` at every boot. Any other module needs a file of its
  own in `/etc/modules-load.d/` (e.g.
  `echo nct6687 | sudo tee /etc/modules-load.d/nct6687.conf` for an out-of-tree
  `nct6687d` build).
- **Reboot:** usually not required to load the module; a reboot may be needed if the
  BIOS/ACPI is claiming the chip's I/O ports (see *ACPI I/O-port conflicts*).
- **Compatibility:** confirm the recommended module matches your board and kernel.
  The page marks whether the driver is in the mainline kernel or needs an
  out-of-tree (DKMS) build — see below.

## Unsupported chips and DKMS drivers

Some chips (notably several Gigabyte ITE variants) are not supported by the mainline
`it87` driver on common LTS kernels and need an out-of-tree build such as
`it87-dkms-git`. The page marks these as **needs out-of-tree (DKMS) driver** and shows
the driver, but installing a DKMS package is a system change you should make
deliberately, after checking it matches your chip. Never pass `force_id` — it can
misconfigure the chip. **`it87-dkms-git` builds from 2026-09-09 (the driver's v2.0)
rename Gigabyte chips** — e.g. `it8696_a008090a`. control-ofc-daemon 3.0.0 and newer
strip that suffix, so no fan header's id changes; on an older daemon every id changes and
pump roles, fan names and profile members need re-checking afterwards — the manual's
Driver Setup page explains how to stay on the old names until the daemon is updated.

## ACPI I/O-port conflicts

If your firmware's ACPI tables claim the same I/O ports the Super-I/O driver needs,
the driver may refuse to bind (under the default `acpi_enforce_resources=strict`),
typically failing with *Device or resource busy*. The page lists the affected
driver(s). Prefer a BIOS update where one addresses the conflict; otherwise the
remedies differ by driver, and they are **not** equally safe:

- **`it87` (ITE) — preferred: the driver-local option.** Add
  `options it87 ignore_resource_conflict=1` to `/etc/modprobe.d/it87.conf`. This
  relaxes the check for **this driver only**. Upstream recommends it over the
  system-wide parameter, because there are reports that a system-wide
  `acpi_enforce_resources=lax` can cause **boot failures** on some systems.
- **`nct6775` / `nct6687` (Nuvoton) — no driver-local equivalent exists.** These
  drivers expose no `ignore_resource_conflict` parameter, so the system-wide
  `acpi_enforce_resources=lax` kernel parameter is the only kernel-side remedy.
  Treat it as the larger hammer it is. On supported ASUS boards `nct6775`
  sidesteps the conflict on its own through an ACPI WMI access path, so no
  parameter is needed there.

Either way this is a firmware-interaction change: make it deliberately, and
re-run **Test PWM Control** afterwards to confirm it actually helped.

## Active port probing

Passive detection is normally sufficient. When a Super-I/O chip is present but its
driver is not loaded, the optional **Probe ports (advanced)** action can
access the chip's configuration I/O ports directly to identify it. This:

- is off by default. The daemon operator enables it with `allow_port_probe = true`
  under `[detection]` in `/etc/control-ofc/daemon.toml`, installs
  `/usr/share/doc/control-ofc-daemon/superio-port-probe.conf.example` as
  `/etc/systemd/system/control-ofc-daemon.service.d/superio-port-probe.conf` (it
  grants the root-equivalent `CAP_SYS_RAWIO`), then runs
  `sudo systemctl daemon-reload` and restarts `control-ofc-daemon`;
- **is not read-only.** Where no chip answers a plain read (`0xffff` or `0x0000`), the daemon writes a
  vendor unlock and exit sequence. The Nuvoton `0x87,0x87` unlock is the one
  measured latching an ITE eSPI-to-LPC bridge until a power-down at the wall, and it is
  withheld only on boards the daemon lists as ITE-only. So the GUI asks for
  explicit confirmation first;
- **refuses the whole probe while any recognised Super-I/O driver is bound**, not
  only the port that driver owns (DEC-433). With `it87` loaded on a dual-chip
  Gigabyte board it therefore never runs. It also refuses when `/proc/ioports`
  cannot be read, and skips a port that file shows reserved by a driver or ACPI;
- is a deliberate one-shot — it never runs automatically, and a second run within
  10 seconds is refused with a note to try again.

Its result is labelled as coming from the active probe (evidence `port_probe`) so it
is never confused with passive detection.

## Quarantined or unclassified sensors

- **Quarantined sensors** are sensors the daemon discovered but could not read (for
  example a WiFi chip's temperature while the radio is off). They are set aside so
  they don't spam logs or raise false staleness warnings; they appear, display-only,
  on the **Overview** page. No action is usually required.
- **Unclassified sensors** are temperature readings the daemon could not confidently
  categorise. They are still shown; if one is your CPU/motherboard sensor, set it as a
  preferred sensor (above) so it is used deliberately.
