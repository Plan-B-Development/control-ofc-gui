"""Dual-axis sensor chart — temps (C, left) and fan RPM (right).

Uses pyqtgraph with a secondary ViewBox for the right axis. Series
visibility is driven by a SeriesSelectionModel. Crosshair hover shows
values for all visible series at the cursor position.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from control_ofc.services.history_store import HistoryStore
from control_ofc.ui.theme import default_dark_theme

if TYPE_CHECKING:
    from control_ofc.services.series_selection import SeriesSelectionModel

# Time range options: label -> seconds
TIME_RANGES = [
    ("30s", 30),
    ("2m", 120),
    ("5m", 300),
    ("10m", 600),
    ("15m", 900),
    ("20m", 1200),
    ("30m", 1800),
    ("1h", 3600),
    ("2h", 7200),
]


class TimelineChart(QWidget):
    """Live-updating dual-axis chart with selection-model-driven visibility."""

    def __init__(
        self,
        history: HistoryStore,
        selection: SeriesSelectionModel | None = None,
        color_overrides: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history
        self._selection = selection
        self._theme = default_dark_theme()
        self._color_overrides: dict[str, str] = color_overrides or {}
        self._time_range_s = 300  # default 5 minutes
        self._temp_items: dict[str, pg.PlotDataItem] = {}
        self._rpm_items: dict[str, pg.PlotCurveItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._range_combo = QComboBox()
        self._range_combo.setObjectName("TimelineChart_Combo_range")
        for label, _ in TIME_RANGES:
            self._range_combo.addItem(label)
        self._range_combo.setCurrentIndex(2)  # 5m default
        self._range_combo.currentIndexChanged.connect(self._on_range_changed)
        controls.addWidget(self._range_combo)
        controls.addStretch()
        layout.addLayout(controls)

        # Plot widget — antialiasing disabled for real-time performance (R48)
        pg.setConfigOptions(antialias=False)
        self._plot_widget = pg.PlotWidget()
        self._rpm_vb: pg.ViewBox | None = None
        self._setup_plot()
        layout.addWidget(self._plot_widget, 1)

        # Hide hover when mouse leaves the plot or app deactivates
        self._plot_widget.installEventFilter(self)
        app = QApplication.instance()
        if app:
            app.applicationStateChanged.connect(self._on_app_state_changed)

        # Wire selection model
        if self._selection:
            self._selection.selection_changed.connect(self.update_chart)

    def _setup_plot(self) -> None:
        t = self._theme
        self._plot_widget.setBackground(t.chart_bg)
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return

        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setLabel("left", "Temperature (\u00b0C)")
        plot.setLabel("bottom", "Time (s ago)")

        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(t.chart_axis_text))
            axis.setTextPen(pg.mkPen(t.text_secondary))

        # Y-axis limits: temperatures and RPM can never be negative
        plot.getViewBox().setLimits(yMin=0)

        # Right axis for RPM via secondary ViewBox
        self._rpm_vb = pg.ViewBox()
        self._rpm_vb.setLimits(yMin=0)
        plot.scene().addItem(self._rpm_vb)
        plot.showAxis("right")
        plot.getAxis("right").linkToView(self._rpm_vb)
        plot.getAxis("right").setLabel("RPM")
        plot.getAxis("right").setPen(pg.mkPen(t.chart_axis_text))
        plot.getAxis("right").setTextPen(pg.mkPen(t.text_secondary))
        self._rpm_vb.setXLink(plot.vb)

        plot.vb.sigResized.connect(self._sync_rpm_viewbox)

        # Crosshair hover: vertical line + text label showing values
        crosshair_pen = pg.mkPen("#888888", width=1)
        self._crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=crosshair_pen)
        plot.addItem(self._crosshair_v, ignoreBounds=True)
        self._crosshair_v.hide()

        self._hover_label = pg.TextItem(anchor=(0, 0), color=t.text_primary)
        self._hover_label.setZValue(100)
        plot.addItem(self._hover_label, ignoreBounds=True)
        self._hover_label.hide()

        # Rate-limited mouse tracking
        self._proxy = pg.SignalProxy(
            plot.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse_moved
        )

    def _sync_rpm_viewbox(self) -> None:
        plot = self._plot_widget.getPlotItem()
        if plot and self._rpm_vb:
            self._rpm_vb.setGeometry(plot.vb.sceneBoundingRect())
            self._rpm_vb.linkedViewChanged(plot.vb, self._rpm_vb.XAxis)

    def _hide_hover(self) -> None:
        """Hide the crosshair and hover label."""
        self._crosshair_v.hide()
        self._hover_label.hide()

    def eventFilter(self, obj, event):
        """Hide hover when mouse leaves the plot widget."""
        if obj is self._plot_widget and event.type() == QEvent.Type.Leave:
            self._hide_hover()
        return super().eventFilter(obj, event)

    def _on_app_state_changed(self, state) -> None:
        """Hide hover when app loses focus (e.g. alt-tab)."""
        if state != Qt.ApplicationState.ApplicationActive:
            self._hide_hover()

    def color_for_key(self, key: str) -> str:
        """Return the colour for a series key: user override > hash default."""
        if key in self._color_overrides:
            return self._color_overrides[key]
        colors = self._theme.chart_series
        idx = hash(key) % len(colors)
        return colors[idx]

    def set_series_color(self, key: str, color: str) -> None:
        """Set a user colour override for a series and update the graph immediately."""
        self._color_overrides[key] = color
        pen = pg.mkPen(color, width=1)
        if key in self._temp_items:
            self._temp_items[key].setPen(pen)
        if key in self._rpm_items:
            self._rpm_items[key].setPen(pen)

    def _is_temp_key(self, key: str) -> bool:
        return key.startswith("sensor:")

    def _is_rpm_key(self, key: str) -> bool:
        return key.endswith(":rpm")

    def _chartable_keys(self) -> list[str]:
        """Return keys suitable for charting.

        Prefers the selection model (displayable entities only — filters iGPU,
        duplicate hwmon fans, etc.). Falls back to history keys when the
        selection model is empty (startup race before first sensor update).
        """
        if self._selection:
            keys = self._selection.known_keys()
            if keys:
                return sorted(keys)
        # Fallback: use raw history keys (startup or no selection model)
        return [k for k in self._history.series_keys() if not k.endswith(":pwm")]

    def update_chart(self) -> None:
        """Refresh the chart from history data."""
        now = time.monotonic()
        plot = self._plot_widget.getPlotItem()
        if not plot:
            return

        all_keys = self._chartable_keys()
        visible = self._selection.visible_keys() if self._selection else set(all_keys)

        active_temp_keys: set[str] = set()
        active_rpm_keys: set[str] = set()

        for key in all_keys:
            if key not in visible:
                if key in self._temp_items:
                    self._temp_items[key].setData([], [])
                if key in self._rpm_items:
                    self._rpm_items[key].setData([], [])
                continue

            series = self._history.get_series(key)
            if not series:
                continue

            x = np.array([r.timestamp - now for r in series])
            y = np.array([r.value for r in series])

            mask = x >= -self._time_range_s
            x = x[mask]
            y = y[mask]

            if len(x) == 0:
                continue

            color = self.color_for_key(key)
            pen = pg.mkPen(color, width=1)

            if self._is_temp_key(key):
                active_temp_keys.add(key)
                if key not in self._temp_items:
                    item = pg.PlotDataItem(
                        x,
                        y,
                        pen=pen,
                        skipFiniteCheck=True,
                        autoDownsample=True,
                        downsampleMethod="peak",
                    )
                    plot.addItem(item)
                    item.setClipToView(True)
                    self._temp_items[key] = item
                else:
                    self._temp_items[key].setData(x, y)
            elif self._is_rpm_key(key) and self._rpm_vb:
                active_rpm_keys.add(key)
                if key not in self._rpm_items:
                    # PlotCurveItem for secondary ViewBox (PlotDataItem does
                    # not render correctly on a bare ViewBox)
                    item = pg.PlotCurveItem(x, y, pen=pen)
                    self._rpm_vb.addItem(item)
                    self._rpm_items[key] = item
                else:
                    self._rpm_items[key].setData(x, y)

        # Remove stale items
        for key in list(self._temp_items):
            if key not in all_keys:
                plot.removeItem(self._temp_items.pop(key))
        for key in list(self._rpm_items):
            if key not in all_keys and self._rpm_vb:
                self._rpm_vb.removeItem(self._rpm_items.pop(key))

        plot.setXRange(-self._time_range_s, 0, padding=0.02)

        if self._rpm_vb:
            self._rpm_vb.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _on_mouse_moved(self, event: tuple) -> None:
        """Update crosshair and hover label when mouse moves over the chart."""
        plot = self._plot_widget.getPlotItem()
        if not plot:
            return

        pos = event[0]
        if not plot.sceneBoundingRect().contains(pos):
            self._crosshair_v.hide()
            self._hover_label.hide()
            return

        mouse_point = plot.vb.mapSceneToView(pos)
        x_val = mouse_point.x()

        self._crosshair_v.setPos(x_val)
        self._crosshair_v.show()

        # Build hover text from SELECTED series only at this x position.
        # Only iterate series the user has chosen to display.
        visible = self._selection.visible_keys() if self._selection else set()
        lines = []
        for key in visible:
            if key in self._temp_items:
                ds = self._temp_items[key].getData()
                if ds is None or ds[0] is None or len(ds[0]) == 0:
                    continue
                xd, yd = ds
                idx = int(np.clip(np.searchsorted(xd, x_val), 0, len(yd) - 1))
                label = key.removeprefix("sensor:").split(":")[-1]
                lines.append(f"{label}: {yd[idx]:.1f}\u00b0C")
            elif key in self._rpm_items:
                xd, yd = self._rpm_items[key].getData()
                if xd is None or len(xd) == 0:
                    continue
                idx = int(np.clip(np.searchsorted(xd, x_val), 0, len(yd) - 1))
                val = int(yd[idx])
                # Suppress 0 RPM from hover — zero-RPM idle is noise, not signal
                if val == 0:
                    continue
                label = key.removeprefix("fan:").split(":")[0]
                if ":" in key:
                    label = key.split(":")[-2] if len(key.split(":")) > 2 else label
                lines.append(f"{label}: {val} RPM")

        if lines:
            secs_ago = abs(x_val)
            header = f"T\u2212{secs_ago:.0f}s"
            self._hover_label.setText("\n".join([header, *lines]))
            self._hover_label.setPos(mouse_point)
            self._hover_label.show()
        else:
            self._hover_label.hide()

    def _on_range_changed(self, index: int) -> None:
        if 0 <= index < len(TIME_RANGES):
            self._time_range_s = TIME_RANGES[index][1]
            self.update_chart()
