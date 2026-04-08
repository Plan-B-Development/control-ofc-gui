# DECISIONS.md

This file records significant architecture, product, and implementation decisions for the OnlyFans GUI repository.

Use it as a concise change log for decisions that affect how the repo should evolve.

Recommended format:
- keep entries chronological
- assign simple decision IDs
- record the reason, alternatives if relevant, and impact
- update the status if a decision is later superseded

---

## DEC-001 — GUI is a daemon/API client only

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The GUI will communicate **only** with the existing daemon/API and will never access hardware directly.

### Why
- preserves a clean system boundary
- avoids duplicated hardware access logic
- respects daemon-owned safety rules
- improves portability and maintainability

### Implications
- no direct serial access
- no direct hwmon sysfs reads/writes
- no direct USB access
- all device state must be represented through daemon/API surfaces

---

## DEC-002 — Linux-first desktop GUI

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The first-class target is Linux desktop, especially CachyOS/Arch with KDE Plasma.

### Why
- aligns with the primary user environment
- reduces early platform abstraction burden
- lets the first release optimize for the real target

### Implications
- UI choices should feel native enough on KDE
- packaging can be Linux-first
- platform-specific assumptions should still be minimized where possible

---

## DEC-003 — Desktop window first, tray later

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
V1 prioritizes a normal desktop app window rather than a tray-first interaction model.

### Why
- core workflows are still being defined
- it simplifies first-pass navigation and state handling
- diagnostics and profile editing benefit from a fuller windowed layout

### Implications
- tray/minimise-to-tray is explicitly deferred
- main-window UX must stand on its own from day one

---

## DEC-004 — PySide6 is the default GUI framework

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Use PySide6 as the desktop UI framework unless a later decision explicitly changes this.

### Why
- strong fit for Linux desktop GUI work
- good layout/navigation/widget capabilities
- suitable for dark desktop utility design
- aligns with previous planning direction

### Alternatives considered
- PyQt6
- GTK / Libadwaita path
- web UI wrapped in desktop shell

### Implications
- app architecture should follow Qt-friendly model/service patterns
- avoid choices that would lock the repo into browser-centric UI design

---

## DEC-005 — Use pyqtgraph for live charts unless proven insufficient

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Use pyqtgraph for time-series fan/sensor charts in V1.

### Why
- suited to live, frequently updating graphs
- integrates naturally with PySide6
- good fit for telemetry-style desktop charts

### Implications
- chart wrapper widgets should remain modular
- avoid over-coupling charting logic to page code

---

## DEC-006 — Python 3.13 is the primary development target

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Use Python 3.13 for primary development, with validation on Python 3.14.

### Why
- balances modern runtime support with reduced edge-case risk
- keeps the project forward-moving without hard-anchoring to older Python unnecessarily

### Implications
- packaging and local setup docs should reflect this
- dependency choices should remain compatible with this target

---

## DEC-007 — Left-sidebar information architecture

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The app will use a left sidebar with four top-level sections:
- Dashboard
- Controls
- Settings
- Diagnostics

### Why
- suits a desktop utility with multiple working areas
- scales better than a small top-tab strip as the app grows
- aligns with earlier UX direction

### Implications
- app shell should be built around a stable navigation container and page stack

---

## DEC-008 — V1 uses a fixed dashboard layout

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The first dashboard is fixed-layout rather than user-custom card/panel driven.

### Why
- reduces complexity
- focuses effort on useful information rather than dashboard composition mechanics

### Implications
- custom dashboard layout is a future feature
- the initial dashboard should still be modular internally for later evolution

---

## DEC-009 — V1 controls page owns profile and group editing UX

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Profile, curve, and fan/group editing belongs on the Controls page rather than being split into Settings.

### Why
- better mental grouping for operational control workflows
- keeps Settings focused on application/runtime configuration

### Implications
- Controls page will likely become the richest page in the app
- Settings should not become a second home for profile logic

---

## DEC-010 — GUI owns fan-curve logic in V1

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Because the daemon is currently imperative and lacks a curve/profile engine, the GUI will implement the V1 fan-curve control loop.

### Why
- enables progress without blocking on daemon redesign
- matches the known backend capability gap

### Implications
- control logic must live in services, not widgets
- profile persistence is GUI-owned
- the repo needs careful tests around control calculations and failure handling

### Caution
This decision should be revisited if the daemon later gains a native policy engine.

---

## DEC-011 — Demo mode is required, not optional

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
A full demo mode must exist so the application is explorable without hardware.

### Why
- supports friend testing
- improves design iteration
- decouples UI development from hardware availability

### Implications
- demo mode should share as much UI/view-model surface area as possible with live mode
- avoid creating a fake separate application

---

## DEC-012 — One active profile at a time

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Only one profile is active at a time for the whole system.

### Why
- aligns with the desired mental model
- reduces ambiguity in control ownership and display state

### Implications
- UI should show active profile prominently
- profile switching must be explicit

---

## DEC-013 — Fan groups are many-to-many

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
A fan may belong to multiple groups.

### Why
- supports flexible logical labeling by role and location

### Implications
- group UI and persistence must not assume a single group per fan
- validation and display should handle overlaps clearly

---

## DEC-014 — Curves are single-sensor in V1

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Each V1 curve uses one sensor only.

### Why
- keeps the first implementation understandable
- avoids premature control complexity

### Implications
- no blending/max/avg logic in V1
- UI should not hint that advanced mixed-sensor behaviour already exists

---

## DEC-015 — Curve editing uses percentage output

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Curves are edited in fan output percentage, not raw PWM values, in V1.

### Why
- more intuitive user-facing model
- better aligned with profile editing mental model

### Implications
- any raw PWM mapping remains an internal/backend concern where relevant

---

## DEC-016 — New curves start with 5 points

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
New curve definitions default to 5 points.

### Why
- enough flexibility without overwhelming the initial editor

### Implications
- curve editor defaults and validation should assume 5-point creation path

---

## DEC-017 — Preset templates exist from the start

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Provide built-in preset templates such as Quiet, Balanced, and Performance.

### Why
- speeds up first use
- provides good starting points for iteration

### Implications
- profile/curve creation UX should expose them clearly

---

## DEC-018 — Dark theme first

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The default experience is a dark theme.

### Why
- matches the user’s explicit preference
- suits the technical utility aesthetic
- fits the parody branding direction

### Implications
- default charts, warning colours, surfaces, and focus states must be readable on dark backgrounds

---

## DEC-019 — Full theme editor is deferred, but theme import/export is in scope

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
V1 will not ship a full visual palette editor, but theme import/export should exist from the start.

### Why
- keeps V1 manageable
- preserves a future path to deeper customization

### Implications
- theme system should use a structured token model even if the editing UI is limited initially

