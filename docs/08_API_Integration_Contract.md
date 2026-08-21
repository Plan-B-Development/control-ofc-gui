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
6. **Every daemon success response carries a top-level `api_version` integer.**
   The response examples below are abbreviated and may omit it (e.g.
   activate/deactivate/active/override); the GUI tolerates its absence on older
   daemons but the current daemon always sends it.

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
curl -s --unix-socket $SOCK -X POST http://localhost/fans/openfan/rescan
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
- `devices.nvidia_gpu` (DEC-204, daemon ≥ 2.8.0) describes an NVIDIA **discrete**
  GPU (the open `nouveau` driver or the opt-in proprietary NVML backend).
  Read-only monitoring: fields are `present`, `model_name`, `display_label`,
  `pci_id`/`pci_bdf`, `driver` (the **kernel module** — `"nouveau"` or
  `"nvidia"`, *not* the `nvml` userspace library), `driver_version` (NVML only),
  `fan_control_method` (`"read_only"`/`"none"`), `fan_rpm_available`, and
  `is_discrete`. There is deliberately **no** `fan_write_supported`/`pci_device_id`
  field — NVIDIA fans are never writable and the daemon has no NVIDIA
  device-id → model table. `model_name`/`driver_version` are NVML-only (the
  nouveau leg yields the generic `"NVIDIA D-GPU"` label). Omitted/`present:false`
  on daemons that predate the field (parser-tolerant).
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
- `autonomous_control` (bool) — the daemon engine is the sole authoritative
  fan writer (the `gui_active` defer was deleted at the 2.0.0 cutover, DEC-165),
  so it writes every tick a profile is active. **The single safety-critical
  flag**: the loop-less GUI refuses to drive control unless this is `true`, and
  a pre-2.0 daemon omits it (so the GUI defaults it to `false` and stays in the
  capability gate rather than leaving fans uncontrolled). Drives the startup
  control gate. `true` since **2.0.0**.
- `min_supported_gui` (string) — the minimum **GUI** version this daemon supports.
  Empty until the 2.0.0 cutover sets it.

  Note the direction: this is a floor the *daemon* places on the *GUI*, the
  opposite of the `autonomous_control` gate, which is about the daemon being too
  old. It does **not** drive that gate — an earlier revision of this document said
  it did, and the GUI's one consumer had the direction backwards too, rendering it
  as "this GUI needs control-ofc-daemon ≥ X". Both read correctly only because the
  two numbers happened to be `2.0.0` (DEC-257).

  Since GUI **2.41.0** it is compared against the GUI's own version and drives a
  persistent but **non-blocking** warning banner. Non-blocking by design: the
  daemon is the sole PWM writer and is controlling fans correctly whatever the
  GUI's age, so refusing to run would strand the user for no safety gain. An empty
  value means the daemon declares no floor and is treated as satisfied — not as
  version zero.

- `openfan_rescan` (bool, daemon ≥ 2.18.0) — the daemon exposes
  `POST /fans/openfan/rescan` (DEC-265). Omitted by older daemons, so a client
  defaults it to `false` (AIP-180) and skips the call rather than issuing one
  that can only `404`.

### Per-call timeouts (DEC-098 / DEC-099)

The GUI's `DaemonClient` accepts a `timeout=` kwarg on every method that
might exceed the global `API_TIMEOUT_S = 5.0`. Endpoints with known long
upper bounds:

- `verify_hwmon_pwm` — daemon sleeps **6 s** (raised from 3 s in DEC-101);
  client timeout is **12 s**.
- `verify_gpu_fan` — daemon sleeps the same **6 s** settle (shared
  `VERIFY_WAIT_SECONDS` constant); client timeout is **12 s**.
- `openfan_rescan` — the daemon opens and identity-probes every candidate
  `ttyACM*`/`ttyUSB*` at `serial.timeout_ms` (500 ms default, up to 1000 ms) and
  re-verifies the winner, so the sweep scales with how many USB-serial devices
  are attached; client timeout is **25 s** (`OPENFAN_RESCAN_TIMEOUT_S`). Aborting
  early is merely slow, not lossy — the daemon performs the adoption in a
  detached task, so a client that gives up does not discard a controller that was
  found (DEC-266).

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

(There is no `target_rpm` HTTP route: closed-loop RPM targeting exists **only**
as an internal serial method — `SerialController::set_target_rpm` /
`Command::SetTargetRpm` — and was never exposed on the daemon's HTTP surface.
The daemon engine is duty-cycle based, so closed-loop control is out of scope
for the API. The bare PWM and hwmon-lease endpoints the v1 GUI used to call
were retired at 2.0.0 — see "Write endpoints" below.)

### GET /status
Use for:
- top-level health
- subsystem status/freshness
- daemon thermal override state
- active manual overrides / fan-identify holds (DEC-163/166)
- sensors discovered but currently unreadable (DEC-193)
- the active profile id + name (DEC-194)

This endpoint feeds:
- the header ribbon + footer status chips (DEC-222)
- diagnostics overview
- warning banners
- the poll-driven thermal-protection banner (DEC-165, superseding the DEC-132 GUI stand-down)

`overall_status` and each subsystem `status` is one of `"ok" | "warn" | "crit"`
(overall is the worst of all subsystems). The GUI treats `"ok"` as healthy and any
other value as a warning; an absent/unparseable field falls back to `"unknown"`.
Emitted by the daemon's `HealthStatus::Display` and pinned on both sides by
`health_status_display_wire_strings` (daemon) and the dashboard health tests (GUI).

`subsystems[]` is `openfan`, `hwmon`, then — on daemon ≥ 2.17.0 — **`engine`**
(DEC-249, additive; `api_version` unchanged). The order is stable and the new
entry is appended, never inserted, so an index-based reader is unaffected; the
GUI iterates the array and needs no change to display it. The first two report
*data* freshness from the poll loops. `engine` reports the profile engine's
**liveness**: the daemon's sole PWM writer also evaluates the 105 °C rule.

On daemon ≥ 2.18.0 the engine task is **supervised** (DEC-266): if it ends —
including by a panic inside a tick, which the runtime otherwise contains — the
daemon restores every fan to firmware control and exits non-zero so systemd
restarts it. So a *stopped* engine now presents to the GUI as a dropped socket,
not as a green `/status`. Before 2.18.0 nothing supervised the task and a panic
inside a tick ended fan control and thermal safety while every other signal
stayed green and `/status` kept answering 200 — this field was the only client-
visible sign. It remains the signal for a **degraded** engine (slow ticks),
which supervision does not cover, and the only signal at all on older daemons.

**A slow tick is not a stopped engine, and the daemon distinguishes them
(DEC-259).** The engine stamps both the start and the completion of every tick,
so `engine` reports one of two situations:

- *Between ticks* — judged on how long ago the last tick **finished**, against a
  fixed 1 Hz period: `ok` ≤ 2 s, `warn` ≤ 5 s, `crit` beyond. Deliberately **not**
  derived from the operator-configurable `poll_interval_ms`, so widening polling
  cannot widen what counts as a live engine.
- *Mid-tick* — judged on how long the current tick has been **running**: `ok`
  within a normal tick, `warn` while it is merely slow, and `crit` only past 30×
  the period, where it is stuck rather than slow.

That second case is why the pair exists. A thermal `force_all` walks all ten
OpenFan channels, each bounded by `serial.timeout_ms` (up to 1 s), so a
degraded-but-open link makes a **legitimate** tick take 5–10 s. With a single
timestamp the daemon reported `crit` / "not ticking — fan control and thermal
safety are stalled" *while it was actively driving the 105 °C emergency* — the
inverse of the truth, in the state where a client can least afford to be misled.
Widening the threshold would have traded that false alarm for blindness to a real
death; splitting the stamps distinguishes the cases instead.

Reasons are engine-specific: `"evaluating on schedule"`, `"tick overdue"`,
`"tick still running — a slow write is holding it up"`, `"tick stuck — the engine
has not finished a pass"`, `"not ticking — fan control and thermal safety are
stalled"`, `"never ticked"`. `age_ms` is always time since the last **completed**
pass, so a client can read "mid-tick, last full pass N ms ago" coherently.

A `crit` engine escalates `overall_status` to `"crit"` — that escalation is the
point of the surface. Daemons < 2.17.0 emit two entries and no `engine`; a client
must treat its absence as "unknown", never as healthy.

