# 05 — Controls, Profiles, and Curves Spec

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Purpose
This page is the operational heart of the app. It owns:
- profile switching
- profile editing
- fan groups
- fan-to-group assignment
- per-profile curve definition
- manual override
- active control clarity

## Why Controls is the right name
This page contains more than saved profile selection. It includes:
- editing
- grouping
- assignment
- overrides
- sensor selection
- curve management

Therefore `Controls` is the top-level navigation label, with profile management inside it.

## Page layout
The current layout (DEC-214/233) is described under
[Implementation: Controls Page Layout](#implementation-controls-page-layout-dec-214233) below.

> **V1 plan — history, superseded by DEC-214.** The V1 spec planned three zones: a left
> column with the profile list, New/Duplicate/Rename/Delete and active-vs-edited indicators;
> a centre with the curve graph, sensor selector, target scope and point table; and a right
> column with the fan-group editor, membership editor, manual-override controls and
> Apply/Save/Reset/Revert. Selecting and activating a profile moved to the sidebar, and the
> page became the three panes below.

## Profile behaviour rules
- Only one profile is active at a time
- It must be obvious which profile is active
- It must be obvious when the user is editing a profile that is not currently active
- Unsaved edits must be visually obvious
- Switching active profile with unsaved changes must trigger a clear choice
- On a Dell whose `dell_smm` driver has one BIOS switch for every fan, a profile must
  control all of those fans or none (DEC-403, `TS-bb`). The switch is shared: taking the
  first fan turns the BIOS off for all of them, and giving it back turns it on for all of
  them. `ProfileService.save_profile` refuses a profile that names some but not all of
  them, before anything is written, and activation saves first, so it refuses too. The
  shared kind is recognised from `/hwmon/headers` alone: its `pwm1` has an enable file
  and some other writable fan has none. A per-fan `dell_smm` machine is unaffected. A
  profile saved earlier, or imported, is flagged on the Controls page until it is fixed.
  The tray and the daemon's boot activation do not pass through this rule; they start
  only what was saved.

## Default built-in profiles
Three starter profiles are created on first launch when no profiles exist
(`profile_service.default_profiles`):
- Quiet
- Balanced
- Performance

Each ships **one "All Fans" control with no members, and a curve with no sensor**, so
activating a starter as shipped **controls no fan** — the GUI cannot know which headers a
machine has or which sensor to follow. They are templates: understandable and editable,
and safe because they drive nothing until the user assigns fans and picks a sensor. The
daemon's packaged example, `/etc/control-ofc/profiles/quiet.json`, is the same: its one
control has an empty `members` list.

## Fan groups
Groups are flexible user labels, not rigid system types.

### Group capabilities
- create group
- rename group
- delete group
- assign multiple fans to a group
- a fan belongs to **at most one** group (the shipped UI calls these *fan roles*) — outputs already assigned elsewhere appear greyed out, so a fan is never owned by two roles
- show group badges consistently across the app

### Suggested starter groups (not shipped)
None of these ships — the starter profiles carry only their empty *All Fans* group. They are
examples of labels a user might create.
- Intake
- Exhaust
- CPU
- Radiator
- Case

## Curve model
Curve rules:
- a **simple** curve reads one sensor; **composite** curves (Mix, Sync) span
  several by depending on *named* curves/controls — explicitly and acyclically
  (DEC-152, retiring the old single-sensor rule DEC-014)
- edit in % output
- 5 points by default (point-based curves)
- temperature on X-axis
- fan output percentage on Y-axis
- no simulation before apply; **Test Curve** shows the curve's output at the sensor's
  current temperature in the editor

### Curve types
The curve library supports seven shapes, each serialised with a `type` field:
- **graph** — piecewise-linear interpolation between user points
- **stepped** — staircase: holds each point's output until the next point's
  temperature is reached (lower-point-wins, half-open segments), no
  interpolation (DEC-148, schema v5)
- **linear** — a single 2-point ramp (start/end temperature → output)
- **flat** — a constant output, temperature-independent
- **trigger** — a two-state latch: below the idle temperature it runs the idle
  speed, at/above the load temperature it runs the load speed, and within the
  band it holds its current state (its own hysteresis, DEC-149, schema v6)
- **mix** — combines the outputs of other curves (each at its own sensor) with a
  function — `max` / `min` / `average` / `sum` / `subtract` — clamped 0–100
  (DEC-150, schema v7)
- **sync** — mirrors another *control's* tuned output, plus an offset, resolved
  same-tick via stable topological control ordering (DEC-151, schema v7)

Graph and Stepped share the point-table editor (same points model, different
fill rule — straight vs staircase); Linear, Flat, and Trigger use a small
parameter panel; Mix and Sync use a modal dialog (a function + a checkable curve
list / a control + offset) with no sensor selector — they compose other curves
or controls instead of reading a sensor directly.

**Composite curves are explicit and acyclic.** Mix references other curves by id;
Sync references a control by id. A dependency cycle is prohibited — the editor
offers only cycle-free choices, and both evaluators guard a cycle at eval time
(falling back to a safe value so the fan holds). Mix and Sync bypass the 2°C
falling-temperature deadband (Mix is multi-sensor; Sync mirrors an
already-resolved value); smoothing comes from the control's step-rate limit.

A profile that uses a curve type an older build doesn't recognise degrades
safely (the GUI falls back to flat; the daemon to 50%).

**GPU compatibility.** Every curve type is supported on AMD GPU fans. The daemon
collapses whatever a curve produces into a single output percentage per cycle
and writes it as a flat PMFW curve, so the GPU never depends on the curve
*shape* — graph, stepped, linear, and flat behave identically on a GPU fan
(subject to the firmware's own 5%/OD-RANGE clamp).

### Curve editor behaviour
- points are draggable, editable in the table, and nudged from the keyboard
- **+ Add Point** adds one at the midpoint of the curve; **Remove Point** (or Delete /
  Backspace) removes the selected one
- **Undo / Redo** (Ctrl+Z / Ctrl+Shift+Z), 50 steps
- a **Preset** menu loads Linear, Quiet or Aggressive into a graph or stepped curve
- X values stay ordered and outputs clamped: the lower bound is the strictest floor of
  the fan roles using the curve (see Safety below)
- edits update the table and graph together
- **Revert** in the page header returns the whole profile to its last saved state; it is
  not a factory reset

### Point rules
- a new curve has 5 points
- a curve keeps at least **2** points; points stay at least 0.5 °C apart across 0–120 °C
- a stored curve with more than 256 points is refused on load (`MAX_CURVE_POINTS`)
- impossible point ordering and out-of-range outputs are prevented

## Sensor selector
Each curve chooses exactly one sensor from the supported V1 categories:
- CPU
- Motherboard
- GPU (AMD only)
- Liquid
- Ambient
- Disk

**Control-eligibility filter (DEC-193):** the sensor dropdown drops any sensor
the daemon marks `control_eligible: false` (API-derived from
`is_wireless_phy_chip(chip_name)` — e.g. an `ath12k`/`iwlwifi` WiFi PHY temp).
Offering one would strand the curve the moment the radio goes down. This is
advisory and mirrors the DEC-102 member-picker drop; a curve already bound to
such a sensor keeps working and still shows its live value. Older daemons omit
the field → the GUI defaults `control_eligible = true` (nothing filtered).

If a previously selected sensor disappears:
- show the broken association clearly
- keep the profile editable
- allow the user to select a replacement sensor

## Scope of profile application
A profile applies to the whole cooling system.
However, within a profile, fan targets may still be organised by:
- individual fan
- group
- source category

This should be modelled carefully so V1 stays usable.

## Profile model
A profile holds a list of **fan roles** (`LogicalControl`, each with its members, a mode,
a curve id and its tuning) and a library of **curves** (`CurveConfig`, each owning its
sensor and points). A role references a curve by id; several roles may share one.

> **V1 plan — history.** V1 proposed storing, per target, a target id, a target type
> (fan/group), a sensor id, a curve id or inline curve, and an enabled flag. The shipped
> model moved the sensor into the curve and the targets into roles, as above.

## Manual override
Manual override is temporary and high-visibility.

### Manual override lifecycle (DEC-163)
- **Enter / exit**: a single checkable **Manual** toggle per role card
  (`ControlCard_Btn_manual_*`). Checking it swaps the card's output line for an inline
  slider and takes a daemon override (`POST /control/{id}/override`); unchecking it
  releases the override (`DELETE`) and the role returns to its curve.
- **Renewal**: the page renews every few seconds (the interval the daemon returns in
  `renew_secs`). A renew the daemon refuses means the override has expired, and the card
  reverts to its curve.
- **What ends one without the user**: the daemon's deadman when renewals stop (a frozen or
  closed GUI — the page does not release overrides on close), and activating or
  deactivating a profile (DEC-189/DEC-218). While the daemon is unreachable a renew fails,
  which the page treats as expiry, so the card reverts.
- **Another client's override** shows on the card as an **External** chip (DEC-169); the
  page clears those chips, and every **Not controlled** chip, on a disconnect, because
  nothing would refresh them while polling is stopped.
- **What the daemon does meanwhile**: it commands the slider's duty for that role only,
  skipping curve evaluation for it (an overridden role is never listed as skipped); every
  other role keeps running its curve, and the thermal ladder still floors the override
  (DEC-307).
- **Floors**: the daemon clamps the requested duty up to the role's floor. The slider's
  own minimum is the label-derived floor (see Safety), not the live pump-role term.

## Safety behaviour

### Per-control minimum PWM (GUI-baked, daemon-enforced, role-aware — DEC-095/DEC-162)
The GUI **bakes** a role-aware default minimum PWM into each control's
`LogicalControl.minimum_pct` when members are assigned or edited; as of 2.0.0
the daemon then **enforces and backstops** that floor (DEC-162 — validate-time
reject + an independent eval-time clamp). The GUI-side defaults are:
- **30%** for any control with a hwmon member that is any of:
  - labelled CPU or pump — its `member_label` contains `CPU`, `PUMP` or `AIO`;
  - labelled CPU or pump **by the daemon** — the label embedded in the member's stable id
    (`hwmon:chip:device:pwmN:LABEL`), which catches a renamed `PUMP` header (DEC-257,
    matching the daemon's DEC-252 union);
  - on a **liquid-cooler chip** (NZXT Kraken, Aquacomputer — the chip in the id, from
    the shared cooler list), so a pump labelled only `pwm1` is still covered (DEC-156);
  - **assigned the `pump` role by the user** (DEC-312). The assignment is unioned into the
  persisted `member_label` at authoring time, because a persisted member carries
  no live header to consult later. Union only — a `chassis_fan` assignment on a
  `PUMP`-labelled header does not strip the floor the label already earned — and
  the tag is deliberately not removed when the role is later cleared, since
  clearing an assignment is not a request to lower a floor. Note the daemon
  reaches the same 30% independently via `assigned_role_earns_hard_floor`, so this is
  what the GUI *displays*, not what protects the hardware.
  **Only Configure AIO bakes the tag** (`aio_member_for_header`); the member
  picker and quick-assign do not, and a member authored before its header was
  assigned `pump` predates the tag. So since DEC-417 the **Min badge** and the
  **Dashboard fan-card state** also union the role in *live* at display time
  (`floor_role_header_roles` — `HwmonHeader.role == "pump"`, the same predicate the
  tag uses, and since `ROLE-a` `"cpu_fan"` where the daemon advertises
  `control.cpu_fan_role_floor`, whose assignment it floors at 30% too), without
  touching the profile. That mirror follows the role both
  ways, as the daemon's assignment term does — straight away for a role the GUI
  writes (it re-reads the headers after its own role writes), and at the next
  300 s header re-read for one written outside it. Three things deliberately do NOT
  follow it, by the user's choice: the control card's **manual slider** minimum
  and the **curve editor's** lower bound keep the label-derived floor (the daemon
  clamps a request between the two up to 30%), and `minimum_pct` is not
  re-stamped. A role raises only its own member — unlike a pump *label*, which
  raises the whole control's `minimum_pct` — so in a mixed control the badge
  shows 30% and its tooltip says the figure covers the pump-assigned member,
  giving the other fans the control's own `minimum_pct` (the daemon's number
  for them).
