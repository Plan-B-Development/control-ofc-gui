# Control-OFC Daemon — End-to-End Technical Documentation

> **Snapshot, not source of truth.** This document was generated from a code
> inspection on 2026-03-25 against daemon v0.2.0. The daemon has since
> evolved through dozens of releases — specific claims about endpoints, error
> codes, or module shape may be stale.
>
> **⚠️ Superseded by the 2.0.0 daemon-control cutover (DEC-159/DEC-165).** Several
> architectural claims below describe a model that no longer exists:
> - The **dual-control model** (§1, §2) is gone — the daemon profile engine is now
>   the **sole PWM writer**; the GUI never writes PWM (poll-only, 1 Hz `GET /poll`).
> - The **GUI-held hwmon lease** and the **30 s `gui_active` deferral** (§9, §10) were
>   **deleted** at the cutover — the daemon self-leases internally, and headless
>   profile/thermal writes to every backend (OpenFan, hwmon, GPU) are fully shipped.
> - **AIO support** is no longer a placeholder — hwmon-attached liquid cooling shipped
>   (DEC-156).
> - Live manual override and fan identify are now **daemon APIs** (DEC-163/DEC-166),
>   not GUI-side writes.
>
> The GPU PCI-ID table in §8 has been corrected inline (it was a factual error, not
> merely stale). For current authoritative documentation:
> - Architecture / sole-writer model: `CLAUDE.md` (this repo)
> - API contract: `docs/08_API_Integration_Contract.md` (in this repo)
> - Module layout: `daemon.md` (in the daemon repo)
> - Endpoint reference: `daemon.md` § API Endpoints
> - Recent changes: daemon `CHANGELOG.md`

**Snapshot taken at:** daemon v0.2.0 (2026-03-25)
**Evidence level:** All claims marked as Implemented, Inferred, or Uncertain

---

## 1. Executive Summary

The Control-OFC daemon (`control-ofc-daemon`) is a Rust service that provides hardware-mediated fan control for Linux systems. It communicates with two hardware interfaces: **OpenFanController** (USB serial, Karanovic Research) and **motherboard hwmon** (Linux sysfs). The GUI communicates with the daemon over HTTP via a Unix domain socket.

**Key architectural themes:** *(⚠️ superseded — see top banner; the dual-control and lease bullets below describe the pre-2.0.0 model)*
- Daemon owns all hardware access — GUI never touches hardware directly
- Dual control model: imperative (GUI drives) or profile (daemon drives autonomously)
- Thermal safety rule: CPU Tctl 105°C → force OpenFan+hwmon fans to 100% (GPU excluded — DEC-130), hold until 80°C
- Lease-based exclusive write access for hwmon
- Atomic state persistence for restart/reboot recovery

**Implementation status:** Core functionality is implemented and tested. hwmon writes from the profile engine require a daemon-side auto-lease (documented as future work).

---

## 2. Architecture Overview

### Module Structure (`daemon/src/`)

| Module | Files | Responsibility |
|--------|-------|---------------|
| `main.rs` | 1 | Entry point, config loading, task spawning, shutdown |
| `config` | 1 | TOML configuration with validation |
| `api` | 5 | HTTP handlers, server, SSE, responses, calibration |
| `serial` | 5 | OpenFanController serial protocol and transport |
| `hwmon` | 9 | Linux hwmon sysfs discovery, reading, PWM writing, lease, GPU detection, PMFW fan control |
| `health` | 4 | StateCache, staleness computation, history ring |
| `profile` | 1 | GUI-created profile loading and curve evaluation |
| `profile_engine` | 1 | Autonomous 1Hz curve evaluation loop |
| `safety` | 1 | Thermal emergency state machine |
| `daemon_state` | 1 | Persistent state (active profile) |
| `error` | 1 | Error type definitions |
| `polling` | 1 | Sensor/fan polling loops |

### Dependencies

