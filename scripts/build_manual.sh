#!/usr/bin/env bash
# Build the user manual: capture fresh screenshots, then verify the output.
#
# Usage:
#   ./scripts/build_manual.sh            # with a display
#   ./scripts/build_manual.sh --headless  # under Xvfb (CI / no monitor)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/screenshots/auto"

cd "$ROOT_DIR"

echo "=== Control-OFC Manual Builder ==="
echo ""

# ── 1. Capture screenshots ─────────────────────────────────────────
echo "Step 1: Capturing screenshots..."

if [[ "${1:-}" == "--headless" ]]; then
    if ! command -v xvfb-run &>/dev/null; then
        echo "ERROR: xvfb-run not found. Install xorg-server-xvfb (Arch) or xvfb (Debian)."
        exit 1
    fi
    xvfb-run -s "-screen 0 1920x1080x24" python scripts/capture_screenshots.py
else
    python scripts/capture_screenshots.py
fi

echo ""

# ── 2. Verify screenshots ──────────────────────────────────────────
echo "Step 2: Verifying screenshots..."

EXPECTED=(
    01_dashboard 02_controls 03_settings_application
    04_diagnostics_overview 05_settings_themes 06_settings_import_export
    07_diagnostics_sensors 08_diagnostics_fans 09_diagnostics_lease
    10_diagnostics_event_log 11_about_dialog 12_fan_role_dialog_curve
    13_fan_role_dialog_manual 14_curve_edit_dialog 15_fan_wizard_intro
    16_splash_screen
)

missing=0
for name in "${EXPECTED[@]}"; do
    file="$OUTPUT_DIR/${name}.png"
    if [[ ! -f "$file" ]]; then
        echo "  MISSING: $file"
        missing=$((missing + 1))
    fi
done

count=$(find "$OUTPUT_DIR" -name "*.png" 2>/dev/null | wc -l)
echo "  Found $count screenshots ($missing missing)"

if [[ $missing -gt 0 ]]; then
    echo "WARNING: Some screenshots were not generated."
fi

# ── 3. Verify manual markdown ──────────────────────────────────────
echo ""
echo "Step 3: Verifying manual markdown..."

MANUAL_DIR="$ROOT_DIR/manual"
PAGES=(
    README.md getting-started.md dashboard.md controls.md
    settings.md diagnostics.md fan-wizard.md profiles-and-curves.md
)

md_missing=0
for page in "${PAGES[@]}"; do
    file="$MANUAL_DIR/$page"
    if [[ ! -f "$file" ]]; then
        echo "  MISSING: $file"
        md_missing=$((md_missing + 1))
    fi
done

echo "  Found ${#PAGES[@]} manual pages ($md_missing missing)"

# ── 4. Check image references ──────────────────────────────────────
echo ""
echo "Step 4: Checking image references in markdown..."

broken=0
# Extract image paths from markdown ![alt](path) using grep + sed
while IFS= read -r img_path; do
    # Skip URLs
    if [[ "$img_path" != http* ]]; then
        resolved="$MANUAL_DIR/$img_path"
        if [[ ! -f "$resolved" ]]; then
            echo "  BROKEN: $img_path"
            broken=$((broken + 1))
        fi
    fi
done < <(grep -ohP '!\[[^]]*\]\(\K[^)]+' "$MANUAL_DIR"/*.md 2>/dev/null || true)

if [[ $broken -eq 0 ]]; then
    echo "  All image references resolve correctly."
else
    echo "  WARNING: $broken broken image reference(s)."
fi

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo "=== Summary ==="
echo "  Screenshots: $count"
echo "  Manual pages: ${#PAGES[@]}"
echo "  Broken images: $broken"

if [[ $missing -eq 0 && $md_missing -eq 0 && $broken -eq 0 ]]; then
    echo ""
    echo "All good. Manual is ready at: $MANUAL_DIR/"
    exit 0
else
    echo ""
    echo "Issues detected. Review the output above."
    exit 1
fi