---

## DEC-020 — Diagnostics is a first-class page

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Diagnostics is a top-level page, not a hidden advanced submenu.

### Why
- troubleshooting is a major part of this product’s real use
- helps users and testers self-diagnose issues faster

### Implications
- diagnostics gets real design attention
- support bundle export and last-error actions should be treated as normal UX

---

## DEC-021 — 2-hour rolling history buffer

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
The GUI retains up to 2 hours of recent polling history for charts.

### Why
- matches the explicit requirement
- gives meaningful recent operational visibility without requiring long-term storage design in V1

### Implications
- charting/history services must implement bounded retention
- persistence of historical telemetry across launches is not implied unless later decided

---

## DEC-022 — Runtime-editable daemon settings are limited to supported API surfaces

**Status:** Accepted  
**Date:** <PLACEHOLDER_DATE>

### Decision
Only daemon settings that are explicitly writable through the runtime API should be editable in the GUI.

### Why
- avoids misleading UI
- respects current daemon boundaries

### Known examples
*(Telemetry settings were the primary example but were removed in R52. This principle still applies to any future daemon-runtime API settings.)*

### Implications
- startup-only daemon config should be shown as unsupported, informational, or omitted

---

## DEC-023 — SSE supplements but does not replace polling

**Status:** Accepted
**Date:** 2026-03-23

### Decision
The SSE `EventStreamService` handles real-time sensor/fan updates, while the `PollingService` is retained for capabilities, headers, and lease status.

### Why
- SSE provides sub-second sensor/fan updates without polling overhead
- Capabilities, headers, and lease change infrequently and don't benefit from streaming
- Keeping polling as fallback ensures the GUI works with older daemon versions

### Implications
- Two data paths feed AppState (SSE + polling)
- If SSE connection fails, polling handles everything (existing behavior)

---

## DEC-024 — httpx-sse added as SSE dependency

**Status:** Accepted
**Date:** 2026-03-23

### Decision
Use `httpx-sse>=0.4` for SSE consumption instead of manual line parsing.

### Why
- Lightweight (~100 lines), maintained by httpx author
- Handles SSE protocol correctly (event/data/id/retry fields)
- Works with httpx Unix socket transport

### Alternatives considered
- Manual SSE line parsing (fragile, reinvents the wheel)
- WebSocket (unnecessary — data flow is unidirectional)

---

## DEC-025 — Daemon-side history pre-fills GUI on connect

**Status:** Accepted
**Date:** 2026-03-23

### Decision
The daemon maintains a 250-sample per-sensor ring buffer. On first GUI connection, the polling service fetches history for each sensor and pre-fills the `HistoryStore`.

### Why
- Timeline chart shows data immediately instead of starting empty
- Survives GUI restarts without losing recent history
- Memory bounded at ~118 KB regardless of daemon uptime

### Implications
- GUI converts daemon wall-clock timestamps to monotonic offsets for pruning compatibility
- First connect has N additional HTTP requests (one per sensor) — acceptable latency

---

## DEC-026 — Calibration sweep runs in daemon, not GUI

**Status:** Accepted
**Date:** 2026-03-23

### Decision
Fan calibration sweeps (PWM ramp + RPM measurement) run as a long-running daemon endpoint (`POST /fans/openfan/{ch}/calibrate`), not as GUI-orchestrated individual writes.

### Why
- Daemon has direct hardware timing control (more accurate settle times)
- Safety abort (thermal limit) can be enforced server-side
- Pre-calibration PWM restore is atomic (daemon handles cleanup)
- GUI only needs to make one HTTP call and wait for the result

### Implications
- Calibration is a long-running HTTP request (steps × hold_seconds)
- GUI should show a progress dialog during the sweep (future UI work)
- Only OpenFan calibration implemented initially; hwmon calibration (requires lease) is future work

---

## DEC-027 — Controls page uses FlowLayout + DraggableFlowContainer for card layout

**Status:** Accepted
**Date:** 2026-03-24

### Decision
Both Fan Roles and Curves sections use `DraggableFlowContainer` wrapping a custom `FlowLayout` (adapted from the official Qt FlowLayout example). Cards are fixed-size, flow left-to-right with wrapping, and support drag-to-reorder.

### Reason
- QGridLayout doesn't support drag-to-reorder or responsive wrapping
- QListWidget IconMode has known Qt bugs for insertion between items
- FlowLayout + event filter QDrag pattern is the recommended approach for small sets of interactive fixed-size widgets (per Qt Forum, PythonGUIs guidance)

### Impact
- `FlowLayout.addItem()` and `takeAt()` must call `invalidate()` — Qt does not auto-call it for custom layouts
- `clear_cards()` uses `blockSignals(True)` + `deleteLater()` for safe widget disposal

---

## DEC-028 — Fixed card sizing: Curve 220×160, Fan Role 260×180

**Status:** Accepted
**Date:** 2026-03-24

### Decision
Curve cards use `setFixedSize(220, 160)`. Fan Role cards use `setFixedSize(260, 180)`. Fan Role cards are larger because they display 5-6 rows of content (name, members, curve, output, actions) vs Curve cards' 4 rows.

### Impact
- FlowLayout respects `sizeHint()` which returns the fixed size
- Cards don't stretch or shrink on window resize; FlowLayout wraps to accommodate

---

## DEC-029 — GUI profile activation wired to daemon API

**Status:** Accepted
**Date:** 2026-03-24

### Decision
When the user activates a profile, the GUI calls `POST /profile/activate` on the daemon with the profile file path. The GUI only updates local "active" state after daemon confirmation. On connect/reconnect, the GUI queries `GET /profile/active` to reflect daemon truth.

### Reason
- Daemon is the runtime owner of active profile (headless operation)
- Daemon persists active profile to `/var/lib/onlyfans/daemon_state.json` — survives restart/reboot
- GUI must not falsely mark profiles active when daemon rejects them

### Impact
- `DaemonClient` plumbed through `MainWindow` to `ControlsPage`, `DashboardPage`, `SettingsPage`
- `PollingService` queries active profile on first successful connection

---

## DEC-030 — Syslog field names: request uses `host`/`port`, response uses `destination_host`/`destination_port`

**Status:** Superseded (telemetry removed in DEC-078 / R52)
**Date:** 2026-03-24

### Decision
The daemon's `TelemetryConfigRequest` struct uses `host`/`port`. The `TelemetryConfigResponse` and `TelemetryStatusResponse` structs use `destination_host`/`destination_port`. The GUI must send `host`/`port` in POST requests, and read `destination_host`/`destination_port` from responses.

### Reason
A field-name mismatch (GUI sending `destination_host` when daemon expected `host`) caused syslog to silently fail — the daemon received `host=None` and rejected enabled configurations.

