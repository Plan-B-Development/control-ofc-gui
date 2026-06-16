# Controls

The Controls page is the operational heart of the application. It manages **profiles**, **fan roles**, and **curves** — the three layers that determine how your fans respond to temperature.

![Controls Page](../screenshots/auto/02_controls.png)

## How It Works

The control model has three layers:

1. **Profile** — A named collection of fan roles and curves. You switch between profiles (e.g., "Quiet" for nighttime, "Performance" for gaming).
2. **Fan Role** — A logical group of physical fans that share the same behaviour (e.g., "Case Intake" groups your three front fans together).
3. **Curve** — A temperature-to-speed mapping that defines how fast fans should spin at any given temperature.

A profile contains one or more fan roles, and each fan role references a curve from the profile's curve library. The [Profiles and Curves Reference](profiles-and-curves.md) explains the model in depth.

## Profile Bar

The top bar manages profiles:

| Control | What it does |
|---------|-------------|
| **Profile dropdown** | Selects which profile to view and edit. The active profile is marked with `*` |
| **Activate** | Saves the selected profile, then makes it the active one — the control loop starts evaluating it, and the daemon is told so it can take over headlessly later |
| **Save** | Writes the profile's changes to disk (`Ctrl+S`) |
| **Manage Profiles…** | Menu with **New Profile**, **Rename Profile**, **Duplicate Profile**, and **Delete Profile** |

Next to Save, a status chip shows **"Unsaved changes"** whenever you have modified the profile without saving, and confirms afterwards with "Settings saved" / "Profile activated". Activation failures are shown in red ("Activation failed: …") — the GUI never falsely marks a profile active.

Deleting a profile asks for confirmation and cannot be undone; deleting the currently active profile deactivates it on the daemon first.

## Fan Roles (Top Section)

