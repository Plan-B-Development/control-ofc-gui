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

## Structure (DEC-222 rebuild)

The live surface is a vertical splitter: the **telemetry graph** across the top, and
below it a horizontal splitter carrying the **fan cards** on the left and the
**Thermal Sensors** rail on the right. The graph is the primary component — it is the
one view nothing else duplicates.

### Page header
The page title row carries a **profile selector + Apply**. The sidebar has one too;
this one is kept deliberately so the landing page can switch profiles without
navigating away.

Three banners sit below it, shown only when they apply: hwmon absent/read-only,
daemon API-version skew, and thermal protection active.

Connection state, uptime and the alerts indicator live on the global **ribbon**;
operation mode, poll freshness, the clickable thermal-safety detail and the
cooling-readiness chip live on the global **footer** (DEC-222 re-homed the last four
from the retired Dashboard status strip, so every page now has them).

### Telemetry graph (DEC-181, top)
A wide temperature / fan-speed-over-time chart with:
- selectable time range
- a curated default series subset on first run (CPU · GPU · one motherboard temp)
  instead of every series at once, resolved by
  `series_selection.default_series_keys`
- **chart modes** (Combined [default] / Thermals / Fans / Diagnostics) + Reset — the
  selectors are the Show-mode combo and the Sensors tree (DEC-186 removed the
  per-series checkbox legend and the synthetic aggregate fan-RPM line)
- poll-diff **event annotations** (profile change, reconnect, thermal transition,
  override start/end, sensor-stale / fan-stall onset)
- current-value emphasis via the crosshair readout

### Fan cards (DEC-222, bottom-left)
A responsive flow of compact cards, **one per logical control** — not per fan. That
granularity is forced by the API: live intent is `POST /control/{id}/override`
(DEC-163) and `fan_identify` is stop/restore only, so there is no per-fan speed
surface a per-fan card could reflect or act on.

Each card shows:
- the control name and a **read-only state chip** — Auto / Override active / Low RPM /
  Stale / Stall / Offline (text always paired with colour, WCAG 1.4.1)
- how many fans it covers, so the blast radius of anything done to it is explicit
- **RPM / SPEED / TEMP** — means across reporting members; `—` where unknown, never a
  fabricated 0. SPEED prefers the daemon-commanded PWM and falls back to a labelled
  firmware-measured `duty` (DEC-204)
- a lightweight **curve preview** of the control's own curve (or a placeholder saying
  why there is none)
- **Edit**, which opens the Controls page focused on that control

Two pseudo-cards cover what a control-keyed view would otherwise miss:
- **Unassigned** — controllable fans no control claims, pooled into one card. With no
  profile active that is every controllable fan, so a fresh install still sees its
  hardware rather than an empty page.
- **Read-only fans** — one card each, never pooled. They cannot be assigned to a
  control (DEC-102), and pooling would average away a GPU's measured duty, which for
  such a fan is the only speed signal there is. Their Edit button is hidden, not dead.

Cards are **read-only by design**. The override take/renew/release session — deadman,
monotonic fencing, threaded dispatch (DEC-163/DEC-220) — is owned by the Controls
page; a second session here would race it for the same control.

### Thermal Sensors rail (DEC-182/184, bottom-right)
The grouped **Sensors** tree (device grouping, per-series checkboxes, colour swatches,
search, freshness in tooltips). Always mounted since DEC-222 — the splitter handle is
how the chart reclaims width on a narrow window, replacing the removed show/hide
toggle.

Active warnings are **not** here: the Logs page hosts them beside the event feed
(DEC-222).

