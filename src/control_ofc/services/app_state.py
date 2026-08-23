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

# DEC-227: presentation suffix tagging a liquid-cooler fan in the Dashboard's
# Sensors rail. It is a hardware fact rendered onto the row, never part of the
# stored name — ``apply_fan_rename`` strips it back off on the way in so a
# round-trip through an in-place editor cannot capture it into settings.
AIO_SUFFIX = " (AIO)"

# DEC-227: upper bound on a user-authored fan alias. Aliases are user-controlled,
# persisted to app_settings.json and portable via Settings export, so an unbounded
# string would bloat the settings file and break row layout on every surface.
MAX_FAN_ALIAS_LEN = 64

# DEC-227: id prefix of an OpenFan channel fan ("openfan:ch00"). Matched strictly —
# anything after it that is not a plain number falls through to the raw id rather
# than inventing a channel number.
_OPENFAN_CH_PREFIX = "openfan:ch"


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
        # fans fell back to role/source grouping. DEC-222 removed the zone UI;
        # this map is retained dormant (no reader) so saved assignments and the
        # settings schema are preserved.
        self.fan_zones: dict[str, str] = {}

        # DEC-156: user sensor-classification overrides (sensor_id ->
        # source_class, only "coolant" today). GUI-owned policy; the daemon
        # stays hardware-truthful. Lets the user force a coolant sensor the
        # conservative auto-classifier missed.
        self.sensor_class_overrides: dict[str, str] = {}

        # DMI board info supplied by /diagnostics/hardware, written only by
        # ``DiagnosticsService.set_hw_diagnostics``. Used by
        # ``hwmon_label_resolver`` to apply per-board fallback labels (A3) when
        # no /etc/sensors.d entry matches and the daemon's sysfs label is either
        # empty *or* a synthesised ``pwmN`` placeholder — the latter is the
        # common case and the one DEC-229 exists for, so a non-empty label does
        # not mean this is unused.
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
        """Set or clear a fan alias. Empty/whitespace-only string clears.

        Capped at ``MAX_FAN_ALIAS_LEN`` here rather than at each call site, so
        every writer (fan wizard, the DEC-227 rename surfaces) is bounded.
        """
        if alias:
            # Strip before capping so the limit counts characters of actual name,
            # not leading whitespace that _set_or_clear is about to discard.
            alias = alias.strip()[:MAX_FAN_ALIAS_LEN]
        self._set_or_clear(self.fan_aliases, fan_id, alias)
        self.fan_alias_changed.emit(fan_id, self.fan_display_name(fan_id))

    def apply_fan_rename(self, fan_id: str, text: str) -> None:
        """Apply a user rename of *fan_id* from any surface (DEC-227).

        Shared by every rename affordance — the Dashboard Sensors rail, the
        read-only fan cards and the Overview fan table — so they cannot drift
        apart. Qt-free, so the rule is unit-testable without a widget.

        Empty text clears the alias. So does text equal to the fan's *fallback*
        name: an in-place ``QTreeWidgetItem`` editor opens pre-filled with the
        already-resolved label (Qt aliases ``EditRole`` to ``DisplayRole``), so
        without this rule merely pressing Enter on an unchanged row would mint an
        alias — and ``fan_display.filter_displayable_fans`` reads "has an alias"
        as "the user wants this fan visible", which would silently pin a 0-RPM
        header on screen forever.

        The presentation-only ``(AIO)`` tag is stripped back off for the same
        reason. Consequence: a fan cannot be named literally "Front (AIO)".
        """
        cleaned = (text or "").strip()
        if cleaned.endswith(AIO_SUFFIX):
            cleaned = cleaned[: -len(AIO_SUFFIX)].strip()
        cleaned = cleaned[:MAX_FAN_ALIAS_LEN]
        if cleaned == self.fan_fallback_name(fan_id):
            cleaned = ""
        self.set_fan_alias(fan_id, cleaned)

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
            2. everything else — see :meth:`fan_fallback_name`
        """
        if fan_id in self.fan_aliases:
            return self.fan_aliases[fan_id]
        return self.fan_fallback_name(fan_id)

    def member_display_name(self, member_id: str, member_label: str = "") -> str:
        """Display name for a profile member — the control-surface counterpart of
        :meth:`fan_display_name` (DEC-228).

        Control cards, fan-role chips and the member editor render a member's
        *cached* ``member_label``, which is a snapshot taken when the member was
        added. A later rename updated ``fan_aliases`` and left that snapshot
        stale, so the same fan showed two different names depending on which page
        you were looking at. A live alias therefore wins here.

        Note the tiers cannot be collapsed to ``fan_display_name(id) or label``:
        ``fan_display_name`` falls back to a synthesised name and so is *never*
        empty, which would make the cached label unreachable.
        """
        if member_id in self.fan_aliases:
            return self.fan_aliases[member_id]
        if member_label:
            return member_label
        return self.fan_fallback_name(member_id)

    def fan_fallback_name(self, fan_id: str) -> str:
        """Return the best display name for a fan *ignoring* any user alias.

        Priority:
            1. GPU model name (for ``amd_gpu:`` / ``intel_gpu:`` / ``nvidia_gpu:``)
            2. OpenFan channel label (``openfan:ch00`` -> ``OpenFan CH0``)
            3. ``hwmon_label_resolver`` (hwmon fans) — the daemon-supplied
               sysfs ``HwmonHeader.label``, then ``/etc/sensors.d``, then the
               in-repo board fallback table (A3), then raw ``pwmN``. A label
               the daemon merely synthesised from the node id is skipped
               rather than treated as authoritative (DEC-229)
            4. raw ``fan_id`` as a last resort

        Split out of :meth:`fan_display_name` for DEC-227: ``apply_fan_rename``
        compares the user's typed text against this to tell "they renamed it"
        from "they pressed Enter on the name already shown".
        """
        if fan_id.startswith("amd_gpu:"):
            if self.capabilities and self.capabilities.amd_gpu.present:
                return f"{self.capabilities.amd_gpu.display_label} Fan"
            return "D-GPU Fan"
        if fan_id.startswith("intel_gpu:"):
            if self.capabilities and self.capabilities.intel_gpu.present:
                return f"{self.capabilities.intel_gpu.display_label} Fan"
            return "Intel D-GPU Fan"
        if fan_id.startswith("nvidia_gpu:"):
            if self.capabilities and self.capabilities.nvidia_gpu.present:
                return f"{self.capabilities.nvidia_gpu.display_label} Fan"
            return "NVIDIA D-GPU Fan"
        # DEC-227: an OpenFan channel has no label source at all — the daemon
        # reports only a channel count, and no hwmon header carries an
        # "openfan:" id — so without this every unnamed channel rendered as its
        # raw daemon id. Presentation only: this is never written into
        # fan_aliases, so it cannot make filter_displayable_fans treat an
        # unnamed 0-RPM channel as one the user asked to keep visible.
        if fan_id.startswith(_OPENFAN_CH_PREFIX):
            channel = fan_id[len(_OPENFAN_CH_PREFIX) :]
            # isdecimal, not isdigit: isdigit accepts superscripts and other
            # Unicode digit forms that int() then rejects with a ValueError, on a
            # path called once per fan per poll. The daemon only ever mints ASCII
            # channel ids, so this is belt-and-braces rather than a live crash.
            if channel.isdecimal():
                return f"OpenFan CH{int(channel)}"
        for h in self.hwmon_headers:
            if h.id == fan_id:
                # DEC-229: the resolver owns tiers 2-5, including whether the
                # daemon's label is real or a synthesised "pwmN" placeholder.
                # A short-circuit on `h.label` here would defeat that — the
                # placeholder is non-empty, so it used to win over the board
                # table that knew the header was CPU_FAN.
                return resolve_hwmon_header_label(
                    sysfs_label=h.label,
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
