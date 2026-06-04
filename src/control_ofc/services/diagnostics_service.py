"""Diagnostics service — event log, detail retrieval, support bundle export."""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.app_state import AppState

if TYPE_CHECKING:
    from control_ofc.api.models import HardwareDiagnosticsResult
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService

log = logging.getLogger(__name__)

# DEC-111: in-process session breadcrumbs are intentionally bounded — the
# system journal is the authoritative cross-restart store. 200 rows covers
# ~20 minutes of typical activity (transitions only, not per-poll noise) and
# keeps memory + filter overhead negligible.
MAX_EVENTS = 200
JOURNAL_LINE_LIMIT = 100
JOURNAL_TIMEOUT_S = 5

# DEC-098: extra system/kernel context captured in the support bundle so
# triagers can identify amdgpu-regression kernels (e.g. 6.19 RDNA hang) and
# verify boot parameters (`amdgpu.ppfeaturemask`) without asking the user
# to run extra commands.
KERNEL_LOG_LINES = 200
KERNEL_LOG_TIMEOUT_S = 5
LSMOD_TIMEOUT_S = 3
# Modules we care about for fan / GPU diagnosis. Filtering keeps the bundle
# small and focused; full lsmod output is rarely needed.
KERNEL_MODULE_FILTER = ("it87", "nct6", "amdgpu", "k10temp", "asus_ec_sensors")


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