`thermal_state` (daemon ≥1.13.0, additive — `api_version` unchanged) is one of
`"normal" | "recovery" | "emergency" | "no_sensor_fallback"`. While it is not
`"normal"` the daemon is forcing all OpenFan+hwmon PWM (GPU fans excluded —
DEC-130) and holding the hwmon lease as `thermal-safety`; the GUI has no loop
to stand down (DEC-165) and simply shows a single poll-driven thermal warning.
Older daemons omit the field — the GUI defaults it to `"normal"`.

On daemon ≥ 2.19.0, `"no_sensor_fallback"` has a **second trigger**: a CPU
reading that is merely *stale* now counts as no reading (DEC-267). The safety
rule reads a cached sensor map with no freshness filter of its own, so a sensor
that stopped updating used to freeze the last temperature — the 105 °C ladder
then evaluated forever against a number that could not rise, the no-sensor
fallback never engaged because the sensor was present rather than missing, and
the engine's liveness heartbeat stayed green because the engine genuinely was
ticking. A reading older than five poll intervals (floored at 5 s, capped at
30 s) is now treated as no reading.

**No client change is required** — the state and its meaning are unchanged, only
how often it can be reached.

Two things worth knowing about the shape of it:

- **The sensor may still appear in `/sensors` with a large, growing `age_ms`
  while this state is reported.** That pairing *is* the signal — both sides read
  the same timestamp, so a reading a client sees as older than the budget is
  exactly one the rule is ignoring. It is not necessarily short-lived: the case
  this protects against is most often a poll leg that **hangs** rather than one
  that dies, and a hung read leaves the task alive, so daemon supervision never
  fires and the pairing persists for as long as the hang. (In the *death* case
  the daemon restores and exits within seconds — usually before the budget plus
  the 5-cycle debounce could produce the state at all.)
- **Reaching the state normally takes the budget *plus* the existing 5-cycle
  debounce** (~10 s at defaults), not the budget alone.

`"no_sensor_fallback"` therefore means what it always meant — the daemon is
forcing `NO_SENSOR_SAFE_PCT` — but a stale reading only reaches it when the last
known temperature was **cool**. Three cases divert first (DEC-269), on the
principle that losing sight of a sensor must never *lower* cooling:

| last known reading | daemon response | `thermal_state` |
| --- | --- | --- |
| stale, emergency latched | holds the emergency's own 100 % | `emergency` |
| stale, mid-recovery | holds the 60 % recovery floor | `recovery` |
| stale, at/above the 80 °C release temp | no force — fan curves keep running on it | `normal` |
| stale and cool | `NO_SENSOR_SAFE_PCT` after the debounce | `no_sensor_fallback` |
| genuinely **absent** mid-emergency | 40 % immediately (DEC-190, unchanged) | `no_sensor_fallback` |

**`cpu_sensor_found`** on `/diagnostics/hardware` changed meaning with the same
release: it used to answer *"is a CpuTemp sensor present?"* and now answers *"is
there a **current** reading?"*, so it is `false` for a sensor that is listed but
no longer updating. Clients rendering it as "found / not found" should reword —
`{"state": "emergency", "cpu_sensor_found": false}` is a normal pairing now.

`overrides` and `fan_identify` (daemon ≥ 1.21.0, additive — `api_version`
unchanged, omitted when empty) make `/status` the poll-authoritative source for
active manual overrides and fan-identify holds (DEC-163 / DEC-166): `overrides[]`
of `{control_id, pwm_percent, expires_in_secs}` and `fan_identify[]` of
`{fan_id, expires_in_secs}`. Both arrays are absent on daemons < 1.21.0 and when
nothing is held; the GUI defaults them to empty.

The GUI **consumes** these read-only (DEC-169). Crucially, the entries carry **no
`override_token`**, and renew/release both require it — so an override this GUI
session did not create (another client, or this GUI restarted within the TTL) can
only be *displayed*, never renewed or released. The Controls page reconciles
`overrides[]` each poll: a "foreign" `control_id` (one not in the GUI's own
token-bearing set) paints a read-only **"External NN%"** chip on its card and
reverts when the daemon stops reporting it; clicking **Manual** on such a card is
an explicit *take-over* (a fresh `override_take`, which supersedes via monotonic
fencing and yields a token the GUI can then manage). The GUI's own overrides are
left to the renew timer — reconcile never touches them, so the two authorities
never collide. Diagnostics surfaces both arrays read-only (and in the support
bundle). `fan_identify[]` is Diagnostics-only — the fan wizard owns its own
stop/restore + deadman lifecycle and is not driven from poll state.

`unavailable_sensors` (daemon ≥ 2.3.0, additive — `api_version` unchanged, omitted when empty) lists
sensors the daemon discovered but currently cannot read — the canonical case is an `ath12k` WiFi
temperature returning `ENETDOWN` while the radio is soft-blocked. Each entry is `{id, label, reason,
unavailable_for_ms}` where `reason` is the daemon's hwmon read error and `unavailable_for_ms` is the
time since the sensor was quarantined. These are evicted from `/sensors` (so a stale value is never
served) and the daemon suppresses its own per-tick read-failure logging for them (DEC-193). The GUI
consumes this **display-only**: the Overview page shows a low-key panel + an "N unavailable"
summary count, and these sensors do **not** raise a staleness warning (they are absent from the live
sensor list). Older daemons omit the array — the GUI defaults it to empty.

`skipped_controls` (daemon ≥ 2.21.0, additive — `api_version` unchanged, omitted when empty) lists
logical controls the daemon's profile engine **cannot resolve**, and is therefore not commanding at
all (273-i). Each entry is `{control_id, control_name, reason, skipped_for_ms}`.

A skipped control's fans **hold their last commanded duty** — a skip never lowers a fan (DEC-269) —
so the symptom is a fan that has quietly stopped responding. Transient causes resolve within a tick
and are never reported; a control is listed only after **three consecutive** skipped ticks, because
`curve_eligible`'s freshness budget floors at 5 s and a sensor sitting on that boundary would
otherwise flap in and out of the list at 1 Hz. The daemon logs one WARN on entry and one INFO on
resolution.

`reason` is a **stable token, not prose** — the daemon deliberately leaves the wording to the client:

| `reason` | Meaning |
| --- | --- |
| `curve_not_found` | The control's `curve_id` names a curve the active profile does not contain |
| `sensor_unavailable` | The curve's sensor is absent — not present on this machine, or age-filtered out as stale |
| `mix_unresolvable` | A Mix produced no value at all (no children, none resolvable, a `subtract` missing its minuend, a cycle, or the depth backstop). A Mix with *some* inputs resolvable is **not** skipped — it runs on the survivors (DEC-272) |
| `sync_unresolvable` | A Sync whose target is unset, is the control itself, or was not computed this tick |

Adding a token is additive; renaming one is breaking. A client **must** render an unrecognised token
rather than dropping the entry — otherwise a newer daemon reintroduces exactly the silence this field
removes.

An **overridden** control is never listed: the engine short-circuits an active override before curve
resolution, so a skip and an override cannot co-occur.

The GUI consumes this **display-only** — it never writes PWM (DEC-165), so there is nothing for it to
do beyond telling the user. The Controls page reconciles `skipped_controls[]` each poll and paints a
**"Not controlled"** chip on the affected card, with the reason in its tooltip; the card's live output
label keeps showing the last commanded value, because that is what the fans are actually holding. The
chip is cleared when the daemon stops reporting the control, and on disconnect (polling stops, so
nothing else would clear it, and an offline GUI does not know whether it is still true). A
user-owned Manual state wins the chip. Older daemons omit the array — the GUI defaults it to empty,
so it never needs to know the daemon's version to read this field.

`active_profile_id` and `active_profile_name` (daemon ≥ 2.4.0, additive — `api_version` unchanged,
**both omitted when no profile is active**) mirror the daemon's currently-active profile onto the
`/status` + `/poll` surface (DEC-194). This lets the GUI reflect an *external* activation (another
client, `--profile`, a systemd unit) within one 1 Hz poll instead of waiting up to the ~5-minute
`GET /profile/active` refresh. The GUI reads them from every `/poll`: a present `active_profile_name`
updates the shown profile immediately (edge-triggered), while an **absent** key — an older daemon that
never sends it, *or* genuinely no active profile — is parsed as `None` and leaves the periodic
`GET /profile/active` fetch authoritative (so the field is never misread as "no profile" against a
pre-2.4.0 daemon). `GET /profile/active` remains the canonical query and the fallback. Note the fast
path covers activation; external *deactivation* still reconciles on the periodic fallback.

