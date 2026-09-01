# 14 — Risks, Gaps, and Future Work

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record (including which audit/remediation waves landed when) and wins where this document disagrees with it.

## Current Feature Status Matrix

### Fan Write Backends

As of 2.0.0 the daemon profile engine is the **sole PWM writer** (DEC-159/DEC-165); the GUI no longer
writes PWM at all — its old imperative write path was deleted at the cutover. The "GUI Imperative"
column therefore reads N/A throughout. Live manual override and fan identify are daemon APIs
(DEC-163/DEC-166), not GUI writes.

| Backend | GUI Imperative | Daemon Profile (Headless) | Thermal Safety Emergency |
|---------|---------------|--------------------------|-------------------------|
| **OpenFan** | N/A (removed at 2.0.0) | Full | Full (trip point→100%) |
| **hwmon (motherboard)** | N/A (removed at 2.0.0) | **Full (daemon self-leases)** | **Full (force_take)** |
| **AMD GPU (PMFW)** | N/A (removed at 2.0.0) | Full | Relies on PMFW firmware |

### Device Lifecycle

| Feature | Status | Notes |
|---------|--------|-------|
| Startup device detection | IMPLEMENTED | hwmon + serial auto-detect at daemon start |
| Serial startup retry | IMPLEMENTED | 5x exponential backoff (1-16s) |
| **Serial runtime reconnect** | **IMPLEMENTED (R43)** | After 5 consecutive errors, enters reconnect mode with backoff |
| hwmon manual rescan | IMPLEMENTED | `POST /hwmon/rescan` endpoint |
| GUI rescan button | **IMPLEMENTED (DEC-147)** | The System State page's "Rescan Hardware" — restores the `DaemonClient.hwmon_rescan` wrapper, pushes fresh headers through `AppState`, chains a diagnostics refetch. New *motherboard* fan-control hardware still requires a daemon restart (daemon-side limit); an OpenFan controller does not, since DEC-265 gave the same button a `POST /fans/openfan/rescan` leg. |
| udev stable symlink | TEMPLATE ONLY | `packaging/99-control-ofc.rules` — requires user VID/PID |
| udev hotplug trigger | ABSENT | No automatic device-event service start |
| Runtime hwmon hotplug | ABSENT | Devices added after startup are invisible |

### Service / Autostart

| Feature | Status | Notes |
|---------|--------|-------|
| systemd service file | IMPLEMENTED | `packaging/control-ofc-daemon.service` with hardening |
| Auto-restart on crash | IMPLEMENTED | `Restart=on-failure`, 3s delay |
| Socket permissions | IMPLEMENTED | chmod 0666 after bind (R38) |
| Boot autostart | IMPLEMENTED | `multi-user.target` |

## Known Limitations

### 1. No runtime hwmon/GPU hotplug detection
Sensor descriptors are discovered at startup and cached (DEC-133); `POST /hwmon/rescan` now also refreshes the cached sensor set (labels, types, thresholds), and the loop self-refreshes on read-failure streaks or while no CpuTemp sensor is cached. PWM-control headers and GPU detection are still captured only at daemon startup — a device plugged in later needs a daemon restart for *control* (its sensors appear after a rescan).

### 2. No GPU-specific thermal safety rule
The thermal safety rule monitors CPU Tctl only (trigger >=105C, per-machine since DEC-308). GPU temperatures rely on PMFW firmware protection. If a daemon-level GPU thermal rule is needed, it would require reading GPU junction temp from the cache and adding a separate threshold.

### 3. GUI/daemon simultaneous control conflict (RESOLVED — 2.0.0 single-writer, DEC-159/DEC-165)
The dual-writer hazard is eliminated at 2.0.0: the daemon's engine is the **sole** writer of every
backend and the GUI no longer writes PWM, so there is nothing to conflict. The 30 s `gui_active` defer
window (and the brief reconnect gap it implied) was deleted along with the GUI control loop.
The thermal force remains the absolute backstop.

*History (pre-2.0):* hwmon used `force_take` lease preemption (GUI wins); GPU used a 30 s
profile-engine defer (DEC-070/DEC-071) so both writers didn't churn PMFW and stutter games.

### 4. FanController ownership model (RESOLVED — R46 assessment)
The daemon's `FanController` is `Option<Arc<parking_lot::Mutex<FanController>>>` in `AppState`. This was previously listed as requiring an Arc refactor for clean API/profile-engine separation. R46 investigation confirmed the current design is correct: locks are held for ~1-2ms (serial I/O), never across `.await` points, contention is minimal (1Hz profile engine + user-driven API), and both paths use the same public `set_pwm()` methods. Per-channel locking would add complexity without benefit since serial I/O is inherently sequential. No refactor needed.

