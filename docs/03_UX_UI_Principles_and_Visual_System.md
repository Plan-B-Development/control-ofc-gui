# 03 — UX/UI Principles and Visual System

**Status:** Living spec, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) is the authoritative release-by-release record and wins where this document disagrees with it.

## Design philosophy
The product is a technical utility. The operational screens must feel:
- dark
- clean
- fast
- legible
- restrained — visual personality must never reduce clarity

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
- debugging and support → the system-health pages (Overview / System State / Hardware / Logs)

### 4. State must be obvious
The user should never need to guess whether the app is:
- connected
- disconnected
- in demo mode
- in manual override
- read-only
- under daemon thermal protection
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
- Overview
- Controls
- System State
- Hardware
- Settings
- Theme
- Logs

### Header ribbon and footer
A lightweight, always-visible status region should expose
(split across the top ribbon and the bottom footer since DEC-222):
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
**Project policy (DEC-109): shipped themes must pass WCAG 2.1 AA on every
contrast pair `check_contrast_warnings()` evaluates.** The thresholds:

- **4.5:1** — body text (< 18 pt regular, < 14 pt bold). Applies to
  `text_primary` on every meaningful surface, `input_text` on `input_bg`,
  `table_text` on `table_row_bg` / `table_row_alt_bg`, and `primary_btn_text`
  on `accent_primary` and `accent_secondary` (hover).
- **3:1** — large text, focus indicators, chart axis labels, and
  non-text UI components. Applies to `text_muted` on cards,
  `text_secondary` on cards, `nav_text_active` on `nav_item_active`,
  `chart_axis_text` on `chart_bg`, and `input_placeholder` on `input_bg`.
  **Focus indicators are enforced separately** (DEC-264) — by
  `tests/test_theme_system.py::TestKeyboardFocusContrast`, which measures every
  `:focus` ring in the built stylesheet against the surface it is drawn on, for
  every shipped theme. `check_contrast_warnings()` does **not** evaluate any
  focus pair, so the Theme Editor will not warn a user authoring their own theme
  about a low-contrast ring. That is a known gap, not an oversight.
- **Disabled controls are exempt** — WCAG itself exempts them, so
  `disabled_text` / `disabled_bg` is not enforced.

Important labels, controls, and meaningful chart elements must remain readable and distinguishable.

### Keyboard focus and accessible names (DEC-251)
**Every interactive control shows a visible focus indicator (WCAG 2.4.7).** This is not
automatic: applying a stylesheet makes Qt drop its native focus rect, so a widget
without an explicit `:focus` rule is invisible to a keyboard user. Two colours, split
by role:

- **Buttons** use a `text_primary` ring — deliberately not the accent, which is this
  app's primary-action language (see DEC-238), and deliberately distinct from
  `[variant="danger"]:hover`, which already swaps its border to `status_crit`. Focus
  and hover must not look alike.
- **Inputs** (`QLineEdit`, `QSpinBox`, `QComboBox`) use `input_border_focus`.
- **Accent-filled controls** (`[variant="primary"]`, `#PrimaryButton`, and the
  `QSlider` handle) ring in `primary_btn_text`, the token that contrasts against
  that fill by construction. The slider was the exception until DEC-264 — it rang
  in `text_primary`, which is chosen to contrast with the *page*, and measured
  1.65:1 against its own accent fill in the default theme. Qt draws a border
  inside the widget rect, so for a filled control the fill is the adjacent
  surface; a ring picked for the page has no reason to be legible on it.
- **Swatches of arbitrary colour** (`ColorSwatch` in the Theme Editor) cannot use
  a fixed token at all — the surface *is* the token being edited, and a
  `text_primary` ring on the `text_primary` swatch is 1.00:1. They pick whichever
  of **black or white** contrasts better with the swatch, so the worst case is
  the crossover grey at **4.61:1** (4.58:1 for an arbitrary colour at that same
  luminance, which a user can type into the picker).

  It must be black/white specifically, not a pair of theme tokens. Picking the
  better of `text_primary` / `primary_btn_text` was tried first and is only as
  good as those two being a genuine light/dark pair — which is a claim about
  values a user can edit. It held in three shipped themes and failed in the
  fourth: Classic Blue's are `#e0e0e8` and `#ffffff`, 1.31:1 apart, leaving 18 of
  54 swatches under 3:1 including the `text_primary` swatch the rule was written
  for. Black and white need no such assumption (DEC-266).

Two constraints, both measured rather than assumed:

