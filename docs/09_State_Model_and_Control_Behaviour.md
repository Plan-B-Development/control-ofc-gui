# 09 — State Model and Control Behaviour

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
The `OperationMode` enum (the status-banner **Mode**) has three values:
- automatic (the daemon engine is controlling)
- read_only — the daemon is **not connected** (the banner reads *Read-only*). It is the mode before the first successful poll and after the daemon is lost; the next successful poll returns it to *automatic*
- demo

The pre-2.0 **control gate** is not a mode. Against a daemon that does not advertise `control.autonomous_control` the GUI shows a persistent upgrade banner and offers no control, while the Mode still reads *Automatic* (see **Control authority** → gated).

A manual override is **not** a fourth mode — while one is active the banner still reads *Automatic*; the override is a per-control overlay tracked under **Control authority** below (DEC-163).

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
- coalesces writes (identical PWM skips the write; `pwm_enable` written once per lease — DEC-073; GPU PMFW uses a 5 % threshold — DEC-070), **verifying an hwmon write it coalesces**: the duty is read back and rewritten if it moved more than 2 points from what the header took after the daemon's last write (not from the command, so a coarse or clamping driver is not drift), and after 3 corrections the next tick still contradicts, the header is flagged `duty_not_holding` and left alone until the command changes or the duty holds again (DEC-406, daemon ≥ 2.53.0). Diagnostics are never reconciled, and the thermal force never coalesces
- manages the hwmon lease internally
- enforces the thermal ladder (100 % until a fresh reading at or below 80 °C, then straight back to the profile — DEC-386), whose duties **floor** overrides and curves rather than replacing them (DEC-307)

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
- the override PWM is still **floor-clamped** (pump/CPU ≥ 30 %, GPU 0 %); deliberately stopping a fan is the identify path, which is floor-exempt for ordinary fans but never stops a pump (DEC-311)
- a frozen/crashed GUI cannot strand fans — the daemon's deadman reverts to the curve when renewals stop

The override must be explicit, obvious in the Controls page, and offer a clear **Return to Automatic** action; it must never silently persist after the user thinks profile control resumed.

## Fan identify (daemon API — DEC-166)
The Fan Wizard's "change a fan to find it" flow calls `POST /fans/{id}/identify {action: "stop"|"restore"}` for every source type. It auto-restores on a deadman; only the named fan is affected — every other fan keeps curve-controlling. The old global automation freeze and raw stop/restore writes are gone.

**The daemon chooses the hold duty from whether it holds the header as a pump (DEC-311/312/384)** — its role, its own label or chip, or (DEC-384) the active profile's name for it. An ordinary fan is forced to 0 and stays floor-exempt. A pump is *perturbed* instead — shifted clear of its current duty, upward where there is headroom, and never below the 30 % pump floor. This supersedes DEC-166's "you must be able to stop a pump to find it": an audible RPM change identifies a pump just as well, and losing coolant flow to find a header is not a trade worth making. The response's `mode` says which happened; gate any pump-specific wording on `control.header_roles`, since an older daemon still stops everything.

## Lease behaviour for hwmon
**The GUI no longer holds an hwmon lease.** The daemon owns the lease lifecycle internally (its engine takes/renews it; hwmon write-verify runs under the daemon's own internal verify lease, so the GUI's `verify_hwmon_pwm` call carries no `lease_id`). The diagnostics Lease tab and the lease-status poll were removed at the cutover.

## Thermal protection (supersedes the DEC-132 GUI stand-down)
The daemon owns the thermal ladder: at the trip point it forces every OpenFan channel and writable hwmon header the machine has to 100 %, holds that until a **fresh** reading at or below 80 °C — including while the CPU sensor is stale or gone (DEC-386) — and then hands control straight back to the profile (the 60 % recovery rung was removed in DEC-386). With nothing latched it applies a 40 % floor if no CPU reading is fresh for 5 cycles, on the fans the active profile controls only (DEC-382); a control skipped that tick keeps its fans at their last duty under it. GPU fans are excluded by design (DEC-130).

**One fresh reading at or above the trip point latches, and the latch is not bounded (DEC-400).** A CPU sensor stuck in that range keeps the emergency on for as long as it reports it: by the user's decision there is no plausibility gate, maximum latch time or sibling cross-check, following IEC 61511-1 11.2.7 — a safety function that has tripped stays tripped until its reset, which here is a fresh reading at or below 80 °C. A stuck sensor fails loud, at 100 %, and is dealt with at classification, as DEC-294 did for an unconnected `CPUTIN`.

**The trip point is per-machine (DEC-308, daemon ≥ 2.26.0), not a constant.** 105 °C is the floor and the fallback. Where the kernel publishes the CPU's own design ceiling — `tempN_crit`, which `coretemp` documents as the maximum junction temperature — the daemon uses `min(ceiling + 5 °C, 115 °C)` instead. The margin exists because a part is *designed* to sit at its ceiling under sustained load: a trip point at or below the ceiling fires on a healthy machine and then latches forever, since release needs a reading at or under 80 °C that a part holding Tjmax never produces. The derivation is raise-only and capped, so no machine ever trips below 105 °C or above 115 °C. It is Intel-only in practice — `k10temp` on Zen publishes no `crit`, so AMD keeps the 105 °C floor, which is correct rather than a gap: with a ~95 °C ceiling AMD was never the broken case.

**A client must render `emergency_threshold_c` from `/diagnostics/hardware` rather than assume 105.** The daemon reports the value it actually acted on, published in the same write as `thermal_state`.

**Thermal force is a FLOOR over overrides and curves, not a replacement for them (DEC-307, daemon ≥ 2.26.0).** Each output in reach receives `max(commanded, forced)`. At 100 % the reach is every OpenFan channel and writable hwmon header, including ones no control commands — that is what gives the emergency its reach. Below 100 % — since DEC-386 only the 40 % no-sensor floor — it is only the profile's own outputs (DEC-382): a 40 % floor on a fan nothing controls would replace its firmware curve and could run it slower, so those fans are left alone, and any the emergency took are given back when it ends. So the ladder can only ever raise a fan. Before DEC-307 the forced duty replaced the profile's output, which meant the 60 % and 40 % rungs could drive a fan *down* below what its curve was asking for; the 100 % emergency was never affected, because 100 is the maximum.

The old **DEC-132 GUI stand-down** (where `ControlLoopService` paused its own writes while `thermal_state != "normal"`) is **gone** — there is no GUI loop to stand down. The GUI now uses `status.thermal_state` only to **show** a poll-driven thermal-protection banner (DEC-165), never to gate a write. `thermal_state` (`normal | recovery | emergency | no_sensor_fallback`) remains in `GET /status`.

## Sensor freshness handling
The GUI surfaces freshness for display, not for control gating (the daemon owns the conservative fallback — e.g. the no-CPU-sensor 40 % floor). If a sensor is stale or invalid the GUI should:
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
- the **thermal ladder** (no recovery rung since DEC-386) and the no-CPU-sensor fallback
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
- stop the manual-override renew timers. The GUI does **not** release an active override on close: the override outlives the GUI until the daemon's deadman lets it lapse, at most 15 s after the last renew (the daemon's `OVERRIDE_TTL_SECS`), and the curve then resumes. A crash ends the same way
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
