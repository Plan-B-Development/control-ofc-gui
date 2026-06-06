# Fan Wizard

The Fan Configuration Wizard helps you identify and label your fans. It stops each fan one at a time so you can observe which physical fan changed, then lets you assign a meaningful name.

![Fan Wizard — Intro](../screenshots/auto/15_fan_wizard_intro.png)

## Why Use the Wizard?

Fan hardware IDs like `openfan:ch03` or `hwmon:it8696:pci0:pwm1:CHA_FAN1` are not helpful for daily use. The wizard lets you assign labels like "Rear Exhaust" or "CPU Cooler" that appear everywhere in the GUI — dashboard, controls, diagnostics, and profile editing.

Launch it with the **Fan Wizard** button in the Controls page's Fan Roles header.

## How It Works

### Step 1: Introduction

Explains the process and runs pre-flight checks. The wizard will not proceed unless:

- the daemon is connected,
- at least one controllable fan output was detected, and
- no CPU sensor is above 85°C — your system should be **idle and cool**.

When everything is in order it reports "Ready — *N* controllable fan(s) detected."

### Step 2: Detected Fans

A table of every testable fan, with a checkbox per row (all selected by default) and **Select All** / **Select None** buttons:

| Column | Meaning |
|--------|---------|
| *(checkbox)* | Include this fan in the identification run |
| **ID** | The hardware ID |
| **Source** | `openfan`, `hwmon`, or `amd_gpu` |
| **RPM** | Current measured speed |
| **Current Label** | Existing alias or daemon-supplied label |

Headers reporting 0 RPM (nothing plugged in) and read-only fans (e.g. firmware-managed Intel Arc GPU fans) are excluded automatically.

### Step 3: Identify Each Fan

The wizard works through your selected fans one at a time. For each fan:

1. Press **Start Test** — the fan's PWM is set to 0% so it spins down
2. A countdown runs ("3 / 8 seconds") with a live RPM readout; press **Abort** to end the test early
3. Watch your case to see **which physical fan stopped**
4. The fan is **restored automatically** when the countdown ends
5. Pick a **label** — a preset (CPU Cooler, Rear Exhaust, Front Intake Top, …) or any custom text
6. If several fans changed at once (they share a splitter or hub), tick **"Multiple physical fans moved (splitter/hub)"** and add an optional note
7. Press **Save Label & Next Fan** — or **Skip — couldn't identify** to move on without saving a label (you can re-run the test first; nothing limits retries)

### Step 4: Review Labels

A summary table (ID, Source, New Label, Notes) where every label and note is still editable — including for fans you skipped. Click **Finish** to save all labels, or **Cancel** to discard everything.

## Safety Features

- **Thermal abort:** CPU temperature is checked before and during every test. If any CPU sensor exceeds **85°C**, the test aborts immediately and the fan is restored.
- **One fan at a time:** only one fan is ever stopped.
- **Restore on every exit:** finishing, cancelling, aborting a test, or closing the wizard all restore the tested fans to their last commanded speed. If no prior speed is known (e.g. a GPU fan that was under firmware control), a **30% fallback** is used until automatic control resumes.
- **Lease management:** if any selected fan is a motherboard (hwmon) header, the wizard holds the hwmon lease for the whole session and releases it when the wizard closes, so nothing else can write those headers mid-test.
- **Control loop paused:** the GUI's own curve evaluation is suspended while the wizard runs and resumes when it closes.

## Settings That Affect the Wizard

| Setting | Location | Effect |
|---------|----------|--------|
| **Fan Wizard spin-down timer** | Settings → Application → Behaviour | How long each fan stays stopped (5–12 seconds, default 8) |

## Where Labels Appear

Once saved, fan labels propagate across the entire application:

- Dashboard fan table and chart legend
- Controls page — fan role member lists
- Diagnostics fan table
- Profile files — member labels are snapshotted into the profile JSON

Labels are stored as `fan_aliases` in `app_settings.json` and persist across sessions. Display names always prefer your alias, then the GPU model or hwmon header label, then the raw hardware ID.

---

Previous: [Diagnostics](/manual/diagnostics.md) | Next: [Profiles and Curves Reference](/manual/profiles-and-curves.md)
