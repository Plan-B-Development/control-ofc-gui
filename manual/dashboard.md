# Dashboard

The Dashboard is the landing page. It answers the most important questions at a glance:

- What profile is active, and what mode is the system in?
- What are the fans doing?
- What are the sensors reading?
- Is the system healthy?

![Dashboard](../screenshots/auto/01_dashboard.png)

The page is laid out for progressive disclosure: a status strip and summary cards
give the at-a-glance picture, fan **zone cards** and a telemetry chart show what's
happening now, and every piece of advanced detail (the raw fan table, the full sensor
tree, the event log) is one click away.

## Status Strip

A single header row across the top is the dashboard's command + status surface:

- **Connection** state
- **Active profile**
- **Control mode** — Automatic / Manual Override / Demo / Read-only
- **Thermal state** — the daemon's safety state (Thermal OK / Recovery / Emergency /
  No CPU sensor); click the chip for a read-only thermal-safety detail
- **"Updated Xs ago"** — time since the last successful poll, so you can tell live
  data from a stalled connection at a glance
- A **warning chip** showing the active-warning count — click it to open the
  active-warnings dialog
- A compact **profile selector + Apply** (see [Profile Selector](#profile-selector))
- A **Sensors** toggle that shows or hides the right-hand Sensors panel

> The status strip also surfaces an API-version mismatch — if the connected daemon's
> API version differs from the version this GUI was built against (a sign the
> `control-ofc-daemon` and `control-ofc-gui` packages were upgraded out of lockstep),
> a warning is raised. Align the two package versions; some features may otherwise
> misbehave.

## Summary Cards

The row of summary cards shows the key readings:

| Card | Shows |
|------|-------|
| **CPU Temp** | Hottest CPU sensor value |
| **GPU Temp** | Primary GPU temperature. The card title shows the detected GPU's model when known — e.g. "RX 7900 XTX Temp" (AMD) or "Arc B580 Temp" (Intel) |
| **Motherboard** | Motherboard chipset temperature |
| **Fans** | Online / expected fan count, plus average PWM and RPM |

The three **temperature cards** (CPU / GPU / Motherboard) each carry a **trend glyph**
(rising / falling / flat) and a **session min/max range**, so you can see at a glance
whether something is heating up. You can **click any temperature card** to change which
sensor it displays — a picker dialog lets you choose from all available sensors of that
type. The daemon's thermal state shows on the status strip's **thermal chip** — click it
for a read-only summary of the current thermal state and any active overrides.

## Fan Zone Cards

Fans are shown as **zone-grouped cards** — the primary fan view. Each fan is a tile
inside a zone card, and each tile shows a **state chip**:

| State | Meaning |
|-------|---------|
| **Normal** | Spinning as expected |
| **Low RPM** | Spinning, but slower than expected |
| **Stall** | A PWM is commanded but the fan reads 0 RPM (stalled or unplugged) |
| **Stale** | Telemetry for this fan has gone stale |
| **Offline** | An expected fan (a profile member) is not currently present |
| **Override** | The fan is under a manual override |

Each **zone card** rolls up its fans: online/expected count and average RPM/PWM. Zones
come from your own **fan-zone assignments** (e.g. *Front Intake*, *Exhaust*); any fan
you haven't assigned falls back to grouping by role/source, so the view is useful out
of the box. Click a fan tile to open a **detail dialog** where you can **rename** the
fan or **reassign** it to a different zone.

**Drag a card by its header** to reorder the zones into the arrangement you prefer — the
order is remembered. A small **"Fan zones"** header above the cards lets you **collapse
the whole section** (and show it again), handing the freed space to the chart; the
collapsed state is remembered too.

### Raw fan data

The dense fan table is preserved under a collapsed **"Raw fan data"** expander at the
bottom of the page — advanced detail, one click away. It lists every detected fan with:

| Column | Meaning |
|--------|---------|
| **Label** | User-assigned alias or hardware label/ID — double-click a row to rename |
| **Source** | Where the fan is connected: OpenFan, hwmon, AMD GPU, or Intel GPU |
| **RPM** | Hardware-measured rotational speed |
| **PWM%** | Last commanded speed percentage |

## Telemetry Chart

The timeline chart is **dual-axis**: temperatures plot against the left axis (°C) and
fan RPM against the right axis, so you can watch a temperature rise and the fans
respond on one graph.

To keep it readable, the chart **does not show every series at once**. On first run it
shows a curated default — CPU temp, GPU temp, and one case/motherboard temp. From there
you control what's shown:

- **Chart modes** — a selector switches between **Combined** (the curated default),
  **Thermals**, **Fans**, and **Diagnostics**, with a **Reset** to return to defaults.
- **Series selection** — toggle individual sensors and fans on or off from the
  **Sensors** panel's checkboxes (see [Sensors panel](#sensors-panel)).
- **Event annotations** — vertical markers flag transitions the GUI detects between
  polls: a profile change, a reconnect, a thermal transition, an override starting or
  ending, and the onset of a stale sensor or a stalled fan.

The **Range** dropdown selects the time window:

| Range | Use Case |
|-------|----------|
| 30s, 2m | Watching real-time response to load changes |
| 5m, 10m, 15m, 20m, 30m | Observing curve behaviour during a gaming session |
| 1h, 2h | Reviewing longer-term patterns |

The default time range (15m) is configurable in Settings. Each visible series carries a
coloured **latest-value marker** on the right edge, and **hovering** the chart shows a
crosshair and a themed tooltip listing every series value at that moment. The
tooltip-plate and crosshair colours are themeable in Settings → Theme Editor → Charts.

## Sensors panel

The right-hand **Sensors panel** is a toggle-button side panel (toggle it from the
status strip). It opens by default on wide windows and collapses on narrow ones so the
chart keeps room. It is a grouped, searchable tree of every **sensor and fan**, grouped
into CPU, GPU, **AIO / Liquid** (liquid-cooler coolant temperatures), Motherboard, Disk,
and Fans (by source: D-GPU, hwmon, OpenFan). Liquid-cooler pump and radiator fans are
tagged **(AIO)**. Type in the "Search sensors…" box to filter; click a row's checkbox to
show/hide its line on the chart; toggle a whole group to declutter. Hidden series persist
across sessions.

> The active **event log** lives on the [Diagnostics](diagnostics.md) page, and the
> active **warnings** open in a dialog from the status strip's warning chip.

## Profile Selector

The profile selector in the status strip lists all available profiles. Pick one and
click **Apply** to activate it. Activating hands the profile to the daemon, whose
profile engine then evaluates its curves every second and drives the fans — so your
fans stay controlled whether the GUI is open or closed. See
[The Daemon Drives the Fans](profiles-and-curves.md#the-daemon-drives-the-fans).

## Thermal Safety States

If the daemon engages its thermal failsafe (a CPU sensor at ≥ 105°C, or no CPU sensor
found), the daemon forces OpenFan and writable hwmon fans itself and holds them until
it reports normal again. The dashboard reflects this in the status strip's **thermal
state** chip (click it for the detail), and raises a warning (visible in the warning
chip and its dialog). See
["Fans run at full speed regardless of profile"](hardware-troubleshooting.md#fans-run-at-full-speed-regardless-of-profile)
for the full behaviour.

## Disconnected / No Hardware States

If the daemon is not reachable, the Dashboard shows a disconnected overlay with a
reconnection message. If the daemon is connected but no controllable hardware is
detected, it shows a "No hardware" message with a link to Diagnostics.

---

Previous: [Setup Checklist](setup-checklist.md) | Next: [Controls](controls.md)
