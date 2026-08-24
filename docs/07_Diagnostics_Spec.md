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
  warnings, thermal safety & GPU, and the PWM/GPU verify + Rescan Hardware +
  Open Full Report actions.
- **Hardware** (`pages/hardware_page.py`) — the daemon `/inventory/readiness`
  go/no-go checklist, Super-I/O chip detection, and the opt-in Probe Ports
  action.
- **Logs** (`pages/logs_page.py`) — the event-log stream + filters, diagnostic
  snapshots, and Export Bundle.

These pages must feel intentionally designed, not like a raw log dump.

## Overview page

The **Overview** page answers "is the daemon reachable, what hardware was
discovered, and are the sensors fresh?" It carries what the retired
Diagnostics ▸ Overview / Connection / Controller / Sensors sub-views showed,
plus the live fan-status table.

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
`Sensors: N total · X CPU · Y board · Z GPU · W disk · K stale · J low-confidence · U unavailable · M hidden`
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
│ SYSTEM HEALTH OVERVIEW            (full content width)       │
└──────────────────────────────────────────────────────────────┘
                          ↕  DEC-234 handle
┌───────────────────────────────────────┬──────────────────────┐
│ HARDWARE REGISTRY               ~75%  ┊  INTERFERENCE MONITOR │
│                                       ┊──────────────────────│
│                                       ┊  SAFETY & GPU LIMITS  │
└───────────────────────────────────────┴──────────────────────┘
> Advanced actions                        (fixed, below the handle)
```

Health carries the page's densest content — each finding is a severity caption,
a title, a description, an HTML detail box and a doc button — so it gets the
whole width. The two status cards moved into a stacked sidebar beside the
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

## Logs page
The **Logs** page provides a readable log/event view for:
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
The daemon exposes `POST /hwmon/rescan` (surfaced as *Rescan Hardware* on the
System State page since DEC-147) for hwmon re-enumeration; serial-controller
reconnection remains daemon-automatic (5× backoff + runtime reconnect mode),
so no GUI reconnect button exists:
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
- Show subsystem `reason` text from daemon alongside age (e.g., "readings fresh", "readings stale")
- Include an explanatory note: "Age = time since daemon last polled this hardware subsystem"
- Show daemon uptime when available
- Do not force subsystem ages to match — they reflect different I/O paths

### Freshness thresholds (daemon-defined)
- **OK**: age <= 2 × expected interval (default: <=2000ms for 1s interval)
- **WARN**: age > 2× and <= 5× interval
- **CRIT**: age > 5× interval or never updated

## Implementation: Event log + diagnostic snapshots (DEC-111)

### Three distinct concepts
The Logs page surfaces three closely-related but distinct streams. Confusing them is the original sin the DEC-111 rewrite cleared up:

| Surface | What it answers | Storage | Lifetime |
|---------|-----------------|---------|----------|
| Event Log (the Logs page) | What has the GUI been doing in this session? | In-process `collections.deque` (`MAX_EVENTS = 200`) | Session-only — cleared on GUI exit |
| Active Warnings (banner badge → dialog) | What is wrong **right now**? | `AppState.active_warnings` recomputed every poll | Cleared when the condition resolves or the user acknowledges |
| System Journal (snapshot button) | What happened across daemon restarts? | systemd journal, fetched on demand via `journalctl -u control-ofc-daemon` | Daemon-owned; persistent |

### Event stream
The event stream lives on the Logs page (`pages/logs_page.py`) as the
`Logs_Table_events` table — the standalone `EventLogView` widget was retired in
the redesign. It renders one row per `DiagEvent` from the shared
`DiagnosticsService` deque (`event_appended` / `events_cleared`), filtered by
three ANDed controls:

- **Severity** — multi-select (`info` / `warning` / `error`) toggle buttons.
- **Source** — single-select `QComboBox`; populates dynamically from observed sources, starting with "All sources".
- **Search** — `QLineEdit` substring match against both message and source columns (case-insensitive) (`Logs_Edit_search`).

Severity foreground colours read from `active_theme()` so a theme switch picks
up the new `status_ok` / `status_warn` / `status_crit` values without a restart
(refreshed via the Logs page's `set_theme`).

Auto-scroll behaviour: the view follows the bottom only when the user is already at the bottom before the new event lands. Scrolling up pauses the follow; scrolling back down resumes it.

### Emitter contract
`DiagnosticsService.log_event(level, source, message)` is called from production services at *state transitions only*, never per cycle:

| Source | Emits when |
|--------|------------|
| `gui` | GUI start/exit; theme changed; demo mode activated; kernel warning acknowledged |
| `polling` | First connection established; disconnected (after a prior connect); daemon-reported active profile detected |
| `profile` | Activated/deactivated; profile load error |

Per-cycle work (every poll, every write attempt) must continue to use Python `logging` directly — the in-process event log is for breadcrumbs the user opens the Logs page to see, not the daemon journal.

### Diagnostic Snapshots sub-section
The four on-demand snapshots (Daemon Status, Controller Status, GPU State,
System Journal) each live in their own card below the event table on the Logs
page (`Logs_Card_daemonStatus` / `Logs_Card_controllerStatus` /
`Logs_Card_gpuStatus` / `Logs_Card_systemJournal`), writing to that card's own
`QPlainTextEdit` (`Logs_Text_daemonStatus`, …) via its own Refresh/Fetch button
(`Logs_Btn_daemonStatus`, …). `Clear Logs` (`Logs_Btn_clear`) only clears the
event-log table; the per-card snapshots are independent, so clearing the log can
never wipe a journal block the user just fetched — the original DEC-111 bug (one
shared `QPlainTextEdit` wiped by Clear Log) is structurally impossible now.

### Journal access
- Uses `subprocess.run()` with `--lines=100 --no-pager --output=short-iso`
- 5-second timeout prevents hangs
- Permission failure → message explaining `systemd-journal` group requirement
- `journalctl` not found → message explaining systemd dependency

### Snapshot widget
`QPlainTextEdit` with `setMaximumBlockCount(2000)` and a monospace font. The high cap is appropriate for journal pastes; the event-log table has its own 200-row cap that mirrors the deque.

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
- **Advisories** (historical objectName `Diagnostics_Container_advisories`; now
  folded into the `Hardware_Action_{code}` cards, DEC-158/DEC-212) — board/chip
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
- **Summary + board identity** — the readiness summary line and board identity.
- **Five flat detail sub-sections** (`CollapsibleSection`, all collapsed by
  default): *Detected hardware* (chip + kernel-module tables), *BIOS
  interference detail* (per-header revert rows + footnote — **hidden entirely
  unless a header reports a non-zero revert count**, DEC-116), *Thermal safety &
  GPU*, *Guidance & documentation* (chip BIOS tips / known issues + doc link),
  and *PWM control test* (verify combo, Test PWM Control, Verify All Writable,
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
  of the report, computed by `readiness_report.readiness_verdict(diag)`:
  `✓ System ready — N headers, M writable · thermal safety <state>`
  (`SuccessChip`) or `⚠ K issue(s) need attention …` (`WarningChip` /
  `CriticalChip`). It leads the report with an at-a-glance answer. Problem
  detection lives in one place (`detect_readiness_problems`) so the verdict and
  the issue checklist can never disagree; **info-level vendor quirks are FYI
  notes and are not counted as problems**.
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

Supported chip families: Nuvoton NCT679x, NCT677x, NCT6683, NCT6687;
ITE IT8688E, IT8689E, IT8696E, IT8686E, IT8625E, IT87xx (generic);
Fintek F71882FG, F718xx; SMSC SCH5627, SCH5636.

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
1. **Overall readiness summary** — a compact verdict banner (Hardware ready / Needs
   attention / Not ready) with the top next step (`rollup.top_summary`), last scan
   time (from `scanned_age_ms`), one "Refresh hardware assessment" action
   (`refresh_requested` → a forced daemon scan), and a read-only note.
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

## Nice-to-have later
- background self-checks
- one-click diagnostics redaction
- direct save of API snapshots
- daemon restart integration if safe and supported
- real-time journal tailing (follow mode) via background thread
- python-systemd native journal access (eliminates subprocess overhead)
