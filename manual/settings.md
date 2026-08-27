# Settings

The Settings page collects the application's own preferences — startup, behaviour, file locations, preferred sensors, and backup. It is a single scrolling surface of **cards** laid out in two columns; boolean options are shown as iOS-style **toggle switches**. Changes are batched: edit as many cards as you like, then click **Save Changes** in the page header to persist them all at once.

Visual appearance (themes, fonts, colours) has its own **[Theme page](#theme-page)**, and full backup/restore lives in the **Sync & Backup** card below.

> **In demo mode, nothing on this page is written to disk.** Changes apply for the
> session so you can see what they do, but the settings file is left untouched —
> demo's synthetic hardware ids match real ones, so a demo session is never
> allowed to overwrite your real configuration.

![Settings page](../screenshots/auto/06_settings.png)

## General & Startup

| Setting | Default | Description |
|---------|---------|-------------|
| **Default startup page** | Dashboard | Which page the application opens to on launch, used when "Restore last selected page" is off |
| **Restore last selected page on startup** | On | Instead of using the default, return to whichever page you were on when you last closed the app |
| **Start in demo mode when daemon is unavailable** | Off | At startup the GUI probes the daemon; if it cannot be reached (and this is on) the GUI starts in demo mode with synthetic data. A slow-to-respond daemon is treated as present. Applies at launch only — a mid-session disconnect uses the normal reconnect path |
| **Show GPU zero-RPM warning** | On | When you add a GPU fan to a fan role, show an informational popup explaining that the GPU's zero-RPM idle mode will be temporarily disabled while the curve is controlling it |
| **Chart default time range** | 15m | The initial time window shown on the Dashboard telemetry chart (choices: 30s, 2m, 5m, 10m, 15m, 20m, 30m, 1h, 2h) |

## Operational Behavior

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Fan Wizard spin-down timer** | 8 seconds | 5-12s | How long each fan is stopped during the Fan Wizard identification test. Longer gives more time to observe which fan changed |
| **Auto-hide integrated GPU sensors** | On | — | When both an integrated GPU (iGPU) and a discrete GPU (dGPU) are present, hide the less-useful iGPU temperature sensors from the Dashboard and the Overview page's sensor lists |
| **Auto-hide unused fan headers** | On | — | Hide motherboard fan headers that report 0 RPM, indicating no fan is plugged into that header |

## Path Management

These let you override where the application stores its data. Each row has a **Browse…** button and a **Reset** button (which returns it to the default XDG location). Leave blank to use the defaults (`~/.config/control-ofc/`).

| Directory | Default | Description |
|-----------|---------|-------------|
| **Profiles** | `~/.config/control-ofc/profiles/` | Where fan profile JSON files are saved. If you change this, the GUI can optionally move existing profiles to the new location |
| **Themes** | `~/.config/control-ofc/themes/` | Where custom theme files are stored |
| **Default export** | Home directory | The default save location when exporting settings or support bundles |

When you change the Profiles directory, the GUI registers the new path with the daemon so it can find profiles for headless activation — and, on `control-ofc-daemon` ≥ v2.23.0, retires the old one in the same step so the daemon's search path does not collect a dead entry every time you move the directory. The daemon's full search path is shown and editable under **Daemon Configuration** below.

## Daemon Configuration

Settings that belong to the daemon rather than to this application. Every value shown here is read from the daemon, never guessed locally, and each row says where its value came from (`set in daemon.toml`, or `set here` when a change made from this card is shadowing the admin file) and whether a daemon restart is still owed. Changes save as you make them — there is no separate Save step for this card, and moving through the card without editing anything writes nothing.

| Setting | Range | Description |
|---------|-------|-------------|
| **Startup delay** | 0-30s | Tells the daemon to wait this many seconds after boot before detecting hardware. Useful if your fan controller initialises slowly. Takes effect on the next daemon restart |
| **Poll interval** | 250-2000 ms | How often the daemon reads your hardware |
| **Serial port** | a `/dev/tty…` path | The OpenFan device path. Leave blank to auto-detect |
| **Serial timeout** | 50-1000 ms | Read timeout for the OpenFan device |
| **Super-I/O port probe** | on/off | Opt-in active chip detection. The switch is only half the requirement — it also needs a root systemd drop-in, and the row says so |
| **NVIDIA telemetry** | on/off | Opt-in read-only NVML. Same drop-in caveat |

### Profile search directories

The list of directories the daemon looks in for profile files. Unlike everything else on this card, changes here apply **immediately** — no restart.

- **Add…** registers another directory with the daemon.
- **Remove** stops the daemon looking in the selected one.

Four entries cannot be removed, and the button is disabled with a tooltip saying which rule applies rather than failing after the fact: `/etc/control-ofc/profiles` (it holds system-installed profiles); the last remaining directory (the daemon would then be unable to find any profile at all); the first entry in the list, which is the daemon's own profile store and the place it writes new profiles; and this application's own profiles directory, which is re-registered on every connect, so removing it here would appear to work and then quietly come back — change that one under **Path Management**, which retires the old registration in the same step. On a daemon older than v2.23.0, **Remove** is unavailable entirely — that version is the one that added the ability to prune an entry.

The card also shows the daemon's admin config path, its runtime config path, its socket and its state directory. Those four are deliberately read-only: a bad socket path would lock every client out of the daemon permanently.

Requires `control-ofc-daemon` ≥ v2.16.0; on an older daemon the whole card stands down rather than showing values it would have to invent.

## Preferred Sensors

If your system exposes several CPU or motherboard temperature sensors, you can pin which one the daemon treats as the reference for each. Pick a **Preferred CPU sensor** and **Preferred motherboard sensor** from the drop-downs — the daemon's own recommendation is marked with a ★, and **Automatic (recommended)** hands the choice back to the daemon's auto-pick. Selections apply immediately and are saved by the daemon (shared across every client), so there is no separate Save step for this card.

This is **advisory only**: thermal safety always uses the hottest CPU sensor regardless of your choice. The drop-downs populate from the daemon the first time you open Settings; on a daemon that predates this feature the card reports that it is unavailable. You can also set a preferred sensor straight from a sensor row on the **Overview** page (right-click → *Set as preferred…*).

Requires `control-ofc-daemon` ≥ v2.6.0.

## Resets & Maintenance

Several cards collect the things that accumulate quietly as you use the application. Each control shows a count and is disabled when there is nothing to do — the count is how you find out the state exists at all.

| Card | What it clears |
|---|---|
| **Fan Names** | Every fan name in one place, including names for hardware that is no longer plugged in. Those rows are kept on purpose: otherwise a stale name could never be removed. Renaming from the Dashboard or the Overview fan table is unchanged |
| **Sensors & Chart Series** | Hidden sensors, coolant classification overrides, custom chart colours, hidden chart series — and **Settings for missing hardware** (below) |
| **Prompts & Dismissals** | Re-arms the AIO pump popup, dismissed driver advisories, the daemon profile-import offer, and the one-time fan-name and chart-series seeding |
| **Card Layout** | Every Controls-page card size at once (resetting one card by double-clicking its grip is unchanged), and **Reset layout**, which puts every panel divider back to an even split |

### Remembered layout

Panel sizes set by dragging the dividers between sections are remembered per page, along with the chart's time range and mode, the Logs level filters and search text, and your theme. Everything here used to reset on every launch.

A restored panel is never allowed to come back collapsed to nothing, however narrow you left it. That matters most on the Dashboard, where the sensor rail is opened and closed by its divider alone — a saved zero-width rail would have left no way to get it back. **Reset layout** is the escape hatch for a saved arrangement you have simply gone off.

### Settings for missing hardware

Chart colours and hidden-series entries are stored per fan and per sensor. Swap a GPU, unplug a fan, or change a kernel that renames a sensor, and those entries stay behind — the chart quietly ignores them, but they are still in your settings file. **Remove (N)** counts them and clears them.

Nothing is removed unless you ask, and the reason is that the application genuinely cannot tell the difference between hardware that is gone and hardware that is merely switched off. A fan that is stopped, or a sensor the daemon has temporarily set aside because it cannot be read (a WiFi temperature with the radio off is the usual one), looks exactly like hardware that has been removed. Deleting those automatically would lose your colours the next time you booted with a device idle. The button is also disabled while disconnected, when the application knows of no hardware at all.

**Fan names are deliberately not included.** Use the Fan Names card for those — it is the surface built for it, and a fan's name also feeds the minimum-speed rule that protects CPU coolers and pumps, so it is not something to clear in bulk by accident.

## Sync & Backup

This card (formerly the Import/Export tab) provides full backup and restore of all application state, plus a one-click push of your local profiles into the daemon. A **backup is created automatically before any import**.

### What Gets Exported

**Export Config** writes a single **portable** JSON file with your shareable configuration:

- Application preferences (startup, general, and operational settings)
- Fan aliases and hidden chart series
- All saved profiles
- All custom themes

Machine-specific state is deliberately **excluded** so the file is safe to share or move between machines: window geometry, last page, data-directory overrides, the default export directory, per-series chart colours, card sizes and card/sensor bindings, fan-zone ordering, hidden sensor rows, sensor-class overrides, dismissed kernel warnings, and the one-time profile-import flag. A full snapshot of everything (for same-machine debugging) lives in the **Logs** page's support bundle instead.

### Import Behaviour

**Import Config** restores from a previously exported file:

1. The file is validated; a malformed or unsupported file is rejected with a clear message and nothing changes
2. A timestamped **backup** of your current settings is created automatically
3. Imported preferences are **merged** onto your current settings — your local machine-specific state (window size, data-directory overrides) is preserved, and directory overrides are applied immediately. An import never changes your daemon's own configuration: daemon settings live on the daemon and are edited in the **Daemon Configuration** card, so a config shared with you cannot reconfigure your daemon
4. Profiles from the export are written to disk (you are asked before overwriting existing ones); invalid profiles are skipped and counted
5. Custom themes are copied to your themes directory; a theme containing an invalid colour is skipped

This makes it safe to experiment — you can always restore from the auto-backup. Some preferences (theme, chart range, aliases) take effect on the next launch.

### Importing Your Profiles into the Daemon

If you run a daemon that owns its own profile store (v1.19 or newer), the
**Sync Local Profiles to Daemon** button copies your local fan profiles
(`~/.config/control-ofc/profiles/`) into the daemon so it can manage them
directly. The first time the GUI connects to a capable daemon and finds local
profiles it offers this automatically; you can also run it any time from this card.

- Your local copies are **left untouched** — the import only ever reads them.
- Profiles already in the daemon are **skipped**; choose **import as copies** to
  bring them in under a renamed copy (e.g. *Quiet (imported)*) instead.
- A profile that fails validation is **quarantined** with a reason and listed in
  the report, without stopping the rest of the import.
- Re-running is safe: profiles already imported are matched by id and skipped.

The button acts only against a daemon that advertises profile storage; against an
older daemon it does nothing.

## Theme page

The **Theme** page is a separate entry in the sidebar (not part of Settings). It controls the visual appearance of the entire application — typography, colour tokens, and contrast — and is the only place that writes theme changes.

![Theme page](../screenshots/auto/07_theme.png)

### Theme selection

A dropdown in the page header lists the built-in themes plus any custom themes saved in your themes directory, with four buttons:

| Button | Action |
|--------|--------|
| **Load** | Load the selected theme into the editor |
| **Save** | Save the current editor state as a theme file |
| **Import…** | Import a theme from an external `.json` file |
| **Export…** | Export the current theme to a `.json` file |

### Typography and cards

| Setting | Description |
|---------|-------------|
| **Font** | Choose any system font, or "(System Default)" |
| **Base Size** | Base font size from 7pt to 16pt (default 10pt) |
| **Card Size** | Density of the Fan Role and Curve cards on the Controls page: **Compact**, **Comfortable** (default), or **Large**. Cards already scale with the font size — this preference multiplies that scaling. (This control moved here from Settings.) |

### Colour editor and preview

The colour-token editor lists every theme token — backgrounds, text, accents, status indicators, borders, and the chart-series palette — grouped by purpose. Each token has a clickable colour swatch **and an editable hex field beside it**: click the swatch to pick a colour, or type a hex value directly.

Alongside the editor a live **UI Blueprint Preview** renders representative widgets in the current colours, and a **contrast diagnostics** line flags any text/background pairing that fails **WCAG AA** (reading "No contrast issues detected (WCAG AA pass)." when everything passes).

Click **Apply Theme Globally** to apply your edits live — the entire interface updates immediately.

---

Previous: [Controls](controls.md) | Next: [Diagnostics](diagnostics.md)