- **20%** for chassis / OpenFan-only controls. An OpenFan channel assigned `pump` (`ROLE-f`), or
  `cpu_fan` where the daemon advertises `control.cpu_fan_role_floor` (`ROLE-a`), is held at
  30% by the daemon on the role alone; the Min badge and fan cards show that (the role term
  above), the stamped `minimum_pct` does not.
- **0%** for GPU-only controls (the card has its own PMFW `OD_RANGE`
  minimum — board-specific, often around 15%; the kernel rejects fan-curve
  points below it and the daemon clamps to it; see DEC-053).

The role floor is a **default**, not a ceiling — users can raise
`minimum_pct` further via the controls page, and the GUI never
silently lowers an explicit user-set value. **One floor is re-applied on
every load (DEC-423):** a control with a pump/CPU member is raised to 30%
if it is below it, because the daemon's `validate()` rejects it otherwise
(`FLOOR_TOO_LOW`) and activation saves first. A control can become pump/CPU
after its profile is stored — a chip joins the cooler list, or a header's
label comes to name a pump. The chassis 20% default is **not** re-imposed on
load: a user may keep a chassis control below it, and the daemon enforces
no chassis floor. The curve editor's drag,
table edit, keyboard nudge, and Linear/Flat spinbox lower bound all
clamp to the strictest floor across controls referencing the curve,
so a curve shared by a chassis control (20%) and a CPU control (30%)
cannot be edited below 30%. The Controls page surfaces the effective
floor via a `Min: NN%` badge on each role card.

