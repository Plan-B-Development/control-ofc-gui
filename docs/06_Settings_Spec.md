# 06 — Settings Spec

**Last updated:** 2026-05-07 (Spec doc — updated infrequently; refer to DECISIONS.md and CHANGELOG.md for current behaviour.)

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
Suggested settings:
- default startup page
- restore last selected page
- start in demo mode when daemon unavailable
- remember last active profile locally
- chart default time range
- chart refresh behaviour preferences
- log verbosity for GUI logging if applicable

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
Safety is daemon-owned and **not editable by the GUI**. The daemon reports `min_pwm_percent: 0` for all hwmon headers (no per-header floors). Thermal safety is temperature-triggered: 105°C → force 100% PWM, hold until temperature falls below 80°C, then apply a 60% PWM recovery floor for one cycle before resuming active control; 40% fallback if no CPU sensor for 5 cycles. The GUI reads safety metadata from `GET /capabilities` under `limits` and uses it for:
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
- port fields must validate as ports
- interval fields must validate against daemon-reported ranges where available
- importing malformed config/theme files must fail clearly with recoverable messaging

## Persistence expectations
- settings changes should save predictably
- write-through to daemon settings should show success/failure state
- imported settings should offer preview/confirmation if destructive

## Theme import/export format
Use a simple, explicit text format such as JSON or TOML for V1.
It should include:
- theme name
- version
- token map
- optional metadata such as author/description

## Nice-to-have later
- advanced palette editor
- live token editor preview
- per-page density preferences
- packaging/update channel preferences
