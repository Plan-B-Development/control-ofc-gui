"""Diagnostics page — daemon health, sensor/fan status, lease, logs, support export."""

from __future__ import annotations

import contextlib
import logging
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from PySide6.QtGui import QColor

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService
from PySide6.QtCore import QObject, QPoint, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
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
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService, format_uptime
from control_ofc.services.series_selection import SeriesSelectionModel
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
    lookup_chip_guidance,
    lookup_vendor_quirks,
    verification_guidance,
)
from control_ofc.ui.sensor_knowledge import (
    classify_sensor,
    format_sensor_tooltip,
    temp_type_label,
)
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.event_log_view import EventLogView
from control_ofc.ui.widgets.readiness_report import (
    ReadinessReportDialog,
    board_identity_line,
    build_fix_guidance_html,
    build_readiness_report_html,
    chip_rows,
    header_summary_line,
    module_rows,
    readiness_verdict,
    thermal_line,
)
from control_ofc.ui.widgets.sensor_detail_dialog import SensorDetailDialog

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

# Display strings for the Source class column (DEC-117). Mirrors
# ``SensorClassification.source_class`` values produced by
# ``sensor_knowledge.classify_sensor``; unknown values pass through verbatim
# so a forward-compatible new class name doesn't render as blank.
_SOURCE_CLASS_DISPLAY: dict[str, str] = {
    "cpu_die": "CPU die",
    "cpu_control": "CPU control",
    "cpu_ccd": "CPU CCD",
    "cpu_peci": "CPU (PECI)",
    "cpu_board_side": "CPU (board-side)",
    "cpu_internal": "CPU internal",
    "cpu_package": "CPU package",
    "amd_tsi": "AMD TSI",
    "gpu_edge": "GPU edge",
    "gpu_junction": "GPU junction",
    "gpu_memory": "GPU memory",
    "gpu_other": "GPU",
    "vrm": "VRM",
    "chipset": "Chipset",
    "external_probe": "External probe",
    "coolant_in": "Coolant in",
    "coolant_out": "Coolant out",
    "coolant": "Coolant",
    "board_ambient": "Board ambient",
    "board_system": "Board (SYSTIN)",
    "board_auxiliary": "Board (aux)",
    "board_thermistor": "Board thermistor",
    "thermal_diode": "Thermal diode",
    "memory_dimm": "DIMM",
    "smbus_device": "SMBus",
    "virtual": "Virtual",
    "chip_local": "Chip local",
    "disk_composite": "Disk",
    "super_io_channel": "Super-I/O ch.",
    "vendor_wmi_unlabeled": "Vendor WMI",
    "vendor_labeled": "Vendor",
    "bogus": "Bogus",
    "unknown": "Unknown",
}


class _SensorColumn(NamedTuple):
    """Definition of one column in the Diagnostics > Sensors table (DEC-117).

    Bundles header text + header tooltip so the two never drift; tests rely
    on looking up column indices by header text rather than hard-coding ints.
    """

    header: str
    tooltip: str


# DEC-117: the 14-column table for Diagnostics > Sensors. The "Details"
# column at the end hosts a per-row button widget.
_SENSOR_COLUMNS: list[_SensorColumn] = [
    _SensorColumn("Label", "Sensor label reported by the kernel driver"),
    _SensorColumn(
        "Sensor ID",
        "Stable identifier (hwmon:<chip>:<dev_id>:<label>) — used by profiles",
    ),
    _SensorColumn(
        "Source class",
        "Source classification from the sensor knowledge base (cpu_die, vrm, board_thermistor, …)",
    ),
    _SensorColumn(
        "Kind",
        "Coarse daemon classification (CpuTemp / MbTemp / GpuTemp / DiskTemp)",
    ),
    _SensorColumn(
        "Source",
        "Daemon source subsystem (hwmon, amd_gpu)",
    ),
    _SensorColumn(
        "Chip",
        "Kernel driver / chip providing the reading (k10temp, nct6798, etc.)",
    ),
    _SensorColumn(
        "Driver type",
        "Sysfs tempN_type value (diode, thermistor, AMD TSI, Intel PECI)",
    ),
    _SensorColumn("Value (°C)", "Current temperature in °C"),
    _SensorColumn(
        "Trend",
        "Smoothed temperature change rate (suppressed below ±0.1 °C/s)",
    ),
    _SensorColumn(
        "Session min/max",
        "Lowest and highest values observed since the daemon started",
    ),
    _SensorColumn("Age (ms)", "Time since the daemon last polled this sensor"),
    _SensorColumn(
        "Freshness",
        "Data freshness: fresh (<2 s), stale (2-10 s), invalid (>10 s)",
    ),
    _SensorColumn(
        "Confidence",
        "Classification confidence from the sensor knowledge base. "
        "Hover a cell for source class, description, and driver notes.",
    ),
    _SensorColumn("Details", "Open the per-sensor detail dialog"),
]
_SENSOR_COL_INDEX: dict[str, int] = {c.header: i for i, c in enumerate(_SENSOR_COLUMNS)}


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


