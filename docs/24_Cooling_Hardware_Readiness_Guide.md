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

The daemon's thermal safety relies on a CPU temperature sensor. If none is found,
emergency fan protection cannot key off CPU temperature.

**What to do:** first check whether a CPU sensor exists but was not auto-selected —
open the **Overview** page and look for a `k10temp`/`coretemp`/`Tctl`/`Tdie`
reading. If one exists, set it as your preferred CPU sensor (see below). If none
exists at all, your motherboard's Super-I/O driver may not be loaded — see *Loading
an in-kernel Super-I/O driver*.

## Selecting a preferred sensor

The daemon auto-picks a CPU (and motherboard) temperature sensor, but you can choose
a specific one. The **Pick a CPU sensor** / **Pick a motherboard sensor** action
opens the **Settings** page (Preferred Sensors card). Your choice is persisted by the daemon and is
advisory — it never silently replaces a working sensor, and a selection that later
disappears (a chip that stopped being detected) is flagged as *stale* here so you can
re-pick. This changes only the daemon's own configuration file; no hardware is touched.

## PWM detected but not verified

The daemon found writable PWM (`pwmN`) attributes, but has not confirmed that writing
them moves a fan. Writable ≠ controllable: routed PWM pins, BIOS/EC behaviour, and
ACPI can all override the chip. Use the fan-control verification workflow to confirm.

## Fan-control verification

The **Test PWM control** action opens the fan-control verification workflow
(on the **System State** page). It briefly nudges a fan and observes the RPM
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
  does not change fan speeds.
- **Temporary vs. persistent:** `modprobe` lasts until reboot. To load at every boot,
  add the module name to `/etc/modules-load.d/` (e.g.
  `echo nct6775 | sudo tee /etc/modules-load.d/nct6775.conf`).
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
misconfigure the chip.

## ACPI I/O-port conflicts

If your firmware's ACPI tables claim the same I/O ports the Super-I/O driver needs,
the driver may refuse to bind (under the default `acpi_enforce_resources=strict`).
The page lists the affected driver(s). Relaxing the enforcement is possible but is a
firmware-interaction change — research it for your specific board before proceeding,
and prefer a BIOS update where one addresses the conflict.

## Active port probing

Passive detection is normally sufficient. When a Super-I/O chip is present but its
driver is not loaded, the optional **Advanced detection ▸ Probe ports** action can
read the chip's configuration I/O ports directly to identify it. This:

- requires the `CAP_SYS_RAWIO` capability (it is off by default and needs a daemon
  opt-in);
- accesses hardware I/O ports directly, so it asks for explicit confirmation;
- is a deliberate one-shot — it never runs automatically, and the daemon refuses any
  port already claimed by a driver or ACPI.

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
