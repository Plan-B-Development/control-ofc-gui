# 08 — API Integration Contract

## Purpose
This file defines how the GUI should consume the current daemon/API safely and predictably.

## General rules
1. All I/O goes through the API client layer.
2. All responses are parsed into typed internal models.
3. The UI never binds directly to raw JSON.
4. The GUI must handle partial capability availability.
5. The GUI must gracefully support absent hardware.

## Read endpoints

### GET /capabilities
Use this at startup and on explicit refresh to determine:
- API version
- daemon version
- IPC transport
- device presence
- feature support
- min/max limits

This endpoint should drive:
- feature enablement
- write-capability UI state
- settings field validation
- source-specific labels and messages

### GET /status
Use for:
- top-level health
- subsystem status/freshness
- queue depth
- dropped counters
- last error summary

This endpoint feeds:
- header status strip
- diagnostics overview
- warning banners

### GET /sensors
Use as the primary sensor snapshot source.
Expected fields:
- id
- kind
- label
- value_c
- source
- age_ms

### GET /fans
Use as the primary current fan state source.
Expected fields:
- id
- source
- rpm (optional, omitted when unavailable)
- last_commanded_pwm (optional, omitted until first write)
- age_ms

Note: fans do **not** include `label` or `kind` from the daemon. Display names come from: user alias (GUI-owned) > hwmon header label (for hwmon fans) > fan id.

### GET /hwmon/headers
Use to discover:
- header ids
- labels
- chip names
- indices
- enable support
- rpm support
- min/max PWM percentages

### GET /hwmon/lease/status
Use to show:
- whether a lease is required
- whether it is currently held
- who holds it
- TTL remaining

### GET /poll
Combined batch endpoint returning status + sensors + fans in one call.
Reduces per-cycle HTTP overhead from 3 requests to 1.
GUI falls back to individual endpoints if `/poll` is not available.

### GET /sensors/history?id=...&last=N
Returns per-sensor time-series history from the daemon's ring buffer (250 samples max).
Used to pre-fill the GUI's `HistoryStore` on first connection so the timeline chart
shows data immediately instead of starting empty.

### GET /events (SSE)
Server-Sent Events stream for real-time sensor/fan updates.
- Event type: `update` (combined sensors + fans payload)
- Heartbeat every 5 seconds
- GUI's `EventStreamService` consumes this for sub-second updates
- Polling service retained for capabilities, headers, and lease

## Write endpoints

### OpenFan writes
- `POST /fans/openfan/{ch}/pwm`
- `POST /fans/openfan/pwm`
- `POST /fans/openfan/{ch}/target_rpm`
- `POST /fans/openfan/{ch}/calibrate` — PWM-to-RPM calibration sweep

The GUI should use PWM writes for V1 control-loop behaviour unless a specific target-RPM workflow is intentionally implemented.

The calibration endpoint runs a long-running sweep (steps × hold_seconds) that sets PWM from 0→100%, reads RPM at each step, and returns a mapping. Safety: aborts on thermal limit (85°C), restores pre-calibration PWM on completion or abort.

### Hwmon lease endpoints
- `POST /hwmon/lease/take`
- `POST /hwmon/lease/release`
- `POST /hwmon/lease/renew`

### Hwmon PWM write
- `POST /hwmon/{header_id}/pwm`

### Profile activation
- `POST /profile/activate` — `{"profile_path": "/path/to/profile.json"}` or `{"profile_id": "quiet"}`
  - Daemon validates, applies, and persists active profile to `/var/lib/control-ofc/daemon_state.json`
  - Returns `{"activated": true, "profile_id": "...", "profile_name": "..."}`
  - GUI must only update "active" state after daemon confirms success
- `GET /profile/active` — returns current active profile or `{"active": false}`
  - GUI queries on connect/reconnect to reflect daemon truth
  - Prevents stale widget state from misleading user

### GPU fan writes
- `POST /gpu/{gpu_id}/fan/pwm` — `{"speed_pct": 0..100}` — set GPU fan to static speed
- `POST /gpu/{gpu_id}/fan/reset` — restore GPU fan to automatic mode (re-enables zero-RPM)

No lease required. Uses 5% minimum change threshold (DEC-070) to avoid SMU firmware churn.
Profile engine defers GPU writes when GUI is active in last 30s (DEC-071).
Daemon disables `fan_zero_rpm_enable` before writing PMFW curve, re-enables on reset (DEC-053).

