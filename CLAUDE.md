# CLAUDE.md — OnlyFans GUI (Python/Qt)

## What this project is
A PySide6 (Qt6) desktop GUI for Linux that communicates with the `onlyfans-daemon` Rust service over its Unix socket HTTP API.

## Architecture
- **Dual control model** — the daemon can run in two modes:
  - **Imperative mode** (default, no profile loaded): GUI owns fan curve logic, daemon is purely imperative (set PWM/RPM now)
  - **Profile mode** (headless): daemon loads a GUI-created profile and evaluates curves autonomously. *Note: PWM write support is partial — see docs/14_Risks_Gaps_and_Future_Work.md for R18 limitations.*
- **Daemon owns thermal safety** — uses hottest CpuTemp sensor (any platform): 105°C → force 100%, hold until 80°C, recover at 60%. Forces 40% if no CPU sensor found for 5 cycles. No per-header PWM floors in daemon (`min_pwm_percent: 0` for all); safety floors are GUI-side profile constraints.
- **Communication** — HTTP over Unix domain socket at `/run/onlyfans/onlyfans.sock`
- **Discovery** — call `GET /capabilities` on startup to determine what UI to show
- **Profile selection** — CLI (`--profile name`), env (`OPENFAN_PROFILE=name`), API (`POST /profile/activate`), or persisted state (`/var/lib/onlyfans/daemon_state.json`)

## Daemon API reference
The daemon repo lives at `/home/mitch/Development/OnlyFans` (Rust workspace).

### Canonical daemon documentation
- `/home/mitch/Development/OnlyFans/daemon.md` — daemon architecture overview (module map, data flow, safety model)
- `/home/mitch/Development/OnlyFans/daemon/README.md` — build, CLI flags, environment variables
- `/home/mitch/Development/OnlyFans/docs/DEVELOPER_HANDOVER.md` — developer onboarding

## Source-of-truth hierarchy

When working in this repository, use the following priority order:

1. Current user instructions in the active conversation
2. `docs/00_README_START_HERE.md`
3. The rest of the files in `docs/`
4. `DECISIONS.md`
5. `MILESTONES.md`
6. This `CLAUDE.md`
7. `README.md`

If these conflict:
- prompt in the first instance and we will work through the rules.
- preserve the architecture boundary that GUI must only talk to daemon/API
- document any deliberate deviation in `DECISIONS.md`

## Non-negotiable architecture rules

### Absolute rule: no direct hardware access
The GUI must **never** directly access:
- serial devices
- hwmon sysfs
- USB devices
- sensors backends
- OpenFanController hardware
- motherboard fan control interfaces

All reads and writes must go through the daemon/API.

### Read endpoints (GET)
- `/capabilities` — device list, feature flags, safety limits
- `/status` — subsystem health + freshness
- `/sensors` — all temperature readings
- `/fans` — fan RPM + last commanded PWM
- `/poll` — combined status + sensors + fans in one call
- `/hwmon/headers` — controllable motherboard PWM outputs
- `/hwmon/lease/status` — lease holder + TTL
- `/sensors/history?id=...&last=N` — time-series history for a sensor entity
- `/events` — SSE stream of real-time sensor/fan updates (event: `update`)
- `/profile/active` — current active profile or `{"active": false}`

### Write endpoints (POST)
- `/fans/openfan/{channel}/pwm` — `{"pwm_percent": 0..100}`
- `/fans/openfan/pwm` — set all channels
- `/fans/openfan/{channel}/target_rpm` — `{"target_rpm": N}`
- `/fans/openfan/{channel}/calibrate` — `{"steps": 10, "hold_seconds": 5}` (long-running sweep)
- `/profile/activate` — `{"profile_id": "quiet"}` or `{"profile_path": "/path/to/profile.json"}` (switch active profile)
- `/hwmon/rescan` — re-enumerate hwmon devices and return fresh header list with capabilities
- `/hwmon/lease/take` — `{"owner_hint": "gui"}`
- `/hwmon/lease/release` — `{"lease_id": "..."}`
- `/hwmon/lease/renew` — `{"lease_id": "..."}`
- `/hwmon/{header_id}/pwm` — `{"pwm_percent": N, "lease_id": "..."}`
- `/gpu/{gpu_id}/fan/pwm` — `{"speed_pct": 0..100}` (no lease required, 5% min threshold)
- `/gpu/{gpu_id}/fan/reset` — restore GPU fan to automatic mode
- `/config/profile-search-dirs` — `{"add": ["/path/to/profiles"]}` (add dirs to search path, takes effect immediately)
- `/config/startup-delay` — `{"delay_secs": N}` (set startup delay, persisted to daemon.toml, takes effect on restart)

