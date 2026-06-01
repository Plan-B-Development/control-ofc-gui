# Control-OFC GUI — Claude Documentation Pack

**Last updated:** 2026-05-07 (audit-pass-2 doc-correctness wave; reading-order list updated; helper-article cross-references refreshed.)

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
9. `09_State_Model_Control_Loop_and_Lease_Behaviour.md`
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

> Slots 16 (`16_User_Decisions_and_API_Notes_Reference.md`) and 17 ("Documentation Audit / Traceability Matrix") are intentionally absent from the published pack. Doc 16 is a local-only running log (gitignored). Doc 17's traceability function is now performed by `DECISIONS.md` + per-release `CHANGELOG.md` + the audit logs under `docs/audit/`.

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
- Fans may belong to **multiple groups**
- Fan curves use **one sensor** in V1
- Simple hysteresis (deadband) is included in V1 control loop
- There must be a **demo mode** for testing without hardware
- Polling history is kept for the **last 2 hours**
- Diagnostics is a first-class page, not an afterthought
- V1 uses a **fixed dashboard**
- Full palette editing is **not** in V1, but theme import/export should exist from the start

## Highest-impact architectural decision
The daemon supports **two control modes**:

- **Imperative mode** (default — no profile loaded): the GUI owns curve evaluation and the daemon performs only the PWM/RPM writes the GUI requests. This is the V1 GUI's primary mode of operation.
- **Profile mode** (headless — profile loaded via CLI, env, API, or persisted state): the daemon evaluates curves autonomously. The GUI still takes priority — when the GUI is active and writing within the last 30 s, the daemon's profile engine defers to it (DEC-071, DEC-074), so concurrent operation never produces a dual-writer conflict.

For V1, with the GUI present:

- the **GUI owns the control loop** while connected
- the GUI polls sensors at 1 Hz
- the GUI evaluates active profile curves and issues PWM write commands through the daemon API
- the GUI persists its own profiles, fan groups, aliases, and themes (as well as activating one of them on the daemon for headless operation)

This is the single most important build assumption in this pack.

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

## Reference note
This pack incorporates:
- the current chat decisions
- the uploaded API notes
- the recent direction around sensors, telemetry, groups, theme architecture, motherboard PWM support via daemon/API, and Linux/KDE-focused UX

## Instruction to Claude
Treat this pack as the implementation brief. Do not redesign the product fundamentals unless there is a critical technical reason. Where there are gaps, choose the most conservative, least-surprising UX and keep the architecture extensible.
