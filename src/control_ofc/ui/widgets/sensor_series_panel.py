"""Merged sensor/fan series panel with grouped tree, live values, and chart toggle checkboxes.

Replaces both the old SeriesPanel (checkboxes only) and the temp tree
(values only) with a single widget that shows everything in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import FanReading, SensorReading
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.sensor_knowledge import classify_sensor, format_sensor_tooltip

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState
    from control_ofc.ui.widgets.timeline_chart import TimelineChart

# Sensor kind → group key + display label
_SENSOR_KIND_GROUPS: dict[str, tuple[str, str]] = {
    "cpu_temp": ("cpu", "CPU"),
    "CpuTemp": ("cpu", "CPU"),
    "gpu_temp": ("gpu", "GPU"),
    "GpuTemp": ("gpu", "GPU"),
    "mb_temp": ("mb", "Motherboard"),
    "MbTemp": ("mb", "Motherboard"),
    "disk_temp": ("disk", "Disk"),
    "DiskTemp": ("disk", "Disk"),
}

# Order for display
_GROUP_ORDER = [
    "cpu",
    "gpu",
    "mb",
    "disk",
    "other",
    "fans_gpu",
    "fans_hwmon",
    "fans_openfan",
]

_GROUP_LABELS = {
    "cpu": "CPU",
    "gpu": "GPU",
    "mb": "Motherboard",
    "disk": "Disk",
    "other": "Other",
    "fans_gpu": "Fans \u2014 D-GPU",
    "fans_hwmon": "Fans \u2014 hwmon",
    "fans_openfan": "Fans \u2014 OpenFan",
}


class SensorSeriesPanel(QFrame):
    """Grouped tree showing sensors and fans with live values and chart toggle checkboxes."""

    def __init__(
        self,
        selection: SeriesSelectionModel,
        state: AppState | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setMaximumWidth(400)
        self._selection = selection
        self._state = state
        self._search_text = ""
        self.hide_igpu = True  # controlled by Settings → hide_igpu_sensors

        # Track known items to avoid rebuild on every tick
        self._known_sensor_ids: list[str] = []
        self._known_fan_ids: list[str] = []
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._sensor_items: dict[str, QTreeWidgetItem] = {}  # sensor_id → tree item
        self._fan_items: dict[str, QTreeWidgetItem] = {}  # fan_id → tree item
        self._updating = False  # Guard against re-entrant checkbox signals

        self._build_ui()
        self._selection.selection_changed.connect(self._sync_checkboxes_from_model)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sensors...")
        self._search.setObjectName("SensorSeriesPanel_Edit_search")
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Sensor", "Value", ""])
        self._tree.setColumnCount(3)
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setIndentation(20)
        self._tree.setAnimated(True)  # Smooth expand/collapse animation
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, self._tree.header().ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, self._tree.header().ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, self._tree.header().ResizeMode.Fixed)
        self._tree.header().resizeSection(2, 24)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._tree, 1)

        # Chart reference for colour sync (set by DashboardPage after construction)
        self._chart = None
        self._settings_service = None

    # ── Colour sync ──────────────────────────────────────────────────

    def set_chart(
        self, chart: TimelineChart, settings_service: AppSettingsService | None = None
    ) -> None:
        """Set chart reference for colour sync."""
        self._chart = chart
        self._settings_service = settings_service

    def _set_color_swatch(self, item: QTreeWidgetItem, series_key: str) -> None:
        """Set column-2 background to match the chart series colour."""
        if self._chart and hasattr(self._chart, "color_for_key"):
            color = self._chart.color_for_key(series_key)
            item.setBackground(2, QColor(color))

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Open colour picker when column 2 (colour swatch) is clicked."""
        if column != 2:
            return
        series_key = item.data(0, Qt.ItemDataRole.UserRole)
        if not series_key or series_key.startswith("__group__"):
            return
        if not self._chart or not hasattr(self._chart, "color_for_key"):
            return

        from PySide6.QtWidgets import QApplication, QColorDialog

        current = QColor(self._chart.color_for_key(series_key))
        # Temporarily clear the app stylesheet to prevent the global
        # QWidget {} rule from corrupting QColorDialog's internal
        # custom-painted widgets (spectrum, hue strip, preview).
        app = QApplication.instance()
        saved_stylesheet = app.styleSheet() if app else ""
        if app:
            app.setStyleSheet("")

        dlg = QColorDialog(current, self.window())
        dlg.setWindowTitle("Pick series colour")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        result = dlg.exec()

        if app:
            app.setStyleSheet(saved_stylesheet)

        if result:
            color = dlg.currentColor()
            hex_color = color.name()
            self._chart.set_series_color(series_key, hex_color)
            item.setBackground(2, color)
            if self._settings_service and hasattr(self._settings_service, "settings"):
                self._settings_service.settings.series_colors[series_key] = hex_color
                self._settings_service.save()

    # ── Public update methods ────────────────────────────────────────

    def displayed_sensor_ids(self) -> list[str]:
        """Return the IDs of sensors currently displayed in the panel."""
        return list(self._known_sensor_ids)

    def update_sensors(self, sensors: list[SensorReading]) -> None:
        """Update sensor rows. Creates groups/items only when sensor list changes."""
        # Filter iGPU sensors BEFORE structure comparison so the check is
        # stable (comparing filtered→filtered, not unfiltered→filtered).
        if self.hide_igpu:
            primary_bdf = None
            if self._state and self._state.capabilities:
                gpu = self._state.capabilities.amd_gpu
                if gpu.present and gpu.is_discrete and gpu.pci_id:
                    primary_bdf = gpu.pci_id
            if primary_bdf:
                sensors = [s for s in sensors if s.source != "amd_gpu" or primary_bdf in s.id]

        new_ids = [s.id for s in sensors]
        structure_changed = new_ids != self._known_sensor_ids

        if structure_changed:
            self._rebuild_sensor_items(sensors)
            self._known_sensor_ids = new_ids
        else:
            self._update_sensor_values(sensors)

        self._update_group_summaries(sensors)

    def update_fans(self, fans: list[FanReading]) -> None:
        """Update fan rows. Only shows fans with real evidence of being active."""
        # Apply shared displayability rule (DEC-047) — filter before building tree
        hide_unused = True
        if self._settings_service and hasattr(self._settings_service, "settings"):
            hide_unused = self._settings_service.settings.hide_unused_fan_headers
        aliases = self._state.fan_aliases if self._state else {}
        displayable = filter_displayable_fans(fans, aliases, hide_unused)

        new_ids = [f.id for f in displayable]
        structure_changed = new_ids != self._known_fan_ids

        if structure_changed:
            self._rebuild_fan_items(displayable)
            self._known_fan_ids = new_ids
        else:
            self._update_fan_values(displayable)

    # ── Sensor rebuild/update ────────────────────────────────────────

    def _rebuild_sensor_items(self, sensors: list[SensorReading]) -> None:
        """Full rebuild of sensor tree items (only when sensor list structure changes)."""
        with block_signals(self._tree):
            # Remove old sensor items
            for item in self._sensor_items.values():
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
            self._sensor_items.clear()

            # Add sensors to groups
            for s in sensors:
                group_key, group_label = _SENSOR_KIND_GROUPS.get(s.kind, ("other", "Other"))
                group_item = self._ensure_group(group_key, group_label)

                series_key = f"sensor:{s.id}"
                label = s.label or s.id
                value = f"{s.value_c:.1f}\u00b0C"

                item = QTreeWidgetItem(group_item)
                item.setText(0, label)
                item.setText(1, value)
                item.setToolTip(0, self._build_sensor_tooltip(s))
                item.setData(0, Qt.ItemDataRole.UserRole, series_key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if self._selection.is_visible(series_key)
                    else Qt.CheckState.Unchecked,
                )

                self._set_color_swatch(item, series_key)
                self._sensor_items[s.id] = item
        self._update_group_check_states()
        self._apply_search_filter()

    def _update_sensor_values(self, sensors: list[SensorReading]) -> None:
        """Update values in-place without rebuilding items."""
        for s in sensors:
            item = self._sensor_items.get(s.id)
            if item:
                value = f"{s.value_c:.1f}\u00b0C"
                if item.text(1) != value:
                    item.setText(1, value)
                item.setToolTip(0, self._build_sensor_tooltip(s))

    def _update_group_summaries(self, sensors: list[SensorReading]) -> None:
        """Update group header text with count and max value."""
        # Group sensors by group key
        groups: dict[str, list[SensorReading]] = {}
        for s in sensors:
            group_key = _SENSOR_KIND_GROUPS.get(s.kind, ("other", "Other"))[0]
            groups.setdefault(group_key, []).append(s)

        for group_key, group_sensors in groups.items():
            group_item = self._group_items.get(group_key)
            if group_item:
                count = len(group_sensors)
                max_val = max(s.value_c for s in group_sensors)
                label = _GROUP_LABELS.get(group_key, group_key)
                group_item.setText(0, f"{label} ({count})")
                group_item.setText(1, f"max {max_val:.1f}\u00b0C")

    def _build_sensor_tooltip(self, s: SensorReading) -> str:
        """Build a rich tooltip using the sensor knowledge base."""
        session_min = None
        session_max = None
        if self._state and hasattr(self._state, "session_stats"):
            stats = self._state.session_stats.get(s.id)
            if stats:
                session_min = stats.min_c
                session_max = stats.max_c

        classification = classify_sensor(
            chip_name=s.chip_name,
            label=s.label,
            temp_type=s.temp_type,
        )
        return format_sensor_tooltip(
            classification,
            sensor_id=s.id,
            chip_name=s.chip_name,
            session_min=session_min,
            session_max=session_max,
            rate_c_per_s=s.rate_c_per_s,
        )

    # ── Fan rebuild/update ───────────────────────────────────────────

    def _rebuild_fan_items(self, fans: list[FanReading]) -> None:
        """Full rebuild of fan tree items."""
        with block_signals(self._tree):
            # Remove old fan items
            for item in self._fan_items.values():
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
            self._fan_items.clear()

            for f in fans:
                if f.source == "amd_gpu":
                    group_key = "fans_gpu"
                elif "hwmon" in f.source:
                    group_key = "fans_hwmon"
                else:
                    group_key = "fans_openfan"
                group_label = _GROUP_LABELS[group_key]
                group_item = self._ensure_group(group_key, group_label)

                series_key = f"fan:{f.id}:rpm"
                display_name = self._state.fan_display_name(f.id) if self._state else f.id
                rpm_text = f"{f.rpm} RPM" if f.rpm is not None else "\u2014"

                item = QTreeWidgetItem(group_item)
                item.setText(0, display_name)
                item.setText(1, rpm_text)
                item.setToolTip(0, f"ID: {f.id}")
                item.setData(0, Qt.ItemDataRole.UserRole, series_key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if self._selection.is_visible(series_key)
                    else Qt.CheckState.Unchecked,
                )

                self._set_color_swatch(item, series_key)
                self._fan_items[f.id] = item
        self._update_group_check_states()
        self._apply_search_filter()

    def _update_fan_values(self, fans: list[FanReading]) -> None:
        """Update fan values in-place."""
        for f in fans:
            item = self._fan_items.get(f.id)
            if item:
                rpm_text = f"{f.rpm} RPM" if f.rpm is not None else "\u2014"
                if item.text(1) != rpm_text:
                    item.setText(1, rpm_text)
                # Update display name in case alias changed
                if self._state:
                    display_name = self._state.fan_display_name(f.id)
                    if item.text(0) != display_name:
                        item.setText(0, display_name)

    # ── Group management ─────────────────────────────────────────────

    def _ensure_group(self, group_key: str, label: str) -> QTreeWidgetItem:
        """Get or create a top-level group node in display order."""
        if group_key in self._group_items:
            return self._group_items[group_key]

        item = QTreeWidgetItem()
        item.setText(0, label)
        item.setFlags(
            item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        )
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setExpanded(True)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        item.setData(0, Qt.ItemDataRole.UserRole, f"__group__{group_key}")

        # Insert at correct position based on _GROUP_ORDER
        target_idx = (
            _GROUP_ORDER.index(group_key) if group_key in _GROUP_ORDER else len(_GROUP_ORDER)
        )
        insert_at = 0
        for i in range(self._tree.topLevelItemCount()):
            existing = self._tree.topLevelItem(i)
            existing_key = (existing.data(0, Qt.ItemDataRole.UserRole) or "").replace(
                "__group__", ""
            )
            existing_idx = (
                _GROUP_ORDER.index(existing_key)
                if existing_key in _GROUP_ORDER
                else len(_GROUP_ORDER)
            )
            if existing_idx > target_idx:
                break
            insert_at = i + 1

        self._tree.insertTopLevelItem(insert_at, item)
        self._group_items[group_key] = item
        return item

    # ── Checkbox handling ────────────────────────────────────────────

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle checkbox state changes from user interaction."""
        if column != 0 or self._updating:
            return

        series_key = item.data(0, Qt.ItemDataRole.UserRole)
        if not series_key or series_key.startswith("__group__"):
            # Group header toggled — Qt's ItemIsAutoTristate handles children automatically
            # We just need to sync all children to the selection model
            self._updating = True
            self._sync_children_to_model(item)
            self._updating = False
            return

        # Individual item toggled
        checked = item.checkState(0) == Qt.CheckState.Checked
        self._updating = True
        self._selection.set_visible(series_key, checked)
        self._updating = False

    def _sync_children_to_model(self, group_item: QTreeWidgetItem) -> None:
        """Sync all children of a group to the selection model after group toggle."""
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            series_key = child.data(0, Qt.ItemDataRole.UserRole)
            if series_key:
                checked = child.checkState(0) == Qt.CheckState.Checked
                self._selection.set_visible(series_key, checked)

    def _sync_checkboxes_from_model(self) -> None:
        """Refresh all checkbox states from the selection model (external change)."""
        if self._updating:
            return
        self._updating = True
        with block_signals(self._tree):
            for item in list(self._sensor_items.values()) + list(self._fan_items.values()):
                series_key = item.data(0, Qt.ItemDataRole.UserRole)
                if series_key:
                    expected = (
                        Qt.CheckState.Checked
                        if self._selection.is_visible(series_key)
                        else Qt.CheckState.Unchecked
                    )
                    if item.checkState(0) != expected:
                        item.setCheckState(0, expected)
        self._updating = False

    def _update_group_check_states(self) -> None:
        """Update group tri-state based on children (for non-AutoTristate scenarios)."""
        # Qt's ItemIsAutoTristate handles this automatically in most cases
        pass

    # ── Search ───────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.lower()
        self._apply_search_filter()

    def _apply_search_filter(self) -> None:
        """Show/hide items based on search text."""
        for items in (self._sensor_items, self._fan_items):
            for _id, item in items.items():
                match = not self._search_text or self._search_text in item.text(0).lower()
                item.setHidden(not match)

        # Hide empty groups
        for _key, group_item in self._group_items.items():
            has_visible = False
            for i in range(group_item.childCount()):
                if not group_item.child(i).isHidden():
                    has_visible = True
                    break
            group_item.setHidden(not has_visible)
