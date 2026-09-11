"""PWM-vs-RPM response chart (AIO Phase 8 Batch 2 §8.3, DEC-334).

A thin renderer over :class:`~control_ofc.services.characterization_view.ResponseCurve`
— the project's view-model + renderer standard. Every decision about which
reading belongs to which leg is made in the Qt-free view model and tested there;
this widget only draws what it is handed.

Three things it deliberately does **not** do:

* **It plots no learned band.** §8.3 offers one as optional, but the daemon
  publishes whether a reading fell *outside* the learned range, not the range
  itself — so a band drawn here could only be derived from this run's own points,
  which is a fabricated reference dressed as a measurement. The verdict is text.
* **It draws nothing when there is nothing to draw.** Empty axes read as "we
  measured and found zero".
* **It marks plateaus but never labels them a fault.** §3 forbids reinterpreting
  a plateau as pump failure, so the shading is neutral-toned and captioned as an
  observation.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from control_ofc.services.characterization_view import ResponseCurve
from control_ofc.ui.theme import ThemeTokens, active_theme

#: Series roles, in the order they are assigned theme colours. Falling first so
#: the two legs are visually distinct even on a palette whose first two entries
#: are close.
_RISING = 0
_FALLING = 1


class PwmResponseChart(QWidget):
    """Rising and falling PWM→RPM series, with plateau and saturation markers."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        object_name: str = "PwmResponseChart",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        # `P8-af`: the LIVE theme, not default-dark. Pinned, this chart sat on
        # the default palette inside a correctly themed dialog. Unlike its
        # sibling `SessionTimelineChart` this one builds no ViewBox, so
        # `_setup_plot` is idempotent and `set_theme` was already safe to call —
        # there was simply never a caller. `P8-bx` added one (the hosting dialog
        # forwards), though it is latent while that dialog is `exec()`-modal.
        self._theme: ThemeTokens = active_theme()
        self._curve = ResponseCurve()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setObjectName(f"{object_name}_Plot")
        # Stretch argument matches `curve_editor`/`timeline_chart`: PySide6 will
        # not resolve pyqtgraph's PlotWidget through the single-argument
        # overload, and the plot should take the surplus anyway.
        layout.addWidget(self._plot_widget, 1)
        self._setup_plot()

    # ─── Theme ────────────────────────────────────────────────────────

    def set_theme(self, tokens: ThemeTokens) -> None:
        self._theme = tokens
        self._setup_plot()
        self.set_curve(self._curve)

    def _setup_plot(self) -> None:
        t = self._theme
        self._plot_widget.setBackground(t.chart_bg)
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return
        # Static: the sweep publishes points as it goes, and the widget redraws
        # on new data. No QTimer, matching the gauge/histogram policy.
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setLabel("left", "Reported RPM")
        plot.setLabel("bottom", "PWM duty (%)")
        plot.setXRange(0, 105, padding=0)
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(t.chart_axis_text))
            axis.setTextPen(pg.mkPen(t.text_secondary))

    # ─── Public API ───────────────────────────────────────────────────

    @property
    def has_data(self) -> bool:
        """Whether the last :meth:`set_curve` had anything to plot."""
        return self._curve.has_data

    def set_curve(self, curve: ResponseCurve) -> None:
        """Render one run's response. Safe to call repeatedly while it sweeps."""
        self._curve = curve
        plot = self._plot_widget.getPlotItem()
        if plot is None:
            return
        plot.clear()
        if not curve.has_data:
            return

        t = self._theme
        palette = t.chart_series or [t.accent_primary]

        def colour(index: int) -> str:
            return palette[index % len(palette)]

        # Plateaus first, so the series draw over them.
        for from_pct, to_pct in curve.plateaus:
            region = pg.LinearRegionItem(
                values=(from_pct, to_pct),
                movable=False,
                brush=pg.mkBrush(t.chart_grid),
                pen=pg.mkPen(None),
            )
            region.setZValue(-10)
            plot.addItem(region)

        if curve.saturation_from_pct is not None:
            plot.addItem(
                pg.InfiniteLine(
                    pos=curve.saturation_from_pct,
                    angle=90,
                    pen=pg.mkPen(t.chart_axis_text, style=Qt.PenStyle.DashLine),
                )
            )

        for series, index, name in (
            (curve.falling, _FALLING, "Falling"),
            (curve.rising, _RISING, "Rising"),
        ):
            if not series:
                continue
            plot.plot(
                [p.duty_pct for p in series],
                [p.rpm for p in series],
                pen=pg.mkPen(colour(index), width=2),
                symbol="o",
                symbolSize=6,
                symbolBrush=colour(index),
                symbolPen=pg.mkPen(None),
                name=name,
            )
