"""Merged sensor/fan series panel with grouped tree, live values, and chart toggle checkboxes.

Replaces both the old SeriesPanel (checkboxes only) and the temp tree
(values only) with a single widget that shows everything in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import FanReading, SensorReading
from control_ofc.knowledge.sensor_knowledge import classify_sensor, format_sensor_tooltip
from control_ofc.services.app_state import AIO_SUFFIX
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.qt_util import block_signals

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
    # DEC-157: liquid-cooler coolant temperatures cluster under "AIO / Liquid".
    "coolant_temp": ("aio", "AIO / Liquid"),
    "CoolantTemp": ("aio", "AIO / Liquid"),
    "mb_temp": ("mb", "Motherboard"),
    "MbTemp": ("mb", "Motherboard"),
    "disk_temp": ("disk", "Disk"),
    "DiskTemp": ("disk", "Disk"),
}

# Order for display
_GROUP_ORDER = [
    "cpu",
    "gpu",
    "aio",
    "mb",
    "disk",
    "other",
    "fans_gpu",
    "fans_hwmon",
    "fans_openfan",
]

# Series-key envelope for a fan row: "fan:<fan_id>:rpm". Fan ids contain colons of
# their own, so callers slice these off rather than splitting on ":".
_FAN_KEY_PREFIX = "fan:"
_FAN_KEY_SUFFIX = ":rpm"

_GROUP_LABELS = {
    "cpu": "CPU",
    "gpu": "GPU",
    "aio": "AIO / Liquid",
    "mb": "Motherboard",
    "disk": "Disk",
    "other": "Other",
    "fans_gpu": "Fans \u2014 D-GPU",
    "fans_hwmon": "Fans \u2014 hwmon",
    "fans_openfan": "Fans \u2014 OpenFan",
}


class _FanRowDelegate(QStyledItemDelegate):
    """Editing rules for the series tree (DEC-227).

    Two column-scoped jobs:

    * ``Qt.ItemIsEditable`` is a per-*item* flag, so marking a fan row editable
      would otherwise let a stray double-click type over its "1200 RPM" reading
      or its colour swatch. Refusing an editor outside column 0 scopes editing
      to the name.
    * ``setModelData`` is the commit hook. It hands the typed text to the panel
      and deliberately does **not** call ``super()``, so Qt never writes the raw
      text into the item — the panel re-renders the row from the alias that was
      actually stored, which may differ (``AppState.apply_fan_rename`` strips the
      "(AIO)" tag, caps length, and treats the fallback name as "clear"). It also
      means ``itemChanged`` never fires for a rename, so the checkbox ->
      ``SeriesSelectionModel`` path in ``_on_item_changed`` stays untouched.
    """

    NAME_COLUMN = 0

    def __init__(self, panel: SensorSeriesPanel) -> None:
        super().__init__(panel)
        self._panel = panel

    def createEditor(self, parent: QWidget, option, index: QModelIndex):
        if index.column() != self.NAME_COLUMN:
            return None
        return super().createEditor(parent, option, index)

    def setModelData(self, editor: QWidget, model, index: QModelIndex) -> None:
        if index.column() != self.NAME_COLUMN:
            return
        fan_id = self._panel.fan_id_for_index(index)
        if fan_id:
            self._panel.rename_fan(fan_id, editor.text())


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
        self._aio_ids: set[str] = set()  # DEC-157 liquid-cooler header ids

        self._build_ui()
        self._selection.selection_changed.connect(self._sync_checkboxes_from_model)
        # DEC-227: a rename from any surface (this panel, a fan card, the Overview
        # table, the wizard) repaints the affected row without waiting for a poll.
        if self._state:
            self._state.fan_alias_changed.connect(self._on_fan_alias_changed)

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
        # DEC-227: in-place fan rename. Editing uses Qt's default DoubleClicked |
        # EditKeyPressed triggers (double-click the name, or F2) — the delegate
        # keeps the editor off the value/colour columns and owns the commit.
        self._tree.setItemDelegate(_FanRowDelegate(self))
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
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
        # Temporarily clear the app stylesheet so its rules cannot reach
        # QColorDialog's internal custom-painted widgets (spectrum, hue strip,
        # preview) — an app-level rule cascades into every child and a
        # dialog-level setStyleSheet() cannot override it. The theme palette
        # stays applied, so the dialog still follows the active theme (DEC-226).
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

        # Once per pass, not once per row — both branches below label rows from it.
        self._refresh_aio_ids()

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
                # Default-visible: at first discovery the key is not yet in the
                # selection model (the dashboard registers it after this
                # rebuild), so is_visible() would be False here and the row
                # would start unchecked — and the group's next itemChanged
                # would sync that unchecked state into the model, permanently
                # hiding every new series. Only an *explicit* hide unchecks.
                item.setCheckState(
                    0,
                    Qt.CheckState.Unchecked
                    if self._selection.is_hidden(series_key)
                    else Qt.CheckState.Checked,
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

        # Cosmetic text-only updates \u2014 block signals so the group row's
        # itemChanged doesn't fire and sync child check-states into the
        # selection model as a side effect of a setText().
        with block_signals(self._tree):
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

    # ── Fan row labelling / renaming ─────────────────────────────────

    def _refresh_aio_ids(self) -> None:
        """Cache the DEC-157 liquid-cooler header ids for this update pass."""
        self._aio_ids = (
            {h.id for h in self._state.hwmon_headers if getattr(h, "is_aio", False)}
            if self._state
            else set()
        )

    def _fan_row_label(self, fan_id: str) -> str:
        """Build a fan row's name cell — display name plus the DEC-157 "(AIO)" tag.

        The single place a fan row label is composed. It used to be built inline
        in ``_rebuild_fan_items`` while ``_update_fan_values`` recomputed a bare
        ``fan_display_name``, so the "(AIO)" tag survived exactly one poll before
        the next tick overwrote it (DEC-227). One formatter makes that class of
        drift structurally impossible.
        """
        if not self._state:
            return fan_id
        label = self._state.fan_display_name(fan_id)
        if fan_id in self._aio_ids:
            label = f"{label}{AIO_SUFFIX}"
        return label

    @staticmethod
    def _fan_id_for_item(item: QTreeWidgetItem) -> str:
        """Recover the fan id from a row's series key, or "" for a non-fan row.

        Sliced rather than split: fan ids contain colons of their own
        (``hwmon:it8696:it87.2624:pwm1:pwm1``).
        """
        key = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if key.startswith(_FAN_KEY_PREFIX) and key.endswith(_FAN_KEY_SUFFIX):
            return key[len(_FAN_KEY_PREFIX) : -len(_FAN_KEY_SUFFIX)]
        return ""

    def fan_id_for_index(self, index: QModelIndex) -> str:
        """Fan id behind a model index, or "" if it is not a fan row."""
        item = self._tree.itemFromIndex(index)
        return self._fan_id_for_item(item) if item else ""

    def rename_fan(self, fan_id: str, text: str) -> None:
        """Apply an inline rename (DEC-227). No dialog — callable straight from tests.

        Delegates the rule to :meth:`AppState.apply_fan_rename` so this panel, the
        read-only fan cards and the Overview fan table cannot drift apart. The row
        is re-rendered by the resulting ``fan_alias_changed`` signal rather than
        here, so a rename originating anywhere lands identically.
        """
        if self._state:
            self._state.apply_fan_rename(fan_id, text)

    def _on_fan_alias_changed(self, fan_id: str, _display_name: str) -> None:
        """Re-render one fan row immediately rather than waiting for the next poll."""
        item = self._fan_items.get(fan_id)
        if item is None:
            return
        with block_signals(self._tree):
            item.setText(0, self._fan_row_label(fan_id))
        self._apply_search_filter()

    def build_fan_menu(self, item: QTreeWidgetItem | None) -> QMenu | None:
        """Build the right-click menu for *item*, or None if it is not a fan row.

        Kept separate from showing it so the menu's contents are assertable
        without driving a real popup. Sensor rows get no menu: sensor labels are
        daemon-owned and there is no sensor-alias setting to write.
        """
        if item is None:
            return None
        fan_id = self._fan_id_for_item(item)
        if not fan_id:
            return None
        menu = QMenu(self)
        rename = QAction("Rename fan…", self)
        rename.setObjectName("SensorSeriesPanel_Action_renameFan")
        rename.triggered.connect(lambda: self._tree.editItem(item, 0))
        menu.addAction(rename)
        if self._state and fan_id in self._state.fan_aliases:
            reset = QAction("Reset to default name", self)
            reset.setObjectName("SensorSeriesPanel_Action_resetFanName")
            reset.triggered.connect(lambda: self.rename_fan(fan_id, ""))
            menu.addAction(reset)
        return menu

    def _on_context_menu(self, pos: QPoint) -> None:
        """Right-click a fan row to rename it.

        Double-click and F2 already open the editor; this exists because neither
        is discoverable on its own.
        """
        menu = self.build_fan_menu(self._tree.itemAt(pos))
        if menu is not None:
            menu.exec(self._tree.viewport().mapToGlobal(pos))

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
                if f.source in ("amd_gpu", "intel_gpu", "nvidia_gpu"):
                    group_key = "fans_gpu"
                elif "hwmon" in f.source:
                    group_key = "fans_hwmon"
                else:
                    group_key = "fans_openfan"
                group_label = _GROUP_LABELS[group_key]
                group_item = self._ensure_group(group_key, group_label)

                series_key = f"{_FAN_KEY_PREFIX}{f.id}{_FAN_KEY_SUFFIX}"
                rpm_text = f"{f.rpm} RPM" if f.rpm is not None else "\u2014"

                item = QTreeWidgetItem(group_item)
                item.setText(0, self._fan_row_label(f.id))
                item.setText(1, rpm_text)
                item.setToolTip(0, f"ID: {f.id}\nDouble-click or press F2 to rename")
                item.setData(0, Qt.ItemDataRole.UserRole, series_key)
                # DEC-227: fan rows are renamable in place; sensor rows are not
                # (sensor labels are daemon-owned and there is no alias setting
                # for them). _FanRowDelegate scopes the editor to column 0.
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
                )
                # Default-visible at first discovery — see _rebuild_sensor_items.
                item.setCheckState(
                    0,
                    Qt.CheckState.Unchecked
                    if self._selection.is_hidden(series_key)
                    else Qt.CheckState.Checked,
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
                # Re-resolve the name in case an alias changed. Must go through
                # _fan_row_label \u2014 recomputing a bare fan_display_name here is
                # what used to erase the "(AIO)" tag on the second poll.
                label = self._fan_row_label(f.id)
                if item.text(0) != label:
                    item.setText(0, label)

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