### Impact
- GUI `_apply_telemetry()` sends `host` and `port` (not `destination_host`/`destination_port`)
- Any future API client must respect this asymmetry

---

## DEC-031 — FlowLayout invalidation: addItem() and takeAt() must call invalidate()

**Status:** Accepted
**Date:** 2026-03-24

### Decision
`FlowLayout.addItem()` and `FlowLayout.takeAt()` both call `self.invalidate()` after modifying the item list. This matches Qt's built-in `QBoxLayout` behavior (confirmed from Qt source code).

### Reason
Without `invalidate()`, Qt's layout system does not recalculate positions after items change. Cards added during a rebuild all stack at position (0,0) because `setGeometry()` is never called. This was the root cause of the card stacking bug.

### Impact
- Layout automatically recalculates after any add/remove operation
- No manual `invalidate()` calls needed by callers

---

## DEC-032 — Each CurveConfig owns sensor_id and points directly

**Status:** Accepted
**Date:** 2026-03-25

### Decision
Each `CurveConfig` in a profile owns its own `sensor_id: str` and `points: list[CurvePoint]`. These are stored, serialized, and loaded per-curve. No shared or global sensor/graph state exists.

### Reason
Multiple curves may reference the same sensor (e.g., two fan groups both keyed to CPU Tctl). Per-curve ownership ensures editing one curve never silently mutates another.

---

## DEC-033 — Mini-preview is derived from curve-owned data

**Status:** Accepted
**Date:** 2026-03-25

### Decision
The mini-preview rendered on each curve card is derived from that curve's own `points`/`type`/`sensor_id` data at render time. No separate preview cache exists. When a curve is edited, `card.update_curve(curve)` re-renders the preview from the modified data.

### Reason
Prevents stale previews and eliminates a class of bugs where previews become decoupled from the actual curve state.

---

## DEC-034 — Controls page text size follows theme via CSS inheritance

**Status:** Accepted
**Date:** 2026-03-25

### Decision
The Controls page has no hardcoded `font-size: Xpx` overrides. All text sizes are inherited from the global theme stylesheet via CSS classes (`.PageTitle`, `.PageSubtitle`, `.Card`). The only inline style on Controls is `font-weight: bold` on the editor title.

### Reason
Ensures text size changes in the theme propagate to the Controls page consistently without per-widget patching.

---

## DEC-035 — Sensor combo initialized from curve.sensor_id in set_curve()

**Status:** Accepted
**Date:** 2026-03-25

### Decision
`CurveEditor.set_curve()` must initialize the sensor combo from `curve.sensor_id` using `blockSignals(True)` to prevent writeback. Additionally, `_last_sensor_ids` is cleared to force `set_available_sensors()` to repopulate on the next poll tick.

### Reason
Without this, switching curves left the sensor combo showing the previous curve's sensor. `get_curve()` then overwrote the new curve's `sensor_id` with the stale combo value — cross-curve state leakage.

---

## DEC-036 — Card metadata uses CardMeta CSS class at small role

**Status:** Accepted
**Date:** 2026-03-25

### Decision
Card metadata labels use `.CardMeta` class (`font-size: {fs["small"]}pt` = 9pt at default). Previously used `.PageSubtitle` (13pt) which was too large after R30 removed inline font-size overrides. `.PageSubtitle` is reserved for section headers, not card internals. Fan Role buttons receive `.Card QPushButton { padding: 4px 8px; }` for comfortable text fit.

---

## DEC-037 — Diagnostics age_ms is daemon data cache staleness, not end-to-end latency

**Status:** Accepted
**Date:** 2026-03-28

### Decision
The `age_ms` values displayed in Diagnostics Overview are daemon-side cache staleness: time since the daemon's polling loop last read each hardware subsystem. They are **not** end-to-end GUI latency. OpenFan (serial I/O, 100-500ms) will always show higher age than hwmon (sysfs, ~1ms). The GUI poll cycle adds an additional 0-1000ms that is not reflected in the displayed value.

### Why
Large differences between subsystem age values were investigated and found to be expected behavior caused by serial vs sysfs I/O speed differences. Changing polling frequency would not make them equal and could increase controller churn. Honest labeling and documentation is the correct fix.

### Implications
- Overview shows subsystem reason text alongside age to explain status
- An explanatory note clarifies that "age = time since daemon last polled this hardware subsystem"
- No polling interval or timer changes were made

---

## DEC-038 — Diagnostics labels use transparent backgrounds and theme CSS classes

**Status:** Accepted
**Date:** 2026-03-28

### Decision
All labels inside Card frames on the Diagnostics page use `background: transparent` inline style and theme CSS classes (`.PageSubtitle` for card titles, `.CardMeta` for explanatory text). No hardcoded `font-size` pixel values remain. This matches the pattern established in R28-R33 for Dashboard and Controls pages.

---

## DEC-039 — Event log detail retrieval via category buttons

**Status:** Accepted
**Date:** 2026-03-28

### Decision
The Diagnostics Event Log tab provides three category buttons (Daemon Status, Controller Status, System Journal) that fetch and append labeled detail blocks to the log view on demand. Each block is prefixed with a source label and timestamp. Journal access uses `subprocess.run()` with `journalctl -u onlyfans.service --lines=100 --no-pager --output=short-iso` bounded by a 5s timeout.

### Why
On-demand retrieval with clear source labeling keeps the event log manageable and truthful. Bounded journal access prevents UI freezes. The subprocess approach requires no additional dependencies.

---

## DEC-040 — Lease tab includes user-facing explanation

**Status:** Accepted
**Date:** 2026-03-28

