# 07 — System-Health Pages Spec (Overview · System State · Hardware · Logs)

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
The system-health pages help the user understand:
- whether the daemon/API is reachable
- whether controllers are available
- whether sensors are fresh
- whether writes are possible
- what the last errors were
- what can be exported for support/debugging

The redesign retired the single tabbed **Diagnostics** page and split its
content across four standalone sidebar pages (DEC-209…216). The features below
still exist — only their page homes changed:

- **Overview** (`pages/overview_page.py`) — daemon/API health, controller &
  device discovery, the sensor table + its right-click menu, and the live
  fan-status table.
- **System State** (`pages/system_state_page.py`) — the `/diagnostics/hardware`
  report: verdict + issue checklist, BIOS-interference monitor, dual-chip
  warnings, thermal safety & GPU, and the PWM/GPU verify + Open Full Report
  actions. *Rescan Hardware* is a **global-footer** action (DEC-208); this page
  renders its outcome line, not the button.
- **Hardware** (`pages/hardware_page.py`) — the daemon's go/no-go readiness
  checklist and Super-I/O chip detection (both from the combined
  `GET /inventory/hardware-readiness`), plus the opt-in Probe ports action.
- **Logs** (`pages/logs_page.py`) — the event-log stream + filters, diagnostic
  snapshots, and Export Bundle.

These pages must feel intentionally designed, not like a raw log dump.

## Overview page

The **Overview** page answers "is the daemon reachable, what hardware was
discovered, and are the sensors fresh?" It carries what the retired
Diagnostics ▸ Overview / Connection / Controller / Sensors sub-views showed,
plus the live fan-status table.

The two tables sit in `Overview_Splitter_sections` below the fixed cards row
and share whatever height the window has spare (DEC-234 handle, DEC-284 fill).
Both scroll internally, so both gain from extra height and neither is favoured:
the splitter carries the layout stretch and Qt shares the surplus in proportion
to the current sizes, which is also what keeps a dragged — or DEC-245 restored —
ratio intact across a resize. A window too short for the content scrolls as a
whole page rather than clipping either table.

### Summary cards
Summary cards for:
- overall daemon status
- OpenFan availability
- hwmon availability
- last error summary

(The thermal-state chip moved to the **System State** page, alongside the rest
of the thermal-safety report.)

### Connection and daemon health
Show:
- daemon version
- API version
- IPC transport
- overall status
- subsystem freshness/age
- health reasons if provided

### Controller and device discovery
Show:
- OpenFan present / absent
- channel count
- write support
- hwmon present / absent
- discovered controllable headers
- whether RPM support is available

### Sensor table
A rich diagnostic table of every sensor the daemon reports, designed to
answer "what is this sensor, what is it doing, and is it reliable?" at a
glance — without forcing the user to hover every cell (DEC-117).

**Header summary line** above the table:
`Sensors: N total · X CPU · Y board · Z GPU · V liquid · W disk · K stale · J low-confidence · U unavailable · M hidden`
(empty kind buckets are suppressed; the line collapses to `Sensors: —` when
no sensors are reported.) The `U unavailable` bucket counts daemon-reported
`unavailable_sensors[]` entries (DEC-193) and is sourced from the status poll,
not the sensor table re-render.

**8-column table**, on the **Overview** page since the tabbed Diagnostics page was
retired (DEC-216). Row height is derived from a polished probe button at build
time and re-derived on theme change, so no row is vertically clipped at any font
size (DEC-196):

1. **#** — row number, so a user can refer to a row unambiguously in a support
   thread without pasting the full sensor id.
2. **Label** — sensor label reported by the kernel driver. Prefixed with `⚠ `
   for bogus-quirk sensors (e.g. ASUS NCT6776F CPUTIN) and `? ` for
   low-confidence classifications.
3. **Sensor ID** — stable `hwmon:<chip>:<dev_id>:<label>` identifier. Users
   need this to bind sensors to profile members.
4. **Source class** — pretty-printed classification from the sensor knowledge
   base (`CPU die`, `VRM`, `External probe`, `Board thermistor`, …). Unknown
   classes pass through verbatim for forward compatibility.
5. **Chip** — kernel driver / chip name (`k10temp`, `nct6798`, …). Em-dash
   when missing.
6. **Value (°C)** — current reading. When `crit_alarm` is asserted OR the
   live value has crossed the reported `crit_c`, the cell appends
   `⚠ ALARM` in `status_crit` colour.
7. **Age (ms)** — time since the daemon last polled this sensor.
8. **Confidence** — classification confidence (`High` / `Medium-High` /
   `Medium` / `Low`).

**The Sensor Detail dialog opens from the row's context menu**, not from a
per-row Details button — the button column went with the page rebuild. Right-click
any row → "Open detail…".

*This section previously described a 10-column table with **Source**, **Session
min/max** and **Details** columns. Those belonged to the retired Diagnostics page;
the claim outlived it by four releases (DEC-258). Session min/max and Source both
remain in the detail dialog.*

DEC-196 removed the former Kind, Driver type, Trend, and Freshness columns
(and the per-row stale/invalid warn/crit paint that rode on Freshness). All
four fields remain in the Sensor Detail dialog, trend also in the every-cell
hover tooltip, and staleness in aggregate as the header summary's `K stale`
count. The Overview page's fan-status table freshness column and its colouring
are unchanged.

**Sensor Detail dialog** (DEC-117) — opens on Details-button click, row
double-click, or right-click → "Open detail…". A `QTextBrowser` that mirrors
the Hardware Readiness pop-out, surfacing:
- Identity block (Sensor ID, Source, Chip, Kind, Driver type)
- Current state (Value, Age, Freshness, Trend)
- Session range with "currently at X% of session range" marker
- Full classification description and **every** classification note (not
  truncated to 3 like the cell tooltip)
- Board-context section with optional board override note
- **Thresholds** section (DEC-117 Phase B) — listing every populated
  `tempN_max/crit/emergency/alarm/...` value the daemon supplied, plus a
  one-line headroom indicator ("25.0 °C below crit"). When the daemon
  supplies nothing, an explicit "Daemon did not report any threshold
  attributes" placeholder appears so the section is never empty chrome.
- "Driver documentation" link to the chip's kernel.org hwmon page.

**Hide-list** (DEC-117) — right-click a row → "Hide sensor" persists the id
to `AppSettings.diagnostics_hidden_sensor_ids`. Hidden sensors collapse into
a single toggle row at the bottom — `▸ N hidden sensor(s) (click to
expand)` — that re-renders the rows in greyed-out form when expanded.
Right-click → "Unhide sensor" reverses. The hide-list is **local to the
Overview page's sensor table** by default; the **Mirror hidden to dashboard**
button in the header pushes the current hide-list into the shared
`SeriesSelectionModel` as a one-shot (so the dashboard chart hides the same
sensors). Subsequent Overview-side changes stay local until the user
mirrors again.

**Classification right-click** — the same context menu offers **Set as
preferred CPU sensor** / **Set as preferred motherboard sensor** and **Treat as
coolant** (`Overview_Action_treatAsCoolant`), which persist to the daemon's
`POST /config/*` preferences. These replace what used to require a trip to
Settings ▸ Preferred sensors.

Tooltip behaviour on each cell is unchanged (still uses
`format_sensor_tooltip` for hover context).

**Unavailable-sensors panel (DEC-193)** — a low-key, display-only label below
the table lists sensors the daemon *discovered but currently cannot read*
(canonically an `ath12k`/`iwlwifi` WiFi temperature returning `ENETDOWN` while
the radio is soft-blocked). It is driven by the `unavailable_sensors[]` array
on `GET /status` + `/poll` (`{id, label, reason, unavailable_for_ms}`), rendered
as `⚠ Unavailable sensors (N) — discovered but not readable, excluded from fan
control:` with one bullet per entry (`• <label> — <reason> (unavailable Ns)`).
These sensors are evicted from the live `sensors` list, so they raise **no**
staleness warning and **no** dashboard banner or popup — the Overview page's
sensor table is the only surface. The panel is hidden entirely when none are
reported (older daemons omit
the field). The header summary's `N unavailable` count is kept in step with this
panel from the same status poll.

### Lease state (removed at 2.0.0 — DEC-165)
The daemon owns the hwmon lease internally as of 2.0.0; the GUI holds none, so the diagnostics
**Lease tab was removed**. Lease state is no longer a GUI-surfaced diagnostic. (Pre-2.0 this section
showed lease required / held / owner / TTL.)

## System State page — layout

Two rows under the page header, sharing height through the DEC-234 drag handle:

```text
┌──────────────────────────────────────────────────────────────┐
│ SYSTEM HEALTH OVERVIEW  [pill]      [Refresh] [Open Full Report]│
└──────────────────────────────────────────────────────────────┘
                          ↕  DEC-234 handle
┌───────────────────────────────────────┬──────────────────────┐
│ HARDWARE REGISTRY               ~75%  ┊  INTERFERENCE MONITOR │
│                                       ┊──────────────────────│
│                                       ┊  SAFETY & GPU LIMITS  │
└───────────────────────────────────────┴──────────────────────┘
> Advanced actions                        (fixed, below the handle)
```

Health carries the page's densest content, and since DEC-357 it holds **two
collections that are deliberately not ranked together**: the severity-sorted
*condition* cards (a caption, title, description, HTML detail box and doc
button each — only what the daemon measured) and, below them, the collapsed
*"Board notes for this hardware (N)"* section built by `build_board_notes`,
whose entries carry an **evidence status** rather than a place in the alarm
stack. It gets the whole width.

