# 09 — State Model, Control Loop, and Lease Behaviour

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
This file defines how the GUI behaves as a **viewer and controller-of-intent**. As of **2.0.0** the **daemon owns runtime control** (DEC-159, DEC-165): its profile engine evaluates the active profile and is the **sole writer** of every fan backend. The GUI runs **no control loop** and holds **no hwmon lease** when connected to real hardware — it polls, renders, and expresses intent (activate a profile, take an expiring override, identify a fan). The one exception is **demo mode**, which runs a GUI-side evaluator against synthetic hardware.

(The control loop and the hwmon lease were deleted from the GUI at the 2.0.0 cutover, along with the daemon's 30 s `gui_active` defer window — retiring DEC-071 / DEC-074 / DEC-093. This document keeps the historical title; the loop and lease it describes now live in the daemon.)

## State model overview
The application should explicitly model these state axes:

### Connection state
- connected
- degraded
- disconnected

### Operation mode
- automatic (the daemon engine is controlling)
- manual_override (an expiring daemon override is pinning one or more controls — DEC-163)
- read_only (the **control gate** is engaged: the daemon is pre-2.0 / does not advertise `control.autonomous_control`, so the GUI offers no control)
- demo

### Control authority
- daemon autonomous — daemon advertises `control.autonomous_control`; it evaluates and writes
- override active — a control is pinned by a renewable daemon override
- gated — pre-2.0 daemon; the GUI shows the upgrade banner and attempts no control (it has no loop to fall back to)
- demo — the GUI-side `DemoController` drives synthetic fans

### Profile state
- active profile (the id the daemon is currently evaluating)
- edited profile / unsaved changes present or absent
- published vs **draft** — a draft is a profile saved locally but not yet accepted by the daemon (e.g. the daemon was offline at save time)

### Data freshness
- fresh
- stale
- invalid

## Control ownership
**The daemon owns the active control loop** (DEC-159, DEC-165). The GUI does not evaluate curves or write PWM against real hardware. While connected the GUI:
1. polls `/poll` (status + sensors + fans) at 1 Hz and publishes view models to the UI
2. authors, validates, and uploads profiles to the daemon store, and activates one
3. expresses live manual intent as an expiring daemon override (never a direct write)
4. identifies fans through the daemon identify API

### What the daemon's loop does (reference)
The daemon's `profile_engine` carries the behaviour that used to live in the GUI loop — see `daemon.md` for detail. Each tick it:
- evaluates the active profile's curves (all curve types incl. Mix/Sync — schema v7)
- applies the **2 °C falling-temperature deadband** (HYSTERESIS_DEADBAND_C — mirrors the GUI's old behaviour, DEC-096)
- applies the tuning pipeline (offset, step-rate, start/stop) and per-member floors (GPU 0 %, DEC-119; pump/CPU ≥ 30 %, DEC-162)
- coalesces writes (identical PWM skips sysfs; `pwm_enable` written once per lease — DEC-073; GPU PMFW uses a 5 % threshold — DEC-070)
- manages the hwmon lease internally
- enforces the 105 / 80 / 60 °C thermal ladder, which supersedes overrides and curves

The GUI and daemon evaluators are pinned together by the shared `parity_vectors.json` golden-vector oracle (DEC-126). Post-cutover the GUI keeps only the **stateless** `curve_eval` tier of that oracle (it still has `CurveConfig.interpolate()` for demo and card previews); the daemon owns the full oracle including the stateful tuning sequence.

## Demo-mode evaluation (the only GUI-side loop)
Demo mode has no daemon and no hardware, so a GUI-side `DemoController` (`services/demo_controller.py`) animates fans:
- runs on a 1 Hz `QTimer`
- evaluates the active profile via the **stateless `interpolate()` tier only** — Mix/Sync curves collapse to a flat output (a documented demo limitation)
- writes synthetic PWM to `DemoService` and mirrors manual-override state into the demo UI

This is the sole place the GUI evaluates curves. It never runs against real hardware.

## Manual override (daemon API — DEC-163)
Live manual control is an **expiring, fencing-guarded daemon override**, not a GUI write:
- `POST /control/{id}/override` pins the control's members to a fixed PWM and returns an `override_token` + `renew_secs`
- the Controls page renews on a `QTimer` (interval from `renew_secs`, ~5 s); a **rejected renew is the expiry signal** — the card reverts to showing curve control
- on release (`DELETE`) or expiry the daemon resumes curve control automatically and resets that control's hysteresis
- the override PWM is still **floor-clamped** (pump/CPU ≥ 30 %, GPU 0 %); deliberately stopping a fan is the floor-exempt identify path
- a frozen/crashed GUI cannot strand fans — the daemon's deadman reverts to the curve when renewals stop

The override must be explicit, obvious in the Controls page, and offer a clear **Return to Automatic** action; it must never silently persist after the user thinks profile control resumed.

## Fan identify (daemon API — DEC-166)
The Fan Wizard's "stop a fan to find it" flow calls `POST /fans/{id}/identify {action: "stop"|"restore"}` for every source type. `stop` is floor-exempt (you must be able to stop a pump to find it) and auto-restores on a deadman; only the named fan is affected — every other fan keeps curve-controlling. The old global automation freeze and raw stop/restore writes are gone.

## Lease behaviour for hwmon
**The GUI no longer holds an hwmon lease.** The daemon owns the lease lifecycle internally (its engine takes/renews it; hwmon write-verify runs under the daemon's own internal verify lease, so the GUI's `verify_hwmon_pwm` call carries no `lease_id`). The diagnostics Lease tab and the lease-status poll were removed at the cutover.

## Thermal protection (supersedes the DEC-132 GUI stand-down)
The daemon owns the thermal ladder: at 105 °C it forces all OpenFan + writable hwmon fans to 100 %, holds until 80 °C, recovers at 60 %, and forces 40 % if no CPU sensor is found for 5 cycles (GPU fans are excluded by design — DEC-130). Thermal force supersedes overrides and curves.

The old **DEC-132 GUI stand-down** (where `ControlLoopService` paused its own writes while `thermal_state != "normal"`) is **gone** — there is no GUI loop to stand down. The GUI now uses `status.thermal_state` only to **show** a poll-driven thermal-protection banner (DEC-164/DEC-165), never to gate a write. `thermal_state` (`normal | recovery | emergency | no_sensor_fallback`) remains in `GET /status`.

## Sensor freshness handling
The GUI surfaces freshness for display, not for control gating (the daemon owns the conservative fallback — e.g. the no-CPU-sensor 40 % force). If a sensor is stale or invalid the GUI should:
- mark the affected reading/target unhealthy in the UI
- surface a warning
- not present a stale value as live

## History retention
The GUI stores only the last **2 hours** of polling history in an in-memory ring buffer (optionally persisting session snapshots). Avoid building a heavy telemetry database.

## GUI-owned vs daemon-owned features
What lives where as of 2.0.0:

**Daemon-owned:**
- **all runtime control** — curve evaluation (always, not only headless), hysteresis, tuning, write coalescing, and every PWM write to every backend (DEC-159, DEC-165)
- **profile storage + CRUD/validate** — `/var/lib/control-ofc/profiles/`, `GET/POST/PUT/DELETE /profiles`, `?validate_only` (DEC-160); activation via `POST /profile/activate`
- the **hwmon lease** lifecycle (internal)
- the **105 / 80 / 60 °C thermal ladder** and the no-CPU-sensor fallback
- **manual override** (DEC-163) and **fan identify** (DEC-166), each with a daemon-clock deadman
- **role-floor enforcement** — validate-time reject + eval-time clamp (DEC-162); GPU per-member floor (DEC-119)
- hardware rescan — `POST /hwmon/rescan`

**GUI-owned (by design):**
- per-machine fan aliases, group/role names, dashboard bindings, themes, window layout
- friendly sensor grouping and per-card sensor selection
- profile **authoring + validation UX**, plus a **local draft cache** with offline fallback and one-time import (DEC-161)
- **role inference + `minimum_pct` baking** — the GUI infers roles and stamps the floor the daemon then enforces (DEC-162, GUI half of DEC-095)
- **demo-mode** curve evaluation (`DemoController`)

## Shutdown behaviour
On app shutdown:
- there is no control loop to stop and no lease to release (the daemon keeps controlling regardless of the GUI's lifecycle)
- release any active manual override cleanly (the daemon's deadman is the backstop if the GUI dies)
- in demo mode, stop the `DemoController` timer
- flush GUI logs/config if needed
- leave the daemon as-is; do not invent direct shutdown control of hardware

## Recommended internal classes
- `AppState`
- `ConnectionState`
- `OperationMode`
- `ActiveProfileState`
- `FreshnessState`
- `TargetAssignment`
- `CurveDefinition`
- `DemoController` (demo-mode evaluator)

## Strong recommendation
Keep the service layer (`DemoController`, `ProfileService`, `PollingService`) headless and testable.
It must not depend on Qt widgets.
