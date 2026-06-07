# Setup Checklist — Sensors & Fan Control

This page is the **ordered path** from a fresh install to verified, working fan control: install → sensors → readiness → drivers/BIOS → verify → first profile. Do the steps in order — each one links to a deeper page for the detail. Commands target **Arch Linux and CachyOS** (the platforms Control-OFC is packaged for); the concepts carry to other distributions but the commands will differ.

> ## ⚠ Read this first
>
> This checklist is **informational guidance only**. Some linked steps install third-party kernel modules, change firmware (UEFI/BIOS) settings, alter kernel boot parameters, or change fan behaviour — done incorrectly these can make a system unbootable, cause overheating, or damage hardware.
>
> Everything here is provided **as-is, without warranty of any kind**. You proceed **at your own risk**; the Control-OFC project and its contributors **accept no liability** for hardware, firmware, or data damage, boot failures, or any other consequence of following this guide (MIT License — see the LICENSE file shipped with the package). If anything conflicts with your hardware vendor's or distribution's documentation, **prefer theirs**.

## Step 0 — What "working" looks like

You are done when all of these are true:

- The [Dashboard](dashboard.md) shows live CPU (and, if present, GPU / motherboard / drive) temperatures.
- Your fans appear in the dashboard fan table and in [Diagnostics → Fans](diagnostics.md) with RPM readings.
- **Diagnostics → Troubleshooting** reports a non-zero *writable* header count (or an OpenFan / AMD GPU fan with a write path).
- **Test PWM Control** (and **Test GPU Fan Control** if you have an AMD GPU) reports that control is working.
- A profile is active and the fans respond when temperatures change.

## Step 1 — Install the daemon and GUI

```bash
paru -S control-ofc-gui          # pulls control-ofc-daemon as a dependency
sudo systemctl enable --now control-ofc-daemon
```

First-time AUR notes (the paru review pager, installing from source) are in [Getting Started](getting-started.md). The daemon's own prerequisites table lives in the [daemon README](https://github.com/Plan-B-Development/control-ofc-daemon#prerequisites).

## Step 2 — Verify sensors first

Fan control depends on temperatures: the readiness checks, fan curves, and the daemon's thermal safety all need a working CPU sensor before any fan work matters. (Sensors-before-fans is the same order the [Arch Wiki fan-speed-control flow](https://wiki.archlinux.org/title/Fan_speed_control) uses.)

Launch the GUI and check:

