"""Series selection model for dashboard chart visibility.

Tracks which time-series keys are visible on the chart. New series
default to visible; the model stores the set of *hidden* keys so
that newly discovered sensors appear automatically.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, Signal


class SeriesGroup(Enum):
    TEMPS = "temps"
    MOBO_FANS = "mobo_fans"
    OPENFAN_FANS = "openfan_fans"


class ChartMode(Enum):
    """Chart readability presets (DEC-181). Each resolves to a visible-series set."""

    THERMALS = "thermals"
    FANS = "fans"
    COMBINED = "combined"  # the curated default subset (kind-aware, dashboard-supplied)
    DIAGNOSTICS = "diagnostics"  # everything (power-user "show all")


# Group-based modes resolve to a fixed set of groups. COMBINED is intentionally
# absent — it is curated by sensor *kind* (CPU/GPU/one mobo + aggregate), which
# only the dashboard knows, so it is applied with explicit keys, not groups.
# DIAGNOSTICS is absent too — it means "all known keys" (select_all).
_MODE_GROUPS: dict[ChartMode, set[SeriesGroup]] = {
    ChartMode.THERMALS: {SeriesGroup.TEMPS},
    ChartMode.FANS: {SeriesGroup.MOBO_FANS, SeriesGroup.OPENFAN_FANS},
}


class SeriesSelectionModel(QObject):
    """Tracks which chart series are visible. Emits on change."""

    selection_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._known_keys: set[str] = set()
        self._hidden_keys: set[str] = set()
        # Active chart-readability mode (DEC-181). Defaults to COMBINED, which is
        # NOT group-based, so the "new keys default visible" contract is untouched
        # until the user picks a group-based mode (Thermals/Fans).
        self._active_mode: ChartMode = ChartMode.COMBINED

    # -- visibility --

    def is_visible(self, key: str) -> bool:
        return key in self._known_keys and key not in self._hidden_keys

    def is_hidden(self, key: str) -> bool:
        """True only if the key was explicitly hidden (persisted or user-toggled).

        Unlike :meth:`is_visible`, an unknown key is *not* hidden — new series
        default to visible. UI that builds rows before the dashboard registers
        keys via :meth:`update_known_keys` must use this, not ``is_visible``,
        or first-discovery rows start unchecked and get synced back into the
        model as hidden.
        """
        return key in self._hidden_keys

    def set_visible(self, key: str, visible: bool) -> None:
        changed = False
        if visible and key in self._hidden_keys:
            self._hidden_keys.discard(key)
            changed = True
        elif not visible and key not in self._hidden_keys:
            self._hidden_keys.add(key)
            changed = True
        if changed:
            self.selection_changed.emit()

    def toggle(self, key: str) -> None:
        self.set_visible(key, not self.is_visible(key))

    def visible_keys(self) -> set[str]:
        return self._known_keys - self._hidden_keys

    def known_keys(self) -> set[str]:
        """Return all keys the selection model knows about (displayable entities)."""
        return set(self._known_keys)

    # -- group operations --

    def set_group_visible(self, group: SeriesGroup, visible: bool) -> None:
        changed = False
        for key in self._known_keys:
            if self.classify(key) == group:
                if visible and key in self._hidden_keys:
                    self._hidden_keys.discard(key)
                    changed = True
                elif not visible and key not in self._hidden_keys:
                    self._hidden_keys.add(key)
                    changed = True
        if changed:
            self.selection_changed.emit()

    def is_group_fully_visible(self, group: SeriesGroup) -> bool:
        group_keys = {k for k in self._known_keys if self.classify(k) == group}
        return bool(group_keys) and not group_keys.intersection(self._hidden_keys)

    def is_group_partially_visible(self, group: SeriesGroup) -> bool:
        group_keys = {k for k in self._known_keys if self.classify(k) == group}
        visible = group_keys - self._hidden_keys
        return bool(visible) and visible != group_keys

    def select_all(self) -> None:
        if self._hidden_keys:
            self._hidden_keys.clear()
            self.selection_changed.emit()

    def select_none(self) -> None:
        new_hidden = set(self._known_keys)
        if new_hidden != self._hidden_keys:
            self._hidden_keys = new_hidden
            self.selection_changed.emit()

    # -- chart modes (DEC-181) --

    @property
    def active_mode(self) -> ChartMode:
        return self._active_mode

    def set_only_visible(self, keys: set[str]) -> None:
        """Show exactly ``keys`` (those that are known); hide every other known
        key. One ``selection_changed`` emit. The primitive behind COMBINED/seed."""
        target_visible = self._known_keys & set(keys)
        new_hidden = self._known_keys - target_visible
        if new_hidden != self._hidden_keys:
            self._hidden_keys = new_hidden
            self.selection_changed.emit()

    def apply_mode(self, mode: ChartMode, curated_keys: set[str] | None = None) -> None:
        """Apply a chart-readability preset and remember it for the new-key rule.

        - DIAGNOSTICS → everything visible (``select_all``).
        - COMBINED → the curated, kind-aware subset; ``curated_keys`` MUST be
          supplied by the dashboard (the model can't tell a CPU temp from a GPU
          temp by key). If ``None`` (e.g. before any sensors arrive) visibility is
          left unchanged but the mode is still recorded.
        - THERMALS / FANS → the group-based preset (``_MODE_GROUPS``).
        """
        self._active_mode = mode
        if mode == ChartMode.DIAGNOSTICS:
            self.select_all()
            return
        if mode == ChartMode.COMBINED:
            if curated_keys is not None:
                self.set_only_visible(curated_keys)
            return
        visible: set[str] = set()
        for group in _MODE_GROUPS.get(mode, set()):
            visible |= self.keys_for_group(group)
        self.set_only_visible(visible)

    # -- classification --

    @staticmethod
    def classify(key: str) -> SeriesGroup:
        """Classify a history key into a group.

        - sensor:* → TEMPS
        - fan:hwmon:*:rpm → MOBO_FANS
        - fan:openfan:*:rpm → OPENFAN_FANS
        """
        if key.startswith("sensor:"):
            return SeriesGroup.TEMPS
        if ":hwmon:" in key:
            return SeriesGroup.MOBO_FANS
        return SeriesGroup.OPENFAN_FANS

    def keys_for_group(self, group: SeriesGroup) -> set[str]:
        return {k for k in self._known_keys if self.classify(k) == group}

    # -- key management --

    def update_known_keys(self, all_keys: list[str]) -> None:
        """Update from HistoryStore.series_keys(). Excludes PWM keys.

        New keys default *visible* (the auto-appear contract) UNLESS a group-based
        mode (Thermals/Fans) is active — then a newly-discovered key follows that
        mode (e.g. a new fan stays hidden under Thermals) so the mode doesn't
        silently leak (DEC-181). Emits ``selection_changed`` only if the mode rule
        actually hid a freshly-seen key, so panels/legends re-sync.
        """
        filtered = {k for k in all_keys if not k.endswith(":pwm")}
        if filtered == self._known_keys:
            return
        added = filtered - self._known_keys
        self._known_keys = filtered
        # Prune hidden keys that no longer exist
        self._hidden_keys &= self._known_keys
        groups = _MODE_GROUPS.get(self._active_mode)
        changed = False
        if groups:
            for key in added:
                if self.classify(key) not in groups and key not in self._hidden_keys:
                    self._hidden_keys.add(key)
                    changed = True
        if changed:
            self.selection_changed.emit()

    # -- persistence --

    def to_dict(self) -> dict:
        return {"hidden_keys": sorted(self._hidden_keys)}

    def load_hidden(self, hidden: list[str]) -> None:
        """Restore hidden keys from persisted data."""
        self._hidden_keys = set(hidden)
