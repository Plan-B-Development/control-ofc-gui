# System Health (Overview, System State, Hardware & Logs)

The application spreads the health and status of every subsystem across four standalone sidebar pages — **Overview**, **System State**, **Hardware**, and **Logs**. Together they are your primary tools for troubleshooting connection issues, stale sensors, and hardware detection problems. (In earlier versions these were tabs on a single **Diagnostics** page; that tabbed page was retired in the redesign and its content re-homed onto the four pages documented below.)

> **Looking for help with a specific motherboard or fan controller?** Start with the [Hardware Troubleshooting](hardware-troubleshooting.md) page — it covers the Hardware Readiness checks on the **System State** page, Test PWM Control, vendor quirks, and what to do when fans report 0 RPM or refuse to change speed.

## Overview page

![Overview page](../screenshots/auto/03_overview.png)

The **Overview** page answers *"is the daemon healthy, what hardware was found, and what are the sensors and fans doing?"*. It opens with two information cards — **Daemon Health** and **Device Discovery** — followed by the **Fan Status** and **Sensors** sections, in that order, separated by a drag handle that retrades height between the two tables. The two tables share whatever height the window has spare, so making the window taller shows more rows rather than more empty page; drag the handle to give one table more of that height than the other. (The two sections are documented below in the reverse order, sensors first.)

### Daemon Health

| Field | Meaning |
|-------|---------|
| **Daemon version** | Version of the running daemon and its API |
| **Status** | Overall health: "healthy", "warning", or "critical" |
| **Uptime** | How long the daemon has been running since last restart |
| **Subsystems** | List of subsystems (openfan, hwmon_sensors, hwmon_pwm) with their status and age |

**Age** is the time in milliseconds since the daemon last polled that hardware subsystem. A low age (under 1000ms) means the data is fresh. A high age means the daemon is having trouble reaching that hardware.

### Device Discovery

| Field | Meaning |
|-------|---------|
| **OpenFan** | Whether an OpenFan Controller is detected, channel count, and write/RPM capability |
| **hwmon** | Whether motherboard fan headers are detected, the header count, and their write capability |
| **AMD GPU** | Whether an AMD discrete GPU is detected, its model, PCI address, and fan control method (`pmfw` or legacy `pwm1`) |
| **Intel GPU** | Whether an Intel Arc discrete GPU is detected, its model, and PCI address. Intel GPU fans are always reported `read_only (firmware-managed)` — the `xe`/`i915` drivers expose no fan-control path |
| **NVIDIA GPU** | Whether an NVIDIA discrete GPU is detected (via the open `nouveau` driver or the opt-in NVML backend), its model, driver, and PCI address. NVIDIA GPU fans are always reported `read-only` — `nouveau`'s writable `pwm1` is excluded for safety and the NVML backend is telemetry-only |
| **Liquid cooling** | Whether a liquid cooler (AIO) is detected via hwmon, and whether its pump/fan is writable, monitor-only (a read-only driver such as NZXT Kraken2), or not detected. USB-only coolers are out of scope and shown as not detected |
| **Features** | Summary of write capabilities (OpenFan writes, hwmon writes) |

### Sensors

An 8-column diagnostic table of every temperature sensor reported by the daemon (`#`, Label, Sensor ID, Source class, Chip, Value, Age, Confidence — right-click a row for the full detail dialog). A **header summary line** above the table answers "is anything wrong?" at a glance — `Sensors: N total · X CPU · Y board · Z GPU · V liquid · W disk · K stale · J low-confidence · U unavailable · M hidden`.

