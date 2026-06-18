# 12 — Implementation Plan and Module Structure

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

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

> **Superseded at 2.0.0 (DEC-165):** this GUI-owned control loop, the hwmon lease service, and the
> PWM write paths were **deleted**. The daemon's profile engine now owns curve evaluation, write
> coalescing, the hwmon lease, and all writes; the GUI retains only a demo-mode evaluator
> (`demo_controller.py`). Live manual control is a daemon override (DEC-163), fan identify a daemon
> call (DEC-166).

### Phase 6 — Settings
Build:
- app settings
- daemon runtime settings (startup delay, profile search dirs)
- theme import/export
- GUI settings import/export

Deliverable:
Settings page is functional and split clearly between GUI and daemon runtime options.

### Phase 7 — Diagnostics
Build:
- health overview
- sensor/fan health tables
- lease status
- hardware readiness report
- recent logs / event log
- support bundle export

Deliverable:
Diagnostics is production-usable for troubleshooting.

### Phase 8 — Polish
Build:
- loading/empty states
- unsaved change protections
- improved status/error text
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

### `services/demo_controller.py` (demo mode only)
- evaluates the active profile against synthetic sensors on a 1 Hz timer
- stateless `interpolate()` tier only (Mix/Sync collapse to flat)
- drives `DemoService`; mirrors manual-override into the demo UI

> The GUI's real control loop (`control_loop.py`) and hwmon lease service (`lease_service.py`) were
> **removed at the 2.0.0 cutover** (DEC-165). The daemon owns curve evaluation, write coalescing,
> the hwmon lease, and all PWM writes; live manual override (DEC-163) and fan identify (DEC-166) are
> issued through `api/client.py`.

### `services/history_store.py`
- 2-hour rolling time-series buffer
- `prefill_sensor()` for pre-populating from daemon history on connect

### `services/profile_service.py`
- daemon-backed profile CRUD (pull/mirror on load; validate + upload on save) — DEC-160/161
- a local draft cache with offline fallback (drafts when the daemon is unreachable)
- assignment validation; active profile state

### `services/demo_service.py`
- synthetic models and event simulation

### Persistence
Persistence is owned by the individual `services/*_service.py` files
(`app_settings_service`, `profile_service`, `series_selection`, etc.).
Each service uses `paths.atomic_write` for crash-safe JSON persistence
and owns its own schema-version handling. There is no separate
`persistence/` package.

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
7. Defer full theme editor. *(Superseded — it shipped later: Settings → Themes per-token editor with contrast checking.)*
8. Prefer a stable, boring architecture over cleverness.

## Charting recommendation
Use pyqtgraph for live sensor/RPM charts and keep chart wrappers modular so the chart implementation can be swapped later if needed.

## Packaging direction
Ship as an AUR package — `packaging/PKGBUILD` builds the Python wheel,
installs the systemd unit, desktop entry, manpage, and bash completion.
The companion daemon ships from `control-ofc-daemon/packaging/PKGBUILD`
and both AUR sources live under `/home/mitch/Development/aur/`.

## Testing focus areas
- disconnected startup
- demo mode
- stale sensors
- invalid imports
- unsaved profile changes
- lease unavailable
- mixed OpenFan + hwmon targets
- profile activation / deactivation
- chart time-range switching
- manual override entry/exit

## Dev-quality expectations
- clear logging
- consistent naming
- no silent failures
- conservative default behaviours
- explicit TODOs for unsupported daemon gaps