class _HwDiagWorker(QObject):
    """Runs in a QThread — executes the blocking GET /diagnostics/hardware call
    off the UI thread. The daemon performs several sysfs/procfs reads to build
    the report, so a synchronous fetch on a slow/contended daemon would freeze
    the GUI — notably the once-per-session auto-fetch when the Fans tab is first
    shown."""

    fetch_ok = Signal(object)  # HardwareDiagnosticsResult
    fetch_error = Signal(str, str)  # category ('unavailable'|'error'), message

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot()
    def do_fetch(self) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().hardware_diagnostics()
            self.fetch_ok.emit(result)
        except DaemonTimeout:
            self.fetch_error.emit("unavailable", "Diagnostics fetch timed out")
        except DaemonUnavailable:
            self.fetch_error.emit("unavailable", "Daemon unavailable — cannot fetch diagnostics")
        except DaemonError as e:
            self.fetch_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("HW diagnostics worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.fetch_error.emit("unavailable", "Connection lost during diagnostics fetch")

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

    # Main-thread signal that kicks the hardware-diagnostics worker (its own
    # QThread) so the blocking GET /diagnostics/hardware never freezes the UI.
    _hw_diag_request = Signal()

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
        series_selection: SeriesSelectionModel | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._client = client
        self._settings_service = settings_service
        # DEC-117: Diagnostics > Sensors "Mirror hidden to dashboard" button
        # pushes the local diagnostics_hidden_sensor_ids list into the shared
        # SeriesSelectionModel as a one-shot. None when the page is built
        # outside main_window (e.g. unit tests) — the button is then hidden.
        self._series_selection = series_selection
        self._diag = diagnostics_service or DiagnosticsService(
            state, settings_service=settings_service, profile_service=profile_service
        )

        # DEC-117: cached sensor list + UI state for the Sensors tab.
        # ``_all_sensors`` is the last payload from ``_on_sensors``; the
        # table is fully re-rendered from this on every refresh so visibility
        # toggles never require a fresh daemon poll.
        self._all_sensors: list[SensorReading] = []
        self._hidden_group_expanded = False
        self._sensor_detail_dialog: SensorDetailDialog | None = None

        # Lazy-created verify worker + thread (see _ensure_verify_worker).
        self._verify_thread: QThread | None = None
        self._verify_worker: _VerifyWorker | None = None
        # Lazy-created hardware-diagnostics worker + thread (see
        # _ensure_hw_diag_worker) — keeps the blocking GET off the UI thread.
        self._hw_diag_thread: QThread | None = None
        self._hw_diag_worker: _HwDiagWorker | None = None
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

        # DEC-113: hardware-readiness auto-fetch + pop-out report state.
        self._hw_diag_auto_fetched = False
        self._report_dialog: ReadinessReportDialog | None = None

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
        # DEC-113: auto-fetch hardware diagnostics the first time the Fans tab
        # (index 2) is shown, so the readiness verdict populates without a click.
        self._fans_tab_index = 2
        self._tabs.currentChanged.connect(self._on_diag_tab_changed)

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
        """Build the expanded Diagnostics > Sensors tab (DEC-117).

        Layout:

        ``Summary line (counts) \u2502 [Mirror hidden to dashboard]``
        ``------------------------------------------------------------``
        ``13-column data table + 14th Details-button column``
        ``  (visible sensors, then a single toggle row, then optionally``
        ``  hidden sensors when the group is expanded)``

        The Details column hosts a per-row :class:`QPushButton`; double-click
        and right-click context menu are also wired so power users have a
        keyboard-free path.
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Header row: counts summary + "Mirror hidden to dashboard" button.
        header_row = QHBoxLayout()
        self._sensor_summary_label = _transparent_label(
            "Sensors: \u2014", "Diagnostics_Label_sensorSummary"
        )
        self._sensor_summary_label.setWordWrap(True)
        header_row.addWidget(self._sensor_summary_label, 1)

        self._mirror_hidden_btn = QPushButton("Mirror hidden to dashboard")
        self._mirror_hidden_btn.setObjectName("Diagnostics_Btn_mirrorHidden")
        self._mirror_hidden_btn.setToolTip(
            "Push the current Diagnostics hide-list into the dashboard chart's "
            "visibility model (one-shot \u2014 future changes here stay local)."
        )
        self._mirror_hidden_btn.clicked.connect(self._mirror_hidden_to_dashboard)
        # Hidden when no SeriesSelectionModel was provided (e.g. unit tests).
        self._mirror_hidden_btn.setVisible(self._series_selection is not None)
        header_row.addWidget(self._mirror_hidden_btn)
        layout.addLayout(header_row)

        # 14-column data table (13 data columns + Details button column).
        self._sensor_table = QTableWidget(0, len(_SENSOR_COLUMNS))
        self._sensor_table.setObjectName("Diagnostics_Table_sensors")
        self._sensor_table.setHorizontalHeaderLabels([c.header for c in _SENSOR_COLUMNS])
        self._sensor_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        # Stretch-last-section matches every other diagnostics table (chips,
        # modules, fans). The Details button column sits in that last slot
        # and grows with the table \u2014 costs nothing visually and keeps the
        # existing "all diagnostics tables stretch-last" assertion stable.
        self._sensor_table.horizontalHeader().setStretchLastSection(True)
        self._sensor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sensor_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sensor_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._sensor_table.customContextMenuRequested.connect(self._on_sensor_context_menu)
        self._sensor_table.cellDoubleClicked.connect(self._on_sensor_cell_double_clicked)

        self._apply_header_tooltips(
            self._sensor_table,
            [c.tooltip for c in _SENSOR_COLUMNS],
        )

        layout.addWidget(self._sensor_table)
        return container

    def _build_fans_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("Diagnostics_Splitter_fans")
        splitter.setChildrenCollapsible(False)

        # ─── Top pane: Hardware Readiness card (scrollable) ───────────
        # DEC-115/DEC-116: the whole card is a single collapsible section. Its
        # persistent area (verdict + the critical/blocking-alert stack) stays
        # visible even when the card is collapsed; the body — informational
        # alerts, summary, board identity, and the five detail sub-sections —
        # folds away so the live fan table can rise. Safety warnings are never
        # hidden: blocking alerts are persistent and the card force-expands on
        # a problem (_populate_hw_diagnostics).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        hw_container = QWidget()
        hw_container_layout = QVBoxLayout(hw_container)
        hw_container_layout.setSpacing(12)
        hw_container_layout.addWidget(self._build_readiness_card())

        # Refresh button — always visible below the card so it works whether
        # or not the card is collapsed.
        fetch_btn = QPushButton("Refresh Hardware Diagnostics")
        fetch_btn.setObjectName("Diagnostics_Btn_fetchHwDiag")
        fetch_btn.clicked.connect(self._fetch_hardware_diagnostics)
        hw_container_layout.addWidget(fetch_btn)

        # Pin to the top so a collapsed card doesn't leave the pane stretched
        # with empty space inside the resizable scroll area.
        hw_container_layout.addStretch()
        scroll.setWidget(hw_container)
        splitter.addWidget(scroll)

        # ─── Bottom pane: live Fan Status table ───────────────────────
        splitter.addWidget(self._build_fan_status_pane())

        # Give the readiness pane enough initial height to show the verdict,
        # any critical alerts, and the section headers without scrolling on a
        # typical board, while leaving the fan table a usable share. The
        # splitter stays user-adjustable and the top pane scrolls when the
        # card is expanded.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([370, 390])
        return splitter

    def _build_readiness_card(self) -> QFrame:
        """Build the Hardware Readiness card as a single collapsible section.

        DEC-115/DEC-116: the section's *persistent* area holds the
        always-visible verdict banner + the critical/blocking-alert stack; its
        collapsible *body* holds the informational alerts, readiness summary,
        board identity, and the five detail sub-sections. Collapsing folds the
        body away (clearing the informational alerts) while the verdict and any
        blocking alerts stay on screen.
        """
        self._hw_ready_frame = QFrame()
        self._hw_ready_frame.setProperty("class", "Card")
        self._hw_ready_frame.setObjectName("Diagnostics_Frame_hwReadiness")
        card_layout = QVBoxLayout(self._hw_ready_frame)

        self._hw_ready_section = CollapsibleSection(
            "Hardware Readiness", "Diagnostics_Section_hwReadiness", expanded=True
        )
        card_layout.addWidget(self._hw_ready_section)

        # ── Persistent: verdict banner + full-report pop-out (DEC-113) ──
        # A prominent one-line verdict fills the collapsed view with a useful
        # at-a-glance answer; the button opens the full report in its own
        # window. Both stay visible when the card is collapsed.
        verdict_row = QHBoxLayout()
        self._readiness_verdict_label = _transparent_label(
            "Checking hardware readiness…", "Diagnostics_Label_readinessVerdict", bold=True
        )
        self._readiness_verdict_label.setWordWrap(True)
        verdict_row.addWidget(self._readiness_verdict_label, 1)

        self._open_report_btn = QPushButton("Open Full Report ↗")
        self._open_report_btn.setObjectName("Diagnostics_Btn_openReport")
        self._open_report_btn.setToolTip(
            "Open the complete hardware-readiness report in its own window "
            "(all detail, clickable links)."
        )
        self._open_report_btn.clicked.connect(self._open_readiness_report)
        self._open_report_btn.setEnabled(False)
        verdict_row.addWidget(self._open_report_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._hw_ready_section.add_persistent_layout(verdict_row)

        # ── Alerts: critical persistent, informational demoted (DEC-116) ──
        # Per NN/g progressive-disclosure guidance, essential warnings stay
        # OUTSIDE the collapsible body so a folded card still surfaces them:
        # blocking alerts (module collisions/conflicts, active BIOS-revert)
        # are persistent. Informational alerts (dual-chip setup, vendor quirks,
        # ACPI) live in the body so collapsing actually clears them — they stay
        # visible by default because a problem board force-expands the card.
        # Both stacks collapse to nothing on a healthy system.
        persistent_alerts, demoted_alerts = self._create_alert_labels()
        for alert in persistent_alerts:
            self._hw_ready_section.add_persistent_widget(alert)

        # ── Collapsible body: demoted alerts, then summary + board ──────
        for alert in demoted_alerts:
            self._hw_ready_section.add_widget(alert)

        self._hw_ready_summary = _transparent_label(
            "Fetching hardware diagnostics…", "Diagnostics_Label_hwReadySummary"
        )
        self._hw_ready_summary.setWordWrap(True)
        self._hw_ready_section.add_widget(self._hw_ready_summary)

        self._board_info_label = _transparent_label("", "Diagnostics_Label_boardInfo")
        self._board_info_label.setWordWrap(True)
        self._board_info_label.setProperty("class", "CardMeta")
        self._board_info_label.setVisible(False)
        self._hw_ready_section.add_widget(self._board_info_label)

        # ── Collapsible body: the five detail sub-sections ──────────────
        self._hw_ready_section.add_widget(self._build_detected_hw_section())
        self._hw_ready_section.add_widget(self._build_bios_section())
        self._hw_ready_section.add_widget(self._build_thermal_gpu_section())
        self._hw_ready_section.add_widget(self._build_guidance_section())
        self._hw_ready_section.add_widget(self._build_pwm_test_section())

        return self._hw_ready_frame

    def _create_alert_labels(self) -> tuple[list[QWidget], list[QWidget]]:
        """Create the readiness alert labels (each setVisible-gated).

        Returns ``(persistent, demoted)`` (DEC-116):

        - ``persistent`` — safety-critical, blocking alerts that stay visible
          even when the card is collapsed: driver-module collisions/conflicts
          (which mean "do not write PWM until resolved") and the active
          BIOS/EC interference headline. A user who folds the card away must
          still see these.
        - ``demoted`` — informational alerts that live in the collapsible body
          so collapsing the card actually clears them: dual-chip setup
          guidance, vendor quirks (FYI notes), and ACPI conflicts. They stay
          visible by default because a problem board force-expands the card
          (`_populate_hw_diagnostics`); only an explicit user collapse hides
          them, and the persistent verdict banner still flags the problem.
        """
        # Module collisions (DEC-105) — daemon-reported critical pairs that
        # race for the same Super I/O chip. Distinct from the GUI-only
        # `_module_conflict_label` below, a fallback for daemons that predate
        # the daemon-side check.
        self._module_collision_label = _transparent_label("", "Diagnostics_Label_moduleCollisions")
        self._module_collision_label.setWordWrap(True)
        self._module_collision_label.setTextFormat(Qt.TextFormat.RichText)
        self._module_collision_label.setOpenExternalLinks(True)
        self._module_collision_label.setVisible(False)

        # Module conflicts (GUI-only fallback).
        self._module_conflict_label = _transparent_label("", "Diagnostics_Label_moduleConflicts")
        self._module_conflict_label.setWordWrap(True)
        self._module_conflict_label.setVisible(False)

        # DEC-101: dual-chip board warning. Surfaces when the daemon's
        # `expected_chips` (from the it87.c DMI lookup) lists chips the kernel
        # did not enumerate — common on Gigabyte AORUS boards whose secondary
        # IT87952E needs an explicit `mmio=on`. Rich text so the docs link is
        # clickable.
        self._dual_chip_warning_label = _transparent_label("", "Diagnostics_Label_dualChipWarning")
        self._dual_chip_warning_label.setWordWrap(True)
        self._dual_chip_warning_label.setTextFormat(Qt.TextFormat.RichText)
        self._dual_chip_warning_label.setOpenExternalLinks(True)
        self._dual_chip_warning_label.setVisible(False)

        # Vendor quirk alert. Explicit PlainText (DEC-106 review): chip names
        # are interpolated into the lookup key, so never let Qt's AutoText
        # default render a clickable `<a href=...>` from a hostile chip name.
        self._vendor_quirk_label = _transparent_label("", "Diagnostics_Label_vendorQuirk")
        self._vendor_quirk_label.setWordWrap(True)
        self._vendor_quirk_label.setTextFormat(Qt.TextFormat.PlainText)
        self._vendor_quirk_label.setVisible(False)

        # ACPI conflicts.
        self._acpi_label = _transparent_label("", "Diagnostics_Label_acpiConflicts")
        self._acpi_label.setWordWrap(True)
        self._acpi_label.setVisible(False)

        # BIOS interference headline (revert counts). The headline carries a
        # stable severity Qt class so tests/screenshots can colour-check it;
        # the per-header detail + footnote live in the collapsible "BIOS
        # interference detail" section.
        self._revert_headline_label = _transparent_label(
            "", "Diagnostics_Label_revertHeadline", bold=True
        )
        self._revert_headline_label.setWordWrap(True)
        self._revert_headline_label.setVisible(False)

        persistent = [
            self._module_collision_label,
            self._module_conflict_label,
            self._revert_headline_label,
        ]
        demoted = [
            self._dual_chip_warning_label,
            self._vendor_quirk_label,
            self._acpi_label,
        ]
        return persistent, demoted

    def _build_detected_hw_section(self) -> CollapsibleSection:
        """Collapsible: detected chip + kernel-module tables."""
        self._chip_table = QTableWidget(0, 5)
        self._chip_table.setObjectName("Diagnostics_Table_chips")
        self._chip_table.setHorizontalHeaderLabels(
            ["Chip", "Driver", "Status", "Mainline", "Headers"]
        )
        self._chip_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._chip_table.horizontalHeader().setStretchLastSection(True)
        self._chip_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._chip_table.setMinimumHeight(80)
        self._apply_header_tooltips(
            self._chip_table,
            [
                "Super I/O or sensor chip model detected by the daemon",
                "Linux kernel driver expected for this chip",
                "Whether the driver is loaded and where it comes from",
                "Whether the driver is included in the mainline Linux kernel",
                "Number of PWM fan headers exposed by this chip",
            ],
        )

        self._modules_table = QTableWidget(0, 3)
        self._modules_table.setObjectName("Diagnostics_Table_kernelModules")
        self._modules_table.setHorizontalHeaderLabels(["Module", "Loaded", "Mainline"])
        self._modules_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._modules_table.horizontalHeader().setStretchLastSection(True)
        self._modules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._modules_table.setMinimumHeight(80)
        self._apply_header_tooltips(
            self._modules_table,
            [
                "Kernel module name (e.g. nct6775, it87)",
                "Whether the module is currently loaded in the running kernel",
                "Whether the module ships with the mainline Linux kernel",
            ],
        )

        table_splitter = QSplitter(Qt.Orientation.Vertical)
        table_splitter.setObjectName("Diagnostics_Splitter_hwTables")
        table_splitter.setChildrenCollapsible(False)
        table_splitter.addWidget(self._chip_table)
        table_splitter.addWidget(self._modules_table)
        table_splitter.setStretchFactor(0, 1)
        table_splitter.setStretchFactor(1, 2)

        section = CollapsibleSection("Detected hardware", "Diagnostics_Section_detectedHardware")
        section.add_widget(table_splitter)
        return section

    def _build_bios_section(self) -> CollapsibleSection:
        """Collapsible: BIOS interference detail (per-header revert rows).

        Auto-expanded by _populate_hw_diagnostics when any header reports a
        non-zero revert count, so a real problem is never hidden.
        """
        self._revert_label = _transparent_label("", "Diagnostics_Label_revertCounts")
        self._revert_label.setWordWrap(True)
        self._revert_label.setTextFormat(Qt.TextFormat.RichText)
        self._revert_label.setOpenExternalLinks(True)
        self._revert_label.setVisible(False)

        self._revert_footnote_label = _transparent_label("", "Diagnostics_Label_revertFootnote")
        self._revert_footnote_label.setWordWrap(True)
        self._revert_footnote_label.setProperty("class", "CardMeta")
        self._revert_footnote_label.setVisible(False)

        # Kept as an instance attr (unlike the other sub-sections) because
        # _populate_hw_diagnostics auto-expands it on a non-zero revert count.
        self._section_bios = CollapsibleSection(
            "BIOS interference detail", "Diagnostics_Section_biosInterference"
        )
        self._section_bios.add_widget(self._revert_label)
        self._section_bios.add_widget(self._revert_footnote_label)
        # DEC-116: the section has nothing to show until the daemon watchdog
        # reports a non-zero pwm_enable revert count — which never happens on
        # the overwhelming majority of systems. Start hidden; only
        # _populate_hw_diagnostics reveals it when reverts actually exist, so
        # the user never expands an empty section header.
        self._section_bios.setVisible(False)
        return self._section_bios

    def _build_thermal_gpu_section(self) -> CollapsibleSection:
        """Collapsible: thermal-safety state + GPU diagnostics."""
        self._thermal_label = _transparent_label("", "Diagnostics_Label_thermalSafety")
        self._thermal_label.setWordWrap(True)
        self._thermal_label.setProperty("class", "CardMeta")

        self._gpu_diag_label = _transparent_label("", "Diagnostics_Label_gpuDiag")
        self._gpu_diag_label.setWordWrap(True)
        self._gpu_diag_label.setVisible(False)

        section = CollapsibleSection("Thermal safety & GPU", "Diagnostics_Section_thermalGpu")
        section.add_widget(self._thermal_label)
        section.add_widget(self._gpu_diag_label)
        return section

    def _build_guidance_section(self) -> CollapsibleSection:
        """Collapsible: 'To fix' guidance, chip guidance, and doc links."""
        # Guidance detail (rich text with clickable driver doc links).
        self._guidance_label = _transparent_label("", "Diagnostics_Label_guidance")
        self._guidance_label.setWordWrap(True)
        self._guidance_label.setTextFormat(Qt.TextFormat.RichText)
        self._guidance_label.setOpenExternalLinks(True)
        self._guidance_label.setProperty("class", "CardMeta")
        self._guidance_label.setVisible(False)

        # Documentation reference link.
        self._docs_link_label = _transparent_label("", "Diagnostics_Label_docsLink")
        self._docs_link_label.setWordWrap(True)
        self._docs_link_label.setTextFormat(Qt.TextFormat.RichText)
        self._docs_link_label.setOpenExternalLinks(True)
        self._docs_link_label.setProperty("class", "CardMeta")
        self._docs_link_label.setVisible(False)

        # "To fix" guidance — GUI-authored remediation steps (DEC-113). No
        # daemon strings, so safe as rich text with external links enabled.
        self._fix_guidance_label = _transparent_label("", "Diagnostics_Label_fixGuidance")
        self._fix_guidance_label.setWordWrap(True)
        self._fix_guidance_label.setTextFormat(Qt.TextFormat.RichText)
        self._fix_guidance_label.setOpenExternalLinks(True)
        self._fix_guidance_label.setProperty("class", "WarningChip")
        self._fix_guidance_label.setVisible(False)

        section = CollapsibleSection("Guidance & documentation", "Diagnostics_Section_guidance")
        section.add_widget(self._fix_guidance_label)
        section.add_widget(self._guidance_label)
        section.add_widget(self._docs_link_label)
        return section

    def _build_pwm_test_section(self) -> CollapsibleSection:
        """Collapsible: PWM control test (single + batch verify).

        The verify controls and their result labels share one section, so
        reaching the buttons necessarily expands the section that shows the
        outcome — no separate auto-expand wiring is needed.
        """
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

        # DEC-101 (2E): batch verification of every writable header. Runs each
        # sequentially through the same _VerifyWorker so the lease is never
        # held longer than one verify at a time. Long-running (~6 s/header) —
        # disabled while in flight.
        self._verify_all_btn = QPushButton("Verify All Writable")
        self._verify_all_btn.setObjectName("Diagnostics_Btn_verifyAll")
        self._verify_all_btn.setToolTip(
            "Sequentially run the PWM test on every writable hwmon header "
            "(~6 s each). Useful when several headers may be misbehaving."
        )
        self._verify_all_btn.clicked.connect(self._run_pwm_verify_all)
        verify_row.addWidget(self._verify_all_btn)
        verify_row.addStretch()

        # Batch progress label (DEC-101 2E). Hidden until a batch run starts.
        self._verify_all_progress_label = _transparent_label(
            "", "Diagnostics_Label_verifyAllProgress"
        )
        self._verify_all_progress_label.setWordWrap(True)
        self._verify_all_progress_label.setProperty("class", "CardMeta")
        self._verify_all_progress_label.setVisible(False)

        self._verify_result_label = _transparent_label("", "Diagnostics_Label_verifyResult")
        self._verify_result_label.setWordWrap(True)
        self._verify_result_label.setVisible(False)

        section = CollapsibleSection("PWM control test", "Diagnostics_Section_pwmTest")
        section.add_layout(verify_row)
        section.add_widget(self._verify_all_progress_label)
        section.add_widget(self._verify_result_label)
        return section

    def _build_fan_status_pane(self) -> QWidget:
        """Bottom splitter pane: the live Fan Status table."""
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
        self._apply_header_tooltips(
            self._fan_table,
            [
                "Fan identifier — display name or hardware ID",
                "Hardware backend: openfan, hwmon, amd_gpu, or hwmon (PWM-only)",
                (
                    "How this fan is controlled. Writable methods include hwmon PWM, "
                    "PMFW curve, and OpenFan USB. 'read-only' means BIOS/EC owns the fan."
                ),
                "Hardware-measured fan speed in RPM.\n'—' means no tachometer or fan stopped.",
                "Last PWM duty cycle commanded by the daemon (0-100%).\n"
                "'—' means no command sent yet.",
                "Data freshness: ok (<2 s), stale (2-5 s), invalid (>5 s or never updated)",
            ],
        )
        fan_pane_layout.addWidget(self._fan_table, 1)
        return fan_pane

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
        self._set_class(self._hwmon_label, "WarningChip" if warn else "")

        self._features_label.setText(_features_line_text(caps, writable))

    # ── Shared widget helpers ────────────────────────────────────────
    # Small, repeatedly-needed Qt idioms factored out so the populate/
    # handler methods read declaratively rather than repeating boilerplate.

    @staticmethod
    def _set_class(widget: QWidget, css_class: str) -> None:
        """Set a widget's themed ``class`` property and repolish in place.

        Qt only re-evaluates stylesheet rules on an explicit unpolish/polish
        cycle, so changing ``class`` without this leaves stale styling.
        """
        widget.setProperty("class", css_class)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    @staticmethod
    def _apply_freshness_color(item: QTableWidgetItem, freshness: Freshness) -> None:
        """Colour a freshness cell: warn when stale, crit when invalid, else
        primary. Re-read from :func:`active_theme` so a theme switch repaints."""
        theme = active_theme()
        if freshness == Freshness.STALE:
            item.setForeground(QColor(theme.status_warn))
        elif freshness == Freshness.INVALID:
            item.setForeground(QColor(theme.status_crit))
        else:
            item.setForeground(QColor(theme.text_primary))

    @staticmethod
    def _ensure_row_items(table: QTableWidget, row: int, ncols: int) -> None:
        """Ensure every cell in ``row`` holds a ``QTableWidgetItem`` so later
        ``item(row, col).setText(...)`` calls are safe."""
        for col in range(ncols):
            if table.item(row, col) is None:
                table.setItem(row, col, QTableWidgetItem())

    @staticmethod
    def _apply_header_tooltips(table: QTableWidget, tooltips: list[str]) -> None:
        """Attach per-column tooltips to a table's horizontal header items."""
        for col, tip in enumerate(tooltips):
            item = table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

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
        """Cache the latest sensor payload and trigger a full re-render
        (DEC-117). Visibility toggles, the hidden-group expander, and the
        Mirror-to-dashboard button all read from ``_all_sensors`` so they
        never need a fresh daemon poll."""
        self._all_sensors = list(sensors)
        self._render_sensors_table()

    # ── DEC-117: Sensors-tab helpers ─────────────────────────────────

    def _hidden_sensor_ids(self) -> set[str]:
        """Live view of ``AppSettings.diagnostics_hidden_sensor_ids``.

        Returns an empty set when no settings service was wired in (test
        construction) or when the setting is missing (forward-compatible
        AppSettings).
        """
        if self._settings_service is None:
            return set()
        return set(
            getattr(self._settings_service.settings, "diagnostics_hidden_sensor_ids", []) or []
        )

    def _set_hidden_sensor_ids(self, ids: list[str]) -> None:
        """Persist the Diagnostics hide-list to ``AppSettings``.

        No-op when no settings service was wired in — the in-memory state
        still updates via the caller, but nothing survives the session.
        """
        if self._settings_service is None:
            return
        self._settings_service.update(diagnostics_hidden_sensor_ids=ids)

    def _render_sensors_table(self) -> None:
        """Re-render the Sensors tab from ``_all_sensors``.

        Partitions into visible/hidden by the current AppSettings hide-list,
        renders visible rows, then a single toggle row, then optionally the
        hidden rows when the group is expanded.
        """
        hidden_ids = self._hidden_sensor_ids()
        visible = [s for s in self._all_sensors if s.id not in hidden_ids]
        hidden = [s for s in self._all_sensors if s.id in hidden_ids]

        total_rows = len(visible)
        if hidden:
            total_rows += 1  # toggle row
            if self._hidden_group_expanded:
                total_rows += len(hidden)

        # Clear any stale cell widgets (Details buttons) before resizing —
        # Qt does not release them when rowCount shrinks, leaving zombie
        # buttons holding references that survive the next render.
        for row in range(self._sensor_table.rowCount()):
            self._sensor_table.removeCellWidget(row, _SENSOR_COL_INDEX["Details"])

        if self._sensor_table.rowCount() != total_rows:
            self._sensor_table.setRowCount(total_rows)
        self._sensor_table.clearSpans()

        board_vendor = ""
        if self._diag.last_hw_diagnostics is not None:
            board_vendor = self._diag.last_hw_diagnostics.board.vendor

        for i, s in enumerate(visible):
            self._set_sensor_row(i, s, board_vendor, dimmed=False)

        if hidden:
            toggle_row = len(visible)
            self._set_hidden_toggle_row(toggle_row, len(hidden))
            if self._hidden_group_expanded:
                for j, s in enumerate(hidden):
                    self._set_sensor_row(toggle_row + 1 + j, s, board_vendor, dimmed=True)

        self._recompute_sensor_summary(visible, hidden)

    def _set_sensor_row(
        self,
        row: int,
        s: SensorReading,
        board_vendor: str,
        *,
        dimmed: bool,
    ) -> None:
        """Populate every column of one sensor row.

        ``dimmed=True`` greys the row's foreground colour so hidden sensors
        (when expanded) read as background context, not active data.
        """
        table = self._sensor_table
        col_count = len(_SENSOR_COLUMNS)
        self._ensure_row_items(table, row, col_count - 1)  # Details holds a widget

        classification = classify_sensor(
            chip_name=s.chip_name,
            label=s.label,
            temp_type=s.temp_type,
            board_vendor=board_vendor,
        )

        # ── Quirk chip on the Label cell ─────────────────────────────
        # source_class == "bogus" comes from the sensor knowledge base
        # quirk database (e.g. ASUS NCT6776F CPUTIN). Low confidence is
        # a softer "we don't know what this is" signal — both surface as
        # an inline "⚠" prefix so the user can spot them at a glance
        # without opening the detail dialog.
        is_quirky = classification.source_class == "bogus"
        is_low_confidence = classification.confidence == "low"
        label_text = s.label or s.id
        prefix = ""
        if is_quirky:
            prefix = "⚠ "
        elif is_low_confidence:
            prefix = "? "
        table.item(row, _SENSOR_COL_INDEX["Label"]).setText(prefix + label_text)
        table.item(row, _SENSOR_COL_INDEX["Sensor ID"]).setText(s.id or "—")

        source_class_text = _SOURCE_CLASS_DISPLAY.get(
            classification.source_class, classification.source_class
        )
        table.item(row, _SENSOR_COL_INDEX["Source class"]).setText(source_class_text)
        table.item(row, _SENSOR_COL_INDEX["Kind"]).setText(s.kind or "—")
        table.item(row, _SENSOR_COL_INDEX["Source"]).setText(s.source or "—")
        table.item(row, _SENSOR_COL_INDEX["Chip"]).setText(s.chip_name or "—")
        table.item(row, _SENSOR_COL_INDEX["Driver type"]).setText(temp_type_label(s.temp_type))

        # ── Value cell with optional alarm suffix ────────────────────
        value_text = f"{s.value_c:.1f}"
        alarm_active = self._is_alarm_active(s)
        if alarm_active:
            value_text += "  ⚠ ALARM"
        value_item = table.item(row, _SENSOR_COL_INDEX["Value (°C)"])
        value_item.setText(value_text)

        # ── Trend cell ───────────────────────────────────────────────
        rate = s.rate_c_per_s
        if rate is not None and abs(rate) >= 0.1:
            arrow = "↑" if rate > 0 else "↓"
            sign = "+" if rate > 0 else ""
            trend_text = f"{arrow} {sign}{rate:.1f} °C/s"
        else:
            trend_text = "—"
        table.item(row, _SENSOR_COL_INDEX["Trend"]).setText(trend_text)

        # ── Session min/max ──────────────────────────────────────────
        if s.session_min_c is not None and s.session_max_c is not None:
            sess_text = f"{s.session_min_c:.1f} - {s.session_max_c:.1f} °C"
        else:
            sess_text = "—"
        table.item(row, _SENSOR_COL_INDEX["Session min/max"]).setText(sess_text)

        table.item(row, _SENSOR_COL_INDEX["Age (ms)"]).setText(str(s.age_ms))

        freshness_item = table.item(row, _SENSOR_COL_INDEX["Freshness"])
        freshness_item.setText(s.freshness.value)
        self._apply_freshness_color(freshness_item, s.freshness)

        confidence_text = _CONFIDENCE_DISPLAY.get(
            classification.confidence, classification.confidence
        )
        table.item(row, _SENSOR_COL_INDEX["Confidence"]).setText(confidence_text)

        # ── Colour cues: dim hidden rows, paint quirks/alarms ────────
        theme = active_theme()
        row_color = theme.text_secondary if dimmed else theme.text_primary
        for col_name in (
            "Label",
            "Sensor ID",
            "Source class",
            "Kind",
            "Source",
            "Chip",
            "Driver type",
            "Trend",
            "Session min/max",
            "Age (ms)",
            "Confidence",
        ):
            table.item(row, _SENSOR_COL_INDEX[col_name]).setForeground(QColor(row_color))
        # Re-apply freshness colour after the loop to preserve its warn/crit
        # paint (the loop above would clobber it otherwise).
        self._apply_freshness_color(freshness_item, s.freshness)

        # Quirk / alarm chips override the row colour on the Label / Value
        # cells so they stand out even in a dimmed hidden row.
        if is_quirky:
            table.item(row, _SENSOR_COL_INDEX["Label"]).setForeground(QColor(theme.status_warn))
        if alarm_active:
            value_item.setForeground(QColor(theme.status_crit))

        # ── Tooltip on every cell (preserved from pre-DEC-117) ───────
        tooltip = format_sensor_tooltip(
            classification,
            sensor_id=s.id,
            chip_name=s.chip_name,
            session_min=s.session_min_c,
            session_max=s.session_max_c,
            rate_c_per_s=s.rate_c_per_s,
        )
        for col in range(col_count - 1):  # skip Details widget cell
            cell = table.item(row, col)
            if cell is not None:
                cell.setToolTip(tooltip)

        # ── Per-row Details button ───────────────────────────────────
        btn = QPushButton("Details")
        btn.setObjectName(f"Diagnostics_SensorDetail_Btn_row{row}")
        btn.setToolTip("Open the per-sensor detail dialog (DEC-117).")
        # Bind a stable reference so re-renders don't lose the sensor.id;
        # default args capture-by-value avoid the closure-late-binding pitfall.
        btn.clicked.connect(lambda _checked=False, sid=s.id: self._open_sensor_detail(sid))
        table.setCellWidget(row, _SENSOR_COL_INDEX["Details"], btn)

    def _set_hidden_toggle_row(self, row: int, hidden_count: int) -> None:
        """Render the single "▸/▾ N hidden sensors (click to …)" toggle row.

        Spans every column so clicks anywhere along the row trigger the
        toggle — no need to aim for a particular cell.
        """
        table = self._sensor_table
        ncols = len(_SENSOR_COLUMNS)
        for col in range(ncols):
            cell = table.item(row, col)
            if cell is None:
                cell = QTableWidgetItem("")
                table.setItem(row, col, cell)
            else:
                cell.setText("")
        table.setSpan(row, 0, 1, ncols)
        toggle = table.item(row, 0)
        arrow = "▾" if self._hidden_group_expanded else "▸"
        verb = "collapse" if self._hidden_group_expanded else "expand"
        suffix = "" if hidden_count == 1 else "s"
        toggle.setText(f"  {arrow} {hidden_count} hidden sensor{suffix} (click to {verb})")
        toggle.setToolTip(
            "Hidden sensors stay reachable here — they're not removed. "
            "Right-click a hidden row to unhide it."
        )
        toggle.setForeground(QColor(active_theme().text_secondary))

    def _recompute_sensor_summary(
        self,
        visible: list[SensorReading],
        hidden: list[SensorReading],
    ) -> None:
        """Render the header summary line above the table.

        Counts are derived from the full ``_all_sensors`` list (visible +
        hidden) so the headline answer to "how many sensors does this
        system have?" doesn't change when the user hides some.
        """
        all_sensors = visible + hidden
        n = len(all_sensors)
        if n == 0:
            self._sensor_summary_label.setText("Sensors: —")
            return

        cpu = sum(1 for s in all_sensors if s.kind == "cpu_temp")
        board = sum(1 for s in all_sensors if s.kind == "mb_temp")
        gpu = sum(1 for s in all_sensors if s.kind == "gpu_temp")
        disk = sum(1 for s in all_sensors if s.kind == "disk_temp")
        stale = sum(1 for s in all_sensors if s.freshness != Freshness.FRESH)

        board_vendor = ""
        if self._diag.last_hw_diagnostics is not None:
            board_vendor = self._diag.last_hw_diagnostics.board.vendor
        low_conf = 0
        for s in all_sensors:
            classification = classify_sensor(
                chip_name=s.chip_name,
                label=s.label,
                temp_type=s.temp_type,
                board_vendor=board_vendor,
            )
            if classification.confidence == "low":
                low_conf += 1

        parts = [f"{n} total"]
        if cpu:
            parts.append(f"{cpu} CPU")
        if board:
            parts.append(f"{board} board")
        if gpu:
            parts.append(f"{gpu} GPU")
        if disk:
            parts.append(f"{disk} disk")
        if stale:
            parts.append(f"{stale} stale")
        if low_conf:
            parts.append(f"{low_conf} low-confidence")
        if hidden:
            parts.append(f"{len(hidden)} hidden")
        self._sensor_summary_label.setText("Sensors: " + " · ".join(parts))

    @staticmethod
    def _is_alarm_active(s: SensorReading) -> bool:
        """True when the daemon reported an asserted crit_alarm or when the
        live value has crossed the reported crit threshold (DEC-117).

        The two paths are deliberately separate: ``crit_alarm`` is the chip's
        latched bit (sampled at daemon discovery), while a ``value_c >= crit_c``
        comparison catches the case where temperature has just crossed the
        crit threshold but the alarm bit hasn't been re-read yet.
        """
        t = s.thresholds
        if t is None:
            return False
        if t.crit_alarm is True:
            return True
        return t.crit_c is not None and s.value_c >= t.crit_c

    def _open_sensor_detail(self, sensor_id: str) -> None:
        """Open the :class:`SensorDetailDialog` for ``sensor_id``.

        Reuses an existing dialog if one is already open (the user usually
        wants to compare a few sensors in a row — re-opening with new
        content is faster than tearing down and rebuilding the widget).
        """
        sensor = next((s for s in self._all_sensors if s.id == sensor_id), None)
        if sensor is None:
            return
        board = None
        if self._diag.last_hw_diagnostics is not None:
            board = self._diag.last_hw_diagnostics.board
        if self._sensor_detail_dialog is None:
            self._sensor_detail_dialog = SensorDetailDialog(sensor, board, parent=self)
            self._sensor_detail_dialog.finished.connect(self._on_sensor_detail_closed)
        else:
            self._sensor_detail_dialog.set_sensor(sensor, board)
        self._sensor_detail_dialog.show()
        self._sensor_detail_dialog.raise_()
        self._sensor_detail_dialog.activateWindow()

    @Slot(int)
    def _on_sensor_detail_closed(self, _result: int) -> None:
        """Drop the cached dialog reference when the user closes it so the
        next ``_open_sensor_detail`` rebuilds with fresh data and avoids
        showing a stale board snapshot."""
        self._sensor_detail_dialog = None

    def _on_sensor_cell_double_clicked(self, row: int, _column: int) -> None:
        """Resolve the row to a sensor.id and either open the detail dialog
        or flip the hidden-group expander when the row IS the toggle."""
        sensor = self._row_to_sensor(row)
        if sensor is None:
            # Double-clicking the toggle row — treat as the toggle.
            if self._is_hidden_toggle_row(row):
                self._hidden_group_expanded = not self._hidden_group_expanded
                self._render_sensors_table()
            return
        self._open_sensor_detail(sensor.id)

    def _on_sensor_context_menu(self, pos: QPoint) -> None:
        """Right-click menu on a sensor row: open detail + hide/unhide."""
        row = self._sensor_table.indexAt(pos).row()
        if row < 0:
            return
        # If this is the toggle row, only offer the toggle action — there's
        # no associated sensor to hide.
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
        detail_action.triggered.connect(lambda: self._open_sensor_detail(sensor.id))
        menu.addAction(detail_action)

        hidden_ids = self._hidden_sensor_ids()
        if sensor.id in hidden_ids:
            unhide_action = QAction("Unhide sensor", self)
            unhide_action.triggered.connect(lambda: self._set_sensor_hidden(sensor.id, False))
            menu.addAction(unhide_action)
        else:
            hide_action = QAction("Hide sensor", self)
            hide_action.triggered.connect(lambda: self._set_sensor_hidden(sensor.id, True))
            menu.addAction(hide_action)

        menu.exec(self._sensor_table.viewport().mapToGlobal(pos))

    def _row_to_sensor(self, row: int) -> SensorReading | None:
        """Resolve a table row index back to the underlying ``SensorReading``.

        Returns ``None`` when the row is the toggle separator or otherwise
        out of bounds. Reads the Sensor ID cell to do the lookup so the
        method survives column reordering.
        """
        if row < 0 or row >= self._sensor_table.rowCount():
            return None
        if self._is_hidden_toggle_row(row):
            return None
        cell = self._sensor_table.item(row, _SENSOR_COL_INDEX["Sensor ID"])
        if cell is None:
            return None
        sensor_id = cell.text()
        if not sensor_id or sensor_id == "—":
            return None
        return next((s for s in self._all_sensors if s.id == sensor_id), None)

    def _is_hidden_toggle_row(self, row: int) -> bool:
        """True iff the given row hosts the toggle separator (cell-span row).

        Detected via Qt's row-span — the toggle row spans all columns,
        normal data rows do not.
        """
        if row < 0 or row >= self._sensor_table.rowCount():
            return False
        return self._sensor_table.columnSpan(row, 0) == len(_SENSOR_COLUMNS)

    def _toggle_hidden_group(self) -> None:
        """Slot for the toggle-row click / context-menu entry."""
        self._hidden_group_expanded = not self._hidden_group_expanded
        self._render_sensors_table()

    def _set_sensor_hidden(self, sensor_id: str, hidden: bool) -> None:
        """Add or remove ``sensor_id`` from the Diagnostics hide-list and
        re-render the table. Other surfaces (dashboard chart, curves) are
        intentionally untouched — the user must explicitly click the
        Mirror-to-dashboard button to propagate the choice."""
        current = list(self._hidden_sensor_ids())
        changed = False
        if hidden and sensor_id not in current:
            current.append(sensor_id)
            changed = True
        elif not hidden and sensor_id in current:
            current.remove(sensor_id)
            changed = True
        if not changed:
            return
        self._set_hidden_sensor_ids(current)
        self._render_sensors_table()

    def _mirror_hidden_to_dashboard(self) -> None:
        """One-shot push of the Diagnostics hide-list into the dashboard's
        :class:`SeriesSelectionModel` (DEC-117).

        Translates each ``sensor.id`` into the ``sensor:<id>`` key format
        the dashboard chart uses. No-op when no series-selection model was
        wired in.
        """
        if self._series_selection is None:
            return
        for sensor_id in self._hidden_sensor_ids():
            self._series_selection.set_visible(f"sensor:{sensor_id}", False)

    # ── End DEC-117 Sensors-tab helpers ──────────────────────────────

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
            self._ensure_row_items(self._fan_table, row, col_count)

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
            self._apply_freshness_color(freshness_item, f.freshness)

            row_tip = self._fan_row_tooltip(f, presence)
            method_tip = _CONTROL_METHOD_TOOLTIPS.get(
                control_method, _CONTROL_METHOD_TOOLTIPS["unknown"]
            )
            for col in range(col_count):
                self._fan_table.item(row, col).setToolTip(method_tip if col == 2 else row_tip)
            row += 1

        for h in pwm_only:
            self._ensure_row_items(self._fan_table, row, col_count)

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
        """Fetch hardware diagnostics from the daemon and populate the UI.

        Production runs the blocking GET on a worker thread so the UI stays
        responsive (including the once-per-session auto-fetch when the Fans tab
        is first shown). When no socket path is available (e.g. tests with a
        mock client), falls back to a synchronous fetch so behaviour there is
        unchanged.
        """
        if not self._client:
            self._hw_ready_summary.setText("Cannot fetch: no daemon connection")
            return

        # Off-thread path (production).
        if self._ensure_hw_diag_worker():
            self._status_label.setText("Refreshing hardware diagnostics…")
            self._hw_diag_request.emit()
            return

        # Synchronous fallback — no socket path (e.g. mock-client tests).
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._client.hardware_diagnostics()
        except DaemonTimeout:
            self._on_hw_diag_error("unavailable", "Diagnostics fetch timed out")
        except DaemonUnavailable:
            self._on_hw_diag_error("unavailable", "Daemon unavailable — cannot fetch diagnostics")
        except DaemonError as e:
            self._on_hw_diag_error("error", e.message)
        else:
            self._on_hw_diag_ok(result)

    @Slot(object)
    def _on_hw_diag_ok(self, result: HardwareDiagnosticsResult) -> None:
        """Apply a hardware-diagnostics result on the main thread."""
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

    @Slot(str, str)
    def _on_hw_diag_error(self, category: str, message: str) -> None:
        """Surface a hardware-diagnostics fetch failure on the main thread."""
        if category == "unavailable":
            self._hw_ready_summary.setText(
                message or "Daemon unavailable — cannot fetch diagnostics"
            )
        else:
            self._hw_ready_summary.setText(f"Diagnostics error: {message}")

    def _on_diag_tab_changed(self, index: int) -> None:
        """Auto-fetch hardware diagnostics the first time the Fans tab is shown.

        Fires once per session (guarded by ``_hw_diag_auto_fetched``) so the
        readiness verdict is populated without the user clicking Refresh.
        """
        if index != self._fans_tab_index or self._hw_diag_auto_fetched:
            return
        self._hw_diag_auto_fetched = True
        if self._client and self._diag.last_hw_diagnostics is None:
            self._fetch_hardware_diagnostics()

    def _open_readiness_report(self) -> None:
        """Open the full hardware-readiness report in its own window (DEC-113)."""
        diag = self._diag.last_hw_diagnostics
        if diag is None:
            return
        html = build_readiness_report_html(diag)
        if self._report_dialog is None:
            self._report_dialog = ReadinessReportDialog(html, self)
        else:
            self._report_dialog.set_html(html)
        self._report_dialog.show()
        self._report_dialog.raise_()
        self._report_dialog.activateWindow()

    def _populate_hw_diagnostics(self, diag: HardwareDiagnosticsResult) -> None:
        """Populate hardware readiness UI from a diagnostics result."""
        hw = diag.hwmon

        # Board info (shared formatter, DEC-115)
        board = diag.board
        identity = board_identity_line(diag)
        if identity:
            self._board_info_label.setText("Board: " + identity)
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
            self._set_class(self._dual_chip_warning_label, "WarningChip")
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
            self._set_class(self._vendor_quirk_label, css)
            self._vendor_quirk_label.setVisible(True)
        else:
            self._vendor_quirk_label.setVisible(False)

        summary_parts = [header_summary_line(hw)]
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

        # Chip table (shared rows, DEC-115 — single source of truth with the
        # pop-out report, including the Status column the report had dropped).
        crows = chip_rows(diag)
        self._chip_table.setRowCount(len(crows))
        for i, r in enumerate(crows):
            self._ensure_row_items(self._chip_table, i, 5)
            self._chip_table.item(i, 0).setText(r.chip)
            self._chip_table.item(i, 1).setText(r.driver)
            self._chip_table.item(i, 2).setText(r.status)
            self._chip_table.item(i, 3).setText(r.mainline)
            self._chip_table.item(i, 4).setText(r.headers)

        # Kernel modules table (shared rows, DEC-115)
        mrows = module_rows(diag)
        self._modules_table.setRowCount(len(mrows))
        for i, r in enumerate(mrows):
            self._ensure_row_items(self._modules_table, i, 3)
            self._modules_table.item(i, 0).setText(r.name)
            self._modules_table.item(i, 1).setText(r.loaded)
            self._modules_table.item(i, 2).setText(r.mainline)

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
            self._set_class(self._acpi_label, "WarningChip")
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
            self._set_class(self._module_collision_label, "CriticalChip")
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
            self._set_class(self._module_conflict_label, "CriticalChip")
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
            # DEC-116: nothing to report — hide the whole sub-section, not just
            # its inner labels, so the user never expands an empty header.
            self._section_bios.setVisible(False)
            self._revert_headline_label.setVisible(False)
            self._revert_label.setVisible(False)
            self._revert_footnote_label.setVisible(False)
        else:
            self._section_bios.setVisible(True)
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
            self._set_class(self._revert_headline_label, severity_class)
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

            # DEC-112: a non-zero revert count is a real problem the user
            # must not miss, so surface the per-header detail by expanding
            # the section. Idempotent and never auto-collapses, so a manual
            # toggle on a healthy system is left untouched.
            self._section_bios.set_expanded(True)

        # Thermal safety (shared formatter, DEC-115)
        self._thermal_label.setText(thermal_line(diag.thermal_safety) or "")

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

        # DEC-113: readiness verdict banner, "To fix" guidance, and enable the
        # full-report pop-out now that a diagnostics result is available.
        verdict_text, verdict_cls = readiness_verdict(diag)
        self._readiness_verdict_label.setText(verdict_text)
        self._set_class(self._readiness_verdict_label, verdict_cls)

        # DEC-115: a problem board force-expands the whole card so the detail
        # and "To fix" guidance are visible by default. The persistent verdict
        # + alert stack already stay visible regardless; this never
        # auto-collapses, so a manual collapse on a healthy system is kept.
        # SuccessChip ⟺ no problems (see readiness_verdict).
        if verdict_cls != "SuccessChip":
            self._hw_ready_section.set_expanded(True)

        fix_html = build_fix_guidance_html(diag)
        if fix_html:
            self._fix_guidance_label.setText(fix_html)
            self._fix_guidance_label.setVisible(True)
        else:
            self._fix_guidance_label.setVisible(False)

        self._open_report_btn.setEnabled(True)
        # If the pop-out is already open, refresh it with the new data.
        if self._report_dialog is not None and self._report_dialog.isVisible():
            self._report_dialog.set_html(build_readiness_report_html(diag))

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
        socket_path = self._client.socket_path if self._client else None
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

    def _ensure_hw_diag_worker(self) -> bool:
        """Create the hardware-diagnostics worker + thread on first use. Returns
        False if no socket path is available (callers then fall back to a
        synchronous fetch)."""
        if self._hw_diag_worker is not None:
            return True
        # getattr: real DaemonClient always has socket_path; test mocks may not,
        # in which case callers fall back to a synchronous fetch.
        socket_path = getattr(self._client, "socket_path", None) if self._client else None
        if not socket_path:
            return False

        self._hw_diag_thread = QThread(self)
        self._hw_diag_worker = _HwDiagWorker(socket_path)
        self._hw_diag_worker.moveToThread(self._hw_diag_thread)

        # Main thread → worker thread via queued connection.
        self._hw_diag_request.connect(
            self._hw_diag_worker.do_fetch, Qt.ConnectionType.QueuedConnection
        )
        # Worker thread → main thread via queued connections.
        self._hw_diag_worker.fetch_ok.connect(
            self._on_hw_diag_ok, Qt.ConnectionType.QueuedConnection
        )
        self._hw_diag_worker.fetch_error.connect(
            self._on_hw_diag_error, Qt.ConnectionType.QueuedConnection
        )

        self._hw_diag_thread.start()
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
            header_id = self._verify_active_header or "unknown"
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
        self._set_class(self._verify_all_progress_label, "CardMeta")
        self._verify_all_progress_label.setVisible(True)

        self._step_pwm_verify_all()

    def _finish_verify_all(self) -> None:
        """Reset batch-verify state and re-enable the controls after a run.

        Shared by the normal end-of-batch path and the lease-lost abort path
        so the teardown can never drift between them.
        """
        self._verify_all_total = 0
        self._verify_btn.setEnabled(self._verify_combo.count() > 0)
        self._verify_all_btn.setEnabled(True)
        self._verify_all_btn.setText("Verify All Writable")

    def _step_pwm_verify_all(self) -> None:
        """Advance the batch-verify state machine by one header.

        If the queue is empty, finalises and shows a summary. If the
        lease has been lost mid-run (no lease_id available), aborts the
        rest of the queue with a clear message.
        """
        # End-of-batch: render summary, reset state.
        if not self._verify_all_queue:
            self._show_verify_all_summary()
            self._finish_verify_all()
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
            self._finish_verify_all()
            return

        lease_id = self._state.lease.lease_id or ""
        header_id = self._verify_all_queue.pop(0)
        # 1-based index of the header now under test (queue already popped).
        index = self._verify_all_total - len(self._verify_all_queue)
        self._verify_all_progress_label.setText(
            f"Testing {index}/{self._verify_all_total}: {header_id}"
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
        self._set_class(self._verify_all_progress_label, css_class)

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
        """Stop the verify + hardware-diagnostics worker threads. Called from the
        main window closeEvent."""
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
        if self._hw_diag_worker is not None:
            self._hw_diag_worker.shutdown()
        if self._hw_diag_thread is not None:
            self._hw_diag_thread.quit()
            if not self._hw_diag_thread.wait(2000):
                log.warning("HW diagnostics thread did not stop within 2s, terminating")
                self._hw_diag_thread.terminate()
                self._hw_diag_thread.wait(1000)
            self._hw_diag_thread = None
            self._hw_diag_worker = None

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
        self._set_class(self._verify_result_label, css_class)
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
