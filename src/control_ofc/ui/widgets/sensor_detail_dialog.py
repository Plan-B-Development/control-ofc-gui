"""Per-sensor detail dialog for Diagnostics > Sensors (DEC-117).

Pop-out modal that mirrors :class:`ReadinessReportDialog`'s structure — a
:class:`QTextBrowser` that scrolls for arbitrary volume, with external
links enabled (kernel.org driver docs, etc.).

Every daemon-supplied string is HTML-escaped before interpolation (DEC-106
discipline). GUI-authored static labels are trusted.

The dialog is intentionally read-only: it surfaces the rich classification
notes, board context, threshold attributes, and session statistics in one
place so the user doesn't have to hover every cell to learn what a sensor
actually represents.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.sensor_knowledge import (
    BOARD_SENSOR_OVERRIDES,
    classify_sensor,
    kernel_doc_url_for_chip,
    lookup_board_override,
    temp_type_label,
)
from control_ofc.ui.theme import active_theme

if TYPE_CHECKING:
    from control_ofc.api.models import (
        BoardInfo,
        SensorReading,
        SensorThresholds,
    )


_CONFIDENCE_DISPLAY: dict[str, str] = {
    "high": "High",
    "medium_high": "Medium-High",
    "medium": "Medium",
    "low": "Low",
}


def _link(url: str, title: str) -> str:
    """Theme-coloured inline anchor (same pattern as ``readiness_report._link``).

    Colour is set inline because the app-wide stylesheet otherwise overrides
    the palette Link role; inline colour is the only reliably-applied path
    inside a :class:`QTextBrowser`.
    """
    return f'<a href="{escape(url)}" style="color:{active_theme().status_info}">{escape(title)}</a>'


def _trend_arrow(rate_c_per_s: float | None) -> str | None:
    """Render an at-a-glance ↑/↓ arrow with rate, or ``None`` when quiet.

    Mirrors the suppression rule already used by
    :func:`sensor_knowledge.format_sensor_tooltip` (hide noise below 0.1 °C/s)
    so the dialog and the tooltip stay consistent.
    """
    if rate_c_per_s is None or abs(rate_c_per_s) < 0.1:
        return None
    arrow = "↑" if rate_c_per_s > 0 else "↓"
    sign = "+" if rate_c_per_s > 0 else ""
    return f"{arrow} {sign}{rate_c_per_s:.1f} °C/s"


def _threshold_rows(t: SensorThresholds) -> list[tuple[str, str]]:
    """Build (label, value) rows for the Thresholds table.

    Skips fields the daemon didn't supply, so a sensor exposing only `crit`
    renders a one-row table rather than ten "—" rows.
    """
    rows: list[tuple[str, str]] = []
    if t.max_c is not None:
        rows.append(("Max (warning)", f"{t.max_c:.1f} °C"))
    if t.min_c is not None:
        rows.append(("Min (warning)", f"{t.min_c:.1f} °C"))
    if t.crit_c is not None:
        rows.append(("Critical", f"{t.crit_c:.1f} °C"))
    if t.crit_hyst_c is not None:
        rows.append(("Critical hysteresis", f"{t.crit_hyst_c:.1f} °C"))
    if t.emergency_c is not None:
        rows.append(("Emergency", f"{t.emergency_c:.1f} °C"))
    if t.emergency_hyst_c is not None:
        rows.append(("Emergency hysteresis", f"{t.emergency_hyst_c:.1f} °C"))
    if t.lcrit_c is not None:
        rows.append(("Lower critical", f"{t.lcrit_c:.1f} °C"))
    if t.offset_c is not None:
        rows.append(("Offset", f"{t.offset_c:+.1f} °C"))
    if t.alarm is not None:
        rows.append(("Alarm", "asserted" if t.alarm else "clear"))
    if t.max_alarm is not None:
        rows.append(("Max alarm", "asserted" if t.max_alarm else "clear"))
    if t.crit_alarm is not None:
        rows.append(("Crit alarm", "asserted" if t.crit_alarm else "clear"))
    if t.fault is not None:
        rows.append(("Fault", "asserted" if t.fault else "clear"))
    return rows


def _headroom_html(value_c: float, t: SensorThresholds) -> str | None:
    """Return a one-line headroom-to-crit string when ``crit_c`` is known.

    Pure GUI-authored content, no daemon strings — safe to render as rich
    text without escaping.
    """
    if t.crit_c is None:
        return None
    headroom = t.crit_c - value_c
    theme = active_theme()
    if headroom <= 0:
        colour = theme.status_crit
        verb = "exceeds crit"
    elif headroom < 10:
        colour = theme.status_warn
        verb = f"only {headroom:.1f} °C below crit"
    else:
        colour = theme.status_ok
        verb = f"{headroom:.1f} °C below crit"
    return f'<span style="color:{colour}">{verb}</span>'


def build_sensor_detail_html(
    sensor: SensorReading,
    board: BoardInfo | None,
) -> str:
    """Build the full self-contained HTML document for the detail dialog.

    Daemon-supplied strings (id, label, chip_name, board vendor/name/BIOS)
    are HTML-escaped at every interpolation site. Threshold values are
    formatted by us from floats, so they need no escaping.
    """
    t = active_theme()
    board_vendor = board.vendor if board is not None else ""
    classification = classify_sensor(
        chip_name=sensor.chip_name,
        label=sensor.label,
        temp_type=sensor.temp_type,
        board_vendor=board_vendor,
    )

    def h(title: str) -> str:
        return f'<h3 style="color:{t.text_primary};margin-bottom:2px">{escape(title)}</h3>'

    parts: list[str] = []

    # ── Header line ────────────────────────────────────────────────
    conf = _CONFIDENCE_DISPLAY.get(classification.confidence, classification.confidence)
    header_label = escape(sensor.label or sensor.id or "Sensor")
    parts.append(
        f'<div style="color:{t.text_primary};font-size:large;font-weight:bold">'
        f"{header_label}</div>"
        f'<div style="color:{t.text_secondary}">'
        f"{escape(classification.source_class)} — {escape(conf)} confidence</div>"
    )

    # ── Identity ───────────────────────────────────────────────────
    parts.append(h("Identity"))
    id_rows = [
        ("Sensor ID", sensor.id or "—"),
        ("Source", sensor.source or "—"),
        ("Chip", sensor.chip_name or "—"),
        ("Kind", sensor.kind or "—"),
        ("Driver type", temp_type_label(sensor.temp_type)),
    ]
    parts.append('<table cellpadding="4">')
    for label, value in id_rows:
        parts.append(
            f'<tr><td style="color:{t.text_secondary}">{escape(label)}</td>'
            f"<td>{escape(value)}</td></tr>"
        )
    parts.append("</table>")

    # ── Current state ──────────────────────────────────────────────
    parts.append(h("Current state"))
    state_rows: list[tuple[str, str]] = [
        ("Value", f"{sensor.value_c:.1f} °C"),
        ("Age", f"{sensor.age_ms} ms"),
        ("Freshness", sensor.freshness.value),
    ]
    trend = _trend_arrow(sensor.rate_c_per_s)
    if trend is not None:
        state_rows.append(("Trend", trend))
    parts.append('<table cellpadding="4">')
    for label, value in state_rows:
        parts.append(
            f'<tr><td style="color:{t.text_secondary}">{escape(label)}</td>'
            f"<td>{escape(value)}</td></tr>"
        )
    parts.append("</table>")

    # ── Session history ────────────────────────────────────────────
    if sensor.session_min_c is not None and sensor.session_max_c is not None:
        parts.append(h("Session range"))
        rng = sensor.session_max_c - sensor.session_min_c
        position = ""
        if rng > 0.05:
            pct = (sensor.value_c - sensor.session_min_c) / rng * 100.0
            pct = max(0.0, min(100.0, pct))
            position = f" — currently at {pct:.0f}% of session range"
        parts.append(
            f"<div>{sensor.session_min_c:.1f} °C - {sensor.session_max_c:.1f} °C"
            f"{escape(position)}</div>"
        )

    # ── Classification + every note ────────────────────────────────
    parts.append(h("Classification"))
    parts.append(f"<div>{escape(classification.display_description)}</div>")
    if classification.notes:
        parts.append('<ul style="margin-top:4px">')
        for note in classification.notes:
            parts.append(f"<li>{escape(note)}</li>")
        parts.append("</ul>")

    # ── Board override (if any) ────────────────────────────────────
    if board is not None:
        override = lookup_board_override(board.vendor, board.name, sensor.label)
        if override is not None:
            parts.append(h("Board override"))
            parts.append(
                f"<div>{escape(override.display_description)}</div>"
                f'<div style="color:{t.text_secondary}">'
                f"Matched {escape(override.vendor_pattern)} / "
                f"{escape(override.model_pattern)} / "
                f"{escape(override.label_pattern)}</div>"
            )
            if override.notes:
                parts.append('<ul style="margin-top:4px">')
                for note in override.notes:
                    parts.append(f"<li>{escape(note)}</li>")
                parts.append("</ul>")
        elif _has_override_for(board.vendor, board.name):
            # Board is known but this label is not — make it explicit that
            # we've seen the board, not that we silently dropped overrides.
            parts.append(
                f'<div style="color:{t.text_secondary};margin-top:6px">'
                "No board-specific override for this label "
                "(general classification applies).</div>"
            )

    # ── Board context ──────────────────────────────────────────────
    if board is not None and (board.vendor or board.name):
        parts.append(h("Board context"))
        bits: list[str] = []
        if board.vendor:
            bits.append(escape(board.vendor))
        if board.name:
            bits.append(escape(board.name))
        if board.bios_version:
            bits.append(f"BIOS {escape(board.bios_version)}")
        parts.append(f"<div>{' — '.join(bits)}</div>")

    # ── Thresholds (DEC-117) ───────────────────────────────────────
    parts.append(h("Thresholds"))
    rows = _threshold_rows(sensor.thresholds) if sensor.thresholds is not None else []
    if not rows:
        parts.append(
            f'<div style="color:{t.text_secondary}">'
            "Daemon did not report any threshold attributes for this sensor "
            "(driver coverage varies — k10temp typically exposes none).</div>"
        )
    else:
        parts.append('<table cellpadding="4">')
        for label, value in rows:
            parts.append(
                f'<tr><td style="color:{t.text_secondary}">{escape(label)}</td>'
                f"<td>{escape(value)}</td></tr>"
            )
        parts.append("</table>")
        if sensor.thresholds is not None:
            head = _headroom_html(sensor.value_c, sensor.thresholds)
            if head is not None:
                parts.append(f'<div style="margin-top:4px">{head}</div>')

    # ── Driver doc link ────────────────────────────────────────────
    doc_url = kernel_doc_url_for_chip(sensor.chip_name)
    if doc_url is not None:
        parts.append(h("Driver documentation"))
        parts.append(f"<div>{_link(doc_url, doc_url)}</div>")

    body = "".join(parts)
    return f'<div style="color:{t.text_primary}">{body}</div>'


def _has_override_for(vendor: str, model: str) -> bool:
    """True when at least one entry in the override database matches this
    board (regardless of label). Used to differentiate "board not in our
    DB" from "board in DB but this sensor has no override"."""
    lower_vendor = vendor.lower()
    lower_model = model.lower()
    for override in BOARD_SENSOR_OVERRIDES:
        if (
            override.vendor_pattern.lower() in lower_vendor
            and override.model_pattern.lower() in lower_model
        ):
            return True
    return False