Profile schema v4 (introduced with GUI v1.10.0 / daemon v1.6.0)
migrates v3-or-older profiles on load: any control whose members
include a CPU/PUMP header gets `minimum_pct ← max(minimum_pct, 30)`;
chassis-only controls are raised to 20%.

#### Per-member flooring — GPU members are never floored (DEC-119)
`minimum_pct` is a single control-wide value, but the floor is applied
**per member**. As of 2.0.0 the daemon applies this rule at write time
(DEC-119/DEC-162); the GUI mirrors it in `profile_service.member_minimum_pct`,
whose one production caller is the Dashboard fan-card state (the Min badge is
control-level, `controls_view.min_pwm_badge`). Since DEC-417 it also takes the
header pump-role term above, as a required argument:
- **GPU members (`source == "amd_gpu"`)** are floored at **0%** — always,
  regardless of how the control is composed. The GPU's PMFW firmware owns
  its idle minimum (its OD_RANGE minimum, board-specific and often ~15%; zero-RPM via the per-member
  `fan_zero_rpm` toggle), so a GUI floor would be redundant and would stop
  the fan from idling.
- **Non-GPU members** honour the control-wide `minimum_pct` exactly as
  before (it is already the strictest role floor across the control's
  members), so no non-GPU behaviour changes.