The section header has **Fan Wizard** (identify and label your physical fans — see [Fan Wizard](fan-wizard.md)), **Configure AIO** (shown only when a liquid cooler is detected — see [Configuring an AIO](#configuring-an-aio--liquid-cooler)), and **+ Fan Role**, which offers two kinds of role:

- **Single Output Fan Role** — one physical output
- **Group Fan Role (Multi-Fan)** — several outputs acting together

Each fan role appears as a card:

| Card element | Meaning |
|--------------|---------|
| **Title row** | Role name plus a status chip: **Applied** (curve output being written), **Manual** (inline override active), or **No members** |
| **Members** | The physical outputs in the role — "Members: Front Intake 1, Front Intake 2, +1 more" |
| **Curve** | The assigned curve and its type, or "Curve: Manual" for fixed-speed roles. A **Min: N%** badge appears when a stall-protection floor applies (see [role-aware minimums](profiles-and-curves.md#role-aware-minimum-stall-protection)) |
| **Now** | Live output and the driving sensor: "Now: 65% • Tctl 45.0°C". Mixed roles with a GPU member also show the GPU's own value ("(GPU 0%)") when it idles below the rest |
| **Bottom row** | Measured RPM on the left; **Manual**, **Delete**, and **Edit…** buttons on the right |

### Configuring an AIO / liquid cooler

When a liquid cooler (e.g. an NZXT Kraken or an Aquacomputer pump) is detected, a **Configure AIO** button appears in the Fan Roles header. It sets your cooler up in one step:

- A **pump** control at a **constant speed** — choose Low (30%), Mid (60%), High (80%, the default), or Max (100%). A pump runs best at a steady speed rather than a temperature curve, so these are fixed levels with a 30% minimum floor.
- A **radiator-fan** control bound to a temperature sensor — the **coolant** sensor by default (recommended, since the radiator's job is to cool the loop), though any sensor is selectable and coolant/CPU are highlighted as preferred.

A read-only / monitor-only cooler (one whose pump the kernel cannot drive, such as an older NZXT Kraken2) skips the pump step and offers radiator + coolant monitoring only — it never offers control that would fail. The controls it creates are ordinary fan roles you can edit afterward.

### Inline Manual Override

The **Manual** button on each card is a toggle: switch it on and a slider replaces the output line, pinning that role's fans to a fixed speed. This is a *temporary* override:

- it pauses curve evaluation for **that role only** — every other card keeps following its curve
- it is **not saved** to the profile
- it clears when you toggle it off or switch profiles

Use it for quick experiments ("what does 80% sound like?") without touching the saved profile. To make a role *permanently* fixed-speed, set its mode to Manual in the Edit dialog instead.

### Editing a Fan Role

Click **Edit…** to open the role dialog:

![Fan Role Dialog — Curve Mode](../screenshots/auto/12_fan_role_dialog_curve.png)

| Field | Description |
|-------|-------------|
| **Name** | Human-readable label ("Case Intake", "CPU Cooler") |
| **Mode** | **Curve** (automatic, temperature-driven) or **Manual** (fixed speed, stored in the profile) |
| **Curve** | Which curve to follow (Curve mode only) |
| **Manual Output** | Fixed percentage with slider and spinbox (Manual mode only) |
| **Members** | Read-only summary, with an **Edit Members** button |

![Fan Role Dialog — Manual Mode](../screenshots/auto/13_fan_role_dialog_manual.png)

When the role contains an AMD GPU fan, a **GPU fan idle behaviour** section appears with a per-GPU **Allow zero-RPM idle** checkbox: leave it checked to let the GPU's firmware stop the fan at idle (it spins up with the curve), or uncheck it so the fan tracks the curve continuously.

### Managing Members

**Edit Members** shows two lists — available outputs and selected members — with **>** / **<** buttons to move fans between them. Entries are tagged by source (`[openfan]`, `[hwmon]`, `[amd_gpu]`).

Each physical fan can belong to **only one role**: outputs already assigned elsewhere appear greyed out with "(Assigned to: …)" so you can see which role owns them. Read-only GPU fans are marked "(read-only)".

### Arranging and Resizing Cards

- **Drag a card** to reorder it within its section (a drop indicator shows the insertion point); the order is saved with the profile.
- **Drag the grip** in a card's bottom-right corner to resize it — sizes snap to a shared 20px grid, so making several cards exactly the same size is easy. **Double-click the grip** to reset the card to its theme-derived size. Per-card sizes persist across restarts and profile switches.
- The baseline card size follows the theme font size and the **Card size** preference (Compact / Comfortable / Large) in [Settings → Themes](settings.md#cards).

## Curves (Bottom Section)

The curve library lives in the lower half (the divider between the two sections is draggable). **+ Curve** offers the seven curve types:

| Type | Description | Use case |
|------|-------------|----------|
| **Graph Curve** | Multiple draggable points defining a custom temperature-to-speed shape | Full control over the response |
| **Stepped Curve** | The same draggable points as a graph, but the output *holds* each point's value until the next point's temperature is reached — a staircase, not a ramp | A fixed fan speed per temperature band, with fewer speed changes |
| **Linear Curve** | Two-point ramp: start temp/speed to end temp/speed | Simple "ramp up between X and Y" |
| **Flat Curve** | Constant output regardless of temperature | Pumps, AIO coolers, always-on fans |
| **Trigger Curve** | A two-state latch: an idle speed below the idle temperature, a load speed above the load temperature, holding its state in between (its own hysteresis) | "Stay quiet, then ramp hard past X°" |
| **Mix Curve** | Combines several *other* curves — each evaluated at its own sensor — into one output using a function: **Max**, **Min**, **Average**, **Sum**, or **Subtract** (result clamped 0–100%). Has no sensor of its own | "Drive this fan from whichever of CPU/GPU/VRM is hottest" |
| **Sync Curve** | Mirrors another fan role's current output, plus an optional offset (−100…+100%). Has no sensor of its own | "Keep the rear fans a few percent above the front fans" |

Mix and Sync are *composite* curves: they reference other curves (Mix) or another fan role (Sync) **by name**, and the editor only offers choices that cannot form a loop, so a composite can never depend on itself (DEC-150/151/152).

Each curve card shows the curve's name and type, the bound sensor with its live reading (composites show no sensor), a preview, and which roles use it ("Used by: …" with an **Assigned** / **Unassigned** chip). The preview is a sparkline for graph curves, a staircase for stepped curves, and otherwise a short summary — for example "35°C→80°C: 30%→100%", "Flat: 65%", "Idle 30% <40° / Load 80% >60°", "Max of 3 curves" (Mix), or "Mirror control +5%" (Sync). The card's **Actions** menu has **Edit**, **Rename**, **Duplicate**, and **Delete**.

### Editing a Graph or Stepped Curve

**Actions → Edit** on a graph or stepped curve opens the inline editor below the curve grid (a stepped curve uses the same point editor — only its preview line renders as a staircase):

- **Drag points** on the graph, or type exact values in the numeric table beside it. **Double-click** empty graph space (or click **+ Add Point**) to add a point; **Remove Point** or the `Delete` key removes the selected one (a curve keeps at least 2 points)
- The **sensor selector** chooses which temperature drives the curve; a live readout shows the current evaluation ("45.0°C → 62%")
- **Presets** (Linear, Quiet, Aggressive) load a starting shape you can refine
- **Undo / redo** with `Ctrl+Z` / `Ctrl+Shift+Z`
- The valid range is 0–120°C and 0–100% output; if a role using this curve has a stall-protection minimum, the editor stops you dragging points below that floor
- Click **Close Editor** when done — edits update the card preview immediately and mark the profile unsaved

### Editing a Linear, Flat, Trigger, Mix, or Sync Curve

These open a small parameter dialog instead:

![Curve Edit Dialog](../screenshots/auto/14_curve_edit_dialog.png)

- **Linear** — name, a sensor, and start/end temperature and output values.
- **Flat** — just a name and an output percentage (no sensor needed).
- **Trigger** — a sensor plus idle/load temperatures and speeds (the idle temperature must be below the load temperature).
- **Mix** — a combine **function** (Max / Min / Average / Sum / Subtract) and a checklist of **curves to combine**. No sensor: each combined curve evaluates at its own sensor and the results are merged. Only curves that would not create a cycle are offered; if none exist the list reads "No other curves available to combine."
- **Sync** — the fan role to **mirror** and an **offset** percentage (−100…+100). No sensor: the output tracks the chosen role's current value plus the offset. Only roles that would not create a cycle are offered.

## Empty States

A new profile shows "No fan roles configured. Click + Fan Role to create one." The Curves section stays hidden until at least one fan role exists — curves are always assigned *to* roles, so the page walks you through creating a role first.

---

Previous: [Dashboard](dashboard.md) | Next: [Settings](settings.md)
