# 11 — Persistence, Config, and File Layout

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
Define what the GUI owns and how it should persist that data.

## GUI-owned persistent data
The GUI persists its **UI-owned** state:
- aliases/friendly names
- fan groups / role names
- theme selection
- imported/exported themes
- GUI settings
- last-used page/state
- demo mode defaults
- a **local profile draft cache** (see below) — curve definitions live inside each profile
- **saved PWM Test Reports** (DEC-404/408) — see "PWM Test Reports" below
- the report's **"Your setup" facts**, inside `app_settings.json` (see below)

## Daemon-owned state
As of 2.0.0 the daemon is the **store of record for profiles** (DEC-160). The GUI does **not** own:
- profile storage of record (`/var/lib/control-ofc/profiles/`; the GUI keeps only a local cache / drafts)
- hardware discovery truth
- daemon status truth
- runtime capability truth
- the hwmon lease (daemon-internal)
- hardware write execution truth

## Storage location strategy
Use standard Linux user paths. All data directories are configurable from
Settings → Application (stored in `app_settings.json` as `profiles_dir_override`,
`themes_dir_override`, `export_default_dir`). Empty override = use XDG default.

Recommended approach:
- config under XDG config location (default)
- state/cache/history under XDG state/cache locations
- exports under user-chosen path
- overrides applied at startup via `set_path_overrides()` before any file I/O

## Suggested file layout

```text
~/.config/control-ofc/
  app_settings.json
  themes/
    default_dark.json
    imported_theme_name.json
  profiles/
    quiet.json
    balanced.json
    performance.json
    custom_profile.json
```

As of 2.0.0 the **daemon** is the profile store of record at `/var/lib/control-ofc/profiles/`
(DEC-160). The GUI's `~/.config/control-ofc/profiles/` is now a **local draft cache**:
`ProfileService` mirrors the daemon's profiles there on load, and writes drafts there when the
daemon is offline. There is **no background auto-sync**: an offline draft is re-published only when
the user saves it again (the next `save_profile` validate-then-upload), not automatically on
reconnect. The GUI uploads and validates profiles through the daemon CRUD API rather than treating
its local copy as authoritative.

GUI runtime state currently lives almost entirely under `~/.config/control-ofc/`.
The XDG state and cache directories are created by `ensure_dirs()`. The GUI does
**not** write to the XDG **state** dir — no on-disk log, no `last_session.json`
snapshot, no `support_bundle_work/` staging directory. It **does** write to the
XDG **cache** dir — the canonical `paths.cache_dir()` (`~/.cache/control-ofc/`):
`ui/theme.py` writes a themed combo-box arrow SVG (`combo-arrow-<digest>.svg`)
there for the active theme. Support bundles are
generated on demand and written to a user-selected export location.
Aliases are persisted inside `app_settings.json` rather than a separate
`aliases.json`; fan roles and their memberships are stored inside each
profile's JSON, not as a separate `groups.json`. Per-card size
overrides for the Controls page live there too (`controls_card_sizes`,
keyed by control/curve id → `[width, height]`; pruned of ids absent from
every known profile whenever a size is saved — DEC-129).

Use platform-aware path helpers rather than hardcoding these paths.

### PWM Test Reports (DEC-404, DEC-408)

Saved reports are user **data** — a record of a measurement that cannot be re-taken — so they
live in the XDG **data** tier, not config or cache:

```text
~/.local/share/control-ofc/          # paths.data_dir()  ($XDG_DATA_HOME/control-ofc)
  reports/                           # paths.reports_dir(), created 0700 on first save
    pwm-report-<UTC>-<id>.json       # one report, compact JSON, file mode 0600
```

- **Written with `atomic_write` after every step** of a run, so a crash loses at most the step
  in flight; a file left `"state": "in_progress"` is repaired to `interrupted` (findings
  re-derived) the next time the report window opens.
- **Never deleted automatically** (D-b). The Reports page (DEC-409) lists them and deletes one
  only on request, after a confirmation, and only a `pwm-report-*.json` directly inside this
  folder (a symlink there is removed as a link, never followed).
