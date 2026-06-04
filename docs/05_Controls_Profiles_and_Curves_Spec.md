# 05 — Controls, Profiles, and Curves Spec

**Last updated:** 2026-05-07 (Spec doc — updated infrequently; refer to DECISIONS.md and CHANGELOG.md for current behaviour.)

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

## V1 page zones

### Left column / upper-left area
- Active profile selector
- Profile list
- New / Duplicate / Rename / Delete profile actions
- Profile status indicators
- Active vs edited profile indicators

### Center main area
- Curve editor graph
- Sensor selector
- Target selector context (fan / group / profile target scope)
- Numeric point table

### Right column / lower section
- Fan groups editor
- Group membership editor
- Manual override controls
- Apply / Save / Reset / Revert actions

## Profile behaviour rules
- Only one profile is active at a time
- It must be obvious which profile is active
- It must be obvious when the user is editing a profile that is not currently active
- Unsaved edits must be visually obvious
- Switching active profile with unsaved changes must trigger a clear choice

## Default built-in profiles
Provide initial starter profiles:
- Quiet
- Balanced
- Performance

These do not need to be perfect hardware-tuned profiles. They need to be:
- safe
- understandable
- immediately usable
- editable by the user

## Fan groups
Groups are flexible user labels, not rigid system types.

### Group capabilities
- create group
- rename group
- delete group
- assign multiple fans to a group
- allow a fan to belong to multiple groups
- show group badges consistently across the app

### Suggested built-in starter groups
- Intake
- Exhaust
- CPU
- Radiator
- Case

## Curve model
V1 curve rules:
- single sensor only
- edit in % output
- 5 points by default
- temperature on X-axis
- fan output percentage on Y-axis
- no live simulation required before apply

### Curve editor behaviour
- points are draggable
- points are editable numerically
- X values must remain ordered
- values must remain clamped to safe ranges
- edits update the numeric table and graph together
- reset returns to last saved profile state, not necessarily to factory preset

### Point rules
- default new curve has exactly 5 points
- point count can remain fixed in V1 unless additional complexity is easy
- prevent impossible point ordering
- prevent values outside valid output range

## Sensor selector
Each curve chooses exactly one sensor from the supported V1 categories:
- CPU
- Motherboard
- GPU (AMD only)
- Liquid
- Ambient
- Disk

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

## Recommended V1 simplification
Use this model:

### Profile contains control assignments
For each controllable target, the profile stores:
- target id
- target type (fan/group)
- selected sensor id
- curve id or inline curve
- enabled flag

This keeps V1 flexible without implying complex daemon-native policy support.

## Manual override
Manual override is temporary and high-visibility.

### Manual override requirements
- obvious enable action
- obvious exit action labeled **Return to Automatic**
- visible page-wide state when active
- profile engine pauses or yields while manual override is active
- override writes still go through daemon safety rules

### Manual override UI
Recommended:
- a distinct banner or chip
- per-target override sliders if implemented
- global quick override only if it is clearly explained
- manual override panel separated from profile editing to avoid confusion

## Safety behaviour

### Per-control minimum PWM (GUI-owned, role-aware — DEC-095)
The GUI enforces a role-aware default minimum PWM by setting
`LogicalControl.minimum_pct` when members are assigned or edited:
- **30%** for any control whose members include a CPU- or pump-labelled
  hwmon header (label contains `CPU`, `PUMP`, or `AIO`).
- **20%** for chassis / OpenFan-only controls.
- **0%** for GPU-only controls (PMFW enforces its own OD_RANGE
  minimum, typically 15%; see DEC-053).

The role floor is a **default**, not a ceiling — users can raise
`minimum_pct` further via the controls page, and the GUI never
silently lowers an explicit user-set value. The curve editor's drag,
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
**per member** at write time via `profile_service.member_minimum_pct`:
- **GPU members (`source == "amd_gpu"`)** are floored at **0%** — always,
  regardless of how the control is composed. The GPU's PMFW firmware owns
  its idle minimum (the OD_RANGE ~15% clamp; zero-RPM via the per-member
  `fan_zero_rpm` toggle), so a GUI floor would be redundant and would stop
  the fan from idling.
- **Non-GPU members** honour the control-wide `minimum_pct` exactly as
  before (it is already the strictest role floor across the control's
  members), so no non-GPU behaviour changes.

This matters only for a **mixed control** — a GPU fan grouped with
chassis/CPU fans. There, the control loop writes the GPU member down to its
0% floor in the **same cycle** that the chassis/CPU members hold their 20% /
30% floor, each tracking an independent step-rate trajectory. A GPU-only
control was already at 0% (its `minimum_pct` is 0). The **daemon profile
engine applies the identical per-member rule** when a profile runs headless,
so a mixed-control GPU idles to 0% whether or not the GUI is connected
(DEC-096 consistency); the PMFW write path then clamps to the firmware
OD_RANGE (~15%) and honours the per-member `fan_zero_rpm` idle stop. The control card's
`Min: NN%` badge shows the non-GPU floor; its tooltip notes that GPU members
in a mixed control are not floored. The curve editor's lower bound is
unchanged (it still clamps to the strictest non-GPU floor for shared
curves), so the per-member GPU freedom is most visible in manual mode and
via `stop_pct`; for full low-end GPU control, keep the GPU fan in its own
control.

