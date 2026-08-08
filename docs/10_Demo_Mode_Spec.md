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

**How it is enforced (DEC-244).** DEC-227 guarded two call sites individually and
the other two silently diverged — which is how the leak below went on to destroy a
real user's chart configuration. Enforcement is no longer per-site, and there is
deliberately **only one copy of the rule**:

`MainWindow.__init__` calls `AppSettingsService.make_ephemeral()` when
`demo_mode` is set. The service then drops every key in `_DEMO_SEALED_KEYS` from
`update()` — from the in-memory object, not merely at the disk boundary, because
a Settings export serialises `portable_dict()` off the live object and a key that
reached memory could still travel. The latch is one-way, and every page shares the
one service instance, so all ~29 persist call sites are covered by construction.
A new persist path needs no guard of its own.

The per-site `MainWindow._demo_blocks_persist` helper was **removed** with this
change rather than kept as belt and braces. A second, partial copy of the rule is
what made DEC-227's gap invisible, and mutation testing showed it also masked
whether the surviving guard worked: with the helper in place, disabling the
service seal left the whole demo suite green.

**The seal is scoped, not total.** It covers hardware-derived state only:
`fan_aliases`, `fan_zones`, `hidden_chart_series`, `series_colors`,
`sensor_class_overrides`, `controls_card_sizes`, and `chart_series_seeded`.
Ordinary preferences — theme, startup page, card size, window geometry,
`demo_on_disconnect`, the directory overrides — save normally even in demo.
Sealing everything was tried first and was wrong twice over:

- **It trapped the user.** Dropped into demo involuntarily by `demo_on_disconnect`
  (see DEC-139) because the daemon was down, they could not turn that setting off:
  the write was dropped, Settings reported success, and the next launch was demo
  again. No in-app escape.
- **It stranded profiles.** `SettingsPage._handle_dir_change` physically
  `shutil.move`s profile files into a newly chosen directory *before* saving the
  override. With the override write dropped, the next launch looked in the old
  location and every saved profile vanished from the GUI.

`chart_series_seeded` is not hardware-keyed but is sealed anyway: it is a one-way
"this install has been seeded" latch, so a demo session flipping it would
permanently consume the real user's first-run chart seeding (DEC-181). Because the
persisted flag therefore never flips in demo, `DashboardPage` keeps a session-local
mirror — without it the seeding re-fires on every 1 Hz tick and stamps the curated
subset back over whatever the user selected.

**The four writers previously recorded here as OPEN are closed**, all through that
single mechanism:

| Writer | Key |
|---|---|
| `dashboard_page._maybe_seed_chart_defaults` | `chart_series_seeded` |
| `main_window._persist_series_selection` | `hidden_chart_series` — the one that was actually lost |
| `main_window._persist_sensor_class_override` | `sensor_class_overrides` |
| `sensor_series_panel._on_item_clicked` (colour picker) | `series_colors` |

(An earlier revision of this table claimed the colour picker "calls `save()`
directly rather than `update()`". It does not — `sensor_series_panel.py` copies the
map, sets the key, and routes through `update()`, with a comment explaining that
mutating in place would bypass `_as_color_dict` validation.)

## Suggested implementation approach
Create a demo service that emits the same internal models used by live mode.
Do not build a completely separate UI code path.

## Demo mode value
This mode is important because a friend/tester may not have the controller hardware.  
The app must still feel complete and testable.