Since DEC-360 the `bios_revert` condition **stands down** once every counted
header has been quiet for `RECLAIM_HISTORIC_AFTER_MS`, using the daemon's
`hwmon.enable_revert_last_seen_ms` (daemon ≥ 2.46.0). The count is monotonic for
the controller's lifetime with no reset path, so before that one reclaim pinned
an ACTION REQUIRED card for the whole uptime. The Interference Monitor keeps the
count and relabels it *Past Interference* with the age interpolated — the
evidence is dated, not discarded. An unknown age (older daemon, or a count
predating the field) never stands anything down.

Since DEC-359, and on the Safety & GPU card since DEC-380, every item in the
page's **alarm surfaces** — the condition cards, the board notes, the
Interference Monitor, the CPU thermal row, the GPU advisories and the Safety &
GPU constraint rows — carries the same Acknowledge/Dismiss
lifecycle, through one layer (`services/health_ack.py`): a silence is an
*occurrence* — `(key, fingerprint, level)` — where the fingerprint must match
exactly and the level must not have escalated. Acknowledge is session-only,
dismiss persists to `dismissed_health_items`. **Which of the two acts is what
decides whether an item leaves the screen: Dismiss removes a condition card,
Acknowledge demotes it** (DEC-363) — the crit bracket goes neutral, the title
greys and an *Acknowledged* pill appears, and the card keeps its place in the
severity sort because only `severity_state` is neutralised, never the raw
`severity` the sort reads. A **reading** is only ever demoted — the Interference
Monitor, the thermal row and a GPU advisory keep their values and lose only
their alarm state, because this section calls them always-visible and the page
must not get quieter by getting less true.

> This paragraph read "a condition card is removed when silenced" until
> 2026-09-12, which stated one rule for two different actions and is where
> `ACK-r` came from: Acknowledge on a condition card relabelled its own button
> and changed nothing else for three releases, while every sibling surface
> demoted. Dismiss was the half that worked, which is what made it plausible.

> **That sentence read "every item" from DEC-359 until 2026-09-17, and it was
> not true of the Safety & GPU card for four releases (`ACK-w`, fixed by
> DEC-380).** Only the kernel-warning advisory rows carried a silence; **six**
> construction sites in `build_safety_gpu_vm` could paint a `warn` row with no
> Acknowledge and no Dismiss at all — `Fan Control` on a `read_only`/`none`/empty
> method, `Overdrive: disabled`, both `ppfeaturemask` branches, `amdgpu binding
> not bound`, and the per-device `AMD <bdf>` rows from the WIRE-v trio. `ACK-w`,
> the register row written to record the gap, itself named only **three**, and
> `ACK-t` before it named the same three: both enumerations were derived by
> listing the `silence=` sites, which is the *silenceable* side and can therefore
> never discover an alarm row that has no `silence=` to list. The two they missed
> are the two that do not look like alarm rows at the call site — `Fan Control`
> takes its state from a helper with no literal `warn` in the constructor, and
> the `AMD <bdf>` rows sit outside the `if gpu:` block further down the function.
> The rule is now enforced in one place (`_GpuRowSilencer`) with a registry sweep
> asserting it from the other direction, so a seventh row fails rather than
> quietly joining them.
>
> **Two things on this page are deliberately outside that list, and saying
> "every item" without naming them is how the overclaim above got written a
> second time — in the very edit that corrected it.** (1) The **Hardware
> Registry** table paints `warn` from `ChipRegistryRowVM.status_state` (a chip
> whose driver is not loaded) and `mainline_state` (an out-of-tree driver, which
> is the *ordinary* case on an `it87`/`nct6687` board) and has no `silence`
> field at all: it is a data table, not an alarm list, and a per-cell Dismiss
> would be meaningless. (2) The `N ACTION REQUIRED` pill is counted before any
> silencing and is never silenceable by design, which is the limit DEC-359 set
> and nothing since has moved.

The rule on the Safety & GPU card is **`state_rank(state) >= state_rank("warn")`**:
a row is silenceable exactly when it raises an alarm. The kernel advisories keep
their pre-DEC-380 exception and are silenceable at every state including `info`,
with their `gpu_advisory_{id}` tokens byte-identical to what DEC-359 wrote, so no
stored silence is orphaned. `ok` and `neutral` rows carry none.

GPU row keys (`gpu_row_*`, one per PCI device for the `AMD <bdf>` rows) are
deliberately **not** shared with the `gpu_readonly` / `gpu_ppfeaturemask`
condition keys, though the tokens would have been identical: Dismiss on a
one-line readout must not also hide the condition card carrying the fix, and the
predicates are not the same fact (`gpu_readonly` additionally requires
`not gpu.ppfeaturemask`). A test asserts the two key sets do not intersect.

`issues_requiring_attention` is computed before any silencing and
`conditions_hidden_count` is rendered beneath the list, so the pill and the
list can be reconciled.

Its `SectionHeader` carries the issue-count pill via `add_trailing` and, since
DEC-358, two controls via `add_action` (right of the stretch): **Refresh**, a
forced refetch of `/diagnostics/hardware`, and **Open Full Report**. Both were
previously the last widgets inside *Advanced actions*, which is constructed
`expanded=False` — so the report, the only entry to `ReadinessReportDialog` in
the application, was off-screen by default, and the page had no refresh at all
while rendering a cache that nothing could retake. The split between the two
header methods is the rule: `add_trailing` for adornments beside the title,
`add_action` for controls. The two status cards moved into a stacked sidebar beside the
registry (`SystemState_Splitter_row2`, horizontal, 3:1, persisted by DEC-245
like every other named splitter).

Three rules keep it from collapsing back into the cramped shape it had, and all
three are **derived, never written down as pixel literals**:

- **The registry's width floor comes from its own columns** —
  `RegistryCard.content_min_width()` sums the header size hints so all six
  columns stay reachable, adds the card's chrome and the width the table's own
  vertical scrollbar will claim, and `set_theme` re-derives the result because
  those hints scale with the theme's base font (the DEC-258 staleness trap;
  `widgets/card_metrics.card_pane_min_width` is the same rule for the Controls
  panes).
  The scrollbar width is read from `verticalScrollBar().sizeHint()`, not from
  the card's `PM_ScrollBarExtent` — the theme sets it via a `QScrollBar` QSS
  rule, which resolves per-widget, so the card's style reports Qt's unstyled
  default instead.
- **The sidebar has no width floor at all.** Qt propagates one from the
  `RadialGauge` minimum plus card padding, so it re-derives itself when the
  gauge, the padding or the font moves.
- **Neither splitter pane carries an explicit height floor.** An explicit
  `minimumSize` *overrides* `minimumSizeHint` rather than backstopping it, so a
  literal there caps the pane below its content instead of protecting it. The
  health card is a `ContentSizedCard`, which reports the height its findings
  need at the current width; when that exceeds the viewport the page scrolls,
  which is what DEC-234 always said it wanted.
  The `RegistryCard` is the deliberate exception and keeps `setMinimumHeight(150)`:
  its table scrolls internally, so the card has no content-driven minimum of its
  own to fall back on and would otherwise collapse to the table's tiny natural
  size. A floor is only harmful where the widget already knows its own height.

Below the combined minimum width the row keeps its floors and the page scrolls
rather than reflowing into a stacked column — the same behaviour every other
multi-pane page in the app has, and no orientation-swapping breakpoint exists
(DEC-281).

**Height the window has to spare goes to row 2, not to the health pane
(DEC-284).** The sections splitter carries the body layout's vertical stretch,
so the band grows with the window instead of a trailing spacer taking the
surplus; `setStretchFactor(1, 1)` then routes that surplus to row 2, whose
registry table scrolls internally and turns extra height into visible rows. The
health card ends its own layout with a stretch, so height given to the top pane
past its content is whitespace inside a card. None of this changes the
constrained case: when the content already needs more than the viewport, the
band is unchanged and the page scrolls exactly as DEC-281 describes.

## Logs page
The **Logs** page is a **List + Inspector** event workflow (DEC-314): find an event on
the left, understand it on the right. Three regions, top to bottom:

1. **Alert bar** (`Logs_AlertBar`, DEC-282) — one line, opening the Alert Centre.
2. **Activity strip** (`Logs_Histogram_activity`) — event volume over the retained feed,
   severity-stacked; selecting a column filters the list to that time slice.
3. **Splitter** (`Logs_Splitter`) — the event list (`Logs_Table_events`) beside the
   tabbed inspector (`Logs_Tabs_inspector`: Details · Raw · Diagnostics · Journal).

It provides a readable log/event view for:
- recent app events
- recent API failures
- validation errors
- profile / daemon-control warnings
- write denials/clamps surfaced by the daemon

## Required user actions
- Reload config
- Reconnect controller
- Export support bundle
- Copy last errors

## Action behaviour notes

### Reload config
This should reload GUI-owned config first.
If the daemon does not expose a runtime reload endpoint, do not fake a daemon config reload. Instead:
- reload local config
- optionally refresh/poll all known read endpoints
- explain what was and was not reloaded

### Reconnect controller
The daemon exposes `POST /hwmon/rescan` (surfaced as *Rescan Hardware* in the
System State page since DEC-147) for hwmon re-enumeration; serial-controller
reconnection remains daemon-automatic — a detached 60s / 180s post-boot search
(DEC-361) plus the poll loop's runtime reconnect mode after 5 consecutive read
errors — so no GUI reconnect button exists. (The startup "5× backoff" this line
used to name was the ladder DEC-361 deleted; the *Rescan Hardware* action gained
a `POST /fans/openfan/rescan` leg in DEC-265 for a controller that appears after
the post-boot window closes.)
- refresh status
- explain that new fan-control hardware may require a daemon restart
- the rescan result line carries that note verbatim

