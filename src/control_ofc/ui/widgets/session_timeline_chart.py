"""A recorded session's timeline (AIO Phase 8 Batch 3a §9.1/§9.2, DEC-335).

A thin renderer over :class:`~control_ofc.services.thermal_view.SessionTrace` —
the project's view-model + renderer standard. Every decision about which reading
becomes a point is made in the Qt-free view model and tested there; this widget
only draws what it is handed.

# Why this is not ``TimelineChart``

``TimelineChart`` is coupled to the live ``AppState``/``HistoryStore`` pair and
keyed on a monotonic clock, so it cannot render a session's sample array at all —
that coupling is exactly why Phase 6 shipped no session chart and recorded the
gap as deferred work. This takes a plain dataclass instead, on the
``PwmResponseChart`` pattern: no timer, no store, no live state.

# Why two plots and not one

The trace carries three incompatible units — °C, RPM and watts. Two axes cannot
hold three units without either a hidden scale factor or an axis label that is a
lie, and this project's standing rule is that a chart must not imply a
measurement it did not make. So temperature and RPM share the main plot on their
own axes (the dual-axis idiom ``timeline_chart`` already uses), and power gets a
short plot beneath with its X range linked. Both scroll together and every axis
says exactly what is on it.

# What it will not do

* **It draws nothing when there is nothing to draw.** Empty axes read as "we
  measured and found zero".
* **It never interpolates across a gap in a way that invents a reading.** A
  ``None`` sample produced no point in the view model; the line simply spans it,
  and the summary block beside the chart is where "not known" is stated in words.
* **The steady-state band is neutral-toned and is not a verdict about the
  hardware.** §3 forbids reading "did not settle" as a fault, and shading that
  looked like a pass/fail region would undo that in a colour.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from control_ofc.services.thermal_view import SessionTrace
from control_ofc.ui.theme import ThemeTokens, active_theme

#: Series order, so colours stay stable between redraws.
_TEMPERATURE = 0
_COOLANT = 1
_PUMP = 2
_RADIATOR = 3
_PACKAGE_POWER = 4
_GPU_POWER = 5

#: How tall the power strip is relative to the main plot.
_POWER_STRETCH = 1
_MAIN_STRETCH = 3

#: Markers are bounded for the same reason `TimelineChart` bounds its
#: annotations: a two-hour session can carry thousands of events, and drawing one
#: line per event turns the plot into a solid block.
_MAX_MARKERS = 40


class SessionTimelineChart(QWidget):
    """Temperature, RPM and power over one recorded session."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        object_name: str = "SessionTimelineChart",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        # `P8-af`: read the LIVE active theme rather than pinning to default-dark
        # at construction (the DEC-109 rule `timeline_chart` already follows).
        # Pinned, this chart sat on the default palette inside a correctly themed
        # dialog for its whole life, because nothing called `set_theme` on it.
        # `P8-bx` then wired the hosting dialog, so a live switch reaches here
        # too — but the seed still has to be right on its own: a dialog opened
        # and never re-themed never receives one.
        self._theme: ThemeTokens = active_theme()
        self._trace = SessionTrace()
        self._rpm_vb: pg.ViewBox | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._main = pg.PlotWidget()
        self._main.setObjectName(f"{object_name}_Main")
        # Explicit stretch: PySide6 will not resolve pyqtgraph's PlotWidget
        # through the single-argument overload (the note `pwm_response_chart`
        # and `curve_editor` both carry).
        layout.addWidget(self._main, _MAIN_STRETCH)

        self._power = pg.PlotWidget()
        self._power.setObjectName(f"{object_name}_Power")
        layout.addWidget(self._power, _POWER_STRETCH)

        self._setup_plots()

    # ─── Theme ────────────────────────────────────────────────────────

    def set_theme(self, tokens: ThemeTokens) -> None:
        self._theme = tokens
        self._setup_plots()
        self.set_trace(self._trace)

    def _setup_plots(self) -> None:
        t = self._theme
        for widget in (self._main, self._power):
            widget.setBackground(t.chart_bg)
            plot = widget.getPlotItem()
            if plot is None:
                continue
            # Static, like every other session-scoped chart here: the data is a
            # finished recording, so there is nothing to pan toward.
            plot.setMouseEnabled(x=False, y=False)
            plot.setMenuEnabled(False)
            plot.showGrid(x=True, y=True, alpha=0.15)
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(t.chart_axis_text))
                axis.setTextPen(pg.mkPen(t.text_secondary))

        main = self._main.getPlotItem()
        if main is not None:
            main.setLabel("left", "Temperature (°C)")
            # The main plot's own X labels are redundant with the power strip
            # directly beneath it, and hiding them is what makes the two read as
            # one chart rather than two.
            main.getAxis("bottom").setStyle(showValues=False)
            # `P8-af`: create/add/link the RPM ViewBox exactly ONCE. This block
            # used to run on every `_setup_plots`, and `set_theme` calls that —
            # so a theme switch orphaned the previous ViewBox in the scene with
            # its RPM curves never cleared, and connected `sigResized` a second
            # time. That is why seeding from `active_theme()` alone would not
            # have been a safe repair: it makes `set_theme` reachable.
            if self._rpm_vb is None:
                self._rpm_vb = pg.ViewBox()
                self._rpm_vb.setLimits(yMin=0)
                main.scene().addItem(self._rpm_vb)
                main.showAxis("right")
                main.getAxis("right").linkToView(self._rpm_vb)
                main.getAxis("right").setLabel("RPM")
                self._rpm_vb.setXLink(main.vb)
                main.vb.sigResized.connect(self._sync_rpm_viewbox)
            # Restyling the axis IS per-theme and stays here.
            right = main.getAxis("right")
            right.setPen(pg.mkPen(t.chart_axis_text))
            right.setTextPen(pg.mkPen(t.text_secondary))

        power = self._power.getPlotItem()
        if power is not None:
            power.setLabel("left", "Power (W)")
            power.setLabel("bottom", "Elapsed (s)")
            if main is not None:
                # The whole point of the second plot: one shared time base.
                power.setXLink(main)

    def _sync_rpm_viewbox(self) -> None:
        """Keep the secondary ViewBox glued to the main plot's geometry."""
        main = self._main.getPlotItem()
        if main is None or self._rpm_vb is None:
            return
        self._rpm_vb.setGeometry(main.vb.sceneBoundingRect())
        self._rpm_vb.linkedViewChanged(main.vb, self._rpm_vb.XAxis)

    # ─── Public API ───────────────────────────────────────────────────

    @property
    def has_data(self) -> bool:
        """Whether the last :meth:`set_trace` had anything to plot."""
        return self._trace.has_data

    def set_trace(self, trace: SessionTrace) -> None:
        """Render one session. Safe to call repeatedly while it records."""
        self._trace = trace
        main = self._main.getPlotItem()
        power = self._power.getPlotItem()
        if main is None or power is None:
            return
        main.clear()
        power.clear()
        if self._rpm_vb is not None:
            self._rpm_vb.clear()
        # The power strip is hidden rather than left empty when the host exposes
        # no power source — which is the COMMON case, not the exotic one
        # (measured: `k10temp` publishes no power attribute at all). An empty
        # axis labelled "Power (W)" would read as a machine drawing none.
        self._power.setVisible(trace.has_power)
        if not trace.has_data:
            return

        t = self._theme
        palette = t.chart_series or [t.accent_primary]

        def colour(index: int) -> str:
            return palette[index % len(palette)]

        # The steady band first, so every series draws over it.
        if trace.steady_from_s is not None and trace.steady_to_s is not None:
            region = pg.LinearRegionItem(
                values=(trace.steady_from_s, trace.steady_to_s),
                movable=False,
                brush=pg.mkBrush(t.chart_grid),
                pen=pg.mkPen(None),
            )
            region.setZValue(-10)
            main.addItem(region)

        for series, index, name in (
            (trace.temperature, _TEMPERATURE, "Temperature"),
            (trace.coolant, _COOLANT, "Coolant"),
        ):
            if not series:
                continue
            main.plot(
                [p.at_s for p in series],
                [p.value for p in series],
                pen=pg.mkPen(colour(index), width=2),
                name=name,
            )

        if self._rpm_vb is not None:
            for series, index in ((trace.pump_rpm, _PUMP), (trace.radiator_rpm, _RADIATOR)):
                if not series:
                    continue
                curve = pg.PlotCurveItem(
                    [p.at_s for p in series],
                    [p.value for p in series],
                    pen=pg.mkPen(colour(index), width=1),
                )
                self._rpm_vb.addItem(curve)
            self._sync_rpm_viewbox()

        for series, index, name in (
            (trace.package_power, _PACKAGE_POWER, "CPU package"),
            (trace.gpu_power, _GPU_POWER, "GPU"),
        ):
            if not series:
                continue
            power.plot(
                [p.at_s for p in series],
                [p.value for p in series],
                pen=pg.mkPen(colour(index), width=1),
                name=name,
            )

        for marker in trace.markers[:_MAX_MARKERS]:
            main.addItem(
                pg.InfiniteLine(
                    pos=marker.at_s,
                    angle=90,
                    pen=pg.mkPen(t.chart_axis_text, style=Qt.PenStyle.DotLine),
                )
            )
