# 08 — API Integration Contract

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
This file defines how the GUI should consume the current daemon/API safely and predictably.

## General rules
1. All I/O goes through the API client layer.
2. All responses are parsed into typed internal models.
3. The UI never binds directly to raw JSON.
4. The GUI must handle partial capability availability.
5. The GUI must gracefully support absent hardware.

## Quick reference — curl examples

All endpoints use HTTP over Unix socket. The `SOCK` variable shortens examples:

```bash
SOCK="/run/control-ofc/control-ofc.sock"

# Read endpoints
curl -s --unix-socket $SOCK http://localhost/capabilities | jq .
curl -s --unix-socket $SOCK http://localhost/status | jq .
curl -s --unix-socket $SOCK http://localhost/sensors | jq .
curl -s --unix-socket $SOCK http://localhost/fans | jq .
curl -s --unix-socket $SOCK http://localhost/poll | jq .
curl -s --unix-socket $SOCK http://localhost/hwmon/headers | jq .
curl -s --unix-socket $SOCK http://localhost/profiles | jq .
curl -s --unix-socket $SOCK 'http://localhost/sensors/history?id=cpu_tctl&last=50' | jq .
curl -s --unix-socket $SOCK http://localhost/profile/active | jq .
curl -s --unix-socket $SOCK http://localhost/diagnostics/hardware | jq .

# Write endpoints (the GUI expresses intent — the daemon writes PWM)
curl -s --unix-socket $SOCK -X POST http://localhost/profiles \
  -H 'Content-Type: application/json' -d @profile.json
curl -s --unix-socket $SOCK -X POST http://localhost/profile/activate \
  -H 'Content-Type: application/json' -d '{"profile_id": "quiet"}'
curl -s --unix-socket $SOCK -X POST http://localhost/control/cpu_fans/override \
  -H 'Content-Type: application/json' -d '{"pwm_percent": 60}'
curl -s --unix-socket $SOCK -X POST http://localhost/fans/amd_gpu:0000:2d:00.0/identify \
  -H 'Content-Type: application/json' -d '{"action": "stop"}'
curl -s --unix-socket $SOCK -X POST http://localhost/hwmon/rescan
curl -s --unix-socket $SOCK -X POST http://localhost/gpu/0000:2d:00.0/fan/reset
```

## Read endpoints

### GET /capabilities
Use this at startup and on explicit refresh to determine:
- API version
- daemon version
- IPC transport
- device presence
- feature support
- min/max limits

This endpoint should drive:
- feature enablement
- write-capability UI state
- settings field validation
- source-specific labels and messages

Notable fields:
- `devices.openfan.channels` is always **10** in V1 (OpenFan v1 hardware has
  10 channels). The field is hardcoded daemon-side — do not assume it can
  vary per device.
- `devices.amd_gpu.pci_id` (legacy) and `devices.amd_gpu.pci_bdf` (canonical)
  both carry the same PCI BDF address during the transition window; GUI
  parsers accept either name (see DEC-042 and the 2026-04-22
  contract-mismatch resolution).