### Export support bundle
Create a structured bundle including:
- GUI settings
- active profile
- profile set
- theme info
- current daemon status snapshots
- capabilities snapshot
- sensor snapshot
- fan snapshot
- recent GUI logs
- system/environment summary useful for Linux debugging

### Copy last errors
Should copy a concise but useful text summary, not an unreadable blob.

## System-health UX rules
- use color for severity, but do not rely on it alone
- keep critical information high on the page
- use expandable detail regions for large raw payloads/logs
- allow copying key blocks easily
- timestamps should be consistent and readable

## Warnings to surface explicitly
- daemon unreachable
- thermal protection active
- stale sensor data
- write support disabled
- unsupported device categories
- demo mode active

## Implementation: Latency semantics (R34)

### What age_ms means
The `age_ms` values shown in the Overview subsystems area are **daemon-side cache staleness**: time since the daemon's polling loop last successfully read data from that hardware subsystem. They are computed in `staleness.rs` as `Instant::now() - last_subsystem_update`.

### Why subsystem ages differ
- **OpenFan** (serial I/O): Each poll cycle involves serial send + wait + parse over USB. Typical latency 100-500ms per cycle.
- **hwmon** (sysfs): Each poll reads files under `/sys/class/hwmon/`. Typical latency ~1ms.
These differences are **expected behavior**, not a bug. The GUI poll cycle (1000ms) adds an additional 0-1000ms of staleness that is not reflected in the daemon's `age_ms` value.

