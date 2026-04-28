# PWM Verify & hwmon Presentation Remediation (v2)

**Status:** Actionable punch list — APPROVED 2026-04-28 for `/implement`
**Origin:** `/investigate-bug` pass on 2026-04-26 triggered by user report that PWM fan control on a **Gigabyte X870E AORUS MASTER** (BIOS F13a) was "not working" despite documented workarounds. The original investigation produced several incorrect technical claims; this v2 supersedes that analysis after on-machine verification and online research on 2026-04-28.
**Intended consumer:** `/implement`. The approved decisions in §"Approved decisions" below are baked into the punch list and must not be re-debated during implementation.
**Do not delete** until the items below have shipped or been explicitly deferred.

---

## Why `/implement` and not `/fix-issue`

The investigation is complete. The "PWM fans cannot be controlled" headline is **false** on this board — control IS working. The user-perceived bug is caused by three downstream defects in how the daemon and GUI report on PWM control plus a documentation gap. `/fix-issue` would re-investigate; `/implement` is correct per its description ("approved changes after an /investigate-* result").

## Corrections from the original investigation

The 2026-04-26 investigation report contained these technical errors. They are corrected here:

| Original claim | Verified reality (2026-04-28) |
|---|---|
| "5 PWM headers, all writable" | **9 headers, 8 writable.** IT8696E pwm1..pwm5 + IT87952E pwm1..pwm3 + amdgpu pwm1 (read-only). Confirmed via `GET /diagnostics/hardware`: `total_headers: 9, writable_headers: 8`. |
| "Only one Super-I/O on this board (IT8696E)" | **Two ITE Super-I/Os present and bound:** IT8696E at `it87.2624` (hwmon4) and IT87952E at `it87.2656` (hwmon5). Both confirmed via `/sys/class/hwmon/*/name`. |
| "`hidden_chart_series` references a phantom chip from a previous board" | **Not phantom.** `it87952:it87.2656` is a real, currently-loaded chip on this exact board. The user's hidden entries are legitimate display preferences and must not be auto-pruned. |
| "Auto-points (BIOS-programmed fan curve) — All zeroed — no BIOS smartfan curve is fighting writes" | **240 enable reverts on pwm1 already counted by the watchdog.** BIOS Smart Fan IS reclaiming `pwm_enable` on CPU_FAN at ~1 Hz. The daemon's `pwm_enable` watchdog (v1.3.0+) handles this transparently and the surface-level write outcome is correct, but the original "no BIOS curve" framing was wrong. |
| "GA-X870E-AORUS-MASTER.conf exists upstream as a label data source" | **Does not exist** in `lm_sensors/configs/Gigabyte/`. Newest Gigabyte config in upstream is from B550/X470 era. Community is working on it. We cannot use it as a static reference. |
| "Profile engine is the verify race source" | **GUI's own control loop is the racer.** DEC-074 already makes the profile engine defer when GUI is active <30s. The concurrent writer the original investigation observed was the GUI itself. The fix lives in the GUI. |

## Approved decisions (locked in)

The following decisions were approved on 2026-04-28 and must not be re-litigated:

1. **Daemon scope:** GUI fix + daemon classifier-wording change. **No** profile-engine in-flight-verify guard — DEC-074 already covers the dominant case.
2. **Per-board fan label data source:** Read `/etc/sensors.d/*.conf` and `/usr/share/sensors/*.conf` at runtime (libsensors-style label extraction), with a small in-repo fallback table for boards/chips with no installed config. Daemon-supplied sysfs `*_label` (already read by `pwm_discovery.rs`) remains highest priority; user aliases (existing `app_settings.json::fan_aliases`) remain absolute top.
3. **Empty-header rendering scope:** Diagnostics → Fans + Controls (fan-role member selection) + Fan Wizard. Dashboard already auto-hides via `hide_unused_fan_headers` and is out of scope.
4. **Unverified label suffix:** Mappings without confirmed silkscreen evidence ship with a "(unverified)" suffix. The X870E AORUS MASTER IT87952E mapping is unverified; the IT8696E mapping is high-confidence per community references.
5. **Doc accuracy:** This file (v2) replaces the v1 plan and the original investigation analysis. The "Corrections from the original investigation" section above is authoritative.

