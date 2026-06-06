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
- Fan Wizard spin-down seconds
- daemon startup delay (pushed to the daemon on save and on import)
- auto-hide iGPU sensors / auto-hide unused fan headers (applied live)
- configurable data directories (profiles / themes / export)

*Removed:* "remember last active profile" — the daemon owns active-profile
persistence (`daemon_state.json`), so a GUI-side toggle controlled nothing (DEC-138).

### B. Themes
V1 requirements:
- import theme
- export theme
- select from shipped themes
- show active theme preview

~~Do not build a full advanced theme editor in V1.~~ Full theme editor implemented (`ThemeEditorWidget`) with per-token color editing, grouped controls, contrast warnings, and live preview.

**Bundled presets (DEC-109):** the GUI ships two presets in
`src/control_ofc/ui/presets/` that are copied into `themes_dir()` on
first run:

- **Default Dark** — the built-in dark palette (no JSON, defined in
  `ThemeTokens` defaults). Tightened in 1.14.0 to pass WCAG AA on
  every contrast pair the checker now evaluates.
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
Safety is daemon-owned and **not editable by the GUI**. The daemon reports `min_pwm_percent: 0` for all hwmon headers (no per-header floors). Thermal safety is temperature-triggered: 105°C → force all OpenFan and writable hwmon fans to 100% PWM, hold until temperature falls below 80°C, then apply a 60% PWM recovery floor for one cycle before resuming active control; 40% fallback if no CPU sensor for 5 cycles. GPU fans are excluded — PMFW firmware owns GPU thermal protection (DEC-130). The GUI reads safety metadata from `GET /capabilities` under `limits` and uses it for:
- curve validation (reject curves that violate floors)
- display in Controls and Diagnostics
- stale-data timeout thresholds for warning presentation
- manual override confirmation preferences

Do not present these floors as editable settings.

### D. ~~Syslog / Telemetry runtime settings~~
Syslog/telemetry settings removed (R52 de-scope).

### E. Import / export
Support:
- export GUI settings
- import GUI settings
- export theme
- import theme

Nice-to-have later:
- export/import profile packs
- export/import aliases/groups separately

## What should NOT be editable in V1 Settings
Unless or until the daemon cleanly supports them at runtime, do not surface as editable GUI settings:
- serial port path and timeout
- daemon IPC socket path
- daemon startup-only polling/publish intervals
- hardware binding details
- experimental daemon internals

## Settings ownership model

### GUI-owned settings
These belong to the GUI:
- themes
- page preferences
- chart defaults
- demo mode defaults
- aliases
- groups
- profiles
- local UI state
- per-card size overrides (`controls_card_sizes` — set via the Controls-page
  resize grips, not via a Settings control; reset per card by double-clicking
  its grip. DEC-129)

### Daemon-owned settings
These belong to the daemon runtime/config:
- capabilities
- health
- write permissions
- lease status
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
  default, numeric fields are clamped to their widget ranges (e.g. startup delay
  0–30 s, wizard spin-down 5–12 s), `card_size` is an enum, `window_geometry`
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
- **Export is portable (DEC-140):** the Settings → Export file carries only
  shareable preferences plus all profiles/themes. Machine/session state and
  hardware-id-keyed maps (`window_geometry`, `last_page_index`, data-dir
  overrides, `series_colors`, `card_sensor_bindings`, `controls_card_sizes`,
  `diagnostics_hidden_sensor_ids`, `acknowledged_kernel_warnings`) are excluded;
  `fan_aliases` and `hidden_chart_series` are kept portable. The full snapshot
  still lives in the diagnostics support bundle.
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