### Error envelope
All errors use a nested envelope: `{"error": {"code": "...", "message": "...", "retryable": bool, "source": "...", "details": ...}}`
- 400 `validation_error` (source: "validation", retryable: false)
- 403 `lease_required` (source: "validation", retryable: false)
- 404 `not_found` (source: "validation", retryable: false)
- 409 `lease_already_held` (source: "validation", retryable: false)
- 500 `internal_error` (source: "internal", retryable: true)
- 409 `thermal_abort` (source: "hardware", retryable: true) — calibration aborted due to high temperature
- 503 `hardware_unavailable` (source: "hardware", retryable: true)

## Quality gates
```bash
ruff format --check src/ tests/
ruff check src/ tests/
pytest
```

## Testing policy
- **New code must ship with tests.** Any new feature, refinement, or hardening pass must include tests in the same change set.
- **Bug fixes should include a regression test** that would fail if the bug returned.
- **High-value logic requires both success and failure-path coverage:** daemon safety, profile loading, hwmon writes, import/export, settings persistence.
- **Hardware-facing logic must use simulated fixtures** (fake sysfs trees, mock writers) — never depend on real hardware in tests.
- **GUI tests assert outcomes** (state mutation, signal emission, widget state) — not just clicks.
- **Daemon tests use pure functions where possible** — isolate logic from I/O for unit testing.
- **Tests must be deterministic** — no flaky timing, no hardware dependencies, no real network calls.

## Control loop
- `ControlLoopService` runs on a 1s QTimer, evaluates active profile curves, writes PWM
- 2°C hysteresis deadband: when temp is falling, hold PWM until temp drops 2°C below last transition
- 1% PWM write suppression: skip writes when delta from last commanded value < 1%
- `LeaseService` manages hwmon lease lifecycle: acquire → renew (30s timer) → release
- Manual override pauses curve evaluation; profile change exits override and resets hysteresis
- OpenFan writes need no lease; hwmon writes require a held lease
- Demo mode routes writes through `DemoService.set_fan_pwm()`

## Enduring ownership rules
- Curve/card previews must always be driven by curve-owned state (`CurveConfig.points`). No curve may silently depend on another curve's sensor, points, or preview.
- Each `CurveConfig` owns its own `sensor_id` and `points`. Multiple curves may reference the same sensor without coupling.
- Controls page text size is inherited from the theme — no hardcoded `font-size` on that page.

## Key constraints
- Daemon has a profile engine for headless curve evaluation; GUI also evaluates curves when connected. GUI takes priority (daemon defers when GUI active within last 30s).
- hwmon writes require a lease (60s TTL) — GUI must take, renew periodically, release on exit
- Sensor responses include `id`, `kind`, `label`, `source`, `value_c`, `age_ms`
- Fan responses include `id`, `source`, `rpm` (optional), `last_commanded_pwm` (optional), `age_ms` — no `label` or `kind`
- Fan display names: user alias (GUI-owned) > hwmon header label > fan id
- `rpm` is hardware-measured; `last_commanded_pwm` is daemon-tracked — never conflate
- Safety floors are daemon-hardcoded and read-only (not editable via API or GUI)
- Control loop must include 2°C hysteresis deadband to prevent fan oscillation
- Python >=3.12, develop on 3.14
- claude is allowed to websearch to determine best practice for tasks. or to see how others have solved the issue.

