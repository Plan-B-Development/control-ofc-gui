# 04 — Dashboard Spec

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

### Top status strip
Always visible page summary:
- Active profile
- Control mode: Automatic / Manual Override / Demo / Read-only
- Daemon health
- Controller availability
- Warning count

### Main dashboard body
Recommended layout:

#### Row 1: summary cards
Cards for:
- CPU temperature
- GPU temperature
- Motherboard temperature
- Active profile
- Total visible fan count
- Warning/fault summary

#### Row 2: primary chart area
A wide fan-speed-over-time chart with:
- selectable time range
- series show/hide
- current-value emphasis
- clean legend or direct series labels

#### Row 3: fan status section
A fan list/card grid showing:
- fan label
- source
- current RPM
- current commanded PWM
- current group membership badges
- state chip if stale/fault/manual

#### Optional side panel or lower strip
Key sensor health list:
- sensor label
- current value
- freshness
- warning marker if stale/invalid

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
