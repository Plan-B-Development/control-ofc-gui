# Hardware Troubleshooting

This page covers the **Hardware Readiness** report on the Diagnostics → Troubleshooting tab and the situations it helps diagnose: chip detection, kernel driver state, missing sensors, BIOS interference, ACPI conflicts, vendor quirks, and verifying that fan headers actually respond to PWM writes.

> **Quick navigation**
> - The Hardware Readiness report lives at **Diagnostics → Troubleshooting**.
> - Click **Refresh Hardware Diagnostics** in the tab header to fetch current state from the daemon.
> - Click **Test PWM Control** to run a ~6-second write test against a selected motherboard header.
> - Click **Test GPU Fan Control** to verify an AMD GPU fan actually responds (~6 s, no lease).

If the report tells you a **driver is missing**, the step-by-step install walkthrough (prerequisites, DKMS, verify, rollback) is on the [Driver Setup](driver-setup.md) page. For the chip and driver matrix, see [Hardware Compatibility](../docs/19_Hardware_Compatibility.md). For vendor-by-vendor BIOS notes, see the [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md). For sensor interpretation, see the [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) and the [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md).

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
| **Thermal safety** | Whether the daemon found a CPU sensor it can use for the 105°C / 80°C / 60°C safety logic |
| **GPU diagnostics** | AMD discrete GPU detection, fan control method, and the `amdgpu.ppfeaturemask` state required for PMFW fan curves |

## Test PWM Control

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

Test PWM Control requires a held hwmon lease. The GUI normally takes the lease automatically when fan control starts. If you see `Cannot verify: no hwmon lease held`, activate any profile (or open Controls → activate) so the lease is acquired, then try again.

