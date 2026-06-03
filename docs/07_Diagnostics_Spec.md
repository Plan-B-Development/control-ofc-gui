# 07 — Diagnostics Spec

**Last updated:** 2026-05-07 (Spec doc — updated infrequently; refer to DECISIONS.md and CHANGELOG.md for current behaviour.)

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
- hwmon lease state
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
Show a list/table of sensors with:
- label
- kind
- current value
- age/freshness
- status
- issue text for stale/invalid cases

### 5. Lease state
If hwmon requires a lease, show:
- lease required
- held / not held
- owner hint
- TTL remaining
- whether GUI writes are currently possible

### 6. Logs and events
Provide a readable log/event view for:
- recent app events
- recent API failures
- validation errors
- profile/control loop warnings
- write denials/clamps
- lease failures

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
If the daemon does not expose a rescan/reconnect endpoint:
- refresh status
- explain that device rediscovery may require daemon restart
- optionally provide a user-facing note to that effect

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
- lease unavailable
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
| `lease` | Acquired; released; renewal failed after all retries (lost) |
| `control_loop` | Loop started/stopped; write-fail threshold crossed (count == 3); per-target recovery; lease lost transition |
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

## Implementation: Lease explanation (R34)

### Explanation content
The Lease tab includes a static explanation card above the live status card:

> **What is a lease?** A lease grants exclusive write access to your motherboard's fan headers (hwmon). Only one client can hold the lease at a time, preventing conflicting PWM commands from different tools.
>
> The GUI automatically acquires and renews the lease while controlling fans. The lease expires after 60 seconds if not renewed (e.g. if the GUI crashes), allowing other tools to take over.
>
> If another tool holds the lease, the GUI cannot write PWM values until the lease is released or expires. OpenFan Controller writes do not require a lease — only motherboard hwmon writes do.

### Status card fields
- Lease held/not held
- Lease ID (UUID or —)
- Owner hint (who holds it)
- TTL remaining (seconds)
- Required (yes/no)

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

## Implementation: Hardware Readiness (v1.1.0)

### Overview
The Fans tab includes a "Hardware Readiness" card above the existing fan status
table. It fetches data from `GET /diagnostics/hardware` (daemon v1.2.0+) and
presents a unified view of hardware compatibility and driver status.

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

### Layout: progressive disclosure (DEC-112, card-level collapse DEC-115)
The card is itself a single collapsible section (DEC-115): a top-level
`CollapsibleSection` titled "Hardware Readiness", **expanded by default**. Its
**persistent area** stays visible even when the card is folded; its
**collapsible body** holds the detail and folds away so the live fan table can
rise. The body's readiness data is *further* grouped into nested collapsible
sub-sections so a problem board does not become a wall of text (the exact case
where it is read). Top-to-bottom:

- **Persistent (visible even when the card is collapsed):** the readiness
  **verdict banner** (DEC-113) + "Open Full Report" button, and the
  **critical-alert stack** — module collisions, module conflicts, dual-chip
  warning, vendor quirks, ACPI conflicts, and the BIOS-interference headline.
  Each alert is individually visibility-gated, so the stack collapses to
  nothing on a healthy system. Per NN/g accordion guidance, essential warnings
  are kept *outside* collapsed panels — here, in the persistent area.
- **Collapsible body — summary:** the readiness summary line and board
  identity.
- **Collapsible body — detail sub-sections** (`CollapsibleSection`, all
  collapsed by default): *Detected hardware* (chip + kernel-module tables),
  *BIOS interference detail* (per-header revert rows + footnote),
  *Thermal safety & GPU*, *Guidance & documentation*, and *PWM control test*
  (verify combo, Test PWM Control, Verify All Writable, progress + result).
- **Always visible (outside the card):** the *Refresh Hardware Diagnostics*
  button below the card, and the live *Fan Status* table in the bottom
  splitter pane.

Default expand state is static (not persisted across launches): the card opens
expanded. `_populate_hw_diagnostics` **force-expands the card** whenever
`readiness_verdict` reports a problem (any non-`SuccessChip` verdict), and the
card never auto-collapses — so a user who folds a healthy card is respected,
but a problem board always shows its detail and "To fix" guidance. The
*BIOS interference detail* sub-section likewise **auto-expands** on a non-zero
revert count. The verify controls and their result labels share one
sub-section, so reaching the buttons necessarily expands the section that
shows the outcome. Because the verdict + alert stack are persistent and the
card force-expands on a problem, **warnings are never hidden behind a
collapse**.

`CollapsibleSection` (`ui/widgets/collapsible_section.py`) is a first-party
widget (DEC-112 D1): a flat `QPushButton` header (chevron rendered in the
button text so it inherits the themed `.CollapsibleSectionHeader` colour, and
so the text left-aligns — `QToolButton` ignores stylesheet `text-align`)
toggling a content container. Multiple sections may be open at once (unlike
`QToolBox`). The toggle is instant (no animation) for deterministic tests. A
section may also carry a **persistent area** (`add_persistent_widget`,
DEC-115) — widgets between the header and the content that stay visible
regardless of collapse state; the Hardware Readiness card uses it for the
verdict + alert stack. Because Qt's `QWidget.isHidden()` reflects a widget's
*own* show/hide flag rather than an ancestor's collapsed state, the
visibility-gated labels keep working unchanged inside the sections.

### Readiness verdict, auto-fetch, "To fix", and pop-out report (DEC-113)
- **Verdict banner** — a prominent, always-visible one-line status at the top
  of the card, computed by `readiness_report.readiness_verdict(diag)`:
  `✓ System ready — N headers, M writable · thermal safety <state>`
  (`SuccessChip`) or `⚠ K issue(s) need attention …` (`WarningChip` /
  `CriticalChip`). It fills the otherwise-empty collapsed view with an
  at-a-glance answer. Problem detection lives in one place
  (`detect_readiness_problems`) so the verdict and the "To fix" guidance can
  never disagree; **info-level vendor quirks are FYI notes and are not counted
  as problems**.
- **Auto-fetch** — opening the Fans tab fetches `/diagnostics/hardware` once
  per session (guarded), so the verdict is populated without a manual
  *Refresh* click.
- **"To fix" guidance** — `build_fix_guidance_html(diag)` renders a remediation
  bullet per detected problem (ACPI, module collision, GPU `ppfeaturemask`,
  dual-chip, all-read-only, …) with a clickable doc link and a shared
  `REMEDIATION_DISCLAIMER`. It is **GUI-authored only** (no daemon strings), so
  it is safe as rich text with external links — sidestepping the DEC-106
  escaping requirement. The dual-chip warning carries the same disclaimer.
- **Pop-out report** — *Open Full Report ↗* opens `ReadinessReportDialog`, a
  themed, resizable `QTextBrowser` window with the complete report (summary,
  detected-hardware table, thermal/GPU, and the "To fix" block). Daemon strings
  **are** HTML-escaped here. Link colour is set inline per anchor (the app-wide
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
- Info banner when hwmon is not detected (suggests checking Diagnostics → Fans)
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
