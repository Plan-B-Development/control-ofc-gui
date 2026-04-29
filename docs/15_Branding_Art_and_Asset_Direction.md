# 15 — Branding, Art, and Asset Direction

## Purpose
Define the visual direction for Control-OFC: icon, typography, and palette.
The working app should feel like a proper Linux desktop utility — restrained,
readable, and professional.

## Where the assets live
The working app ships its derived assets from the repo-level `assets/branding/`
tree (sibling of `src/`, loaded at runtime by
`src/control_ofc/ui/branding.py`):

```text
assets/branding/
  app_icon/   # application icon set (SVG)
```

`src/control_ofc/ui/branding.py` is the runtime entry point; it currently
exposes a single helper, `load_app_icon()`, which loads the SVG icon from
`assets/branding/app_icon/app_icon.svg`.

## Tone
The branding should feel:
- restrained
- modern
- competent
- legible at any size

The product should not feel:
- decorative
- neon-chaotic
- cluttered
- unserious in operational screens

## App icon
A simplified, square or rounded-square mark with a stylised fan element. The
icon must:
- read clearly at small sizes (sidebar, taskbar, system tray)
- contain no embedded text
- work on both light and dark backgrounds
- ship as a vector (`assets/branding/app_icon/app_icon.svg`)

## Typography
- System default sans-serif by default
- Base font size: 10pt
- User-configurable font family and base size from Settings → Themes
- No decorative or script faces in operational UI

## Palette direction
- background: near-black charcoal
- panel surface: deep grey-blue
- primary accent: vivid mid-blue
- secondary accent: lighter icy blue
- text primary: cool off-white
- text secondary: muted grey-blue
- warning: amber
- critical: red
- success: restrained green

The default theme (`Default Dark`) is the canonical reference; custom themes
can override individual tokens via the Settings → Themes editor.

## Avoid
- decorative banners or images embedded in operational pages
- heavy gradients in tables, forms, or charts
- coloured overlays that reduce chart readability
- typography or imagery that fights with the primary accent

## Practical instruction to Claude
Keep the working interface restrained, dark, and readable. Visual weight
should follow data — sensors, fans, profiles, diagnostics — not branding.