### Hwmon rescan
- `POST /hwmon/rescan` — re-enumerate hwmon devices

## Error model
All errors use a standard nested envelope:

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": "any | omitted",
    "retryable": true,
    "source": "string"
  }
}
```

Error codes and HTTP statuses:
- 400 `validation_error` (source: `"validation"`, retryable: false)
- 403 `lease_required` (source: `"validation"`, retryable: false)
- 404 `not_found` (source: `"validation"`, retryable: false)
- 409 `lease_already_held` (source: `"validation"`, retryable: false)
- 409 `thermal_abort` (source: `"hardware"`, retryable: true) — calibration aborted due to high temperature
- 500 `internal_error` (source: `"internal"`, retryable: true)
- 503 `hardware_unavailable` (source: `"hardware"`, retryable: true)

The API client must normalize this into an internal error object that includes:
- endpoint
- method
- code
- message
- retryable
- source
- details (optional)
- timestamp

## Safety behaviours to respect
According to the provided daemon notes:

### OpenFan
- 0% allowed for max 8s (stop timeout queryable via `GET /capabilities` → `limits.openfan_stop_timeout_s`)
- 1–19% clamped to 20%
- duplicate writes may be coalesced

### Hwmon
- chassis: 0% or 20–100% (1–19% clamped to 20%)
- CPU/pump: 30–100% (0% rejected outright)
- first write per lease auto-sets `pwmN_enable` to manual mode
- identical writes coalesced at daemon level (DEC-073)
- lease is required
- `pwm_enable` restored to automatic (2) on daemon shutdown

### AMD GPU (PMFW)
- 0–100% accepted, no lease required
- 5% minimum change threshold to avoid SMU firmware churn (DEC-070)
- Daemon disables `fan_zero_rpm_enable` before writing PMFW curve, re-enables on reset
- Profile engine defers when GUI active (DEC-071)
- Daemon restores fan curve to automatic on shutdown

The GUI must reflect these constraints honestly.

## Recommended polling plan

### Startup sequence
1. `GET /capabilities`
2. `GET /hwmon/headers`
3. `GET /poll` (combined status + sensors + fans)
4. `GET /sensors/history?id=...` for each sensor (pre-fill timeline chart)
5. `GET /hwmon/lease/status`

### Ongoing cadence
- **Primary data (sensors/fans/status):** via SSE stream (`GET /events`) when available, with polling fallback
- **Lease status:** moderate (every poll cycle)
- **Capabilities/headers:** startup + on reconnect only

The polling service handles capabilities, headers, and lease.
The SSE `EventStreamService` handles real-time sensor/fan updates when the daemon supports it.

## Model normalisation
Define internal view-model friendly data classes for:
- CapabilitySnapshot
- StatusSnapshot
- SensorReading
- FanReading
- HwmonHeader
- LeaseState
- ApiFault

## Missing or partial data
The GUI must expect:
- missing devices
- missing rpm
- missing last commanded pwm
- unsupported categories
- stale ages
- absent write support

Do not treat these as application crashes.

## Endpoint-specific UI implications

### /capabilities drives feature gating
Examples:
- disable hwmon write controls if unsupported
- validate interval fields against reported ranges

### /hwmon/lease/status drives control messaging
Examples:
- show lease chip in header or Controls page
- disable hwmon writes if lease unavailable
- show owner hint where available

### /fans and /sensors drive control loop inputs
These are operational inputs, not just display data.

## Retry guidance
- retry read polling naturally on next cycle
- avoid aggressive write retries that could thrash hardware state
- on retryable write errors, surface the failure and let the next control cycle reconcile
- rate-limit repeated failure banners/logs

## Profile management

The daemon has a profile engine (`profile_engine.rs`) that evaluates fan curves autonomously when a profile is active. The GUI can:
- Activate a profile: `POST /profile/activate`
- Query the active profile: `GET /profile/active`
- The daemon persists the active profile to `/var/lib/control-ofc/daemon_state.json`

Profile *storage* remains GUI-owned — the daemon loads profiles from configured search directories.

## Config management

- `POST /config/profile-search-dirs` — add directories to the daemon's profile search path (persisted to daemon.toml)
- `POST /config/startup-delay` — set the daemon startup delay in seconds (persisted to daemon.toml, takes effect on next restart)
