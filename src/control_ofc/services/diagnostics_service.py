"""Diagnostics service — event log, detail retrieval, support bundle export."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from control_ofc.services.app_state import AppState

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService

log = logging.getLogger(__name__)

MAX_EVENTS = 200
JOURNAL_LINE_LIMIT = 100
JOURNAL_TIMEOUT_S = 5


def format_uptime(seconds: int) -> str:
    """Format an uptime duration as a human-readable string."""
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs}h {mins}m {secs}s"
    if mins:
        return f"{mins}m {secs}s"
    return f"{secs}s"


@dataclass
class DiagEvent:
    """A timestamped diagnostic event."""

    timestamp: float
    level: str  # "info", "warning", "error"
    source: str  # "control_loop", "lease", "polling", "api", etc.
    message: str

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


class DiagnosticsService:
    """Collects diagnostic events, retrieves status detail, and exports support bundles."""

    def __init__(
        self,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
        profile_service: ProfileService | None = None,
    ) -> None:
        self._state = state
        self._settings_service = settings_service
        self._profile_service = profile_service
        self._events: deque[DiagEvent] = deque(maxlen=MAX_EVENTS)

    @property
    def events(self) -> list[DiagEvent]:
        return list(self._events)

    def log_event(self, level: str, source: str, message: str) -> None:
        event = DiagEvent(
            timestamp=time.time(),
            level=level,
            source=source,
            message=message,
        )
        self._events.append(event)
        log.log(
            {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(
                level, logging.INFO
            ),
            "[%s] %s",
            source,
            message,
        )

    def clear_events(self) -> None:
        self._events.clear()

    # ─── Detail retrieval ────────────────────────────────────────────

    def format_daemon_status(self) -> str:
        """Format the current daemon status from AppState as readable text."""
        if not self._state:
            return "No application state available."

        lines = []
        lines.append(f"Connection: {self._state.connection.value}")
        lines.append(f"Mode: {self._state.mode.value}")

        if self._state.capabilities:
            caps = self._state.capabilities
            lines.append(f"Daemon version: {caps.daemon_version}")
            lines.append(f"API version: {caps.api_version}")

        status = self._state.daemon_status
        if status:
            lines.append(f"Overall status: {status.overall_status}")
            if status.uptime_seconds is not None:
                lines.append(f"Uptime: {format_uptime(status.uptime_seconds)}")
            if status.gui_last_seen_seconds_ago is not None:
                lines.append(f"GUI last seen: {status.gui_last_seen_seconds_ago}s ago")
            for s in status.subsystems:
                age = f" (age {s.age_ms}ms)" if s.age_ms is not None else ""
                reason = f" — {s.reason}" if s.reason else ""
                lines.append(f"  {s.name}: {s.status}{age}{reason}")
            c = status.counters
            if c.last_error_summary:
                lines.append(f"Last error: {c.last_error_summary}")
        else:
            lines.append("Daemon status: not available (no response received)")

        lines.append(f"Sensors: {len(self._state.sensors)}")
        lines.append(f"Fans: {len(self._state.fans)}")
        lines.append(f"Warnings: {self._state.warning_count}")
        if self._state.active_profile_name:
            lines.append(f"Active profile: {self._state.active_profile_name}")

        lines.append("")
        lines.append("Source: GUI application state (snapshot at retrieval time)")
        return "\n".join(lines)

    def format_controller_status(self) -> str:
        """Format OpenFan controller detection and status from AppState."""
        if not self._state:
            return "No application state available."

        lines = []
        caps = self._state.capabilities
        if not caps:
            lines.append("Controller capabilities: not yet received from daemon")
            lines.append("")
            lines.append("The daemon has not responded to a capabilities request.")
            lines.append("Check that the daemon is running and reachable.")
            return "\n".join(lines)

        of = caps.openfan
        lines.append("OpenFan Controller:")
        lines.append(f"  Present: {'Yes' if of.present else 'No'}")
        if of.present:
            lines.append(f"  Channels: {of.channels}")
            lines.append(f"  Write support: {'Yes' if of.write_support else 'No'}")
            lines.append(f"  RPM support: {'Yes' if of.rpm_support else 'No'}")
        else:
            lines.append("  No OpenFan controller detected by daemon.")
            lines.append("  Check USB connection and serial device permissions.")

        hw = caps.hwmon
        lines.append("")
        lines.append("hwmon (motherboard):")
        lines.append(f"  Present: {'Yes' if hw.present else 'No'}")
        if hw.present:
            lines.append(f"  PWM headers: {hw.pwm_header_count}")
            lines.append(f"  Write support: {'Yes' if hw.write_support else 'No'}")
            lines.append(f"  Lease required: {'Yes' if hw.lease_required else 'No'}")

        # Subsystem freshness from status
        status = self._state.daemon_status
        if status:
            lines.append("")
            lines.append("Subsystem freshness:")
            for s in status.subsystems:
                age = f"age {s.age_ms}ms" if s.age_ms is not None else "no data"
                lines.append(f"  {s.name}: {s.status} ({age}) — {s.reason}")

        lines.append("")
        lines.append("Source: daemon /capabilities + /status endpoints (cached in GUI)")
        return "\n".join(lines)

    def format_gpu_status(self) -> str:
        """Format AMD GPU detection and fan state from AppState."""
        if not self._state:
            return "No application state available."

        lines = []
        caps = self._state.capabilities
        if not caps:
            lines.append("GPU capabilities: not yet received from daemon")
            return "\n".join(lines)

        gpu = caps.amd_gpu
        lines.append("AMD GPU:")
        lines.append(f"  Detected: {'Yes' if gpu.present else 'No'}")
        if gpu.present:
            lines.append(f"  Model: {gpu.model_name or 'Unknown'}")
            lines.append(f"  Display label: {gpu.display_label}")
            if gpu.pci_id:
                lines.append(f"  PCI ID: {gpu.pci_id}")
            lines.append(f"  Fan control method: {gpu.fan_control_method}")
            lines.append(f"  PMFW supported: {'Yes' if gpu.pmfw_supported else 'No'}")
            lines.append(f"  Fan RPM available: {'Yes' if gpu.fan_rpm_available else 'No'}")
            lines.append(f"  Fan write supported: {'Yes' if gpu.fan_write_supported else 'No'}")
            lines.append(f"  Discrete GPU: {'Yes' if gpu.is_discrete else 'No'}")
            lines.append(f"  Overdrive enabled: {'Yes' if gpu.overdrive_enabled else 'No'}")
            if not gpu.overdrive_enabled and not gpu.pmfw_supported:
                lines.append("")
                lines.append("  Note: PMFW fan control requires overdrive to be enabled.")
                lines.append("  Add 'amdgpu.ppfeaturemask=0xffffffff' to your kernel parameters")
                lines.append("  and reboot to enable GPU fan curve control.")
        else:
            lines.append("  No AMD discrete GPU detected by daemon.")

        # GPU fan state from fans list
        gpu_fans = [f for f in self._state.fans if f.source == "amd_gpu"]
        if gpu_fans:
            lines.append("")
            lines.append("GPU Fan State:")
            for f in gpu_fans:
                rpm = f"{f.rpm} RPM" if f.rpm is not None else "N/A"
                pwm = f"{f.last_commanded_pwm}%" if f.last_commanded_pwm is not None else "auto"
                lines.append(f"  {f.id}: {rpm}, commanded: {pwm}, age: {f.age_ms}ms")

        lines.append("")
        lines.append("Source: daemon /capabilities + /fans endpoints (cached in GUI)")
        return "\n".join(lines)

    def fetch_journal_entries(self) -> str:
        """Fetch recent control-ofc-daemon journal entries via journalctl subprocess.

        Bounded to JOURNAL_LINE_LIMIT lines with a JOURNAL_TIMEOUT_S timeout.
        Returns formatted text or an error/permission message.
        """
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    "control-ofc-daemon",
                    "--no-pager",
                    f"--lines={JOURNAL_LINE_LIMIT}",
                    "--output=short-iso",
                ],
                capture_output=True,
                text=True,
                timeout=JOURNAL_TIMEOUT_S,
            )
        except FileNotFoundError:
            return (
                "journalctl not found.\n"
                "System journal access requires systemd and the journalctl command."
            )
        except subprocess.TimeoutExpired:
            return (
                f"journalctl timed out after {JOURNAL_TIMEOUT_S}s.\n"
                "The journal query took too long. Try again later."
            )
        except OSError as e:
            return f"Failed to run journalctl: {e}"

        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if not output:
            msg = "No journal entries found for control-ofc-daemon."
            if stderr and "permission" in stderr.lower():
                msg += (
                    "\n\nInsufficient permissions to read system journal.\n"
                    "Add your user to the systemd-journal group:\n"
                    "  sudo usermod -aG systemd-journal $USER\n"
                    "Then log out and back in."
                )
            elif stderr:
                msg += f"\n\njournalctl stderr: {stderr[:200]}"
            else:
                msg += (
                    "\n\nThis may mean the service has not run recently, "
                    "or your user lacks journal read permissions."
                )
            return msg

        lines = [f"Last {JOURNAL_LINE_LIMIT} entries for control-ofc-daemon:"]
        lines.append("")
        lines.append(output)
        lines.append("")
        lines.append(f"Source: journalctl -u control-ofc-daemon (limit {JOURNAL_LINE_LIMIT} lines)")
        return "\n".join(lines)

    # ─── Support bundle ──────────────────────────────────────────────

    def export_support_bundle(self, path: Path) -> None:
        """Export a JSON support bundle for troubleshooting."""
        missing: list[str] = []
        bundle: dict = {
            "timestamp": time.time(),
            "system": {
                "platform": platform.platform(),
                "python": sys.version,
                "arch": platform.machine(),
            },
            "events": [
                {
                    "time": e.time_str,
                    "level": e.level,
                    "source": e.source,
                    "message": e.message,
                }
                for e in self._events
            ],
        }

        if self._state:
            bundle["state"] = {
                "connection": self._state.connection.value,
                "mode": self._state.mode.value,
                "sensor_count": len(self._state.sensors),
                "fan_count": len(self._state.fans),
                "warning_count": self._state.warning_count,
                "active_profile": self._state.active_profile_name,
            }
            if self._state.capabilities:
                caps = self._state.capabilities
                bundle["capabilities"] = {
                    "daemon_version": caps.daemon_version,
                    "api_version": caps.api_version,
                    "openfan_present": caps.openfan.present,
                    "openfan_channels": caps.openfan.channels,
                    "hwmon_present": caps.hwmon.present,
                    "hwmon_headers": caps.hwmon.pwm_header_count,
                }
            else:
                missing.append("capabilities: daemon not connected or not yet polled")
            if self._state.daemon_status:
                bundle["daemon_status"] = {
                    "overall": self._state.daemon_status.overall_status,
                    "subsystems": [
                        {"name": s.name, "status": s.status, "age_ms": s.age_ms}
                        for s in self._state.daemon_status.subsystems
                    ],
                }
            else:
                missing.append("daemon_status: daemon not connected or not yet polled")
            bundle["lease"] = {
                "held": self._state.lease.held,
                "lease_id": self._state.lease.lease_id,
                "ttl": self._state.lease.ttl_seconds_remaining,
                "owner": self._state.lease.owner_hint,
            }
            # Fan state snapshot (RPM + last commanded PWM for all fans)
            bundle["fan_state"] = [
                {
                    "id": f.id,
                    "source": f.source,
                    "rpm": f.rpm,
                    "last_commanded_pwm": f.last_commanded_pwm,
                    "age_ms": f.age_ms,
                }
                for f in self._state.fans
            ]
        else:
            missing.append("state: AppState not available")

        # App settings (full config for diagnosis)
        if self._settings_service and hasattr(self._settings_service, "settings"):
            settings = self._settings_service.settings
            bundle["app_settings"] = settings.to_dict()

        # Profile inventory (names + IDs, not full curve data)
        if self._profile_service and hasattr(self._profile_service, "profiles"):
            bundle["profiles"] = [
                {"id": p.id, "name": p.name, "controls": len(p.controls), "curves": len(p.curves)}
                for p in self._profile_service.profiles
            ]

        # Theme info
        if self._settings_service and hasattr(self._settings_service, "settings"):
            from control_ofc.paths import themes_dir

            theme_dir = themes_dir()
            custom_themes = []
            if theme_dir.exists():
                custom_themes = [f.stem for f in theme_dir.glob("*.json")]
            bundle["themes"] = {
                "active_theme": settings.theme_name,
                "custom_themes": custom_themes,
                "series_color_count": len(settings.series_colors),
                "fan_alias_count": len(settings.fan_aliases),
            }

        # GPU capabilities for diagnosis
        if self._state and self._state.capabilities:
            gpu = self._state.capabilities.amd_gpu
            if gpu.present:
                bundle["gpu"] = {
                    "model": gpu.model_name,
                    "display_label": gpu.display_label,
                    "pci_id": gpu.pci_id,
                    "fan_control_method": gpu.fan_control_method,
                    "pmfw_supported": gpu.pmfw_supported,
                    "overdrive_enabled": gpu.overdrive_enabled,
                }

        # System journal (daemon logs)
        journal_text = self.fetch_journal_entries()
        if journal_text:
            bundle["journal"] = journal_text
        else:
            missing.append("journal: journalctl returned no output")

        if missing:
            bundle["missing_sections"] = missing

        from control_ofc.paths import atomic_write

        atomic_write(path, json.dumps(bundle, indent=2) + "\n")