### Display rules
- Show subsystem `reason` text from daemon alongside age (e.g., "readings fresh", "readings stale").
  Treat it as **daemon prose, not contract** — render it, never match on it. On daemon ≥ 2.24.2 a
  partial-coverage wording also appears ("N of M readings stale — the poll loop is running but is
  not refreshing them"). An absent OpenFanController reads "no OpenFanController connected"
  (DEC-302) — but **since GUI v2.78.0 the Overview card does not render that entry at all**
  when `/capabilities` reports `openfan.present == false` *and* the subsystem's own `status`
  is `ok` (DEC-381). The daemon still emits it; the wire shape in `docs/08` is unchanged, and
  the support bundle and system report still carry the full array. The health condition is
  load-bearing: an `openfan` subsystem reporting `warn` or `crit` is still rendered, because
  it feeds `overall_status` and the `Status:` pill must not degrade with its explanation
  hidden — `/capabilities` refreshes every 300 s against `/status`'s 1 Hz, so the two can
  disagree for minutes after a controller is unplugged mid-session
- Include an explanatory note: "Age = how long ago this subsystem's data was last refreshed".
  **Not** "time since the daemon last polled it": on daemon ≥ 2.24.2 the **`openfan`** entry reports
  the worse of poll *liveness* and data *freshness*, so when a poll is running but not covering every
  channel, `age_ms` is the **oldest reading's** age rather than the poll's (DEC-302). That is the
  whole point of the change — the old wording described the number that made a 3-of-10 frame read
  as "readings fresh". `hwmon` still reports poll liveness only, deliberately (see `docs/08`)
- Show daemon uptime when available
- Do not force subsystem ages to match — they reflect different I/O paths

### Freshness thresholds (daemon-defined)
- **OK**: age <= 2 × expected interval (default: <=2000ms for 1s interval)
- **WARN**: age > 2× and <= 5× interval
- **CRIT**: age > 5× interval or never updated

## Implementation: Event log + diagnostic probes (DEC-111, redesigned DEC-314)

### Three distinct concepts
The Logs page surfaces three closely-related but distinct streams. Confusing them is the original sin the DEC-111 rewrite cleared up:

| Surface | What it answers | Storage | Lifetime |
|---------|-----------------|---------|----------|
| Event Log (the Logs page) | What has the GUI been doing in this session? | In-process `collections.deque` (`MAX_EVENTS = 200`) | Session-only — cleared on GUI exit |
| Alerts (badge → alert surface) | What is wrong **right now**, and what was wrong recently? | `AppState.alerts`, an occurrence ledger reconciled every poll (DEC-282) | An occurrence closes when the condition resolves, but is retained as recovered history until acknowledged — it is never deleted on acknowledgement, and acknowledging one occurrence never suppresses a later one |
| System Journal (snapshot button) | What happened across daemon restarts? | systemd journal, fetched on demand via `journalctl -u control-ofc-daemon` | Daemon-owned; persistent |

### Event stream
The event stream lives on the Logs page (`pages/logs_page.py`) as the
`Logs_Table_events` **`QTableView`** — a `LogEventModel` (`widgets/log_event_model.py`)
painted by `LogRowDelegate` (`widgets/log_row_delegate.py`). It was a `QTableWidget`
with four text columns and a `StatusPill` child widget per row until DEC-314; the
standalone `EventLogView` widget was retired before that, in the DEC-216 redesign.

**One derivation, not two.** Every visible surface is re-derived by
`LogsPage._refresh_view()` from the pure functions in `services/logs_view.py`. The
pipeline is fixed and its order is load-bearing:

```
build_log_rows → collapse_repeats → filter_log_rows → newest_first
```

Collapsing runs **before** filtering, so "consecutive" means consecutive in the real
feed rather than in the filtered view, and a run's repeat count does not change as the
user types in the search box. Ordering is flipped exactly once, at the end — the list
is **newest first**.

Each row is two lines: a severity edge and message, then a subdued meta line (time ·
source · `component` where the event carries one), with a `×N` badge for a collapsed
run. Filters are four ANDed controls:

- **Severity** — three *independent* toggles (`info` / `warning` / `error`), each
  showing a live count, plus an `All` chip (`Logs_Btn_levelAll`) that checks all three.
  Independent rather than the mutually exclusive `All | WARN | ERR` of the design
  reference, which cannot express "INFO only" or "WARN + ERR".
- **Source** — single-select `QComboBox`; populates dynamically from observed sources,
  starting with "All sources". A restored-but-absent selection is retained as an option
  (DEC-245).
- **Search** — `QLineEdit` substring match against message and source
  (case-insensitive) (`Logs_Edit_search`).
- **Time window** — set by clicking a column of the activity strip; cleared with
  `Logs_Btn_clearWindow`, by clicking the selected column again, or with Escape.

**Counts differ between widgets, by design.** The filter chips count *rows* (a
collapsed run counts once — what checking the chip will show you); the activity strip
counts *events* (real volume). The `×N` badge is what explains the difference.

Colours read from `active_theme()` at paint time, so a theme switch is picked up on the
next repaint with no plumbing; `set_theme` only asks for that repaint.

**Selection is keyed by `DiagEvent.seq`, never by row position.** Under newest-first
ordering every row's index changes whenever an event arrives, so an index is
meaningless a moment after it is read. A selected row that is filtered out or aged out
of the feed keeps its detail in the inspector (DEC-210's rule) while losing its
highlight.

Follow behaviour: the tail is the **top** of the list. Following holds the view at the
top; scrolling to older events suspends it and counts arrivals into
`Logs_Btn_newEvents`; clicking that resumes.

Keyboard (brief §9), scoped to the list with `WidgetShortcut` so it cannot fire while
the user is typing in the search box: `/` focuses search, `f` toggles Follow, `Esc`
clears the selection, Up/Down move it.

### Emitter contract
`DiagnosticsService.log_event(level, source, message, *, fields=None)` is called from
production services at *state transitions only*, never per cycle:

| Source | Emits when |
|--------|------------|
| `gui` | GUI start/exit; theme changed; demo mode activated; kernel warning acknowledged; fan-name re-match; uncaught exception |
| `polling` | First connection established; disconnected (after a prior connect); daemon-reported active profile detected |
| `profile` | Activated/deactivated; profile load error |
| `hwmon` / `openfan` / `gpu` | Hardware rescan result; OpenFan adoption; GPU fan restore |
| `sensor` / `fan` / `api` | Alert onsets and recoveries, via `attach_alert_source` |

`fields` (DEC-314) is optional structured metadata, carried **only where the emitter
genuinely holds it** and would otherwise flatten it into the sentence — the alert
ledger's `component` / `alert_key` / `duration_s`, an exception's type and value, a
rescan's header count, an adopted port. It is never synthesised from message text, and
an empty mapping renders nothing: the inspector shows no placeholder rows for absent
data. `component` is the one field promoted onto the row and is the inspector's
correlation key.

Per-cycle work (every poll, every write attempt) must continue to use Python `logging` directly — the in-process event log is for breadcrumbs the user opens the Logs page to see, not the daemon journal.

### Inspector tabs (DEC-314)
The four probes moved off the page body into `Logs_Tabs_inspector`. There is no
permanent bottom section any more.

| Tab | Contents | Loading |
|-----|----------|---------|
| **Details** | Severity, precise timestamp, source, full message, structured fields (only when present), repeat count + first/most-recent occurrence (only for a run), related events, `Copy event + context`, and a contextual action for sources with a known follow-up page | With the selection |
| **Raw** | `Logs_Text_raw` — the **stored event record** in full, selectable and copyable. A GUI event was never a line of text, so this serialises the record rather than assembling a syslog-shaped string out of display fields and labelling it "raw". A row carrying a genuine verbatim source line is shown untouched | With the selection |
| **Diagnostics** | Daemon Status, Controller (OpenFan), GPU State — `Logs_Text_daemonStatus` / `…controllerStatus` / `…gpuStatus`, each with its own Refresh (`Logs_Btn_daemonStatus`, …), all from the existing `DiagnosticsService.format_*` providers | **Lazy**, on first activation only |
| **Journal** | `Logs_Text_systemJournal` + `Logs_Btn_systemJournal`, unchanged fetch/error/permission handling | **Lazy**, on first activation only |

Neither probe tab polls: opening Logs must not spawn a `journalctl` subprocess, and
re-selecting a tab does not silently re-run a probe — Refresh/Fetch is how a stale one
is renewed.

**Related events** correlate on `fields["component"]` where the event has one, else on
`source`, and the panel *says which*. The design reference's preferred first tier — a
stable sensor/channel/device id — does not exist in the event model; see
`DECISIONS_OPEN_ITEMS.md`. `Filter to these` routes through the same public
`LogsPage.show_related_logs(source, component)` the Alert Centre uses.

`Clear Logs` (`Logs_Btn_clear`) only clears the event feed; the probe panes are
independent, so clearing the log can never wipe a journal block the user just fetched —
the original DEC-111 bug (one shared `QPlainTextEdit` wiped by Clear Log) remains
structurally impossible.

### Empty and error states
`Waiting for events…` (nothing logged yet) · `No events match this filter` ·
`Select an event to inspect` · the probe panes surface the real error text returned by
the existing diagnostic/journal paths, with their Refresh/Fetch button as the retry.

### Journal access
- Uses `subprocess.run()` with `--lines=100 --no-pager --output=short-iso`
- 5-second timeout prevents hangs
- Permission failure → message explaining `systemd-journal` group requirement
- `journalctl` not found → message explaining systemd dependency

### Probe widget
`QPlainTextEdit` with `setMaximumBlockCount(2000)` and a monospace font. The high cap is appropriate for journal pastes; the event list has its own 200-row cap that mirrors the deque.

## Implementation: Lease tab (removed at 2.0.0 — DEC-165)

The diagnostics **Lease tab** (explanation card + live status card) was **removed** at the 2.0.0
cutover. The GUI no longer holds an hwmon lease — the daemon acquires, renews, and releases it
internally as the sole writer, and runs hwmon write-verify under its own internal lease. There is no
GUI-surfaced lease state to explain.

## Implementation: System-health page theming (R34)

### Transparent labels
All labels inside Card frames use `background: transparent` inline style. This prevents opaque label backgrounds from conflicting with the Card class background across themes.

### CSS class usage
- Card title labels: `.PageSubtitle` class (bold section-header role, inherits theme size)
- Metadata/explanatory labels: `.CardMeta` class (smaller, secondary color)
- Status label in button row: `.CardMeta` class
- Collapsible section headers: `.CollapsibleSectionHeader` class (DEC-112) —
  body-sized + semibold, subordinate to `.PageSubtitle` card titles, theme-
  derived font size (no hardcoded px), chevron in the button text
- No hardcoded `font-size: Npx` on any system-health-page label

### No inline font-size overrides
All font sizing is inherited from the global theme stylesheet via CSS classes. Changing the theme text size changes the text on the Overview / System State / Hardware / Logs pages consistently.

## Implementation: Hardware Readiness — System State page (v1.1.0; own tab in v1.26.0 — DEC-124; relocated to the System State page — DEC-211)

### What it shows
The **System State** page (`pages/system_state_page.py`) presents the "Hardware
Readiness" health report. It fetches data from `GET /diagnostics/hardware`
(daemon v1.2.0+) and presents a unified view of hardware compatibility and
driver status. The live Fan Status table now lives on the **Overview** page.
(Historically — DEC-124 — this report lived on a dedicated Diagnostics
**Troubleshooting** tab inserted right after Fans; the redesign moved it to its
own page.)

### Card contents
1. **Summary line** — total headers, writable count, warnings if all read-only
   or no chips detected.
2. **Chip table** (5 columns: Chip, Driver, Status, Mainline, Headers) — one
   row per detected hwmon chip with driver load status from kernel modules.
3. **Kernel modules table** (3 columns: Module, Loaded, Mainline) — all known
   hwmon driver modules and their load state from `/proc/modules`.
4. **ACPI conflicts** — shown only when the daemon detects ACPI OpRegion
   claims overlapping known Super I/O I/O port ranges. Includes remediation
   tip (kernel parameter or BIOS change).
5. **Thermal safety** — current safety rule state, CPU sensor availability,
   emergency/release thresholds.
6. **GPU diagnostics** — shown only when an AMD dGPU is present. PCI BDF,
   model, fan control method, overdrive status, ppfeaturemask value and bit 14
   status, zero-RPM availability.
7. **Chip guidance** — contextual BIOS tips, known issues, and driver
   documentation links from the chip-family knowledge base
   (`hwmon_guidance.py`). Shown per unique chip prefix.

### Layout: cooling-readiness on the Hardware page (DEC-212 redesign)
The redesign moved cooling-readiness off a Diagnostics tab onto its own
**Hardware** page (DEC-212). It is now a **checklist of readiness checks** plus a
list of **actionable steps** — live structure: a checklist `Card`
(`Hardware_Card_checklist`) with a `Hardware_Pill_verdict` rollup and one
`Hardware_Check_{code}` row per check, and an actions `Card`
(`Hardware_Card_actions`) with one `Hardware_Action_{code}` card per step. The
per-advisory rows and the liability disclaimer described below were folded into
those action cards / retired.

The pre-redesign **DEC-124** design (kept for provenance; superseded the
DEC-115/DEC-116 cards): on its own System State page nothing competed with a fan
table for vertical space, so the readiness content was a flat, always-readable
health report inside one `Card` frame (then `Diagnostics_Frame_hwReadiness`),
top-to-bottom:

- **Header action row** — the "Hardware Readiness" title, *Open Full Report ↗*
  (pop-out), *Rescan Hardware* (DEC-147: `POST /hwmon/rescan` — daemon-side
  re-enumeration after loading a sensor kernel module; a result line under the
  row reports the header count, notes that sensors refresh on the next poll
  cycle, and repeats the daemon's caveat that new *motherboard* fan-control
  hardware still requires a daemon restart — suppressed when an OpenFan
  controller was adopted, since the same action also carries a
  `POST /fans/openfan/rescan` leg that adopts one without a restart (DEC-265),
  and the line then names the port instead; a successful rescan pushes the fresh header list
  through `AppState.set_hwmon_headers` and chains a `/diagnostics/hardware`
  refetch). *Rescan Hardware* is now the application's global-footer action
  (DEC-216, relocated from the retired Diagnostics page); the separate
  *Refresh Hardware Diagnostics* GUI-side refetch button was removed in the
  same redesign — the footer rescan's chained refetch supersedes it.
- **Verdict banner** (DEC-113) — always visible, traffic-light coloured.
- **Blocking-alert stack** — module collisions, module conflicts, and the
  BIOS-interference headline (those that mean "do not write PWM until resolved"
  or report active EC contention). Each is individually visibility-gated, so the
  stack collapses to nothing on a healthy system, and is always on screen when
  present — never behind a collapse.
- **Issue checklist** (DEC-124) — one row per detected problem
  (`detect_readiness_problems`): a severity badge, the problem label, its
  one-line fix, and a clickable doc link. A healthy system shows a single
  `✓ No issues detected` line. This promotes the former buried "To fix" block
  into a first-class, always-visible checklist (per NN/g progressive disclosure
  + PatternFly status-and-severity guidance). The badge is built from the shared
  `severity_display` mapping (DEC-158), so it carries an icon **and** the word
  **and** a colour (`CriticalChip` red / `WarningChip` orange) — colour is never
  the only cue (WCAG 1.4.1).
- **Advisories** (historical objectName `Diagnostics_Container_advisories`) — board/chip
  vendor quirks, one collapsible row each, most-severe-first. Replaces the old
  single flat `[SEVERITY] …` PlainText label: every advisory now shows a
  per-severity badge (icon + word + colour + weight) and an always-visible
  summary, with its detail in a `CollapsibleSection` that opens by default for
  **CRITICAL/HIGH** and stays collapsed for **MEDIUM/INFO**. The four tiers map
  CRITICAL→red, HIGH→orange, MEDIUM→amber (`status_caution`), INFO→blue
  (`status_info`) — so **INFO no longer shares the warning tiers' orange**. Each
  detail links to the Hardware Compatibility Guide's *Manufacturer Quirks*
  section and reduces bullet overuse (`advisory_detail_html`: 1–2 items render as
  prose, only 3+ short parallel items become a list). Only GUI-authored DB
  strings are rendered (no daemon string is interpolated), so rich text is safe
  (DEC-106). The **dual-chip** setup warning and **ACPI conflicts** sit alongside
  it — advisory, shown only when present.

  > **Updated, 2026-09-11 (DEC-357).** Two corrections in one, because the first
  > was found while tracing the second.
  >
  > *Where they live.* This bullet used to say the advisories were "now folded
  > into the `Hardware_Action_{code}` cards, DEC-158/DEC-212". They were not —
  > `cooling_readiness` / `hardware_view` contain no advisory or quirk code at
  > all. They went to the **System State** page at DEC-211.
  >
  > *What they are.* DEC-211 merged them into the health **issue** stack, and
  > DEC-357 unpicked that: a vendor quirk is a **board note**, not a condition.
  > `services/system_state_view.build_condition_cards` renders only conditions
  > the daemon measured; `build_board_notes` renders the quirks in a collapsed
  > *"Board notes for this hardware (N)"* section below them, via
  > `widgets/system_state_cards._make_note_row`. A note carries an **evidence
  > status** (`observed` / `not_observed` / `unverified` / `reference`) rather
  > than being ranked into the alarm stack, and it can be acknowledged or
  > dismissed per occurrence. `severity` still governs presentation exactly as
  > described above; it no longer decides whether the page raises an alarm.
  >
  > The collapse rule and the four-hue map described above were **lost** in the
  > DEC-211 move and are **restored** by DEC-357 on the note rows —
  > `SeverityDisplay.default_expanded` has a production consumer again, and the
  > caption takes the themed chip class directly, so MEDIUM/LOW paint
  > `status_caution` and INFO `status_info`. Register rows `SSN-c` / `SSN-d`,
  > both closed.

- **Summary + board identity** — the readiness summary line and board identity.
- **Five flat detail sub-sections** (`CollapsibleSection`, all collapsed by
  default): *Detected hardware* (chip + kernel-module tables), *BIOS
  interference detail* (per-header revert rows + footnote — **hidden entirely
  unless a header reports a non-zero revert count**, DEC-116), *Thermal safety &
  GPU*, *Guidance & documentation* (chip BIOS tips / known issues + doc link),
  and *PWM control test* (verify combo, Test PWM Control, Verify All Writable,
  **Characterise PWM Response** — DEC-313, the deeper PWM/RPM sweep, gated on
  `control.pwm_characterization` and hidden entirely without it,

  Since **DEC-334** (daemon ≥ 2.40.0, gated on `control.pwm_behaviour_characterization`)
  the dialog opens on the daemon's **safety preflight**, exactly as Control-Path Discovery
  has since DEC-333, and the sweep walks the header **down from the top and back up** so it
  can report hysteresis. It ends at the highest duty, which is what keeps an interrupted run
  benign. Results gain a compact summary (safe tested range · effective control range ·
  reported RPM range · hysteresis · RPM stability · response and settling time), a
  rising/falling curve, and a collapsible engineering block carrying sample interval,
  measurement resolution, standard deviation, coefficient of variation, dropouts, outliers
  and plateaus. **Nothing this adds can report a failure**: hysteresis, plateaus, tach
  variability and an out-of-learned-range response are all observations, and the last is
  worded with the benign explanations listed beside it.

  Since **DEC-405** (daemon ≥ 2.52.0) the figures behind those rows are honest about a
  slow tach: settling is judged on register *updates* and never before the first one, RPM
  stability is computed over the **settled tail** only, "measurement resolution" is the
  register's own cadence rather than the 500 ms sampler, and monotonicity is judged per leg.
  A point that never settled shows **"Not settled"** — an absence of steady-state evidence,
  neutral, never a warning. The default settle is 12 s, so a default sweep takes about
  twice as long as before; a validation session's per-diagnostic estimates say so.
  progress + result, and — DEC-120 — **Test GPU Fan Control** with its own
  result label, shown only when a writable AMD GPU is present and the daemon
  supports the verify route, ≥ 1.11.0). Beside the GPU verify button sits
  **Restore GPU Fan to Automatic** (DEC-147: `POST /gpu/{id}/fan/reset`) —
  shown for any writable AMD GPU with **no** daemon version floor (the reset
  route predates every supported daemon), and **disabled with an explanatory
  tooltip while the active profile owns an `amd_gpu:` member** (the daemon
  engine would silently re-assert its curve within seconds). The click
  handler (`_run_gpu_restore`) re-checks that gate; the async result callbacks
  (`_on_gpu_restore_ok` / `_on_gpu_restore_error`) then report the daemon's
  result: a reset shows a success chip, a
  daemon-reported no-op shows a warning chip, and an error shows a critical
  chip — every outcome lands in the event log. There is **no** session flag and
  **no** close-time auto-reset: the GUI never writes GPU PWM (DEC-165), so there
  is nothing to undo on close.
- **Discover Control Path** (DEC-333, AIO Phase 8 Batch 1) — a third button on
  every **Hardware page** PWM header card, beside *Test Control* and
  *Characterise*, gated on `control.control_path_discovery` and **disabled with
  the reason in its tooltip** rather than hidden, so an older daemon explains
  itself. It opens `ControlPathDiscoveryDialog`, whose **first state is the
  safety preflight**: eleven rows from `GET /diagnostics/preflight`, each with a
  state pill and the daemon's own wording, and a verdict chip. A `blocked`
  verdict **disables Start and lists the blocking reasons**; the GUI reads the
  daemon's `verdict` and `blocking[]` and never rolls the rows up itself
  (`docs/08` states the rule). A preflight that could not be fetched is
  *advisory-unavailable* and does **not** block — the daemon still runs its own
  guards on the POST, and refusing on a missing advisory would make an older
  daemon less usable than before the feature existed.

  The result view lists every tach channel watched — responders first with
  confidence, direction, before/after RPM and repeatability, then the quiet
  channels with "no meaningful response", because an absent row is
  indistinguishable from a channel nobody checked. A **failed or skipped restore
  is surfaced as its own critical-toned line**, not folded into the notes. After
  a successful run the relationship appears in the header card's *Details*
  disclosure as "Control relationship … Confidence … Last validated …", read from
  the daemon's persisted store so it survives a GUI restart.

  A `no_tach_response` result is rendered informationally, never critically: a
  header may legitimately drive no tach-reporting device, or drive one running
  under its own internal control.
- **Evidence & confidence** (DEC-333) — a collapsed disclosure in the validation
  dialog explaining the six provenance classifications, and naming the nine
  properties software cannot establish from motherboard sensors at all. Those are
  listed explicitly rather than omitted, because an omitted row reads as a pass.
  The same legend is embedded in the JSON export.
- **Liability disclaimer** (historical objectName
  `Diagnostics_Label_readinessDisclaimer`; retired in the DEC-212 Hardware
  redesign, DEC-158) —
  one calm, persistent note at the bottom of the card (`REMEDIATION_DISCLAIMER`,
  `CardMeta` weight): the checklist fixes, advisory details, and chip guidance
  all describe kernel/driver/firmware changes applied at the user's own risk.
  Low-weight by design — heavy red styling is reserved for the real alerts above.

The live *Fan Status* table lives on the **Overview** page.

Because the verdict, the blocking-alert stack, and the issue checklist are all
**always visible** (no outer collapse), safety warnings can never be hidden
behind a collapse — a strict strengthening of the DEC-116 rule. The five detail
sub-sections still open on demand; the *BIOS interference detail* sub-section is
**hidden whenever there is no interference to report** and is revealed +
**auto-expanded** only on a non-zero revert count (DEC-116) — so it never
presents an empty header to expand into nothing. The verify controls and their
result labels share one sub-section, so reaching the buttons necessarily expands
the section that shows the outcome.

`CollapsibleSection` (`ui/widgets/collapsible_section.py`) is a first-party
widget (DEC-112 D1): a flat `QPushButton` header (chevron rendered in the
button text so it inherits the themed `.CollapsibleSectionHeader` colour, and
so the text left-aligns — `QToolButton` ignores stylesheet `text-align`)
toggling a content container. Multiple sections may be open at once (unlike
`QToolBox`). The toggle is instant (no animation) for deterministic tests. A
section historically supported a **persistent area** (`add_persistent_widget`,
DEC-115) — widgets between the header and the content that stayed visible
regardless of collapse state. DEC-124 retired the readiness card's use of it (the
verdict + alerts are now always-visible siblings), and with no remaining caller the
add-persistent API was removed in v2.8.0. Because Qt's `QWidget.isHidden()`
reflects a widget's
*own* show/hide flag rather than an ancestor's collapsed state, the
visibility-gated labels keep working unchanged inside the sections.

### Readiness verdict, auto-fetch, "To fix", and pop-out report (DEC-113)
- **Verdict banner** — a prominent, always-visible one-line status at the top
  of the report, computed by
  `readiness_report.readiness_verdict(diag, pwm_control_verified=…)`:
  `✓ System ready — N headers, M writable · thermal safety <state>`
  (`SuccessChip`) or `⚠ K issue(s) need attention — see "To fix" below`
  (`WarningChip` / `CriticalChip`). It leads the report with an at-a-glance
  answer. **Info-level vendor quirks are FYI notes and are not counted as
  problems.**
  **The report is the System State page's answer at length, not a second
  opinion** (`SSN-i`, answered as option C): both derive from one
  `detect_readiness_problems(diag, pwm_control_verified=…)` pass **with the same
  argument**, so the banner and the page's own `N ACTION REQUIRED` pill cannot
  disagree.
  **⚠ Read the argument as load-bearing, not decorative.** One derivation was
  always the design, and from DEC-357 (GUI v2.71.0) to DEC-379 (v2.76.5) it was
  not enough: that change gave `detect_readiness_problems` a
  `pwm_control_verified` argument and threaded it through two of its four call
  sites — `build_system_state_vm` and `build_condition_cards` passed it,
  `readiness_verdict` and `build_fix_guidance_html` called it bare — so both ran
  as if PWM control had never been tested. Measured 2026-09-17 on a Gigabyte
  AORUS MASTER / `it87`-family chip with a recorded *ineffective* verify, the
  System State pill read `1 ACTION REQUIRED` while the Full Report it launches
  opened with `✓ System ready` and carried an empty "To fix" block. Row `SSN-l`,
  closed by DEC-379; the page now obtains the flag from a single accessor
  (`SystemStatePage._pwm_verified`) so a future consumer cannot omit it.
- **Auto-fetch** — opening the System State page fetches `/diagnostics/hardware`
  once per session (guarded), so the verdict + checklist populate without a
  manual *Refresh* click. Since DEC-229 the poll worker also prefetches it once
  on the first capabilities cycle: the DMI board identity keys the hwmon label
  fallback table, so fan names on a chip that publishes no labels would otherwise
  stay `pwmN` until the user happened to visit this page. Both paths land in
  `DiagnosticsService.set_hw_diagnostics`, the single writer of the shared cache
  **and** of `AppState.board_info`.
- **Issue checklist (inline "To fix")** — the always-visible checklist (above)
  renders one row per detected problem (ACPI, module collision, GPU
  `ppfeaturemask`, dual-chip, all-read-only, …) with its one-line fix and a
  clickable doc link, from `detect_readiness_problems(diag)`. Both it and the
  pop-out's "To fix" block (`build_fix_guidance_html`, carrying the shared
  `REMEDIATION_DISCLAIMER`) derive from that one problem list, so they can never
  disagree. Content is **GUI-authored only** (no daemon strings), so it is safe
  as rich text with external links — sidestepping the DEC-106 escaping
  requirement.
