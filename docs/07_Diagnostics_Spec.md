# 07 — Diagnostics Spec

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
Diagnostics helps the user understand:
- whether the daemon/API is reachable
- whether controllers are available
- whether sensors are fresh
- whether writes are possible
- what the last errors were
- what can be exported for support/debugging

This page must feel intentionally designed, not like a raw log dump.

## V1 diagnostic sections

### 1. Overview
Summary cards for:
- overall daemon status
- OpenFan availability
- hwmon availability
- thermal state
- last error summary

### 2. Connection and daemon health
Show:
- daemon version
- API version
- IPC transport
- overall status
- subsystem freshness/age
- health reasons if provided

### 3. Controller and device discovery
Show:
- OpenFan present / absent
- channel count
- write support
- hwmon present / absent
- discovered controllable headers
- whether RPM support is available

### 4. Sensor health
A rich diagnostic table of every sensor the daemon reports, designed to
answer "what is this sensor, what is it doing, and is it reliable?" at a
glance — without forcing the user to hover every cell (DEC-117).

**Header summary line** above the table:
`Sensors: N total · X CPU · Y board · Z GPU · W disk · K stale · J low-confidence · V unavailable · M hidden`
(empty kind buckets are suppressed; the line collapses to `Sensors: —` when
no sensors are reported.) The `V unavailable` bucket counts daemon-reported
`unavailable_sensors[]` entries (DEC-193) and is sourced from the status poll,
not the sensor table re-render.

**14-column table** (all visible by default; the last column hosts a per-row
"Details" button widget):

1. **Label** — sensor label reported by the kernel driver. Prefixed with `⚠ `
   for bogus-quirk sensors (e.g. ASUS NCT6776F CPUTIN) and `? ` for
   low-confidence classifications.
2. **Sensor ID** — stable `hwmon:<chip>:<dev_id>:<label>` identifier. Users
   need this to bind sensors to profile members.
3. **Source class** — pretty-printed classification from the sensor knowledge
   base (`CPU die`, `VRM`, `External probe`, `Board thermistor`, …). Unknown
   classes pass through verbatim for forward compatibility.
4. **Kind** — coarse daemon classification (`cpu_temp` / `mb_temp` /
   `gpu_temp` / `disk_temp`).
5. **Source** — daemon source subsystem (`hwmon` / `amd_gpu`).
6. **Chip** — kernel driver / chip name (`k10temp`, `nct6798`, …). Em-dash
   when missing.
7. **Driver type** — human label for `tempN_type` (`diode (3)`,
   `thermistor (4)`, `AMD TSI (5)`, `Intel PECI (6)`, em-dash when absent).
8. **Value (°C)** — current reading. When `crit_alarm` is asserted OR the
   live value has crossed the reported `crit_c`, the cell appends
   `⚠ ALARM` in `status_crit` colour.
9. **Trend** — smoothed change rate (`↑ +0.6 °C/s`); suppressed below
   ±0.1 °C/s to match the existing tooltip rule.
10. **Session min/max** — lowest and highest values observed since daemon
    start (`21.0 - 78.5 °C`).
11. **Age (ms)** — time since the daemon last polled this sensor.
12. **Freshness** — `fresh`/`stale`/`invalid`, paint-coloured.
13. **Confidence** — classification confidence (`High` / `Medium-High` /
    `Medium` / `Low`).
14. **Details** — per-row button opening the **Sensor Detail dialog**
    (`Diagnostics_SensorDetail_Dialog`).

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
Right-click → "Unhide sensor" reverses. The Diagnostics hide-list is
**local to this tab** by default; the **Mirror hidden to dashboard** button
in the header pushes the current hide-list into the shared
`SeriesSelectionModel` as a one-shot (so the dashboard chart hides the same
sensors). Subsequent diagnostics-side changes stay local until the user
mirrors again.

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
staleness warning and **no** dashboard banner or popup — Diagnostics is the only
surface. The panel is hidden entirely when none are reported (older daemons omit
the field). The header summary's `N unavailable` count is kept in step with this
panel from the same status poll.

