"""DEC-219: the System State page's four display cards, carved out of the
former god-class page (widget decomposition, Phase 7.1).

Each card owns its widgets and a ``render(vm)`` method fed the Qt-free
view-models from ``services.system_state_view``; the page composes the cards and
routes the VM. Qt-UI only — no logic lives here. objectNames are preserved
verbatim from the page (the DEC-219 golden-master pins them).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import BracketCard, Card, SectionHeader
from control_ofc.ui.components.gauges import RadialGauge
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.theme import active_theme

_REGISTRY_COLS = ["Status", "Chip / Component", "Driver", "Driver Status", "Mainline", "Headers"]
_REG_STATUS = 0
_REG_MAINLINE = 4


# ── shared UI helpers (card-local) ───────────────────────────────────────


def _severity_border_color(state: str, theme) -> str:
    if state == "crit":
        return theme.status_crit
    if state == "warn":
        return theme.status_warn
    return theme.text_muted


def _row_state_color(state: str, theme) -> str:
    return {
        "ok": theme.status_ok,
        "warn": theme.status_warn,
        "crit": theme.status_crit,
    }.get(state, theme.text_primary)


def _mono(widget) -> None:
    font = widget.font()
    font.setFamily("monospace")
    widget.setFont(font)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            child = item.layout()
            if child is not None:
                _clear_layout(child)


def _set_pill(table: QTableWidget, row: int, col: int, text: str, state: str) -> None:
    pill = StatusPill(text, state)
    pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    holder = QWidget()
    holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(6, 2, 6, 2)
    lay.setSpacing(0)
    lay.addWidget(pill)
    lay.addStretch(1)
    table.setCellWidget(row, col, holder)


def _ensure_items(table: QTableWidget, row: int, ncols: int) -> None:
    for col in range(ncols):
        if table.item(row, col) is None:
            table.setItem(row, col, QTableWidgetItem())


def _make_issue_card(vm) -> QWidget:
    theme = active_theme()
    color = _severity_border_color(vm.severity_state, theme)
    # DEC-258: the shared BracketCard, not a hand-rolled twin. This built the
    # same left-accent-bar shape from a QFrame strip whose colour came from an
    # inline setStyleSheet — an interpolated token, frozen at render time, so the
    # bar kept the old theme's colour after a live theme change. The primitive
    # carries the severity as a QSS property instead, and it was dead code until
    # this call site adopted it.
    card = BracketCard(
        object_name=f"SystemState_IssueCard_{vm.key}",
        state=vm.severity_state if vm.severity_state in ("crit", "warn") else "neutral",
    )
    row = QHBoxLayout(card)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)

    body = QWidget()
    v = QVBoxLayout(body)
    v.setSpacing(4)
    caption = QLabel(f"{vm.severity_glyph} {vm.severity_word}")
    caption.setObjectName(f"SystemState_IssueSeverity_{vm.key}")
    caption.setStyleSheet(f"color: {color}; font-weight: bold;")
    v.addWidget(caption)
    title = QLabel(vm.title)
    title.setObjectName(f"SystemState_IssueTitle_{vm.key}")
    title.setWordWrap(True)
    title.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600;")
    v.addWidget(title)
    if vm.description:
        desc = QLabel(vm.description)
        desc.setObjectName(f"SystemState_IssueDesc_{vm.key}")
        desc.setProperty("class", "CardMeta")
        desc.setWordWrap(True)
        v.addWidget(desc)
    if vm.detail:
        box = QLabel(vm.detail)
        box.setObjectName(f"SystemState_IssueDetail_{vm.key}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setWordWrap(True)
        box.setOpenExternalLinks(True)
        box.setStyleSheet(
            f"background: {theme.surface_2}; border: 1px solid {theme.border_default};"
            " border-radius: 4px; padding: 6px;"
        )
        v.addWidget(box)
    if vm.doc_url:
        btn = make_button(
            f"{vm.doc_title or 'Hardware Guide'} ↗",
            "secondary",
            object_name=f"SystemState_IssueDoc_{vm.key}",
        )
        btn.clicked.connect(lambda _=False, url=vm.doc_url: QDesktopServices.openUrl(QUrl(url)))
        doc_row = QHBoxLayout()
        doc_row.addWidget(btn)
        doc_row.addStretch(1)
        v.addLayout(doc_row)
    row.addWidget(body, 1)
    return card


# ── cards ────────────────────────────────────────────────────────────────


class HealthCard(Card):
    """System Health Overview — issue-count pill, summary line, and the
    severity-sorted issue cards. The page also pushes fetch/error text through
    ``set_summary()``."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_health")
        v = QVBoxLayout(self)
        header = SectionHeader(
            "System Health Overview", object_name="SystemState_SectionHeader_health"
        )
        self._issue_pill = StatusPill("—", "neutral")
        self._issue_pill.setObjectName("SystemState_Pill_issueCount")
        header.add_trailing(self._issue_pill)
        v.addWidget(header)

        self._summary_label = QLabel("—")
        self._summary_label.setObjectName("SystemState_Label_summary")
        self._summary_label.setProperty("class", "CardMeta")
        self._summary_label.setWordWrap(True)
        v.addWidget(self._summary_label)

        self._issues_container = QWidget()
        self._issues_layout = QVBoxLayout(self._issues_container)
        self._issues_layout.setContentsMargins(0, 0, 0, 0)
        self._issues_layout.setSpacing(8)
        v.addWidget(self._issues_container)
        v.addStretch(1)

    def set_summary(self, text: str) -> None:
        self._summary_label.setText(text)

    def render(self, vm) -> None:
        self._issue_pill.set_text(vm.issue_count_label)
        self._issue_pill.set_state(vm.issue_count_state)
        summary = vm.summary_line
        if vm.board_line:
            summary = f"{summary}  ·  {vm.board_line}"
        self._summary_label.setText(summary)
        self._rebuild_issue_cards(vm.issue_cards)

    def _rebuild_issue_cards(self, cards) -> None:
        _clear_layout(self._issues_layout)
        if not cards:
            ok = QLabel("No hardware issues detected — system ready.")
            ok.setObjectName("SystemState_Label_noIssues")
            ok.setProperty("class", "CardMeta")
            self._issues_layout.addWidget(ok)
            return
        for vm in cards:
            self._issues_layout.addWidget(_make_issue_card(vm))