While the test is running, the GUI's 1 Hz control loop pauses writes to the header under test so its own ticks do not interfere with the daemon's verify wait. A 9-second safety timer (set above the daemon's ~6-second verify wait) guarantees writes resume even if the test hangs.

## Test GPU Fan Control

AMD GPU fan control fails *silently* far more often than motherboard headers: the driver accepts a `fan_curve` write but the firmware ignores it (missing `amdgpu.ppfeaturemask` bit `0x4000`), an SMU firmware/driver mismatch swallows it, or a BIOS overdrive lock blocks it. The static **GPU diagnostics** row can show that the *configuration* looks right while fan control still does not work.

**Test GPU Fan Control** (Diagnostics → Troubleshooting — shown only when a writable AMD GPU is present and the daemon is ≥ 1.11.0) briefly drives the GPU fan to a test speed — always *upward*, so it never reduces cooling on a hot GPU — waits ~6 seconds, reads back the applied PMFW `fan_curve` (or legacy `pwm1`) and the `fan1_input` RPM, then restores the previous state. No lease is required. The result is one of:

| Result | Meaning & fix |
|--------|---------------|
| **GPU fan control is working** | The fan responded to the test. Nothing to do |
| **Zero-RPM idle (normal)** | The curve applied but the fan stays stopped because the GPU is below its zero-RPM stop temperature. Expected — the fan spins up under load |
| **No RPM sensor to corroborate** | The write was confirmed via curve read-back, but this GPU exposes no `fan1_input` to measure RPM |
| **The GPU ignored the write** | Accepted at sysfs but not applied. Add `amdgpu.ppfeaturemask=0xffffffff` to the kernel command line and reboot; if it is already set, suspect an SMU firmware/driver mismatch or a BIOS overdrive lock (see the GPU advisories in the Troubleshooting tab) |
| **Fan did not respond** | The curve applied but RPM did not change with zero-RPM disabled — an SMU firmware issue or a known kernel regression for this GPU. Confirm the fan is physically connected and check your kernel version |
| **BIOS/EC reclaimed control** (legacy `pwm1` GPUs) | `pwm1_enable` reverted to automatic — disable any vendor "Smart Fan" / EC fan-control option in firmware setup |
| **Write was rejected** | The driver/firmware refused the write. Ensure `amdgpu.ppfeaturemask=0xffffffff` is set and that `amdgpu` (not `vfio-pci`) is bound to the GPU |

The firmware **OD_RANGE minimum** (commonly ~15%) and zero-RPM idle are reported as informational outcomes, never as failures — a healthy idle GPU is never flagged as broken. Failure verdicts add their fix to the **issue checklist**. If the control is not shown at all, the GPU has no write path (read-only — see "GPU fan control says feature_unavailable" below) or the daemon is older than 1.11.0.

## Intel Arc GPUs are monitor-only

If you have an Intel Arc discrete GPU (Battlemage / Arc B-series on the `xe` driver, or Alchemist / Arc A-series on `i915`), Control-OFC **monitors** it but cannot control its fan:

- Its package / VRAM / memory-controller / PCIe temperatures appear in the dashboard chart and Diagnostics → Sensors, and any of them can be selected as a **curve sensor** to drive *other* fans.
- Its fan shows up in the dashboard fan table and Diagnostics → Fans with control method **read-only (firmware-managed)**.
- It is **never** offered as a controllable curve member and is never written to.

This is by design, not a bug or a missing driver. The Linux `xe`/`i915` hwmon interface exposes the GPU fan's RPM (`fan1_input`) as read-only and provides **no PWM/write attribute** — the card's fan is governed autonomously by an on-card firmware blob (shipped in `linux-firmware` as `fan_control_*.bin`). There is no kernel-side knob for Control-OFC, or any other Linux tool, to set its speed. So no lease, PMFW curve, `ppfeaturemask`, or overdrive setting applies to an Intel GPU, and there is no "Test GPU Fan Control" for it.

Only the Arc **B580** currently maps to a specific model name; other Intel discrete GPUs display as "Intel D-GPU" until an authoritative device-ID → name mapping is confirmed for them.

## Per-header pwm_enable reclaim count

Some boards (most commonly Gigabyte AM5 with Smart Fan 6) repeatedly reset `pwm_enable` from manual back to automatic. Each reset is a "reclaim" — the daemon sets it back, but the EC keeps stealing it.

The Hardware Readiness report surfaces a per-header count with a severity ramp:

| Reclaim count | Colour | Meaning |
|---------------|--------|---------|
| **0** | Green (OK) | Header is being controlled cleanly — no contention |
| **1–9** | Amber (WARN) | Occasional reclaim; control still working but the EC is fighting back |
| **≥10** | Red (HIGH) | Persistent contention; expect fan speed to drift even though the GUI keeps writing |

The verdict takes the highest severity across all headers, so if any single header is in HIGH state the whole report alerts you to it.

The daemon includes a watchdog that re-asserts `pwm_enable=1` automatically — control still works in the WARN/HIGH cases, but BIOS Smart Fan 6 should be set to "Manual" for the affected headers (see the vendor guidance the report auto-shows for Gigabyte + IT8696E systems).

## Vendor quirks

When the daemon reports a board vendor and chip combination that matches a known workaround pattern, the Hardware Readiness report automatically renders the relevant guidance — BIOS settings to change, kernel modules to install, or known-issue notes. Each advisory is shown as its own row, most-severe-first, with a colour-coded severity badge that pairs an icon, the word, and a colour — **CRITICAL** (red), **HIGH** (orange), **MEDIUM** (amber), **INFO** (blue) — so an informational note never looks like a warning. The summary is always visible; click **Details** to expand the full explanation and a link to the Hardware Compatibility Guide (CRITICAL and HIGH advisories start expanded; MEDIUM and INFO start collapsed to keep the panel uncluttered). Currently surfaced quirks include:

- **Gigabyte + IT8696E** — Smart Fan 6 BIOS setup notes for AM5 800-series AORUS boards
- **NCT6798 / NCT6799 on ASUS** — typical driver-loaded paths and ASUS WMI sensor helpers

For the full list of vendor-specific workarounds and BIOS settings, see the [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md).

## Fan presence — "no fan detected" annotations

Modern boards ship with more PWM headers than most builds populate. The X870E AORUS MASTER, for example, exposes 8 PWM headers (5 on the IT8696E + 3 on the IT87952E) but a typical build only uses 3–4 of them.

To prevent users from accidentally assigning curves to empty headers, the GUI annotates any **writable** hwmon header that reads 0 RPM with `(no fan detected)`. This appears in:

- Diagnostics → Fans → RPM column
- Controls → Fan Role member picker
- Fan Wizard

If you have a fan plugged in and still see "no fan detected", the most common causes are:
- The fan has no tachometer (3-pin DC fans without RPM sense, or pumps wired to the PWM-only `FAN_PUMP` headers)
- The fan is stopped because the header is currently being driven at 0% (zero-RPM mode active)
- The fan is stalled

Use **Test PWM Control** on the header — if the write is effective but RPM does not change, the header is controllable but cannot confirm fan presence; if the write is reverted, the BIOS is overriding control regardless.

## Per-board hwmon header label resolver

The daemon reports each hwmon header with whatever label `/sys` exposes. On many boards the kernel returns generic labels like `pwm1` / `pwm2` / `pwm3` because the chip itself does not know the silkscreen names.

The GUI fills in the gap with a **per-board label resolver** that picks names in this priority order:

1. **User alias** (set via the [Fan Wizard](fan-wizard.md))
2. **Daemon-supplied sysfs label** (when the kernel knows it)
3. **`/etc/sensors.d` and `/usr/share/sensors/*` chip-block labels** (libsensors config, parsed for `chip` / `label` / `ignore` directives)
4. **In-repo fallback table** (curated per-board mapping)

The fallback table currently covers the **Gigabyte X870E AORUS MASTER** as a worked example:

- IT8696E primary chip — 5 verified silkscreen labels: `CPU_FAN`, `SYS_FAN1`, `SYS_FAN2`, `SYS_FAN3`, `CPU_OPT`
- IT87952E secondary chip — 3 community-reported labels (`SYS_FAN5_PUMP`, `SYS_FAN6_PUMP`, `SYS_FAN4`, from [frankcrawford/it87 issue #103](https://github.com/frankcrawford/it87/issues/103)) suffixed `(unverified)` until silkscreen tracing on a physical board confirms them

If you see an `(unverified)` suffix on a header label, treat the assignment as a hint, not a fact. The Fan Wizard is the safe way to confirm — it stops one fan at a time so you can see exactly which physical fan corresponds to which header.

## Sensors missing or fewer than expected

Fan control depends on sensors: curves need temperatures, and the daemon's thermal safety needs a CPU sensor. If the Dashboard or Diagnostics → Sensors shows nothing — or less than you expect — work down this list:

- **No CPU temperature** — the CPU modules (`k10temp` for AMD, `coretemp` for Intel) are mainline and auto-load via device matching on essentially every distribution. If the readiness report's **Thermal safety** row says "no CPU sensor", try loading the module by hand (`sudo modprobe k10temp` or `sudo modprobe coretemp`) and check `sudo dmesg` for errors. Once the module loads, click **Rescan Hardware** (Diagnostics → Troubleshooting) — the daemon picks up the new sensor within a couple of poll cycles, no restart needed.
- **No motherboard temperatures or fan RPMs** — Super-I/O chip modules cannot auto-load (the chips sit on ISA I/O ports with no bus-enumerable trigger), so the daemon package ships `/etc/modules-load.d/control-ofc.conf`, which loads `nct6775`, `it87`, `w83627ehf`, and `drivetemp` at boot. Loading a module for a chip that is not present is harmless. If your chip needs an out-of-tree driver instead, the readiness chips table says so — see [Driver Setup](driver-setup.md).
- **No drive temperatures** — NVMe drives report temperatures through the kernel `nvme` driver automatically; SATA/SAS drives need `drivetemp` (already in the daemon's modules-load list above).
- **`lm_sensors` is optional** — the daemon reads `/sys/class/hwmon` directly and does not use libsensors. Installing `lm_sensors` gives you the `sensors` CLI, which is handy for cross-checking what the kernel exposes.

### About `sensors-detect`

Prefer the readiness report first — it identifies your board's chips **without probing the hardware**. Treat `sudo sensors-detect` as a **last resort**, run at your own risk: its probing "can access chips in a way these chips do not like, causing problems ranging from SMBus lockup to permanent hardware damage (a rare case, thankfully)" — [sensors-detect(8)](https://man.archlinux.org/man/extra/lm_sensors/sensors-detect.8.en). If you do run it, accept its conservative defaults rather than answering yes to every probe, and **never run it after boot on a dual-chip Gigabyte board** — it can wedge the Super-I/O bridge so the secondary chip vanishes until reboot (see ["Some of my fan headers are missing"](#some-of-my-fan-headers-are-missing--only-5-of-8-show-up) below).

## Common situations

### "All my hwmon headers show as read-only"

Open Diagnostics → Troubleshooting, look at the **Hardware Readiness** report:

- If the chips table shows the expected chip but **status is "not loaded"**, the kernel module is missing. The chip column lists which module to install (e.g., `it87-dkms-git` on AUR for Gigabyte AM5 boards).
- If the status is "loaded" but **writable_headers is 0**, run **Test PWM Control** on a header. A `pwm_enable_reverted` result means the BIOS is overriding control — fix it in BIOS Smart Fan settings.
- If `acpi_enforce_resources=lax` is required (common with `it87`), the ACPI conflicts row will tell you so.

### "Some of my fan headers are missing — only 5 of 8 show up"

Open Diagnostics → Troubleshooting. If the **dual-chip warning banner** at the top of the Hardware Readiness report is visible, your motherboard is one of the dual-IO Gigabyte boards (X870E AORUS MASTER, X670E AORUS MASTER, Z790 AORUS MASTER, etc.) where the secondary ITE chip silently failed to enumerate.

This usually means one of:

- The installed `it87` driver build is too old. Current (2026-03+) `it87-dkms-git` builds reach the secondary chip through an MMIO path that is on by default, and both enumerate **and** control it; older builds need the `mmio=on` module parameter set manually.
- The SuperIO bridge was left in configuration mode by an earlier process (most commonly a previous run of `sensors-detect`), so the secondary chip's DEVID read returned `0xFFFF`.

The fix, in order:

1. Update the driver: reinstall `it87-dkms-git` (a `-git` package reinstall builds the current upstream snapshot — see [Driver Setup](driver-setup.md)).
2. Only on older (pre-2026-03) builds: create `/etc/modprobe.d/it87.conf` containing `options it87 mmio=on`.
3. Avoid running `sensors-detect` after boot.
4. Reboot.
5. Click **Refresh Hardware Diagnostics** — the chips table should now list both ITE chips and `total_headers` should match what the board physically exposes.

If the warning persists after these steps, see the upstream tracker thread at [frankcrawford/it87 issue #70](https://github.com/frankcrawford/it87/issues/70) for board-specific notes.

> One exception to "MMIO is good": on **IT8665E** boards (X399 era, e.g. ASUS ROG Zenith Extreme) the new MMIO default *breaks* PWM writes — set `options it87 mmio=off` there instead ([issue #106](https://github.com/frankcrawford/it87/issues/106)).

### "Fans run at full speed regardless of profile"

The daemon's thermal failsafe has two distinct triggers, with different fan speeds:

- **Emergency (100%):** any CPU sensor reports ≥ 105°C. All OpenFan and writable hwmon fans are forced to 100% and held there until the hottest CPU sensor falls to 80°C or below, then run at a 60% recovery floor for two cycles (the release cycle and one more) before the active profile resumes. GPU fans are deliberately excluded — the GPU's own firmware handles GPU thermal protection.
- **No-sensor fallback (40%):** no CPU sensor has been seen for 5 consecutive poll cycles. OpenFan and writable hwmon fans are set to a 40% safe floor — so fans sitting at a uniform ~40% (rather than 100%) usually mean a missing CPU sensor, not an overheat.

While either override is active the GUI **stands down on purpose**: the control loop pauses all fan writes, hwmon lease renewals pause, and a "Daemon thermal override active" warning appears (Dashboard warning count and the Diagnostics event log). Control resumes automatically once the daemon reports a normal thermal state — fans pinned during an override are the daemon protecting the system, not a stuck profile.

Check the **Thermal safety** row of the Hardware Readiness report. If it reports "no CPU sensor", install / load the matching driver (`k10temp` for AMD, `coretemp` for Intel are mainline; some boards also need `nct6775` or `it87`). See [Sensors missing or fewer than expected](#sensors-missing-or-fewer-than-expected).

### "GPU fan control says feature_unavailable"

Open Diagnostics → Troubleshooting, look at the **GPU diagnostics** row. If `amdgpu.ppfeaturemask` is missing bit `0x4000` (`PP_OVERDRIVE_MASK`), the kernel will not expose PMFW fan curves on RDNA3+ GPUs (RX 7000 / 9000 series). Add `amdgpu.ppfeaturemask=0xffffffff` to your kernel command line and reboot. See the [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) doc for the full kernel-parameter explanation.

### "A popup said my kernel has a known regression — should I worry?"

The daemon ships a curated catalogue of amdgpu kernel regressions (`hwmon/kernel_warnings.rs`, DEC-098). When the running kernel matches a known issue affecting your hardware, the GUI raises a one-time popup, and the warning stays listed on the Diagnostics page until you acknowledge it. The popup includes a Phoronix or upstream-issue reference link.

Currently catalogued:

- **`rdna_hang_kernel_6_18_6_19` (Critical):** Linux **6.18.x and 6.19.x** on RDNA3/RDNA4 GPUs (RX 7000/9000 series) hard-hang under load ([Phoronix EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025); [ROCm #6101](https://github.com/ROCm/ROCm/issues/6101) reports panics on 6.18.20 and 6.19.10). Pin to a **6.15–6.17** longterm kernel — **do not roll back to 6.18, which is also affected.**
- **`smu_mismatch_navi48_r9700` (Critical):** the AMD R9700 (PCI `0x7551`) has no working `fan_curve` path on current kernels — an SMU interface-version mismatch (firmware v50 vs driver v46, [ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)) leaves `pwm1` read-only and commanded fan changes ineffective. Device-scoped, not 7.0-specific; the RX 9070 XT (`0x7550`) is **not** affected.

If you acknowledge a popup it is remembered in the `acknowledged_kernel_warnings` field of your `app_settings.json` and won't re-fire on reconnect or restart. To force the popup to re-appear (e.g. after a kernel update), edit `app_settings.json` and remove the relevant entry, then restart the GUI.

---

Previous: [Profiles and Curves Reference](profiles-and-curves.md) | Next: [Driver Setup](driver-setup.md) | Back to [Table of Contents](README.md)