This matters only for a **mixed control** — a GPU fan grouped with
chassis/CPU fans. There, the daemon engine writes the GPU member down to its
0% floor in the **same cycle** that the chassis/CPU members hold their 20% /
30% floor, each tracking an independent step-rate trajectory. A GPU-only
control was already at 0% (its `minimum_pct` is 0). The **daemon profile
engine applies this per-member rule on every tick** (it is the sole writer as
of 2.0.0), so a mixed-control GPU idles to 0%; the daemon's PMFW write path then clamps to the
card's OD_RANGE minimum (board-specific, often ~15% — the kernel rejects a lower curve point) and
honours the per-member `fan_zero_rpm` idle stop. The control card's
`Min: NN%` badge shows the non-GPU floor; its tooltip notes that GPU members
in a mixed control are not floored. The curve editor's lower bound is
unchanged (it still clamps to the strictest non-GPU floor for shared
curves), so the per-member GPU freedom is most visible in manual mode and
via `stop_pct`; for full low-end GPU control, keep the GPU fan in its own
control — the fastest way to do that is **Dedicate GPU Fan** (below).

#### Dedicate GPU Fan — one-click 0-RPM idle (DEC-221)
A shared curve can't be authored below its chassis member's floor, and a bare
0% curve value alone still spins at the card's OD_RANGE minimum (often ~15%): the kernel
rejects a point below it, so the daemon clamps up to it. To make a
writable AMD GPU fan idle at **true 0 RPM** when the GPU is cool, the Controls
page offers a one-click **Dedicate GPU Fan** action (shown only when a profile can
drive the GPU fan — `AmdGpuCapability.profile_writable`: present, `fan_write_supported`
and `fan_control_method: "pmfw_curve"`, DEC-445 — and the daemon reports
`gpu_zero_rpm_available`).
It:
- pulls the GPU fan out of any control that currently drives it (no double-writer);
- creates a **GPU-only** `LogicalControl` (role floor 0%, so its curve is
  authorable all the way down to 0%) bound to a GPU temperature sensor;
- seeds a default curve that idles to 0% up to 45 °C then ramps
  (20 %/40 %/60 %/100 % at 47/58/75/95 °C); and
- sets the member's `fan_zero_rpm = True` — the actual lever for a firmware
  idle-stop (a 0% curve value alone is clamped up to the OD_RANGE minimum).

The pure builder is `profile_service.build_gpu_control`; the dialog
(`GpuDedicateDialog`) is a thin UI over it. Zero-RPM is a firmware feature that
only stops the fan when the GPU is genuinely cool, and the daemon restores
automatic zero-RPM control on shutdown (DEC-053) — GPU thermal protection stays
owned by PMFW (DEC-130).

