# 03 — UX/UI Principles and Visual System

## Design philosophy
The product is a technical utility with parody branding. The operational screens must feel:
- dark
- clean
- fast
- legible
- mildly playful, not chaotic

The branding should add personality. It should not reduce clarity.

## Core UX principles

### 1. At-a-glance first
The first screen should answer:
- what profile is active?
- are fans spinning as expected?
- are temperatures normal?
- is the daemon healthy?
- is anything broken right now?

### 2. Progressive disclosure
Keep the default UI simple. Advanced controls should appear only when needed.

Examples:
- curve editor opens simple by default
- advanced options are collapsed
- diagnostic detail can be expanded
- error detail is available on demand, not always shown inline

### 3. Recognisable structure
Users should not have to remember hidden locations. Important tasks should have predictable homes:
- overview → Dashboard
- control and profile tasks → Controls
- app/runtime configuration → Settings
- debugging and support → Diagnostics

### 4. State must be obvious
The user should never need to guess whether the app is:
- connected
- disconnected
- in demo mode
- in manual override
- read-only
- holding a hwmon lease
- using an unsaved edited profile

### 5. Color is supportive, not the only signal
Warnings and critical states should use:
- color
- icon
- label
- optionally tooltip/detail text

Never rely on color alone.

## Layout and navigation

### Global layout
Use a classic desktop shell:
- left navigation sidebar
- header/status region
- central content area
- contextual page toolbar if needed

### Sidebar items
- Dashboard
- Controls
- Settings
- Diagnostics

### Header/status strip
A lightweight, always-visible status region should expose:
- connection state
- active profile
- control mode
- warning count
- demo mode badge when relevant

## Visual hierarchy
Prioritise:
1. current profile and control mode
2. warnings and faults
3. current temperatures and fan status
4. trend charts
5. detailed controls
6. low-level diagnostics text

## Density
This is not a kiosk dashboard. It is a desktop utility.
Use moderate density:
- enough information for power users
- enough spacing to remain readable
- no giant toy-like controls
- no spreadsheet-like clutter on default screens

## Typography
Use the system/Qt defaults unless a clear reason exists not to.
Tone of copy:
- short
- clear
- plain language
- technically accurate
- not over-explained

## Dark theme direction
Default to a dark theme in V1.

### Mood
- charcoal and near-black surfaces
- vivid but restrained blue accenting from the logo
- neutral grey text hierarchy
- amber/orange warnings
- red criticals
- green success used sparingly

## Theme system strategy
Do **not** build a giant freeform color editor in V1.

Instead:
- build a token-based theme system now
- expose import/export now
- expose full advanced editing later

### Theme token groups
At minimum define tokens for:
- window background
- panel background
- raised surface
- border/subtle separator
- text primary
- text secondary
- text muted
- accent primary
- accent secondary
- success
- warning
- critical
- focus ring
- selection
- disabled foreground
- disabled surface
- chart grid
- chart axis
- chart series 1..n
- dashboard card states
- manual override highlight
- demo mode highlight

## Accessibility and readability rules
Aim for strong contrast and legibility in dark mode.
Important labels, controls, and meaningful chart elements must remain readable and distinguishable.

### Chart readability rules
- always show a legend or direct labels for visible series
- allow hiding/showing series cleanly
- preserve contrast between lines and background
- do not use too many saturated colors at once
- keep gridlines subtle

## Brand application
Use the attached parody image as inspiration for:
- app icon
- splash/loading screen
- about page
- empty/disconnected/demo illustrations

Do not put a bright full-banner graphic behind every workflow screen.

## Page-level consistency
Every page should follow the same structural rhythm:
- page title
- short descriptive subtitle or helper text when needed
- action row
- main content
- error/warning banners if relevant

## Interaction consistency
Buttons and actions should follow clear intent labels:
- Apply
- Save
- Reset
- Reconnect
- Reload
- Export
- Import
- Return to Automatic

Avoid ambiguous labels like:
- Do It
- Push
- Run Thing
- Retry Maybe

## Empty states
Build intentional empty states for:
- no daemon reachable
- demo mode not started
- no fans discovered
- no sensors available
- no saved profiles yet
- diagnostics log empty

Each empty state should:
- explain what is happening
- explain what the user can do next
- avoid looking like a crash

## Microcopy examples
Good:
- "Daemon unreachable"
- "Manual override is active"
- "Lease held by another controller"
- "Sensor update is stale"
- "Profile has unsaved changes"

Bad:
- "Something went wrong"
- "Error!"
- "Status unknown maybe"
