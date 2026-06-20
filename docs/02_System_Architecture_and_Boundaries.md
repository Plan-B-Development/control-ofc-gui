# 02 — System Architecture and Boundaries

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Top-level architecture

```text
+----------------------+
|   Control-OFC GUI       |
|  (PySide6 desktop)   |
+----------+-----------+
           |
           | HTTP / UDS via daemon API client
           v
+----------------------+
| OpenFan daemon / API |
+----------+-----------+
           |
           | daemon-owned hardware integration
           v
+----------------------+
| OpenFan / hwmon/etc. |
+----------------------+
```

## Absolute boundary
The GUI is an **application client** of the daemon. It is not a hardware controller.

## Why this matters
This boundary protects the design from:
- hardware-specific breakage in the GUI
- duplicated device access logic
- accidental unsafe writes outside daemon safety rules
- portability issues across Linux systems
- architecture drift as the daemon evolves

## Control ownership (2.0.0+)
The daemon is the **single source of truth for runtime control** (DEC-159, DEC-165). Its profile
engine (`profile_engine.rs`) evaluates the active profile's curves autonomously and is the **sole
writer** of every fan backend (OpenFan, hwmon, GPU PMFW); it keeps fans controlled through GUI close,
crash, or sleep. The GUI runs **no control loop** and holds **no hwmon lease** — both were deleted at
the 2.0.0 cutover, along with the 30 s `gui_active` defer window the daemon used while the GUI was the
writer (retiring DEC-071 / DEC-074 / DEC-093).

That means the GUI will:
1. poll sensor / fan / status data at 1 Hz and render it
2. author and **validate** profiles, then upload them to the daemon's profile store (DEC-160)
3. **activate** a profile for the daemon to evaluate
4. express live manual intent as an **expiring daemon override** (DEC-163) — never a direct PWM write
5. drive fan identification through the daemon **identify** API (DEC-166)
6. persist its own **UI-owned** state locally (aliases, themes, layout)

A new-GUI / old-daemon mix is refused (capability gate on `control.autonomous_control` + the package
pin `control-ofc-daemon>=2.0.0`); the GUI has no loop to fall back to. **Demo mode** is the sole
exception — it runs a GUI-side evaluator against synthetic hardware, never touching the daemon.

## Consequences of this decision
The GUI is a **viewer and controller-of-intent**, not a control authority. It is:
- a profile **editor and validator**
- an **intent** client — activate a profile; take/renew/release an expiring override; identify a fan
- a **poller / renderer** of daemon telemetry
- a persistence owner for **UI-owned** state (aliases, themes, layout, demo defaults)

It is **not** a policy engine, a control scheduler, or a PWM writer — the daemon owns all of that.

## Recommended tech stack
- **Python**
- **PySide6**
- **pyqtgraph** for live charts
- standard Qt threading/timer primitives where needed
- no embedded browser stack
- no web frontend wrapped inside Electron/Tauri/etc.

## Recommended Python target
- develop against **Python 3.14**
- require **>=3.12** as a minimum
- keep packaging friendly for testers
- avoid making testers manage a complex virtualenv setup

## Why Qt/PySide6
The app needs:
- a mature desktop widget stack
- strong Linux/KDE fit
- good dark-theme support
- predictable windowing behaviour
- model/view architecture
- clean sidebar + stacked page patterns
- native feeling on Linux desktops

## Why pyqtgraph
The app needs:
- live-updating fan and telemetry graphs
- responsive interaction
- straightforward Qt embedding
- reasonable performance on Linux desktops

## Suggested high-level module layout

