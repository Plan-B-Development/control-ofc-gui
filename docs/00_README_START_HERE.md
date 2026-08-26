# Control-OFC GUI — Claude Documentation Pack

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

This pack is the **working source of truth** for building the Linux-first desktop GUI for **Control-OFC**, the GUI for controlling the **OpenFanController** system through the daemon/API.

## Read this pack in this order
1. `01_Product_Overview.md`
2. `02_System_Architecture_and_Boundaries.md`
3. `03_UX_UI_Principles_and_Visual_System.md`
4. `04_Dashboard_Spec.md`
5. `05_Controls_Profiles_and_Curves_Spec.md`
6. `06_Settings_Spec.md`
7. `07_Diagnostics_Spec.md`
8. `08_API_Integration_Contract.md`
9. `09_State_Model_and_Control_Behaviour.md`
10. `10_Demo_Mode_Spec.md`
11. `11_Persistence_Config_and_File_Layout.md`
12. `12_Implementation_Plan_and_Module_Structure.md`
13. `13_Acceptance_Criteria.md`
14. `14_Risks_Gaps_and_Future_Work.md`
15. `15_Branding_Art_and_Asset_Direction.md`
16. `18_Operations_Guide.md`
17. `19_Hardware_Compatibility.md`
18. `20_Sensor_Interpretation_Guide.md`
19. `21_AMD_Motherboard_Fan_Control_Guide.md` — vendor-by-vendor setup, drivers, quirks, troubleshooting
20. `22_AMD_Sensor_Interpretation_Deep_Dive.md` — what sensor readings actually mean and confidence levels
21. `23_Intel_Motherboard_Fan_Control_Guide.md` — Intel LGA1700 / LGA1851 companion to the AMD guide (DEC-110)
22. `24_Cooling_Hardware_Readiness_Guide.md` — Hardware-page cooling-readiness checks explained: what each finding means and how to fix it (DEC-206/207; target of the page's "Learn how" links)
23. `25_GPU_Support_Rules.md` — GPU discovery, PMFW vs legacy `pwm1` fan control, RDNA3/RDNA4 differences, and GPU display naming. **Read before touching any GPU fan path**

> Slots 16 (`16_User_Decisions_and_API_Notes_Reference.md`) and 17 ("Documentation Audit / Traceability Matrix") are intentionally absent from the published pack. Doc 16 is a local-only running log (gitignored). Doc 17's traceability function is now performed by the per-release `CHANGELOG.md` entries.

## Key decisions already made
- Linux-first desktop app
- Primary target: **CachyOS / Arch Linux + KDE Plasma**
- GUI must **only** talk to the **daemon/API**
- GUI must **never** talk directly to hardware
- V1 is **desktop-window first**
- Tray/minimise-to-tray comes later
- Default theme is **dark**
- Branding is restrained and professional; the working UI feels technically credible
- One **profile** is active at a time
- Each fan belongs to **at most one fan role** (the member picker disables fans already assigned elsewhere)
- Fan curves use **one sensor** in V1
- Simple hysteresis (deadband) is included in V1 control loop
- There must be a **demo mode** for testing without hardware
- Polling history is kept for the **last 2 hours**
- System health is **first-class** — its own Overview / System State / Hardware / Logs pages, not an afterthought
- V1 uses a **fixed dashboard**
- Theme import/export existed from the start; a full theme editor (per-token palette editing with contrast checking) shipped in a later release

## Highest-impact architectural decision
**The daemon owns runtime control.** As of **2.0.0** (DEC-159, DEC-165) the daemon's profile engine is the **single authoritative writer** of every fan backend (OpenFan, hwmon, GPU PMFW): it evaluates the active profile's curves autonomously and keeps fans controlled through GUI close, crash, or sleep. The GUI is an **editor/viewer/controller-of-intent that never writes PWM**.

With the GUI present:

- the GUI **authors and validates** profiles, uploads them to the daemon's profile store (DEC-160), and **activates** one for autonomous evaluation
- the GUI **polls** the daemon at 1 Hz for sensors, fans, and status, and renders them — it runs no control loop and holds no hwmon lease
- live manual control is an **expiring daemon override** (DEC-163), not a GUI write; fan identification is a daemon **identify** call (DEC-166)
- the GUI persists its own UI-owned state locally (fan aliases, themes, window layout)

A new-GUI / old-daemon mix is **refused**, never run dual-writer: the GUI gates control on the daemon advertising `control.autonomous_control`, and the package pins `control-ofc-daemon>=2.0.0`. **Demo mode** is the one exception — it runs a GUI-side evaluator against synthetic hardware, never touching the daemon (DEC-165).

This daemon-owns-control model is the single most important build assumption in this pack. (Before 2.0.0 the GUI owned the control loop — DEC-010, now superseded.)

## Design intent
The UI should feel like:
- a proper Linux desktop utility
- dark, readable, and fast
- clean enough for daily use
- restrained and professional in tone
- simple at first glance, with advanced complexity progressively revealed

## Pack contents
This pack includes:
- product requirements
- UX/UI rules
- page-by-page specs
- API contract integration notes
- control-loop rules
- demo mode design
- persistence model
- module structure guidance
- acceptance criteria
- risks and future work
- asset/branding direction
- **operations guide** (18) — daemon config, CLI, permissions, troubleshooting
- **hardware compatibility** (19), **sensor guide** (20), **AMD motherboard guide** (21), **AMD sensor deep-dive** (22), **Intel motherboard guide** (23) — helper / compatibility articles for end-user troubleshooting
- **cooling hardware readiness** (24)
- **GPU support rules** (25) — the detailed AMD GPU fan-control rules, split out of `CLAUDE.md` so they load on demand

## Looking for code, not documents?
This file is a **reading order for the docs pack**. It will not tell you where a
function lives.

- **`02_System_Architecture_and_Boundaries.md` § Module map** is the annotated `src/`
  tree — the fastest route from "where does X live?" to a file path.
- **`docs/architecture/`** holds the two deep dives that are not part of the numbered
  pack, and were previously indexed nowhere:
  - `daemon-end-to-end.md` — the full request/'control path through the daemon
  - `openfan-controller-integration.md` — the OpenFan serial/USB integration

## Reference note
This pack incorporates:
- the current chat decisions
- the uploaded API notes
- the recent direction around sensors, telemetry, groups, theme architecture, motherboard PWM support via daemon/API, and Linux/KDE-focused UX

## Instruction to Claude
Treat this pack as the implementation brief. Do not redesign the product fundamentals unless there is a critical technical reason. Where there are gaps, choose the most conservative, least-surprising UX and keep the architecture extensible.