| Crate | Version | Purpose |
|-------|---------|---------|
| `tokio` | 1.x | Async runtime (multi-threaded) |
| `axum` | 0.8 | HTTP framework for Unix socket API |
| `serialport` | 4.9.0 | Serial port I/O for OpenFanController |
| `parking_lot` | 0.12 | Fast mutexes without poisoning |
| `serde`/`serde_json` | 1.x | JSON serialization |
| `toml` | 0.8 | Configuration file parsing |
| `env_logger` | 0.11 | Logging |

---

### Controller Ownership Model (R46 assessment)

Both `FanController` (OpenFan serial) and `HwmonPwmController` (motherboard PWM) are stored in `AppState` as `Option<Arc<parking_lot::Mutex<T>>>`. This shared ownership is intentional:

- **API handlers** acquire the lock for ~1-2ms per write (single serial round-trip)
- **Profile engine** acquires the same lock at 1Hz per channel (no `.await` inside lock scope)
- **Polling loop** accesses the shared transport via `spawn_blocking` (lock inside sync block, `.await` outside)
- **parking_lot::Mutex** is non-poisoning and fair — no panics on contention

**Why this is correct:**
- Serial I/O is inherently sequential — per-channel locks would add complexity without benefit
- Contention is minimal: 1Hz profile engine + user-driven API calls + 1Hz polling
- The lock is never held across `.await` points (verified for calibration handler, profile engine, and polling loop)
- Both API and profile engine call the same public `set_pwm()` interface — no architectural bypass

**Why alternatives were rejected:**
- Channel/actor pattern would add latency and complexity for an inherently sequential device
- Per-channel locks are meaningless when the underlying serial transport is a single stream
- Removing shared state would require either message passing (overhead) or single-writer-at-a-time (already what we have)

---

## 3. End-to-End Runtime Flow

### Startup Sequence

```
1. Load config from /etc/control-ofc/daemon.toml (defaults if missing)
2. Initialize StateCache (shared in-memory state)
3. Detect OpenFanController serial device
   └─ Retry loop: 5 retries with exponential backoff (1s, 2s, 4s, 8s, 16s)
   └─ Auto-detect: scans /dev/ttyACM0–9 or uses libudev
   └─ Opens at 115,200 baud, 8N1, no flow control
4. Discover hwmon PWM headers from /sys/class/hwmon/
5. Load initial profile (CLI --profile > OPENFAN_PROFILE env > persisted state > none)
6. Construct AppState with all controllers, cache
7. Spawn async tasks:
   ├─ hwmon sensor polling loop (1s interval)
   ├─ OpenFan RPM polling loop (1s interval, if connected)
   ├─ Profile engine loop (1Hz, evaluates curves and writes PWM)
   └─ IPC HTTP server on Unix socket
8. Wait for Ctrl+C signal
9. Signal all tasks to shutdown
10. Clean up socket file
```

### Steady-State Operation

The daemon runs concurrent async tasks on the Tokio multi-threaded runtime:

1. **hwmon poll** — reads all `temp*_input` files, updates StateCache
2. **OpenFan poll** — sends `ReadAllRpm` over serial, updates StateCache
3. **Profile engine** — evaluates curves against cached sensor data, writes PWM
4. **IPC server** — handles GUI HTTP requests over Unix socket

### Shutdown

- `Ctrl+C` → `tokio::signal::ctrl_c()`
- Watch channel broadcasts `true` to all polling/engine tasks
- Oneshot channel signals IPC server
- Tasks complete, socket file cleaned up

---

## 4. GUI ↔ Daemon Interaction Model

### Transport
- **Protocol:** HTTP/1.1 over Unix domain socket
- **Socket path:** `/run/control-ofc/control-ofc.sock` (configurable)
- **Framework:** Axum 0.8 (server), httpx with UDS transport (client)
- **Rationale:** HTTP over UDS provides typed request/response, standard error handling, and tool compatibility (curl) while remaining local-only

### API Surface (20 endpoints)

**Read endpoints (GET):**

