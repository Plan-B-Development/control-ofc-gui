"""Inline SVG charts for the PWM Test Report's HTML export (DEC-404 Stage 5).

Qt-free and dependency-free: every chart is a string of SVG drawn to scale,
embedded in a self-contained HTML file that loads nothing and runs no script.

**No colour lives here.** Every mark carries a CSS class (``series-rpm``,
``shade-thermal``, …) and the page's stylesheet gives the class its colour from
the theme tokens, once for dark and once for light (``prefers-color-scheme``).
That keeps this module inside the project's token rule and lets one SVG follow
the reader's colour scheme.

**Every dynamic string is escaped** (DEC-106): a title or a series name can
carry a daemon label or a user alias, and SVG inline in HTML is markup.

Two rules about what a line may claim (S5-3):

* **Gaps stay gaps.** ``None`` means "not reported" and breaks the line; it is
  never bridged or interpolated.
* **Decimation is stated.** Above :data:`DECIMATE_ABOVE` points a series is
  reduced to the minimum and maximum inside each pixel column, which keeps
  every spike and dip visible; the chart says it did so.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from control_ofc.services.characterization_view import ResponseCurve

#: S5-3: a series longer than this is min/max-decimated per pixel column.
DECIMATE_ABOVE = 2000

WIDTH = 720
HEIGHT = 280
_LEFT = 60
_RIGHT = 60
_TOP = 30
_BOTTOM = 44
PLOT_W = WIDTH - _LEFT - _RIGHT
PLOT_H = HEIGHT - _TOP - _BOTTOM


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _num(value: float) -> str:
    """A coordinate, rounded so the file stays small and deterministic."""
    out = f"{value:.1f}"
    return "0" if out == "-0.0" else out.removesuffix(".0")


def nice_ceiling(value: float) -> float:
    """The smallest 1/2/5 x 10^k at or above *value* (at least 1)."""
    if not math.isfinite(value) or value <= 1:
        return 1.0
    exp = math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        candidate = step * 10**exp
        if candidate >= value:
            return float(candidate)
    return float(10 ** (exp + 1))


def _ticks(top: float, count: int = 5) -> list[float]:
    return [top * i / count for i in range(count + 1)]


def _tick_label(value: float) -> str:
    return f"{value:g}" if value < 10000 else f"{value / 1000:g}k"


def split_runs(xs: Sequence[float], ys: Sequence[float | None]) -> list[list[tuple[float, float]]]:
    """Contiguous runs of reported values. A ``None`` ends a run — a gap."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for x, y in zip(xs, ys, strict=False):
        # Anything but a finite number is "not reported" — a gap. A file from
        # elsewhere can hold any JSON value here.
        if not isinstance(y, (int, float)) or isinstance(y, bool) or not math.isfinite(y):
            if current:
                runs.append(current)
                current = []
            continue
        current.append((float(x), float(y)))
    if current:
        runs.append(current)
    return runs


def decimate_minmax(
    run: Sequence[tuple[float, float]], x_min: float, x_max: float, columns: int
) -> list[tuple[float, float]]:
    """Keep the first, minimum, maximum and last point of each pixel column, in
    time order. Every extreme survives, so a spike or a stall cannot vanish."""
    if not run or columns <= 0:
        return list(run)
    span = (x_max - x_min) or 1.0
    buckets: dict[int, list[tuple[float, float]]] = {}
    order: list[int] = []
    for point in run:
        col = min(columns - 1, max(0, int((point[0] - x_min) / span * columns)))
        if col not in buckets:
            buckets[col] = []
            order.append(col)
        buckets[col].append(point)
    out: list[tuple[float, float]] = []
    for col in order:
        pts = buckets[col]
        keep = {0, len(pts) - 1}
        keep.add(min(range(len(pts)), key=lambda i: pts[i][1]))
        keep.add(max(range(len(pts)), key=lambda i: pts[i][1]))
        out.extend(pts[i] for i in sorted(keep))
    return out