### Retired presentations (DEC-222)
The summary cards, Fan Array header, Fan Zone card grid, raw fan table, Quick Actions
panel, Alerts panel and status strip were all removed. Three of them answered "what
are the fans doing?" in three different shapes; the fan cards answer it once. The fan
**zone model** and its settings keys are retained dormant (no schema migration, no
lost zone assignments) — only the UI is gone.

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
The daemon's fan response includes `id` and `source` but not a display label. The dashboard uses the best available display name in this order:
1. user alias (GUI-owned, persisted locally)
2. GPU model name (for `amd_gpu:` / `intel_gpu:` / `nvidia_gpu:` fans)
3. OpenFan channel label — `openfan:ch00` renders as **OpenFan CH0** (DEC-227). Display only; it is never stored as an alias, so it cannot pin an idle header visible via the "user labelled it" rule in `filter_displayable_fans`
4. hwmon header label (from `GET /hwmon/headers`, for hwmon fans only) — **unless it is a
   synthesised `pwmN` placeholder**, in which case tiers 5-6 run. The daemon invents
   `pwm{N}` when the chip publishes neither `pwmN_label` nor `fanN_label`, so a non-empty
   label is not automatically a real one; `is_placeholder_hwmon_label` skips the exact
   `pwm{index}` restatement of the header's own id (DEC-229). This is safety-relevant, not
   only cosmetic — the resolved name is what `_role_preserving_label` persists as
   `ControlMember.member_label`, which sets the DEC-095/162 30% CPU/pump floor
5. `/etc/sensors.d` + in-repo board fallback table (hwmon only). The board half is keyed on
   DMI vendor/model from `AppState.board_info`, written only by
   `DiagnosticsService.set_hw_diagnostics`
6. raw `pwmN` for a known hwmon header; the stable fan id otherwise, as a last resort

**Renaming (DEC-227).** A fan is renamable from every surface that shows its name:
the Sensors rail (double-click the name, or F2, or right-click ▸ "Rename fan…"),
the read-only fan cards, and the Overview fan table. Clearing the text — or
committing the name already shown — removes the alias rather than storing one, so
pressing Enter on an untouched row is a true no-op. The "(AIO)" tag is
presentation, not part of the name, and is stripped on the way in.

A **control** card is titled with `control.name`, which is profile data — renaming
it is a profile write and stays on the Controls page (DEC-222). Sensors are not
renamable: their labels are daemon-owned and there is no sensor-alias setting.

**One-time adoption of profile labels (DEC-228).** A fan's name used to live in two
places that never reconciled: `fan_aliases` (read by every *display* surface, via
`fan_display_name`) and `ControlMember.member_label` (read by the *control*
surfaces). A user who named their fans while building a profile filled only the
second, so the display surfaces showed a fallback. On the first fan poll,
`services/fan_alias_seed.py` adopts those labels into `fan_aliases` — once, gated
by `AppSettings.fan_aliases_seeded`, so a cleared alias is never resurrected.
Labels are stripped of picker badges, length-capped, and skipped when they equal
the fan's fallback name (adopting one would pin an idle header visible, per the
DEC-227 rule). Never runs in demo mode. Profiles are read, never written.

**Control surfaces resolve through `AppState.member_display_name`** (alias >
cached `member_label` > fallback), so a rename made anywhere reaches control-card
member rows, fan-role chips and the member editor.

## Warning behaviours
If a fan or sensor is stale:
- reflect it in the footer health rollup and the Logs page's Active Warnings list
  (DEC-222 — the Dashboard's own warning chip went with the status strip)
- mark the affected fan card's state chip Stale
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
- link/action to System State

## Widgets in use
- timeline chart (primary)
- per-control fan cards
- Thermal Sensors rail
- warning/info banners (hwmon, API skew, thermal)
- global ribbon + footer for connection, mode, freshness, thermal and readiness

## Data update expectations
The dashboard should feel live, but not noisy.
Good defaults:
- update visible values on the normal polling cadence
- chart points append smoothly
- avoid layout thrash or card jumping — fan cards are reconciled in place by control
  id, so a 1 Hz refresh updates text rather than destroying and rebuilding widgets

## Nice-to-have later
- user-customisable cards
- detachable charts
- richer telemetry overlays
- comparative sensor/fan charting on same timeline