| Path | Purpose | Latency class |
|------|---------|--------------|
| `/status` | Subsystem health, freshness (age_ms), uptime, thermal state, active overrides/identify | Fast |
| `/sensors` | All temperature readings with age/rate/min/max | Fast |
| `/fans` | All fan states (RPM, PWM, stall detection) | Fast |
| `/poll` | Combined status+sensors+fans (batch) | Fast |
| `/sensors/history` | Time-series ring buffer (250 points) | Fast |
| `/events` | SSE stream (real-time updates) | Streaming |
| `/capabilities` | Device presence, feature flags, safety limits | Fast |
| `/hwmon/headers` | Discovered PWM outputs | Fast |
| `/hwmon/lease/status` | Lease holder and TTL | Fast |
| `/profile/active` | Currently active profile | Fast |

**Write endpoints (POST):**

| Path | Purpose | Requires lease |
|------|---------|---------------|
| `/fans/openfan/{ch}/pwm` | Set PWM on one channel | No |
| `/fans/openfan/pwm` | Set PWM on all channels | No |
| `/fans/openfan/{ch}/target_rpm` | Set RPM target | No |
| `/fans/openfan/{ch}/calibrate` | PWM-to-RPM sweep | No |
| `/hwmon/{id}/pwm` | Write PWM to motherboard | **Yes** |
| `/hwmon/lease/take` | Acquire exclusive lease | — |
| `/hwmon/lease/release` | Release lease | — |
| `/hwmon/lease/renew` | Extend lease TTL | — |
| `/hwmon/rescan` | Re-enumerate devices | No |
| `/profile/activate` | Switch active profile | No |

> **Correction (factual error, not just stale):** `/fans/openfan/{ch}/target_rpm`
> above was **never an HTTP route**, even at v0.2.0 — closed-loop RPM targeting
> exists only as an internal serial method (`set_target_rpm` / `Command::SetTargetRpm`).
> Separately, every bare PWM-write row and the entire `/hwmon/lease/*` quartet were
> **retired at 2.0.0** (DEC-165): the daemon engine self-leases and is the sole
> writer. See `docs/08_API_Integration_Contract.md` for the current surface.

### Error Model

All errors use `ErrorEnvelope`:
```json
{"error": {"code": "string", "message": "string", "retryable": bool, "source": "string"}}
```

| HTTP | Code | Source | Retryable |
|------|------|--------|-----------|
| 400 | `validation_error` | validation | false |
| 404 | `not_found` | validation | false |
| 409 | `thermal_abort` | hardware | true |
| 500 | `internal_error` | internal | true |
| 503 | `hardware_unavailable` | hardware | true |

### Staleness and age_ms semantics

The `age_ms` field in `/status` subsystem entries and `/sensors`/`/fans` responses represents **daemon-side data cache staleness**: the elapsed time since the daemon's polling loop last successfully read data from that hardware source.

**Computation:** `staleness.rs` computes `Instant::now() - last_subsystem_update` at response time. The value increases between poll cycles and resets when fresh data arrives.

**Why subsystem ages differ:**
- **OpenFan** (serial): Each poll cycle involves USB serial send + wait + parse. Typical I/O time is 100-500ms. The subsystem timestamp updates when the serial response is cached.
- **hwmon** (sysfs): Each poll reads kernel-exposed files under `/sys/class/hwmon/`. Typical I/O time is ~1ms.

**Freshness thresholds (staleness.rs):**
- OK: age <= 2 × expected interval
- WARN: age > 2× and <= 5× interval
- CRIT: age > 5× interval or never updated

**Important:** The GUI's polling service adds 0-1000ms of additional staleness (its own poll interval) that is not reflected in the daemon's `age_ms` value. The total staleness observed by the user is approximately `daemon_age_ms + gui_poll_lag`.

---

## 5. Sensors, Curves, Profiles, and Control Loop