class InterferenceCard(Card):
    """BIOS-reclaim Interference Monitor — radial reverts gauge + contention text."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_interference")
        v = QVBoxLayout(self)
        v.addWidget(
            SectionHeader(
                "Interference Monitor", object_name="SystemState_SectionHeader_interference"
            )
        )
        self._gauge = RadialGauge(object_name="SystemState_Gauge_reverts")
        gauge_row = QHBoxLayout()
        gauge_row.addStretch(1)
        gauge_row.addWidget(self._gauge)
        gauge_row.addStretch(1)
        v.addLayout(gauge_row)

        self._contention_title = QLabel("—")
        self._contention_title.setObjectName("SystemState_Label_contentionTitle")
        self._contention_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._contention_title.setProperty("class", "PageSubtitle")
        v.addWidget(self._contention_title)

        self._header_id_label = QLabel("")
        self._header_id_label.setObjectName("SystemState_Label_headerId")
        self._header_id_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _mono(self._header_id_label)
        v.addWidget(self._header_id_label)

        self._interference_explain = QLabel("")
        self._interference_explain.setObjectName("SystemState_Label_interferenceExplain")
        self._interference_explain.setProperty("class", "CardMeta")
        self._interference_explain.setWordWrap(True)
        v.addWidget(self._interference_explain)

    def render(self, vm) -> None:
        if vm.has_contention:
            self._gauge.set_value(
                vm.gauge_fraction,
                center_text=str(vm.highest_count),
                caption="REVERTS",
                state=vm.severity_state,
            )
        else:
            self._gauge.set_value(0.0, center_text="0", caption="REVERTS", state="ok")
        self._contention_title.setText(vm.title)
        self._header_id_label.setText(vm.header_id or "")
        self._header_id_label.setVisible(bool(vm.header_id))
        self._interference_explain.setText(vm.explanation)


class SafetyCard(Card):
    """Safety & GPU limits — CPU thermal state, GPU rows, firmware speed range."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_safety")
        v = QVBoxLayout(self)
        v.addWidget(
            SectionHeader("Safety & GPU Limits", object_name="SystemState_SectionHeader_safety")
        )

        thermal_row = QHBoxLayout()
        tl = QLabel("CPU Thermal State")
        tl.setProperty("class", "CardMeta")
        thermal_row.addWidget(tl)
        thermal_row.addStretch(1)
        self._thermal_label = QLabel("—")
        self._thermal_label.setObjectName("SystemState_Label_thermal")
        thermal_row.addWidget(self._thermal_label)
        self._thermal_pill = StatusPill("—", "neutral")
        self._thermal_pill.setObjectName("SystemState_Pill_thermal")
        thermal_row.addWidget(self._thermal_pill)
        v.addLayout(thermal_row)

        self._gpu_model_label = QLabel("")
        self._gpu_model_label.setObjectName("SystemState_Label_gpuModel")
        v.addWidget(self._gpu_model_label)

        self._gpu_rows_container = QWidget()
        self._gpu_rows_layout = QVBoxLayout(self._gpu_rows_container)
        self._gpu_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._gpu_rows_layout.setSpacing(4)
        v.addWidget(self._gpu_rows_container)

        self._speed_bar_holder = QWidget()
        self._speed_bar_holder.setObjectName("SystemState_Bar_speedRange")
        speed_v = QVBoxLayout(self._speed_bar_holder)
        speed_v.setContentsMargins(0, 4, 0, 0)
        speed_v.setSpacing(2)
        self._speed_label = QLabel("")
        self._speed_label.setProperty("class", "CardMeta")
        speed_v.addWidget(self._speed_label)
        self._speed_bar = QWidget()
        self._speed_bar.setFixedHeight(6)
        self._speed_bar_inner = QHBoxLayout(self._speed_bar)
        self._speed_bar_inner.setContentsMargins(0, 0, 0, 0)
        self._speed_bar_inner.setSpacing(0)
        speed_v.addWidget(self._speed_bar)
        v.addWidget(self._speed_bar_holder)
        self._speed_bar_holder.setVisible(False)

    def render(self, vm) -> None:
        thermal = vm.thermal_text
        if vm.thermal_limit_text:
            thermal = f"{thermal}  ({vm.thermal_limit_text})"
        self._thermal_label.setText(thermal)
        self._thermal_pill.set_text(vm.thermal_text)
        self._thermal_pill.set_state(vm.thermal_state)

        _clear_layout(self._gpu_rows_layout)
        self._gpu_model_label.setText(vm.gpu_model if vm.has_gpu else "No discrete GPU detected")
        theme = active_theme()
        for r in vm.gpu_rows:
            label = QLabel(f"{r.label}: {r.value}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_row_state_color(r.state, theme)};")
            self._gpu_rows_layout.addWidget(label)

        if vm.speed_bar_visible and vm.speed_min is not None and vm.speed_max is not None:
            self._speed_label.setText(f"Firmware speed range: {vm.speed_min}% - {vm.speed_max}%")
            _clear_layout(self._speed_bar_inner)
            below = QFrame()
            below.setStyleSheet(f"background: {theme.status_crit}; border: none;")
            within = QFrame()
            within.setStyleSheet(f"background: {theme.status_ok}; border: none;")
            self._speed_bar_inner.addWidget(below, max(vm.speed_min, 1))
            self._speed_bar_inner.addWidget(within, max(vm.speed_max - vm.speed_min, 1))
            self._speed_bar_holder.setVisible(True)
        else:
            self._speed_bar_holder.setVisible(False)