- **The ring must not resize the control.** Where a border already exists, swap its
  colour; where it does not (`border: none`), declare the border only in the `:focus`
  state. Adding a transparent border to the *resting* rule instead grows the widget by
  2 px in both axes.
- **`QCheckBox` is styled only in `:focus`.** Any resting rule makes Qt replace the
  native indicator with the stylesheet's own, repainting and shrinking it — the same
  subcontrol trap as `QComboBox::drop-down`. The resting appearance is untouched; the
  focused one shifts its content right by the border width (~2 px), which is a repaint,
  not a reflow — neighbouring widgets do not move.
- **For a subcontrol, the pseudo-state goes last.** `QSlider::handle:horizontal:focus`
  renders; `QSlider:focus::handle:horizontal` is silently inert.
- **Some widgets cannot be reached by QSS at all.** A widget with its own
  `setStyleSheet` outranks the application stylesheet by *origin*, not specificity
  (`ColorSwatch`), and an owner-drawn `paintEvent` never consults the style at all
  (`ToggleSwitch`). Both need their focus ring written where they are drawn.

**A tooltip is not an accessible name.** Qt does not expose it as one and a
keyboard-only screen-reader user never triggers it, so any control whose visible label
is empty or a bare glyph sets `setAccessibleName` explicitly.

**"Empty or a bare glyph" is the floor, not the whole rule.** A combo box, spin box
or line edit has no label of its own either — its visible words live in a *separate*
`QLabel` beside it, so it announces its value with nothing to say what the value is
*for*. Every such control must name itself from the words already next to it.

**Settings is the only page that does so today.** The rule above is the standard for
new and edited code, not a description of the app as it stands: combo boxes, spin
boxes and line edits on the theme, curve-editor, logs, wizard and dialog surfaces are
still unnamed, and the enforcing test is scoped to `SettingsPage`. Treat an
app-wide compliance claim as false until those surfaces are swept and the test
widened.

On Settings the naming happens inside `_setting_row` — the place holding both the
control and its caption — so a control added *through that helper* is named by
construction. Two controls do not go through it: the CPU and motherboard preference
combos are stacked under their captions rather than beside them and are named at
their own construction sites. So "by construction" covers the common path, not every
path; the test is what actually holds the line (DEC-268 → DEC-271).

**Naming a combo box takes two calls, not one.** `setAccessibleName` is enough for a
spin box or a line edit — `QAccessible::Text::Name` and `::Value` are separate
queries, so the name is added to the announcement rather than replacing the value.
It is **not** enough for a non-editable `QComboBox`: Qt's Unix
`QAccessibleComboBox::text(Name)` falls through to the current item and discards the
property entirely, so the combo goes on announcing "Dashboard". `setBuddy` is what
carries the label there — it publishes a `RelationFlag.Label` that AT-SPI exports as
*labelled-by*, which is what Orca reads. Set both: the name for platforms that honour
it, the buddy for the one this app ships to.

This is why the tests assert the **announced** name (`QAccessible` interface) for
combo boxes, spin boxes and line edits. The property reads back perfectly on a combo
that announces nothing useful, so a property assertion there is a guaranteed false
green — it shipped as one once (DEC-271).

For `QPushButton` the property assertion is correct and the suite uses it: Qt honours
`setAccessibleName` on a button, so `widget.accessibleName()` and the announced name
agree. The rule is "assert what is announced where the two can diverge", not "never
read the property".

Both rules are enforced by rendering, not by grepping the stylesheet: a `:focus` rule
can be present and still draw nothing (an accent ring on an accent fill). See
`tests/test_theme_system.py::TestKeyboardFocusVisibility`.

The Theme Editor's "Contrast Warnings" panel lists any pair that falls
below its threshold. A theme that produces no warnings can be considered
AA-compliant for the pairs the GUI cares about.

### Chart readability rules
- always show a legend or direct labels for visible series
- allow hiding/showing series cleanly
- preserve contrast between lines and background
- do not use too many saturated colors at once
- keep gridlines subtle

## Brand application
Branding is intentionally minimal:
- a single app icon (rendered in the sidebar and About dialog)
- text-only application name in the sidebar header
- dark theme with restrained accent colours
- no marketing imagery, no banners, no decorative graphics behind workflow screens

The product is a technical utility. Visual identity should not draw attention away from operational state.

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

## Status and error text examples
Good:
- "Daemon unreachable"
- "Manual override is active"
- "Thermal protection active"
- "Sensor update is stale"
- "Profile has unsaved changes"

Bad:
- "Something went wrong"
- "Error!"
- "Status unknown maybe"
