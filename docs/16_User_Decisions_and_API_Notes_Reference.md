# 16 — User Decisions and API Notes Reference

This file captures the key explicit decisions supplied by the user and the uploaded daemon/API notes.

## Product direction
- Primarily for the user, but portable enough for friend testing and possible future release
- Linux first
- CachyOS / Arch + KDE Plasma are primary targets
- Desktop app first
- Tray later
- GUI must only talk to daemon/API

## Control model
- Fans can be grouped flexibly by label/role/location
- One profile applies at a time to the whole system
- Fans can belong to multiple groups
- Manual override is required
- Safety floors are daemon-hardcoded and read-only (not editable)
- Simple hysteresis (2°C deadband) is included in V1 control loop

## Sensors
First-class in V1:
- CPU
- Motherboard
- GPU (AMD only)
- Liquid
- Ambient
- Disk

Curve logic:
- one sensor only in V1

History:
- retain only the last 2h

Fault handling:
- highlight red and warn if sensor disappears/stops/returns nonsense

## Dashboard
Visible on launch:
- fan labels
- RPM
- current profile
- graph of current fan speeds

V1 dashboard:
- fixed layout

Chart ranges:
- 30s
- 2m
- 5m
- 10m
- 15m
- 20m
- 30m
- 1h
- 2h

## Curve editor
- % output
- 5 default points
- preset templates included
- no simulation before apply

## Settings
- full theme editor not required in V1
- theme export/import required
- ~~telemetry/syslog-related settings~~ removed (R52 de-scope)

## Diagnostics
Show:
- logs
- daemon/API status
- controller status
- sensor health
- USB details where available
- config validation
- export bundle

Actions:
- reload config
- reconnect controller
- export support bundle
- copy last errors

## Branding
- parody branding
- default dark theme
- supplied image should guide icon/splash/other assets as appropriate

## Demo mode
- yes, required

## Control-page placement
- fan/group editing should live in Controls or Profiles, whichever fits better
- this pack standardises on `Controls`

## Daemon/API endpoints of note

### Read endpoints
- `GET /capabilities`
- `GET /status`
- `GET /sensors`
- `GET /fans`
- `GET /hwmon/headers`
- `GET /hwmon/lease/status`
- `GET /sensors/history?id=...&last=N`
- `GET /events` (SSE stream)
- `GET /profile/active`

### OpenFan writes
- `POST /fans/openfan/{ch}/pwm`
- `POST /fans/openfan/pwm`
- `POST /fans/openfan/{ch}/target_rpm`

### Hwmon lease and writes
- `POST /hwmon/lease/take`
- `POST /hwmon/lease/release`
- `POST /hwmon/lease/renew`
- `POST /hwmon/{header_id}/pwm`

### Profile and GPU
- `POST /profile/activate`
- `POST /gpu/{gpu_id}/fan/pwm`
- `POST /gpu/{gpu_id}/fan/reset`
- `POST /hwmon/rescan`
- `POST /fans/openfan/{ch}/calibrate`

## API-writable runtime settings already available
- OpenFan PWM commands
- OpenFan target RPM
- hwmon PWM with lease

## Not writable via API
- serial port path + timeout
- daemon main polling/publish interval
- IPC socket path

## Known gaps (historical — see docs/14 for current status)
- ~~no daemon-native fan curves/profiles~~ — resolved: daemon has profile engine
- no control-mode visibility
- no hardware rescan
- limited per-header RPM visibility
- safety floor customisation not runtime-configurable (read-only via capabilities)
- stop timeout queryable but not configurable
- no daemon-side profile persistence
- no sensor alias/group API
- fan responses lack `label`/`kind` — GUI derives display names from user alias > hwmon header label > fan id
- AIO support placeholder only
