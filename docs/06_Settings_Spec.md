# 06 — Settings Spec

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
Settings manages:
- app-level preferences
- safe daemon-exposed runtime settings
- theme import/export
- GUI config import/export
- global safety preferences
- demo mode defaults

It should not become a dumping ground for operational controls that belong in Controls.

## Page sections

### A. Application
Implemented settings:
- default startup page — honoured on launch when "restore last selected page" is off (F3)
- restore last selected page
- start in demo mode when daemon unavailable — **launch-only**: the daemon is
  probed once at startup (`GET /status`, ~1.5 s) and demo mode is entered if it
  is unreachable; a mid-session disconnect keeps the normal reconnect path (DEC-139)
- chart default time range — labels are driven by the chart's own `TIME_RANGES`,
  so the Settings label always matches what the dashboard opens (F6)
- GPU zero-RPM warning toggle
- AIO pump info toggle — the one-time "constant-speed pump" popup (DEC-157).
  Dismissing it flips the key to `false`; before DEC-237 nothing could flip it
  back, unlike its GPU counterpart directly above.
- Fan Wizard spin-down seconds
- auto-hide iGPU sensors / auto-hide unused fan headers (applied live)
- configurable data directories (profiles / themes / export)

*Removed:* "remember last active profile" — the daemon owns active-profile
persistence (`daemon_state.json`), so a GUI-side toggle controlled nothing (DEC-138).

Added in DEC-237 — mirror and reset surfaces for settings authored elsewhere:
- **Fan Names** — every fan name in one table, including names for hardware that
  is no longer present (otherwise unclearable). Renaming in place from the
  Dashboard and the Overview fan table is unchanged; this is an *additional*
  surface. Edits route through `AppState.apply_fan_rename`, never through
  `settings_service.update(fan_aliases=…)` — the `fan_alias_changed` signal is
  what persists them, and the settings service seals `fan_aliases` for the whole
  demo session (DEC-244 — the per-site `MainWindow._demo_blocks_persist` guard
  this used to name was removed in favour of that single mechanism). Demo fan
  ids collide exactly with real hardware ids, so the table is read-only in demo
  mode. DEC-247: on the first fan poll, an alias whose hwmon id no longer names a
  live fan is re-keyed onto the header with the same `(chip, pwm index)` when that
  match is unique (`services/id_migration.py`) — those two parts are what a driver
  relabel leaves alone. Only hwmon ids embed a label, so `openfan:chNN` and GPU BDF
  ids are never re-keyed.
- **Sensors & Chart Series** — reset hidden sensors, coolant classification
  overrides, custom series colours, hidden chart series. Classification resets
  route through `AppState.set_sensor_class_override` so the Overview table
  re-renders; "show all series" calls `SeriesSelectionModel.select_all()` so the
  live chart updates rather than only the stored key list.
  Also **"Settings for missing hardware"** (DEC-246): clears `series_colors` and
  `hidden_chart_series` entries whose entity the daemon no longer reports. The rule
  is the Qt-free `services/orphan_prune.py`; it returns *nothing* when the live key
  set is empty (disconnected, or pre-first-poll), because otherwise "remove what the
  daemon did not mention" deletes everything. Quarantined sensors (DEC-193) count as
  known — they are evicted from `sensors` and reported separately, so omitting them
  would drop a WiFi temperature's colour whenever its radio is off. `fan_aliases` is
  deliberately out of scope: see the Fan Names card above, and the `member_label`
  safety-floor path.
- **Prompts & Dismissals** — re-arm `show_aio_pump_info`, clear
  `acknowledged_kernel_warnings`, re-offer the daemon profile import, re-run the
  fan-alias and chart-series seeding latches.
- **Card Layout** — bulk reset of `controls_card_sizes` (per-card reset by
  double-clicking a grip is unchanged, DEC-129).

Reset controls show the count they would affect and disable at zero: an
always-enabled reset button cannot tell you whether there is anything to reset.

*Coverage is enforced, not documented.* `SETTINGS_FIELD_WIDGETS` in
`settings_page.py` maps each exposed `AppSettings` field to the objectName of the
widget that edits or resets it. `tests/test_settings_coverage_dec237.py` asserts
that map plus the Theme page's fields plus an explicitly-justified implicit list
accounts for *every* dataclass field, and that each named widget exists on a
constructed page. A new preference fails the suite until it is given a home.

*Persisted but deliberately not surfaced:* `fan_zones` (Dashboard fan-zone
layout, DEC-176/187) is **dormant since DEC-222** — the Dashboard surfaces that
wrote it were removed, and the key is retained unread so no settings-schema
migration is needed and no saved zone assignments are lost. It must not gain a
control. (Its companions `fan_zone_order`, `fan_zones_collapsed`,
`card_sensor_bindings` and `show_hardware_guidance` were **fully dropped in
DEC-224 (v3)** as written-never-read keys.) `version`, `window_geometry` and
`last_page_index` are session/schema state with no meaningful control. All still
round-trip through `AppSettings.from_dict`/`to_dict` and the import/export trust
boundary.