- **Temperatures visible** on the Dashboard and in **Diagnostics → Sensors**? Continue to Step 3.
- **Missing or fewer than expected** (no CPU temperature, no motherboard temperatures, no drive temperatures)? → [Sensors missing or fewer than expected](hardware-troubleshooting.md#sensors-missing-or-fewer-than-expected), then come back here.

## Step 3 — Run the Hardware Readiness check

Open **Diagnostics → Troubleshooting** (the report fetches automatically the first time; **Refresh Hardware Diagnostics** re-runs it) and read the **summary line** — chip count, writable header count, and any issues. [What each section of the report means](hardware-troubleshooting.md#what-the-hardware-readiness-report-shows).

## Step 4 — Branch: what your hardware needs

| The readiness report says / you have | Meaning | Go to |
|---|---|---|
| Writable headers > 0, no issues | The mainline driver already works | Step 5 |
| A chips-table row says **"not loaded — install …"** | Your board needs an out-of-tree DKMS driver (most 2022+ Gigabyte / MSI, some ASRock) | [Driver Setup](driver-setup.md) |
| **BIOS interference**, or Test PWM Control says control was reverted | Firmware Smart Fan keeps reclaiming the headers | [Driver Setup — Step 5 (BIOS)](driver-setup.md#step-5--bios-settings-the-half-people-skip); vendor depth: [AMD boards](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) / [Intel boards](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) |
| AMD RDNA3+ dGPU (RX 7000 / 9000) and GPU diagnostics flags `ppfeaturemask` | GPU fan-curve writes need a one-time kernel parameter | [Driver Setup — AMD GPU prerequisite](driver-setup.md#amd-gpu-fan-control-prerequisite-rdna3) |
| OpenFan Controller | **Nothing to do** — the daemon auto-detects it on `/dev/ttyACM*` / `/dev/ttyUSB*`, and the service ships with serial access | Step 5 |
| Intel Arc dGPU | Monitor-only **by design** (firmware-managed fan; the kernel exposes no write interface) | [Why](hardware-troubleshooting.md#intel-arc-gpus-are-monitor-only) |

## Step 5 — Stop competing fan-control software

Only one program should drive a fan header. `pwm` / `pwm_enable` are single sysfs values — the **last writer wins** — so two controllers overwrite each other and the fans oscillate. The readiness report's *BIOS interference* counter cannot tell an embedded controller apart from another program: competing software shows up as the same "reverted / reclaimed" pattern. Other projects document the same conflict — CoolerControl [warns against running LACT and CoolerControl on the same AMD GPU](https://docs.coolercontrol.org/hardware-support.html) ("erratic fan behavior, with each application's settings being overwritten by the other"), and fan2go [logs third-party PWM changes](https://github.com/markusressel/fan2go) as another program "competing with fan2go".

Check for the usual suspects and disable any that are active:

```bash
systemctl list-units --type=service | grep -iE 'fancontrol|coolercontrol|fan2go'

# For each hit, e.g.:
sudo systemctl disable --now fancontrol.service      # lm_sensors' fancontrol
sudo systemctl disable --now coolercontrold.service  # CoolerControl
sudo systemctl disable --now fan2go.service          # fan2go
```

Also worth knowing:

- **LACT / CoreCtrl** (GPU tools): don't let them manage the fan of a GPU that Control-OFC controls — pick one owner per device.
- **`pwmconfig`** (lm_sensors' interactive prober) stops fans while it tests. Fine as a one-off diagnostic, but only run it while `control-ofc-daemon` is stopped.

## Step 6 — Verify control end-to-end

In **Diagnostics → Troubleshooting**:

1. Run **Test PWM Control** on a **non-critical chassis fan** header (not CPU or pump). *"PWM control is working correctly"* is the finish line; every other verdict comes with a tailored next step — see [the result table](hardware-troubleshooting.md#test-pwm-control).
2. If you have an AMD GPU with a write path, run **Test GPU Fan Control** (daemon ≥ 1.11.0) — see [its result table](hardware-troubleshooting.md#test-gpu-fan-control).

## Step 7 — Create your first profile

1. Run the [Fan Wizard](fan-wizard.md) to identify and label each fan — it stops one fan at a time so you can match headers to physical fans.
2. On [Controls](controls.md), create fan roles and assign curves. Concepts: [Profiles and Curves](profiles-and-curves.md).

## When to redo what

| After this event | What can break | What to redo |
|---|---|---|
| **Kernel update** | The DKMS module silently fails to rebuild when the installed headers don't match the new kernel — motherboard fans/sensors vanish | [Driver Setup — Step 2](driver-setup.md#step-2--prerequisites-dkms--kernel-headers) and [Staying current](driver-setup.md#staying-current) |
| **BIOS update or settings reset** | Firmware usually restores Smart Fan defaults — headers turn read-only again | [Driver Setup — Step 5 (BIOS)](driver-setup.md#step-5--bios-settings-the-half-people-skip), then re-run the readiness check |
| **Fan regression with a driver installed months ago** | `-git` AUR drivers only pick up upstream fixes when reinstalled | [Staying current](driver-setup.md#staying-current) |
| **Bootloader change / kernel command-line edit** | `amdgpu.ppfeaturemask` falls off the command line — GPU fan writes stop working | [AMD GPU prerequisite](driver-setup.md#amd-gpu-fan-control-prerequisite-rdna3); check with `cat /proc/cmdline` |

## Where to go next

- Something failed along the way → [Hardware Troubleshooting](hardware-troubleshooting.md)
- Driver depth (DKMS, module parameters, Secure Boot, rollback) → [Driver Setup](driver-setup.md)
- How profiles, fan roles, and curves fit together → [Profiles and Curves](profiles-and-curves.md)

---

Previous: [Getting Started](getting-started.md) | Next: [Dashboard](dashboard.md) | Back to [Table of Contents](README.md)