### 5. AIO cooler support — hwmon shipped (Phase 1), USB-only out of scope
Phase 1 (DEC-156, GUI 1.39.0 / daemon 1.18.0) ships **hwmon** liquid-cooler support: coolant
classification (`CoolantTemp`), an `is_aio` header flag, a dynamic `aio_hwmon` capability, and
`AioPumpState` wired into the poll loop. Coolers ride the existing hwmon write/lease path — no new
control plumbing and **no coolant safety rule** (CPU-only `safety.rs` is unchanged). **USB-only
coolers** (liquidctl/USB-HID, e.g. much Corsair iCUE/Commander Core) remain **out of scope** — the
daemon never opens USB-HID, so they read as not-detected, never faked. Phase 2 (guided AIO UX — one-click "Configure AIO" on the Controls page,
dashboard liquid-cooler grouping) shipped in GUI 1.40.0 (DEC-157).

### 6. Multi-GPU UI selection
Data model supports multiple GPUs. API reports primary only. No UI to select between multiple discrete GPUs. Untested with 2+ dGPUs.

### 7. Some GUI spec features not implemented
- Background self-checks (deferred)
- One-click diagnostics redaction (deferred — partial PII scrubbing gives false confidence)
- ~~**SSE consumption (`GET /events`) — daemon exposes it, GUI does not consume it (formally deferred, DEC-164).**~~ RESOLVED (DEC-198, daemon v2.5.0): the unused `GET /events` endpoint was **removed** rather than consumed — no client ever used it (`httpx-sse` was dropped in v1.0.0). The GUI stays poll-only (1 Hz `GET /poll`, transitions by poll-diff), which is sufficient; sub-second UI updates, if ever wanted, would reintroduce a push channel from scratch.
- ~~**Dashboard fan table — group-membership badges and per-fan state chips** (stale/fault/manual).~~ RESOLVED (DEC-176/179, GUI v2.2.0): the dashboard's primary fan view is now zone-grouped **fan cards** with a per-fan state chip (Normal / Low RPM / Stall / Stale / Offline / Override) and per-zone roll-ups (online/expected, avg RPM/PWM); the raw label/source/RPM/PWM table is preserved in a collapsed "Raw fan data" expander.
  **Superseded by DEC-222 (GUI v2.25.0):** the zone grid and the raw table were both retired;
  the fan view is now one card per logical control, each with a state chip and a member count.
- ~~**Dashboard sensor panel — per-sensor freshness age and stale/invalid warning marker.**~~ RESOLVED (DEC-178 cards + DEC-182 inspector, GUI v2.2.0): summary cards show per-card freshness glyphs (`⏱` stale, `⚠` invalid) with age tooltips, and the collapsible right pane is the grouped **Sensors** browser (device grouping, freshness in tooltips, search). (DEC-184, GUI v2.3.0, later reduced it to Sensors-only — Events moved to Diagnostics, warnings to a dialog.)
  **Superseded in part by DEC-222 (GUI v2.25.0):** the summary cards were removed, the Sensors rail
  is always mounted, and warnings moved from the dialog to the Logs page.

### 8. polkit helper for offline config editing (deferred)

When the daemon is not running, the GUI cannot use the API to update profile search dirs. Users must manually edit `/etc/control-ofc/daemon.toml`. A polkit privileged helper could provide a GUI dialog for this, but is not needed while the daemon is running (the API endpoint handles it).

### 9. Drop-in directory pattern for profile config (deferred)

A composable drop-in directory (`/etc/control-ofc/profiles.d/*.conf`) could replace the single `search_dirs` array for more flexible multi-user config. The current API-based approach (DEC-087) is sufficient for V1.

### 10. Full Vendor Hardware Setup Wizard (deferred — DEC-092)

A dedicated wizard page that walks first-time users through the complete hardware setup workflow:

**Proposed workflow:**
1. **Board detection** — auto-read DMI vendor/model, display detected SIO chips and loaded modules
2. **Driver assessment** — compare detected chips against the knowledge base, flag missing or wrong drivers, recommend specific AUR packages (e.g. `it87-dkms-git` for Gigabyte, `nct6687d-dkms-git` for MSI)
3. **DKMS prerequisite check** — verify `dkms` is installed and kernel headers match the running kernel (common pitfall on Arch/CachyOS with multiple kernel flavours)
4. **Module conflict resolution** — detect and offer to blacklist conflicting drivers (e.g. in-kernel nct6683 vs out-of-tree nct6687d)
5. **BIOS configuration guidance** — vendor-specific step-by-step BIOS instructions with screenshots or descriptions (Gigabyte SmartFan 5/6 → "Full Speed", MSI → disable "Smart Fan Mode", etc.)
6. **Per-header verification** — walk through each writable header: write a test PWM, confirm RPM response, classify as effective/read-only/BIOS-overridden
7. **Summary report** — show what works, what doesn't, recommended next steps, and offer to export as a support bundle