### Sensor Acquisition
- **hwmon:** Reads `/sys/class/hwmon/hwmonN/temp*_input` (millidegrees → °C)
- **OpenFan:** Sends `>00\n` (ReadAllRpm), parses RPM from hex response
- **Polling interval:** Configurable, default 1000ms
- **Staleness:** Based on 2× and 5× polling interval thresholds

### Profile Evaluation (profile_engine.rs)
- Runs at 1Hz when a profile is active
- Reads sensor values from StateCache
- Evaluates each curve's points via linear interpolation
- Produces PwmCommand list (member_id, pwm_percent, source)
- Writes OpenFan PWM via FanController (Arc-shared)
- hwmon writes implemented via auto-lease in profile engine (R43, v0.5.1)

### Thermal Safety (safety.rs)
- **Implemented and tested** with 8 unit tests
- Evaluates CPU Tctl from cache each engine cycle
- State machine: Normal → Emergency (105°C) → Hold (>80°C) → Recovery (≤80°C, 60% for 2 cycles) → Normal

---

## 6. OpenFan Controller Integration Deep-Dive

*See companion document: `docs/architecture/openfan-controller-integration.md`*

---

## 6a. AMD GPU Integration (R35 + R36)

### Detection (`hwmon/gpu_detect.rs`)

The daemon scans hwmon devices at startup looking for `name == "amdgpu"`. For each match:
1. Resolves the `device` symlink to get the PCI device path
2. Extracts the PCI BDF address (e.g. `0000:2d:00.0`) for stable identity
3. Reads PCI device ID (`/sys/bus/pci/devices/{BDF}/device`) to determine GPU model
4. Reads PCI class code to distinguish discrete VGA (`0x030000`) from render-only (`0x038000`)
5. Checks for PMFW fan_curve support at `/sys/class/drm/cardN/device/gpu_od/fan_ctrl/fan_curve`

### Source labeling

Sensors from amdgpu hwmon devices report `source: "amd_gpu"` (not `"hwmon"`). This allows the GUI to group and display GPU sensors separately from motherboard sensors.

### PMFW fan_curve control (`hwmon/gpu_fan.rs`)

RDNA3+ GPUs expose a firmware-managed fan curve at `gpu_od/fan_ctrl/fan_curve`. The daemon can:
- **Read** the current curve: parses the `OD_FAN_CURVE:` format into `(temp_c, speed_pct)` points
- **Write** a custom curve: writes `"INDEX TEMP SPEED"` per point, then commits with `"c"`
- **Set static speed**: writes a flat curve where all points have the same percentage
- **Reset to auto**: writes `"r"` then `"c"` to restore firmware default curve

**Prerequisites:** `amdgpu.ppfeaturemask` kernel parameter must include the PP_OVERDRIVE_MASK bit (0x4000). Without it, the `fan_curve` file does not exist.

### GPU model identification

PCI device IDs (plus revision, where one device ID covers multiple SKUs) are
mapped to marketing names via a lookup table:

| PCI ID | Revision | Model |
|--------|----------|-------|
| 0x7550 | 0xC0 | RX 9070 XT |
| 0x7550 | 0xC3 | RX 9070 |
| 0x744C | — | RX 7900 XTX |
| 0x7448 | — | RX 7900 XT |
| ... | | (see gpu_detect.rs for full table) |

Navi 48 (RX 9070 XT / 9070) shares device ID **0x7550** and is disambiguated by
PCI revision (0xC0 = XT, 0xC3 = non-XT). Unknown IDs fall back to "AMD D-GPU"
as the display label.

### Capabilities API

The `/capabilities` response includes an `amd_gpu` object:
```json
{
  "present": true,
  "model_name": "RX 9070 XT",
  "display_label": "9070XT",
  "pci_id": "0000:2d:00.0",
  "fan_control_method": "pmfw_curve",
  "pmfw_supported": true,
  "fan_rpm_available": true,
  "fan_write_supported": true,
  "is_discrete": true
}
```

### Safety

