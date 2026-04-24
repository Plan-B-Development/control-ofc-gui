# Changelog

## [Unreleased]

Diagnostics enumeration truthfulness pass (Batch A of the remediation
tracked in `DIAGNOSTICS_REMEDIATION.md`). GUI-only; no daemon changes.

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

### Project Rebrand — OnlyFans → Control-OFC

**BREAKING CHANGE:** Complete project rebrand. All paths, package names, and identifiers have changed.

- **Package name:** `onlyfans` → `control-ofc-gui`
- **Import path:** `onlyfans.*` → `control_ofc.*`
- **CLI command:** `onlyfans` → `control-ofc-gui`
- **Display name:** "OnlyFans" → "Control-OFC"
- **Socket path:** `/run/onlyfans/onlyfans.sock` → `/run/control-ofc/control-ofc.sock`
- **Config dir:** `~/.config/onlyfans/` → `~/.config/control-ofc/`
- **Daemon service name:** `onlyfans-daemon` → `control-ofc-daemon`

**Migration:** Users upgrading from the OnlyFans-named installation must:
1. Uninstall old package: `pip uninstall onlyfans`
2. Install new: `pip install -e ".[dev]"` (or from package)
3. Move user config: `mv ~/.config/onlyfans ~/.config/control-ofc`
4. Update the daemon to v1.0.0 (new socket path)
5. Launch with: `control-ofc-gui` (or `control-ofc-gui --demo`)

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

[Unreleased]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.6...HEAD
[1.0.6]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.3...v1.0.4
[1.0.2]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Plan-B-Development/control-ofc-gui/compare/v0.86.4...v1.0.0