**Why deferred:**
- The existing Hardware-page readiness report already covers steps 1-6 in a single scrollable view with auto-populated guidance
- Most users only need this once during initial setup
- The knowledge base (`CHIP_GUIDANCE_DB` / `VENDOR_QUIRKS_DB` in `hwmon_guidance.py`) provides the same information inline
- A wizard adds a new page, new service, and significant test surface for a one-time workflow
- DEC-144 additionally ships a beginner-oriented, copy-paste driver setup walkthrough as a manual page ([`manual/driver-setup.md`](../manual/driver-setup.md)) — prerequisites, install, verify, rollback, with the standard remediation disclaimer. This satisfies the documentation-level share of the wizard's goals without the new UI surface, consistent with DEC-092's original reasoning.
- DEC-145 adds the ordered end-to-end setup path as a manual page ([`manual/setup-checklist.md`](../manual/setup-checklist.md)) — install → verify sensors → readiness → branch (driver / BIOS / GPU / OpenFan) → stop competing fan software → verify control → first profile, plus a "when to redo what" table. This closes the wizard's step-ordering share at the documentation level; the remaining delta to DEC-092 is only the interactive UI surface.

**When to build:**
- If user feedback shows the Hardware page (readiness) is insufficient for first-time setup
- If we add automated driver installation (e.g. `yay -S` integration), a wizard becomes the natural home
- If we support more vendors/platforms where the setup matrix grows beyond what inline guidance can cover

**Dependencies:**
- Would require a `HardwareWizardService` managing a state machine across the detection → install → configure → verify steps
- DKMS/package manager integration would need careful security review (polkit, sandboxing)
- BIOS guidance with images would need an asset pipeline and per-board screenshot library

### 11a. AORUS-class boards: BIOS Smart Fan is the root fix for pwm_enable reclaim

On Gigabyte AORUS-family motherboards using the IT8696E / IT8689E Super-I/O,
the EC firmware's Smart Fan algorithm continuously rewrites `pwm_enable` from
manual mode (`1`) back to BIOS-controlled (`2`) at roughly 1 Hz once the OS
takes manual control. The daemon's pwm_enable watchdog (added in daemon
v1.3.0) detects this and re-writes manual mode + the PWM value on every
loop, so fan control remains correct in practice — but **this is the
operational mitigation, not the root fix**.

The root fix is BIOS-side: in the AORUS BIOS, set Smart Fan 6 (or Smart Fan
5 on older boards) to **Manual / Full Speed** for every header that the OS
is meant to control, or configure a degenerate fan curve where every
temperature point sits at the same value with all duty points at 0% except
the final point at 100%. Either configuration disables the EC's own curve
evaluation and stops the reclaim cycle entirely.

The GUI surfaces the live reclaim count per header on the System State
page (v1.7.1 onwards) with a severity colour ramp; the daemon throttles
the matching log line so the journal is not spammed (daemon v1.5.2). The
System State page also auto-shows the matching `VendorQuirk` card when the
board+chip combination is recognised, pointing the operator at this BIOS
setting.

### 12. Real-time daemon journal follow (deferred — DEC-111)

The "System Journal" snapshot button fetches the last ~100 lines of
`journalctl -u control-ofc-daemon` on demand. A streaming follow (the
equivalent of `journalctl -u control-ofc-daemon -f`) is not wired —
users who want live daemon logs run `journalctl` themselves. Adding
streaming follow inside the GUI would require a subprocess + thread
to read continuously and is out of scope for the DEC-111 in-process
event log rewrite.

**When to build:** if support tickets repeatedly need real-time
daemon-side context that the in-process event log + on-demand journal
snapshot don't cover.

### 13. Cross-restart event-log persistence (deferred — DEC-111)

DEC-111 keeps the event log session-only (in-memory deque, MAX_EVENTS
= 200). The system journal is the proper cross-restart store; the
support bundle JSON already captures a snapshot of the deque on
demand. Persisting per-session files to disk was considered and
rejected in DEC-111 as duplicate responsibility plus a privacy /
cleanup surface for no significant gain over the bundle.

**When to build:** unlikely. If we ever want a forensic trail
across restarts, the daemon journal is the right place.

### 11. GUI surface for `reset_gpu_fan` (RESOLVED — DEC-147)

The System State page's "Restore GPU Fan to Automatic" wires
`DaemonClient.reset_gpu_fan` to a user-facing action beside the GPU verify
button: shown for any writable AMD GPU (no daemon version floor — the reset
route predates every supported daemon), disabled with an explanatory tooltip
while the active profile owns an `amd_gpu:` member (the daemon engine
would silently re-assert its curve), and re-checked at click time.

**Post-2.0.0 note:** the GUI no longer writes GPU PWM at all — the daemon engine
is the sole writer (DEC-165) — so the original close-time auto-reset (M9) is gone,
along with the `gui_wrote_gpu_fan` session flag it depended on (deleted in v2.6.1).
The Diagnostics action itself remains useful for handing a GPU left in a
static/manual state back to PMFW automatic without restarting the daemon.

**Remaining (optional) surface:** a secondary "Restore to automatic" action in
the fan wizard / fan role editor for GPU fans. Deferred — that surface only
exists when a profile with a GPU member is open, while the stuck-GPU scenario
typically arises precisely when no control drives the GPU anymore.

