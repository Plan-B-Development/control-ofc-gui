# Contract Mismatch Backlog — 2026-04-22 investigation

Working-doc backlog from a cross-stack `/investigate-mismatch` sweep against
the GUI ↔ daemon contract.

Investigated versions: **GUI 1.5.2**, **daemon 1.4.2** (post-ship). Findings
are from static analysis only; none were reproduced on real hardware.

All 15 items are now shipped across the two repositories. This file can be
retired on the next housekeeping pass — the durable content has been folded
into `docs/08_API_Integration_Contract.md`, `docs/14_Risks_Gaps_and_Future_Work.md`,
and the daemon's `DECISIONS.md`.

## Implementation status (2026-04-23)

GUI-side items shipped on `main` (control-ofc-gui):

| # | Title | Status | Notes |
|---|---|---|---|
| M4 | Lease acquisition gated on `hwmon.present` | **Shipped** | `ControlLoopService._maybe_acquire_lease`; retries on capabilities update |
| M5 | Profile search dir re-registers on reconnect | **Shipped** | Wired through `state.capabilities_updated` |
| M8 | `activate_profile` accepts `profile_path` or `profile_id` | **Shipped** | Decision A(a); keyword-only `profile_id` |
| M9 | GPU fan reset on GUI close (no active profile) | **Shipped** | Decision B(a); cached profile state, best-effort |
| M10 | Demo `min_pwm_percent` 30 → 0 | **Shipped** | Matches daemon reality |
| M11 (GUI half) | GUI tolerates `pci_id`/`pci_bdf` on both endpoints | **Shipped** | Decision C(a); `_coalesce_pci_bdf` in `api/models.py` |
| M14 | `test_acquire_failure` uses reachable error code | **Shipped** | `hardware_unavailable` instead of `lease_already_held` |
| M2 / M3 | docs/08 stale PWM-floor claims | **Shipped** | Replaced with "pass-through; safety floors are GUI-side" |
| M6 | `stall_detected` documented | **Shipped** | Added to /fans field list |
| M7 | `chip_name` always-present documented | **Shipped** | "Always present; `amdgpu` for GPU sources" |
| M15 | `openfan.channels` = 10 documented | **Shipped** | Added to /capabilities notable fields |

Daemon-side items shipped (control-ofc-daemon, pending a version bump):

| # | Title | Status | Notes |
|---|---|---|---|
| M1 | Profile engine applies full tuning pipeline | **Shipped** | Option (b) — `ProfileEngineState` owned by `profile_engine_loop`. Ports GUI's 6-step pipeline (offset → minimum → step-rate → stop-snap → start-hysteresis → clamp), tracks pre-rounding f64 across cycles, clears on profile id change / deactivation. 10 new unit tests |
| M12 | `hwmon_verify` missing-controller: 404 → 503 | **Shipped** | Now matches sibling hwmon handlers; integration test added |
| M13 | GPU hardware-unavailable: 500 → 503 | **Shipped** | 4 match arms in `gpu.rs`; spawn_blocking task failures stay 500 (internal). Unknown-GPU stays 404 (validation) |
| M11 (daemon half) | Emit both `pci_id` and `pci_bdf` everywhere | **Shipped** | `/capabilities` gains `pci_bdf`, `/diagnostics/hardware` gains `pci_id`; both structs populated from the same BDF string. Serialization tests confirm |

## Decisions resolved (2026-04-23)

- **M1 cross-cycle state** → **(b)** Task-local `ProfileEngineState` owned
  by `profile_engine_loop`. No shared-state plumbing; matches the existing
  task-local pattern already used for `gpu_fail_cache`,
  `openfan_consecutive_failures`, and `no_cpu_sensor_cycles`. Observability
  can be added later by snapshotting into the cache if `/status` ever needs
  it.
- **M8 `profile_id` surface** → **(a)** `DaemonClient.activate_profile` now
  accepts either `profile_path` (positional or keyword, canonical) or
  `profile_id` (keyword-only, for daemon-bundled profiles). Exactly one is
  required; `ValueError` otherwise.
- **M9 GPU reset policy** → **(a)** `MainWindow._maybe_reset_gpu_on_close`
  calls `client.reset_gpu_fan` when `AppState.gui_wrote_gpu_fan` is set AND
  `active_profile_name` is empty. Uses cached state — no blocking API call
  during close. Failures are logged, not surfaced.
- **M11 PCI field naming** → **(a)** Daemon emits both `pci_id` and
  `pci_bdf` on `/capabilities.amd_gpu` and on `/diagnostics/hardware.gpu`;
  GUI parsers coalesce either name into whichever dataclass field exists.
  Legacy name (`pci_id` on capabilities, `pci_bdf` on diagnostics) marked
  deprecated in Rust doc comments for eventual removal.

