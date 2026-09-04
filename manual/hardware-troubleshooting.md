# Hardware Troubleshooting

This page covers the **Hardware Readiness** report on the **System State** page and the situations it helps diagnose: chip detection, kernel driver state, missing sensors, BIOS interference, ACPI conflicts, vendor quirks, and verifying that fan headers actually respond to PWM writes.

> **Quick navigation**
> - The Hardware Readiness report lives on the **System State** page.
> - Click **Rescan Hardware** in the global footer to fetch current state from the daemon.
> - Click **Test PWM Control** to run a ~6-second write test against a selected motherboard header.
> - Click **Characterise PWM Response** for the deeper sweep — how a header responds across 30-100%, reported as three separate verdicts (daemon 2.29.0+).
> - Click **Test GPU Fan Control** to verify an AMD GPU fan actually responds (~6 s, no lease).

If the report tells you a **driver is missing**, the step-by-step install walkthrough (prerequisites, DKMS, verify, rollback) is on the [Driver Setup](driver-setup.md) page. For the chip and driver matrix, see [Hardware Compatibility](../docs/19_Hardware_Compatibility.md). For vendor-by-vendor BIOS notes, see the [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md). For sensor interpretation, see the [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) and the [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md).

New to the terms on this page — hwmon, Super I/O, `pwm_enable`, "read-only headers"? [Understanding Motherboard Fan Control](understanding-fan-control.md) explains them in plain English first, then points back here for the diagnosis.

## What the Hardware Readiness report shows

When you fetch hardware diagnostics, the report populates with:

| Section | What it tells you |
|---------|-------------------|
| **Summary** | One-line headline: chip count, writable header count, and overall readiness |
| **Board info** | Vendor and board name reported by DMI (e.g., `Gigabyte X870E AORUS MASTER`) |
| **Vendor quirk alert** | Auto-shown when a known vendor + chip combination has documented BIOS-level workarounds (e.g., Gigabyte + IT8696E → Smart Fan 6 BIOS notes) |
| **Chips table** | Each detected Super I/O / sensor chip with its expected driver, load status, mainline-or-not, and PWM header count |
| **Kernel modules table** | Modules the daemon expects for your hardware: whether they are loaded and whether they ship in the mainline Linux kernel |
| **ACPI conflicts** | Warnings if an ACPI region claims the same I/O ports as a hwmon driver (most common with `it87` on AMD AM5 boards — driver may need `acpi_enforce_resources=lax`) |
| **Module conflicts** | Warnings when two modules try to claim the same chip (e.g., both `it87` and `nct6775`) |
| **BIOS interference** | Per-header `pwm_enable` reclaim count and severity colour |
| **Thermal safety** | Whether the daemon found a CPU sensor it can use for the emergency / release / recovery safety logic. The panel shows the emergency limit this machine is actually using |
| **GPU diagnostics** | AMD discrete GPU detection, fan control method, and the `amdgpu.ppfeaturemask` state required for PMFW fan curves |

## Test PWM Control

**Where to find it.** Since v2.56.0 the primary place is the **Hardware** page — each PWM header card has its own **Test Control** button, because the test belongs to a physical header. The **System State** page keeps the same controls as an advanced shortcut (and is still the only place with the bulk **Verify All Writable** sweep); both pages run the identical implementation and report identical wording.

For motherboard hwmon headers it is often unclear whether a write actually reaches the fan. The board may accept the write at the sysfs level but the embedded controller (EC) or BIOS overrides it within milliseconds — the classic "Linux says PWM=50%, fan still runs at 100%" problem.

**Test PWM Control** writes a known-distinct PWM value to a chosen header, waits ~6 seconds, then reads back what actually happened. The result is one of:

