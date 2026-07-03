"""Central application state — the single source of truth for UI binding.

Services write to AppState; UI pages read from it via signals.
AppState is a QObject so it can emit signals on the main thread.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    Freshness,
    HwmonHeader,
    OperationMode,
    SensorReading,
)
from control_ofc.knowledge.hwmon_label_resolver import resolve_hwmon_header_label
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
    active_profile_changed = Signal(str)  # profile name
    active_profile_id_changed = Signal(str)  # active profile id
    warning_count_changed = Signal(int)
    warnings_cleared = Signal()
    fan_alias_changed = Signal(str, str)  # fan_id, display_name
    fan_zones_changed = Signal(str, str)  # fan_id, zone_name ("" = unassigned)
    sensor_class_override_changed = Signal(str, str)  # sensor_id, source_class ("" = cleared)

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
        self.active_profile_name: str = ""
        self.active_profile_id: str = ""
        self.warning_count: int = 0
        self.active_warnings: list[dict] = []  # [{timestamp, level, source, message}]
        self._acknowledged: set[str] = set()  # keys of acknowledged warnings
        self._warning_first_seen: dict[str, float] = {}  # key -> first-seen timestamp
        self._external_warnings: list[dict] = []  # ad-hoc warnings from services

        # Fan aliases: fan_id -> display name (GUI-owned)
        self.fan_aliases: dict[str, str] = {}

        # DEC-176: fan_id -> user-assigned physical zone name (GUI-owned).
        # Opt-in overlay on the dashboard's role/source grouping; unassigned
        # fans fall back to that grouping (services/fan_grouping.py).
        self.fan_zones: dict[str, str] = {}

        # DEC-156: user sensor-classification overrides (sensor_id ->
        # source_class, only "coolant" today). GUI-owned policy; the daemon
        # stays hardware-truthful. Lets the user force a coolant sensor the
        # conservative auto-classifier missed.
        self.sensor_class_overrides: dict[str, str] = {}

        # DMI board info supplied by /diagnostics/hardware. Used by
        # ``hwmon_label_resolver`` to apply per-board fallback labels
        # (A3) when the daemon's sysfs label is empty and no
        # /etc/sensors.d entry matches.
        self.board_info: BoardInfo = BoardInfo()

        # Per-sensor session min/max tracker (resets on reconnect)
        self.session_stats = SessionStatsTracker()

        # Monotonic timestamp of the last successful poll / demo tick, for the
        # dashboard status strip's "Updated Xs ago" indicator (DEC-176/177).
        # None until the first success; deliberately NOT reset on disconnect so
        # the age keeps growing to signal staleness.
        self.last_poll_monotonic: float | None = None

    def mark_poll_success(self, now: float | None = None) -> None:
        """Record the time of the latest successful poll (or demo tick).

        Uses a monotonic clock so the dashboard can show time-since-last-update;
        tests pass an explicit ``now`` to avoid real timing.
        """
        self.last_poll_monotonic = now if now is not None else time.monotonic()

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
        # DEC-194: reflect the daemon's active profile on every poll when it is
        # mirrored onto the status, so an external activation shows within ~1 s
        # instead of the slow /profile/active refresh. The name drives the status
        # banner; the id (routed by main_window → ProfileService.set_active) drives
        # the id-based combo selection + the Controls `*`-active marker. `None`
        # means an older daemon or no active profile → leave the /profile/active
        # fallback (_on_active_profile) authoritative rather than clobbering it.
        # Cheap: both setters are edge-triggered, so a signal fires only on change.
        if status.active_profile_id is not None:
            self.set_active_profile_id(status.active_profile_id)
        if status.active_profile_name is not None:
            self.set_active_profile(status.active_profile_name)
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

    def set_active_profile(self, name: str) -> None:
        if name != self.active_profile_name:
            self.active_profile_name = name
            self.active_profile_changed.emit(name)

    def set_active_profile_id(self, profile_id: str) -> None:
        if profile_id != self.active_profile_id:
            self.active_profile_id = profile_id
            self.active_profile_id_changed.emit(profile_id)

    @staticmethod
    def _set_or_clear(mapping: dict[str, str], key: str, value: str) -> str:
        """Store the stripped *value* under *key*, or drop the key when the value
        is empty/whitespace-only. Returns the cleaned value so callers can emit
        their own signal from it (or from a derived payload)."""
        cleaned = value.strip() if value else ""
        if cleaned:
            mapping[key] = cleaned
        else:
            mapping.pop(key, None)
        return cleaned

    def set_fan_alias(self, fan_id: str, alias: str) -> None:
        """Set or clear a fan alias. Empty/whitespace-only string clears."""
        self._set_or_clear(self.fan_aliases, fan_id, alias)
        self.fan_alias_changed.emit(fan_id, self.fan_display_name(fan_id))

    def set_fan_zone(self, fan_id: str, zone: str) -> None:
        """Assign or clear a fan's physical zone (DEC-176).

        Empty/whitespace-only ``zone`` unassigns the fan (it then falls back to
        role/source grouping in the dashboard). Mirrors :meth:`set_fan_alias`.
        """
        cleaned = self._set_or_clear(self.fan_zones, fan_id, zone)
        self.fan_zones_changed.emit(fan_id, cleaned)

    def set_sensor_class_override(self, sensor_id: str, source_class: str) -> None:
        """Force (or clear) a sensor's display classification (DEC-156).

        ``source_class == "coolant"`` marks the sensor as coolant; an empty
        string clears the override (revert to auto-classification). GUI-owned
        user policy — the daemon stays hardware-truthful.
        """
        cleaned = self._set_or_clear(self.sensor_class_overrides, sensor_id, source_class)
        self.sensor_class_override_changed.emit(sensor_id, cleaned)

    def fan_display_name(self, fan_id: str) -> str:
        """Return the best display name for a fan.

        Priority:
            1. user alias (``fan_aliases``)
            2. GPU model name (for ``amd_gpu:`` fans)
            3. daemon-supplied sysfs ``HwmonHeader.label`` (for hwmon fans)
            4. ``hwmon_label_resolver`` — ``/etc/sensors.d`` and the
               in-repo board fallback table (A3)
            5. raw ``fan_id`` as a last resort

        Steps 4 onward only fire for hwmon fans; OpenFan and GPU fans
        keep their existing precedence.
        """
        if fan_id in self.fan_aliases:
            return self.fan_aliases[fan_id]
        if fan_id.startswith("amd_gpu:"):
            if self.capabilities and self.capabilities.amd_gpu.present:
                return f"{self.capabilities.amd_gpu.display_label} Fan"
            return "D-GPU Fan"
        if fan_id.startswith("intel_gpu:"):
            if self.capabilities and self.capabilities.intel_gpu.present:
                return f"{self.capabilities.intel_gpu.display_label} Fan"
            return "Intel D-GPU Fan"
        for h in self.hwmon_headers:
            if h.id == fan_id:
                if h.label:
                    return h.label
                return resolve_hwmon_header_label(
                    sysfs_label="",
                    chip_name=h.chip_name,
                    pwm_index=h.pwm_index,
                    board_vendor=self.board_info.vendor,
                    board_name=self.board_info.name,
                )
        return fan_id

    def add_warning(self, level: str, source: str, message: str, key: str = "") -> None:
        """Add an ad-hoc warning from a service (e.g. a profile-load or daemon-poll failure).

        Recomputes ``active_warnings`` and emits ``warning_count_changed``
        immediately rather than waiting for the next polling tick — callers
        signalling a transient daemon problem expect the UI to reflect it
        without a 1s delay.
        """
        if not key:
            key = f"{source}:{message}"
        if key in self._acknowledged:
            return
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
        self._update_warnings()

    def remove_warning(self, key: str) -> None:
        """Remove an ad-hoc warning by key (e.g., when condition clears).

        Recomputes ``active_warnings`` and emits ``warning_count_changed``
        immediately so the UI clears the badge without waiting for the next
        polling tick.
        """
        self._external_warnings = [w for w in self._external_warnings if w.get("_key") != key]
        self._warning_first_seen.pop(key, None)
        self._update_warnings()

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

    def _make_warning(
        self, warnings: list[dict], key: str, level: str, source: str, message: str
    ) -> None:
        """Append a ``{timestamp, level, source, message, _key}`` entry to
        *warnings*, unless *key* is acknowledged. Records the key's first-seen
        time on first sight so a persistent warning keeps its original timestamp."""
        if key in self._acknowledged:
            return
        if key not in self._warning_first_seen:
            self._warning_first_seen[key] = time.time()
        warnings.append(
            {
                "timestamp": self._warning_first_seen[key],
                "level": level,
                "source": source,
                "message": message,
                "_key": key,
            }
        )

    def _update_warnings(self) -> None:
        warnings: list[dict] = []

        for s in self.sensors:
            if s.freshness != Freshness.FRESH:
                self._make_warning(
                    warnings,
                    f"sensor_stale:{s.id}",
                    "warning",
                    "sensor",
                    f"Sensor '{s.label or s.id}' is {s.freshness.name.lower()} (age {s.age_ms}ms)",
                )

        for f in self.fans:
            if f.freshness != Freshness.FRESH:
                self._make_warning(
                    warnings,
                    f"fan_stale:{f.id}",
                    "warning",
                    "fan",
                    f"Fan '{f.id}' is {f.freshness.name.lower()} (age {f.age_ms}ms)",
                )
            if f.stall_detected:
                self._make_warning(
                    warnings,
                    f"fan_stall:{f.id}",
                    "error",
                    "fan",
                    f"Fan '{f.id}' stall detected (RPM=0 while PWM commanded)",
                )

        # Merge ad-hoc warnings from services
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
