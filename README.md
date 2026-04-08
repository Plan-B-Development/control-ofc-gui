# Control-OFC GUI

> PySide6 desktop GUI for the Control-OFC fan control daemon on Linux.

Control-OFC GUI is a dark-theme-first desktop application for monitoring and controlling fans via the [control-ofc-daemon](https://github.com/your-org/control-ofc-daemon) service. It communicates exclusively through the daemon's Unix socket HTTP API — it never accesses hardware directly.

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

## Upgrade notes

### v0.86.0+

Settings page now manages daemon config via API (`POST /config/profile-search-dirs`, `POST /config/startup-delay`) instead of direct file writes. No manual migration needed — settings are preserved.

## Known issues

See [Risks, Gaps, and Future Work](docs/14_Risks_Gaps_and_Future_Work.md) for the full list. Key limitations:

- No runtime hwmon/GPU hotplug detection (restart daemon to pick up new devices)
- AIO cooler support is placeholder only
- Multi-GPU: data model supports it, but API reports primary GPU only
- GPU fan control requires RDNA3+ (RX 7000/9000) with `amdgpu.ppfeaturemask` kernel parameter

## License

MIT
