# Control-OFC GUI

**Latest release:** v1.38.3 — 2026-06-12. Pairs with `control-ofc-daemon` ≥ v1.17.0. See [`CHANGELOG.md`](CHANGELOG.md) for the full history.

Desktop fan control interface for Linux. Communicates with the [`control-ofc-daemon`](https://github.com/Plan-B-Development/control-ofc-daemon) service to monitor temperatures, manage fan speeds, and apply custom fan curves.

![Dashboard](screenshots/auto/01_dashboard.png)

## Features

- **Dashboard** — real-time sensor temperatures, fan RPM, active profile, system health with per-sensor freshness indicators; a dual-axis telemetry chart with latest-value markers and a hover tooltip
- **Controls** — profile switching, graph/linear/flat curve editing, fan roles, manual override; drag-resizable cards that snap to a shared grid (double-click the grip to reset)
- **Multi-source fan control** — OpenFan Controller channels, motherboard hwmon headers (lease-managed), and AMD discrete GPU fans (PMFW `fan_curve` / legacy `pwm1`)
- **GPU monitoring** — AMD and Intel Arc discrete GPU temperatures and fan RPM (Intel Arc fans are firmware-managed and read-only)
- **Settings** — GUI preferences, daemon runtime config, full theme editor with contrast checking, import/export
- **Diagnostics** — connection health, subsystem status, 14-column sensor table, lease state, Test PWM Control / Test GPU Fan Control, Restore GPU Fan to Automatic, hardware rescan, hardware-readiness reporting, support bundle export
- **Fan Wizard** — guided fan identification and labelling
- **Demo mode** — full UI without hardware (`--demo`)

## Install

**AUR (recommended):**

```bash
paru -S control-ofc-gui
```

> **Tip — first-time AUR install UX:** paru pages the `PKGBUILD` and `.install`
> through `less` and asks you to confirm before building. That is paru's default
> security review (press `q` to exit the pager, then `y` to proceed), not
> specific to this package. To install non-interactively, pass `--skipreview`
> to paru (`paru -S --skipreview control-ofc-gui`), or add `SkipReview` to the
> `[options]` section of `~/.config/paru/paru.conf`.

**From source:**

```bash
git clone https://github.com/Plan-B-Development/control-ofc-gui.git
cd control-ofc-gui
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# 1. Ensure daemon is running
systemctl is-active control-ofc-daemon

# 2. Launch the GUI
control-ofc-gui

# 3. Or try demo mode (no daemon required)
control-ofc-gui --demo
```

> **First-time daemon setup?** The daemon has its own prerequisites — kernel
> modules for your motherboard's Super I/O chip, possibly an AUR DKMS driver
> on newer Gigabyte / MSI / ASRock boards (2022+), and a kernel parameter
> for RDNA3+ AMD GPU fan control. Follow the ordered
> [Setup Checklist](manual/setup-checklist.md), see the
> [daemon prerequisites guide](https://github.com/Plan-B-Development/control-ofc-daemon#prerequisites),
> or open **Diagnostics → Troubleshooting** in the GUI after install — the
> Hardware Readiness report identifies your chip and recommends the exact
> AUR package.

## CLI

```
control-ofc-gui [OPTIONS]

Options:
  --socket <path>   Daemon socket path (default: /run/control-ofc/control-ofc.sock)
  --demo            Run in demo mode with simulated hardware
```

## Requirements

System:
- Python >= 3.12 (developed on 3.14)
- Linux (primary target: Arch Linux / CachyOS, KDE Plasma)
- A running `control-ofc-daemon` instance (or use `--demo`)
- `hicolor-icon-theme` (for the application icon to be picked up by launchers)

Python runtime dependencies (resolved automatically by `pip install` or
the AUR package — listed here for transparency):
- `PySide6 >= 6.6` — Qt6 bindings (UI toolkit)
- `httpx >= 0.27` — HTTP client used for the daemon's Unix-socket API
- `pyqtgraph >= 0.14` — chart rendering (timeline + curve editor)
- `numpy >= 1.26` — numerical helpers behind chart maths
- `colorama >= 0.4` — required transitively at `import pyqtgraph` time;
  pyqtgraph imports it unconditionally even on Linux

Development extras (`pip install -e ".[dev]"`):
- `pytest >= 8.0`, `pytest-qt >= 4.3`, `ruff >= 0.4`

## Configuration

| Location | Contents |
|----------|----------|
| `~/.config/control-ofc/app_settings.json` | GUI preferences |
| `~/.config/control-ofc/profiles/` | Fan control profiles |
| `~/.config/control-ofc/themes/` | Custom themes |

Daemon configuration: see the [daemon repo](https://github.com/Plan-B-Development/control-ofc-daemon) and the [Operations Guide](docs/18_Operations_Guide.md).

## Architecture

The GUI is a **daemon API client only**. All hardware access goes through the daemon's HTTP-over-Unix-socket API.

| Owned by GUI | Owned by daemon |
|-------------|----------------|
| Fan curve evaluation (1 Hz control loop) | Hardware access (hwmon, serial, GPU PMFW) |
| Profile/theme/settings persistence | Thermal safety enforcement |
| User-facing presentation | Sensor polling and history |
| Hwmon lease management | Socket permissions and IPC |

See the [architecture docs](docs/02_System_Architecture_and_Boundaries.md) and [API contract](docs/08_API_Integration_Contract.md) for details.

## Documentation

- **[User Manual](manual/README.md)** — installation, features, and usage guide
- **[Setup Checklist](manual/setup-checklist.md)** — ordered path from fresh install to verified sensors and fan control
- **[Hardware Troubleshooting](manual/hardware-troubleshooting.md)** — Hardware Readiness, Test PWM Control, vendor quirks
- **[Hardware Compatibility](docs/19_Hardware_Compatibility.md)** — chip support matrix, kernel drivers, ACPI conflicts
- **[AMD Motherboard Fan Control Guide](docs/21_AMD_Motherboard_Fan_Control_Guide.md)** — vendor-by-vendor BIOS notes (Gigabyte, ASUS, MSI, ASRock)
- **[Intel Motherboard Fan Control Guide](docs/23_Intel_Motherboard_Fan_Control_Guide.md)** — Intel LGA1700/1851 Super-I/O fan control, per-vendor notes
- **[Sensor Interpretation Guide](docs/20_Sensor_Interpretation_Guide.md)** — what each sensor name means and which to trust
- **[AMD Sensor Interpretation Deep Dive](docs/22_AMD_Sensor_Interpretation_Deep_Dive.md)** — Tctl/Tdie, edge/junction, and common AMD-specific traps
- **[Architecture](docs/00_README_START_HERE.md)** — design docs and specs
- **[API Contract](docs/08_API_Integration_Contract.md)** — daemon endpoint reference
- **[Changelog](CHANGELOG.md)** — version history
- **[Contributing](CONTRIBUTING.md)** — build, test, and PR guidelines

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for build instructions, quality gates, and PR guidelines.

## License

MIT