## Constraints (must hold)

Pulled from `CLAUDE.md`:

- GUI must never directly access hardware. All reads/writes through the daemon HTTP API. Reading `/etc/sensors.d/*.conf` is **not** hardware access — it is reading user-space configuration files, equivalent to reading `app_settings.json`.
- Daemon is the source of truth for fan-control reality (`fan_control_method`, `is_writable`, `writable_headers`, `pwm_mode`, `temp_type`, `verify` classifications).
- **Never claim more certainty about a fan/header/control state than the daemon's probe supports.** When the classifier cannot distinguish two causes, surface ambiguity.
- Testing policy: every item ships with tests. Bug fixes include a regression test. Hardware-facing logic uses simulated fixtures.
- Quality gates: GUI — `ruff format --check src/ tests/`, `ruff check src/ tests/`, `pytest`. Daemon — `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.
- Do **not** modify `CLAUDE.md`, `DECISIONS.md`, or anything under `.claude/`. The implementer should write proposed DEC-NN entries locally for traceability without committing them.
- Daemon item must preserve API contract — no new error envelope variants, no breaking changes to the verify `result` enum (`effective`, `pwm_enable_reverted`, `pwm_value_clamped`, `no_rpm_effect`, `rpm_unavailable`).

## Priority legend

- **P1** — Truthfulness or core UX. Ship in Batch A/B.
- **P2** — Useful supplement. Ship in Batch A/B.
- **P3** — Documentation. Ship in Batch C.

---

## Punch list

### A1 — GUI: pause control loop for header under in-flight verify (P1)

**Layer:** GUI (Python). **Approved Decision 1.**

**Problem.** The GUI's `ControlLoopService._write_target` (`src/control_ofc/services/control_loop.py:518`) ticks every 1 s and writes hwmon PWM via the daemon. The Diagnostics `_VerifyWorker` (`src/control_ofc/ui/pages/diagnostics_page.py:267`) calls `verify_hwmon_pwm` which writes a test value, sleeps 3 s server-side, then reads back. During the 3 s sleep, the control loop's next tick overwrites the test value, and the daemon classifier blames "BIOS/EC" via `pwm_value_clamped` (`hwmon_ctl.rs:450`).

**Fix.** Add a per-header pause-set to `ControlLoopService` (e.g. `_paused_headers: set[str]`). The Diagnostics page calls `control_loop.pause_writes_for_header(header_id)` before emitting `_verify_request`, and `resume_writes_for_header(header_id)` from the verify-completed/error slots. Defensive timeout: an absolute 5 s timer auto-resumes regardless, so a hung verify cannot leave a header paused forever.

**Acceptance.**
- Test: starting a verify on header X causes the next 1 s tick to skip writes to X but proceed for other headers.
- Test: verify completes → next tick writes to X again immediately.
- Test: verify worker never signals (mocked stuck) → 5 s safety timer auto-resumes writes; assertion that the tick after 5 s writes.
- Test: paired pause/resume calls don't lose other headers' state if multiple verifies overlap (use a counter or set, not a single boolean).
- No regression in hysteresis or the 1 % PWM write-suppression for unaffected headers.

**Files.** `src/control_ofc/services/control_loop.py` (new methods + state), `src/control_ofc/ui/pages/diagnostics_page.py` (call pause/resume around verify), `tests/services/test_control_loop.py` (new tests).

---

### A2 — GUI: "controllable, no fan detected" classification across surfaces (P1)

**Layer:** GUI (Python). **Approved Decision 3 — three surfaces.**

**Problem.** On the X870E AORUS MASTER 7 of 8 writable hwmon headers read 0 RPM because nothing is plugged into them. Today they look identical to a broken/uncontrollable header. Three GUI surfaces have the gap:

1. **Diagnostics → Fans table** (`diagnostics_page.py:1027-1113`) — RPM column shows `0` or `—` with no badge.
2. **Controls fan-role member selection** (`fan_role_dialog.py` Edit Members callback path) — empty headers appear in the picker indistinguishable from working ones.
3. **Fan Wizard** — already filters fans whose `rpm` is None or 0 (per R59/R60), but a header that becomes "controllable, no fan" mid-session needs the same treatment with a clearer reason.

**Fix.** Add a small helper `classify_fan_presence(fan, header) -> FanPresence` returning one of:

| Enum | Display | When |
|---|---|---|
| `PRESENT` | (no badge) | `rpm > 0`, OR `last_commanded_pwm > 0` and PWM-only (no `fan_input`) |
| `EMPTY_HEADER` | "no fan detected" (info chip) | `is_writable=True`, `rpm == 0`, and `fan_input` exists in sysfs (daemon reports `rpm_available=True`) |
| `READ_ONLY` | "read-only" (warn chip) | `is_writable=False` |
| `PWM_ONLY` | "PWM only — no RPM" (info chip) | `is_writable=True` and `fan_input` absent (daemon reports `rpm_available=False`) |

Use this helper from all three surfaces. The Diagnostics page has the existing `pwm_only` path (line 1083); merge with the new helper instead of duplicating logic.

Tooltip for `EMPTY_HEADER`: *"This header is controllable but the fan tachometer reads 0 RPM. Either no fan is plugged in, or the fan does not have a tachometer wire (a 3-pin DC fan on a 4-pin PWM header)."*

**Acceptance.**
- Test the helper directly against synthetic `(FanReading, HwmonHeader)` pairs covering all four states.
- Test Diagnostics → Fans: fixture with one `EMPTY_HEADER`, one `PRESENT`, one `READ_ONLY`, one `PWM_ONLY` → exact cell text + tooltip + chip class asserted.
- Test Controls fan-role member dialog: `EMPTY_HEADER` rows render with the badge; the user can still select them (assigning a curve to an empty header is valid — they may plug in later).
- Test Fan Wizard: when a previously-spinning fan transitions to `EMPTY_HEADER` (e.g. cable disconnected mid-test), surface this without aborting, with the new copy.
- No regression on dashboard (still uses `hide_unused_fan_headers` separately).

**Files.** New `src/control_ofc/ui/fan_presence.py` (or extend `fan_display.py`); update `diagnostics_page.py`, `fan_role_dialog.py`, fan-wizard pages; tests in `tests/ui/`.

---

### A3 — GUI: per-board hwmon fan label resolver (P2)

**Layer:** GUI (Python). **Approved Decision 2 — runtime libsensors config + small fallback. Approved Decision 4 — unverified suffix on guesses.**

**Problem.** The GUI shows raw sysfs labels (`pwm1..pwm5`, `pwm1..pwm3`) for two ITE chips on the X870E AORUS MASTER because the it87 driver does not expose `*_label` files for these boards. Users can't tell which `pwmN` corresponds to which silkscreen header.

**Fix.** Three-tier label resolution, evaluated top-down:

1. **User alias** (`app_settings.json::fan_aliases`) — existing top-priority. No change.
2. **Daemon-supplied sysfs label** (`HwmonHeader.label` from `pwmN_label`/`fanN_label`) — already wired in `pwm_discovery.rs:166-185`. No change.
3. **`/etc/sensors.d/*.conf` and `/usr/share/sensors/*.conf` chip-block label entries** — NEW. Parse with a minimal libsensors-syntax-aware reader (only the constructs we need: `chip "name-bus-addr"` blocks and `label fanN "..."` lines).
4. **In-repo fallback table** — NEW. Hardcoded `(vendor, board_name_glob, chip_name) -> {sensor_id: label}`. Seeds X870E AORUS MASTER's IT8696E (high confidence) and IT87952E (best-guess, marked unverified).

Priority order: 1 > 2 > 3 > 4. Resolve once on connect; refresh on `/hwmon/rescan` (already triggered by daemon endpoint when wired — out of scope here, see DIAGNOSTICS_REMEDIATION.md::P3.3).

**Parser scope (decision 2-B applies):**
- Recognise `chip "<glob>"` blocks. Glob match against the chip's bus + addr (e.g. `it8696-isa-*`, `it87952-isa-*`). Wildcards `?` `*` only — full libsensors glob is overkill.
- Inside a chip block, recognise `label <sensor> "<text>"` lines for `fanN`, `pwmN`, `tempN`, `inN`. Strip surrounding quotes, support escaped chars `\"` `\\` only.
- Recognise `ignore <sensor>` lines (skip sensor). Honour them by mapping to a sentinel `_IGNORED` so the GUI can hide the header if the user wants.
- Ignore everything else (`compute`, `set`, comments).
- Files read once at GUI start and after a daemon `/hwmon/rescan`. Read errors logged at WARN; never fatal.
- All 4 standard locations checked: `/etc/sensors.conf`, `/etc/sensors3.conf`, `/etc/sensors.d/*.conf`, `/usr/share/sensors/*.conf` (system installs land in `/usr/share/sensors/3.5/*.conf` on most distros — glob both).

**X870E AORUS MASTER fallback seed (decision 4-A — unverified suffix where applicable):**

```python
HWMON_LABEL_FALLBACK: dict[BoardKey, dict[str, FallbackLabel]] = {
    BoardKey(vendor="Gigabyte Technology Co., Ltd.",
             board_glob="X870E AORUS MASTER",
             chip="it8696"): {
        "pwm1": FallbackLabel("CPU_FAN", verified=True),
        "pwm2": FallbackLabel("SYS_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN2", verified=True),
        "pwm4": FallbackLabel("SYS_FAN3", verified=True),
        "pwm5": FallbackLabel("CPU_OPT", verified=True),
    },
    BoardKey(vendor="Gigabyte Technology Co., Ltd.",
             board_glob="X870E AORUS MASTER",
             chip="it87952"): {
        "pwm1": FallbackLabel("SYS_FAN4", verified=False),
        "pwm2": FallbackLabel("SYS_FAN5_PUMP", verified=False),
        "pwm3": FallbackLabel("SYS_FAN6_PUMP", verified=False),
    },
}
```

Where `verified=False`, the GUI suffixes the rendered label with `" (unverified)"` and tooltips read: *"Guessed from this board family's typical IT87952E layout. If you can confirm by physically tracing your fan cables, please file an issue with confirmation."*

User aliases override everything, including the `(unverified)` suffix.

**Acceptance.**
- Test the libsensors parser directly: synthetic config with chip blocks, label lines, ignore lines, comments, edge cases (escaped quotes, unicode, missing trailing newline) — assert returned dict structure.
- Test resolver priority: alias > sysfs label > /etc/sensors.d > fallback. Each level wins over the next when present.
- Test fallback table: `X870E AORUS MASTER` IT8696E `pwm1` → `CPU_FAN` (no unverified suffix). IT87952E `pwm1` → `SYS_FAN4 (unverified)`.
- Test alias override: aliasing `pwm1` to `Custom Name` wins over sysfs label and fallback alike.
- Test: missing `/etc/sensors.d` directory → graceful, no exceptions, falls through to in-repo table.
- Test: malformed `*.conf` → logged WARN, parser returns empty dict for that file, other files still parse.
- No regression on existing fan-display tests.

**Files.** New `src/control_ofc/ui/hwmon_label_resolver.py` (parser + fallback table + resolve function); update `fan_display.py` and any current consumers of `fan_display_name`; tests in `tests/ui/test_hwmon_label_resolver.py`.

**Out of scope.** A polished BoardKey glob matcher, full libsensors `compute` syntax, on-disk caching of parsed results, automatic config-file reload on inotify. All deferrable.

---

### B1 — Daemon: narrow `pwm_value_clamped` and `pwm_enable_reverted` wording (P1)

**Layer:** Daemon (Rust). **Approved Decision 1 — wording only, no profile-engine guard.**

**Problem.** Current details strings (`hwmon_ctl.rs:435-455`) name BIOS/EC as the only possible cause of register changes during the verify wait. After A1 ships, GUI-induced races are eliminated, but other API clients holding the lease and writing during verify, or thermal-safety overrides firing, can still produce a result-string that lies. Even on the user's machine, the original investigation was misled by exactly this lie.

**Fix.** Rename neither the `result` enum values nor the response shape — only the `details` field:

- `pwm_value_clamped` → first sentence unchanged (`PWM value changed from test {test_raw} to {final_raw}`); add a second sentence: `Most likely cause: BIOS/EC firmware reclaiming the PWM register. Less likely: another writer (lease holder, thermal-safety override) wrote to the same header during the 3s test window. To disambiguate, re-run with no profile active and no other client writing.`
- `pwm_enable_reverted` → first sentence unchanged; add a second sentence: `Most likely cause: BIOS/EC firmware reasserting automatic mode. Less likely: another writer flipped pwm_enable=2 during the test window.`

The `result` field stays exactly the same so `hwmon_guidance.verification_guidance` keys still match without GUI changes.

**Acceptance.**
- Unit test in `daemon/src/api/handlers/hwmon_ctl.rs`: drive `classify_verify_result` with synthetic `HwmonVerifyState` pairs that match each result. Assert exact two-sentence `details` strings.
- Compile-check: GUI `hwmon_guidance.verification_guidance` lookup still finds the right key after rebuild — verified by adjusting the existing GUI test fixture for the verify response payload.
- No new error-envelope variants, no HTTP status changes, no schema changes.

**Files.** `/home/mitch/Development/control-ofc-daemon/daemon/src/api/handlers/hwmon_ctl.rs` (only). Test in the same file's `mod tests`.

---

### C1 — Docs: Gigabyte X870E AORUS MASTER worked example (P3)

**Layer:** Docs.

**Add to** `docs/21_AMD_Motherboard_Fan_Control_Guide.md` under the Gigabyte → "Reported examples" section:

> **Gigabyte X870E AORUS MASTER (IT8696E + IT87952E):** PWM fan control
> works on `it87-dkms-git 332.20f2f2f+` and BIOS F13a (2026-03 onwards) with
> no kernel parameters and no BIOS flat-curve workaround required. Distinct
> from the X670E AORUS MASTER case — that one is IT8689E rev 1 with a
> manual-control limitation; this is IT8696E rev 0 (primary) plus IT87952E
> (secondary), both controllable. The board exposes 8 writable PWM headers
> total: 5 on IT8696E (CPU_FAN, SYS_FAN1, SYS_FAN2, SYS_FAN3, CPU_OPT) and
> 3 on IT87952E (likely SYS_FAN4, SYS_FAN5_PUMP, SYS_FAN6_PUMP — silkscreen
> mapping unverified). BIOS Smart Fan 6 reclaims `pwm_enable` on the
> CPU_FAN header at ~1 Hz; the daemon's `pwm_enable` watchdog (v1.3.0+)
> handles this transparently. To eliminate the reclaim entirely, set Smart
> Fan 6 to *Manual / Full Speed* for every header in BIOS. No upstream
> `lm_sensors` config exists for this board yet (as of 2026-04); the GUI
> ships a fallback label table.

Bump the doc's "Last updated" header. Cross-reference C2.

---

### C2 — Docs: list IT8696E and IT87952E in `19_Hardware_Compatibility.md` (P3)

**Layer:** Docs.

**Add rows to** the ITE table in `docs/19_Hardware_Compatibility.md`:

| Chip Series | Kernel Driver | Mainline | Package |
|---|---|---|---|
| IT8696E (rev 0+) | `it87` | **No** | `it87-dkms-git` (AUR) — frankcrawford fork |
| IT87952E | `it87` | **No** | `it87-dkms-git` (AUR) — secondary chip on dual-IO Gigabyte boards |

Bump "Last updated". Cross-reference C1.

---

## Items explicitly NOT shipping

- **Daemon profile-engine in-flight-verify guard.** Rejected per Approved Decision 1 — DEC-074 covers GUI-active case; remaining scenario is a niche "verify via API while no GUI connected and profile loaded" that nobody has reported. Reconsider only if a real user hits the race outside the GUI.
- **Stale `hidden_chart_series` prune.** Rejected per Corrections section — the entries the v1 plan called "phantom" are real chips on this board. Auto-prune would lose user preferences.
- **Custom in-house IT8696E driver / brute-force user-space writes.** Rejected — no datasheet from ITE; existing `it87-dkms-git` is open and working. See "Why we are NOT doing X" in v1 (preserved below for context).
- **Reverse-engineering Gigabyte Control Center / proprietary WMI.** Rejected — same physical IT8696E registers reachable via the public driver. Linux's path is at least as good as Windows third-party tools (which self-describe as "reverse engineered guess").
- **Adopting `amdgpu-sysfs` crate.** Rejected per DEC-043 (LGPL-3.0 license friction).
- **Hiding pwm2..pwm5 / IT87952E pwm1..pwm3 from the view.** Rejected — they are real, controllable, and may be populated by the user later. A2's `EMPTY_HEADER` classification is the correct UX.

---

## Why we are NOT doing X (preserved for context)

| Mechanism | Verdict | Reason |
|---|---|---|
| Custom in-house Linux driver for IT8696E | **Rejected** | ITE has not released the IT8696E datasheet (per `it87/source/README`). Any new driver would have to reverse-engineer the same registers `it87-dkms-git` already targets — duplicated effort, worse maintenance, no upside. There is no separate hidden fan-control chip on this board (DMI auto-match in `it87.c` matches `X870E AORUS MASTER` to the IT8696E + IT87952E combo, both bound). |
| Direct `/dev/port` or `/dev/mem` writes from user space | **Rejected** | The `it87` driver already uses both the LPC ISA window (0xa40) and the MMIO bridge (0xfe100000). Bypassing the driver adds concurrent-access risk for no benefit. |
| Kernel parameter `acpi_enforce_resources=lax` | **Rejected (for this board)** | Daemon `acpi_conflicts: []` on F13a — no conflict to resolve. |
| Module parameter `force_id=` | **Rejected** | Driver detects IT8696E correctly. Upstream marks this testing-only. |
| Module parameter `fix_pwm_polarity` | **Rejected** | README marks DANGEROUS. Not this board. |
| BIOS flat-curve workaround (40/40/40/40/40/40/100) | **Not needed for IT8696E** | Documented for IT8689E in upstream `TODOS`. Different chip. The watchdog handles the BIOS Smart Fan reclaim adequately; the user can additionally set Smart Fan 6 to Manual for every header to silence reclaims entirely. |
| Reverse-engineering Gigabyte Control Center | **Rejected** | Same physical registers reachable via public it87 driver. Even Windows third-party tools (FanControl + LibreHardwareMonitor) self-describe as "reverse engineered guess" of GCC behaviour and have repeatedly broken on X870 boards (LHM v228..v249 regressions). |
| `nct6687d`'s `msi_fan_brute_force=1` | **N/A** | Different vendor, different driver. |

---

## Suggested execution order

Ship as **three PRs** (one per repo + docs):

### Batch A — GUI (single PR, semver minor)
- **A1** Control loop pause for header under in-flight verify
- **A2** "Controllable, no fan detected" classification across Diagnostics, Controls, Fan Wizard
- **A3** Per-board hwmon label resolver (libsensors-config reader + in-repo fallback table)

Coordinate with `DIAGNOSTICS_REMEDIATION.md` if it lands in the same release window.

### Batch B — Daemon (single PR, semver patch)
- **B1** `pwm_value_clamped` / `pwm_enable_reverted` wording

### Batch C — Docs (single PR or rolled into Batch A)
- **C1** X870E AORUS MASTER worked example in `21_`
- **C2** IT8696E + IT87952E rows in `19_`

---

## Files the implementer will touch

### Batch A — GUI
**New:**
- `src/control_ofc/ui/hwmon_label_resolver.py` (libsensors parser + fallback table + resolver)
- `src/control_ofc/ui/fan_presence.py` (or extend `fan_display.py`) — `classify_fan_presence`
- `tests/ui/test_hwmon_label_resolver.py`
- `tests/ui/test_fan_presence.py`
- `tests/services/test_control_loop_pause.py` (or extend existing control-loop tests)

**Modified:**
- `src/control_ofc/services/control_loop.py` (pause/resume API)
- `src/control_ofc/ui/pages/diagnostics_page.py` (call pause/resume around verify; render `FanPresence`)
- `src/control_ofc/ui/widgets/fan_role_dialog.py` and the member-edit dialog (render `FanPresence` in member picker)
- Fan wizard pages — render `FanPresence` for transitions
- `src/control_ofc/ui/fan_display.py` (consume new resolver for default labels)
- `tests/ui/test_diagnostics_page.py`, `tests/ui/test_fan_role_dialog.py`, fan-wizard tests

**Must not touch:** `CLAUDE.md`, `DECISIONS.md`, `.claude/`.

### Batch B — Daemon
**Modified:**
- `daemon/src/api/handlers/hwmon_ctl.rs` (only)

**Must not touch:** anything else under the daemon tree, `CLAUDE.md`, `DECISIONS.md`, `.claude/`.

### Batch C — Docs
- `docs/21_AMD_Motherboard_Fan_Control_Guide.md`
- `docs/19_Hardware_Compatibility.md`
- `CHANGELOG.md` (under `Unreleased`)

---

## Testing policy (applies to every item)

- Every item ships with at least one new test asserting an outcome (state, signal emission, returned value), not just a click.
- Bug-fix items ship with a regression test that would have caught the original bug:
  - A1 — concurrent-write-during-verify test that fails on current code, passes after fix.
  - A2 — four-state classification fixture per surface.
  - A3 — parser unit tests; resolver priority test; fallback-table fixture.
  - B1 — wording assertion test (string match).
- Fixtures only: no live daemon, no real HTTP, no real sysfs, no flaky timing.
- Quality gates as listed in Constraints. Run before declaring done.
- Manual sanity checks before declaring each batch shipped:
  - **A1**: with the daemon running and a profile loaded, click "Test PWM Control" while the GUI control loop is active. Verify result must be `effective` or `rpm_unavailable`, never a false `pwm_value_clamped`.
  - **A2**: open Diagnostics on this X870E AORUS MASTER box. Confirm CPU_FAN row shows `PRESENT` (no badge), every other row shows `EMPTY_HEADER` with the info chip and the new tooltip.
  - **A3**: with no user aliases, every IT8696E header shows its silkscreen label (no suffix) and every IT87952E header shows label + `(unverified)`. Set an alias on one and confirm it overrides.
  - **B1**: verify response on a real BIOS-reclaim simulation still classifies as `pwm_enable_reverted`; new wording present.

---

## Version / changelog

- **Daemon Batch B** — semver patch (truthfulness wording fix). Changelog → `Fixed`.
- **GUI Batch A** — semver minor (A2 + A3 add visible UX surfaces). Changelog: `Fixed` for A1, `Changed`/`Added` for A2 + A3.
- **Docs Batch C** — no version bump; rides next release.
- DEC-NN entries (locally only, not committed):
  - DEC-NN(daemon): "Verify endpoint `details` strings acknowledge concurrent in-process writers; result enum unchanged."
  - DEC-NN(GUI): "Control loop yields hwmon writes for any header under in-flight verify, with 5 s safety auto-resume."
  - DEC-NN(GUI): "hwmon header label resolver: alias > daemon sysfs label > /etc/sensors.d > in-repo fallback. Unverified fallback labels carry the `(unverified)` suffix."

---

## Quick truthfulness sanity checks before merging

### Batch A
- [ ] Verify under load returns `effective`/`rpm_unavailable`, never false `pwm_value_clamped`.
- [ ] Diagnostics → Fans, Controls fan-role member picker, and Fan Wizard each show `EMPTY_HEADER` distinctly from `PRESENT` / `READ_ONLY` / `PWM_ONLY`.
- [ ] X870E AORUS MASTER displays IT8696E silkscreen labels (no suffix) and IT87952E labels with `(unverified)` suffix without any user aliases set.
- [ ] User-set alias overrides resolver output.
- [ ] No regression on dashboard fan display.

### Batch B
- [ ] `details` strings include the disambiguation second sentence for both clamped/reverted cases.
- [ ] GUI `hwmon_guidance.verification_guidance` still finds correct keys (no GUI release coordination required).

### Batch C
- [ ] `21_` distinguishes X670E (IT8689E rev 1) from X870E (IT8696E rev 0 + IT87952E).
- [ ] `19_` lists both IT8696E and IT87952E with `it87-dkms-git`.

---

## References

### Code locations (verified 2026-04-28)
- `daemon/src/api/handlers/hwmon_ctl.rs:307-487` — verify handler + classifier (B1 site).
- `daemon/src/hwmon/pwm_discovery.rs:166-185` — sysfs `*_label` reader (already correct, no change needed).
- `daemon/src/profile_engine.rs:349,393,466` — DEC-074 `gui_active` deferral (preserved as-is).
- `src/control_ofc/services/control_loop.py:518-562` — `_write_target` (A1 site).
- `src/control_ofc/ui/pages/diagnostics_page.py:267-310` — `_VerifyWorker` (A1 caller, A2 surface).
- `src/control_ofc/ui/pages/diagnostics_page.py:1027-1113` — `_on_fans` (A2 surface).
- `src/control_ofc/ui/widgets/fan_role_dialog.py` — fan-role member picker (A2 surface).
- `src/control_ofc/ui/fan_display.py` — `filter_displayable_fans` (A3 consumer).
- `src/control_ofc/ui/hwmon_guidance.py::verification_guidance` — consumes `result` enum (B1 contract).
- `src/control_ofc/services/app_settings_service.py:29,45` — `hidden_chart_series` (no change), `hide_unused_fan_headers` (no change).

### Project docs
- `CLAUDE.md` — non-negotiable rules.
- `DIAGNOSTICS_REMEDIATION.md` — sibling remediation; coordinate version-bump with this work.
- `docs/14_Risks_Gaps_and_Future_Work.md §11a` — AORUS BIOS Smart Fan reclaim story (already accurate; this work doesn't supersede it).
- `docs/19_Hardware_Compatibility.md` — chip table (C2 site).
- `docs/21_AMD_Motherboard_Fan_Control_Guide.md` — vendor guidance (C1 site).
- DEC-073, DEC-074 — daemon write-coordination decisions (already in place; A1 is the GUI counterpart).

### External
- frankcrawford/it87 driver source — DMI alias for `X870E AORUS MASTER` confirms IT8696E + IT87952E pairing.
- it87 issue #70 — X870E AORUS PRO missing fan headers (related but different SKU).
- it87 issue #96 — X670E AORUS MASTER IT8689E rev 1 (different chip; explicit non-overlap).
- LibreHardwareMonitor issue #1994 — X870E AORUS MASTER X3D ICE IT87952E mapping reference.
- nathan818fr gist — X570 AORUS MASTER lm_sensors config (IT8688 + IT8792 dual-chip pattern; basis for X870E IT87952E educated guess).
- lm_sensors `configs/Gigabyte/` — confirms no `GA-X870E-AORUS-MASTER.conf` upstream as of 2026-04.
