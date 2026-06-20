# Profiles and Curves Reference

This page explains how the three control layers — profiles, fan roles, and curves — work together to control your fans.

## The Control Model

```
Profile ("Quiet")
  |
  +-- Fan Role ("Case Intake")
  |     Mode: Curve
  |     Curve: "Case Fan Curve"
  |     Members: Front Intake 1, Front Intake 2, GPU Adjacent Intake
  |
  +-- Fan Role ("CPU Cooler")
  |     Mode: Curve
  |     Curve: "CPU Ramp"
  |     Members: CPU Fan
  |
  +-- Fan Role ("Pump / AIO")
        Mode: Curve
        Curve: "Pump Fixed"
        Members: CPU OPT / Pump

Curve Library:
  - "Case Fan Curve" (graph, 5 points, sensor: CPU Tctl)
  - "CPU Ramp" (linear, 35-80C -> 30-100%, sensor: CPU Tctl)
  - "Pump Fixed" (flat, 65%)
```

## Profiles

A profile is a named collection of fan roles and curves. You can have multiple profiles for different situations:

| Profile | Typical Use |
|---------|-------------|
| Quiet | Low noise for desktop work, gentle fan ramp |
| Balanced | Moderate noise, good cooling for mixed workloads |
| Performance | Maximum cooling for gaming or rendering, higher noise |

Only one profile can be **active** at a time. The daemon evaluates the active profile's fan roles every second and drives the fans.

### Built-in Profiles

Control-OFC ships with three default profiles (Quiet, Balanced, Performance) that are created on first launch if no profiles exist. These are fully editable — they are regular profiles, not special.

### Profile Storage

The daemon is the store of record for profiles — it holds the active profile and evaluates it. The GUI keeps a local draft cache of profiles as individual JSON files in your profiles directory (default: `~/.config/control-ofc/profiles/`) so you can author and edit them, including while disconnected; drafts reconcile with the daemon the next time the GUI connects.

## Fan Roles

A fan role groups physical fan outputs that should behave the same way. For example, if you have three front intake fans, you probably want them all to follow the same temperature curve.

### Modes

| Mode | Behaviour |
|------|-----------|
| **Curve** | Output is calculated from the assigned curve based on the current temperature reading |
| **Manual** | Output is a fixed percentage that you set with a slider |

### Members

Each fan role contains a list of physical fan outputs (members). A member can be:

- An OpenFan Controller channel (e.g., channels 0-9)
- A motherboard hwmon fan header (e.g., CPU Fan, chassis fans)
- An AMD GPU fan output

Each physical fan can only belong to one role. If you try to add a fan that is already assigned elsewhere, the member editor shows which role currently owns it.

### Tuning Parameters

Each fan role carries a set of tuning parameters that the daemon applies to the raw curve output before it is written to the hardware. These are stored in the profile and applied automatically — there is **no dedicated UI to hand-edit them**; they use sensible role-aware defaults (and **Minimum** in particular is set automatically from the role — see below).

| Parameter | Effect |
|-----------|--------|
| **Step up** | Maximum increase per cycle (limits how fast fans ramp up). Default: no limit |
| **Step down** | Maximum decrease per cycle (limits how fast fans ramp down). Default: no limit |
| **Start %** | Kickstart value when a fan resumes from 0% (helps overcome motor stiction). Default: 0% |
| **Stop %** | Below this output, snap to 0% (enables zero-RPM idle). Default: 0% |
| **Offset** | Fixed percentage added to the curve output. Default: 0% |
| **Minimum** | Hard floor — output never goes below this value |

#### Role-aware minimum (stall protection)

The **Minimum** floor is chosen automatically from the role inferred for the fan group, so chassis and CPU/pump fans don't stall, while GPU fans are free to idle at 0%:

- **30%** for CPU / pump-labelled hwmon members
- **20%** for chassis / OpenFan members
- **0%** for GPU members — and in a *mixed* group, the GPU member idles to its own 0% floor in the same cycle the chassis/CPU members hold their floor (the GPU's firmware owns its real ~15% minimum)

The daemon does **not** enforce these per-role floors itself; they are GUI-owned policy baked into the profile.

## Curves

Curves define the relationship between a temperature sensor reading and a fan output percentage.

### Graph (Freeform)

A multi-point curve where you define exactly which temperature maps to which output. Points are draggable on a visual graph and editable in a numeric table.

- Points must be ordered by temperature (left to right)
- Temperature range: 0-120 degrees C
- Output range: 0-100%
- Between points, values are linearly interpolated
- Below the first point, output equals the first point's value
- Above the last point, output equals the last point's value

### Stepped (Staircase)

The same point editor as Graph, but it **holds** each point's output until the next point's temperature is reached instead of ramping between points — a staircase. Each band `[this point, next point)` runs at the lower point's output; the step changes exactly at the next point's temperature.

Good for "run quiet up to 60C, then jump to a fixed louder speed" without a sloped ramp.

### Linear

A two-point interpolation defined by start and end values:

- Below start temperature: output stays at start percentage
- Above end temperature: output stays at end percentage
- Between: linearly interpolated

Good for simple "ramp from 30% at 35C to 100% at 80C" use cases.

### Flat

A constant output regardless of temperature. No sensor binding needed.

Good for pumps, AIO coolers, or fans that should run at a fixed speed.

### Trigger (Two-State Latch)

A two-speed curve with its own hysteresis band. Below the **idle temperature** it runs the idle speed; at/above the **load temperature** it runs the load speed; **within the band it holds** whichever state it is already in. This latch (idle must be below load) prevents the fan hunting back and forth at the boundary.

Good for "stay silent at an idle speed, then jump to a fixed load speed past X degrees, and don't flip-flop while cooling back down".

### Mix (Composite)

Combines the outputs of **other curves** — each evaluated at its own sensor — into one value with a function:

- **Max** — the loudest of its inputs (the classic "follow whichever of CPU/GPU is hotter")
- **Min** — the quietest of its inputs
- **Average** — the mean
- **Sum** — added together (clamped at 100%)
- **Subtract** — the first input minus the rest (clamped at 0%)

Edited in a dialog: pick the function and tick the curves to combine. A Mix has no sensor of its own — its inputs bring their own. The editor only offers curves that cannot form a loop.

### Sync (Composite)

Mirrors **another control's** output, plus an offset — for example "rear exhaust = CPU fans + 10%". Useful to keep a group of fans tracking another group without re-creating its curve. Edited in a dialog: pick the control to mirror and an offset. The editor only offers controls that cannot form a loop.

### Sensor Binding

Graph, Stepped, Linear, and Trigger curves are each bound to one temperature sensor that drives evaluation. Flat needs no sensor (constant output); **Mix and Sync also have no sensor of their own** — Mix reads each input curve at *its* sensor, and Sync mirrors a control's already-computed output. Common single-sensor bindings:

| Curve Purpose | Typical Sensor |
|---------------|---------------|
| Case fans | CPU temperature (since CPU is usually the hottest component) |
| CPU cooler | CPU temperature |
| GPU fans | GPU temperature |
| Radiator fans | CPU or GPU temperature, depending on what the radiator cools |

## The Control Loop

The **daemon** runs the control loop every second — the GUI never writes fan speeds. Each second the daemon performs this sequence:

1. Read the latest sensor temperatures
2. For each fan role in the active profile:
   - If mode is Manual: use the fixed output percentage
   - If mode is Curve: look up the curve, read the bound sensor's temperature, interpolate the output
3. Apply the **hysteresis deadband** (2 degrees C): when temperature is falling, hold the current PWM until the temperature drops 2 degrees below the last transition point (prevents fan oscillation)
4. Write the final PWM values to every fan backend (OpenFan, motherboard hwmon, and AMD GPU PMFW), **coalescing redundant writes**: for hwmon it skips the write when the new PWM is byte-identical to the last commanded value, and for AMD GPU PMFW it skips changes smaller than 5% (to avoid SMU firmware churn). This coalescing is entirely daemon-internal — the GUI itself never writes PWM.

## The Daemon Drives the Fans

Once you **Activate** a profile, the daemon owns fan control completely:

- The daemon's profile engine evaluates the active profile and writes every fan backend itself
- Your fans stay controlled whether the GUI is open, closed, or has crashed — there is nothing to keep running
- The GUI's job is to author and validate profiles, upload and activate them, poll the daemon once a second, and show you what is happening

This is why **Activate** is the moment that matters: it hands your profile to the daemon, which then keeps your fans managed headlessly.

> **Demo mode is the one exception.** With no daemon to talk to, the GUI animates fans with its own built-in evaluator so you can explore curves and profiles. Nothing is written to real hardware.

---

Previous: [Fan Wizard](fan-wizard.md) | Next: [Hardware Troubleshooting](hardware-troubleshooting.md)
