# 07 — Diagnostics Spec

## Purpose
Diagnostics helps the user understand:
- whether the daemon/API is reachable
- whether controllers are available
- whether sensors are fresh
- whether writes are possible
- what the last errors were
- what can be exported for support/debugging

This page must feel intentionally designed, not like a raw log dump.

## V1 diagnostic sections

### 1. Overview
Summary cards for:
- overall daemon status
- OpenFan availability
- hwmon availability
- hwmon lease state
- last error summary

### 2. Connection and daemon health
Show:
- daemon version
- API version
- IPC transport
- overall status
- subsystem freshness/age
- health reasons if provided

### 3. Controller and device discovery
Show:
- OpenFan present / absent
- channel count
- write support
- hwmon present / absent
- discovered controllable headers
- whether RPM support is available

### 4. Sensor health
Show a list/table of sensors with:
- label
- kind
- current value
- age/freshness
- status
- issue text for stale/invalid cases

### 5. Lease state
If hwmon requires a lease, show:
- lease required
- held / not held
- owner hint
- TTL remaining
- whether GUI writes are currently possible

### 6. Logs and events
Provide a readable log/event view for:
- recent app events
- recent API failures
- validation errors
- profile/control loop warnings
- write denials/clamps
- lease failures

## Required user actions
- Reload config
- Reconnect controller
- Export support bundle
- Copy last errors

## Action behaviour notes

### Reload config
This should reload GUI-owned config first.
If the daemon does not expose a runtime reload endpoint, do not fake a daemon config reload. Instead:
- reload local config
- optionally refresh/poll all known read endpoints
- explain what was and was not reloaded

### Reconnect controller
If the daemon does not expose a rescan/reconnect endpoint:
- refresh status
- explain that device rediscovery may require daemon restart
- optionally provide a user-facing note to that effect

### Export support bundle
Create a structured bundle including:
- GUI settings
- active profile
- profile set
- theme info
- current daemon status snapshots
- capabilities snapshot
- sensor snapshot
- fan snapshot
- recent GUI logs
- system/environment summary useful for Linux debugging

### Copy last errors
Should copy a concise but useful text summary, not an unreadable blob.

## Diagnostics UX rules
- use color for severity, but do not rely on it alone
- keep critical information high on the page
- use expandable detail regions for large raw payloads/logs
- allow copying key blocks easily
- timestamps should be consistent and readable

## Warnings to surface explicitly
- daemon unreachable
- lease unavailable
- stale sensor data
- write support disabled
- unsupported device categories
- demo mode active

## Implementation: Latency semantics (R34)

### What age_ms means
The `age_ms` values shown in the Overview subsystems area are **daemon-side cache staleness**: time since the daemon's polling loop last successfully read data from that hardware subsystem. They are computed in `staleness.rs` as `Instant::now() - last_subsystem_update`.

### Why subsystem ages differ
- **OpenFan** (serial I/O): Each poll cycle involves serial send + wait + parse over USB. Typical latency 100-500ms per cycle.
- **hwmon** (sysfs): Each poll reads files under `/sys/class/hwmon/`. Typical latency ~1ms.
These differences are **expected behavior**, not a bug. The GUI poll cycle (1000ms) adds an additional 0-1000ms of staleness that is not reflected in the daemon's `age_ms` value.

### Display rules
- Show subsystem `reason` text from daemon alongside age (e.g., "readings fresh", "readings stale")
- Include an explanatory note: "Age = time since daemon last polled this hardware subsystem"
- Show daemon uptime when available
- Do not force subsystem ages to match — they reflect different I/O paths

### Freshness thresholds (daemon-defined)
- **OK**: age <= 2 × expected interval (default: <=2000ms for 1s interval)
- **WARN**: age > 2× and <= 5× interval
- **CRIT**: age > 5× interval or never updated

## Implementation: Event log detail retrieval (R34)

### Category buttons
The Event Log tab provides three on-demand detail retrieval buttons:

| Button | Source | What it shows |
|--------|--------|---------------|
| Daemon Status | AppState snapshot | Connection, mode, daemon version, overall status, subsystem details, sensor/fan counts, warnings, active profile |
| Controller Status | AppState capabilities + status | OpenFan/hwmon presence, channels, write support, subsystem freshness, reason text |
| System Journal | `journalctl -u onlyfans-daemon` | Last 100 lines of onlyfans-daemon journal entries |

### Source labeling
Each detail block is appended to the log view with:
- A separator line
- Timestamp and `[SOURCE]` header
- The detail content
- A source attribution line explaining where the data came from

### Journal access
- Uses `subprocess.run()` with `--lines=100 --no-pager --output=short-iso`
- 5-second timeout prevents hangs
- Permission failure → message explaining `systemd-journal` group requirement
- `journalctl` not found → message explaining systemd dependency

### Log widget
Uses `QPlainTextEdit` (not `QTextEdit`) for efficient append-heavy plain text.
`setMaximumBlockCount(2000)` prevents unbounded memory growth.

## Implementation: Lease explanation (R34)

### Explanation content
The Lease tab includes a static explanation card above the live status card:

> **What is a lease?** A lease grants exclusive write access to your motherboard's fan headers (hwmon). Only one client can hold the lease at a time, preventing conflicting PWM commands from different tools.
>
> The GUI automatically acquires and renews the lease while controlling fans. The lease expires after 60 seconds if not renewed (e.g. if the GUI crashes), allowing other tools to take over.
>
> If another tool holds the lease, the GUI cannot write PWM values until the lease is released or expires. OpenFan Controller writes do not require a lease — only motherboard hwmon writes do.

### Status card fields
- Lease held/not held
- Lease ID (UUID or —)
- Owner hint (who holds it)
- TTL remaining (seconds)
- Required (yes/no)

## Implementation: Diagnostics theming (R34)

### Transparent labels
All labels inside Card frames use `background: transparent` inline style. This prevents opaque label backgrounds from conflicting with the Card class background across themes.

### CSS class usage
- Card title labels: `.PageSubtitle` class (bold section-header role, inherits theme size)
- Metadata/explanatory labels: `.CardMeta` class (smaller, secondary color)
- Status label in button row: `.CardMeta` class
- No hardcoded `font-size: Npx` on any Diagnostics label

### No inline font-size overrides
All font sizing is inherited from the global theme stylesheet via CSS classes. Changing the theme text size changes Diagnostics page text consistently.

## Nice-to-have later
- background self-checks
- one-click diagnostics redaction
- direct save of API snapshots
- daemon restart integration if safe and supported
- real-time journal tailing (follow mode) via background thread
- python-systemd native journal access (eliminates subprocess overhead)
