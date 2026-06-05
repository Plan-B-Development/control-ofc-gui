# Changelog

## [1.26.0] — 2026-06-05

### Changed
- **Diagnostics: new Troubleshooting tab.** The Diagnostics **Fans** tab now
  shows only the live Fan Status table; all Hardware Readiness content moved to a
  dedicated **Troubleshooting** tab (immediately after Fans). It is redesigned as
  a flattened health report — an always-visible verdict banner, an issue
  checklist (one row per detected problem with its one-line fix and a doc link,
  or "✓ No issues detected" when healthy), then on-demand detail sections —
  replacing the deep collapsible card. Safety warnings (the verdict and blocking
  alerts) are now always on screen rather than behind a collapse. (**DEC-124**)

## [1.25.0] — 2026-06-05

### Added
- **API-version-skew guard.** The GUI now compares the daemon's reported
  `api_version` against the contract version it was built against and shows a
  non-fatal dashboard banner (plus an explicit flag in the diagnostics support
  bundle) when they differ — so an out-of-lockstep package upgrade is visible
  instead of producing silent field-mismatch behavior. (**DEC-123**)

### Fixed
- **Daemon-disconnect robustness.** A daemon that dies mid-response (restart,
  OOM, SIGKILL) raises an httpx `RemoteProtocolError` / `ReadError` /
  `WriteError` that the API client did not map, so it escaped the polling and
  control-loop worker slots uncaught — freezing the dashboard or killing the
  write worker with no reconnect. These are now mapped to the retryable
  "daemon unavailable" path (clean disconnect + auto-reconnect), and a
  last-resort exception hook logs any future uncaught worker-slot error into
  the support bundle. (**DEC-122**)

### Internal
- Test suite no longer hangs on modal dialogs: an autouse fixture neutralizes
  blocking `QMessageBox` / `QFileDialog` / `QInputDialog` / `QDialog.exec`
  calls with safe (declining) defaults, and `pytest-timeout` is configured as a
  thread-method backstop.

## [1.24.0] — 2026-06-04

Intel discrete GPU (Arc) monitoring (**DEC-121**) — Battlemage (Arc B-series,
`xe` driver) and Alchemist (Arc A-series, `i915`) discrete GPUs are now
recognised. Pairs with **daemon v1.12.0**; an additive wire-contract change, so
a new GUI works with any daemon (an older one simply reports no Intel GPU).

### Added
- **Intel GPU temperature + fan-RPM monitoring.** Temps are grouped as GPU
  telemetry (package / VRAM / memory-controller / PCIe, per the kernel ABI), the
  GPU fan appears in the dashboard fan table and diagnostics, and an Intel GPU
  temperature is selectable as a curve **sensor** to drive other fans.
- **Diagnostics → Device Discovery** now shows an "Intel GPU" line, and
  Diagnostics → Fans reports the Intel GPU's read-only, firmware-managed state.
- Demo mode includes an Intel Arc B580 so the UI is exercisable without hardware.

### Notes
- **Intel GPU fan control is read-only.** There is no fan-control interface in
  the Linux `xe`/`i915` drivers — the card's fan is managed autonomously by
  on-card firmware. Intel GPU fans are therefore shown as read-only, are never
  offered as controllable curve members, and are never written. (Verified
  against the kernel hwmon ABI and linux-firmware.)
- Only the Arc **B580** (`0xE20B`) maps to a specific model name today; other
  Intel discrete GPUs display as "Intel D-GPU" until an authoritative
  device-ID → name mapping is confirmed.

## [1.23.0] — 2026-06-04

GPU fan active verification (**DEC-120**) — a "Test GPU Fan Control" diagnostic
at parity with the motherboard-fan "Test PWM Control". Pairs with **daemon
v1.11.0**; an additive wire-contract change, so the control is shown only when
the connected daemon supports it (≥ 1.11.0) and a writable AMD GPU is present.

### Added
- **"Test GPU Fan Control" in Diagnostics → Fans.** Briefly drives the GPU fan
  to a test speed (biased upward so cooling is never reduced), waits ~6 s, reads
  back the applied PMFW curve / `pwm1` plus RPM, restores the prior state, and
  reports whether control actually works. Detects the silent failures static
  checks miss: `ppfeaturemask` bit 14 unset, an SMU firmware/driver mismatch, or
  a BIOS overdrive lock — each with GUI-authored "To fix" guidance.
- Verdicts distinguish a genuine no-effect from a normal zero-RPM idle and from
  the firmware OD_RANGE clamp, so a healthy idle GPU is never flagged as broken.

### Notes
- No lease is required (GPU writes never are). The control is hidden on daemons
  older than 1.11.0, and on a read-only GPU (no PMFW `fan_curve` and no
  `pwm1`+`pwm1_enable`).

## [1.22.0] — 2026-06-04

GPU fan floor removal and GPU detection hardening (**DEC-119**). Pairs with
**daemon v1.10.0**; the diagnostics additions are an additive wire-contract
change, so a new GUI works with any daemon ≥ 1.9.0 (it just shows less).

### Changed
- **GPU fans are never floored by the GUI, even in a mixed control.** The
  per-control minimum PWM is now applied **per member**: a GPU fan grouped
  with chassis/CPU fans idles to its own 0% floor in the same cycle that the
  chassis/CPU members hold their 20% / 30% stall-protection floor. GPU-only
  controls were already at 0%; non-GPU behaviour is unchanged. The GPU's PMFW
  firmware owns its real idle minimum (the ~15% OD_RANGE clamp + zero-RPM).