| Result | Meaning |
|--------|---------|
| **PWM control is working correctly** | The write took effect and RPM responded as expected. The header is genuinely controllable from Linux |
| **BIOS/EC reverted pwm_enable** | The board's EC flipped `pwm_enable` back from manual (1) to automatic (2 or 5) during the wait — fan control is being overridden. This is the dominant failure mode on Gigabyte / AORUS boards with Smart Fan 6. See [AMD Motherboard Fan Control Guide § Gigabyte](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) |
| **PWM value was clamped or ignored** | The write was accepted but the value the hardware reports back differs from what was written (or differs more than expected). Often a partial BIOS override |
| **PWM accepted but RPM did not change** | Write took effect at the sysfs level but the fan did not respond. Either the header has nothing connected, the fan is stalled, or there is no tachometer to confirm |
| **PWM accepted; RPM readback unavailable** | Write looks fine but the board does not provide a `fan*_input` value for this header to confirm |

The result panel also shows the initial → final RPM and `pwm_enable` values, plus a **Next step** suggestion tailored to the result and your board vendor.

### Prerequisites

Just a connected daemon and a writable header to test. The daemon performs the write test under its own internal hwmon lease and coordinates it with its profile engine, so its own per-second writes never collide with the verify wait — the GUI sends no lease and holds none. Earlier versions asked you to "activate a profile to acquire the lease" first; that step is gone in 2.0.0.

## Characterise PWM Response

**Test PWM Control** answers one question at one duty: does a write reach the fan at all? **Characterise PWM Response** answers the fuller one — *how* does this header respond across its range. It holds the header at a series of duties (30%, 40%, … 100% by default), waits for each to settle, and records what came back. Rows appear as they are measured, and you can stop it at any point.

It is a *deeper* test beside the quick one, not a replacement. Use it when a fan or pump behaves oddly rather than plainly not working: it will not tell you anything new about a header that is clearly reverted.

**Requires daemon 2.29.0 or newer.** On an older daemon the button is disabled and its tooltip says why.

Since v2.56.0 the results table also reports **Response** and **Settling** for each step — how long the fan took to react at all, and how long the daemon held that duty — with a *Response latency* and *Typical settling time* summary underneath. Both are measured values: a header with no tachometer, or a fan whose speed never moved, shows an em dash rather than a fabricated zero, and the summary lines are omitted entirely when nothing was measurable. The figures are rounded to a tenth of a second because the daemon samples RPM every 500 ms — any more precision would be invented.

### The three verdicts, and why they are separate

The summary reports three things independently, and this matters more than it looks:

| Verdict | Question it answers |
|---------|--------------------|
| **PWM command** | Did the write itself succeed? |
| **PWM readback** | Did the header report the duty back correctly? |
| **RPM response** | Did the fan actually change speed? |

A pump can accept a PWM command, report it back perfectly, and still run at whatever speed it likes — because many AIO pumps drive themselves during startup or when their own thermal protection kicks in. That combination is reported as a possible device override, **not** as a failed PWM write, and the dialog says so:

> Some pumps temporarily override PWM during startup or internal thermal-protection behaviour. If RPM does not follow PWM, allow that behaviour to finish before concluding that control is unavailable.

If you have just powered the machine on, give the pump a minute and run it again before drawing conclusions.

### What else it reports

- **Observed range** — the measured RPM span across the tested PWM span. One noisy sample is not a hardware specification; treat it as what this run saw.
- **A non-monotonic response** — speed that does not rise steadily with PWM. Reported as an observation. Plenty of healthy pumps and fans are non-linear or hysteretic; it is not a fault on its own.
- **A dead zone** — speed flat at the low end before it starts rising.
- **A PWM clamp** — the header reporting the same duty back for several different requests, which suggests the hardware pins it there.
- **Interference** — another controller (BIOS, EC, or board firmware) taking the header back mid-test. The run stops and says so, because readings taken while something else is driving the header do not mean anything.

### Safety