### Daemon thermal-emergency override (daemon-owned)
The daemon owns one absolute backstop independent of the GUI: at or above
the trip point on the hottest CpuTemp sensor, every OpenFan channel and writable
hwmon header the machine has is driven to 100% (see `daemon/src/safety.rs`, DEC-022).
This is non-editable and fires regardless of profile content. Since DEC-443 a
coolant sensor at or above the coolant limit (Settings, 40–70 °C, default 60) takes
the same 100 % force; see `docs/09`. **The trip point
is per-machine (DEC-308)** — at least 105°C, raised to `min(ceiling + 5 °C, 115 °C)` where the kernel publishes the
CPU's own design ceiling — and `/diagnostics/hardware`
reports the value in use, so a client renders it rather than assuming 105. The 40%
no-sensor fallback is likewise non-editable (the 60% recovery rung was removed in
DEC-386: release hands control straight back). **Both are floors over the active
profile's output, not replacements for it (DEC-307)**: each fan receives
`max(commanded, forced)`. Only the 100% emergency also reaches fans no control
commands; the 40% floor applies to the fans the profile controls, and every other fan
the emergency took is given back when it ends
(DEC-382).
GPU fans are deliberately excluded (DEC-130): there is no GPU emergency
threshold — AMD PMFW firmware owns GPU thermal protection independently
of OS fan control. While any override is active the daemon reports
`thermal_state != "normal"` in `GET /status`; the GUI has no loop to stand
down (DEC-165, superseding the DEC-132 GUI stand-down) and simply shows a
poll-driven thermal-protection banner.

It is a **backstop above the CPU's own throttle point, not a cooling-failure
detector** (`TS-m`): a CPU holds itself at its ceiling by throttling, so a stopped
pump or stalled fans produce a CPU pinned at that ceiling, not an emergency.

Per-header safety floors are **not** enforced by the daemon's hwmon
controller (`min_pwm_percent: 0` for every header). But the **role-aware
pump/CPU floor is now daemon-enforced** (DEC-162): the daemon clamps a
pump/CPU member to ≥30% on every eval tick regardless of the profile's
declared `minimum_pct`, so hand-edited profile JSON or a third-party client
can no longer strand a pump/CPU below its safety minimum. The GUI still
**bakes** the floor and owns the rest of curve safety policy; the daemon owns
thermal emergency and the floor backstop (CLAUDE.md, DEC-022, DEC-095,
DEC-162). The project's threat model treats local writers as trusted
(DEC-049).

### Per-GPU zero-RPM idle (per-member toggle)
Each `amd_gpu` member of a control carries a `fan_zero_rpm` boolean
(default false). The daemon honours the flag when programming the
PMFW curve (DEC-095): true → preserve `fan_zero_rpm_enable`, false →
disable it before writing the curve so the fan spins continuously.
Surfaced as an "Allow zero-RPM idle" checkbox per GPU member in the
Edit Fan Role dialog.

---

## Implementation: Controls Page Layout (DEC-214/233)

### Page structure
```
ControlsPage (QVBoxLayout)
├── Header: "Controls" │ the profile being edited · ⋮ (New / Rename / Duplicate / Delete)
│           ……… Set up ▾ · Revert · Save · unsaved-changes chip
├── Dell shared-switch banner (DEC-403; only when the profile breaks the rule)
└── QSplitter (Horizontal, Controls_Splitter_sections) — width ratio 1 : 1 : 2
    ├── 1  Assign Roles — "+" (single-output or group role) · role cards in a
    │      DraggableFlowContainer · "Unassigned Fans (N)" pinned at the bottom
    └── QSplitter (Horizontal, Controls_Splitter_curvesEditor)
        ├── 2  Link Logic — "+" (new curve of any type) · curve cards
        └── 3  Curve Editor — "Editing: <curve>" · Test Curve · Close (Esc);
               always mounted, with a placeholder until a graph or stepped curve is
               opened (composite and parameter curves edit in a dialog / panel)
```

- **Profile selection and activation live in the sidebar** (DEC-208/214): its combo
  chooses which profile this page edits, and its **Apply** activates it. **Stop**
  beside it deactivates (DEC-462); a deactivation, from there or anywhere else, leaves
  this page on the profile it was showing, unsaved edits included. The header
  names the profile being edited so it is always clear what **Save** writes.
- **Save** (Ctrl+S) validates and uploads the profile to the daemon's store; saving the
  active profile re-applies it (DEC-188). **Revert** is enabled only while there are
  unsaved edits.
- **Set up ▾** holds the hardware-setup actions: **Auto-Connect Wizard…** (identify and
  label fans, DEC-166), **Configure AIO…** (name the pump header — the path that assigns the
  `pump` role — choose how the pump is driven, and group the radiator fans; shown when a
  liquid cooler or pump header is detected) and **Dedicate GPU Fan…** (DEC-221; shown when
  the GPU supports it).
