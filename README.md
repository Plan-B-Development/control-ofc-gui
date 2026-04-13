# Control-OFC GUI

# Control-OFC User Manual

**Control-OFC** is a desktop fan control application for Linux. It communicates with the `control-ofc-daemon` service to monitor temperatures, manage fan speeds, and apply custom fan curves — all from a graphical interface.

This manual covers every page, setting, and feature of the application.

## Table of Contents

1. [Getting Started](/manual/getting-started.md) — Installation, first launch, and connecting to the daemon
2. [Dashboard](/manual/dashboard.md) — Real-time overview of fans, sensors, and system health
3. [Controls](/manual/controls.md) — Profiles, fan roles, curves, and manual override
4. [Settings](/manual/settings.md) — Application preferences, themes, and backup/restore
5. [Diagnostics](/manual/diagnostics.md) — Daemon health, device status, lease info, and logs
6. [Fan Wizard](/manual/fan-wizard.md) — Guided fan identification and labelling
7. [Profiles and Curves Reference](/manual/profiles-and-curves.md) — How profiles, fan roles, and curves work together

## Screenshots

All screenshots in this manual are captured automatically from the application running in demo mode. See the [screenshots](/screenshots/) directory for images of the application running live. 

## Features

- **Dashboard** — real-time sensor temperatures, fan RPM, active profile, system health
- **Controls** — profile switching, curve editing (5-point), fan roles, manual override
- **Settings** — GUI preferences, daemon runtime config, theme editor, import/export
- **Diagnostics** — connection health, subsystem status, lease state, support bundle export
- **Demo mode** — full UI without hardware, for testing and development

## Requirements

- Python >= 3.12 (developed on 3.14)
- Linux (primary target: CachyOS / Arch Linux, KDE Plasma)
- PySide6 >= 6.6
- A running `control-ofc-daemon` instance (or use `--demo` mode)

### System dependencies (Arch-based)

```bash
# PySide6 wheels usually bundle Qt, but if building from source:
sudo pacman -S python python-pip base-devel
```

## Installation

```bash
# Clone the repository
git clone <repo-url> && cd control-ofc-gui

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install
pip install -e .
```

## Usage

```bash
# Normal mode (connects to daemon at /run/control-ofc/control-ofc.sock)
control-ofc

# Demo mode (no daemon required)
control-ofc --demo
```

Or run as a module:
```bash
python -m control_ofc.main
python -m control_ofc.main --demo
```

## Daemon setup

The GUI requires the `control-ofc-daemon` to be running. See the [daemon repository](/home/mitch/Development/control-ofc-daemon) for build and installation instructions, or the [Operations Guide](docs/18_Operations_Guide.md) for configuration reference.

### Quick daemon check

```bash
# Is the daemon running?
systemctl is-active control-ofc-daemon

# Can we reach it?
curl --unix-socket /run/control-ofc/control-ofc.sock http://localhost/status
```

## Configuration

### GUI config

Stored at `~/.config/control-ofc/`:
- `settings.json` — application preferences
- `profiles/` — fan control profiles
- `themes/` — custom themes

### Daemon config

See [Operations Guide](docs/18_Operations_Guide.md) for `daemon.toml` reference.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run quality gates
ruff format --check src/ tests/
ruff check src/ tests/
pytest
```

## Architecture

The GUI is a **daemon API client only**. All hardware interaction goes through the daemon's HTTP-over-Unix-socket API. The GUI owns:

- Fan curve evaluation (control loop at 1Hz)
- Profile/theme/settings persistence
- User-facing presentation

The daemon owns:
- Hardware access (hwmon sysfs, serial, GPU PMFW)
- Thermal safety enforcement
- Sensor polling and history

See [System Architecture](docs/02_System_Architecture_and_Boundaries.md) and the [API Integration Contract](docs/08_API_Integration_Contract.md) for details.

## License

MIT
