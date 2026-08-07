# 18 — Operations Guide

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

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
# Two defaults: the system dir plus a home-relative dir derived from
# XDG_CONFIG_HOME (or $HOME/.config, or /root/.config when HOME is unset for a
# systemd service). The daemon also *prepends* its own store dir
# (/var/lib/control-ofc/profiles/) at startup. Add more via the API.
search_dirs = ["/etc/control-ofc/profiles", "/root/.config/control-ofc/profiles"]

[detection]
# Both are opt-in and default false, and both need a root-installed systemd
# drop-in as well as the flag — the flag alone does NOT enable the feature.
allow_port_probe = false        # active Super-I/O /dev/port probe (DEC-203);
                                # also needs superio-port-probe.conf.example
                                # (CAP_SYS_RAWIO)
enable_nvidia_telemetry = false # read-only NVML telemetry (DEC-204);
                                # also needs nvidia-telemetry.conf.example
                                # (/dev/nvidia* rw). Experimental.
```

All fields are optional — defaults are shown above.

### Two config files — `daemon.toml` vs `runtime.toml` (ADR-002)

`/etc/control-ofc/daemon.toml` is **admin-owned**; the daemon never writes it, so
your comments and edits survive. `{state_dir}/runtime.toml` (default
`/var/lib/control-ofc/runtime.toml`) is **daemon-owned**, written only by
`POST /config/*`, and **overlays** the admin file — runtime wins for any key
present in both. This mirrors NetworkManager's admin-conf + intern-conf split.

If a runtime value is shadowing a `daemon.toml` edit you made, the daemon says so
once at startup in an `info` log, and `GET /config` reports `source: "runtime"`
for that key.

### Which keys can be changed without editing a file (DEC-243)

`GET /config` returns every key with its effective value, its `source`
(`runtime` / `admin` / `default`), whether it is `mutable`, and whether a
persisted change is not yet in effect (`restart_pending`). The GUI's
**Settings ▸ Daemon Configuration** card is a view of exactly this.

| Key | Mutable via API | Notes |
|---|---|---|
| `profiles.search_dirs` | `POST /config/profile-search-dirs` | Additive; also re-applied on SIGHUP |
| `startup.delay_secs` | `POST /config/startup-delay` | 0–30 |
| `polling.poll_interval_ms` | `POST /config/poll-interval` | 250–10000 |
| `serial.port` | `POST /config/serial-port` | Must be under `/dev/`; `null` = auto-detect |
| `serial.timeout_ms` | `POST /config/serial-timeout` | 50–5000 |
| `detection.allow_port_probe` | `POST /config/allow-port-probe` | **Also needs the drop-in** |
| `detection.enable_nvidia_telemetry` | `POST /config/nvidia-telemetry` | **Also needs the drop-in** |
| `ipc.socket_path` | **No — read-only** | A bad value locks every client out of the daemon |
| `state.state_dir` | **No — read-only** | Moving it orphans `runtime.toml` and the profile store |

**Everything except the profile search dirs takes effect only on restart.** SIGHUP
re-reads both files but re-applies only `profiles.search_dirs` to the running
process; the rest are consumed once at startup:
```bash
sudo systemctl restart control-ofc-daemon
```

### Serial device path
**Use stable `/dev/serial/by-id/` paths** instead of `/dev/ttyACM0`. The unstable path changes after USB re-enumeration (reboot, unplug/replug). Find your stable path:
```bash
ls -la /dev/serial/by-id/
```

---

## CLI arguments

| Argument | Description |
|----------|-------------|
| `--config <path>` | Path to `daemon.toml`. Takes precedence over `$CONTROL_OFC_CONFIG` and the default location |
| `--profile <name>` | Load a named profile from search paths on startup |
| `--profile-file <path>` | Load a profile from an absolute file path |
| `--allow-non-root` | Permit startup as a non-root user. Hardware writes that need root will fail — for development and inspection, not normal operation |

### Profile search paths
When using `--profile <name>`, the daemon searches (highest priority first):
1. `/var/lib/control-ofc/profiles/<name>.json` — the daemon-owned **store of record**, prepended at startup so CRUD-created profiles are always found first (DEC-160)
2. `/etc/control-ofc/profiles/<name>.json`
3. `$XDG_CONFIG_HOME/control-ofc/profiles/<name>.json` (default: `~/.config/control-ofc/profiles/`)

---

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RUST_LOG` | Logging level (`error`, `warn`, `info`, `debug`, `trace`) | `info` (set in systemd service) |
| `CONTROL_OFC_CONFIG` | Path to `daemon.toml`. Overridden by `--config` | `/etc/control-ofc/daemon.toml` |
| `OPENFAN_PROFILE` | Profile name to load at startup (fallback if no `--profile` CLI arg) | none |
| `HOME` | Used to derive the home-relative profile search dir when `XDG_CONFIG_HOME` is unset | unset under systemd → `/root` |
| `XDG_CONFIG_HOME` | Override config directory for profile search | `~/.config` |

---

## Permissions and groups

### hwmon sysfs access
The daemon reads from and writes to the motherboard PWM nodes (`/sys/class/hwmon/hwmonN/pwmN`, which are symlinks resolving to `/sys/devices/...`). Running as root (via systemd) provides the necessary permissions, **and** the packaged unit's sandbox must expose the device tree for writing — `ReadWritePaths=/sys/devices` (daemon ≥ v2.5.2; see "Motherboard/GPU fans discovered but not responding" under Troubleshooting, DEC-199).

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
4. None → the daemon runs purely imperative (no autonomous control; nothing is evaluated until a profile is activated). The GUI never drives PWM — the daemon's profile engine is the sole writer (DEC-159 / DEC-165).

### GUI activation flow
When the user activates a profile in the GUI:
1. GUI saves the profile to its local draft cache (`~/.config/control-ofc/profiles/<id>.json`) **and** uploads it to the daemon — the **store of record** (DEC-160) — via `PUT /profiles/<id>` (or `POST /profiles` to create it). The daemon writes it into its own store dir (`/var/lib/control-ofc/profiles/`).
2. GUI calls `POST /profile/activate {"profile_id": "<id>"}` — the daemon resolves the id from its own store/search dirs. (Activation also accepts a `profile_path`, but it must lie **within a daemon search dir**; the GUI user's `~/.config/...` path is *not* one of the root daemon's search dirs, so a user-HOME `profile_path` would be rejected. Activate by `profile_id`.)
3. Daemon validates, applies, and persists the active selection to `/var/lib/control-ofc/daemon_state.json`
4. Profile survives daemon restart, reboot, and GUI close; the daemon can re-hydrate the full profile document from its own store via `GET /profiles/<id>` (DEC-175)

### Deactivating a profile
Two ways to leave profile mode without restarting the daemon:
- **Activate a different profile** — `POST /profile/activate` replaces the current one.
- **Deactivate entirely** — `POST /profile/deactivate` (body ignored) clears the active profile and returns the daemon to imperative-only mode. It is idempotent (deactivating when none is active is a success no-op), persists the cleared state so a restart does not resurrect the profile, and releases the daemon's internal `profile-engine` hwmon lease (the GUI holds no lease — DEC-097/DEC-165). Response: `{"deactivated": true, "previous_profile_id": ..., "previous_profile_name": ...}`.

Restarting the daemon without a profile also works, but is no longer required.

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

### Motherboard/GPU fans discovered but not responding
If a header or GPU fan appears in the dashboard but never changes speed, and the daemon journal repeats a line like:

```
[WARN] hwmon write failed for hwmon:it8696:pwm1: … /sys/class/hwmon/hwmonN/pwm1_enable: Read-only file system (os error 30)
```

then the packaged daemon is older than **v2.5.2**: its systemd sandbox carved out `/sys/class/hwmon` / `/sys/class/drm` (symlink directories) instead of the real device tree, so every fan write hit `EROFS` and the fans stayed in BIOS/PMFW automatic mode. **Upgrade the daemon package** — the fix sets `ReadWritePaths=/sys/devices` (DEC-199). If you cannot upgrade immediately, a systemd drop-in restores control:
```bash
sudo systemctl edit control-ofc-daemon
#   [Service]
#   ReadWritePaths=/sys/devices
sudo systemctl daemon-reload && sudo systemctl restart control-ofc-daemon
```
If writes still fail **after** upgrading, the cause is hardware prerequisites rather than the sandbox — open the **Hardware** page, which detects a missing Super I/O driver, `acpi_enforce_resources=lax`, or `amdgpu.ppfeaturemask` and shows the exact fix.

### GUI shows "Daemon disconnected"
- Check daemon is running: `systemctl is-active control-ofc-daemon`
- Check socket exists: `ls -la /run/control-ofc/control-ofc.sock`
- Check socket permissions (GUI user must be able to connect)

### Profile not restoring after reboot
- Check persisted state: `cat /var/lib/control-ofc/daemon_state.json`
- Check profile file exists at the path stored in state
- Check daemon logs for profile loading errors on startup

---

## Safety behaviour

The daemon enforces a single thermal safety rule (non-negotiable, not configurable):
- **Trigger**: hottest CPU temperature reaches 105°C
- **Action**: Force all OpenFan channels and writable hwmon headers to 100% PWM. GPU fans are excluded — there is no GPU emergency threshold; AMD PMFW firmware protects the GPU independently (DEC-130)
- **Hold**: Until temperature drops below 80°C
- **Recovery**: Apply a 60% PWM recovery floor for two cycles (the release cycle and one more), then resume active profile control
- **Fallback**: Force 40% PWM (OpenFan + hwmon) if no CPU sensor is reachable for 5 consecutive poll cycles
- **Visibility**: `GET /status` reports `thermal_state` (`normal` / `recovery` / `emergency` / `no_sensor_fallback`); the GUI has no fan control to pause and only **shows** a poll-driven thermal-protection banner while protection is active (DEC-165, superseding the retired DEC-132 GUI stand-down)

There are no per-*header* PWM floors: the daemon reports `min_pwm_percent: 0` for every hwmon header. The **role-aware minimum** is different. The GUI *bakes* a role-aware default into each control's `LogicalControl.minimum_pct` (30% for CPU/pump-labelled members, 20% for chassis/openfan, 0% for GPU-only — DEC-095), and as of 2.0.0 the **daemon enforces and backstops** it (DEC-162): a profile whose pump/CPU control sets `minimum_pct` below the hard 30% floor (`HARD_PUMP_CPU_FLOOR_PCT`) is rejected with `400 validation_error` (`FLOOR_TOO_LOW`), and the profile engine independently re-clamps every eval tick (`member_effective_floor` → `max(minimum_pct, 30%)`). So floor enforcement is **not** purely the GUI's responsibility — the daemon does refuse and re-floor on the role-aware minimum.

The thermal trigger/release thresholds are reported by `GET /diagnostics/hardware` in its thermal-safety section (`emergency_threshold_c`, `release_threshold_c`, plus `state` and `cpu_sensor_found`). They are **not** in `GET /capabilities`: the `limits` object there carries only `pwm_percent_min`, `pwm_percent_max`, and `openfan_stop_timeout_s`. The live override state is also surfaced as `thermal_state` in `GET /status`.