class SensorDetailDialog(QDialog):
    """Themed, resizable window showing one sensor's full diagnostic detail.

    Uses a :class:`QTextBrowser` so the report scrolls for arbitrarily long
    content (some chips have many notes) and so kernel.org driver doc links
    open in the user's browser with a single click.
    """

    def __init__(
        self,
        sensor: SensorReading,
        board: BoardInfo | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Diagnostics_SensorDetail_Dialog")
        title = sensor.label or sensor.id or "Sensor"
        self.setWindowTitle(f"Sensor Detail — {title}")
        self.resize(640, 600)

        layout = QVBoxLayout(self)

        self._browser = QTextBrowser()
        self._browser.setObjectName("Diagnostics_SensorDetail_Browser")
        self._browser.setOpenExternalLinks(True)
        self._browser.setHtml(build_sensor_detail_html(sensor, board))
        layout.addWidget(self._browser, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("Diagnostics_SensorDetail_Btn_close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def set_sensor(self, sensor: SensorReading, board: BoardInfo | None) -> None:
        """Replace contents in place — used when the dialog is reopened on a
        different row of the table without rebuilding the widget."""
        title = sensor.label or sensor.id or "Sensor"
        self.setWindowTitle(f"Sensor Detail — {title}")
        self._browser.setHtml(build_sensor_detail_html(sensor, board))