- On daemon shutdown, all known PMFW fan_curve paths are reset to automatic
- The GPU firmware (PMFW) provides thermal throttling protection even in manual mode
- The kernel driver implements SW CTF (Software Critical Thermal Failure) shutdown at `temp_crit`
- Hardware thermal trip provides last-resort protection independent of software

---

### GPU fan polling (R36, R39)

GPU fan RPM is polled alongside motherboard hwmon sensors in the same `hwmon_poll_loop`. For each detected AMD GPU with `fan1_input`, the polling loop reads RPM and stores it in `DaemonState.gpu_fans` (keyed by `amd_gpu:<PCI_BDF>`).

**Important:** GPU fans are included in BOTH the `/fans` endpoint AND the `/poll` batch endpoint (R39 fix). The `/poll` handler iterates `snap.gpu_fans` alongside `openfan_fans` and `hwmon_fans`. Prior to R39, GPU fans were missing from `/poll`, which is the GUI's primary data source.

### GPU fan write endpoints (R36)

| Endpoint | Purpose | Lease required? |
|----------|---------|-----------------|
| `POST /gpu/{gpu_id}/fan/reset` | Reset GPU fan to firmware automatic mode | No |

(The bare `POST /gpu/{gpu_id}/fan/pwm` static-speed write was retired at 2.0.0 — DEC-165. GPU fans are engine-driven; live manual control is the override API, DEC-163.)

GPU fan writes are routed through the PMFW `fan_curve` sysfs interface (RDNA3+) or `pwm1_enable=1` + `pwm1` (pre-RDNA3). No lease is required — PMFW operations are atomic and firmware-managed.

**Write suppression (v0.5.3):** GPU PMFW writes use a 5% minimum change threshold (not 1% like OpenFan/hwmon). Each PMFW commit triggers SMU firmware processing that can stall the GPU display pipeline. During gaming, temperature fluctuations of 0.5-1°C per second would otherwise produce continuous writes. The `disable_zero_rpm()` call is idempotent — it reads the multi-line sysfs output and skips if already disabled.

**Single-writer (2.0.0, DEC-159/DEC-165):** the daemon's profile engine is the sole writer of every backend. The pre-2.0 dual-writer hazard (GUI control loop + engine both writing PMFW) and its 30 s `gui_active` defer window (`cache.last_gui_write_at`, DEC-070/DEC-071) were deleted at the cutover — there is no longer a GUI writer to defer to.

### Profile engine GPU support (R36)

The profile engine's write loop handles `source == "amd_gpu"` members alongside OpenFan. When a profile contains a GPU fan member, the engine:
1. Checks if speed matches last commanded value — if so, skips (write suppression)
2. Extracts the PCI BDF from the member_id
3. Finds the matching GPU in the detected list
4. Calls `set_static_speed()` via `spawn_blocking()` (sysfs writes are synchronous)

(The pre-2.0 "GUI active in last 30 s → skip" defer step was removed at the cutover — the engine is the sole writer, DEC-165.)

### Shutdown safety (R36)

On graceful shutdown (SIGTERM/SIGINT), the daemon:
1. Signals all polling tasks to stop
2. **Resets GPU fan curves to automatic** — iterates all detected GPUs with PMFW fan_curve paths and calls `reset_to_auto()`
3. Shuts down the IPC server

If the daemon crashes, the GPU firmware automatically reverts to its default fan curve on the next driver load/reboot.

---

## 7. Safety and Failure-Handling Matrix

