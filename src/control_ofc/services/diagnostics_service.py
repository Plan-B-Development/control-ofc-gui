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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.alerts import transition_to_fields, transition_to_log
from control_ofc.services.app_state import AppState

if TYPE_CHECKING:
    from control_ofc.api.models import HardwareDiagnosticsResult
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService

log = logging.getLogger(__name__)

# Pure-UI/rendering settings with no diagnostic value, stripped from the support
# bundle. This is a SUBSET of AppSettings.MACHINE_SPECIFIC_KEYS (which portable
# export uses): the bundle deliberately KEEPS the diagnostically load-bearing
# machine-specific keys (sensor_class_overrides, diagnostics_hidden_sensor_ids,
# acknowledged_kernel_warnings, and the profiles/themes dir overrides) — those
# are exactly what reveals a misconfiguration in a troubleshooting bundle. Only
# genuine window/layout state is dropped.
_BUNDLE_EXCLUDED_SETTING_KEYS = frozenset(
    {
        "window_geometry",
        "last_page_index",
        "controls_card_sizes",
        "series_colors",
        "export_default_dir",
        "daemon_import_prompted",
        # DEC-245 view state. `splitter_sizes` is exactly the "genuine window/layout
        # state" this set exists to drop, and `logs_search_text` is free text the
        # user typed — neither belongs in a bundle they may hand to someone else.
        "splitter_sizes",
        "logs_search_text",
        "logs_source_filter",
    }
)

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
    """A timestamped diagnostic event.

    ``seq`` is the event's **stable identity** (DEC-314) and is assigned by
    :meth:`DiagnosticsService.log_event` from a monotonic per-session counter. The
    Logs page keys selection off it. Before it existed, selection was restored by
    frozen-view-model *equality*, which two identical messages logged in the same
    second satisfy — so the inspector could silently re-anchor onto the wrong
    event. A default of ``0`` keeps hand-built events (tests, fixtures) valid;
    only the service mints real ones.

    ``fields`` is optional structured metadata, carried **only where an emitter
    genuinely has it** and would otherwise flatten it into the message string. It
    is never synthesised from the message, and an empty mapping renders nothing —
    the Logs inspector shows no placeholder rows for absent data.
    """

    timestamp: float
    level: str  # "info", "warning", "error"
    source: str  # emitter tag, e.g. "polling", "api"
    message: str
    seq: int = 0
    fields: dict[str, str] = field(default_factory=dict)

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
        # Monotonic event identity (DEC-314). Never reset by ``clear_events``: an id
        # that is reused after a clear can collide with one the Logs page is still
        # holding as its selection, which is the exact ambiguity ``seq`` exists to
        # remove.
        self._seq = 0
        self.last_hw_diagnostics: HardwareDiagnosticsResult | None = None

    @property
    def events(self) -> list[DiagEvent]:
        return list(self._events)

    def attach_alert_source(self, state: AppState) -> None:
        """Log alert onsets and recoveries into the event feed (DEC-282).

        This is the observer half of the alert lifecycle: ``AppState`` computes
        transitions and knows nothing about logging, and the dependency can only run
        this way round — this module already imports ``AppState`` at runtime, so the
        reverse import would be a cycle.

        Only genuine transitions arrive here, so a condition that persists across
        hundreds of polls still logs exactly one onset line and one recovery line.
        That matters more than it looks: the feed is capped at ``MAX_EVENTS`` entries,
        so logging per poll would flush every other diagnostic out of it within a
        couple of minutes.

        Generalises the hand-rolled dual-write at ``main.py``'s profile-load failure,
        whose DEC-111 comment already stated the principle — record the transition in
        the event log so the bundle carries it even after the user acknowledges.
        """
        state.alert_transitions.connect(self._on_alert_transitions)

    @Slot(list)
    def _on_alert_transitions(self, transitions: list) -> None:
        for tr in transitions:
            level, source, message = transition_to_log(tr)
            self.log_event(level, source, message, fields=transition_to_fields(tr))

    def set_hw_diagnostics(self, result: HardwareDiagnosticsResult) -> None:
        """Record a ``GET /diagnostics/hardware`` result — the **only** writer.

        Two consumers, one entry point (DEC-229): the shared cache that pages
        render from, and ``AppState.board_info``, which the DMI-keyed hwmon
        label fallback table matches on. Splitting them is what broke gap #16 —
        the retired ``DiagnosticsPage`` pushed the board into ``AppState``, its
        v2.22.0 replacement kept only the cache, and `board_info` silently had
        no writer for seven minor releases (nothing failed loudly because the
        placeholder-label short-circuit masked it). Keeping both sides here
        means a future page can drop the *call* but not half of it.

        The two halves update on different rules. The **cache** always takes the
        newest result — a rescan legitimately refreshes header counts and revert
        tallies. **Board identity never downgrades**: this is not a startup-only
        latch (``SystemStatePage._on_rescan_ok`` re-fetches on every "Rescan
        Hardware" click), and DMI is a boot-time constant, so a re-read that
        comes back blank is a failed read, not a board that stopped existing.
        Overwriting on one would silently revert every hwmon fan to ``pwmN``
        mid-session and hand ``_role_preserving_label`` a role-less name again —
        re-opening the very floor bug this change closes, from a button click.
        """
        self.last_hw_diagnostics = result
        if self._state is None:
            return
        incoming, known = result.board, self._state.board_info
        if (incoming.vendor or incoming.name) or not (known.vendor or known.name):
            self._state.board_info = incoming

    def log_event(
        self,
        level: str,
        source: str,
        message: str,
        *,
        fields: Mapping[str, str] | None = None,
    ) -> None:
        """Append one event to the session feed.

        ``fields`` is structured metadata the emitter already holds. Pass it only
        when it is real: the Logs inspector renders exactly the keys given and
        nothing when there are none (DEC-314). Values are coerced to ``str`` here so
        a caller may hand over ints, floats or enums without formatting them first.
        """
        self._seq += 1
        event = DiagEvent(
            timestamp=time.time(),
            level=level,
            source=source,
            message=message,
            seq=self._seq,
            fields={str(k): str(v) for k, v in (fields or {}).items()},
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
            for s in status.subsystems:
                age = f" (age {s.age_ms}ms)" if s.age_ms is not None else ""
                reason = f" — {s.reason}" if s.reason else ""
                lines.append(f"  {s.name}: {s.status}{age}{reason}")
            # DEC-169: daemon-held live overrides + fan-identify holds, so a
            # support bundle records what the daemon was actively pinning.
            for o in status.overrides:
                lines.append(
                    f"Override: {o.control_id} {o.pwm_percent}% (expires {o.expires_in_secs}s)"
                )
            for i in status.fan_identify:
                lines.append(f"Identify: {i.fan_id} (expires {i.expires_in_secs}s)")
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

        # NVIDIA discrete GPU (DEC-204) — read-only monitoring (nouveau + NVML).
        ngpu = caps.nvidia_gpu
        lines.append("")
        lines.append("NVIDIA GPU:")
        lines.append(f"  Detected: {'Yes' if ngpu.present else 'No'}")
        if ngpu.present:
            lines.append(f"  Model: {ngpu.model_name or 'Unknown'}")
            lines.append(f"  Display label: {ngpu.display_label}")
            if ngpu.pci_id:
                lines.append(f"  PCI ID: {ngpu.pci_id}")
            if ngpu.driver:
                lines.append(f"  Driver: {ngpu.driver}")
            if ngpu.driver_version:
                lines.append(f"  Driver version: {ngpu.driver_version}")
            lines.append(f"  Fan control method: {ngpu.fan_control_method}")
            lines.append(f"  Fan RPM available: {'Yes' if ngpu.fan_rpm_available else 'No'}")
            lines.append("  Fan write supported: No (read-only telemetry, no write path)")
        else:
            lines.append("  No NVIDIA discrete GPU detected by daemon.")

        # GPU fan state from fans list (AMD + Intel + NVIDIA discrete fans)
        gpu_fans = [
            f for f in self._state.fans if f.source in ("amd_gpu", "intel_gpu", "nvidia_gpu")
        ]
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

        # App settings — full settings minus pure window/layout state. The bundle
        # is exported and shared, but a troubleshooting bundle MUST keep the
        # diagnostically load-bearing settings (sensor-class overrides, card
        # sensor bindings, hidden-sensor ids, acknowledged kernel warnings, and
        # the profiles/themes dir overrides that are often the actual root cause)
        # — so it drops only _BUNDLE_EXCLUDED_SETTING_KEYS, NOT the whole
        # MACHINE_SPECIFIC_KEYS set that portable_dict() strips for export.
        if self._settings_service and hasattr(self._settings_service, "settings"):
            settings = self._settings_service.settings
            bundle["app_settings"] = {
                k: v
                for k, v in settings.to_dict().items()
                if k not in _BUNDLE_EXCLUDED_SETTING_KEYS
            }

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