- **Unassigned Fans (N)** lists the fans no role controls. A writable one can be added to
  an existing role from its submenu, or it says to create a role first; a read-only one is
  listed as such. It reads "All fans assigned" when there are none (DEC-233).

### Role cards
A role card shows the role name and fan count, its members with live RPM, the assigned
curve, a `Min: NN%` floor badge, the output (or the inline manual slider), and
**Manual · Delete · Edit…**. Two chips can appear on it:
- **Not controlled** (`Not controlled · 4m`) when the daemon lists the role in
  `skipped_controls` (273-i, daemon ≥ 2.21.0). The tooltip gives the reason in words —
  its curve is missing, its sensor is unavailable, none of a Mix's inputs could be read,
  the role a Sync mirrors is not running, or (daemon 2.55.0+) none of its fans can be
  controlled by the daemon — and says the fans hold their last speed, or, for the last
  case, that their speed is up to the hardware. An unrecognised reason still shows the
  chip. An overridden role is never listed.
- the Dell shared-switch warning above, for the profile as a whole.

A member row carries an amber **HEADER MISSING** pill when the member is a hwmon header the
daemon does not report (DEC-461, `BRD-v`): the DEC-102 startup sweep no longer deletes such a
member, so the card says it is idle. Motherboard members only; nothing is judged before the
first header list arrives, against an empty one, or in demo. The comparison is on canonical ids
(DEC-442), as the sweep's is. The pill follows `headers_updated` and `mode_changed`. The member
name beside it is an `ElidedLabel`: on a compact or narrowed card a long name ends in `…` and
carries its full text as the tooltip, rather than being cut off mid-glyph.

### Member picker drops
The Edit Fan Role dialog offers only fans a role can drive:
- a hwmon header the daemon reports `is_writable: false` is **not listed** (DEC-102) — it
  stays visible on the hardware surfaces. The startup sweep also drops a profile member on
  such a header; a member whose header is **absent** is kept, and shown in Selected Members
  as `(header missing)` (DEC-461);
- Intel and NVIDIA GPU fans are not listed (no kernel write path, DEC-121/DEC-204);
- an AMD GPU fan no profile can drive is listed, **disabled**, with its reason
  (DEC-445): `(read-only)` for an RX 7000/9000 without its PMFW `fan_curve` (fixable,
  `amdgpu.ppfeaturemask`), `(verify only)` for an RX 6000 or older, whose legacy `pwm1`
  only the GPU fan verify and reset write. Its tooltip says why. The gate is
  `AmdGpuCapability.profile_writable`, which also reads the method, because daemons
  before DEC-445 report a legacy card `fan_write_supported: true`;
- a fan already in another role is shown disabled;
- a sensor the daemon marks `control_eligible: false` is dropped from the curve's sensor
  picker (DEC-193, above).

### Card container: DraggableFlowContainer
Both Fan Roles and Curves sections use `DraggableFlowContainer`, which provides:
- **FlowLayout** — responsive wrapping (adapts to window width, tiles left-to-right)
- **Drag-to-reorder** — event filter detects mouse drag, QDrag handles the operation
- **Drop indicator** — thin vertical bar shows where the card will land
- **order_changed signal** — emits list of card IDs in new order after a drop
- **Snap-back** — cards dropped outside the valid area return to original position

### Card sizing
Cards are **content-aware**, not a fixed pixel box (DEC-128):
- **Fixed width + minimum-height floor** — each card sets a fixed *width* (so
  the flow grid forms aligned columns) and a *minimum* height (no maximum), so
  a card grows taller to fit scaled text rather than clipping its rows. The
  previous fixed 220×160px box clipped rows once the theme font grew.
- **Theme-scaled** — width and minimum height are derived from the theme's
  `base_font_size_pt` (7–16) by `card_metrics.card_dimensions()`, so cards
  honour the current text size automatically.
- **Density tier** — a `card_size` preference (compact / comfortable / large,
  default comfortable; the Theme page) multiplies the computed size. Live
  cards re-size when the theme/font or tier changes
  (`ControlsPage.set_theme` → `Card.apply_card_size`).
- **Curve cards**: header, sensor, preview, footer.
- **Fan Role cards**: name, members, curve, output, actions (same width + floor).
- **Tight rows** — row spacing 2px, vertical margins 4px; surplus card height
  pools in a stretch above the Fan Role action row (Curve cards give it to
  the preview), so rows never read as double-spaced (DEC-129).

