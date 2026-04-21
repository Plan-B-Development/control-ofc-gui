"""Tests for table UX improvements: resizable columns, splitter, tooltips, doc links."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QSplitter, QTableWidget

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    FanReading,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    HwmonHeader,
    KernelModuleInfo,
    OperationMode,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_page(qtbot, state=None):
    s = state or _make_state()
    page = DiagnosticsPage(state=s)
    qtbot.addWidget(page)
    return page, s


def _make_hw_diagnostics(**overrides) -> HardwareDiagnosticsResult:
    defaults = dict(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(
                    chip_name="nct6798",
                    device_id="nct6798.656",
                    expected_driver="nct6775",
                    in_mainline_kernel=True,
                    header_count=5,
                ),
            ],
            total_headers=5,
            writable_headers=3,
        ),
        thermal_safety=ThermalSafetyInfo(
            state="normal",
            cpu_sensor_found=True,
            emergency_threshold_c=105.0,
            release_threshold_c=80.0,
        ),
        kernel_modules=[
            KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True),
        ],
        board=BoardInfo(vendor="ASUSTeK", name="ROG STRIX X670E"),
    )
    defaults.update(overrides)
    return HardwareDiagnosticsResult(**defaults)


# ---------------------------------------------------------------------------
# 1. Resizable columns (Interactive mode)
# ---------------------------------------------------------------------------


class TestDiagnosticsResizableColumns:
    """All diagnostics tables use Interactive resize mode with stretch-last."""

    def test_sensor_table_interactive(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_sensors")
        header = table.horizontalHeader()
        for col in range(table.columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection() is True

    def test_chip_table_interactive(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_chips")
        header = table.horizontalHeader()
        for col in range(table.columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection() is True

    def test_modules_table_interactive(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_kernelModules")
        header = table.horizontalHeader()
        for col in range(table.columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection() is True

    def test_fan_table_interactive(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_fans")
        header = table.horizontalHeader()
        for col in range(table.columnCount()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection() is True


# ---------------------------------------------------------------------------
# 2. Draggable splitter in Fans tab
# ---------------------------------------------------------------------------


class TestFansTabSplitter:
    """Fans tab uses a vertical splitter between Hardware Readiness and Fan table."""

    def test_splitter_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        splitter = page.findChild(QSplitter, "Diagnostics_Splitter_fans")
        assert splitter is not None

    def test_splitter_vertical(self, qtbot):
        page, _ = _make_page(qtbot)
        splitter = page.findChild(QSplitter, "Diagnostics_Splitter_fans")
        assert splitter.orientation() == Qt.Orientation.Vertical

    def test_splitter_not_collapsible(self, qtbot):
        page, _ = _make_page(qtbot)
        splitter = page.findChild(QSplitter, "Diagnostics_Splitter_fans")
        assert splitter.childrenCollapsible() is False

    def test_splitter_has_two_children(self, qtbot):
        page, _ = _make_page(qtbot)
        splitter = page.findChild(QSplitter, "Diagnostics_Splitter_fans")
        assert splitter.count() == 2


# ---------------------------------------------------------------------------
# 3. Tooltips on Fans tab headers and cells
# ---------------------------------------------------------------------------


class TestFanTableHeaderTooltips:
    """Fan table column headers have descriptive tooltips."""

    def test_all_headers_have_tooltips(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_fans")
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            assert item is not None
            assert len(item.toolTip()) > 0, f"Column {col} missing tooltip"

    def test_id_header_tooltip(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_fans")
        assert "identifier" in table.horizontalHeaderItem(0).toolTip().lower()

    def test_freshness_header_tooltip(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_fans")
        tip = table.horizontalHeaderItem(4).toolTip()
        assert "ok" in tip.lower()
        assert "stale" in tip.lower()


class TestFanTableCellTooltips:
    """Fan table cells get per-row tooltips on data update."""

    def test_fan_cell_tooltip_contains_id(self, qtbot):
        state = _make_state()
        page, _ = _make_page(qtbot, state=state)
        fans = [FanReading(id="fan1", source="openfan", rpm=1200, age_ms=100)]
        page._on_fans(fans)
        item = page._fan_table.item(0, 0)
        assert "fan1" in item.toolTip()

    def test_hwmon_fan_tooltip_shows_chip(self, qtbot):
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon0_fan1", chip_name="nct6798", is_writable=True),
        ]
        page, _ = _make_page(qtbot, state=state)
        fans = [FanReading(id="hwmon0_fan1", source="hwmon", rpm=800, age_ms=50)]
        page._on_fans(fans)
        tip = page._fan_table.item(0, 0).toolTip()
        assert "nct6798" in tip
        assert "nct6775" in tip  # driver name

    def test_pwm_only_tooltip_says_no_rpm(self, qtbot):
        state = _make_state()
        state.hwmon_headers = [
            HwmonHeader(id="hwmon0_pwm3", chip_name="nct6798", is_writable=False),
        ]
        page, _ = _make_page(qtbot, state=state)
        page._on_fans([])  # no real fans, just pwm_only
        tip = page._fan_table.item(0, 0).toolTip()
        assert "PWM output only" in tip
        assert "read-only" in tip

    def test_all_cells_in_row_share_tooltip(self, qtbot):
        state = _make_state()
        page, _ = _make_page(qtbot, state=state)
        fans = [FanReading(id="fan1", source="openfan", rpm=1200, age_ms=100)]
        page._on_fans(fans)
        tip0 = page._fan_table.item(0, 0).toolTip()
        for col in range(1, 5):
            assert page._fan_table.item(0, col).toolTip() == tip0


class TestChipTableHeaderTooltips:
    """Chip and modules tables also have header tooltips."""

    def test_chip_table_headers_have_tooltips(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_chips")
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            assert item is not None
            assert len(item.toolTip()) > 0, f"Chip table column {col} missing tooltip"

    def test_modules_table_headers_have_tooltips(self, qtbot):
        page, _ = _make_page(qtbot)
        table = page.findChild(QTableWidget, "Diagnostics_Table_kernelModules")
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            assert item is not None
            assert len(item.toolTip()) > 0, f"Modules table column {col} missing tooltip"


# ---------------------------------------------------------------------------
# 4. Clickable documentation links
# ---------------------------------------------------------------------------


class TestGuidanceLinks:
    """Guidance section uses rich text with clickable driver doc links."""

    def test_guidance_label_is_rich_text(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_guidance")
        assert label.textFormat() == Qt.TextFormat.RichText

    def test_guidance_label_opens_external_links(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_guidance")
        assert label.openExternalLinks() is True

    def test_guidance_contains_clickable_driver_link(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_hw_diagnostics()
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_guidance")
        text = label.text()
        assert '<a href="' in text
        assert "kernel.org" in text or "github.com" in text

    def test_docs_link_label_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_docsLink")
        assert label is not None
        assert label.textFormat() == Qt.TextFormat.RichText
        assert label.openExternalLinks() is True

    def test_docs_link_shown_when_chips_detected(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_hw_diagnostics()
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_docsLink")
        assert not label.isHidden()
        assert "Hardware Compatibility Guide" in label.text()
        assert '<a href="' in label.text()

    def test_docs_link_hidden_when_no_chips(self, qtbot):
        page, _ = _make_page(qtbot)
        diag = _make_hw_diagnostics(
            hwmon=HwmonDiagnostics(
                chips_detected=[],
                total_headers=0,
                writable_headers=0,
            )
        )
        page._populate_hw_diagnostics(diag)
        label = page.findChild(QLabel, "Diagnostics_Label_docsLink")
        assert label.isHidden()
