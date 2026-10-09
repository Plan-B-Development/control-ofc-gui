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
navigating away. With no profile active the selector shows the placeholder
**No active profile** at index −1 (DEC-462) — a rebuild never falls back to the first
entry, which named a profile that was not running. It follows the daemon from both
sources (DEC-470, `WUI-a`): the held profile by id, else AppState's daemon-reported name
(by text — skipped when the daemon's id names a profile this GUI does not hold, since a
held profile can share its name), and a running profile this GUI does not hold (a
`--profile` from a system folder) shows **Running: <name> (not in this GUI)** at index −1.
"No active profile" only when both are empty. A failed Apply re-syncs to what runs: the
placeholder from the stopped state (`WUI-b`), the running profile otherwise.

Five banners sit below it, each shown only while it applies:
- **hwmon** absent or all read-only;
- **API-version skew** between the daemon and this GUI;
- **thermal protection active** (DEC-132, worded as a floor under the profile — DEC-307);
- **engine liveness** (DEC-249/259): a slow engine is a warning that says the fans are still
  driven and thermal protection is active; a stopped one is an error that says the daemon is
  not driving the fans and **its thermal emergency protection is not running**, and to restart
  `control-ofc-daemon`;
- **runtime-config fallback** (DEC-321): the daemon could not read `runtime.toml` and is running
  on defaults, which carry no user-assigned header roles.

A failed **Apply** — here or in the sidebar — is shown in the main window's banner as
`Could not activate "<name>": <reason>`, or, for a Dell shared-switch refusal (DEC-403), as the
refusal itself, which names the fans to change (DEC-416, `CTRL-k`). A later successful Apply
takes that banner down, including a re-apply of the same profile.

The global **ribbon** carries the connection state, uptime, a thermal pill and the alerts
indicator; the thermal pill is hidden while the daemon is unreachable, since there is then no
current thermal state (DEC-389, `TS-g`). The **status strip** under it shows the active profile
and, in demo, a **DEMO** badge. The global **footer** carries operation mode, poll freshness, the
clickable thermal-safety detail and the cooling-readiness chip (DEC-222 re-homed those from the
retired Dashboard status strip, so every page has them).

### Telemetry graph (DEC-181, top)
A wide temperature / fan-speed-over-time chart with:
- selectable time range
- a curated default series subset on first run (CPU · GPU · one motherboard temp · and,
  on a liquid-cooled machine, one coolant temp — DEC-329/`WIRE-ai`; a slot with no matching
  sensor is simply dropped, so an air-cooled machine gets the same three as before; a memory
  module never fills the motherboard slot, DEC-491)
  instead of every series at once, resolved by
  `series_selection.default_series_keys`
- **chart modes** (Combined [default] / Thermals / Fans / Diagnostics) + Reset — the
  selectors are the Show-mode combo and the Sensors tree (DEC-186 removed the
  per-series checkbox legend and the synthetic aggregate fan-RPM line)
- poll-diff **event annotations** (profile change, reconnect, thermal transition,
  override start/end, sensor-stale / fan-stall onset)
- current-value emphasis via the crosshair readout

The chart's choices are remembered across launches: the mode (`chart_mode`, also written by
Reset, which returns to Combined and the curated subset), the hidden series
(`hidden_chart_series`), per-series colours (`series_colors`) and the default time range
(`chart_default_range_index`). The hidden series and colours are not saved in demo (docs/10).

### Fan cards (DEC-222, bottom-left)
A responsive flow of compact cards, **one per logical control** — not per fan. That
granularity is forced by the API: live intent is `POST /control/{id}/override`
(DEC-163), which is per-control. `fan_identify` is per-fan but is not a speed
surface — it is a transient, deadman-bounded identification hold whose duty the
daemon chooses (0 for a fan, a floored perturbation for a pump — DEC-311), not a
value a client sets. So there is still no per-fan speed surface a per-fan card
could reflect or act on.

Each card shows:
- the control name, with **Edit** beside it (DEC-238) — a ghost button opening the
  Controls page focused on that control
