# 12 — Implementation Plan and Module Structure

## Purpose
Give Claude a practical build sequence that reduces rework.

## Recommended implementation order

### Phase 1 — Foundation
Build:
- main application shell
- sidebar navigation
- global status/header strip
- theme loading
- shared view-model patterns
- API client layer
- demo mode infrastructure

Deliverable:
A navigable app shell with placeholder pages and working dark theme.

### Phase 2 — API and state wiring
Build:
- capabilities/status/sensors/fans client models
- polling service
- connection state handling
- basic disconnected/demo handling
- history buffer for charts

Deliverable:
The app can connect to the daemon, poll read endpoints, and display live summary data.

### Phase 3 — Dashboard
Build:
- summary cards
- fan status list/cards
- time-range chart
- fan visibility toggles
- warning banners

Deliverable:
A usable live dashboard and a believable demo dashboard.

### Phase 4 — Controls
Build:
- profile selector/list
- curve editor
- sensor selector
- group editor
- save/reset flows
- manual override UI shell

Deliverable:
Profiles and curves can be edited and persisted locally.

### Phase 5 — Control loop
Build:
- curve evaluation service with 2°C hysteresis deadband
- write suppression/coalescing (1% PWM threshold)
- OpenFan PWM write path
- hwmon lease service
- hwmon write path
- mode state reconciliation

Deliverable:
Automatic control works through the daemon/API.

### Phase 6 — Settings
Build:
- app settings
- telemetry runtime settings
- theme import/export
- GUI settings import/export

Deliverable:
Settings page is functional and split clearly between GUI and daemon runtime options.

### Phase 7 — Diagnostics
Build:
- health overview
- sensor/fan health tables
- lease status
- telemetry status
- recent logs
- support bundle export

Deliverable:
Diagnostics is production-usable for troubleshooting.

### Phase 8 — Polish
Build:
- loading/empty states
- unsaved change protections
- improved copy/microcopy
- keyboard polish
- error banners
- default demo data improvements

Deliverable:
The app feels coherent and test-ready.

## Suggested concrete module responsibilities

### `api/client.py`
- low-level requests
- endpoint wrappers
- timeout handling
- error envelope parsing

### `services/polling.py`
- periodic read orchestration
- publishes snapshots to stores/view models

### `services/control_loop.py`
- active profile logic
- interpolation
- write decisions
- manual override interactions

### `services/lease_service.py`
- hwmon lease acquire/renew/release
- lease health updates

### `services/history_store.py`
- 2-hour rolling time-series buffer
- `prefill_sensor()` for pre-populating from daemon history on connect

### `services/event_stream.py`
- SSE-based real-time sensor/fan updates from daemon `GET /events`
- Runs in daemon thread with exponential backoff reconnect
- Emits Qt signals (`sensors_ready`, `fans_ready`)
- Complements polling (which handles capabilities, headers, lease, telemetry)

### `services/profile_service.py`
- profile CRUD
- assignment validation
- active profile state

### `services/demo_service.py`
- synthetic models and event simulation

### `persistence/*`
- read/write JSON config objects
- schema version handling

### `ui/pages/*`
- thin page controllers + widgets
- consume typed state, not raw backend data

## Strong implementation rules
1. Build demo mode early.
2. Do not embed business logic in widgets.
3. Keep control logic unit-testable.
4. Keep API models typed and explicit.
5. Keep read-only mode and disconnected mode intentional.
6. Defer tray work.
7. Defer full theme editor.
8. Prefer a stable, boring architecture over cleverness.

## Charting recommendation
Use pyqtgraph for live telemetry-style graphs and keep chart wrappers modular so the chart implementation can be swapped later if needed.

## Packaging direction
Target a packaged Linux desktop release suitable for friend testing.
AppImage is the most practical early packaging direction.
Do not optimise packaging before the core experience works.

## Testing focus areas
- disconnected startup
- demo mode
- stale sensors
- invalid imports
- unsaved profile changes
- lease unavailable
- mixed OpenFan + hwmon targets
- telemetry config validation
- chart time-range switching
- manual override entry/exit

## Dev-quality expectations
- clear logging
- consistent naming
- no silent failures
- conservative default behaviours
- explicit TODOs for unsupported daemon gaps