### B. Themes
**Now its own page (DEC-215):** theming moved out of Settings into a top-level **Theme** sidebar page. The V1 requirements and preset notes below still hold — they now describe that Theme page, not a Settings sub-section.

V1 requirements:
- import theme
- export theme
- select from shipped themes
- show active theme preview

~~Do not build a full advanced theme editor in V1.~~ Full theme editor implemented (`ThemeEditorWidget`) with per-token color editing, grouped controls, contrast warnings, and live preview.

**Bundled presets (DEC-109):** the GUI ships three preset JSON files in
`src/control_ofc/ui/presets/` — `classic_blue.json`, `noctua_dark.json`,
`solar_light.json` — copied into `themes_dir()` on first run. (The built-in
**Default Dark** palette needs no JSON — it is defined in `ThemeTokens`
defaults, tightened in 1.14.0 to pass WCAG AA on every contrast pair the
checker evaluates.)

- **Classic Blue** — cool-blue accent on a dark charcoal base.
- **Solar Light** — neutral GitHub-style light theme.
- **Noctua Dark** — Noctua beige/brown on near-black charcoal,
  inspired by the iconic NF-A14 colour scheme. Primary button text is
  dark on the beige accent to keep contrast.

All three meet the project's **WCAG 2.1 AA contrast target** (see
`docs/03_UX_UI_Principles_and_Visual_System.md`). A user-edited preset
is preserved across launches — the first-run copy is skipped when the
target file already exists.

**Persisted theme is restored at startup.** `AppSettings.theme_name`
is read in `main.py` and the matching JSON file in `themes_dir()` is
applied before the main window is shown. If the persisted name does
not match any installed theme the GUI falls back to Default Dark and
logs the miss.

### C. Safety display
Safety is daemon-owned and **not editable by the GUI**. The daemon reports `min_pwm_percent: 0` for all hwmon headers (no per-header floors). Thermal safety is temperature-triggered: 105°C → force all OpenFan and writable hwmon fans to 100% PWM, hold until temperature falls below 80°C, then apply a 60% PWM recovery floor for two cycles (the release cycle and one more) before resuming active control; 40% fallback if no CPU sensor for 5 cycles. GPU fans are excluded — PMFW firmware owns GPU thermal protection (DEC-130). The GUI reads safety metadata from `GET /capabilities` under `limits` and uses it for:
- curve validation (reject curves that violate floors)
- display in Controls and on the System State page
- stale-data timeout thresholds for warning presentation
- manual override confirmation preferences

Do not present these floors as editable settings.

### D. ~~Syslog / Telemetry runtime settings~~
Syslog/telemetry settings removed (R52 de-scope).

### E. Import / export
**Now the Settings page's Sync & Backup card (DEC-215):** import/export is no longer a separate Settings section — it is folded into the **Sync & Backup** card on the Settings page. The behaviour below is unchanged.

Support:
- export GUI settings
- import GUI settings
- export theme
- import theme

Nice-to-have later:
- export/import profile packs
- export/import aliases/groups separately

## What should NOT be editable in Settings

**Amended by DEC-243.** The original rule read "unless or until the daemon
cleanly supports them at runtime, do not surface as editable: serial port path
and timeout, daemon IPC socket path, daemon startup-only polling/publish
intervals, hardware binding details, experimental daemon internals."

The precondition is now met for three of those. ADR-002 (daemon) established
`runtime.toml` — daemon-owned, overlaying the admin-owned `daemon.toml`, written
only through `POST /config/*` — so the daemon *does* cleanly support mutating
them, and it does so without a privileged helper. DEC-243 therefore surfaces
**serial port**, **serial timeout** and **poll interval** as editable on the
Daemon Configuration card, each labelled with its source and a restart-required
state (`GET /config` reports both).

**Extended by DEC-285.** The card is now the home of *every* daemon key the
daemon reports as `mutable: true`, and that completeness is enforced rather than
intended — `tests/test_daemon_config_coverage.py` fails if a mutable key has no
control, has one that does not exist on the page, or has one whose write is not
wired. Two keys moved under that rule:

- **`startup.delay_secs`** was on the Operational Behavior card, driven by an
  `AppSettings` mirror and POSTed unconditionally on Save and on Import. It is a
  daemon-owned key, so it belongs here, behind the same no-op-write guard as its
  siblings; the mirror was deleted (settings schema v4). The old arrangement was
  not merely untidy: pressing Save once wrote the key into `runtime.toml` and
  permanently shadowed the operator's `daemon.toml` with a value nobody chose.
- **`profiles.search_dirs`** had no surface at all. It gets a real list editor
  (Add / Remove) rather than a single-value row, and it is the one key that
  applies **live**, so the card renders the daemon's `running_value` rather than
  its on-disk `value`.

Still **not** editable, and not merely for want of daemon support:

- **daemon IPC socket path** — a bad value permanently locks every client,
  including the GUI writing it, out of the daemon. Displayed read-only.
