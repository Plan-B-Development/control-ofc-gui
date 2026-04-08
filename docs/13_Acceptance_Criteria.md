# 13 — Acceptance Criteria

## Global acceptance criteria

### Architecture
- [ ] GUI never talks directly to hardware
- [ ] All hardware interaction goes through daemon/API
- [ ] Demo mode runs without daemon/hardware
- [ ] App runs as a desktop window on Linux
- [ ] Dark theme is the default

### Navigation
- [ ] App has a left sidebar with Dashboard, Controls, Settings, Diagnostics
- [ ] Header/status strip clearly shows connection/control state
- [ ] Disconnected and demo states are obvious

### Dashboard
- [ ] Launch shows fan labels, RPM, active profile, and fan-speed graph
- [ ] User can switch chart time ranges from 30s to 2h
- [ ] User can hide/show individual fan series
- [ ] Stale sensors/fans are visibly marked

### Controls
- [ ] Built-in Quiet, Balanced, Performance profiles exist
- [ ] Only one profile is active at a time
- [ ] Unsaved edits are clearly shown
- [ ] New curve defaults to 5 points
- [ ] Curves edit in % output
- [ ] Single-sensor selection works
- [ ] User can create and manage fan groups
- [ ] A fan can belong to multiple groups
- [ ] Manual override exists and has a clear Return to Automatic action
- [ ] Curve and Fan Role cards use fixed sizing (220×160 / 260×180)
- [ ] Cards use FlowLayout with responsive wrapping
- [ ] Cards support drag-to-reorder with visual drop indicator
- [ ] New cards append to end of section
- [ ] Cards do not stack, duplicate, or lose order across refreshes
- [ ] Profile activation calls daemon API — GUI does not falsely mark active on failure
- [ ] Vertical splitter between Fan Roles and Curves sections
- [ ] Editing a curve updates that curve card's mini-preview immediately
- [ ] Curve B's preview does not change when curve A is edited
- [ ] Each curve persists its own sensor_id across save/reload
- [ ] Each curve persists its own points/graph across save/reload
- [ ] Two curves may reference the same sensor without sharing graph state
- [ ] Controls page text size follows the theme text-size setting
- [ ] Card metadata text uses CardMeta class (small role), not PageSubtitle
- [ ] Card text is consistent with the rest of the application at all theme text sizes
- [ ] Fan Role buttons have comfortable padding for larger theme text
- [ ] Opening curve CPU (sensor=tctl) then curve GPU (sensor=edge) shows each curve's own sensor
- [ ] Switching between curves never inherits the previous curve's sensor selection
- [ ] get_curve().sensor_id always matches the currently loaded curve

### Automatic control
- [ ] Active profile drives writes through daemon/API
- [ ] GUI control loop works without direct hardware access
- [ ] OpenFan writes function through API
- [ ] Hwmon writes respect lease requirements
- [ ] Lease failures are visible and do not appear silently successful
- [ ] 2°C hysteresis deadband prevents fan oscillation near curve inflection points

### Settings
- [ ] Theme import/export works
- [ ] GUI settings import/export works
- [ ] Settings clearly separate GUI-owned vs daemon-runtime settings

### Diagnostics
- [ ] Diagnostics shows daemon/API status
- [ ] Diagnostics shows sensor health
- [ ] Diagnostics shows controller/device discovery
- [ ] Diagnostics shows lease state with user-facing explanation
- [ ] Diagnostics offers reload, reconnect, export support bundle, and copy last errors
- [ ] Export support bundle produces a structured output
- [ ] Subsystem age_ms values include reason text and explanatory note (R34)
- [ ] Daemon uptime displayed when available (R34)
- [ ] Event log provides daemon status, controller status, and system journal retrieval (R34)
- [ ] Each detail block is labeled with its source (R34)
- [ ] Journal retrieval is bounded and handles permission errors truthfully (R34)
- [ ] Diagnostics labels use transparent backgrounds and theme CSS classes (R34)
- [ ] No hardcoded font-size pixel values on Diagnostics page (R34)

### Demo mode
- [ ] Demo mode is available without hardware
- [ ] Demo mode clearly indicates synthetic data
- [ ] Demo mode supports profile switching, charting, and warnings

## UX acceptance criteria
- [ ] App feels readable in dark theme
- [ ] Warning and error states are visually distinct
- [ ] Controls are understandable without reading a manual first
- [ ] Empty states look intentional
- [ ] The parody branding is present but does not compromise usability

## Non-acceptance criteria for V1
The following are explicitly not required for V1 acceptance:
- tray-first workflow
- full theme editor
- multi-sensor curve logic
- user-custom dashboard layout
- daemon-side profile storage
- AIO-specific advanced support UI
