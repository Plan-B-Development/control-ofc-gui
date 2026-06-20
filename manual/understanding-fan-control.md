# Understanding Motherboard Fan Control (hwmon)

This page explains, in plain English, *how* Linux controls motherboard fans and *why* the rest of this manual keeps mentioning things like hwmon, Super I/O chips, drivers, and BIOS settings. It is background reading — you do **not** need it to get fans working. The [Setup Checklist](setup-checklist.md) and the GUI's **Hardware Readiness** report do the actual work and tell you what your specific board needs. Read this when you want to understand *why* a step is being asked of you, or when something misbehaves and you want the mental model to troubleshoot it.

None of this applies to an [OpenFan Controller](openfan-controller.md) (a USB device with its own page) or to AMD GPU fans — it is specifically about the fan headers on your motherboard.

## The one-paragraph version

Your motherboard's fan headers are driven by a small dedicated chip (the **Super I/O** chip, or sometimes the board's embedded controller). Linux talks to that chip through a kernel driver, which exposes each fan as a handful of plain-text files under a system called **hwmon**. Control-OFC's daemon reads and writes those files for you. Most boards work out of the box; some newer ones need an extra driver loaded first, and most boards need one BIOS setting changed so the firmware stops fighting Linux for control. That is the whole story — the sections below just expand each part.

## What is hwmon?

**hwmon** ("hardware monitoring") is the part of the Linux kernel that exposes sensors and fan controls — temperatures, fan speeds, voltages, and PWM outputs — in one uniform place. Every chip that can report a temperature or drive a fan shows up under hwmon once the kernel has a driver for it.

**What this means:** hwmon is Linux's standard "fan and sensor panel". Control-OFC does not invent its own way to reach your fans; it uses the same hwmon interface that `lm_sensors`, CoolerControl, fancontrol, and every other Linux fan tool use.

## What is sysfs (in this context)?

**sysfs** is a virtual filesystem (mounted at `/sys`) where the kernel presents hardware as files and folders. These are not files on your disk — reading or writing one of them is really talking to the kernel and the hardware behind it.

hwmon lives inside sysfs, under `/sys/class/hwmon/`. So when this manual says "the daemon writes a PWM value", it literally means the daemon writes a number into a file like `/sys/class/hwmon/hwmon3/pwm1`, and the kernel passes that on to the fan chip.

**What this means:** "fan control" on Linux is mostly reading and writing small text files that the kernel maps onto real hardware. That is why permissions, drivers, and *who is writing the file* all matter so much.

## The fan-control files

For each controllable fan header, the driver exposes a small set of files. You rarely touch these directly — Control-OFC does — but recognising them makes every other page clearer:

| File | What it is | What this means |
|---|---|---|
| `pwmN` | The speed command for fan *N*, a number from `0` (stopped) to `255` (full speed) | This is the dial Control-OFC turns. The GUI shows it as a percentage |
| `pwmN_enable` | The *mode* of the header: typically `2`/`5` = automatic (firmware decides), `1` = manual (software decides) | A header must be in manual mode before a `pwmN` write does anything. This is the file the BIOS most often steals back |
| `fanN_input` | The measured speed of the fan, in RPM | Read-only. This is the real, hardware-measured RPM the dashboard shows — never a value software set |
| `tempN_input` | A measured temperature, in thousandths of a degree Celsius (`45000` = 45 °C) | Read-only. Fan curves use these as their input |

**What this means:** a *writable* fan header is one where the driver lets the daemon set `pwmN` **and** hold `pwmN_enable` in manual mode. A header that exposes `fanN_input` (so you can see its RPM) but has no writable `pwmN` is **read-only** — you can watch it but not control it.

## "AMD board" or "Intel board" is the wrong question

A common assumption is that fan control depends on whether you have an AMD or Intel CPU. It does not. The CPU vendor owns *temperature* sensors (`k10temp` for AMD, `coretemp` for Intel), but your **fans** are driven by a separate **Super I/O** chip soldered to the motherboard — made by ITE or Nuvoton (NCT), whichever CPU is in the socket. On some boards part of this job is handled by the board's **embedded controller (EC)** instead.

**What this means:** which driver you need — and whether your fans are controllable at all — depends on your **motherboard** and its Super I/O chip, not your CPU brand. A Gigabyte AMD board and a Gigabyte Intel board can need the very same `it87` driver.

The vendor-by-vendor specifics live in their own reference pages, so this page does not repeat them:

- [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) — Gigabyte, ASUS, MSI, ASRock notes
- [Intel Motherboard Fan Control Guide](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) — Intel LGA1700 / 1851 boards
- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — the full chip → driver matrix

## Why some boards are read-only until a driver is loaded

Super I/O chips sit on an old-style bus with no automatic "plug-and-play" announcement, so the kernel cannot always load the right driver by itself. If no driver is bound to your chip, hwmon simply does not show your motherboard fans — or shows their RPM but no writable `pwmN`.