### Per-card user resize (DEC-129)
Every card has a bottom-right **resize grip** (`ui/widgets/card_resize.py`):
- **Drag** resizes that card live; sizes snap to an **absolute lattice**
  (multiples of `card_metrics.SNAP_STEP_PX = 20`) so nearby sizes land on
  exactly the same value — the affordance that makes equal-sized cards easy.
- **Clamping** — width ≥ `MIN_USER_CARD_WIDTH_PX` (200, *derived* from the
  narrowest width the tier system itself ships — do not restate it as a
  literal here, which is how this line came to say 220); height ≥ the card
  layout's `minimumSize()` rounded up to the lattice, so a shrink can never
  clip rows. With an override the card is fixed in *both* dimensions;
  without one, DEC-128 fixed-width + min-height semantics apply unchanged.
- **Reset** — double-click the grip to restore the theme-derived size.
- **Persistence** — `AppSettings.controls_card_sizes` (`id → [w, h]`),
  re-applied on grid rebuilds; theme/tier changes and content growth
  *re-clamp* an override but never clear it; saved sizes are pruned of ids
  that no longer exist in any known profile.
- **Gesture isolation** — the grip consumes its own mouse events, so a
  resize drag can never start the container's reorder drag (its event
  filter watches the card only) or the card's click-to-select.

### Curve preview (owner-drawn, DEC-129)
`CurvePreview` paints the graph polyline (or the linear/flat text summary)
in `paintEvent` with a **constant font-derived size hint** (~3 text lines)
and an Expanding policy. The previous QLabel+QPixmap preview re-rendered at
its own size in `resizeEvent`, and a QLabel's hint is its pixmap — a
render→hint→grant→render ratchet that inflated graph cards into ~570px
towers. With the owner-drawn widget the default card shows a modest
sparkline, and extra height granted by a user resize grows the graph
intentionally.

### Section layout (the three panes)
The outer `Controls_Splitter_sections` holds Assign Roles and a second horizontal
splitter, `Controls_Splitter_curvesEditor`, which holds Link Logic and the Curve Editor;
the net width ratio is 1 : 1 : 2 and every divider is user-draggable. The two card panes'
minimum width follows the card metric and is re-derived when the font or density changes
(DEC-260). (Before DEC-214 this was a vertical split between a Fan Roles pane and a
Curves pane.)

### Order model
- **Source of truth**: `Profile.curves` and `Profile.controls` lists
- **Drag reorder syncs model from layout**: `_on_curves_reordered()` / `_on_controls_reordered()` reconstruct the list from layout order
- **Refresh rebuilds layout from model**: `_refresh_curves_grid()` / `_refresh_controls_grid()` clear all cards and re-add from model list
- **New cards append to end**: `profile.curves.append()` / `profile.controls.append()`
- **Order persists via profile save**: JSON serialization preserves list order

### Layout invalidation
`FlowLayout.addItem()` and `FlowLayout.takeAt()` both call `self.invalidate()` to trigger Qt's asynchronous layout recalculation. Without this, cards stack at position (0,0).

### Widget lifecycle
`clear_cards()` blocks signals, removes event filters, orphans widgets, and calls `deleteLater()` for deterministic Qt-side cleanup. Python references are cleared separately via `_control_cards.clear()` / `_curve_cards.clear()`.

### Profile activation
There is no Activate button on this page. The sidebar's **Apply** and the Dashboard's
**Apply** both call `ProfileService.activate`, which:
1. Saves the profile — validates it and uploads it to the daemon's store, as Save does
2. Calls `POST /profile/activate` with the path of the profile's local copy
3. Updates local state (AppState, the sidebar) only after the daemon confirms
4. Otherwise reports the failure without marking the profile active

The sidebar's **Stop** (DEC-462) is the reverse: `ProfileService.deactivate` calls
`POST /profile/deactivate` and clears the active id only after the daemon confirms. It has
no confirmation dialog (stopping is undone by Apply); a 10 s info banner says what the fans
do now, and a failure shows `Could not stop profile control: <reason>`. It is enabled while
either the GUI's active id or AppState's daemon-reported name is set, so it also stops a
profile the daemon runs that this GUI does not hold, and its tooltip names the profile
(DEC-470, `WUI-a`). Deleting the active profile deactivates through the same method first;
if that fails and the daemon then refuses the delete (`409 profile_in_use`), the profile is
kept, AppState goes on naming it, and the banner says `Could not delete '<name>': the daemon
is still running it (<reason>). Press Stop, then delete.` (`WUI-c`).

