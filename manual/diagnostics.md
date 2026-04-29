# Diagnostics

The Diagnostics page exposes the health and status of every subsystem. It is your primary tool for troubleshooting connection issues, stale sensors, lease conflicts, and hardware detection problems.

> **Looking for help with a specific motherboard or fan controller?** Start with the [Hardware Troubleshooting](hardware-troubleshooting.md) page — it covers the Hardware Readiness card, Test PWM Control, vendor quirks, and what to do when fans report 0 RPM or refuse to change speed.

![Diagnostics — Overview Tab](../screenshots/auto/04_diagnostics_overview.png)

## Overview Tab

Two information cards:

### Daemon Health

| Field | Meaning |
|-------|---------|
| **Daemon version** | Version of the running daemon and its API |
| **Status** | Overall health: "healthy", "warning", or "critical" |
| **Uptime** | How long the daemon has been running since last restart |
| **Subsystems** | List of subsystems (openfan, hwmon_sensors, hwmon_pwm) with their status and age |

**Age** is the time in milliseconds since the daemon last polled that hardware subsystem. A low age (under 1000ms) means the data is fresh. A high age means the daemon is having trouble reaching that hardware.

### Device Discovery

| Field | Meaning |
|-------|---------|
| **OpenFan** | Whether an OpenFan Controller is detected, channel count, and write/RPM capability |
| **hwmon** | Whether motherboard fan headers are detected, header count, and whether writes require a lease |
| **AMD GPU** | Whether a discrete GPU is detected, its model, PCI address, and fan control method |
| **Features** | Summary of write capabilities (OpenFan writes, hwmon writes) |

## Sensors Tab

![Diagnostics — Sensors Tab](../screenshots/auto/07_diagnostics_sensors.png)

A table of every temperature sensor reported by the daemon:

| Column | Meaning |
|--------|---------|
| **Label** | Human-readable sensor name (e.g., "CPU Tctl", "GPU Edge") |
| **Kind** | Sensor category: CpuTemp, GpuTemp, MbTemp, DiskTemp, etc. |
| **Chip** | Driver/chip name reporting this sensor (e.g., `k10temp`, `nct6798`, `amdgpu`) |
| **Confidence** | How certain the GUI is about how to interpret this sensor: `high`, `medium`, `low`, or `unknown`. Lower confidence usually means the sensor's chip has known quirks (e.g., the ASUS NCT6776F `CPUTIN` reading is a board temperature, not the CPU) |
| **Value** | Current temperature reading in degrees Celsius |
| **Age (ms)** | Time since the daemon last read this sensor |
| **Freshness** | "fresh" (under 2s), "stale" (2-10s), or "invalid" (over 10s) |

Stale sensors appear in yellow. Invalid sensors appear in red. This helps identify hardware that has stopped responding.

Hover any row to see a tooltip explaining the chip's source class, description, and any known driver quirks. For deeper sensor interpretation, see the [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) and the [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md).

## Fans Tab

![Diagnostics — Fans Tab](../screenshots/auto/08_diagnostics_fans.png)

The Fans tab is split vertically:

- **Top pane: Hardware Readiness** — chip detection, driver status, kernel modules, ACPI conflicts, vendor quirk guidance, and a **Test PWM Control** button for verifying that motherboard headers actually move fans. Covered in detail on the [Hardware Troubleshooting](hardware-troubleshooting.md) page.
- **Bottom pane: Fans table** — every controllable fan output reported by the daemon.

The fan table has the following columns:

| Column | Meaning |
|--------|---------|
| **ID** | Display name (user alias if set, otherwise hardware label or fan ID) |
| **Source** | Connection type: openfan, hwmon, or amd_gpu |
| **Control method** | How this fan can be controlled: `openfan`, `hwmon` (with PWM-only or full read/write), `amd_gpu` (PMFW or legacy pwm1), `read_only`, or `unknown`. Read-only entries cannot be commanded — Test PWM Control in the Hardware Readiness pane explains why |
| **RPM** | Hardware-measured speed (dash if not available). Writable hwmon headers reading 0 RPM are annotated `(no fan detected)` so you don't accidentally assign a curve to an empty header |
| **PWM (%)** | Last commanded speed percentage (dash if not set) |
| **Freshness** | Data freshness indicator, same as the sensors table |

Hover any cell for a tooltip explaining what the value means and, for read-only fans, why the GUI cannot drive them.

## Lease Tab

![Diagnostics — Lease Tab](../screenshots/auto/09_diagnostics_lease.png)

### What is a Lease?

A lease grants exclusive write access to your motherboard's fan headers (hwmon). Only one client can hold the lease at a time, preventing conflicting speed commands from different tools.

The GUI automatically acquires and renews the lease while controlling fans. The lease expires after 60 seconds if not renewed (e.g., if the GUI crashes), allowing other tools to take over.

OpenFan Controller and GPU fan writes do **not** require a lease — only motherboard hwmon writes do.

### Lease Status

| Field | Meaning |
|-------|---------|
| **Lease** | "Held" or "Not held" |
| **Lease ID** | Unique identifier of the current lease |
| **Owner** | Which client holds the lease (e.g., "gui") |
| **TTL remaining** | Seconds until the lease expires if not renewed |
| **Required** | Whether any detected hardware requires a lease for writes |

If another tool (or another instance of Control-OFC) holds the lease, the GUI cannot write PWM values until the lease is released or expires.

## Event Log Tab

![Diagnostics — Event Log Tab](../screenshots/auto/10_diagnostics_event_log.png)

A timestamped log of events, warnings, and errors. The log retains up to 2000 entries.

### Detail Buttons

The top row provides on-demand detail fetches:

| Button | What it Fetches |
|--------|----------------|
| **Daemon Status** | Current daemon health snapshot formatted as text |
| **Controller Status** | OpenFan controller detection and capability details |
| **GPU Status** | AMD GPU detection, fan capabilities, and current fan state |
| **System Journal** | Recent entries from the `control-ofc-daemon.service` systemd journal |

### Log Controls

| Button | Action |
|--------|--------|
| **Refresh Log** | Reload the event list |
| **Clear Log** | Remove all entries from the display |
| **Clear Warnings** | Reset the warning counter shown in the status banner |
| **Copy Last Errors** | Copy recent errors and warnings to the clipboard for sharing |

## Export Support Bundle

The **Export Support Bundle** button (below all tabs) creates a JSON file containing:

- System configuration
- Daemon status and version
- Sensor and fan states
- Event log entries
- Active profile information

This file is useful for reporting issues. Review it before sharing — it may contain system-specific details.

---

Previous: [Settings](/manual/settings.md) | Next: [Fan Wizard](/manual/fan-wizard.md)
