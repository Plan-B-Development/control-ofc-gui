# Getting Started

## What You Need

Control-OFC requires:

- **Linux** with Python 3.12 or newer
- **control-ofc-daemon** running as a systemd service (provides the hardware interface)
- A supported fan controller (OpenFan Controller, motherboard hwmon headers, or AMD GPU)

The GUI never accesses hardware directly. All reads and writes go through the daemon's API over a local Unix socket.

## Installation

### Arch Linux (AUR)

```bash
yay -S control-ofc-gui
```

### From Source

```bash
git clone https://github.com/your-org/control-ofc-gui.git
cd control-ofc-gui
pip install -e ".[dev]"
```

## First Launch

```bash
control-ofc-gui
```

On first launch, Control-OFC will:

1. Show a branded **splash screen** (can be disabled in Settings)
2. Attempt to connect to the daemon at `/run/control-ofc/control-ofc.sock`
3. If the daemon is reachable, fetch hardware capabilities and begin polling
4. If the daemon is not reachable, show a "Disconnected" state (or enter demo mode if configured)
5. Open the **Dashboard** page

### Demo Mode

If you want to explore the interface without hardware or a running daemon:

```bash
control-ofc-gui --demo
```

Demo mode generates synthetic sensor temperatures and fan speeds. All features work identically — you can create profiles, edit curves, and test the full UI. A **DEMO** badge appears in the status banner so you always know when synthetic data is being shown.

You can also enable "Start in demo mode when daemon is unavailable" in Settings so the GUI falls back to demo automatically.

## The Status Banner

The horizontal banner at the top of every page shows:

| Element | Meaning |
|---------|---------|
| **Connection indicator** | Green "Connected", yellow "Degraded", or red "Disconnected" |
| **Profile name** | The currently active fan profile, or "No profile" |
| **Mode** | "Automatic" (curve-driven), "Manual Override", "Read-only", or "Demo mode" |
| **Warning count** | Number of active warnings (click to view details) |
| **DEMO badge** | Visible only in demo mode |

## Navigation

The left sidebar provides access to all four pages:

| Page | Purpose |
|------|---------|
| **Dashboard** | At-a-glance monitoring: temperatures, fan speeds, charts |
| **Controls** | Profile management, fan grouping, curve editing |
| **Settings** | Application preferences, themes, backup/restore |
| **Diagnostics** | Daemon health, sensor freshness, lease status, logs |

An **About** button at the bottom of the sidebar shows version and credit information.

![Splash Screen](../screenshots/auto/16_splash_screen.png)

---

Next: [Dashboard](dashboard.md)
