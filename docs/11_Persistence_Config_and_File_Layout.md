# 11 — Persistence, Config, and File Layout

## Purpose
Define what the GUI owns and how it should persist that data.

## GUI-owned persistent data
The GUI must persist:
- profiles
- curve definitions
- fan groups
- aliases/friendly names
- theme selection
- imported/exported themes
- GUI settings
- last-used page/state
- demo mode defaults

## Daemon-owned state
The GUI does **not** own:
- hardware discovery truth
- daemon status truth
- runtime capability truth
- lease truth
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
  aliases.json
  groups.json

~/.local/state/control-ofc/
  gui.log
  last_session.json
  support_bundle_work/
```

Use platform-aware path helpers rather than hardcoding these paths.

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

A fan can belong to multiple groups.

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