### Daemon thermal-emergency override (daemon-owned)
The daemon owns one absolute backstop independent of the GUI: at
≥105°C on the hottest CpuTemp sensor, all fans are forced to 100%
(see `daemon/src/safety.rs`, DEC-022). This is non-editable and
fires regardless of profile content. The 60% recovery floor and
40% no-sensor fallback are likewise hardcoded.

Per-header safety floors are **not** enforced by the daemon — the
hwmon controller treats `min_pwm_percent: 0` for every header. The
"GUI owns curve safety policy, daemon owns thermal emergency" split
is intentional (CLAUDE.md, DEC-022, DEC-095). Hand-edited profile
JSON or third-party clients that bypass the GUI's role-aware floor
can still command low PWM; the project's threat model treats local
writers as trusted (DEC-049).

### Per-GPU zero-RPM idle (per-member toggle)
Each `amd_gpu` member of a control carries a `fan_zero_rpm` boolean
(default false). The daemon honours the flag when programming the
PMFW curve (DEC-095): true → preserve `fan_zero_rpm_enable`, false →
disable it before writing the curve so the fan spins continuously.
Surfaced as an "Allow zero-RPM idle" checkbox per GPU member in the
Edit Fan Role dialog.

---

## Implementation: Controls Page Layout (v0.27.0)

### Page structure
```
ControlsPage (QVBoxLayout)
├── Profile bar (combo, activate, save, manage)
├── QSplitter (Vertical) — user-draggable divider
│   ├── Top pane: Fan Roles
│   │   ├── Header + Fan Wizard + Add button
│   │   └── QScrollArea → DraggableFlowContainer (fan role cards)
│   └── Bottom pane: Curves
│       ├── Header + Add button
│       ├── QScrollArea → DraggableFlowContainer (curve cards)
│       └── CurveEditor (expandable, hidden by default)
└── No-controls hint (shown when no fan roles exist)
```

### Card container: DraggableFlowContainer
Both Fan Roles and Curves sections use `DraggableFlowContainer`, which provides:
- **FlowLayout** — responsive wrapping (adapts to window width, tiles left-to-right)
- **Drag-to-reorder** — event filter detects mouse drag, QDrag handles the operation
- **Drop indicator** — thin vertical bar shows where the card will land
- **order_changed signal** — emits list of card IDs in new order after a drop
- **Snap-back** — cards dropped outside the valid area return to original position

### Card sizing
- **Curve cards**: fixed 220×160px — header, sensor, preview, footer
- **Fan Role cards**: fixed 220×160px (unified sizing) — name, members, curve, output, actions

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
When the user clicks Activate, the GUI:
1. Saves the profile to disk
2. Calls `POST /profile/activate` on the daemon with the profile file path
3. Only updates local state (AppState, combo) after daemon confirms success
4. Shows error feedback on failure without falsely marking the profile active

When a user tries to create an unsafe curve:
- clamp or validate before save
- explain why the value was changed or rejected
- do not silently accept an invalid curve

## Hwmon lease implications
If the active target includes hwmon-controlled headers:
- show lease state in the Controls page
- show whether writes are currently possible
- do not allow the user to think a profile is actively controlling something when the lease is unavailable

## Suggested key workflows

### Workflow: switch profile
1. User selects a different profile
2. App shows whether there are unsaved edits
3. App applies the selected profile
4. Control loop begins using that profile
5. Dashboard and header update immediately

### Workflow: edit curve
1. User selects profile
2. User selects target
3. User selects sensor
4. User edits 5-point curve
5. User saves
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

### Card metadata typography (R33)
Card metadata labels (members, curve assignment, output, sensor, used-by, RPM) use the `.CardMeta` CSS class, which inherits the `small` font role (`base * 0.9`). This keeps metadata visually subordinate to body text. Card titles (`_name_label`) inherit the global body font size. The `.PageSubtitle` class is reserved for section headers and page-level subtitles, not card internals.

Fan Role buttons receive modest padding via `.Card QPushButton { padding: 4px 8px; }` to accommodate larger theme text sizes without clipping.

### Editor sensor isolation (R32)
When the curve editor opens a curve, it must restore that curve's own saved `sensor_id` in the sensor combo. The editor uses `blockSignals(True)` during programmatic population to prevent signal-driven writeback. Switching between curves always loads the selected curve's own sensor — never the previous curve's residue.

### Theme text size
The Controls page inherits text sizes from the global theme stylesheet via CSS classes (`.PageTitle`, `.PageSubtitle`, `.Card`). No hardcoded `font-size: Xpx` overrides exist on the Controls page. Changing the theme text size changes Controls page text consistently.