| Condition | Detection | Response | User impact | Gaps |
|-----------|-----------|----------|-------------|------|
| CPU Tctl ≥ 105°C | Profile engine polls cache | Force all OpenFan+hwmon fans 100% (auto-lease via force_take, R43) | Fans max until 80°C | GPU fans excluded by design — PMFW self-protects (DEC-130) |
| CPU Tctl ≤ 80°C (after emergency) | Safety rule evaluate() | Release + 60% recovery for 2 cycles (release + 1) | Fans drop to 60% for two cycles, then profile resumes | None |
| Serial device not found | Retry loop (5×, exponential backoff) | Daemon starts without OpenFan | GUI shows "not connected" | ~~Auto-reconnect at runtime not implemented~~ — **shipped in R43**: after 5 consecutive errors the daemon enters reconnect mode (auto-detect + backoff) |
| Serial timeout (no response) | Per-read serialport timeout (500ms) | Returns `SerialError::Timeout` | Write skipped for this cycle | No automatic retry of failed commands |
| Debug line flooding | MAX_DEBUG_LINES=50 + wall-clock deadline | Returns `SerialError::Protocol` | Write fails, logged | Cannot recover without restart |
| 0% PWM > 8 seconds | Per-channel `stop_started_at` tracking | Reject further 0% commands | Fan restarts at last non-zero | Only protects API writes, not direct sysfs |
| Stale sensor (>2× interval) | Health computation in staleness.rs | Status → Warn/Crit | Warning in diagnostics | No automatic fallback PWM |
| Client hwmon PWM write attempt | No public write route (engine is sole writer since 2.0.0 — DEC-165) | 404 `validation_error` | Not possible | Verify path uses the daemon's own internal lease (DEC-170) |
| Concurrent calibration | `AtomicBool` guard | 409 Conflict | Second request rejected | None |
| Corrupted config file | Config parser returns error | Daemon exits with code 1 | Must fix config | No fallback to defaults on parse error |
| Missing config file | `ErrorKind::NotFound` check | Use defaults silently | Daemon runs with defaults | Correct behavior |
| Daemon state corruption | JSON parse failure | Use defaults, log warning | Profile lost, starts fresh | Correct fallback |

---

## 8. Standards, Protocols, and Libraries

| Standard/Library | Usage | Verified |
|-----------------|-------|----------|
| HTTP/1.1 | GUI↔daemon IPC over Unix socket | Axum 0.8 docs |
| serialport 4.9.0 | Serial I/O for OpenFanController | crates.io docs |
| 115200/8N1 | Serial parameters | Karanovic firmware |
| Linux hwmon sysfs | Temperature/fan/PWM interface | kernel.org docs |
| parking_lot 0.12 | Non-poisoning mutexes | crates.io docs |
| tokio 1.x | Multi-threaded async runtime | tokio.rs docs |

---

## 9. Key Design Decisions

> ⚠️ **Superseded rows (see top banner):** "Lease-based hwmon exclusivity" and
> "Profile engine GUI deferral" describe the pre-2.0.0 model — the GUI-held lease and
> the 30 s `gui_active` deferral were deleted at the 2.0.0 cutover (DEC-165). The daemon
> is now the sole PWM writer and self-leases internally.

| Decision | Implementation | Evidence |
|----------|---------------|----------|
| HTTP over Unix socket for IPC | `axum` + `hyper` + `UnixListener` | ADR `docs/ADRs/001-ipc-transport.md` |
| Daemon owns all hardware access | GUI uses `DaemonClient` only | CLAUDE.md: "Absolute rule: no direct hardware access" |
| Lease-based hwmon exclusivity | 60s TTL, take/release/renew | Prevents GUI↔daemon write conflicts |
| parking_lot instead of std::sync | All mutexes/rwlocks non-poisoning | V2 audit P0-6 fix — prevents daemon crash cascade |
| Stable device IDs (not hwmonN) | PCI/platform path extraction | Survives reboots — `hwmon:k10temp:0000:03:00.0:Tctl` |
| Thermal safety hardcoded | 105°C/80°C/60% not configurable | Safety floors must not be user-adjustable |
| Atomic state persistence | tmp file + `rename()` | POSIX atomicity guarantee |
| Profile precedence: CLI > env > persisted > none | `resolve_initial_profile()` | Explicit priority documented in main.rs |
| hwmon write coalescing | Per-header `last_commanded_pct` + `manual_mode_set` | 0 sysfs ops in steady state (was 4/sec/header) |
| Profile engine GUI deferral | Skips OpenFan/GPU writes when GUI active (30s) | Prevents dual-writer contention across all backends |
| Shutdown hwmon restore | `pwm_enable=2` written for all headers on exit | BIOS regains thermal control after daemon stop/crash |
| Thermal safety error logging | Failed override writes logged at ERROR level | Operator visibility during thermal emergency |
| GPU PMFW OD_RANGE clamping | `set_static_speed` reads device range before writing | Prevents EINVAL from out-of-range speed/temp values |
| GPU write failure suppression | Profile engine skips retry for 60s after failure | Prevents 1/sec journal spam on persistent EINVAL |