### 14. Intel GPU writable fan control (blocked on the kernel — DEC-121)

Intel discrete GPUs (Arc) are supported for read-only monitoring only
(temperature + fan RPM). There is no writable fan-control interface for them
on Linux: neither the `xe` nor the `i915` driver exposes a `pwm` attribute or
a fan-write callback, and fan speed is managed autonomously by on-card
firmware. This is a kernel/firmware limitation, not a daemon one — the
daemon's `intel_gpu_detect` model is intentionally read-only.

**When to build:** if Intel ever exposes a writable fan interface upstream
in `xe`/`i915`, the read-only model would be extended to surface those Intel
GPU fans as controllable curve members (mirroring the AMD PMFW path). Until
then they are never offered as writable members.

The model-name table currently maps only the Arc **B580** (`0xE20B`); all
other Intel discrete GPUs display as the generic "Intel D-GPU". Extend the
table only with authoritatively-verified device-ID → name pairs.

### 15. NVIDIA GPU writable fan control (deferred — DEC-204)

Read-only NVIDIA sensing shipped in GUI v2.11.0 / daemon v2.8.0: temperatures
(and, via the opt-in NVML backend, a fan `duty_pct` measurement) surface through
two driver worlds — the open **nouveau** driver (hwmon, whose writable `pwm1` is
deliberately *excluded* from control so the GPU subsystem owns it) and the
proprietary driver via an opt-in, default-off **NVML** telemetry backend. Fan
*writes* are **not** implemented — NVIDIA fan control (Phase 2) is deferred and
hardware-blocked (no NVIDIA hardware to verify against). NVIDIA fans are never
offered as writable curve members, mirroring the Intel Arc read-only pattern
(§14).

### 16. Controls-page fan-role card clips its "Manual" button (FIXED — GUI v2.41.0, DEC-258)

**Resolved 2026-08-09.** Re-measuring for the fix found the gap was wider than
recorded below: the whole details block clipped, not just the Manual button, and
it started at **11 pt in the default density and 9 pt in compact** — the 4-digit
RPM threshold was one symptom of a metric that was simply too shallow
(`_WIDTH_PER_PT = 11` against a measured ~23 px/pt requirement).

It also could not be closed by a constant. The dominant contributor is the curve
label, whose text is **profile-authored and arbitrary-length** — 286 px of a
304 px content area at 16 pt for an ordinary name — so no card width is ever
sufficient for the widest possible content. The fix is therefore both halves: the
metric re-derived from measurement, and the curve label switched to the existing
`ElidedLabel` primitive so the unbounded case degrades to an ellipsis instead of
a clip. Compact still elides above ~9 pt, which is the honest meaning of that
density once the content is fixed-width.

**Re-opened and re-closed 2026-08-31 (285-h).** The re-derived metric was
*itself* measured against the wrong font: the test harness never called
`register_bundled_fonts()`, so the sweep resolved the host's fallback rather than
the bundled DM Sans the running app uses. Against the real font the requirement
is ~26.4 px/pt, not the 23 px/pt provisioned — so the headroom shrank with every
point of font size and reached exactly **0 px** at comfortable/15 pt. The claim
"the default and large densities never elide at any size 7–16 pt" was therefore
true only by a tie, and only on machines whose fallback font happened to be no
wider than this one's. Now base **305**, **27 px/pt**, worst must-fit cell +20 px,
with the harness pinning the font so the figure means the same thing everywhere.

Guarded by `test_the_control_card_details_row_fits_every_density`, which sweeps
every font size *and* every density rather than pinning the one reported symptom,
asserts a minimum **headroom** rather than a bare fit, and asserts the resolved
font family before trusting any of it.

<details><summary>Original report (2026-08-08)</summary>

#### Controls-page fan-role card clips its "Manual" button (cosmetic)

**Symptom.** The action row at the bottom of each fan-role card reads
`718 RPM │ Manual │ Delete │ Edit…`, and the Manual button's label renders as
"Manua". Visible in the shipped `screenshots/auto/02_controls.png` (Pump / AIO at
1191 RPM) and in every capture back to at least v2.38.0 — it is not a regression
from the v2.39.0/v2.40.0 work, it was simply never noticed.

**Measured behaviour** (offscreen, `ControlCard` at each density, default theme):

| Card size | Card width | Manual button gets | `sizeHint` | Result |
|---|---|---|---|---|
| compact | 258 px | 51 px | 64 px | **clipped at every RPM** |
| comfortable (default) | 280 px | 64 px @ 3 digits, 59 px @ 4 | 64 px | **clipped from 1191 RPM up** |
| large | 330 px | 64 px | 64 px | never clipped |

So the reported 4-digit trigger is only the *default* density's threshold —
**compact clips for every fan, at every speed**. Any pump or fan reading over
999 RPM hits it on comfortable, which is most pumps.