### Added
- **Diagnostics now explain the GPU firmware minimum.** The Thermal safety &
  GPU section shows the PMFW fan-speed range (e.g. "15% to 100%; values below
  15% are clamped by the GPU firmware, not the daemon"), the `fan_minimum_pwm`
  setting when present, and any kernel-regression advisories for the GPU.
- **"GPU present but driver not bound" detection.** The diagnostics page now
  reports an AMD GPU that exists in PCI space but has no `amdgpu` driver bound
  (blacklist, KMS failure, or vfio-pci passthrough) — previously such a GPU
  was completely invisible because it produces no hwmon node. The hint
  distinguishes "module not loaded" from "loaded but unbound".
- The control-card `Min: NN%` badge tooltip now notes, for mixed controls,
  that GPU members are not floored (the GPU firmware manages their minimum).

## [1.21.0] — 2026-06-04

Dashboard telemetry chart polish (**DEC-118**): smoother lines, a themed
hover-tooltip plate, and a per-series "latest value" marker. GUI-only — no
daemon or API changes; works with any daemon ≥ 1.9.0.

### Added
- **Per-series "latest value" markers.** Each visible series shows a dot at
  its most recent reading (the right edge of the chart), coloured to match
  the series — a current-value cue at a glance. Markers track the live value,
  clear when a series is hidden, and are removed when a series drops out of
  the chart.
- **Themed hover-tooltip plate.** The crosshair readout now paints on a
  background plate with a border, driven by two new theme tokens
  (`chart_tooltip_bg`, `chart_tooltip_border`) so it stays legible over busy
  gridlines/series and recolours live on a theme switch. Both tokens are
  editable in Settings → Theme Editor → Charts and ship in the Solar Light
  and Noctua Dark presets.

### Changed
- **Antialiased dashboard chart lines.** The dashboard's temperature and RPM
  series now render antialiased via pyqtgraph's per-item `antialias=True`, so
  only these curves are smoothed. The global pyqtgraph antialias config stays
  `False` (DEC-068), leaving other charts and the curve editor untouched and
  keeping the real-time render cost bounded.

## [1.20.0] — 2026-06-03

Expanded Diagnostics > Sensors tab into a 14-column diagnostic table with
a per-sensor detail dialog, inline quirk chips, and a local hide-list
(**DEC-117**). Pairs with **control-ofc-daemon v1.9.0**, which adds an
optional `thresholds` object to every `SensorEntry` in `/sensors` and
`/poll`.

### Added
- **Diagnostics > Sensors grew from 7 → 14 columns**, all visible by default:
  Label · Sensor ID · Source class · Kind · Source · Chip · Driver type ·
  Value · Trend · Session min/max · Age · Freshness · Confidence · Details.
  Information the GUI already had — source class, smoothed rate, session
  min/max, temp_type label — now lives on-screen instead of in hover
  tooltips only.
- **Header summary line** above the table: `Sensors: N total · X CPU ·
  Y board · Z GPU · W disk · K stale · J low-confidence · M hidden`.
  Lets users answer "is anything wrong?" at a glance.
- **Inline quirk/advisory chips**. The Label cell prefixes `⚠ ` for
  bogus-quirk sensors (e.g. the documented ASUS NCT6776F CPUTIN case)
  and `? ` for low-confidence classifications. The Value cell suffixes
  `⚠ ALARM` (in `status_crit`) when the daemon reports `crit_alarm` OR
  the live value has crossed the reported `crit_c`.
- **Per-sensor Sensor Detail dialog** (`Diagnostics_SensorDetail_Dialog`).
  Opens via the per-row "Details" button, row double-click, or
  right-click → "Open detail…". Shows the full classification
  description and every classification note (not truncated like the
  tooltip), board context, a Thresholds section with a headroom-to-crit
  indicator, and a clickable kernel.org driver doc link.
- **Sensor hide-list** persisted as
  `AppSettings.diagnostics_hidden_sensor_ids`. Right-click a row →
  "Hide sensor". Hidden sensors collapse into a `▸ N hidden sensors`
  toggle row at the bottom — they're never silently removed. The
  Diagnostics hide-list is **local to this tab**; the **Mirror hidden
  to dashboard** button in the header pushes it into the shared
  `SeriesSelectionModel` as a one-shot.
- **`thresholds` field on every `SensorReading`**. Parsed from the new
  daemon-side `thresholds` object on `SensorEntry`. Includes `max_c`,
  `min_c`, `crit_c`, `crit_hyst_c`, `emergency_c`, `emergency_hyst_c`,
  `lcrit_c`, `offset_c`, `alarm`, `max_alarm`, `crit_alarm`, `fault`.
  Older daemons (pre-DEC-117) omit the key and `parse_sensors` defaults
  it to `None` — no breaking change.

### Changed
- `parse_sensors` now hand-parses the nested `thresholds` object (the
  previous `_filter_fields`-only pattern dropped nested dataclasses).
  Empty/malformed threshold objects collapse to `None`. A non-list
  `sensors` key still raises (pre-existing safety contract).
- `_build_sensors_tab` rebuilt around a single `_SENSOR_COLUMNS` table
  so header labels, tooltips, and column ordering can't drift. Tests
  look up columns by header text rather than by hard-coded index.

### Tests
- 67 new tests across `tests/test_diagnostics_sensors_tab.py`,
  `tests/test_sensor_detail_dialog.py`, and
  `tests/test_sensor_thresholds_parser.py`. Existing
  `tests/test_diagnostics_enumeration.py` refactored to look up columns
  by header text (`_col(page, "…")`).

## [1.19.1] — 2026-06-03

Follow-up to the DEC-115 collapsible Hardware Readiness card (**DEC-116**),
fixing two reports that it presented "data for the sake of it." Also bundles
internal efficiency/robustness fixes from a full cross-stack audit (no
behaviour or daemon-contract change); pairs with **control-ofc-daemon v1.8.4**.

### Fixed
- **Collapsing the Hardware Readiness card now actually hides the detail.**
  DEC-115 pinned the verdict *and the whole alert stack* to the always-visible
  area, so folding a flagged board (e.g. one with a vendor quirk) left the
  alerts on screen and the collapse felt like a no-op. Informational alerts —
  dual-chip setup, vendor quirks, and ACPI conflicts — now live in the foldable
  body, so a collapse clears them. **Blocking** alerts (driver-module
  collisions/conflicts and the active BIOS-revert headline) and the readiness
  **verdict** stay visible even when folded, and a problem board still
  force-expands, so no safety warning is hidden.
- **The "BIOS interference detail" section no longer shows an empty body.** It
  rendered a header on every system but only had content when the daemon's
  watchdog had recorded a `pwm_enable` revert — which never happens on the vast
  majority of machines (and not on the demo board), so it expanded to nothing.
  The section is now hidden unless a header reports a real revert count, and
  re-hides if the interference clears.

### Changed
- **Hardware-diagnostics fetch no longer blocks the UI thread (audit).** The
  `GET /diagnostics/hardware` call now runs on a worker thread (mirroring the
  existing verify worker), so the once-per-session auto-fetch on the Fans tab
  and the manual Refresh no longer freeze the UI on a slow/contended daemon.
- **Unified the control-loop write-dispatch routing (audit).** The background
  write worker and the synchronous fallback now share one `_dispatch_write`
  helper, so target-id → daemon-call routing can't drift between them.
- **`/sensors/history` requests are URL-encoded (audit).** The sensor entity id
  is passed via httpx `params=` rather than interpolated into the path, so a
  sysfs label with query-special characters can't corrupt the request.

### Tests
- Persistent-vs-demoted alert placement, a collapse-hides-informational-keeps-
  blocking behavioural test, and BIOS-section hidden/shown coverage (empty,
  all-zero counts, and revert-then-clear).
- Audit fixes ship with tests: off-thread fetch slots + sync-fallback + worker
  lifecycle, the `_dispatch_write` helper (all routes + timeout-shape), and a
  special-character `/sensors/history` round-trip.

## [1.19.0] — 2026-06-03

Diagnostics → Fans usability + internal de-duplication (**DEC-115**), bundled
with the v1.18.1 hardware-claim correctness fixes (**DEC-114**, detailed below)
that were not separately released. The DEC-115 work is GUI-only; the bundle
pairs with `control-ofc-daemon` ≥ **v1.8.3** (carried over from DEC-114).

### Added
- **The Hardware Readiness card is now collapsible.** Diagnostics → Fans wraps
  the whole card in one collapsible section: click the "Hardware Readiness"
  header to fold the detail away and let the live fan table rise. The readiness
  **verdict** and any **critical alerts** stay visible even when collapsed
  (a new always-visible *persistent* area of `CollapsibleSection`), and a
  problem board **force-expands** the card so its detail and "To fix" guidance
  are never hidden. The card opens expanded; a manual collapse on a healthy
  board is respected.

### Fixed
- **Full Report drift.** The pop-out "Hardware Readiness — Full Report" had
  silently dropped the chip **Status** column and the kernel-module
  **Mainline** column present on the inline card. Both now render identically:
  the card and the report derive every chip / module / board / summary /
  thermal line from one shared set of formatters, so they cannot drift again.

### Changed
- `CollapsibleSection` gains a reusable persistent-area API
  (`add_persistent_widget` / `add_persistent_layout`).
- `diagnostics_page.py` internals: `_build_fans_tab` (~380 lines) split into a
  thin orchestrator + eight focused sub-builders; a 10×-copied
  stylesheet-repolish idiom and duplicated freshness / row-init /
  header-tooltip loops folded into shared helpers; the verify worker now uses
  the public `DaemonClient.socket_path` property. No behaviour change.

### Tests
- New coverage: card-level collapse (incl. force-expand-on-problem and
  respect-manual-collapse), the `CollapsibleSection` persistent area, the
  shared section-body formatters, and card↔report render consistency.

## [1.18.1] — 2026-06-02

Hardware-claim correctness fix (**DEC-114**) from a cross-repo documentation
audit that re-verified every externally-sourced hardware claim against
primary sources and now links them inline. One finding is safety-critical.
Pairs with **daemon v1.8.3**.

### Fixed
- **Kernel hard-hang guidance was unsafe.** The RDNA3/RDNA4 6.19 warning
  recommended rolling back to "6.18 LTS", but 6.18 is _also_ affected
  (Phoronix EOY 2025; ROCm #6101 reports panics on 6.18.20 and 6.19.10). The
  GUI guidance now recommends a verified-safe **6.15–6.17** longterm kernel,
  covers both 6.18.x and 6.19.x, and drops the unverified "AMD reverted
  patches" claim. The warning id is renamed `rdna_hang_kernel_6_19_x` →
  `rdna_hang_kernel_6_18_6_19` so the popup re-fires for anyone who
  acknowledged the earlier, unsafe advice.
- **Wrong RX 9070 device ID** in the GPU kernel-warning guidance
  (`hwmon_guidance.py`): RX 9070 is PCI `0x7550` rev `0xC3`, not `0x7551`
  (which is the R9700). Corrected.
- **R9700 SMU warning re-characterised:** it is an SMU interface-version
  mismatch (firmware v50 vs driver v46, ROCm #6101) that leaves no working
  fan-control path (`pwm1` read-only) across all current kernels — not a
  "silent `fan_curve` write failure" on 7.0.x only. Id renamed
  `smu_mismatch_navi48_r9700_kernel_7_0` → `smu_mismatch_navi48_r9700`.
- **nct6687 `0xd450` collision** is now framed as historical (the chip-ID
  claim was removed upstream in Fred78290/nct6687d PR #164, 2026); the false
  "Bazzite blacklists nct6687 by default" is corrected to a requested,
  unshipped fix (ublue-os/bazzite #4498).

### Documented
- **docs/19, 21, 22 corrected** for the above plus: the asus_wmi_sensors
  fan-stop warning scope (only the PRIME X470-PRO is singled out), the
  asus_ec_sensors AM4 400-series allowlist (three boards, not one), the
  nct6775 ACPI workaround (a 5.16 ASUS-WMI access path, not "ACPI mutex
  ≥5.17"), the docs/19 NCT6687 Intel-table chip IDs (`0xd440`/`0xd441` were
  wrong/unverifiable — now described by DMI selection), and the it87
  issue #70/#81 framing.
- **docs/19 gains a Sources section** with inline primary-source links
  (kernel.org, torvalds/linux, Phoronix, ROCm #6101, Fred78290/nct6687d,
  frankcrawford/it87, Bazzite #4498, AMD `amdgpu.ids`), matching docs/23's
  citation style.

### Tested
- New `test_renamed_ids_invalidate_old_acknowledgements` proves the renamed
  ids invalidate stale acknowledgements; existing kernel-warning parsing /
  gating tests updated to the new ids.

## [1.18.0] — 2026-06-02

Diagnostics > Fans readiness UX refinement and a combo-box arrow fix
(**DEC-113**), following up on the DEC-112 progressive-disclosure work.
GUI-only release — no daemon contract change.

### Fixed
- **Combo-box down-arrow was invisible app-wide.** Styling
  `QComboBox::drop-down` made Qt drop the native arrow on every combo box
  (Settings, fan-role dialog, curve editor, the PWM-verify picker, …). The
  theme now generates a chevron SVG in the active theme's colour and the
  stylesheet references it, so the arrow is always visible and follows the
  theme. The DEC-112 section chevron was never the problem — it renders
  correctly; the "disappearing dropdown" was this missing combo arrow.
- **Timeline-chart teardown use-after-free.** The dashboard chart's secondary
  RPM ViewBox stayed X-linked into widget destruction, raising "Internal C++
  object (ViewBox) already deleted" on shutdown/teardown. `TimelineChart`
  cleanup now breaks the links and `DashboardPage.closeEvent` runs it.

### Added
- **Readiness verdict banner** at the top of Hardware Readiness — a green
  *✓ System ready — N headers, M writable · thermal safety <state>* or an
  amber/red *⚠ K issue(s) need attention*, so the collapsed view answers at a
  glance. Populated automatically the first time the Fans tab is opened
  (auto-fetch), filling the previously-empty space.
- **"To fix" guidance** in *Guidance & documentation* — per-issue remediation
  steps (ACPI conflicts, module collisions, GPU `ppfeaturemask`, dual-chip,
  all-read-only, …) with a short safety disclaimer and a clickable
  documentation link for each.
- **Open Full Report ↗** — opens the complete hardware-readiness report
  (`ReadinessReportDialog`) in its own resizable window with all detail and
  clickable links, for boards with a lot of information.
- New first-party module `ui/widgets/readiness_report.py` and theme helper
  `combo_arrow_svg_path`.

### Changed
- Info-level vendor quirks are treated as FYI notes and no longer count as
  "issues needing attention" in the verdict.
- The `module_collisions` and BIOS-revert labels now open external links;
  report/guidance link colour is set inline for contrast on dark themes.
- Internal: the `CollapsibleSection` header is a flat `QPushButton` (so its
  text left-aligns; `QToolButton` ignores stylesheet `text-align`).

## [1.17.0] — 2026-06-02

Diagnostics > Fans tab refined with **progressive disclosure** (**DEC-112**).
The "Hardware Readiness" pane stacked ~20 widgets in one long scroll, which
became a wall of warnings, tables, and driver forensics on exactly the problem
boards where it is read — pushing the live fan table below the fold. The detail
is now grouped into collapsible sections while the readiness summary, the
critical-alert stack, and the fan table stay visible. GUI-only release — no
daemon contract change.

### Added
- **`CollapsibleSection` widget** (`src/control_ofc/ui/widgets/collapsible_section.py`)
  — a first-party titled, collapsible container (chevron header, multiple
  sections open at once, instant toggle). Reusable on other pages later.
- **`.CollapsibleSectionHeader`** theme rule — body-sized, semibold, theme-
  derived font size (no hardcoded px), chevron colour inherited from the theme.

### Changed
- **Diagnostics > Fans tab layout** — the Hardware Readiness card now shows an
  always-visible readiness summary + board identity and a critical-alert stack
  (module collisions/conflicts, dual-chip, vendor quirks, ACPI, BIOS-revert
  headline), followed by five collapsible sections: *Detected hardware*,
  *BIOS interference detail*, *Thermal safety & GPU*, *Guidance & documentation*,
  and *PWM control test*. The *Refresh Hardware Diagnostics* button and the live
  *Fan Status* table remain always visible.
- **BIOS interference detail auto-expands** when any header reports a non-zero
  revert count, so a real problem is never hidden; it never auto-collapses, so a
  manual toggle on a healthy system is respected.
- The Fans-tab splitter now favours the live fan table by default, since the
  top pane is compact when sections are collapsed.

### Notes
- No data-model, business-logic, or daemon-API changes — only the widget tree
  was reorganised. Every existing widget attribute and `objectName` is
  preserved; the `Diagnostics_Splitter_fans` 2-child vertical contract is kept.
- Critical warnings are deliberately kept *outside* collapsed panels, per NN/g
  desktop-accordion guidance.

## [1.16.0] — 2026-06-02

Diagnostics > Event Log overhaul (**DEC-111**). The event log was
visible to users but never populated — `DiagnosticsService.log_event`
had zero non-test call sites. This release wires every meaningful
transition into the deque, rebuilds the tab around a filterable
`QTableView`, and carves the on-demand snapshot dumps into a separate
sub-section so clearing the log no longer wipes journal output the
user just fetched. GUI-only release — no daemon contract change.

### Added
- **`EventLogView` widget** (`src/control_ofc/ui/widgets/event_log_view.py`)
  — table-based event log with multi-select severity filter,
  source dropdown, free-text search, auto-follow-while-at-bottom,
  details pane, and Export / Copy of the currently-filtered rows.
- **Live event emission** from production services:
  - `polling` — daemon connected / disconnected (transitions only,
    no per-cycle noise) and daemon-reported active profile.
  - `lease` — acquired / released / lost.
  - `control_loop` — started / stopped, write-fail threshold crossed,
    per-target recovery, lease-lost transition.
  - `profile` — load failures.
  - `gui` — GUI start / exit, theme change, manual override entered,
    demo mode active, kernel-warning acknowledged.
- **`Diagnostic Snapshots` sub-section** below the event log. The four
  on-demand detail buttons (Daemon Status, Controller Status, GPU
  Status, System Journal) write to their own `QPlainTextEdit`
  (`Diagnostics_Text_snapshotView`) and have a new `Clear Snapshots`
  button. Clearing the event log no longer wipes snapshot output.
- **`DiagnosticsService` is now a `QObject`** emitting
  `event_appended(DiagEvent)` and `events_cleared()` so the view
  follows the deque live. Adds `filter_events(...)` and
  `known_sources()` helpers.
- **DEC-111** in `DECISIONS.md` recording the architecture, the
  three-concept separation (Event Log vs. Active Warnings vs. System
  Journal), and the alternatives considered.

### Changed
- `PollingService` / `LeaseService` / `ControlLoopService` /
  `MainWindow` accept a new optional `diagnostics=` keyword to
  receive the shared `DiagnosticsService`. Without it (e.g. tests),
  each service keeps working exactly as before — emitters become
  no-ops.
- `manual/diagnostics.md` rewritten to describe the new layout and
  filter controls; the stale "2000 entries" claim is corrected to
  the actual 200-row cap.
- `docs/07_Diagnostics_Spec.md` "Event log detail retrieval" section
  rewritten to cover the three-concept separation, the emitter
  contract, and the snapshot sub-section.

### Fixed
- `Clear Log` no longer wipes Daemon Status / Controller Status / GPU
  Status / System Journal output the user just fetched — the
  snapshot view is now a separate widget.
- The event log finally renders real GUI activity instead of being a
  permanently empty `QPlainTextEdit`.

### Tests
- New `tests/test_event_log_view.py` (qtbot) covering severity /
  source / search filters, details-pane rendering, copy-filtered-view,
  empty-state visibility, and live row append on
  `event_appended` signal.
- New `tests/test_event_log_emitters.py` pinning every transition
  emission for polling / lease / control loop (write-fail threshold,
  recovery, lease lost).
- Extended `tests/test_diagnostics_service.py` with `event_appended`
  / `events_cleared` signal tests and `filter_events` matrix.
- Existing diagnostics tests updated to the new
  `Diagnostics_Text_snapshotView` widget name and the new
  `Diagnostic Snapshots` carve-out.

## [1.15.0] — 2026-06-01

Initial Intel motherboard / CPU support foundation (DEC-110). No
behavioural changes to the control loop, lease lifecycle, or write
paths — this is a truthful-reporting pass that makes Intel hardware
show up correctly in Diagnostics and BIOS guidance. Pairs with
**daemon v1.8.2**, which adds the `cpu_vendor` field on
`/diagnostics/hardware` and the `intel_pch_thermal` row in
`KNOWN_MODULES`.

### Added
- **Intel platform vendor quirks** in `hwmon_guidance.py` covering
  ASUS Intel asus_ec_sensors allowlist (Z690 FORMULA + STRIX
  Z690-A/E + Z790-E II / -H / -I WIFI), ASUS Intel NCT6798D
  mainline coverage, MSI Intel Z690/Z790 NCT6687D (plain — no
  `msi_alt1`), MSI Intel Z890 NCT6687DR (HIGH severity — needs
  `msi_alt1`), Gigabyte Intel Z690/Z790 IT8689E dual-chip,
  Gigabyte Intel Z890 IT8696E dual-chip, ASRock Intel Z690/Z790
  NCT6798D coverage. Each entry is platform-scoped via the new
  `VendorQuirk.platform` field so AMD coverage from the same
  vendors continues firing unchanged.
- **`VendorQuirk.platform` (`"intel"`/`"amd"`/`None`) and
  `VendorQuirk.board_pattern` (case-insensitive substring) fields**
  for scoping quirks to one CPU vendor and/or one board-name
  pattern. The MSI Z690/Z790 vs Z890 NCT6687 distinction
  (same chip name, different register layouts) is the headline
  case for `board_pattern`. Pre-DEC-110 quirks default to
  `platform=None` / `board_pattern=""` and continue to match
  vendor + chip only.
- **`lookup_vendor_quirks(cpu_vendor=..., board_name=...)`**
  keyword args, plumbed from the diagnostics page using the
  daemon's new `cpu_vendor` field and the existing `board.name`.
  Backward-compatible — existing two-positional-arg callers
  continue to work.
- **Intel `BoardSensorOverride` entries** for the six kernel-
  documented `asus_ec_sensors` Intel allowlist boards.
- **ASRock Z690 Extreme label fallback** (`verified=True`) — the
  only LGA1700-era board with an upstream lm-sensors config in
  `lm-sensors/configs/ASRock/Z690_Extreme.conf`.
- **Intel PECI sensor classification** in `_classify_nct6775`:
  labels like `PECI Agent 0` and `PECI 0` (no `"CPU"` substring)
  now classify as `cpu_peci` with `medium_high` confidence and a
  truthful "Intel CPU temperature reported via the PECI bus"
  tooltip.
- **`HardwareDiagnosticsResult.cpu_vendor: str`** parsed from
  the daemon. Defaults to `""` for older daemons.
- **`docs/23_Intel_Motherboard_Fan_Control_Guide.md`** — vendor-
  by-vendor setup, drivers, quirks, troubleshooting for Intel
  LGA1700 / LGA1851 platforms. Companion to doc 21.
- **`docs/19_Hardware_Compatibility.md`** Intel platform → typical
  chip mapping table parallel to the AMD one.
- **`docs/08_API_Integration_Contract.md`** documents the new
  `cpu_vendor` field and `intel_pch_thermal` module entry.
- **`docs/00_README_START_HERE.md`** reading list updated to
  include doc 23.

### Tested
- `tests/test_intel_lga1700_quirks.py` (29 tests): CPU vendor round-
  trip, VendorQuirk platform/board_pattern scoping, MSI Intel
  NCT6687D vs AMD disambiguation, ASUS Intel asus_ec_sensors
  allowlist quirk, ASUS Intel NCT6798D supported guidance,
  Gigabyte Intel Z690/Z790 dual-chip, ASRock Intel NCT6798D,
  Intel coretemp + PECI classification, kernel allowlist board
  overrides, ASRock Z690 Extreme label fallback.
- `tests/test_intel_lga1851_quirks.py` (8 tests): MSI Z890
  NCT6687DR `msi_alt1` quirk fires on Z890 + Intel only;
  suppressed on Z690 / Z790 (plain) + AMD X870E (same chip,
  different platform); Gigabyte Z890 AORUS Intel IT8696E quirk.

## [1.14.1] — 2026-06-01

Audit remediation pass following a cross-stack `/audit effort=max` sweep
on 2026-06-01. No new features, no contract-breaking changes. Pairs
with **daemon v1.8.1**, which lands the matching SIGTERM and
documentation fixes on the Rust side.

### Hardened
- **`paths.atomic_write` now fsyncs the parent directory after rename**,
  matching daemon DEC-108. Without this step, ext4/btrfs can land the
  rename in the journal while the dirent change is still in the page
  cache, so power loss between rename and journal flush can resurrect
  the old name on next mount even though the new file's data is
  durable. Failure of the dir fsync is non-fatal (some filesystems —
  tmpfs in particular — do not honour it). Two new regression tests in
  `tests/test_paths.py` pin the fsync attempt and the failure tolerance.

### Cleanup
- **Removed three unused `DaemonClient` wrappers**:
  `set_openfan_all_pwm`, `hwmon_rescan`, `calibrate_openfan`. None had
  any caller under `src/`; the v1 GUI never exposed a Rescan button or
  a built-in calibration sweep. The daemon endpoints stay in place
  (documented under "Daemon endpoints the v1 GUI does not call" in
  `docs/08_API_Integration_Contract.md`) — restore the wrappers from
  git if a future UI surface needs them. Test infrastructure
  cleaned up alongside: FakeDaemonClient mocks, `simulate_unavailable`
  list, `test_control_loop_writes` write-method allow-list, the
  calibrate dynamic-timeout test.
- **Removed dead helpers and constants**: `paths.log_path()`,
  `constants.ORG_NAME`, `constants.HISTORY_MAX_SAMPLES`,
  `ui.theme.current_font_sizes()` (and the now-orphan
  `_active_base_size` module-state + `set_active_base_size`). Each had
  zero callers in `src/` or `tests/`.
- **Replaced one hardcoded socket-path literal** in
  `ui/pages/dashboard_page.py:234` with `DEFAULT_SOCKET_PATH` from
  `constants.py`, removing the last duplicate of that string in the
  GUI's runtime code.
- **Removed the empty `src/control_ofc/persistence/` package**. The
  module shipped only `__init__.py` since v1.0.0; persistence work
  lives in the per-service `*_service.py` files (`app_settings_service`,
  `profile_service`, `series_selection`, etc.). `docs/12_Implementation_Plan`
  is corrected to match.

### Documentation
- **`docs/08_API_Integration_Contract.md`** now documents the
  `pwm_mode` field on `GET /hwmon/headers` (`0` = DC, `1` = PWM,
  omitted when the chip does not expose `pwmN_mode`) — consumed by the
  dashboard and diagnostics pages but previously undocumented in the
  contract spec. The `calibrate_openfan` bullet is replaced with a
  "Daemon endpoints the v1 GUI does not call" section listing the four
  POST endpoints the daemon ships but the client does not wrap.
- **`docs/12_Implementation_Plan_and_Module_Structure.md`** no longer
  references the telemetry features that were fully de-scoped in
  v0.72.0 (R52). The persistence section now correctly describes the
  per-service ownership model, and the packaging direction is corrected
  from AppImage (early speculation) to AUR (what shipped).
- **`docs/11_Persistence_Config_and_File_Layout.md`** suggested-layout
  block no longer lists `gui.log`, `last_session.json`, and
  `support_bundle_work/` — none of which exist in the GUI. Replaced
  with a note that runtime state lives entirely under
  `~/.config/control-ofc/` and that support bundles are generated on
  demand to a user-selected export location. Aliases and groups live
  inside `app_settings.json`, not separate files.
- **`docs/14_Risks_Gaps_and_Future_Work.md`** "GUI rescan button —
  ABSENT" line updated to record that the underlying client wrapper
  was removed in v1.14.1 (restore from git when implementing the UI).
- **README "Latest release" line** refreshed to v1.14.1 (was 4 minor
  versions stale).

### Tests
- 1467 tests passing (+7 new atomic_write tests).
- 89% line coverage maintained.

## [1.14.0] — 2026-05-18

Themes audit (DEC-109). Every visible widget is now driven by named
tokens, the default-dark palette was tightened to pass WCAG AA on every
pair the checker now evaluates, and the GUI ships two new bundled
presets (Solar Light and Noctua Dark). Persisted theme selection is now
actually restored on startup.

### Added
- **Active-theme registry** in `theme.py` (`set_active_theme`,
  `active_theme`) so widgets without a parent reference can look up
  the live tokens on every render instead of capturing a stale snapshot
  at import time. Mirrors the existing `_active_base_size` pattern.
- **Bundled theme presets** in `src/control_ofc/ui/presets/`:
  - **Solar Light** — neutral GitHub-style light theme, every pair
    verified ≥ WCAG AA.
  - **Noctua Dark** — beige/brown-on-charcoal palette inspired by
    Noctua's NF-A14 colours, every pair verified ≥ WCAG AA. Primary
    button text is dark on the beige accent to keep contrast.
  Presets are copied into `themes_dir()` on first run via
  `ensure_bundled_themes_installed`, which is idempotent and never
  overwrites a user-edited file.
- **`code_block_bg` token** for inline command/code surfaces so a light
  theme can swap to a light tint instead of pure black.
- **`set_theme()` methods** on `DashboardPage`, `DiagnosticsPage`, and
  `TimelineChart` so a theme switch repaints the inline command label,
  freshness column foreground colours, hardware reclaim card, and the
  timeline chart's background / axes / crosshair / existing series.
- **Chart series swatches** are now editable from the Theme Editor —
  the previously inaccessible 8-colour `chart_series` palette is now
  exposed as a per-slot grid.
- **`chart_crosshair`** is now editable from the Theme Editor.

### Changed
- **Default Dark palette tightened** to pass WCAG AA on every contrast
  pair the checker now evaluates. Affected tokens: `text_muted` →
  `#8a92a4` (3.4:1 on cards, was 2.5:1), `accent_primary` → `#2f73c4`
  (4.8:1 with white, was 3.3:1), `accent_secondary` repurposed as a
  darker hover (`#1d5fa9`, 6.4:1), `nav_text_active` → `#a4caf5` on
  `nav_item_active` `#1a3a6a` (6.6:1, was 2.6:1), `chart_axis_text`
  and `input_placeholder` → `#8a92a4`.
- **`check_contrast_warnings()` covers eight additional token pairs**
  (primary-button-text on accent + hover, muted text on cards, chart
  axis text on chart bg, placeholder text on input bg, active nav on
  its own fill, plus the normal-button pressed state). The editor's
  "No contrast issues detected" badge is no longer a lie on themes
  that fail real pairs.
- **Persisted theme is restored at startup** — `main.py` reads
  `AppSettings.theme_name`, scans `themes_dir()`, and applies the
  matching JSON file before falling back to Default Dark. Previously
  the persisted name was ignored and every restart returned to
  Default Dark.

### Fixed
- **Theme switching no longer leaves stale widgets** on the previous
  palette:
  - `DashboardPage` enable-command label background previously pinned
    to `rgba(0,0,0,0.25)` — now uses `code_block_bg` and follows
    light themes correctly.
  - `SettingsPage` dir-picker label colour previously pinned to the
    default-dark `text_muted` snapshot — now reads `active_theme()`.
  - `DiagnosticsPage` module-level `_THEME = default_dark_theme()`
    snapshot is gone; sensor/fan freshness column foreground colours
    and the per-header reclaim card now read `active_theme()` per
    render.
  - `TimelineChart` snapshot in `__init__` is gone; the chart now
    refreshes background, axes, crosshair, hover label, and existing
    (non-overridden) series colours when `set_theme()` is invoked.

### Tests
- **37 new tests** in `tests/test_theme_dec109.py` covering the
  active-theme registry, the expanded contrast checks (six negative
  cases pinning the original failing pairs, one positive case per
  newly-checked pair on Default Dark), bundled-preset loading and AA
  verification, first-run install idempotency, startup theme
  resolution (including missing/corrupted/missing-dir cases), the new
  `set_theme()` hooks, the live-theme behaviour of
  `reclaim_severity_color`, and a regression guard for each of the
  four hardcoded-style offenders the audit identified.

### Docs
- **DEC-109** added to `DECISIONS.md`: bundled presets ship in-tree,
  WCAG AA is adopted as project policy, and the persisted-theme
  startup contract is documented.

## [1.13.0] — 2026-05-13

Pairs with **daemon v1.8.0**. Combined release of two prior `[Unreleased]`
waves: the DEC-107 mutation-driven test-tests hardening pass (+47 tests
across both repos, two small non-breaking daemon contract additions)
and the DEC-108 `/audit` follow-up hardening pass (3 P1 + 4 P2 fixes
spanning fsync atomic writes, a dedicated lease worker thread, write-
worker shutdown ordering, `set_pwm_all` broadcast coalescing,
profile-path-traversal IPC tests, and a noise-reduction log-level
demotion). All additions are backward-compatible on the wire and the
internal API; older daemons and older GUIs interoperate with the new
counterpart without behavioural change. GUI: 1423 tests pass; daemon:
482 tests pass.

### Wave 1 — DEC-107 test-tests audit hardening

Test-tests audit hardening pass. A `/test-tests` mutation-driven audit
identified a set of equivalent-mutant survivors in both the Python GUI
and the Rust daemon; this pass closes those gaps with 31 new GUI tests
and the corresponding daemon counterpart, plus two small contract
additions to make daemon-side behaviour observable. See DEC-107.

### Tests
- **31 new GUI tests** across six suites, every one anchored to a
  specific mutation-survivor pattern from the audit:
  - **`test_polling_service.py`**: 6 new tests exercising the real
    `PollingService.__init__` instead of bypassing it via
    `__new__`. Locks down `setInterval(POLL_INTERVAL_MS)`, the
    six worker→state signal wirings, the worker QThread being
    started, and `start()` / `stop()` toggling the timer.
  - **`test_lease_service.py`**: 3 new tests pinning down the
    renew-timer interval (`LEASE_RENEW_INTERVAL_S * 1000` ms),
    the literal `"gui"` owner-hint string sent to the daemon,
    and the invariant `LEASE_RENEW_INTERVAL_S < 60` against the
    daemon's lease TTL.
  - **`test_history.py`**: 3 new tests for `prefill_sensor` on a
    pre-existing series (append-not-replace semantics) and the
    `_prune` boundary (`<` vs `<=`) — an entry exactly at the
    cutoff must be retained, an entry one tick past it must be
    dropped.
  - **`test_session_stats.py`**: 4 new tests covering the `<`
    and `>` boundaries in `SessionStatsTracker.update`. Catches
    `<` → `<=` and `>` → `>=` mutations that were previously
    survivable because no test fed a value exactly equal to the
    current min or max.
  - **`test_ui_clicks.py`**: 2 widget-existence-only tests
    (`*_button_exists`) replaced with behaviour assertions — the
    new control click now drives a real `LogicalControl` into
    the active profile, and the refresh-overview click drives
    the status label to `"Refreshed"`.
  - **`test_models.py`**: 13 new parser failure-mode tests
    covering missing required fields, wrong-type passthroughs,
    null lease_id, completely empty input dicts, and a few
    explicit raises-on-pathological-shape cases. Pins the
    "safe defaults at the boundary" contract end-to-end.

### Added (daemon contract surface, non-breaking)
- **`LeaseError::Expired` variant** — distinguishes "lease id
  matches but TTL elapsed" from "lease id doesn't match the
  active lease". HTTP wire shape unchanged: both still map to
  `403 lease_required`. Internally, callers can now reason
  about whether to renew vs re-acquire without parsing log
  text. See DEC-107.
- **`HwmonPwmController.verify_mismatch_counts()` accessor** —
  exposes a per-header cumulative count of PWM verify-after-
  write mismatches. Mirrors `enable_revert_counts()`. Useful
  for diagnostics and — more importantly — makes the existing
  mismatch path observable in tests. See DEC-107.

### Documentation
- **DECISIONS.md:** new DEC-107 (test-tests audit hardening:
  rationale, mutation-survivor inventory, contract additions).

---

### Wave 2 — DEC-108 `/audit` follow-up hardening pass

A post-v1.12.0 / v1.7.0 `/audit` of both repos surfaced three P1
issues and four P2 issues; this section captures the GUI-side
fixes. See DEC-108 for the full rationale.

#### Fixed (GUI)
- **Lease HTTP calls no longer block the Qt main thread.** A new
  `_LeaseWorker` `QObject` runs on a dedicated `QThread`;
  `LeaseService` keeps its public API and signals but the actual
  `POST /hwmon/lease/{take,renew,release}` calls now happen on the
  worker. Pairs with DEC-099 to close the last main-thread blocking
  site in the 1 Hz hot path. (P1-C)
- **`ControlLoopService.shutdown()` quits and waits the write
  worker thread BEFORE closing the worker's `DaemonClient`.** The
  previous order mutated `_WriteWorker._client = None` from the
  main thread while the worker could still be running `do_write`.
  GIL made it benign in practice; the reorder removes the race.
  Regression locked by `test_shutdown_quits_thread_before_closing_worker_client`.
  (P2-C)
- **`_PollWorker._register_profile_search_dir` no longer logs at
  INFO on every reconnect.** First registration per process is
  still INFO; subsequent re-registrations (same dir) log at DEBUG.
  The HTTP call still fires every reconnect for safety (daemon
  may have restarted with a stale search-dir list). (P2-D)

#### Added (GUI)
- **`LEASE_API_TIMEOUT_S = 1.5` constant** (in `constants.py`)
  passed to all three lease HTTP calls. Below `LEASE_RENEW_INTERVAL_S
  = 30 s` so a contended daemon cannot pile up concurrent renews.
- **`SetPwmAllResult.coalesced: bool`** field on the GUI model,
  parsed from the new daemon response field. Defaults to `False`
  for forward compatibility with pre-DEC-108 daemons.
- **`LeaseService.__init__(client, *, socket_path=None)`** —
  passing `socket_path` engages worker-thread mode. Existing
  callers (and all current tests) continue to work without it
  by using the sync fallback against an injected mock client.

#### Tests (GUI)
- **+8 lease worker-mode tests** in `test_lease_service.py`:
  thread created when `socket_path` supplied, sync mode when
  not, `acquire()` queues request and returns True, duplicate
  acquires coalesce, take_completed updates state + signal,
  take_completed failure → lease_lost, renew coalesces while
  in-flight, stale renew response after release is discarded,
  shutdown joins thread before closing client.
- **+2 existing test updates** for the new `timeout` kwarg on
  `hwmon_lease_take` / `hwmon_lease_release`.
- **+1 shutdown-order test** in `test_control_loop.py`
  (`test_shutdown_quits_thread_before_closing_worker_client`).
- **+1 parser test** in `test_models.py`
  (`test_parse_set_pwm_all_with_coalesced_field`).
- **+1 log-level test** in `test_profile_search_dir_registration.py`
  (`test_first_registration_logs_info_subsequent_logs_debug`).
- Test count: 1411 → 1423 passing (+12 net, all green).

#### Documentation
- **DECISIONS.md:** new DEC-108 (audit follow-up: full rationale,
  cross-repo coupling, fixes for P1-A through P2-D).

---

## [1.12.0] — 2026-05-13

Pairs with **daemon v1.7.0**. Coordinated AMD-board-support hardening
release covering AM4 400-series (DEC-105) and AM4 500 / AM5 600 / AM5
800 (DEC-106) in one minor bump. Adds verified-against-upstream label
fallbacks, an expanded vendor-quirk database spanning every shipping
AMD chipset generation, a CRITICAL diagnostics banner for the
NCT6797D-vs-`nct6687` register-corruption brick risk, and refined
detector behaviour that no longer false-CRITICALs on legitimate dual-
Nuvoton boards (ASRock X870E Taichi Lite). 1380 GUI tests pass; 407
daemon tests pass.

### DEC-106 — AM4 500 / AM5 600 / AM5 800 hardening

Stacks on DEC-105's AM4 400-series pass to bring the rest of the AMD
lineup up to the same quality bar. Adds collision-detector refinement,
narrower chip-prefix entries, new vendor quirks, new label fallbacks,
and an expanded vendor-by-vendor doc section spanning AM4 500-series
through AM5 800-series. Continues the no-breaking-contract discipline:
the daemon emits the same `/diagnostics/hardware.module_collisions`
shape; the refinement is purely a reduction in false-positive entries.

#### Features
- **Daemon collision-detector dual-Nuvoton exemption (DEC-106).** The
  `(nct6687, nct6775)` simultaneous-load collision no longer emits a
  CRITICAL banner when `chips_detected` shows TWO distinct nct6 chips
  at distinct `device_id`s. This protects users on legitimate dual-
  Nuvoton boards — most prominently **ASRock X870E Taichi Lite**
  (NCT6686 at 0x0a20 + NCT6799 at 0x0290) — from being told to
  blacklist one of their working drivers. The original brick-risk
  detection (single chip + both modules loaded) is unchanged.
- **AM4 500-series & AM5 800-series Gigabyte AORUS dual-chip entries
  added to `GIGABYTE_DUAL_CHIP_BOARDS`:** B550 VISION D (it8688 +
  it8792, verified against upstream `configs/Gigabyte/
  GA-B550-VISION-D.conf`) and B850-AI-TOP (it8696 + it87952, per
  frankcrawford/it87 issue #93). X870 AORUS STEALTH ICE is
  deliberately NOT added — its IT8883 secondary has no Linux driver,
  so a permanent missing-chip warning would be useless; it is
  documented in the chip-guidance DB instead.
- **Narrower NCT679x chip-prefix entries in GUI `CHIP_GUIDANCE_DB`.**
  NCT6799D (with ASRock Taichi Lite reference), NCT6798D (with the
  chip-ID-0xd428 / no-DEC-105-risk explanation), and NCT6796D-S (with
  the ASRock X870 Nova reference) now have their own entries instead
  of falling through to the generic `nct679` row. Longest-prefix
  match still resolves correctly for NCT6797D, NCT6795D, etc.
- **IT8883 preliminary chip entry (per D4.A).** Ships with explicit
  "no Linux driver as of 2026-Q2" notes and a link to frankcrawford/
  it87 issue #81, so users on Gigabyte X870 AORUS STEALTH ICE see a
  named chip rather than "Unknown chip" when investigating their
  diagnostic gaps.
- **Eight new x500/x600/x800 vendor quirks** in `VENDOR_QUIRKS_DB`:
  Gigabyte IT8688E AM4 500-series (info — dual-chip & sleep/resume
  hint), MSI nct6687 auto-allowlist (info — points users at the v2.x
  msi_alt1 self-enabling table), MSI 500-series NCT6687-R camp
  (medium — distinguishes from the DEC-105 NCT6797D brick risk),
  ASRock NCT6799 Taichi Lite (info — references DEC-106), ASRock
  NCT6798D AM4 500-series (info), ASRock NCT6796D-S X870 Nova (info),
  ASUS NCT6798D AM4 500 / AM5 600 (info), and Gigabyte IT8696E
  X870 AORUS STEALTH ICE / IT8883 unsupported (medium). The existing
  IT8689E Rev 1 critical quirk now also references frankcrawford/it87
  issue #96.
- **Three new `HWMON_LABEL_FALLBACK` boards** (D-B.B1 — verified
  against upstream lm-sensors `configs/` only): Gigabyte B550 VISION D
  (it8688 + it8792 dual-chip mapping), Gigabyte B550M AORUS PRO
  (it8688 single-chip; glob covers WIFI variant), and MSI X570-A PRO
  (nct6797). Per D3.A, MSI nct6687d-only-fanN-labelled boards
  (MAG B550 TOMAHAWK, MS-7C56 B550 A-PRO) were skipped — they rely on
  the runtime libsensors parser instead.

#### Documentation
- `docs/21_AMD_Motherboard_Fan_Control_Guide.md`: added dedicated
  "AM4 500-series specifics", "AM5 600-series specifics", and "AM5
  800-series specifics" sections paralleling DEC-105's AM4 400-series
  treatment. Each section enumerates the per-vendor chip / driver
  story and calls out the generation-specific hazards (Gigabyte
  IT8689E Rev 1 dead end, ASRock dual-Nuvoton, MSI auto-allowlist).
- `docs/19_Hardware_Compatibility.md`: AM4 500 / AM5 600 / AM5 800
  rows in the AMD-platform→chip-mapping table expanded from one row
  each to per-vendor granularity, matching the AM4 400-series level
  of detail. ASRock alternative-driver list extended to AM5 600.

#### Tests
- New `tests/test_am5_600_series_quirks.py` (23 tests) covers the
  narrower chip-prefix entries (longest-prefix matching invariants),
  the IT8883 preliminary entry, every new vendor quirk, every new
  label fallback (including wrong-chip / wrong-vendor negative
  cases), and the GUI parser round-trip for both the daemon's
  suppressed and emitted `module_collisions` shapes.
- Daemon `api::diagnostics::tests` extended with five new tests:
  legitimate-dual-Nuvoton suppression, single-chip critical retention,
  empty-chips defensive fallback (still CRITICAL), non-nct6 chips
  ignored for the suppression rule, plus dual-chip table coverage
  for B550 VISION D and B850-AI-TOP.
- Three pre-existing `test_v1_2_diagnostics.py::TestVendorQuirkLookup`
  tests loosened from "exact count" to "content present" so adding
  legitimate new quirks to the database does not break unrelated
  tests. The `test_hwmon_guidance.py` NCT6798 / case-insensitive
  tests updated to expect the new narrower `nct6798` prefix.
  Implementation-coupled `test_msi_x870_brute_force_quirk` (was
  `details[2]`-indexed) replaced with content-anywhere assertion;
  `test_case_insensitive_vendor` strengthened from "returns
  something" to "returns the SAME set of summaries as the canonical-
  cased lookup".

#### Security / robustness
- **`_vendor_quirk_label` now explicitly sets `Qt.TextFormat.PlainText`**
  (security-reviewer finding). The label was previously relying on
  Qt's `AutoText` default; mirroring the explicit format set on every
  other sibling label closes a latent gap should daemon-supplied
  strings ever flow into this label.
- **Headline collision-detector assertions tightened.** The new
  `test_msi_nct6687_auto_allowlist_quirk` now asserts on both
  `msi_alt1` (specific module parameter name) AND the 33-board
  allowlist count (anchors the upstream source). The new
  `test_nct6798_picks_specific_entry_not_generic` now requires the
  `0xd428` chip-ID anchor explicitly (was a disjunction that could
  silently accept unrelated text).
- **New `test_unverified_entry_gets_unverified_suffix`** asserts the
  `(unverified)` suffix on `FallbackLabel.display()` so an accidental
  edit that drops the suffix logic cannot silently mislabel
  unverified headers as if they were verified.

#### Documentation
- `docs/08_API_Integration_Contract.md` extended with a DEC-106
  paragraph under `/diagnostics/hardware.module_collisions` describing
  the dual-Nuvoton suppression condition. Old daemons emit the
  broader result; the GUI parser handles both shapes identically.

### DEC-105 — AM4 400-series hardening

Pulls AM4 400-series (B450 / X470) board coverage up to the same
standard as the AM5 boards, after wider research surfaced a chip-ID-
collision trap that can permanently brick CPU fan headers on common
MSI AM4 boards. No breaking contract changes: the new daemon response
field (`/diagnostics/hardware.module_collisions`) is additive and
older GUIs default to `[]`. Older daemons that don't emit the field
fall back to the GUI's existing `CONFLICTING_MODULE_SETS` static
table.

#### Features
- **NCT6797D ↔ `nct6687` chip-ID collision detection (DEC-105).** The
  daemon's `/diagnostics/hardware` endpoint now emits a new
  `module_collisions` list. The flagship entry flags the simultaneous
  load of `nct6687` (out-of-tree, chip-ID 0xd450) and `nct6775`
  (in-kernel) — a known register-corruption race on AM4 400-series MSI
  boards using NCT6797D (B450M MORTAR, X470 GAMING PRO CARBON, MAG B450
  TOMAHAWK MAX) as well as the wider AM5 lineage. The GUI renders a
  CRITICAL banner with the exact blacklist remediation and discourages
  PWM writes until the collision is resolved. The same pair is also
  added to the GUI's static `CONFLICTING_MODULE_SETS` table so users on
  daemons that predate this field still see the warning.
- **AM4 400-series Gigabyte AORUS dual-chip lookup.** The daemon's
  `GIGABYTE_DUAL_CHIP_BOARDS` table now covers X470 AORUS ULTRA GAMING,
  X470 AORUS GAMING 5 WIFI, X470 AORUS GAMING 7 WIFI, and B450 AORUS PRO
  (matches the WIFI variant via substring). Existing B450 AORUS PRO-CF
  entry kept for continuity. Chip pairing is IT8686E + IT8792E,
  consistent with the lm-sensors upstream config for the ULTRA GAMING.
- **Kernel-documented `asus_wmi_sensors` AM4 boards.** New vendor quirk
  enumerates the kernel allowlist (PRIME X470-PRO, ROG STRIX B450-E/F/I
  GAMING, ROG STRIX X470-F/I GAMING) and reiterates the upstream warning
  that high-frequency polling can stop fans or pin them at maximum on
  these boards. The daemon's 1 Hz polling stays within safe bounds; the
  guidance discourages running additional sensor-polling tools
  concurrently.
- **`asus_atk0110` recognition.** Added to the daemon's `KNOWN_MODULES`
  table and the GUI's `CHIP_GUIDANCE_DB`, plus a vendor quirk explaining
  this is a read-only ACPI sensor path — closing a real diagnostic dead
  end for ASUS users who saw the driver loaded but no controllable PWM
  headers.
- **MSI NCT6795D / NCT6797D / NCT6798D guidance.** New vendor quirks
  cover the AM4-series MSI chip lineup explicitly. NCT6795D is marked
  INFO (mainline supported, no collision concern). NCT6797D/6798D carry
  the CRITICAL collision warning. Helps users distinguish between
  "needs out-of-tree driver" and "must not load out-of-tree driver".
- **ASRock NCT6779D / NCT6792D AM4 guidance.** New INFO-severity quirks
  confirm mainline coverage and point users toward BIOS Smart Fan
  toggles as the usual culprit when headers appear read-only.
- **AM4 400-series `HWMON_LABEL_FALLBACK` entries.** Verified against
  upstream lm-sensors `configs/`: X470 AORUS ULTRA GAMING (it8686 +
  it8792), MSI X470 GAMING PRO (nct6795), MSI B450M MORTAR (nct6797),
  ASRock B450 Gaming ITX/AC (nct6792). Every entry quotes its upstream
  source file in the comment so the mapping can be re-verified later.
  Only boards with an upstream config are added — others continue to
  rely on the user's local `/etc/sensors.d/` content or the raw `pwmN`
  sensor name.

#### Documentation
- `docs/21_AMD_Motherboard_Fan_Control_Guide.md`: promoted the one-line
  AM4 400-series row in the platform overview to a dedicated "AM4
  400-series specifics" section covering all four DEC-105 hazards
  (NCT6797D collision, ASUS WMI polling, Gigabyte dual-chip, ASRock
  generally-smooth).
- `docs/19_Hardware_Compatibility.md`: added NCT6797D / NCT6795D /
  NCT6792D / NCT6779D rows to the Nuvoton table; added an ASUS
  sensor-only-drivers comparison table covering `asus_wmi_sensors` /
  `asus_ec_sensors` / `asus_atk0110`; added an "AMD platform → typical
  chip mapping" table spanning AM4 400, AM4 500, AM5 600, AM5 800.

#### Tests
- New `tests/test_am4_400_series_quirks.py` covers the MSI NCT6797
  CRITICAL misID quirk (anchored on both `0xd450` AND `nct6687` strings
  inside the SAME critical quirk's details — not just any-of), ASUS WMI
  AM4 board allowlist, ASRock AM4 guidance (asserts INFO severity, not
  just existence), GUI-side fallback module-conflict detection, daemon
  `module_collisions` round-trip through `parse_hardware_diagnostics`,
  the `severity` default-to-`"info"` invariant, the GUI fallback
  banner-suppression sorted-tuple canonicalisation, and the four AM4
  400-series label-fallback entries verified against upstream
  lm-sensors configs.

#### Security / robustness
- **HTML-escape daemon-supplied strings in the `module_collisions`
  banner.** The Diagnostics page renders `summary` and `remediation`
  in a Qt RichText label; the new escape pattern mirrors the existing
  revert-counts banner. Defensive even though the daemon is the user's
  own process today.
- **`ModuleCollisionInfo.severity` defaults to `"info"` (not
  `"critical"`).** Mirrors the `KernelWarning.severity` precedent so
  any future malformed entry without `severity` does not falsely
  promote to a CRITICAL banner.
- **Remediation framing rewritten** to require chip-ID verification
  (`cat /sys/class/hwmon/hwmon*/name`) before any blacklist command,
  and to show both the `blacklist nct6775` and `blacklist nct6687`
  paths as parallel alternatives. Protects users on genuine NCT6687-R
  boards from inadvertently removing their only working fan-control
  driver.

## [1.11.3] — 2026-05-08

Documentation-only release. Pairs with **daemon v1.6.5**. Documents
paru's PKGBUILD-review pager UX so first-time AUR installers know what
the "press `q`" prompt is and how to opt out for unattended installs.
No GUI code changes — same binary wheel, same API client, same
behaviour.

### Documentation
- **Install-UX tip in `README.md` and `manual/getting-started.md`
  (DEC-104).** Footnote-style note describing paru's PKGBUILD-review
  pager (the "press `q`" prompt new users see on first install) and
  how to opt out via `paru -S --skipreview` or `SkipReview` in
  `~/.config/paru/paru.conf`. Phrased as a tip, not the canonical
  install command — paru's review is a security feature and we are not
  normalising "skip review by default" for an Arch audience.
- **DEC-104 added** to the authoritative GUI `DECISIONS.md` log and
  mirrored in the daemon `DECISIONS.md`. Records the investigation
  that traced the new-user "press `q`" complaint to paru's default
  review pager (not a SHA256 mismatch — both daemon and GUI checksums
  verified), the alternatives considered (custom signed pacman repo
  rejected as disproportionate for a single-author project), and why
  the in-package fix is limited to cutting our own pager content +
  documenting paru's opt-out.

## [1.11.2] — 2026-05-08

Bug-fix release. Reverts a packaging-only regression introduced by
DEC-100 (v1.10.2): the `colorama` dependency was dropped after a
grep-based audit flagged it as "dead" because nothing under `src/` or
`tests/` directly imports it. In reality, `pyqtgraph 0.14.x`
unconditionally imports `colorama.win32` and `colorama.winterm` at
module-load time (`pyqtgraph/util/cprint.py`) — the platform check
happens *after* the imports, so the imports fire on Linux too. Arch's
upstream `python-pyqtgraph` package omits `python-colorama` from its
declared dependencies, so on a clean system that doesn't already have
`python-colorama` installed (e.g. fresh CachyOS), the GUI crashed at
launch with `ModuleNotFoundError: colorama` before Qt was even
initialised. This is the second time the same regression has happened;
DEC-103 captures the rule so future audits cannot remove it again.

### Fixed
- **GUI fails to start on clean installs (`ModuleNotFoundError:
  colorama`) (DEC-103).** Re-added `colorama>=0.4` to `pyproject.toml`
  `dependencies` and `python-colorama` to the AUR PKGBUILD `depends`
  array, with an in-PKGBUILD comment pointing to DEC-103 so the
  declaration is load-bearing and explicit. The same fix shipped in
  v1.9.0 and was incorrectly reverted by DEC-100 P1.2 in v1.10.2.

### Tests
- New `tests/test_packaging_dependencies.py`. Parses both
  `pyproject.toml` and `packaging/PKGBUILD` and asserts that each
  declares `colorama` / `python-colorama`. The failure message
  references DEC-103 and cites `pyqtgraph/util/cprint.py:6-7`, so any
  future grep-based audit attempting the same drop is caught at gate
  time with a pointer to the rationale.

### Documentation
- **DEC-103 added.** Records that `colorama` is a required transitive
  runtime dependency via `pyqtgraph` and that grep-based checks of
  `src/` cannot be the sole evidence for removing it. Supersedes the
  `pyproject.toml` / `packaging/PKGBUILD` clauses of DEC-100 P1.2 in
  the implications of v1.10.2.
- **`packaging/.SRCINFO` regenerated** against the v1.11.2 PKGBUILD so
  the in-repo metadata once again reflects the actual published
  package.

## [1.11.1] — 2026-05-08

Bug-fix release. Pairs with **daemon v1.6.4**. Stops a 1 Hz error storm
on RDNA3+ AMD GPU systems where the daemon was advertising the GPU's
read-only hwmon `pwm1` shadow as a controllable fan header.

### Fixed
- **AMD GPU hwmon `pwm1` shadow no longer offered as a fan target
  (DEC-102).** Pre-fix, the daemon advertised
  `hwmon:amdgpu:0000:XX:XX.X:pwm1:pwm1` in `GET /hwmon/headers`. The
  Controls → Edit Members picker showed it (with a cosmetic
  "(read-only)" suffix); a user binding it to a control produced one
  `503 Service Unavailable` per second forever, with the daemon log
  reporting `Permission denied (os error 13)` against
  `/sys/class/hwmon/hwmonN/pwm1`. RDNA3+ kernels expose that file
  read-only without `pwm1_enable`, so the write can never succeed —
  this is documented kernel behaviour, not a permissions bug. The fix
  has three layers:
  - The daemon excludes `chip_name == "amdgpu"` from hwmon discovery
    entirely. GPU fans are owned by the GPU subsystem (`amd_gpu:`
    prefix) only.
  - The daemon's `POST /hwmon/{header_id}/pwm` short-circuits with
    `400 feature_unavailable` when the header's `is_writable=false`,
    so any unforeseen chip with a read-only `pwmN` produces a clean
    non-retryable error instead of a misclassified 503 storm.
  - The GUI member-picker filters `is_writable=false` headers out of
    the available list (replacing the previous "(read-only)" cosmetic
    label, which still allowed selection).
- **Existing user profiles that bound the dead header are auto-repaired
  on first launch (DEC-102).** `Profile.from_dict` drops members whose
  `member_id` starts with `hwmon:amdgpu:` and re-saves the profile to
  disk. A runtime sanitizer in `MainWindow` makes the same drop against
  the live header set when the daemon's `headers_updated` signal first
  fires, catching any other read-only header pattern that doesn't match
  the syntactic shape.

### Tests
- 8 new GUI tests in `tests/test_dec102_amdgpu_hwmon_exclusion.py`
  covering the picker filter, the load-time syntactic drop, the
  re-save behaviour, and the runtime sanitizer's three branches
  (unwritable / missing / non-hwmon members).
- The daemon side gains four new integration tests in
  `daemon/tests/ipc_integration.rs` plus four reworked unit tests in
  `pwm_discovery::tests`. See daemon CHANGELOG.

## [1.11.0] — 2026-05-07

Diagnostics → Fans tab improvements. Pairs with **daemon v1.6.3**. The
PWM verify wait is doubled from 3 s to 6 s so slow-spinning fans settle
in time to be classified correctly, and the Diagnostics page now
surfaces a dedicated warning when a Gigabyte dual-chip motherboard
fails to enumerate its second ITE chip — the most common cause of
"some of my fan headers are missing" reports.

### Added
- **Dual-chip board warning banner (DEC-101).** When the daemon's new
  `/diagnostics/hardware.expected_chips` lookup names a chip that is
  not in `chips_detected` (typical case: Gigabyte X670/X870/Z790 AORUS
  boards where the secondary IT87952E silently failed to bind), the
  Fans tab now shows a clearly-marked warning naming the missing chip
  and listing the exact `mmio=on` modprobe.d remediation. Includes a
  pointer to frankcrawford/it87 issue #70 and the project Hardware
  Compatibility Guide.
- **"Verify All Writable" button (DEC-101 / 2E).** Sequentially runs
  the per-header PWM test against every writable hwmon header,
  showing per-step progress and an aggregated severity-coded summary
  at the end. Reuses the existing background verify worker, so the
  hwmon lease is only held for one verify at a time.
- **Verify-result panel mentions the dual-chip case (DEC-101 / 2F).**
  When a `pwm_value_clamped` or `no_rpm_effect` outcome lands on a
  board that's missing one of its expected chips, the result panel
  appends a one-line pointer to the dual-chip warning so users fix
  the enumeration before chasing per-header behaviour.

### Changed
- **PWM verify wait raised from 3 s to 6 s (DEC-101 / 1A).**
  Slow-spinning fans (pumps, large 140 mm chassis fans) need more
  than 3 s to settle their RPM after a PWM transition; the previous
  wait produced false `no_rpm_effect` verdicts. Coordinated with the
  daemon: the GUI's `verify_hwmon_pwm` HTTP timeout is now 12 s
  (was 8 s) and the control-loop pause-safety auto-resume is now 9 s
  (was 5 s). New regression tests (`TestVerifyTimingConstantsDec101`)
  guard the relationship `daemon_wait < pause_safety < http_timeout`
  so a future drift can't silently re-introduce the race the original
  pause was meant to prevent.
- **`it87` row in the Diagnostics → Modules table now reads
  "Mainline: No" (DEC-101 / 2C).** The *module name* `it87` ships in
  the mainline tree, but every chip we actually target requires the
  out-of-tree DKMS build, so calling the module mainline was
  misleading users running the DKMS build. The chip-level "Mainline"
  column already reported per-chip accuracy and is unchanged.

## [1.10.2] — 2026-05-07

Audit-driven hygiene pass. Pairs with **daemon v1.6.2**. No new user-visible
features — the changes either eliminate misleading internal naming, plug a
1-second UI-update delay in ad-hoc warnings, or expose a previously
swallowed daemon error to the operator.

### Fixed
- **`AppState.add_warning` / `remove_warning` now emit
  `warning_count_changed` synchronously (DEC-100).** Previously the
  external-warning list was mutated, but `active_warnings` and the badge
  count stayed stale until the next 1 Hz polling tick called
  `_update_warnings()`. Control-loop write failures, lease-loss events,
  and other ad-hoc warnings now appear in the UI without waiting for the
  next sensor refresh.
- **Verify-result UI surfaces a restore-PWM failure (DEC-100).** The
  daemon's verify endpoint now returns `restore_failed: true` when the
  post-verify restore-to-original-PWM write fails (previously the error
  was silently swallowed). The diagnostics page renders an additional
  line explaining that the header is left at the verify test value and
  that the user should re-set the desired PWM. The new field defaults to
  `False` against older daemons, so the GUI is safe to mix with v1.6.1.
- **Lease renew retry no longer overlaps with the recurring 30 s timer
  (DEC-100).** `LeaseService._renew` now stops the periodic renew
  `QTimer` for the duration of the 5 s/10 s/15 s backoff retry chain and
  restarts it once a retry succeeds. Previously a recurring tick could
  fire mid-backoff and produce a second concurrent `lease/renew` API
  call. No behavioural symptom in the wild — extra log noise and one
  redundant API call per renewal failure — but the contention was
  genuine.

### Removed
- **Dead `colorama` dependency.** Never imported in `src/` or `tests/`,
  flagged by `namcap`. Dropped from `pyproject.toml` and the AUR
  PKGBUILD.

### Tests
- 8 new tests:
  - `test_app_state.py`: synchronous emission of `warning_count_changed`
    on `add_warning` / `remove_warning`, plus the no-emit-on-acknowledged
    idempotency contract.
  - `test_lease_service.py`: recurring renew timer is suspended during
    the retry chain, restarts after a retry succeeds, and stays stopped
    after retry exhaustion.
  - `test_v1_2_diagnostics.py`: `parse_hwmon_verify_result` defaults
    `restore_failed=False` for older daemons and faithfully parses
    `restore_failed=True` from new daemons.
- `pytest tests/` — **1305 passed** (was 1297 before this pass).
- Cross-stack: pairs with daemon v1.6.2 integration tests for
  `gpu_reset_fan_records_gui_write` and the
  `HwmonVerifyResponse.restore_failed` wire format.

### Documentation
- **DECISIONS.md:** new DEC-100 (audit-pass-2 remediations: ad-hoc
  warning signal emission, verify restore_failed contract addition, GPU
  reset records gui_active, lease retry timer suspend).
- **CHANGELOG/PKGBUILD:** version bump to 1.10.2; pinned daemon dep to
  `>= 1.6.2`.

---

## [1.10.1] — 2026-05-06

Audit-driven correctness and resilience fixes. Pairs with **daemon v1.6.1**.
No new user-visible features — every change either corrects a misleading
error message, makes the GUI more honest about transient daemon load, or
gives triagers more context in the support bundle.

### Fixed
- **Verify timeout (DEC-098).** `_VerifyWorker` and the `verify_hwmon_pwm`
  client now use an 8s per-call timeout instead of the global 5s. The
  daemon's verify endpoint sleeps 3s between the test write and readback;
  worst-case round-trip under contention is ~4.5s. The previous 5s budget
  could time out client-side while the daemon completed the write
  successfully — the user saw a misleading "Daemon unavailable during
  verify" message. The worker now distinguishes timeout from disconnect
  and explains that the write may have landed.
- **Write timeout + retry (DEC-099).** `_WriteWorker` now uses a 2s
  per-call timeout (`WRITE_TIMEOUT_S`) and retries once on
  `DaemonTimeout` before counting failure. PWM writes complete in <100ms
  typically; the previous 5s default was both too long for the fast path
  and too short during the daemon's thermal-emergency override scan. The
  combined behaviour: a transient mutex-held window doesn't surface a
  spurious `write_fail` warning; sustained timeouts still do.
- **Calibrate timeout is dynamic.** `calibrate_openfan` now computes its
  per-call timeout from `(steps + 1) * hold_seconds + 10s`. The previous
  global 5s default would have failed every default calibration sweep
  (the daemon clamps to up to `21 * 15 = 315s`). No GUI caller is wired
  up yet, but the next time the fan wizard calls this it will work.
- **Stale comment in `polling.py`.** Said "API_TIMEOUT_S default 10s";
  the constant is 5.0.

### Added
- **`DaemonTimeout` error subclass.** Distinct from `DaemonUnavailable`
  so callers (and tests) can tell "the daemon is slow" from "the daemon
  is gone". `_VerifyWorker` rewrites its UI message to acknowledge that
  a timed-out verify may have actually completed on the daemon side.
- **Per-call `timeout=` kwarg on `DaemonClient._post` / `_get`.** Reuses
  the connection pool (per HTTPX docs) — no separate client instance
  per slow endpoint. Plumbed through to `verify_hwmon_pwm`,
  `calibrate_openfan`, `set_openfan_pwm`, `set_hwmon_pwm`,
  `set_gpu_fan_speed`, `reset_gpu_fan`.
- **Outcome-aware write counter (DEC-099).** `_on_write_completed` now
  takes an outcome string (`ok` / `timeout` / `unavailable` /
  `validation` / `other`) instead of a bool. The user-visible
  `write_fail` warning text now adapts to the dominant outcome — "fan
  write timed out N times — daemon may be overloaded" beats the
  previous generic "check lease/connection" line.
- **Kernel-warning popup (DEC-098).** When the daemon emits an
  `amd_gpu.kernel_warnings` entry of `high` or `critical` severity,
  the GUI surfaces a one-time `QMessageBox` with the daemon's message
  plus GUI-side detailed-text guidance and reference URLs. Dismissals
  are persisted in `app_settings.acknowledged_kernel_warnings` so the
  popup doesn't fire on every reconnect or restart.
- **`AmdGpuGuidance` knowledge base.** New model in
  `ui/hwmon_guidance.py` keyed by `KernelWarning.id`, with detailed
  explanations and references for `rdna_hang_kernel_6_19_x` (Phoronix
  EOY 2025 Linux 6.19 hard hang) and
  `smu_mismatch_navi48_r9700_kernel_7_0` (ROCm Issue #6101).
- **Support bundle: kernel context (DEC-098).** Now captures
  `os.uname()` (release/version/machine), `/proc/cmdline`,
  `/sys/module/amdgpu/parameters/ppfeaturemask`, a filtered `lsmod`
  snapshot scoped to fan/GPU drivers, and a 200-line journalctl
  excerpt grepped for `amdgpu|smu`. Triagers can now spot a 6.19
  amdgpu regression or a missing ppfeaturemask without asking the user
  to run extra commands.
- **`Capabilities.amd_gpu.kernel_warnings`** parsed from the daemon's
  new field. Empty list when the daemon is older or has nothing to
  flag — older daemons cause no UI change.

### Tests
- 31 new tests in `test_audit_2026_05_06_remediation.py`. Highlights:
  - Per-call timeout plumbing for `verify_hwmon_pwm` (8s) and
    `calibrate_openfan` (dynamic).
  - `DaemonTimeout` is distinct from `DaemonUnavailable` (subclassing
    contract).
  - `_post` / `_get` raise `DaemonTimeout` on `httpx.TimeoutException`
    and `DaemonUnavailable` on `httpx.ConnectError` — separately.
  - `_WriteWorker.do_write` retry-on-timeout matrix
    (timeout-then-success → OK; double-timeout → TIMEOUT;
    unavailable → UNAVAILABLE no retry; 4xx → VALIDATION; 5xx → OTHER).
  - **Fake-daemon integration test:** A `BaseHTTPRequestHandler` on a
    Unix socket that holds POSTs for 6s. The real `_WriteWorker`
    against the fake produces `OUTCOME_TIMEOUT` (not `UNAVAILABLE`),
    proving the categories are distinct end-to-end.
  - Outcome-aware warning messages (timeout / unavailable /
    validation each yield distinct user-visible text).
  - `parse_capabilities` round-trips `kernel_warnings`; missing/null
    fields yield empty lists (older-daemon compat).
  - Support bundle includes the new `kernel` and `kernel_modules`
    sections; `collect_kernel_modules` filters to known drivers.
  - Kernel-warning popup gating logic: acknowledged warnings are
    skipped; unacknowledged critical warnings qualify; low-severity
    warnings never pop.
  - `lookup_amd_gpu_guidance` returns guidance for known IDs and
    `None` for unknown IDs.
- Existing `test_audit_v3_p1p2_regressions.py` and
  `test_gpu_reset_on_close.py` updated to use the new outcome-string
  API instead of the legacy bool.
- 1297 tests pass (was 1266).

### Documentation
- `DECISIONS.md`: DEC-098 (legacy-PWM gate + kernel_warnings, full
  rationale + alternatives), DEC-099 (`spawn_blocking` + per-channel
  mutex + per-call timeout, full rationale).
- `AUDIT_2026-05-06_FINDINGS.md` documents the audit verdicts; this
  file is gitignored.

---

## [1.10.0] — 2026-05-02

Profile-mode safety + cross-stack parity release. Pairs with
**daemon v1.6.0**. The headline change is a GUI-side, role-aware
minimum-PWM floor that protects pump and CPU headers from being
commanded below their stall thresholds, plus a per-GPU zero-RPM
toggle the daemon now honours, plus an explicit deactivate flow so
deleting an active profile no longer leaves it driving fans.

### Added
- **Role-aware `LogicalControl.minimum_pct` (DEC-095).** When members
  are assigned or edited, the GUI now derives a default safety floor
  from the role of the control:
  - 30% for any control with a CPU- or pump-labelled hwmon member,
  - 20% for chassis / OpenFan-only controls,
  - 0% for GPU-only controls (PMFW enforces its own OD_RANGE minimum).
  The floor is recorded as `LogicalControl.minimum_pct` and the daemon's
  existing tuning pipeline already clamps curve outputs to it. The GUI
  never lowers an explicit user-set value — it only raises the floor
  when the role policy demands a higher minimum than the user has set.
- **Role-aware curve-editor floor.** The Graph editor's drag, table edit,
  keyboard nudge, and Linear/Flat spinboxes now clamp to the strictest
  floor across all controls referencing the curve, so the user cannot
  author a point that would be clamped at write time. Surfaced via a
  new `CurveEditor.set_min_output(pct)` API wired up from the controls
  page when a curve is opened for editing.
- **Minimum-PWM badge on each fan-role card.** The Controls page now
  shows `Min: NN%` on every control card, with a tooltip explaining
  the role-derived rationale (chassis 20% / CPU+pump 30%). Pumps no
  longer silently run below stall threshold because of an
  out-of-the-box curve.
- **Per-GPU `fan_zero_rpm` toggle in the Edit Fan Role dialog.** When a
  role has any `amd_gpu` member, a new section lists each GPU member
  with an "Allow zero-RPM idle" checkbox. Toggling persists onto
  `ControlMember.fan_zero_rpm` and the daemon honours the flag when
  programming the PMFW curve. Default is unchecked (existing behaviour:
  fans always spin while the curve is active).
- **Profile schema v4 with backward-compatible migration.** `Profile`
  bumps to `version: 4` on save. Loading any v3-or-older profile from
  disk applies the role-aware floor pass to every control before
  re-saving, so existing profiles are upgraded automatically on first
  load. Schema migration: any control whose members include a CPU/PUMP
  header gets `minimum_pct ← max(minimum_pct, 30)`; the rest get the
  20% chassis floor (raising 0 → 20, never lowering an explicit value).
- **`POST /profile/deactivate` daemon endpoint, wired into the GUI
  delete flow (DEC-097).** When the user deletes the daemon's active
  profile, the GUI now calls `client.deactivate_profile()` first so the
  curve stops driving fans the moment the file is gone. Idempotent on
  the daemon side; failures are logged as warnings and the local
  delete still proceeds. New `DaemonClient.deactivate_profile()`
  method, new `ProfileDeactivateResult` model, new
  `parse_profile_deactivate` helper.

### Changed
- **`ControlMember` gains a `fan_zero_rpm: bool` field** (default
  `False`) for v4 profiles. Round-trips through `to_dict` / `from_dict`
  and is persisted on disk. Ignored for non-GPU members; legacy v3
  profiles deserialise unchanged with the safe default.
- **`Profile.from_dict` runs the v4 floor migration** for v3-and-older
  inputs (including the v1 → v2 path) so every loaded profile lands at
  the current schema with role-aware minima applied.
- **Controls page member-edit accept now reapplies the role floor**
  via `apply_role_floor(control)` after the user changes membership.
  A chassis role becoming a CPU/pump role automatically tightens its
  minimum_pct to 30%.
- **Controls page profile-delete flow now calls
  `client.deactivate_profile()`** when the deleted profile was the
  active one, before unlinking the JSON file from disk. AppState's
  active-profile signal fires with `""` so the dashboard banner
  updates immediately.

### Tests
- New tests in `tests/test_profile_service.py` (10 tests): role
  inference for chassis/CPU/pump/GPU/mixed; `apply_role_floor` raise
  vs. preserve; v3→v4 migration of `minimum_pct` for CPU/pump,
  chassis, and explicit-user-value cases; `fan_zero_rpm` field
  round-trip; load-resaves-when-migrating regression.
- New tests in `tests/test_curve_editor.py` (7 tests): default floor,
  `set_min_output` storage and clamp, table-edit clamps to role
  floor, keyboard down-arrow clamps to role floor, Linear/Flat
  spinboxes inherit role floor.
- New tests in `tests/test_fan_role_dialog.py` (5 tests): GPU
  zero-RPM section visibility, one-checkbox-per-GPU-member, initial
  state from member, `get_result` includes the zero-RPM map, empty
  map for chassis-only roles.
- New tests in `tests/test_control_card.py` (4 tests):
  chassis-default badge, explicit-minimum badge, role-derived
  badge for CPU/pump, badge refresh on `update_control`.
- New tests in `tests/test_profile_activation_r24.py` (4 tests):
  `DaemonClient.deactivate_profile` exists, parser round-trips
  with-and-without `previous_profile_*`, and the controls page
  calls `deactivate_profile` only when the deleted profile was
  active.
- Existing tests updated to assert `version == 4` (3 sites) and to
  bump the sensor temperature when a curve definition changes mid-test
  (3 sites — the new daemon-side deadband would otherwise hold the
  output across these synthetic transitions).

### Why
A cross-stack audit and `/investigate-mismatch` pass found that the
GUI and daemon together had no enforced minimum-PWM floor for
motherboard pump/CPU headers, despite `docs/05` claiming a
"hardcoded 20% chassis / 30% CPU/pump" floor (which the daemon does
not implement and never has — confirmed against
`min_pwm_percent: 0` in `pwm_discovery.rs`). Industry references
(Noctua PWM whitepaper, FanControl, fancontrol(8), Arch Wiki) place
4-pin fan stall threshold at 20% PWM and AIO pump minimum at
~30–50% PWM, so the audit's recommendation to enforce these floors
GUI-side (option B) matches established practice. The daemon does
not gain a per-role policy in this release — that remains the GUI's
responsibility, preserving the "GUI owns curve safety policy,
daemon owns thermal emergency" split established by CLAUDE.md and
DEC-022.

## [1.9.3] — 2026-04-30

Packaging hygiene release. No source code changes.

### Fixed
- **`depends=` now declares `hicolor-icon-theme`.** The package installs
  `/usr/share/icons/hicolor/scalable/apps/control-ofc.svg` and the
  shipped `.desktop` file references the icon by name. Per Arch
  packaging policy any package installing into `hicolor/` should list
  `hicolor-icon-theme` so the directory exists and `gtk-update-icon-cache`
  has something to scan. Previously satisfied transitively via
  PySide6 / Qt's own dependencies — declaring it directly fixes the
  namcap **error** and ensures the icon is discoverable on minimal
  installs that don't pull in the theme any other way.
- **In-repo `packaging/.SRCINFO` regenerated.** The committed file had
  drifted to `pkgver = 1.2.0` (the AUR clone's `.SRCINFO` was current,
  but the in-repo copy was unmaintained). Regenerated against the
  current PKGBUILD.
- **`README.md` requirements section expanded** to list every Python
  runtime dependency (`PySide6`, `httpx`, `pyqtgraph`, `numpy`,
  `colorama`) plus the `hicolor-icon-theme` system dep, with a note
  on why `colorama` is required (transitive at `import pyqtgraph` time).

### Tooling
- **New `.githooks/pre-commit`** that auto-regenerates
  `packaging/.SRCINFO` whenever `packaging/PKGBUILD` is staged for
  commit, so the in-repo file cannot drift again. Opt-in via:
  `git config core.hooksPath .githooks`. The hook is a no-op when
  `makepkg` is not on PATH (e.g. on non-Arch CI).

## [1.9.2] — 2026-04-30

Patch release fixing a man-page rendering bug. No code changes.

### Fixed
- **Man page em-dash rendering.** `man control-ofc-gui` rendered every
  em-dash (U+2014) as a doubled `——` on systems with groff 1.24+ (current
  Arch). The groff 1.24 `tty.tmac` defines `.char \[em] \[em]\[em]` for
  UTF-8 output to approximate the typographic em-quad width on a
  half-width cell grid; passing literal U+2014 through scdoc therefore
  emits `——`. Replaced em-dashes in `man/control-ofc-gui.1.scd` with
  the canonical man-page convention `--`. `man control-ofc-gui` now
  reads correctly. Verified with `groff -man -K utf8 -Tutf8 -ww` (zero
  warnings) and `man -l`.

## [1.9.1] — 2026-04-30

Install-experience and documentation packaging release. No changes to
profile/theme files, daemon contract, or persisted user settings —
loading on top of an existing 1.9.0 install carries everything forward.

### Added
- **Man page ships.** `man control-ofc-gui` now renders a manual that
  documents CLI flags, environment variables, files, and the demo-mode
  flow.
- **Shell completions ship** for bash, zsh, and fish under
  `/usr/share/{bash-completion,zsh/site-functions,fish/vendor_completions.d}/`.
  Tab-completion of `control-ofc-gui --` works after install.
- **Full user manual ships in `/usr/share/doc/control-ofc-gui/manual/`** —
  the nine guides under `manual/` plus `README.md` and `CHANGELOG.md`.
  Discoverable offline via `pacman -Ql control-ofc-gui | grep manual/`.
- **First-launch dashboard hint.** When the dashboard is in the
  Disconnected state and the systemd service `control-ofc-daemon.service`
  is installed but not enabled, the dashboard shows a card with the
  exact `sudo systemctl enable --now control-ofc-daemon` command and a
  Copy button. Probe is best-effort: if `systemctl` is not on PATH or
  any probe fails, no hint is shown rather than misleading text. The
  hint never appears once the service is enabled or active. New unit /
  UI tests cover the decision matrix end-to-end.
- **Diagnostics surfaces the missing AMD GPU kernel parameter when
  `ppfeaturemask` is absent and the GPU is read-only.** Previously the
  guidance only fired when `ppfeaturemask` had a value but bit 14 was
  unset — a fresh install with no kernel arg set saw nothing. The hint
  now points at `man control-ofc-daemon` for per-bootloader detail.

### Changed
- **`post_install` message tightened.** Drops the duplicated "load
  modules" / "restart daemon" lines (already covered by the daemon's
  `post_install`) and points users at the new
  `/usr/share/doc/control-ofc-gui/manual/getting-started.md`.
- **`post_upgrade` message added.** Previously the GUI install script
  defined only `post_install`, so an upgrade transcript was silent
  even on visual / behavioural changes. The new `post_upgrade`
  reminds the user to restart any running instance and points at the
  shipped CHANGELOG.
- **Dashboard hardware-permissions hint mentions the right group per
  distro.** Previously instructed Arch users to join the `dialout`
  group, which doesn't exist on Arch (correct group is `uucp`). Now
  surfaces both group names with their distros: `uucp` on Arch /
  CachyOS, `dialout` on Debian / Ubuntu / Fedora.
- **`SECURITY.md` supported-versions table refreshed** to "1.x
  supported, < 1.0 unsupported" — was stuck at "1.0.x".

### Documentation
- **Removed stale "imperative-only daemon" claims** from
  `docs/00_README_START_HERE.md`, `docs/01_Product_Overview.md`,
  `docs/09_State_Model_Control_Loop_and_Lease_Behaviour.md`. The
  daemon has had a profile engine since the 1.x line; the V1 GUI is
  still the active controller while connected because the daemon
  defers to it (DEC-071, DEC-074), and the docs now say so.
- **Corrected thermal-safety recovery wording** in
  `docs/06_Settings_Spec.md`, `docs/18_Operations_Guide.md`, and
  `docs/19_Hardware_Compatibility.md`. The daemon's recovery is a
  60 % PWM floor applied for one cycle, not "recover at 60°C" — the
  three docs now match `safety.rs` and the daemon's man page.
- **Removed per-header safety-floor claim** in
  `docs/18_Operations_Guide.md`. The daemon reports
  `min_pwm_percent: 0` for every hwmon header; floor enforcement is
  GUI-side, not daemon-side.
- **Removed stale syslog/telemetry troubleshooting section** from
  `docs/18_Operations_Guide.md` after the R52 de-scope.

### Packaging
- Adds `scdoc` to `makedepends`. Builds the man page via
  `scdoc < man/control-ofc-gui.1.scd` in `build()` and installs to
  `/usr/share/man/man1/control-ofc-gui.1`.
- `sha256sums` switched to `SKIP` pending the post-tag-push checksum
  refresh — same pattern as previous releases.

### Cleanup
- **`PWM_VERIFY_REMEDIATION.md` is now `.gitignore`d.** Previously this
  internal `/investigate-bug` planning artifact was committed and
  shipped to the v1.9.0 GitHub release tarball. The pattern
  `*_REMEDIATION.md` catches any future variants automatically.

### Why
A `/audit documentation` pass on both repos plus a fresh
`paru -S control-ofc-daemon control-ofc-gui` test surfaced eight
install-experience defects, four of them on the GUI side. This
release fixes all four and adds a soft-landing for users who scrolled
past `post_install` (the dashboard hint) plus a more proactive
diagnostics surface for the AMD GPU kernel parameter. See the daemon
v1.5.4 entry for the matching daemon-side changes.

## [1.9.0] — 2026-04-29

Content update — refreshed visual presentation across the sidebar, About
dialog, and Settings page, plus a documentation cleanup pass. No changes
to fan control, profiles, sensors, daemon contract, or persisted
profile/theme files. Existing user settings load unchanged; legacy
display preferences in saved JSON are tolerated and ignored.

### Documentation
- **New `manual/hardware-troubleshooting.md`** covering the Diagnostics →
  Fans Hardware Readiness card, vendor quirks, Test PWM Control button
  and result interpretation, per-header pwm_enable reclaim counter
  (severity ramp), fan presence annotations, and the per-board hwmon
  header label resolver. Cross-links into `docs/19_Hardware_Compatibility.md`,
  `docs/20_Sensor_Interpretation_Guide.md`,
  `docs/21_AMD_Motherboard_Fan_Control_Guide.md`, and
  `docs/22_AMD_Sensor_Interpretation_Deep_Dive.md`.
- **Manual updates for v1.7.0–v1.8.0 features that previously had no user
  docs.** `manual/diagnostics.md` now covers the Sensors tab Chip /
  Confidence columns and the Fans tab Control method column / fan
  presence annotation. The page also points users at the new
  hardware-troubleshooting page from a top-of-page callout.
- **README and manual TOC link to motherboard / sensor reference docs.**
  Top-level `README.md` Documentation section and `manual/README.md`
  now surface the four `docs/19/20/21/22` reference docs that previously
  had no entry point from the user-facing manual.
- **Removed stale parody/microcopy/splash language from internal docs.**
  `docs/01_Product_Overview.md`, `docs/03_UX_UI_Principles_and_Visual_System.md`,
  `docs/12_Implementation_Plan_and_Module_Structure.md`,
  `docs/13_Acceptance_Criteria.md`, and
  `docs/16_User_Decisions_and_API_Notes_Reference.md` are now consistent
  with the de-branded v1.9.0 visual direction.

### Fixed
- **In-app Hardware Compatibility Guide link points at the correct
  GitHub org.** Diagnostics → Fans → Hardware Readiness previously
  hard-coded `github.com/control-ofc/control-ofc-gui` (404). Now uses
  `Plan-B-Development/control-ofc-gui`.
- **README hero image and `manual/getting-started.md` clone URL.** README
  was referencing a non-existent `screenshots/dashboard.png`; clone URL
  in getting-started used the `your-org` placeholder.
- **`manual/getting-started.md` no longer documents a splash screen** that
  was removed in v1.9.0.

### Tooling
- **`scripts/capture_screenshots.py`** dropped its imports of the deleted
  `control_ofc.ui.microcopy` and `control_ofc.ui.splash` modules (which
  caused the script to crash before producing any screenshots) and now
  injects synthetic hardware diagnostics into the Diagnostics → Fans
  capture so the Hardware Readiness card renders populated. The
  `16_splash_screen` expected file was removed from
  `scripts/build_manual.sh`. All 15 manual screenshots regenerated.
- **`DemoService.hardware_diagnostics()`** new factory returning a
  populated `HardwareDiagnosticsResult` (Gigabyte X870E AORUS MASTER /
  IT8696E / RDNA3 PMFW). Used by the screenshot tooling; not consumed by
  the live `--demo` UI (the diagnostics fetch path still requires a
  daemon client). Covered by two new tests in `tests/test_demo.py`.

## [1.8.0] — 2026-04-28

Truthfulness pass triggered by an X870E AORUS MASTER report that PWM fan
control was "not working". The board controls correctly; the user-visible
problem was three downstream presentation defects in the GUI plus a
documentation gap. This release fixes the GUI side. See
`PWM_VERIFY_REMEDIATION.md` for the investigation and the approved plan.

### Fixed
- **Control loop no longer races the daemon's verify wait (A1).** Clicking
  *Test PWM Control* on Diagnostics now pauses the GUI's 1 Hz control-loop
  writes to the header under verify for the duration of the daemon's 3 s
  test. Previously the control loop's next tick landed during the wait
  and the daemon classifier mis-attributed the change to "BIOS/EC" (a
  false `pwm_value_clamped`). A 5 s safety auto-resume guarantees a hung
  verify cannot pin the header. New `pause_writes_for_header` /
  `resume_writes_for_header` API on `ControlLoopService`. Wired through
  Diagnostics' new `verify_started` / `verify_completed` signals in
  `MainWindow`.

### Added
- **Fan presence classification across Diagnostics, Controls, and Fan
  Wizard (A2).** New `FanPresence` enum (`PRESENT` / `EMPTY_HEADER` /
  `READ_ONLY` / `PWM_ONLY` / `UNKNOWN`) computed purely from
  daemon-supplied `FanReading` + `HwmonHeader` fields. Diagnostics →
  Fans now appends "no fan detected" to the RPM cell for writable
  hwmon headers reading 0 RPM (the dominant X870E AORUS MASTER case
  where 7 of 8 PWM headers are unpopulated). Controls fan-role member
  picker decorates the same way so users do not accidentally assign
  curves to empty headers.
- **Per-board hwmon header label resolver (A3).** New three-tier
  resolution: alias > daemon-supplied sysfs label > `/etc/sensors.d`
  and `/usr/share/sensors/*` chip-block labels > in-repo fallback
  table. Seeded with the X870E AORUS MASTER's IT8696E (5 verified
  silkscreen labels: CPU_FAN, SYS_FAN1..3, CPU_OPT) and IT87952E
  (3 best-guess labels marked `(unverified)` until silkscreen tracing
  confirms). New minimal libsensors-syntax parser recognises `chip`,
  `label`, and `ignore` directives — sufficient for every Gigabyte /
  ASUS / MSI fan-header config in the wild without tracking the full
  libsensors grammar. `AppState.fan_display_name` now consults the
  resolver when the daemon's sysfs label is empty.

### Tests
- `tests/test_control_loop.py::TestVerifyPause` — 8 new tests covering
  pause / resume / safety auto-resume / overlapping pauses /
  end-to-end paused-during-cycle.
- `tests/test_fan_presence.py` — 16 tests covering all four classified
  states plus presentation-data invariants.
- `tests/test_fan_presence_integration.py` — Diagnostics fan-table,
  Controls member picker, and Fan Wizard surface tests.
- `tests/test_hwmon_label_resolver.py` — 34 tests covering the
  libsensors parser, fallback table, resolver priority chain, and
  cache behaviour.
- `tests/test_app_state.py` — new `fan_display_name` integration tests
  for the resolver chain (sysfs → libsensors → fallback → alias
  override).

## [1.7.1] — 2026-04-25

Operator-experience patch: surface BIOS pwm_enable reclaim activity directly
on the Diagnostics → Hardware tab so AORUS-class users can see the
EC-vs-Linux contention without reading `journalctl`. Pairs with **daemon
v1.5.2**, which throttles the matching log line. GUI-only changes; daemon
pin unchanged.

### Added
- **Per-header reclaim count surfacing in Diagnostics → Hardware.** The BIOS
  interference card now renders one row per affected hwmon header with a
  severity colour ramp tied to the count: 0 = OK (green), 1–9 = WARN (amber),
  ≥10 = HIGH (red). The card headline takes the highest severity across all
  headers, so the operator's eye lands on the hot fan first.
- **Auto-shown vendor guidance for Gigabyte + IT8696E.** The matching
  `VendorQuirk` card (existing, unchanged text) is surfaced when the daemon
  reports both `vendor` containing "gigabyte" and a chip prefix of `it8696`
  in `/diagnostics/hardware`, so the BIOS Smart Fan 6 fix path is one
  glance away from the live evidence of the contention.

### Changed
- **Reclaim card layout split into headline + body + footnote.** The single
  `Diagnostics_Label_revertCounts` was split into
  `Diagnostics_Label_revertHeadline` (severity-coloured summary),
  `Diagnostics_Label_revertCounts` (per-row rich-text body), and
  `Diagnostics_Label_revertFootnote` (the "watchdog auto-recovers" hint).
  The new node names are stable test rendezvous points.
- **Forward-compat with pre-1.3 daemons.** GUI now uses `getattr` and
  defaults `enable_revert_counts` to `{}` on the rendering side as well as
  on the parsing side, so the new Diagnostics tab does not break on older
  daemons that omit the field.

### Tests
- New `tests/test_diagnostics_hardware_render.py` — 30 tests covering the
  classifier (K∈{0, 1, 5, 9, 10, 50, 10_000}), per-row colour mapping,
  rich-text rendering, HTML escaping for quirky chip names, the
  Gigabyte+IT8696 quirk auto-show, and tolerance of daemons that omit
  `enable_revert_counts` entirely.
- Updated `tests/test_v1_2_diagnostics.py::TestDiagnosticsPageRevertCounts`
  to assert the watchdog-explanation footnote on its new dedicated label.

## [1.7.0] — 2026-04-24

Diagnostics enumeration truthfulness pass (Batch A of the remediation
tracked in `DIAGNOSTICS_REMEDIATION.md`). GUI-only; pairs with **daemon
v1.5.1**; daemon pin stays `>=1.5.0` because Batch A uses daemon fields
(`HwmonHeader.is_writable`, `AmdGpuCapability.fan_control_method`,
`HwmonDiagnostics.writable_headers`, `BoardInfo.vendor`,
`SensorReading.chip_name`/`temp_type`) that already exist in the
`>=1.5.0` API surface — no new wire contract.

### Changed
- **Diagnostics → Fans now deduplicates GPU/hwmon overlap (DEC-047).** On
  systems where `amdgpu` exposes a GPU fan through both `amd_gpu:<BDF>` and
  `hwmon:...:<BDF>:fan1`, the Diagnostics fans table previously rendered
  two rows for the same physical fan while the dashboard rendered one.
  Diagnostics now shares the dashboard's `filter_displayable_fans` rule,
  so both views agree. Multi-GPU systems (different BDFs) remain as
  separate rows.
- **Diagnostics → Fans has a new "Control method" column.** Answers the
  "which fans are controllable and how" question directly rather than
  requiring the user to read per-row tooltips. Values are derived from
  daemon-reported typed data only (`HwmonHeader.is_writable`,
  `AmdGpuCapability.fan_control_method`) — no heuristic inference, and
  unclassified fans render literally as `unknown`. Column cell tooltips
  give a plain-English explanation per method; the read-only tooltip
  points users at Test PWM Control to verify BIOS/EC interference.
- **Diagnostics → Overview reconciles hwmon capability with runtime
  reality.** Once hardware diagnostics is fetched, the Overview hwmon
  line becomes `Present (N headers — ALL read-only)` with a warn chip
  when `writable_headers == 0`, matching the dashboard banner behaviour.
  The Features line annotates `hwmon writes` as
  `(daemon-supported; 0 writable headers on this system)` in that state
  so the daemon-code-level flag isn't mistaken for runtime writability.
- **Diagnostics → Sensors table shows Chip and Confidence columns.** The
  existing `sensor_knowledge.classify_sensor` infrastructure (already
  used by the dashboard sensor panel) is now wired into the Diagnostics
  Sensors tab. Rows expose the driver/chip name and confidence level
  on-screen; per-row tooltips surface source class, description, and
  driver quirks (e.g. the ASUS NCT6776F CPUTIN bogus caveat from
  `docs/20_Sensor_Interpretation_Guide.md`). Board vendor flows from
  `/diagnostics/hardware` into classification so board-specific quirks
  light up after the user fetches diagnostics.

## [1.6.1] — 2026-04-23

Follow-up audit remediation on v1.6.0. Pairs with **daemon v1.5.1**; daemon
pin stays `>=1.5.0` because the GPU-error envelope rename is a wire-level
refinement clients only notice when they route on the envelope code.

### Changed
- **Profile-search-dir registration runs on the polling worker thread.**
  Previously wired to `state.capabilities_updated`, which fires on the Qt
  main thread. On a slow or half-dead daemon the synchronous HTTP call
  could stall the UI for up to `API_TIMEOUT_S` (10s). The registration
  now lives inside `PollingService._PollWorker`, running in the same
  first-poll / reconnect block as `/capabilities`. No user-visible
  behaviour change other than a responsive UI during daemon hiccups.
- **Docs: `/sensors/history?last=N` corrected.** Documentation previously
  described "250 samples max"; the daemon defaults to 250 but caps at
  1000 server-side. Both values are now documented.
- **Docs: new and missing error-envelope codes.** `feature_unavailable`
  (introduced on the daemon side for GPUs without a fan write path) and
  `too_many_clients` (SSE transport cap) are now documented in
  `docs/08_API_Integration_Contract.md`.

### Added
- **Profile load failures surface to Diagnostics.** `ProfileService.load`
  now returns per-profile `(path, error)` pairs; `main.py` converts each
  into a `state.add_warning(level="warning", source="profile_service", …)`.
  A corrupted `~/.local/share/control-ofc-gui/profiles/*.json` is now
  visibly flagged rather than silently dropped from the UI.

### Removed
- **`ControlLoopService.write_performed` signal.** Declared, emitted, and
  tested but never consumed by production UI code. The emission on the
  async write path was also premature — it fired before the HTTP write
  actually completed. Deleted along with its test assertion; write
  success/failure is still tracked via `_on_write_completed` and surfaced
  as a Diagnostics warning after repeated failures (unchanged).

### Fixed
- **P1-1 GPU error envelope (paired with daemon 1.5.1).** The daemon now
  returns HTTP 400 `feature_unavailable` (retryable:false) instead of
  the contract-violating 400 `hardware_unavailable` (retryable:true) when
  a GPU exists but has no fan write path. Clients that route on the
  envelope code must handle the new code as permanent — the condition is
  non-retryable for that device.

## [1.6.0] — 2026-04-23

Contract-mismatch remediation (15-item cross-stack sweep). Pairs with
**daemon v1.5.0**; the Arch PKGBUILD bumps the daemon pin to `>=1.5.0`
because the daemon-side tuning-pipeline port (M1) is the feature that keeps
headless profile-mode output identical to GUI-driven output. See
`docs/23_Contract_Mismatch_Backlog.md` for the full investigation trail.

### Added
- **M8: `DaemonClient.activate_profile` accepts `profile_id`.** The client
  now takes either a `profile_path` (positional or keyword, canonical for
  on-disk profiles) or a keyword-only `profile_id` (for daemon-bundled
  profiles). Exactly one is required — `ValueError` otherwise. Matches the
  daemon's documented `/profile/activate` contract, which already supported
  both shapes; the GUI previously only emitted `profile_path`.
- **M9: GPU fan reset on GUI close when no profile is active.**
  `MainWindow._maybe_reset_gpu_on_close` calls `client.reset_gpu_fan` when
  `AppState.gui_wrote_gpu_fan` is set and no profile is active, so the GPU
  doesn't stay pinned to the last commanded PWM after the GUI exits. Skipped
  when a profile is active — the daemon's profile engine takes over after
  the 30s GUI-active heartbeat lapses. Uses cached state to avoid a blocking
  API call during close; failures are logged, not surfaced.
- **M11 (GUI half): tolerate `pci_id` and `pci_bdf` on both endpoints.**
  New `_coalesce_pci_bdf` helper in `api/models.py` accepts either PCI
  field name from `/capabilities.amd_gpu` and `/diagnostics/hardware.gpu`
  so GUI code can use whichever dataclass field exists during the daemon's
  transition window. Paired with the daemon emitting both names.
- **`docs/23_Contract_Mismatch_Backlog.md`** — investigation working doc
  tracking all 15 items through to shipped status.
- **Regression test: `tests/test_gpu_reset_on_close.py`** — covers the
  M9 reset-on-close policy branches.

### Changed
- **M4: Lease acquisition gated on `hwmon.present`.**
  `ControlLoopService._maybe_acquire_lease` no longer asks for a lease on
  OpenFan-only or GPU-only systems where the daemon would return
  `503 hardware_unavailable` every cycle. Acquisition retries automatically
  when hwmon transitions from absent → present (e.g. after
  `/hwmon/rescan` finds a controller).
- **M5: Profile search dir re-registers on reconnect.** Registration is
  now wired to `state.capabilities_updated` so it runs after every
  successful poll — covering daemon-up-at-startup, daemon-down-at-startup,
  and daemon-restart-while-GUI-runs. Previously a one-shot at startup
  could leave the first `/profile/activate` failing with
  "profile_path must be within a profile search directory".
- **M10: Demo `min_pwm_percent` 30 → 0.** Demo hwmon headers now match
  the daemon's real behaviour (`min_pwm_percent: 0` for all; per-profile
  soft floors are GUI-side).
- **M14: `test_acquire_failure` uses a reachable error code.** Switched
  from `lease_already_held` (which `POST /hwmon/lease/take` never returns —
  it force-takes) to `hardware_unavailable` so the test exercises a real
  failure path.
- **Docs refresh (M2/M3/M6/M7/M15).** `docs/08_API_Integration_Contract.md`
  and `docs/14_Risks_Gaps_and_Future_Work.md` updated: stale PWM-floor
  claims replaced with "pass-through; safety floors are GUI-side",
  `stall_detected` documented in `/fans` fields, `chip_name` annotated as
  always-present (`amdgpu` for GPU sources), `openfan.channels` (=10)
  added to `/capabilities` notable fields.

## [1.5.2] — 2026-04-22

Audit remediation. GUI-side fixes pair with daemon v1.4.2 for the
contract-level change (P1 verify strings) and the hwmon-phase `gui_active`
extension (DEC-093). Requires **daemon >= 1.4.0**; the Arch PKGBUILD now pins
this explicitly.

### Fixed
- **P1: Verify result strings never matched daemon output.** The diagnostics
  PWM verify panel's `status_map` used short keys (`"reverted"`, `"clamped"`)
  that the daemon never emits — every real BIOS/EC override was being
  displayed as `"Unknown result: pwm_enable_reverted"`. Fixed the keys to the
  daemon's actual return strings (`pwm_enable_reverted`, `pwm_value_clamped`)
  and removed the dead pre-lookup remap. Replaced the false-positive test with
  one that uses the daemon's real payload, and added a missing regression for
  the clamped case.
- **P2: Polling fallback could emit partial success.** When the batch `/poll`
  endpoint failed and individual fallback calls raised mid-way, `status_ready`
  was emitted before `sensors`/`fans` were fetched — downstream freshness
  calculations briefly reported a fresh status with stale fans. The fallback
  now fetches all three before emitting so each cycle is atomic.
- **P3: Narrow bare excepts in fan wizard.** `FanWizard.stop_fan` and
  `FanWizard.restore_fan` caught `Exception` broadly; now they catch
  `(DaemonError, DaemonUnavailable, OSError, ConnectionError)` so unrelated
  bugs are no longer silently suppressed.
- **P3: pytest warning on unknown `asyncio_mode`.** Removed the unused
  `asyncio_mode = "auto"` setting and the corresponding `pytest-asyncio` dev
  dependency — no tests use asyncio.

### Changed
- **P2: Move `verify_hwmon_pwm` off the UI thread.** The 3-second hardware
  probe ran synchronously on the Qt main thread, freezing the rest of the GUI
  (polling, splitter, menus). It now runs on a dedicated `_VerifyWorker`
  QThread that emits `verify_ok`/`verify_error` signals back to the main
  thread. `DiagnosticsPage.cleanup()` stops the thread on window close.
- **Arch PKGBUILD pins the daemon dep.** `depends=` now lists
  `control-ofc-daemon>=1.4.0` so installing an older daemon with this GUI
  version fails at install time instead of degrading silently.
- **Contract documentation.** `docs/08_API_Integration_Contract.md` and
  `CLAUDE.md` now list `persistence_failed` (503), annotate
  `lease_already_held` as hwmon-PWM-write-only, and add an explicit "Trust
  model" section noting that the 0666 socket relies on a trust-the-local-
  machine assumption.

### Added
- **DEC-093 — Profile engine defers hwmon writes when GUI is active.** Also
  captured in the new daemon `DECISIONS.md`. The hwmon phase now mirrors the
  OpenFan (DEC-074) and GPU (DEC-071) phases by skipping writes when the GUI
  has written in the last 30s, closing a narrow startup/lease-lapse race.
- **Daemon-side unit coverage for `gui_active()`** (three tests) and a
  missing `/poll` integration test that locks in the top-level response shape
  the GUI consumes.
- **Regression tests** for the verify-string fix, the polling fallback
  atomicity, and the verify worker-thread re-enable paths.

## [1.5.1] — 2026-04-22

### Changed
- **Diagnostics Fans tab: resizable tables.** The chip table and kernel modules
  table in the Hardware Readiness section are now separated by a draggable
  splitter instead of fixed max heights. Gives more space to whichever table
  needs it (e.g., 15-row modules list no longer cramped while 2-row chip table
  wastes space).

## [1.5.0] — 2026-04-21

Table UX polish, vendor/sensor documentation, and audit remediation.

### Added
- **Resizable table columns across the app.** Diagnostics (4 tables),
  dashboard fan table, warnings dialog, fan wizard (2 tables), and curve
  editor now use `Interactive` resize mode with `stretchLastSection`, so
  users can drag column borders.
- **Diagnostics Fans tab: vertical splitter** between Hardware Readiness and
  Fan Status, non-collapsible, matching the controls page pattern.
- **Rich tooltips on diagnostics tables.** Header tooltips on all three
  tables (chips, modules, fans); per-cell tooltips on fan rows surface
  chip/driver context for hwmon fans and GPU context for `amd_gpu` fans.
- **Clickable driver documentation links** in the chip guidance section
  (rendered as rich text HTML). A Hardware Compatibility Guide link appears
  when hardware chips are detected.
- **New user-facing docs:** `docs/21_AMD_Motherboard_Fan_Control_Guide.md`
  (vendor-by-vendor fan control setup, drivers, quirks, troubleshooting) and
  `docs/22_Sensor_Interpretation_Deep_Dive.md` (sensor class deep dive,
  confidence model, hwmon quirks explained). Reading order updated in doc 00.
- **23 new tests** covering resize modes, splitter properties, header/cell
  tooltips, rich-text format, and doc link visibility.

### Changed
- **Doc 19 (ASRock correction)** and **doc 20** updated with NCT6686D notes,
  MSI 7-point curve, Gigabyte MMIO/force_id/ACPI fix options, nct6683 AMD
  board list, source label enumeration, SB-TSI address mapping, and
  sensors-detect issue references.

### Fixed
- **P0-1: APP_VERSION drift.** Replaced hardcoded version constant with
  `importlib.metadata` lookup so the GUI version stays in sync with
  `pyproject.toml` (was stuck at 1.0.5 for 6 releases).
- **P0-2: Control loop lease acquisition.** Check `acquire()` return value
  and proceed on success instead of unconditionally skipping the first
  hwmon write cycle. Lease is now acquired proactively at `start()`.
- **P1-3 / P1-4 / P2-4: Narrow exception handlers.** Fan wizard restore,
  settings import, and 9 handlers in the settings page now catch specific
  exception tuples (`DaemonError`, `OSError`, `JSONDecodeError`,
  `UnicodeDecodeError`) instead of bare `Exception`, and include error
  details in log messages.
- **P2-2: Warning dismissal.** Stop clearing warning first-seen timestamps
  on dismiss; let the existing pruning in `_update_warnings()` handle stale
  key removal.
- **P2-chart: Dashboard cleanup.** Added `dashboard_page.cleanup()` to
  release the chart `SignalProxy` on app shutdown, wired from
  `MainWindow.closeEvent`.
- **P3-4: V1 profile migration log level.** Lowered duplicate-fan log from
  warning to info.

## [1.4.0] — 2026-04-21

Sensor interpretation knowledge base and session tracking for dashboard
temperature categories. Requires daemon v1.4.0.

### Added
- **Session min/max tracking.** Dashboard summary cards now show session
  low/high for each temperature category, giving at-a-glance awareness of
  thermal range since GUI launch.
- **Sensor interpretation knowledge base** (`sensor_knowledge.py`). Classifies
  sensors by driver, label, and temp_type with confidence levels (high,
  medium_high, medium, low). Covers k10temp, coretemp, sbtsi_temp, nct6775
  family, nct6683/6686/6687, it87 family, asus_ec_sensors, asus_wmi_sensors,
  gigabyte_wmi, amdgpu, and nvme drivers.
- **Board-specific sensor override database** for validated sensor placements.
  Initial entries for ASUS Crosshair VIII, ASUS STRIX X670E, ASRock X670E,
  and Gigabyte B550.
- **Rich sensor tooltips** in series panel showing classification, session
  stats, rate of change, and provenance (driver name, confidence level).
- **New daemon API fields:** `chip_name` and `temp_type` for sensor metadata
  enrichment in `/sensors` and `/poll` responses.

### Changed
- Demo mode sensors now include realistic `chip_name` values and stable IDs.
- `SummaryCard` widget supports optional session range sub-label.
- Sensor series panel tooltips enhanced with knowledge base classification.

## [1.3.0] — 2026-04-21

Comprehensive vendor/driver knowledge base expansion for AMD motherboard fan
control. Covers ASUS, MSI, ASRock, and Gigabyte quirks with actionable
guidance. Requires daemon v1.3.0.

### Added
- **NCT6686D chip guidance** — ASRock A620/B650/X670 boards with in-kernel
  nct6683 driver monitoring but incomplete PWM write support. Points users to
  out-of-tree nct6686d and asrock-nct6683 drivers.
- **ASUS EC/WMI sensor driver entries** — `asus_ec_sensors` and
  `asus_wmi_sensors` chip guidance. Clearly marks these as sensor-enrichment
  drivers (not PWM write paths) and warns about high-frequency WMI polling
  risks on affected ASUS boards (PRIME X470-PRO specifically called out).
- **ASUS WMI polling risk vendor quirk** (high severity) — warns that frequent
  WMI polling can trigger fan stop, fan max, or stuck sensor readings on some
  ASUS BIOS implementations.
- **MSI X870/B850 system fan quirk** (high severity) — system fans may not
  respond to single PWM writes; documents `msi_fan_brute_force=1` module
  parameter and `/etc/modprobe.d/nct6687.conf` persistence.
- **ASRock NCT6686D/NCT6683 quirks** (medium severity) — read-vs-write
  mismatch guidance with board-specific out-of-tree driver recommendations.
- **Gigabyte ITE `ignore_resource_conflict` quirk** (info) — recommends
  driver-local conflict resolution over system-wide `acpi_enforce_resources=lax`.
  Warns against using `force_id` in production.
- **Module conflict detection** — `detect_module_conflicts()` checks for
  conflicting driver combinations (e.g. nct6683 + nct6687 both loaded).
  Displayed with critical styling in diagnostics when detected.
- **Post-verification guidance** — after running the PWM verification test,
  the result now includes vendor-specific next-step recommendations based on
  the board and chip context (e.g. Gigabyte IT8689E Rev 1 no-workaround notice,
  ASRock out-of-tree driver suggestion, MSI brute-force parameter).
- **Improved ACPI conflict tip** — for ITE chips, recommends the safer
  driver-local `ignore_resource_conflict=1` parameter before the system-wide
  `acpi_enforce_resources=lax` kernel parameter.
- **hwmon tooltips on dashboard fan table** — hovering a hwmon fan now shows
  chip name, driver, mainline status, and PWM mode (DC/PWM).
- **hwmon tooltips on controls member editor** — hwmon headers in the fan role
  member editor show chip/driver context on hover.
- **Gigabyte degenerate fan curve values** in IT8689E BIOS tips — specific
  7-point workaround values (PWM 40/Temp 0-90-90-90-90-90-90, final point
  PWM 100/Temp 90).
- Enriched NCT6683 chip entry with MSI/ASRock-specific known issues.
- 29 new tests covering all new chip entries, vendor quirks, module conflict
  detection, and verification guidance.

### Changed
- **Daemon**: `asus_wmi_sensors` added to tracked kernel modules alongside
  existing `asus_wmi_ec_sensors` (both module name variants now detected).
- Chip knowledge base expanded from 13 to 17 entries.
- Vendor quirk database expanded from 6 to 12 entries.
- MSI NCT6687 vendor quirk test updated for dual-result (medium + high).

## [1.2.0] — 2026-04-21

PWM interference detection, board identification, and vendor quirk guidance.
Requires daemon v1.3.0.

### Added
- **Board identification** in Hardware Readiness card. Displays motherboard
  vendor, model, and BIOS version from DMI sysfs, giving immediate context
  for chip-specific guidance.
- **Vendor quirk alerts** in Hardware Readiness card. When the detected board
  vendor + Super I/O chip matches a known problematic combination (Gigabyte
  SmartFan 5/6 + ITE chips, MSI Smart Fan + NCT6687, ASUS ACPI + NCT679x),
  a severity-colored alert shows the specific issue and workaround steps.
- **BIOS interference detection.** Displays per-header `pwm_enable` revert
  counts from the daemon's watchdog, showing how often the BIOS/EC has
  reclaimed fan control since the daemon started.
- **PWM verification test** ("Test PWM Control" button). Writes a test PWM
  value to a selected header, waits 3 seconds, and reads back to classify
  the result as effective/reverted/clamped/no_rpm_effect/rpm_unavailable.
  Requires an active hwmon lease.
- **Vendor quirk knowledge base** (`hwmon_guidance.py`). Six vendor+chip
  entries covering Gigabyte IT8689E/IT8696E/IT8688E/IT8686E, MSI NCT6687,
  and ASUS NCT679x, with severity levels, summaries, and detailed workaround
  steps.
- **Support bundle enhancement.** Exports board info and BIOS interference
  data (revert counts) when hardware diagnostics have been fetched.

### Changed
- `HwmonDiagnostics` model now includes `enable_revert_counts` field.
- `HardwareDiagnosticsResult` model now includes `board` (BoardInfo) field.
- New models: `BoardInfo`, `HwmonVerifyState`, `HwmonVerifyResult`.
- `DaemonClient.verify_hwmon_pwm()` method for the new
  `POST /hwmon/{header_id}/verify` endpoint.

## [1.1.0] — 2026-04-21

Hardware readiness and diagnostics feature release. Requires daemon v1.2.0.

### Added
- **Hardware Readiness section** in Diagnostics → Fans tab. Shows detected
  hwmon chips, expected drivers, load status, ACPI I/O port conflicts, kernel
  module state, GPU diagnostics (ppfeaturemask, overdrive, PMFW), and thermal
  safety rule status. One-click "Refresh Hardware Diagnostics" fetches live
  data from the new `GET /diagnostics/hardware` daemon endpoint.
- **Chip-family knowledge base** (`hwmon_guidance.py`). Maps Super I/O chip
  prefixes (Nuvoton NCT679x/NCT6687, ITE IT8688E/IT8689E/IT8696E, Fintek
  F718xx, SMSC SCH56xx) to driver info, BIOS tips, known manufacturer quirks,
  and external documentation links. Guidance is displayed contextually in the
  Hardware Readiness card.
- **Dashboard hwmon info banner.** Shows a contextual notification when no
  motherboard fan headers are detected, or when all detected headers are
  read-only, with a link to Diagnostics → Fans for guidance.
- **Read-only hwmon header labels** in Controls page member editor. Non-writable
  hwmon headers now show "(read-only)" suffix, matching the existing GPU
  read-only pattern.
- **`device_id` field** on `HwmonHeader` model — aligns with daemon's per-header
  device identification.
- **GPU capability fields** `pci_device_id`, `pci_revision`, `gpu_zero_rpm_available`
  on `AmdGpuCapability` model — aligns with daemon v1.2.0 capabilities response.
- **`show_hardware_guidance` setting** — allows users to toggle hardware guidance
  display (default: enabled).
- **48 new tests** covering chip guidance lookup, driver status formatting,
  hardware diagnostics parsing, UI population, dashboard banner behavior,
  and settings persistence.

### Changed
- Diagnostics → Fans tab now uses a scroll area with the Hardware Readiness
  card above the existing fan status table.
- DiagnosticsPage now accepts an optional `client` parameter for direct
  daemon API calls (hardware diagnostics endpoint).

## [1.0.6] — 2026-04-17

Code quality and robustness hardening from full audit pass.

### Fixed
- **Exception-unsafe `blockSignals` pairs.** 14 bare `blockSignals(True)`/
  `blockSignals(False)` pairs across 6 widget files could leave signals
  permanently blocked if an exception occurred between them. Replaced with
  a `block_signals()` context manager (`ui/qt_util.py`) that guarantees
  restore via `try/finally`.
- **Overly broad exception catches.** `PollingService` and `ControlLoopService`
  caught bare `Exception`, masking programming errors. Narrowed to specific
  types (`DaemonError`, `ConnectionError`, `OSError`, `KeyError`, `ValueError`).
- **QThread shutdown ignores timeout.** Both `PollingService` and
  `ControlLoopService` called `thread.wait(2000)` without checking the return
  value. Now logs a warning and terminates the thread if it does not stop
  within 2 seconds.
- **Profile activation shows empty error.** When `POST /profile/activate`
  failed with a `DaemonError` whose `message` was empty, the error dialog
  displayed "Activation failed: ". Now falls back to `'unknown error'`.
- **Profile activation leaves combo in wrong state on error.** The profile
  combo box snapped to the new selection before the API call succeeded. Now
  reverts to the previous selection on both API and unexpected errors.
- **Batch poll fallback test used bare `Exception`.** Updated to use
  `DaemonError` to match the narrowed except clause.

### Added
- **Per-sensor freshness indicators on dashboard.** Sensor cards now show
  `⏱` (stale, 2–10s) or `⚠` (invalid, >10s) suffixes with age tooltips,
  matching the freshness model in `docs/04_Dashboard_Spec.md`.
- **Theme CSS property classes.** Added `.SectionTitle` and `.SmallLabel`
  classes to the theme stylesheet, replacing 5 hardcoded `font-size` inline
  styles across `about_dialog`, `fan_wizard`, `dashboard_page`, and
  `theme_editor`.

### Changed
- **Stronger type annotations.** Replaced `object` type hints with specific
  types (`DaemonClient`, `AppSettingsService`, `ProfileService`,
  `TimelineChart`) in `settings_page`, `diagnostics_page`, and
  `sensor_series_panel` using `TYPE_CHECKING` imports.

## [1.0.5] — 2026-04-17

### Changed
- **Streamlined install messages.** `post_install` now includes daemon
  enable command, sensor module loading hint, and demo mode reference.
  Cross-references daemon package for users who installed the GUI first.

## [1.0.4] — 2026-04-16

### Fixed
- **GUI fails to start on clean installs (ModuleNotFoundError: colorama).** pyqtgraph 0.14.0 unconditionally imports `colorama` at module load, but Arch's `python-pyqtgraph` package omits it from its dependencies. On systems where no other package pulls in `python-colorama` (e.g. clean CachyOS), the GUI crashed at import time before Qt was even initialized. Added `python-colorama` to PKGBUILD `depends` and `colorama>=0.4` to `pyproject.toml` `dependencies`.

## [1.0.2] — 2026-04-11

### Fixed
- **Profile activation delay (30–60s) — GUI side.** Re-activating the already-active profile after editing its curves (or activating fresh shortly after startup) could leave OpenFan fans running at the previous speed for up to a minute. Root cause was twofold: `AppState.set_active_profile()` suppresses `active_profile_changed` when the name is unchanged, so `ControlLoopService._on_profile_changed` (which resets hysteresis and forces a cycle) never fires; and the daemon's profile engine was deferring writes under `gui_active`, leaving a dead zone where neither writer pushed new values. GUI fix: new public `ControlLoopService.reevaluate_now()` explicitly invoked by `ControlsPage._on_activate()` and `DashboardPage._on_apply_profile()` after `state.set_active_profile()`. It resets the hysteresis anchors, clears any manual override, and immediately runs `_cycle()`, bypassing the suppressed-signal path. `MainWindow` now threads `control_loop` into `DashboardPage` and rewires both pages when demo mode lazily constructs its control loop. Regression tests in `tests/test_control_loop.py::TestReevaluateNow`. The matching daemon fix (activation refreshes `record_gui_write`) is tracked in the daemon CHANGELOG.

### Added
- **`.github/workflows/release-aur.yml`** — GitHub Actions workflow that publishes to the AUR automatically when a release tag (`v*.*.*`) is pushed. Uses the same strict verify-and-fail guards as `scripts/release-aur.sh`: refuses to publish if `packaging/PKGBUILD` was not bumped before tagging, or if its `sha256sums` does not match the GitHub release tarball. Delegates the AUR clone/commit/push to [`KSXGitHub/github-actions-deploy-aur@v4.1.2`](https://github.com/KSXGitHub/github-actions-deploy-aur), which runs inside an Arch container and regenerates `.SRCINFO` automatically. Requires a one-time `AUR_SSH_PRIVATE_KEY` repository secret. The existing `scripts/release-aur.sh` remains as a manual fallback. Release flow: bump `packaging/PKGBUILD` → commit → `git tag v1.0.2 && git push origin main v1.0.2`.
- **`scripts/release-aur.sh`** — local release script that syncs `packaging/PKGBUILD` to the AUR. Verifies the GitHub tarball sha256 matches the PKGBUILD before clone/push, clones (or ff-pulls) `ssh://aur@aur.archlinux.org/control-ofc-gui.git` into `~/Development/aur/control-ofc-gui/`, regenerates `.SRCINFO` via `makepkg --printsrcinfo`, and commits/pushes with explicit confirmation prompts (`--yes` to skip, `--no-push` to stage only). Run from the repo root as `./scripts/release-aur.sh <version>` after bumping `packaging/PKGBUILD`.

## [1.0.1] — 2026-04-10

### Fixed
- **Profile activation on fresh installs.** The GUI now auto-registers its profile directory with the daemon on startup via `POST /config/profile-search-dirs`. Previously, `POST /profile/activate` rejected every profile with `"profile_path must be within a profile search directory"` because the daemon's default search paths (`/etc/control-ofc/profiles`, `/root/.config/control-ofc/profiles`) never matched the GUI's XDG config dir. The call is idempotent (the daemon deduplicates and persists) and failures at startup are logged but non-fatal so offline/demo mode still works. Regression tests in `tests/test_profile_search_dir_registration.py`.

## [1.0.0] — 2026-04-08

Content update establishing the package layout, paths, and identifiers used
by the GUI from this release onward (`control-ofc-gui` package,
`control_ofc.*` import path, `~/.config/control-ofc/` config dir, and the
`/run/control-ofc/control-ofc.sock` daemon socket).

### Removed
- **`httpx-sse` dependency removed.** The planned `EventStreamService` (DEC-024) was never wired up and the dependency was unused. The GUI continues to use the 1 Hz polling loop for all data. The daemon still exposes `GET /events` for other clients; GUI consumption is tracked as deferred work in `docs/14_Risks_Gaps_and_Future_Work.md`. See DEC-023 and DEC-024 for the updated rationale.

## [0.86.4] — 2026-04-08

### R71 — Fix dashboard timeline chart AttributeError (PlotCurveItem regression)

**Bug:** `AttributeError: 'PlotCurveItem' object has no attribute 'setClipToView'` raised on every poll cycle (~1Hz), preventing the dashboard chart from displaying any data.

**Root cause:** R66 (V5 Phase 2 remediation, finding F6) added `item.setClipToView(True)` to RPM `PlotCurveItem` instances. The audit incorrectly stated that `PlotCurveItem` supports this method — it does not in pyqtgraph 0.14.0. `setClipToView()` exists only on `PlotDataItem`, which is a sibling class, not a parent.

**Fix:** Removed the invalid `setClipToView(True)` call. No performance regression — `update_chart()` already manually clips data to the visible time range via numpy masking before passing it to `setData()`.

**Changes:**
- Removed `item.setClipToView(True)` from RPM item creation in `timeline_chart.py`
- Corrected F6 finding in V5 Phase 2 audit doc and release readiness doc
- Fixed stale test docstring in `test_performance_r48.py`
- Added 10 new tests: update_chart() error-free completion, item type verification, data population, multi-cycle regression test

## [0.86.3] — 2026-04-08

### R70 — Pre-release Security Hardening (V5 Phase 6)

Addresses findings from the V5 Phase 6 security & dependencies audit.

**S1 (P1) — Import validation:**
- Profile and theme import now validates data via `Profile.from_dict()` and `ThemeTokens` construction before writing to disk. Invalid entries are skipped with a warning and the user is notified of the count. Prevents corrupt data from silently persisting.
- Added 12 tests: profile/theme validation unit tests + SettingsPage integration tests.

**S2 (P2) — Dev dependency CVEs:**
- Updated pip (25.3 → 26.0.1) and pygments (2.19.2 → 2.20.0) to resolve CVE-2026-1703 and CVE-2026-4539. Neither is a production runtime dependency.

**S7 (P3) — Support bundle privacy notice:**
- Export Support Bundle button now shows tooltip: "Includes system configuration and daemon logs. Review before sharing."

**S6 (P3) — Text input length limits:** Deferred. All profile/curve/control names use UUID-based filenames; user text never becomes a filename except for themes. Risk is cosmetic, not a security vulnerability.

## [0.86.2] — 2026-04-08

### R69 — Pre-release Test Quality Remediation (V5 Phase 4)

Addresses all P1, P2, and P3 findings from the V5 Phase 4 test quality audit.

**Rust daemon (+7 tests, 289 → 296):**
- **T1 (P1):** Added `ScriptedSysfsWriter` mock and 3 sysfs write failure tests — verifies enable-fail, PWM-fail-after-enable, and error propagation without cache corruption
- **T2 (P1):** Added 3 async integration tests for `profile_engine_loop()` — verifies normal profile evaluation writes, thermal safety override forcing all 10 channels to 100%, and clean shutdown
- **T4 (P2):** Added thermal safety oscillation test — verifies hysteresis keeps override locked during boundary oscillation

**Python GUI (+8 tests, 776 → 784):**
- **T3 (P1):** Added full-field profile persistence roundtrip test — verifies curves, controls, members, and all tuning parameters survive save/load
- **T5 (P2):** Added write failure recovery test (3 failures → warning → 3 successes → warning cleared) and lease acquire-on-expired test
- **T6 (P2):** Added label content tests for diagnostics — verifies daemon version and status text display, not just CSS transparency
- **T7 (P2):** Added outcome state assertions — verifies `_write_failure_counts` dict correctly increments/decrements
- **T9 (P3):** Added PollingService lifecycle smoke test with real `__init__` path

**Accepted/deferred:**
- T8 (P3): Lease timing tests accepted as-is — 5:1 margin is stable
- T10 (P3): Low UI rendering coverage deferred — visual paths, not control logic

**Coverage changes:** control_loop.py 72→74%, polling.py 67→81%, profile_service.py 94→95%.

## [0.86.1] — 2026-04-08

### R68 — Pre-release API Contract Cleanup (V5 Phase 3)

Resolves F2 and F4 from the V5 Phase 3 cross-boundary API contract audit.

- **F2 (P2):** Updated 4 test fixture files to match daemon's current response schemas. Removed telemetry fields, corrected `ipc_transport` from `"unix_socket"` to `"uds/http"`, removed defunct safety floor and telemetry range fields from limits, added `amd_gpu`/`aio_hwmon`/`aio_usb` device entries to capabilities fixtures.
- **F4 (P3):** Fixed timeline_chart.py docstring — replaced "telemetry" with "sensor".
- Bumped version 0.85.0 → 0.86.1 (incorporates missed 0.86.0 pyproject.toml bump from R66).

## [0.86.0] — 2026-04-07

### R66: Pre-release Python GUI Quality Remediation (V5 Phase 2)

Resolved all 21 findings from the V5 Phase 2 code review audit.

**Quality gate fixes (F10, F9):**
- Fixed 5 formatting violations and 9 lint errors (unused imports, lambda assignment, unsorted imports, try-except-pass patterns)

**P1 — Error handling & truthfulness (F1-F3):**
- `client.py`: JSON decode errors now raise `DaemonError(code="parse_error")` instead of unhandled `ValueError`
- `control_loop.py`: Write worker unexpected errors now logged with full traceback via `log.exception()`
- `settings_page.py`: Startup delay sync failure now shows truthful status message instead of silent swallow

**P1 — Performance (F4-F6):**
- `controls_page.py`: Sensor dropdown only rebuilt when sensor list changes (cached ID set); RPM updates moved to `fans_updated` signal
- `diagnostics_page.py`: Sensor and fan tables reuse `QTableWidgetItem` objects instead of recreating ~100 items/second
- `timeline_chart.py`: RPM chart items now use `setClipToView(True)` to avoid rendering off-screen data

**P1 — Naming (F7):**
- Renamed `TestPage` to `IdentifyFanPage` in fan_wizard.py — eliminates PytestCollectionWarning

**P2 — Code quality (F8, F13, F17, F21):**
- `client.py`: `set_startup_delay()` and `update_profile_search_dirs()` now return typed `StartupDelayResult`/`ProfileSearchDirsResult` models
- `polling.py`: Active profile query failure logged at WARNING (was DEBUG)
- `history_store.py`: Empty deques pruned from `_series` dict to prevent unbounded growth
- `microcopy.py`: Unknown keys now logged at WARNING instead of silently returning raw key

**P2 — Theme consistency (F14-F16):**
- Replaced hardcoded `font-size` in theme_editor.py (3x), warnings_dialog.py, series_chooser_dialog.py (2x), curve_editor.py with theme CSS classes (`.PageSubtitle`, `.ValueLabel`)
- Added `.ValueLabel` CSS class to theme stylesheet
- `CurveEditor` and `CurveCard` now receive theme updates via `set_theme()` — wired through `controls_page` from `theme_changed` signal

**P3 — Cleanup (F18-F20):**
- `main.py`: Replaced manual `sys.argv` parsing with `argparse` (adds `--help`, proper error handling)
- `sidebar.py`: Brand fallback text uses `.PageTitle` CSS class instead of hardcoded 16px
- `curve_card.py`: Preview line color uses `theme.accent_primary` instead of hardcoded `#4a90d9`

**Coverage improvements:**
- Added 46 new tests across 4 new test files
- `curve_edit_dialog.py`: 0% → 95%
- `polling.py`: 19% → 67%
- `client.py`: 38% → 46%
- `draggable_flow.py`: 34% → 62%
- Overall: 71% → 85% (776 tests, 0 failures, 0 warnings)

## [0.85.0] — 2026-04-07

### R65: Four Configurable Application Settings

- **Feature:** Fan Wizard spin-down timer configurable (5-12s, default 8s) — Settings → Application
- **Feature:** Daemon startup delay configurable (0-30s, default 0) — persisted to daemon via `POST /config/startup-delay`
- **Feature:** Auto-hide iGPU sensors toggle — when disabled, iGPU temperatures shown alongside dGPU
- **Feature:** Auto-hide unused fan headers toggle — when disabled, all hwmon fan headers shown regardless of RPM
- All four settings persist in `app_settings.json` and take effect immediately (except startup delay: next restart)
- GPU fans always shown regardless of toggle (DEC-047 preserved)
- 4 new settings roundtrip tests, 730 total

## [0.84.0] — 2026-04-07

### R64: Daemon API for Profile Search Dirs (supersedes DEC-084 direct writes)

- **Breaking change:** GUI no longer writes `daemon.toml` directly. Profile directory changes are sent to the daemon via `POST /config/profile-search-dirs` API endpoint. The daemon writes its own config (no permission issues).
- Added `update_profile_search_dirs()` client method.
- Removed `daemon_config_writer.py` (superseded by daemon API).
- Settings → Application "Browse..." for profiles now calls daemon API. If disconnected, shows manual config message.
- DEC-084 superseded by DEC-087 (GUI uses daemon API for config changes).

## [0.83.0] — 2026-04-07

### R62: Profile Activation, Switching, Per-Profile Ownership, Configurable Paths

**Four related profile-system defects resolved:**

#### Bug fixes
- **Profile activation no longer fails** — daemon profile search dirs are now configurable via `daemon.toml` `[profiles] search_dirs`, fixing the HOME directory mismatch between the root daemon and the GUI user (DEC-083)
- **Profile selection now works** — `_on_profile_selected()` no longer calls `_refresh_profile_combo()` which was destroying the user's selection by snapping back to the active profile
- **Per-profile content visible** — switching profiles now loads that profile's curves and controls; creating a new profile shows a blank slate
- **Daemon path validation hardened** — both incoming path and search directories are now canonicalized before comparison (CWE-22 fix)

#### New features
- **Configurable data directories** — profiles, themes, and default export paths configurable from Settings → Application with directory picker dialogs (DEC-086)
- **File migration on directory change** — optional move of existing files to new directory with confirmation dialog
- **Daemon.toml auto-update** — GUI writes daemon.toml `[profiles]` section when profile directory changes, with fallback to manual instructions on permission denied (DEC-084)

#### Tests added
- 6 profile switching tests (selection, preservation, blank slate, isolation, activation labels, deletion fallback)
- 9 configurable paths tests (overrides, roundtrip, daemon config writer, TOML formatter, permission handling)
- 3 daemon config tests (profiles section parsing, defaults, optional section)

#### Decisions
- DEC-083: Daemon profile search dirs configurable via daemon.toml
- DEC-084: GUI writes daemon.toml for profile directory sync
- DEC-085: Per-profile ownership of curves and controls confirmed
- DEC-086: Configurable data directories in GUI Settings

## [0.80.0] — 2026-04-03

### R61: Fan Wizard — Fix Startup-Blocking Recursion

**Root cause:** The R60 dynamic page approach called `removePage()` from within `_create_test_pages()`, which was called from `nextId()`. Qt's `removePage()` triggers `nextId()` re-evaluation → infinite recursion → `RecursionError` → app crash.

**Fix:** Reverted to a **single TestPage** that cycles through fans internally. No dynamic page creation or removal. No `removePage()` calls. Zero re-entrant `nextId()` risk.

- TestPage now manages fan cycling via an internal "Save Label & Next Fan" button
- `advance_to_next_fan()` increments the fan index and re-initializes the page
- `isComplete()` returns `True` only when all fans are tested (controls QWizard's Next button)
- `nextId()` is simple and side-effect-free: Discovery → Test → Review
- Page IDs changed to sequential: 0 (Intro), 1 (Discovery), 2 (Test), 3 (Review)

**What R60 got right (preserved):**
- Zero-RPM filtering (`not fan.rpm` catches None and 0)
- amdgpu hwmon skip (not writable via pwm)

**What R60 got wrong (removed):**
- Dynamic `_create_test_pages()` with `removePage()` inside `nextId()`
- `PAGE_REVIEW = 9999` (reverted to 3)
- Page ID arithmetic for `current_target()` (reverted to simple index)

## [0.79.0] — 2026-04-03

### R60: Fan Wizard — Navigation Fix, Zero-RPM Filter, amdgpu Skip

**Critical fix — "Next" navigation stuck (D):**
- **Root cause:** `nextId()` returned the same page ID (`PAGE_TEST_BASE = 100`) for every fan. QWizard tracks visited page IDs and refuses to revisit them — producing `"QWizard::next: Page 100 already met"` warnings and blocking navigation.
- **Fix:** Test pages are now created dynamically with unique IDs (`PAGE_TEST_BASE + 0`, `+1`, `+2`, ...) via `_create_test_pages()` called when leaving the Discovery page. Each fan gets its own `TestPage` instance. `PAGE_REVIEW` moved to 9999 to stay above all test pages.
- **Log evidence:** The test WAS running successfully (stop → 5s → restore, all 200 OK) but the user couldn't advance to the next fan.

**Zero-RPM filtering (A):**
- Changed filter from `fan.rpm is None` to `not fan.rpm` — now excludes both `None` AND `0` (disconnected headers reporting zero RPM).

**amdgpu hwmon skip (A):**
- Fans with `source == "hwmon"` and `"amdgpu"` in their ID are now excluded from the wizard target list. These are GPU hwmon entries that are not writable via the pwm path (GPU fans use PMFW `fan_curve` instead). Previously caused `Permission denied` on restore.

**Tests (4 new, 711 → 715 total):**
- `test_fans_with_zero_rpm_excluded` — rpm=0 fans excluded
- `test_amdgpu_hwmon_excluded` — amdgpu hwmon entries excluded
- `test_creates_test_pages_for_selected_fans` — dynamic page creation verified
- `test_next_id_chains_through_test_pages` — page ID chaining verified

## [0.78.0] — 2026-04-03

### R59: Fan Wizard — Text, Filtering, Test Errors, Restore Policy

**Restore policy (D):**
- Wizard now restores fans to their **prior PWM** (captured at wizard entry via `last_commanded_pwm`) instead of forcing 100%. Falls back to 30% when prior state is unknown (`last_commanded_pwm` is None).
- `_restore_all_fans()` now delegates to `restore_fan()` for all sources including GPU (was previously missing GPU restoration).
- Intro text updated: "fans will be restored to their prior speed" replaces "restored to 100%".
- End-of-test and abort messages updated accordingly.

**Detected fans filtering (B):**
- `_build_targets()` now skips fans where `rpm is None`. Only fans with tachometer readings appear on the discovery page.

**Start test error surfacing (C):**
- `stop_fan()` now returns `str | None` — error message on failure, `None` on success.
- `_start_test()` checks the return value and shows the error in the status message if the fan stop fails, instead of silently proceeding with the timer.

**Progress bar fix:**
- Format changed from `"%vs remaining"` (showed elapsed) to `"%v / %m seconds"`.

**Tests (8 new, 703 → 711 total):**
- `test_fans_without_rpm_excluded` / `test_fans_with_rpm_included` — RPM filtering
- `test_stop_fan_returns_none_on_success` / `test_stop_fan_returns_error_on_no_client` — error surfacing
- `test_restore_uses_prior_pwm` / `test_restore_fallback_30_when_no_prior` — restore policy
- `test_build_targets_captures_prior_pwm` — prior state capture
- `test_restore_gpu_fan_fallback_30_when_no_prior` — GPU fallback path

## [0.77.0] — 2026-04-02

### R58: Color Dialog — Clear App Stylesheet During Dialog

**Root cause (confirmed via runtime measurement):** The app's global `QWidget { background-color: ...; color: ...; font-size: ... }` stylesheet rule cascades into QColorDialog's internal custom-painted widgets and **cannot be overridden by any dialog-level `setStyleSheet()`** — Qt's CSS specificity gives the app-level `QWidget` selector equal priority on unnamed internal child widgets. All previous fixes (R54 `DontUseNativeDialog`, R55 `setMinimumSize`, R56 `SetDefaultConstraint`, R57 dialog-level stylesheet isolation) were necessary but insufficient because the cascade cannot be broken from within the dialog.

**Runtime evidence:**
- With app stylesheet active: `sizeHint` = 140x412, 28 internal widgets constrained to 30px max-width
- With app stylesheet cleared: `sizeHint` = 522x418, 1 widget constrained (correct behavior)

**Fix:** Temporarily clear the app stylesheet before opening QColorDialog, restore it immediately after `exec()` returns:
```python
saved = app.styleSheet()
app.setStyleSheet("")
dlg = QColorDialog(initial, self.window())
dlg.setOption(DontUseNativeDialog)
result = dlg.exec()
app.setStyleSheet(saved)
```

Also changed parent from `self` (32x24px ColorSwatch with `max-width: 30px`) to `self.window()` to avoid parent size constraints bleeding into the dialog.

Previous R54-R57 workarounds (layout constraint override, minimum size, dialog stylesheet) removed as they are no longer needed — the dialog renders at its natural correct size (522x418) when the app stylesheet is cleared.

## [0.76.0] — 2026-04-02

### R57: Color Dialog — Stylesheet Cascade Isolation

**True root cause found:** The app's global `QWidget { background-color: ...; color: ...; font-size: ... }` stylesheet rule (theme.py:279) cascades into every internal widget of `QColorDialog`, corrupting its custom-painted widgets (color spectrum, hue strip, saturation/value gradient, preview patch). These widgets render as flat colored rectangles at minimum size instead of their intended appearance. This is a [known Qt footgun](https://forum.qt.io/topic/85202) with broad `QWidget` selectors.

Previous fixes (R54 `DontUseNativeDialog`, R55 `setMinimumSize`, R56 `SetDefaultConstraint`) were all correct and necessary but addressed the dialog shell — not the internal widget painting corruption.

**Fix:** Isolate the `QColorDialog` from the app stylesheet by applying a dialog-level stylesheet that targets only the dialog frame, buttons, labels, and inputs — not the custom-painted color picker widgets:
```python
dlg.setStyleSheet(
    f"QColorDialog {{ background-color: {bg}; color: {fg}; }}"
    f" QPushButton {{ ... }} QLabel {{ ... }} QSpinBox, QLineEdit {{ ... }}"
)
```

Colors are read from the app's current palette at runtime, so the dialog frame matches the active dark theme while the color picker internals render correctly.

## [0.75.0] — 2026-04-02

### R56: Color Dialog — SetFixedSize Layout Override

**Root cause found:** Qt's `QColorDialog` constructor internally calls `mainLay->setSizeConstraint(QLayout::SetFixedSize)`, which silently ignores all subsequent `setMinimumSize()` and `resize()` calls. The R54/R55 fixes correctly added `DontUseNativeDialog` and `setMinimumSize(550, 400)`, but the internal layout constraint overrode them.

**Fix:** After creating the `QColorDialog` instance, override the layout constraint before showing:
```python
dlg.layout().setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
dlg.setMinimumSize(550, 400)
dlg.resize(550, 400)
```

`SetDefaultConstraint` restores normal QDialog sizing where `setMinimumSize()` and `resize()` work correctly. The dialog is now resizable via drag handles and opens at a usable 550x400 default size.

Both color picker paths fixed: theme editor (`theme_editor.py`) and chart series (`sensor_series_panel.py`).

## [0.74.0] — 2026-04-02

### R55: Theme Dialog, Fan Table, Copy Errors, Deferred Features

**Theme colour dialog (A):**
- Switched from static `QColorDialog.getColor()` to instance-based `QColorDialog` with `setMinimumSize(550, 400)`. The static method doesn't expose the dialog widget for size control — the instance-based approach allows enforcing a usable minimum size while keeping `DontUseNativeDialog`.

**Dashboard fan table (B):**
- All 4 columns now use `QHeaderView.ResizeMode.Stretch` for even spacing. Previously columns 0-1 stretched while 2-3 used `ResizeToContents`, causing uneven distribution.

**Copy Last Errors (C):**
- New "Copy Last Errors" button on Diagnostics → Event Log tab. Filters event log for `error` and `warning` level entries, formats them with timestamp/source/message, and copies to system clipboard. Shows count or "No recent errors" feedback.

**Deferred (D+E):**
- **Reconnect Controller:** Deferred — daemon already handles serial reconnection automatically (5 consecutive errors → exponential backoff). No endpoint or GUI button needed.
- **One-click diagnostics redaction:** Deferred — no redaction logic exists. Implementing partial PII scrubbing would give false security confidence. Documented as future work.

**Tests (3 new, 701 → 703 total):**
- `test_copy_errors_button_exists` — button present and enabled
- `test_copy_errors_filters_events` — only errors/warnings copied, info excluded
- `test_copy_errors_empty_shows_message` — feedback when no errors exist

## [0.73.0] — 2026-04-02

### R54: Color Dialog Usability + Startup Page Truthfulness

**Color dialog fix:**
- Both color pickers (theme editor + chart series) now use `QColorDialog.DontUseNativeDialog` flag. This renders a properly-sized, resizable Qt-styled color dialog instead of the tiny native platform dialog that was unusable on Linux.
- Affects: `theme_editor.py` (theme token colors) and `sensor_series_panel.py` (chart series colors)

**Startup page nav mismatch fix:**
- On startup with `restore_last_page=True`, the sidebar now correctly highlights the restored page instead of always showing Dashboard as selected. Root cause: `page_stack.setCurrentIndex()` was called without `sidebar.select_page()`.

**Tests (3 new, 698 → 701 total):**
- `test_theme_editor_color_dialog_flag` — regression: DontUseNativeDialog in theme editor
- `test_sensor_series_panel_color_dialog_flag` — regression: DontUseNativeDialog in series panel
- `test_sidebar_matches_restored_page` — regression: sidebar selection matches restored page index on startup

## [0.72.0] — 2026-04-01

### R52: Syslog / Telemetry De-Scope

**Product direction:** Syslog/telemetry export is no longer included. Full audit-led removal across both GUI and daemon.

**Daemon (de-scoped):**
- Deleted `telemetry/` module entirely (5 files, ~1,133 lines): syslog.rs, queue.rs, aggregator.rs, exporter.rs, mod.rs
- Removed `TelemetryConfig` from daemon config. Existing `daemon.toml` files with `[telemetry]` section will now fail to parse (intentional — forces cleanup of stale config).
- Removed `/telemetry/status` and `/telemetry/config` API endpoints
- Removed `TelemetryConnectionState`, `TelemetryStats` from health state
- Removed telemetry from staleness computation, cache updates, capabilities response
- 49 telemetry tests removed (301 → 252 daemon tests)

**GUI (de-scoped):**
- Removed Settings → Syslog tab (host/port/publish config, apply/check buttons)
- Removed Diagnostics → Telemetry tab (6 status labels)
- Removed Dashboard telemetry subsystem label
- Removed `TelemetryStatus`, `TelemetryConnectionState`, `TelemetryConfigResult` from models
- Removed `telemetry_status()` and `set_telemetry_config()` from API client
- Removed telemetry signal/field/setter from AppState, polling, demo service
- 23 telemetry tests removed, `test_syslog_wiring_r24.py` deleted (721 → 698 GUI tests)

**What remains unchanged:**
- All non-telemetry diagnostics (daemon status, controller status, GPU status, journal)
- Import/export of settings, profiles, and themes
- Support bundle generation (still includes journal, fan state, capabilities)
- All fan control, safety, and hardware features

## [0.71.0] — 2026-04-01

### R51: Persistence, Import/Export, Diagnostics, Support Bundle

**Diagnostics (P0 fix):**
- **Journal unit name mismatch:** System Journal button filtered by `control-ofc-daemon.service` (doesn't exist) instead of `control-ofc-daemon` (the actual systemd unit). Fixed in code and spec doc. Journal retrieval now returns real daemon logs.

**Support bundle improvements (P1):**
- **Journal logs included:** Support bundle now calls `fetch_journal_entries()` and includes daemon journal output as a `journal` key.
- **Fan state snapshot:** Bundle now includes `fan_state` array with RPM, last_commanded_pwm, and age_ms for all fans.
- **Missing sections tracking:** When daemon is unavailable or data is missing, bundle includes a `missing_sections` list explaining what was omitted and why (instead of silent omission).

**Import/export improvements (P2):**
- **Export version validation:** Import now rejects files with `export_version > 1` with a clear error message instead of silently applying incompatible data.
- **All custom themes exported:** Export now includes all custom themes from `themes/` directory (was: only active theme). Import restores all themes.
- **Theme import wiring:** `_import_themes()` method added; import now applies settings, profiles, AND themes from exported bundle.

**Tests (6 new, 715 → 721 total):**
- `test_journal_uses_correct_unit_name` — regression test verifying `control-ofc-daemon` in journalctl command
- `test_bundle_includes_journal` — bundle has `journal` key with log output
- `test_bundle_includes_fan_state` — bundle has `fan_state` array
- `test_bundle_missing_sections_when_no_daemon` — bundle reports omitted sections
- `test_export_includes_export_version` — export has version field
- `test_export_includes_all_custom_themes` — all themes in export

## [0.70.0] — 2026-04-01

### V4 Audit G4: Test Hardening — Critical-Path Coverage

**8 new tests** covering failure paths that had zero coverage (707 → 715 total):

- **Profile error handling:**
  - `test_load_corrupted_json_does_not_crash` — invalid JSON on disk → defaults loaded, no crash
  - `test_from_dict_empty_dict_uses_defaults` — `from_dict({})` → valid profile with auto-ID
  - `test_from_dict_missing_controls_and_curves` — minimal dict → empty controls/curves, name preserved

- **Control loop write failures:**
  - `test_daemon_unavailable_caught_in_write` — `DaemonUnavailable` (subclass of `DaemonError`) → caught, targets_skipped incremented
  - `test_write_failure_accumulation_triggers_warning` — 3 consecutive failures → state warning with target ID and count
  - `test_hwmon_write_with_stale_lease_handled` — daemon rejects expired lease_id → error caught, failure counted

- **Lease service disconnect:**
  - `test_daemon_unavailable_on_renew_clears_lease` — `DaemonUnavailable` during lease renewal → lease cleared, `lease_lost` signal emitted

- **Hysteresis lifecycle:**
  - `test_manual_override_exit_resets_hysteresis` — enter then exit manual override → `_target_states` cleared, fresh evaluation on next cycle

## [0.69.0] — 2026-04-01

### V4 Comprehensive Code Audit

**Daemon (v0.5.4) safety fixes:**
- **Fix (P0):** Daemon now restores `pwm_enable=2` (automatic) for all hwmon headers on shutdown. Previously only GPU fans were reset — motherboard fans could be stuck in manual mode after a daemon crash.
- **Fix (P1):** Thermal safety override now logs errors at ERROR level instead of silently discarding them. Failed writes during thermal emergency use "THERMAL SAFETY" prefix for operator visibility.

**Documentation alignment (G2):**
- CLAUDE.md: Corrected safety floor claim — per-header floors are active (20% chassis, 30% CPU/pump), not removed. Added GPU fan write endpoints (`/gpu/{gpu_id}/fan/pwm`, `/gpu/{gpu_id}/fan/reset`) to API reference.
- `docs/08_API_Integration_Contract.md`: Added GPU fan write section with PMFW constraints, 5% threshold, zero-RPM behavior. Added GPU safety behaviors.
- `docs/09_State_Model_Control_Loop_and_Lease_Behaviour.md`: Added GPU fan behavior to mixed-source control section. Documented per-backend write suppression and daemon deferral rules (DEC-070, DEC-071, DEC-073, DEC-074).

**Dead code removal (G3):**
- Removed `set_openfan_target_rpm()` from `client.py` — method was never called (daemon endpoint exists, GUI never wired RPM targeting). `SetRpmResult` and `parse_set_rpm` kept in `models.py` (independently tested API contract parsing).
- Removed 5 unused signals from `ControlCard` (`mode_changed`, `manual_output_changed`, `curve_selected`, `edit_members_requested`, `renamed`) and their `.connect()` calls in `controls_page.py` — declared but never emitted (vestiges of planned inline editing, refactored to dialogs).
- Removed `last_session_path()` from `paths.py` — no session restoration feature exists.
- Removed 2 unused conftest fixtures (`fake_client_no_openfan`, `capabilities_no_telemetry`) and orphaned `FeatureFlags` import.
- Full removal log: `docs/DEAD_CODE_REMOVAL_LOG_V4.md`

**Audit deliverables:**
- Updated `docs/COMPREHENSIVE_AUDIT_REPORT.md` (V4)
- Instruction files for deferred work: G4 (test hardening), G5 (quality gates), G6 (minor daemon fix), G7 (efficiency)

## [0.68.0] — 2026-03-31

### R50: Daemon Persisted-State Hardening

**Daemon (v0.5.4) fixes:**
- **Root cause:** `daemon_state.json` writes failed with `EROFS (Read-only file system, os error 30)` because systemd `ProtectSystem=strict` blocked writes to `/var/lib/control-ofc` — the service file was missing `StateDirectory=control-ofc` and the path wasn't in `ReadWritePaths`
- **Service file:** Added `StateDirectory=control-ofc` (systemd creates/manages the directory) and `/var/lib/control-ofc` to `ReadWritePaths`
- **Configurable state directory:** New `[state] state_dir` in `daemon.toml` (default: `/var/lib/control-ofc`). Supports custom paths for testing, containers, or non-standard layouts
- **Implementation:** `daemon_state.rs` rewritten to use `OnceLock<String>` for runtime-configurable state path, initialized from config before any load/save operations
- **Impact:** Profile persistence now works correctly under systemd sandbox — active profile survives daemon restart and reboot

**Write-path sanity check (daemon v0.5.4 continued):**
- **hwmon coalescing:** Added per-header write state tracking. Identical PWM writes now produce 0 sysfs ops (was 4/sec/header). `pwm_enable` written once per lease, not every call. State resets on lease release.
- **OpenFan gui_active:** Profile engine defers OpenFan writes when GUI active (last 30s), matching existing GPU behavior. Prevents dual-writer contention.
- **Audit confirmed safe:** sysfs scalar parsing correct, hwmon lease prevents dual writes, serial mutex prevents concurrent writes, reconnect is write-free.
- 4 new daemon tests (301 total)

**GUI (v0.68.0):**
- Documentation updates: Operations Guide, architecture docs, Risks/Gaps, DECISIONS.md (DEC-072, DEC-073, DEC-074)
- No GUI code changes required — daemon API contract unchanged

## [0.67.0] — 2026-03-31

### Live Issue Fix: GPU Fan Write Suppression — Stuttering Root Cause

**Root cause:** The daemon wrote the full PMFW fan_curve (8 sysfs writes) EVERY SECOND even when the fan speed hadn't changed. Each write triggers GPU firmware (SMU) processing that briefly stalls the display pipeline. Journal logs confirmed: `Disabled GPU fan zero-RPM` + `Wrote 5 PMFW fan curve points` repeating at 1Hz. KWin errors confirmed GPU contention: `atomic commit failed: Device or resource busy`.

**User evidence:** Stuttering occurs after 10-15 min of gaming (Rocket League). Persists when GUI minimized. Persists when GUI closed. Stops ONLY when daemon stopped. Starts again within 10-15 min when daemon restarted.

**Daemon (v0.5.2) fixes:**
- **`disable_zero_rpm()` now idempotent**: Reads current `fan_zero_rpm_enable` value before writing. If already "0", skips the write entirely. Eliminates 2 redundant sysfs writes per second.
- **GPU fan API handler write suppression**: Compares requested speed with `cache.gpu_fans[id].last_commanded_pct`. If identical, returns success immediately without touching sysfs. Eliminates 6 redundant writes per second.
- **Profile engine GPU write suppression**: Checks cached `last_commanded_pct` before spawning the blocking write task. Skips when unchanged.
- **v0.5.2 (first attempt)**: Write suppression added but ineffective because:
1. `fan_zero_rpm_enable` sysfs returns multi-line formatted output — `trim() == "0"` never matched
2. GUI control loop AND profile engine both wrote simultaneously (dual-writer conflict)
3. Temperature fluctuation of 0.5°C during gaming produced 1% PWM changes that passed the old threshold

**v0.5.2 (corrected)**:
- `disable_zero_rpm` parses multi-line sysfs format correctly (looks for value after header)
- Profile engine skips GPU writes when GUI was active in last 30s (GUI takes priority)
- GPU fan API handler uses 5% minimum change threshold (flat PMFW curves don't benefit from 1% granularity)
- **Net result**: zero sysfs writes during stable gaming. One write per 5%+ speed change.

## [0.65.0] — 2026-03-30

### R49: Graph Lines Regression Fix — Chartable Keys Fallback, RPM Revert, Structure Stability

**Root causes:**
1. **R45 `_chartable_keys()` returned empty on startup**: When the selection model was not yet seeded (first timer tick before sensor data arrives), `known_keys()` returned `set()` → no series created → never recovered. Fixed: fall back to `history.series_keys()` when selection model is empty.
2. **R48 RPM PlotDataItem on secondary ViewBox**: `PlotDataItem` does not render correctly when added to a bare `ViewBox` (it expects a `PlotItem` parent). Fixed: reverted RPM items to `PlotCurveItem` (the proven pattern for secondary axis).
3. **Sensor panel `structure_changed` oscillation**: Compared unfiltered `new_ids` with filtered `_known_sensor_ids`, causing True every poll when iGPU exists → unnecessary rebuild every second. Fixed: filter BEFORE comparing.

5 new tests in `test_graph_regression_r49.py`: fallback to history, selection model used when populated, no-selection uses history, RPM type, structure stability. **707 total passing.**

## [0.64.0] — 2026-03-30

### R48: Microstutter Investigation — Chart Performance & Visibility Gating

**Root causes confirmed:**
1. Chart timer ran unconditionally 1Hz even when dashboard was hidden or app unfocused
2. Antialiasing enabled (20-40% extra CPU render cost)
3. RPM series used PlotCurveItem without clipToView or autoDownsample
4. No downsampling at long time ranges (7200 points rendered without decimation)

**NOT contributing:** Daemon doesn't touch AMDGPU performance sysfs. pyqtgraph uses software QPainter (not OpenGL). Polling runs on QThread. Control loop at 1Hz is minimal.

**Fixes:**
- **Antialiasing disabled** on timeline chart (`antialias=False`) — 20-40% faster render (DEC-068)
- **autoDownsample + peak** enabled on all PlotDataItem creation — automatic decimation at long ranges
- **RPM series switched from PlotCurveItem to PlotDataItem** — gains clipToView + autoDownsample
- **Chart timer stops when dashboard hidden** — `hideEvent`/`showEvent` gate the timer
- **Chart timer throttles when app unfocused** — 1Hz→0.2Hz via `applicationStateChanged`, restores on focus (DEC-069)

7 new tests in `test_performance_r48.py`. **702 total passing.**

## [0.63.0] — 2026-03-30

### R47: Persistence Hardening — Bundle, Geometry, Import/Export, Theme Editor

- **Support bundle expanded** (DEC-066): Now includes full AppSettings, profile inventory (names + control/curve counts), active theme name, custom theme list, series_color count, fan_alias count, and GPU capabilities. Enables real diagnosis of configuration issues.
- **Window geometry + last page persisted** (DEC-067): `closeEvent()` saves current page index and window geometry to AppSettings. Restored on next launch when `restore_last_page` is enabled.
- **Theme editor minimum width**: Colour token scroll area gets `setMinimumWidth(360)` to prevent colour swatches from being squeezed too thin.
- **New AppSettings fields**: `last_page_index` (int, default 0) and `window_geometry` (list[int], default [100,100,1200,800]). Both included in import/export.
- **DiagnosticsService**: Now accepts optional `settings_service` and `profile_service` for bundle assembly. DiagnosticsPage passes both from MainWindow.
- 8 new tests in `test_persistence_r47.py`: bundle contents (settings, profiles, themes, graceful fallback), geometry roundtrip, export inclusion, full settings roundtrip. **695 total passing.**

**Code quality notes from tracing:**
- All GUI writes use `atomic_write()` (mkstemp + fsync + os.replace) — crash-safe. No partial JSON risk.
- All settings writes are on-change (immediate), not periodic or on-close — only window geometry saves on close.
- `last_session_path()` in `paths.py` is defined but unused — orphaned code (LOW priority cleanup).

## [0.62.0] — 2026-03-30

### R46: FanController Ownership Assessment — No Refactor Needed

**Assessment:** Thorough inspection of all FanController access paths, lock scopes, contention scenarios, and concurrency patterns. The current `Option<Arc<parking_lot::Mutex<FanController>>>` design is correct, idiomatic, and production-appropriate.

**Findings:**
- Locks held ~1-2ms (single serial round-trip), never across `.await` points
- Contention minimal: 1Hz profile engine + user-driven API + 1Hz polling
- Profile engine and API handlers are first-class peers using same public interface
- No deadlocks, no races, no poisoning (parking_lot)
- Per-channel locking rejected: serial I/O is inherently sequential
- Channel/actor pattern rejected: unnecessary complexity for sequential device

**Outcome:** Documentation-only. Removed "FanController Arc refactor" from future work, documented ownership model in daemon architecture doc. (DEC-065)

## [0.61.0] — 2026-03-30

### R45: Graph/Panel Parity — Selection Model Seeded from Displayable Keys

**Root cause:** The selection model was seeded from `history.series_keys()` (unfiltered), which included iGPU sensors and non-displayable fans. Since new keys default to visible, the graph drew entities that the panel had filtered out. This was a systemic truthfulness bug — any entity present in history but absent from the panel appeared on the graph.

**Fix:** DashboardPage now seeds the selection model with only displayable keys — sensor IDs from the panel (after iGPU filtering) + fan keys from the dedup/displayability pass. The chart's `_chartable_keys()` uses `selection.known_keys()` instead of raw history. History store remains unfiltered (data preservation). (DEC-064)

**Persistent `0` line resolved:** The GPU fan RPM=0 series was always visible because it was in `known_keys` by default. Now it only appears if the GPU fan passes the displayability filter and is in the selection model.

**Card sizing:** Already uniform from R44 — all SummaryCard instances use identical `QSizePolicy.Maximum` with same margins. No change needed.

- Added `displayed_sensor_ids()` to SensorSeriesPanel for coordination
- Added `known_keys()` to SeriesSelectionModel for chart access
- 7 new tests in `test_graph_parity_r45.py`. **687 total passing.**

## [0.60.0] — 2026-03-30

### R43: Missing Feature Audit — Auto-Lease, Reconnect, Safety, udev

**5-item audit completed. 3 implemented, 2 documented.**

**Daemon (v0.5.1):**
- **Auto-lease for headless hwmon writes** (DEC-062): Profile engine auto-acquires hwmon lease with `owner_hint: "profile-engine"`. GUI preempts via `force_take_lease()`. Thermal safety uses `force_take_lease("thermal-safety")` for emergency 100% writes to ALL fans (OpenFan + hwmon).
- **Serial auto-reconnect** (DEC-063): After 5 consecutive poll errors, enters reconnect mode with exponential backoff (1s→30s). Uses `auto_detect_port()` + `RealSerialTransport::open()` to re-detect device. Transport replaced in-place. No daemon restart needed.
- **Thermal safety broadened**: Emergency override now writes to hwmon fans in addition to OpenFan channels (via auto-lease force_take).
- **Lease take always succeeds**: API `POST /hwmon/lease/take` now uses `force_take_lease()` so the GUI always preempts internal holders. Integration test updated (conflict → preempt).

**Packaging:**
- **udev rule template**: `packaging/99-control-ofc.rules` creates stable `/dev/control-ofc-controller` symlink. Requires user VID/PID from `udevadm info`.

**Documentation (GUI v0.60.0):**
- `docs/14_Risks_Gaps_and_Future_Work.md`: Complete rewrite with current feature matrix, status for all 5 items, resolved gaps table, and remaining known limitations.
- `DECISIONS.md`: DEC-062 (auto-lease), DEC-063 (reconnect)
- Known limitations documented: no runtime hotplug, no GPU thermal rule, FanController Arc refactor (future), AIO placeholder, multi-GPU UI, GUI spec features not wired.

## [0.59.0] — 2026-03-30

### R44: Sensor Colour Selector, Card Sizing, Hover Lifecycle

- **Sensor panel colour selector restored**: 3rd column colour swatch with click-to-pick via `QColorDialog`. Was accidentally removed in R42 alongside the fan table colour column. Colour persistence via `AppSettings.series_colors` still intact — only the UI control was missing.
- **Summary card sizing**: Replaced `setMaximumHeight(100)` with `QSizePolicy(Preferred, Maximum)`. Cards now size to content (font metrics), eliminating blank space at default sizes while scaling correctly at large theme sizes. Margins tightened `(12,8)→(10,6)`, spacing `4→2`. (DEC-060)
- **Hover lifecycle**: Event filter on PlotWidget hides crosshair+label on `QEvent.Leave`. `applicationStateChanged` hides hover on app deactivation (alt-tab). Hover no longer persists after pointer exits graph or app loses focus. (DEC-061)
- **Typography**: Confirmed top cards already use theme-driven classes (`.PageSubtitle`, `.CardValue`) — no change needed.
- 13 new tests in `test_dashboard_r44.py`. **680 total passing.**

## [0.58.0] — 2026-03-30

### R42: Dashboard GPU De-Duplication, Hover Truthfulness, Panel Affordance

- **GPU fan de-duplication**: hwmon fan entries from the same amdgpu device are suppressed when an `amd_gpu` fan entity exists (matched by PCI BDF in ID). Non-GPU hwmon fans unaffected. (DEC-057)
- **GPU sensor de-duplication**: iGPU sensors filtered from sensor panel when a discrete dGPU is the primary GPU. Prevents confusing duplicate "edge" entries. (DEC-058)
- **Colour column removed**: Fan table reverted to 4 columns (Label, Source, RPM, PWM%). Sensor panel reverted to 2 columns. Colour swatches and QColorDialog pickers removed.
- **Hover: selected-only + zero-RPM suppression**: Hover iterates only `visible_keys()` from the selection model. 0 RPM values suppressed from hover (idle noise). Temperature zeros still shown. (DEC-059)
- **Expand/collapse arrows**: QTreeWidget branch CSS uses border-triangle technique with `text_secondary` colour for visible right/down arrows on dark theme.
- Column sizing restored to `minimumSectionSize=50`.
- 7 new tests in `test_dashboard_dedup_r42.py`. Updated R41 and R28 tests. **667 total passing.**

## [0.57.0] — 2026-03-29

### R41: Dashboard Graph — Y-Axis Limits, Crosshair Hover, Colour Selection

- **Y-axis limits**: Both temperature and RPM ViewBoxes set `yMin=0`. Mouse wheel can no longer zoom into negative values. (DEC-055)
- **Crosshair hover**: Vertical InfiniteLine + TextItem shows values for all visible series at cursor position. Rate-limited to 30 Hz via `SignalProxy`. Uses `np.searchsorted()` for nearest-point lookup. (DEC-056)
- **Colour persistence**: New `series_colors: dict[str, str]` in AppSettings. `color_for_key()` checks user override before hash-based theme default. (DEC-054)
- **Sensor panel colour swatch**: 3rd column shows colour indicator (background fill) for each sensor/fan. Click opens `QColorDialog`. Changes persist to settings and update graph immediately.
- **Sensor panel expand affordance**: Group items now use `ShowIndicator` policy for clearer expand/collapse visual.
- **Fan table colour column**: New 5th column "Colour" with clickable swatch. Click opens `QColorDialog`. Changes sync to graph and persist.
- Fan table `minimumSectionSize` reduced to 30px to accommodate the narrow colour column.
- 13 new tests in `test_dashboard_graph_r41.py`. Updated `test_dashboard_layout_r28.py` (column count 4→5, min section 50→30). **661 total passing.**

## [0.56.0] — 2026-03-29

### R40: GPU Curve Assignment Fix — Zero-RPM Management + Warning Popup

**Root cause:** RDNA3+ GPU firmware keeps fans stopped below the zero-RPM temperature threshold (~50-60C) regardless of the PMFW fan curve. The daemon wrote the curve correctly, but `fan_zero_rpm_enable=1` caused the firmware to ignore it at idle temperatures. Fans reported 0 RPM despite having a flat curve at 50%.

**Daemon (v0.5.0):**
- `set_static_speed()` now disables `fan_zero_rpm_enable` (writes "0" + commits) before writing the flat PMFW curve. Fans respond immediately at any temperature.
- `reset_to_auto()` now re-enables zero-RPM (writes "1" + commits) after resetting the curve. Firmware idle fan-stop behaviour restored.
- `AmdGpuInfo` now tracks `fan_zero_rpm_path` alongside `fan_curve_path`.
- Shutdown handler passes zero-RPM path to `reset_to_auto()`.
- 2 new Rust tests for zero-RPM disable/enable during write/reset.

**GUI (v0.56.0):**
- **Zero-RPM info popup**: Shown once when a GPU fan is first added to a Fan Role. Explains that zero-RPM idle mode is temporarily disabled while the curve controls the GPU, and automatically restored when the curve is removed or daemon shuts down. Includes "Don't show this again" checkbox.
- **Settings toggle**: "Show GPU zero-RPM warning when adding GPU fan to role" checkbox in Settings page. Persisted as `show_gpu_zero_rpm_warning` in app settings.
- **V1 migration fix**: GPU fan targets (`amd_gpu:...`) now correctly tagged as `source="amd_gpu"` instead of `"hwmon"`.
- 12 new Python tests: V1 migration GPU source detection, settings persistence, settings page checkbox, warning trigger conditions.

**DEC-053**: Auto-disable zero-RPM for GPU curve control.

## [0.55.0] — 2026-03-29

### R39: GPU Fan /poll Endpoint Fix — Root Cause of Missing GPU Fan Data

**Root cause:** The `/poll` batch endpoint (daemon `handlers.rs:237-268`) iterated `openfan_fans` and `hwmon_fans` but **skipped `gpu_fans` entirely**. The standalone `/fans` endpoint correctly included them. Since the GUI uses `/poll` as the primary data source, GPU fan entries were silently dropped on every successful poll cycle.

**Why it wasn't caught:** The `/poll` handler was written before R36 added GPU fan support. R36 correctly added GPU fans to `fans_handler()` but missed `poll_handler()` — a copy-omission between two handlers serving overlapping data.

**Fix:** Added `snap.gpu_fans` iteration to `poll_handler()`, identical to the code already in `fans_handler()`.

**GPU temperature assessment:** Current hwmon `temp*_input` path works correctly — edge/junction/mem temps all appear in `/poll` and `/sensors` responses. `gpu_metrics` binary blob alternative evaluated and NOT adopted (more complex, version-dependent parsing, no immediate benefit).

**Daemon v0.4.3.** No GUI code changes needed — all R36-R38 GUI fixes were already correct.

## [0.54.0] — 2026-03-29

### V3 Audit: P1 + P2 Fixes

**P1 functional fixes:**
- **Write failure counter** (control_loop.py): Decrements on success instead of deleting. Warning at count >= 3 persists until counter drops below 3. Prevents intermittent success from hiding persistent failure patterns. (DEC-051)
- **Poll count after reconnect** (polling.py): Set `_poll_count = 1` after reconnect (was 0), preventing redundant capability refetch on the next cycle.
- **Lease threading documentation** (lease_service.py): Added docstring documenting Qt single-thread safety assumption for lease renewal.
- **V1 migration dedup** (profile_service.py): `seen_members` set prevents duplicate fan assignments creating conflicting controls. Duplicate fans logged as warnings. (DEC-052)

**P2 quality fixes:**
- **Atomic export** (app_settings_service.py): `export_settings()` now uses `atomic_write()` for crash safety, consistent with `save()`.
- **Fan alias whitespace** (app_state.py): `set_fan_alias()` strips whitespace before checking; whitespace-only strings treated as empty (alias cleared).

**16 new regression tests** in `test_audit_v3_p1p2_regressions.py`: write counter decrement/intermittent/sustained, poll count reconnect, V1 migration dedup/unique/group, atomic export roundtrip, alias whitespace/trim/clear/none. **636 total passing.**

## [0.53.0] — 2026-03-29

### R38: End-to-End GPU Fan Integration — Socket, Display Names, Wizard, Read-Only

**Root causes found:**
1. **Socket permissions** (CRITICAL): Daemon socket created as root with 0755 — non-root GUI couldn't connect. Fixed: chmod 0666 after bind.
2. **Cache overwrites commanded state**: `update_gpu_fans()` replaced `last_commanded_pct` with None every poll cycle. Fixed: preserve existing value.
3. **Fan wizard wrong API for GPU**: `stop_fan()`/`restore_fan()` fell through to hwmon branch for GPU fans. Fixed: explicit `amd_gpu` branch.
4. **Fan display name was raw PCI BDF**: `fan_display_name("amd_gpu:0000:03:00.0")` returned the raw ID. Fixed: returns "{model} Fan" from capabilities.
5. **No read-only indication**: GPU fans assignable to roles without knowing writes wouldn't work. Fixed: "(read-only)" suffix when `fan_write_supported=false`.

**Daemon (v0.4.2):**
- Socket chmod'd to 0666 after bind (allows non-root GUI connections)
- `update_gpu_fans()` preserves `last_commanded_pct` from existing cache entry when polling

**GUI (v0.53.0):**
- Fan wizard: `stop_fan()` and `restore_fan()` handle `amd_gpu` source via `set_gpu_fan_speed()`
- `fan_display_name()`: GPU fans return "{model} Fan" (e.g. "9070XT Fan"), fallback "D-GPU Fan"
- Controls page: read-only GPU fans labeled "(read-only)" in role member selection
- 13 new tests in `test_gpu_integration_r38.py`, 612 total passing

**Decisions**: DEC-049 (socket 0666), DEC-050 (GPU fan display name from capabilities)

## [0.52.0] — 2026-03-29

### R37: GPU Detection Truthfulness — PCI IDs, Displayability, Overdrive Guidance

**Root causes found and fixed:**
1. **Wrong PCI device IDs**: The RX 9070 XT/9070 device ID is `0x7550` (Navi 48), NOT `0x69C0`/`0x69C1`. Verified via `lspci` on live hardware and the pci.ids database. XT vs non-XT distinguished by PCI revision (`0xC0` = XT, `0xC3` = non-XT).
2. **PMFW "No" was correct**: `ppfeaturemask` (`0xfff7bfff`) had bit 14 (overdrive) unset. The `gpu_od/` directory only exists when overdrive is enabled.
3. **`pwm1_enable` absent on RDNA4**: Legacy hwmon manual mode is impossible. Fan control method was incorrectly reported as `"hwmon_pwm"` — now `"read_only"`.
4. **Dashboard hid GPU fan**: Displayability filter required RPM > 0. GPU was in zero-RPM idle mode (RPM=0 is normal).
5. **iGPU (0x13c0) detected as useful**: Granite Ridge iGPU has no fan interface — now properly sorted below dGPUs with fans.

**Daemon (v0.4.1):**
- Fixed PCI ID lookup table: `0x7550` + revision-based naming, removed bogus `0x69C0`/`0x69C1`
- Added PCI revision reading (`read_pci_hex_u8`) for XT/non-XT distinction
- Added `pwm1_enable` existence check — `fan_control_method` now truthfully reports `"read_only"` when no write path exists
- Added ppfeaturemask detection — reads `/sys/module/amdgpu/parameters/ppfeaturemask` bit 14
- Added `overdrive_enabled` to `AmdGpuCapability` API response
- Added `has_any_fan_interface()` method — GPUs with fans sorted first in primary selection
- Detailed logging when RDNA3+ GPU found without overdrive — suggests ppfeaturemask parameter
- Known iGPU IDs (0x13C0 Granite Ridge, 0x1681 Rembrandt, 0x164E Raphael, 0x15BF Phoenix) return None for marketing name
- 6 new Rust unit tests, 294 total passing

**GUI (v0.52.0):**
- Fixed displayability filter: GPU fans (`source == "amd_gpu"`) always visible — zero-RPM idle is normal
- Added `overdrive_enabled` field to `AmdGpuCapability` model
- Diagnostics GPU Status now shows overdrive status and ppfeaturemask guidance when PMFW unavailable
- 12 new Python tests in `test_gpu_truthfulness_r37.py`, 599 total passing

**Decisions**: DEC-046 (device ID + revision), DEC-047 (GPU fans always displayable), DEC-048 (read_only method)

## [0.51.0] — 2026-03-29

### R36: AMD dGPU Fan Parity — Full Control Loop, Dashboard, Diagnostics Integration

**Daemon (v0.4.0):**
- **GPU fan polling**: GPU fan RPM (`fan1_input`) polled alongside hwmon sensors, stored in `DaemonState.gpu_fans` keyed by `amd_gpu:<PCI_BDF>`
- **GPU fan write endpoints**: `POST /gpu/{gpu_id}/fan/pwm` (set static speed via PMFW flat curve) and `POST /gpu/{gpu_id}/fan/reset` (restore automatic mode). No lease required — PMFW operations are atomic
- **GPU fans in /fans API**: GPU fan entries included in `/fans` response with `source: "amd_gpu"`, RPM, and last commanded speed
- **Profile engine GPU branch**: `source=="amd_gpu"` members handled in profile evaluation loop. PMFW writes via `spawn_blocking()`. Guard scoped to avoid `!Send` across `.await`
- **Shutdown safety**: On graceful shutdown, daemon resets ALL GPU PMFW fan curves to automatic before stopping
- **Pre-RDNA3 fallback**: GPU write handler falls back to `pwm1_enable=1` + `pwm1` for older AMD GPUs without PMFW

**GUI (v0.51.0):**
- **Client API**: `set_gpu_fan_speed(gpu_id, speed_pct)` and `reset_gpu_fan(gpu_id)` methods
- **Control loop**: New `amd_gpu:` write routing — extracts PCI BDF, calls GPU fan write endpoint
- **Sensor panel**: New "Fans — D-GPU" group in series tree, ordered before hwmon/OpenFan fan groups
- **Diagnostics**: "GPU Status" button in event log shows GPU detection, PMFW support, fan state, model info
- **DiagnosticsService**: `format_gpu_status()` produces labeled output from capabilities + fan state

**Decisions**: DEC-043 (no amdgpu-sysfs), DEC-044 (one fan per GPU), DEC-045 (imperative write model)

**18 new GUI tests** in `test_gpu_fan_parity_r36.py`: control loop routing (GPU + OpenFan), sensor panel group labels, diagnostics GPU fan table, GPU Status button and event log, format_gpu_status output, fan source compatibility, freshness, ID format

## [0.50.0] — 2026-03-29

### R35: AMD Dedicated GPU Support (sensors + fan read/write)

**Daemon (v0.3.0):**
- **GPU detection** (`hwmon/gpu_detect.rs`): Scans hwmon for `name=="amdgpu"`, resolves PCI BDF for stable identity, reads PCI device/class IDs, maps to marketing name via lookup table (RDNA2/3/4 coverage), detects PMFW fan_curve support
- **PMFW fan control** (`hwmon/gpu_fan.rs`): Parse/write/reset PMFW fan_curve sysfs interface. Supports custom curves (5-point temp→speed%), static speed, and reset-to-auto. Handles `OD_FAN_CURVE:` format with `OD_RANGE:` limits
- **Source labeling**: amdgpu sensors now report `source: "amd_gpu"` (was `"hwmon"`). New `DeviceLabel::AmdGpu` and `SensorSource::AmdGpu` variants
- **Capabilities API**: New `amd_gpu` object in `/capabilities` response: model_name, display_label, pci_id, fan_control_method (pmfw_curve/hwmon_pwm/none), pmfw_supported, fan_rpm_available, fan_write_supported, is_discrete
- **Primary GPU selection**: Discrete VGA GPUs preferred over render-only, sorted by PCI BDF
- **26 new Rust tests**: GPU detection (single/multi/absent), PMFW curve parsing (standard/partial/empty/ranges), write/reset/static, PCI BDF extraction, marketing name lookup, fan control method detection, display label generation

**GUI (v0.50.0):**
- **Models**: New `AmdGpuCapability` dataclass, parsed from daemon `/capabilities` response. Forward-compatible with unknown fields
- **Dashboard**: GPU card title updates from capabilities (e.g. "9070XT Temp" instead of "GPU Temp")
- **Diagnostics**: GPU line in Device Discovery card showing model, PCI ID, and fan control method
- **SummaryCard**: Added `set_title()` method for dynamic title updates
- **17 new Python tests**: capability parsing (with/without GPU, unknown fields), dashboard GPU card title, diagnostics GPU display, source label handling, freshness with amd_gpu source

**Decisions**: DEC-041 (direct sysfs + PMFW over LACT), DEC-042 (PCI BDF for stable GPU identity)

**Research**: Extensive analysis of LACT architecture/API, amdgpu kernel hwmon interface, PMFW fan_curve protocol, RDNA4 kernel requirements, safety mechanisms (PMFW thermal throttling, SW CTF, hardware thermal trip)

## [0.49.0] — 2026-03-28

### R34: Diagnostics Page — Latency Investigation, Event Log Detail, Lease Explanation, Theming

- **Latency investigation**: Traced `age_ms` through daemon `staleness.rs` → API `/status` → GUI `_on_status()`. The large difference between subsystem ages (e.g., openfan 847ms vs hwmon 112ms) is expected: OpenFan serial I/O takes 100-500ms per cycle while hwmon sysfs reads take ~1ms. No timer changes needed — fixed with truthful labeling.
- **Overview improvements**: Subsystem reason text now displayed alongside age. Daemon uptime shown when available. Explanatory note clarifies that "age = time since daemon last polled this hardware."
- **Event log enhancement**: Switched from `QTextEdit` to `QPlainTextEdit` (more efficient for append-heavy text, `setMaximumBlockCount(2000)`). Added three category buttons: Daemon Status, Controller Status, System Journal. Each appends a labeled block with source attribution.
- **Journal access**: `journalctl -u control-ofc-daemon.service --lines=100 --no-pager --output=short-iso` via `subprocess.run()` with 5s timeout. Handles: FileNotFoundError, TimeoutExpired, empty output, permission errors (explains `systemd-journal` group).
- **Lease explanation**: New explanation card in Lease tab covering what a lease is (exclusive hwmon write access), why it exists (prevent conflicting PWM commands), and practical considerations (60s TTL, auto-acquire/renew, OpenFan doesn't need lease).
- **Transparent labels**: All labels inside Card frames across Overview, Lease, and Telemetry tabs use `background: transparent`. Replaced hardcoded `font-size: 14px` with theme CSS classes (`.PageSubtitle`, `.CardMeta`).
- **67 new tests** in `test_diagnostics_r34.py`: transparent labels (Overview/Lease/Telemetry), no inline font-size, subsystem reason display, uptime, lease explanation content, QPlainTextEdit properties, category button existence, daemon/controller/journal detail retrieval, source labeling, journal error handling (not found, timeout, permissions, empty).
- **Docs**: 07_Diagnostics_Spec (latency semantics, event log sources, lease explanation, theming), daemon-end-to-end (age_ms semantics section), 13_Acceptance_Criteria (7 new R34 criteria), DECISIONS (DEC-037 through DEC-040)

## [0.48.0] — 2026-03-25

### R33: Card Typography Consistency and Fan Role Button Sizing

- **Root cause**: Card metadata labels used `.PageSubtitle` (13pt section-header role) instead of a card-specific class. After R30 removed inline `font-size` overrides, the labels inherited the full 13pt — visibly larger than 10pt body text.
- **Fix**: Added `.CardMeta` CSS class at `small` role (9pt at default, `base * 0.9`). Changed all card metadata labels from `PageSubtitle` to `CardMeta`. Added `.Card QPushButton { padding: 4px 8px; }` for comfortable button text fit (~15% increase, within 20% bound).
- **4 new tests**: CardMeta class usage on both card types, stylesheet presence of CardMeta and button padding
- **Docs**: Controls spec (card metadata typography), Acceptance Criteria (text consistency), DECISIONS (DEC-036)

## [0.47.0] — 2026-03-25

### R32: Fix Curve Editor Sensor Selection Leakage

- **Root cause**: `set_curve()` never initialized the sensor combo from `curve.sensor_id`. When switching curves, the combo showed the previous curve's sensor. `get_curve()` then overwrote the new curve's sensor_id with the stale combo value.
- **Fix**: Added sensor combo restoration in `set_curve()` with `blockSignals(True)` to prevent writeback. Cleared `_last_sensor_ids` to force `set_available_sensors()` repopulation on next poll tick.
- **3 new tests**: sensor restoration on set_curve(), switching curves shows correct sensors, get_curve() returns correct sensor_id after switch
- **Docs**: Updated Controls spec (editor isolation), Acceptance Criteria (sensor isolation), DECISIONS (DEC-035)

## [0.46.0] — 2026-03-25

### R31: Curve Ownership, Preview Truthfulness, Theme Adherence, and Documentation Alignment

#### Root cause
Curve card mini-previews did not refresh when editing a curve. `_on_curve_changed()` updated control card output labels but never called `card.update_curve()` on the curve card being edited.

#### Fix (5 lines)
Added curve card refresh in `_on_curve_changed()` — reads the edited curve from the editor and calls `update_curve()` on the corresponding card.

#### Diagnosis confirmed: data model already correct
- Each `CurveConfig` already owns `sensor_id` and `points` per-curve
- No global/shared sensor state exists
- Serialization is per-curve via `to_dict()`/`from_dict()`
- Editor modifies curve in-place (correct — card holds same reference)
- Controls page has no hardcoded font-size (inherits from theme after R30)

#### Documentation updates (6 files)
- **docs/05_Controls_Spec**: per-curve ownership rules, preview truthfulness, theme adherence
- **docs/13_Acceptance_Criteria**: 7 new criteria for curve ownership + preview + theme
- **DECISIONS.md**: DEC-032 (sensor ownership), DEC-033 (preview derived from data), DEC-034 (theme inheritance)
- **CLAUDE.md**: enduring rule — curve previews must be driven by curve-owned state

#### Tests: 478 (9 new)
- Per-curve sensor independence (3 tests)
- Per-curve graph independence (3 tests)
- Save/load roundtrip preserves per-curve state (2 tests)
- No hardcoded font-size on Controls page (1 test)

## [0.45.0] — 2026-03-25

### R30: Theme Typography System, Font Selection, and Style Cleanup

#### Theme coverage audit
- Identified 48 inline `setStyleSheet()` calls across 17 files bypassing the theme system
- 8 different hardcoded font sizes (10px, 12px, 13px, 14px, 16px, 18px, 22px, 26px)
- 2 hardcoded `color: red` instances (fan_wizard.py)
- Colour system rated 8/10, typography system rated 2/10

#### Typography model: hybrid (base size + role multipliers)
- New `font_sizes(base)` function computes role-based sizes: title (1.6x), section (1.3x), body (1.0x), card_title (1.1x), small (0.9x), card_value (2.2x), brand (1.4x)
- `build_stylesheet()` now uses computed `{fs["role"]}pt` values instead of hardcoded px
- New `.CardValue` CSS class for summary card readings

#### Font family selection
- New `font_family` field in `ThemeTokens` (default: system font)
- Font picker combo in Settings → Themes populated from `QFontDatabase.families()`
- Applied via `QApplication.setFont()` — correct Qt6 approach (not stylesheet)

#### Text size control
- New `base_font_size_pt` field in `ThemeTokens` (default: 10pt, range 7–16)
- Size spinner in Settings → Themes
- All typography roles scale proportionally from the base

#### Style cleanup
- Removed inline `font-size` from card labels (now inherited from stylesheet CSS classes)
- Replaced `color: red` in fan_wizard.py with `CriticalChip` theme class
- Summary card value uses `.CardValue` class instead of inline `font-size: 26px`
- Font settings persist in theme JSON and survive save/load roundtrips

#### Tests: 469 (13 new)
- Font size computation (3 base values)
- ThemeTokens typography fields and defaults
- Stylesheet uses computed sizes (no hardcoded 13px)
- Theme save/load roundtrip for typography fields
- Backward compatibility (old themes without font fields get defaults)

## [0.44.0] — 2026-03-25

### R29: Controls Page Card Polish, Section Resizing, and Alignment

#### A. Curves/editor splitter
- Added `QSplitter(Vertical)` between curves card grid and curve editor panel
- User can drag the divider to adjust height allocation between curves and editor
- Both panes non-collapsible with sensible defaults

#### B/C. Transparent card label backgrounds
- All labels inside CurveCard and ControlCard now use `background: transparent` in inline stylesheets
- The `.Card` CSS class owns the card background; child labels don't paint their own

#### D. Shared card sizing
- New `card_metrics.py` with `CARD_WIDTH=220`, `CARD_HEIGHT=160`
- ControlCard aligned from 260×180 → 220×160 to match CurveCard
- Margins/spacing/fonts tightened to fit 5 rows in 160px height
- Both card types import from the shared source

#### E. Fan Role bottom row corrected
- Order changed from `[Edit, Delete, Stretch, RPM]` to `[RPM, Stretch, Delete, Edit]`
- RPM label is left-aligned, Delete is left-of-Edit, Edit is far right

#### F. Cross-section alignment
- Fan Roles section spacing normalised from 4px to 8px (matching Curves section)
- Both sections now use consistent margins and spacing

#### Tests
- 9 new tests: splitter existence/children/collapsibility, shared card sizing, bottom row order, transparent label backgrounds
- Updated existing card size assertion from 260×180 to shared constants
- 456 total tests

## [0.43.0] — 2026-03-25

### R28: Dashboard Layout, Sizing, and Resize-Behaviour Hardening

#### Root cause: inverted splitter hierarchy
The sensor panel was inside `h_splitter` (top pane of `v_splitter`), while the fan table was a direct child of `v_splitter` (bottom pane). This caused: sensor panel stopping at chart bottom (not extending to table bottom), and fan table spanning full window width (wider than chart).

#### Fix: restructured to horizontal-outer, vertical-inner
```
h_splitter (Horizontal) — LEFT/RIGHT divide
├── v_splitter (Vertical) — left pane: chart + fan table
└── sensor_panel — right pane: spans full chart+table height
```
Graph and fan table now share the same left-column width. Sensor panel spans full height. Both horizontal and vertical splitters are user-draggable.

#### Changes
- **Layout restructure** (dashboard_page.py): swapped from v_splitter(h_splitter(chart, sensor) | table) to h_splitter(v_splitter(chart, table) | sensor)
- **Fan table columns**: Label/Source use `Stretch`, RPM/PWM use `ResizeToContents` with `minimumSectionSize(50)` — all 4 columns always visible
- **Summary card typography**: value font 22px→26px, title 13px→14px, card max-height 90→100
- **Label transparency**: both title and value labels use `background: transparent` — card background shows through cleanly
- **14 new tests**: splitter hierarchy, column modes, header labels, typography, transparent backgrounds, non-collapsible panes

## [0.42.0] — 2026-03-25

### V2 Audit Correctness, Error Handling, and Documentation Fixes

#### Control loop correctness (V2-04, V2-05)
- **V2-04**: Use passed `profile` parameter instead of re-fetching `active_profile` — prevents race if profile changes mid-cycle
- **V2-05**: `_should_write()` now correctly handles fan not in state (first write allowed, but explicit about it)

#### Daemon telemetry tracking (V2-06)
- **V2-06**: Added `record_gui_write()` to `set_pwm_all_handler` and `set_target_rpm_handler` — GUI activity now properly tracked for all write endpoints

#### GUI error handling (V2-07, V2-08, V2-09, V2-10)
- **V2-07**: Profile combo refreshes on profile selection — catches external renames
- **V2-08**: Settings load now logs actual exception on fallback to defaults
- **V2-09**: Diagnostics export catches `PermissionError` separately, truncates long error messages
- **V2-10**: Theme load now logs actual exception for invalid files

#### Documentation (V2-13)
- Updated `docs/14_Risks_Gaps_and_Future_Work.md` with thermal safety fix status and remaining hwmon limitation

## [0.41.0] — 2026-03-25

### V2 Audit Immediate Safety Fixes (P0-V2-01, P0-V2-02, P0-V2-03)

- **V2-01 FIXED**: Thermal safety rule now actively evaluated in profile engine — reads CPU Tctl from cache, calls `safety.evaluate(temp)`, and forces all OpenFan channels to emergency PWM if triggered. The 105°C → 100% → hold until 80°C → 60% recovery state machine is now functional in headless mode.
- **V2-02 FIXED**: Calibration handler `.unwrap()` replaced with safe `let-else` pattern — returns SERVICE_UNAVAILABLE if controller disconnects mid-calibration instead of panicking.
- **V2-03 FIXED**: Polling backoff capped at 8 seconds (was 30) — appropriate for local Unix socket reconnection. Progression: 2s → 4s → 8s (capped).

## [0.40.0] — 2026-03-25

### WP5 + WP6: Daemon Lock Poisoning + Code Deduplication (P0-6, DEL-06)

- **P0-6 FIXED**: Migrated entire daemon from `std::sync::Mutex`/`RwLock` to `parking_lot` equivalents. 24+ `.expect("lock poisoned")` calls removed. No more crash-cascade on thread panic. 11 files, -91 net lines.
- **DEL-06 FIXED**: Extracted `device_id_from_path` and `read_sysfs_string` into shared `hwmon/util.rs`. Uses the more complete `pwm_discovery` version (includes `nct` device pattern). -21 net lines.

### Audit Action Plan: 26/26 COMPLETE

All 26 action items from the comprehensive code audit have been resolved.

## [0.39.0] — 2026-03-25

### WP7: Test Hardening (TEST-03, TEST-04)

#### Parser resilience tests (7 new)
- Extra/unknown fields from future daemon versions silently dropped (sensors, fans, capabilities, status subsystems)
- Missing optional fields use dataclass defaults
- Empty sensor/fan lists handled correctly

#### Reconnect behavior tests (8 new)
- Disconnect transitions state correctly
- Active profile name survives disconnect and full reconnect cycle
- Fan aliases (GUI-owned) survive reconnect
- Warnings preserved on disconnect
- Connection signal emitted on state change; duplicate state suppressed
- Tests are deterministic, headless-safe, and timing-independent

#### Tests: 433 (was 418)

## [0.38.0] — 2026-03-25

### WP4: Rust Daemon Safety (P1-7, P1-8, P1-9, P1-10)

- **P1-7 FIXED**: Config error misclassification — non-NotFound I/O errors now map to `ConfigError::Parse` with actual error message (was misleadingly `NotFound`)
- **P1-8 FIXED**: `set_pwm_all` safety bypass — stop-timeout check now applied for 0% PWM on all channels (was skipped)
- **P1-9 FIXED**: Calibration race condition — `AtomicBool` guard prevents concurrent calibration sweeps (returns 409 Conflict). Reset on all exit paths.
- **P1-10 FIXED**: False stall alerts — added `rpm_polled: bool` to `OpenFanState`. Stall detection only fires after first real RPM poll, preventing false alerts when PWM write creates entry before polling starts.

## [0.37.0] — 2026-03-25

### WP3: Dead Code Cleanup + False-Confidence Tests (DEL-01–04, TEST-02)

#### Dead code removed
- **DEL-01**: Deleted `ui/widgets/empty_state.py` — `EmptyState` widget never imported
- **DEL-02**: Deleted `ui/widgets/fan_card.py` — `FanCard` widget never imported (replaced by `ControlCard`)
- **DEL-03**: Deleted `services/event_stream.py` — `EventStreamService` never instantiated
- **DEL-04**: Removed `_display_name` method from `dashboard_page.py` — never called

#### False-confidence tests removed (-6 tests)
- `test_tab_switching` — tested `QTabWidget.setCurrentIndex()`, not app behavior
- `test_syslog_checkbox_toggles` — tested `QCheckBox.setChecked()`, not app behavior
- `test_range_combo_changes` — tested `QComboBox.setCurrentIndex()`, not app behavior
- `test_splash_can_be_created` — asserted `splash is not None` (can never fail)
- `test_fan_card_height` / `test_fan_card_max_width` — tested deleted dead widget

#### Tests: 418 (was 424)

## [0.36.0] — 2026-03-25

### WP2: GUI State & UI Feedback (P0-7, P1-6, FIX-08)

- **P0-7 FIXED**: Hwmon write failures now surface to UI — after 3 consecutive failures for a target, an `AppState` warning is shown. Clears automatically on successful write.
- **P1-6 FIXED**: Warning timestamps now track first-seen time — `_warning_first_seen` dict preserves original timestamp across warning rebuilds. Prunes when warning resolves.
- **FIX-08 FIXED**: `theme_changed` signal now wired — `MainWindow` connects to `settings_page.theme_changed` and rebuilds the application stylesheet via `build_stylesheet()`.
- New `add_warning()` / `remove_warning()` methods on `AppState` for ad-hoc warnings from services.

## [0.35.0] — 2026-03-25

### WP1: GUI Quick Fixes (P0-8, P1-3, P1-4)

- **P0-8 FIXED**: Fragile channel ID parsing — replaced `target_id.split("ch")[1]` with compiled regex `re.compile(r"^openfan:ch(\d+)$")`. Malformed IDs now logged and skipped instead of crashing. 1 new test.
- **P1-3 FIXED**: Profile load exception swallowed — `except Exception:` now captures and logs the actual exception (`except Exception as e: log.warning(..., e)`)
- **P1-4 FIXED**: Double `profile_service.load()` — removed redundant call from `ControlsPage.__init__` (already called in `main.py`)

## [0.34.0] — 2026-03-25

### Refinement 27: Fix Card Stacking — FlowLayout Visibility Check Bug

- **Root cause**: `FlowLayout._do_layout()` used `widget.isVisible()` to skip items. `isVisible()` checks the entire parent chain — returns `False` for all children when the window isn't shown yet. All cards positioned at (0,0) → visual stacking.
- **Fix**: Changed to `widget.isHidden()` which only checks if the widget itself was explicitly hidden (e.g., during drag). Matches Qt's own `QWidgetItem::isEmpty()` behavior.
- **Research verified**: Official Qt C++ FlowLayout example has NO visibility check in `doLayout()`. Our Python adaptation incorrectly added one. Qt docs confirm `isHidden()` is the correct check for layout filtering.
- **Symptoms fixed**: cards no longer stack on initial render, after profile activation, or after creating new cards. Drag-to-fix workaround no longer needed.
- **1 new test**: verifies cards are positioned correctly even in a not-yet-shown parent container

## [0.33.0] — 2026-03-25

### Phase 5: FanController Arc Refactor — Daemon Headless Autonomy (P0 Fix)

- **Root cause**: `AppState.fan_controller` was `Option<Mutex<T>>` — not clonable, so the profile engine task couldn't share the controller. A 55-line placeholder discarded all PWM commands.
- **Fix**: Changed to `Option<Arc<Mutex<T>>>` for both `fan_controller` and `hwmon_controller`. Replaced placeholder with real `profile_engine_loop()` call. Net -35 lines.
- **Source compatible**: All 15+ handler lock access sites unchanged — Rust auto-derefs `Arc<Mutex<T>>` to `Mutex<T>`
- **Research verified**: `std::sync::Mutex` correct (lock not held across .await), `Option<Arc<Mutex<T>>>` correct for startup-time detection, parking_lot deferred as separate concern
- **Enables**: headless fan control at 1Hz, thermal safety emergency writes, GUI-close resilience, reboot persistence
- **Tests**: 29 daemon tests pass unchanged (proves source compatibility)

## [0.32.0] — 2026-03-25

### Phase 4: Serial Transport Safety Deadline (P0 Fix)

- **Root cause**: `send_command()` loop in `daemon/src/serial/transport.rs` had no bounds — firmware debug flood caused infinite loop, blocking all fan control
- **Fix**: belt-and-suspenders guards — wall-clock deadline (`Instant::now() + timeout`) + iteration cap (`MAX_DEBUG_LINES = 50`)
- **Research verified**: `serialport` crate per-read timeout only fires when no data arrives (useless during debug flood); `std::time::Instant` is correct for sync deadlines; `tokio::time::timeout` cannot cancel blocking reads
- **3 new Rust tests**: debug flood abort, deadline exceeded, many-debug-then-response (guards don't interfere)
- **No API changes**: both guards return existing `SerialError` variants

## [0.31.0] — 2026-03-25

### Phase 3: Control Loop → Daemon Write Integration Tests

16 new integration tests covering the entire control loop → daemon API write path — the most critical data path that previously had zero end-to-end test coverage.

#### OpenFan write path (6 tests)
- Verifies `set_openfan_pwm(channel, pwm)` called with correct args after curve evaluation
- Channel parsing: `"openfan:ch03"` → channel=3 extracted correctly
- PWM rounding: float 51.5% → int 52 passed to client
- Write suppression: delta < 1% → zero client calls
- `write_performed` signal emitted with correct target_id and PWM
- Multi-member control writes to each member independently

#### Hwmon write path (4 tests)
- Lease-gated: `set_hwmon_pwm(header_id, pwm, lease_id)` with correct args
- No lease: write skipped, zero client calls
- Lease ID passthrough verified exactly
- Unknown target format → zero write calls

#### Failure handling (3 tests)
- DaemonError caught per-member, loop continues
- `client=None` → graceful skip, no crash
- Error on first member does not prevent second member write

#### Status counters (3 tests)
- `targets_active` incremented on successful write
- `targets_skipped` incremented on failure
- Suppressed write (delta < 1%) counts as active, zero client calls

#### Tests
- 422 total tests (16 new)

## [0.30.0] — 2026-03-24

### Audit Remediation: Atomic Writes, Wizard Safety, Phase Plans

#### Atomic file persistence (P1-2 fix)
- New `atomic_write()` helper in `paths.py` — temp file + fsync + os.replace pattern
- Applied to profile saves, app settings saves, and theme exports
- Mid-write crash now leaves original file intact (POSIX rename guarantee)
- Research: Dan Luu "Files are hard", POSIX rename(2), atomicwrites library pattern

#### Fan wizard service wiring (P0-1/2 fix)
- `control_loop` and `lease_service` now plumbed from `main.py` -> `MainWindow` -> `ControlsPage` -> wizard
- Wizard can now pause curve evaluation during fan identification (was silently skipped)
- Wizard can now acquire hwmon lease for motherboard fan testing (was silently skipped)
- Removed redundant `hasattr()` checks and dead label-persistence callback

#### Phase plans created
- `Phase3.md` — control loop -> daemon write integration test (14 test scenarios)
- `Phase4.md` — serial transport max-retry safety deadline (wall-clock + iteration cap)
- `Phase5.md` — FanController Arc refactor for headless autonomy (15-line change, bulletproof migration)

## [0.29.0] — 2026-03-24

### Comprehensive Code Audit + Immediate Fixes

#### Audit scope
- 54 Python source files, 38 Rust source files, 32 test suites, 140 markdown documents
- Static analysis: ruff, cargo clippy, cargo fmt, pytest, cargo test
- Deep code path audit of all major workflows
- Test suite audit for correctness and false-confidence

#### Findings
- **66 issues identified**: 8 P0 (critical), 15 P1 (significant), 21 P2 (moderate), 19 P3 (minor)
- **5 false-confidence tests** identified (test Qt behavior, not app behavior)
- **10 high-value untested areas** identified (control loop->daemon write, telemetry export lifecycle, SSE, reconnect)
- **2 rewrite candidates** identified (FanController Arc refactor, lock poisoning mitigation)

#### Immediate fixes applied
- **FIX-01**: `main.py` — guard `client` variable with `None` initialization before conditional block
- **FIX-02**: `api/models.py` — all `**` unpacking in parsers now uses `_filter_fields()` for forward-compatibility (prevents TypeError when daemon adds new API fields)
- **FIX-03**: `__init__.py` — version now references `constants.APP_VERSION` (was hardcoded `0.1.0`)
- **FIX-04**: Rust — all 8 clippy warnings fixed (unused import, `.clamp()` x4, unnecessary cast x2, `.contains()` x1)
- **FIX-05**: Rust — `cargo fmt` applied to all daemon source files

#### Audit deliverable documents
- `docs/COMPREHENSIVE_AUDIT_REPORT.md` — full findings with per-file references and severity
- `docs/CODEBASE_TRACEABILITY_MATRIX.md` — feature -> code -> tests -> docs mapping
- `docs/TEST_AUDIT_REPORT.md` — test strength assessment, false-confidence tests, missing coverage
- `docs/REWRITE_CANDIDATES.md` — FanController Arc refactor, lock poisoning mitigation
- `docs/RESEARCH_NOTES_AUDIT.md` — external sources consulted and their influence
- `docs/AUDIT_ACTION_PLAN.md` — prioritized remediation plan (immediate/near-term/architecture)

## [0.28.0] — 2026-03-24

### Full Documentation Audit & Gap Remediation

#### New documentation
- **`docs/18_Operations_Guide.md`** — comprehensive daemon operations reference: config schema, CLI args, environment variables, systemd service, permissions, profile search paths, syslog setup, IPC socket, troubleshooting
- **`docs/DOCUMENTATION_AUDIT.md`** — traceability matrix (25 features mapped to code/tests/docs), gap register (14 items), remediation summary
- **`daemon/README.md`** — daemon build, install, CLI, API reference
- **`packaging/daemon.toml.example`** — annotated example config file

#### Updated documentation
- **`docs/13_Acceptance_Criteria.md`** — added Controls page card layout criteria (FlowLayout, drag-reorder, fixed sizing, daemon activation)
- **`docs/14_Risks_Gaps_and_Future_Work.md`** — added R24-R26 resolved gaps section (profile activation, syslog fix, layout hardening)
- **`docs/00_README_START_HERE.md`** — updated pack contents with new docs

#### Audit findings
- 140 markdown files reviewed (33 canonical, 105 supplementary, 2 stale)
- 92 source files and 31 test files mapped to documentation
- 14 gaps identified, 10 fixed (5 in this pass, 5 in R26), 4 deferred (low severity)

## [0.27.0] — 2026-03-24

### Card Layout Hardening + Documentation Catchup (R26)

#### Defensive layout hardening
- `FlowLayout.takeAt()` now calls `invalidate()` — matches Qt's built-in `QLayout::removeWidget()` behavior, prevents stale layout if called from new code paths
- `DraggableFlowContainer.clear_cards()` now blocks signals on old cards, calls `deleteLater()` for deterministic Qt-side cleanup (was `setParent(None)` only)
- Confirmed via Qt source code: `addItem()` invalidation (added in R25) is required — Qt does NOT auto-call it for custom layouts

#### Comprehensive transition-path tests (14 new)
- Card count matches model (curves + controls)
- New curve/control appends to end, existing order preserved
- Order stable after single and double refresh
- Profile activate preserves card count and order
- No duplicates after rapid add/delete/refresh cycles
- Card IDs match profile model exactly

#### Documentation updated (6 files)
- `docs/05_Controls_Profiles_and_Curves_Spec.md` — FlowLayout, DraggableFlowContainer, drag-to-reorder, card sizing, splitter, profile activation flow
- `docs/08_API_Integration_Contract.md` — `POST /profile/activate`, `GET /profile/active`, syslog field mapping, hwmon rescan
- `docs/06_Settings_Spec.md` — syslog RFC 5424 over TCP, field name mapping, validation behavior, status model
- `DECISIONS.md` — DEC-027 through DEC-031 (FlowLayout, card sizing, profile activation, syslog fields, invalidation)
- `MILESTONES.md` — Milestone 5 acceptance criteria expanded (card stability, drag-reorder, fixed sizing, daemon activation)
- `docs/02_System_Architecture_and_Boundaries.md` — FlowLayout, DraggableFlowContainer, card widgets in module list

#### Tests
- 406 total tests (14 new)

## [0.26.0] — 2026-03-24

### Card Ordering, Fan Role Parity, Syslog Field Fix (R25)

#### A. FlowLayout stacking bug fixed
- **Root cause**: `FlowLayout.addItem()` did not call `self.invalidate()`, so cards added during rebuild were never positioned — all stacked at (0,0)
- Fix: single line addition — `self.invalidate()` in `addItem()`, matching `insertWidget()` behavior
- This also resolves the "cards reset to first position" symptom — the model order was always correct, but the layout never recalculated after rebuild

#### B. Fan Role cards → DraggableFlowContainer + fixed size
- Replaced `QGridLayout` (4-column) with `DraggableFlowContainer` for fan role cards
- Fan Role cards are now **draggable and reorderable** with the same interaction as Curve cards
- Fixed size: **260×180px** (slightly larger than Curve cards' 220×160 to fit 5-6 rows of content)
- New `_on_controls_reordered()` handler syncs `profile.controls` list from layout order
- New fan roles append to end of list (already worked, now explicit)

#### C. Syslog field-name mismatch fixed
- **Root cause**: GUI sent `destination_host`/`destination_port` but daemon's `TelemetryConfigRequest` expects `host`/`port`
- The **response** struct uses `destination_host`/`destination_port` — GUI was built to match response naming, not request naming
- Result: `host=None` in daemon → validation error "host is required when enabling telemetry"
- Fix: GUI now sends `host` and `port` to match the request struct

#### Tests
- 392 total tests (11 new)
- FlowLayout invalidation: cards positioned correctly after add
- Curve card append/order stability across refresh
- Fan Role: fixed size, flow container, append, model-level reorder
- Syslog: correct field names (`host`/`port` not `destination_host`/`destination_port`)

## [0.25.0] — 2026-03-24

### Cross-Layer Refinement: Drag Bounds, Daemon Profile Activation, Syslog Fix (R24)

#### A. Drag/drop invalid-drop recovery
- Cards dropped outside the curves area snap back to their original position (implicit via Qt drag model)
- Added logging for cancelled drags, successful reorders, and missing source widget edge cases
- Forced relayout after drag completes to prevent visual glitch
- 6 new tests for snap-back, ordering, and visibility

#### B. Profile activation wired to daemon API
- **GUI now calls `POST /profile/activate`** when activating a profile — daemon becomes runtime owner
- GUI only updates "active" state after daemon confirms success; failures shown to user
- New `GET /profile/active` daemon endpoint returns current active profile
- GUI queries daemon active profile on connect/reconnect — reflects daemon truth, not stale widget state
- `DaemonClient` gains `activate_profile()` and `active_profile()` methods
- New `ProfileActivateResult` and `ActiveProfileInfo` models with parsers
- `DaemonClient` plumbed through `MainWindow` → `ControlsPage`, `DashboardPage`, `SettingsPage`

#### C. Daemon profile persistence (already implemented, now used)
- Daemon persists active profile to `/var/lib/control-ofc/daemon_state.json` on activation
- Profile survives daemon restart and reboot
- Startup precedence: CLI > persisted state > none
- Closing the GUI does not reset the daemon's active profile

#### D. Syslog "No daemon connection" root cause found and fixed
- **Root cause**: `DaemonClient` was never passed to `SettingsPage` — `self._client` was always `None`
- Fix: client now plumbed from `main.py` → `MainWindow` → `SettingsPage`
- Added "Check Status" button that queries daemon syslog connection state
- Updated port tooltip to document TCP transport (not UDP)
- The daemon syslog pipeline (RFC 5424 over TCP with octet-counting framing) was already complete and functional
- 6 new tests for syslog apply with/without client, success/failure paths, status check

#### Tests
- 381 total tests (26 new)
- Profile activation: success/failure/no-client paths, daemon query, model parsing
- Drag snap-back: order preservation, visibility, clear, valid reorder
- Syslog wiring: apply/check with and without client, signature verification

## [0.24.0] — 2026-03-24

### Controls Page: Splitter, Fixed Cards, Drag Reorder, Search Removal (R23)
- **Vertical splitter** between Fan Roles and Curves sections — user-draggable divider
- **Drag-to-reorder** curve cards via FlowLayout + DraggableFlowContainer. Reorder persists via profile save.
- **Search function removed** — search bar, type filter, and all related code cleanly removed
- **Fixed-size curve cards** — 220×160px, consistent across all cards, don't stretch on window resize
- **8 new tests** (355 total): splitter, search removal, fixed size, model-level reorder

## [0.23.0] — 2026-03-24

### Hide Empty Fan Channels + Harden OpenFan Detection (R22)

#### Problem 1: Empty fan channels hidden
- **Unified displayability rule** for ALL fans (OpenFan AND hwmon equally — no source-based bypass)
- Fan is shown only if: `rpm > 0` (spinning), OR user has labeled it, OR `last_commanded_pwm > 0` (actively controlled)
- RPM=0 with no label and no active control → hidden (previously OpenFan fans were always shown)
- Filter applied in BOTH the dashboard fan table AND the sensor series panel
- Diagnostics Fans tab remains unfiltered (full diagnostic view)
- 7 new tests: RPM=0 hidden, RPM>0 shown, RPM=None hidden, labeled fan shown, actively-controlled shown, mixed set filtering

#### Problem 2: OpenFan detection after reboot
- **Root cause identified**: hardcoded `/dev/ttyACM0` in daemon config + no systemd USB device dependency + no retry logic
- **Stable by-id path**: daemon should use `/dev/serial/by-id/usb-Karanovic_Research_OpenFan_DE615CB14721492C-if00` (survives USB re-enumeration)
- **Startup retry loop**: daemon retries serial detection up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s) — handles slow USB enumeration after boot
- **Manual action required**: update `/etc/control-ofc/daemon.toml` port to stable by-id path

## [0.22.0] — 2026-03-24

### Warnings Workflow + Fan Filtering + Diagnostics PWM Visibility (R21)
- **Warnings dialog**: clicking Dashboard warnings card opens a dedicated WarningsDialog (was incorrectly opening SensorPickerDialog)
- **Warning model**: `active_warnings` list with structured dicts (timestamp, level, source, message)
- **Clear All**: acknowledges warnings without deleting event log history. Available in WarningsDialog and Diagnostics Event Log tab
- **Acknowledgment**: cleared warnings stay acknowledged until a genuinely new warning appears
- **Fan stall warnings**: `set_fans()` now calls `_update_warnings()` (was only from `set_sensors()`)
- **Dashboard RPM=0 shown**: stopped fans are meaningful. RPM=None (no tach) hidden.
- **Diagnostics PWM-only headers**: Fans tab shows hwmon headers without fan readings as "hwmon (PWM-only)"
- **14 new tests** (344 total): warning count, clear semantics, dialog, fan filtering

## [0.21.0] — 2026-03-24

### Test Coverage Hardening
- **Daemon: +14 new tests** (259 total, was 245):
  - Profile loading: valid JSON, invalid JSON, missing file, missing optional fields
  - Profile engine: manual mode, curve evaluation, missing sensor, empty members, offset/minimum, output clamping
  - PWM control: raw_to_percent conversion, cache update verification
  - Curve evaluation: unknown type fallback, empty graph
- **GUI: +5 new tests** (330 total, was 325):
  - Settings: fan_aliases round-trip, card_sensor_bindings round-trip, hidden_chart_series round-trip, unknown keys ignored, fan aliases persist across save/load
- **Testing policy** established: new code must ship with tests

## [0.20.0] — 2026-03-23

### Motherboard Fan Polling + Populated Filter (R20)
- **Continuous hwmon fan polling**: daemon now reads `fanN_input` (RPM) and `pwmN` (PWM%) for all discovered motherboard headers on every poll tick, not just after PWM writes
- **Hwmon fans fully surfaced**: motherboard fans appear in dashboard table, sensor panel, and controls page with live RPM and PWM data — same as OpenFan channels
- **Populated filter**: empty motherboard headers (no RPM, no PWM data, no user label) are hidden from standard views. Populated fans show automatically.
- **GUI FanReading model**: added `control_mode` field (pwmX_enable: 0=full, 1=manual, 2+=auto)

### Motherboard Feature Check (daemon + GUI)
- **Writability detection**: daemon now probes `pwmN` file permissions at discovery time, exposes `is_writable` in headers API
- **DC/PWM mode detection**: reads `pwmN_mode` (0=DC, 1=PWM) if present, exposes in headers API
- **Hwmon rescan endpoint**: new `POST /hwmon/rescan` re-enumerates hwmon devices without daemon restart
- **GUI HwmonHeader model**: accepts `is_writable` and `pwm_mode` fields (backward-compatible defaults)

### Motherboard Code Hardening (daemon)
- **PWM write verification**: after every `pwmX` write, the daemon reads back the value and logs a warning if it doesn't match. Prevents silent write failures from going undetected.
- **Manual mode on every write**: `pwmX_enable=1` is now written before every PWM write, not just the first per lease. Eliminates a class of bugs where lease expiration could desync mode tracking.
- **Device ID fallback fixed**: if symlink resolution fails during hwmon discovery, the ID now uses `"unknown"` instead of the unresolved symlink path. Prevents unstable IDs on systems with broken symlinks.
- **Temperature sanity bounds**: sensor readings are clamped to -50°C to 250°C. Values outside this range are logged as implausible. Prevents garbage values from triggering the thermal safety rule.
- **Vestigial `classify_header_floor()` removed**: the function always returned 0 since R18. Inlined at call site for clarity.
- **`pwmX_enable` semantics documented**: code comments now describe the standard ABI (0=full speed, 1=manual, 2=automatic, 3+=driver-specific).

### Fan Configuration Wizard
- **New `FanConfigWizard`** — guided QWizard for identifying and labelling controllable fans
- **Identification by stop**: stops each fan one at a time for 5 seconds so user can observe which physical fan changed
- **4-page flow**: Intro (pre-flight checks) → Discovery (select targets) → Test (per-fan identification with countdown, RPM readout, abort) → Review (edit labels before save)
- **Thermal guard**: polls CPU temps every second during test, auto-aborts if any CPU sensor exceeds 85°C
- **Safety**: restores all fans to 100% on abort, cancel, or wizard close; activates manual override to pause control loop; acquires hwmon lease if needed
- **Label presets**: CPU Cooler, Rear Exhaust, Front Intake, Radiator, Pump, etc. — with custom text input
- **Multiple fans detection**: "Multiple physical fans moved" checkbox with notes field for splitter/hub setups
- **Labels persist** via existing `AppSettings.fan_aliases` — propagate across dashboard, sensor panel, fan table
- **Entry point**: "Fan Wizard" button on Controls page toolbar
- 12 new tests covering wizard creation, pre-flight checks, discovery selection, thermal guard, channel parsing

## [0.19.0] — 2026-03-23

### Dashboard Overhaul (Refinements 9–16)

#### R9 — Dual-Axis Chart + Series Selection + Fan Rename + Profile Quick Switch
- **Dual y-axis chart:** left = °C (temps), right = RPM (fans) via pyqtgraph secondary ViewBox
- **Series selection model:** `SeriesSelectionModel` tracks chart visibility, persists to settings
- **Series panel sidebar:** grouped checkboxes (Temps/Mobo Fans/OpenFan Fans) with search filter
- **Fan rename:** double-click fan label → `QInputDialog` rename, persists via AppSettings
- **Profile quick switch:** combo + Apply button on dashboard with feedback cycle
- **Summary cards clickable:** open sensor picker dialog (radio buttons, does NOT affect chart)
- **Card sensor bindings:** persist which sensor each card displays via AppSettings

#### R10 — Card Values + Dialog Detail + Layout Polish
- **Sensor picker dialog shows live values** (°C/RPM) next to each radio button
- **Dialog values update live** while open (wired to `sensors_updated` signal)
- **Profile selector moved to far right** of summary card row
- **Fan cards denser:** height 72→56px, margins tightened, font 13→12px

#### R11 — Card Bindings Decoupled, Collapsible Groups, Rename Dialog
- **Card sensor binding decoupled from chart:** dialog uses radio buttons for card binding, SeriesSelectionModel untouched
- **Collapsible series panel groups:** `CollapsibleSection` with ▼/▶ arrows, multiple sections open
- **Fan grid 4 columns:** max width 240px per card
- **Rename dialog:** replaced broken inline QLineEdit with `QInputDialog.getText()`

#### R12 — Focus Bug Fix, Profile Selector, Fan Table, Temp Grouping
- **CRITICAL FIX: popup/menu dismissal on tick** — `series_panel.update_series_list()` was destroying/recreating all checkboxes every poll tick; now skips rebuild when keys unchanged
- **Guard unpolish/polish:** only repolish when CSS class actually changes (fan cards, summary cards)
- **Profile selector fixed:** `populate_profiles()` was never called — now called from MainWindow
- **Fan card grid → QTableWidget:** compact table with Label/Source/RPM/PWM%/Status columns
- **Temperature tree:** QTreeWidget grouped by kind (CPU/GPU/Motherboard/Disk) with live values

#### R13 — Kind Format Fix, Theme Styling, Resize Priority
- **Kind format mismatch fixed:** daemon sends `cpu_temp` (snake_case), demo sends `CpuTemp`; both formats now handled in card matching and temp tree grouping
- **QTreeWidget themed:** added alternate-row, hover, selection, branch styles using existing tokens
- **Resize priority:** chart stretch=1, bottom panels stretch=2 so bottom grows first

#### R14 — Merged Sensor Series Panel
- **New `SensorSeriesPanel`:** merged series selector (checkboxes) + temp tree (values) into single QTreeWidget panel with grouped sensors AND fans, live values, chart toggle checkboxes
- **Deleted `series_panel.py`** and removed temp tree from dashboard bottom
- **Groups by sensor kind:** CPU, GPU, Motherboard, Disk, Other, Fans-hwmon, Fans-OpenFan
- **Tri-state group checkboxes** via Qt's `ItemIsAutoTristate`

#### R15 — Layout + Splitter + Sizing Polish
- **Default window size:** 1000x650 → 1200x750 minimum, 1400x850 default
- **Splitter functional:** sensor panel changed from `setFixedWidth` to min/max width range
- **Sensor panel border removed:** no longer uses Card class
- **Tree expand/collapse:** visible disclosure triangles, animated, 20px indentation
- **Fan table column sizing:** Label=Stretch, others=ResizeToContents

#### R16 — Fan Table Cleanup
- **Status column removed** from fan table (4 columns: Label, Source, RPM, PWM%)
- **Equal-width columns** via global `QHeaderView.ResizeMode.Stretch`
- **Table stops expanding:** stretch=0 + `QSizePolicy.Maximum` — chart absorbs extra space

#### R17 — Settings Page Refinement
- **Safety tab removed** completely (daemon floors no longer configurable)
- **Telemetry → Syslog** renamed throughout (tab, labels, objectNames, error messages)
- **Import/Export expanded** — exports all configurable state (settings + profiles + theme), auto-backup before import
- **Card sensor bindings** persisted in AppSettings

#### R18 — Daemon Autonomy (Rust)
- **Thermal safety rule** (`daemon/src/safety.rs`): CPU Tctl emergency — 105°C → 100%, hold until 80°C, recover at 60%. 8 unit tests.
- **Legacy floors removed**: MIN_PWM_PERCENT=20 (serial), per-header 20%/30% (hwmon), floor fields from Capabilities API — all removed
- **Profile model** (`daemon/src/profile.rs`): loads GUI v3 JSON profiles, evaluates graph/linear/flat curves
- **Profile engine** (`daemon/src/profile_engine.rs`): 1Hz headless curve evaluation loop (logging-only, see limitations)
- **State persistence** (`daemon/src/daemon_state.rs`): atomic JSON at `/var/lib/control-ofc/daemon_state.json`
- **CLI args**: `--profile <name>`, `--profile-file <path>`, `OPENFAN_PROFILE` env var
- **API**: `POST /profile/activate` for runtime profile switching
- **Precedence**: CLI > env > API > persisted state > no profile (imperative mode)
- **Known limitations**: profile engine cannot write PWM directly (FanController needs Arc refactor); hwmon writes need auto-lease; simultaneous GUI+daemon control needs ownership model. See `docs/14_Risks_Gaps_and_Future_Work.md`.

#### Code Audit
- **Daemon `.unwrap()` → `.expect()`:** ~30 production unwrap calls replaced with context messages
- **APP_VERSION synced:** constants.py now matches pyproject.toml
- **Documentation updated:** CODE_REVIEW_LOG, AUDIT_REPORT refreshed

#### Statistics
- GUI: 313 tests, all passing
- Daemon: 245 tests (216 unit + 29 integration), all passing
- 0 open P0/P1 issues

## [0.18.0] — 2026-03-23

### Forward Tasks: History, SSE, Calibration (IMP-008, IMP-003, IMP-007)

#### IMP-008: Daemon-Side Sensor History Ring Buffer
- **Daemon:** new `daemon/src/health/history.rs` — `HistoryRing` per-entity ring buffer (250
  samples max, ~118 KB total), 5 unit tests
- **Daemon:** `GET /sensors/history?id=...&last=N` endpoint returns timestamped array
- **Daemon:** `hwmon_poll_loop` records sensor values to history after each poll cycle
- **GUI:** new `HistoryPoint`, `SensorHistory` models + `parse_sensor_history()` parser
- **GUI:** `DaemonClient.sensor_history()` method for fetching daemon-side history
- **GUI:** `HistoryStore.prefill_sensor()` — converts daemon wall-clock timestamps to monotonic
  for integration with existing pruning logic
- **GUI:** `PollingService` pre-fills history from daemon on first successful connection —
  timeline chart shows data immediately on GUI start
- **GUI:** `FakeDaemonClient.sensor_history()` mock for tests

#### IMP-003: Server-Sent Events for Real-Time Updates
- **Daemon:** new `daemon/src/api/sse.rs` — `events_handler` returning SSE stream with `update`
  events every 1s containing combined sensor+fan data
- **Daemon:** `GET /events` route with 5s heartbeat via `KeepAlive`
- **Daemon:** added `futures-util` and `tokio-stream` dependencies
- **GUI:** new `src/control_ofc/services/event_stream.py` — `EventStreamService` with Qt signals
  (`sensors_ready`, `fans_ready`), runs in daemon thread, exponential backoff reconnect
- **GUI:** added `httpx-sse>=0.4` dependency to `pyproject.toml`
- **Note:** polling service retained for capabilities, headers, lease, and telemetry

#### IMP-007: Fan RPM-to-PWM Calibration Sweep
- **Daemon:** new `daemon/src/api/calibration.rs` — `CalPoint`, `CalibrationResult`,
  `CalibrationRequest`, `check_thermal_safety()` (85°C abort), 3 unit tests
- **Daemon:** `POST /fans/openfan/{channel}/calibrate` handler with thermal abort, pre-cal PWM
  restore, step clamping (2-20 steps, 2-15s hold)
- **Daemon:** `CalibrationResponse` in responses.rs
- **GUI:** `CalPoint`, `CalibrationResult` models + `parse_calibration_result()` parser
- **GUI:** `DaemonClient.calibrate_openfan(channel, steps, hold_seconds)` method
- **GUI:** `FakeDaemonClient.calibrate_openfan()` mock for tests

#### Test counts
- Daemon: 201 unit tests + 29 integration tests = 230 total (was 197+29=226)
- GUI: 278 tests (was 272)
- All passing

## [0.17.0] — 2026-03-22

### Curves Section "Killer UI" Rework (Refinement 9)
- **Curve card complete rewrite** — compact, information-dense cards with:
  - Name (bold, 12px, ellipsis+tooltip), type badge, Actions dropdown
  - Sensor + live value line
  - Preview: graph sparkline re-renders at widget size (not fixed 140px), Linear/Flat text summaries
  - Used-by with truncation (3 max + "+N"), status chip (Assigned/Unassigned)
  - Margins reduced to 6/4/6/4, spacing 2px — ~50% smaller than previous
- **Preview rendering fix** — graph sparklines now render at actual widget width via `resizeEvent`,
  not hardcoded 140×36. Uses QPen width=2 for visibility. Empty/single-point curves handled.
- **Curve library toolbar** — search box ("Search curves...") + type filter dropdown (All/Graph/Linear/Flat).
  Cards filter in real-time as user types. Scales to 15+ curves.
- **maxHeight removed from curves scroll** — curves section expands to use available space
  instead of being capped at 300px.
- **Grid spacing reduced** from 8px to 6px for denser layout.
- 272 tests (271 → 272), all passing

## [0.16.0] — 2026-03-22

### Curve Cards Polish + Edit Dialogs (Refinement 7)
- **R7-001: Sparkline scaling** — removed fixed height, uses dynamic min height of 24px.
  Linear/Flat curves show text summaries ("30C->80C: 20%->100%" or "Flat: 50%").
- **R7-002: Compact curve cards** — reduced min/max width (150-280px), tighter margins.
- **R7-003: Actions discoverability** — replaced cryptic kebab with "Actions ▾" label button.
  Removed separate Edit button (Edit is first menu item).
- **R7-004: Linear/Flat edit dialog** — new `CurveEditDialog` with name, sensor, params.
  Graph curves use embedded editor. Linear/Flat no longer need the graph widget.
- **R7-005: Applied guard** — `set_output()` returns early if control has no members.
- 271 tests (269 -> 271), all passing

## [0.15.1] — 2026-03-22

### End-to-End Audit Fixes + Test Coverage Expansion
- **P0-001 Fixed:** `parse_status()` now extracts `uptime_seconds` and `gui_last_seen_seconds_ago`
  from daemon responses. Both fields were defined on the model but never parsed.
- **P0-002 Fixed:** Batch poll exception handler now logs the error instead of silently swallowing.
- **P1-001/002 Fixed:** UI_INTERACTION_MAP updated — removed 5 stale ControlCard objectNames
  (moved to FanRoleDialog in v0.14.0), added all FanRoleDialog and CurveCard kebab objectNames.
- **New test file: `test_fan_role_dialog.py`** — 9 tests: mode toggle visibility (3), manual
  speed slider/spin sync (2), get_result fields (4).
- **New test file: `test_curve_card.py`** — 9 tests: sparkline rendering for graph/flat/empty/single
  point curves (5), sensor display (1), used-by labels (2), name display (1).
- **New test file: `test_member_editor.py`** — 4 tests: exclusive membership (assigned fans
  disabled with role name), unassigned fans enabled, all-enabled without assignments, get_members.
- **Merged `test_theme.py` into `test_theme_system.py`** — 3 unique tests moved, redundant file
  deleted.
- 269 tests total (246 → 269), all passing

## [0.15.0] — 2026-03-22

### Controls Manual Mode + Membership Rules + Curves Density (Refinement 6)
- **R6-007: Mix curve removed** — `CurveType.MIX`, `MixFunction`, mix fields, mix UI widgets,
  mix evaluation in control loop, and mix tests all removed. Unknown curve types in saved
  configs now fall back to Flat with a log warning (graceful migration). 7 mix tests removed.
- **R6-001: Manual mode speed UX** — FanRoleDialog now has slider+spin for manual output %
  when mode is Manual. Value saved to `manual_output_pct` on the control model and persisted
  with profile save. Curve selector and manual controls toggle based on mode.
- **R6-002: Exclusive fan membership** — MemberEditorDialog now receives `assigned_elsewhere`
  map. Fans already assigned to another role appear disabled with "Assigned to: RoleName"
  tooltip. Prevents duplicate fan assignment across roles.
- **R6-003: Hide manual controls in Curve mode** — FanRoleDialog hides manual slider/spin
  when mode is Curve, shows them only when mode is Manual. Curve selector hidden in Manual mode.
- **R6-005: Curve cards tightened** — reduced margins (10→8, 8→6), reduced spacing,
  min width 160px for better grid fit.
- **R6-006: Sparkline preview** — Graph curve cards now display a mini polyline preview
  (140×36px) rendered via QPainter. Updates on curve edit. Non-graph curves hide the sparkline.
  No pyqtgraph dependency — pure QPainterPath on QPixmap.
- 246 tests (253 - 7 removed mix tests), all passing

## [0.14.1] — 2026-03-21

### CRITICAL FIX: Profile activation now actually controls fans
- **Root cause: OperationMode stuck in READ_ONLY** — on daemon connection, the polling service
  set `ConnectionState.CONNECTED` but never transitioned mode from `READ_ONLY` to `AUTOMATIC`.
  The control loop's `_prerequisites_met()` requires `AUTOMATIC` or `MANUAL_OVERRIDE`, so
  **no PWM writes ever happened in live mode**. Fixed: polling `_on_connected()` now sets
  `AUTOMATIC` when daemon is available; `_on_disconnected()` reverts to `READ_ONLY`.
- **Immediate cycle on profile change** — `_on_profile_changed()` now calls `_cycle()`
  immediately instead of waiting up to 1 second for the next timer tick. Fan speeds change
  the instant a profile is activated.
- **Visual feedback on activate** — "Profile activated" success chip shown on the controls
  page when a profile is activated. Control cards refresh immediately.
- 253 tests, all passing

## [0.14.0] — 2026-03-21

### Controls & Curves UX Overhaul (Refinement 5)
- **R5-004 CRITICAL FIX: Control loop wiring in live mode** — `status_changed` signal was
  only connected in demo mode. Now wired in `main.py` for live daemon connections. This makes
  fan output changes visible on control cards in real deployments.
- **R5-006: Renamed "Control" → "Fan Role"** throughout all user-facing UI text: buttons,
  labels, tooltips, empty states, dialog titles. Internal code names unchanged.
- **R5-002/R5-009: Compact card redesign** — dense 5-row layout:
  - Row 1: Fan Role name + status chip (Applied/No members/Blocked)
  - Row 2: Members (compact, truncated + tooltip for overflow)
  - Row 3: Curve assignment with type badge
  - Row 4: Output + sensor context in one line
  - Row 5: Edit.../Delete actions + RPM
  - Removed: large output %, progress bar, mode combo, manual slider, source label
  - All detailed editing moved to Edit Fan Role dialog
- **R5-003: Curve edits update cards in real-time** — `_update_card_previews()` re-evaluates
  output for all cards referencing the curve being edited. Shows "Preview: XX%" immediately.
- **R5-005/R5-007: Curves section as primary work area** — controls section max 200px height
  (compact), curves section expanded to 300px. "Used by: Role1, Role2" shown on each CurveCard.
- **R5-008: Edit Fan Role dialog** — new `FanRoleDialog` with name, mode, curve selector,
  members summary + edit button. Opened via "Edit..." on card. Card stays minimal.
- **New widget:** `src/control_ofc/ui/widgets/fan_role_dialog.py`
- 253 tests (251 → 253), all passing

## [0.13.1] — 2026-03-21

### Batch Poll Endpoint + GUI Wiring
- **IMP-010: Batch poll** — GUI now uses `GET /poll` to fetch status+sensors+fans in one HTTP
  call (was 3 separate calls). Falls back to individual endpoints if `/poll` not available.
- Added `DaemonClient.poll()` method and `FakeDaemonClient.poll()` for tests.
- Polling service uses batch endpoint by default, reducing per-cycle HTTP overhead by ~60%.
- 251 tests, all passing.

## [0.13.0] — 2026-03-21

### Daemon + GUI Improvements — Safety, Performance, Observability
**Daemon changes (control-ofc-daemon):**
- **IMP-001: Fan stall detection** — `stall_detected: bool` field added to `/fans` response.
  Daemon flags stall when RPM=0 and last_commanded_pwm > 20% (safety floor).
  Applied to both OpenFan and hwmon fans.
- **IMP-004: Sensor rate/min/max fields** — `rate_c_per_s`, `session_min_c`, `session_max_c`
  added to `/sensors` response schema (currently None; daemon-side tracking TBD in StateCache).
- **IMP-009: Daemon uptime** — `uptime_seconds` field added to `/status` response.
  `start_time: Instant` tracked in AppState.

**GUI changes:**
- **IMP-001:** `FanReading.stall_detected` parsed from daemon. `_update_warnings()` now counts
  stalled fans (RPM=0 while PWM>floor) in the warning count badge.
- **IMP-004/005:** `SensorReading` gains `rate_c_per_s`, `session_min_c`, `session_max_c` fields
  (parsed when daemon provides them, None otherwise — forward compatible).
- **IMP-006: Reduced capabilities polling** — capabilities and hwmon headers now fetched only
  on first successful poll (not every N cycles). Re-fetched automatically on reconnect after
  disconnection by resetting poll_count to 0.
- **IMP-009:** `DaemonStatus.uptime_seconds` parsed for diagnostics display.
- All daemon tests pass (29/29). All GUI tests pass (251/251).

## [0.12.0] — 2026-03-21

### Controls UX + Flow Simplification (Refinement 4)
- **Profile management simplified:**
  - Replaced "New Profile" + "Delete" buttons with single "Manage Profiles..." dropdown menu
  - Menu includes: New, Rename, Duplicate, Delete
  - New profile prompts for name immediately
  - Profile rename persists on save
- **+Control guided dropdown:**
  - Replaced flat "+ Control" button with dropdown: "Single Output Control" / "Group Control (Multi-Fan)"
  - New control immediately opens member editor for assignment
  - Name prompt at creation time
- **Curves creation simplified:**
  - Replaced `[Type combo] [+ Curve]` with single "+ Curve" button that opens type-selection menu
  - Menu shows: Graph / Linear / Flat / Mix as clear options
  - CurveCard now has kebab menu (⋮) with: Edit, Rename, Duplicate, Delete
- **Progressive disclosure:**
  - Curves section hidden when no controls exist
  - Shows guidance: "Create a Control first. Curves are assigned to Controls."
  - Curves section appears when first control is created
- **Apply feedback:**
  - Control cards show status badge: "Applied" (green) when control loop writes, "Not applied" when no members
  - Status updates in real-time from control loop output
- **Sensor selector** already above graph (§8 — verified, no change needed)
- 251 tests, all passing

## [0.11.1] — 2026-03-21

### Stability Gate — Audit, Refactor, Tests (v0.11.1)
- **P2-001 Fixed:** Removed dead `tuning_changed` signal from ControlCard (never emitted after
  tuning UI removal) and its connection in ControlsPage
- **P2-002 Fixed:** Cached `get_curve()` result in delete handler to avoid double call
- **P2-003 Fixed:** Updated UI_INTERACTION_MAP — removed 8 stale entries for deleted widgets
  (Controls_List_profiles, Controls_Btn_duplicate, etc.), added current widgets
  (Controls_Combo_profile, CurveCard_Btn_edit/delete, Controls_Btn_closeEditor)
- **P1-001/002 Fixed:** Added 2 delete integration tests:
  - `test_delete_control_removes_from_profile` — verifies control removed from model + card grid
  - `test_delete_curve_cascades_to_controls` — verifies curve deletion clears curve_id on
    referencing controls
- Produced stability gate documents: REVIEW_NOTES.md, AUDIT_REPORT.md, TEST_PLAN.md, REFACTOR_PLAN.md
- 251 tests total (249 -> 251), all passing

## [0.11.0] — 2026-03-21

### Controls Refinement 2 — Delete Flows, Scaling, Labels, Hover, Apply (R2-001 to R2-007)
- **R2-001 Fixed: Delete flows** — ControlCard and CurveCard now have Delete buttons with
  `delete_requested` signals. Deleting a curve unassigns it from all controls that reference it.
  Deleting a control removes it from the profile.
- **R2-002 Fixed: Graph auto-scaling** — removed hardcoded `setXRange(20, 90)`, now auto-scales
  X axis to fit all points with 5C padding. Points at 0C or 120C are now visible.
- **R2-003 Fixed: Friendly sensor labels** — CurveCard shows resolved sensor label + live
  value (`CPU Package — 43.1C`) instead of raw hwmon ID. Raw ID in tooltip only.
- **R2-004: Save model** — already correct (profile-level save persists all changes). No
  structural changes needed.
- **R2-005 Fixed: Tuning UI removed** — removed tuning button, frame, and 6 spinboxes from
  ControlCard. Tuning parameters remain on the data model (still used by control loop) but
  are no longer exposed in the UI.
- **R2-006 Fixed: Hover tooltip** — moving mouse over the graph area now shows
  `Hover: XX.X°C -> YY.Y%` using the curve's `interpolate()` method. Updates in real-time
  as cursor moves across the plot.
- **R2-007 Fixed: Controls apply semantics** — removed backward-compat "empty members = write
  to all fans" path from control loop. Controls with no members now skip writes entirely.
  Cards show "No outputs assigned" guidance. Removed dead `_write_all_fans` and
  `_should_write_group` methods.
- 249 tests, all passing

## [0.10.1] — 2026-03-21

### Sensor Visibility + Meaningful Card Readouts (Refinement 2)
- **Control card readout redesign:**
  - Large output % shows "—" instead of "0%" when no outputs assigned
  - Source context line shows `SensorName: XX.X°C -> YY%` (curve mode) or `Manual` (manual mode)
  - "No outputs assigned" text replaces confusing 0% for empty controls
  - RPM display from aggregated fan readings across control members
  - Members count shown as secondary info
- **Sensor dropdown upgrade:**
  - Each sensor item shows label + live value: `CPU Package (CpuTemp) — 43.1°C`
  - Values update on each sensor poll without resetting selection
- **Curve editor live readout:**
  - New `CurveEditor_Label_sensorValue` shows `XX.X°C -> YY%` inline next to sensor selector
  - Updates in real-time as sensor values change
  - Computed output from curve interpolation shown alongside temperature
- **Control output context wiring:**
  - `update_control_outputs` now passes sensor name + value to each card
  - Cards show the full chain: sensor reading → curve evaluation → commanded output
- 249 tests, all passing

## [0.10.0] — 2026-03-21

### Controls Page Refactor — FanControl-Style 2-Section Layout
- **Complete layout restructure** from 3-column horizontal to 2-section vertical:
  - **Profile bar** (top) — compact combo + Activate + Save + New/Delete buttons
  - **Section A: Controls grid** — responsive 4-column grid of ControlCard widgets
  - **Section B: Curves grid** — responsive 4-column grid of CurveCard widgets
  - **Expandable curve editor** — appears below curves when "Edit" clicked, with Close button
- **New widget: CurveCard** — compact card showing curve name, type, sensor, and Edit button
- **Removed:**
  - Left column (profile QListWidget, logical controls buttons section)
  - Right column (curve QListWidget, always-visible curve editor)
  - Replaced with the 2-section grid layout matching Ideal.png
- **Profile management** moved from list panel to compact top-bar combo
- **Control CRUD** via "+ Control" button in section header
- **Curve CRUD** via "+ Curve" button with type selector in section header
- **Empty states** for both controls and curves sections
- All existing test assertions updated for new layout (combo vs list references)
- 249 tests, all passing

## [0.9.1] — 2026-03-20

### Test Coverage Gaps Fixed
All 9 oversight gaps from test-overview.md resolved:
- **P1: Mouse drag test** — simulates press→move→release via viewport eventFilter with
  QMouseEvent, verifies point moves and undo pushed at drag start. Also fixed deprecated
  `event.pos()` to `event.position().toPoint()` in eventFilter.
- **P1: Curve selection test** — replaced always-skipping test with one that creates a
  2-curve profile explicitly, no longer depends on default profiles
- **P1: History pruning test** — uses `unittest.mock.patch` on `time.monotonic` to advance
  clock by 6s, verifies entries older than max_age_s=5 are pruned
- **P2: Write-response parsers** — 7 new tests for `parse_set_pwm`, `parse_set_pwm_all`,
  `parse_set_rpm`, `parse_lease_result`, `parse_lease_released`, `parse_hwmon_set_pwm`,
  `parse_telemetry_config`
- **P2: V1 migration group target** — tests `target_type="group"` with `target_id="all"`,
  verifies empty members list and "Group: all" name
- **P2: Hardcoded color lint expanded** — scan now includes `src/control_ofc/ui/` root
  (sidebar, status_banner, splash, about_dialog, branding)
- **P2: Step-down rate limiting** — test drops temp from 70°C to 30°C (curve: 80%→20%),
  verifies step_down_pct=10 clamps output to 70% (not 20%)
- **P2: AppSettings roundtrip** — fun_mode and show_splash explicitly tested in
  serialization round-trip
- 249 tests total (236 -> 249), all passing, zero warnings

## [0.9.0] — 2026-03-20

### Refactor — Audit Fixes, Mix Source Editor, Press-to-Drag, Tests
- **R-001 Fixed:** Removed stale `Controls_Btn_override` from UI_INTERACTION_MAP, corrected
  daemon call count from 2 to 1
- **R-002 Fixed:** Mix curve editor now has interactive source management — QListWidget showing
  current sources, "Add Source" button with dropdown of available curves (excludes self and
  other Mix curves), "Remove" button. CurveEditor gains `set_available_curves()` method.
- **R-003 Fixed:** Removed dead `aliases_path()` and `groups_path()` from paths.py
- **R-004 Fixed:** Renamed duplicate `Sidebar_Brand` to `Sidebar_Brand_image` / `Sidebar_Brand_text`
- **R-005 Fixed:** Curve editor drag now uses viewport event filter (`eventFilter` on
  `PlotWidget.viewport()`) for true press-to-drag instead of `sigMouseClicked` which fires
  on release. Drag starts on mouse press near a point, ends on release.
- **R-006 Fixed:** Tuning spinboxes use `editingFinished` instead of `valueChanged` to avoid
  per-keystroke signal spam
- **R-007 Fixed:** Replaced fragile `__import__` hack with normal `CurvePoint` import
- **New tests** (`tests/test_control_card.py`): tuning model updates, mode toggle visibility,
  click-to-rename flow, output display. Mix source add/remove/exclude-self tests in
  `tests/test_curve_editor.py`.
- 236 tests total (224 -> 236), all passing

## [0.8.3] — 2026-03-20

### Control Cards Rework — Dashboard Tile Style
- **Complete visual rework** of ControlCard widget to dashboard tile style (NZXT CAM / Home Assistant inspired):
  - Gear icon + name (double-click to rename) + mode dropdown in header row
  - Large centered output percentage (32px bold) as primary visual element
  - Slim progress bar below output value
  - Clean mode-specific controls: curve dropdown (Curve mode) or slider+spin (Manual mode)
  - Compact members summary with "Members" button
  - Tuning section collapsed by default, expandable on demand
- Replaces the previous dense card layout with a cleaner, more scannable design
- Members summary simplified to "N outputs assigned" (detail in Members dialog)
- All existing signals and behavior preserved — visual-only change
- 224 tests, all passing

## [0.8.2] — 2026-03-19

### Curve Editor Drag Fix
- **Fixed:** Graph points are now draggable — pyqtgraph's built-in pan/zoom was intercepting
  mouse drag events. Fixed by disabling `setMouseEnabled(x=False, y=False)` and
  `setMenuEnabled(False)` on the PlotItem, and adding proper `_drag_active` tracking
  so `sigMouseMoved` only processes during an active drag (button held on a point).
- Undo is now pushed at drag start (not release), preventing lost states
- 224 tests, all passing

## [0.8.1] — 2026-03-19

### Controls Page Audit Fixes
- **CTRL-001 Fixed:** Sensor selection no longer silently reverts to first sensor.
  Root cause: `set_available_sensors()` cleared and repopulated the combo every 1Hz poll
  tick without preserving selection. Fix: block signals during repopulation, skip when
  sensor list unchanged, persist selection to curve model immediately on user change.
- **CTRL-002:** Already fixed in M3 (drag, add/remove, keyboard nudge, undo/redo)
- **CTRL-003:** Already fixed in M5 (logical control cards with mode, curve, manual, members)
- **CTRL-004:** Already fixed in M5 (logical controls CRUD, member editor, curve assignment)
- Added 4 sensor selection tests: persists to model, survives refresh, preserves on list change,
  handles removed sensor
- 224 tests total (220 -> 224), all passing

## [0.8.0] — 2026-03-19

### FanControl-Style Controls Redesign (GUI-M9)
- **Per-control tuning parameters** — 6 new fields on LogicalControl:
  - Step up/down %/s (rate limiting for output changes)
  - Start % (kickstart for fans resuming from 0%)
  - Stop % (snap to 0% below threshold)
  - Offset % (fixed offset added to curve output)
  - Minimum % (hard floor — output never below this)
- **Tuning pipeline in control loop** — applied post-evaluation:
  offset -> minimum -> step rate limiting -> start/stop thresholds -> clamp
- **Mix curve type** — combines multiple source curves via Max, Min, or Average function.
  Control loop resolves source curves recursively (prevents infinite loops).
- **Control card redesign**:
  - Output progress bar showing current % with bold label
  - Click-to-rename: double-click name label for inline editing
  - Collapsible tuning section with 6 spinboxes
  - Live output updates from control loop status signal
- **Mix curve editor** — function selector (Max/Min/Average) and source curve display
  in the curve editor when a Mix curve is selected
- **Control loop status** includes per-control output values (`control_outputs` dict)
  wired through MainWindow to ControlsPage cards
- **Profile data model v3** — backward compatible (new fields have defaults)
- **New tests**:
  - Tuning: offset, minimum floor, step rate limiting, stop threshold, output in status
  - Mix curves: max, min, average of source curves
  - Data model: Mix curve roundtrip, tuning parameter roundtrip
- 220 tests total (209 -> 220), all passing

## [0.7.2] — 2026-03-19

### Audit P2 Polish (GUI-M8 follow-up)
- **AUD-005:** Added rename for logical controls (Rename button + QInputDialog)
- **AUD-006:** Added rename for curves (Rename button + QInputDialog)
- **AUD-007:** Removed dead `DemoService.groups()` and `DemoService.freshness_for()` methods
- **AUD-008:** Added Linear/Flat curve parameter editors — selecting a Linear curve shows
  start/end temperature and output spinboxes; selecting a Flat curve shows a single output
  spinbox. Graph editor (plot + table) hides for non-Graph types.
- **AUD-009:** Added empty state for controls page center column — shows guidance text when
  profile has zero logical controls
- **AUD-010:** Updated `docs/UI_INTERACTION_MAP.md` with all M7 widgets: NavButton_About,
  Settings_Check_funMode, Settings_Check_showSplash, Settings_Btn_saveTheme,
  Settings_Btn_applyThemeToApp, Settings_ThemeEditor, About_Btn_close,
  Controls_Btn_renameControl, Controls_Btn_renameCurve, ControlCard_Btn_editMembers
- **AUD-011:** Centralized asset path resolution — new `paths.assets_dir()` with fallback chain
  (dev layout, /opt/control-ofc, CWD). All branding consumers (splash, sidebar, about, icon)
  now use `control_ofc.ui.branding` helpers backed by `paths.assets_dir()`.
- **AUD-012:** Added curve selection wiring test (verifies curve dropdown change updates model)
- 209 tests + 1 skip, all passing

## [0.7.1] — 2026-03-19

### Audit P1 Fixes (GUI-M8 follow-up)
- **AUD-001 Fixed:** Wired MemberEditorDialog into ControlCard — "Edit Members" button on each
  control card opens dual-list dialog with available fan outputs from AppState (fans + hwmon headers).
  Membership changes update card display immediately.
- **AUD-002 Fixed:** Wired microcopy into main UI widgets:
  - Status banner: connection text ("Connected. Let's get cooling." / "Ghosted by the daemon.")
    and demo mode label use microcopy
  - Dashboard: no-hardware empty state title uses microcopy
  - Controls page: save confirmation uses microcopy ("Saved. Your fans remember." / "Settings saved")
- **AUD-003 Fixed:** Removed dead `manual_override_toggled` signal from ControlsPage and its
  connection + handler in MainWindow. Per-control manual mode (M5) replaced the global override.
- **AUD-004 Fixed:** Delete control now removes the selected control (tracked via card click)
  instead of always popping the last one.
- 209 tests, all passing

## [0.7.0] — 2026-03-19

### Branding, Splash, Icons, and Parody Look & Feel (GUI-M7)
- **Asset system** — `assets/branding/` structure with:
  - `splash/splash.png` — cheeky parody splash screen image
  - `banner.png` — clean "Not Just Control-OFC" logo
  - `app_icon/app_icon.svg` — fan-blade-in-rounded-square SVG icon
- **Splash screen** — `AppSplashScreen` shown on startup for 3s while connecting:
  - Uses SplashScreen.png as background
  - Status messages update through startup stages (init, connecting, loading, ready)
  - Closeable via setting (`show_splash` in app settings)
  - Uses microcopy system for playful/professional text variants
- **Sidebar branding** — Banner.png displayed at top of sidebar navigation
- **About dialog** — accessible via "About" button in sidebar:
  - Shows banner image, app name, version, tagline, credits
  - Uses microcopy for fun/professional mode switching
- **App icon** — SVG fan icon set as window/taskbar icon via `QIcon`
- **Fun mode** — togglable microcopy system (`Settings > Application > Fun mode`):
  - 15 text pairs (splash, dashboard, status, profile, override, etc.)
  - All cheeky text in single `microcopy.py` module, no scattered strings
  - Default ON, persisted in app settings
- **Brand tokens** added to theme: `brand_primary`, `brand_secondary`, `brand_accent`
- **New tests** (`tests/test_branding.py`) — 16 tests covering:
  - Asset existence (banner, splash, icon SVG)
  - SVG validity (parseable XML)
  - Microcopy fun/pro modes, toggle persistence, key coverage
  - Splash creation and status updates
  - About dialog rendering and close button
  - Branding helpers (icon loading, path resolution)
- 209 tests total (193 -> 209), all passing

## [0.6.0] — 2026-03-19

### Full Theme & Colour Customisation System (GUI-M6)
- **Expanded theme tokens** from ~25 to 50+ covering all surfaces, navigation, inputs,
  tables, dialogs, charts, status, and interactive states per spec
- **Renamed tokens** to spec names (e.g., `window_bg` -> `app_bg`, `panel_bg` -> `surface_1`,
  `success` -> `status_ok`) with automatic migration of old theme files
- **Eliminated all hardcoded colours** from widget code — curve editor and timeline chart
  now use `chart_point_selected`, `chart_point_hover`, `chart_bg`, `chart_axis_text` tokens
- **Theme Editor** in Settings page:
  - Grouped token editing: Core, Borders, Interactive States, Status, Charts, Navigation,
    Inputs, Tables, Dialogs — each with colour swatches, hex display, per-token reset
  - Live preview panel with sample buttons, cards, status chips, and table
  - Contrast warnings (WCAG luminance ratio) for critical text/background pairs
- **Theme management**: Load, Save, Import, Export, and Apply to Application buttons
- **Stylesheet expanded** with QTableWidget, QListWidget, QDialog, and slider styling
  all driven from tokens
- **New tests** (`tests/test_theme_system.py`):
  - Token coverage: all 50+ required tokens present with valid hex values
  - Stylesheet references core tokens
  - Contrast checker: high/low contrast detection, default theme passes, bad theme warns
  - Token migration: v1 -> v2 name mapping
  - Save/load roundtrip, partial import with defaults fallback
  - Hardcoded colour lint: fails if new hex literals appear in widget/page code
- 193 tests total (182 -> 193), all passing

## [0.5.0] — 2026-03-19

### FanControl-Style Logical Controls (GUI-M5)
- **Data model overhaul** — replaced `TargetAssignment` + `CurveDefinition` with:
  - `LogicalControl`: named fan groups with mode (Curve/Manual), member list, and curve reference
  - `CurveConfig`: typed curve library supporting Graph, Linear, and Flat curve types
  - `ControlMember`: physical fan output (OpenFan channel or hwmon header) assigned to a group
  - `ControlMode` enum: CURVE (driven by sensor+curve) / MANUAL (fixed output %)
  - `CurveType` enum: GRAPH (multi-point interpolation) / LINEAR (2-point ramp) / FLAT (constant)
  - Profile version bumped to v2 with automatic v1 migration on load
- **Controls page rewritten** — 3-column FanControl-style layout:
  - Left: profile list + logical controls CRUD + save/unsaved indicator
  - Center: scrollable control cards (mode switch, curve selector, manual slider, member summary)
  - Right: curve library panel (add/delete curves by type) + interactive graph curve editor
- **Control loop updated** — iterates over logical controls instead of target assignments;
  handles Curve mode (sensor-driven), Manual mode (fixed output), and empty-member fallback
- **New widgets**:
  - `ControlCard` — card widget for one logical control with mode toggle, curve selector, manual slider
  - `MemberEditorDialog` — dual-list dialog for assigning physical outputs to a control group
- **New tests**:
  - Manual mode control loop evaluation
  - Linear and flat curve type interpolation in control loop
  - v1→v2 profile migration
  - CurveConfig roundtrip for all types (Graph, Linear, Flat)
- 182 tests total (171 -> 182), all passing

## [0.4.0] — 2026-03-19

### Dashboard UX, Overview, and Empty States (GUI-M4)
- Redesigned "No Hardware Detected" state with:
  - Actionable title: "No Sensors or Fans Reported"
  - Subsystem status breakdown (OpenFan, hwmon, Telemetry) with live capability updates
  - "What to do next" card with troubleshooting steps
  - "Open Diagnostics" button that navigates to Diagnostics page
- Added subsystem health display from daemon status (shows error reasons like "permission denied")
- Fixed dashboard not switching to live content when fans arrive without sensors
- Added GPU temperature color coding (warning >80C, critical >90C)
- Added operation mode badge on live dashboard (DEMO MODE / MANUAL OVERRIDE / READ ONLY)
- Wired capabilities and status signals to dashboard for real-time subsystem health
- Added `tests/test_dashboard.py` — 15 tests covering:
  - State transitions: disconnected, no-hardware, live content, disconnect-reset
  - Subsystem health labels from capabilities and daemon status
  - Card content updates (CPU/GPU temps, fan count, warning count)
  - Mode badge display
  - Open Diagnostics button exists
- 171 tests total (156 -> 171), all passing

## [0.3.0] — 2026-03-19

### Controls Page UX + Fan Curve Editor (GUI-M3)
- Rebuilt curve editor with full interactivity:
  - Drag points to change temperature/output mapping with real-time updates
  - Add points (double-click on graph or "+ Add Point" button)
  - Remove points (select + Delete/Backspace, min 2 enforced)
  - Keyboard nudges (arrow keys, 1 degree/1% per press, respects neighbour constraints)
  - Undo/redo (Ctrl+Z / Ctrl+Shift+Z, 50-level stack)
  - Hover highlight with enlarged points, selection ring, live coordinate display
  - Current sensor reading marker (vertical dashed line + diamond dot on curve)
  - Preset curves: Linear, Quiet, Aggressive (load from dropdown)
  - Monotonic x-constraint: points can't cross neighbours during drag or nudge
- Fixed Controls page button layout:
  - Replaced cramped 4-button horizontal row with 2x2 grid
  - Changed left/right columns from `setFixedWidth(240)` to `setMinimumWidth(200)` + `setMaximumWidth(300)` for responsive resizing
  - Added tooltips to all profile action buttons and override button
  - Added word wrap to override status and active profile labels
- Wired live sensor values from AppState to curve editor marker
- Added `tests/test_curve_editor.py` — 35 tests covering table editing, add/remove, undo/redo, presets, keyboard nudge, sensor marker, monotonic constraints
- 156 tests total (121 -> 156), all passing

## [0.2.0] — 2026-03-19

### Quality & Contract Tightening (GUI-2)
- Expanded `docs/UI_INTERACTION_MAP.md` with daemon call, success effect, and error columns per widget
- Enhanced `FakeDaemonClient` with error injection: `simulate_error()`, `clear_errors()`, `simulate_unavailable()`
- Added JSON fixtures under `tests/fixtures/` (status_ok, status_no_openfan, capabilities_no_telemetry, error_validation, error_unavailable)
- Added 3 new fixture helpers: `fake_client_unavailable`, `fake_client_no_openfan`, `capabilities_no_telemetry`
- Added `tests/test_ui_contracts.py` — 9 contract tests covering Controls (activate, delete, save),
  Settings (save, telemetry daemon call, no-client error, daemon error), and Diagnostics (refresh, export bundle)
- Added `tests/test_capability_gating.py` — 4 tests verifying UI enable/disable based on daemon capabilities
- Added telemetry widget capability gating to `SettingsPage` (disabled when `telemetry_supported=False`)
- Added override button capability gating to `ControlsPage` (disabled when no write support)
- Created `docs/CODE_REVIEW_LOG.md` — module-by-module review of all 21 non-trivial files
- 121 tests total (108 → 121), all passing

## [0.1.0]

### UI Testing Foundation
- Assigned unique `objectName` to all 38 interactive widgets using `{Page}_{Role}_{Purpose}` convention
- Replaced duplicate "PrimaryButton" objectNames with unique identifiers per widget
- Added `FakeDaemonClient` test harness in `tests/conftest.py` with shared fixtures
- Added 9 automated click tests (`tests/test_ui_clicks.py`) covering all screens:
  sidebar navigation, profile creation, override toggle, tab switching,
  telemetry checkbox, log clearing, refresh button, error banner dismiss,
  chart range selection
- Created `docs/UI_INTERACTION_MAP.md` — full widget inventory with test coverage tracking
- 108 tests total (99 existing + 9 new)

### Bug fixes
- Dashboard 3-state empty state: disconnected → no hardware → live content (was blank when connected with no sensors)

### Phase 8 — Polish
- `ErrorBanner` widget: dismissible error/warning/info banners with auto-dismiss timer
- Dashboard empty state: 3-state stack (Not Connected / No Hardware Detected / live content)
- Connection state error banner: warning on disconnect, info on reconnect
- Keyboard shortcuts on Controls page: Ctrl+S to save, Escape to reset
- Dashboard switches between empty state and content via QStackedWidget

### Phase 7 — Diagnostics
- `DiagnosticsService`: timestamped event log (200-event ring buffer), support bundle export
- Full diagnostics page with six tabs: Overview, Sensors, Fans, Lease, Telemetry, Event Log
- Overview: daemon version/status, subsystem health, device discovery, feature flags
- Sensor/fan health tables with freshness color coding (fresh/stale/invalid)
- Lease status display (held/not-held, TTL, owner hint)
- Telemetry status display (connection state, queue depth, error count)
- Support bundle export with system info, state snapshot, and event history
- 6 new tests (99 total)

### Phase 6 — Settings
- `AppSettingsService`: GUI preferences with JSON persistence
- Full settings page with five tabs: Application, Themes, Safety, Telemetry, Import/Export
- Theme select/import/export from `~/.config/control-ofc/themes/`
- Safety floors read-only display from daemon capabilities
- Telemetry config write-through to daemon via `POST /telemetry/config`
- GUI settings export/import with file dialogs
- 7 new tests (93 total)

### Phase 5 — Control loop
- `ControlLoopService`: curve evaluation with 2°C hysteresis deadband and 1% PWM write suppression
- `LeaseService`: hwmon lease acquire/renew/release lifecycle with auto-renewal timer
- OpenFan and hwmon write paths (single target and set-all)
- Manual override mode with explicit enter/exit and profile-change auto-exit
- Mode reconciliation: AUTOMATIC, MANUAL_OVERRIDE, READ_ONLY, DEMO
- Stale/invalid sensor handling with warnings
- Wired into `MainWindow` and `main.py` for both live and demo modes
- 28 new tests (86 total)

### Phase 4 — Controls
- `ProfileService`: profile CRUD with JSON persistence under `~/.config/control-ofc/profiles/`
- `CurveEditor`: interactive pyqtgraph curve editor with synced numeric table
- `ControlsPage`: profile list, activate/new/duplicate/delete, save/reset, manual override toggle
- Three default profiles: Quiet, Balanced, Performance (5-point linear curves)
- Linear interpolation engine in `CurveDefinition`
- 11 new tests (58 total)

### Phase 3 — Dashboard
- `DashboardPage`: summary cards (CPU/GPU/MB temp, active profile, fan count, warnings)
- `TimelineChart`: pyqtgraph chart with 9 selectable time ranges
- Fan card grid with RPM/PWM display and freshness indicators
- Wired to `AppState` signals for live updates

### Phase 2 — API and state wiring
- `DaemonClient`: synchronous httpx client over Unix socket with typed responses
- `PollingService`: QTimer + QThread periodic reads with auto-reconnect
- `AppState`: central QObject with signals for all state changes
- `HistoryStore`: 2-hour rolling time-series buffer
- `DemoService`: synthetic data for 10 fans, 6 sensors, 2 hwmon headers, 5 groups
- 20+ typed API models with parse helpers

### Phase 1 — Foundation
- Application shell with sidebar navigation and stacked pages
- Token-based dark theme with QSS stylesheet generation
- XDG-compliant path helpers
- Error types for daemon communication
- `--demo` and `--socket` CLI arguments

[Unreleased]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.7.0...HEAD
[1.7.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.6.1...v1.7.0
[1.6.1]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.6...v1.1.0
[1.0.6]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.3...v1.0.4
[1.0.2]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v0.86.4...v1.0.0
