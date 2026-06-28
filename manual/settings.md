# Settings

The Settings page manages application preferences, visual themes, and backup/restore. It has three tabs.

![Settings — Application Tab](../screenshots/auto/03_settings_application.png)

## Application Tab

### Startup

| Setting | Default | Description |
|---------|---------|-------------|
| **Default startup page** | Dashboard | Which page the application opens to on launch, used when "Restore last selected page" is off |
| **Restore last selected page on startup** | On | Instead of using the default, return to whichever page you were on when you last closed the app |
| **Start in demo mode when daemon is unavailable** | Off | At startup the GUI probes the daemon; if it cannot be reached (and this is on) the GUI starts in demo mode with synthetic data. A slow-to-respond daemon is treated as present. Applies at launch only — a mid-session disconnect uses the normal reconnect path |

### Display

| Setting | Default | Description |
|---------|---------|-------------|
| **Show GPU zero-RPM warning** | On | When you add a GPU fan to a fan role, show an informational popup explaining that the GPU's zero-RPM idle mode will be temporarily disabled while the curve is controlling it |
| **Chart default time range** | 15m | The initial time window shown on the Dashboard telemetry chart (choices: 30s, 2m, 5m, 10m, 15m, 20m, 30m, 1h, 2h) |

### Behaviour

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Fan Wizard spin-down timer** | 8 seconds | 5-12s | How long each fan is stopped during the Fan Wizard identification test. Longer gives more time to observe which fan changed |
| **Daemon startup delay** | 0 seconds | 0-30s | Tells the daemon to wait this many seconds after boot before detecting hardware. Useful if your fan controller initializes slowly. This setting is sent to the daemon and requires a daemon restart to take effect |
| **Auto-hide integrated GPU sensors** | On | — | When both an integrated GPU (iGPU) and a discrete GPU (dGPU) are present, hide the less-useful iGPU temperature sensors from the Dashboard and Diagnostics |
| **Auto-hide unused fan headers** | On | — | Hide motherboard fan headers that report 0 RPM, indicating no fan is plugged into that header |

### Data Directories

These let you override where the application stores its data. Leave blank to use the default XDG-compliant locations (`~/.config/control-ofc/`).

| Directory | Default | Description |
|-----------|---------|-------------|
| **Profiles** | `~/.config/control-ofc/profiles/` | Where fan profile JSON files are saved. If you change this, the GUI can optionally move existing profiles to the new location |
| **Themes** | `~/.config/control-ofc/themes/` | Where custom theme files are stored |
| **Default export** | Home directory | The default save location when exporting settings or support bundles |

When you change the Profiles directory, the GUI also registers the new path with the daemon so it can find profiles for headless activation.

Click **Save Application Settings** to persist all changes.

## Themes Tab

![Settings — Themes Tab](../screenshots/auto/05_settings_themes.png)

The Themes tab manages the visual appearance of the entire application.

### Theme Selection

The dropdown lists the built-in **Default Dark** theme plus any custom themes saved in your themes directory. Use the buttons to:

| Button | Action |
|--------|--------|
| **Load** | Load the selected theme into the editor |
| **Save** | Save the current editor state as a theme file |
| **Import** | Import a theme from an external `.json` file |
| **Export** | Export the current theme to a `.json` file |

### Typography

| Setting | Description |
|---------|-------------|
| **Font** | Choose from any system font, or "(System Default)" |
| **Size** | Base font size from 7pt to 16pt (default 10pt) |

### Cards

| Setting | Description |
|---------|-------------|
| **Card size** | Density of the Fan Role and Curve cards on the Controls page: **Compact**, **Comfortable** (default), or **Large**. Cards already scale automatically with the font size — this preference multiplies that scaling. Takes effect with **Apply Theme to Application** |

### Theme Editor

The colour token editor lets you customise individual colour values for backgrounds, text, accents, status indicators, and borders. Each token controls a specific aspect of the UI.

Click **Apply Theme to Application** to apply your changes live. The entire interface updates immediately.

## Import / Export Tab

![Settings — Import/Export Tab](../screenshots/auto/06_settings_import_export.png)

This tab provides full backup and restore of all application state.

### What Gets Exported

A single **portable** JSON file with your shareable configuration:

- Application preferences (startup, display, behaviour)
- Fan aliases and hidden chart series
- All saved profiles
- All custom themes

Machine-specific state is deliberately **excluded** so the file is safe to share or move between machines: window geometry, last page, data-directory overrides, the default export directory, per-series chart colours, card sizes and card/sensor bindings, fan-zone ordering, hidden diagnostics sensors, sensor-class overrides, dismissed kernel warnings, and the one-time profile-import flag. A full snapshot of everything (for same-machine debugging) lives in the Diagnostics **support bundle** instead.

### Import Behaviour

When importing:

1. The file is validated; a malformed or unsupported file is rejected with a clear message and nothing changes
2. A timestamped **backup** of your current settings is created automatically
3. Imported preferences are **merged** onto your current settings — your local machine-specific state (window size, data-directory overrides) is preserved, and directory overrides plus the daemon startup delay are applied immediately
4. Profiles from the export are written to disk (you are asked before overwriting existing ones); invalid profiles are skipped and counted
5. Custom themes are copied to your themes directory; a theme containing an invalid colour is skipped

This makes it safe to experiment — you can always restore from the auto-backup. Some preferences (theme, chart range, aliases) take effect on the next launch.

### Importing Your Profiles into the Daemon

If you run a daemon that owns its own profile store (v1.19 or newer), the
**Import local profiles into daemon…** button copies your local fan profiles
(`~/.config/control-ofc/profiles/`) into the daemon so it can manage them
directly. The first time the GUI connects to a capable daemon and finds local
profiles it offers this automatically; you can also run it any time from this tab.

- Your local copies are **left untouched** — the import only ever reads them.
- Profiles already in the daemon are **skipped**; choose **import as copies** to
  bring them in under a renamed copy (e.g. *Quiet (imported)*) instead.
- A profile that fails validation is **quarantined** with a reason and listed in
  the report, without stopping the rest of the import.
- Re-running is safe: profiles already imported are matched by id and skipped.

The button appears only when the daemon advertises profile storage; against an
older daemon it does nothing.

---

Previous: [Controls](controls.md) | Next: [Diagnostics](diagnostics.md)