---

## 10. Implemented vs Planned vs Unclear

| Feature | Status | Notes |
|---------|--------|-------|
| OpenFan serial control | **Implemented** | PWM, RPM, target RPM, calibration |
| hwmon sensor reading | **Implemented** | All temp*_input files |
| hwmon PWM writing (via GUI lease) | **Implemented** | With readback verification |
| hwmon PWM writing (headless/profile) | **Not implemented** | Requires daemon auto-lease |
| Thermal safety evaluation | **Implemented** | Evaluates in profile engine loop |
| Thermal safety fan writes | **Partially implemented** | Writes to OpenFan only (no hwmon) |
| Profile persistence across reboot | **Implemented** | `/var/lib/control-ofc/daemon_state.json` |
| Profile activation via API | **Implemented** | `POST /profile/activate` |
| SSE event stream | **Implemented** | `GET /events` |
| Auto-reconnect on serial disconnect | **Implemented (R43)** | After 5 consecutive errors the daemon enters reconnect mode (auto-detect + backoff); no restart needed |
| AIO pump support | **Placeholder only** | Struct exists, no implementation |
| udev hotplug detection | **Not implemented** | Static detection at startup only |

---

## 11. Documentation Gaps

1. **No inline protocol spec** — the serial protocol is only documented by code and test vectors
2. **No sequence diagrams** — startup, polling, and write flows are described in prose only
3. **No daemon README** existed before v0.40.0 — now present at `daemon/README.md`
4. **No example config** existed before v0.40.0 — now at `packaging/daemon.toml.example`
5. **hwmon auto-lease design** not documented — needed for headless motherboard control
6. **Auto-reconnect strategy** not designed — serial device loss requires daemon restart
7. **AIO pump support** not designed — only placeholder types exist

---

## 12. Appendix

### Configuration Keys

| Key | Default | Range | Unit |
|-----|---------|-------|------|
| `serial.port` | None (auto-detect) | — | path |
| `serial.timeout_ms` | 500 | ≥50 | ms |
| `polling.poll_interval_ms` | 1000 | ≥100 | ms |
| `polling.publish_interval_ms` | 5000 | ≥poll_interval | ms |
| `ipc.socket_path` | `/run/control-ofc/control-ofc.sock` | — | path |

### Runtime Paths

| Path | Purpose | Owner |
|------|---------|-------|
| `/etc/control-ofc/daemon.toml` | Configuration | Admin |
| `/run/control-ofc/control-ofc.sock` | IPC socket | Daemon (systemd RuntimeDirectory) |
| `/var/lib/control-ofc/daemon_state.json` | Persistent state (configurable via `[state] state_dir`) | Daemon (systemd StateDirectory) |
| `/etc/control-ofc/profiles/` | System profiles | Admin |
| `~/.config/control-ofc/profiles/` | User profiles | GUI |

### Glossary

| Term | Definition |
|------|-----------|
| **OpenFanController** | Karanovic Research USB fan controller (10 channels, serial protocol) |
| **hwmon** | Linux kernel hardware monitoring subsystem (sysfs interface) |
| **Lease** | Exclusive write lock for hwmon PWM (60s TTL, GUI must renew) |
| **Profile** | GUI-created fan curve configuration (JSON, loaded by daemon) |
| **Tctl** | AMD CPU junction temperature (primary thermal safety input) |
| **StateCache** | Thread-safe in-memory cache of all sensor/fan state |
