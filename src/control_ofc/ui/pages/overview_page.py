"""Overview page — merged Diagnostics ▸ Overview + Fans + Sensors (DEC-209).

A thin renderer over the Qt-free ``services.overview_view`` view-models, styled
with the Stage-1 component library (cards, dense tables, status pills). Fed
directly by the 1 Hz poll (`AppState` signals). Full parity with the Sensors tab:
hide/unhide, mirror-to-dashboard, coolant + preferred-sensor right-click, the
Details dialog (double-click / right-click / Enter), the summary line, and the
DEC-193 unavailable panel — all on the same shared services, so hiding/overriding
here reflects on every other live surface rendering the same sensors (and vice
versa), via ``AppState`` signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.overview_view import (
    build_daemon_health_vm,
    build_device_discovery_vm,
    build_fan_rows,
    build_sensor_rows,
    build_sensor_summary,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.sensor_detail_dialog import SensorDetailDialog

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.api.models import SensorReading
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState
    from control_ofc.services.series_selection import SeriesSelectionModel

_FAN_COLS = ["Name", "Source", "Control method", "RPM", "PWM (%)", "Freshness"]
_FAN_FRESH_COL = 5

_SENSOR_COLS = [
    "#",
    "Label",
    "Sensor ID",
    "Source class",
    "Chip",
    "Value (°C)",
    "Age (ms)",
    "Confidence",
]
_S_ID = 2  # Sensor ID column index (used by _row_to_sensor — order-robust)
_S_LABEL = 1
_S_VALUE = 5
_S_CONF = 7


class _KeyTable(QTableWidget):
    """QTableWidget that emits ``return_pressed(row)`` on Enter/Return.

    Restores the keyboard path to the Details dialog that the removed per-row
    Details button used to provide (DEC-209 8-column table).
    """

    return_pressed = Signal(int)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.return_pressed.emit(self.currentRow())
            return
        super().keyPressEvent(event)


class OverviewPage(QWidget):
    """The merged Overview page (system status + fan table + sensor table)."""

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        settings_service: AppSettingsService | None = None,
        series_selection: SeriesSelectionModel | None = None,
        client: DaemonClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Overview_Root")
        self._state = state
        self._diag = diagnostics_service or DiagnosticsService(state)
        self._settings_service = settings_service
        self._series_selection = series_selection
        self._client = client

        # Cached poll state.
        self._caps = None
        self._status = None
        self._all_sensors: list = []
        self._unavailable_sensors: list = []
        self._hidden_group_expanded = False
        self._sensor_detail_dialog: SensorDetailDialog | None = None
        self._daemon_classifications: dict = {}
        self._daemon_classifications_loaded = False
        self._preferred_sensor_unsupported = False

        self._build_ui()

        if state is not None:
            state.capabilities_updated.connect(self._on_caps)
            state.status_updated.connect(self._on_status)
            state.sensors_updated.connect(self._on_sensors)
            state.fans_updated.connect(self._on_fans)
            # A classification override set from any sensor surface (here, or
            # elsewhere) re-renders this table so every surface stays in step.
            state.sensor_class_override_changed.connect(lambda *_: self._render_sensors_table())
            # DEC-227: a rename from any surface repaints this table immediately
            # instead of leaving a stale name until the next poll. Bound method,
            # not a lambda capturing `state`: a lambda's lifetime is the sender's,
            # so an AppState outliving this page would re-enter _on_fans against
            # a deleted C++ widget. PySide6 drops a bound-method connection when
            # the receiver is collected. Matters here because the py3.12 CI job
            # has an open PySide6 teardown-lifetime segfault (see CLAUDE.md).
            state.fan_alias_changed.connect(self._on_fan_alias_changed)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        scroll.setWidget(body)

        layout.addLayout(self._build_cards_row())

        layout.addWidget(SectionHeader("Fan Status", object_name="Overview_SectionHeader_fans"))
        self._fan_table = QTableWidget(0, len(_FAN_COLS))
        self._fan_table.setObjectName("Overview_Table_fans")
        self._fan_table.setHorizontalHeaderLabels(_FAN_COLS)
        apply_dense_table(self._fan_table)
        self._fan_table.horizontalHeader().setStretchLastSection(True)
        self._fan_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._fan_table.customContextMenuRequested.connect(self._on_fan_context_menu)
        layout.addWidget(self._fan_table)

        sensor_header = SectionHeader(
            "Sensor Intelligence", object_name="Overview_SectionHeader_sensors"
        )
        self._sensor_summary_label = QLabel("Sensors: —")
        self._sensor_summary_label.setObjectName("Overview_Label_sensorSummary")
        self._sensor_summary_label.setProperty("class", "CardMeta")
        sensor_header.add_trailing(self._sensor_summary_label)
        self._mirror_btn = make_button(
            "Mirror hidden to dashboard", "ghost", object_name="Overview_Btn_mirrorHidden"
        )
        self._mirror_btn.setToolTip(
            "Hide the sensors hidden here from the dashboard chart too (one-shot)."
        )
        self._mirror_btn.clicked.connect(self._mirror_hidden_to_dashboard)
        if self._series_selection is None:
            self._mirror_btn.hide()
        sensor_header.add_trailing(self._mirror_btn)
        layout.addWidget(sensor_header)

        self._sensor_table = _KeyTable(0, len(_SENSOR_COLS))
        self._sensor_table.setObjectName("Overview_Table_sensors")
        self._sensor_table.setHorizontalHeaderLabels(_SENSOR_COLS)
        apply_dense_table(self._sensor_table)
        self._sensor_table.horizontalHeader().setStretchLastSection(True)
        self._sensor_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sensor_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._sensor_table.customContextMenuRequested.connect(self._on_sensor_context_menu)
        self._sensor_table.cellDoubleClicked.connect(self._on_sensor_cell_double_clicked)
        self._sensor_table.return_pressed.connect(self._on_sensor_return)
        self._sensor_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        layout.addWidget(self._sensor_table)

        self._unavailable_label = QLabel("")
        self._unavailable_label.setObjectName("Overview_Label_unavailableSensors")
        self._unavailable_label.setProperty("class", "CardMeta")
        self._unavailable_label.setWordWrap(True)
        self._unavailable_label.setVisible(False)
        layout.addWidget(self._unavailable_label)
        layout.addStretch(1)

    def _build_cards_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        # Daemon-health card.
        daemon_card = Card()
        daemon_card.setObjectName("Overview_Card_daemonHealth")
        dl = QVBoxLayout(daemon_card)
        title_row = QHBoxLayout()
        self._daemon_version_label = _meta(
            QLabel("Daemon: —"), "Overview_Label_daemonVersion", cls="PageSubtitle"
        )
        title_row.addWidget(self._daemon_version_label)
        self._daemon_status_pill = StatusPill("—", "neutral")
        self._daemon_status_pill.setObjectName("Overview_Pill_daemonStatus")
        title_row.addWidget(self._daemon_status_pill)
        title_row.addStretch(1)
        dl.addLayout(title_row)
        self._daemon_status_label = _meta(QLabel("Status: —"), "Overview_Label_daemonStatus")
        dl.addWidget(self._daemon_status_label)
        self._daemon_uptime_label = _meta(
            QLabel("Uptime: —"), "Overview_Label_daemonUptime", cls="CardMeta"
        )
        dl.addWidget(self._daemon_uptime_label)
        self._subsystems_label = _meta(
            QLabel("Subsystems: —"), "Overview_Label_subsystems", wrap=True
        )
        dl.addWidget(self._subsystems_label)
        self._overrides_label = _meta(
            QLabel("Overrides: —"), "Overview_Label_overrides", cls="CardMeta", wrap=True
        )
        dl.addWidget(self._overrides_label)
        self._age_note_label = _meta(QLabel(""), "Overview_Label_ageNote", cls="CardMeta")
        dl.addWidget(self._age_note_label)
        dl.addStretch(1)
        row.addWidget(daemon_card, 1)

        # Device-discovery card.
        device_card = Card()
        device_card.setObjectName("Overview_Card_deviceDiscovery")
        vl = QVBoxLayout(device_card)
        vl.addWidget(
            _meta(QLabel("Device Discovery"), "Overview_Label_deviceTitle", cls="PageSubtitle")
        )
        self._openfan_label = _meta(QLabel("OpenFan: —"), "Overview_Label_openfan")
        vl.addWidget(self._openfan_label)
        hwmon_row = QHBoxLayout()
        self._hwmon_label = _meta(QLabel("hwmon: —"), "Overview_Label_hwmon")
        hwmon_row.addWidget(self._hwmon_label)
        self._hwmon_warn_pill = StatusPill("read-only", "warning")
        self._hwmon_warn_pill.setObjectName("Overview_Pill_hwmonWarn")
        self._hwmon_warn_pill.hide()
        hwmon_row.addWidget(self._hwmon_warn_pill)
        hwmon_row.addStretch(1)
        vl.addLayout(hwmon_row)
        self._amd_gpu_label = _meta(QLabel("AMD GPU: —"), "Overview_Label_amdGpu")
        vl.addWidget(self._amd_gpu_label)
        self._intel_gpu_label = _meta(QLabel("Intel GPU: —"), "Overview_Label_intelGpu")
        vl.addWidget(self._intel_gpu_label)
        self._nvidia_gpu_label = _meta(QLabel("NVIDIA GPU: —"), "Overview_Label_nvidiaGpu")
        vl.addWidget(self._nvidia_gpu_label)
        self._aio_label = _meta(QLabel("Liquid cooling: —"), "Overview_Label_aio")
        vl.addWidget(self._aio_label)
        self._features_label = _meta(QLabel("Features: —"), "Overview_Label_features")
        vl.addWidget(self._features_label)
        vl.addStretch(1)
        row.addWidget(device_card, 1)
        return row

    # ── Poll handlers ────────────────────────────────────────────────

    def _on_caps(self, caps) -> None:
        self._caps = caps
        self._render_cards()

    def _on_status(self, status) -> None:
        self._status = status
        self._unavailable_sensors = list(status.unavailable_sensors)
        self._render_cards()
        self._render_unavailable_sensors()

    def _on_sensors(self, sensors: list) -> None:
        self._all_sensors = list(sensors)
        self._render_sensors_table()

    def _on_fans(self, fans: list) -> None:
        headers = self._state.hwmon_headers if self._state else []
        caps = self._state.capabilities if self._state else None
        name_fn = self._state.fan_display_name if self._state else (lambda x: x)
        rows = build_fan_rows(fans, headers, caps, name_fn)
        self._clear_cell_widgets(self._fan_table, _FAN_FRESH_COL)
        self._fan_table.setRowCount(len(rows))
        for r, vm in enumerate(rows):
            _ensure_items(self._fan_table, r, len(_FAN_COLS))
            for c, text in enumerate(
                (vm.name, vm.source, vm.control_method, vm.rpm_text, vm.pwm_text)
            ):
                item = self._fan_table.item(r, c)
                item.setText(text)
                item.setToolTip(vm.control_method_tooltip if c == 2 else vm.row_tooltip)
            # DEC-227: the table has no ID column, so carry the id on the row's
            # first cell — that is how a right-click finds the fan it named.
            self._fan_table.item(r, 0).setData(Qt.ItemDataRole.UserRole, vm.fan_id)
            self._set_pill(
                self._fan_table, r, _FAN_FRESH_COL, vm.freshness_label, vm.freshness_state
            )

    # ── Card rendering ───────────────────────────────────────────────

    def _render_cards(self) -> None:
        writable = None
        if self._diag.last_hw_diagnostics is not None:
            writable = self._diag.last_hw_diagnostics.hwmon.writable_headers

        dh = build_daemon_health_vm(self._caps, self._status)
        self._daemon_version_label.setText(dh.version_text)
        self._daemon_status_label.setText(dh.status_text)
        status_word = dh.status_text.removeprefix("Status: ")
        self._daemon_status_pill.set_text(status_word)
        self._daemon_status_pill.set_state(dh.status_state)
        self._daemon_uptime_label.setText(dh.uptime_text)
        self._subsystems_label.setText(dh.subsystems_text)
        self._overrides_label.setText(dh.overrides_text)
        self._age_note_label.setText(dh.age_note)

        dd = build_device_discovery_vm(self._caps, writable)
        self._openfan_label.setText(dd.openfan)
        self._hwmon_label.setText(dd.hwmon)
        set_chip_class(self._hwmon_label, "WarningChip" if dd.hwmon_warn else "")
        self._hwmon_warn_pill.setVisible(dd.hwmon_warn)
        self._amd_gpu_label.setText(dd.amd_gpu)
        self._intel_gpu_label.setText(dd.intel_gpu)
        self._nvidia_gpu_label.setText(dd.nvidia_gpu)
        self._aio_label.setText(dd.aio)
        self._features_label.setText(dd.features)

    def _render_unavailable_sensors(self) -> None:
        rows = self._unavailable_sensors
        if not rows:
            self._unavailable_label.setVisible(False)
            self._unavailable_label.setText("")
        else:
            lines = [
                f"⚠ Unavailable sensors ({len(rows)}) — discovered but not readable, "
                "excluded from fan control:"
            ]
            for u in rows:
                secs = max(0, u.unavailable_for_ms // 1000)
                lines.append(f"   • {u.label or u.id} — {u.reason} (unavailable {secs}s)")
            self._unavailable_label.setText("\n".join(lines))
            self._unavailable_label.setVisible(True)
        self._refresh_summary()

    # ── Sensor table rendering ───────────────────────────────────────

    def _board_vendor(self) -> str:
        if self._diag.last_hw_diagnostics is not None:
            return self._diag.last_hw_diagnostics.board.vendor
        return ""

    def _refresh_summary(self) -> None:
        hidden_ids = self._hidden_sensor_ids()
        self._sensor_summary_label.setText(
            build_sensor_summary(
                self._all_sensors,
                hidden_count=sum(1 for s in self._all_sensors if s.id in hidden_ids),
                unavailable_count=len(self._unavailable_sensors),
                board_vendor=self._board_vendor(),
            )
        )

    def _render_sensors_table(self) -> None:
        hidden_ids = self._hidden_sensor_ids()
        visible = [s for s in self._all_sensors if s.id not in hidden_ids]
        hidden = [s for s in self._all_sensors if s.id in hidden_ids]
        board_vendor = self._board_vendor()
        overrides = self._sensor_overrides()

        total = len(visible)
        if hidden:
            total += 1 + (len(hidden) if self._hidden_group_expanded else 0)

        self._clear_cell_widgets(self._sensor_table, _S_CONF)
        self._sensor_table.setRowCount(total)
        self._sensor_table.clearSpans()

        for i, vm in enumerate(
            build_sensor_rows(visible, overrides=overrides, board_vendor=board_vendor)
        ):
            self._populate_sensor_row(i, vm, dimmed=False)

        if hidden:
            toggle_row = len(visible)
            self._set_hidden_toggle_row(toggle_row, len(hidden))
            if self._hidden_group_expanded:
                for j, vm in enumerate(
                    build_sensor_rows(hidden, overrides=overrides, board_vendor=board_vendor)
                ):
                    self._populate_sensor_row(toggle_row + 1 + j, vm, dimmed=True)

        self._refresh_summary()

    def _populate_sensor_row(self, row: int, vm, *, dimmed: bool) -> None:
        _ensure_items(self._sensor_table, row, len(_SENSOR_COLS) - 1)  # Confidence holds a widget
        theme = active_theme()
        row_color = theme.text_secondary if dimmed else theme.text_primary
        texts = [
            str(row + 1),
            vm.label,
            vm.sensor_id,
            vm.source_class_text,
            vm.chip,
            vm.value_text,
            vm.age_text,
        ]
        for col, text in enumerate(texts):
            item = self._sensor_table.item(row, col)
            item.setText(text)
            item.setForeground(QColor(row_color))
            item.setToolTip(vm.tooltip)
        if vm.is_quirky:
            self._sensor_table.item(row, _S_LABEL).setForeground(QColor(theme.status_warn))
        if vm.is_alarm:
            self._sensor_table.item(row, _S_VALUE).setForeground(QColor(theme.status_crit))
        self._set_pill(self._sensor_table, row, _S_CONF, vm.confidence_label, vm.confidence_state)

    def _set_hidden_toggle_row(self, row: int, hidden_count: int) -> None:
        ncols = len(_SENSOR_COLS)
        for col in range(ncols):
            if self._sensor_table.item(row, col) is None:
                self._sensor_table.setItem(row, col, QTableWidgetItem(""))
            else:
                self._sensor_table.item(row, col).setText("")
        self._sensor_table.setSpan(row, 0, 1, ncols)
        arrow = "▾" if self._hidden_group_expanded else "▸"
        verb = "collapse" if self._hidden_group_expanded else "expand"
        suffix = "" if hidden_count == 1 else "s"
        toggle = self._sensor_table.item(row, 0)
        toggle.setText(f"  {arrow} {hidden_count} hidden sensor{suffix} (click to {verb})")
        toggle.setToolTip(
            "Hidden sensors stay reachable here. Right-click a hidden row to unhide it."
        )
        toggle.setForeground(QColor(active_theme().text_secondary))

    # ── Sensor interactions (ported, on the shared services) ─────────

    def _hidden_sensor_ids(self) -> set[str]:
        if self._settings_service is None:
            return set()
        return set(
            getattr(self._settings_service.settings, "diagnostics_hidden_sensor_ids", []) or []
        )

    def _set_hidden_sensor_ids(self, ids: list[str]) -> None:
        if self._settings_service is not None:
            self._settings_service.update(diagnostics_hidden_sensor_ids=ids)

    def _set_sensor_hidden(self, sensor_id: str, hidden: bool) -> None:
        current = list(self._hidden_sensor_ids())
        if hidden and sensor_id not in current:
            current.append(sensor_id)
        elif not hidden and sensor_id in current:
            current.remove(sensor_id)
        else:
            return
        self._set_hidden_sensor_ids(current)
        self._render_sensors_table()

    def _sensor_overrides(self) -> dict[str, str]:
        if self._state is not None:
            return self._state.sensor_class_overrides
        if self._settings_service is not None:
            return dict(
                getattr(self._settings_service.settings, "sensor_class_overrides", {}) or {}
            )
        return {}

    def _set_sensor_class_override(self, sensor_id: str, source_class: str) -> None:
        if self._state is not None:
            self._state.set_sensor_class_override(sensor_id, source_class)
        elif self._settings_service is not None:
            overrides = dict(
                getattr(self._settings_service.settings, "sensor_class_overrides", {}) or {}
            )
            if source_class:
                overrides[sensor_id] = source_class
            else:
                overrides.pop(sensor_id, None)
            self._settings_service.update(sensor_class_overrides=overrides)
        self._render_sensors_table()

    def _mirror_hidden_to_dashboard(self) -> None:
        if self._series_selection is None:
            return
        for sensor_id in self._hidden_sensor_ids():
            self._series_selection.set_visible(f"sensor:{sensor_id}", False)

    def _toggle_hidden_group(self) -> None:
        self._hidden_group_expanded = not self._hidden_group_expanded
        self._render_sensors_table()

    def _on_fan_alias_changed(self, _fan_id: str, _display_name: str) -> None:
        """Repaint the fan table after a rename from any surface (DEC-227)."""
        if self._state is not None:
            self._on_fans(self._state.fans)

    def _row_to_fan_id(self, row: int) -> str:
        """Fan/header id behind a fan-table row, or "" if there is none."""
        if row < 0 or row >= self._fan_table.rowCount():
            return ""
        cell = self._fan_table.item(row, 0)
        if cell is None:
            return ""
        return cell.data(Qt.ItemDataRole.UserRole) or ""

    def build_fan_menu(self, fan_id: str) -> QMenu | None:
        """Build the fan-row right-click menu, or None when there is nothing to name.

        Split from showing it so the contents are assertable without a real popup.
        """
        if not fan_id or self._state is None:
            return None
        menu = QMenu(self)
        rename = QAction("Rename fan…", self)
        rename.setObjectName("Overview_Action_renameFan")
        rename.triggered.connect(lambda: self._prompt_fan_rename(fan_id))
        menu.addAction(rename)
        if fan_id in self._state.fan_aliases:
            reset = QAction("Reset to default name", self)
            reset.setObjectName("Overview_Action_resetFanName")
            reset.triggered.connect(lambda: self._state.apply_fan_rename(fan_id, ""))
            menu.addAction(reset)
        return menu

    def _on_fan_context_menu(self, pos: QPoint) -> None:
        """Right-click a fan row to rename it (DEC-227).

        Mirrors the Sensors rail: the label shown here is the same
        ``fan_display_name``, so it should be authorable from here too.
        """
        menu = self.build_fan_menu(self._row_to_fan_id(self._fan_table.indexAt(pos).row()))
        if menu is not None:
            menu.exec(self._fan_table.viewport().mapToGlobal(pos))

    def _prompt_fan_rename(self, fan_id: str) -> None:
        """Ask for a new fan name. The rule itself lives on AppState."""
        if self._state is None:
            return
        current = self._state.fan_display_name(fan_id)
        name, ok = QInputDialog.getText(self, "Rename Fan", "Fan name:", text=current)
        if ok:
            self._state.apply_fan_rename(fan_id, name)

    def _row_to_sensor(self, row: int) -> SensorReading | None:
        if row < 0 or row >= self._sensor_table.rowCount() or self._is_hidden_toggle_row(row):
            return None
        cell = self._sensor_table.item(row, _S_ID)
        if cell is None:
            return None
        sensor_id = cell.text()
        if not sensor_id or sensor_id == "—":
            return None
        return next((s for s in self._all_sensors if s.id == sensor_id), None)

    def _is_hidden_toggle_row(self, row: int) -> bool:
        if row < 0 or row >= self._sensor_table.rowCount():
            return False
        return self._sensor_table.columnSpan(row, 0) == len(_SENSOR_COLS)

    def _on_sensor_return(self, row: int) -> None:
        sensor = self._row_to_sensor(row)
        if sensor is not None:
            self._open_sensor_detail(sensor.id)

    def _on_sensor_cell_double_clicked(self, row: int, _column: int) -> None:
        sensor = self._row_to_sensor(row)
        if sensor is None:
            if self._is_hidden_toggle_row(row):
                self._toggle_hidden_group()
            return
        self._open_sensor_detail(sensor.id)

    def _on_sensor_context_menu(self, pos: QPoint) -> None:
        row = self._sensor_table.indexAt(pos).row()
        if row < 0:
            return
        if self._is_hidden_toggle_row(row):
            menu = QMenu(self)
            toggle_action = QAction(
                "Collapse hidden group" if self._hidden_group_expanded else "Expand hidden group",
                self,
            )
            toggle_action.triggered.connect(self._toggle_hidden_group)
            menu.addAction(toggle_action)
            menu.exec(self._sensor_table.viewport().mapToGlobal(pos))
            return

        sensor = self._row_to_sensor(row)
        if sensor is None:
            return
        menu = QMenu(self)
        detail_action = QAction("Open detail…", self)
        detail_action.setObjectName("Overview_Action_openDetail")
        detail_action.triggered.connect(lambda: self._open_sensor_detail(sensor.id))
        menu.addAction(detail_action)

        if sensor.id in self._hidden_sensor_ids():
            unhide = QAction("Unhide sensor", self)
            unhide.setObjectName("Overview_Action_unhideSensor")
            unhide.triggered.connect(lambda: self._set_sensor_hidden(sensor.id, False))
            menu.addAction(unhide)
        else:
            hide = QAction("Hide sensor", self)
            hide.setObjectName("Overview_Action_hideSensor")
            hide.triggered.connect(lambda: self._set_sensor_hidden(sensor.id, True))
            menu.addAction(hide)

        menu.addSeparator()
        if self._sensor_overrides().get(sensor.id) == "coolant":
            reset = QAction("Reset classification to auto", self)
            reset.setObjectName("Overview_Action_resetSensorClass")
            reset.triggered.connect(lambda: self._set_sensor_class_override(sensor.id, ""))
            menu.addAction(reset)
        else:
            coolant = QAction("Treat as coolant", self)
            coolant.setObjectName("Overview_Action_treatAsCoolant")
            coolant.triggered.connect(lambda: self._set_sensor_class_override(sensor.id, "coolant"))
            menu.addAction(coolant)

        if self._client is not None and not self._preferred_sensor_unsupported:
            menu.addSeparator()
            pref_cpu = QAction("Set as preferred CPU sensor", self)
            pref_cpu.setObjectName("Overview_Action_setPreferredCpu")
            pref_cpu.triggered.connect(lambda: self._set_preferred_sensor(sensor.id, "cpu"))
            menu.addAction(pref_cpu)
            pref_mb = QAction("Set as preferred motherboard sensor", self)
            pref_mb.setObjectName("Overview_Action_setPreferredMb")
            pref_mb.triggered.connect(lambda: self._set_preferred_sensor(sensor.id, "mb"))
            menu.addAction(pref_mb)

        menu.exec(self._sensor_table.viewport().mapToGlobal(pos))

    def _set_preferred_sensor(self, sensor_id: str, role: str) -> None:
        if self._client is None:
            return
        from control_ofc.api.errors import DaemonError

        try:
            if role == "cpu":
                self._client.set_preferred_cpu_sensor(sensor_id)
            else:
                self._client.set_preferred_mb_sensor(sensor_id)
        except DaemonError as e:
            if getattr(e, "status", None) == 404:
                self._preferred_sensor_unsupported = True
            return
        except (ConnectionError, OSError):
            return

    def _ensure_daemon_classifications(self) -> None:
        if self._daemon_classifications_loaded or self._client is None:
            return
        self._daemon_classifications_loaded = True
        from control_ofc.api.errors import DaemonError

        try:
            inv = self._client.inventory_hwmon()
        except (DaemonError, ConnectionError, OSError):
            return
        self._daemon_classifications = {s.id: s for s in inv.temp_sensors}

    def _open_sensor_detail(self, sensor_id: str) -> None:
        sensor = next((s for s in self._all_sensors if s.id == sensor_id), None)
        if sensor is None:
            return
        board = self._diag.last_hw_diagnostics.board if self._diag.last_hw_diagnostics else None
        self._ensure_daemon_classifications()
        daemon_cls = self._daemon_classifications.get(sensor_id)
        if self._sensor_detail_dialog is None:
            self._sensor_detail_dialog = SensorDetailDialog(sensor, board, daemon_cls, parent=self)
            self._sensor_detail_dialog.finished.connect(self._on_sensor_detail_closed)
        else:
            self._sensor_detail_dialog.set_sensor(sensor, board, daemon_cls)
        self._sensor_detail_dialog.show()
        self._sensor_detail_dialog.raise_()
        self._sensor_detail_dialog.activateWindow()

    @Slot(int)
    def _on_sensor_detail_closed(self, _result: int) -> None:
        self._sensor_detail_dialog = None

    # ── Theme + teardown ─────────────────────────────────────────────

    def set_theme(self, _tokens) -> None:
        """Re-render tables + cards so pill states + item colours track the theme."""
        self._render_cards()
        self._render_unavailable_sensors()
        if self._state is not None:
            self._on_fans(self._state.fans)
            self._render_sensors_table()

    def cleanup(self) -> None:
        if self._sensor_detail_dialog is not None:
            self._sensor_detail_dialog.close()
            self._sensor_detail_dialog = None

    # ── Small helpers ────────────────────────────────────────────────

    @staticmethod
    def _clear_cell_widgets(table: QTableWidget, col: int) -> None:
        for row in range(table.rowCount()):
            table.removeCellWidget(row, col)

    @staticmethod
    def _set_pill(table: QTableWidget, row: int, col: int, text: str, state: str) -> None:
        # Left-align a compact pill in the (stretched) column; both the holder and
        # the pill are mouse-transparent so row selection / context / double-click
        # still resolve via the table's indexAt().
        pill = StatusPill(text, state)
        pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(0)
        lay.addWidget(pill)
        lay.addStretch(1)
        table.setCellWidget(row, col, holder)


def _meta(label: QLabel, object_name: str, *, cls: str = "", wrap: bool = False) -> QLabel:
    label.setObjectName(object_name)
    label.setStyleSheet("background: transparent;")
    if cls:
        label.setProperty("class", cls)
    if wrap:
        label.setWordWrap(True)
    return label


def _ensure_items(table: QTableWidget, row: int, ncols: int) -> None:
    for col in range(ncols):
        if table.item(row, col) is None:
            table.setItem(row, col, QTableWidgetItem())
