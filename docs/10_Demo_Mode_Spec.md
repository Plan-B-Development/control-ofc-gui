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

### GUI-owned per-hardware maps are session-only in demo (DEC-227, closed by DEC-244)
`_start_demo_mode` **replaces** `AppState.fan_aliases` / `fan_zones` with
`DemoService`'s synthetic maps, and demo fan ids are deliberately realistic —
`openfan:ch00` … `openfan:ch07` are the canonical OpenFan channel ids. Any
persistence of those maps from a demo session would therefore both wipe the
user's real labels and write demo names onto their actual hardware, and
`fan_aliases` is portable (see `docs/06`), so the pollution could travel in a
shared export.

Demo is startup-only (there is no demo → live transition), so refusing the write
is sufficient; nothing needs snapshotting or restoring. Renaming a fan in demo
works for the session and is silently not saved — demo hardware is synthetic, so
persisting a name for it is meaningless, and the feature stays demonstrable for
the hardware-less tester below.

**How it is enforced (two layers, DEC-244).** DEC-227 guarded two call sites
individually and the rest silently diverged — which is how the leak below went on
to destroy a real user's chart configuration. Enforcement is no longer per-site:

1. **The file.** `MainWindow.__init__` calls `AppSettingsService.make_ephemeral()`
   when `demo_mode` is set, latching the service off disk for the whole process.
   The latch is one-way, and every page shares the one service instance, so all
   ~29 persist call sites are covered by construction. A new persist path needs
   no guard of its own and cannot reintroduce this.
2. **The in-memory object.** `_demo_blocks_persist` survives with a narrowed job:
   keeping demo ids out of `AppSettings` itself for the maps that travel in a
   portable export. `fan_aliases`, `fan_zones` and `hidden_chart_series` are the
   only hardware-keyed maps absent from `MACHINE_SPECIFIC_KEYS` (see `docs/06`),
   so they are the three that need it.

Any future GUI-owned map keyed by hardware id inherits layer 1 automatically. It
needs an explicit layer-2 decision only if it is *portable*.

**The four writers previously recorded here as OPEN are closed.** All were reached
through `AppSettingsService`, so the ephemeral latch closes the persisted-file leak
for every one:

| Writer | Key | Status |
|---|---|---|
| `dashboard_page._maybe_seed_chart_defaults` | `chart_series_seeded` | File-level only. The in-memory latch must still flip, or the seeding re-fires on every tick and fights the user's in-session chart choices. |
| `main_window._persist_series_selection` | `hidden_chart_series` | Both layers. This is the one that was actually lost — portable, so it also travelled in exports. |
| `main_window._persist_sensor_class_override` | `sensor_class_overrides` | File-level; machine-specific, so it is stripped from export anyway. |
| `sensor_series_panel._on_item_clicked` (colour picker) | `series_colors` | File-level. It calls `save()` directly rather than `update()`, which the latch covers regardless. |

One residue, deliberately left: `chart_series_seeded` is **not** in
`MACHINE_SPECIFIC_KEYS`, unlike its twin `fan_aliases_seeded`, so an export taken
during a demo session carries `true` and would suppress first-run chart seeding on
the machine that imports it. Cosmetic, and fixing it means changing export
semantics, so it is recorded rather than folded in.

## Suggested implementation approach
Create a demo service that emits the same internal models used by live mode.
Do not build a completely separate UI code path.

## Demo mode value
This mode is important because a friend/tester may not have the controller hardware.  
The app must still feel complete and testable.