def merge_spans(spans: Sequence[tuple[int, int]], gap: float) -> list[tuple[int, int]]:
    """Join spans less than *gap* apart — one pixel column at the chart's scale —
    so a chart never draws more shaded rectangles than it has columns."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start - out[-1][1] < gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


@dataclass(frozen=True)
class TimeSeries:
    """One line on a time chart. ``axis`` is ``left`` or ``right``."""

    name: str
    values: Sequence[float | None]
    css_class: str
    axis: str = "left"


@dataclass
class _Frame:
    parts: list[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        self.parts.append(text)


def _open(frame: _Frame, title: str, desc: str) -> None:
    frame.add(
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" '
        f'role="img" aria-label="{_esc(title)}" xmlns="http://www.w3.org/2000/svg">'
    )
    frame.add(f"<title>{_esc(title)}</title>")
    if desc:
        frame.add(f"<desc>{_esc(desc)}</desc>")
    frame.add(f'<rect class="plot-bg" x="{_LEFT}" y="{_TOP}" width="{PLOT_W}" height="{PLOT_H}"/>')


def _y_axis(frame: _Frame, top: float, label: str, *, right: bool) -> None:
    x_axis = _LEFT + PLOT_W if right else _LEFT
    anchor = "start" if right else "end"
    dx = 6 if right else -6
    for value in _ticks(top):
        y = _TOP + PLOT_H - value / top * PLOT_H
        if not right:
            frame.add(
                f'<line class="grid" x1="{_LEFT}" y1="{_num(y)}" '
                f'x2="{_LEFT + PLOT_W}" y2="{_num(y)}"/>'
            )
        frame.add(
            f'<text class="axis" x="{_num(x_axis + dx)}" y="{_num(y + 4)}" '
            f'text-anchor="{anchor}">{_esc(_tick_label(value))}</text>'
        )
    lx = WIDTH - 14 if right else 14
    ly = _TOP + PLOT_H / 2
    frame.add(
        f'<text class="axis-label" x="{_num(lx)}" y="{_num(ly)}" text-anchor="middle" '
        f'transform="rotate(-90 {_num(lx)} {_num(ly)})">{_esc(label)}</text>'
    )


def _x_axis(frame: _Frame, x_max: float, label: str, ticks: Sequence[float]) -> None:
    for value in ticks:
        x = _LEFT + (value / x_max * PLOT_W if x_max else 0)
        frame.add(
            f'<text class="axis" x="{_num(x)}" y="{_TOP + PLOT_H + 16}" '
            f'text-anchor="middle">{_esc(_tick_label(value))}</text>'
        )
    frame.add(
        f'<text class="axis-label" x="{_LEFT + PLOT_W / 2:g}" y="{HEIGHT - 6}" '
        f'text-anchor="middle">{_esc(label)}</text>'
    )


def _legend(frame: _Frame, entries: Sequence[tuple[str, str]]) -> None:
    x = float(_LEFT)
    for name, css in entries:
        frame.add(f'<line class="{_esc(css)}" x1="{_num(x)}" y1="14" x2="{_num(x + 18)}" y2="14"/>')
        frame.add(f'<text class="legend" x="{_num(x + 24)}" y="18">{_esc(name)}</text>')
        x += 36 + 7 * len(name)


def response_chart(
    curve: ResponseCurve,
    *,
    title: str,
    desc: str = "",
    falling_name: str = "Falling (100 % → down)",
    rising_name: str = "Rising (back up)",
) -> str:
    """RPM against duty, both legs drawn separately, to scale (0-100 % duty).

    The falling and rising legs are never joined: a hysteresis gap between them
    is evidence, and a single line through both would hide it.
    """
    frame = _Frame()
    _open(frame, title, desc)
    points = [*curve.falling, *curve.rising]
    top = nice_ceiling(max((p.rpm for p in points), default=0) * 1.1)
    _y_axis(frame, top, "RPM", right=False)
    _x_axis(frame, 100, "Duty (%)", [0, 20, 40, 60, 80, 100])

    def xy(duty: float, rpm: float) -> tuple[float, float]:
        return _LEFT + duty / 100 * PLOT_W, _TOP + PLOT_H - rpm / top * PLOT_H

    legend: list[tuple[str, str]] = []
    for name, css, leg in (
        (falling_name, "series-fall", curve.falling),
        (rising_name, "series-rise", curve.rising),
    ):
        if not leg:
            continue
        legend.append((name, css))
        coords = [xy(p.duty_pct, p.rpm) for p in sorted(leg, key=lambda p: p.duty_pct)]
        frame.add(
            f'<polyline class="{css}" points="'
            + " ".join(f"{_num(x)},{_num(y)}" for x, y in coords)
            + '"/>'
        )
        for (x, y), p in zip(coords, sorted(leg, key=lambda p: p.duty_pct), strict=True):
            frame.add(
                f'<circle class="{css} dot" cx="{_num(x)}" cy="{_num(y)}" r="3">'
                f"<title>{_esc(f'{p.duty_pct} % → {p.rpm} rpm')}</title></circle>"
            )
    _legend(frame, legend)
    frame.add("</svg>")
    return "".join(frame.parts)


def time_chart(
    t_ms: Sequence[int],
    series: Sequence[TimeSeries],
    *,
    title: str,
    left_label: str,
    right_label: str = "",
    right_top: float = 100.0,
    shading: Sequence[tuple[int, int]] = (),
    shading_label: str = "",
    desc: str = "",
) -> tuple[str, bool]:
    """A time chart over the run. Returns ``(svg, decimated)``.

    ``shading`` spans are ``(start_ms, end_ms)`` pairs drawn behind the lines.
    """
    frame = _Frame()
    _open(frame, title, desc)
    x_max = float(max(t_ms, default=0)) or 1.0
    minutes = x_max / 60000

    def x_of(t: float) -> float:
        return _LEFT + t / x_max * PLOT_W

    for start, end in merge_spans(shading, x_max / PLOT_W):
        x0, x1 = x_of(start), x_of(max(end, start + 1000))
        frame.add(
            f'<rect class="shade-thermal" x="{_num(x0)}" y="{_TOP}" '
            f'width="{_num(max(1.0, x1 - x0))}" height="{PLOT_H}"/>'
        )
    left_values = [
        float(v)
        for s in series
        if s.axis == "left"
        for v in s.values
        if v is not None and not isinstance(v, bool)
    ]
    left_top = nice_ceiling(max(left_values, default=0) * 1.1)
    _y_axis(frame, left_top, left_label, right=False)
    if right_label:
        _y_axis(frame, right_top, right_label, right=True)
    tick_step = nice_ceiling(minutes / 6) if minutes > 0 else 1
    ticks = [i * tick_step for i in range(int(minutes / tick_step) + 1)]
    _x_axis(frame, minutes or 1, "Minutes since the run began", ticks)

    decimated = False
    xs = [float(t) for t in t_ms]
    for s in series:
        top = right_top if s.axis == "right" else left_top
        runs = split_runs(xs, s.values)
        if sum(len(r) for r in runs) > DECIMATE_ABOVE:
            runs = [decimate_minmax(r, 0.0, x_max, PLOT_W) for r in runs]
            decimated = True
        for run in runs:
            coords = " ".join(
                f"{_num(x_of(x))},{_num(_TOP + PLOT_H - min(y, top) / top * PLOT_H)}"
                for x, y in run
            )
            if len(run) == 1:
                x, y = run[0]
                frame.add(
                    f'<circle class="{_esc(s.css_class)} dot" cx="{_num(x_of(x))}" '
                    f'cy="{_num(_TOP + PLOT_H - min(y, top) / top * PLOT_H)}" r="2"/>'
                )
            else:
                frame.add(f'<polyline class="{_esc(s.css_class)}" points="{coords}"/>')
    legend = [(s.name, s.css_class) for s in series]
    if shading and shading_label:
        legend.append((shading_label, "shade-thermal-key"))
    _legend(frame, legend)
    frame.add("</svg>")
    return "".join(frame.parts), decimated