### 5. Lease state (removed at 2.0.0 — DEC-165)
The daemon owns the hwmon lease internally as of 2.0.0; the GUI holds none, so the diagnostics
**Lease tab was removed**. Lease state is no longer a GUI-surfaced diagnostic. (Pre-2.0 this section
showed lease required / held / owner / TTL.)

### 6. Logs and events
Provide a readable log/event view for:
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
Troubleshooting tab since DEC-147) for hwmon re-enumeration; serial-controller
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

## Diagnostics UX rules
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
The Diagnostics > Event Log tab surfaces three closely-related but distinct streams. Confusing them is the original sin the DEC-111 rewrite cleared up:

| Surface | What it answers | Storage | Lifetime |
|---------|-----------------|---------|----------|
| Event Log (this tab) | What has the GUI been doing in this session? | In-process `collections.deque` (`MAX_EVENTS = 200`) | Session-only — cleared on GUI exit |
| Active Warnings (banner badge → dialog) | What is wrong **right now**? | `AppState.active_warnings` recomputed every poll | Cleared when the condition resolves or the user acknowledges |
| System Journal (snapshot button) | What happened across daemon restarts? | systemd journal, fetched on demand via `journalctl -u control-ofc-daemon` | Daemon-owned; persistent |

### EventLogView widget
Located at `src/control_ofc/ui/widgets/event_log_view.py`. Backed by a `QStandardItemModel` (one row per `DiagEvent`) wrapped in a custom `QSortFilterProxyModel` (`_EventFilterProxy`) that ANDs three filters:

- **Severity** — multi-select (`info` / `warning` / `error`) via checkable `QPushButton`s.
- **Source** — single-select `QComboBox`; populates dynamically from observed sources, starting with "All sources".
- **Search** — `QLineEdit` substring match against both message and source columns (case-insensitive).

Severity column foreground colours read from `active_theme()` on every repaint and on `refresh_theme()` (called from `DiagnosticsPage.set_theme`) so a theme switch picks up the new `status_ok` / `status_warn` / `status_crit` values without a restart.

Auto-scroll behaviour: the view follows the bottom only when the user is already at the bottom before the new event lands. Scrolling up pauses the follow; scrolling back down resumes it.

### Emitter contract
`DiagnosticsService.log_event(level, source, message)` is called from production services at *state transitions only*, never per cycle:

| Source | Emits when |
|--------|------------|
| `gui` | GUI start/exit; theme changed; manual override entered; demo mode activated; kernel warning acknowledged |
| `polling` | First connection established; disconnected (after a prior connect); daemon-reported active profile detected |
| `profile` | Activated/deactivated; profile load error |

Per-cycle work (every poll, every write attempt) must continue to use Python `logging` directly — the in-process event log is for breadcrumbs the user opens Diagnostics to see, not the daemon journal.

### Diagnostic Snapshots sub-section
The four on-demand detail buttons (Daemon Status, Controller Status, GPU Status, System Journal) live in a separate sub-section below the event log, writing to their own `QPlainTextEdit` (`Diagnostics_Text_snapshotView`). `Clear Log` only clears the event-log table; `Clear Snapshots` clears the snapshot view. Before DEC-111 both shared one `QPlainTextEdit` and Clear Log wiped journal blocks the user had just fetched.

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

## Implementation: Diagnostics theming (R34)

### Transparent labels
All labels inside Card frames use `background: transparent` inline style. This prevents opaque label backgrounds from conflicting with the Card class background across themes.

### CSS class usage
- Card title labels: `.PageSubtitle` class (bold section-header role, inherits theme size)
- Metadata/explanatory labels: `.CardMeta` class (smaller, secondary color)
- Status label in button row: `.CardMeta` class
- Collapsible section headers: `.CollapsibleSectionHeader` class (DEC-112) —
  body-sized + semibold, subordinate to `.PageSubtitle` card titles, theme-
  derived font size (no hardcoded px), chevron in the button text