### Decision
The Lease tab includes a static explanation card above the live status card. The explanation covers: what a lease is (exclusive hwmon write access), why it exists (prevent conflicting PWM commands), and practical considerations (60s TTL, auto-acquire/renew, OpenFan writes don't require a lease).

---

## DEC-041 — AMD GPU support via direct sysfs + PMFW fan_curve (not LACT)

**Status:** Accepted
**Date:** 2026-03-29

### Decision
AMD dGPU support uses direct sysfs access for sensor reading (already working via hwmon discovery) and the PMFW `fan_curve` sysfs interface for fan control on RDNA3+ GPUs. LACT integration was evaluated and rejected as an approach because it would create a hard external daemon dependency and potential exclusive-control conflicts.

### Why
- The daemon already has complete hwmon infrastructure (discovery, reading, writing)
- PMFW fan_curve interface is well-documented and stable in kernel sysfs
- LACT would require the user to install and enable a separate daemon service
- LACT takes exclusive GPU fan control, which would conflict with our daemon
- Direct sysfs has zero additional dependencies

### Alternatives considered
- **LACT daemon integration** (JSON over Unix socket): Rejected — hard dependency, daemon-to-daemon coupling, exclusive control conflicts
- **AMD SMI library**: Rejected — heavy dependency, poor Arch packaging, unstable device indices
- **Hybrid (read sysfs, write LACT)**: Rejected — partial dependency is worse than full

### Implications
- Sensors from amdgpu hwmon devices now report `source: "amd_gpu"` instead of `"hwmon"`
- New `AmdGpuCapability` in the `/capabilities` response reports GPU model, PCI ID, fan control method
- PMFW fan_curve support (RDNA3+) requires `amdgpu.ppfeaturemask` kernel parameter
- GPU model name resolved from PCI device ID lookup table
- Daemon shutdown resets GPU fan curves to automatic (safety)

---

## DEC-042 — GPU identity uses PCI Bus:Device.Function address

**Status:** Accepted
**Date:** 2026-03-29

### Decision
GPU identity is based on the PCI BDF address (e.g. `0000:2d:00.0`), not hwmon index or runtime enumeration order. The sensor ID format `hwmon:amdgpu:<PCI_BDF>:<label>` is stable across reboots because PCI topology is fixed.

### Why
ROCm/AMD SMI device indices can change across reboots, making them unsuitable for persistent identity. PCI BDF addresses are determined by physical slot and are stable.

---

## DEC-043 — Do not adopt amdgpu-sysfs crate

**Status:** Accepted
**Date:** 2026-03-29

### Decision
The `amdgpu-sysfs` Rust crate (v0.19.3, LGPL-3.0) was evaluated and not adopted. Our existing `gpu_detect.rs` (200 LOC) and `gpu_fan.rs` (150 LOC) cover the exact fan detection and PMFW control needs with zero new dependencies.

### Why
- LGPL-3.0 license creates re-linking obligation for static Rust binaries
- 2800 lines of code we mostly don't need (overdrive, power profiles, VRAM)
- Does not provide `gpu_metrics` (the main thing beyond our current scope)
- Still requires our own GPU scanning code (crate takes a path as input)
- Can always reconsider if we need overdrive/power features later

---

## DEC-044 — One fan entity per AMD GPU (not per physical fan)

**Status:** Accepted
**Date:** 2026-03-29

### Decision
Each AMD GPU is represented as exactly one fan entity (`amd_gpu:<PCI_BDF>`). Triple-fan GPUs still show one aggregate RPM reading.

### Why
Confirmed at kernel source level: the amdgpu driver exposes only `fan1_input` — never `fan2_input` or `fan3_input`. PMFW drives all physical fans from a single PWM signal and reports one aggregate RPM. This is hardware/firmware architecture, not a software limitation. Verified across 15+ GPU models from HD 7870 to RX 9070 XT via LACT test data.

---

## DEC-045 — Imperative PWM write model for GPU fan control

**Status:** Accepted
**Date:** 2026-03-29

### Decision
GPU fan control uses the same imperative model as OpenFan/hwmon: the GUI control loop evaluates curves, sends a speed percentage to the daemon, which writes a flat PMFW curve (all points = same %). No lease required for GPU writes.

### Alternatives considered
- **Curve upload**: GUI sends full 5-point curve, daemon writes to PMFW once. Rejected — different control model from all other fans, adds complexity.
- **Hybrid**: Both imperative and curve upload. Rejected — more API surface, more code paths.

### Why imperative wins
- Consistent with how all other fans work (GUI evaluates, sends %)
- PMFW curve commits take <1ms, so 1/second from the control loop is fine
- LACT uses this exact approach for "manual/static" mode
- No new control paradigm to introduce

---

## DEC-046 — GPU detection uses PCI device ID + revision for model identification

**Status:** Accepted
**Date:** 2026-03-29

### Decision
RX 9070 XT and RX 9070 share the same PCI device ID (`0x7550`, Navi 48). They are distinguished by PCI revision: `0xC0` = XT, `0xC3` = non-XT. The daemon reads both `device` and `revision` sysfs files to determine the marketing name. Unknown revisions fall back to "RX 9070 Series".

### Why
The initial lookup table used bogus IDs (`0x69C0`/`0x69C1`). Live system verification with `lspci` and the pci.ids database confirmed `0x7550` as the correct Navi 48 device ID.

---

## DEC-047 — GPU fans always displayable (zero-RPM idle is normal)

**Status:** Accepted
**Date:** 2026-03-29

### Decision
GPU fans (`source == "amd_gpu"`) bypass the displayability filter that hides hwmon fans with RPM=0. Zero-RPM is normal GPU idle behavior (fan-stop mode), not a disconnected header. The dashboard and sensor panel always show GPU fans when the daemon reports them.

### Why
The displayability filter was designed for motherboard fan headers where RPM=0 means "nothing connected." GPU zero-RPM idle is a deliberate power-saving feature that should not hide the fan from the UI.

---

## DEC-048 — Fan control method "read_only" for RDNA4 without overdrive

**Status:** Accepted
**Date:** 2026-03-29

### Decision
When an RDNA4 GPU has `fan1_input` and `pwm1` but NO `pwm1_enable` and NO PMFW `fan_curve`, the fan control method is `"read_only"` (not `"hwmon_pwm"`). This truthfully reflects that the daemon can read fan state but cannot control it. The previous `"hwmon_pwm"` label was misleading — it implied write capability that doesn't exist without `pwm1_enable`.

### Why
RDNA3+ GPUs do not support `pwm1_enable=1` (manual mode). Fan write control requires PMFW `fan_curve`, which requires the `amdgpu.ppfeaturemask` kernel parameter with bit 14 set. The diagnostics now explain this clearly.

---

## DEC-049 — Daemon socket permissions set to 0666 after bind

**Status:** Accepted
**Date:** 2026-03-29

### Decision
The daemon's Unix socket at `/run/onlyfans/onlyfans.sock` is chmod'd to 0666 immediately after `UnixListener::bind()`. This allows non-root GUI processes to connect.

### Why
Unix domain sockets require write permission to connect. The daemon runs as root and creates the socket with root-only write access by default. The GUI runs as a regular user and cannot connect without relaxed permissions. LACT uses the same 0666 pattern.

---

## DEC-050 — GPU fan display name uses "{model} Fan" from capabilities

**Status:** Accepted
**Date:** 2026-03-29

### Decision
`fan_display_name()` checks if the fan ID starts with `amd_gpu:` and returns `"{display_label} Fan"` (e.g. "9070XT Fan") from the GPU capabilities. Falls back to "D-GPU Fan" if capabilities aren't loaded. User aliases still take priority.

---

## DEC-051 — Write failure counter decrements on success instead of deleting

**Status:** Accepted
**Date:** 2026-03-29

### Decision
When a fan write succeeds, the failure counter decrements by 1 instead of being deleted. The warning threshold is `>= 3` (not `== 3`), and warnings persist until the counter drops below 3. This prevents a single lucky success from hiding a pattern of intermittent failures.

---

## DEC-052 — V1 profile migration deduplicates fan members

**Status:** Accepted
**Date:** 2026-03-29

### Decision
During V1→V2 profile migration, a `seen_members` set tracks fan IDs. If the same fan appears in multiple assignments, only the first gets the member; subsequent duplicates are skipped with a log warning. This prevents conflicting PWM writes from multiple controls targeting the same physical fan.

---

## DEC-053 — Daemon auto-disables GPU zero-RPM for curve control

**Status:** Accepted
**Date:** 2026-03-29

### Decision
When the daemon writes a static GPU fan speed via PMFW `fan_curve`, it automatically disables `fan_zero_rpm_enable` (writes "0" + commits) first. When resetting to auto (`reset_to_auto`), it re-enables zero-RPM (writes "1" + commits). This matches LACT's approach and is required because RDNA3+ firmware ignores curve-driven speeds below the zero-RPM stop temperature threshold.

### Why
Live system testing showed fans at 0 RPM despite a flat curve being written. Root cause: `fan_zero_rpm_enable=1` causes the GPU firmware to keep fans stopped below ~50-60C regardless of the curve. Disabling zero-RPM is the only way to get immediate fan response at idle temperatures.

### User notification
A one-time informational popup is shown in the GUI when a GPU fan is first added to a fan role. The popup explains the zero-RPM behaviour and can be permanently dismissed via a "Don't show this again" checkbox (persisted in app settings as `show_gpu_zero_rpm_warning`).

---

## DEC-054 — Graph series colours user-selectable with persistence

**Status:** Accepted
**Date:** 2026-03-29

### Decision
Each graph series has a default colour from the theme palette (hash-based, deterministic). Users can override colours via colour picker in the sensor panel or fan table. Overrides are persisted in `AppSettings.series_colors: dict[str, str]` (hex strings keyed by series key). `color_for_key()` checks user override before hash default.

---

## DEC-055 — Y-axis range limited to yMin=0

**Status:** Accepted
**Date:** 2026-03-29

### Decision
Both the temperature and RPM ViewBoxes have `setLimits(yMin=0)`. Temperature and fan RPM can never be negative in this application. This prevents mouse wheel zoom from showing nonsensical negative values.

---

## DEC-056 — Crosshair hover shows values for all visible series

**Status:** Accepted
**Date:** 2026-03-29

### Decision
A vertical InfiniteLine crosshair tracks the mouse position. A TextItem label shows the value of each visible series at the cursor's x-position, using `np.searchsorted()` for nearest-point lookup. Rate-limited to 30 Hz via `SignalProxy` to avoid performance impact. This follows the canonical pyqtgraph crosshair pattern.

---

## DEC-057 — GPU fan de-duplication by PCI BDF identity

**Status:** Accepted
**Date:** 2026-03-30

### Decision
When an `amd_gpu` fan entity exists, hwmon fan entries from the same amdgpu device (matched by PCI BDF in the fan ID) are suppressed in the Dashboard fan table and sensor panel. The identity link is: `amd_gpu:{PCI_BDF}` ↔ hwmon ID containing `amdgpu:{PCI_BDF}`. Non-GPU hwmon fans are unaffected. If no `amd_gpu` fan exists, the hwmon entry is shown.

---

## DEC-058 — iGPU sensors filtered when dGPU is primary

**Status:** Accepted
**Date:** 2026-03-30

### Decision
When a discrete AMD GPU is the primary GPU (reported by capabilities), sensor entries from other amdgpu devices (e.g. iGPU) are filtered from the sensor panel. Both use `source: "amd_gpu"` since both run the amdgpu driver, but only the primary GPU's PCI BDF is kept. iGPU temperatures are rarely useful when monitoring/controlling a discrete GPU.

---

## DEC-059 — Hover only shows selected series, suppresses 0 RPM

**Status:** Accepted
**Date:** 2026-03-30

### Decision
Graph hover iterates only `_selection.visible_keys()` (not all registered items). RPM series showing 0 at the cursor position are suppressed — zero-RPM idle is noise, not signal. Temperature zeros are still shown (meaningful sensor readings).

---

## DEC-060 — Summary card height driven by font metrics, not hardcoded pixels

**Status:** Accepted
**Date:** 2026-03-30

### Decision
Summary cards use `QSizePolicy(Preferred, Maximum)` instead of `setMaximumHeight(100)`. Height is driven by label font metrics and adapts to any theme text size. Margins tightened to `(10, 6, 10, 6)` with spacing `2`.

### Why
The hardcoded 100px maximum didn't scale with font size and created unnecessary blank space at default sizes while risking clipping at large sizes.

---

## DEC-061 — Graph hover clears on widget leave and app deactivation

**Status:** Accepted
**Date:** 2026-03-30

### Decision
An event filter on the PlotWidget hides the crosshair and hover label on `QEvent.Leave`. A connection to `QApplication.applicationStateChanged` hides them when the app loses focus. This ensures hover information only shows while the user is actively interacting with the graph.

---

## DEC-062 — Daemon auto-lease for headless hwmon writes

**Status:** Accepted
**Date:** 2026-03-30

### Decision
The profile engine auto-acquires the hwmon lease with `owner_hint: "profile-engine"` when evaluating profiles that include hwmon fan members. The GUI always preempts via `force_take_lease()` — when the GUI calls `/hwmon/lease/take`, any internal holder is evicted. When the GUI releases or the lease expires, the profile engine re-acquires on the next cycle. Thermal safety uses `force_take_lease("thermal-safety")` to override all holders during emergency.

### Why
The lease system was designed for GUI-driven control. Headless profile mode could not write to hwmon fans because the profile engine had no lease. Auto-lease enables full headless operation for motherboard fans without breaking the GUI-priority model.

---

## DEC-063 — Serial auto-reconnect with exponential backoff

**Status:** Accepted
**Date:** 2026-03-30

### Decision
After 5 consecutive serial poll errors, the OpenFan polling loop enters reconnect mode. It calls `auto_detect_port()` + `RealSerialTransport::open()` with exponential backoff (1s, 2s, 4s...30s cap). On success, the transport is replaced in-place via the existing `Arc<Mutex<>>`. No separate background task — the poll loop is the natural owner.

### Why
The daemon previously became a dead loop after serial disconnection with no way to recover except a full restart. Auto-reconnect enables hot-unplug/hot-replug resilience.

---

## DEC-064 — Graph series derived from selection model, not raw history

**Status:** Accepted
**Date:** 2026-03-30

### Decision
The chart's `_chartable_keys()` returns keys from the selection model's `known_keys()`, not from `history.series_keys()`. The selection model is seeded by DashboardPage with only keys for entities visible in the Sensor Panel and Fan Table (after iGPU filtering, fan deduplication, and displayability checks). The history store remains unfiltered — it records all daemon data for completeness — but the graph only draws what the user can see and control.

### Why
The history store recorded ALL sensors (including iGPU) and the selection model was seeded from it. Since new keys default to visible, the graph drew iGPU data even though the panel filtered it out. This was a systemic truthfulness bug: any entity filtered by the panel but present in history appeared on the graph.

---

## DEC-065 — FanController Arc<Mutex<>> ownership model confirmed as correct

**Status:** Accepted
**Date:** 2026-03-30

### Decision
The current `Option<Arc<parking_lot::Mutex<FanController>>>` design is confirmed as the correct ownership model. No refactor or rewrite is needed. Both API handlers and the profile engine share the same Arc-cloned reference and call the same public `set_pwm()` methods. Locks are held for ~1-2ms (serial I/O), never across `.await` points, and contention is minimal.

### Alternatives rejected
- **Channel/actor pattern**: Adds latency and complexity for an inherently sequential serial device
- **Per-channel locks**: Meaningless when the underlying transport is a single serial stream
- **Single-writer-only**: Already effectively what we have — the mutex serialises writes

### Why this was previously listed as future work
The concern was that the profile engine couldn't write to fans. R43 resolved this by implementing auto-lease for hwmon and wiring the profile engine to use the existing shared controller. The "refactor" turned out to be unnecessary — the architecture was already correct.

---

## DEC-066 — Support bundle includes full app config for diagnosis

**Status:** Accepted
**Date:** 2026-03-30

### Decision
The support bundle export includes: full AppSettings (all 16 fields), profile inventory (names + counts), active theme name, custom theme list, series_color count, fan_alias count, and GPU capabilities. This enables diagnosis of configuration issues without requiring the user to describe their setup.

---

## DEC-067 — Window geometry and last page persisted on close

**Status:** Accepted
**Date:** 2026-03-30

### Decision
`MainWindow.closeEvent()` saves the current page index and window geometry (x, y, width, height) to AppSettings. On next launch, if `restore_last_page` is true, the last page is restored. Window geometry is always restored. This eliminates the "always starts at dashboard with default size" complaint.

---

## DEC-068 — Chart antialiasing disabled for real-time performance

**Status:** Accepted
**Date:** 2026-03-30

### Decision
`pg.setConfigOptions(antialias=False)` on the timeline chart. Antialiasing adds 20-40% CPU render cost per frame. For a real-time 1Hz monitoring dashboard, the visual quality tradeoff is acceptable. The curve editor retains antialiasing (static, interactive — smoothness matters for UX).

---

## DEC-069 — Chart timer visibility-gated and focus-throttled

**Status:** Accepted
**Date:** 2026-03-30

### Decision
The dashboard chart timer stops when the dashboard page is hidden (switched to another page) and restarts when shown. When the app loses focus (e.g. user switches to a game), the timer throttles from 1Hz to 0.2Hz (5s interval). This reduces compositor work by 80% while gaming. When the app regains focus, the timer restores to 1Hz.

---

## DEC-070 — GPU fan PMFW write threshold 5% (not 1%)

**Status:** Accepted
**Date:** 2026-03-31

### Decision
GPU fan speed writes via PMFW fan_curve use a 5% minimum change threshold before issuing sysfs writes. The standard 1% threshold used for OpenFan/hwmon is too sensitive for PMFW because: (a) PMFW flat curves don't benefit from 1% granularity — the firmware manages actual fan speed, and (b) each PMFW commit triggers GPU SMU firmware processing that briefly stalls the display pipeline, causing gaming stutter.

---

## DEC-071 — Profile engine defers GPU writes when GUI is active

**Status:** Accepted
**Date:** 2026-03-31

### Decision
The profile engine skips GPU fan writes when the GUI was active in the last 30 seconds (`cache.last_gui_write_at`). This prevents the dual-writer conflict where both the GUI control loop and the profile engine independently evaluate the same curve and write to PMFW, doubling the sysfs write rate. In headless mode (no GUI), the profile engine writes normally.

---

## DEC-072 — Configurable daemon state directory with systemd StateDirectory

**Status:** Accepted
**Date:** 2026-03-31

### Decision
The daemon state directory (`daemon_state.json`) is configurable via `[state] state_dir` in `daemon.toml` (default: `/var/lib/onlyfans`). The systemd service file uses `StateDirectory=onlyfans` to have systemd create and manage the directory. The path is also added to `ReadWritePaths` for belt-and-suspenders protection. The daemon uses `OnceLock<String>` to accept the configured path at startup.

### Why
Under `ProtectSystem=strict`, the daemon's atomic write (tmp + rename) to `/var/lib/onlyfans/daemon_state.json` failed with `EROFS (Read-only file system, os error 30)`. The root cause was a missing `StateDirectory=onlyfans` directive in the systemd service file, and `/var/lib/onlyfans` was not in `ReadWritePaths`. This made profile persistence non-functional: the active profile was lost on every daemon restart.

### Alternatives considered
- **Option A (service file fix only):** Faster but would not support non-standard deployments.
- **Option B (service file + configurable path):** Chosen. Supports custom state directories for testing, containers, or non-default layouts.

### Implications
- Daemon config gains `[state]` section (existing configs unaffected — defaults apply)
- `daemon_state.rs` uses `OnceLock` — `init_state_dir()` must be called before any load/save
- Profile persistence now works correctly under systemd sandbox

---

## DEC-073 — hwmon write coalescing (per-header state tracking)

**Status:** Accepted
**Date:** 2026-03-31

### Decision
`HwmonPwmController` now tracks `last_commanded_pct` and `manual_mode_set` per header. Identical PWM writes are skipped entirely (0 sysfs ops). `pwm_enable=1` is written once per lease, not on every call. Coalescing state resets on lease release via `on_lease_released()`.

### Why
The write-path sanity check (post-GPU-stuttering audit) found that hwmon wrote 4 sysfs operations per header per second in steady state (pwm_enable + pwm value + verification readback + RPM read) even when the value hadn't changed. OpenFan already had coalescing since initial implementation; hwmon did not. While ITE/NCT register writes are cheaper than PMFW, they are still unnecessary I/O.

### Alternatives considered
- **Option A (PWM coalescing only):** Simpler but still writes pwm_enable every call.
- **Option B (Full coalescing, chosen):** Tracks both PWM value and manual mode. Eliminates all redundant sysfs ops in steady state.
- **Option C (No change):** Acceptable risk but leaves asymmetry between hwmon and OpenFan.

---

## DEC-074 — Profile engine defers OpenFan writes when GUI active

**Status:** Accepted
**Date:** 2026-03-31

### Decision
The profile engine's OpenFan write phase now checks `last_gui_write_at` and skips writes when the GUI was active in the last 30 seconds. This matches the existing GPU write deferral (DEC-071) and the hwmon lease-based priority.

### Why
The write-path audit found that GPU and hwmon writes had GUI-priority mechanisms, but OpenFan did not. When both GUI and profile engine are active, both evaluate the same curves and call `FanController.set_pwm()`. Coalescing catches exact duplicates, but timing differences could cause alternating writes. Deferring to the GUI provides consistent behavior across all three backends.

---

## DEC-075 — V4 audit dead code removal

**Status:** Accepted
**Date:** 2026-04-01

### Decision
Removed verified dead code: `set_openfan_target_rpm()` client method (never called), 5 ControlCard signals declared but never emitted (mode_changed, manual_output_changed, curve_selected, edit_members_requested, renamed), `last_session_path()` function (no session restore feature), and 2 unused conftest fixtures. `SetRpmResult`/`parse_set_rpm` kept in models.py (independently tested API contract). Handler methods `_on_control_curve_selected` and `_on_edit_members` kept (used from FanRoleDialog callback).

### Why
V4 comprehensive code audit identified these items as unreferenced across the entire codebase. Each was verified with exhaustive grep searches. Removal reduces maintenance surface and eliminates false coupling (5 signals were connected but never emitted, so their handlers never fired).

---

## DEC-076 — Support bundle must include journal logs and report missing sections

**Status:** Accepted
**Date:** 2026-04-01

### Decision
The support bundle now includes system journal output (via `fetch_journal_entries()`) and a `missing_sections` list that explicitly records which sections were omitted and why. When daemon is unavailable, the bundle succeeds with available data but clearly indicates what is missing.

### Why
A support bundle without daemon logs is insufficient for troubleshooting daemon issues. Silent omission of sections when daemon is offline made it impossible for support engineers to distinguish "no GPU" from "daemon was offline."

---

## DEC-077 — Import/export includes all custom themes and validates version

**Status:** Accepted
**Date:** 2026-04-01

### Decision
Settings export captures all custom themes from the `themes/` directory (not just the active theme). Import validates `export_version` and rejects unsupported versions. Theme import restores all exported theme files alongside settings and profiles.

### Why
Exporting only the active theme risked data loss if user had multiple custom themes. No version validation meant future schema changes could silently corrupt settings on import.

---

## DEC-078 — Syslog/telemetry intentionally de-scoped

**Status:** Accepted
**Date:** 2026-04-01

### Decision
Syslog/telemetry export (RFC 5424 over TCP) has been removed from both the daemon and GUI. The entire telemetry module, API endpoints, config section, UI tabs, and related tests have been deleted.

### Why
The feature was fully implemented (M7) but is no longer considered worth including for the product's target use case. Removal simplifies the codebase, reduces maintenance surface, and eliminates ~2,700 lines of code across both repos.

### Implications
- Existing daemon.toml files with [telemetry] section will fail to parse (forces cleanup)
- Settings import files with telemetry keys are silently ignored (no migration needed)
- Support bundles no longer include telemetry status sections
- The daemon no longer advertises telemetry_supported in capabilities

---

## DEC-079 — QColorDialog requires stylesheet isolation from app theme

**Status:** Superseded by DEC-080
**Date:** 2026-04-02

### Decision
All QColorDialog instances must isolate from the app's global `QWidget {}` stylesheet by applying a dialog-level stylesheet that targets only `QColorDialog`, `QPushButton`, `QLabel`, `QSpinBox`, and `QLineEdit` — never bare `QWidget`. Colors are read from the app palette at runtime for theme consistency.

### Why
The app's global `QWidget { background-color: ...; }` rule cascades into QColorDialog's internal custom-painted widgets (color spectrum, hue strip, preview), corrupting their rendering. This is a known Qt limitation: broad `QWidget` selectors break standard dialogs. The fix isolates the dialog tree from the cascade while re-applying dark theme only to the dialog frame widgets.

### Implications
Any future QColorDialog usage must follow this pattern. If the global stylesheet is ever refactored to use narrower selectors (e.g., `QMainWindow > QWidget`), this isolation may become unnecessary.

---

## DEC-080 — QColorDialog requires app stylesheet cleared during exec()

**Status:** Accepted
**Date:** 2026-04-02

### Decision
All QColorDialog usage must temporarily clear the app stylesheet before `exec()` and restore it after. The dialog parent must be `self.window()` (not the triggering widget). No dialog-level stylesheet isolation, layout constraint overrides, or minimum size workarounds are needed — the dialog renders correctly at its natural size when the app stylesheet is cleared.

### Why
Qt's global `QWidget {}` stylesheet rule cannot be overridden by dialog-level `setStyleSheet()` — CSS specificity gives the app-level selector equal priority on unnamed internal child widgets. Previous attempts (R54-R57) to fix the color dialog via flags, layout constraints, and dialog-level stylesheets all failed because the cascade operates at the app level. The only proven approach is to temporarily remove the source of the cascade.

### Supersedes
DEC-079 (dialog-level stylesheet isolation — proven insufficient).

---

## DEC-081 — Fan Wizard restores to prior PWM (30% fallback)

**Status:** Accepted
**Date:** 2026-04-03

### Decision
The Fan Wizard restores each fan to its `last_commanded_pwm` value captured at wizard entry. If prior state is unknown (None), the fallback is 30%. The previous behavior of forcing all fans to 100% is removed.

### Why
100% is unnecessarily aggressive and noisy. Users expect fans to return to their pre-wizard state. 30% as fallback is a safe minimum that keeps airflow without being disruptive. GPU fans were previously not restored at all in `_restore_all_fans()` — this is also fixed.

---

## DEC-082 — Fan Wizard uses single TestPage with internal cycling, not dynamic pages

**Status:** Accepted
**Date:** 2026-04-03

### Decision
The Fan Wizard uses a single `TestPage` instance that cycles through fans internally via `advance_to_next_fan()`. Dynamic page creation/removal (`removePage()` + `setPage()`) must NOT be used inside `nextId()` because Qt re-evaluates `nextId()` on any page set change, causing infinite recursion.

### Why
R60 introduced dynamic test pages with unique IDs to work around QWizard's "page already met" limitation. However, calling `removePage()` from within `nextId()` triggers Qt to call `nextId()` again — infinite recursion → `RecursionError` → app crash (R61). The single-page approach avoids all QWizard page-history issues.

### Supersedes
R60's dynamic page approach.

---

## DEC-083 — Daemon profile search dirs configurable via daemon.toml

**Status:** Accepted
**Date:** 2026-04-07

### Decision
The daemon's profile search directories are configurable via a `[profiles] search_dirs` array in `daemon.toml`, replacing the hardcoded `HOME`-based logic. Both the incoming profile path and the search directories are canonicalized before comparison (CWE-22 fix).

### Why
The daemon runs as root (systemd, no `User=`), so its `HOME` is `/root` — not the GUI user's home. The GUI stores profiles at `~/.config/onlyfans/profiles/` which the daemon rejected with "profile_path must be within a profile search directory." Making search_dirs configurable lets the install script set the correct user path.

### Alternatives considered
- GUI sends profile content inline (breaks headless restart, major API change)
- Remove path validation entirely (security regression of P1-R4 hardening)

### Implications
Daemon.toml.example updated with `[profiles]` section. Install scripts should configure the user's profile directory.

---

## DEC-084 — GUI writes daemon.toml for profile directory sync

**Status:** Superseded by DEC-087
**Date:** 2026-04-07

### Decision
~~When the user changes their profile directory in Settings → Application, the GUI writes the updated `search_dirs` directly to `/etc/onlyfans/daemon.toml`.~~

### Superseded by
DEC-087 (R64) — GUI now uses daemon API endpoint instead of direct file writes.

---

## DEC-085 — Per-profile ownership of curves and controls confirmed

**Status:** Accepted
**Date:** 2026-04-07

### Decision
Each Profile owns its own `controls[]` and `curves[]`. Switching profiles loads only that profile's data. Creating a new profile presents a blank slate (no curves, no controls). This was already correct in the data model but was not visible due to a UI bug (profile selection snapping back to active).

### Why
The Controls page combo box `_refresh_profile_combo()` was clearing and rebuilding the combo on every selection, snapping the index back to the active profile. The user's selection was silently lost before the page could refresh with the new profile's content.

### Implications
Profile selection, creation, duplication, and deletion now correctly update the viewed profile in the combo and refresh the page content.

---

## DEC-086 — Configurable data directories in GUI Settings

**Status:** Accepted
**Date:** 2026-04-07

### Decision
All user-facing data paths (profiles, themes, default export directory) are configurable from Settings → Application via directory picker dialogs. Overrides are stored in `app_settings.json` and applied at startup via `set_path_overrides()`. When a directory changes, existing files are optionally moved with a confirmation dialog.

### Why
Users must be able to choose where their data lives rather than relying on hardcoded XDG defaults.

### Implications
`paths.py` now checks `_overrides` dict before XDG defaults. `AppSettings` has three new fields: `profiles_dir_override`, `themes_dir_override`, `export_default_dir`. Path overrides are applied in `main.py` before `ProfileService.load()`.

---

## DEC-087 — GUI uses daemon API for profile search dir updates (supersedes DEC-084)

**Status:** Accepted
**Date:** 2026-04-07

### Decision
When the user changes the profile directory in Settings → Application, the GUI calls `POST /config/profile-search-dirs` on the daemon API. The daemon validates the request, updates its in-memory search dirs immediately, and persists the change to `daemon.toml` atomically. No daemon restart required.

### Why
DEC-084 (GUI writes daemon.toml directly) crossed the architecture boundary (GUI is API client only, DEC-001), required manual file permission setup, and still needed a daemon restart. The API approach follows the NetworkManager/PipeWire pattern where the service owns its config files and the GUI communicates only via API. Research confirmed this is the standard Linux desktop service pattern.

### Alternatives considered
- Keep DEC-084 direct writes + group permissions — crosses architecture boundary, fragile
- polkit privileged helper — overengineered for this use case
- Drop-in directory pattern — more complex than needed for V1

### Implications
- `daemon_config_writer.py` removed from GUI
- Daemon has new `POST /config/profile-search-dirs` endpoint (additive: `{"add": [...]}`)
- Daemon supports SIGHUP config reload (`systemctl reload onlyfans-daemon`)
- `profile_search_dirs` in AppState now uses `RwLock` for runtime mutability
- Multi-user support: each user can add their dir via the API

---

## DEC-088 — Daemon supports SIGHUP config reload

**Status:** Accepted
**Date:** 2026-04-07

### Decision
The daemon handles SIGHUP by re-reading `daemon.toml` and updating `profile_search_dirs` in memory. The systemd service file includes `ExecReload=/bin/kill -HUP $MAINPID` so `systemctl reload onlyfans-daemon` works.

### Why
SIGHUP is the standard Unix convention for daemon config reload. Combined with the API endpoint (DEC-087), this provides both programmatic and operational reload paths (Prometheus pattern).

### Implications
`AppState.profile_search_dirs` changed from `Vec<PathBuf>` to `RwLock<Vec<PathBuf>>`. SIGHUP loops (does not exit); only SIGINT triggers shutdown.

---

## DEC-089 — Sysfs paths in daemon error responses are intentional

**Status:** Accepted
**Date:** 2026-04-08

### Decision
Daemon HTTP error responses include sysfs paths (e.g. `/sys/class/hwmon/hwmon0/pwm1`) in the `message` field. This is intentional and will not be stripped.

### Why
The paths are public kernel-exported sysfs nodes, not secrets. The daemon communicates over a local-only Unix socket (no TCP exposure). The diagnostic value of including paths in error messages significantly aids troubleshooting. Stripping them would require a sanitization layer that adds complexity for negligible security benefit in the current threat model.

---

## DEC-090 — CapabilityBoundingSet deferred for daemon service

**Status:** Accepted
**Date:** 2026-04-08

### Decision
The systemd service file does not set `CapabilityBoundingSet`. Root is required for sysfs writes to hwmon and GPU fan control nodes. With `NoNewPrivileges=true` and `SystemCallFilter` already active, capability bounding provides marginal additional protection. Revisit post-release with per-capability verification on real hardware.

### Why
Adding a restrictive capability set risks breaking sysfs fan writes if the wrong capability is dropped, and cannot be fully validated without deployed hardware running all three backends (hwmon, GPU PMFW, serial OpenFan). The existing hardening (`ProtectSystem=strict`, `NoNewPrivileges`, `MemoryDenyWriteExecute`, `SystemCallFilter`, `RestrictNamespaces`, etc.) already provides strong containment.

---

## Template for future decisions

```md
## DEC-<NUMBER> — <TITLE>

**Status:** Accepted | Superseded | Rejected | Proposed  
**Date:** YYYY-MM-DD

### Decision
<What was decided>

### Why
<Why it was decided>

### Alternatives considered
<Optional>

### Implications
<Impact on code, UX, docs, or workflow>

### Superseded by
<Optional DEC-ID>
```