## Historical — original decisions-needed list (resolved)

Kept for audit trail; all items now closed above.

- [x] **M1 cross-cycle state.** Applying `step_up_pct` / `step_down_pct`
  requires the daemon profile engine to persist each control's `last_output`
  across 1 Hz cycles. Options: (a) extend `DaemonState` with per-control
  tuning state; (b) add a `ProfileEngineState` struct owned by the engine
  loop task. (b) is simpler but less observable from `/status`.

---

## M1 — Daemon profile engine ignores step/start/stop tuning

**Classification:** Daemon bug (missing feature). Behaviour of headless
profile mode diverges from GUI-driven mode for the same profile.

**Priority:** P1 — silent divergence; two modes produce different fan
outputs from identical inputs.

**Files:**
- `daemon/src/profile_engine.rs:66-79` (`evaluate_profile`) — applies only
  `offset_pct` and `minimum_pct`. `step_up_pct`, `step_down_pct`,
  `start_pct`, `stop_pct` are read from the profile but never used.
- `src/control_ofc/services/profile_service.py:178-206` — `LogicalControl`
  field declarations (full tuning surface).
- `src/control_ofc/services/control_loop.py:366-392` — GUI's `_apply_tuning`
  shows the expected order: step-rate limit → start/stop thresholds →
  offset → minimum clamp.

**Evidence:** `grep -n "step_up_pct\|start_pct\|stop_pct" daemon/src/profile_engine.rs`
returns matches only in profile-deserialisation code, never in the
evaluator path. `docs/09_State_Model_Control_Loop_and_Lease_Behaviour.md`
describes the full pipeline as the contract.

**Fix outline:**
1. Extend `profile_engine.rs` to track per-control `last_output: HashMap<String, f64>`
   across cycles (decision above).
2. Apply `step_up_pct`/`step_down_pct` against the previous cycle's output
   (bounded by `+step_up_pct`/`-step_down_pct` per cycle).
3. Apply `start_pct`/`stop_pct` as hysteresis thresholds (fan stays off
   below `stop_pct`, turns on at `start_pct`).
4. Apply `offset_pct`, then clamp to `minimum_pct..=100`.

**Test plan:** Add unit tests in `profile_engine.rs` mirroring the GUI's
expectations. Key cases: step-up rate-limited from 30→60 with
`step_up_pct=10`; start threshold keeps fan off under first crossing;
cross-cycle state maintained between `evaluate_profile` calls.

---

## M4 — GUI acquires hwmon lease on no-hwmon systems

**Classification:** GUI bug. User-visible error banner at startup on valid
hardware configurations.

**Priority:** P1 — anyone on OpenFan-only or GPU-only machines sees a scary
"Fans returning to BIOS control" warning that is factually wrong.

**Files:**
- `src/control_ofc/services/control_loop.py:200-201` — `ControlLoopService.start()`
  unconditionally calls `self._lease.acquire()`.
- `src/control_ofc/services/control_loop.py:481-490` — `_on_lease_lost`
  transitions to `READ_ONLY` and logs the error.
- `src/control_ofc/services/lease_service.py:48-63` — `LeaseService.acquire`
  emits `lease_lost` on daemon 503 `hardware_unavailable`.

**Evidence:** CLAUDE.md: "OpenFan writes need no lease; hwmon writes require
a held lease." The current code ignores `capabilities.hwmon.present`.

**Fix outline:**
1. In `ControlLoopService.start()`, check
   `self._state.capabilities.hwmon and self._state.capabilities.hwmon.present`
   before calling `self._lease.acquire()`.
2. If not present, skip lease acquisition entirely; OpenFan/GPU writes work
   without it.
3. When capabilities update (e.g. after rescan), re-evaluate and acquire if
   hwmon becomes present.

**Test plan:** New test in `tests/test_control_loop.py` — start control loop
with `Capabilities(hwmon=HwmonCapability(present=False))`, assert
`lease_service.acquire` is not called and no `lease_lost` warning is
emitted. Existing tests for the hwmon-present path should continue to pass.

---

## M5 — `register_profile_search_dir` runs only once at startup

**Classification:** GUI bug. Affects users who start the daemon after the
GUI.

**Priority:** P2 — edge case, but silent failure mode: first
`/profile/activate` after the daemon comes up returns 400
`validation_error: "profile_path must be within a profile search directory"`.

**Files:**
- `src/control_ofc/main.py:36-61` — `register_profile_search_dir` helper.
- `src/control_ofc/main.py:127` — single call site, before polling starts.
- `src/control_ofc/services/polling.py` — `_on_connected` does not re-run
  registration on reconnect.

**Evidence:** Hook point: `PollingService._on_connected` already fires on
reconnect (`polling.py:206-212`) — registration can be slotted in there.

