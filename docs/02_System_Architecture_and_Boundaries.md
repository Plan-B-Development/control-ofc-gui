# 02 — System Architecture and Boundaries

## Top-level architecture

```text
+----------------------+
|   OnlyFans GUI       |
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

## V1 control ownership decision
The daemon is currently imperative and does not provide a curve/profile engine. Therefore V1 must implement a **GUI-owned control loop**.

That means the GUI will:
1. poll sensor data
2. evaluate the active profile's curve rules
3. decide the desired PWM outputs
4. write those outputs through daemon endpoints
5. respect hwmon lease requirements
6. persist all GUI-owned profile state locally

## Consequences of this decision
The GUI is not just a passive dashboard. It is also:
- a policy engine
- a scheduler/timer owner
- a state reconciler
- a persistence owner for profiles/groups/themes

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
onlyfans/
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
    polling.py
    control_loop.py
    history_store.py
    profile_service.py
    theme_service.py
    diagnostics_service.py
    demo_service.py
    lease_service.py
    export_service.py
  persistence/
    config_repo.py
    profiles_repo.py
    themes_repo.py
    aliases_repo.py
    state_repo.py
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
      fan_card.py
      sensor_badge.py
      health_chip.py
      curve_editor.py
      group_editor.py
      timeline_chart.py
      log_viewer.py
      empty_state.py
      flow_layout.py          # Qt FlowLayout — responsive card wrapping
      draggable_flow.py       # DraggableFlowContainer — drag-to-reorder
      curve_card.py            # 220×160 fixed-size curve card
      control_card.py          # 260×180 fixed-size fan role card
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
- periodic reads
- freshness tracking
- emission of updated view models
- selective pause/resume in demo/manual/disconnected states

### Control-loop service
Responsible for:
- active profile evaluation
- curve interpolation
- write throttling/coalescing
- manual override state
- safe fallback when prerequisites fail

### Lease service
Responsible for:
- acquiring hwmon lease
- renewing lease
- releasing lease
- exposing lease state to UI and control loop

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
Daemon reachable, profile active, control loop running.

### Connected manual override
Daemon reachable, manual override active, control loop suspended or bypassed.

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
- log all failed write attempts with context
- never hide lease conflicts
