# 09 — State Model, Control Loop, and Lease Behaviour

## Purpose
This file defines how the V1 GUI should behave as an active control client over an imperative daemon.

## State model overview
The application should explicitly model these state axes:

### Connection state
- connected
- degraded
- disconnected

### Operation mode
- automatic
- manual_override
- read_only
- demo

### Write capability
- openfan writable
- hwmon writable with lease
- no writes available

### Profile state
- active profile
- edited profile
- unsaved changes present / absent

### Data freshness
- fresh
- stale
- invalid

## Control loop ownership
The GUI owns the active control loop in V1.

## Recommended control loop pipeline
Each cycle:
1. read latest sensor snapshot
2. read latest fan snapshot as needed
3. evaluate active profile assignments
4. interpolate target output for each target
5. clamp/validate against known rules
6. acquire/renew lease if hwmon targets are involved
7. issue writes only when necessary
8. update internal state/logging
9. publish view models to UI

## Control loop prerequisites
The control loop should only actively drive hardware when:
- daemon is reachable
- an active profile exists
- required sensors are fresh enough
- relevant write support exists
- required hwmon lease is available where applicable
- manual override is not active

If these conditions fail, the UI should explain why automatic control is degraded or paused.

## Curve evaluation
Use simple linear interpolation between the 5 configured points for V1.

### Example rule
Given:
- selected sensor temperature
- ordered list of temperature/output points

Return:
- interpolated output percentage
- clamped to applicable bounds

## Hysteresis (deadband)
V1 must include simple hysteresis to prevent fan speed oscillation near curve transition points.

### Rules
- Use a fixed **2°C deadband** around the last-acted temperature.
- When temperature is rising, evaluate the curve normally.
- When temperature is falling, only reduce fan output once the temperature drops **2°C below** the point where it last triggered an increase.
- This prevents constant ramp-up/ramp-down cycling when a sensor hovers near a curve inflection point.

### Implementation notes
- Track `last_transition_temp` per target assignment.
- The deadband applies to the sensor input, not the output percentage.
- Reset `last_transition_temp` when the active profile changes or manual override is entered/exited.
- Advanced per-curve deadband tuning is deferred to post-V1.

## Write suppression and coalescing
The GUI should avoid noisy writes.

Recommended rules:
- do not write if target output has not meaningfully changed (hysteresis deadband handles most of this)
- use a **1% PWM threshold** to suppress sub-perceptible churn
- respect daemon-side duplicate coalescing but do not rely on it for all deduplication
- slow write cadence slightly compared with read cadence if needed

## Sensor freshness handling
If a required sensor is stale or invalid:
- mark the target assignment unhealthy
- surface a warning
- do not continue blindly as if the sensor is healthy

### Conservative fallback recommendation
For V1, prefer one of these clearly-defined behaviours:
1. hold last known commanded value for a short safety window, then warn loudly
2. fall back to a safe conservative output floor
3. suspend automatic updates for the affected target and explain why

Choose one approach and apply it consistently.  
**Recommended default:** fall back to a conservative safe floor and raise a warning.

## Manual override behaviour
Manual override temporarily suspends automatic curve-driven writes for affected targets.

### Requirements
- must be explicit
- must be obvious in header and Controls page
- must have a clear **Return to Automatic** action
- must not silently remain on after the user thinks profile control resumed

## Lease behaviour for hwmon
Hwmon writes require a lease.

### Lease lifecycle
1. detect lease requirement from capabilities/status
2. acquire lease before first hwmon write
3. renew lease before expiry while active control needs it
4. release lease when no longer needed, on shutdown, or when leaving modes that require it

### Lease failure handling
If lease acquisition fails:
- show the reason/owner hint if available
- disable hwmon automatic control
- keep OpenFan control running if possible
- do not misrepresent hwmon targets as being under active control

## Mixed-source control
A profile may include targets from:
- OpenFan (no lease required)
- hwmon (lease required, 60s TTL)
- AMD GPU / PMFW (no lease required, 5% min threshold)

The control loop treats these as separate write paths with shared decision logic.

### GPU fan write behavior
- GPU fans use `POST /gpu/{gpu_id}/fan/pwm` — no lease required
- GUI-side write suppression uses the standard 1% threshold (same as OpenFan/hwmon)
- Daemon-side uses a **5% minimum change threshold** for PMFW writes to avoid SMU firmware churn (DEC-070)
- Profile engine defers GPU writes when GUI was active in last 30s (DEC-071)
- Profile engine defers OpenFan writes when GUI was active in last 30s (DEC-074)
- hwmon writes coalesce at daemon level: identical values skip sysfs, `pwm_enable` written once per lease (DEC-073)

## History retention
The GUI stores only the last **2 hours** of polling history.

### Recommended storage model
Use an in-memory ring buffer and optionally persist session snapshots only if needed.
Avoid building a heavy telemetry database for V1.

## Missing daemon-side features
The following are not currently daemon-native and must be GUI-owned or clearly unsupported:
- profile persistence
- curve storage
- alias/group persistence
- fan control mode visibility
- hardware rescan
- friendly sensor grouping
- custom safety floor tuning

## Shutdown behaviour
On app shutdown:
- stop control loop cleanly
- release hwmon lease if held
- flush GUI logs/config if needed
- leave daemon as-is; do not invent direct shutdown control of hardware

## Recommended internal classes
- `AppState`
- `ConnectionState`
- `OperationMode`
- `ActiveProfileState`
- `ControlDecision`
- `LeaseStateModel`
- `FreshnessState`
- `TargetAssignment`
- `CurveDefinition`

## Strong recommendation
Keep the control loop service headless and testable.  
It must not depend on Qt widgets.
