# Control-OFC User Manual

**Control-OFC** is a desktop fan control application for Linux. It communicates with the `control-ofc-daemon` service to monitor temperatures, manage fan speeds, and apply custom fan curves — all from a graphical interface.

It controls fans on an OpenFan Controller, on motherboard hwmon headers, and on AMD discrete GPUs, and it additionally **monitors** AMD and Intel discrete GPU temperatures and fan RPM (Intel Arc fans are firmware-managed and read-only).

This manual covers every page, setting, and feature of the application.

> All guidance in this manual is informational and provided **as-is, without warranty of any kind**. Changes you make to your system are **at your own risk**, and the project and its contributors accept no liability for them (MIT License — see the LICENSE file shipped with the package).

## Table of Contents

1. [Getting Started](getting-started.md) — Installation, first launch, and connecting to the daemon
2. [Setup Checklist](setup-checklist.md) — Ordered path from fresh install to verified fan control: sensors → readiness → drivers/BIOS → verify → first profile
3. [Dashboard](dashboard.md) — Real-time overview of fans, sensors, and system health
4. [Controls](controls.md) — Profiles, fan roles, curves, and manual override
5. [Settings](settings.md) — Application preferences, themes, and backup/restore
6. [Diagnostics](diagnostics.md) — Daemon health, device status, and logs
7. [Fan Wizard](fan-wizard.md) — Guided fan identification and labelling
8. [Profiles and Curves Reference](profiles-and-curves.md) — How profiles, fan roles, and curves work together
9. [Hardware Troubleshooting](hardware-troubleshooting.md) — Hardware Readiness, missing sensors, vendor quirks, Test PWM Control, and why some fans appear read-only
10. [Driver Setup](driver-setup.md) — Beginner walkthrough for the out-of-tree motherboard fan drivers (DKMS), Secure Boot, the AMD GPU kernel parameter, verification, and rollback
11. [Understanding Motherboard Fan Control (hwmon)](understanding-fan-control.md) — Plain-English primer: what hwmon, sysfs, Super I/O, and PWM are, and why drivers and BIOS settings matter
12. [OpenFan Controller](openfan-controller.md) — The OpenFan USB fan controller: detection, serial access, stable device paths, channels, fan identification, profiles, and troubleshooting

## Reference Docs

For deeper hardware and sensor topics, see:

- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — chip support matrix, kernel driver requirements, ACPI conflicts
- [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) — vendor-by-vendor BIOS notes (Gigabyte, ASUS, MSI, ASRock)
- [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) — what each sensor name means and which to trust
- [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md) — Tctl vs Tdie, edge vs junction, and common AMD-specific traps

## Screenshots

All screenshots in this manual are captured automatically from the application running in demo mode, so they always show a reproducible setup. The `screenshots/auto/` directory holds the canonical set referenced from these pages.

The screenshots are not included in the packaged copy under `/usr/share/doc/control-ofc-gui/` — view this manual [on GitHub](https://github.com/Plan-B-Development/control-ofc-gui/tree/main/manual) for the rendered images.