**Mechanism.** `control_card.py:196-227` builds the row as
`QLabel(rpm) → addStretch() → Manual → Delete → Edit…`. The card is a
fixed-width grid item (DEC-128 density tiers, DEC-129 per-card overrides), so
when the row's natural width exceeds the card the stretch collapses first and Qt
then shrinks the buttons. Note the button reports
`minimumSizeHint == sizeHint == 64 px` and is still allocated 51 px: the row is
over-constrained, so Qt goes *below* the stated minimum and Qt elides the label.
Widening the RPM text (3 → 4 digits) simply moves the threshold.

**Why it is only cosmetic.** Nothing functional depends on the caption; the
button keeps its full hit area, its tooltip, and its `ControlCard_Btn_manual_{id}`
objectName, so both users and tests still reach it.

**Fix shapes, cheapest first.** Give the RPM label a fixed width sized for four
digits (it is the only variable-width item, and a stable column also stops the
buttons shifting as RPM changes); or drop the row to icons with tooltips at the
compact tier; or let the row wrap below a width threshold. Not attempted here —
it is a `/frontend-design` question about the density tiers, not a one-line
padding tweak, and DEC-128/129 own that surface.

</details>

## Resolved Gaps (previously listed as future work)

> **Historical ledger.** Each row records a fix at the version in its Evidence
> column. Some describe mechanisms that were **later deleted at the 2.0.0 cutover
> (DEC-165)** — the GUI control loop, `LeaseService`/`ControlLoopService`, the
> GUI-held hwmon lease, and the 30 s `gui_active` profile-engine deferral. Those
> rows are kept for provenance; the components no longer exist (the daemon is the
> sole PWM writer and self-leases internally).