A failure is shown in the main window's banner as `Could not activate "<name>": <reason>`
(DEC-416, `CTRL-k`); a later successful Apply takes it down. Step 1 can refuse (DEC-403): a
profile that breaks the Dell shared-switch rule above is not saved, the daemon is never
asked, and the banner shows the rule's own message, which names the fans to change.

When a user tries to create an unsafe curve:
- clamp or validate before save
- explain why the value was changed or rejected
- do not silently accept an invalid curve

## Hwmon control implications
The daemon owns the hwmon lease internally (the GUI holds no lease as of 2.0.0 — DEC-165). If the active profile includes hwmon-controlled headers:
- reflect the daemon's reported control/health state for those headers in the Controls page
- show whether the daemon reports the header as writable (a header the daemon cannot drive — e.g. a read-only RDNA3+ `pwm1`, DEC-102 — must not look actively controlled)
- do not allow the user to think a profile is actively controlling something the daemon cannot drive

## Suggested key workflows

### Workflow: switch profile
1. User selects a different profile in the sidebar (or on the Dashboard)
2. App shows whether there are unsaved edits
3. User clicks **Apply**; the app saves it and activates it on the daemon (`POST /profile/activate`)
4. The daemon begins evaluating that profile (the GUI does not write PWM), and clears any
   standing manual overrides (DEC-189)
5. Dashboard and status strip update on the next poll

### Workflow: edit curve
1. User selects profile
2. User selects target
3. User selects sensor
4. User edits 5-point curve
5. User saves (saving the active profile re-applies it to the daemon — DEC-188 — so the edit takes effect immediately)
6. User optionally activates the profile if not already active

### Workflow: create group
1. User creates group label
2. User selects one or more fans
3. Group badges update
4. Group becomes available as a filter and assignment target

## Nice-to-have later
- multi-sensor logic
- curve smoothing helpers
- advanced hysteresis tuning (per-curve deadband configuration)
- profile schedules
- workload-aware automation
- import/export of individual profiles

---

## Per-curve data ownership (R31)

### Sensor reference
Each `CurveConfig` owns its own `sensor_id` field. This is a string referencing a sensor entity (e.g., `"cpu_temp"`). Multiple curves may reference the same sensor — this does not create coupling. Editing one curve's sensor never changes another curve's sensor.

### Graph data
Each `CurveConfig` owns its own `points: list[CurvePoint]`. Points are stored per-curve, serialized per-curve, and loaded per-curve. Two curves referencing the same sensor have completely independent point sets.

### Preview truthfulness
When a curve is edited via the embedded editor, the corresponding curve card's mini-preview must update to reflect the curve's current graph shape. The preview is derived from the curve's own `points` data — not cached separately, not sourced from another curve, not left stale.

### Composite curves (Mix/Sync, DEC-152)
Mix and Sync intentionally depend on other curves/controls — but the dependency is **explicit and by id**, never silent shared state. A Mix owns its `mix_function` + `mix_curve_ids`; a Sync owns its `sync_control_id` + `sync_offset_pct`. Two Mix curves referencing the same child do not couple — editing one's function or input list never changes the other. The dependencies form a DAG: cycles are prevented at author time (the editor offers only cycle-free choices) and guarded at eval time (safe fallback). Composite card previews are self-contained — a Mix shows `Max of N curves`, a Sync shows `Mirror control +N%`, derived from the curve's own fields without resolving other curves'/controls' names (so the preview can never go stale against data the card was not given).

### Card metadata typography (R33)
Card metadata labels (members, curve assignment, output, sensor, used-by, RPM) use the `.CardMeta` CSS class, which inherits the `small` font role (`base * 0.9`). This keeps metadata visually subordinate to body text. Card titles (`_name_label`) inherit the global body font size. The `.PageSubtitle` class is reserved for section headers and page-level subtitles, not card internals.

Fan Role buttons receive modest padding via `.Card QPushButton { padding: 4px 8px; }` to accommodate larger theme text sizes without clipping.

### Editor sensor isolation (R32)
When the curve editor opens a curve, it must restore that curve's own saved `sensor_id` in the sensor combo. The editor uses `blockSignals(True)` during programmatic population to prevent signal-driven writeback. Switching between curves always loads the selected curve's own sensor — never the previous curve's residue.

### Theme text size
The Controls page inherits text sizes from the global theme stylesheet via CSS classes (`.PageTitle`, `.PageSubtitle`, `.Card`). No hardcoded `font-size: Xpx` overrides exist on the Controls page. Changing the theme text size changes Controls page text consistently.
