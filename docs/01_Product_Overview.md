# 01 — Product Overview

## Product name
**Control-OFC**  
A Linux-first GUI for controlling the OpenFanController ecosystem through the existing daemon/API.

## Short description
Control-OFC is a desktop application for observing system cooling state, switching between cooling profiles, editing fan curves, applying manual override, and diagnosing issues, without ever touching hardware directly.

## Primary user
The primary user is the project owner.

## Secondary user
A technically capable friend/tester who may not own the OpenFanController hardware.

## Primary platform
- Linux
- CachyOS / Arch Linux
- KDE Plasma

## Portability goal
The app should be portable enough to:
- move between Linux PCs with minimal friction
- be packaged cleanly for testing
- support future public release without major redesign

## Core goals
1. Provide a clean desktop GUI for the existing daemon/API.
2. Show a clear real-time overview of sensors, fan speeds, profile state, and health.
3. Allow safe profile switching and curve editing.
4. Support both OpenFanController channels and daemon-exposed hwmon PWM headers.
5. Make diagnostics highly visible and useful.
6. Support a full-featured demo mode for testing without hardware.
7. Preserve a good path toward future public packaging.

## Non-goals for V1
1. No direct hardware access from the GUI.
2. No tray-first workflow.
3. No full theme editor UI.
4. No multi-sensor blending in curve logic.
5. No user-customisable dashboard layout.
6. No daemon-side profile storage.
7. No AIO pump-specific advanced flow UI unless backed by real daemon data.
8. No automatic public release tooling in V1.

## Mandatory architectural rule
The GUI must **never** talk directly to:
- serial devices
- hwmon sysfs
- USB devices
- controller hardware
- sensor backends

All reads and writes must go through the daemon/API.

## Product vocabulary

### Fan
An individual controllable target shown in the GUI. This may come from:
- the OpenFanController
- hwmon PWM headers exposed by the daemon

### Fan group
A flexible logical grouping such as:
- Intake
- Exhaust
- CPU
- Radiator
- Case

A fan may belong to one, many, or no groups.

### Profile
A whole-of-system operating mode. Only one profile is active at a time. A profile includes:
- assigned curve(s)
- sensor selection
- fan/group assignment rules
- manual/automatic behaviour
- optional per-profile presentation metadata

### Manual override
A temporary operator-driven mode that overrides automatic curve application. It must be visually obvious and easy to exit.

## V1 functional scope
- Dashboard
- Controls
- Settings
- Diagnostics
- Demo mode
- Theme import/export
- GUI settings import/export
- Local persistence of profiles, groups, aliases, and themes

## V1 data sources
The GUI consumes daemon/API endpoints for:
- capabilities
- status
- sensors
- fans
- hwmon headers
- hwmon lease state
- write commands for OpenFanController, hwmon PWM, and GPU fans

## V1 sensors
The GUI must treat these as first-class sensor categories:
- CPU
- Motherboard
- GPU (AMD only)
- Liquid
- Ambient
- Disk

## Profile and curve decisions
- One active profile at a time
- New curves default to 5 points
- Curves are edited in **% output**
- Preset templates are included:
  - Quiet
  - Balanced
  - Performance
- Curves use one sensor only in V1
- No live simulation preview before apply

## Health and fault handling
When a sensor disappears, stops updating, or returns invalid data:
- the UI must highlight the issue in red
- the user must see a warning, not just a silent fallback
- Diagnostics must expose the specific reason where possible

## Dashboard decisions
Immediately visible on launch:
- fan labels
- fan RPM
- current profile
- graph of current fan speeds

## Diagnostics decisions
Diagnostics must expose:
- logs
- daemon/API status
- controller status
- sensor health
- USB-related details if the daemon exposes them through status or diagnostics inputs
- config validation
- export support bundle

## User actions expected in diagnostics
- reload config
- reconnect controller
- export support bundle
- copy last errors

## Visual tone
The UI should lean into the parody branding, but operation screens must still feel:
- serious enough for regular use
- dark-first
- readable
- trustworthy
- not cluttered
