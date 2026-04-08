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


class SeriesSelectionModel(QObject):
    """Tracks which chart series are visible. Emits on change."""

    selection_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._known_keys: set[str] = set()
        self._hidden_keys: set[str] = set()

    # -- visibility --

    def is_visible(self, key: str) -> bool:
        return key in self._known_keys and key not in self._hidden_keys

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
        """Update from HistoryStore.series_keys(). Excludes PWM keys."""
        filtered = {k for k in all_keys if not k.endswith(":pwm")}
        if filtered != self._known_keys:
            self._known_keys = filtered
            # Prune hidden keys that no longer exist
            self._hidden_keys &= self._known_keys

    # -- persistence --

    def to_dict(self) -> dict:
        return {"hidden_keys": sorted(self._hidden_keys)}

    def load_hidden(self, hidden: list[str]) -> None:
        """Restore hidden keys from persisted data."""
        self._hidden_keys = set(hidden)
