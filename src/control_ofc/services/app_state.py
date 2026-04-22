"""Central application state — the single source of truth for UI binding.

Services write to AppState; UI pages read from it via signals.
AppState is a QObject so it can emit signals on the main thread.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    Freshness,
    HwmonHeader,
    LeaseState,
    OperationMode,
    SensorReading,
)
from control_ofc.services.session_stats import SessionStatsTracker


class AppState(QObject):
    """Observable application state. Emits signals when data changes."""

    # Signals for UI binding
    connection_changed = Signal(ConnectionState)
    mode_changed = Signal(OperationMode)
    capabilities_updated = Signal(Capabilities)
    status_updated = Signal(DaemonStatus)
    sensors_updated = Signal(list)  # list[SensorReading]
    fans_updated = Signal(list)  # list[FanReading]
    headers_updated = Signal(list)  # list[HwmonHeader]
    lease_updated = Signal(LeaseState)
    active_profile_changed = Signal(str)  # profile name
    warning_count_changed = Signal(int)
    warnings_cleared = Signal()
    fan_alias_changed = Signal(str, str)  # fan_id, display_name

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Current state
        self.connection = ConnectionState.DISCONNECTED
        self.mode = OperationMode.READ_ONLY
        self.capabilities: Capabilities | None = None
        self.daemon_status: DaemonStatus | None = None
        self.sensors: list[SensorReading] = []
        self.fans: list[FanReading] = []
        self.hwmon_headers: list[HwmonHeader] = []
        self.lease: LeaseState = LeaseState()
        self.active_profile_name: str = ""
        self.warning_count: int = 0
        self.active_warnings: list[dict] = []  # [{timestamp, level, source, message}]
        self._acknowledged: set[str] = set()  # keys of acknowledged warnings
        self._warning_first_seen: dict[str, float] = {}  # key -> first-seen timestamp
        self._external_warnings: list[dict] = []  # ad-hoc warnings from services

        # Fan aliases: fan_id -> display name (GUI-owned)
        self.fan_aliases: dict[str, str] = {}

        # Per-sensor session min/max tracker (resets on reconnect)
        self.session_stats = SessionStatsTracker()

    def set_connection(self, state: ConnectionState) -> None:
        if state != self.connection:
            self.connection = state
            self.connection_changed.emit(state)

    def set_mode(self, mode: OperationMode) -> None:
        if mode != self.mode:
            self.mode = mode
            self.mode_changed.emit(mode)

    def set_capabilities(self, caps: Capabilities) -> None:
        self.capabilities = caps
        self.capabilities_updated.emit(caps)

    def set_status(self, status: DaemonStatus) -> None:
        self.daemon_status = status
        self.status_updated.emit(status)

    def set_sensors(self, sensors: list[SensorReading]) -> None:
        self.sensors = sensors
        self.session_stats.update_batch([(s.id, s.value_c) for s in sensors])
        self.sensors_updated.emit(sensors)
        self._update_warnings()

    def reset_session_stats(self) -> None:
        """Reset session statistics (call on reconnect)."""
        self.session_stats.reset()

    def set_fans(self, fans: list[FanReading]) -> None:
        self.fans = fans
        self.fans_updated.emit(fans)
        self._update_warnings()

    def set_hwmon_headers(self, headers: list[HwmonHeader]) -> None:
        self.hwmon_headers = headers
        self.headers_updated.emit(headers)

    def set_lease(self, lease: LeaseState) -> None:
        self.lease = lease
        self.lease_updated.emit(lease)

    def set_active_profile(self, name: str) -> None:
        if name != self.active_profile_name:
            self.active_profile_name = name
            self.active_profile_changed.emit(name)

    def set_fan_alias(self, fan_id: str, alias: str) -> None:
        """Set or clear a fan alias. Empty/whitespace-only string clears."""
        cleaned = alias.strip() if alias else ""
        if cleaned:
            self.fan_aliases[fan_id] = cleaned
        else:
            self.fan_aliases.pop(fan_id, None)
        self.fan_alias_changed.emit(fan_id, self.fan_display_name(fan_id))

    def fan_display_name(self, fan_id: str) -> str:
        """Return the best display name for a fan: alias > GPU model > hwmon label > id."""
        if fan_id in self.fan_aliases:
            return self.fan_aliases[fan_id]
        if fan_id.startswith("amd_gpu:"):
            if self.capabilities and self.capabilities.amd_gpu.present:
                return f"{self.capabilities.amd_gpu.display_label} Fan"
            return "D-GPU Fan"
        for h in self.hwmon_headers:
            if h.id == fan_id:
                return h.label
        return fan_id

    def add_warning(self, level: str, source: str, message: str, key: str = "") -> None:
        """Add an ad-hoc warning from a service (e.g., control loop write failure)."""
        if not key:
            key = f"{source}:{message}"
        if key not in self._acknowledged:
            self._external_warnings = [w for w in self._external_warnings if w.get("_key") != key]
            self._external_warnings.append(
                {
                    "timestamp": self._warning_first_seen.get(key, time.time()),
                    "level": level,
                    "source": source,
                    "message": message,
                    "_key": key,
                }
            )
            if key not in self._warning_first_seen:
                self._warning_first_seen[key] = time.time()

    def remove_warning(self, key: str) -> None:
        """Remove an ad-hoc warning by key (e.g., when condition clears)."""
        self._external_warnings = [w for w in self._external_warnings if w.get("_key") != key]
        self._warning_first_seen.pop(key, None)

    def clear_warnings(self) -> None:
        """Acknowledge all current warnings — resets count, preserves event log history."""
        for w in self.active_warnings:
            self._acknowledged.add(w.get("_key", ""))
        self.active_warnings.clear()
        self._external_warnings.clear()
        if self.warning_count != 0:
            self.warning_count = 0
            self.warning_count_changed.emit(0)
        self.warnings_cleared.emit()

    def _update_warnings(self) -> None:
        warnings: list[dict] = []
        now = time.time()

        for s in self.sensors:
            if s.freshness != Freshness.FRESH:
                key = f"sensor_stale:{s.id}"
                if key not in self._acknowledged:
                    if key not in self._warning_first_seen:
                        self._warning_first_seen[key] = now
                    warnings.append(
                        {
                            "timestamp": self._warning_first_seen[key],
                            "level": "warning",
                            "source": "sensor",
                            "message": (
                                f"Sensor '{s.label or s.id}' is "
                                f"{s.freshness.name.lower()} (age {s.age_ms}ms)"
                            ),
                            "_key": key,
                        }
                    )

        for f in self.fans:
            if f.freshness != Freshness.FRESH:
                key = f"fan_stale:{f.id}"
                if key not in self._acknowledged:
                    if key not in self._warning_first_seen:
                        self._warning_first_seen[key] = now
                    warnings.append(
                        {
                            "timestamp": self._warning_first_seen[key],
                            "level": "warning",
                            "source": "fan",
                            "message": (
                                f"Fan '{f.id}' is {f.freshness.name.lower()} (age {f.age_ms}ms)"
                            ),
                            "_key": key,
                        }
                    )
            if f.stall_detected:
                key = f"fan_stall:{f.id}"
                if key not in self._acknowledged:
                    if key not in self._warning_first_seen:
                        self._warning_first_seen[key] = now
                    warnings.append(
                        {
                            "timestamp": self._warning_first_seen[key],
                            "level": "error",
                            "source": "fan",
                            "message": f"Fan '{f.id}' stall detected (RPM=0 while PWM commanded)",
                            "_key": key,
                        }
                    )

        # Merge ad-hoc warnings from services (e.g., control loop write failures)
        for w in self._external_warnings:
            if w.get("_key") not in self._acknowledged:
                warnings.append(w)

        # Prune first-seen entries for warnings that resolved
        active_keys = {w.get("_key") for w in warnings}
        stale_keys = [k for k in self._warning_first_seen if k not in active_keys]
        for k in stale_keys:
            del self._warning_first_seen[k]

        self.active_warnings = warnings
        count = len(warnings)
        if count != self.warning_count:
            self.warning_count = count
            self.warning_count_changed.emit(count)