| Column | Meaning |
|--------|---------|
| **Label** | Sensor label reported by the kernel driver (e.g., "Tctl", "edge"). Prefixed with `⚠ ` for a sensor with a documented bogus-quirk (e.g. the ASUS NCT6776F `CPUTIN` case) and `? ` for a low-confidence classification |
| **Sensor ID** | Stable identifier (e.g. `hwmon:<chip>:<dev_id>:<label>`) — the id profiles use to bind a curve to this sensor |
| **Source class** | Fine-grained classification from the sensor knowledge base (`cpu_die`, `vrm`, `board_thermistor`, `gpu_package`, …) |
| **Source** | Daemon source subsystem: `hwmon`, `amd_gpu`, `intel_gpu`, or `nvidia_gpu` |
| **Chip** | Kernel driver / chip providing the reading (e.g., `k10temp`, `nct6798`, `amdgpu`, `xe`) |
| **Value (°C)** | Current temperature in °C. Suffixed `⚠ ALARM` when the daemon reports a critical alarm or the live value has crossed the reported critical threshold |
| **Session min/max** | Lowest and highest values observed since the daemon started |
| **Age (ms)** | Time since the daemon last read this sensor |
| **Confidence** | How certain the GUI is about how to interpret this sensor: `high`, `medium`, `low`, or `unknown`. Lower confidence usually means the sensor's chip has known quirks (e.g., the ASUS NCT6776F `CPUTIN` reading is a board temperature, not the CPU) |
| **Details** | A per-row button that opens the **Sensor Detail** dialog |

The `K stale` count on the header summary line is the quick check for sensors that have stopped updating. Per-sensor kind, driver type, trend, and freshness are not table columns — open the row's **Details** dialog to see them (trend also appears in the hover tooltip).

#### Unavailable sensors

Some sensors exist but currently can't be read at all — the classic example is a Wi-Fi-radio temperature (an `ath12k` chip) that returns "Network is down" whenever the radio is switched off. Rather than letting the daemon log that failure endlessly, these are reported as **unavailable** and listed in a small panel below the table: each shows its label, the read-error reason, and how long it has been unavailable, and the count appears as `· U unavailable` on the header summary line. The panel is hidden entirely when nothing is unavailable. Because such a sensor can disappear the moment its device powers down, it is **never offered as a fan-curve source** in the Controls page (a curve already bound to one keeps working). This is display-only — there is no banner or pop-up — and an unavailable sensor re-appears in the table automatically the moment it becomes readable again.

#### Sensor Detail dialog

Open it via the per-row **Details** button, a row double-click, or right-click → **Open detail…**. It shows the full classification description and every classification note (not truncated like the hover tooltip), board context, a **Thresholds** section with a headroom-to-critical indicator, and a clickable kernel.org driver documentation link. On a daemon that reports its own classification (≥ v2.6.0), the dialog adds a **Daemon classification** section — the daemon's authoritative class, confidence, and rationale — shown alongside the GUI's own heuristic, not replacing it.

#### Hiding sensors

Right-click a row → **Hide sensor** to remove a sensor you don't care about (a duplicate, or a chip that always reads garbage). Hidden sensors are never silently dropped — they collapse into a `▸ N hidden sensors` toggle row at the bottom that you can expand again. This hide-list is **local to this page**; the **Mirror hidden to dashboard** button in the header pushes the current hide-list into the shared dashboard series selection as a one-shot.

Right-click also offers **Treat as coolant** / **Reset to auto** — force a sensor to be classified as a liquid-cooling **coolant** temperature (so it groups under *AIO / Liquid* on the dashboard) when the automatic classifier is too conservative, or clear that override. This is a local GUI preference.

On a daemon ≥ v2.6.0 the menu also offers **Set as preferred CPU sensor** / **Set as preferred motherboard sensor** — the same daemon-persisted, advisory preference you can set from the **Settings** page (Preferred Sensors card); thermal safety still uses the hottest CPU sensor regardless.

Hover any row to see a tooltip explaining the chip's source class, description, and any known driver quirks. For deeper sensor interpretation, see the [Sensor Interpretation Guide](../docs/20_Sensor_Interpretation_Guide.md) and the [AMD Sensor Interpretation Deep Dive](../docs/22_AMD_Sensor_Interpretation_Deep_Dive.md).

