#!/usr/bin/env python3
"""Capture screenshots of every page, tab, and dialog in demo mode.

Usage:
    # With a display (X11/Wayland):
    python scripts/capture_screenshots.py

    # Headless (CI / no monitor):
    xvfb-run -s "-screen 0 1920x1080x24" python scripts/capture_screenshots.py

All PNGs are saved to screenshots/auto/ with semantic filenames.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# Ensure the project source is importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

logging.basicConfig(level=logging.WARNING)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "screenshots" / "auto"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def process_events(cycles: int = 20, per_ms: int = 50) -> None:
    """Pump the Qt event loop to let layouts, timers, and paints settle."""
    for _ in range(cycles):
        QApplication.processEvents()
        time.sleep(per_ms / 1000)


def grab(widget, name: str) -> Path:
    """Grab a widget as PNG and return the saved path."""
    QApplication.processEvents()
    pixmap = widget.grab()
    path = OUTPUT_DIR / f"{name}.png"
    pixmap.save(str(path), "PNG")
    print(f"  [{pixmap.width()}x{pixmap.height()}] {path.name}")
    return path


# ---------------------------------------------------------------------------
# Demo profile with rich content (controls + curves of every type)
# ---------------------------------------------------------------------------


def create_demo_profile():
    """Build a profile with multiple fan roles and all three curve types."""
    from control_ofc.services.profile_service import (
        ControlMember,
        ControlMode,
        CurveConfig,
        CurvePoint,
        CurveType,
        LogicalControl,
        Profile,
    )

    # Curve: freeform graph
    case_curve = CurveConfig(
        id="case_curve",
        name="Case Fan Curve",
        type=CurveType.GRAPH,
        sensor_id="hwmon:k10temp:Tctl",
        points=[
            CurvePoint(temp_c=30, output_pct=25),
            CurvePoint(temp_c=45, output_pct=35),
            CurvePoint(temp_c=60, output_pct=55),
            CurvePoint(temp_c=75, output_pct=80),
            CurvePoint(temp_c=85, output_pct=100),
        ],
    )

    # Curve: linear ramp
    cpu_curve = CurveConfig(
        id="cpu_curve",
        name="CPU Ramp",
        type=CurveType.LINEAR,
        sensor_id="hwmon:k10temp:Tctl",
        start_temp_c=35,
        start_output_pct=30,
        end_temp_c=80,
        end_output_pct=100,
    )

    # Curve: flat
    pump_curve = CurveConfig(
        id="pump_curve",
        name="Pump Fixed",
        type=CurveType.FLAT,
        sensor_id="",
        flat_output_pct=65,
    )

    return Profile(
        id="demo_showcase",
        name="Demo Showcase",
        description="Demonstrates all curve types and fan groupings",
        curves=[case_curve, cpu_curve, pump_curve],
        controls=[
            LogicalControl(
                id="intake_role",
                name="Case Intake",
                mode=ControlMode.CURVE,
                curve_id="case_curve",
                members=[
                    ControlMember(source="openfan", member_id="openfan:ch00", member_label="Front Intake 1"),
                    ControlMember(source="openfan", member_id="openfan:ch01", member_label="Front Intake 2"),
                    ControlMember(source="openfan", member_id="openfan:ch05", member_label="GPU Adjacent Intake"),
                ],
            ),
            LogicalControl(
                id="exhaust_role",
                name="Case Exhaust",
                mode=ControlMode.CURVE,
                curve_id="case_curve",
                members=[
                    ControlMember(source="openfan", member_id="openfan:ch02", member_label="Rear Exhaust"),
                    ControlMember(source="openfan", member_id="openfan:ch03", member_label="Top Exhaust 1"),
                    ControlMember(source="openfan", member_id="openfan:ch04", member_label="Top Exhaust 2"),
                ],
            ),
            LogicalControl(
                id="cpu_role",
                name="CPU Cooler",
                mode=ControlMode.CURVE,
                curve_id="cpu_curve",
                members=[
                    ControlMember(source="hwmon", member_id="hwmon:it8696:pci0:pwm1:CHA_FAN1", member_label="CPU Fan"),
                ],
            ),
            LogicalControl(
                id="pump_role",
                name="Pump / AIO",
                mode=ControlMode.CURVE,
                curve_id="pump_curve",
                members=[
                    ControlMember(source="hwmon", member_id="hwmon:it8696:pci0:pwm3:CHA_FAN3", member_label="CPU OPT / Pump"),
                ],
            ),
            LogicalControl(
                id="rad_role",
                name="Radiator Push",
                mode=ControlMode.MANUAL,
                curve_id="",
                manual_output_pct=70,
                members=[
                    ControlMember(source="openfan", member_id="openfan:ch06", member_label="Radiator Push 1"),
                    ControlMember(source="openfan", member_id="openfan:ch07", member_label="Radiator Push 2"),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Main capture sequence
# ---------------------------------------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")

    app = QApplication(sys.argv)
    app.setApplicationName("Control-OFC")

    # Apply theme
    from control_ofc.ui.theme import apply_theme_font, build_stylesheet, default_dark_theme

    theme = default_dark_theme()
    app.setStyleSheet(build_stylesheet(theme))
    apply_theme_font(theme)

    # Set up services exactly like main.py does for demo mode
    from control_ofc.paths import ensure_dirs
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState
    from control_ofc.services.history_store import HistoryStore
    from control_ofc.services.profile_service import ProfileService
    from control_ofc.ui.microcopy import set_fun_mode

    ensure_dirs()
    settings_service = AppSettingsService()
    settings_service.load()
    set_fun_mode(True)

    state = AppState()
    history = HistoryStore()
    profile_service = ProfileService()
    profile_service.load()

    # Inject our demo showcase profile
    demo_profile = create_demo_profile()
    profile_service._profiles[demo_profile.id] = demo_profile
    profile_service.set_active(demo_profile.id)

    # Wire history recording
    state.sensors_updated.connect(history.record_sensors)
    state.fans_updated.connect(history.record_fans)

    # Build main window in demo mode
    from control_ofc.ui.main_window import MainWindow

    window = MainWindow(
        state=state,
        history=history,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=True,
    )
    window.resize(1400, 850)
    window.show()

    # Let demo data flow for several ticks so charts and tables populate
    print("\nWaiting for demo data to populate...")
    process_events(cycles=60, per_ms=100)

    # ─── 1. Main pages ──────────────────────────────────────────────
    print("\n=== Main Pages ===")

    from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, PAGE_DIAGNOSTICS, PAGE_SETTINGS

    pages = [
        (PAGE_DASHBOARD, "01_dashboard"),
        (PAGE_CONTROLS, "02_controls"),
        (PAGE_SETTINGS, "03_settings_application"),
        (PAGE_DIAGNOSTICS, "04_diagnostics_overview"),
    ]

    for page_id, name in pages:
        window.sidebar.select_page(page_id)
        process_events(cycles=10)
        grab(window, name)

    # ─── 2. Settings tabs ───────────────────────────────────────────
    print("\n=== Settings Tabs ===")
    window.sidebar.select_page(PAGE_SETTINGS)
    process_events(cycles=5)

    # Tab 0 already captured as 03_settings_application
    # Tab 1: Themes
    window.settings_page._tabs.setCurrentIndex(1)
    process_events(cycles=5)
    grab(window, "05_settings_themes")

    # Tab 2: Import/Export
    window.settings_page._tabs.setCurrentIndex(2)
    process_events(cycles=5)
    grab(window, "06_settings_import_export")

    # ─── 3. Diagnostics tabs ───────────────────────────────────────
    print("\n=== Diagnostics Tabs ===")
    window.sidebar.select_page(PAGE_DIAGNOSTICS)
    process_events(cycles=5)

    # Tab 0 already captured as 04_diagnostics_overview
    # Tab 1: Sensors
    window.diagnostics_page._tabs.setCurrentIndex(1)
    process_events(cycles=5)
    grab(window, "07_diagnostics_sensors")

    # Tab 2: Fans
    window.diagnostics_page._tabs.setCurrentIndex(2)
    process_events(cycles=5)
    grab(window, "08_diagnostics_fans")

    # Tab 3: Lease
    window.diagnostics_page._tabs.setCurrentIndex(3)
    process_events(cycles=5)
    grab(window, "09_diagnostics_lease")

    # Tab 4: Event Log
    window.diagnostics_page._tabs.setCurrentIndex(4)
    process_events(cycles=5)
    grab(window, "10_diagnostics_event_log")

    # ─── 4. Dialogs ────────────────────────────────────────────────
    print("\n=== Dialogs ===")

    # About dialog
    from control_ofc.ui.about_dialog import AboutDialog

    about = AboutDialog(window)
    about.show()
    process_events(cycles=5)
    grab(about, "11_about_dialog")
    about.close()

    # Fan Role dialog (populated with data from our demo profile)
    from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog

    intake_role = demo_profile.controls[0]  # "Case Intake" with 3 members, curve mode
    role_dlg = FanRoleDialog(intake_role, demo_profile.curves, parent=window)
    role_dlg.show()
    process_events(cycles=5)
    grab(role_dlg, "12_fan_role_dialog_curve")
    role_dlg.close()

    # Fan Role dialog in Manual mode
    rad_role = demo_profile.controls[4]  # "Radiator Push", manual mode
    role_dlg_manual = FanRoleDialog(rad_role, demo_profile.curves, parent=window)
    role_dlg_manual.show()
    process_events(cycles=5)
    grab(role_dlg_manual, "13_fan_role_dialog_manual")
    role_dlg_manual.close()

    # Curve Edit dialog (linear)
    from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

    cpu_curve = demo_profile.curves[1]  # "CPU Ramp" linear
    sensor_items = [(s.id, s.label or s.id) for s in state.sensors]
    try:
        curve_dlg = CurveEditDialog(
            cpu_curve, sensor_items=sensor_items, parent=window
        )
        curve_dlg.show()
        process_events(cycles=5)
        grab(curve_dlg, "14_curve_edit_dialog")
        curve_dlg.close()
    except Exception as e:
        print(f"  Skipped curve edit dialog: {e}")

    # Fan Wizard — capture the intro page only (safe, no fan writes)
    from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

    try:
        wizard = FanConfigWizard(
            state=state,
            client=None,
            control_loop=window._control_loop,
            spindown_seconds=8,
        )
        wizard.show()
        process_events(cycles=5)
        grab(wizard, "15_fan_wizard_intro")
        wizard.close()
    except Exception as e:
        print(f"  Skipped fan wizard: {e}")

    # ─── 5. Splash screen ─────────────────────────────────────────
    print("\n=== Splash Screen ===")
    from control_ofc.ui.splash import AppSplashScreen

    try:
        splash = AppSplashScreen()
        splash.show()
        process_events(cycles=5)
        grab(splash, "16_splash_screen")
        splash.close()
    except Exception as e:
        print(f"  Skipped splash screen: {e}")

    # ─── Done ──────────────────────────────────────────────────────
    window.close()
    app.quit()

    count = len(list(OUTPUT_DIR.glob("*.png")))
    print(f"\nDone. {count} screenshots saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