class DiagnosticsService(QObject):
    """Collects diagnostic events, retrieves status detail, and exports support bundles.

    DEC-111: a ``QObject`` so the event-log view can subscribe to fresh events
    via Qt signals instead of polling the deque. Listeners on the main thread
    receive ``event_appended`` synchronously; cross-thread emitters get queued
    delivery automatically.
    """

    # Emitted whenever ``log_event`` appends a new entry.
    event_appended = Signal(object)  # DiagEvent
    # Emitted when ``clear_events`` is called so the view can flush its rows.
    events_cleared = Signal()

    def __init__(
        self,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
        profile_service: ProfileService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._settings_service = settings_service
        self._profile_service = profile_service
        self._events: deque[DiagEvent] = deque(maxlen=MAX_EVENTS)
        self.last_hw_diagnostics: HardwareDiagnosticsResult | None = None

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
        # Notify subscribers AFTER the deque is updated so listeners that
        # re-read ``events`` (e.g. for export-current-view) see the row.
        self.event_appended.emit(event)

    def clear_events(self) -> None:
        self._events.clear()
        self.events_cleared.emit()

    def filter_events(
        self,
        *,
        levels: set[str] | None = None,
        sources: set[str] | None = None,
        search: str = "",
    ) -> list[DiagEvent]:
        """Return events matching every supplied filter.

        ``levels`` and ``sources`` are interpreted as multi-select sets — an
        empty/``None`` set means *no level/source filter*. ``search`` is a
        case-insensitive substring match against both the message text and
        the source attribution. The result preserves insertion order so the
        view can render newest-at-bottom without re-sorting.
        """
        needle = search.strip().lower()
        result: list[DiagEvent] = []
        for ev in self._events:
            if levels and ev.level not in levels:
                continue
            if sources and ev.source not in sources:
                continue
            if needle:
                hay = f"{ev.message} {ev.source}".lower()
                if needle not in hay:
                    continue
            result.append(ev)
        return result

    def known_sources(self) -> list[str]:
        """Return the distinct event sources observed so far, sorted.

        Used by the event-log view to populate its source filter dropdown
        without prescribing a fixed source vocabulary. New emitters (added
        later) automatically appear in the dropdown the first time they fire.
        """
        return sorted({ev.source for ev in self._events})

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
            api_line = f"API version: {caps.api_version}"
            if caps.api_version != EXPECTED_API_VERSION:
                api_line += f"  [!] MISMATCH — GUI expects v{EXPECTED_API_VERSION}"
            lines.append(api_line)

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

        # Intel discrete GPU (DEC-121) — read-only monitoring.
        igpu = caps.intel_gpu
        lines.append("")
        lines.append("Intel GPU:")
        lines.append(f"  Detected: {'Yes' if igpu.present else 'No'}")
        if igpu.present:
            lines.append(f"  Model: {igpu.model_name or 'Unknown'}")
            lines.append(f"  Display label: {igpu.display_label}")
            if igpu.pci_id:
                lines.append(f"  PCI ID: {igpu.pci_id}")
            if igpu.driver:
                lines.append(f"  Driver: {igpu.driver}")
            lines.append(f"  Fan control method: {igpu.fan_control_method}")
            lines.append(f"  Fan RPM available: {'Yes' if igpu.fan_rpm_available else 'No'}")
            lines.append("  Fan write supported: No (firmware-managed, no kernel write path)")
        else:
            lines.append("  No Intel discrete GPU detected by daemon.")

        # GPU fan state from fans list (AMD + Intel discrete fans)
        gpu_fans = [f for f in self._state.fans if f.source in ("amd_gpu", "intel_gpu")]
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

    @staticmethod
    def collect_kernel_info() -> dict[str, str | None]:
        """Capture kernel release, command line, and amdgpu boot parameters.

        Best-effort: every field is independently optional. Missing files
        return ``None`` so the support bundle can record absence rather
        than failing to write.

        DEC-098: the daemon's `amd_gpu.kernel_warnings` capability surfaces
        known regressions, but the support bundle still needs the raw
        kernel string and command line so a triager who sees a *new*
        regression has the data without asking the user to run `uname`.
        """
        info: dict[str, str | None] = {
            "release": None,
            "version": None,
            "machine": None,
            "cmdline": None,
            "amdgpu_ppfeaturemask": None,
        }
        try:
            uname = os.uname()
            info["release"] = uname.release
            info["version"] = uname.version
            info["machine"] = uname.machine
        except OSError as e:
            log.debug("os.uname() failed: %s", e)

        try:
            info["cmdline"] = Path("/proc/cmdline").read_text(errors="replace").strip()
        except OSError as e:
            log.debug("read /proc/cmdline failed: %s", e)

        try:
            info["amdgpu_ppfeaturemask"] = (
                Path("/sys/module/amdgpu/parameters/ppfeaturemask")
                .read_text(errors="replace")
                .strip()
            )
        except OSError as e:
            log.debug("read amdgpu ppfeaturemask failed: %s", e)

        return info

    @staticmethod
    def collect_kernel_modules() -> str:
        """Return a filtered `lsmod` snapshot for fan / GPU drivers.

        Filters by `KERNEL_MODULE_FILTER` so the bundle stays focused.
        Returns a placeholder string on error rather than raising — the
        bundle export must remain resilient.
        """
        try:
            result = subprocess.run(
                ["lsmod"],
                capture_output=True,
                text=True,
                timeout=LSMOD_TIMEOUT_S,
            )
        except FileNotFoundError:
            return "lsmod not found (no /proc/modules access)"
        except subprocess.TimeoutExpired:
            return f"lsmod timed out after {LSMOD_TIMEOUT_S}s"
        except OSError as e:
            return f"lsmod failed: {e}"

        if result.returncode != 0:
            return f"lsmod exited {result.returncode}: {result.stderr.strip()[:200]}"

        lines = result.stdout.splitlines()
        if not lines:
            return "lsmod returned no output"
        # Keep the header line + any matching modules.
        header = lines[0]
        matches = [
            line for line in lines[1:] if any(line.startswith(mod) for mod in KERNEL_MODULE_FILTER)
        ]
        if not matches:
            return f"{header}\n(no matching modules: {', '.join(KERNEL_MODULE_FILTER)})"
        return "\n".join([header, *matches])

    @staticmethod
    def fetch_kernel_log_amdgpu() -> str:
        """Return recent `amdgpu` / `smu` kernel log lines from journalctl.

        Bounded to `KERNEL_LOG_LINES` lines. Returns a placeholder string
        on permission error so the bundle still records the attempt.
        """
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    "-k",
                    "-b",
                    "0",
                    "--no-pager",
                    f"--lines={KERNEL_LOG_LINES}",
                    "--grep=amdgpu|smu",
                ],
                capture_output=True,
                text=True,
                timeout=KERNEL_LOG_TIMEOUT_S,
            )
        except FileNotFoundError:
            return "journalctl not found"
        except subprocess.TimeoutExpired:
            return f"journalctl -k timed out after {KERNEL_LOG_TIMEOUT_S}s"
        except OSError as e:
            return f"journalctl -k failed: {e}"

        output = result.stdout.strip()
        stderr = result.stderr.strip()
        if not output:
            if stderr and "permission" in stderr.lower():
                return (
                    "journalctl -k denied (insufficient permissions). "
                    "Add your user to systemd-journal."
                )
            return "(no amdgpu/smu kernel log entries in current boot)"
        return output

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
        kernel_info = self.collect_kernel_info()
        bundle: dict = {
            "timestamp": time.time(),
            "system": {
                "platform": platform.platform(),
                "python": sys.version,
                "arch": platform.machine(),
                # DEC-098: kernel release + boot parameters so triagers
                # can identify amdgpu regressions and verify ppfeaturemask
                # without asking the user for extra commands.
                "kernel": kernel_info,
                "kernel_modules": self.collect_kernel_modules(),
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
                    "expected_api_version": EXPECTED_API_VERSION,
                    "api_version_skew": caps.api_version != EXPECTED_API_VERSION,
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

        # Hardware diagnostics (if previously fetched)
        if self.last_hw_diagnostics:
            hd = self.last_hw_diagnostics
            bundle["hardware_diagnostics"] = {
                "board": {
                    "vendor": hd.board.vendor,
                    "name": hd.board.name,
                    "bios_version": hd.board.bios_version,
                },
                "hwmon": {
                    "chips": [c.chip_name for c in hd.hwmon.chips_detected],
                    "total_headers": hd.hwmon.total_headers,
                    "writable_headers": hd.hwmon.writable_headers,
                    "enable_revert_counts": hd.hwmon.enable_revert_counts,
                },
            }

        # System journal (daemon logs)
        journal_text = self.fetch_journal_entries()
        if journal_text:
            bundle["journal"] = journal_text
        else:
            missing.append("journal: journalctl returned no output")

        # DEC-098: kernel ring-buffer entries scoped to amdgpu/smu so a
        # silent fan_curve write failure (R9700 SMU mismatch) leaves
        # forensic evidence in the bundle.
        kernel_log = self.fetch_kernel_log_amdgpu()
        if kernel_log:
            bundle["kernel_log_amdgpu"] = kernel_log

        if missing:
            bundle["missing_sections"] = missing

        from control_ofc.paths import atomic_write

        atomic_write(path, json.dumps(bundle, indent=2) + "\n")