- a **read-only state chip** — Auto / Override active / Driver alarm / Low RPM / Stale /
  Stall / Offline (worst member wins, in the reverse of that order; **Driver alarm**, DEC-459,
  is the driver's `fan_alarm` bit, a state and never an alert, with a tooltip naming each
  alarmed fan and its `rpm_min_threshold` in short lines, one fact each, and the same text as
  the chip's accessible description — DEC-472, `ALERT-b`/`ALERT-c`), and **Read-only** in place of Auto on a read-only fan's card, since nothing
  drives it (text always paired with colour, WCAG 1.4.1) — alongside how many fans the
  control covers, so the blast radius of anything done to it is explicit
- **RPM / SPEED / TEMP** — means across reporting members; `—` where unknown, never a
  fabricated 0. SPEED prefers the daemon-commanded PWM; where only a firmware-measured
  `duty` exists the **column caption** reads `DUTY` instead of `SPEED`, so a measurement
  is never presented as a value the daemon commanded (DEC-204, relabelled in DEC-238)
- a lightweight **curve preview** of the control's own curve, as a full-bleed band along
  the card's bottom edge (or a placeholder saying why there is none). The band and the
  placeholder share one layout slot, so every card is the same height whichever is
  showing — a card whose curve renders as a text summary reserves no more room than one
  that paints a sparkline

**The band carries controls that are actually driving fans, and nothing else**
(DEC-356). A control gets a card only while at least one of its members appears in the
poll — a role with nothing assigned yet does not, and neither does one whose members
are all absent. A controllable fan no control claims gets no card at all: the Controls
page owns assigning it and counts them on its **Unassigned Fans (N)** button (DEC-233).
When that leaves the band empty it is left **empty** — there is no empty-state label,
because the one it replaced ("No controllable fans detected.") was false in the case it
fired most often, and a disconnect is already announced by the connection banner.

One pseudo-card remains:
- **Read-only fans** — one card each, never pooled. They cannot be assigned to a
  control (DEC-102), so unlike an unassigned fan nothing else will ever account for
  them, and pooling would average away a GPU's measured duty, which for such a fan is
  the only speed signal there is. Their Edit button is hidden, not dead.

Note the deliberate limit DEC-356 accepts: a **partly** live control still reports its
missing members as `Offline`, but a control that has gone *fully* dark has no card to
report them on. That was chosen with the cost stated; see DEC-356 § Part 2.

Cards are **read-only by design**. The override take/renew/release session — deadman,
monotonic fencing, threaded dispatch (DEC-163/DEC-220) — is owned by the Controls
page; a second session here would race it for the same control.

Cards take a **fixed, font-derived width** (`card_metrics.fan_tile_width`) so the flow
grid forms real columns, and opt out of `.Card`'s QSS padding via `density="tile"` so the
inset is charged once rather than by both the stylesheet and the layout (DEC-238). Names
too long for that width elide with a tooltip rather than widening one card.

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
Memory-module temperatures are `mb_temp` on the wire but file under their own **"Memory"** group,
after Motherboard, by classification, and each module is named by its SPD address (**DIMM 0x51**)
because they all publish the label `temp1` (DEC-491).

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
- reflect it in the footer health rollup and the Logs page's alert bar (DEC-282)
  (DEC-222 — the Dashboard's own warning chip went with the status strip)
- mark the affected fan card's state chip Stale
- visually soften or mark stale values
- do not silently continue to present the value as fully healthy

If a fan is stalled (DEC-459, `TS-bg`): every surface reads `AppState.stalled_fan_ids`, which
holds a stall through a missing `stall_detected` for up to 5 s (ending early on RPM above 0) —
so the alert, the chart's single "Stall:" onset marker and the card's Stall chip do not blink
when the daemon misses one reading. `null` means *not evaluated*, never *not stalled*.

If the daemon's `thermal_state` is not normal (DEC-459): an alert — `emergency` an error naming
its causes, `no_sensor_fallback`, `recovery` (an older daemon) and an unknown token a warning —
counted in the footer's health rollup, so the footer never reads "All systems nominal" beside a
Thermal: Emergency chip. It clears once the daemon has been unreachable for 5 s (nothing current is
known); not at once, because the poll reports a disconnect on its first failed cycle and one
timeout would otherwise log a false recovery and re-raise the alert unacknowledged.

If the daemon is disconnected, the page is replaced by the **Not Connected** state (below): the
fan cards are cleared rather than kept as stale values, so nothing implies active control.

## Empty state rules
### No connection
The **Not Connected** page says it is waiting for the daemon and to use `--demo` to run without
hardware. There are no Retry or Demo buttons: the poll reconnects on its own, and demo mode is
chosen at launch (docs/10). When the daemon service is installed but not enabled, a card shows
the enable command with a **Copy command** button.

### No discovered fans
Connected, but no sensor or fan data yet: the **No Hardware Detected** page, with a Subsystem
Status card (OpenFan, hwmon, controls) and a "What to do next" card — check the service, open
the Hardware page for a missing driver, and what the OpenFan controller needs.

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
