"""Interactive fan curve editor — draggable points on a temp→output graph.

Supports: drag, add/remove point, keyboard nudges, undo/redo, presets,
live sensor reading marker, and a synced numeric table.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.profile_service import CurveConfig, CurvePoint, CurveType
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.theme import ThemeTokens, default_dark_theme

# Constraints
MIN_TEMP = 0.0
MAX_TEMP = 120.0
MIN_OUTPUT = 0.0
MAX_OUTPUT = 100.0
MIN_TEMP_SPACING = 0.5  # minimum °C between adjacent points
MIN_POINTS = 2
MAX_UNDO = 50

# Preset curves
PRESETS: dict[str, list[CurvePoint]] = {
    "Linear": [
        CurvePoint(20.0, 20.0),
        CurvePoint(40.0, 40.0),
        CurvePoint(60.0, 60.0),
        CurvePoint(80.0, 80.0),
        CurvePoint(100.0, 100.0),
    ],
    "Quiet": [
        CurvePoint(30.0, 25.0),
        CurvePoint(45.0, 30.0),
        CurvePoint(60.0, 40.0),
        CurvePoint(75.0, 55.0),
        CurvePoint(85.0, 65.0),
    ],
    "Aggressive": [
        CurvePoint(30.0, 40.0),
        CurvePoint(40.0, 60.0),
        CurvePoint(50.0, 80.0),
        CurvePoint(60.0, 95.0),
        CurvePoint(75.0, 100.0),
    ],
}


class CurveEditor(QWidget):
    """Graph + table editor for a fan curve.

    Points are draggable on the graph. Table and graph stay in sync.
    X-axis: temperature (°C). Y-axis: output (%).
    """

    curve_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._curve: CurveConfig | None = None
        self._theme = default_dark_theme()
        self._dragging_idx: int | None = None
        self._drag_active: bool = False
        self._selected_idx: int | None = None
        self._current_sensor_value: float | None = None

        # Undo/redo stacks
        self._undo_stack: list[list[CurvePoint]] = []
        self._redo_stack: list[list[CurvePoint]] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Left: graph + controls
        graph_layout = QVBoxLayout()

        # Top row: sensor selector + presets
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Sensor:"))
        self._sensor_combo = QComboBox()
        self._sensor_combo.setObjectName("CurveEditor_Combo_sensor")
        self._sensor_combo.currentIndexChanged.connect(self._on_sensor_selected)
        top_row.addWidget(self._sensor_combo, 1)
        self._last_sensor_ids: list[str] = []  # track to avoid redundant repopulation

        # Live sensor readout
        self._sensor_value_label = QLabel("")
        self._sensor_value_label.setProperty("class", "PageSubtitle")
        self._sensor_value_label.setObjectName("CurveEditor_Label_sensorValue")
        self._sensor_value_label.setToolTip("Current sensor reading and computed curve output")
        top_row.addWidget(self._sensor_value_label)

        top_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setObjectName("CurveEditor_Combo_preset")
        self._preset_combo.addItem("— Load preset —")
        for name in PRESETS:
            self._preset_combo.addItem(name)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        top_row.addWidget(self._preset_combo)

        graph_layout.addLayout(top_row)

        # Plot
        pg.setConfigOptions(antialias=True)
        self._plot_widget = pg.PlotWidget()
        self._setup_plot()
        graph_layout.addWidget(self._plot_widget, 1)

        # Below graph: add point button + coordinate display
        bottom_row = QHBoxLayout()
        self._add_btn = QPushButton("+ Add Point")
        self._add_btn.setObjectName("CurveEditor_Btn_addPoint")
        self._add_btn.setToolTip("Add a new point at the midpoint of the curve")
        self._add_btn.clicked.connect(self._on_add_point)
        bottom_row.addWidget(self._add_btn)

        self._remove_btn = QPushButton("Remove Point")
        self._remove_btn.setObjectName("CurveEditor_Btn_removePoint")
        self._remove_btn.setToolTip("Remove the selected point (min 2 points)")
        self._remove_btn.clicked.connect(self._on_remove_point)
        self._remove_btn.setEnabled(False)
        bottom_row.addWidget(self._remove_btn)

        bottom_row.addStretch()
        self._coord_label = QLabel("")
        self._coord_label.setProperty("class", "PageSubtitle")
        bottom_row.addWidget(self._coord_label)
        graph_layout.addLayout(bottom_row)

        layout.addLayout(graph_layout, 3)

        # Right: numeric table
        right_layout = QVBoxLayout()

        self._table = QTableWidget(0, 2)
        self._table.setObjectName("CurveEditor_Table_points")
        self._table.setHorizontalHeaderLabels(["Temp (°C)", "Output (%)"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setMaximumWidth(220)
        self._table.cellChanged.connect(self._on_table_edited)
        self._table.currentCellChanged.connect(self._on_table_selection_changed)
        right_layout.addWidget(self._table, 1)

        layout.addLayout(right_layout, 1)

        # Linear/Flat parameter editors (alternative to graph+table)
        self._param_widget = QWidget()
        param_layout = QVBoxLayout(self._param_widget)
        param_layout.setSpacing(8)

        self._param_type_label = QLabel("")
        self._param_type_label.setProperty("class", "PageSubtitle")
        param_layout.addWidget(self._param_type_label)

        # Linear params
        self._linear_widget = QWidget()
        lin_layout = QVBoxLayout(self._linear_widget)
        lin_layout.setContentsMargins(0, 0, 0, 0)
        for field_name, label_text, _attr in [
            ("start_temp", "Start Temperature (C):", "start_temp_c"),
            ("start_output", "Start Output (%):", "start_output_pct"),
            ("end_temp", "End Temperature (C):", "end_temp_c"),
            ("end_output", "End Output (%):", "end_output_pct"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            spin = QDoubleSpinBox()
            spin.setObjectName(f"CurveEditor_Spin_{field_name}")
            spin.setRange(0, 120 if "temp" in field_name else 100)
            spin.setDecimals(1)
            spin.valueChanged.connect(self._on_linear_param_changed)
            setattr(self, f"_lin_{field_name}", spin)
            row.addWidget(spin)
            lin_layout.addLayout(row)
        param_layout.addWidget(self._linear_widget)

        # Flat params
        self._flat_widget = QWidget()
        flat_layout = QVBoxLayout(self._flat_widget)
        flat_layout.setContentsMargins(0, 0, 0, 0)
        flat_row = QHBoxLayout()
        flat_row.addWidget(QLabel("Output (%):"))
        self._flat_output_spin = QDoubleSpinBox()
        self._flat_output_spin.setObjectName("CurveEditor_Spin_flatOutput")
        self._flat_output_spin.setRange(0, 100)
        self._flat_output_spin.setDecimals(1)
        self._flat_output_spin.valueChanged.connect(self._on_flat_param_changed)
        flat_row.addWidget(self._flat_output_spin)
        flat_layout.addLayout(flat_row)
        param_layout.addWidget(self._flat_widget)

        param_layout.addStretch()
        self._param_widget.hide()
        layout.addWidget(self._param_widget, 1)

        # Plot items
        self._line_plot: pg.PlotDataItem | None = None
        self._scatter: pg.ScatterPlotItem | None = None
        self._sensor_vline: pg.InfiniteLine | None = None
        self._sensor_marker: pg.ScatterPlotItem | None = None
        self._highlight_scatter: pg.ScatterPlotItem | None = None

        # Connect mouse events on the plot
        self._plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._plot_widget.scene().sigMouseClicked.connect(self._on_mouse_clicked)

        # Install event filter on viewport for press-to-drag (sigMouseClicked fires on release)
        self._plot_widget.viewport().installEventFilter(self)

        # Keyboard shortcuts
        self._setup_shortcuts()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_shortcuts(self) -> None:
        undo = QShortcut(QKeySequence("Ctrl+Z"), self)
        undo.activated.connect(self.undo)
        redo = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        redo.activated.connect(self.redo)
        delete = QShortcut(QKeySequence("Delete"), self)
        delete.activated.connect(self._on_remove_point)
        backspace = QShortcut(QKeySequence("Backspace"), self)
        backspace.activated.connect(self._on_remove_point)

    def set_theme(self, tokens: ThemeTokens) -> None:
        """Update theme tokens and re-render the plot with new colours."""
        self._theme = tokens
        self._setup_plot()
        self._redraw()

    def eventFilter(self, obj, event) -> bool:
        """Intercept mouse press on plot viewport for press-to-drag."""
        if obj is self._plot_widget.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton and self._curve:
                    scene_pos = self._plot_widget.mapToScene(event.position().toPoint())
                    plot = self._plot_widget.getPlotItem()
                    if plot:
                        vb = plot.vb
                        view_pos = vb.mapSceneToView(scene_pos)
                        idx = self._find_nearest_point(view_pos.x(), view_pos.y())
                        if idx is not None:
                            self._selected_idx = idx
                            self._dragging_idx = idx
                            self._drag_active = True
                            self._push_undo()
                            self._update_selection_highlight()
                            self._refresh_table()
                            return True  # consumed
            elif event.type() == QEvent.Type.MouseButtonRelease and self._drag_active:
                self._dragging_idx = None
                self._drag_active = False
                self.curve_changed.emit()
                return True
        return super().eventFilter(obj, event)

    def _setup_plot(self) -> None:
        t = self._theme
        self._plot_widget.setBackground(t.chart_bg)
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        # Disable built-in pan/zoom so our point dragging works
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)

        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setLabel("left", "Output (%)")
        plot.setLabel("bottom", "Temperature (°C)")
        plot.setYRange(0, 105, padding=0)

        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(t.chart_axis_text))
            axis.setTextPen(pg.mkPen(t.text_secondary))

    # ─── Public API ───────────────────────────────────────────────────

    def set_curve(self, curve: CurveConfig) -> None:
        self._curve = curve
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selected_idx = None

        is_graph = curve.type == CurveType.GRAPH
        # Show/hide graph vs parameter editors
        self._plot_widget.setVisible(is_graph)
        self._add_btn.setVisible(is_graph)
        self._remove_btn.setVisible(is_graph)
        self._coord_label.setVisible(is_graph)
        self._table.setVisible(is_graph)
        self._preset_combo.setVisible(is_graph)

        self._param_widget.setVisible(not is_graph)
        self._linear_widget.setVisible(curve.type == CurveType.LINEAR)
        self._flat_widget.setVisible(curve.type == CurveType.FLAT)

        if is_graph:
            self._refresh_plot()
            self._refresh_table()
        elif curve.type == CurveType.LINEAR:
            self._param_type_label.setText("Linear Curve Parameters")
            self._load_linear_params(curve)
        elif curve.type == CurveType.FLAT:
            self._param_type_label.setText("Flat Curve Parameters")
            self._load_flat_params(curve)

        # Restore sensor combo from this curve's own saved state
        self._last_sensor_ids = []  # force set_available_sensors to repopulate
        with block_signals(self._sensor_combo):
            if curve.sensor_id:
                idx = self._sensor_combo.findData(curve.sensor_id)
                if idx >= 0:
                    self._sensor_combo.setCurrentIndex(idx)

    def get_curve(self) -> CurveConfig | None:
        if self._curve is None:
            return None
        self._curve.sensor_id = self._sensor_combo.currentData() or ""
        return self._curve

    def set_available_sensors(self, sensor_ids: list[tuple[str, str]]) -> None:
        """Update sensor choices without clobbering user selection."""
        new_ids = [sid for sid, _ in sensor_ids]
        if new_ids == self._last_sensor_ids:
            return  # no change, skip repopulation
        self._last_sensor_ids = new_ids

        with block_signals(self._sensor_combo):
            self._sensor_combo.clear()
            for sid, label in sensor_ids:
                self._sensor_combo.addItem(label, sid)
            if self._curve and self._curve.sensor_id:
                idx = self._sensor_combo.findData(self._curve.sensor_id)
                if idx >= 0:
                    self._sensor_combo.setCurrentIndex(idx)

    def set_current_sensor_value(self, value_c: float | None) -> None:
        """Update the live sensor reading marker on the graph and readout label."""
        self._current_sensor_value = value_c
        self._update_sensor_marker()
        self._update_sensor_readout()

    def _update_sensor_readout(self) -> None:
        """Update the inline sensor value + computed output label."""
        if self._current_sensor_value is None or self._curve is None:
            self._sensor_value_label.setText("")
            return
        output = self._curve.interpolate(self._current_sensor_value)
        self._sensor_value_label.setText(
            f"{self._current_sensor_value:.1f}\u00b0C \u2192 {output:.0f}%"
        )

    def _on_sensor_selected(self, index: int) -> None:
        """Persist sensor selection to the curve model immediately."""
        if self._curve is None:
            return
        sensor_id = self._sensor_combo.currentData() or ""
        if sensor_id != self._curve.sensor_id:
            self._curve.sensor_id = sensor_id
            self.curve_changed.emit()

    # ─── Undo/Redo ────────────────────────────────────────────────────

    def _push_undo(self) -> None:
        if not self._curve:
            return
        snapshot = [CurvePoint(p.temp_c, p.output_pct) for p in self._curve.points]
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> None:
        if not self._undo_stack or not self._curve:
            return
        # Save current state to redo
        current = [CurvePoint(p.temp_c, p.output_pct) for p in self._curve.points]
        self._redo_stack.append(current)
        # Restore
        self._curve.points = self._undo_stack.pop()
        self._selected_idx = None
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()

    def redo(self) -> None:
        if not self._redo_stack or not self._curve:
            return
        current = [CurvePoint(p.temp_c, p.output_pct) for p in self._curve.points]
        self._undo_stack.append(current)
        self._curve.points = self._redo_stack.pop()
        self._selected_idx = None
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()

    # ─── Refresh ──────────────────────────────────────────────────────

    def _refresh_plot(self) -> None:
        if not self._curve or not self._curve.points:
            return

        x = np.array([p.temp_c for p in self._curve.points])
        y = np.array([p.output_pct for p in self._curve.points])

        t = self._theme
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        # Auto-scale X axis to fit points with 5°C padding
        x_min = max(0, float(x.min()) - 5)
        x_max = min(MAX_TEMP, float(x.max()) + 5)
        if x_max - x_min < 10:
            x_min = max(0, x_min - 5)
            x_max = min(MAX_TEMP, x_max + 5)
        plot.setXRange(x_min, x_max, padding=0.02)

        # Line
        if self._line_plot is None:
            self._line_plot = plot.plot(x, y, pen=pg.mkPen(t.accent_primary, width=2))
        else:
            self._line_plot.setData(x, y)

        # Scatter (all points)
        if self._scatter is not None:
            plot.removeItem(self._scatter)
        self._scatter = pg.ScatterPlotItem(
            x=x,
            y=y,
            size=14,
            pen=pg.mkPen(t.accent_secondary, width=2),
            brush=pg.mkBrush(t.accent_primary),
            hoverable=True,
            hoverSize=18,
            hoverPen=pg.mkPen(t.chart_point_hover, width=3),
        )
        plot.addItem(self._scatter)

        # Highlight selected point
        self._update_selection_highlight()

        # Sensor marker
        self._update_sensor_marker()

    def _update_selection_highlight(self) -> None:
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        if self._highlight_scatter is not None:
            plot.removeItem(self._highlight_scatter)
            self._highlight_scatter = None

        if (
            self._selected_idx is not None
            and self._curve
            and 0 <= self._selected_idx < len(self._curve.points)
        ):
            p = self._curve.points[self._selected_idx]
            self._highlight_scatter = pg.ScatterPlotItem(
                x=[p.temp_c],
                y=[p.output_pct],
                size=20,
                pen=pg.mkPen(self._theme.chart_point_selected, width=3),
                brush=pg.mkBrush(self._theme.accent_secondary),
                symbol="o",
            )
            plot.addItem(self._highlight_scatter)
            self._remove_btn.setEnabled(len(self._curve.points) > MIN_POINTS)
            self._coord_label.setText(f"Point: {p.temp_c:.1f}°C → {p.output_pct:.1f}%")
        else:
            self._remove_btn.setEnabled(False)
            self._coord_label.setText("")

    def _update_sensor_marker(self) -> None:
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        # Remove old markers
        if self._sensor_vline is not None:
            plot.removeItem(self._sensor_vline)
            self._sensor_vline = None
        if self._sensor_marker is not None:
            plot.removeItem(self._sensor_marker)
            self._sensor_marker = None

        if self._current_sensor_value is None or not self._curve:
            return

        temp = self._current_sensor_value
        output = self._curve.interpolate(temp)

        # Vertical line at current temperature
        self._sensor_vline = pg.InfiniteLine(
            pos=temp,
            angle=90,
            pen=pg.mkPen(self._theme.status_warn, width=1, style=Qt.PenStyle.DashLine),
        )
        plot.addItem(self._sensor_vline)

        # Dot at intersection
        self._sensor_marker = pg.ScatterPlotItem(
            x=[temp],
            y=[output],
            size=10,
            pen=pg.mkPen(self._theme.status_warn, width=2),
            brush=pg.mkBrush(self._theme.status_warn),
            symbol="d",
        )
        plot.addItem(self._sensor_marker)

    def _refresh_table(self) -> None:
        if not self._curve:
            return
        with block_signals(self._table):
            self._table.setRowCount(len(self._curve.points))
            for i, p in enumerate(self._curve.points):
                self._table.setItem(i, 0, QTableWidgetItem(f"{p.temp_c:.1f}"))
                self._table.setItem(i, 1, QTableWidgetItem(f"{p.output_pct:.1f}"))
        # Restore selection
        if self._selected_idx is not None and self._selected_idx < self._table.rowCount():
            self._table.selectRow(self._selected_idx)

    # ─── Mouse interaction ────────────────────────────────────────────

    def _on_mouse_clicked(self, event) -> None:
        if not self._curve:
            return

        pos = event.scenePos()
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        vb = plot.vb
        mouse_point = vb.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()

        # Double-click: add point on the curve
        if event.double():
            self._add_point_at(mx)
            return

        # Single click: select point (drag handled by eventFilter on press)
        idx = self._find_nearest_point(mx, my)
        if idx is not None:
            self._selected_idx = idx
            self._update_selection_highlight()
            self._refresh_table()
        else:
            self._selected_idx = None
            self._update_selection_highlight()
            self._coord_label.setText("")

    def _on_mouse_moved(self, pos) -> None:
        # Passive hover: show interpolated value at cursor
        if not self._drag_active and self._curve and self._curve.type == CurveType.GRAPH:
            plot = self._plot_widget.getPlotItem()
            if plot:
                vb = plot.vb
                mouse_point = vb.mapSceneToView(pos)
                temp = mouse_point.x()
                if MIN_TEMP <= temp <= MAX_TEMP:
                    output = self._curve.interpolate(temp)
                    self._coord_label.setText(f"Hover: {temp:.1f}\u00b0C \u2192 {output:.1f}%")

        if not self._drag_active or self._dragging_idx is None or not self._curve:
            return

        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        vb = plot.vb
        mouse_point = vb.mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()

        idx = self._dragging_idx
        points = self._curve.points

        # Clamp x between neighbours
        x_min = points[idx - 1].temp_c + MIN_TEMP_SPACING if idx > 0 else MIN_TEMP
        x_max = points[idx + 1].temp_c - MIN_TEMP_SPACING if idx < len(points) - 1 else MAX_TEMP
        new_temp = round(max(x_min, min(x_max, mx)), 1)
        new_output = round(max(MIN_OUTPUT, min(MAX_OUTPUT, my)), 1)

        points[idx].temp_c = new_temp
        points[idx].output_pct = new_output

        self._coord_label.setText(f"Dragging: {new_temp:.1f}°C → {new_output:.1f}%")
        self._refresh_plot()
        self._refresh_table()

    def _find_nearest_point(self, mx: float, my: float) -> int | None:
        if not self._curve or not self._curve.points:
            return None

        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return None

        # Get view range for normalization
        vb = plot.vb
        view_range = vb.viewRange()
        x_range = view_range[0][1] - view_range[0][0]
        y_range = view_range[1][1] - view_range[1][0]

        if x_range == 0 or y_range == 0:
            return None

        best_idx = None
        best_dist = float("inf")
        threshold = 0.05  # 5% of view range

        for i, p in enumerate(self._curve.points):
            dx = (p.temp_c - mx) / x_range
            dy = (p.output_pct - my) / y_range
            dist = (dx**2 + dy**2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_dist < threshold:
            return best_idx
        return None

    # ─── Keyboard nudge ──────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        if self._selected_idx is None or not self._curve:
            super().keyPressEvent(event)
            return

        idx = self._selected_idx
        points = self._curve.points

        if idx < 0 or idx >= len(points):
            super().keyPressEvent(event)
            return

        key = event.key()
        handled = False

        if key == Qt.Key.Key_Up:
            self._push_undo()
            points[idx].output_pct = round(min(MAX_OUTPUT, points[idx].output_pct + 1), 1)
            handled = True
        elif key == Qt.Key.Key_Down:
            self._push_undo()
            points[idx].output_pct = round(max(MIN_OUTPUT, points[idx].output_pct - 1), 1)
            handled = True
        elif key == Qt.Key.Key_Right:
            x_max = points[idx + 1].temp_c - MIN_TEMP_SPACING if idx < len(points) - 1 else MAX_TEMP
            self._push_undo()
            points[idx].temp_c = round(min(x_max, points[idx].temp_c + 1), 1)
            handled = True
        elif key == Qt.Key.Key_Left:
            x_min = points[idx - 1].temp_c + MIN_TEMP_SPACING if idx > 0 else MIN_TEMP
            self._push_undo()
            points[idx].temp_c = round(max(x_min, points[idx].temp_c - 1), 1)
            handled = True

        if handled:
            self._refresh_plot()
            self._refresh_table()
            self.curve_changed.emit()
        else:
            super().keyPressEvent(event)

    # ─── Add/Remove ──────────────────────────────────────────────────

    def _on_add_point(self) -> None:
        if not self._curve:
            return
        # Add at midpoint of the curve
        points = self._curve.points
        if not points:
            self._add_point_at(50.0)
            return
        mid_temp = (points[0].temp_c + points[-1].temp_c) / 2
        self._add_point_at(mid_temp)

    def _add_point_at(self, temp_c: float) -> None:
        if not self._curve:
            return

        self._push_undo()
        temp_c = round(max(MIN_TEMP, min(MAX_TEMP, temp_c)), 1)
        output = round(self._curve.interpolate(temp_c), 1)

        # Ensure minimum spacing from existing points
        for p in self._curve.points:
            if abs(p.temp_c - temp_c) < MIN_TEMP_SPACING:
                return  # too close to existing point

        new_point = CurvePoint(temp_c, output)
        self._curve.points.append(new_point)
        self._curve.points.sort(key=lambda pt: pt.temp_c)

        # Select the new point
        self._selected_idx = next(i for i, p in enumerate(self._curve.points) if p is new_point)
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()

    def _on_remove_point(self) -> None:
        if not self._curve or self._selected_idx is None:
            return
        if len(self._curve.points) <= MIN_POINTS:
            return

        self._push_undo()
        del self._curve.points[self._selected_idx]
        self._selected_idx = min(self._selected_idx, len(self._curve.points) - 1)
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()

    # ─── Presets ─────────────────────────────────────────────────────

    def _on_preset_selected(self, index: int) -> None:
        if index <= 0 or not self._curve:
            return
        preset_name = self._preset_combo.currentText()
        if preset_name not in PRESETS:
            return

        self._push_undo()
        self._curve.points = [CurvePoint(p.temp_c, p.output_pct) for p in PRESETS[preset_name]]
        self._selected_idx = None
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()
        # Reset combo to placeholder
        with block_signals(self._preset_combo):
            self._preset_combo.setCurrentIndex(0)

    # ─── Table editing ───────────────────────────────────────────────

    def _on_table_edited(self, row: int, col: int) -> None:
        if not self._curve or row >= len(self._curve.points):
            return
        item = self._table.item(row, col)
        if item is None:
            return
        try:
            val = float(item.text())
        except ValueError:
            self._refresh_table()
            return

        self._push_undo()
        p = self._curve.points[row]
        if col == 0:
            p.temp_c = round(max(MIN_TEMP, min(MAX_TEMP, val)), 1)
        else:
            p.output_pct = round(max(MIN_OUTPUT, min(MAX_OUTPUT, val)), 1)

        # Enforce X ordering
        self._curve.points.sort(key=lambda pt: pt.temp_c)
        self._refresh_plot()
        self._refresh_table()
        self.curve_changed.emit()

    def _on_table_selection_changed(
        self, row: int, _col: int, _prev_row: int, _prev_col: int
    ) -> None:
        if row >= 0:
            self._selected_idx = row
            self._update_selection_highlight()

    # ─── Linear/Flat parameter editors ───────────────────────────────

    def _load_linear_params(self, curve: CurveConfig) -> None:
        for spin, attr in [
            (self._lin_start_temp, "start_temp_c"),
            (self._lin_start_output, "start_output_pct"),
            (self._lin_end_temp, "end_temp_c"),
            (self._lin_end_output, "end_output_pct"),
        ]:
            with block_signals(spin):
                spin.setValue(getattr(curve, attr))

    def _load_flat_params(self, curve: CurveConfig) -> None:
        with block_signals(self._flat_output_spin):
            self._flat_output_spin.setValue(curve.flat_output_pct)

    def _on_linear_param_changed(self) -> None:
        if not self._curve or self._curve.type != CurveType.LINEAR:
            return
        self._curve.start_temp_c = self._lin_start_temp.value()
        self._curve.start_output_pct = self._lin_start_output.value()
        self._curve.end_temp_c = self._lin_end_temp.value()
        self._curve.end_output_pct = self._lin_end_output.value()
        self.curve_changed.emit()

    def _on_flat_param_changed(self) -> None:
        if not self._curve or self._curve.type != CurveType.FLAT:
            return
        self._curve.flat_output_pct = self._flat_output_spin.value()
        self.curve_changed.emit()