**Fix outline:**
1. Move `register_profile_search_dir` call to a slot on the `connected`
   signal (or `capabilities_ready`, which also fires on first poll after
   reconnect).
2. Make it idempotent (the daemon handler already tolerates duplicate
   additions — confirm).

**Test plan:** `tests/test_polling_service.py` — simulate
connect→disconnect→reconnect and assert one additional
`update_profile_search_dirs` call is issued after reconnect.

---

## M9 — GPU fan stays at last commanded value when GUI exits with no active profile

**Classification:** GUI gap. Not a bug per se — daemon handles the reset on
its own shutdown — but a graceful-exit gap when the GUI quits while the
daemon keeps running.

**Priority:** P3 — blocked on **decision** (see above).

**Files:**
- `src/control_ofc/ui/main_window.py:240-254` — `closeEvent`; only calls
  control loop and page cleanups.
- `src/control_ofc/api/client.py:236-239` — `reset_gpu_fan` is defined but
  has no UI caller.

**Evidence:** `grep -rn "reset_gpu_fan" src/control_ofc/ui/` returns no
hits. See also docs/14 §11 "GUI surface for `reset_gpu_fan` (deferred)" —
this mismatch is the close-event half of the same gap.

**Fix outline (if decision = ship):** In `closeEvent`, if
`ActiveProfileInfo.active is False` and `AppState` has a recent GUI-written
GPU fan, call `client.reset_gpu_fan(gpu_id)` best-effort. One test covering
the active-profile branch and the no-profile branch.

---

## M10 — Demo `min_pwm_percent=30` contradicts daemon reality (0)

**Classification:** Demo drift. Minor GUI bug, harmless in practice because
`min_pwm_percent` is no longer used for clamping anywhere.

**Priority:** P3.

**Files:**
- `src/control_ofc/services/demo_service.py:93-114` — `_DEMO_HWMON_HEADERS`
  sets `min_pwm_percent: 30` for two headers.

**Fix outline:** Set both to `0`. Grep for any demo-facing tests asserting
`30` — none expected but worth a quick check.

---

## M12 — `hwmon_verify_handler` returns 404 for missing controller

**Classification:** Daemon bug (minor inconsistency). All sibling hwmon
handlers return 503 `hardware_unavailable` for the same condition.

**Priority:** P3.

**Files:**
- `daemon/src/api/handlers/hwmon_ctl.rs:320-327` — returns 404
  `validation_error` when `state.hwmon_controller` is `None`.
- Sibling handlers in the same file that return 503: `hwmon_lease_take_handler:64-69`,
  `hwmon_lease_release_handler:92-97`, `hwmon_set_pwm_handler` (earlier),
  etc.

**Fix outline:** Change to `StatusCode::SERVICE_UNAVAILABLE` +
`ErrorEnvelope::hardware_unavailable(...)` to match siblings. Extend the
handler tests to assert the status.

---

## M13 — GPU handlers use 500 for `hardware_unavailable`

**Classification:** Daemon bug. Wrong HTTP status paired with the right
error code.

**Priority:** P3 — GUI treats both as generic `DaemonError`, so no
user-visible breakage, but the contract says 503.

**Files:**
- `daemon/src/api/handlers/gpu.rs:101-106` (GPU set speed), `:152-155`
  (GPU fan reset) — both use `StatusCode::INTERNAL_SERVER_ERROR` for
  `hardware_unavailable`.

**Fix outline:** Swap to `StatusCode::SERVICE_UNAVAILABLE`. Update the
matching handler tests.

---

## M14 — `test_acquire_failure` simulates an unreachable error code

**Classification:** Test drift. The mocked error cannot actually be emitted
by `POST /hwmon/lease/take` (force-take never returns `lease_already_held`
per DEC-049 and our own `docs/08`).

**Priority:** P3.

**Files:**
- `tests/test_lease_service.py:43-50` — mocks
  `hwmon_lease_take.side_effect = DaemonError(code="lease_already_held", ...)`.

**Fix outline:** Change the mocked code to `hardware_unavailable` (the code
that this path actually returns for a missing controller). The assertion
(`lease_lost` emission) stays unchanged.

---

## M2 — Docs claim hwmon 20%/30% floors; code passes through

**Classification:** Documentation drift. Daemon intentionally delegates
floor safety to the GUI and `ThermalSafetyRule`, but docs still describe
the old clamping behaviour.

**Priority:** P3.

**Files (source of reality):**
- `daemon/src/hwmon/pwm_control.rs:184-191` — only rejects `pwm_percent > 100`.
- `daemon/src/hwmon/pwm_control.rs:459` (test comment): "No floor clamping
  — thermal safety handled by ThermalSafetyRule".

