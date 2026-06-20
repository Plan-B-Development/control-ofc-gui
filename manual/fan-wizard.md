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
2. A progress bar fills as the timer runs ("3 / 8 seconds", counting elapsed time upward) with a live RPM readout; press **Abort** to end the test early
3. Watch your case to see **which physical fan stopped**
4. The fan is **restored automatically** when the test period ends
5. Pick a **label** — a preset (CPU Cooler, Rear Exhaust, Front Intake Top, …) or any custom text
6. If several fans changed at once (they share a splitter or hub), tick **"Multiple physical fans moved (splitter/hub)"** and add an optional note
7. Press **Save Label & Next Fan** — or **Skip — couldn't identify** to move on without saving a label (you can re-run the test first; nothing limits retries)

### Step 4: Review Labels

A summary table (ID, Source, New Label, Notes) where every label and note is still editable — including for fans you skipped. Click **Finish** to save all labels, or **Cancel** to discard everything.

## Safety Features

- **Thermal abort:** CPU temperature is checked before and during every test. If any CPU sensor exceeds **85°C**, the test aborts immediately and the fan is restored.
- **One fan at a time:** the wizard asks the daemon to stop only the fan you are identifying. Every other fan keeps running on its curve — there is no global pause and the daemon stays in charge throughout.
- **Daemon-enforced auto-restore:** each stop is a daemon request with a built-in deadman timer, so even if the GUI closes or crashes mid-test the daemon restores that fan on its own. Finishing, cancelling, aborting a test, or closing the wizard also restore the tested fan. On restore the daemon simply removes the identify entry, and the fan resumes its normal curve control on the next daemon tick (1 Hz) — there is no separate fallback speed.

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

Previous: [Diagnostics](diagnostics.md) | Next: [Profiles and Curves Reference](profiles-and-curves.md)
