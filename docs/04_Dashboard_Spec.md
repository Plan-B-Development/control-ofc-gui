# 04 — Dashboard Spec

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
The Dashboard is the default landing page and provides a quick operational overview.

## Primary questions the page answers
- What profile is active?
- Are fans spinning?
- Are temperatures acceptable?
- Is the daemon/API healthy?
- Are there any warnings or stale sensors?
- What has fan speed done over time?

## V1 structure

### Top status strip (DEC-177)
A single always-visible command + status header. While the dashboard is active it is
the **only** status surface — the global status banner is hidden to avoid duplication:
- Connection state
- Active profile
- Control mode: Automatic / Manual Override / Demo / Read-only
- Daemon **thermal state** (Thermal OK / Recovery / Emergency / No CPU sensor)
- "Updated Xs ago" — time since the last successful poll
- A clickable **warning chip** (count); clicking opens a standalone warnings dialog
- A compact profile selector + Apply
- A **Sensors** toggle that shows/hides the right-hand Sensors panel (DEC-182/184)

### Main dashboard body
Recommended layout:

#### Row 1: summary cards (DEC-178; DEC-185 removed the Safety card)
Four compact cards — **CPU**, **GPU**, **Motherboard**, **Fans**. Each
**temperature** card (CPU / GPU / Motherboard) carries a trend glyph (rising /
falling / flat, derived from `rate_c_per_s`) and a session min/max range. The
**Fans** card shows online/expected counts plus average PWM/RPM. The daemon
`thermal_state` shows on the strip's **thermal chip**, which is clickable and opens
a read-only thermal-safety detail (DEC-185 — re-homed from the former Safety card).
Clicking a temperature card opens its sensor-binding picker.

#### Row 2: primary chart area (DEC-181)
A wide temperature / fan-speed-over-time chart with:
- selectable time range
- a curated default series subset on first run (CPU · GPU · one case temp) instead
  of every series at once
- **chart modes** (Combined [default] / Thermals / Fans / Diagnostics) + Reset — the
  selectors are the Show-mode combo and the Sensors tree (DEC-186 removed the
  per-series checkbox legend and the synthetic aggregate fan-RPM line)
- poll-diff **event annotations** (profile change, reconnect, thermal transition,
  override start/end, sensor-stale / fan-stall onset)
- current-value emphasis via the crosshair readout

#### Row 3: fan zone cards (DEC-176/179)
Fans render as **zone-grouped cards** (the primary fan view), driven by a pure
fan-grouping view-model:
- user-assigned **fan zones** (GUI-owned `fan_zones`, e.g. Front Intake / Exhaust),
  falling back to role/source grouping for unassigned fans
- a per-fan **state chip** — Normal / Low RPM / Stall / Stale / Offline / Override
- per-zone roll-ups: online/expected count, average RPM/PWM
- a per-tile detail dialog to rename a fan or reassign its zone
- **drag a card by its header to reorder** the groups (order persists per machine,
  `fan_zone_order`), and a small **"Fan zones"** collapsible header **shows/hides**
  the whole section so the chart can reclaim the space (`fan_zones_collapsed`) — both
  DEC-187

The dense **raw fan table** (label / source / RPM / PWM) is preserved but re-homed
into a collapsed **"Raw fan data"** expander on the dashboard — advanced detail, one
click away. (Resolves the previously-deferred group-membership badges + per-fan state
chip; see `docs/14_Risks_Gaps_and_Future_Work.md`.)

#### Right-hand Sensors panel (DEC-182/184)
A toggle-button **side panel** — opens by default on wide windows, collapses on
narrow ones so the chart keeps room — hosting the grouped **Sensors** tree (device
grouping, per-series checkboxes, colour swatches, search, freshness in tooltips).

DEC-184 reduced this from the former Sensors/Events/Warnings tabbed inspector: the
Events breadcrumb now lives only in Diagnostics, and active warnings open in a
standalone dialog from the strip's warning chip.

(Resolves the previously-deferred per-sensor freshness side panel; see
`docs/14_Risks_Gaps_and_Future_Work.md`.)

## Time ranges
The dashboard must support:
- 30 sec
- 2 min
- 5 min
- 10 min
- 15 min
- 20 min
- 30 min
- 1 hr
- 2 hr

## Fan chart requirements
The chart must:
- render smoothly with live updates
- support multiple visible series
- support per-series toggle
- preserve readable colours in dark mode
- show time on X-axis
- show RPM on Y-axis
- handle missing samples gracefully
- clearly indicate stale or unavailable series

## Fan visibility controls
Include lightweight controls to:
- show all fans
- hide individual fans
- show/hide by group
- reset visibility to defaults

These controls may be:
- a compact filter menu
- a side drawer
- a pill/badge row
- checkboxes in a chart options panel

The series panel groups coolant temperatures (`coolant_temp`) under an **"AIO / Liquid"** group,
and liquid-cooler pump/radiator fans are tagged "(AIO)" so an AIO reads as a cluster (DEC-157).

## Fan naming
The daemon's fan response includes `id` and `source` but not a display label. The dashboard should use the best available display name in this order:
1. user alias (GUI-owned, persisted locally)
2. hwmon header label (from `GET /hwmon/headers`, for hwmon fans only)
3. stable fan id (e.g. `openfan:ch00`)

## Warning behaviours
If a fan or sensor is stale:
- show a warning chip
- visually soften or mark stale values
- do not silently continue to present the value as fully healthy

If the daemon is disconnected:
- keep last known values marked as stale
- show clear disconnected state
- stop implying active control

## Empty state rules
### No connection
Show:
- a disconnected illustration/state
- explanation that daemon/API is unavailable
- actions: Retry, Enter Demo Mode

### No discovered fans
Show:
- a clean empty state
- possible reasons
- link/action to Diagnostics

## Suggested widgets
- summary status cards
- timeline chart
- fan status cards or compact table
- warning banner
- sensor freshness strip

## Data update expectations
The dashboard should feel live, but not noisy.
Good defaults:
- update visible summary values on the normal polling cadence
- chart points append smoothly
- avoid layout thrash or card jumping

## Nice-to-have later
- user-customisable cards
- detachable charts
- richer telemetry overlays
- comparative sensor/fan charting on same timeline