- `devices.amd_gpu.kernel_warnings` (DEC-098, daemon ≥ 1.6.1) is a list of
  `{id, severity, message}` entries describing kernel-version regressions
  applicable to the active GPU (e.g. RDNA3/4 hard-hang on Linux 6.19,
  R9700 SMU mismatch on 7.0). Field is omitted entirely when empty so
  pre-1.6.1 daemons (which don't set it) yield an empty list on the GUI
  side without parser changes. The GUI surfaces `high` and `critical`
  entries as a one-time popup gated by
  `app_settings.acknowledged_kernel_warnings`.
- `devices.intel_gpu` (DEC-121, daemon ≥ 1.12.0) describes an Intel **discrete**
  GPU (Arc — `xe`/`i915`). Read-only monitoring: fields are `present`,
  `model_name`, `display_label`, `pci_id`/`pci_bdf`, `pci_device_id`, `driver`
  (`"xe"`/`"i915"`), `fan_control_method` (always `"read_only"` or `"none"` —
  there is no userspace fan write path), `fan_rpm_available`, and `is_discrete`.
  There is deliberately **no** `fan_write_supported`/PMFW/overdrive/zero-RPM/
  kernel-warning field — Intel GPU fans are firmware-managed and never writable.
  Omitted/`present:false` on daemons that predate the field (parser-tolerant).
- `devices.aio_hwmon` (DEC-156, daemon ≥ 1.18.0) describes hwmon-attached liquid
  cooling (AIO). Dynamic object: `present` (a liquid cooler or coolant sensor is
  detected), `status` (`"supported"` = a writable AIO pump/fan header exists;
  `"monitor_only"` = detected but nothing writable, e.g. NZXT Kraken2 or coolant
  sensing only — never offer control; `"unsupported"` = nothing detected),
  `pump_writable`, and `coolant_available`. Additive superset of the legacy
  `{present, status}` shape — pre-1.18.0 daemons send only the first two and the
  GUI defaults the rest to `false`. `devices.aio_usb` stays
  `{present:false, status:"unsupported"}` permanently: USB-only coolers
  (liquidctl/USB-HID) are out of scope (the daemon never opens USB-HID).

The top-level **`control` block** (daemon ≥ 1.19.0, DEC-159/160) advertises which
control responsibilities the daemon can own. Each flag defaults to the
pre-migration/safe value when absent — a pre-1.19 daemon sends no block, so the
GUI treats every flag as false / old behaviour (AIP-180):
- `profile_storage` (bool) — daemon exposes the `/profiles` CRUD + validate
  surface; `true` since **1.19.0** (DEC-160). The GUI gates its one-time
  profile-import offer on this flag.
- `curve_evaluation` (bool) — daemon evaluates fan curves headlessly; always
  `true` (the profile engine has done this since DEC-096).
- `manual_override` (bool) — daemon exposes the manual-override API (DEC-163);
  `false` in 1.19–1.20, **`true` since 1.21.0**. Gates the GUI's Manual-card
  override UI.
- `fan_identify` (bool) — daemon exposes the fan-identify API (DEC-166);
  `false` in 1.19–1.20, **`true` since 1.21.0**. Gates the Fan Wizard's
  daemon-mediated identify.
- `min_supported_gui` (string) — coarse GUI-version floor for a legible
  hard-fail; empty until the 2.0.0 cutover sets it (drives the GUI's startup
  capability gate).

### Per-call timeouts (DEC-098 / DEC-099)

The GUI's `DaemonClient` accepts a `timeout=` kwarg on every method that
might exceed the global `API_TIMEOUT_S = 5.0`. Endpoints with known long
upper bounds:

- `verify_hwmon_pwm` — daemon sleeps **6 s** (raised from 3 s in DEC-101);
  client timeout is **12 s**. The control-loop pause-safety auto-resume
  must stay strictly above the daemon wait — see
  `control_loop.VERIFY_PAUSE_SAFETY_MS` (currently 9 s).
- `set_openfan_pwm`, `set_hwmon_pwm`, `set_gpu_fan_speed` (write fast
  path) — client timeout is **2s** with one retry on `DaemonTimeout`.

`DaemonTimeout` is a distinct subclass of `DaemonError` (separate from
`DaemonUnavailable`) so callers can distinguish "the daemon is slow" from
"the daemon is gone."

### Daemon endpoints the GUI does not call

As of 2.0.0 the GUI is not a writer, so it does not call the daemon's runtime
PWM-write endpoints — the daemon's own engine drives fans. A few endpoints
remain on the daemon surface but are unused (or only curl-exercised) by the GUI:

- `POST /fans/openfan/{channel}/calibrate` — long-running PWM-to-RPM
  calibration sweep. The Fan Wizard provides a guided identify alternative;
  full calibration as a built-in UI flow is deferred.
- `POST /fans/openfan/{channel}/target_rpm` — closed-loop RPM target. The
  daemon engine is duty-cycle based; closed-loop control is out of scope.

(The bare PWM and hwmon-lease endpoints the v1 GUI used to call were retired at
2.0.0 — see "Write endpoints" below.)

### GET /status
Use for:
- top-level health
- subsystem status/freshness
- queue depth
- dropped counters
- last error summary
- daemon thermal override state

This endpoint feeds:
- header status strip
- diagnostics overview
- warning banners
- the poll-driven thermal-protection banner (DEC-165, superseding the DEC-132 GUI stand-down)

`overall_status` and each subsystem `status` is one of `"ok" | "warn" | "crit"`
(overall is the worst of all subsystems). The GUI treats `"ok"` as healthy and any
other value as a warning; an absent/unparseable field falls back to `"unknown"`.
Emitted by the daemon's `HealthStatus::Display` and pinned on both sides by
`health_status_display_wire_strings` (daemon) and the dashboard health tests (GUI).

`thermal_state` (daemon ≥1.13.0, additive — `api_version` unchanged) is one of
`"normal" | "recovery" | "emergency" | "no_sensor_fallback"`. While it is not
`"normal"` the daemon is forcing all OpenFan+hwmon PWM (GPU fans excluded —
DEC-130) and holding the hwmon lease as `thermal-safety`; the GUI has no loop
to stand down (DEC-165) and simply shows a single poll-driven thermal warning.
Older daemons omit the field — the GUI defaults it to `"normal"`.

`overrides` and `fan_identify` (daemon ≥ 1.21.0, additive — `api_version`
unchanged, omitted when empty) make `/status` the poll-authoritative source for
active manual overrides and fan-identify holds (DEC-163 / DEC-166): `overrides[]`
of `{control_id, pwm_percent, expires_in_secs}` and `fan_identify[]` of
`{fan_id, expires_in_secs}`. Both arrays are absent on daemons < 1.21.0 and when
nothing is held; the GUI defaults them to empty.

### GET /sensors
Use as the primary sensor snapshot source.
Expected fields:
- id
- kind — one of `cpu_temp`, `mb_temp`, `disk_temp`, `gpu_temp`, or `coolant_temp`
  (DEC-156, daemon ≥ 1.18.0 — liquid-cooler coolant temperature, surfaced by the GUI
  as a first-class **Liquid** sensor). The GUI treats `kind` as an opaque string and
  layers its own richer `sensor_knowledge` classification on top.
- label
- value_c
- source — `"hwmon"`, `"amd_gpu"`, or `"intel_gpu"` (DEC-121; Intel discrete GPU temps via the `xe`/`i915` hwmon node, kind `gpu_temp`).
- age_ms
- rate_c_per_s (optional, float) — smoothed temperature change rate in °C/s; omitted (`skip_serializing_if`) until computable
- session_min_c (optional, float) — lowest value seen for this sensor since daemon start; omitted until set
- session_max_c (optional, float) — highest value seen for this sensor since daemon start; omitted until set
- chip_name — hwmon driver name from sysfs (e.g. `k10temp`, `nct6798`, `it8689`). Always present; `"amdgpu"` for AMD GPU sources and `"xe"`/`"i915"` for Intel GPU sources.
- temp_type (optional, integer) — thermistor type code from `tempN_type` sysfs. Values: 3 = diode, 4 = thermistor, 5 = AMD TSI, 6 = Intel PECI. Absent when the driver does not expose type information.
- thresholds (optional object) — DEC-117 curated subset of hwmon temperature-threshold sysfs attributes. The daemon reads these once at discovery and re-reads them on `POST /hwmon/rescan`. Implausible values (<-50 °C, >200 °C) and the `it87`-family `tempN_max == 0` placeholder are filtered at the daemon side. The whole object is omitted when no attribute was readable for this sensor (k10temp typically exposes none). When present, every sub-field is also omitted-when-None so the on-wire shape is the minimal honest set. Sub-fields (all optional):
  - `max_c`, `min_c` — typical upper/lower warning thresholds (°C)
  - `crit_c`, `crit_hyst_c` — critical threshold and hysteresis (°C)
  - `emergency_c`, `emergency_hyst_c` — emergency threshold and hysteresis (°C)
  - `lcrit_c` — lower critical threshold (°C, cold-side)
  - `offset_c` — userspace-applied calibration offset (°C)
  - `alarm`, `max_alarm`, `crit_alarm` — chip-asserted alarm bits (bool); sampled at discovery only, not refreshed per poll cycle
  - `fault` — chip-reported sensor fault (bool)

Entries are sorted by `id` — deterministic across daemon restarts and rescans
(DEC-146; fans were already sorted, sensors now match).

### GET /fans
Use as the primary current fan state source.
Expected fields:
- id
- source
- rpm (optional, omitted when unavailable)
- last_commanded_pwm (optional, omitted until first write)
- age_ms
- stall_detected (optional bool) — daemon-asserted; set when commanded PWM ≥5% but measured RPM is zero for ≥2 cycles. Surfaced by the GUI as an `error`-level warning.

Note: fans do **not** include `label` or `kind` from the daemon. Display names come from: user alias (GUI-owned) > hwmon header label (for hwmon fans) > fan id.

Fan `source` is `"openfan"`, `"hwmon"`, `"amd_gpu"`, or `"intel_gpu"`. GPU fan IDs embed the PCI BDF: `amd_gpu:{bdf}` and `intel_gpu:{bdf}`. Intel GPU fans (DEC-121) are **read-only** — `rpm` is reported (from `fan1_input`) but `last_commanded_pwm` is always absent; the GUI must never issue a write to an `intel_gpu:` target.

### GET /hwmon/headers
Use to discover:
- header ids
- labels
- chip names
- indices
- enable support
- rpm support
- min/max PWM percentages
- `is_writable` — whether the daemon believes the `pwmN` file is writable.
  After DEC-102 the daemon excludes `chip_name == "amdgpu"` from this list
  entirely (GPU fans are owned by the GPU subsystem; their hwmon shadow
  file is read-only on RDNA3+ kernels and writes return `EACCES`). Any
  other chip whose `pwmN` lacks write permission appears here with
  `is_writable: false` — the GUI must not offer such headers in the
  member-picker, and the daemon rejects a profile that binds one (DEC-102 —
  `400 feature_unavailable` at validation / activation).
- `pwm_mode` (optional integer) — `0` = DC (voltage) mode, `1` = PWM
  mode, omitted when the chip does not expose `pwmN_mode`. Consumed by
  the dashboard fan table and the diagnostics hwmon panel to label
  DC-driven fans differently from PWM-driven ones (`models.py`
  `HwmonHeader.pwm_mode`, daemon `responses.rs`
  `PwmHeaderEntry.pwm_mode`).
- `is_aio` (boolean, DEC-156, daemon ≥ 1.18.0) — `true` when the header belongs to a
  liquid cooler (NZXT Kraken / Aquacomputer). Daemon-authoritative hint so the GUI can
  cluster and floor pumps without re-deriving hardware knowledge; per-driver pump
  writability still rides `is_writable`. Omitted/`false` on daemons that predate it.

### GET /poll
Combined batch endpoint returning status + sensors + fans in one call.
Reduces per-cycle HTTP overhead from 3 requests to 1.
GUI falls back to individual endpoints if `/poll` is not available.

### GET /sensors/history?id=...&last=N
Returns per-sensor time-series history from the daemon's ring buffer.
`last` is parsed up to a server-side cap of 1000, but the daemon's per-sensor history ring holds at most **250** samples, so a request never returns more than 250 regardless of `last` (which itself defaults to 250).
Used to pre-fill the GUI's `HistoryStore` on first connection so the timeline chart
shows data immediately instead of starting empty.

### GET /diagnostics/hardware

Comprehensive hardware readiness report. Stable v1 fields documented in
the daemon's `responses.rs::HardwareDiagnosticsResponse`. New optional
fields added in DEC-101 — both serialise with
`skip_serializing_if = "Vec::is_empty"`, so older daemons emit nothing
and the GUI parser defaults to `[]`:

- `expected_chips: list[str]` — chip names this DMI board is known to
  expose, sourced from a curated dual-chip board lookup. Lower-cased,
  no `E` suffix (matches the `chip_name` format under
  `hwmon.chips_detected`). Empty for boards not in the lookup. The GUI
  uses `set(expected_chips) − set(detected_chip_names)` to drive a
  Fans-tab warning banner with the dual-chip remediation (driver update
  first; the `mmio=on` modprobe.d line only on pre-2026-03 driver
  builds — DEC-144).
- `kernel_detected_chips: list[str]` — best-effort kernel-level chip
  detection parsed from `/dev/kmsg` `it87:` lines. Populated when the
  kernel ring buffer is readable (Arch default
  `kernel.dmesg_restrict=0`); empty otherwise. Useful for distinguishing
  "kernel saw the chip but driver did not bind" from "kernel never saw
  the chip"; not authoritative — the source of truth for "what works"
  is `hwmon.chips_detected`.

Additional optional field added in DEC-105 (same wire convention —
`skip_serializing_if = "Vec::is_empty"`, so older daemons emit nothing
and the GUI parser defaults to `[]`):

- `module_collisions: list[ModuleCollisionInfo]` — pairs of simultaneously
  loaded driver modules known to race for the same chip. Each entry has
  `module_a`, `module_b`, `severity` (`"critical" | "high" | "medium"`),
  `summary`, and `remediation` fields. The flagship entry is
  `(nct6687, nct6775)` at CRITICAL severity — these two drivers overlap
  on chip ID `0xd450` (NCT6797D's legitimate ID) and concurrent loading
  can corrupt non-volatile fan registers on common AM4/AM5 MSI boards
  (NCT6797D ships on the B450M MORTAR per its upstream lm-sensors
  config). The GUI renders this as a CRITICAL banner above the existing
  module-conflict label, suppresses the GUI-only `CONFLICTING_MODULE_SETS`
  banner for the same pair (avoids two warnings for one problem), and
  refuses no writes but discourages PWM writes until the user resolves
  the load ordering. All daemon-supplied strings in this field are
  HTML-escaped before interpolating into the Qt RichText label.

  **DEC-106 refinement:** the daemon suppresses the `(nct6687, nct6775)`
  entry when `hwmon.chips_detected` shows two or more distinct `nct6`-
  family chips at distinct `device_id`s. This avoids a false CRITICAL
  banner on legitimate dual-Nuvoton boards (e.g. ASRock X870E Taichi
  Lite, which ships NCT6686 at 0x0a20 + NCT6799 at 0x0290, each bound
  by its own driver). The single-chip brick scenario from DEC-105 still
  emits CRITICAL. Older daemons (pre-DEC-106) emit the broader
  result; the GUI parser handles both shapes identically.

Additional optional field added in DEC-110 (`skip_serializing_if =
"String::is_empty"` — older daemons emit nothing and the GUI parser
defaults to `""`):

- `cpu_vendor: str` — CPU vendor normalised from `/proc/cpuinfo`
  `vendor_id`: `"Intel"` (for `GenuineIntel`), `"AMD"` (for
  `AuthenticAMD` and `HygonGenuine`), or `""` when the file is
  unreadable or the vendor is unrecognised (hypervisor strings etc.).
  The GUI uses this to scope platform-specific vendor quirks. Quirks
  that declare a `platform` scope (`"intel"` / `"amd"`) match only when
  `cpu_vendor` is non-empty AND matches; empty `cpu_vendor` suppresses
  platform-scoped quirks (the truthful direction: "we don't know, so
  don't claim"). Quirks without a `platform` scope fire on any vendor,
  preserving pre-DEC-110 behaviour.

  Also added to `KernelModuleInfo` indirectly: the daemon's
  `KNOWN_MODULES` table now lists `intel_pch_thermal` (mainline=true) so
  Intel users see it reported honestly. `x86_pkg_temp` is deliberately
  excluded because its kernel driver registers with `.no_hwmon = true` —
  it appears as a thermal zone only, not under `/sys/class/hwmon`.

Additional optional fields added in DEC-119 (daemon ≥ 1.10.0). All use
`#[serde(default)]` / `skip_serializing_if`, so older daemons emit nothing
and the GUI parser defaults safely:

- Top-level `amd_pci_devices: list[AmdPciDeviceInfo]` — AMD VGA-class PCI
  devices and their driver binding, detected by scanning
  `/sys/bus/pci/devices` **independently of hwmon**. Each entry has
  `pci_bdf`, `pci_device_id`, `driver: str | None` (bound driver basename,
  e.g. `"amdgpu"` / `"vfio-pci"`, or absent when unbound), `amdgpu_bound:
  bool`, and `hwmon_present: bool`. This is the only place a GPU whose
  `amdgpu` driver failed to bind (blacklist, KMS failure, passthrough)
  appears — such a device produces no hwmon node, so the `gpu` field is
  `null`. Omitted (→ `[]`) when no AMD VGA device exists.
- Top-level `amdgpu_module_loaded: bool` — whether `/sys/module/amdgpu`
  exists. Paired with `amd_pci_devices` to distinguish "module not loaded"
  (blacklist / missing module) from "loaded but unbound" (passthrough / KMS
  failure). Defaults `false`.
- `gpu.fan_speed_min_pct` / `gpu.fan_speed_max_pct: int | None` — PMFW
  `fan_curve` `OD_RANGE` fan-speed bounds (percent, typically `15` / `100`
  on RDNA3+). The firmware-enforced minimum is the real reason a PMFW GPU
  fan cannot be driven below ~15% via the curve; surfaced so it is not
  mistaken for a GUI/daemon clamp. `null` for non-PMFW GPUs.
- `gpu.fan_minimum_pwm: int | None` — best-effort percent parse of the
  `gpu_od/fan_ctrl/fan_minimum_pwm` attribute. `null` when absent /
  unparseable.
- `gpu.amdgpu_driver_bound: bool` — whether `amdgpu` is bound to this GPU's
  PCI device (cross-referenced from `amd_pci_devices`). Defaults `true` (an
  hwmon node implies a bound driver).
- `gpu.kernel_warnings: list[KernelWarning]` — the same advisory catalogue
  as `/capabilities.amd_gpu.kernel_warnings` (id / severity / message),
  duplicated so the diagnostics support bundle is self-contained. Omitted
  (→ `[]`) when none apply. Hand-parsed by the GUI (nested objects can't
  round-trip through the flat dataclass unpack).
- `intel_gpu: object | null` (DEC-121, daemon ≥ 1.12.0) — Intel discrete GPU
  diagnostics: `pci_bdf`/`pci_id`, `pci_device_id`, `pci_revision`, `model_name`,
  `driver` (`"xe"`/`"i915"`), `fan_control_method` (`"read_only"`/`"none"`),
  `fan_rpm_available`, and `fan_control_note` (a daemon-supplied, display-ready
  explanation of why fan control is unavailable). `null` when no Intel GPU is
  present or the daemon predates the field.

### GET /events (SSE) — daemon-only, not consumed by GUI
The daemon exposes a Server-Sent Events stream at `GET /events` for other clients
(custom integrations, future tooling, etc.).
- Event type: `update` (combined sensors + fans payload)
- Heartbeat every 5 seconds

The V1 GUI does **not** consume this stream. All data flows through the 1 Hz
`PollingService` using the combined `GET /poll` endpoint. The planned
`EventStreamService` was never wired up and its `httpx-sse` dependency was
removed (see DEC-023, DEC-024, CHANGELOG v1.0.0). SSE consumption is tracked as
deferred work in `docs/14_Risks_Gaps_and_Future_Work.md`.

## Write endpoints

As of **2.0.0** the daemon is the sole writer (DEC-159, DEC-165). The GUI has **no bare PWM write
surface** — it expresses control as *intent* (activate a profile, take an expiring override, identify
a fan) and runs a few diagnostics / maintenance calls (calibrate, verify, GPU reset, rescan).

**Retired at 2.0.0** — no longer routed by the daemon (the `gui_active` defer window they lived behind
is gone):
- `POST /fans/openfan/{ch}/pwm` and `POST /fans/openfan/pwm` — bare per-channel + set-all PWM
- `POST /fans/openfan/{ch}/target_rpm` — closed-loop RPM target (never consumed)
- `POST /hwmon/{header_id}/pwm` — bare hwmon PWM
- `POST /hwmon/lease/take` / `/release` / `/renew` and `GET /hwmon/lease/status` — the GUI holds no
  lease; the daemon manages it internally
- `POST /gpu/{gpu_id}/fan/pwm` — bare GPU static-speed write (replaced by override / identify)

### OpenFan calibrate
- `POST /fans/openfan/{ch}/calibrate` — PWM-to-RPM calibration sweep

The calibration endpoint runs a long-running sweep (steps × hold_seconds) that sets PWM from 0→100%, reads RPM at each step, and returns a mapping. Safety: aborts on thermal limit (85°C), restores pre-calibration PWM on every exit path — completion, thermal abort, or a failed PWM write mid-sweep (DEC-134).

### Hwmon PWM verify
- `POST /hwmon/{header_id}/verify` — empty body (no `lease_id` as of 2.0.0 — DEC-165)

Probes whether a `pwmN` write actually moves the fan, to detect BIOS/EC
interference. The daemon writes a test PWM, sleeps
`VERIFY_WAIT_SECONDS = 6 s` (raised from 3 s in DEC-101 — slow-spinning
fans need settle time), reads back `pwmN` / `pwmN_enable` / `fanN_input`,
then restores the prior PWM. The daemon runs the probe under its **own
internal verify lease** — the GUI sends no `lease_id` and holds no lease.
The GUI sends a **12 s** per-call timeout (`verify_hwmon_pwm`, `client.py`)
to cover the worst-case ~7.5 s round-trip. See the "Per-call timeouts
(DEC-098 / DEC-099)" note above.

Response (daemon `HwmonVerifyResponse` ↔ GUI `HwmonVerifyResult`):
- `header_id: str`
- `result: str` — `"effective"`, `"pwm_enable_reverted"`,
  `"pwm_value_clamped"`, `"no_rpm_effect"`, or `"rpm_unavailable"`
- `initial_state`, `final_state` — `{pwm_enable, pwm_raw, pwm_percent,
  rpm}`, each sub-field optional
- `test_pwm_percent: int`, `wait_seconds: int`, `details: str`
- `restore_failed: bool` — omitted when false (`skip_serializing_if`);
  when true, the header was left at the test value because the
  restore-to-original write failed, so the caller should write the
  desired PWM explicitly rather than trust the verify call to have
  restored it.

Errors: `404 validation_error` (unknown header — the wire `code` is
`validation_error`, not `not_found`, which is reserved for unknown routes),
`503 hardware_unavailable` (no hwmon headers or controller absent). The pre-2.0
`403 lease_required` no longer applies — the daemon owns the verify lease.

### Profile storage (CRUD — DEC-160, daemon ≥ 1.19.0)
The daemon is the profile **store of record** (`/var/lib/control-ofc/profiles/`). The GUI uploads and
validates full profile documents; it keeps a local draft cache but does not treat it as authoritative.
- `GET /profiles` — list stored profiles; `GET /profiles/{id}` — fetch one
- `POST /profiles` — create (`409 already_exists` on a duplicate id)
- `PUT /profiles/{id}` — replace stored desired-state (no hot-reload — re-activate to apply)
- `DELETE /profiles/{id}` — `409 profile_in_use` if it is the active profile
- `POST` / `PUT` accept `?validate_only=true` — runs the real validation, persists nothing
- Validation returns hard `errors` (reject) + soft `warnings` (accept). An unknown `sensor_id` is a
  warning, not an error (profiles stay portable across machines). Field-level detail rides the error
  envelope's `field_violations` (see Error model).

### Profile activation
- `POST /profile/activate` — `{"profile_path": "/path/to/profile.json"}` or `{"profile_id": "quiet"}`
  - Daemon validates, applies, and persists active profile to `/var/lib/control-ofc/daemon_state.json`
  - Returns `{"activated": true, "profile_id": "...", "profile_name": "..."}`
  - GUI must only update "active" state after daemon confirms success
- `POST /profile/deactivate` — body ignored (DEC-097, daemon v1.6.0+)
  - Clears the in-memory active profile, persists the cleared state, and
    releases the daemon's internal `profile-engine` lease so a later
    re-activate cleanly re-takes it. (There is no GUI lease to preserve as
    of 2.0.0 — DEC-165.)
  - Idempotent: returns `{"deactivated": true, "previous_profile_id": null,
    "previous_profile_name": null}` when no profile was active. With an
    active profile, the previous values are populated.
  - The GUI calls this when the user deletes the active profile so the
    daemon stops driving fans from a curve whose JSON has been removed.
- `GET /profile/active` — returns current active profile or `{"active": false}`
  - GUI queries on connect/reconnect to reflect daemon truth
  - Prevents stale widget state from misleading user

### Manual override (DEC-163, daemon ≥ 1.21.0)

Daemon-owned, expiring, fencing-guarded per-control pin. Replaces the GUI's transient Manual card.
The override **reverts to autonomous curve control** if the GUI stops renewing (deadman on the
daemon's clock); a stale token cannot re-pin (fencing).

- `POST /control/{control_id}/override` — body `{"pwm_percent": 0..100, "ttl_secs"?: N}` →
  `200 {"control_id","override_token","pwm_percent","ttl_secs","renew_secs","expires_in_secs"}`.
  `404` if the control is not in the active profile; `400` if `pwm_percent` out of range.
  The override PWM is still clamped by the daemon's hard pump/CPU floor (≥30 %) and GPU 0 % floor.
- `POST /control/{control_id}/override/renew` — body `{"override_token": N}` →
  `200 {"control_id","override_token","ttl_secs","expires_in_secs"}`. Renew at ~`renew_secs`
  (≈5 s, ⅓ of the 15 s TTL). `409 stale_fencing_token` if superseded; `404 override_expired` if it
  already lapsed (re-take, don't renew).
- `DELETE /control/{control_id}/override` — body `{"override_token": N}` →
  `200 {"control_id","released": bool}` (reverts immediately; `released:false` = nothing was held,
  idempotent). `409 stale_fencing_token` on a stale token.

The 105 °C thermal force always overrides an active override. No absolute max-duration cap — a live
renewing GUI holds indefinitely.

### Fan identify (DEC-166, daemon ≥ 1.21.0)

Per-fan stop/restore for the Fan Wizard, with a deadman auto-restore. Replaces the wizard's global
freeze + raw writes.

- `POST /fans/{fan_id}/identify` — body `{"action": "stop" | "restore", "ttl_secs"?: N}` →
  `200 {"fan_id","action","expires_in_secs"?}` (`expires_in_secs` present only for `stop`).
  `stop` forces the fan to 0 (floor-exempt — even a pump) and auto-restores after the deadman;
  `restore` clears it (the engine resumes the fan's curve value). `404` if the fan id is unknown on
  `stop`; `400` on a bad action. Only the named fan is affected — others keep curve-controlling.

### GPU fan reset
- `POST /gpu/{gpu_id}/fan/reset` — restore GPU fan to automatic mode (re-enables zero-RPM)
  - GUI caller: Diagnostics ▸ Troubleshooting ▸ *Restore GPU Fan to Automatic* (DEC-147 — disabled
    while the **active profile** owns an `amd_gpu:` member, since the daemon is actively driving it).

The bare `POST /gpu/{gpu_id}/fan/pwm` static-speed write is **retired at 2.0.0** — GPU fans are driven
by the daemon engine, with live manual control via the override API (DEC-163) and identification via
the identify API (DEC-166). No lease is required for GPU writes; the daemon applies a 5% minimum-change
threshold (DEC-070) to avoid SMU firmware churn.

**Zero-RPM handling.** The daemon engine honours each member's `fan_zero_rpm` boolean: when true it
preserves `fan_zero_rpm_enable` so the GPU stops the fan at its idle threshold (DEC-095 / DEC-053);
when false the fan spins continuously at the commanded speed. The default for omitted / legacy v3
profiles is false.

### GPU fan verify
- `POST /gpu/{gpu_id}/fan/verify` — empty body, **no lease** (DEC-120, daemon v1.11.0+)

Probes whether a GPU fan-control write actually takes effect, catching the
silent failures static diagnostics miss (`ppfeaturemask` bit 14 unset, SMU
firmware/driver mismatch, BIOS overdrive lock). The daemon drives a test speed
biased **upward** (idle/low → 75%, already-high → 100%, clamped to OD_RANGE so
cooling is never reduced), sleeps `GPU_VERIFY_WAIT_SECONDS = 6 s` (matching the
hwmon verify), reads back the applied PMFW `fan_curve` (or legacy `pwm1`) plus
`fan1_input` RPM and `fan_zero_rpm_enable`, then restores the prior state
(re-applies the last commanded speed if the GPU was being driven, else resets to
auto + re-enables zero-RPM). The GUI sends a **12 s** per-call timeout
(`verify_gpu_fan`, `client.py`); the daemon coordinates the probe with its own
engine, so the GUI no longer pauses any control loop.

Response (daemon `GpuVerifyResponse` ↔ GUI `GpuVerifyResult`) — **no
`api_version`**, symmetric with `HwmonVerifyResponse`:
- `gpu_id: str`
- `result: str` — `"effective"`, `"curve_not_applied"`, `"no_rpm_effect"`,
  `"zero_rpm_suppressed"`, `"rpm_unavailable"`, `"write_failed"`, or
  `"pwm_enable_reverted"` (legacy `pwm1` path only)
- `initial_state`, `final_state` — `{applied_speed_pct, rpm, pwm_enable,
  zero_rpm_enabled}`, each sub-field optional
- `test_speed_pct: int`, `wait_seconds: int`, `fan_control_method: str`,
  `details: str`
- `restore_failed: bool` — omitted when false (`skip_serializing_if`)

Errors: `400 feature_unavailable` (read-only GPU — no PMFW `fan_curve` and no
legacy `pwm1`+`pwm1_enable`), `404 validation_error` (unknown `gpu_id` — wire `code` is `validation_error`, not `not_found`). OD_RANGE
clamping and zero-RPM idle are reported as informational verdicts, not errors.
Old daemons predating the route answer `404`, which the GUI treats as
"unsupported" and hides the control.

### Hwmon rescan
- `POST /hwmon/rescan` — re-enumerate hwmon devices
  - Response: `{"api_version": N, "headers": [...], "count": N}` — same
    header entry shape as `GET /hwmon/headers`.
  - Called by Diagnostics ▸ Troubleshooting ▸ *Rescan Hardware* (DEC-147).
    On success the GUI pushes the fresh list through
    `AppState.set_hwmon_headers` and chains a `/diagnostics/hardware`
    refetch.
  - Daemon side effect: flags the sensor polling loop to rebuild its cached
    descriptor set on the next tick (DEC-133), so newly loaded sensor chips
    appear through normal polling within ~2 s. Does **not** replace the
    running PWM controller — new fan-control hardware still requires a
    daemon restart; the GUI repeats this caveat in the result line.

## Error model
All errors use a standard nested envelope:

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": "any | omitted",
    "retryable": true,
    "source": "string"
  }
}
```

Error codes and HTTP statuses:
- 400 `validation_error` (source: `"validation"`, retryable: false) — a malformed request, or a profile that fails daemon-owned validation (DEC-160). Profile validation attaches a structured `details.field_violations: [{field, reason, description, severity}]` array (additive superset; `reason` is UPPER_SNAKE_CASE — e.g. `OUT_OF_RANGE`, `TRIGGER_IDLE_GE_LOAD`, `UNKNOWN_CURVE_REF`, and `FLOOR_TOO_LOW` when a control with a pump/CPU member declares `minimum_pct` below the 30% hard pump floor, DEC-162). All violations are collected before responding; clients map `reason` and must never string-match `description`.
- 400 `feature_unavailable` (source: `"validation"`, retryable: false) — the endpoint exists and the addressed device exists, but that device does not support the requested operation. Currently surfaced by:
  - GPU fan writes/resets when the GPU has neither a PMFW `fan_curve` nor legacy `pwm1` write path (DEC-098); and
  - hwmon PWM writes when the targeted header's discovered `is_writable=false` (DEC-102), e.g. an unforeseen chip exposing a read-only `pwmN` file.

  Distinct from `hardware_unavailable` (transient / retryable) and `validation_error` (malformed request). Permanent for this device — clients must not retry.
- 403 `lease_required` (source: `"validation"`, retryable: false) — **retired at 2.0.0** with the bare hwmon PWM-write and the GUI-held lease (DEC-165); the daemon now verifies under its own internal lease. Listed for historical context.
- 404 `not_found` (source: `"validation"`, retryable: false) — **unknown route/URI only**. An unknown *resource* on a known route (hwmon header, GPU id) returns 404 with code `validation_error`, not `not_found`.
- 404 `override_expired` (source: `"validation"`, retryable: false) — renew/release of a manual override (DEC-163) that already lapsed on the daemon's deadman, or was never taken; re-take rather than renew.
- 409 `lease_already_held` (source: `"validation"`, retryable: false) — **retired at 2.0.0** with the GUI-held lease (DEC-165); the GUI no longer surfaces it. Listed for historical context.
- 409 `thermal_abort` (source: `"hardware"`, retryable: true) — calibration aborted due to high temperature
- 409 `stale_fencing_token` (source: `"validation"`, retryable: false) — override renew/release (DEC-163) bearing a superseded `override_token`; a newer override has been issued for that control, so the stale holder cannot re-pin (fencing)
- 500 `internal_error` (source: `"internal"`, retryable: true)
- 503 `hardware_unavailable` (source: `"hardware"`, retryable: true)
- 503 `persistence_failed` (source: `"internal"`, retryable: true) — returned by `POST /config/*` when the daemon cannot persist the runtime configuration file
- 503 `too_many_clients` (source: `"internal"`, retryable: true) — `GET /events` SSE stream only; returned when the server-side concurrent-client cap is reached. Not consumed by the V1 GUI, but documented for external clients integrating with SSE.

## Trust model

The daemon listens on `/run/control-ofc/control-ofc.sock` with mode 0666 so a non-root GUI can connect (DEC-049). There is no authentication on the socket — any local user can issue any API call, including activating a profile or pinning a control via `POST /control/{id}/override`. This is intentional: the project assumes a trust-the-local-machine model. If the socket is ever proxied to the network, that proxy is responsible for authentication and for rejecting writes from untrusted callers.

The API client must normalize this into an internal error object that includes:
- endpoint
- method
- code
- message
- retryable
- source
- details (optional)
- timestamp

## Safety behaviours to respect
According to the provided daemon notes:

### OpenFan
- 0% allowed for max 8s (stop timeout queryable via `GET /capabilities` → `limits.openfan_stop_timeout_s`)
- PWM 0–100 passed through — no clamping in the daemon. Role-aware floors are
  baked into the profile by the GUI and enforced by the daemon engine
  (DEC-162; see `docs/09_State_Model_Control_Loop_and_Lease_Behaviour.md`).
- the daemon engine coalesces duplicate writes internally — it skips the serial
  command when a channel's value matches the last commanded value (DEC-073 /
  DEC-108). The bare OpenFan write endpoints that returned a `coalesced` field
  were retired at 2.0.0 (DEC-165).

### Hwmon
- PWM 0–100 passed through — no per-header floors in the daemon
  (`min_pwm_percent: 0` on every header). The role-aware pump/CPU floor is
  baked into the profile by the GUI and enforced by the daemon engine
  (DEC-162); the 105 °C thermal force (`safety.rs`) is the absolute backstop.
  See DEC-022 and the "No per-header PWM floors" rule in `CLAUDE.md`.
- the daemon engine auto-sets `pwmN_enable` to manual mode on the first write per lease
- identical writes coalesced at daemon level (DEC-073)
- the daemon holds the hwmon lease internally (the GUI holds none — DEC-165)
- `pwm_enable` restored to automatic (2) on daemon shutdown

### AMD GPU (PMFW)
- 0–100% accepted, no lease required
- 5% minimum change threshold to avoid SMU firmware churn (DEC-070)
- Daemon disables `fan_zero_rpm_enable` before writing the PMFW curve, re-enables on reset
- The daemon engine is the sole GPU writer (the 30 s GUI-defer was retired at 2.0.0 — DEC-165)
- Daemon restores the fan curve to automatic on shutdown

### Intel GPU (read-only, DEC-121)
- **No write path exists.** The Linux `xe`/`i915` drivers expose only read-only
  `fanN_input` RPM and temperatures; fan control is managed autonomously by
  on-card firmware. There is no `/gpu/.../fan/pwm` equivalent for Intel.
- `fan_control_method` is always `"read_only"` (fan present) or `"none"`.
- The GUI must not offer Intel GPU fans as controllable curve members and must
  never write to an `intel_gpu:` target; the GPU's temperatures remain usable as
  curve *sensors*.

The GUI must reflect these constraints honestly.

## Recommended polling plan

### Startup sequence
1. `GET /capabilities`
2. `GET /hwmon/headers`
3. `GET /poll` (combined status + sensors + fans)
4. `GET /sensors/history?id=...` for each sensor (pre-fill timeline chart)

### Ongoing cadence
- **Primary data (sensors/fans/status):** 1 Hz via `GET /poll` (combined batch endpoint)
- **Capabilities/headers:** startup + on reconnect only

The `PollingService` owns the full read path (`/poll`, history). No SSE stream is consumed — the GUI
is poll-only and detects transitions by poll-diff (DEC-164 defers SSE past 2.0.0; see the `/events`
note above and DEC-023/DEC-024).

## Model normalisation
Define internal view-model friendly data classes for:
- CapabilitySnapshot
- StatusSnapshot
- SensorReading
- FanReading
- HwmonHeader
- ApiFault

## Missing or partial data
The GUI must expect:
- missing devices
- missing rpm
- missing last commanded pwm
- unsupported categories
- stale ages
- absent write support

Do not treat these as application crashes.

## Endpoint-specific UI implications

### /capabilities drives feature gating
Examples:
- disable hwmon write controls if unsupported
- validate interval fields against reported ranges

### /status drives control-gate + thermal messaging
Examples:
- show the "daemon upgrade required" banner when `control.autonomous_control` is absent (DEC-165)
- show the poll-driven thermal-protection banner from `thermal_state` (DEC-165)
- reflect active overrides / fan-identify holds from `/status.overrides[]` / `fan_identify[]`

### /fans and /sensors are display inputs
These feed the dashboard, charts, and freshness indicators. The daemon (not the GUI) consumes them for control.

## Retry guidance
- retry read polling naturally on next cycle
- avoid aggressive write retries that could thrash hardware state
- on retryable write errors, surface the failure and let the next control cycle reconcile
- rate-limit repeated failure banners/logs

## Profile management

The daemon's profile engine (`profile_engine.rs`) evaluates fan curves and is the sole writer whenever a profile is active (DEC-159). The GUI:
- Stores profiles on the daemon via the CRUD surface above (`/profiles`, DEC-160) — the daemon is the store of record (`/var/lib/control-ofc/profiles/`)
- Activates a profile: `POST /profile/activate`; queries it: `GET /profile/active`
- The daemon persists the active-profile *pointer* to `/var/lib/control-ofc/daemon_state.json`

Profile *storage of record* moved to the daemon at 2.0.0 (DEC-160); the GUI keeps a local draft cache and uploads / validates through the CRUD API.

The profile **curve schema is v7** (GUI `PROFILE_SCHEMA_VERSION` / daemon `default_version`). Both evaluators must recognise the same curve `type` values — `graph`, `stepped`, `linear`, `flat`, `trigger`, `mix`, `sync` — and the **composite** types carry extra fields the daemon parses and evaluates: `mix` (`mix_function`, `mix_curve_ids`) combines other curves at their own sensors; `sync` (`sync_control_id`, `sync_offset_pct`) mirrors another control's tuned output via stable topological control ordering (DEC-150/151, retiring the single-sensor rule DEC-014 via DEC-152). The byte-identical `parity_vectors.json` fixture pins GUI ⇄ daemon evaluation agreement (DEC-126). Schema changes are additive: a v7 profile using a new curve type still loads on an older daemon/GUI, which degrades safely (daemon → 50%, GUI → flat) rather than crashing.

## Config management

- `POST /config/profile-search-dirs` — add directories to the daemon's profile search path (persisted to `runtime.toml` per ADR-002)
- `POST /config/startup-delay` — set the daemon startup delay in seconds (persisted to `runtime.toml`, takes effect on next restart). The GUI pushes this best-effort on **both** Settings → Save and Settings → Import; a `DaemonError` is logged and surfaced in the save status, never fatal (Settings page wires the daemon client as of the 2026-06 audit, F2/F11).

## GUI startup behaviour

- **Demo-on-disconnect (DEC-139):** when the user enables "start in demo mode when daemon is unavailable", `main.py` probes the daemon once at launch with `GET /status` (~1.5 s timeout) on a throwaway client. Only `DaemonUnavailable` (socket missing/refused) triggers the demo fallback; a timeout or server error is treated as "present but slow" so a sluggish daemon never silently disables real control. This is launch-only — a mid-session disconnect uses the normal READ_ONLY/reconnect path.