- No hardcoded `font-size: Npx` on any Diagnostics label

### No inline font-size overrides
All font sizing is inherited from the global theme stylesheet via CSS classes. Changing the theme text size changes Diagnostics page text consistently.

## Implementation: Hardware Readiness (v1.1.0; dedicated Troubleshooting tab in v1.26.0 — DEC-124)

### Overview
The **Troubleshooting** tab (a dedicated Diagnostics tab inserted right after
Fans, DEC-124) presents the "Hardware Readiness" health report. It fetches data
from `GET /diagnostics/hardware` (daemon v1.2.0+) and presents a unified view of
hardware compatibility and driver status. The sibling **Fans** tab now shows
only the live Fan Status table.

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

### Layout: flattened health report (DEC-124, supersedes the DEC-115/DEC-116 card layout)
On its own **Troubleshooting** tab nothing competes with a fan table for
vertical space, so the readiness content is a flat, always-readable health
report rather than the deep accordion-in-accordion card of DEC-115/DEC-116.
Inside one `Card` frame (`Diagnostics_Frame_hwReadiness`), top-to-bottom:

- **Header action row** — the "Hardware Readiness" title, *Open Full Report ↗*
  (pop-out), *Rescan Hardware* (DEC-147: `POST /hwmon/rescan` — daemon-side
  re-enumeration after loading a sensor kernel module; a result line under the
  row reports the header count, notes that sensors refresh on the next poll
  cycle, and repeats the daemon's caveat that new fan-*control* hardware still
  requires a daemon restart; a successful rescan pushes the fresh header list
  through `AppState.set_hwmon_headers` and chains a diagnostics refetch), and
  *Refresh Hardware Diagnostics* (GUI-side refetch only).
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
- **Advisories** (`Diagnostics_Container_advisories`, DEC-158) — board/chip
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
  engine would silently re-assert its curve within seconds). The
  click handler re-checks that gate, a success clears the session's
  `gui_wrote_gpu_fan` flag (making the close-time auto-reset a no-op until
  the next GUI GPU write), and both outcomes land in the event log.
- **Liability disclaimer** (`Diagnostics_Label_readinessDisclaimer`, DEC-158) —
  one calm, persistent note at the bottom of the card (`REMEDIATION_DISCLAIMER`,
  `CardMeta` weight): the checklist fixes, advisory details, and chip guidance
  all describe kernel/driver/firmware changes applied at the user's own risk.
  Low-weight by design — heavy red styling is reserved for the real alerts above.

The sibling **Fans** tab holds only the live *Fan Status* table.

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
section may also carry a **persistent area** (`add_persistent_widget`,
DEC-115) — widgets between the header and the content that stay visible
regardless of collapse state. (DEC-124 retired the readiness card's use of this:
the verdict + alerts are now always-visible siblings, not a persistent area; the
feature remains available to other sections.) Because Qt's `QWidget.isHidden()`
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
- **Auto-fetch** — opening the Troubleshooting tab fetches `/diagnostics/hardware`
  once per session (guarded), so the verdict + checklist populate without a
  manual *Refresh* click.
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
down-arrow. The app is stylesheet-only (no `QPalette`) and ships no image
assets, and supports arbitrary custom theme colours, so a static asset cannot
follow the theme. `theme.combo_arrow_svg_path(color)` instead generates a tiny
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
- Info banner when hwmon is not detected (suggests checking Diagnostics → Troubleshooting)
- Warning banner when hwmon is detected but all headers are read-only
- Hidden when writable headers are available

### Controls page read-only labels
Non-writable hwmon headers show "(read-only)" suffix in the fan role member
editor, matching the existing GPU read-only pattern.

### Settings
- `show_hardware_guidance: bool = True` — persisted in `app_settings.json`

## Nice-to-have later
- background self-checks
- one-click diagnostics redaction
- direct save of API snapshots
- daemon restart integration if safe and supported
- real-time journal tailing (follow mode) via background thread
- python-systemd native journal access (eliminates subprocess overhead)
