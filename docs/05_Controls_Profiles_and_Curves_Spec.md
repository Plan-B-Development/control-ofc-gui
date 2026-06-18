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
- a fan belongs to **at most one** group (the shipped UI calls these *fan roles*) — outputs already assigned elsewhere appear greyed out, so a fan is never owned by two roles
- show group badges consistently across the app

### Suggested built-in starter groups
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
- no live simulation required before apply

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

### Per-control minimum PWM (GUI-baked, daemon-enforced, role-aware — DEC-095/DEC-162)
The GUI **bakes** a role-aware default minimum PWM into each control's
`LogicalControl.minimum_pct` when members are assigned or edited; as of 2.0.0
the daemon then **enforces and backstops** that floor (DEC-162 — validate-time
reject + an independent eval-time clamp). The GUI-side defaults are:
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
**per member**. As of 2.0.0 the daemon applies this rule at write time
(DEC-119/DEC-162); the GUI mirrors it in `profile_service.member_minimum_pct`
for the floor badge, profile baking, and demo evaluation:
- **GPU members (`source == "amd_gpu"`)** are floored at **0%** — always,
  regardless of how the control is composed. The GPU's PMFW firmware owns
  its idle minimum (the OD_RANGE ~15% clamp; zero-RPM via the per-member
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
of 2.0.0), so a mixed-control GPU idles to 0%; the PMFW write path then clamps to the firmware
OD_RANGE (~15%) and honours the per-member `fan_zero_rpm` idle stop. The control card's
`Min: NN%` badge shows the non-GPU floor; its tooltip notes that GPU members
in a mixed control are not floored. The curve editor's lower bound is
unchanged (it still clamps to the strictest non-GPU floor for shared
curves), so the per-member GPU freedom is most visible in manual mode and
via `stop_pct`; for full low-end GPU control, keep the GPU fan in its own
control.

### Daemon thermal-emergency override (daemon-owned)
The daemon owns one absolute backstop independent of the GUI: at
≥105°C on the hottest CpuTemp sensor, all OpenFan channels and writable
hwmon headers are forced to 100% (see `daemon/src/safety.rs`, DEC-022).
This is non-editable and fires regardless of profile content. The 60%
recovery floor and 40% no-sensor fallback are likewise hardcoded.
GPU fans are deliberately excluded (DEC-130): there is no GPU emergency
threshold — AMD PMFW firmware owns GPU thermal protection independently
of OS fan control. While any override is active the daemon reports
`thermal_state != "normal"` in `GET /status`; the GUI has no loop to stand
down (DEC-165, superseding the DEC-132 GUI stand-down) and simply shows a
poll-driven thermal-protection banner.

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
Cards are **content-aware**, not a fixed pixel box (DEC-128):
- **Fixed width + minimum-height floor** — each card sets a fixed *width* (so
  the flow grid forms aligned columns) and a *minimum* height (no maximum), so
  a card grows taller to fit scaled text rather than clipping its rows. The
  previous fixed 220×160px box clipped rows once the theme font grew.
- **Theme-scaled** — width and minimum height are derived from the theme's
  `base_font_size_pt` (7–16) by `card_metrics.card_dimensions()`, so cards
  honour the current text size automatically.
- **Density tier** — a `card_size` preference (compact / comfortable / large,
  default comfortable; Settings → Themes) multiplies the computed size. Live
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
- **Clamping** — width ≥ `MIN_USER_CARD_WIDTH_PX` (220); height ≥ the card
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

### Section layout (Fan Roles / Curves)
The two sections share a vertical `QSplitter` (`Controls_Splitter_sections`)
configured with **equal stretch factors and equal seeded sizes**, so the split
defaults to ~50/50 and stays proportional as the window resizes, while the
divider remains user-draggable (DEC-128, D3). The inner curves/editor splitter
(`Controls_Splitter_curvesEditor`) is unchanged.

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

## Hwmon control implications
The daemon owns the hwmon lease internally (the GUI holds no lease as of 2.0.0 — DEC-165). If the active profile includes hwmon-controlled headers:
- reflect the daemon's reported control/health state for those headers in the Controls page
- show whether the daemon reports the header as writable (a header the daemon cannot drive — e.g. a read-only RDNA3+ `pwm1`, DEC-102 — must not look actively controlled)
- do not allow the user to think a profile is actively controlling something the daemon cannot drive

## Suggested key workflows

### Workflow: switch profile
1. User selects a different profile
2. App shows whether there are unsaved edits
3. App activates the selected profile on the daemon (`POST /profile/activate`)
4. The daemon begins evaluating that profile (the GUI does not write PWM)
5. Dashboard and header update on the next poll

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

### Composite curves (Mix/Sync, DEC-152)
Mix and Sync intentionally depend on other curves/controls — but the dependency is **explicit and by id**, never silent shared state. A Mix owns its `mix_function` + `mix_curve_ids`; a Sync owns its `sync_control_id` + `sync_offset_pct`. Two Mix curves referencing the same child do not couple — editing one's function or input list never changes the other. The dependencies form a DAG: cycles are prevented at author time (the editor offers only cycle-free choices) and guarded at eval time (safe fallback). Composite card previews are self-contained — a Mix shows `Max of N curves`, a Sync shows `Mirror control +N%`, derived from the curve's own fields without resolving other curves'/controls' names (so the preview can never go stale against data the card was not given).

### Card metadata typography (R33)
Card metadata labels (members, curve assignment, output, sensor, used-by, RPM) use the `.CardMeta` CSS class, which inherits the `small` font role (`base * 0.9`). This keeps metadata visually subordinate to body text. Card titles (`_name_label`) inherit the global body font size. The `.PageSubtitle` class is reserved for section headers and page-level subtitles, not card internals.

Fan Role buttons receive modest padding via `.Card QPushButton { padding: 4px 8px; }` to accommodate larger theme text sizes without clipping.

### Editor sensor isolation (R32)
When the curve editor opens a curve, it must restore that curve's own saved `sensor_id` in the sensor combo. The editor uses `blockSignals(True)` during programmatic population to prevent signal-driven writeback. Switching between curves always loads the selected curve's own sensor — never the previous curve's residue.

### Theme text size
The Controls page inherits text sizes from the global theme stylesheet via CSS classes (`.PageTitle`, `.PageSubtitle`, `.Card`). No hardcoded `font-size: Xpx` overrides exist on the Controls page. Changing the theme text size changes Controls page text consistently.
