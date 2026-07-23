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
- an AMD discrete GPU (RX 7900 XTX) with a controllable fan, plus an Intel Arc B580 and an NVIDIA RTX 4080, each with a read-only fan (the NVIDIA fan reports a `duty_pct` measurement — DEC-204)
- CPU / GPU (AMD + Intel + NVIDIA) / motherboard / NVMe disk sensors
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
- RTX 4080 Fan (NVIDIA GPU, read-only)

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

### GUI-owned per-hardware maps are session-only in demo (DEC-227)
`_start_demo_mode` **replaces** `AppState.fan_aliases` / `fan_zones` with
`DemoService`'s synthetic maps, and demo fan ids are deliberately realistic —
`openfan:ch00` … `openfan:ch07` are the canonical OpenFan channel ids. Any
persistence of those maps from a demo session would therefore both wipe the
user's real labels and write demo names onto their actual hardware, and
`fan_aliases` is portable (see `docs/06`), so the pollution could travel in a
shared export.

`MainWindow._persist_fan_alias` / `_persist_fan_zones` accordingly refuse to write
while `_demo_mode` is set. Renaming a fan in demo works for the session and is
silently not saved — demo hardware is synthetic, so persisting a name for it is
meaningless, and the feature stays demonstrable for the hardware-less tester
below. Demo is startup-only (there is no demo → live transition), so refusing the
write is sufficient; nothing needs snapshotting or restoring.

Any future GUI-owned map keyed by hardware id must make the same choice
explicitly. `hidden_chart_series`, `series_colors` and `chart_series_seeded`
currently persist from demo and share the id-collision problem at lower blast
radius (chart preferences, not names) — deliberately left for their own change.

## Suggested implementation approach
Create a demo service that emits the same internal models used by live mode.
Do not build a completely separate UI code path.

## Demo mode value
This mode is important because a friend/tester may not have the controller hardware.  
The app must still feel complete and testable.