- **Reopened with the report's own 16 MiB cap** (`store.REPORT_MAX_BYTES`), not the shared 4 MiB
  import cap (`paths.MAX_IMPORT_BYTES`): the 1 Hz trace of every fan and sensor measured ~1.5 MB
  an hour on a 19-fan / 24-sensor machine and is capped at three hours. The read stays bounded,
  so a crafted file still cannot exhaust memory. `NaN`/`Infinity` are refused at save and load.
- **Export** (DEC-409) writes JSON (byte-identical), Markdown or HTML to a user-chosen file, or
  up to three CSV files to a user-chosen folder, each with `atomic_write` (file mode 0600).
- **Open a report file…** reads a report from anywhere with the same limit and schema check and
  **never writes it into this folder**.

### "Your setup" facts (DEC-404 decision 7)

Two keys in `app_settings.json`, both **machine-specific** (in `MACHINE_SPECIFIC_KEYS`, so a
settings export never carries them and an import never applies them) and **demo-sealed** (a demo
session never writes them — demo's synthetic ids collide with real hardware):

- `hardware_notes` — per stable header id: `{connected, fans_behind, bios_mode, notes}`, the
  first and third from fixed vocabularies (`services/pwm_report/setup_facts.py`), `fans_behind`
  blank or 1–8, `notes` ≤ 500 characters, at most 64 headers. A blank entry is not stored.
- `cooler_notes` — `{model, pump_switch}`, free text, ≤ 120 characters each.

Both are USER_METADATA in a report: recorded as what the user said, never promoted to a
measurement, and "not supplied" when blank.

## Recommended V1 format
Use JSON for V1 unless TOML is already strongly preferred by the project owner.
Reasons:
- easy import/export
- easy debug
- easy schema versioning
- easy integration with Python

## Schema versioning
Every exported/imported object should include:
- schema version
- object type
- created/updated metadata where useful

### Example object types
- theme
- profile
- app_settings
- alias_map
- group_map

## Profile persistence model
A profile file should store:
- id
- name
- description optional
- assignments
- curve definitions
- active flag optional/local only
- created_at / updated_at optional
- version

## Group persistence model
Store:
- group id
- group label
- member fan ids

Each fan belongs to at most one fan role.

## Alias persistence model
Store:
- stable target id
- user-friendly name

## Theme persistence model
Store:
- theme name
- version
- token map
- author/description optional

## Import/export expectations
- theme import/export is required in V1
- GUI settings import/export is required in V1
- malformed files must fail safely and clearly
- imports should not silently destroy current config

## History retention
Only keep polling history for the last 2 hours.
This should be stored as state/cache, not as permanent configuration.

## Support bundle output
Support bundles should be exported to a user-selected location, typically as a zip file containing:
- config snapshots
- recent logs
- API snapshots
- diagnostics metadata

## Data safety principles
- write atomically where practical
- keep backups or temp files when overwriting config
- validate before saving
- ignore unknown future keys where reasonable to aid forward compatibility

### Settings write lifecycle (DEC-244)
`AppSettingsService.save()` serialises the **whole** `AppSettings` object — there is
no read-modify-write, so any single `update()` rewrites every key at once. That makes
*who is allowed to write* the load-bearing question, and three rules answer it:

1. **An unloaded service refuses to save.** `__init__` seeds a defaults object and only
   `main.py` calls `load()`, so a default-constructed service holds placeholders while
   still pointing at the real file. Saving from one replaces the user's entire config
   with defaults. It logs a warning: reaching this is a programming error, not a user
   condition.
2. **`load()` arms the service, including when the file is absent** — on a fresh install
   the defaults *are* the truth, and the first save legitimately creates the file.
3. **An ephemeral service never saves.** `make_ephemeral()` is a one-way latch used by
   demo mode (see `docs/10`). One-way because a re-armable service would put the clobber
   back within reach of a later caller.

Both refusals block the *write* only; in-memory state still updates, so the session
behaves normally and simply leaves no trace on disk.

`load()` also distinguishes **unparseable** from **unreadable**. A file that fails to
parse is renamed to `app_settings.json.corrupt` and normal saving resumes — the first
quarantine is kept and never overwritten, because after one the app writes a clean file
and a later `.corrupt` would be that generated file rather than the user's data. An
`OSError` is treated as "we could not read it", not "it is bad": the service stays
unloaded and persists nothing, so a transient I/O failure cannot cost a healthy config.
