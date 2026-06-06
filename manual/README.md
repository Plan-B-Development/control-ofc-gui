# Control-OFC User Manual

**Control-OFC** is a desktop fan control application for Linux. It communicates with the `control-ofc-daemon` service to monitor temperatures, manage fan speeds, and apply custom fan curves — all from a graphical interface.

It controls fans on an OpenFan Controller, on motherboard hwmon headers, and on AMD discrete GPUs, and it additionally **monitors** AMD and Intel discrete GPU temperatures and fan RPM (Intel Arc fans are firmware-managed and read-only).

This manual covers every page, setting, and feature of the application.

## Table of Contents

1. [Getting Started](getting-started.md) — Installation, first launch, and connecting to the daemon
2. [Dashboard](dashboard.md) — Real-time overview of fans, sensors, and system health
3. [Controls](controls.md) — Profiles, fan roles, curves, and manual override
4. [Settings](settings.md) — Application preferences, themes, and backup/restore
5. [Diagnostics](diagnostics.md) — Daemon health, device status, lease info, and logs
6. [Fan Wizard](fan-wizard.md) — Guided fan identification and labelling
7. [Profiles and Curves Reference](profiles-and-curves.md) — How profiles, fan roles, and curves work together
8. [Hardware Troubleshooting](hardware-troubleshooting.md) — Hardware Readiness, vendor quirks, Test PWM Control, and why some fans appear read-only
9. [Driver Setup](driver-setup.md) — Beginner walkthrough for installing the out-of-tree motherboard fan drivers (DKMS), verifying them, and rolling back

## Reference Docs

For deeper hardware and sensor topics, see:

- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — chip support matrix, kernel driver requirements, ACPI conflicts
- [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) — vendor-by-vendor BIOS notes (Gigabyte, ASUS, MSI, ASRock)
- [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) — what each sensor name means and which to trust
- [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md) — Tctl vs Tdie, edge vs junction, and common AMD-specific traps

## Screenshots

All screenshots in this manual are captured automatically from the application running in demo mode, so they always show a reproducible setup. The `screenshots/auto/` directory holds the canonical set referenced from these pages.