- **A pump is never driven below 30%, and no header is ever driven to 0%.** The daemon clamps every duty itself; nothing the GUI sends can lower that floor.
- Duties are tested from low to high, so a run that stops early leaves the fan running faster, never slower.
- **Curve control for every fan is paused while the test runs**, and each fan holds its last duty. Thermal safety is unaffected and still overrides everything — the test refuses to start while the system is hot or while thermal protection is active, and stops if either happens mid-run.
- The header's original speed is restored on every exit path on which nothing else owns the fan: finishing, cancelling, a failed write, interference, or a thermal stop. **This happens in the daemon**, so closing the window — or the GUI crashing — does not leave a fan stuck at a test speed.
- The two exceptions are both deliberate, and both leave the fan running *faster* rather than slower: if thermal protection kicks in it keeps the fan high and the original speed is not put back until it releases, and if the daemon is shutting down the header is handed to the motherboard instead. The result tells you which happened, so the window never claims a speed was restored when it was not.

## Test GPU Fan Control

AMD GPU fan control fails *silently* far more often than motherboard headers: the driver accepts a `fan_curve` write but the firmware ignores it (missing `amdgpu.ppfeaturemask` bit `0x4000`), an SMU firmware/driver mismatch swallows it, or a BIOS overdrive lock blocks it. The static **GPU diagnostics** row can show that the *configuration* looks right while fan control still does not work.

**Test GPU Fan Control** (on the **System State** page — shown only when a writable AMD GPU is present and the daemon is ≥ 1.11.0) briefly drives the GPU fan to a test speed — always *upward*, so it never reduces cooling on a hot GPU — waits ~6 seconds, reads back the applied PMFW `fan_curve` (or legacy `pwm1`) and the `fan1_input` RPM, then restores the previous state. No lease is required. The result is one of:

| Result | Meaning & fix |
|--------|---------------|
| **GPU fan control is working** | The fan responded to the test. Nothing to do |
| **Zero-RPM idle (normal)** | The curve applied but the fan stays stopped because the GPU is below its zero-RPM stop temperature. Expected — the fan spins up under load |
| **No RPM sensor to corroborate** | The write was confirmed via curve read-back, but this GPU exposes no `fan1_input` to measure RPM |
| **The GPU ignored the write** | Accepted at sysfs but not applied. Add `amdgpu.ppfeaturemask=0xffffffff` to the kernel command line and reboot; if it is already set, suspect an SMU firmware/driver mismatch or a BIOS overdrive lock (see the GPU advisories on the **System State** page) |
| **Fan did not respond** | The curve applied but RPM did not change with zero-RPM disabled — an SMU firmware issue or a known kernel regression for this GPU. Confirm the fan is physically connected and check your kernel version |
| **BIOS/EC reclaimed control** (legacy `pwm1` GPUs) | `pwm1_enable` reverted to automatic — disable any vendor "Smart Fan" / EC fan-control option in firmware setup |
| **Write was rejected** | The driver/firmware refused the write. Ensure `amdgpu.ppfeaturemask=0xffffffff` is set and that `amdgpu` (not `vfio-pci`) is bound to the GPU |

The firmware **OD_RANGE minimum** (commonly ~15%) and zero-RPM idle are reported as informational outcomes, never as failures — a healthy idle GPU is never flagged as broken. Failure verdicts add their fix to the **issue checklist**. If the control is not shown at all, the GPU has no write path (read-only — see "GPU fan control says feature_unavailable" below) or the daemon is older than 1.11.0.

## Intel Arc GPUs are monitor-only

If you have an Intel Arc discrete GPU (Battlemage / Arc B-series on the `xe` driver, or Alchemist / Arc A-series on `i915`), Control-OFC **monitors** it but cannot control its fan:

- Its package / VRAM / memory-controller / PCIe temperatures appear in the dashboard chart and on the **Overview** page, and any of them can be selected as a **curve sensor** to drive *other* fans.
- Its fan shows up in the dashboard fan table and on the **Overview** page with control method **read-only (firmware-managed)**.
- It is **never** offered as a controllable curve member and is never written to.