## GPU support rules
- AMD dGPU sensors report `source: "amd_gpu"` (not `"hwmon"`) — used for display grouping
- GPU identity uses PCI BDF address (stable across reboots), not hwmon index
- RDNA3+ GPUs (RX 7000/9000 series) do NOT support `pwm1_enable=1` manual mode — fan control MUST use the PMFW `fan_curve` sysfs interface
- Pre-RDNA3 GPUs (RX 6000 and older) use traditional `pwm1_enable=1` + `pwm1` control
- GPU display label: specific model if PCI device ID is recognized (e.g. "9070XT"), otherwise "AMD D-GPU"
- Daemon must reset GPU fan curves to automatic on shutdown (safety)
- Do not claim GPU fan write support unless PMFW fan_curve or hwmon pwm1 is actually available
- `amdgpu.ppfeaturemask` kernel parameter required for PMFW fan_curve access
- Exactly one fan entity per GPU — kernel exposes only fan1_input (aggregate RPM for all physical fans)
- GPU fan writes use imperative model (set_static_speed via flat PMFW curve), no lease required
- Do not adopt amdgpu-sysfs crate — LGPL-3.0 license friction, our focused code suffices (DEC-043)
- GPU fans participate in fan roles, curves, profiles, dashboard fan table, and diagnostics
- Navi 48 (RX 9070 XT/9070) has PCI device ID 0x7550, distinguished by revision (0xC0=XT, 0xC3=non-XT)
- GPU fans ALWAYS displayable on dashboard — zero-RPM idle is normal, not a disconnected header (DEC-047)
- Fan control method must be truthful: "read_only" when no write path exists (no pwm1_enable AND no PMFW)
- ppfeaturemask bit 14 (0x4000) required for PMFW — diagnostics must explain this when missing
- Daemon socket must be chmod 0666 after bind to allow non-root GUI connections (DEC-049)
- GPU fan display name: "{model} Fan" from capabilities, fallback "D-GPU Fan" (DEC-050)
- Fan wizard stop/restore must handle all source types: openfan, amd_gpu, hwmon
- Read-only GPU fans show "(read-only)" suffix in fan role member selection
- Daemon auto-disables fan_zero_rpm_enable before writing PMFW curve, re-enables on reset (DEC-053)
- GUI shows one-time zero-RPM info popup when GPU fan added to role (settings: show_gpu_zero_rpm_warning)
- GPU PMFW fan writes use 5% threshold (not 1%) to avoid SMU firmware churn during gaming (DEC-070)
- Profile engine defers GPU writes when GUI is active (last 30s) to avoid dual-writer conflict (DEC-071)
- Profile engine defers OpenFan writes when GUI is active (last 30s) — consistent across all backends (DEC-074)
- hwmon writes coalesce at daemon level: identical PWM values skip sysfs entirely, pwm_enable written once per lease (DEC-073)
- fan_zero_rpm_enable sysfs returns multi-line formatted output — must parse header+value, not just trim()

## 9. UX standards

### Dashboard
Must make it easy to answer:
- what profile is active?
- what are the fans doing?
- what are the sensors doing?
- is the system healthy?
- am I in demo/manual/disconnected state?

### Controls
Must make it easy to answer:
- what is the current profile?
- which fans are controlled by what?
- what curve is assigned?
- how do I temporarily override?
- what changes are unsaved?

### Settings
Must clearly separate:
- GUI-owned settings
- daemon runtime settings supported by API
- unsupported or startup-only daemon config

### Diagnostics
Must expose:
- connection health
- subsystem freshness
- lease state
- sensor staleness
- last known errors
- exportable support information

### Visual rules
- dark-first
- readable spacing
- visible warning hierarchy
- no neon overload
- parody branding as accent, not chaos
- avoid overusing bright blue/white if it hurts readability

---

### GUI testing rules
- all widgets must have unique objectNames
- tests must assert outcomes, not just clicks
- unknown expectations must be marked REVIEW, not guessed

## 13. How Claude should respond when building

When asked to implement or update the repo:
1. briefly state assumptions when necessary
2. identify the milestone or scope being worked on
3. avoid re-architecting the whole project unless needed
4. preserve backwards compatibility inside the repo where practical
5. keep docs updated when the architecture meaningfully changes

When uncertainty exists:
- choose the least surprising UX
- keep the code extensible
- avoid inventing missing daemon features
- use placeholders or TODOs where the backend contract is genuinely unknown

---

## 14. Milestone discipline

Claude should work in phases aligned to `MILESTONES.md`.
Do not jump straight to polishing before the foundations exist.
A sensible order is:
1. repo skeleton and app shell
2. typed API client and demo mode
3. dashboard and polling/history
4. controls and profile editing
5. GUI-owned control loop and manual override
6. settings and diagnostics
7. tests, packaging readiness, and UX refinement

---

## 15. Files Claude should keep updated when needed

When implementation changes materially, Claude should consider updating:
- `README.md`
- `DECISIONS.md`
- `MILESTONES.md`
- relevant docs under `docs/`

Do not create unnecessary duplicate documents.
Prefer focused updates over repetitive prose.

---