```text
control_ofc/
  app/
    main.py
    application.py
    paths.py
    constants.py
  api/
    client.py
    models.py
    errors.py
  services/
    app_settings_service.py
    app_state.py
    demo_controller.py        # demo-only curve evaluator (no daemon, no hardware) — DEC-165
    demo_service.py
    diagnostics_service.py
    history_store.py
    polling.py
    profile_import_service.py  # one-time local->daemon profile import — DEC-161
    profile_service.py         # daemon-backed profile CRUD + local draft cache — DEC-160/161
    series_selection.py
  # Persistence (JSON repos, XDG paths) lives inside services/ and the
  # app_settings_service module rather than a separate persistence/ package.
  # See persistence layout details in docs/11_Persistence_Config_and_File_Layout.md.
  ui/
    main_window.py
    sidebar.py
    status_banner.py
    pages/
      dashboard_page.py
      controls_page.py
      settings_page.py
      diagnostics_page.py
    widgets/
      control_card.py          # fan role card (theme-derived size, user-resizable — DEC-128/129)
      curve_card.py            # curve card (theme-derived size, user-resizable — DEC-128/129)
      summary_card.py          # dashboard summary tiles
      curve_editor.py
      curve_edit_dialog.py
      member_editor.py         # fan-role membership editor
      fan_role_dialog.py
      fan_wizard.py            # fan-identify wizard (daemon identify API — DEC-166)
      timeline_chart.py
      sensor_series_panel.py
      series_chooser_dialog.py
      sensor_detail_dialog.py
      event_log_view.py
      error_banner.py
      readiness_report.py
      warnings_dialog.py
      theme_editor.py
      aio_config_dialog.py
      collapsible_section.py
      flow_layout.py          # Qt FlowLayout — responsive card wrapping
      draggable_flow.py       # DraggableFlowContainer — drag-to-reorder
      card_metrics.py         # shared card sizing helpers
      card_resize.py          # resize-grip support
  assets/
    ...
```

## Process model
Use a single desktop process unless a compelling reason appears otherwise.

Within that process:
- keep UI work on the main Qt thread
- keep blocking API/network work off the UI thread where necessary
- use a service layer to keep page widgets thin
- avoid tightly coupling widgets to raw API calls

## Service boundaries

### API client
Responsible for:
- endpoint calls
- request/response decoding
- error-envelope handling
- retries where appropriate
- timeouts
- transport abstraction if UDS/HTTP is variable

### Polling service
Responsible for:
- periodic reads (a single 1 Hz `QTimer` driving a worker-thread `poll()`)
- freshness tracking
- emission of updated view models
- start/stop of the whole loop (`start()`/`stop()`/`shutdown()`); it does not pause selectively per state — disconnect is detected by poll failure, and demo mode swaps in synthetic data rather than pausing this service

### Demo controller (demo mode only)
Responsible for:
- evaluating the active profile against synthetic sensors on a 1 Hz timer
- driving `DemoService` fan outputs (stateless `interpolate()` tier; Mix/Sync collapse to flat)
- mirroring manual override into the demo UI

Outside demo mode there is **no** GUI-side control loop and **no** lease service — the daemon owns
profile evaluation, write coalescing, the hwmon lease, and safe fallback. Live manual override and fan
identify are issued through the API client directly (the Controls page owns the override renew timer).

### Profile service
Responsible for:
- daemon-backed profile CRUD (pull + mirror on load; validate + upload on save) with a local draft cache
- offline fallback — local drafts when the daemon is unreachable, reconciled on reconnect
- exposing published / draft state to the UI

### Persistence layer
Responsible for:
- profiles
- theme selections
- theme imports/exports
- group definitions
- aliases
- last used app state
- demo mode defaults

## Mandatory separation rules
1. UI widgets do not parse raw API JSON directly.
2. UI widgets do not own business logic.
3. Control logic does not live inside page classes.
4. Persistence does not depend on page widgets.
5. Demo mode must reuse the same view models and page flows where possible.

## Supported operating modes

### Connected automatic mode
Daemon reachable, profile active, the daemon engine controlling.

### Connected manual override
Daemon reachable, an expiring daemon override (DEC-163) pinning one or more controls; the engine
resumes curve control automatically when the override is released or expires.

### Connected read-only
Daemon reachable, but writes unavailable or blocked.

### Disconnected
Daemon unavailable. App still runs and shows clear state.

### Demo mode
Synthetic data; fully explorable without hardware.

## Risk containment choices
- prefer conservative defaults
- clamp unsafe user input in UI before send
- still trust daemon as final safety authority
- log all failed intent attempts with context
- never hide a control-capability gate (new-GUI / old-daemon) or an override rejection