This is by design, not a bug or a missing driver. The Linux `xe`/`i915` hwmon interface exposes the GPU fan's RPM (`fan1_input`) as read-only and provides **no PWM/write attribute** — the card's fan is governed autonomously by an on-card firmware blob (shipped in `linux-firmware` as `fan_control_*.bin`). There is no kernel-side knob for Control-OFC, or any other Linux tool, to set its speed. So no lease, PMFW curve, `ppfeaturemask`, or overdrive setting applies to an Intel GPU, and there is no "Test GPU Fan Control" for it.

Only the Arc **B580** currently maps to a specific model name; other Intel discrete GPUs display as "Intel D-GPU" until an authoritative device-ID → name mapping is confirmed for them.

## NVIDIA GPUs are monitor-only

If you have an NVIDIA discrete GPU, Control-OFC **monitors** it but does not drive its fan:

- Its temperature appears in the dashboard chart and on the **Overview** page, and can be selected as a **curve sensor** to drive *other* fans.
- Its fan shows up in the dashboard fan table and on the **Overview** page with control method **read-only**. The NVML path also reports the firmware's *measured* fan duty (shown as "N% duty"), which is distinct from a commanded value and may exceed 100%.
- It is **never** offered as a controllable curve member and is never written to.

This is by design. NVIDIA presents two mutually-exclusive driver worlds, and Control-OFC treats both as read-only:

- **`nouveau`** (the open driver) publishes an hwmon node whose `pwm1` *is* writable, but Control-OFC deliberately **excludes** it from control — GPU fans are owned by the GPU subsystem, the same safety rule applied to AMD.
- **Proprietary NVIDIA** exposes no hwmon fan node; where enabled, Control-OFC reads temperature and fan telemetry through the **NVML** userspace library. This path is **opt-in and off by default** (`[detection] enable_nvidia_telemetry` in the daemon config, plus a `/dev/nvidia*` systemd drop-in) and is **experimental** — built and tested, but not yet verified against NVIDIA hardware. It never writes to the GPU.

Fan *write* control for NVIDIA is a possible future addition, deliberately deferred until it can be validated on real hardware.

## Per-header pwm_enable reclaim count

Some boards (most commonly Gigabyte AM5 with Smart Fan 6) repeatedly reset `pwm_enable` from manual back to automatic. Each reset is a "reclaim" — the daemon sets it back, but the EC keeps stealing it.

The Hardware Readiness report surfaces a per-header count with a severity ramp:

| Reclaim count | Colour | Meaning |
|---------------|--------|---------|
| **0** | Green (OK) | Header is being controlled cleanly — no contention |
| **1–9** | Amber (WARN) | Occasional reclaim; control still working but the EC is fighting back |
| **≥10** | Red (HIGH) | Persistent contention; expect fan speed to drift even though the daemon keeps writing |

The verdict takes the highest severity across all headers, so if any single header is in HIGH state the whole report alerts you to it.

Since v2.56.0 each header's own card in **Cooling Hardware** shows this count too, in its **Details ▸ Capabilities** block — a header that has never been reclaimed reads *Not observed*. A header currently under firmware control **and** with reclaims on record shows a **Control reclaimed** status; a header that was reclaimed in the past but is back under the daemon's control does not, because that is contention the daemon won rather than a live problem.

The daemon includes a watchdog that re-asserts `pwm_enable=1` automatically — control still works in the WARN/HIGH cases, but BIOS Smart Fan 6 should be set to "Manual" for the affected headers (see the vendor guidance the report auto-shows for Gigabyte + IT8696E systems).

## Vendor quirks

