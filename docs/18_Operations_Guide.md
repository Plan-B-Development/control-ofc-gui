# 18 — Operations Guide

## Purpose
This document covers daemon configuration, startup, permissions, CLI usage, environment variables, profile management, syslog setup, and troubleshooting. It is the canonical operational reference for running Control-OFC in production.

---

## Daemon installation

### Build from source
```bash
cd daemon && cargo build --release
sudo cp target/release/control-ofc-daemon /usr/local/bin/
```

### Systemd service
```bash
sudo cp packaging/control-ofc-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now control-ofc-daemon
```

The service runs as root (required for hwmon sysfs writes and serial device access). Security hardening is applied: `ProtectHome=read-only`, `ProtectSystem=strict`, `PrivateTmp=true`, `NoNewPrivileges=true`.

---

## Daemon configuration

### Config file location
`/etc/control-ofc/daemon.toml` — loaded at startup. Create manually if needed.

### Config schema
```toml
[serial]
port = "/dev/serial/by-id/usb-Karanovic_Research_OpenFan_...-if00"  # stable path
# port = "/dev/ttyACM0"  # unstable, may change after reboot
timeout_ms = 500

[polling]
poll_interval_ms = 1000

[ipc]
socket_path = "/run/control-ofc/control-ofc.sock"

[state]
state_dir = "/var/lib/control-ofc"  # persistent state directory

[startup]
delay_secs = 0  # seconds to wait before device detection after boot (0-30)

[profiles]
search_dirs = ["/etc/control-ofc/profiles"]  # add user profile dirs via API
```

All fields are optional — defaults are shown above.

### Serial device path
**Use stable `/dev/serial/by-id/` paths** instead of `/dev/ttyACM0`. The unstable path changes after USB re-enumeration (reboot, unplug/replug). Find your stable path:
```bash
ls -la /dev/serial/by-id/
```

---

## CLI arguments

| Argument | Description |
|----------|-------------|
| `--profile <name>` | Load a named profile from search paths on startup |
| `--profile-file <path>` | Load a profile from an absolute file path |

### Profile search paths
When using `--profile <name>`, the daemon searches (in order):
1. `/etc/control-ofc/profiles/<name>.json`
2. `$XDG_CONFIG_HOME/control-ofc/profiles/<name>.json` (default: `~/.config/control-ofc/profiles/`)

---

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RUST_LOG` | Logging level (`error`, `warn`, `info`, `debug`, `trace`) | `info` (set in systemd service) |
| `OPENFAN_PROFILE` | Profile name to load at startup (fallback if no `--profile` CLI arg) | none |
| `XDG_CONFIG_HOME` | Override config directory for profile search | `~/.config` |

---

## Permissions and groups

### hwmon sysfs access
The daemon reads from and writes to `/sys/class/hwmon/hwmonN/pwmN`. Running as root (via systemd) provides the necessary permissions.

### Serial device access
The systemd service includes `SupplementaryGroups=uucp` for `/dev/ttyACM*` access. Ensure the `uucp` group has access to your serial device:
```bash
ls -la /dev/ttyACM0
# Should show: crw-rw---- 1 root uucp ... /dev/ttyACM0
```

### Runtime directories
- `/run/control-ofc/` — created by systemd (`RuntimeDirectory=control-ofc`)
- `/var/lib/control-ofc/` — daemon state persistence (created by systemd via `StateDirectory=control-ofc`, configurable via `[state] state_dir` in daemon.toml)

---

## Profile activation and persistence

### Startup precedence
1. CLI: `--profile quiet` or `--profile-file /path/to/profile.json`
2. Environment: `OPENFAN_PROFILE=quiet`
3. Persisted state: `/var/lib/control-ofc/daemon_state.json` (from previous API activation)
4. None → imperative mode (GUI drives PWM writes)

### GUI activation flow
When the user activates a profile in the GUI:
1. GUI saves profile to `~/.config/control-ofc/profiles/<id>.json`
2. GUI calls `POST /profile/activate {"profile_path": "/home/user/.config/control-ofc/profiles/<id>.json"}`
3. Daemon validates, applies, and persists to `/var/lib/control-ofc/daemon_state.json`
4. Profile survives daemon restart, reboot, and GUI close

### Deactivating a profile
Currently no explicit deactivate endpoint. Activating a different profile replaces the current one. To return to imperative mode, restart the daemon without a profile.

---

## IPC socket

Default: `/run/control-ofc/control-ofc.sock`

The GUI connects via `httpx` with a Unix socket transport. Test manually:
```bash
curl --unix-socket /run/control-ofc/control-ofc.sock http://localhost/status
curl --unix-socket /run/control-ofc/control-ofc.sock http://localhost/capabilities
curl --unix-socket /run/control-ofc/control-ofc.sock http://localhost/fans
curl --unix-socket /run/control-ofc/control-ofc.sock http://localhost/sensors
```

---

## Troubleshooting

### Daemon won't start
```bash
sudo systemctl status control-ofc-daemon
sudo journalctl -u control-ofc-daemon -f
```

### Serial device not found
- Check device exists: `ls /dev/ttyACM*`
- Check permissions: `ls -la /dev/ttyACM0`
- Use stable path: `ls /dev/serial/by-id/`
- The daemon retries detection 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s)

### hwmon fans not detected
- Check sysfs exists: `ls /sys/class/hwmon/`
- Check PWM files: `find /sys/class/hwmon -name 'pwm[0-9]' 2>/dev/null`
- Request rescan: `curl -X POST --unix-socket /run/control-ofc/control-ofc.sock http://localhost/hwmon/rescan`

### GUI shows "Daemon disconnected"
- Check daemon is running: `systemctl is-active control-ofc-daemon`
- Check socket exists: `ls -la /run/control-ofc/control-ofc.sock`
- Check socket permissions (GUI user must be able to connect)

### Syslog not working
- Verify host/port are set: Check Status button in Settings
- Verify receiver accepts **TCP** (not UDP-only) on the configured port
- Check daemon logs: `journalctl -u control-ofc-daemon | grep telemetry`
- Test with `logger`: `logger -n <host> -P <port> --tcp "test message"`

### Profile not restoring after reboot
- Check persisted state: `cat /var/lib/control-ofc/daemon_state.json`
- Check profile file exists at the path stored in state
- Check daemon logs for profile loading errors on startup

---

## Safety behaviour

The daemon enforces a single thermal safety rule (non-negotiable, not configurable):
- **Trigger**: CPU Tctl reaches 105°C
- **Action**: Force all fans to 100%
- **Hold**: Until temperature drops below 80°C
- **Recovery**: Resume normal control at 60°C

Per-header safety floors:
- Chassis fans: minimum 20% (1-19% clamped to 20%, 0% allowed briefly)
- CPU/pump fans: minimum 30% (0% rejected outright)

These are daemon-hardcoded and visible via `GET /capabilities` under `limits`.
