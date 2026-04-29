# Hardware Troubleshooting

This page covers the **Hardware Readiness** card on the Diagnostics → Fans tab and the situations it helps diagnose: chip detection, kernel driver state, BIOS interference, ACPI conflicts, vendor quirks, and verifying that fan headers actually respond to PWM writes.

> **Quick navigation**
> - The Hardware Readiness card lives at **Diagnostics → Fans**, top pane.
> - Click **Refresh Hardware Diagnostics** at the bottom of the card to fetch current state from the daemon.
> - Click **Test PWM Control** to run a 3-second write test against a selected motherboard header.

For the chip and driver matrix, see [Hardware Compatibility](../docs/19_Hardware_Compatibility.md). For vendor-by-vendor BIOS notes, see the [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md). For sensor interpretation, see the [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) and the [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md).

## What the Hardware Readiness card shows

When you fetch hardware diagnostics, the card populates with:

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

**Test PWM Control** writes a known-distinct PWM value to a chosen header, waits ~3 seconds, then reads back what actually happened. The result is one of:

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

While the test is running, the GUI's 1 Hz control loop pauses writes to the header under test so its own ticks do not interfere with the daemon's verify wait. A 5-second safety timer guarantees writes resume even if the test hangs.

## Per-header pwm_enable reclaim count

Some boards (most commonly Gigabyte AM5 with Smart Fan 6) repeatedly reset `pwm_enable` from manual back to automatic. Each reset is a "reclaim" — the daemon sets it back, but the EC keeps stealing it.

The Hardware Readiness card surfaces a per-header count with a severity ramp:

| Reclaim count | Colour | Meaning |
|---------------|--------|---------|
| **0** | Green (OK) | Header is being controlled cleanly — no contention |
| **1–9** | Amber (WARN) | Occasional reclaim; control still working but the EC is fighting back |
| **≥10** | Red (HIGH) | Persistent contention; expect fan speed to drift even though the GUI keeps writing |

The card headline takes the highest severity across all headers, so if any single header is in HIGH state the whole card alerts you to it.

The daemon includes a watchdog that re-asserts `pwm_enable=1` automatically — control still works in the WARN/HIGH cases, but BIOS Smart Fan 6 should be set to "Manual" for the affected headers (see the vendor guidance the card auto-shows for Gigabyte + IT8696E systems).

## Vendor quirks

When the daemon reports a board vendor and chip combination that matches a known workaround pattern, the Hardware Readiness card automatically renders the relevant guidance — BIOS settings to change, kernel modules to install, or known-issue notes. Currently surfaced quirks include:

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
- IT87952E secondary chip — 3 best-guess labels suffixed `(unverified)` until silkscreen tracing on a physical board confirms them

If you see an `(unverified)` suffix on a header label, treat the assignment as a hint, not a fact. The Fan Wizard is the safe way to confirm — it stops one fan at a time so you can see exactly which physical fan corresponds to which header.

## Common situations

### "All my hwmon headers show as read-only"

Open Diagnostics → Fans, look at the **Hardware Readiness** card:

- If the chips table shows the expected chip but **status is "not loaded"**, the kernel module is missing. The chip column lists which module to install (e.g., `it87-dkms-git` on AUR for Gigabyte AM5 boards).
- If the status is "loaded" but **writable_headers is 0**, run **Test PWM Control** on a header. A `pwm_enable_reverted` result means the BIOS is overriding control — fix it in BIOS Smart Fan settings.
- If `acpi_enforce_resources=lax` is required (common with `it87`), the ACPI conflicts row will tell you so.

### "Fans run at full speed regardless of profile"

The thermal safety logic forces 100% PWM if no CPU sensor is found for 5 cycles, or if any CPU sensor reports ≥105°C. Check the **Thermal safety** row of the Hardware Readiness card. If it reports "no CPU sensor", install / load the matching driver (`k10temp` for AMD, `coretemp` for Intel are mainline; some boards also need `nct6775` or `it87`).

### "GPU fan control says feature_unavailable"

Open Diagnostics → Fans, look at the **GPU diagnostics** row. If `amdgpu.ppfeaturemask` is missing bit `0x4000`, the kernel will not expose PMFW fan curves on RDNA3+ GPUs (RX 7000 / 9000 series). Add `amdgpu.ppfeaturemask=0xfff7ffff` to your kernel command line and reboot. See the [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) doc for the full kernel-parameter explanation.

---

Previous: [Profiles and Curves Reference](profiles-and-curves.md) | Back to [Table of Contents](README.md)