When the daemon reports a board vendor and chip combination that matches a known workaround pattern, the Hardware Readiness report automatically renders the relevant guidance — BIOS settings to change, kernel modules to install, or known-issue notes. Each advisory is shown as its own row, most-severe-first, with a colour-coded severity badge that pairs an icon, the word, and a colour — **CRITICAL** (red), **HIGH** (orange), **MEDIUM** (amber), **INFO** (blue) — so an informational note never looks like a warning. The summary is always visible; click **Details** to expand the full explanation and a link to the Hardware Compatibility Guide (CRITICAL and HIGH advisories start expanded; MEDIUM and INFO start collapsed to keep the panel uncluttered). Currently surfaced quirks include:

- **Gigabyte + IT8696E** — Smart Fan 6 BIOS setup notes for AM5 800-series AORUS boards
- **NCT6798 / NCT6799 on ASUS** — typical driver-loaded paths and ASUS WMI sensor helpers

For the full list of vendor-specific workarounds and BIOS settings, see the [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md).

## Fan presence — "no fan detected" annotations

Modern boards ship with more PWM headers than most builds populate. The X870E AORUS MASTER, for example, exposes 8 PWM headers (5 on the IT8696E + 3 on the IT87952E) but a typical build only uses 3–4 of them.

To prevent users from accidentally assigning curves to empty headers, the GUI annotates any **writable** hwmon header that reads 0 RPM with `(no fan detected)`. This appears in:

- Overview → Fan Status → RPM column
- Controls → Fan Role member picker
- Fan Wizard

If you have a fan plugged in and still see "no fan detected", the most common causes are:
- The fan has no tachometer (3-pin DC fans without RPM sense, or pumps wired to the PWM-only `FAN_PUMP` headers)
- The fan is stopped because the header is currently being driven at 0% (zero-RPM mode active)
- The fan is stalled

Use **Test PWM Control** on the header — if the write is effective but RPM does not change, the header is controllable but cannot confirm fan presence; if the write is reverted, the BIOS is overriding control regardless.

## Per-board hwmon header label resolver

On many boards the chip publishes no fan-header names at all — the silkscreen
labels printed next to the headers (`CPU_FAN`, `SYS_FAN1`, …) live on the board,
not in the chip. Every Gigabyte board using the `it87` driver is in this
category. Where sysfs has no name, the daemon reports the header's own node id
(`pwm1`, `pwm2`, …) as a stand-in.

The GUI fills in the gap with a **per-board label resolver** that picks names in
this priority order:

1. **User alias** (set via the [Fan Wizard](fan-wizard.md), or by renaming a fan
   wherever it appears)
2. **Daemon-supplied sysfs label** — *when the chip really published one*. A
   label that just restates the header's own node id (`pwm1` on header 1) is a
   stand-in, not a name, and is skipped so the steps below get their turn
