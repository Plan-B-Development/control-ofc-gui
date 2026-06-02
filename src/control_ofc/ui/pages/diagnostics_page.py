"""Diagnostics page — daemon health, sensor/fan status, lease, logs, support export."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    FanReading,
    Freshness,
    HardwareDiagnosticsResult,
    HwmonCapability,
    HwmonHeader,
    HwmonVerifyResult,
    LeaseState,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService, format_uptime
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    PRESENCE_TOOLTIP,
    FanPresence,
    classify_fan_presence,
)
from control_ofc.ui.hwmon_guidance import (
    detect_module_conflicts,
    dual_chip_verify_hint,
    dual_chip_warning_html,
    format_driver_status,
    lookup_chip_guidance,
    lookup_vendor_quirks,
    verification_guidance,
)
from control_ofc.ui.sensor_knowledge import classify_sensor, format_sensor_tooltip
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.event_log_view import EventLogView

_TRANSPARENT = "background: transparent;"

log = logging.getLogger(__name__)

# Plain-English explanations for the Control method column. Keys are the
# display strings emitted by ``_fan_control_method`` /
# ``_pwm_only_control_method`` — every string those helpers can return must
# have an entry here so a tooltip is guaranteed.
_CONTROL_METHOD_TOOLTIPS: dict[str, str] = {
    "OpenFan USB": "OpenFan Controller connected via USB serial. No lease required.",
    "hwmon PWM (lease)": (
        "Motherboard fan controlled via hwmon PWM. Requires a hwmon lease before writes."
    ),
    "hwmon PWM — no RPM": (
        "Motherboard PWM output without a tachometer input. Writable but no RPM feedback."
    ),
    "hwmon PWM (legacy)": ("Pre-RDNA3 GPU fan controlled via the legacy pwm1 sysfs interface."),
    "PMFW curve": ("GPU fan controlled via the AMD PMFW fan_curve sysfs interface."),
    "read-only": (
        "BIOS/EC owns this fan; PWM writes will be reverted. Run Test PWM Control to confirm."
    ),
    "no fan control": "GPU has no writable fan control path exposed to the OS.",
    "unknown": "Daemon did not report a classification for this fan.",
}

# Display strings for the sensor Confidence column. Keys come from
# ``SensorClassification.confidence`` (high / medium_high / medium / low).
_CONFIDENCE_DISPLAY: dict[str, str] = {
    "high": "High",
    "medium_high": "Medium-High",
    "medium": "Medium",
    "low": "Low",
}


def _fan_control_method(fan: FanReading, state: AppState | None) -> str:
    """Return the Control method display string for a fan.

    Derived exclusively from daemon-reported typed data
    (``HwmonHeader.is_writable``, ``AmdGpuCapability.fan_control_method``).
    No heuristic inference from source/id strings — returns the literal
    ``"unknown"`` when the daemon has not classified the fan.
    """
    if fan.source == "openfan":
        return "OpenFan USB"
    if fan.source == "amd_gpu":
        if not state or not state.capabilities or not state.capabilities.amd_gpu.present:
            return "unknown"
        method = state.capabilities.amd_gpu.fan_control_method
        return {
            "pmfw_curve": "PMFW curve",
            "hwmon_pwm": "hwmon PWM (legacy)",
            "read_only": "read-only",
            "none": "no fan control",
        }.get(method, "unknown")
    if fan.source == "hwmon":
        if not state:
            return "unknown"
        header = next((h for h in state.hwmon_headers if h.id == fan.id), None)
        if header is None:
            return "unknown"
        return "read-only" if not header.is_writable else "hwmon PWM (lease)"
    return "unknown"


def _pwm_only_control_method(header: HwmonHeader) -> str:
    """Return the Control method display string for a PWM-only hwmon header
    (a header that has no ``fan_input`` tachometer)."""
    return "read-only" if not header.is_writable else "hwmon PWM — no RPM"


def _hwmon_overview_text(
    hwmon_cap: HwmonCapability,
    writable_headers: int | None,
) -> tuple[str, bool]:
    """Render the Overview hwmon line and whether it should be warn-styled.

    ``writable_headers`` is the runtime value from
    ``HardwareDiagnosticsResult.hwmon.writable_headers`` once hardware
    diagnostics has been fetched, or ``None`` before then. When
    ``writable_headers == 0`` but headers exist, the line is rewritten to
    surface the read-only state rather than the daemon-code-level ``write``
    capability flag.
    """
    if not hwmon_cap.present:
        return "hwmon: Not present", False
    count = hwmon_cap.pwm_header_count
    if count > 0 and writable_headers == 0:
        return f"hwmon: Present ({count} headers — ALL read-only)", True
    parts: list[str] = []
    if hwmon_cap.write_support:
        parts.append("write")
    if hwmon_cap.lease_required:
        parts.append("lease required")
    suffix = (", " + ", ".join(parts) + ")") if parts else ")"
    return f"hwmon: Present ({count} headers{suffix}", False


def _features_line_text(
    caps: Capabilities,
    writable_headers: int | None,
) -> str:
    """Render the Overview Features line.

    When ``hwmon_write_supported`` is advertised but the runtime
    ``writable_headers`` is zero, the line is annotated so the user
    understands the daemon supports hwmon writes even though the hardware
    currently has no writable header.
    """
    f = caps.features
    features: list[str] = []
    if f.openfan_write_supported:
        features.append("OpenFan writes")
    if f.hwmon_write_supported:
        if writable_headers == 0 and caps.hwmon.present and caps.hwmon.pwm_header_count > 0:
            features.append("hwmon writes (daemon-supported; 0 writable headers on this system)")
        else:
            features.append("hwmon writes")
    return f"Features: {', '.join(features) or 'none'}"


# Severity buckets for the per-header pwm_enable reclaim count surfaced from
# ``HardwareDiagnosticsResult.hwmon.enable_revert_counts``. Tuned to match the
# operator's mental model on AORUS-class boards: zero events means the daemon
# watchdog has nothing to do, occasional reverts mean BIOS interference is
# recoverable, and ≥10 events on a single header indicates a continuous
# tug-of-war between Linux and the EC firmware that BIOS configuration should
# resolve.
RECLAIM_SEVERITY_OK = "ok"
RECLAIM_SEVERITY_WARN = "warn"
RECLAIM_SEVERITY_HIGH = "high"


def classify_reclaim_severity(count: int) -> str:
    """Return the severity bucket for a pwm_enable reclaim count.

    Buckets:
      - ``"ok"``    → ``count <= 0`` (header is healthy, no BIOS interference).
      - ``"warn"``  → ``1 <= count < 10`` (occasional reclaim — daemon is
        recovering but the operator may want to check BIOS Smart Fan settings).
      - ``"high"``  → ``count >= 10`` (continuous reclaim — BIOS is fighting
        the daemon; recommend disabling Smart Fan or using a degenerate curve).

    Negative counts are treated as ``ok`` so callers do not have to defend
    against malformed daemon payloads. The buckets are deliberately coarse so
    the operator's eye is drawn to the *hot* header, not to small fluctuations.
    """
    if count <= 0:
        return RECLAIM_SEVERITY_OK
    if count < 10:
        return RECLAIM_SEVERITY_WARN
    return RECLAIM_SEVERITY_HIGH


def reclaim_severity_color(severity: str) -> str:
    """Return the theme hex colour for a reclaim severity bucket.

    Mirrors ``SuccessChip`` / ``WarningChip`` / ``CriticalChip`` so the per-row
    colours line up with the rest of the diagnostics UI even when this widget
    is rendered in rich-text mode (which doesn't pick up Qt CSS class styling).

    Reads from :func:`active_theme` on every call so a theme switch picks up
    the new status colours on the next render — pre-DEC-109 this was pinned
    to a module-level Default Dark snapshot.
    """
    theme = active_theme()
    if severity == RECLAIM_SEVERITY_OK:
        return theme.status_ok
    if severity == RECLAIM_SEVERITY_HIGH:
        return theme.status_crit
    return theme.status_warn


def render_reclaim_rows(reverts: dict[str, int] | None) -> str | None:
    """Render the per-header reclaim count card body as rich-text HTML.

    Returns ``None`` when there is nothing to surface (no payload, or every
    header reports zero reclaims) so the caller can hide the card entirely.
    Returns a non-empty HTML string otherwise — each header on its own row,
    coloured by ``classify_reclaim_severity``.

    The ``None``-tolerant signature is deliberate: older daemons (pre-1.3.x)
    don't include ``enable_revert_counts`` in the diagnostics payload, and the
    GUI must not crash when the key is absent.
    """
    if not reverts:
        return None
    # Hide the card if every header is at zero — the daemon won't normally
    # emit such a payload, but defending against it keeps the UI quiet when
    # a future daemon decides to surface healthy headers in the same map.
    if not any(count > 0 for count in reverts.values()):
        return None

    rows: list[str] = []
    for header_id in sorted(reverts):
        count = reverts[header_id]
        severity = classify_reclaim_severity(count)
        color = reclaim_severity_color(severity)
        # ``severity`` is a fixed enum string so it is safe to format raw;
        # header_id and count come from the daemon JSON and are escaped so
        # quirky chip names (e.g. "it87.2624") never break the markup.
        from html import escape

        rows.append(
            f'<span style="color: {color};">'
            f"<b>{escape(header_id)}</b>: {count} revert(s) "
            f"[{severity.upper()}]"
            "</span>"
        )
    return "<br>".join(rows)


class _VerifyWorker(QObject):
    """Runs in a QThread — executes the blocking ~3s verify_hwmon_pwm call off
    the UI thread so the rest of the GUI (polling, splitter, menus) keeps
    reacting during the hardware probe."""

    verify_ok = Signal(object)  # HwmonVerifyResult
    verify_error = Signal(str, str)  # category ('unavailable'|'error'), message

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot(str, str)
    def do_verify(self, header_id: str, lease_id: str) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().verify_hwmon_pwm(header_id, lease_id)
            self.verify_ok.emit(result)
        except DaemonTimeout:
            # DEC-098: a verify timeout means the daemon was slow — the write
            # may still have landed. Don't say "unavailable", which implies
            # the daemon is gone. The category stays "unavailable" so the
            # main_window's resume-writes path (paired with verify_completed)
            # still fires; only the message is rewritten.
            self.verify_error.emit(
                "unavailable",
                "Verify timed out (>8s). The daemon may have completed the "
                "write — re-check the fan and re-run if needed.",
            )
        except DaemonUnavailable:
            self.verify_error.emit("unavailable", "Daemon unavailable during verify")
        except DaemonError as e:
            self.verify_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Verify worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.verify_error.emit("unavailable", "Connection lost during verify")

    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


def _transparent_label(text: str, object_name: str, *, bold: bool = False) -> QLabel:
    """Create a QLabel with transparent background, suitable for use inside Card frames."""
    label = QLabel(text)
    label.setObjectName(object_name)
    style = _TRANSPARENT
    if bold:
        style += " font-weight: bold;"
    label.setStyleSheet(style)
    return label


class DiagnosticsPage(QWidget):
    """System health, device discovery, lease state, logs, and support bundle export."""

    # Main-thread signal that fires a queued connection to the verify worker
    # (running on its own QThread) so the ~6s hardware probe never blocks the UI.
    _verify_request = Signal(str, str)

    # Public signals used by main_window to coordinate the GUI control loop —
    # the loop pauses writes to the header under verify so its 1Hz tick does
    # not race the daemon's 6-second verify wait (A1, DEC-101).
    verify_started = Signal(str)  # header_id
    verify_completed = Signal(str)  # header_id

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        settings_service: AppSettingsService | None = None,
        profile_service: ProfileService | None = None,
        client: DaemonClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._client = client
        self._diag = diagnostics_service or DiagnosticsService(
            state, settings_service=settings_service, profile_service=profile_service
        )

        # Lazy-created verify worker + thread (see _ensure_verify_worker).
        self._verify_thread: QThread | None = None
        self._verify_worker: _VerifyWorker | None = None
        # Header currently under verify — used to emit verify_completed with
        # the right id from both ok and error paths (the error signal does not
        # carry the header_id).
        self._verify_active_header: str | None = None

        # DEC-101 (2E): batch verification state. ``_verify_all_queue`` is
        # the remaining headers to test (FIFO); ``_verify_all_results`` is
        # the per-header outcome string for the summary; ``_verify_all_total``
        # is the original count for the progress label "k/N". When the queue
        # is empty and ``_verify_all_total > 0`` we are between iterations
        # and should NOT start a new single-verify. Empty queue + zero total
        # means no batch is active.
        self._verify_all_queue: list[str] = []
        self._verify_all_results: list[tuple[str, str]] = []  # [(header_id, result), …]
        self._verify_all_total: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        title = QLabel("Diagnostics")
        title.setProperty("class", "PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Daemon health, device status, logs, and support tools")
        subtitle.setProperty("class", "PageSubtitle")
        layout.addWidget(subtitle)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        self._tabs.addTab(self._build_sensors_tab(), "Sensors")
        self._tabs.addTab(self._build_fans_tab(), "Fans")
        self._tabs.addTab(self._build_lease_tab(), "Lease")
        self._tabs.addTab(self._build_logs_tab(), "Event Log")
        layout.addWidget(self._tabs, 1)

        # Action buttons
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("Diagnostics_Btn_refreshOverview")
        refresh_btn.clicked.connect(self._refresh_all)
        btn_row.addWidget(refresh_btn)

        export_btn = QPushButton("Export Support Bundle...")
        export_btn.setObjectName("Diagnostics_Btn_export")
        export_btn.setToolTip(
            "Includes system configuration and daemon logs. Review before sharing."
        )
        export_btn.clicked.connect(self._export_bundle)
        btn_row.addWidget(export_btn)

        btn_row.addStretch()
        self._status_label = QLabel("")
        self._status_label.setProperty("class", "CardMeta")
        btn_row.addWidget(self._status_label)
        layout.addLayout(btn_row)

        # Wire signals
        if self._state:
            self._state.capabilities_updated.connect(self._on_capabilities)
            self._state.status_updated.connect(self._on_status)
            self._state.sensors_updated.connect(self._on_sensors)
            self._state.fans_updated.connect(self._on_fans)
            self._state.lease_updated.connect(self._on_lease)

    # ─── Tab builders ────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Daemon health card
        daemon_frame = QFrame()
        daemon_frame.setProperty("class", "Card")
        daemon_layout = QVBoxLayout(daemon_frame)

        self._daemon_version_label = _transparent_label(
            "Daemon: \u2014", "Diagnostics_Label_daemonVersion", bold=True
        )
        self._daemon_version_label.setProperty("class", "PageSubtitle")
        daemon_layout.addWidget(self._daemon_version_label)

        self._daemon_status_label = _transparent_label(
            "Status: \u2014", "Diagnostics_Label_daemonStatus"
        )
        daemon_layout.addWidget(self._daemon_status_label)

        self._daemon_uptime_label = _transparent_label(
            "Uptime: \u2014", "Diagnostics_Label_daemonUptime"
        )
        self._daemon_uptime_label.setProperty("class", "CardMeta")
        daemon_layout.addWidget(self._daemon_uptime_label)

        self._subsystems_label = _transparent_label(
            "Subsystems: \u2014", "Diagnostics_Label_subsystems"
        )
        self._subsystems_label.setWordWrap(True)
        daemon_layout.addWidget(self._subsystems_label)

        # Age explanation note
        age_note = _transparent_label(
            "Age = time since daemon last polled this hardware subsystem",
            "Diagnostics_Label_ageNote",
        )
        age_note.setProperty("class", "CardMeta")
        daemon_layout.addWidget(age_note)

        layout.addWidget(daemon_frame)

        # Device discovery card
        device_frame = QFrame()
        device_frame.setProperty("class", "Card")
        device_layout = QVBoxLayout(device_frame)

        device_title = _transparent_label(
            "Device Discovery", "Diagnostics_Label_deviceTitle", bold=True
        )
        device_title.setProperty("class", "PageSubtitle")
        device_layout.addWidget(device_title)

        self._openfan_label = _transparent_label("OpenFan: \u2014", "Diagnostics_Label_openfan")
        device_layout.addWidget(self._openfan_label)

        self._hwmon_label = _transparent_label("hwmon: \u2014", "Diagnostics_Label_hwmon")
        device_layout.addWidget(self._hwmon_label)

        self._amd_gpu_label = _transparent_label("AMD GPU: \u2014", "Diagnostics_Label_amdGpu")
        device_layout.addWidget(self._amd_gpu_label)

        self._features_label = _transparent_label("Features: \u2014", "Diagnostics_Label_features")
        self._features_label.setWordWrap(True)
        device_layout.addWidget(self._features_label)

        layout.addWidget(device_frame)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_sensors_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self._sensor_table = QTableWidget(0, 7)
        self._sensor_table.setObjectName("Diagnostics_Table_sensors")
        self._sensor_table.setHorizontalHeaderLabels(
            [
                "Label",
                "Kind",
                "Chip",
                "Value (\u00b0C)",
                "Age (ms)",
                "Freshness",
                "Confidence",
            ]
        )
        self._sensor_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._sensor_table.horizontalHeader().setStretchLastSection(True)
        self._sensor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        _sensor_header_tooltips = [
            "Sensor label reported by the kernel driver",
            "Coarse daemon classification (CpuTemp / MbTemp / GpuTemp / DiskTemp)",
            "Kernel driver / chip providing the reading (k10temp, nct6798, etc.)",
            "Current temperature in \u00b0C",
            "Time since the daemon last polled this sensor",
            "Data freshness: fresh (<2 s), stale (2-10 s), invalid (>10 s)",
            (
                "Classification confidence from the sensor knowledge base. "
                "Hover a cell for source class, description, and driver notes."
            ),
        ]
        for col, tip in enumerate(_sensor_header_tooltips):
            item = self._sensor_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        layout.addWidget(self._sensor_table)
        return container

    def _build_fans_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("Diagnostics_Splitter_fans")
        splitter.setChildrenCollapsible(False)

        # ─── Top pane: Hardware Readiness (scrollable) ────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        hw_container = QWidget()
        hw_container_layout = QVBoxLayout(hw_container)
        hw_container_layout.setSpacing(12)

        # Hardware Readiness card
        self._hw_ready_frame = QFrame()
        self._hw_ready_frame.setProperty("class", "Card")
        self._hw_ready_frame.setObjectName("Diagnostics_Frame_hwReadiness")
        hw_layout = QVBoxLayout(self._hw_ready_frame)

        hw_title = _transparent_label(
            "Hardware Readiness", "Diagnostics_Label_hwReadyTitle", bold=True
        )
        hw_title.setProperty("class", "PageSubtitle")
        hw_layout.addWidget(hw_title)

        self._hw_ready_summary = _transparent_label(
            "Fetching hardware diagnostics\u2026",
            "Diagnostics_Label_hwReadySummary",
        )
        self._hw_ready_summary.setWordWrap(True)
        hw_layout.addWidget(self._hw_ready_summary)

        # Board info
        self._board_info_label = _transparent_label("", "Diagnostics_Label_boardInfo")
        self._board_info_label.setWordWrap(True)
        self._board_info_label.setProperty("class", "CardMeta")
        self._board_info_label.setVisible(False)
        hw_layout.addWidget(self._board_info_label)

        # Vendor quirk alert
        self._vendor_quirk_label = _transparent_label("", "Diagnostics_Label_vendorQuirk")
        self._vendor_quirk_label.setWordWrap(True)
        # Explicit PlainText (DEC-106 review): chip names are interpolated
        # into the lookup key, so a future regression that ever pipes
        # daemon-supplied strings into the rendered text would otherwise
        # be auto-detected as HTML by Qt's AutoText default and could
        # render a clickable `<a href=...>` from a hostile chip name.
        self._vendor_quirk_label.setTextFormat(Qt.TextFormat.PlainText)
        self._vendor_quirk_label.setVisible(False)
        hw_layout.addWidget(self._vendor_quirk_label)

        # DEC-101: dual-chip board warning. Surfaces when the daemon's
        # `expected_chips` (derived from the it87.c DMI lookup) lists chips
        # the kernel did not enumerate. Common on Gigabyte X670/X870/Z790
        # AORUS boards where the secondary IT87952E silently fails to bind
        # without an explicit `mmio=on` modprobe.d option. Rich text so the
        # docs link is clickable.
        self._dual_chip_warning_label = _transparent_label("", "Diagnostics_Label_dualChipWarning")
        self._dual_chip_warning_label.setWordWrap(True)
        self._dual_chip_warning_label.setTextFormat(Qt.TextFormat.RichText)
        self._dual_chip_warning_label.setOpenExternalLinks(True)
        self._dual_chip_warning_label.setVisible(False)
        hw_layout.addWidget(self._dual_chip_warning_label)

        # Chip/driver table
        self._chip_table = QTableWidget(0, 5)
        self._chip_table.setObjectName("Diagnostics_Table_chips")
        self._chip_table.setHorizontalHeaderLabels(
            ["Chip", "Driver", "Status", "Mainline", "Headers"]
        )
        self._chip_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._chip_table.horizontalHeader().setStretchLastSection(True)
        self._chip_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._chip_table.setMinimumHeight(80)

        _chip_header_tooltips = [
            "Super I/O or sensor chip model detected by the daemon",
            "Linux kernel driver expected for this chip",
            "Whether the driver is loaded and where it comes from",
            "Whether the driver is included in the mainline Linux kernel",
            "Number of PWM fan headers exposed by this chip",
        ]
        for col, tip in enumerate(_chip_header_tooltips):
            item = self._chip_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        # Kernel modules table
        self._modules_table = QTableWidget(0, 3)
        self._modules_table.setObjectName("Diagnostics_Table_kernelModules")
        self._modules_table.setHorizontalHeaderLabels(["Module", "Loaded", "Mainline"])
        self._modules_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._modules_table.horizontalHeader().setStretchLastSection(True)
        self._modules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._modules_table.setMinimumHeight(80)

        _mod_header_tooltips = [
            "Kernel module name (e.g. nct6775, it87)",
            "Whether the module is currently loaded in the running kernel",
            "Whether the module ships with the mainline Linux kernel",
        ]
        for col, tip in enumerate(_mod_header_tooltips):
            item = self._modules_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        table_splitter = QSplitter(Qt.Orientation.Vertical)
        table_splitter.setObjectName("Diagnostics_Splitter_hwTables")
        table_splitter.setChildrenCollapsible(False)
        table_splitter.addWidget(self._chip_table)
        table_splitter.addWidget(self._modules_table)
        table_splitter.setStretchFactor(0, 1)
        table_splitter.setStretchFactor(1, 2)
        hw_layout.addWidget(table_splitter)

        # ACPI conflicts
        self._acpi_label = _transparent_label("", "Diagnostics_Label_acpiConflicts")
        self._acpi_label.setWordWrap(True)
        self._acpi_label.setVisible(False)
        hw_layout.addWidget(self._acpi_label)

        # Module collisions (DEC-105) — daemon-reported critical pairs
        # that race for the same Super I/O chip. Distinct from the
        # GUI-only `_module_conflict_label` below, which is a static
        # fallback table for daemons that predate the daemon-side check.
        self._module_collision_label = _transparent_label("", "Diagnostics_Label_moduleCollisions")
        self._module_collision_label.setWordWrap(True)
        self._module_collision_label.setTextFormat(Qt.TextFormat.RichText)
        self._module_collision_label.setVisible(False)
        hw_layout.addWidget(self._module_collision_label)

        # Module conflicts
        self._module_conflict_label = _transparent_label("", "Diagnostics_Label_moduleConflicts")
        self._module_conflict_label.setWordWrap(True)
        self._module_conflict_label.setVisible(False)
        hw_layout.addWidget(self._module_conflict_label)

        # BIOS interference (revert counts)
        # Headline label keeps a stable Qt class for the highest severity so
        # automated screenshots and tests can colour-check at a glance; the
        # detail label renders one row per header in matching colours.
        self._revert_headline_label = _transparent_label(
            "", "Diagnostics_Label_revertHeadline", bold=True
        )
        self._revert_headline_label.setWordWrap(True)
        self._revert_headline_label.setVisible(False)
        hw_layout.addWidget(self._revert_headline_label)

        self._revert_label = _transparent_label("", "Diagnostics_Label_revertCounts")
        self._revert_label.setWordWrap(True)
        self._revert_label.setTextFormat(Qt.TextFormat.RichText)
        self._revert_label.setVisible(False)
        hw_layout.addWidget(self._revert_label)

        self._revert_footnote_label = _transparent_label("", "Diagnostics_Label_revertFootnote")
        self._revert_footnote_label.setWordWrap(True)
        self._revert_footnote_label.setProperty("class", "CardMeta")
        self._revert_footnote_label.setVisible(False)
        hw_layout.addWidget(self._revert_footnote_label)

        # Thermal safety
        self._thermal_label = _transparent_label("", "Diagnostics_Label_thermalSafety")
        self._thermal_label.setWordWrap(True)
        self._thermal_label.setProperty("class", "CardMeta")
        hw_layout.addWidget(self._thermal_label)

        # GPU diagnostics
        self._gpu_diag_label = _transparent_label("", "Diagnostics_Label_gpuDiag")
        self._gpu_diag_label.setWordWrap(True)
        self._gpu_diag_label.setVisible(False)
        hw_layout.addWidget(self._gpu_diag_label)

        # Guidance detail (rich text with clickable driver doc links)
        self._guidance_label = _transparent_label("", "Diagnostics_Label_guidance")
        self._guidance_label.setWordWrap(True)
        self._guidance_label.setTextFormat(Qt.TextFormat.RichText)
        self._guidance_label.setOpenExternalLinks(True)
        self._guidance_label.setProperty("class", "CardMeta")
        self._guidance_label.setVisible(False)
        hw_layout.addWidget(self._guidance_label)

        # Documentation reference link
        self._docs_link_label = _transparent_label("", "Diagnostics_Label_docsLink")
        self._docs_link_label.setWordWrap(True)
        self._docs_link_label.setTextFormat(Qt.TextFormat.RichText)
        self._docs_link_label.setOpenExternalLinks(True)
        self._docs_link_label.setProperty("class", "CardMeta")
        self._docs_link_label.setVisible(False)
        hw_layout.addWidget(self._docs_link_label)

        # PWM verify section
        verify_row = QHBoxLayout()
        self._verify_combo = QComboBox()
        self._verify_combo.setObjectName("Diagnostics_Combo_verifyHeader")
        self._verify_combo.setMinimumWidth(200)
        verify_row.addWidget(self._verify_combo)

        self._verify_btn = QPushButton("Test PWM Control")
        self._verify_btn.setObjectName("Diagnostics_Btn_verifyPwm")
        self._verify_btn.setToolTip(
            "Write a test PWM value and check if the BIOS overrides it (~6s)"
        )
        self._verify_btn.clicked.connect(self._run_pwm_verify)
        verify_row.addWidget(self._verify_btn)

        # DEC-101 (2E): batch verification of every writable header.
        # Runs each header sequentially through the same _VerifyWorker so
        # we never hold the lease longer than one verify at a time. The
        # progress label updates between steps; results are summarised at
        # the end. Long-running (~6s per header) — disabled while in flight.
        self._verify_all_btn = QPushButton("Verify All Writable")
        self._verify_all_btn.setObjectName("Diagnostics_Btn_verifyAll")
        self._verify_all_btn.setToolTip(
            "Sequentially run the PWM test on every writable hwmon header "
            "(~6 s each). Useful when several headers may be misbehaving."
        )
        self._verify_all_btn.clicked.connect(self._run_pwm_verify_all)
        verify_row.addWidget(self._verify_all_btn)
        verify_row.addStretch()
        hw_layout.addLayout(verify_row)

        # Batch progress label (DEC-101 2E). Hidden until a batch run starts.
        self._verify_all_progress_label = _transparent_label(
            "", "Diagnostics_Label_verifyAllProgress"
        )
        self._verify_all_progress_label.setWordWrap(True)
        self._verify_all_progress_label.setProperty("class", "CardMeta")
        self._verify_all_progress_label.setVisible(False)
        hw_layout.addWidget(self._verify_all_progress_label)

        self._verify_result_label = _transparent_label("", "Diagnostics_Label_verifyResult")
        self._verify_result_label.setWordWrap(True)
        self._verify_result_label.setVisible(False)
        hw_layout.addWidget(self._verify_result_label)

        # Fetch button
        fetch_btn = QPushButton("Refresh Hardware Diagnostics")
        fetch_btn.setObjectName("Diagnostics_Btn_fetchHwDiag")
        fetch_btn.clicked.connect(self._fetch_hardware_diagnostics)
        hw_layout.addWidget(fetch_btn)

        hw_container_layout.addWidget(self._hw_ready_frame)
        scroll.setWidget(hw_container)
        splitter.addWidget(scroll)

        # ─── Bottom pane: Fan Status table ────────────────────────────
        fan_pane = QWidget()
        fan_pane_layout = QVBoxLayout(fan_pane)
        fan_pane_layout.setContentsMargins(0, 4, 0, 0)

        fan_label = _transparent_label("Fan Status", "Diagnostics_Label_fanTableTitle", bold=True)
        fan_label.setProperty("class", "PageSubtitle")
        fan_pane_layout.addWidget(fan_label)

        self._fan_table = QTableWidget(0, 6)
        self._fan_table.setObjectName("Diagnostics_Table_fans")
        self._fan_table.setHorizontalHeaderLabels(
            ["ID", "Source", "Control method", "RPM", "PWM (%)", "Freshness"]
        )
        self._fan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._fan_table.horizontalHeader().setStretchLastSection(True)
        self._fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        _fan_header_tooltips = [
            "Fan identifier — display name or hardware ID",
            "Hardware backend: openfan, hwmon, amd_gpu, or hwmon (PWM-only)",
            (
                "How this fan is controlled. Writable methods include hwmon PWM, "
                "PMFW curve, and OpenFan USB. 'read-only' means BIOS/EC owns the fan."
            ),
            "Hardware-measured fan speed in RPM.\n'—' means no tachometer or fan stopped.",
            "Last PWM duty cycle commanded by the daemon (0-100%).\n'—' means no command sent yet.",
            "Data freshness: ok (<2 s), stale (2-5 s), invalid (>5 s or never updated)",
        ]
        for col, tip in enumerate(_fan_header_tooltips):
            item = self._fan_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        fan_pane_layout.addWidget(self._fan_table, 1)

        splitter.addWidget(fan_pane)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        return splitter

    def _build_lease_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Explanation card
        explain_frame = QFrame()
        explain_frame.setProperty("class", "Card")
        explain_layout = QVBoxLayout(explain_frame)

        explain_title = _transparent_label(
            "What is a lease?", "Diagnostics_Label_leaseExplainTitle", bold=True
        )
        explain_title.setProperty("class", "PageSubtitle")
        explain_layout.addWidget(explain_title)

        explain_text = _transparent_label(
            "A lease grants exclusive write access to your motherboard\u2019s fan "
            "headers (hwmon). Only one client can hold the lease at a time, "
            "preventing conflicting PWM commands from different tools.\n\n"
            "The GUI automatically acquires and renews the lease while controlling "
            "fans. The lease expires after 60 seconds if not renewed (e.g. if the "
            "GUI crashes), allowing other tools to take over.\n\n"
            "If another tool holds the lease, the GUI cannot write PWM values "
            "until the lease is released or expires. OpenFan Controller writes "
            "do not require a lease \u2014 only motherboard hwmon writes do.",
            "Diagnostics_Label_leaseExplainText",
        )
        explain_text.setWordWrap(True)
        explain_text.setProperty("class", "CardMeta")
        explain_layout.addWidget(explain_text)

        layout.addWidget(explain_frame)

        # Status card
        frame = QFrame()
        frame.setProperty("class", "Card")
        frame_layout = QVBoxLayout(frame)

        self._lease_held_label = _transparent_label(
            "Lease: \u2014", "Diagnostics_Label_leaseHeld", bold=True
        )
        self._lease_held_label.setProperty("class", "PageSubtitle")
        frame_layout.addWidget(self._lease_held_label)

        self._lease_id_label = _transparent_label("Lease ID: \u2014", "Diagnostics_Label_leaseId")
        frame_layout.addWidget(self._lease_id_label)

        self._lease_owner_label = _transparent_label(
            "Owner: \u2014", "Diagnostics_Label_leaseOwner"
        )
        frame_layout.addWidget(self._lease_owner_label)

        self._lease_ttl_label = _transparent_label(
            "TTL remaining: \u2014", "Diagnostics_Label_leaseTtl"
        )
        frame_layout.addWidget(self._lease_ttl_label)

        self._lease_required_label = _transparent_label(
            "Required: \u2014", "Diagnostics_Label_leaseRequired"
        )
        frame_layout.addWidget(self._lease_required_label)

        layout.addWidget(frame)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_logs_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        intro = QLabel(
            "Live session events from the GUI. The system journal "
            "(<code>journalctl -u control-ofc-daemon</code>) is the authoritative "
            "cross-restart log; the Active Warnings dialog reflects current conditions."
        )
        intro.setProperty("class", "CardMeta")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        # Event-log controls (severity / source / search / auto-scroll / export / copy)
        # live inside the EventLogView widget; only Clear Log / Clear Warnings
        # remain at the page level so they're visible regardless of which
        # event the user has selected.
        log_action_row = QHBoxLayout()

        clear_btn = QPushButton("Clear Log")
        clear_btn.setObjectName("Diagnostics_Btn_clearLogs")
        clear_btn.setToolTip("Clear the in-process event log (does not affect snapshots below)")
        clear_btn.clicked.connect(self._clear_log)
        log_action_row.addWidget(clear_btn)

        clear_warn_btn = QPushButton("Clear Warnings")
        clear_warn_btn.setObjectName("Diagnostics_Btn_clearWarnings")
        clear_warn_btn.setToolTip("Acknowledge all current active warnings")
        clear_warn_btn.clicked.connect(self._clear_warnings)
        log_action_row.addWidget(clear_warn_btn)

        copy_errors_btn = QPushButton("Copy Last Errors")
        copy_errors_btn.setObjectName("Diagnostics_Btn_copyErrors")
        copy_errors_btn.setToolTip(
            "One-click copy of every error/warning row, regardless of the current filter"
        )
        copy_errors_btn.clicked.connect(self._copy_last_errors)
        log_action_row.addWidget(copy_errors_btn)

        log_action_row.addStretch()
        layout.addLayout(log_action_row)

        self._event_log_view = EventLogView(self._diag)
        self._event_log_view.setObjectName("Diagnostics_View_eventLog")
        layout.addWidget(self._event_log_view, 1)

        # ─── Diagnostic Snapshots ───────────────────────────────────
        # Separate sub-section beneath the event log so the on-demand
        # detail dumps (daemon, controller, GPU, journal) don't share the
        # same scroll area as the live event stream (DEC-111).
        snapshots_label = QLabel("Diagnostic Snapshots")
        snapshots_label.setProperty("class", "CardSubtitle")
        layout.addWidget(snapshots_label)

        snapshots_hint = QLabel(
            "Fetch on-demand details that aren't part of the live event stream. "
            "Output is appended to the snapshot view below; clearing the event "
            "log does not affect it."
        )
        snapshots_hint.setProperty("class", "CardMeta")
        snapshots_hint.setWordWrap(True)
        layout.addWidget(snapshots_hint)

        cat_row = QHBoxLayout()

        daemon_btn = QPushButton("Daemon Status")
        daemon_btn.setObjectName("Diagnostics_Btn_daemonStatus")
        daemon_btn.setToolTip("Fetch current daemon health and subsystem status")
        daemon_btn.clicked.connect(self._fetch_daemon_status)
        cat_row.addWidget(daemon_btn)

        controller_btn = QPushButton("Controller Status")
        controller_btn.setObjectName("Diagnostics_Btn_controllerStatus")
        controller_btn.setToolTip("Fetch OpenFan controller detection and capabilities")
        controller_btn.clicked.connect(self._fetch_controller_status)
        cat_row.addWidget(controller_btn)

        gpu_btn = QPushButton("GPU Status")
        gpu_btn.setObjectName("Diagnostics_Btn_gpuStatus")
        gpu_btn.setToolTip("Fetch AMD GPU detection, fan capabilities, and fan state")
        gpu_btn.clicked.connect(self._fetch_gpu_status)
        cat_row.addWidget(gpu_btn)

        journal_btn = QPushButton("System Journal")
        journal_btn.setObjectName("Diagnostics_Btn_systemJournal")
        journal_btn.setToolTip("Fetch recent control-ofc-daemon.service entries from journalctl")
        journal_btn.clicked.connect(self._fetch_journal)
        cat_row.addWidget(journal_btn)

        clear_snap_btn = QPushButton("Clear Snapshots")
        clear_snap_btn.setObjectName("Diagnostics_Btn_clearSnapshots")
        clear_snap_btn.setToolTip("Empty the snapshot view")
        clear_snap_btn.clicked.connect(self._clear_snapshots)
        cat_row.addWidget(clear_snap_btn)

        cat_row.addStretch()
        layout.addLayout(cat_row)

        self._snapshot_view = QPlainTextEdit()
        self._snapshot_view.setObjectName("Diagnostics_Text_snapshotView")
        self._snapshot_view.setReadOnly(True)
        self._snapshot_view.setMaximumBlockCount(2000)
        self._snapshot_view.setPlaceholderText(
            "Click a button above to fetch a snapshot — daemon health, "
            "controller status, GPU state, or recent journal entries."
        )
        font = self._snapshot_view.font()
        font.setFamily("monospace")
        self._snapshot_view.setFont(font)
        layout.addWidget(self._snapshot_view, 1)

        return container

    # ─── Signal handlers ─────────────────────────────────────────────

    def _on_capabilities(self, caps: Capabilities) -> None:
        self._daemon_version_label.setText(
            f"Daemon: v{caps.daemon_version} (API v{caps.api_version})"
        )

        of = caps.openfan
        of_status = f"Present ({of.channels} ch" if of.present else "Not present"
        if of.present:
            parts = []
            if of.write_support:
                parts.append("write")
            if of.rpm_support:
                parts.append("RPM")
            of_status += ", " + "+".join(parts) + ")" if parts else ")"
        self._openfan_label.setText(f"OpenFan: {of_status}")

        # hwmon and Features lines are reconciled against runtime writable_headers
        # when hardware diagnostics has been fetched — shared helper below.
        self._refresh_hwmon_and_features(caps)

        gpu = caps.amd_gpu
        if gpu.present:
            gpu_parts = [gpu.display_label]
            if gpu.pci_id:
                gpu_parts.append(f"PCI {gpu.pci_id}")
            gpu_parts.append(f"fan: {gpu.fan_control_method}")
            self._amd_gpu_label.setText(f"AMD GPU: {', '.join(gpu_parts)}")
        else:
            self._amd_gpu_label.setText("AMD GPU: Not detected")

    def _refresh_hwmon_and_features(self, caps: Capabilities) -> None:
        """Render the Overview hwmon + Features lines, using runtime
        ``writable_headers`` when ``HardwareDiagnosticsResult`` is available.

        Called by ``_on_capabilities`` on every capabilities update, and
        by ``_populate_hw_diagnostics`` once a fresh ``writable_headers``
        value is available so the overview line reflects runtime reality
        rather than the daemon-code-level ``write_support`` flag alone.
        """
        writable: int | None = None
        if self._diag.last_hw_diagnostics is not None:
            writable = self._diag.last_hw_diagnostics.hwmon.writable_headers

        text, warn = _hwmon_overview_text(caps.hwmon, writable)
        self._hwmon_label.setText(text)
        self._apply_warn_styling(self._hwmon_label, warn)

        self._features_label.setText(_features_line_text(caps, writable))

    @staticmethod
    def _apply_warn_styling(label: QLabel, warn: bool) -> None:
        """Toggle the WarningChip theme class on a label in place."""
        new_class = "WarningChip" if warn else ""
        label.setProperty("class", new_class)
        label.style().unpolish(label)
        label.style().polish(label)

    def _on_status(self, status: DaemonStatus) -> None:
        self._daemon_status_label.setText(f"Status: {status.overall_status}")

        if status.uptime_seconds is not None:
            self._daemon_uptime_label.setText(f"Uptime: {format_uptime(status.uptime_seconds)}")
        else:
            self._daemon_uptime_label.setText("Uptime: \u2014")

        parts = []
        for s in status.subsystems:
            age = f" (age {s.age_ms}ms)" if s.age_ms is not None else ""
            reason = f" \u2014 {s.reason}" if s.reason else ""
            parts.append(f"{s.name}: {s.status}{age}{reason}")
        self._subsystems_label.setText(
            "Subsystems:\n" + "\n".join(parts) if parts else "Subsystems: \u2014"
        )

    def _on_sensors(self, sensors: list) -> None:
        col_count = 7
        if self._sensor_table.rowCount() != len(sensors):
            self._sensor_table.setRowCount(len(sensors))

        board_vendor = ""
        if self._diag.last_hw_diagnostics is not None:
            board_vendor = self._diag.last_hw_diagnostics.board.vendor

        for i, s in enumerate(sensors):
            for col in range(col_count):
                if self._sensor_table.item(i, col) is None:
                    self._sensor_table.setItem(i, col, QTableWidgetItem())

            classification = classify_sensor(
                chip_name=s.chip_name,
                label=s.label,
                temp_type=s.temp_type,
                board_vendor=board_vendor,
            )

            self._sensor_table.item(i, 0).setText(s.label or s.id)
            self._sensor_table.item(i, 1).setText(s.kind)
            self._sensor_table.item(i, 2).setText(s.chip_name or "—")
            self._sensor_table.item(i, 3).setText(f"{s.value_c:.1f}")
            self._sensor_table.item(i, 4).setText(str(s.age_ms))

            freshness_item = self._sensor_table.item(i, 5)
            freshness_item.setText(s.freshness.value)
            theme = active_theme()
            if s.freshness == Freshness.STALE:
                freshness_item.setForeground(QColor(theme.status_warn))
            elif s.freshness == Freshness.INVALID:
                freshness_item.setForeground(QColor(theme.status_crit))
            else:
                freshness_item.setForeground(QColor(theme.text_primary))

            confidence_text = _CONFIDENCE_DISPLAY.get(
                classification.confidence, classification.confidence
            )
            self._sensor_table.item(i, 6).setText(confidence_text)

            tooltip = format_sensor_tooltip(
                classification,
                sensor_id=s.id,
                chip_name=s.chip_name,
                session_min=s.session_min_c,
                session_max=s.session_max_c,
                rate_c_per_s=s.rate_c_per_s,
            )
            for col in range(col_count):
                self._sensor_table.item(i, col).setToolTip(tooltip)

    def _on_fans(self, fans: list) -> None:
        # Build fan_ids from the ORIGINAL fans list so PWM-only calculation
        # still finds headers that have no matching fan reading at all \u2014 any
        # display-level dedup below must not change which headers are known.
        fan_ids = {f.id for f in fans}
        pwm_only = []
        if self._state:
            pwm_only = [h for h in self._state.hwmon_headers if h.id not in fan_ids]

        # Lookup map for header context \u2014 used by the FanPresence classifier
        # to distinguish "controllable, no fan detected" (writable header,
        # rpm=0) from "uncontrollable" (read-only header) (A2).
        header_by_id = {h.id: h for h in self._state.hwmon_headers} if self._state else {}

        # Deduplicate GPU/hwmon overlap for display (DEC-047), mirroring the
        # dashboard. hide_unused=False because Diagnostics must show every
        # known fan \u2014 zero-RPM or idle does not disqualify a row here.
        display_fans = filter_displayable_fans(fans, {}, hide_unused=False)

        col_count = 6
        total = len(display_fans) + len(pwm_only)
        if self._fan_table.rowCount() != total:
            self._fan_table.setRowCount(total)

        row = 0
        for f in display_fans:
            for col in range(col_count):
                if self._fan_table.item(row, col) is None:
                    self._fan_table.setItem(row, col, QTableWidgetItem())

            display_name = f.id
            if self._state:
                display_name = self._state.fan_display_name(f.id)
            self._fan_table.item(row, 0).setText(display_name)
            self._fan_table.item(row, 1).setText(f.source)

            control_method = _fan_control_method(f, self._state)
            self._fan_table.item(row, 2).setText(control_method)

            presence = classify_fan_presence(f, header_by_id.get(f.id))
            rpm_text = self._format_rpm_cell(f, presence)
            self._fan_table.item(row, 3).setText(rpm_text)
            self._fan_table.item(row, 4).setText(
                str(f.last_commanded_pwm) if f.last_commanded_pwm is not None else "\u2014"
            )

            freshness_item = self._fan_table.item(row, 5)
            freshness_item.setText(f.freshness.value)
            theme = active_theme()
            if f.freshness == Freshness.STALE:
                freshness_item.setForeground(QColor(theme.status_warn))
            elif f.freshness == Freshness.INVALID:
                freshness_item.setForeground(QColor(theme.status_crit))
            else:
                freshness_item.setForeground(QColor(theme.text_primary))

            row_tip = self._fan_row_tooltip(f, presence)
            method_tip = _CONTROL_METHOD_TOOLTIPS.get(
                control_method, _CONTROL_METHOD_TOOLTIPS["unknown"]
            )
            for col in range(col_count):
                self._fan_table.item(row, col).setToolTip(method_tip if col == 2 else row_tip)
            row += 1

        for h in pwm_only:
            for col in range(col_count):
                if self._fan_table.item(row, col) is None:
                    self._fan_table.setItem(row, col, QTableWidgetItem())

            self._fan_table.item(row, 0).setText(h.label or h.id)
            self._fan_table.item(row, 1).setText("hwmon (PWM-only)")

            control_method = _pwm_only_control_method(h)
            self._fan_table.item(row, 2).setText(control_method)

            self._fan_table.item(row, 3).setText("\u2014")
            self._fan_table.item(row, 4).setText("\u2014")
            self._fan_table.item(row, 5).setText("N/A")

            pwm_tip_parts = [
                f"ID: {h.id}",
                "Source: hwmon (PWM output only — no RPM tachometer)",
                f"Control method: {control_method}",
            ]
            if h.label:
                pwm_tip_parts.append(f"Label: {h.label}")
            if h.chip_name:
                pwm_tip_parts.append(f"Chip: {h.chip_name}")
            pwm_tip = "\n".join(pwm_tip_parts)
            method_tip = _CONTROL_METHOD_TOOLTIPS.get(
                control_method, _CONTROL_METHOD_TOOLTIPS["unknown"]
            )
            for col in range(col_count):
                self._fan_table.item(row, col).setToolTip(method_tip if col == 2 else pwm_tip)
            row += 1

    def _fan_row_tooltip(self, fan, presence: FanPresence | None = None) -> str:
        """Build a tooltip for a fan row with chip/driver and control-method context."""
        parts = [f"ID: {fan.id}", f"Source: {fan.source}"]
        parts.append(f"Control method: {_fan_control_method(fan, self._state)}")
        if presence is not None and presence not in (FanPresence.PRESENT, FanPresence.UNKNOWN):
            parts.append(f"Presence: {PRESENCE_BADGE[presence]}")
            parts.append(PRESENCE_TOOLTIP[presence])
        if fan.age_ms is not None:
            parts.append(f"Data age: {fan.age_ms} ms")
        if self._state:
            if fan.source == "hwmon":
                header = next((h for h in self._state.hwmon_headers if h.id == fan.id), None)
                if header:
                    if header.chip_name:
                        parts.append(f"Chip: {header.chip_name}")
                    g = lookup_chip_guidance(header.chip_name) if header.chip_name else None
                    if g:
                        status = "mainline" if g.in_mainline else g.driver_package
                        parts.append(f"Driver: {g.driver_name} ({status})")
                    mode = {0: "DC", 1: "PWM"}.get(
                        header.pwm_mode if header.pwm_mode is not None else -1
                    )
                    if mode:
                        parts.append(f"PWM mode: {mode}")
            elif fan.source == "amd_gpu":
                caps = self._state.capabilities
                if caps and caps.amd_gpu.present:
                    gpu = caps.amd_gpu
                    parts.append(f"GPU: {gpu.display_label}")
                    if gpu.pci_id:
                        parts.append(f"PCI: {gpu.pci_id}")
        return "\n".join(parts)

    def _format_rpm_cell(self, fan, presence: FanPresence) -> str:
        """RPM cell text — augmented with the presence badge when the fan is
        not in the default PRESENT state, so the user can distinguish
        "controllable, no fan plugged in" from "fan working" at a glance (A2).
        """
        rpm_text = str(fan.rpm) if fan.rpm is not None else "—"
        badge = PRESENCE_BADGE.get(presence, "")
        if badge:
            return f"{rpm_text} — {badge}"
        return rpm_text

    def _on_lease(self, lease: LeaseState) -> None:
        held_text = "Held" if lease.held else "Not held"
        self._lease_held_label.setText(f"Lease: {held_text}")
        self._lease_id_label.setText(f"Lease ID: {lease.lease_id or '\u2014'}")
        self._lease_owner_label.setText(f"Owner: {lease.owner_hint or '\u2014'}")
        ttl = f"{lease.ttl_seconds_remaining}s" if lease.ttl_seconds_remaining else "\u2014"
        self._lease_ttl_label.setText(f"TTL remaining: {ttl}")
        self._lease_required_label.setText(f"Required: {'Yes' if lease.lease_required else 'No'}")

    # ─── Hardware diagnostics ──────────────────────────────────────────

    def _fetch_hardware_diagnostics(self) -> None:
        """Fetch hardware diagnostics from daemon and populate the UI."""
        if not self._client:
            self._hw_ready_summary.setText("Cannot fetch: no daemon connection")
            return
        try:
            from control_ofc.api.errors import DaemonError, DaemonUnavailable

            result = self._client.hardware_diagnostics()
            self._diag.last_hw_diagnostics = result
            self._populate_hw_diagnostics(result)
            # writable_headers and board.vendor are now available — refresh the
            # Overview hwmon+Features lines (runtime reality) and re-render the
            # Sensors tab so classification picks up board-specific quirks
            # (e.g. the ASUS NCT6776F CPUTIN caveat).
            if self._state:
                # Push DMI board info into AppState so AppState.fan_display_name
                # can apply board-specific hwmon label fallbacks (A3).
                self._state.board_info = result.board
                if self._state.capabilities:
                    self._refresh_hwmon_and_features(self._state.capabilities)
                self._on_sensors(self._state.sensors)
                # Re-render fans now that resolver has board context.
                self._on_fans(self._state.fans)
            self._status_label.setText("Hardware diagnostics refreshed")
        except DaemonUnavailable:
            self._hw_ready_summary.setText("Daemon unavailable — cannot fetch diagnostics")
        except DaemonError as e:
            self._hw_ready_summary.setText(f"Diagnostics error: {e.message}")

    def _populate_hw_diagnostics(self, diag: HardwareDiagnosticsResult) -> None:
        """Populate hardware readiness UI from a diagnostics result."""
        hw = diag.hwmon

        # Board info
        board = diag.board
        if board.vendor or board.name:
            parts = []
            if board.vendor:
                parts.append(board.vendor)
            if board.name:
                parts.append(board.name)
            if board.bios_version:
                parts.append(f"BIOS {board.bios_version}")
            self._board_info_label.setText("Board: " + " — ".join(parts))
            self._board_info_label.setVisible(True)
        else:
            self._board_info_label.setVisible(False)

        # DEC-101: dual-chip board warning. Computed before chip-table render
        # so users see "missing chips" guidance above the table that will
        # otherwise look short. ``expected_chips`` is empty for boards the
        # daemon doesn't know about (and for daemons that predate DEC-101),
        # in which case the warning stays hidden.
        detected_chip_names = [c.chip_name for c in hw.chips_detected]
        dual_chip_html = dual_chip_warning_html(
            board.name,
            list(diag.expected_chips),
            detected_chip_names,
        )
        if dual_chip_html:
            self._dual_chip_warning_label.setText(dual_chip_html)
            self._dual_chip_warning_label.setProperty("class", "WarningChip")
            self._dual_chip_warning_label.style().unpolish(self._dual_chip_warning_label)
            self._dual_chip_warning_label.style().polish(self._dual_chip_warning_label)
            self._dual_chip_warning_label.setVisible(True)
        else:
            self._dual_chip_warning_label.setVisible(False)

        # Vendor quirks — pass the daemon-supplied CPU vendor and board
        # name so DEC-110 platform-scoped Intel quirks fire on real
        # hardware. Older daemons without `cpu_vendor` send empty string
        # here, which suppresses platform-scoped quirks rather than
        # firing them indiscriminately.
        all_quirks = []
        for chip in hw.chips_detected:
            all_quirks.extend(
                lookup_vendor_quirks(
                    board.vendor,
                    chip.chip_name,
                    cpu_vendor=diag.cpu_vendor,
                    board_name=board.name,
                )
            )
        if all_quirks:
            quirk_lines: list[str] = []
            for q in all_quirks:
                quirk_lines.append(f"[{q.severity.upper()}] {q.summary}")
                for d in q.details:
                    quirk_lines.append(f"  • {d}")
            self._vendor_quirk_label.setText("\n".join(quirk_lines))
            has_critical = any(q.severity == "critical" for q in all_quirks)
            css = "CriticalChip" if has_critical else "WarningChip"
            self._vendor_quirk_label.setProperty("class", css)
            self._vendor_quirk_label.style().unpolish(self._vendor_quirk_label)
            self._vendor_quirk_label.style().polish(self._vendor_quirk_label)
            self._vendor_quirk_label.setVisible(True)
        else:
            self._vendor_quirk_label.setVisible(False)

        summary_parts = []
        summary_parts.append(
            f"{hw.total_headers} PWM header(s) detected, {hw.writable_headers} writable"
        )
        if hw.total_headers > 0 and hw.writable_headers == 0:
            summary_parts.append(
                "All headers are read-only. Check BIOS fan settings or driver status."
            )
        if len(hw.chips_detected) == 0:
            summary_parts.append(
                "No hwmon chips detected. Motherboard fan control may require "
                "a kernel driver — see the modules table below."
            )
        self._hw_ready_summary.setText("\n".join(summary_parts))

        # Chip table
        self._chip_table.setRowCount(len(hw.chips_detected))
        for i, chip in enumerate(hw.chips_detected):
            for col in range(5):
                if self._chip_table.item(i, col) is None:
                    self._chip_table.setItem(i, col, QTableWidgetItem())
            self._chip_table.item(i, 0).setText(chip.chip_name)
            self._chip_table.item(i, 1).setText(chip.expected_driver)

            loaded_modules = {m.name for m in diag.kernel_modules if m.loaded}
            driver_loaded = chip.expected_driver in loaded_modules
            status_text = format_driver_status(chip.chip_name, driver_loaded)
            self._chip_table.item(i, 2).setText(status_text)

            mainline_text = "Yes" if chip.in_mainline_kernel else "No (out-of-tree)"
            self._chip_table.item(i, 3).setText(mainline_text)
            self._chip_table.item(i, 4).setText(str(chip.header_count))

        # Kernel modules table
        modules = diag.kernel_modules
        self._modules_table.setRowCount(len(modules))
        for i, mod in enumerate(modules):
            for col in range(3):
                if self._modules_table.item(i, col) is None:
                    self._modules_table.setItem(i, col, QTableWidgetItem())
            self._modules_table.item(i, 0).setText(mod.name)
            self._modules_table.item(i, 1).setText("Loaded" if mod.loaded else "Not loaded")
            self._modules_table.item(i, 2).setText("Yes" if mod.in_mainline else "No")

        # ACPI conflicts
        if diag.acpi_conflicts:
            lines = ["ACPI I/O port conflicts detected:"]
            has_it87 = False
            for c in diag.acpi_conflicts:
                lines.append(
                    f"  {c.io_range} claimed by '{c.claimed_by}' "
                    f"— conflicts with {c.conflicts_with_driver}"
                )
                if c.conflicts_with_driver == "it87":
                    has_it87 = True
            if has_it87:
                lines.append(
                    "Tip (ITE chips): prefer driver-local 'ignore_resource_conflict=1' "
                    "(add 'options it87 ignore_resource_conflict=1' to "
                    "/etc/modprobe.d/it87.conf) over the system-wide "
                    "'acpi_enforce_resources=lax' kernel parameter."
                )
            else:
                lines.append(
                    "Tip: add 'acpi_enforce_resources=lax' to kernel parameters, "
                    "or disable ACPI hardware monitoring in BIOS."
                )
            self._acpi_label.setText("\n".join(lines))
            self._acpi_label.setProperty("class", "WarningChip")
            self._acpi_label.style().unpolish(self._acpi_label)
            self._acpi_label.style().polish(self._acpi_label)
            self._acpi_label.setVisible(True)
        else:
            self._acpi_label.setVisible(False)

        # DEC-105: daemon-reported module collisions (critical pairs that
        # race for the same chip, e.g. nct6687 + nct6775 → corrupted fan
        # registers). Rendered first so users see the most severe warning
        # at the top; the GUI-only fallback CONFLICTING_MODULE_SETS check
        # below covers older daemons that don't emit module_collisions.
        # All daemon-supplied strings are HTML-escaped before interpolating
        # into this RichText label — same defensive pattern as the
        # revert-counts banner. The daemon is the user's own process
        # today, but the trust model should not assume future networked
        # transports or compromised installs cannot ship hostile strings.
        daemon_collisions = getattr(diag, "module_collisions", []) or []
        if daemon_collisions:
            from html import escape

            parts: list[str] = [
                "<b>Driver module collision detected — do not write PWM until resolved.</b><br>"
            ]
            for col in daemon_collisions:
                parts.append(
                    f"<br><b>{escape(col.module_a)}</b> + "
                    f"<b>{escape(col.module_b)}</b> "
                    f"({escape(col.severity.upper())})<br>"
                    f"{escape(col.summary)}<br>"
                    f"<i>Remediation:</i> {escape(col.remediation)}"
                )
            self._module_collision_label.setText("".join(parts))
            self._module_collision_label.setProperty("class", "CriticalChip")
            self._module_collision_label.style().unpolish(self._module_collision_label)
            self._module_collision_label.style().polish(self._module_collision_label)
            self._module_collision_label.setVisible(True)
        else:
            self._module_collision_label.setVisible(False)

        # Module conflicts (GUI-only fallback for older daemons that don't
        # emit module_collisions, plus any pairs that are not yet daemon-side).
        loaded_names = [m.name for m in diag.kernel_modules if m.loaded]
        mod_conflicts = detect_module_conflicts(loaded_names)
        # Suppress the fallback banner when the daemon already reported
        # the same pair via module_collisions — avoids two banners for
        # one underlying problem.
        if daemon_collisions:
            daemon_pairs = {tuple(sorted([c.module_a, c.module_b])) for c in daemon_collisions}
            mod_conflicts = [
                mc
                for mc in mod_conflicts
                if tuple(sorted([mc.module_a, mc.module_b])) not in daemon_pairs
            ]
        if mod_conflicts:
            lines = ["Driver module conflicts detected:"]
            for mc in mod_conflicts:
                lines.append(f"  {mc.module_a} + {mc.module_b}: {mc.explanation}")
            self._module_conflict_label.setText("\n".join(lines))
            self._module_conflict_label.setProperty("class", "CriticalChip")
            self._module_conflict_label.style().unpolish(self._module_conflict_label)
            self._module_conflict_label.style().polish(self._module_conflict_label)
            self._module_conflict_label.setVisible(True)
        else:
            self._module_conflict_label.setVisible(False)

        # BIOS interference (revert counts)
        # Tolerates pre-1.3 daemons that omit ``enable_revert_counts`` — the
        # parser already defaults to {} (api/models.py:633), and ``getattr``
        # below guards against any future shape drift on the GUI side too.
        reverts = getattr(hw, "enable_revert_counts", None) or {}
        body_html = render_reclaim_rows(reverts)
        if body_html is None:
            self._revert_headline_label.setVisible(False)
            self._revert_label.setVisible(False)
            self._revert_footnote_label.setVisible(False)
        else:
            max_count = max(reverts.values())
            top_severity = classify_reclaim_severity(max_count)
            severity_class = {
                RECLAIM_SEVERITY_HIGH: "CriticalChip",
                RECLAIM_SEVERITY_WARN: "WarningChip",
                RECLAIM_SEVERITY_OK: "SuccessChip",
            }[top_severity]

            headline = (
                "BIOS interference detected — the EC/BIOS reclaimed fan control "
                f"(highest: {max_count} reverts, {top_severity.upper()})"
            )
            self._revert_headline_label.setText(headline)
            self._revert_headline_label.setProperty("class", severity_class)
            self._revert_headline_label.style().unpolish(self._revert_headline_label)
            self._revert_headline_label.style().polish(self._revert_headline_label)
            self._revert_headline_label.setVisible(True)

            self._revert_label.setText(body_html)
            self._revert_label.setVisible(True)

            self._revert_footnote_label.setText(
                "The daemon watchdog automatically re-enables manual mode on every "
                "reclaim. Persistently HIGH counts indicate ongoing BIOS contention — "
                "see the matching vendor guidance card above for the BIOS settings to "
                "change."
            )
            self._revert_footnote_label.setVisible(True)

        # Thermal safety
        ts = diag.thermal_safety
        thermal_text = (
            f"Thermal safety: {ts.state} | CPU sensor: "
            f"{'found' if ts.cpu_sensor_found else 'NOT found'} | "
            f"Emergency: {ts.emergency_threshold_c:.0f}\u00b0C | "
            f"Release: {ts.release_threshold_c:.0f}\u00b0C"
        )
        self._thermal_label.setText(thermal_text)

        # GPU diagnostics
        if diag.gpu:
            gpu = diag.gpu
            lines = [f"GPU: {gpu.model_name or 'AMD D-GPU'} (PCI {gpu.pci_bdf})"]
            lines.append(f"  Fan control: {gpu.fan_control_method}")
            lines.append(f"  Overdrive: {'enabled' if gpu.overdrive_enabled else 'disabled'}")
            if gpu.ppfeaturemask:
                bit14 = "set" if gpu.ppfeaturemask_bit14_set else "NOT set"
                lines.append(f"  ppfeaturemask: {gpu.ppfeaturemask} (bit 14: {bit14})")
                if not gpu.ppfeaturemask_bit14_set:
                    lines.append(
                        "  Fan control requires bit 14 — add "
                        "'amdgpu.ppfeaturemask=0xffffffff' to kernel parameters"
                    )
            elif gpu.fan_control_method == "read_only":
                # No ppfeaturemask kernel param at all, and no fan write path is
                # available. The most common cause on RDNA3+ (RX 7000/9000) is
                # the missing kernel parameter; pre-RDNA3 cards normally have
                # pwm1 working and would not land here without something else
                # being wrong. Surface the param as the first thing to try.
                lines.append("  ppfeaturemask: not set on kernel command line")
                lines.append(
                    "  Tip: RDNA3+ fan control needs "
                    "'amdgpu.ppfeaturemask=0xffffffff' (see man control-ofc-daemon)"
                )
            lines.append(
                f"  Zero-RPM: {'available' if gpu.zero_rpm_available else 'not available'}"
            )
            self._gpu_diag_label.setText("\n".join(lines))
            self._gpu_diag_label.setVisible(True)
        else:
            self._gpu_diag_label.setVisible(False)

        # Guidance from chip knowledge base (HTML with clickable links)
        guidance_parts: list[str] = []
        seen_prefixes: set[str] = set()
        for chip in hw.chips_detected:
            g = lookup_chip_guidance(chip.chip_name)
            if g and g.chip_prefix not in seen_prefixes:
                seen_prefixes.add(g.chip_prefix)
                if g.bios_tips:
                    guidance_parts.append(f"<b>{chip.chip_name} — BIOS tips:</b>")
                    for tip in g.bios_tips:
                        guidance_parts.append(f"&nbsp;&nbsp;\u2022 {tip}")
                if g.known_issues:
                    guidance_parts.append(f"<b>{chip.chip_name} — Known issues:</b>")
                    for issue in g.known_issues:
                        guidance_parts.append(f"&nbsp;&nbsp;\u2022 {issue}")
                if g.driver_url:
                    guidance_parts.append(
                        f'&nbsp;&nbsp;Driver docs: <a href="{g.driver_url}">{g.driver_url}</a>'
                    )
        if guidance_parts:
            self._guidance_label.setText("<br>".join(guidance_parts))
            self._guidance_label.setVisible(True)
        else:
            self._guidance_label.setVisible(False)

        # Show docs link when any hardware chips were detected
        if hw.chips_detected:
            self._docs_link_label.setText(
                "For detailed hardware compatibility information, see the "
                '<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
                'docs/19_Hardware_Compatibility.md">Hardware Compatibility Guide</a>.'
            )
            self._docs_link_label.setVisible(True)
        else:
            self._docs_link_label.setVisible(False)

        # Populate verify header combo
        self._verify_combo.clear()
        if self._state:
            for h in self._state.hwmon_headers:
                if h.is_writable:
                    label = h.label or h.id
                    self._verify_combo.addItem(f"{label} ({h.id})", h.id)
        self._verify_btn.setEnabled(self._verify_combo.count() > 0)

    def _run_pwm_verify(self) -> None:
        """Run PWM verification test on the selected header."""
        header_id = self._verify_combo.currentData()
        if not header_id:
            self._verify_result_label.setText("No writable header selected")
            self._verify_result_label.setVisible(True)
            return
        if not self._client:
            self._verify_result_label.setText("Cannot verify: no daemon connection")
            self._verify_result_label.setVisible(True)
            return
        if not self._state or not self._state.lease.held:
            self._verify_result_label.setText(
                "Cannot verify: no hwmon lease held. Start fan control or acquire a lease first."
            )
            self._verify_result_label.setVisible(True)
            return

        lease_id = self._state.lease.lease_id
        if not lease_id:
            self._verify_result_label.setText("Cannot verify: lease ID unavailable")
            self._verify_result_label.setVisible(True)
            return

        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("Testing...")
        self._verify_result_label.setVisible(False)

        if not self._ensure_verify_worker():
            # No socket path available (defensive — _client is not None at this
            # point, but the helper may fail to extract it in exotic configs).
            self._verify_btn.setEnabled(True)
            self._verify_btn.setText("Test PWM Control")
            self._verify_result_label.setText("Verify unavailable: no socket path")
            self._verify_result_label.setVisible(True)
            return

        # Pause the GUI control loop's writes to this header so the daemon's
        # 6-second verify wait does not get stomped on by our own 1Hz tick (A1).
        # The control loop has its own 9-second safety auto-resume, so a hung
        # verify cannot pin the header indefinitely even if we never emit
        # verify_completed.
        self._verify_active_header = header_id
        self.verify_started.emit(header_id)

        # Fire queued signal to worker running on its own thread.
        self._verify_request.emit(header_id, lease_id)

    def _ensure_verify_worker(self) -> bool:
        """Create the verify worker + thread on first use. Returns False if no
        socket path is available to construct the worker."""
        if self._verify_worker is not None:
            return True
        socket_path = getattr(self._client, "_socket_path", None)
        if not socket_path:
            return False

        self._verify_thread = QThread(self)
        self._verify_worker = _VerifyWorker(socket_path)
        self._verify_worker.moveToThread(self._verify_thread)

        # Main thread → worker thread via queued connection.
        self._verify_request.connect(
            self._verify_worker.do_verify, Qt.ConnectionType.QueuedConnection
        )
        # Worker thread → main thread via queued connections.
        self._verify_worker.verify_ok.connect(
            self._on_verify_ok, Qt.ConnectionType.QueuedConnection
        )
        self._verify_worker.verify_error.connect(
            self._on_verify_error, Qt.ConnectionType.QueuedConnection
        )

        self._verify_thread.start()
        return True

    @Slot(object)
    def _on_verify_ok(self, result: HwmonVerifyResult) -> None:
        self._show_verify_result(result)
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Test PWM Control")
        self._emit_verify_completed()
        # DEC-101 (2E): if a batch run is active, record this result and
        # advance the queue. Outside a batch run this is a no-op.
        if self._verify_all_total > 0:
            self._verify_all_results.append((result.header_id, result.result))
            self._step_pwm_verify_all()

    @Slot(str, str)
    def _on_verify_error(self, category: str, message: str) -> None:
        if category == "unavailable":
            self._verify_result_label.setText(message or "Daemon unavailable during verify")
        else:
            self._verify_result_label.setText(f"Verify error: {message}")
        self._verify_result_label.setVisible(True)
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Test PWM Control")
        self._emit_verify_completed()
        # DEC-101 (2E): in a batch run, record the failure and advance
        # rather than aborting the whole run. The user still gets the
        # remaining headers tested; the summary highlights the error.
        if self._verify_all_total > 0:
            header_id = self._verify_all_active_header_id() or "unknown"
            self._verify_all_results.append((header_id, f"error:{category}"))
            self._step_pwm_verify_all()

    # ── DEC-101 (2E): batch verify all writable headers ──────────────

    def _run_pwm_verify_all(self) -> None:
        """Sequentially verify every writable hwmon header."""
        if not self._state:
            self._verify_all_progress_label.setText("Cannot verify: no app state")
            self._verify_all_progress_label.setVisible(True)
            return
        if not self._client:
            self._verify_all_progress_label.setText("Cannot verify: no daemon connection")
            self._verify_all_progress_label.setVisible(True)
            return
        if not self._state.lease.held or not self._state.lease.lease_id:
            self._verify_all_progress_label.setText(
                "Cannot verify: no hwmon lease held. Start fan control or acquire a lease first."
            )
            self._verify_all_progress_label.setVisible(True)
            return
        if self._verify_all_total > 0:
            # Already running — guard against double-clicks.
            return

        writable = [h.id for h in self._state.hwmon_headers if h.is_writable]
        if not writable:
            self._verify_all_progress_label.setText("No writable headers to test.")
            self._verify_all_progress_label.setVisible(True)
            return

        if not self._ensure_verify_worker():
            self._verify_all_progress_label.setText("Verify unavailable: no socket path")
            self._verify_all_progress_label.setVisible(True)
            return

        self._verify_all_queue = list(writable)
        self._verify_all_results = []
        self._verify_all_total = len(writable)

        # Lock both buttons during the run; the per-header progress label
        # tells the user what's happening, the per-step verify result
        # label still fills in as each step completes.
        self._verify_btn.setEnabled(False)
        self._verify_all_btn.setEnabled(False)
        self._verify_all_btn.setText("Testing...")
        self._verify_all_progress_label.setProperty("class", "CardMeta")
        self._verify_all_progress_label.style().unpolish(self._verify_all_progress_label)
        self._verify_all_progress_label.style().polish(self._verify_all_progress_label)
        self._verify_all_progress_label.setVisible(True)

        self._step_pwm_verify_all()

    def _verify_all_active_header_id(self) -> str | None:
        """Header currently under test in the batch run, if any."""
        return self._verify_active_header

    def _step_pwm_verify_all(self) -> None:
        """Advance the batch-verify state machine by one header.

        If the queue is empty, finalises and shows a summary. If the
        lease has been lost mid-run (no lease_id available), aborts the
        rest of the queue with a clear message.
        """
        # End-of-batch: render summary, reset state.
        if not self._verify_all_queue:
            self._show_verify_all_summary()
            self._verify_all_total = 0
            self._verify_btn.setEnabled(self._verify_combo.count() > 0)
            self._verify_all_btn.setEnabled(True)
            self._verify_all_btn.setText("Verify All Writable")
            return

        # Lease check before each step — if the lease evaporated mid-run
        # we cannot send another verify request, so abort cleanly with
        # whatever results we have so far.
        if not self._state or not self._state.lease.held:
            remaining = list(self._verify_all_queue)
            self._verify_all_queue.clear()
            for h in remaining:
                self._verify_all_results.append((h, "error:lease_lost"))
            self._show_verify_all_summary(aborted=True)
            self._verify_all_total = 0
            self._verify_btn.setEnabled(self._verify_combo.count() > 0)
            self._verify_all_btn.setEnabled(True)
            self._verify_all_btn.setText("Verify All Writable")
            return

        lease_id = self._state.lease.lease_id or ""
        header_id = self._verify_all_queue.pop(0)
        completed = self._verify_all_total - len(self._verify_all_queue) - 1
        next_index = completed + 1
        self._verify_all_progress_label.setText(
            f"Testing {next_index}/{self._verify_all_total}: {header_id}"
        )
        self._verify_active_header = header_id
        self.verify_started.emit(header_id)
        self._verify_request.emit(header_id, lease_id)

    def _show_verify_all_summary(self, *, aborted: bool = False) -> None:
        """Render the multi-header summary into the progress label."""
        if not self._verify_all_results:
            self._verify_all_progress_label.setText("Verify all: no results.")
            return

        # Severity heuristic for the badge: any error/critical → CriticalChip,
        # any warning-level → WarningChip, else SuccessChip.
        critical_keys = {"pwm_enable_reverted"}
        warning_keys = {"pwm_value_clamped", "no_rpm_effect"}
        has_critical = any(
            r.startswith("error:") or r in critical_keys for _, r in self._verify_all_results
        )
        has_warning = any(r in warning_keys for _, r in self._verify_all_results)
        css_class = (
            "CriticalChip" if has_critical else "WarningChip" if has_warning else "SuccessChip"
        )

        intro = (
            "Verify all (aborted — lease lost):"
            if aborted
            else f"Verify all complete ({len(self._verify_all_results)}/"
            f"{self._verify_all_total} tested):"
        )
        lines = [intro]
        for header_id, result_str in self._verify_all_results:
            short = {
                "effective": "OK",
                "pwm_enable_reverted": "BIOS reclaimed",
                "pwm_value_clamped": "clamped",
                "no_rpm_effect": "no RPM change",
                "rpm_unavailable": "no tach",
            }.get(result_str, result_str)
            lines.append(f"  • {header_id}: {short}")

        self._verify_all_progress_label.setText("\n".join(lines))
        self._verify_all_progress_label.setProperty("class", css_class)
        self._verify_all_progress_label.style().unpolish(self._verify_all_progress_label)
        self._verify_all_progress_label.style().polish(self._verify_all_progress_label)

    def _emit_verify_completed(self) -> None:
        """Resume the control loop's writes for the header that was under
        verify (A1). Both ok and error paths must call this, so it lives in a
        single helper."""
        header = self._verify_active_header
        if header:
            self.verify_completed.emit(header)
        self._verify_active_header = None

    def set_theme(self, _tokens) -> None:
        """Force a re-render of theme-coloured cells after a theme change.

        Sensor and fan tables are repainted from the latest cached readings
        so the freshness column foreground colours pick up the new
        ``status_warn`` / ``status_crit`` / ``text_primary`` values from
        :func:`active_theme` (DEC-109). The reclaim-count card depends on
        ``reclaim_severity_color`` and is refreshed by re-populating from
        the cached hardware diagnostics result if one has been fetched.

        DEC-111: also repaint the event-log severity column so a theme
        switch updates the per-row Info/Warning/Error colours.
        """
        if self._state is not None:
            if self._state.sensors:
                self._on_sensors(self._state.sensors)
            if self._state.fans:
                self._on_fans(self._state.fans)
        cached_hw = getattr(self._diag, "last_hw_diagnostics", None)
        if cached_hw is not None:
            self._populate_hw_diagnostics(cached_hw)
        if getattr(self, "_event_log_view", None) is not None:
            self._event_log_view.refresh_theme()

    def cleanup(self) -> None:
        """Stop the verify worker thread. Called from main window closeEvent."""
        if self._verify_worker is not None:
            self._verify_worker.shutdown()
        if self._verify_thread is not None:
            self._verify_thread.quit()
            if not self._verify_thread.wait(2000):
                log.warning("Verify thread did not stop within 2s, terminating")
                self._verify_thread.terminate()
                self._verify_thread.wait(1000)
            self._verify_thread = None
            self._verify_worker = None

    def _show_verify_result(self, result: HwmonVerifyResult) -> None:
        """Display the result of a PWM verification test.

        Keys match the daemon's classify_verify_result return strings exactly
        (see daemon/src/api/handlers/hwmon_ctl.rs).
        """
        status_map = {
            "effective": (
                "PWM control is working correctly",
                "SuccessChip",
            ),
            "pwm_enable_reverted": (
                "BIOS/EC reverted pwm_enable — fan control is being overridden",
                "CriticalChip",
            ),
            "pwm_value_clamped": (
                "PWM value was clamped or ignored by hardware",
                "WarningChip",
            ),
            "no_rpm_effect": (
                "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
                "WarningChip",
            ),
            "rpm_unavailable": (
                "PWM write accepted but RPM readback unavailable to confirm",
                "CardMeta",
            ),
        }
        summary, css_class = status_map.get(
            result.result, (f"Unknown result: {result.result}", "CardMeta")
        )

        lines = [f"Result: {summary}"]
        if result.details:
            lines.append(result.details)
        lines.append(f"Test: wrote {result.test_pwm_percent}% PWM, waited {result.wait_seconds}s")
        init = result.initial_state
        final = result.final_state
        if init.rpm is not None and final.rpm is not None:
            lines.append(f"RPM: {init.rpm} → {final.rpm}")
        if init.pwm_enable is not None and final.pwm_enable is not None:
            lines.append(f"pwm_enable: {init.pwm_enable} → {final.pwm_enable}")
        if result.restore_failed:
            lines.append(
                f"Note: restoring the original PWM after the test failed — "
                f"the header is left at the {result.test_pwm_percent}% test "
                f"value. Re-set the desired PWM from the Controls page."
            )

        # Post-verification guidance based on result + board/chip context
        board_vendor = ""
        chip_name = ""
        expected_chips: list[str] = []
        detected_chip_names: list[str] = []
        if self._state:
            header = next(
                (h for h in self._state.hwmon_headers if h.id == result.header_id),
                None,
            )
            if header:
                chip_name = header.chip_name
            if self._diag.last_hw_diagnostics:
                board_vendor = self._diag.last_hw_diagnostics.board.vendor
                expected_chips = list(self._diag.last_hw_diagnostics.expected_chips)
                detected_chip_names = [
                    c.chip_name for c in self._diag.last_hw_diagnostics.hwmon.chips_detected
                ]

        guidance = verification_guidance(result.result, board_vendor, chip_name)
        if guidance:
            lines.append("")
            lines.append(f"Next step: {guidance}")

        # DEC-101 (2F): when a clamped/no-rpm result coincides with a known
        # dual-chip board missing one of its chips, append a pointer to the
        # Fans-tab dual-chip notice. The hint is None on boards that don't
        # match the criteria so the existing wording is unaffected.
        dual_hint = dual_chip_verify_hint(result.result, expected_chips, detected_chip_names)
        if dual_hint:
            lines.append("")
            lines.append(dual_hint)

        self._verify_result_label.setText("\n".join(lines))
        self._verify_result_label.setProperty("class", css_class)
        self._verify_result_label.style().unpolish(self._verify_result_label)
        self._verify_result_label.style().polish(self._verify_result_label)
        self._verify_result_label.setVisible(True)

    # ─── Actions ─────────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        """Re-populate all tabs from current state."""
        if not self._state:
            return
        if self._state.capabilities:
            self._on_capabilities(self._state.capabilities)
        if self._state.daemon_status:
            self._on_status(self._state.daemon_status)
        self._on_sensors(self._state.sensors)
        self._on_fans(self._state.fans)
        self._on_lease(self._state.lease)
        # Event log self-updates from diag signals; no refresh action needed.
        self._status_label.setText("Refreshed")

    def _clear_log(self) -> None:
        # DiagnosticsService.events_cleared drives EventLogView to flush its
        # rows, so the table goes empty without the page touching the widget.
        self._diag.clear_events()
        self._status_label.setText("Event log cleared")

    def _clear_warnings(self) -> None:
        if self._state:
            self._state.clear_warnings()

    def _clear_snapshots(self) -> None:
        """Empty the snapshot view (separate from the event log)."""
        self._snapshot_view.clear()
        self._status_label.setText("Snapshots cleared")

    def _copy_last_errors(self) -> None:
        """One-click copy of every error/warning event regardless of filter.

        The EventLogView's own "Copy view" button copies the *filtered* set;
        this is the explicit "give me everything that's currently noisy"
        shortcut so users don't have to remember to flip filters before
        copying.
        """
        from PySide6.QtWidgets import QApplication

        error_levels = {"error", "warning"}
        lines = []
        for e in self._diag.events:
            if e.level in error_levels:
                lines.append(f"[{e.time_str}] [{e.level.upper():7s}] [{e.source}] {e.message}")

        if lines:
            text = "\n".join(lines)
            QApplication.clipboard().setText(text)
            self._status_label.setText(f"Copied {len(lines)} error(s) to clipboard")
        else:
            self._status_label.setText("No recent errors to copy")

    def _fetch_daemon_status(self) -> None:
        """Fetch and display a daemon status snapshot in the snapshot view."""
        text = self._diag.format_daemon_status()
        self._append_snapshot_block("DAEMON STATUS", text)

    def _fetch_controller_status(self) -> None:
        """Fetch and display controller detection info in the snapshot view."""
        text = self._diag.format_controller_status()
        self._append_snapshot_block("CONTROLLER STATUS", text)

    def _fetch_gpu_status(self) -> None:
        """Fetch and display GPU detection and fan state in the snapshot view."""
        text = self._diag.format_gpu_status()
        self._append_snapshot_block("GPU STATUS", text)

    def _fetch_journal(self) -> None:
        """Fetch and display recent journal entries in the snapshot view."""
        text = self._diag.fetch_journal_entries()
        self._append_snapshot_block("SYSTEM JOURNAL", text)

    def _append_snapshot_block(self, source: str, text: str) -> None:
        """Append a labeled detail block to the snapshot view."""
        import time

        timestamp = time.strftime("%H:%M:%S")
        separator = "\u2500" * 60
        block = f"\n{separator}\n[{timestamp}] [{source}]\n{separator}\n{text}\n"
        self._snapshot_view.appendPlainText(block)
        scrollbar = self._snapshot_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _export_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Support Bundle",
            "control_ofc_support_bundle.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            self._diag.export_support_bundle(Path(path))
            self._status_label.setText(f"Bundle exported to {Path(path).name}")
        except PermissionError:
            self._status_label.setText("Export failed: permission denied")
        except Exception as e:
            msg = str(e)[:80] if len(str(e)) > 80 else str(e)
            self._status_label.setText(f"Export failed: {msg}")