- **daemon state directory** — moving it orphans `runtime.toml` and the
  daemon-owned profile store. Displayed read-only.
- **hardware binding details** — the daemon is the authority on what hardware
  exists; the GUI never pins it.
- **experimental daemon internals** — no stable contract to edit against.

The two `[detection]` opt-ins (`allow_port_probe`, `enable_nvidia_telemetry`)
are editable **but explicitly incomplete**: each also needs a root-installed
systemd drop-in that no API can install. The card must show the outstanding
requirement and must never present the feature as enabled on the strength of the
config flag alone.

Safety floors remain non-editable and daemon-owned (see § C above) — DEC-243
does not touch them.

## Settings ownership model

### GUI-owned settings
These belong to the GUI:
- themes
- page preferences
- chart defaults
- demo mode defaults
- aliases
- groups
- local UI state
- a local profile **draft cache** (the daemon is the profile store of record — DEC-160)
- per-card size overrides (`controls_card_sizes` — set via the Controls-page
  resize grips, not via a Settings control; reset per card by double-clicking
  its grip. DEC-129)

### Daemon-owned settings
These belong to the daemon runtime/config:
- capabilities
- health
- write permissions
- profile store of record (DEC-160)
- hardware availability

## Settings UX rules
- group settings by intent, not by raw backend structure
- keep labels human-readable
- explain risky settings briefly
- show whether a setting is applied immediately or on next session
- separate GUI settings from daemon settings visually

## Validation rules
- interval fields must validate against daemon-reported ranges where available
- importing malformed config/theme files must fail clearly with recoverable messaging
- **`AppSettings.from_dict` is the trust boundary (DEC-137):** it never raises.
  Every field is type-checked and coerced — wrong types fall back to the field
  default, numeric fields are clamped to their widget ranges (e.g. wizard
  spin-down 5–12 s), `card_size` is an enum, `window_geometry`
  must be four sane ints, and `series_colors` keeps only valid hex entries. A
  non-dict payload yields all-defaults rather than a crash on the next launch.
- **Theme tokens are hex-only (DEC-142):** colour tokens (and every
  `chart_series` entry) must match `#RGB`/`#RGBA`/`#RRGGBB`/`#RRGGBBAA`;
  `base_font_size_pt` is clamped to 7–16 and `font_family` coerced to a string.
  Loading an on-disk theme drops invalid tokens to the default; *importing* a
  theme rejects the whole theme if any colour is invalid (skip-and-warn).

## Persistence expectations
- settings changes should save predictably
- write-through to daemon settings should show success/failure state
- **Import auto-backs-up first:** the current `app_settings.json` is copied to
  `config/backups/` before an import is applied.
- **Export is portable (DEC-140):** the Settings page's Sync & Backup Export file carries only
  shareable preferences plus all profiles/themes. Machine/session state and
  hardware-id-keyed maps (`window_geometry`, `last_page_index`, data-dir
  overrides, `series_colors`, `controls_card_sizes`,
  `diagnostics_hidden_sensor_ids`, `sensor_class_overrides`,
  `acknowledged_kernel_warnings`, `fan_aliases_seeded`, `daemon_import_prompted`) are excluded — the
  authoritative set is `MACHINE_SPECIFIC_KEYS` in `app_settings_service.py`;
  `fan_aliases`, `fan_zones`, and `hidden_chart_series` are kept portable. The
  full snapshot still lives in the diagnostics support bundle.
  Because `fan_aliases` is portable it can travel to another machine, which is
  why demo mode must never write to it — demo's synthetic fan ids collide exactly
  with real ones (`openfan:ch00` …), so a demo-authored name would otherwise
  overwrite a real fan's name and then be exported (DEC-227). `fan_aliases` is
  authored from the Dashboard Sensors rail, the read-only fan cards and the
  Overview fan table as well as the Fan Configuration Wizard; values are capped at
  64 characters.
- **`fan_aliases_seeded`** (DEC-228) — set once the GUI has adopted profile
  `member_label`s into `fan_aliases`. **Machine-specific** (it records what this
  install has already done, not a preference), so it is in `MACHINE_SPECIFIC_KEYS`
  and excluded from portable export. Without it, an alias the user deliberately
  clears would be re-seeded from the profile on the next launch and could never be
  removed — the same reasoning as `chart_series_seeded` (DEC-181).
- **Import merges, preserving local machine state:** imported values overlay the
  current settings, and machine-specific keys are stripped from the incoming
  data, so importing a shared (or legacy full) file never moves your window or
  wipes your local data-dir overrides.

## Theme import/export format
Use a simple, explicit text format such as JSON or TOML for V1.
It should include:
- theme name
- version
- token map (colour tokens are hex strings — `#RGB`/`#RGBA`/`#RRGGBB`/`#RRGGBBAA`;
  `base_font_size_pt` is an int clamped to 7–16; see Validation rules)
- optional metadata such as author/description

## Nice-to-have later
- advanced palette editor
- live token editor preview
- per-page density preferences
- packaging/update channel preferences