3. **`/etc/sensors.d` and `/usr/share/sensors/*` chip-block labels** (libsensors config, parsed for `chip` / `label` / `ignore` directives)
4. **In-repo fallback table** (curated per-board mapping, matched on your
   motherboard's vendor and model)
5. **The node id** (`pwm1`), when nothing above knows the board

> Before v2.30.0, step 2 accepted the stand-in as a real name, so steps 3-5 never
> ran and every header on such a board displayed as `pwmN`. If you renamed your
> fans by hand to work around that, your names are kept — a user alias still wins.

The fallback table currently covers the **Gigabyte X870E AORUS MASTER** as a worked example:

- IT8696E primary chip — 5 verified silkscreen labels: `CPU_FAN`, `SYS_FAN1`, `SYS_FAN2`, `SYS_FAN3`, `CPU_OPT`
- IT87952E secondary chip — 3 community-reported labels (`SYS_FAN5_PUMP`, `SYS_FAN6_PUMP`, `SYS_FAN4`, from [frankcrawford/it87 issue #103](https://github.com/frankcrawford/it87/issues/103)) suffixed `(unverified)` until silkscreen tracing on a physical board confirms them

If you see an `(unverified)` suffix on a header label, treat the assignment as a hint, not a fact. The Fan Wizard is the safe way to confirm — it stops one fan at a time so you can see exactly which physical fan corresponds to which header.

## Sensors missing or fewer than expected

Fan control depends on sensors: curves need temperatures, and the daemon's thermal safety needs a CPU sensor. If the Dashboard or the **Overview** page shows nothing — or less than you expect — work down this list:

- **No CPU temperature** — the CPU modules (`k10temp` for AMD, `coretemp` for Intel) are mainline and auto-load via device matching on essentially every distribution. If the readiness report's **Thermal safety** row says "no CPU sensor", try loading the module by hand (`sudo modprobe k10temp` or `sudo modprobe coretemp`) and check `sudo dmesg` for errors. Once the module loads, click **Rescan Hardware** (on the **System State** page) — the daemon picks up the new sensor within a couple of poll cycles, no restart needed.
- **No motherboard temperatures or fan RPMs** — Super-I/O chip modules cannot auto-load (the chips sit on ISA I/O ports with no bus-enumerable trigger), so the daemon package ships `/etc/modules-load.d/control-ofc.conf`, which loads `nct6775`, `it87`, `w83627ehf`, and `drivetemp` at boot. Loading a module for a chip that is not present is harmless. If your chip needs an out-of-tree driver instead, the readiness chips table says so — see [Driver Setup](driver-setup.md).
- **No drive temperatures** — NVMe drives report temperatures through the kernel `nvme` driver automatically; SATA/SAS drives need `drivetemp` (already in the daemon's modules-load list above).
- **`lm_sensors` is optional** — the daemon reads `/sys/class/hwmon` directly and does not use libsensors. Installing `lm_sensors` gives you the `sensors` CLI, which is handy for cross-checking what the kernel exposes.

### About `sensors-detect`

Prefer the readiness report first — it identifies your board's chips **without probing the hardware**. Treat `sudo sensors-detect` as a **last resort**, run at your own risk: its probing "can access chips in a way these chips do not like, causing problems ranging from SMBus lockup to permanent hardware damage (a rare case, thankfully)" — [sensors-detect(8)](https://man.archlinux.org/man/extra/lm_sensors/sensors-detect.8.en). If you do run it, accept its conservative defaults rather than answering yes to every probe, and **never run it after boot on a dual-chip Gigabyte board** — it can wedge the Super-I/O bridge so the secondary chip vanishes until reboot (see ["Some of my fan headers are missing"](#some-of-my-fan-headers-are-missing--only-5-of-8-show-up) below).

## Common situations

### "All my hwmon headers show as read-only"

Open the **System State** page, look at the **Hardware Readiness** report:

- If the chips table shows the expected chip but **status is "not loaded"**, the kernel module is missing. The chip column lists which module to install (e.g., `it87-dkms-git` on AUR for Gigabyte AM5 boards).
- If the status is "loaded" but **writable_headers is 0**, run **Test PWM Control** on a header. A `pwm_enable_reverted` result means the BIOS is overriding control — fix it in BIOS Smart Fan settings.
- If `acpi_enforce_resources=lax` is required (common with `it87`), the ACPI conflicts row will tell you so.

### "Some of my fan headers are missing — only 5 of 8 show up"

Open the **System State** page. If the **dual-chip warning banner** at the top of the Hardware Readiness report is visible, your motherboard is one of the dual-IO Gigabyte boards (X870E AORUS MASTER, X670E AORUS MASTER, Z790 AORUS MASTER, etc.) where the secondary ITE chip silently failed to enumerate.

There are **three** causes, and they are not interchangeable — the first two are
fixable and the third is not. Find out which one you have **before** trying
anything, because the remedy for the fixable cases does nothing for the third.

**Find out which:** run `sudo dmesg | grep it87` (or `journalctl -k -b | grep it87`).

| What the kernel says | Which case | Fixable? |
| --- | --- | --- |
| Nothing, or "module not found" | Driver not installed | Yes — case A |
| `Unsupported chip (DEVID=0xFFFF)` | Super-I/O stuck in config mode | Yes — case B |
| `Unsupported chip (DEVID=0x8883)` | Secondary behind a bridge | **No — case C** |

**Case A — the driver build is too old or missing.** Current (2026-03+)
`it87-dkms-git` builds reach the secondary chip through an MMIO path that is on
by default; older builds needed `mmio=on` set manually.

1. Reinstall `it87-dkms-git` (a `-git` package reinstall builds the current
   upstream snapshot — see [Driver Setup](driver-setup.md)).
2. Only on older (pre-2026-03) builds: create `/etc/modprobe.d/it87.conf`
   containing `options it87 mmio=on`.
3. Reboot, then click **Rescan Hardware**.

**Case B — the bridge was left in configuration mode**, most commonly by a
previous run of `sensors-detect`. The secondary chip's DEVID then reads
`0xFFFF`.

1. Avoid running `sensors-detect` after boot.
2. Reboot — this clears it.
3. Click **Rescan Hardware**.

**Case C — the secondary answers `0x8883`. There is no local fix.**
Measured on an **X870E AORUS MASTER** on 2026-09-04, against `it87` at upstream
HEAD: the driver finds the primary IT8696E over MMIO and then reports
`Unsupported chip (DEVID=0x8883)` for the secondary. One `it87` hwmon device
appears instead of two, and roughly three headers stay unreachable.

`0x8883` is almost certainly an ITE eSPI-to-LPC **bridge** answering in place of
the IT87952E behind it ([issue #64](https://github.com/frankcrawford/it87/issues/64)).
The driver has no entry for `0x8883` at all, while it *does* support the
IT87952E — so the chip is **unreachable, not unsupported**.

**Do not spend time on the following — all three are already known not to work:**

- **`mmio=on`** — the MMIO path is already the driver default, so setting it
  changes nothing.
- **Reinstalling `it87-dkms-git`** — the failure reproduces at upstream HEAD.
- **`force_id`** — the reporter on
  [issue #81](https://github.com/frankcrawford/it87/issues/81) forced the ID and
  still lost three fans and a water pump. That issue is **open, not resolved**;
  earlier revisions of this page cited it as a fix, which was wrong.

Your remaining headers work normally. The unreachable ones need driver work
upstream. There is **no open upstream issue tracking this specific case** as at
2026-09-04 — [#64](https://github.com/frankcrawford/it87/issues/64) is where the
eSPI-to-LPC bridge reading comes from but was **closed in 2025-12**, and
[#81](https://github.com/frankcrawford/it87/issues/81) is open for the sibling
STEALTH ICE board without a resolution. Watch the
[driver repository](https://github.com/frankcrawford/it87) rather than a single
issue.

**This is per-board, not per-family.** Other boards in the same generation with
the same IT8696E + IT87952E pairing do work — the X870E AORUS ELITE is
owner-confirmed with both chips controllable
([issue #89](https://github.com/frankcrawford/it87/issues/89)). Do not read case
C as "X870E boards are unsupported".

> One historical exception to "MMIO is good": on **IT8665E** boards (X399 era, e.g. ASUS ROG Zenith Extreme) the MMIO default *broke* PWM writes. This was fixed upstream by [PR #120](https://github.com/frankcrawford/it87/pull/120) (merged 2026-07-22), which removed MMIO support for that chip in the driver, closing [issue #106](https://github.com/frankcrawford/it87/issues/106). On a current build you need no parameter — update the driver. `options it87 mmio=off` is only needed on a build predating that merge.

### "Fans run at full speed regardless of profile"

The daemon's thermal failsafe has two distinct triggers, with different fan speeds:

- **Emergency (100%):** any CPU sensor reports at or above the emergency limit. That limit is at least 105°C, and where the kernel publishes the CPU's own design ceiling the daemon raises it to that ceiling **plus 5°C**, capped at 115°C. The margin matters: a modern Intel part is *meant* to run at its ceiling under load, so a limit set *at* the ceiling would trip on a perfectly healthy machine and then stay tripped, because release needs a reading at or below 80°C that a part holding its ceiling never produces. The Hardware page shows the limit in use. All OpenFan and writable hwmon fans are forced to 100% and held there until the hottest CPU sensor falls to 80°C or below, then run at a 60% recovery floor for two cycles (the release cycle and one more) before the active profile resumes. GPU fans are deliberately excluded — the GPU's own firmware handles GPU thermal protection.
- **No-sensor fallback (40%):** no CPU sensor has been seen for 5 consecutive poll cycles. OpenFan and writable hwmon fans are set to a 40% safe floor — so fans sitting at a uniform ~40% (rather than 100%) usually mean a missing CPU sensor, not an overheat.

Both overrides are owned and driven entirely by the daemon — it forces the fan speeds and holds them itself. The GUI simply reflects what the daemon reports: while an override is active it shows a "Daemon thermal override active" warning (in the Dashboard warning count and the event log on the **Logs** page), driven by the `thermal_state` field in the daemon's 1 Hz poll. Normal profile control resumes automatically once the daemon reports a normal thermal state again — fans pinned during an override are the daemon protecting the system, not a stuck profile.

Check the **Thermal safety** row of the Hardware Readiness report. If it reports "no CPU sensor", install / load the matching driver (`k10temp` for AMD, `coretemp` for Intel are mainline; some boards also need `nct6775` or `it87`). See [Sensors missing or fewer than expected](#sensors-missing-or-fewer-than-expected).

### "GPU fan control says feature_unavailable"

Open the **System State** page, look at the **GPU diagnostics** row. If `amdgpu.ppfeaturemask` is missing bit `0x4000` (`PP_OVERDRIVE_MASK`), the kernel will not expose PMFW fan curves on RDNA3+ GPUs (RX 7000 / 9000 series). Add `amdgpu.ppfeaturemask=0xffffffff` to your kernel command line and reboot. See the [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) doc for the full kernel-parameter explanation.

### "A popup said my kernel has a known regression — should I worry?"

The daemon ships a curated catalogue of amdgpu kernel regressions (`hwmon/kernel_warnings.rs`). When the running kernel matches a known issue affecting your hardware, the GUI raises a one-time popup, and the warning stays listed on the **Logs** page until you acknowledge it. The popup includes a Phoronix or upstream-issue reference link.

Currently catalogued:

- **`rdna_hang_kernel_6_18_6_19` (Critical):** Linux **6.18.x and 6.19.x** on RDNA3/RDNA4 GPUs (RX 7000/9000 series) hard-hang under load ([Phoronix EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025); [ROCm #6101 — closed 2026-07, fault still reported](https://github.com/ROCm/ROCm/issues/6101) reports panics on 6.18.20 and 6.19.10). Pin to a **6.15–6.17** longterm kernel — **do not roll back to 6.18, which is also affected.**
- **`smu_mismatch_navi48_r9700` (Critical):** the AMD R9700 (PCI `0x7551`) has no working `fan_curve` path on current kernels — an SMU interface-version mismatch (firmware v50 vs driver v46, [ROCm #6101 — closed 2026-07, fault still reported](https://github.com/ROCm/ROCm/issues/6101)) leaves `pwm1` read-only and commanded fan changes ineffective. Device-scoped, not 7.0-specific; the RX 9070 XT (`0x7550`) is **not** affected.

If you acknowledge a popup it is remembered in the `acknowledged_kernel_warnings` field of your `app_settings.json` and won't re-fire on reconnect or restart. To force the popup to re-appear (e.g. after a kernel update), edit `app_settings.json` and remove the relevant entry, then restart the GUI.

---

Previous: [Profiles and Curves Reference](profiles-and-curves.md) | Next: [Driver Setup](driver-setup.md) | Back to [Table of Contents](README.md)
