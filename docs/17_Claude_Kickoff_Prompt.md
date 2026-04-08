# 17 — Claude Kickoff Prompt

Use the following as the initial build prompt for Claude after placing the pack in the project folder:

---

Read the documentation pack in this folder, starting with `00_README_START_HERE.md`, then implement the V1 desktop GUI for **Control-OFC**.

Rules:
1. Do not redesign the product fundamentals unless there is a critical technical reason.
2. The GUI must **only** talk to the daemon/API.
3. The GUI must **never** talk directly to hardware.
4. Build for Linux first, especially CachyOS/Arch + KDE.
5. V1 is desktop-window first, not tray-first.
6. Default to a dark theme.
7. Include demo mode early so the app is usable without hardware.
8. Keep the architecture clean, boring, and extensible.
9. Keep business logic out of widgets.
10. Implement the GUI-owned control loop as described, since the daemon is currently imperative and lacks fan-curve/profile ownership.

Implementation expectations:
- Python + PySide6
- Live charts suitable for telemetry/fan RPM timelines
- Left sidebar with Dashboard / Controls / Settings / Diagnostics
- Clear header/status strip
- Profile switching, fan grouping, curve editing, manual override
- Settings with theme import/export and supported telemetry runtime options
- Diagnostics with support bundle export
- Respect daemon capabilities, limits, and lease requirements
- Fail clearly and gracefully

Before writing large amounts of code:
- propose the module structure
- list assumptions
- identify any API ambiguities
- then begin implementation in phases

If there is uncertainty:
- choose the safest and least surprising UX
- preserve future extensibility
- do not invent hardware access outside the daemon

---