- **Pop-out report** — *Open Full Report ↗* opens `ReadinessReportDialog`, a
  themed, resizable `QTextBrowser` window with the complete report (summary, an
  **Advisories** section, detected-hardware table, thermal/GPU, and the "To fix"
  block). The Advisories section (DEC-158) lists the same `advisory_rows(diag)`
  the inline panel shows, in the same most-severe-first order and with the same
  `severity_display` colour + icon + word — `severity_hex` resolves the chip
  class to a hex colour since the HTML report has no QSS class cascade — so the
  report and the panel cannot drift (DEC-115). Daemon strings **are**
  HTML-escaped here. Link colour is set inline per anchor (the app-wide
  stylesheet overrides the palette Link role, so inline `style="color:…"` is
  the only reliably-applied path for contrast).

### Combo-box down-arrow (DEC-113)
The theme styles `QComboBox::drop-down`, which makes Qt drop the native
down-arrow. The app ships no image assets and supports arbitrary custom theme
colours, so a static asset cannot follow the theme. (Since DEC-226 the theme
also paints a `QPalette` from the same tokens, but a palette carries colours,
not glyphs — it cannot supply the arrow either.) `theme.combo_arrow_svg_path(color)` instead generates a tiny
chevron SVG in the theme's `text_secondary` colour to the cache dir and the
stylesheet references it via `QComboBox::down-arrow { image: url(…) }`. It
degrades gracefully (no rule) if the cache is not writable.

