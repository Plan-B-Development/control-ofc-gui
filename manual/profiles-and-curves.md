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

Only one profile can be **active** at a time. The active profile's fan roles are evaluated by the control loop every second.

### Built-in Profiles

Control-OFC ships with three default profiles (Quiet, Balanced, Performance) that are created on first launch if no profiles exist. These are fully editable — they are regular profiles, not special.

### Profile Storage

Profiles are saved as individual JSON files in your profiles directory (default: `~/.config/control-ofc/profiles/`). They are GUI-owned — the daemon does not manage profiles, but it can load them for headless operation when the GUI is not running.

## Fan Roles

A fan role groups physical fan outputs that should behave the same way. For example, if you have three front intake fans, you probably want them all to follow the same temperature curve.

### Modes

| Mode | Behaviour |
|------|-----------|
| **Curve** | Output is calculated from the assigned curve based on the current temperature reading |
| **Manual** | Output is a fixed percentage that you set with a slider |

### Members

Each fan role contains a list of physical fan outputs (members). A member can be:

- An OpenFan Controller channel (e.g., channels 0-7)
- A motherboard hwmon fan header (e.g., CPU Fan, chassis fans)
- An AMD GPU fan output

Each physical fan can only belong to one role. If you try to add a fan that is already assigned elsewhere, the member editor shows which role currently owns it.

### Tuning Parameters

Fan roles support advanced tuning that modifies the curve output:

| Parameter | Description |
|-----------|-------------|
| **Step up** | Maximum increase per second (limits how fast fans ramp up) |
| **Step down** | Maximum decrease per second (limits how fast fans ramp down) |
| **Start %** | Kickstart value when a fan resumes from 0% (helps overcome motor stiction) |
| **Stop %** | Below this output, snap to 0% (enables zero-RPM idle) |
| **Offset** | Fixed percentage added to the curve output |
| **Minimum** | Hard floor — output never goes below this value |

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

### Linear

A two-point interpolation defined by start and end values:

- Below start temperature: output stays at start percentage
- Above end temperature: output stays at end percentage
- Between: linearly interpolated

Good for simple "ramp from 30% at 35C to 100% at 80C" use cases.

### Flat

A constant output regardless of temperature. No sensor binding needed.

Good for pumps, AIO coolers, or fans that should run at a fixed speed.

### Sensor Binding

Each curve (except Flat) is bound to a temperature sensor. The sensor determines which temperature reading drives the curve evaluation. Common bindings:

| Curve Purpose | Typical Sensor |
|---------------|---------------|
| Case fans | CPU temperature (since CPU is usually the hottest component) |
| CPU cooler | CPU temperature |
| GPU fans | GPU temperature |
| Radiator fans | CPU or GPU temperature, depending on what the radiator cools |

## The Control Loop

The control loop runs every second and performs this sequence:

1. Read the latest sensor temperatures from the daemon
2. For each fan role in the active profile:
   - If mode is Manual: use the fixed output percentage
   - If mode is Curve: look up the curve, read the bound sensor's temperature, interpolate the output
3. Apply the **hysteresis deadband** (2 degrees C): when temperature is falling, hold the current PWM until the temperature drops 2 degrees below the last transition point (prevents fan oscillation)
4. Apply **write suppression**: skip writes when the calculated output differs from the last commanded value by less than 1% (reduces unnecessary hardware writes)
5. Write the final PWM values to the daemon

## GUI vs Daemon Priority

Both the GUI and the daemon have profile evaluation capability:

- When the GUI is running and has an active profile, **the GUI drives fan speeds**
- When the GUI closes, the daemon can take over using the last activated profile
- The daemon defers to the GUI if it has been active within the last 30 seconds

This means you can close the GUI and your fans continue to be managed by the daemon headlessly.

---

Previous: [Fan Wizard](fan-wizard.md) | Back to [Table of Contents](README.md)
