"""Microcopy — playful vs professional text substitutions.

All cheeky/parody text lives here. When fun_mode is off, the professional
variant is used instead. No scattered strings in widget code.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Map of key -> (fun_text, professional_text)
_COPY: dict[str, tuple[str, str]] = {
    "splash_status_init": ("Warming up the fans...", "Initialising..."),
    "splash_status_connecting": ("Sliding into daemon's DMs...", "Connecting to daemon..."),
    "splash_status_loading": ("Loading your exclusive content...", "Loading sensors..."),
    "splash_status_ready": ("Cooling content delivered.", "Ready"),
    "dashboard_empty_title": ("Nothing to see here... yet", "No Hardware Detected"),
    "dashboard_fans_working": ("Fans are working hard.", "Fans active"),
    "status_connected": ("Connected. Let's get cooling.", "Connected"),
    "status_disconnected": ("Ghosted by the daemon.", "Disconnected"),
    "status_demo": ("Demo mode. Just browsing.", "Demo mode"),
    "about_tagline": ("Not just... idle.", "Fan control for Linux"),
    "about_credits": ("A cheeky fan-control project", "Open-source fan control"),
    "profile_quiet": ("Keeping it on the down-low", "Low noise, gentle ramp"),
    "profile_performance": ("Full blast. No regrets.", "Maximum cooling"),
    "override_active": ("Going manual. You're in control.", "Manual override is active"),
    "save_success": ("Saved. Your fans remember.", "Settings saved"),
}

_fun_mode: bool = True


def set_fun_mode(enabled: bool) -> None:
    global _fun_mode
    _fun_mode = enabled


def is_fun_mode() -> bool:
    return _fun_mode


def get(key: str) -> str:
    """Get the appropriate microcopy for the current mode."""
    if key not in _COPY:
        log.warning("Unknown microcopy key: %r — returning raw key", key)
        return key
    fun, pro = _COPY[key]
    return fun if _fun_mode else pro