### Chip-family knowledge base
`src/control_ofc/ui/hwmon_guidance.py` maps chip name prefixes to:
- Driver name and whether it's in mainline kernel
- Package name for out-of-tree drivers (e.g. `nct6687d-dkms-git (AUR)`)
- Driver documentation URL
- BIOS tips specific to manufacturer/chipset combinations
- Known issues (ACPI conflicts, read-only headers, etc.)

Supported chip families: Nuvoton NCT679x (incl. the NCT6701D, which mainline
reports as `nct6799`), NCT677x, NCT6683, NCT6686, NCT6687; ITE IT8603E / IT8620E /
IT8628E (mainline), IT8613E, IT8625E, IT8665E, IT8686E, IT8688E, IT8689E, IT8696E,
IT8698E, IT87952E, IT87xx (generic); the IT8883 bridge (as an explanation, not a
chip); ASUS sensor-only drivers (`asusec`, `asus_wmi_sensors`, `atk0110` — keyed on
the hwmon names since DEC-421); Fintek F71882FG, F718xx; SMSC SCH5627, SCH5636.

### Dashboard banner
An `ErrorBanner` widget on the live dashboard content shows:
- Info banner when hwmon is not detected (suggests checking the System State page)
- Warning banner when hwmon is detected but all headers are read-only
- Hidden when writable headers are available

### Controls page read-only labels
Non-writable hwmon headers show "(read-only)" suffix in the fan role member
editor, matching the existing GPU read-only pattern.

### Settings
- `show_hardware_guidance: bool = True` — persisted in `app_settings.json`

## Implementation: Cooling Hardware Readiness — Hardware page (merged Readiness + Super-I/O — DEC-207)

The merged **Cooling Hardware Readiness** report (both the go/no-go readiness
checklist and the former standalone Super-I/O detection) is the redesign's
**Hardware** page (`pages/hardware_page.py`, DEC-212). It first appeared in GUI
v2.13.0 as the Diagnostics ▸ Readiness tab (`ui/widgets/cooling_readiness_view.py`,
retired) with the Super-I/O tab folded in; the redesign promoted it to its own
page and the standalone view widget was removed (its rendering now lives in
`hardware_page.py`). The page fetches everything in one request from the daemon's
combined `GET /inventory/hardware-readiness` (daemon ≥ v2.11.0), which serves a
single shared, coalesced hardware-assessment scan (the older `/inventory/readiness`
+ `/inventory/superio` endpoints remain as compat readers over the same snapshot).
Off-thread via `_HardwareReadinessWorker`; on a pre-v2.11.0 daemon the route
`404`s and the page shows an "unavailable" state.

Five sections, most-actionable first (`Hardware_*` object names):
1. **Overall readiness summary** — a `Hardware_Pill_verdict` rollup on the
   *Hardware Readiness Checklist* section header. It is **not** a separate banner and
   **not** a page-wide verdict: it is visibly scoped to that checklist, which is the
   Hardware page answering its own narrower question (is the hardware/driver stack
   set up for fan control?) rather than the machine's overall health — see `SSN-i`.
   Beside it: the top next step (`rollup.top_summary`), last scan time (from
   `scanned_age_ms`), one "Refresh hardware assessment" action (`refresh_requested`
   → a forced daemon scan), and a read-only note. `hardware_view._VERDICT` has
   **two** words, not three — `HARDWARE READY` (daemon `overall` of `ok`/`info`) and
   `HARDWARE NEEDS ATTENTION` (`warning`/`critical`); "Not ready" is not a state this
   page can render. Sections 1 and 3 are one `Hardware_Card_checklist` card, not two.
   **Both the section title and the verdict word say "Hardware" on purpose**
   (DEC-379): they used to read *System Readiness Checklist* and a bare `READY`,
   against the System State page's `SYSTEM READY` — two different questions a glance
   apart in near-identical words, which is what `SSN-i` was raised about. The two
   surfaces' verdict vocabularies must stay disjoint; a test asserts it
   (`tests/test_readiness_report_ssn_l_g48.py`).
