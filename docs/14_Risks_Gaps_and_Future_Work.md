# 14 — Risks, Gaps, and Future Work

**Last updated:** 2026-04-08 (V5 Phase 7 documentation audit)

## Current Feature Status Matrix

### Fan Write Backends

| Backend | GUI Imperative | Daemon Profile (Headless) | Thermal Safety Emergency |
|---------|---------------|--------------------------|-------------------------|
| **OpenFan** | Full | Full | Full (105C→100%) |
| **hwmon (motherboard)** | Full (lease required) | **Full (R43: auto-lease)** | **Full (R43: force_take)** |
| **AMD GPU (PMFW)** | Full | Full | Relies on PMFW firmware |

### Device Lifecycle

| Feature | Status | Notes |
|---------|--------|-------|
| Startup device detection | IMPLEMENTED | hwmon + serial auto-detect at daemon start |
| Serial startup retry | IMPLEMENTED | 5x exponential backoff (1-16s) |
| **Serial runtime reconnect** | **IMPLEMENTED (R43)** | After 5 consecutive errors, enters reconnect mode with backoff |
| hwmon manual rescan | IMPLEMENTED | `POST /hwmon/rescan` endpoint |
| GUI rescan button | ABSENT | Endpoint exists but not wired in GUI |
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
Devices are only discovered at daemon startup. If a USB device is plugged in after the daemon starts, it is invisible until the daemon is restarted. The `POST /hwmon/rescan` endpoint re-enumerates PWM headers but does not update GPU detection.

### 2. No GPU-specific thermal safety rule
The thermal safety rule monitors CPU Tctl only (105C trigger). GPU temperatures rely on PMFW firmware protection. If a daemon-level GPU thermal rule is needed, it would require reading GPU junction temp from the cache and adding a separate threshold.

### 3. GUI/daemon simultaneous control conflict (PARTIALLY RESOLVED — v0.5.3)
For hwmon fans: GUI always wins via `force_take` lease preemption.
For GPU fans: profile engine defers to GUI when GUI was active in last 30s (DEC-071). Previously, both the GUI control loop and profile engine independently wrote to PMFW every second, causing gaming stutter via GPU SMU firmware churn.
**Remaining limitation:** If the GUI disconnects and reconnects within 30s, there may be a brief period where neither writes. This is acceptable — the PMFW firmware manages fans automatically during the gap.

### 4. FanController ownership model (RESOLVED — R46 assessment)
The daemon's `FanController` is `Option<Arc<parking_lot::Mutex<FanController>>>` in `AppState`. This was previously listed as requiring an Arc refactor for clean API/profile-engine separation. R46 investigation confirmed the current design is correct: locks are held for ~1-2ms (serial I/O), never across `.await` points, contention is minimal (1Hz profile engine + user-driven API), and both paths use the same public `set_pwm()` methods. Per-channel locking would add complexity without benefit since serial I/O is inherently sequential. No refactor needed.

### 5. AIO cooler support placeholder
`AioPumpState` struct exists in the daemon but has no implementation. No AIO detection, no AIO control.

### 6. Multi-GPU UI selection
Data model supports multiple GPUs. API reports primary only. No UI to select between multiple discrete GPUs. Untested with 2+ dGPUs.

### 7. Some GUI spec features not implemented
- Background self-checks (deferred)
- One-click diagnostics redaction (deferred — partial PII scrubbing gives false confidence)

### 8. polkit helper for offline config editing (deferred)

When the daemon is not running, the GUI cannot use the API to update profile search dirs. Users must manually edit `/etc/control-ofc/daemon.toml`. A polkit privileged helper could provide a GUI dialog for this, but is not needed while the daemon is running (the API endpoint handles it).

### 9. Drop-in directory pattern for profile config (deferred)

A composable drop-in directory (`/etc/control-ofc/profiles.d/*.conf`) could replace the single `search_dirs` array for more flexible multi-user config. The current API-based approach (DEC-087) is sufficient for V1.

## Resolved Gaps (previously listed as future work)

| Gap | Resolution | Version |
|-----|-----------|---------|
| hwmon headless writes | Auto-lease in profile engine | v0.5.1 (R43) |
| Thermal safety hwmon | force_take_lease in safety rule | v0.5.1 (R43) |
| Serial auto-reconnect | Reconnect mode in poll loop | v0.5.1 (R43) |
| Socket permissions | chmod 0666 after bind | v0.4.2 (R38) |
| GPU fan in /poll | Added to poll_handler | v0.4.3 (R39) |
| GPU zero-RPM | Auto-disable in set_static_speed | v0.5.0 (R40) |
| FanController Arc refactor | Assessed: current design is correct, no refactor needed | R46 |
| GPU PMFW write churn (gaming stutter) | 5% threshold + dual-writer conflict resolution + sysfs parse fix | v0.5.3 |
| Daemon state persistence fails under systemd sandbox | StateDirectory + configurable state_dir + ReadWritePaths | v0.5.4 (R50) |
| hwmon redundant sysfs writes in steady state | Per-header coalescing (pwm_enable + PWM value) | v0.5.4 (sanity check) |
| OpenFan dual-writer when GUI + profile engine active | Profile engine defers to GUI (30s check) | v0.5.4 (sanity check) |
| hwmon pwm_enable not restored on daemon shutdown | Shutdown handler writes pwm_enable=2 for all headers | v0.5.4 (V4 audit P0) |
| Thermal safety override errors silently dropped | Errors logged at ERROR level with THERMAL SAFETY prefix | v0.5.4 (V4 audit P1) |
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