`readiness` (daemon ≥ 2.10.0, additive — `api_version` unchanged, **omitted until the daemon has
cached a rollup**, and by daemons predating the field) is a compact hardware-readiness rollup mirrored
onto the `/status` + `/poll` surface for the GUI's Dashboard cooling-readiness health chip (DEC-206):
`{overall, critical, warning, info, top_summary, top_code}` — the rollup severity (`ok`/`info`/
`warning`/`critical`), the per-severity item counts, and the most-severe item's one-line summary +
stable `code` (both omitted when `overall` is `ok`). It is derived from the same items
`GET /inventory/readiness` returns and **cached** in the daemon: refreshed only on discovery-changing
events (startup, a preferred-sensor change, and each `/inventory/readiness` GET), never recomputed on
the 1 Hz poll. The **full** item list stays on `GET /inventory/readiness` — this rollup is a summary,
not a replacement. The GUI parses an absent key to `None` and hides the chip (older daemon, demo, or
before the daemon's startup seed runs); `top_summary` is a daemon string, rendered as plain text.

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
- source — `"hwmon"`, `"amd_gpu"`, `"intel_gpu"` (DEC-121; Intel discrete GPU temps via the `xe`/`i915` hwmon node, kind `gpu_temp`), or `"nvidia_gpu"` (DEC-204; NVIDIA discrete GPU temps via the `nouveau` hwmon node or the NVML backend, kind `gpu_temp`).
- age_ms
- rate_c_per_s (optional, float) — smoothed temperature change rate in °C/s; omitted (`skip_serializing_if`) until computable
- session_min_c (optional, float) — lowest value seen for this sensor since daemon start; omitted until set
- session_max_c (optional, float) — highest value seen for this sensor since daemon start; omitted until set
- chip_name — hwmon driver name from sysfs (e.g. `k10temp`, `nct6798`, `it8689`). Always present; `"amdgpu"` for AMD GPU sources, `"xe"`/`"i915"` for Intel GPU sources, and `"nouveau"` (open) or `"nvml"` (proprietary) for NVIDIA GPU sources.
- temp_type (optional, integer) — thermistor type code from `tempN_type` sysfs. Values: 3 = diode, 4 = thermistor, 5 = AMD TSI, 6 = Intel PECI. Absent when the driver does not expose type information.
- thresholds (optional object) — DEC-117 curated subset of hwmon temperature-threshold sysfs attributes. The daemon reads these once at discovery and re-reads them on `POST /hwmon/rescan`. Implausible values (<-50 °C, >200 °C) and the `it87`-family `tempN_max == 0` placeholder are filtered at the daemon side. The whole object is omitted when no attribute was readable for this sensor (k10temp typically exposes none). When present, every sub-field is also omitted-when-None so the on-wire shape is the minimal honest set. Sub-fields (all optional):
  - `max_c`, `min_c` — typical upper/lower warning thresholds (°C)
  - `crit_c`, `crit_hyst_c` — critical threshold and hysteresis (°C)
  - `emergency_c`, `emergency_hyst_c` — emergency threshold and hysteresis (°C)
  - `lcrit_c` — lower critical threshold (°C, cold-side)
  - `offset_c` — userspace-applied calibration offset (°C)
  - `alarm`, `max_alarm`, `crit_alarm` — chip-asserted alarm bits (bool); sampled at discovery only, not refreshed per poll cycle
  - `fault` — chip-reported sensor fault (bool)
- control_eligible (bool, DEC-193, daemon ≥ 2.3.0) — `false` when this temperature must **not** be
  offered as a fan-curve source. Currently set only for wireless-radio PHY temps (e.g. `ath12k`
  WiFi, chip names `ath*_hwmon` / `iwlwifi*`), which read `ENETDOWN` whenever the radio is down and
  would strand a curve. Advisory and display-agnostic: the GUI drops `control_eligible == false`
  sensors from the curve sensor picker (mirroring how `is_writable: false` headers are dropped from
  the member picker, DEC-102) but still shows them everywhere else; the daemon engine never consults
  it. Omitted by pre-2.3.0 daemons → the GUI defaults it to `true` (every sensor stays selectable).

Entries are sorted by `id` — deterministic across daemon restarts and rescans
(DEC-146; fans were already sorted, sensors now match).

Sensors that exist but currently fail every read are **not** in this array — the daemon evicts them
and reports them under `unavailable_sensors` on `/status` + `/poll` instead (DEC-193, see below), so
a sensor that goes unreadable is never served at a stale value.

### GET /fans
Use as the primary current fan state source.
Expected fields:
- id
- source
- rpm (optional, omitted when unavailable)
- last_commanded_pwm (optional, omitted until first write)
- duty_pct (optional; DEC-204) — firmware-**measured** current fan duty %, present only for sources with a duty readback (NVIDIA via NVML). Distinct from `last_commanded_pwm` (commanded) — never conflate. May exceed 100 (NVML expresses it as a % of max noise tolerance), but it is a `u8` on the wire (`responses.rs`) and so saturates at **255** — a larger reading is not representable. Omitted when absent (and on pre-DEC-204 daemons).
- age_ms
- stall_detected (optional bool) — daemon-asserted; set when commanded PWM is above the daemon's `STALL_PWM_THRESHOLD` (20%, i.e. ≥21%) but measured RPM is zero. Evaluated per-tick from the latest snapshot (no multi-cycle counter); `null`/omitted when RPM is not polled. Surfaced by the GUI as an `error`-level warning.

Note: fans do **not** include `label` or `kind` from the daemon — every display name is
derived GUI-side by `AppState.fan_display_name` / `fan_fallback_name`, in this order:

1. user alias (GUI-owned, `fan_aliases`; authored from the Dashboard Sensors rail, the
   read-only fan cards, the Overview fan table, or the Fan Wizard — DEC-227). On the
   first fan poll, any name already saved as a profile `member_label` is adopted into
   this store once (DEC-228)
2. GPU model name, for `amd_gpu:` / `intel_gpu:` / `nvidia_gpu:` fans
3. OpenFan channel label — `openfan:ch00` renders as `OpenFan CH0` (DEC-227)
4. hwmon header `label` from `GET /hwmon/headers` (hwmon fans only) — **unless it is a
   synthesised placeholder**, see below
5. `/etc/sensors.d` + the in-repo board fallback table (`hwmon_label_resolver`, hwmon only)
6. raw `pwmN` for a fan that matches a known hwmon header; the raw fan id otherwise (a fan
   in no header list at all), as a last resort

**`HwmonHeader.label` is never empty, and may be invented (DEC-229).** The daemon's
`read_label` tries `pwm{N}_label`, then `fan{N}_label`, then *synthesises* `pwm{N}` — and
the it87 driver publishes no label files for most Gigabyte boards, so the synthesised form
is common. Tiers 4-6 therefore live inside `resolve_hwmon_header_label`, which skips a
label exactly equal to the header's own `pwm{index}` token (`is_placeholder_hwmon_label`)
and lets tiers 5-6 run. The predicate is deliberately exact rather than a "looks generic"
heuristic: `pwm[1-*]_label` is not a documented hwmon attribute at all (only
`fan[1-*]_label` is), and discarding a genuine board label would be the worse failure.

Note the label is also **embedded in the header id** (`hwmon:<chip>:<device>:pwmN:<label>`),
which is why the daemon cannot simply send `label: ""` for the synthesised case — every
saved profile member id and `fan_aliases` key would change.

Tier 5's board-table half is keyed on DMI `(vendor, board)` from `GET /diagnostics/hardware`,
which the GUI fetches once on the first capabilities cycle and stores in `AppState.board_info`
via `DiagnosticsService.set_hw_diagnostics` (its single writer). With no board identity the
table cannot match and tier 6 answers — degraded, never wrong.

**Profile member labels are a separate store with a different job (DEC-228).**
`ControlMember.member_label` lives in the profile document — which the daemon holds as
the store of record (DEC-160) and round-trips verbatim — and is *not* a display-name
tier. The control surfaces resolve through `AppState.member_display_name`
(alias > cached `member_label` > fallback), and `member_label` itself is kept
**hardware-truthful** rather than set to the user's alias, because both the GUI's
`infer_member_role` and the daemon's `member_is_pump_or_cpu` match "cpu"/"pump"/"aio"
against it to apply the DEC-095/162 30% CPU/pump floor.

**As of daemon 2.17.0 this is no longer a pure mirror (DEC-252).** The daemon's
*eval-time* classifier is a **union**: the hard floor applies if the client's
`member_label` says cpu/pump/aio **or** the label the daemon itself discovered does —
the latter travelling in the member's own stable id (`hwmon:{chip}:{device}:pwmN:{label}`).
Union only: the daemon can add a floor, never remove one the profile asked for. So a
header the user renamed away from `PUMP` keeps its 30% floor at runtime even though the
GUI stamped 20%.

Two consequences a client must know:
- `validate()`'s `FLOOR_TOO_LOW` / `PUMP_STOP_FORBIDDEN` **rejections deliberately still
  use the narrow, author-declared classifier**, so a daemon upgraded ahead of its GUI
  cannot start refusing profiles the GUI still bakes.
- **The GUI adopted the same union in v2.41.0** (DEC-257), so the displayed floor now
  matches what the daemon enforces: `infer_member_role` parses the daemon-discovered
  label out of the member id exactly as the daemon does. Safe in either skew direction —
  a GUI stamping a *higher* floor is accepted by any daemon, which is why the daemon's
  `validate()` rejection was deliberately left on the narrower classifier.
- One residual: `apply_role_floor` runs when members are edited, not on profile *load*,
  so a profile written before v2.41.0 keeps its stored `minimum_pct` until it is next
  edited. The **displayed** floor is union-correct either way, and the daemon clamps at
  eval time regardless, so the stored value lagging is cosmetic rather than a safety gap.

The limit is worth stating: where a chip publishes no label file the daemon synthesises
`pwmN`, and it reads no `/etc/sensors.d`, so on such a board the author's label is still
the only signal. This is a backstop, not an independent pump detector — `member_label`
remains a safety input.

Fan `source` is `"openfan"`, `"hwmon"`, `"amd_gpu"`, `"intel_gpu"`, or `"nvidia_gpu"`. GPU fan IDs embed the PCI BDF: `amd_gpu:{bdf}`, `intel_gpu:{bdf}`, and `nvidia_gpu:{bdf}`. OpenFan fan IDs are `openfan:ch{NN}`, where `NN` is the **zero-padded** channel index (hardware-fixed range 0–9, `NUM_CHANNELS = 10`). The GUI parses that index for tier 3 above, so the padding and decimal form are part of the contract; a non-decimal suffix falls through to the raw id rather than being guessed at. Intel (DEC-121) and NVIDIA (DEC-204) GPU fans are **read-only** — `rpm` is reported when available (and NVIDIA additionally reports a measured `duty_pct`), but `last_commanded_pwm` is always absent; the GUI must never issue a write to an `intel_gpu:`/`nvidia_gpu:` target.

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
  mistaken for a GUI/daemon clamp. `null` for non-PMFW GPUs (and, on daemon ≥ 2.18.0, for a PMFW GPU whose reported `OD_RANGE` speed pair is inverted or outside 0–100 — the daemon rejects an implausible range rather than trusting it, DEC-266).
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
- `nvidia_gpu: object | null` (DEC-204, daemon ≥ 2.8.0) — NVIDIA discrete GPU
  diagnostics: `pci_bdf`/`pci_id`, `model_name`, `driver` (kernel module —
  `"nouveau"`/`"nvidia"`), `driver_version` (NVML only), `fan_control_method`
  (`"read_only"`/`"none"`), `fan_rpm_available`, and `fan_control_note`. No
  `pci_device_id`/`pci_revision`. `null` when no NVIDIA GPU is present or the
  daemon predates the field.

> **Note — `GET /events` (SSE) removed.** The daemon exposed a Server-Sent Events
> stream that no client ever consumed (the GUI is poll-only; DEC-164 deferred SSE
> past 2.0.0). It was removed entirely in daemon v2.5.0 (DEC-198). All data flows
> through the 1 Hz `PollingService` over `GET /poll`.

### GET /inventory/hwmon (DEC-200, daemon ≥ 2.6.0)

Structured, read-only inventory of hwmon-visible hardware — the daemon's own
classified view, distinct from the live `/sensors` + `/fans` polls. One-shot,
fetched on demand (the Settings page's preferred-sensor pickers; the Overview page), never at 1 Hz.
Full field set in the daemon's `responses.rs::HwmonInventoryResponse`. **404-only
gated** — a daemon predating the endpoint 404s and the GUI shows the dependent UI
as unavailable. The daemon never writes hardware to build this report.

- `api_version: int` — always `1`.
- `temp_sensors: list` — the live temperature sensors (same identity/fields as
  `/sensors`) enriched with an advisory `classification` (`cpu_package |
  cpu_core | cpu_tctl | cpu_tdie | motherboard_temp | vrm_temp | chipset_temp |
  gpu_temp | disk_temp | coolant_temp | unknown_temp`), a `confidence` (`high |
  medium | low | unknown`), a plain-English `rationale`, and `control_eligible:
  bool` (DEC-193 — a wireless-PHY temp is dropped from the curve sensor picker).
  The classification **refines** the coarse `kind` and never contradicts it.
- `pwm_controls: list` — controllable PWM headers (same shape as
  `/hwmon/headers`).
- `monitor_only_fans: list[{id, source:"hwmon", chip_name, label, fan_index}]` —
  `fanN_input` tachometers with no matching `pwmN` (otherwise invisible to the
  API). Omitted when empty (additive).
- `default_cpu: {sensor_id, confidence, rationale, source}?` — the daemon's
  default-CPU recommendation; `source` is `"user"` when it echoes the persisted
  preferred CPU sensor, else `"auto"`. Omitted when no CPU sensor is present.
- `preferences: {cpu_sensor_id, mb_sensor_id}?` — the user's persisted preferred
  CPU/motherboard sensors (pinned via the `POST /config/preferred-{cpu,mb}-sensor`
  writes). Either id may be stale — cross-check against the live sensor list.
  Omitted when none are set.

### GET /inventory/readiness (DEC-200, daemon ≥ 2.6.0)

A structured, read-only diagnose-and-guide list: the daemon's assessment of the
CPU/hwmon/PWM inventory as actionable items. Never mutates the system. 404-only
gated. Full shape in `responses.rs::ReadinessResponse`.

**Not consumed by this GUI (DEC-257).** DEC-207 merged readiness and Super-I/O
into `GET /inventory/hardware-readiness`, which is what the Hardware page calls
(`DaemonClient.hardware_readiness`); this endpoint has no client method. It is
documented because the daemon still serves it — for other clients, and because
the daemon refreshes its cached rollup on each GET of it — not because the GUI
calls it. Earlier revisions of this document described it as GUI-consumed, which
was true before the merge.

- `api_version: int` — always `1`.
- `overall: str` — the rollup severity (`ok | info | warning | critical`), equal
  to the most severe item's severity.
- `items: list[ReadinessItem]` — each item is:
  - `code: str` — a **stable machine key** the GUI keys knowledge-base entries and
    acknowledgement state off (e.g. `cpu_sensor_missing`, `cpu_sensor_present`,
    `no_pwm_controls`, `pwm_read_only`, `monitor_only_fans_present`, and — when a
    Super-I/O chip is detected without its driver — `superio_driver_unloaded` /
    `superio_acpi_conflict`, DEC-202).
  - `severity: str` — `ok | info | warning | critical`.
  - `component: str` — `cpu | pwm | hwmon | sensor`.
  - `summary`, `detail`, `recommended_action: str`.
  - impact flags: `can_automate`, `blocks_monitoring`, `blocks_control`,
    `affects_safety`, `reboot_may_be_required: bool`.

Forward-compatible: parse each item with a field filter (unknown daemon keys
dropped) and default an absent `severity` to `info` (never falsely `ok`).

### GET /inventory/superio (DEC-202, daemon ≥ 2.7.0)

**Not consumed by this GUI (DEC-257)** — same reason as `/inventory/readiness`
above: DEC-207 folded this into `GET /inventory/hardware-readiness`. The GUI's
only Super-I/O call is the opt-in `POST /inventory/superio/probe`. Documented for
the daemon surface it still is.

Passive Super-I/O chip detection. **Read-only** — the daemon composes signals it
already has (DMI board table, bound hwmon chips, `/proc/modules`, `/dev/kmsg`,
ACPI `/proc/ioports` overlaps) into a per-chip report; it never runs a port
protocol, loads a module, or writes hardware. (When the active probe is *enabled*
it does transiently open `/dev/port` here to report `port_probe_available`
accurately — an open/close only, no port I/O.) One-shot and off the poll loop —
the GUI fetches it on demand (a dedicated panel on the Hardware page), never at 1 Hz.

**Gating:** 404-only, like the other `/inventory/*` routes — no capability flag.
A `404 not_found` means the daemon predates the feature; the GUI hides the panel.

Fields (`responses.rs::SuperIoResponse`; additive fields use
`skip_serializing_if`, so a client defaults them to empty/absent):

- `api_version: int` — always `1`.
- `arch_supported: bool` — `false` on non-x86 (with `chips: []`); Super-I/O
  detection is an x86/ISA concept.
- `chips: list[SuperIoChip]` — each: `chip_name`, `vendor`
  (`ite|nuvoton|winbond|smsc|national|fintek|unknown`), `evidence: list[str]`
  (`dmi_board_table|kernel_log|bound_hwmon`), `confidence`
  (`high|medium|low|unknown`), `bound_driver: str?` (inferred; present only when
  the chip is bound *and* its driver is recognized), `expected_module`,
  `module_loaded: bool`, `hwmon_present: bool`,
  `recommendation: SuperIoRecommendation?` (present only for an unbound,
  allowlisted chip — `module`, `in_mainline`, `load_hint`, `reason`,
  `risk_notes: list[str]`), and `caveats: list[str]`.
- `acpi_conflict_drivers: list[str]` — driver names whose ISA I/O range collides
  with an ACPI OperationRegion (omitted when empty).
- `notes: list[str]` — report-level notes; on x86 always includes the honest
  "detection proves a chip is present, not that fan control is available" caveat
  (on non-x86, where `arch_supported:false`, it instead carries a single
  "unsupported architecture" note). Omitted when empty.
- `port_probe_available: bool` — whether the opt-in ACTIVE probe (below) can run
  right now. Off by default. The GUI gates its advanced "probe ports" affordance
  on this.
- `port_probe_reason: str` — `"available"`, or a plain-English reason it is not
  (flag off / no `CAP_SYS_RAWIO` / kernel lockdown / no `/dev/port`). Show this
  as the disabled button's tooltip.

The same detection also enriches **`GET /inventory/readiness`** with up to two
**aggregate** items (one per code, regardless of how many chips match):
`superio_driver_unloaded` (severity `warning`, `reboot_may_be_required: true` —
loading a module often needs a reboot to re-run the Super-I/O scan; the matched
chips are listed in the item's `detail`) and `superio_acpi_conflict` (severity
`warning`, `reboot_may_be_required: false`). Board-specific "load *this* driver"
guidance thus joins the generic `no_pwm_controls` item. These items use the same
`ReadinessItem` shape (`code`, `severity`, `component`, `summary`, `detail`,
`recommended_action`, and the `blocks_monitoring` / `blocks_control` /
`affects_safety` / `reboot_may_be_required` flags) as the existing DEC-200
`/inventory/readiness` endpoint. **Detection is not control:** a recommendation
means a chip is present and a driver exists — never that PWM control is proven.
Loading the driver, or the daemon's separate verify path, is what confirms
control.

### GET /inventory/hardware-readiness (DEC-207, daemon ≥ 2.11.0)

The **combined** readiness + Super-I/O snapshot the GUI's merged *Cooling Hardware
Readiness* page fetches in **one** request, so both halves come from a single shared
daemon scan (no cross-endpoint drift, no redundant detection). Read-only. Fields:

- `api_version: int`
- `rollup: ReadinessRollup` — the same compact object mirrored on `/status` + `/poll`
  (`{overall, critical, warning, info, top_summary?, top_code?}`).
- `overall: str` — the rollup severity (`ok` | `info` | `warning` | `critical`),
  echoed for convenience.
- `items: ReadinessItem[]` — identical to `GET /inventory/readiness`.
- `superio: SuperIoResponse` — identical to `GET /inventory/superio` (including
  `port_probe_available` / `port_probe_reason`).
- `scanned_age_ms: int` — milliseconds since the underlying passive scan completed
  (the GUI renders a "last scanned" time; matches the `age_ms` freshness convention).
- `generation: int` — a monotonic scan id; it changes exactly when a new scan is
  served, so the GUI can detect a fresh assessment without diffing.

Query: `?refresh=true` forces a fresh (coalesced) scan — the page's "Refresh hardware
assessment" action; anything else (or absent) serves the daemon's cached assessment.
A malformed `refresh` value never 400s.

**Shared snapshot (DEC-207):** since daemon v2.11.0 this endpoint, `GET
/inventory/readiness`, `GET /inventory/superio`, and the `/status`+`/poll` rollup are
all served from **one** cached passive scan, coalesced so simultaneous requests don't
launch duplicate scans. The two older endpoints keep their exact response shapes (this
combined endpoint does not replace them). Absent route ⇒ daemon predates the feature;
the GUI feature-detects on `404` and shows an "unavailable" state, falling back to the
existing endpoints only where a caller needs them.

**Status:** `200` with the snapshot, or a retryable `503 hardware_unavailable`
("hardware assessment is temporarily unavailable — retry") when the shared passive
scan has not completed yet (`inventory.rs`). The GUI surfaces the daemon's message
on the Hardware page's status line (it does not auto-retry); the user re-runs the
scan via the page's Refresh action.

### POST /inventory/superio/probe (DEC-203, opt-in, daemon ≥ 2.7.0)

The **opt-in ACTIVE** Super-I/O probe — a *deliberate, one-shot* action (never
polled) that reads the Super-I/O config ports (0x2E/0x4E) to identify an
**unbound** chip the passive `GET` cannot see. It is a `POST` because it is a
deliberate side-effecting action (it writes the chip's enter/exit protocol
bytes), not a passive read.

**Off by default.** It runs only when the operator has BOTH set
`[detection] allow_port_probe = true` and installed the `CAP_SYS_RAWIO` systemd
drop-in. When it cannot run it returns the normal report with
`port_probe_available: false` and a `notes[]` entry explaining why — it never
errors for being disabled. It refuses to touch a port owned by a bound driver or
reserved by ACPI, and never writes a configuration value or `force_id`.

**Response:** the same `SuperIoResponse` shape as the `GET`, with any
probe-identified chips appended to `chips[]` (each carries `evidence: ["port_probe"]`
and, for an unbound chip, a load `recommendation`). ITE chips are identified
precisely (DEVID → chip name → driver + DKMS status); the Nuvoton/Winbond family
is identified at vendor level with the raw DEVID and an `nct6775` recommendation.

**GUI usage:** gate an advanced "Probe ports" button on `port_probe_available`;
disable it with `port_probe_reason` as the tooltip when false. Because the probe
touches raw I/O ports, present a confirmation before POSTing. **Detection is
still not control.**

## Write endpoints

As of **2.0.0** the daemon is the sole writer (DEC-159, DEC-165). The GUI has **no bare PWM write
surface** — it expresses control as *intent* (activate a profile, take an expiring override, identify
a fan) and runs a few diagnostics / maintenance calls (calibrate, verify, GPU reset, rescan).

**Retired at 2.0.0** — these were genuine HTTP routes that the daemon no longer routes (the
`gui_active` defer window they lived behind is gone):
- `POST /fans/openfan/{ch}/pwm` and `POST /fans/openfan/pwm` — bare per-channel + set-all PWM
- `POST /hwmon/{header_id}/pwm` — bare hwmon PWM
- `POST /hwmon/lease/take` / `/release` / `/renew` and `GET /hwmon/lease/status` — the GUI holds no
  lease; the daemon manages it internally
- `POST /gpu/{gpu_id}/fan/pwm` — bare GPU static-speed write (replaced by override / identify)

Note: `POST /fans/openfan/{ch}/target_rpm` is **not** in this list because it was never an HTTP route.
Closed-loop RPM targeting was an internal-only serial method (`set_target_rpm` /
`Command::SetTargetRpm`) that no GUI ever consumed and the daemon never exposed over HTTP; it was
deleted as dead code in daemon v2.5.0.

### OpenFan calibrate
- `POST /fans/openfan/{ch}/calibrate` — PWM-to-RPM calibration sweep

The calibration endpoint runs a long-running sweep (steps × hold_seconds) that sets PWM from 0→100%, reads RPM at each step, and returns a mapping. Safety: aborts on thermal limit (85°C), restores pre-calibration PWM on every exit path — completion, thermal abort, or a failed PWM write mid-sweep (DEC-134). For the sweep's duration the daemon pauses its profile-engine write phase — the same single-flight pause used by hardware verify — so an active profile cannot overwrite each step's test PWM and corrupt the readback (DEC-191, daemon ≥ 2.2.2). A hardware verify already in progress is therefore rejected with `409` (and an in-progress calibration likewise blocks a verify).

### Hwmon PWM verify
- `POST /hwmon/{header_id}/verify` — empty body (no `lease_id` as of 2.0.0 — DEC-165). Returns `409 thermal_abort` when any sensor exceeds the 85 °C verify limit: a verify pauses the engine (incl. the 105 °C thermal force) for its window, so it refuses to start while hot (DEC-201, daemon ≥ 2.6.0). The GUI shows this as a soft "let it cool, then retry" notice. Also returns `409 validation_error` if a hardware verify or calibration is already in progress (single-flight — the verify shares the calibration pause).

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
`503 hardware_unavailable` (no hwmon headers or controller absent; also if the
daemon's own internal verify lease lapses mid-write — DEC-170). The pre-2.0
`403 lease_required` no longer applies — the daemon owns the verify lease, and an
internal-lease lapse surfaces as retryable `503 hardware_unavailable`, never a
client lease error.

### Profile storage (CRUD — DEC-160, daemon ≥ 1.19.0)
The daemon is the profile **store of record** (`/var/lib/control-ofc/profiles/`). The GUI uploads and
validates full profile documents; it keeps a local draft cache but does not treat it as authoritative.

**Activation currently runs the GUI-local copy, by path.** Even though the daemon is the store of
record, the GUI activates by `profile_path` pointing at its own
`~/.config/control-ofc/profiles/{id}.json` (`ControlsPage` / `ProfileService.activate_profile`), not
by `profile_id` against the store. The daemon accepts that path because the GUI registers
`~/.config/control-ofc/profiles/` as a profile **search dir** on every connect/reconnect
(`polling._register_profile_search_dir`). So if the GUI-local copy and the daemon-store copy of the
same id ever diverge, activation applies the **local** copy — not necessarily the one under
`/var/lib/control-ofc/profiles/`.
- `GET /profiles` — list stored profiles as `{id, name, description}` summaries (no controls or
  curves); `GET /profiles/{id}` — fetch one profile's full document. On load the GUI lists, then
  **hydrates** each id via `GET /profiles/{id}` before parsing — a summary alone has no controls or
  curves (DEC-175).
- `POST /profiles` — create → **201 Created** (`409 already_exists` on a duplicate id)
- `PUT /profiles/{id}` — create-or-replace stored desired-state → **200 OK** (no hot-reload —
  re-activate to apply)
- `DELETE /profiles/{id}` — `409 profile_in_use` if it is the active profile
- Profile ids are filesystem-safe stems: non-empty, ≤128 bytes, no `/` `\` `..` or control
  characters, else `400 validation_error` (DEC-173). The GUI auto-generates 8-char hex ids, so this
  only constrains hand-authored/imported ids.
- `POST` / `PUT` accept `?validate_only=true` — runs the real validation, persists nothing, and
  returns **200** on success (validation short-circuits before the persist step)
- Validation returns hard `errors` (reject) + soft `warnings` (accept). An unknown `sensor_id` is a
  warning, not an error (profiles stay portable across machines).
- **Two outcome shapes by validity:**
  - **Invalid** (any request, with or without `validate_only`) → `400 validation_error` **error
    envelope** with the field violations (errors, plus any warnings) under `error.details.field_violations` (see Error model). A
    `validate_only` request fails exactly when a real one would (AIP-163).
  - **Valid `?validate_only=true`** → `200` with a **top-level** body
    `{"api_version": N, "valid": true, "field_violations": [<soft warnings>]}` (the soft `warnings`
    ride the `field_violations` key here; nothing is persisted). A valid real create/update returns
    its persisted-result body (`created`/`updated`, `profile_id`, `warnings`, …) instead.

### Profile activation
- `POST /profile/activate` — `{"profile_path": "/path/to/profile.json"}` or `{"profile_id": "quiet"}`
  - Daemon validates, applies, and persists active profile to `/var/lib/control-ofc/daemon_state.json`
  - `profile_path` must canonicalize to a path **inside a registered profile
    search directory**, else `400 validation_error` ("profile_path must be
    within a profile search directory"). The GUI always qualifies — it
    registers its own profiles dir as a search dir on connect (see the
    store-of-record note below); independent API consumers must register
    theirs via `POST /config/profile-search-dirs` first.
  - Returns `{"activated": true, "profile_id": "...", "profile_name": "..."}`
  - GUI must only update "active" state after daemon confirms success
- `POST /profile/deactivate` — body ignored (DEC-097, daemon v1.6.0+)
  - Clears the in-memory active profile, persists the cleared state, and
    releases the daemon's internal `profile-engine` lease so a later
    re-activate cleanly re-takes it. (There is no GUI lease to preserve as
    of 2.0.0 — DEC-165.)
  - **Also clears all active control-overrides (DEC-218, daemon ≥ 2.12.0)** —
    deactivation relinquishes curve-driven control, so standing manual
    overrides are dropped symmetrically with activation (DEC-189). A client
    renewing a pre-deactivate override receives `404 override_expired`
    (re-take, don't renew). Fan-identify stops are **not** cleared.
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
  `404` (wire code `validation_error`, not `not_found`) if the control is not in the active profile;
  `400` if `pwm_percent` out of range.
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

**Activating a profile clears all active control-overrides (DEC-189, daemon ≥ 2.2.1).** A
`POST /profile/activate` — including a same-id re-apply — reverts every pinned control to its curve,
so an override taken against the previous profile cannot bleed onto a same-id control in the new one.
The GUI is poll-only and already drops its Manual cards when `/poll` no longer reports the override;
no client action is required. Fan-identify stops (below) are per physical fan and are **not** cleared
by an activation.

**Deactivating a profile also clears all active control-overrides (DEC-218, daemon ≥ 2.12.0),**
symmetric with activation: with no profile active there is no curve to revert to, so the standing
live intent is dropped rather than left to re-apply onto a same-id control in the next activated
profile. As with activation, no client action is required — the next renew is rejected
`404 override_expired` and the card reverts. Identify stops survive deactivation too.

### Fan identify (DEC-166, daemon ≥ 1.21.0)

Per-fan stop/restore for the Fan Wizard, with a deadman auto-restore. Replaces the wizard's global
freeze + raw writes.

- `POST /fans/{fan_id}/identify` — body `{"action": "stop" | "restore", "ttl_secs"?: N}` →
  `200 {"fan_id","action","expires_in_secs"?}` (`expires_in_secs` present only for `stop`).
  `stop` forces the fan to 0 (floor-exempt — even a pump) and auto-restores after the deadman;
  `restore` clears it (the engine resumes the fan's curve value). `404` if the fan id is unknown on
  `stop`; `400` on a bad action. Only the named fan is affected — others keep curve-controlling.

The floor-exempt `stop` on a world-writable socket (0666, DEC-049) means any local user can hold a
pump-class header stopped by re-issuing `stop` inside the deadman window. This is an **accepted,
bounded risk** (2026-07-21 audit): identification requires stopping any fan by design (DEC-166),
the deadman limits an abandoned stop to one TTL, and a thermal emergency outranks the overlay —
the daemon's 105 °C `force_all` (and the no-sensor 40 % fallback) drives every OpenFan + writable
hwmon header directly, spinning a stalled pump back up regardless of standing identify-stops.

### GPU fan reset
- `POST /gpu/{gpu_id}/fan/reset` — restore GPU fan to automatic mode (re-enables zero-RPM). **AMD GPUs only** — `gpu_id` is a bare PCI BDF; a BDF that resolves to an NVIDIA/Intel GPU (read-only fans) is not among the daemon's AMD GPUs, so it returns `404 validation_error` ("GPU not found").
  - GUI caller: the System State page's *Restore GPU Fan to Automatic* (DEC-147 — disabled
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
- `POST /gpu/{gpu_id}/fan/verify` — empty body, **no lease** (DEC-120, daemon v1.11.0+). **AMD GPUs only** — an `nvidia_gpu:` / `intel_gpu:` BDF returns `404 validation_error` (those fans are read-only). Also returns `409 thermal_abort` while hot (the GPU verify likewise pauses the engine — DEC-201, daemon ≥ 2.6.0), and `409 validation_error` if a verify or calibration is already in progress (single-flight).

Probes whether a GPU fan-control write actually takes effect, catching the
silent failures static diagnostics miss (`ppfeaturemask` bit 14 unset, SMU
firmware/driver mismatch, BIOS overdrive lock). The daemon drives a test speed
biased **upward** (idle/low → 75%, already-high → 100%, clamped to OD_RANGE so
cooling is never reduced), sleeps `VERIFY_WAIT_SECONDS = 6 s` (matching the
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
  - Called by the System State page's *Rescan Hardware* (DEC-147).
    On success the GUI pushes the fresh list through
    `AppState.set_hwmon_headers` and chains a `/diagnostics/hardware`
    refetch.
  - Daemon side effect: flags the sensor polling loop to rebuild its cached
    descriptor set on the next tick (DEC-133), so newly loaded sensor chips
    appear through normal polling within ~2 s. Does **not** replace the
    running PWM controller — new hwmon fan-control hardware still requires a
    daemon restart; the GUI repeats this caveat in the result line. (The
    OpenFan controller is the exception, and has its own route below.)

### OpenFan rescan (DEC-265, daemon ≥ 2.18.0)
- `POST /fans/openfan/rescan` — adopt an OpenFanController without a restart
  - Response `200`: `{"api_version": int, "adopted": bool,
    "already_connected": bool, "port": str, "message": str}`. `port` is present
    only when a controller was newly adopted. Rescanning while one is already
    connected is a **no-op success** (`adopted:false, already_connected:true`),
    not an error — it probes nothing and leaves the existing controller in
    place. (`api_version` was missing from the 2.18.0 pre-release shape,
    breaking General Rule 6 above; added in DEC-266 before it shipped.)
  - `503 hardware_unavailable` — no candidate port both opened *and* identified
    as an OpenFanController. This is the normal answer on a machine with no
    OpenFan hardware.
  - `409 validation_error` — a rescan is already in progress (single-flight;
    two racing probes would open the same tty and the loser would install a
    controller over the winner's).
  - `404 not_found` on any daemon before 2.18.0. **Gate on
    `capabilities.control.openfan_rescan`.**
  - **Why this exists.** The daemon adopts its controller during startup only.
    A device that enumerated a moment too late, or that failed the DEC-250
    identity handshake once, previously left the daemon with no OpenFan backend
    for the whole process lifetime — and since the profile engine's 105 °C
    `force_all` reaches OpenFan fans through that same backend, the thermal
    emergency silently lost its OpenFan leg too. A failed boot connect only
    logs a warning, so `Restart=on-failure` never fired and nothing recovered
    it. Adoption uses the same identity-verified path as boot, so a port that
    opens but is not an OpenFanController is still refused.
  - Called by the GUI as part of the System State page's *Rescan Hardware*
    action, not as a separate button: that action is what a user reaches for
    when hardware is missing, and requiring them to know *which kind* of
    hardware went missing is the worse UX. The leg is best-effort — a `503`
    (no controller found) never fails the hwmon rescan — and runs **after** the
    hwmon rescan, so the contracted leg is not delayed behind a serial sweep
    (DEC-266). Only an adoption is reported to the user; every other outcome is
    silent, and the success line's "requires a daemon restart" advice is
    suppressed when a controller *was* adopted, since that is exactly the case
    this route makes restart-free.

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
- 400 `validation_error` (source: `"validation"`, retryable: false) — a malformed request, or a profile that fails daemon-owned validation (DEC-160). Profile validation attaches a structured `details.field_violations: [{field, reason, description, severity}]` array (additive superset; `reason` is UPPER_SNAKE_CASE — e.g. `OUT_OF_RANGE`, `TRIGGER_IDLE_GE_LOAD`, `UNKNOWN_CURVE_REF`, `FLOOR_TOO_LOW` when a control with a pump/CPU member declares `minimum_pct` below the 30% hard pump floor (DEC-162), `PUMP_STOP_FORBIDDEN` when a control with a pump/CPU member declares a non-zero `stop_pct` — a pump must never be configured to stop (DEC-167) — and `TOO_MANY_CURVES` / `TOO_MANY_CONTROLS` when a profile exceeds 256 of either. Those two are recursion bounds, not taste limits: Mix/Sync dependency resolution recurses once per link, and a deep but perfectly *acyclic* chain passes the cycle check, so an unbounded one overflowed the daemon's stack. The GUI mirrors the same caps at its parse boundary). All violations are collected before responding; clients map `reason` and must never string-match `description`.
- 400 `feature_unavailable` (source: `"validation"`, retryable: false) — the endpoint exists and the addressed device exists, but that device does not support the requested operation. Currently surfaced by:
  - GPU fan writes/resets when the GPU has neither a PMFW `fan_curve` nor legacy `pwm1` write path (DEC-098); and
  - hwmon PWM writes when the targeted header's discovered `is_writable=false` (DEC-102), e.g. an unforeseen chip exposing a read-only `pwmN` file.

  Distinct from `hardware_unavailable` (transient / retryable) and `validation_error` (malformed request). Permanent for this device — clients must not retry.
- 403 `lease_required` (source: `"validation"`, retryable: false) — **retired** with the bare hwmon PWM-write and the GUI-held lease (DEC-165); **fully removed at DEC-170**, when the verify path's internal-lease lapse was re-mapped to retryable `503 hardware_unavailable`. No route emits this code any more. Listed for historical context.
- 404 `not_found` (source: `"validation"`, retryable: false) — **unknown route/URI only**. An unknown *resource* on a known route (hwmon header, GPU id) returns 404 with code `validation_error`, not `not_found`.
- 404 `override_expired` (source: `"validation"`, retryable: false) — renew/release of a manual override (DEC-163) that already lapsed on the daemon's deadman, or was never taken; re-take rather than renew.
- 409 `lease_already_held` (source: `"validation"`, retryable: false) — **retired** with the GUI-held lease (DEC-165); **fully removed at DEC-170** (the verify mapper no longer emits it). No route emits this code any more. Listed for historical context.
- 409 `already_exists` (source: `"validation"`, retryable: false) — `POST /profiles` with an `id` that already exists (DEC-160). Rename or `PUT` the existing profile instead.
- 409 `profile_in_use` (source: `"validation"`, retryable: false) — `DELETE /profiles/{id}` on the currently active profile (DEC-160); deactivate or switch profiles first.
- 409 `thermal_abort` (source: `"hardware"`, retryable: true) — a fan diagnostic was aborted or refused due to high temperature: calibration aborts mid-sweep, and a verify refuses to start while any sensor is over the 85 °C limit (DEC-201, daemon ≥ 2.6.0)
- 409 `validation_error` (source: `"validation"`, retryable: false) — a fan diagnostic (`POST /fans/openfan/{ch}/calibrate`, `POST /hwmon/{id}/verify`, or `POST /gpu/{id}/fan/verify`) when another calibration **or** verify is already in progress (they share a single-flight pause, DEC-191, daemon ≥ 2.2.2). Retry once the in-flight operation completes. (HTTP 409 with the `validation_error` code — matches the long-standing "calibration already in progress" response shape.) `POST /fans/openfan/rescan` uses the same shape for its own single-flight (DEC-265, daemon ≥ 2.18.0), on a **separate** flag — a rescan and a calibration do not block each other.
- 409 `stale_fencing_token` (source: `"validation"`, retryable: false) — override renew/release (DEC-163) bearing a superseded `override_token`; a newer override has been issued for that control, so the stale holder cannot re-pin (fencing)
- 500 `internal_error` (source: `"internal"`, retryable: true)
- 503 `hardware_unavailable` (source: `"hardware"`, retryable: true)
- 503 `persistence_failed` (source: `"internal"`, retryable: true) — returned by `POST /config/*` when the daemon cannot persist the runtime configuration file

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
  (DEC-162; see `docs/09_State_Model_and_Control_Behaviour.md`).
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

### NVIDIA GPU (read-only, DEC-204)
- **No write path exists (by design).** The open `nouveau` driver *does* expose
  a writable `pwm1`, but the daemon deliberately excludes it from hwmon discovery
  for safety; the opt-in proprietary NVML backend is telemetry-only. NVIDIA fans
  are never written.
- `fan_control_method` is always `"read_only"` (fan present) or `"none"`.
- Temps arrive with `source: "nvidia_gpu"`, kind `gpu_temp`, `chip_name`
  `"nouveau"` (open) or `"nvml"` (proprietary); fans as `nvidia_gpu:{bdf}` with a
  measured `duty_pct` (may exceed 100) but no `last_commanded_pwm`.
- The `driver` field on the capability/diagnostics is the **kernel module name**
  (`"nouveau"`/`"nvidia"`), not the `nvml` library.
- The GUI must not offer NVIDIA GPU fans as controllable curve members and must
  never write to a `nvidia_gpu:` target; the GPU's temperatures remain usable as
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

The `PollingService` owns the full read path (`/poll`, history). The GUI is poll-only and detects
transitions by poll-diff. (SSE was never consumed — DEC-164 deferred it past 2.0.0, and the `/events`
endpoint was removed entirely in daemon v2.5.0, DEC-198.)

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
- reflect daemon-held overrides from `/status.overrides[]` on Controls cards — a read-only
  "External" chip for foreign overrides (no token → display-only + explicit take-over) (DEC-169).
  The legacy standalone read-only `overrides[]` / `fan_identify[]` table was retired with the
  tabbed Diagnostics page in the redesign; override state now surfaces on the Controls cards

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
  - The GUI activates **by path** (`profile_path` = its own `~/.config/control-ofc/profiles/{id}.json`), which the daemon resolves because the GUI registers that directory as a profile search dir on connect. If the GUI-local and daemon-store copies of an id diverge, activation runs the **local** copy — not necessarily the `/var/lib/control-ofc/profiles/` one.
- The daemon persists the active-profile *pointer* to `/var/lib/control-ofc/daemon_state.json`

Profile *storage of record* moved to the daemon at 2.0.0 (DEC-160); the GUI keeps a local draft cache and uploads / validates through the CRUD API.

The profile **curve schema is v7** (GUI `PROFILE_SCHEMA_VERSION` / daemon `default_version`). Both evaluators must recognise the same curve `type` values — `graph`, `stepped`, `linear`, `flat`, `trigger`, `mix`, `sync` — and the **composite** types carry extra fields the daemon parses and evaluates: `mix` (`mix_function`, `mix_curve_ids`) combines other curves at their own sensors; `sync` (`sync_control_id`, `sync_offset_pct`) mirrors another control's tuned output via stable topological control ordering (DEC-150/151, retiring the single-sensor rule DEC-014 via DEC-152). The byte-identical `parity_vectors.json` fixture pins GUI ⇄ daemon evaluation agreement (DEC-126). Schema changes are additive: a v7 profile using a new curve type still loads on an older daemon/GUI, which degrades safely (daemon → 50%, GUI → flat) rather than crashing.

## Config management

- **`GET /config` — the read side (daemon ≥ 2.16.0, DEC-243).** Returns the
  daemon's effective configuration: `admin_config_path`, `runtime_config_path`, a
  top-level `restart_pending` rollup, and `keys[]`. Each key carries:
  - `key` — dotted path (`startup.delay_secs`)
  - `value` — the **on-disk effective** value: `daemon.toml` with the
    `runtime.toml` overlay applied, i.e. what a restart would produce
  - `running_value` — what this daemon process actually started with. **Always
    present.** It was briefly omitted-when-equal, but that is unrepresentable for
    a nullable key: `serial.port` is `Option<String>`, so a genuine null
    serialises as `"running_value": null` and a client applying an
    absent-means-same rule reports the *file's* port as the one in use. Always
    sending it makes `null` mean exactly one thing — not set.
  - `source` — `runtime` | `admin` | `default`. `runtime` means a `POST /config/*`
    write is shadowing the admin file — the one case where an operator's
    `daemon.toml` edit appears to do nothing.
  - `mutable` — a `POST /config/*` route exists for it
  - `requires_restart`, `restart_pending` — `restart_pending` is **the daemon's
    verdict** (`value != running_value`). Clients must not re-derive it from what
    they posted.
  - `requires_privilege` — present only when a config write alone cannot enable
    the feature (the two `[detection]` opt-ins, which also need a root systemd
    drop-in). A client must not present such a key as enabled on the strength of
    the flag.

  Before this endpoint the writable knobs were **write-only**: the GUI kept a
  local mirror and pushed it on save, so a fresh GUI against a daemon set to 10 s
  displayed 0 s. Older daemons answer `404`; the GUI stands the card down for the
  session rather than showing invented values (the DEC-200 precedent).

  `ipc.socket_path` and `state.state_dir` are reported with `mutable: false` **by
  design** — a bad socket path permanently locks out every client, and moving the
  state dir orphans `runtime.toml` and the profile store.

- **Extended writes (daemon ≥ 2.16.0, DEC-243).** All persist to `runtime.toml`,
  all are start-only, all return `503 persistence_failed` on a write error, and
  each response carries `{"updated", "key", "value", "note"}`:
  - `POST /config/poll-interval` — `{"poll_interval_ms": 250..2000}`. Both bounds
    are deliberate. The floor: the control loop, serial I/O and sysfs writes all
    run on this cadence, so a tiny value is a self-inflicted DoS on the hardware
    the daemon exists to protect. **The ceiling is [SAFETY]**: this drives the
    sensor poll loop, and the 105 °C rule's staleness budget is derived from it
    (5×, DEC-267 — see § Freshness above; the leg is *not* unfiltered, and this
    ceiling bounds that budget rather than substituting for it). The API is
    tighter than the admin file because it is reachable by any local user (0666
    socket). The admin file's own range is **100–6000 ms**: past 6000 the budget
    stops tracking the cadence (it is capped at 30 s), so the 5x headroom erodes
    towards 1x and ordinary readings start to look stale — and past 30 s it
    inverts, every reading stale on arrival and the ladder never firing. The
    daemon clamps to 6000 with a warning rather than honouring a slower value
    (DEC-270).
  - `POST /config/serial-port` — `{"port": string | null}`. **Validated against
    the serial transport's own allowlist** (`/dev/tty{S,USB,ACM,AMA}*`,
    `/dev/serial/*`; no `..`, no NUL) and capped at 256 characters — the daemon
    opens this path as root and the endpoint is unprivileged-reachable. A second,
    looser copy of this check previously accepted `/dev/shm/...`. `null` clears
    the override and returns to auto-detection. Note the daemon **falls back to
    auto-detection when a configured port cannot be opened**, so a bad value
    cannot durably remove OpenFan control (and with it the 105 °C emergency's
    only path to those fans).
  - `POST /config/serial-timeout` — `{"timeout_ms": 50..1000}`. **The ceiling is
    [SAFETY]**: an emergency `force_all` awaits the OpenFan backend before the
    hwmon one, costing up to `channels × timeout` on a wedged link.
  - `POST /config/allow-port-probe` / `POST /config/nvidia-telemetry` —
    `{"enabled": bool}`. On success with `enabled: true` the response adds
    `requires_privilege`, because the systemd drop-in is the other half of the
    requirement and no API can install it.

  Out-of-range, wrong-typed and missing keys are all `400 validation_error`.

- `POST /config/profile-search-dirs` — add directories to the daemon's profile search path (persisted to `runtime.toml` per ADR-002). Each added dir must be an absolute path with no `..`. **Peer-uid confined (daemon ≥ 2.9.0, DEC-205):** a non-root client may only add directories that exist and canonicalize to a path within its **own home directory** (resolved from the socket peer's `SO_PEERCRED` uid); root / CLI callers are exempt. An out-of-home dir, a nonexistent/unreadable dir, or a caller whose uid/home cannot be resolved is `400 validation_error`; a persistence failure is `503 persistence_failed`. Older daemons (< 2.9.0) accept any absolute dir. The GUI already surfaces the daemon's message to the user (Settings ▸ profiles-directory picker shows it prefixed with `Failed to update daemon:`), so no client change was needed.
- `POST /config/startup-delay` — set the daemon startup delay in seconds (persisted to `runtime.toml`, takes effect on next restart). The GUI pushes this best-effort on **both** Settings → Save and Settings → Import; a `DaemonError` is logged and surfaced in the save status, never fatal (Settings page wires the daemon client as of the 2026-06 audit, F2/F11).
- `POST /config/preferred-cpu-sensor` / `POST /config/preferred-mb-sensor` — persist the user's preferred CPU / motherboard temperature sensor by stable id (body `{"sensor_id": string | null}`; `null` clears the preference). The id is validated against the live sensor set — an unknown id (or a missing key) is `400 validation_error`; a persistence failure is `503 persistence_failed`. Advisory only (thermal safety still keys off `kind`) — reflected in `/inventory/hwmon` `default_cpu` (`source: "user"`) + `preferences` and the readiness `selected_cpu_sensor_missing` item. Daemon ≥ 2.6.0 (DEC-200); older daemons answer `404` and the GUI hides the feature for the session. The GUI offers these from the Overview page's sensor-table context menu and the Settings page.

## GUI startup behaviour

- **Demo-on-disconnect (DEC-139):** when the user enables "start in demo mode when daemon is unavailable", `main.py` probes the daemon once at launch with `GET /status` (~1.5 s timeout) on a throwaway client. Only `DaemonUnavailable` (socket missing/refused) triggers the demo fallback; a timeout or server error is treated as "present but slow" so a sluggish daemon never silently disables real control. This is launch-only — a mid-session disconnect uses the normal READ_ONLY/reconnect path.