2. **Recommended actions** — the actionable findings (critical → warning → info),
   each an actionable card with impact chips, a primary action button
   (`action_requested`), and a "Learn how" doc link. Actions route (in
   `hardware_page._route_action`) to a cross-page deep-link (`open_preferred_sensors`
   → the Settings page's Preferred Sensors card), an in-surface scroll to the
   Super-I/O section on this page, or a jump to the System State page (PWM verify,
   `open_system_state`) or the Overview page (sensor table, `open_overview`).
   The pure code→action / doc / group mapping lives in `ui/cooling_readiness.py`.
3. **Hardware checks** — the complete checklist in compact grouped rows (Temperature
   monitoring / Fan monitoring and control / Super-I/O and kernel support / Sensor
   configuration); passing checks stay one calm line.
4. **Super-I/O details** — per-chip driver detection with copy-paste module-load
   commands (mono label + "Copy command"; the page never runs it) and the measured
   liability note.
5. **Advanced detection** — a collapsed section hosting the opt-in active port probe,
   behind an explicit confirmation (`probe_requested`); results update only this
   section (`set_superio`).

Security boundary preserved: daemon strings render `PlainText`; only GUI-authored doc
links are `RichText`. Doc links use the existing `doc_url`/`doc_title` mechanism into
`docs/24_Cooling_Hardware_Readiness_Guide.md`. The daemon (DEC-207) guarantees
ordinary hwmon chips (amdgpu/k10temp/nvme/spd5118) are never listed as Super-I/O, so
the section shows a concise result rather than a card per device.

## Implementation: Validation sessions (DEC-317, AIO-MB Phase 5 — backend only)

**Phase 6 (DEC-318, GUI v2.56.0) shipped that UI.** The session panel is
`ui/widgets/validation_session_dialog.py`, launched from the Hardware page's *Hardware
Diagnostics* section. One dialog serves **all three** session kinds — validation, lifecycle
recording and, since DEC-335, thermal observation — because Phase 5 made them one engine
with a `kind` discriminator, and a dialog per kind would be the duplication the brief
forbids. (It said "both session kinds" until `P8-bj`; the reason is stated rather than the
count, because the reason is what stays true when a kind is added.) It shows the live
status, the per-member telemetry table, a row per orchestrated diagnostic
as it completes (DEC-337) and the findings summary; offers Mark Event / Stop & Save / Stop &
Mark Cancelled; records external measurements; and exports CSV and JSON through the Qt-free
serializers below, from one `Export` menu button since GUI v2.65.1.

**A session can also stop itself, from GUI v2.66.0 / daemon 2.43.0 (DEC-338).** The start
form carries *"Stop the session automatically when the diagnostics finish"*, which sends
`stop_when_diagnostics_complete` on `POST /validation/session`; the daemon's orchestrator then
finalises the session when its walk completes. Three GUI rules go with it, and each exists to
stop the dialog claiming something the daemon will not do:

- **Gated on `control.validation_auto_stop`, and hidden rather than disabled when absent.** An
  older daemon parses and *drops* the unknown request field instead of rejecting it, so it
  answers `200` and then records to the two-hour cap — probing cannot distinguish the two.
- **Unavailable until a diagnostic is ticked**, with a hint saying so. The daemon rejects the
  flag with an empty `diagnostics[]` (`400 validation_error`), because no orchestration task is
  spawned for an empty one and there would be nothing to carry the terminal stop.
- **Pre-ticked for the `validation` kind only** — that kind exists for its diagnostics, while
  lifecycle and thermal recordings are passive by design.

The intro's end-condition sentence renders the **echoed** `stop_when_diagnostics_complete`
while a session of ours is recording, and the checkbox otherwise (once a session finishes the
options form is editable again, so the sentence is describing the run being composed next).

**The two stop buttons differ only in the recorded state, not in what is kept.** They were
"Stop" and a `danger`-styled "Cancel Session" until GUI v2.65.1, which was false in both the
styling and the wording: `cancel()` is `finish(STATE_CANCELLED)` and finalises exactly as
`stop` does — same findings, same samples, same persistence (`P8-bc`).

**The chart was deferred, and DEC-335 discharged the deferral.** This paragraph led with
"charts remain deliberately absent" until `P8-bj` — stale from the moment Batch 3a landed,
and contradicted by its own next sentence. The brief's own guidance is "do not make graphing
mandatory" and "a stable tabular implementation is preferable"; `TimelineChart` is coupled
to live `AppState` history and cannot render a session's `samples[]` array without a new
plot. That was recorded as deferred work and is **discharged by DEC-335**: Batch 3a adds
`SessionTimelineChart`, which takes a Qt-free trace built from the session's own samples
instead of reaching for `HistoryStore`. `TimelineChart` is still not reusable for this and
was not reused. The tables remain primary — §14's "graphing is not mandatory" is honoured
by the chart living in a disclosure and drawing nothing when there is no series.

What Phase 5 built and Phase 6 consumes:

- `api/models.py` — `ValidationSession` and its parts, `parse_validation_session`,
  `parse_validation_session_summary` (the miniature on `/status` + `/poll`), and
  `parse_validation_session_index`.
- `api/client.py` — start / read / stop / cancel, `add_validation_marker`,
  `add_validation_measurement`, `validation_sessions`, `validation_session_by_id`. All
  capability-gated on `control.validation_sessions`; nothing is clamped or defaulted
  client-side.
- `services/validation_view.py` — the Qt-free VM: finding rows, evidence rows and
  per-member telemetry ranges.
- `services/validation_export.py` — the Qt-free serializers: the JSON session document, and
  CSV for samples, events and findings. They return **text**; the save dialog is Phase 6's.

Three presentation rules the VM already encodes, which a Phase 6 renderer must not undo:

1. **`unavailable` and `not_tested` are neutral, never error-styled.** Hardware that does
   not expose a capability has not failed, and a diagnostic nobody ran has not failed
   either. A red row would tell the user their cooler is broken when nothing of the sort was
   established.
2. **A possible device-side control is `observed` evidence, not a fault.** Reporting working
   motherboard PWM control as a failed write is the specific outcome the classification
   exists to prevent.
3. **An unrecognised finding id or result token renders humanised, and an unknown state maps
   to a muted tone** (the 273-i rule). A newer daemon's token must not make evidence vanish,
   and must not paint a red row on a GUI that has not learned the word.

## Nice-to-have later
- background self-checks
- one-click diagnostics redaction
- direct save of API snapshots
- daemon restart integration if safe and supported
- real-time journal tailing (follow mode) via background thread
- python-systemd native journal access (eliminates subprocess overhead)


## Thermal observation, steady state and startup behaviour (DEC-335, Batch 3a)

Since **DEC-335** (daemon >= 2.41.0, gated on `control.thermal_observation`) the Hardware page
offers a third session action, **Thermal Observation**, beside Startup / Lifecycle Recording and
AIO Validation. It is the same engine and the same dialog — a third `kind`, not a third
implementation.

**Control-OFC never starts, stops or controls the workload.** §2 and §11 forbid launching a
stress or benchmark tool, and the dialog says so where the user starts the run rather than
burying it in help. Nothing in the observation drives a fan or a pump; it only watches.

What it records, beyond an ordinary session: CPU package power (from a CPU chip's hwmon power
attribute, else the powercap RAPL counter), GPU power where exposed, and — at finalisation —
a steady-state verdict over the control temperature and a per-member startup fingerprint.

Three honesty rules the UI enforces, each of which the spec states as a prohibition:

- **An unknown power is a dash, never `0 W`.** Several machines expose no CPU package power at
  all (measured: `k10temp` publishes none), so "not known" is the common case and a zero would
  claim an idle processor.
- **"Not established" is a statement about the observation, not about the cooler.** A run
  stopped early and a loop that genuinely cannot stabilise produce the same verdict, and nothing
  renders either as a fault.
- **A high startup RPM with a honoured duty is a device behaviour.** The commanded duty and its
  readback are shown together beside the peak RPM, because the two agreeing is exactly what
  rules out a control fault.

**Component-isolation templates are guided.** A stage names the duty to set — through the
Controls page's existing floor-clamped manual override — and marks the timeline when the user
confirms it. There is no daemon-driven stepping and no new PWM write path. Stepping is refused
while thermal protection is active or while no temperature can be read; that client-side gate is
the only place a guided workflow can refuse, and it does.

**An opt-in startup recording** (`[startup] record_startup`, off by default) lets the daemon
capture the startup window without an operator present, because a hand-started session cannot
reach it. It never blocks anyone: starting a session takes over immediately, the partial
recording is still saved, and auto-records are retained separately so they cannot displace
sessions made by hand.


## PWM Test Report (DEC-404, DEC-408 — GUI 2.81.0; DEC-409 — GUI 2.82.0)

A whole-machine assessment the user starts from **Hardware ▸ PWM Test Report…**, in its own
non-modal, single-instance window. It answers "what can Control-OFC actually observe and verify
about this cooling setup?", and its value is a report that never overstates the evidence: there
is **no global verdict**, and "not tested", "unavailable" and "inconclusive" are ordinary
answers, not failures.

**The GUI orchestrates; the daemon performs.** The report is a sequence of the daemon's own
diagnostics — `POST /hwmon/{id}/verify`, `/discover-control-path`, `/characterize` and
`/stall-probe` — one at a time, each preceded by `GET /diagnostics/preflight`. Every write, guard
and restore stays the daemon's. The runner (`services/pwm_report/runner.py`) is a pure state
machine; its Qt controller (`ui/pages/pwm_report_controller.py`) belongs to the Hardware page, so a
run outlives its window.

### Pages

0. **Reports** (GUI 2.82.0, DEC-409) — where the window opens unless a run is in progress or has
   just finished. Saved reports newest first (started, board, tests completed, state, where it
   came from), with **Open**, **Export**, **Delete…** (after a confirmation; D-b), **Compare**
   (exactly two), **Open a report file…** and **New report**. An unreadable file in the reports
   folder is listed as *Unreadable* (the reason in the tooltip) so it can be deleted. The report
   a run is writing is listed *In progress* and cannot be opened, exported, compared or deleted.
1. **Scope.** One row per channel the daemon reports (hwmon headers, OpenFan channels, GPU fans)
   in stable-id order, with a checkbox per test. Pre-selected: the **PWM control test** and **tach
   pairing** on writable headers whose fan reads RPM > 0. Never pre-selected: the **full sweep**
   and the **stall probe**. A test that cannot be offered is disabled with its reason as the
   tooltip — read-only header, OpenFan/GPU (reported read-only), a daemon without the capability
   (`daemon_supports(...) is True`), a daemon below **2.52.0** for the sweep and pairing (their
   settling and noise figures were wrong before DEC-405), and the probe's envelope: never a
   pump-protected header, never `cpu_fan`, only `chassis_fan`/`radiator_fan`, and only with a tach.
2. **Your setup** (optional). The cooler's model and pump-switch position, and per header what is
   connected, how many fans share it (> 1 = splitter or hub), the BIOS header mode and notes.
   Remembered per stable header id (`hardware_notes`, `cooler_notes`) as USER_METADATA; blank is
   recorded as *not supplied*.
3. **Review & consent.** The plan in plain words per header and test, the safety paragraph, a
   general consent checkbox when any selected test writes, and — for each selected probe — its
   own "I'll stay at the machine" confirmation. Start stays disabled until all are ticked and no
   start refusal applies (demo mode, disconnected, thermal protection active, another diagnostic
   running, a validation session recording, a PWM verify or *Verify All Writable* sweep started on
   System State still running). The last one is the GUI's own record, not the poll's
   `verify_active`: that reads false in the gap between two of a sweep's verifies, so the sweep's
   remaining headers would otherwise be written during the run (`PTA-d`, DEC-410). The refusals
   are re-read when Start is pressed, so one that arose since the last poll is shown then.
4. **Run.** The step list with its statuses, the header under test's live command, readback and
   RPM, the thermal state, elapsed time and an estimate of what is left, and **Cancel run**.
5. **Report.** State, summary, *Needs attention*, *Observations*, *Restoration* (with **Re-apply
   profile** when a failure is one the profile fixes), a section per channel (facts, the test
   matrix, findings, a PWM→RPM chart for a sweep and for the probe, the probe's points table),
   *Not tested*, environment, configuration, the provenance legend and the evidence (the daemon's
   raw answers, filled on expand). **Export** is a menu: JSON, Markdown, HTML, CSV (§ Exports).
   **Re-apply profile is offered only for the report that has just finished in the window**
   (S5-7): a report reopened from the list or a file shows a note instead, because the button acts
   on the machine now and the report's evidence is from then.
6. **Compare** (DEC-409) — two reports, earlier first (§ Comparison); exportable as Markdown or
   HTML.

### What a run does

- **Baseline snapshot**, read-only: `/capabilities`, `/status`, `/fans`, `/sensors`,
  `/hwmon/headers`, `/diagnostics/hardware`, `/profile/active` and the active profile's document,
  `/inventory/cooling-devices` and `/diagnostics/control-path` — each answer kept verbatim with
  its status (a 404 from an older daemon is recorded, not dropped).
- **Per header, in stable-id order: verify → pairing → sweep → probe.** A `blocked` preflight is
  *not tested* with the daemon's rows kept; a refusal (409 busy, a thermal or retryable refusal,
  an ineligible header) is *not tested* with the daemon's words — never a hardware verdict. The
  sweep asks for 20, 30 … 100 %, both directions, a 20 s stability dwell, and **no settle**: the
  daemon picks it and floors a pump's walk; the report records what the run echoed. After each
  test the runner waits 3 s (one engine tick + two polls) for DEC-382's hand-back.
- **Run-level stops:** thermal protection leaving `normal` cancels the running test and ends the
  run `aborted`; losing the daemon ends it `interrupted`; Cancel (or closing the window, after a
  confirmation) sends `DELETE` and marks the rest *cancelled by you*; quitting the application
  cancels synchronously and saves the report `interrupted`.
- **Final snapshot** and the restoration checks (below).
- **The trace:** every fan (RPM, readback, command, mode) and every temperature, once a second
  from the app's own poll, stored column-wise; it stops at three hours and says so.

### Evidence rules

- **Scoped claims.** A verify finding names its one test duty; a sweep finding its tested range;
  a declared splitter scopes every RPM claim to the one tach-reporting fan; a pump-protected
  header is "below 30 % not tested, by design"; an empty header is "no fan detected (inferred)",
  its stall flag explained as not a stall (`PTR-k`); a device override is an observation; a
  firmware-controlled header is a state.
- **Provenance** on every finding: measured, commanded, derived, user-supplied, device-reported,
  unverified. The daemon's `unknown` reads **"Inconclusive"** in the report only.
- **Always listed, never implied:** daemon stop/start and firmware fallback, the `pwmN_enable`
  mode matrix, suspend/resume, a pump below 30 %, coolant temperature, OpenFan/GPU active tests,
  closed-loop thermal response (use a Thermal Observation session), and the nine properties
  motherboard hwmon cannot establish (`provenance.UNVERIFIABLE`).
- **Exposure** (derived): the lowest duty the active profile can command on each tested member,
  in the daemon's tuning order (offset → floor → stop-snap; a pump's hard floor is never snapped),
  against the lowest duty a sweep saw turning or the probe's stall/restart duties. A Mix or Sync
  curve is reported as unbounded.

