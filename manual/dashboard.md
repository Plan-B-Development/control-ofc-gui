# Dashboard

The Dashboard is the landing page. It answers the most important questions at a glance:

- What profile is active?
- What are the fans doing?
- What are the sensors reading?
- Is the system healthy?

![Dashboard](../screenshots/auto/01_dashboard.png)

## Summary Cards

The top row shows summary cards for key readings:

| Card | Shows |
|------|-------|
| **CPU Temp** | Hottest CPU sensor value |
| **GPU Temp** | Primary GPU temperature. The card title shows the detected GPU's model when known — e.g. "RX 7900 XTX Temp" (AMD) or "Arc B580 Temp" (Intel) |
| **Motherboard** | Motherboard chipset temperature |
| **Fans** | Number of detected controllable fans |
| **Warnings** | Count of active warnings |

You can **click any temperature card** to change which sensor it displays. A picker dialog lets you choose from all available sensors of that type.

> The dashboard shows a **warning banner** if the connected daemon's API version differs from the version this GUI was built against — a sign the `control-ofc-daemon` and `control-ofc-gui` packages were upgraded out of lockstep. Align the two package versions; some features may otherwise misbehave.

## Telemetry Chart

The timeline chart is **dual-axis**: temperatures plot against the left axis (°C) and fan RPM against the right axis, so you can watch a temperature rise and the fans respond on one graph. The **Range** dropdown selects the time window:

| Range | Use Case |
|-------|----------|
| 30s, 2m | Watching real-time response to load changes |
| 5m, 10m, 15m, 20m, 30m | Observing curve behaviour during a gaming session |
| 1h, 2h | Reviewing longer-term patterns |

The default time range (15m) is configurable in Settings.

Each visible series carries a coloured **latest-value marker** — a dot at its most recent reading on the right edge of the chart — so you can read the current value at a glance. **Hovering** the chart shows a crosshair and a themed tooltip plate listing every series value at that moment in time. The temperature and RPM lines are antialiased for a smooth look. The tooltip-plate and crosshair colours are themeable in Settings → Theme Editor → Charts.

### Series Visibility

The panel on the right is a grouped, searchable tree of every **sensor and fan** — grouped into CPU, GPU, Motherboard, Disk, and Fans (by source: D-GPU, hwmon, OpenFan). You can:

- **Type in the "Search sensors…" box** to filter the list
- **Click a row's checkbox** to show/hide its line on the chart
- **Toggle a whole group** to declutter the view (e.g., hide all hwmon fans)

Hidden series persist across sessions — your visibility preferences are saved automatically.

## Fan Status Table

Below the chart, a table lists every detected fan with:

| Column | Meaning |
|--------|---------|
| **Name** | User-assigned alias (from Fan Wizard) or hardware ID |
| **Source** | Where the fan is connected: OpenFan, hwmon, or AMD GPU |
| **RPM** | Hardware-measured rotational speed |
| **PWM** | Last commanded speed percentage |

## Profile Selector

The dropdown in the top-right corner lists all available profiles. Selecting a profile here activates it immediately — the daemon begins evaluating curves and writing fan speeds.

## Disconnected / No Hardware States

If the daemon is not reachable, the Dashboard shows a disconnected overlay with a reconnection message. If the daemon is connected but no controllable hardware is detected, it shows a "No hardware" message with a link to Diagnostics.

---

Previous: [Getting Started](/manual/getting-started.md) | Next: [Controls](/manual/controls.md)