**Files (stale docs to fix):**
- `docs/08_API_Integration_Contract.md:222-234` — "chassis: 0% or 20–100%;
  CPU/pump: 30–100% (0% rejected outright)".
- `CLAUDE.md` — same claims mirrored.

**Fix outline:** Replace with a single line: "daemon passes PWM 0–100
through; safety floors are GUI-side profile constraints (see
`docs/09_State_Model_Control_Loop_and_Lease_Behaviour.md`)." Confirmed
intentional by DEC-022 and the "No per-header PWM floors" rule in CLAUDE.md.

---

## M3 — OpenFan "1–19% clamped to 20%" likewise not enforced

Same pattern as M2.

**Files (reality):** `daemon/src/serial/controller.rs:214-228`
(`apply_safety`) — only enforces 0% stop timeout.

**Files (stale docs):** `docs/08_API_Integration_Contract.md:223`.

**Fix outline:** Delete the clamping claim from docs/08; leave the 0%
stop-timeout claim intact (that IS enforced).

---

## M6 — `stall_detected` field undocumented

**Classification:** Documentation drift. Both code sides are in sync; only
the contract spec is stale.

**Files (reality):**
- `daemon/src/api/responses.rs:94-97` — `FanEntry.stall_detected: Option<bool>`.
- `src/control_ofc/api/models.py:172` — GUI parses it.
- `src/control_ofc/services/app_state.py:211` — GUI raises warnings from it.

**Files (stale docs):** `docs/08_API_Integration_Contract.md:87-96` — fan
field list omits `stall_detected`.

**Fix outline:** Add one bullet to the /fans field list: "`stall_detected`
(optional bool) — daemon-asserted; set when commanded PWM ≥5% but measured
RPM is zero for ≥2 cycles."

---

## M7 — `chip_name` documented optional, daemon always emits

**Classification:** Documentation drift.

**Files (reality):** `daemon/src/api/handlers/mod.rs:55` wraps as
`Some(s.chip_name.clone())` unconditionally; AMD GPU path emits
`chip_name: "amdgpu"` (from `hwmon/discovery.rs:18`).

**Files (stale docs):** `docs/08_API_Integration_Contract.md:84` — "Absent
for non-hwmon sources."

**Fix outline:** Change "Absent for non-hwmon sources" to "Always present;
set to `\"amdgpu\"` for GPU sources."

---

## M15 — `openfan.channels` hardcoded to 10

**Classification:** Documentation drift / undocumented assumption.

**Files:** `daemon/src/api/handlers/status.rs:117-121` — `channels: 10` for
both `present` and non-present cases.

**Fix outline:** Add a single line to `docs/08_API_Integration_Contract.md`
openfan capability section: "`channels` is always 10 in V1 (OpenFan v1
hardware has 10 channels)."

---

## M8 — GUI only sends `profile_path`; daemon also accepts `profile_id`

**Classification:** Surface asymmetry. Blocked on **decision** (see above).

**Files:**
- `src/control_ofc/api/client.py:200-203` — `activate_profile(self, profile_path: str)`.
- `daemon/src/api/handlers/profile.rs` — accepts both `profile_id` and
  `profile_path`.

---

## M11 — `pci_id` (capabilities) vs `pci_bdf` (diagnostics)

**Classification:** Contract inconsistency. Blocked on **decision** (see above).

**Files:**
- `daemon/src/api/responses.rs:236-240` — `AmdGpuCapability.pci_id`.
- `daemon/src/api/responses.rs:394` — `GpuDiagnostics.pci_bdf`.
- GUI models mirror both names faithfully in
  `src/control_ofc/api/models.py`.

---

## Investigation provenance

Re-running the exact checks (for verification or re-audit):

```bash
# M1 — does daemon apply full tuning?
grep -n "step_up_pct\|step_down_pct\|start_pct\|stop_pct" \
  /home/mitch/Development/control-ofc-daemon/daemon/src/profile_engine.rs

# M4 — does ControlLoopService gate lease acquisition on capabilities?
grep -n "self._lease.acquire\|hwmon.present" \
  /home/mitch/Development/control-ofc-gui/src/control_ofc/services/control_loop.py

# M5 — where is register_profile_search_dir called?
grep -rn "register_profile_search_dir" \
  /home/mitch/Development/control-ofc-gui/src/

# M9 — is reset_gpu_fan wired anywhere in the UI?
grep -rn "reset_gpu_fan" /home/mitch/Development/control-ofc-gui/src/control_ofc/ui/

# M12–M13 — HTTP status for hardware_unavailable
grep -n "hardware_unavailable" \
  /home/mitch/Development/control-ofc-daemon/daemon/src/api/handlers/*.rs

# M14 — lease_already_held in tests
grep -n "lease_already_held" \
  /home/mitch/Development/control-ofc-gui/tests/test_lease_service.py
```