class RegistryCard(Card):
    """Hardware Registry — the chip/driver table + a trailing summary label
    (the page pushes the same summary line the health card shows)."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_registry")
        v = QVBoxLayout(self)
        header = SectionHeader(
            "Hardware Registry", object_name="SystemState_SectionHeader_registry"
        )
        self._registry_summary = QLabel("—")
        self._registry_summary.setObjectName("SystemState_Label_registrySummary")
        self._registry_summary.setProperty("class", "CardMeta")
        header.add_trailing(self._registry_summary)
        v.addWidget(header)

        self._registry_table = QTableWidget(0, len(_REGISTRY_COLS))
        self._registry_table.setObjectName("SystemState_Table_registry")
        self._registry_table.setHorizontalHeaderLabels(_REGISTRY_COLS)
        apply_dense_table(self._registry_table)
        self._registry_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._registry_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._registry_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._registry_table)

    def set_summary(self, text: str) -> None:
        self._registry_summary.setText(text)

    def render(self, rows) -> None:
        for r in range(self._registry_table.rowCount()):
            self._registry_table.removeCellWidget(r, _REG_STATUS)
        self._registry_table.setRowCount(len(rows))
        theme = active_theme()
        for i, vm in enumerate(rows):
            _ensure_items(self._registry_table, i, len(_REGISTRY_COLS))
            self._registry_table.item(i, 1).setText(vm.component)
            self._registry_table.item(i, 2).setText(vm.driver)
            self._registry_table.item(i, 3).setText(vm.driver_status)
            mainline_item = self._registry_table.item(i, _REG_MAINLINE)
            mainline_item.setText(vm.mainline)
            mainline_item.setForeground(QColor(_row_state_color(vm.mainline_state, theme)))
            self._registry_table.item(i, 5).setText(vm.headers)
            if vm.tooltip:
                for c in range(1, len(_REGISTRY_COLS)):
                    self._registry_table.item(i, c).setToolTip(vm.tooltip)
            _set_pill(self._registry_table, i, _REG_STATUS, vm.status_label, vm.status_state)