### Fan Status

The **Fan Status** section is a single live view of every controllable fan output the daemon reports. (Hardware-health checks — chip detection, driver state, BIOS interference, and PWM verification — live on the separate [System State](#system-state-page) page.)

The fan table has the following columns:

| Column | Meaning |
|--------|---------|
| **ID** | Display name (user alias if set, otherwise hardware label or fan ID) |
| **Source** | Connection type: openfan, hwmon, amd_gpu, intel_gpu, or nvidia_gpu |
| **Control method** | How this fan can be controlled: `openfan`, `hwmon` (with PWM-only or full read/write), `amd_gpu` (PMFW or legacy pwm1), `read_only`, or `unknown`. Read-only entries cannot be commanded — **Test PWM Control** on the [System State](#system-state-page) page explains why |
| **RPM** | Hardware-measured speed (dash if not available). Writable hwmon headers reading 0 RPM are annotated `(no fan detected)` so you don't accidentally assign a curve to an empty header |
| **PWM (%)** | Last commanded speed percentage (dash if not set) |
| **Freshness** | "fresh" (under 2 s), "stale" (2-10 s, shown in yellow), or "invalid" (over 10 s, shown in red) |

Hover any cell for a tooltip explaining what the value means and, for read-only fans, why the GUI cannot drive them.

## System State page

![System State page](../screenshots/auto/04_system_state.png)

The **System State** page is the **Hardware Readiness** health report — it answers *"is my fan-control hardware healthy, and if not, how do I fix it?"*. It fetches `/diagnostics/hardware` automatically the first time you open the page (or on demand via the global footer's **Rescan Hardware** action).

It leads with the answer and keeps the detail one click away:

- **Verdict banner** — a green *✓ System ready* line, or an amber/red *⚠ N issue(s) need attention* — always on screen.
- **Issue checklist** — one row per detected problem, each with a colour-coded severity badge (**CRITICAL** / **WARN**, icon + word + colour), a one-line **fix**, and a clickable link to the relevant documentation. A healthy system shows a single *✓ No issues detected* line. This is the fastest path from "something is wrong" to "here is what to change".
- **Alerts & advisories** — blocking alerts (driver-module collisions, active BIOS interference) and informational notices (dual-chip setup, vendor quirks, ACPI conflicts) appear here when relevant. Vendor quirks render as per-advisory rows with a four-tier severity badge (**CRITICAL** red / **HIGH** orange / **MEDIUM** amber / **INFO** blue) and a collapsible detail. Safety-critical alerts are always visible — never hidden behind a collapse.
- **Hardware Registry** — the chip/driver table (Status, Chip / Component, Driver, Driver Status, Mainline, Headers), always visible below the health report. It is the dominant element of the page's second row, taking about three quarters of the width; drag the divider on its right to give it more or less.
- **Interference Monitor** and **Safety & GPU Limits** — always-visible cards stacked in the column beside the Hardware Registry. The Interference Monitor carries the BIOS-reclaim gauge and names the contended header when the BIOS has been reclaiming fan control; Safety & GPU Limits carries the CPU thermal state, the GPU rows, and the firmware speed range. Neither is hidden behind a collapse — the page keeps its safety-relevant readings on screen.
- **Advanced actions** (the page's one collapsible section, collapsed by default): the **Test PWM Control** / **Verify All Writable** buttons, **Characterise PWM Response** (a deeper PWM/RPM sweep beside the quick test — daemon 2.29.0+, hidden otherwise; see [Hardware Troubleshooting](hardware-troubleshooting.md#characterise-pwm-response)), plus **Test GPU Fan Control** and **Restore GPU Fan to Automatic** when a writable AMD GPU is present.
- **Restore GPU Fan to Automatic** hands the GPU fan back to the firmware's default curve — use it to undo a static GPU speed set this session without restarting the daemon. It is disabled (with the reason in its tooltip) while an active profile is driving the GPU fan: remove the GPU from its fan role or deactivate the profile first.
- **Rescan Hardware** asks the daemon to re-enumerate hwmon sensors and PWM headers — use it right after loading a sensor kernel module (for example following a driver install). New sensors appear within a couple of poll cycles; brand-new motherboard fan-*control* hardware still requires a daemon restart. An **OpenFan controller is the exception** (daemon 2.18.0+): the same button also asks the daemon to look for one, and if it finds one it is adopted immediately — the result line names the port it was found on, and says nothing about restarting.
- **Open Full Report ↗** opens the complete readiness report in its own resizable window; every link in it is clickable.

This page is covered in depth on the [Hardware Troubleshooting](hardware-troubleshooting.md) page.

> **Lease management is daemon-internal as of 2.0.0.** The daemon is the sole writer of motherboard fan headers, so it manages the hwmon lease entirely on its own — the GUI never holds one. The dedicated **Lease** tab that earlier versions showed here has been removed.

## Hardware page

![Hardware page](../screenshots/auto/05_hardware.png)

The **Hardware** page is where you see, understand and test your cooling hardware. It has four sections: the **System Readiness Checklist** (the daemon's go/no-go assessment), **Recommended Actions**, **Cooling Hardware** (your coolers and every PWM header), **Hardware Diagnostics** (the active tests), and the **Super-I/O Architecture** report (motherboard sensor/fan-chip detection). The readiness and Super-I/O halves come from a *single* request to the daemon's combined `GET /inventory/hardware-readiness`, which serves one shared, coalesced hardware scan — so those two sections can never disagree with each other.

Every readiness check on this page is explained in full — what it means, why it fails, and how to clear it — in the [Cooling Hardware Readiness Guide](../docs/24_Cooling_Hardware_Readiness_Guide.md); the page's own *Learn how* links point there.

Everything the page *displays* is read-only. The tests in **Hardware Diagnostics** do exercise your hardware, and each is described below; all of them run inside the daemon, which keeps its hwmon lease, the pump safety floor and thermal protection in force throughout. **The GUI never writes a PWM value itself, and no action on this page can stop a pump or drive a fan below its floor.**

### System Readiness Checklist

The **System Readiness Checklist** shows the daemon's own structured assessment of your cooling hardware — its answer to *"what is ready, what needs attention, and what should I do next?"*. It populates the first time you open the page (or via **Refresh Readiness**).

It is similar in spirit to the **System State** page but comes from a different source: the **System State** page is the GUI's own hardware-readiness report built from `/diagnostics/hardware` (drivers, chips, BIOS interference, PWM tests), while this checklist is the *daemon's* go/no-go assessment — CPU-sensor presence, default-CPU confidence, whether PWM controls are present / read-only / not-yet-verified, monitor-only fan tachometers, quarantined sensors, and any preferred sensor that has gone missing.

- **Verdict banner** — an overall *Hardware ready* / *Needs attention* / *Not ready* line, colour- and glyph-coded, always on top.
- **Item checklist** — one card per item, most severe first. Each shows a severity chip (**CRITICAL** / **WARN** / **OK**, icon + word + colour), a one-line summary, and — inside an expandable **Details** section — the technical detail, the recommended next step, and impact flags (*affects safety*, *blocks fan control*, *blocks monitoring*, *reboot may be required*). Warning and critical items open their detail automatically.
- A healthy system shows *✓ All hardware-readiness checks passed.*

Requires `control-ofc-daemon` ≥ v2.6.0. On a daemon that predates the feature the checklist reports that hardware readiness is unavailable.

### Recommended Actions

Each checklist item carries a recommended next step; the page gathers these into a **Recommended Actions** summary so you can work down the list from most to least urgent. A **PWM control test** action now scrolls you to this page's own **Hardware Diagnostics** section — the tests live here. GPU-fan verification, and the bulk **Verify All Writable** sweep, still live on the **System State** page, reachable from the **Advanced (System State)** button in that section.

### Cooling Hardware

This section answers *"what cooling hardware do I have, and what is it doing right now?"*.

**Cooling devices.** If you have grouped a pump and its radiator fans into a cooling device (via **Configure AIO…** on the Controls page), it appears here as one assembly rather than a set of unrelated hwmon channels: pump, radiator fans, control sensor, pump strategy, coolant telemetry and an overall status, with live RPM and PWM beside each member. Buttons let you jump to the headers, characterise the pump, start a validation session, edit the configuration, or **Forget Device** — which removes only the *grouping*. Your headers, their assigned roles and the pump safety floor are unaffected, and no fan changes speed.

**PWM headers.** Every discovered header gets a card showing its name, role, live RPM, and — kept deliberately apart — the PWM the daemon **requested** and the PWM the hardware **reports back**. Those two numbers are what let you tell a failed write from a BIOS/EC reclaim from a device doing its own control; a single merged number would hide all three. A **Details** disclosure adds the engineering view: which chip and channel, the kernel label, the capability audit (PWM write, readback, `pwm_enable`, supported control modes, PWM/DC mode, base frequency, tach pulses, alarm state, BIOS/EC reclaim count), and the classification and safety block — role, role source, the device safety floor, and whether stopping the fan is prohibited.

A few things read as **Unknown** on most boards, and that is normal rather than a problem: the supported control modes are known only for `it87` and `nct6775` chips, and many boards expose no PWM frequency or tach-pulse count at all. The page says *Unknown* rather than *Unsupported* precisely because a driver that stays silent is not a driver saying no.

Requested PWM shows as approximate on a daemon older than v2.33.0, which did not report the commanded duty separately from the readback.

### Hardware Diagnostics

The active tests, gathered in one place instead of scattered across the page. Each is disabled with a reason in its tooltip when your hardware or daemon cannot support it — a read-only header cannot be driven, and validation sessions need daemon v2.32.0 or newer plus a configured cooling device.

- **Test Control** (on each header card) — the quick write-and-read-back test. See [Hardware Troubleshooting § Test PWM Control](hardware-troubleshooting.md#test-pwm-control).
- **Characterise** (on each header card, and **Characterise Pump** on a device card) — the full sweep, now also reporting response latency and settling time per step. See [§ Characterise PWM Response](hardware-troubleshooting.md#characterise-pwm-response).
- **Startup / Lifecycle Recording** — records how your cooler behaves across startup, resume and profile changes.
- **AIO Validation** — records what your cooler actually does and finalises into an evidence summary you can export as CSV or JSON.

Both recorders open the same dialog. It shows elapsed time, per-member telemetry and, when the session finishes, a findings table using explicit result words — **PASS**, **FAIL**, **OBSERVED**, **NOT OBSERVED**, **NOT TESTED**, **UNKNOWN**, **UNAVAILABLE**. Nothing that was not actually tested is ever reported as PASS, and a capability your hardware simply does not expose is reported as *unavailable*, never as a failure. You can mark an event while recording, and attach external electrical measurements (a meter or logic-analyser reading) to the session — those are stored for your own analysis and are never used for any control or safety decision.

### Super-I/O Architecture

The **Super-I/O Architecture** section answers *"which motherboard sensor/fan chip do I have, and is its driver loaded?"*. Most desktop boards route their fan headers and temperature sensors through a Super-I/O chip (ITE, Nuvoton/Winbond, SMSC, …); if its kernel driver is not loaded, the daemon can't see those fans at all. This section renders the Super-I/O half of that same combined response and shows **one card per detected chip** — its vendor, a confidence level, how the chip was detected (the board table, the kernel log, an already-bound driver, or the opt-in port probe), and whether its driver is bound. For a chip whose driver is *not* loaded, the card expands a **How to enable** section with the exact driver name and a copy-paste command to load it.

- The report is **passive and read-only** — the daemon composes signals it already has (DMI board table, bound hwmon chips, `/proc/modules`, `/dev/kmsg`, ACPI port overlaps). It never loads a module or changes your system.
- **Detection is not control.** A card means a chip is present and a driver exists — it does *not* prove fan control works. Loading the driver (and the **System State** page's PWM test) is what confirms that.
- On a non-x86 machine, or a daemon that predates the feature, the section reports that detection is unavailable.

#### Probe ports (advanced)

Passive detection can't see a chip whose driver never loaded and that never appeared in the kernel log. For that case an **opt-in active probe** can read the Super-I/O configuration ports directly to identify an unbound chip. The **Probe ports (advanced)** button runs it — but it is **off by default** and stays disabled (with the reason in its tooltip) unless the daemon operator has deliberately enabled it, because it requires a root-equivalent capability (`CAP_SYS_RAWIO`). Enabling it needs two steps on the daemon host: set `allow_port_probe = true` in `/etc/control-ofc/daemon.toml` **and** install the opt-in `superio-port-probe.conf.example` systemd drop-in (shipped in the daemon package's docs). Even when enabled, the probe only reads chip-ID registers, never touches a port a driver or the firmware is already using, and changes nothing. Clicking the button asks for confirmation first.

The Super-I/O Architecture section requires `control-ofc-daemon` ≥ v2.7.0.

## Logs page

![Logs page](../screenshots/auto/08_logs.png)

A live, filterable list of in-process GUI events: daemon connect/disconnect, profile activations and override actions, alert onsets and recoveries, theme changes, and the like. The log retains up to 200 entries (oldest discarded first).

The page is a **find it, then understand it** workflow: a compact alert bar and an
activity strip across the top, the event list on the left, and an inspector on the right
that explains whichever event you have selected — and which also holds the diagnostic
probes, so they no longer take a strip along the bottom.

**Newest entries are at the top.** Repeated identical events collapse into a single row
carrying a `×N` badge rather than filling the list; the inspector tells you how many
times it happened and when it started.

Three concepts that look similar but answer different questions:

| Surface | Question it answers | Persistence |
|---------|---------------------|-------------|
| **Event Log** (this page) | *What has the GUI been doing in this session?* | In-process only, capped at 200 |
| **Alerts** (this page) | *What is wrong right now — and what was wrong a moment ago?* | An alert stops being active when the condition resolves, but stays listed as **recovered** until you acknowledge it, so one that clears before you look at it can still be read afterwards. **"Acknowledge all"** records that you have seen them; it does not claim the condition is fixed, and it never suppresses the same problem happening again |
| **System Journal** (inspector tab) | *What happened across restarts on the daemon side?* | Persisted by systemd |

### Activity strip

A row of columns above the list showing how many events arrived over the retained feed,
coloured by severity so a burst of errors is visible without reading a single line.
Click a column to narrow the list to that slice of time; click it again, press
<kbd>Esc</kbd>, or use **Clear time filter** to go back to everything. It is keyboard
operable — <kbd>←</kbd>/<kbd>→</kbd> to move, <kbd>Space</kbd> to apply.

The column heights count *events*, while the severity chips beside the search box count
*rows*. Those differ whenever a run has collapsed, which is what the `×N` badge is
telling you.

### Alert bar

One line above the table. When nothing is wrong it reads `✓ No active alerts` and takes
almost no space; if something recently cleared without you seeing it, it adds
`Recent alert: … recovered at 09:21:06` so a problem that fixed itself is still
discoverable.

When something *is* wrong it names the counts and the most serious alert —
`✖ 1 critical   ⚠ 2 warnings` — and offers **View alerts**, which opens the **Alert
Centre**: every active alert with when it was first and last detected, where it came
from, a suggested next step where one can be given reliably, and **Acknowledge** /
**Show related logs**; plus a compact list of what recently recovered.

**Acknowledging is not fixing.** It records that you have seen the alert. The footer
health rollup keeps reporting the condition for as long as it genuinely exists, and the
same problem happening again later always raises a fresh alert.

### Filters

| Control | Behaviour |
|---------|-----------|
| **All** | Re-checks every severity in one click. |
| **INFO / WARN / ERR chips** | Independent severity filters, each showing how many rows it holds. Uncheck a level to hide every row at that severity; uncheck all three and the list is empty, which is a real state rather than a reset. The counts stay put when you uncheck a chip, so you can see what turning it back on would bring. |
| **Source dropdown** | Single-select source filter — `gui`, `polling`, `profile`, `kernel`, etc. New sources appear automatically the first time they fire. |
| **Search** | Case-insensitive substring match against message text and source. |
| **Follow** | Follows new entries as they arrive at the top of the list. Scroll away and it pauses, showing `N new events ↑` — click that to jump back to the newest and resume. You are never yanked back while reading older rows. |

Keyboard shortcuts, active while the list has focus: <kbd>↑</kbd>/<kbd>↓</kbd> to move
the selection, <kbd>/</kbd> to jump to the search box, <kbd>f</kbd> to toggle Follow,
<kbd>Esc</kbd> to clear the selection.

### Inspector

The pane on the right. Selecting a row fills its first two tabs; the last two are
session tools that do not depend on which event is selected. Close it with **✕** to give
the list the full width. Selecting an event again brings it back.

| Tab | What it shows |
|-----|---------------|
| **Details** | Severity, the precise timestamp, source, and the full untruncated message. Where the event carried structured data — an alert's component, an exception type, a rescan's header count — it is listed as named fields; where it did not, nothing is shown rather than empty rows. A repeated event also reports how many times and when it started. Below that, **related events** and one-click actions. |
| **Raw** | The stored event record in full, selectable and copyable. This is the event as it was recorded, not a reconstructed log line. |
| **Diagnostics** | The three on-demand probes — **Daemon Status**, **Controller (OpenFan)**, **GPU State** — each with its own **Refresh**. |
| **Journal** | Recent entries from the `control-ofc-daemon.service` systemd journal, with **Fetch** / **Refresh**. |

Diagnostics and Journal are fetched the first time you open their tab, not when you open
the page — so simply visiting Logs never shells out to the system journal. Clearing the
event log never wipes a probe you just fetched.

#### Related events, and what "related" means here

An event carrying a **component** (an alert about a specific fan or sensor, say) lists
other events about that same component. Otherwise it falls back to other events from the
same source, and the panel says so — because "same source tag" is a weaker claim than
"related", and it should not pretend otherwise. **Filter to these** narrows the list to
that group.

To jump to one, **activate** it: press **Enter** on the highlighted entry, or double-click
it. On desktops that open items with a single click — KDE Plasma's default — a single
click does it too. (One gesture, not two: the panel rebuilds itself the moment you jump,
so a second activation of the same click would land on a list that no longer exists.)

#### Actions

| Button | Action |
|--------|--------|
| **Copy event + context** | Copy the full record plus its related events. |
| **Open Hardware / Open Controls** | Appears for events from a source with an obvious next place to look, and takes you there. |
| **Clear Logs** (toolbar) | Empty the event log (does not affect the inspector's probes). |
| **Copy** (toolbar) | Copy the *currently-visible* rows, after filters, search and time window are applied. |

Exporting a support bundle is the footer's **Export Support Bundle** button, which is
available from every page.

### Export Support Bundle

The **Export Support Bundle** button creates a JSON file containing:

- System configuration
- Daemon status and version
- Sensor and fan states
- Event log entries
- Active profile information

This file is useful for reporting issues. Review it before sharing — it may contain system-specific details. The same bundle is also reachable from the global footer's **Export Bundle** button.

---

Previous: [Settings](settings.md) | Next: [Fan Wizard](fan-wizard.md)