| Gap | Resolution | Version |
|-----|-----------|---------|
| hwmon fans displayed `pwm1`, not `CPU_FAN` (§16, opened 2026-07-23) | **Two independent blockers** (§16's "likely fix" covered only the first): (a) the daemon *synthesises* `pwmN` when the chip publishes no label file, so "non-empty label" was wrongly read as "authoritative" — an exact-match `is_placeholder_hwmon_label` now skips it and the resolver owns tiers 2-5; (b) `AppState.board_info`, which keys the DMI fallback table, had had **no production writer** since `090370e`/v2.22.0 dropped it from the retired `DiagnosticsPage` — `DiagnosticsService.set_hw_diagnostics` is now its single writer and polling prefetches `/diagnostics/hardware` once at startup. Also fixes the DEC-095/162 30% CPU/pump floor on these boards (DEC-229) | GUI v2.30.0 |
| GUI rescan button (endpoint existed, never wired) | Diagnostics ▸ Troubleshooting "Rescan Hardware" + restored `hwmon_rescan` wrapper + chained diagnostics refetch (DEC-147) | GUI v1.35.0 |
| GUI surface for `reset_gpu_fan` (§11) | Diagnostics ▸ Troubleshooting "Restore GPU Fan to Automatic", gated against the active profile owning an `amd_gpu:` member (DEC-147; re-keyed off the loop at 2.0.0, DEC-165) | GUI v1.35.0 |
| Emergency ↔ GUI lease ping-pong (alternating curve/forced PWM during thermal emergencies) | `thermal_state` in GET /status + GUI control-loop/lease stand-down (DEC-132) | GUI v1.30.0 / daemon v1.13.0 |
| Per-tick sensor re-discovery (~340 sysfs ops/s; asus_wmi_sensors polling risk) | Descriptor cache + triggered re-discovery (DEC-133) | daemon v1.13.0 |
| GPU GUI-priority lapse on slow ramps (coalesced writes didn't count as liveness; engine used exact-match suppression) | record_gui_write on coalesced returns + shared 5% threshold (DEC-131) | daemon v1.13.0 |
| Calibration could park a fan on mid-sweep write failure; dead duplicate sweep implementation | Handler delegates to single tested helper; restore on every exit path (DEC-134) | daemon v1.13.0 |
| profile_engine_loop 5-jobs-in-one (threshold drift between inline phases) | Decomposed: safety tick + WriteBackend per backend; GPU structurally outside safety (DEC-135) | daemon v1.13.0 |
| Control loop retried failing targets at 1 Hz forever | 15 s retry decay after 3 consecutive failures (DEC-136) | GUI v1.30.0 |
| hwmon headless writes | Auto-lease in profile engine | v0.5.1 (R43) |
| Thermal safety hwmon | force_take_lease in safety rule | v0.5.1 (R43) |
| Serial auto-reconnect | Reconnect mode in poll loop | v0.5.1 (R43) |
| Socket permissions | chmod 0666 after bind | v0.4.2 (R38) |
| GPU fan in /poll | Added to poll_handler | v0.4.3 (R39) |
| GPU zero-RPM | Auto-disable in set_static_speed | v0.5.0 (R40) |
| Hardware readiness diagnostics | New daemon endpoint + GUI display | GUI v1.1.0 / daemon v1.2.0 |
| Chip-family guidance | Knowledge base with BIOS tips and driver info | GUI v1.1.0 |
| Vendor knowledge base gaps (ASRock, ASUS WMI, MSI X870, NCT6686D) | Expanded to 17 chip + 12 vendor quirk entries, module conflict detection, post-verify guidance (DEC-092) | GUI v1.3.0 |
| Read-only hwmon labels | Controls page shows "(read-only)" for non-writable headers | GUI v1.1.0 |
| FanController Arc refactor | Assessed: current design is correct, no refactor needed | R46 |
| GPU PMFW write churn (gaming stutter) | 5% threshold + dual-writer conflict resolution + sysfs parse fix | v0.5.3 |
| Daemon state persistence fails under systemd sandbox | StateDirectory + configurable state_dir + ReadWritePaths | v0.5.4 (R50) |
| hwmon redundant sysfs writes in steady state | Per-header coalescing (pwm_enable + PWM value) | v0.5.4 (sanity check) |
| OpenFan dual-writer when GUI + profile engine active | Profile engine defers to GUI (30s check) | v0.5.4 (sanity check) |
| hwmon pwm_enable not restored on daemon shutdown | Shutdown handler writes pwm_enable=2 for all headers | v0.5.4 (V4 audit P0) |
| Thermal safety override errors silently dropped | Controls-page status chip surfaces "Override blocked — thermal emergency (fans held by safety)" when the daemon refuses a `thermal_abort` override (the earlier ERROR-log mitigation was retired) | GUI v2.8.2 (audit-2026-07-03) |
| GPU write endpoints missing from API docs | Added to CLAUDE.md, 08_API_Contract, 09_State_Model | v0.69.0 (V4 audit G2) |
| Dead code: unused signals, client method, fixtures | Removed with full removal log | v0.69.0 (V4 audit G3) |
| Journal unit name wrong (control-ofc-daemon.service → control-ofc-daemon) | Fixed in code and spec | v0.71.0 (R51) |
| Support bundle missing journal logs and fan state | Added journal + fan_state + missing_sections | v0.71.0 (R51) |
| Export only captured active theme (not all custom) | All custom themes now exported and imported | v0.71.0 (R51) |
| Import didn't validate export version | Version check added, rejects unsupported versions | v0.71.0 (R51) |
| Syslog/telemetry de-scoped | Full removal from daemon + GUI (R52) | v0.72.0 |
| GPU PMFW curve writes rejected with EINVAL | OD_RANGE clamping + failure suppression (R53) | v0.5.4 |
| Color dialog tiny and non-resizable on Linux | DontUseNativeDialog flag (R54) | v0.73.0 |
| Startup sidebar shows Dashboard when another page restored | sidebar.select_page() on restore (R54) | v0.73.0 |
| Daemon config path hardcoded | --config CLI + CONTROL_OFC_CONFIG env var override | v0.5.4 (release gen) |
| Serial fallback limited to ttyACM only | Added ttyUSB0-9 probing | v0.5.4 (release gen) |
| Event Log tab perpetually empty | log_event wired to polling/lease/control_loop/profile/gui transitions; new EventLogView with filters + search (DEC-111) | v1.16.0 |
| Service DeviceAllow hardcoded to ttyACM0-1 | Wildcard char-ttyACM/ttyUSB classes | v0.5.4 (release gen) |
| Serial group uucp not portable | Both uucp + dialout in SupplementaryGroups | v0.5.4 (release gen) |
| Color dialog too small (static getColor) | Instance-based QColorDialog with setMinimumSize | v0.74.0 (R55) |
| Color dialog still tiny (Qt SetFixedSize layout constraint) | Override layout to SetDefaultConstraint before sizing | v0.75.0 (R56) |
| Color dialog internal widgets corrupted by app QWidget stylesheet | Stylesheet isolation with targeted dialog-frame theming | v0.76.0 (R57) |
| Color dialog still broken — dialog-level stylesheet cannot override app QWidget rule | Clear app stylesheet temporarily during QColorDialog exec() | v0.77.0 (R58) |
| Fan Wizard restored to 100% instead of prior state | Restore to prior PWM with 30% fallback (R59) | v0.78.0 |
| Fan Wizard showed fans without RPM readings | Filter by rpm is not None (R59) | v0.78.0 |
| Fan Wizard Start test failed silently | stop_fan returns error, shown in UI (R59) | v0.78.0 |
| Fan Wizard _restore_all_fans missing GPU path | Delegates to restore_fan for all sources (R59) | v0.78.0 |
| Fan Wizard Next stuck — same page ID returned by nextId | Dynamic test pages with unique IDs (R60) | v0.79.0 |
| Fan Wizard showed fans with RPM=0 (empty slots) | Filter `not fan.rpm` catches None and 0 (R60) | v0.79.0 |
| Fan Wizard amdgpu hwmon entries caused Permission denied | Skip hwmon entries with "amdgpu" in ID (R60) | v0.79.0 |
| Fan Wizard dynamic pages caused infinite recursion / app crash | Reverted to single TestPage with internal fan cycling (R61) | v0.80.0 |
| Profile activation fails (path validation mismatch) | Configurable `[profiles] search_dirs` in daemon.toml + CWE-22 canonicalization fix | v0.83.0 (R62) |
| Profile selection has no visible effect | Fixed combo box snap-back bug in `_on_profile_selected()` | v0.83.0 (R62) |
| Per-profile content not visible when switching | Data model was correct; UI bug prevented switching (fixed with combo) | v0.83.0 (R62) |
| User data paths not configurable | Settings → Application directory pickers + `set_path_overrides()` | v0.83.0 (R62) |
| Daemon restart required after profile dir change | SIGHUP reload + `POST /config/profile-search-dirs` API endpoint | v0.84.0 (R64) |
| daemon.toml write permissions / architecture boundary | GUI uses daemon API instead of direct file writes (DEC-087 supersedes DEC-084) | v0.84.0 (R64) |
| Multi-user profile directory configuration | API endpoint supports additive dir registration from multiple users | v0.84.0 (R64) |
| Fan table columns uneven | All 4 columns Stretch mode | v0.74.0 (R55) |
| Copy last errors not implemented | Button added to diagnostics event log tab | v0.74.0 (R55) |
| Reconnect controller button | Deferred — daemon auto-reconnects with backoff | Intentionally deferred (R55) |
| One-click diagnostics redaction | Deferred — partial PII scrubbing gives false confidence | Intentionally deferred (R55) |
| Per-sensor freshness on dashboard | Summary cards show ⏱/⚠ indicators with age tooltips | V5 audit |
| Dashboard fan group badges + per-fan state chips (spec Row 3) | Zone-grouped fan cards with per-fan state chip + per-zone roll-ups; raw table → collapsed expander (DEC-176/179) | GUI v2.2.0 |
| Dashboard sensor-freshness side panel (spec) | Collapsible **Sensors** panel (DEC-184; was a Sensors/Events/Warnings inspector, DEC-182) + status strip + summary-card freshness glyphs (DEC-177/178) | GUI v2.2.0–2.3.0 |
| Dense dashboard dominated by raw data | Progressive-disclosure IA: status strip, refined cards, styled/reorderable/collapsible fan-zone cards, readable-by-default chart with modes + annotations, collapsible Sensors panel (DEC-176–187) | GUI v2.2.0–2.3.0 |
| Dashboard accreted five overlapping fan/status presentations | Rebuilt telemetry-first: graph primary, one card per logical control, Sensors rail; summary cards / Fan Array / Fan Zone grid / raw table / Quick Actions / Alerts / status strip all retired, four indicators re-homed to the global footer (DEC-222) | GUI v2.25.0 |
| Daemon panic leaves hardware in manual mode | Panic hook restores GPU curves + hwmon pwm_enable=2 | V5 audit (daemon) |
| GPU reset_to_auto skips zero-RPM on partial failure | Always re-enable zero-RPM regardless of curve reset outcome | V5 audit (daemon) |
| blockSignals pairs exception-unsafe (GUI) | block_signals() context manager with try/finally | V5 audit |
| Read-only RDNA3/4 GPUs returned wrong error code | Canonical `AmdGpuInfo::can_write_legacy_pwm()` helper; both `set` and `reset` arms return `400 feature_unavailable + retryable: false` | DEC-098 (daemon v1.6.1 / GUI v1.10.1) |
| Kernel-version regressions had no in-product surface | `hwmon/kernel_warnings.rs` catalogue + `amd_gpu.kernel_warnings` capability field + GUI one-time popup with acknowledgement persistence | DEC-098 (daemon v1.6.1 / GUI v1.10.1) |
| Fan PWM writes pinned tokio worker threads | All hwmon and OpenFan write handlers run on `spawn_blocking`; thermal-emergency scan re-locks per channel | DEC-099 (daemon v1.6.1) |
| GPU `POST /fan/reset` was overwritten by profile engine within 1 s | Reset records GUI activity (both PMFW and legacy-pwm1 arms) so the engine defers for `GUI_ACTIVITY_TIMEOUT` | DEC-100 (daemon v1.6.2) |
| `POST /hwmon/{id}/verify` silently swallowed restore-PWM errors | Handler returns `restore_failed: bool`; GUI surfaces the failure in the verify result panel | DEC-100 (daemon v1.6.2 / GUI v1.10.2) |
| `AppState.add_warning` / `remove_warning` had a 1 s UI lag | Both methods now call `_update_warnings()` synchronously and emit `warning_count_changed` immediately | DEC-100 (GUI v1.10.2) |
| `LeaseService` recurring 30 s timer raced its own retry chain | Recurring timer suspended for the duration of the 5 s/10 s/15 s backoff retry, restarted on first success | DEC-100 (GUI v1.10.2) |
| `control-ofc-restore-auto.sh` did not restore `fan_zero_rpm_enable` on SIGKILL/OOM | ExecStopPost hook now writes `1\n` + `c\n` to every `fan_zero_rpm_enable` sysfs file alongside the curve reset | DEC-100 (daemon v1.6.2) |
| SSE admission could spuriously 503 under tight CAS contention | `SSE_ADMISSION_ATTEMPTS = 4` with `tokio::task::yield_now()` between failures | DEC-100 (daemon v1.6.2) |
| `GPU_PMFW_WRITE_RETRIES` constant misleadingly named | Renamed to `GPU_PMFW_NUM_CURVE_POINTS` with corrected doc (it is the curve point count, not retry count) | DEC-100 (daemon v1.6.2) |
| `LeaseService` HTTP calls blocked the Qt main thread for up to `API_TIMEOUT_S = 5 s` on a contended daemon | All three lease HTTP calls (`take`/`renew`/`release`) now run on a dedicated `_LeaseWorker` `QThread` with a `LEASE_API_TIMEOUT_S = 1.5 s` cap. `acquire()` is request-based in worker mode; `_write_target` skips a cycle when the lease isn't yet held instead of blocking on a sync take | DEC-108 (GUI v1.13.0) |
| Daemon `daemon_state.json` / `runtime.toml` writes were only atomic against process crash, not against power loss | New `daemon::atomic_io::write_atomic` helper does fsync of the tmp file before rename and fsync of the parent directory after, mirroring `paths.atomic_write` on the GUI side. Both call sites use it | DEC-108 (daemon v1.8.0) |
| `_WriteWorker.shutdown()` was being called from the main thread BEFORE the worker thread was joined, racing the worker's `do_write` slot | `ControlLoopService.shutdown()` reordered to `thread.quit() → thread.wait() → worker.shutdown()`. Regression test locks the order | DEC-108 (GUI v1.13.0) |
| `POST /fans/openfan/pwm` did not coalesce identical broadcasts the way single-channel `POST /fans/openfan/{ch}/pwm` did, so all-channel writes pinned the serial transport mutex every 1 Hz cycle even when nothing had changed | `SetPwmAllResult` gains a `coalesced: bool` field; when every channel already holds the requested value the controller short-circuits with no serial write and no cache update | DEC-108 (daemon v1.8.0) |
| `POST /profile/activate`'s direct-`profile_path` traversal protection had only unit-test coverage of the helper; no integration test exercised the canonicalise + `starts_with` defence end-to-end through the HTTP layer | 3 new `tests/ipc_integration.rs` cases (outside dir → 400, symlink chained outside → 400, inside dir → 200) | DEC-108 (daemon v1.8.0) |
| `_register_profile_search_dir` logged at INFO on every reconnect, cluttering the journal on a flapping socket | First registration per process logs INFO; subsequent re-registrations (same dir) log DEBUG. The HTTP call still fires every reconnect for daemon-restart safety | DEC-108 (GUI v1.13.0) |
| The GUI and daemon curve evaluators were two hand-mirrored implementations with no test feeding the same input to both (the latent cause of DEC-096 / DEC-119 drift) | Shared, byte-identical `parity_vectors.json` asserted on both sides — GUI `tests/test_evaluator_parity.py` + daemon `profile_engine.rs` parity tests — covering curve interpolation and the full deadband/step/start-stop/mixed-GPU tuning sequence; daemon `evaluate_graph` fallthrough aligned to the GUI | DEC-126 (GUI 1.27.0 / daemon 1.12.2) |
| Controls tab had no inline/transient manual override (only the persisted 6-step `ControlMode.MANUAL` flow), leaving the documented "how do I temporarily override?" UX unmet | Per-card Manual toggle + inline slider driving a new per-control `set_control_manual`/`clear_control_manual` loop API (not persisted, clears on profile change), distinct from the wizard's global override | DEC-127 (GUI 1.27.0) |
| Present-but-unreadable sensors spammed the journal (ath12k WiFi temp at 1 Hz) | `SensorFailureTracker` quarantine → `unavailable_sensors[]` on /status+/poll + `control_eligible`; GUI Diagnostics-only panel + curve-picker filter (DEC-193) | daemon v2.3.0 / GUI v2.5.0 |
| No structured hwmon inventory / CPU+mobo sensor selection / readiness surface | `GET /inventory/hwmon` + `GET /inventory/readiness` + preferred CPU/mobo sensor persistence; GUI Readiness tab + preferred-sensor pickers; thermal-guarded (safe) verify (DEC-200/201) | daemon v2.6.0 / GUI v2.9.0 |
| Manual Super-I/O chip identification during setup | Passive Super-I/O detection (`GET /inventory/superio`) + opt-in active `/dev/port` probe (`POST /inventory/superio/probe`); GUI Super-I/O diagnostics tab (DEC-202/203) | daemon v2.7.0 / GUI v2.10.0 |
| NVIDIA discrete GPU invisible | Read-only NVIDIA sensing — nouveau hwmon (control-excluded) + opt-in NVML telemetry, `duty_pct` wire field, `nvidia_gpu` caps/diag (DEC-204) | daemon v2.8.0 / GUI v2.11.0 |