### Restoration (measured, not assumed)

For every header a test touched: `pwm_enable_mode` against the baseline. For every profile
member: readback against command within the daemon's 2-point tolerance (the read-only D3 check,
any daemon). For every test: its `restore_outcome`. Then no override the run did not start with,
the active profile by id and content hash, the thermal state, and — on daemon 2.53.0+ — any
`duty_corrections` during the run (evidence of a second writer) and any header left
`duty_not_holding`. A missing final snapshot is *unverified*, never *passed*.

### The report file

`~/.local/share/control-ofc/reports/pwm-report-<UTC>-<id>.json` (`paths.reports_dir()`), compact
JSON, schema version 1, saved after every step and never deleted automatically. Reopened with its
**own 16 MiB limit** — the shared 4 MiB import cap cannot hold a three-hour trace (a measured
4.1 MB on a 19-fan / 24-sensor machine). A file left `in_progress` by a crash is repaired to
`interrupted` the next time the report is opened, with its findings re-derived.

### Opening a report from elsewhere

**Open a report file…** loads a report through the same 16 MiB limit and schema check, repairs an
`in_progress` one in memory only, and shows it read-only. It is **never copied into the reports
folder** (S5-6): it sits in the list for the rest of the session, marked as a file, can be opened,
exported and compared, and cannot be deleted from here. A file picked from the reports folder
itself opens as the saved report it is.

Such a file is untrusted, so the schema check also **bounds the work** it can cause: at most 1,024
channels, 4,096 steps and 65,536 findings, a trace of at most three hours, and every trace series
exactly as long as its timestamps (which the recorder guarantees). An export's size is therefore
proportional to the file, never to samples x series. A file nested too deeply to parse is refused
like any malformed one, and a failure while rendering or exporting one is shown as a message. The
Reports list parses each saved file once per session and reuses the result while its inode,
modification time and size are unchanged.

### Exports (DEC-409)

All four are generated from the same view models the window renders (`view.build_report_view`,
`compare.compare_reports`), so an export never says something the window does not.

- **JSON** — the document, byte-identical to the saved file.
- **Markdown** (`export_markdown.py`) — summary, attention, observations, restoration, a channel
  table, not tested, environment, configuration and the legend. Every dynamic string has every
  ASCII punctuation character backslash-escaped and its line breaks folded, so no label, alias or
  note can become a link, an image, raw HTML, a heading or an extra table column. No length cap
  (S5-11): the first lines say to attach a long file rather than paste it (an issue body holds
  65,536 characters).
- **HTML** (`export_html.py`, `svg_chart.py`) — one self-contained file: inline CSS and SVG, no
  script, no external asset, no web font, and a `Content-Security-Policy` of `default-src
  'none'`. Every dynamic string is HTML-escaped (DEC-106). Colours are CSS variables filled from
  the theme tokens — the default dark theme, and the bundled light preset under
  `prefers-color-scheme: light`. Charts are drawn to scale: each sweep (falling and rising legs
  never joined), each probe (down from 20 %, then back up), and (S5-3) one trace chart per
  **tested** header (RPM, and readback on a right-hand % axis) plus one of the **hottest
  `cpu_temp` sensor at each second**, with any sample whose reported `thermal_state` is not
  `normal` shaded. A gap stays a gap. A series over 2,000 points keeps the first, lowest,
  highest and last point of every pixel column, and the page says it did. The evidence (S5-4)
  follows in a closed `<details>`.
- **CSV** (`export_csv.py`) — a folder, and one file per table that has rows (S5-2):
  `…-sweep.csv` (one row per walked point; columns derived from `CharPoint`, nested `stability.*`
  and `estimated_physical_rpm.*` flattened), `…-probe.csv` (one row per held duty, from
  `ProbePoint`) and `…-trace.csv` (one row per second, one column per `<stable id> <quantity>`).
  RFC 4180 with CRLF. `None` is an empty cell, never 0. A text cell starting `=`, `+`, `-`, `@`,
  tab or CR is prefixed with `'` so a spreadsheet does not run it; numbers are never touched. The
  confirmation names the files written and any table that had no rows.

### Comparison (DEC-409)

`services/pwm_report/compare.py`, a Qt-free view model. Two reports are ordered by `started_at`;
every difference is *later minus earlier*.

- **Channels pair by stable id only.** An id in one report only is listed on its side. A
  *possible rename* — same `hwmon:chip:device:pwmN`, different label — is listed as an inference
  and **never paired**, so no measurement is attributed to a header it may not be.
- **Five categories, each difference in exactly one:** hardware and wiring (sensor chips, the
  user's cooler and per-header facts), environment (GUI/daemon/API, both kernels, board, BIOS,
  Python/Qt, loaded modules), configuration (profile id, name and content hash, trip and release
  points as reported, cooling devices, each paired header's role, role source, floor, stop
  permission, writability and cooling device), measured response, and not comparable. Values
  that match are counted, not listed.
- **A test is compared only like for like.** It is *not comparable*, with the reason, when it ran
  in one report only, did not complete in either, was asked for or echoed different parameters
  (the request, plus the sweep's points/settle/bidirectional/dwell, pairing's delta/cycles/window,
  verify's test duty), or when the thermal state at the start differed.
- **Response rows:** verify result and RPM at the test duty; pairing relationship, confidence and
  strongest tach; the sweep's summary figures and every duty present in both walks, each point's
  settled median (else its last reading) **beside each run's own spread** (min–max, CV, samples);
  the probe's outcome, stall and restart duties, baseline RPM and tach refresh.
- **Start conditions are shown, never judged** (S5-5): the thermal state and the hottest CPU
  temperature at the start, both values and the difference. No threshold makes a warmer start
  "not comparable", nothing is called significant, and there is no verdict.

### While a run is active

It holds the daemon's one diagnostic slot, so the Hardware page's per-header Test / Characterise
/ Discover buttons, its session buttons and each cooling-device card's *Characterise Pump* and
*Start Validation*, and System State's Test PWM Control, Verify All Writable and Characterise, are
disabled with the reason. The GPU fan buttons stay enabled, as do a device card's *View Headers*,
*Edit Configuration* and *Forget Device*, which run nothing.

The buttons are one gate; the methods behind them are the other (`PTA-b`, DEC-410). The daemon has
no notion of a report run and its slot is **free between two steps** (the 3 s hand-back wait), so
any diagnostic that reaches it then is accepted. Opening characterisation or a session window is
refused mid-run, and a session window opened *before* the run — it is modeless, so it stays open
— has its Start refused with the reason in its status line, for every session kind.

### The duty-drift card on System State (DEC-408, daemon ≥ 2.53.0)

A header the daemon has stopped correcting (`duty_not_holding`, DEC-406) raises a condition card —
one per header, one per episode (fingerprinted by the correction count at the give-up, so a
dismissal cannot hide the next episode). It counts toward "N ACTION REQUIRED" and appears in the
pop-out Full Report, because it enters `detect_readiness_problems` like every other condition.
Corrections that held are a line in the Interference Monitor — a reading, never an alarm — and
the monitor's headline then says "No BIOS/EC Reclaim Detected" rather than "No Interference
Detected". A disconnect clears the card: with no daemon to ask it is no longer a current fact.
