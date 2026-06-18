# 10 — Demo Mode Spec

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
Demo mode allows the GUI to be:
- explored without hardware
- shown to other people
- tested visually on unsupported machines
- developed when the daemon/controller is unavailable

Demo mode is a product feature, not just a developer hack.

## Entry points
Users should be able to enter demo mode:
- from an explicit startup option
- from a disconnected empty state
- from Settings if desired

## Visual rules
When demo mode is active:
- show a clear Demo badge in the header
- explain that data is simulated
- prevent confusion with real hardware control

## Demo mode goals
1. Showcase the full UI structure.
2. Allow profile switching.
3. Allow curve editing.
4. Show realistic fan and sensor trends.
5. Exercise warnings and edge states.
6. Avoid requiring the daemon or hardware.

## Demo data model
Provide a believable synthetic environment. As shipped, demo mode includes:
- OpenFan present, with 8 named channels in use
- two writable hwmon headers (ITE chip: CPU Fan, CPU OPT / Pump)
- an AMD discrete GPU (RX 7900 XTX) with a controllable fan, plus an Intel Arc B580 with a read-only fan
- CPU / GPU (AMD + Intel) / motherboard / NVMe disk sensors
- built-in profiles
- realistic RPM and temperature motion over time

## Demo targets (as shipped)
- Front Intake 1 / Front Intake 2 (OpenFan)
- Rear Exhaust, Top Exhaust 1 / Top Exhaust 2 (OpenFan)
- GPU Adjacent Intake (OpenFan)
- Radiator Push 1 / Radiator Push 2 (OpenFan)
- CPU Fan, CPU OPT / Pump (hwmon)
- RX 7900 XTX Fan (AMD GPU)
- Arc B580 Fan (Intel GPU, read-only)

## Suggested demo roles
- Intake
- Exhaust
- CPU
- Radiator
- Case

## Suggested demo profiles
- Quiet
- Balanced
- Performance

## Demo behaviours
The synthetic data should:
- drift, not stay perfectly flat
- react plausibly to profile changes
- show different fan curves affecting RPM
- occasionally simulate stale sensor or warning conditions when useful
- support all dashboard time ranges

## Demo edge cases
Allow optional toggles or scripted events for:
- daemon disconnected
- stale sensor
- manual override active
- missing fan RPM
- unsupported device category

These are useful for development and screenshot/testing work.

## Demo mode restrictions
Demo mode must never:
- attempt real daemon writes
- imply real hardware safety state
- overwrite the user's real runtime settings without clear confirmation

## Suggested implementation approach
Create a demo service that emits the same internal models used by live mode.
Do not build a completely separate UI code path.

## Demo mode value
This mode is important because a friend/tester may not have the controller hardware.  
The app must still feel complete and testable.
