# 18 — Operations Guide

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
This document covers daemon configuration, startup, permissions, the service lifecycle, CLI usage, environment variables, profile management, logs, and troubleshooting. It is the canonical operational reference for running Control-OFC in production.

---

## Daemon installation

Install the `control-ofc-daemon` package — from the signed `[control-ofc]` pacman
repository, or built yourself from the daemon repo's `packaging/PKGBUILD` with
`makepkg -si` (both are in the daemon repo's `README.md` § Install). Then:
```bash
sudo systemctl enable --now control-ofc-daemon
```

Do not copy the binary and unit file in by hand. The unit runs
`/usr/bin/control-ofc-daemon` and, after every stop, `/usr/bin/control-ofc-restore-auto`,
which gives each fan header back to what it was doing before the daemon took it;
the package also installs the sleep hook, the Super-I/O guard and
`/etc/modules-load.d/control-ofc.conf`. A hand-copied binary under `/usr/local/bin`
installs none of these, so the unit cannot start it or cannot hand the fans back.
`makepkg` builds the tagged release, not a local checkout; the daemon repo's
`docs/DEVELOPER_HANDOVER.md` says how to run your own build.

**`/etc/modules-load.d/control-ofc.conf`** loads the Super-I/O and drive-temperature
drivers at boot (`nct6775`, `it87`, `w83627ehf`, `drivetemp`); the kernel cannot
auto-load them because the chips sit on ISA ports. Rename or delete the file to stop it.
**The Super-I/O guard** (`/usr/lib/modprobe.d/control-ofc-superio.conf`, which runs
`/usr/lib/control-ofc/control-ofc-superio-guard`) stops `nct6775` and `w83627ehf` from
probing on a Gigabyte board (every Gigabyte board from daemon 2.56.1; older daemons
cover only the boards the guard lists): those drivers write to the Super-I/O ports before
they check for their chip, and on a Gigabyte board with an ITE eSPI bridge that write can
hide the second fan chip until the machine is powered off at the wall. On every other
board the real `modprobe` runs unchanged. To turn the guard off, create an empty file
with the **same name**, `/etc/modprobe.d/control-ofc-superio.conf`, and reboot; a file
with a different name does not reliably override it. Recovering a chip the probe has
already hidden is in the manual's
[Hardware Troubleshooting](../manual/hardware-troubleshooting.md#some-of-my-fan-headers-are-missing--only-5-of-8-show-up).

The service runs as root (required for hwmon sysfs writes and serial device access). Security hardening is applied: `ProtectHome=read-only`, `ProtectSystem=strict`, `PrivateTmp=true`, `NoNewPrivileges=true`.

---

## Service lifecycle

The unit's own comments (`/usr/lib/systemd/system/control-ofc-daemon.service`) are the
source for every value here; this is the summary an operator needs.

- **Start.** `Type=notify`: the daemon tells systemd it is ready only once the profile
  engine is ticking, the API is serving and its signal handlers are in place, so
  `systemctl start` returns when the fans are actually under control.
  `TimeoutStartSec=120` is pinned because distributions change the default; the
  boot-time serial probe extends it per device, so it never counts against the 120 s.
- **Watchdog.** `WatchdogSec=15`. The daemon pings systemd from each completed engine
  tick and from nowhere else, so a loop that stops ticking is noticed within 15 s even
  though the process is still alive. On a timeout systemd sends `SIGTERM` — a graceful
  stop, so the exit floor still runs — and kills the process 10 s later
  (`TimeoutAbortSec=10`) if it has not exited.
- **Sleep.** The package's sleep hook (`/usr/lib/systemd/system-sleep/control-ofc-daemon`)
  tells the daemon before each suspend, and the daemon widens the watchdog to 120 s until
  the resume call, or until 120 s pass without one. A machine whose suspend and resume
  take longer can raise `WatchdogSec=` in a drop-in.
- **Restart.** `Restart=on-failure` covers a crash, a non-zero exit and a watchdog
  timeout. The delay backs off: 3 s, then about 5.5, 10, 18 and 33 s, then 60 s for
  every later restart. There is **no start limit**, so the unit never ends up
  permanently failed and waiting for `systemctl reset-failed`. After five minutes of
  completed ticks the daemon resets the back-off, so a later, unrelated fault starts
  again at 3 s (systemd 258 or later; older versions ignore that reset). The back-off
  itself needs systemd 254 or later.
- **Stop.** On `SIGTERM` the daemon stops its API, lets its tasks finish, and then
  restores the hardware: the exit floor for each OpenFan channel and each header with no
  mode to go back to, then a reset of the GPU fan curves, then each other motherboard
  header handed back to what it was doing before the daemon took it. (From the first
  daemon release after 2.56.3 only a GPU the daemon drove is reset; daemon 2.56.3 and
  older reset every AMD card at every stop.)
  `TimeoutStopSec=40` is the outer bound. `ExecStopPost=/usr/bin/control-ofc-restore-auto`
  runs after **every** stop — a crash and `SIGKILL` included — and repeats the header
  hand-back, from the daemon's record of what each header was doing, and the GPU reset.
  It cannot reach an OpenFan channel, so after a crash those channels stay at their last
  duty until the daemon starts again.
- **Reload.** `systemctl reload` sends `SIGHUP`, which re-reads the profile search
  directories and the exit floor; everything else needs a restart (see below).

---

## Daemon configuration

### Config file location
`/etc/control-ofc/daemon.toml` — loaded at startup. The package installs it with every
key commented out, so the defaults apply until you uncomment one; pacman keeps your
edits on upgrade (`backup=`). With no file at all the daemon also runs on defaults.

### A bad edit stops the daemon — read this before editing
The file is strict. **An unknown key, a misspelt section or an out-of-range value makes
the daemon exit at startup**, and systemd restarts it on a back-off (3 s, then up to one
start a minute — see [Service lifecycle](#service-lifecycle)) until the file is fixed.
For all that time **nothing controls the fans and the thermal emergency cannot run**.
The journal names the offending key (`sudo journalctl -u control-ofc-daemon`; reading the system journal
needs root or the `systemd-journal` group). The ranges the file accepts:

| Key | Accepted | Default |
|---|---|---|
| `polling.poll_interval_ms` | 100 or more; above 6000 it is clamped to 6000 with a warning (see the table below) | 1000 |
| `serial.timeout_ms` | 50 or more | 500 |
| `startup.delay_secs` | 0–30 | 0 |
| `shutdown.exit_floor_pct` | 0–100 | 50 |

After an edit, `sudo systemctl reload control-ofc-daemon` applies the profile search
directories and the exit floor at once; every other key needs
`sudo systemctl restart control-ofc-daemon`. A reload that finds the file invalid logs
the error and keeps the running values.

### Config schema
Every key is optional; the values shown are the defaults unless a comment says otherwise.
```toml
[serial]
# port = "/dev/serial/by-id/usb-Karanovic_Research_OpenFan_...-if00"
#   No default: when unset the daemon auto-detects the controller. If you set it,
#   use the stable /dev/serial/by-id/ path, not /dev/ttyACM0.
timeout_ms = 500

[polling]
poll_interval_ms = 1000

[ipc]
socket_path = "/run/control-ofc/control-ofc.sock"

[state]
state_dir = "/var/lib/control-ofc"  # persistent state directory

[startup]
delay_secs = 0  # seconds to wait before device detection after boot (0-30)
record_startup = false  # record a short validation session at every start (DEC-335).
                        # daemon.toml only: runtime.toml's [startup] rejects it

[shutdown]
exit_floor_pct = 50  # the lowest speed a clean stop leaves a fan it cannot give
                     # back to firmware at — an OpenFan channel, or a header with
                     # no mode switch (DEC-388). 0 turns it off

[profiles]
# Default: /etc/control-ofc/profiles plus a home-relative dir —
# $XDG_CONFIG_HOME/control-ofc/profiles if that is set, else
# $HOME/.config/control-ofc/profiles. The unit sets HOME=/root, so under
# systemd that is /root/.config/control-ofc/profiles. The daemon always
# *prepends* its own store dir ({state_dir}/profiles) at startup, and the GUI
# adds its own profile folder through the API on every connect.
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

**Moving `state_dir` or `socket_path` needs a systemd drop-in as well.** The unit runs
with `ProtectSystem=strict`, so outside its private `/tmp` the daemon can write files
only under `/run/control-ofc` and `/var/lib/control-ofc` (its `RuntimeDirectory=` and
`StateDirectory=`) and `/sys/devices`. A new location must be added with `ReadWritePaths=` in
`sudo systemctl edit control-ofc-daemon`, or the daemon cannot create its socket or
save state there.

### Two config files — `daemon.toml` vs `runtime.toml` (ADR-002)

`/etc/control-ofc/daemon.toml` is **admin-owned**; the daemon never writes it, so
your comments and edits survive. `{state_dir}/runtime.toml` (default
`/var/lib/control-ofc/runtime.toml`) is **daemon-owned**, written only by
`POST /config/*`, and **overlays** the admin file — runtime wins for any key
present in both. This mirrors NetworkManager's admin-conf + intern-conf split.

**`runtime.toml` holds the fan header roles you assign** (`[hardware] header_roles`,
set by the GUI's header-role picker and Configure AIO, or `POST /config/header-role`),
as well as the cooling devices (`[[cooling_devices]]`), the preferred sensors, the exit
floor and every key in the table below that was changed through the API. On a board
whose chip publishes no fan labels, a `pump` assignment there is the only evidence a
header drives a pump, so it is what gives that header its 30 % floor and keeps fan
identify from stopping it. **Do not delete the file or edit it by hand, and back it up
with `daemon.toml`.**

If the daemon cannot read it, it still starts — on defaults, **with no header roles** —
and reports `runtime_config_degraded` on `GET /status`; the Dashboard shows a banner
(see the manual's Dashboard page). If a setting is then saved, the daemon keeps the
unreadable file as `runtime.toml.invalid-<unix-time>` and writes a new one carrying the
header roles and cooling devices it is running with; anything else must be copied back
from the kept copy, followed by a restart. The daemon's `docs/USER_GUIDE.md`
§ When `runtime.toml` cannot be read has the per-phase detail.

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
| `profiles.search_dirs` | `POST /config/profile-search-dirs` | `add` and/or `remove` (`remove` is daemon ≥ 2.23.0, DEC-285); also re-applied on SIGHUP. `/etc/control-ofc/profiles` cannot be removed, and neither can the last remaining entry |
| `startup.delay_secs` | `POST /config/startup-delay` | 0–30 |
| `polling.poll_interval_ms` | `POST /config/poll-interval` | 250–2000 via the API (the API ceiling bounds thermal-safety reaction latency). The admin file allows 100–6000: past 6000 ms the thermal-emergency rule's staleness budget stops tracking the cadence (capped at 30 s), so its 5x headroom erodes towards 1x — and past 30 s the budget is shorter than one poll period and the ladder never fires at all. A slower value is clamped to 6000 with a warning rather than honoured (DEC-270) |
| `serial.port` | `POST /config/serial-port` | Must match the serial allowlist (`/dev/tty{S,USB,ACM,AMA}*`, `/dev/serial/*`), ≤256 chars; `null` = auto-detect. The allowlist is wider than the unit: the service can open only `ttyACM` and `ttyUSB` nodes, so a `ttyS` or `ttyAMA` port also needs a `DeviceAllow=` drop-in (see [Serial device access](#serial-device-access)). A port that fails to open — or opens but does not answer the OpenFanController handshake — falls back to auto-detection (DEC-250), so a wrong-but-openable tty cannot be adopted as the fan controller |
| `serial.timeout_ms` | `POST /config/serial-timeout` | 50–1000 via the API (bounds emergency write latency) |
| `detection.allow_port_probe` | `POST /config/allow-port-probe` | **Also needs the drop-in** |
| `detection.enable_nvidia_telemetry` | `POST /config/nvidia-telemetry` | **Also needs the drop-in** |
| `shutdown.exit_floor_pct` | `POST /config/exit-floor` | 0–100 (DEC-388): the lowest speed a clean stop leaves an OpenFan fan, or a header with no mode switch, at. **Applies immediately**; also re-applied on SIGHUP. `0` turns it off |
| `ipc.socket_path` | **No — read-only** | A bad value locks every client out of the daemon |
| `state.state_dir` | **No — read-only** | Moving it orphans `runtime.toml` and the profile store |

**Everything except the profile search dirs and the exit floor takes effect only
on restart.** Those two apply immediately (both via their API and on SIGHUP), which
is why `GET /config` reports them with `requires_restart: false`; every other key is
consumed once at startup:
```bash
sudo systemctl restart control-ofc-daemon
```

### Serial device path
**Use stable `/dev/serial/by-id/` paths** instead of `/dev/ttyACM0`. The unstable path changes after USB re-enumeration (reboot, unplug/replug). Find your stable path:
```bash
ls -la /dev/serial/by-id/
```

### How the OpenFanController is adopted at boot (DEC-291 / DEC-361)
The controller is **optional hardware**, and a machine without one must pay
neither a boot stall nor a warning that reads like a fault. Boot therefore makes
**exactly one** adoption attempt:

1. **Enumerate** the `/dev/ttyACM*` and `/dev/ttyUSB*` candidates via libudev,
   falling back to a path scan. A configured `serial.port` is tried first but is
   never the only candidate — so a wrong value cannot remove OpenFan control
   (DEC-250). **Nothing is opened at this step** (DEC-291).
2. **Identify** each candidate by opening it — at most once per candidate — and
   asking for `ReadAllRpm`. Only a device that answers is adopted; a tty that
   merely opens is refused, because one that is not an OpenFanController would
   accept every write with `Ok`.
3. **Move on.** The profile engine, the hwmon poll loop and the IPC server start
   regardless, so the daemon is answering the API and evaluating thermal safety
   from this point whether or not a controller was found. (The *OpenFan* poll
   loop is the one thing that is conditional — there is no transport to poll
   until something is adopted.)

If nothing was adopted, the search continues **in the background** for **60
seconds**, or **180 seconds** if you have set `[serial] port`. It re-probes only
when the set of serial devices actually *changes*, so plugging the controller in
during that window is picked up within a few seconds, while a machine whose
devices never change is never re-probed. That matters if you have other
USB-serial hardware attached: identifying a device means opening it, and on
Linux opening a serial port asserts DTR, which **resets Arduino-class boards**.

After the window closes, use the GUI's **Rescan Hardware** action (or
`POST /fans/openfan/rescan`) to adopt a controller without restarting the
daemon. A controller that was adopted and then dropped off is recovered
automatically by the poll loop's own reconnect, with no action needed: it keeps
trying, about every 30 s at the default poll interval, for as long as the daemon
runs. From the first daemon release after 2.56.3 (DEC-436), each try opens only the
configured port, the controller's own device node, and a serial device that has appeared
since the controller dropped off (on every try for its first minute, then once every five
minutes), so USB-serial hardware that was attached all along is not reset while the
controller is away. Daemon 2.56.3 and older open every `ttyACM`/`ttyUSB` device on each
try.
A controller that stops answering without leaving the USB bus is not recovered
this way — restart the daemon.

> Before DEC-361 this was a ladder of up to six attempts sleeping 1+2+4+8+16 s
> that ran *ahead* of the API server and the profile engine. If you are reading
> older notes that describe a ~31 s startup retry, they no longer apply.

---

## CLI arguments

| Argument | Description |
|----------|-------------|
| `--config <path>` | Path to `daemon.toml`. Takes precedence over `$CONTROL_OFC_CONFIG` and the default location |
| `--profile <name>` | Load a named profile (a file stem) from search paths on startup — see [Startup precedence](#startup-precedence). Under systemd, pass it in a drop-in (`systemctl edit control-ofc-daemon`: `ExecStart=` on its own line, then the full command) |
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
| `OPENFAN_PROFILE` | Profile file stem to load at startup; tried after `--profile`/`--profile-file` and before the saved profile. Under systemd, `Environment=OPENFAN_PROFILE=<name>` in a drop-in | none |
| `HOME` | Used to derive the home-relative profile search dir when `XDG_CONFIG_HOME` is unset | `/root` — the unit sets `Environment=HOME=/root` |
| `XDG_CONFIG_HOME` | Override config directory for profile search | `~/.config` |

---

## Permissions and groups

### hwmon sysfs access
The daemon reads from and writes to the motherboard PWM nodes (`/sys/class/hwmon/hwmonN/pwmN`, which are symlinks resolving to `/sys/devices/...`). Running as root (via systemd) provides the necessary permissions, **and** the packaged unit's sandbox must expose the device tree for writing — `ReadWritePaths=/sys/devices` (daemon ≥ v2.5.2; see "Motherboard/GPU fans discovered but not responding" under Troubleshooting, DEC-199).

### Serial device access
The daemon runs as root, so no group membership gates its access to the serial device on any distribution — the unit's `SupplementaryGroups=uucp` does nothing for a root service, and Debian/Ubuntu need no `dialout` drop-in. What does limit it is the unit's `DeviceAllow=char-ttyACM rw` and `DeviceAllow=char-ttyUSB rw`: the service can open only `/dev/ttyACM*` and `/dev/ttyUSB*` nodes (a `/dev/serial/by-id/` link to one is fine). A controller on another kind of node needs a drop-in adding its device class, for example `DeviceAllow=char-ttyS rw`.

### Runtime directories
- `/run/control-ofc/` — created by systemd (`RuntimeDirectory=control-ofc`)
- `/var/lib/control-ofc/` — daemon state persistence (created by systemd via `StateDirectory=control-ofc`, configurable via `[state] state_dir` in daemon.toml — a different directory also needs a `ReadWritePaths=` drop-in, see Config schema above)

---

## Profile activation and persistence

### Startup precedence
From the first daemon release after 2.56.3 (DEC-435), the daemon uses the first of these that
loads. A source that names no file, or a file that will not load, is logged and the next is tried.
Daemon 2.56.3 and older stop at a CLI or environment profile that will not load, with no profile
active, and save a CLI or environment choice to `daemon_state.json` as though it had been
activated:
1. CLI: `--profile quiet` or `--profile-file /path/to/profile.json` — whichever comes first. `quiet`
   is the **file stem** (`quiet.json`) in a search path, not the profile's display name.
2. Environment: `OPENFAN_PROFILE=quiet` (also a file stem)
3. Persisted state: `/var/lib/control-ofc/daemon_state.json` — written **only** by
   `POST /profile/activate` and `/deactivate` (the GUI, the tray). `--profile` and
   `OPENFAN_PROFILE` are never saved there: while one is set it wins on every start, and removing it
   brings back the last profile activated from the GUI.
4. None → no curve is evaluated until a profile is activated, but the daemon's thermal safety still acts on its own: an emergency takes every writable fan to 100 % and gives each one back when it ends (DEC-382). The 40 % no-sensor floor, by contrast, needs a profile's fans to act on. The GUI never drives PWM — the daemon's profile engine is the sole writer (DEC-159 / DEC-165).

### GUI activation flow
When the user activates a profile in the GUI:
1. GUI saves the profile to its local draft cache (`~/.config/control-ofc/profiles/<id>.json`) **and** uploads it to the daemon — the **store of record** (DEC-160) — via `PUT /profiles/<id>` (or `POST /profiles` to create it). The daemon writes it into its own store dir (`/var/lib/control-ofc/profiles/`).
2. GUI calls `POST /profile/activate {"profile_path": "<path to its local copy>"}` — the path must lie **within a daemon search dir**, and the GUI registers its own profile folder, `~/.config/control-ofc/profiles`, as one on every connect, so the path is accepted while that registration stands. (Activation also accepts a `profile_id`, which the daemon resolves from its own store and search dirs; the PWM Test Report's **Re-apply profile** activates that way.)
3. Daemon validates, applies, and persists the active selection to `/var/lib/control-ofc/daemon_state.json`
4. Profile survives daemon restart, reboot, and GUI close; the daemon can re-hydrate the full profile document from its own store via `GET /profiles/<id>` (DEC-175)

### Deactivating a profile
Two ways to leave profile mode without restarting the daemon:
- **Activate a different profile** — `POST /profile/activate` replaces the current one.
- **Deactivate entirely** — `POST /profile/deactivate` (body ignored) clears the active profile and every standing manual override (DEC-218), after which the daemon evaluates no fan curve and hands back the motherboard headers it took (DEC-382); its thermal emergency still acts on its own. It is idempotent (deactivating when none is active is a success no-op), persists the cleared state so a restart does not resurrect the profile, and releases the daemon's internal `profile-engine` hwmon lease (the GUI holds no lease — DEC-097/DEC-165). Response: `{"deactivated": true, "previous_profile_id": ..., "previous_profile_name": ...}`.

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

## System tray and single-instance behaviour

From daemon v2.44.0 / GUI v2.68.0 (DEC-352) the **daemon package** installs
`control-ofc-tray`, a StatusNotifierItem client that starts at login via
`/etc/xdg/autostart/control-ofc-tray.desktop`. It is not part of this GUI and
holds no state; it reads `GET /status` + `GET /profiles` when its menu opens and
can activate or deactivate a profile. Operationally it matters only in that:

- **It can launch this GUI**, on a left click. The StatusNotifierItem protocol
  has no double-click, so Plasma delivers `Activate` twice on a double click.
- **This GUI is therefore single-instance** (`services/single_instance.py`). A
  second launch — from the tray, the application menu, or a terminal — raises
  the running window instead of starting a second application. Demo and live
  keep separate keys, so `control-ofc-gui --demo` is never blocked by a live GUI.
- The instance socket lives in `$XDG_RUNTIME_DIR` and is cleared at logout. A
  crash leaves the socket behind; the next launch detects that it is dead and
  reclaims it, so no cleanup is needed.
- **Known limitation:** on Wayland, raising the existing window is subject to
  KWin's focus-stealing prevention. It usually comes forward; where it does not,
  the task-bar entry is marked as demanding attention instead. This cannot be
  fixed from either side — SNI's `Activate` carries no xdg-activation token.

Tray troubleshooting (unit control, logs, disabling autostart) is in
`man control-ofc-tray`, not here — it is a daemon-package component.

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
- The daemon makes **one** detection attempt at startup, then keeps looking in
  the background for 60s — 180s if you have set `[serial] port`. Plugging the
  controller in during that window is adopted within a few seconds, with no
  restart. See [How the OpenFanController is adopted at boot](#how-the-openfancontroller-is-adopted-at-boot-dec-291--dec-361)
- After the window closes: use **Rescan Hardware** in the GUI footer, or
  `curl -X POST --unix-socket /run/control-ofc/control-ofc.sock http://localhost/fans/openfan/rescan`
- `sudo journalctl -u control-ofc-daemon | grep -i openfan` — an adopted controller
  logs `OpenFanController connected on <port>`. On a machine with none, the
  absence is logged at `info`, not as a warning: it is optional hardware

### hwmon fans not detected
- Check sysfs exists: `ls /sys/class/hwmon/`
- Check PWM files: `ls /sys/class/hwmon/hwmon*/pwm[0-9]`. (A plain
  `find /sys/class/hwmon -name 'pwm[0-9]'` finds nothing: each `hwmonN` entry is a symlink,
  which `find` does not follow.)
- A **sensor** chip whose driver you have just loaded appears after a rescan —
  **Rescan Hardware** in the GUI footer, or
  `curl -X POST --unix-socket /run/control-ofc/control-ofc.sock http://localhost/hwmon/rescan`.
  **New PWM headers need a daemon restart** (`sudo systemctl restart control-ofc-daemon`):
  the rescan lists them but does not add them to the running controller.

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

### A fan's duty keeps being changed back

If System State shows a duty-drift card for a header, the daemon has stopped correcting it
(`duty_not_holding`, DEC-406, daemon ≥ 2.53.0): three times in a row it re-wrote the curve's
duty and the next tick's readback showed something else had changed it again. Something else
is writing that header — usually the BIOS/EC's own fan control, or another fan tool (`fancontrol`,
CoreCtrl, a vendor utility). The daemon still commands the curve but no longer re-asserts it, so
the fan runs at whatever the other writer leaves. Stop the other writer (for the BIOS, set the
header's Smart Fan mode to manual or full speed) and the flag clears when a readback agrees
again or the command next changes. The diagnostics spec's
[duty-drift card](07_Diagnostics_Spec.md#the-duty-drift-card-on-system-state-dec-408-daemon--2530)
has the detail.

### GUI shows "Daemon disconnected"
- Check daemon is running: `systemctl is-active control-ofc-daemon`
- Check socket exists: `ls -la /run/control-ofc/control-ofc.sock`
- Check socket permissions (GUI user must be able to connect)

### Profile not restoring after reboot
- Check persisted state: `sudo cat /var/lib/control-ofc/daemon_state.json` (the state
  directory is `0700`, root only)
- Check profile file exists at the path stored in state
- Check daemon logs for profile loading errors on startup

---

## Safety behaviour

The daemon enforces a single thermal safety rule (non-negotiable, not configurable):
- **Trigger**: hottest CPU temperature reaches the emergency limit. That limit is **per-machine** (DEC-308): 105°C is the floor and the fallback, raised to `min(CPU-reported design ceiling + 5°C, 115°C)` where the kernel publishes the ceiling (`tempN_crit`). `GET /diagnostics/hardware` reports the value in use — read it there, never assume 105
- **Action**: Force every OpenFan channel and writable hwmon header the machine has to 100% PWM. GPU fans are excluded — there is no GPU emergency threshold; AMD PMFW firmware protects the GPU independently (DEC-130)
- **Hold**: Until a fresh reading at or below 80°C. A CPU sensor that stops updating or disappears does not end it — the emergency stays at 100% while the daemon is blind (DEC-386)
- **Release**: Straight back to active profile control — there is no recovery rung since DEC-386. Every other fan the emergency took is given back (DEC-382)
- **Fallback**: With nothing latched, apply a 40% PWM floor to the fans the active profile controls if no CPU reading is fresh for 5 consecutive poll cycles. A control skipped because its sensor is gone keeps its fans at their last duty under it (DEC-386). An OpenFan channel whose last duty is unknown because the controller reconnected or the host resumed goes to 100% instead (DEC-401, daemon ≥ 2.51.3); a duty unknown for any other reason takes the bare 40%. Fans no profile controls stay under their firmware curve, and with no profile active nothing is forced (DEC-382)
- **Floors, not replacements** (DEC-307): every duty above is a floor over the active profile's output — each fan gets `max(commanded, forced)`, and only the 100% emergency also reaches a fan no control commands (DEC-382). The ladder can only ever raise a fan
- **After a reconnect or resume**: the daemon treats each OpenFan channel's duty as unknown until it next writes the channel (DEC-393, daemon ≥ 2.51.1). If an emergency starts in that window, a channel no control commands has no duty to be given back, so it stays at 100% when the emergency ends
- **Visibility**: `GET /status` reports `thermal_state` (`normal` / `emergency` / `no_sensor_fallback`, and `recovery` from daemons before DEC-386); the GUI has no fan control to pause and only **shows** a poll-driven thermal-protection banner while protection is active (DEC-165, superseding the retired DEC-132 GUI stand-down)

There are no per-*header* PWM floors: the daemon reports `min_pwm_percent: 0` for every hwmon header. The **role-aware minimum** is different. The GUI *bakes* a role-aware default into each control's `LogicalControl.minimum_pct` (30% for CPU/pump-labelled members, 20% for chassis/openfan, 0% for GPU-only — DEC-095), and as of 2.0.0 the **daemon enforces and backstops** it (DEC-162): a profile whose pump/CPU control sets `minimum_pct` below the hard 30% floor (`HARD_PUMP_CPU_FLOOR_PCT`) is rejected with `400 validation_error` (`FLOOR_TOO_LOW`), and the profile engine independently re-clamps every eval tick (`member_effective_floor` → `max(minimum_pct, 30%)`). So floor enforcement is **not** purely the GUI's responsibility — the daemon does refuse and re-floor on the role-aware minimum.

The thermal trigger/release thresholds are reported by `GET /diagnostics/hardware` in its thermal-safety section (`emergency_threshold_c`, `release_threshold_c`, plus `state` and `cpu_sensor_found`). They are **not** in `GET /capabilities`: the `limits` object there carries `pwm_percent_min`, `pwm_percent_max`, `openfan_stop_timeout_s` and, from daemon 2.55.0, `diagnostic_max_temp_c` (the PWM Test Report's consent limit, DEC-411). The live override state is also surfaced as `thermal_state` in `GET /status`.