Mainline Linux already includes drivers for many chips (for example `nct6775` covers a lot of ASUS boards). Newer boards — most 2022-and-later Gigabyte and MSI, some ASRock — use chips whose driver is **out-of-tree**: not shipped with the kernel, installed separately from the AUR and rebuilt for your kernel by **DKMS**.

**What this means:** "my motherboard fans don't show up" usually means "the driver for my Super I/O chip isn't loaded yet", not "my board is unsupported". The GUI's **Hardware Readiness** report identifies your chip and names the exact package to install.

> ### Before you change anything
>
> Some setup steps below can change how your system loads drivers, controls fans, or boots. Follow them only if you understand the change and accept the risk. This guidance is provided **as-is**; the project accepts **no liability** for changes made to your system (MIT License). The detailed, copy-paste version of each step — with a full rollback — lives on the [Driver Setup](driver-setup.md) page.

## Why the BIOS can fight Linux (Smart Fan / Q-Fan)

Motherboard firmware has its own fan controller — Gigabyte calls it Smart Fan, ASUS calls it Q-Fan, others have similar names. When it is enabled, the firmware periodically rewrites the fan registers, flipping `pwmN_enable` back from manual to automatic. The result is the classic symptom: *Linux says the fan is at 50%, but it keeps running at full speed.*

The fix is a one-time BIOS change — set the affected headers to manual or full-speed so the firmware stops reclaiming them. The exact setting per vendor is in the [Driver Setup BIOS step](driver-setup.md#step-5--bios-settings-the-half-people-skip) and the vendor guides above.

**What this means:** a correctly-installed driver can still "lose" to the BIOS until you change one firmware setting. The GUI's **Test PWM Control** detects exactly this — it reports when the BIOS reverted control during the test.

## Why only one tool should control a header at a time

`pwmN` and `pwmN_enable` are single values, and the **last writer wins**. If two programs both drive the same header — say Control-OFC and a leftover `fancontrol` service — they overwrite each other every second and the fan oscillates.

**What this means:** pick one fan controller per header. Before letting Control-OFC manage your fans, stop other fan software (fancontrol, CoolerControl, fan2go, and GPU tools like LACT / CoreCtrl for GPU fans). The [Setup Checklist](setup-checklist.md#step-5--stop-competing-fan-control-software) lists the usual suspects and how to disable them.

## Why DKMS, Secure Boot, kernel headers, and boot parameters can matter

These come up only on some systems, and the [Driver Setup](driver-setup.md) page walks through each with commands. In brief:

- **DKMS** rebuilds an out-of-tree driver every time your kernel updates, so the driver keeps working after upgrades.
- **Kernel headers** matching your *running* kernel are what DKMS builds against. A mismatch is the most common reason a driver "installs" but never loads.
- **Secure Boot**, when enabled, refuses to load unsigned out-of-tree modules — the driver builds but is rejected at load time. You either disable Secure Boot or sign the module.
- **Boot parameters** (the kernel command line) are needed in one specific case: AMD **GPU** fan control on RDNA3+ cards needs `amdgpu.ppfeaturemask=0xffffffff`. Motherboard fan control needs no boot parameter.

**What this means:** none of these are required for most boards. When one *is* required, the Hardware Readiness report says so, and Driver Setup has the exact steps — including how to undo them.

## How Control-OFC removes the guesswork

You do not have to discover any of the above by hand. By design, the **control-ofc-daemon** owns all hardware access — it reads your hwmon chips, evaluates fan curves, and is the **only** component that writes PWM. The **control-ofc-gui** is a client: it shows you what the daemon sees and lets you author profiles and run tests. The GUI never writes to a fan directly.

Three GUI features turn the concepts above into concrete answers, all under **Diagnostics → Troubleshooting**:

- **Hardware Readiness** — names your board and Super I/O chip, says which driver each chip needs and whether it is loaded, counts writable headers, and flags BIOS interference and ACPI conflicts. Start here.
- **Test PWM Control** — writes a known value to a header you choose, waits a few seconds, and reports what actually happened: control works, the BIOS reverted it, or the value was ignored. This is how you confirm a header is genuinely controllable instead of guessing.
- **Rescan / Refresh Hardware Diagnostics** — re-checks the hardware after you load a driver or change a BIOS setting.

[Hardware Troubleshooting](hardware-troubleshooting.md) explains how to read each part of these reports.

## Where to go next

- [Setup Checklist](setup-checklist.md) — the ordered path that puts all of this into practice
- [Driver Setup](driver-setup.md) — copy-paste driver install, Secure Boot, BIOS, and rollback
- [Hardware Troubleshooting](hardware-troubleshooting.md) — reading the Hardware Readiness report and Test PWM Control results
- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — full chip and driver matrix
- [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) — what each temperature sensor means
- [AMD](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) / [Intel](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) fan-control guides — vendor-by-vendor depth

---

Previous: [Driver Setup](driver-setup.md) | Next: [OpenFan Controller](openfan-controller.md) | Back to [Table of Contents](README.md)
