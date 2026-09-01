"""Controls page — FanControl-style 2-section layout.

Section A (top): Control cards grid — logical controls with mode, curve, output.
Section B (bottom): Curve cards grid + expandable curve editor.
Profile bar at top for profile management.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import ConnectionState, DaemonStatus
from control_ofc.services.app_state import AppState
from control_ofc.services.controls_view import (
    aio_tag_for,
    assigned_elsewhere_map,
    build_member_candidates,
    build_radiator_candidates,
    build_sensor_choices,
    curve_min_output_floor,
    divergent_gpu_output,
    member_rpm_map,
    override_rejection_feedback,
    parse_stored_card_size,
    prune_card_sizes,
    renew_interval_ms,
    sensor_combo_label,
    unassigned_fan_ids,
)
from control_ofc.services.controls_view import (
    # Re-exported under its historical private name: this is a safety-relevant
    # helper (it decides the persisted member_label, hence the 30% CPU/pump
    # floor) with regression tests that import it from this module.
    role_preserving_label as _role_preserving_label,
)
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    ProfileService,
    apply_role_floor,
    mix_candidate_curves,
    sync_candidate_controls,
)
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.qt_util import repolish, set_chip_class, style_splitter
from control_ofc.ui.widgets.card_metrics import DEFAULT_CARD_SIZE, card_pane_min_width
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard
from control_ofc.ui.widgets.curve_editor import CurveEditor
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.demo_controller import DemoController


# DEC-220: manual-override take/renew/release run OFF the Qt main thread on a
# dedicated worker (``_OverrideWorker`` below). Run synchronously they could
# freeze the UI for up to this timeout per call against a slow or half-dead
# daemon — and ``_renew_overrides`` iterates every held override, so the freeze
# was N times over, which also stalled the 1 Hz poll and the thermal banner. The bound is
# kept as a backstop so even off-thread a wedged call cannot pin the worker
# forever.
_OVERRIDE_HTTP_TIMEOUT_S = 2.0


class _OverrideWorker(QObject):
    """Runs manual-override HTTP calls (DEC-163) off the Qt main thread (DEC-220).

    Lives on its own ``QThread`` with its own ``DaemonClient`` — never shared with
    the main thread's profile calls, since an httpx client is not safe for
    concurrent use. The page dispatches take/renew/release via queued request
    signals and applies every result back on the main thread, so the shared
    override dicts stay single-threaded.
    """

    # control_id, pct, grant | None, error (DaemonError) | None
    take_result = Signal(str, int, object, object)
    # control_id, sent_token, new override_token | None, error (DaemonError) | None
    renew_result = Signal(str, object, object, object)

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _get_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    def attach_client(self, client: DaemonClient) -> None:
        """Reuse an existing client instead of lazily creating one — used only by
        the synchronous test-dispatch path (ControlsPage._OVERRIDE_USE_THREAD)."""
        self._client = client

    @Slot(str, int)
    def take(self, control_id: str, pct: int) -> None:
        try:
            grant = self._get_client().override_take(
                control_id, pct, timeout=_OVERRIDE_HTTP_TIMEOUT_S
            )
            self.take_result.emit(control_id, pct, grant, None)
        except DaemonError as exc:
            self.take_result.emit(control_id, pct, None, exc)
        except Exception as exc:
            # AUD-m: the same rule CONC-4 gave `renew` below, and the reason it
            # matters more here. A non-DaemonError escape — a malformed-but-200
            # grant body raising during parse, say — used to emit NO signal at
            # all, and `take_result` is the only thing that resolves the take.
            # `_manual_intent` therefore stayed latched with no token: the card
            # sat in Manual, nothing was pinned daemon-side, no renew was ever
            # scheduled, and `_on_status_reconcile` permanently excluded the card
            # from reconciliation because it believes a manual intent is pending.
            # A failed take must land as a FAILED take, never as silence.
            logging.getLogger(__name__).warning("Override take crashed for %s: %s", control_id, exc)
            self.take_result.emit(
                control_id,
                pct,
                None,
                DaemonError(code="internal_error", message=str(exc), status=0),
            )

    @Slot(str, object)
    def renew(self, control_id: str, token: int) -> None:
        try:
            result = self._get_client().override_renew(
                control_id, token, timeout=_OVERRIDE_HTTP_TIMEOUT_S
            )
            self.renew_result.emit(control_id, token, result.override_token, None)
        except DaemonError as exc:
            self.renew_result.emit(control_id, token, None, exc)
        except Exception as exc:
            # CONC-4: a non-DaemonError escape (client-layer bug) must still
            # deliver a result — the in-flight guard is cleared by the result
            # handler, so a swallowed slot exception would silently stall this
            # control's renews until release. Shape it as a DaemonError so the
            # handler's lapse path (error.code) applies unchanged.
            logging.getLogger(__name__).warning(
                "Override renew crashed for %s: %s", control_id, exc
            )
            self.renew_result.emit(
                control_id,
                token,
                None,
                DaemonError(code="internal_error", message=str(exc), status=0),
            )

    @Slot(str, object)
    def release(self, control_id: str, token: int) -> None:
        # Fire-and-forget: on failure the daemon deadman reverts the override.
        #
        # AUD-m: suppress every exception, not just `DaemonError`. Nothing awaits
        # a result here, so the cost of a non-DaemonError escape is not a stuck
        # card as it is in `take` — it is an unhandled exception raised inside a
        # worker-thread slot, which is a failure mode nothing in this page is
        # positioned to handle and which buys nothing over a logged warning. The
        # override still lapses either way: the daemon's deadman is what actually
        # reverts it, and that runs regardless of whether this call was heard.
        try:
            self._get_client().override_release(control_id, token, timeout=_OVERRIDE_HTTP_TIMEOUT_S)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Override release failed for %s (the daemon deadman will revert it): %s",
                control_id,
                exc,
            )

    @Slot()
    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


class ControlsPage(QWidget):
    """FanControl-style controls: profile bar, control cards grid, curve cards grid."""

    profile_activated = Signal(str)

    # DEC-220: dispatch manual-override HTTP calls to the off-thread worker.
    # Queued to the worker thread; results return via the worker's *_result
    # signals, handled on the main thread.
    _request_take = Signal(str, int)  # control_id, pct (a real 0-100 int)
    # Fencing tokens cross as ``object``, not ``int``: a queued cross-thread
    # connection marshals a Python int through 32-bit ``QMetaType::Int``, which
    # would silently truncate a monotonic token past 2^31. ``object`` passes the
    # Python value through untouched, and matches ``renew_result`` above.
    _request_renew = Signal(str, object)  # control_id, token
    _request_release = Signal(str, object)  # control_id, token

    _log = logging.getLogger(__name__)
    # Manual-override (DEC-163) GUI timing. The renew cadence follows each
    # grant's advised ``renew_secs``; this is only the fallback. The value
    # debounce coalesces a live slider drag into a single re-pin (a new
    # override_take supersedes the prior token) instead of one call per pixel.
    _OVERRIDE_RENEW_FALLBACK_MS = 5000
    _OVERRIDE_VALUE_DEBOUNCE_MS = 200
    # DEC-220: dispatch override HTTP on a worker thread (production). Tests flip
    # this to False to run take/renew/release inline — deterministic and no
    # thread to join — while the real threaded path is covered by
    # test_controls_dec220.py.
    _OVERRIDE_USE_THREAD = True

    def __init__(
        self,
        state: AppState | None = None,
        profile_service: ProfileService | None = None,
        client: DaemonClient | None = None,
        demo_controller: DemoController | None = None,
        settings_service: AppSettingsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._profile_service = profile_service or ProfileService()
        self._client = client
        # Demo-only mini-evaluator (DEC-165). Live mode has no control loop —
        # the daemon is the authoritative writer; this drives the demo manual
        # and curve simulation only.
        self._demo_controller = demo_controller
        self._settings_service = settings_service
        self._control_cards: dict[str, ControlCard] = {}
        self._curve_cards: dict[str, CurveCard] = {}
        self._selected_control_id: str | None = None
        self._has_unsaved: bool = False
        # Profile id whose content the page currently displays (set by
        # _refresh_all). Retained for reference; the unsaved-edit guard now lives
        # in main_window's sidebar apply flow (DEC-214).
        self._loaded_profile_id: str | None = None
        # Last-known card-writability. Cards are disabled when the daemon reports
        # no writable backend OR is not autonomous (pre-2.0, where override is
        # unsupported — DEC-163); tracked here so a rebuild (profile switch) and a
        # later capability change both honour the latest value instead of
        # stranding cards disabled (see _on_capabilities_updated).
        self._cards_writable: bool = True
        # Live manual overrides (DEC-163): control_id -> current override_token.
        # Populated only in daemon-driven (live) mode; demo mode drives the demo
        # control loop instead. The renew timer keeps every held override alive
        # well inside its TTL — a rejected renew means it lapsed (GUI froze /
        # daemon restarted), so the card reverts. The value timer debounces a
        # live slider drag into a single re-pin.
        self._overrides: dict[str, int] = {}
        # CONC-4 (2026-07-21 audit): controls whose renew is currently in
        # flight on the worker. A daemon response slower than one renew period
        # must not queue a duplicate renew; cleared when the result arrives
        # (either outcome) or the override is released.
        self._renew_in_flight: set[str] = set()
        # F-2: per-held-grant advised renew cadence (control_id -> renew_secs),
        # kept in lockstep with ``_overrides``. The single shared renew timer is
        # driven from the MIN across all held grants so a longer-cadence grant
        # can never stretch a renew past a shorter override's TTL (setInterval
        # also resets the running countdown, so we always recompute from the
        # tightest grant).
        self._override_renew_secs: dict[str, int | None] = {}
        self._override_pending: dict[str, int] = {}
        # DEC-169: daemon-held overrides this session does NOT own (control_id ->
        # pwm), discovered by reconciling `/status.overrides[]`. Distinct from
        # `_overrides`: no fencing token, so they are display-only (read-only
        # "External" chip) — the renew timer never touches them, and clicking
        # Manual takes a fresh override (explicit ownership). Kept separate so the
        # two authorities (renew timer vs poll reconcile) never collide.
        self._external_overrides: dict[str, int] = {}
        # 273-i: control_id → reason token, for controls the daemon reports it
        # cannot resolve. Tracked so the reconcile below acts only on the
        # per-poll delta, exactly like `_external_overrides`.
        self._skipped_controls: dict[str, str] = {}
        self._override_renew_timer = QTimer(self)
        self._override_renew_timer.setObjectName("Controls_Timer_overrideRenew")
        self._override_renew_timer.timeout.connect(self._renew_overrides)
        self._override_value_timer = QTimer(self)
        self._override_value_timer.setObjectName("Controls_Timer_overrideValue")
        self._override_value_timer.setSingleShot(True)
        self._override_value_timer.setInterval(self._OVERRIDE_VALUE_DEBOUNCE_MS)
        self._override_value_timer.timeout.connect(self._flush_override_values)
        # DEC-220: manual-override HTTP calls run on a dedicated worker thread so
        # they never block the Qt main loop. `_manual_intent` is the set of
        # controls the user currently wants pinned — the source of truth for the
        # take↔release race: a take that returns *after* the user released is
        # released again by the result handler, so no override outlives intent.
        # The worker exists only in live mode (demo mode drives the demo loop).
        self._manual_intent: set[str] = set()
        self._override_thread: QThread | None = None
        self._override_worker: _OverrideWorker | None = None
        # DEC-231: set by cleanup(); guards the queued take/renew result slots
        # from mutating torn-down state if a result is delivered post-teardown.
        self._is_shut_down = False
        if self._client is not None:
            self._override_worker = _OverrideWorker(self._client.socket_path)
            self._override_worker.take_result.connect(self._on_take_result)
            self._override_worker.renew_result.connect(self._on_renew_result)
            if self._OVERRIDE_USE_THREAD:
                self._override_thread = QThread()
                self._override_worker.moveToThread(self._override_thread)
                qc = Qt.ConnectionType.QueuedConnection
                self._request_take.connect(self._override_worker.take, qc)
                self._request_renew.connect(self._override_worker.renew, qc)
                self._request_release.connect(self._override_worker.release, qc)
                self._override_thread.start()
            else:
                # Deterministic synchronous dispatch (tests): no worker thread,
                # direct connections, and the worker reuses the page's client so
                # take/renew/release complete inline.
                self._override_worker.attach_client(self._client)
                self._request_take.connect(self._override_worker.take)
                self._request_renew.connect(self._override_worker.renew)
                self._request_release.connect(self._override_worker.release)

        # DEC-214: the page now edits the active/sidebar-selected profile. A
        # non-None _viewed_profile_id lets a freshly-created draft (New/Duplicate)
        # be edited without activating it; otherwise _get_current_profile falls
        # back to the active profile.
        self._viewed_profile_id: str | None = None
        # DEC-214: the curve editor is always mounted now, so gate its ~1 Hz
        # current-temp marker on page visibility — no pyqtgraph item churn while
        # the Controls page is hidden (the gaming-perf rule).
        self._page_visible = True
        # DEC-233: the curve id currently open in the editor pane, so its card can
        # be highlighted ("on the workbench") and the highlight re-applied after a
        # curve-grid rebuild. None when the editor shows its placeholder.
        self._editing_curve_id: str | None = None
        # DEC-233: fan ids not controlled by any role (feeds the actionable
        # "Unassigned Fans" button); recomputed from each fan poll.
        self._unassigned_fan_ids: list[str] = []

        self.setObjectName("Controls_Root")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # ─── Header: title + edited-profile + manage · Set up / Revert / Save ───
        main_layout.addLayout(self._build_header())

        # ─── 3-pane layout (DEC-214): Assign Roles | Link Logic | Curve Editor ─
        # Outer horizontal splitter [pane1, curves_section]; the inner horizontal
        # splitter (curves_section) holds [Link Logic, Curve Editor]; net width
        # ratio 1 : 1 : 2. Both splitter objectNames carry over from the old
        # vertical layout so the existence/count/collapsible tests stay green.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("Controls_Splitter_sections")
        style_splitter(self._splitter)  # shared resize-handle convention (DEC-234)

        # ── Pane 1: Assign Roles ──
        pane1 = QWidget()
        # DEC-260: derived from the card metric, not a literal. A hardcoded 300
        # tracked the pre-DEC-258 card width by coincidence; once the card grew to
        # 299 the pane could no longer hold one, and Qt answered with a permanent
        # horizontal scrollbar and a clipped right edge at the default window size.
        pane_min = card_pane_min_width(self._base_font_pt(), self._card_size_tier())
        pane1.setMinimumWidth(pane_min)
        # Held so `set_theme` can re-derive the minimum. The metric depends on
        # the base font size and the density tier, both of which the Theme page
        # changes live — so computing it once at construction pins the panes to
        # whatever was in effect at startup while the cards inside them keep
        # growing, which is the DEC-260 overflow all over again.
        self._card_panes = [pane1]
        p1_layout = QVBoxLayout(pane1)
        p1_layout.setContentsMargins(0, 0, 0, 0)
        p1_layout.setSpacing(6)
        roles_header = SectionHeader(
            "Assign Roles", object_name="Controls_Section_assignRoles", step=1
        )
        self._add_control_btn = make_button(
            "+",
            "secondary",
            object_name="Controls_Btn_newControl",
            accessible_name="Create a new fan role",
        )
        self._add_control_btn.setToolTip("Create a new fan role")
        self._add_control_btn.setFixedWidth(32)
        self._add_control_btn.clicked.connect(self._on_new_control_menu)
        roles_header.add_trailing(self._add_control_btn)
        p1_layout.addWidget(roles_header)

        self._controls_empty = QLabel("No fan roles configured. Click + to create one.")
        self._controls_empty.setProperty("class", "PageSubtitle")
        self._controls_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._controls_empty.setWordWrap(True)
        p1_layout.addWidget(self._controls_empty)

        self._controls_scroll = QScrollArea()
        self._controls_scroll.setWidgetResizable(True)
        self._controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._controls_flow = DraggableFlowContainer()
        self._controls_flow.order_changed.connect(self._on_controls_reordered)
        self._controls_scroll.setWidget(self._controls_flow)
        p1_layout.addWidget(self._controls_scroll, 1)

        # DEC-233: Unassigned Fans — an actionable button pinned at the bottom
        # (fed by _on_fan_rpm_updated via services/controls_view.unassigned_fan_ids).
        # Clicking lists the fans no role controls and offers to add each writable
        # one to an existing role; disabled when every fan is assigned.
        self._unassigned_btn = make_button(
            "Unassigned Fans (0)", "ghost", object_name="Controls_Btn_unassigned"
        )
        self._unassigned_btn.setToolTip("Fans not controlled by any role — click to list or assign")
        self._unassigned_btn.clicked.connect(self._on_unassigned_clicked)
        self._unassigned_btn.setEnabled(False)
        p1_layout.addWidget(self._unassigned_btn)

        self._splitter.addWidget(pane1)

        # ─── Bottom pane: Curves (with drag-to-reorder) ──────────────
        self._curves_section = QWidget()
        curves_section_layout = QVBoxLayout(self._curves_section)
        curves_section_layout.setContentsMargins(0, 0, 0, 0)
        curves_section_layout.setSpacing(0)

        # Inner horizontal splitter: Link Logic | Curve Editor.
        self._curves_editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._curves_editor_splitter.setObjectName("Controls_Splitter_curvesEditor")
        style_splitter(self._curves_editor_splitter)  # shared handle convention (DEC-234)

        # Pane 2: Link Logic (curve cards)
        pane2 = QWidget()
        pane2.setMinimumWidth(pane_min)
        self._card_panes.append(pane2)
        p2_layout = QVBoxLayout(pane2)
        p2_layout.setContentsMargins(0, 0, 0, 0)
        p2_layout.setSpacing(6)
        link_header = SectionHeader("Link Logic", object_name="Controls_Section_linkLogic", step=2)
        self._add_curve_btn = make_button(
            "+",
            "secondary",
            object_name="Controls_Btn_addCurve",
            accessible_name="Add a new curve to the library",
        )
        self._add_curve_btn.setToolTip("Add a new curve to the library")
        self._add_curve_btn.setFixedWidth(32)
        self._add_curve_btn.clicked.connect(self._on_add_curve_menu)
        link_header.add_trailing(self._add_curve_btn)
        p2_layout.addWidget(link_header)

        self._curves_empty = QLabel("No curves. Click + to create one.")
        self._curves_empty.setProperty("class", "PageSubtitle")
        self._curves_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p2_layout.addWidget(self._curves_empty)

        self._curves_scroll = QScrollArea()
        self._curves_scroll.setWidgetResizable(True)
        self._curves_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._curves_flow = DraggableFlowContainer()
        self._curves_flow.order_changed.connect(self._on_curves_reordered)
        self._curves_scroll.setWidget(self._curves_flow)
        p2_layout.addWidget(self._curves_scroll, 1)
        self._curves_editor_splitter.addWidget(pane2)

        # Pane 3: Curve Editor (always mounted, DEC-214)
        pane3 = QWidget()
        pane3.setMinimumWidth(400)
        p3_layout = QVBoxLayout(pane3)
        p3_layout.setContentsMargins(0, 0, 0, 0)
        p3_layout.setSpacing(6)
        editor_header = SectionHeader(
            "Curve Editor", object_name="Controls_Section_curveEditor", step=3
        )
        self._editor_title = QLabel("Editing: \u2014")
        self._editor_title.setObjectName("Controls_Label_editorTitle")
        self._editor_title.setProperty("class", "CardMeta")
        # DEC-231: the curve name is profile-authored (untrusted) \u2014 render verbatim.
        self._editor_title.setTextFormat(Qt.TextFormat.PlainText)
        editor_header.add_trailing(self._editor_title)
        self._test_curve_btn = make_button(
            "Test Curve", "secondary", object_name="Controls_Btn_testCurve"
        )
        self._test_curve_btn.setToolTip("Show the curve's output at the current sensor temperature")
        self._test_curve_btn.clicked.connect(self._on_test_curve)
        # DEC-233: Test + Close are contextual \u2014 shown only while a graph/stepped
        # curve is loaded in the editor (composite curves edit in a modal dialog).
        self._test_curve_btn.setVisible(False)
        editor_header.add_trailing(self._test_curve_btn)
        self._close_editor_btn = make_button(
            "Close", "ghost", object_name="Controls_Btn_closeEditor"
        )
        self._close_editor_btn.setToolTip("Close the curve editor (Esc)")
        self._close_editor_btn.clicked.connect(self._close_editor)
        self._close_editor_btn.setVisible(False)
        editor_header.add_trailing(self._close_editor_btn)
        p3_layout.addWidget(editor_header)

        # Editor frame: the reused CurveEditor + a placeholder swapped in when no
        # graph curve is being edited (the editor is always mounted now).
        self._editor_frame = QWidget()
        editor_layout = QVBoxLayout(self._editor_frame)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self._editor_placeholder = QLabel("Select a curve's Edit action to shape it here.")
        self._editor_placeholder.setObjectName("Controls_Label_editorPlaceholder")
        self._editor_placeholder.setProperty("class", "PageSubtitle")
        self._editor_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._editor_placeholder.setWordWrap(True)
        editor_layout.addWidget(self._editor_placeholder, 1)
        self._curve_editor = CurveEditor()
        self._curve_editor.setObjectName("Controls_CurveEditor_main")
        self._curve_editor.curve_changed.connect(self._on_curve_changed)
        self._curve_editor.hide()
        editor_layout.addWidget(self._curve_editor, 1)
        p3_layout.addWidget(self._editor_frame, 1)
        self._curves_editor_splitter.addWidget(pane3)

        self._curves_editor_splitter.setStretchFactor(0, 1)
        self._curves_editor_splitter.setStretchFactor(1, 2)
        self._curves_editor_splitter.setSizes([pane_min, pane_min * 2])
        self._curves_editor_splitter.setCollapsible(0, False)
        self._curves_editor_splitter.setCollapsible(1, False)
        curves_section_layout.addWidget(self._curves_editor_splitter, 1)

        self._splitter.addWidget(self._curves_section)

        # Outer split (DEC-214): Assign Roles (1) : curves = Link Logic + Editor (3).
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setSizes([pane_min, pane_min * 3])
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)
        main_layout.addWidget(self._splitter, 1)

        # No-controls guidance (shown when curves section is hidden)
        self._no_controls_hint = QLabel(
            "Create a Fan Role first. Curves are assigned to Fan Roles."
        )
        self._no_controls_hint.setProperty("class", "PageSubtitle")
        self._no_controls_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_controls_hint.setWordWrap(True)
        self._no_controls_hint.hide()
        main_layout.addWidget(self._no_controls_hint)

        # ─── Keyboard shortcuts ──────────────────────────────────────
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save_profile)
        # DEC-233: Esc closes the curve editor, but only while focus is inside the
        # editor pane (WidgetWithChildrenShortcut) so it never swallows Esc
        # elsewhere on the page. A no-op when the editor already shows its
        # placeholder.
        close_editor_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._editor_frame)
        close_editor_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        close_editor_shortcut.activated.connect(self._close_editor)

        # ─── Populate ────────────────────────────────────────────────
        self._refresh_all()

        self._prev_sensor_ids: set[str] | None = None

        # DEC-214: the sidebar owns profile selection/activation now, so the page
        # follows the service — refresh when the active profile changes or when
        # profiles are created/renamed/deleted elsewhere.
        self._profile_service.active_changed.connect(self._on_active_profile_changed)
        self._profile_service.profiles_changed.connect(self._on_profiles_changed)

        if self._state:
            self._state.sensors_updated.connect(self._on_sensor_values_updated)
            self._state.fans_updated.connect(self._on_fan_rpm_updated)
            self._state.capabilities_updated.connect(self._on_capabilities_updated)
            self._state.connection_changed.connect(self._on_connection_changed)
            # DEC-169: reconcile daemon-held overrides from the 1 Hz poll so a
            # foreign override (another client, or this GUI restarted within the
            # TTL) shows on the card instead of a stale "Curve".
            self._state.status_updated.connect(self._on_status_reconcile)
            # DEC-228: member rows render through member_display_name, so a
            # rename made on any surface must repaint them rather than leave the
            # cached member_label showing until the page is rebuilt.
            self._state.fan_alias_changed.connect(self._on_fan_alias_changed)

    def set_demo_controller(self, demo_controller: DemoController | None) -> None:
        """Inject the demo-mode mini-evaluator (DEC-165).

        The ctor param remains for tests; ``main_window`` uses this setter to
        wire the controller after construction instead of poking the private
        attribute.
        """
        self._demo_controller = demo_controller

    # ─── Lifecycle (DEC-214) ─────────────────────────────────────────

    def showEvent(self, event) -> None:
        self._page_visible = True
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._page_visible = False
        super().hideEvent(event)

    def cleanup(self) -> None:
        """Deterministically tear down the always-mounted curve editor's
        pyqtgraph scene (DEC-180 lineage) and the DEC-220 override worker thread.
        Idempotent — the editor latches and the thread teardown nulls itself."""
        self._is_shut_down = True  # DEC-231: reject any post-cleanup queued result
        # Defence-in-depth: stop the override timers so no queued renew/flush
        # fires mid-teardown (the daemon deadman already covers correctness).
        self._override_renew_timer.stop()
        self._override_value_timer.stop()
        if self._override_thread is not None:
            # Stop the worker's event loop and JOIN before closing its client:
            # closing the httpx client from the main thread while an override call
            # is still in flight on the worker raises a non-DaemonError the worker
            # slot does not catch (a teardown traceback). After wait() the worker
            # is idle, so the close cannot race a live request (DEC-220).
            self._override_thread.quit()
            if not self._override_thread.wait(2000):
                self._override_thread.terminate()
                self._override_thread.wait(1000)
            if self._override_worker is not None:
                self._override_worker.shutdown()
            self._override_thread = None
            self._override_worker = None
        self._curve_editor.cleanup()

    # ─── Header ──────────────────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        """Header (DEC-214/233): title + the profile being edited + a "⋮" manage
        menu, then a right-aligned action cluster — "Set up ▾" (fan wizard / AIO /
        GPU), Revert, Save, and the ``_unsaved_label`` status chip.

        Profile *selection/activation* lives in the sidebar (DEC-208); this page
        shows the edited-profile name for orientation (DEC-233) and keeps
        Save/Revert/Manage.
        """
        bar = QHBoxLayout()
        bar.setSpacing(8)

        title = QLabel("Controls")
        title.setProperty("class", "PageTitle")
        bar.addWidget(title)

        divider = QFrame()
        divider.setObjectName("Controls_Divider_header")
        divider.setFrameShape(QFrame.Shape.VLine)
        bar.addWidget(divider)

        # DEC-233: which profile these edits + Save apply to. Selection is
        # sidebar-owned (DEC-214); this is a read-only orientation label so the
        # user always knows what Save writes to. Untrusted name → PlainText.
        self._edited_profile_label = QLabel("")
        self._edited_profile_label.setObjectName("Controls_Label_editedProfile")
        self._edited_profile_label.setProperty("class", "CardMeta")
        self._edited_profile_label.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self._edited_profile_label)

        manage_btn = make_button(
            "⋮",
            "ghost",
            object_name="Controls_Btn_manageProfiles",
            accessible_name="Manage profiles",
        )
        manage_btn.setToolTip("Create, rename, duplicate, or delete profiles")
        manage_btn.setFixedWidth(32)
        manage_btn.clicked.connect(self._on_manage_profiles)
        bar.addWidget(manage_btn)

        bar.addStretch()

        # DEC-233: fold the contextual hardware-setup actions into one "Set up ▾"
        # menu so Save + the unsaved-status chip stay prominent and the bar stops
        # clipping on narrow windows. The AIO/GPU entries stay hidden until the
        # matching hardware is detected (see _on_capabilities_updated).
        self._setup_btn = make_button("Set up", "secondary", object_name="Controls_Btn_setup")
        self._setup_btn.setToolTip("Hardware setup: identify fans, liquid cooler, GPU fan")
        setup_menu = QMenu(self._setup_btn)
        setup_menu.setToolTipsVisible(True)
        self._wizard_action = setup_menu.addAction("Auto-Connect Wizard…", self._on_fan_wizard)
        self._wizard_action.setObjectName("Controls_Act_fanWizard")
        self._wizard_action.setToolTip("Identify and label your fans")
        self._configure_aio_action = setup_menu.addAction("Configure AIO…", self._on_configure_aio)
        self._configure_aio_action.setObjectName("Controls_Act_configureAio")
        self._configure_aio_action.setToolTip(
            "One-click setup for a liquid cooler — a constant-speed pump and a radiator-fan group"
        )
        self._configure_aio_action.setVisible(False)
        self._dedicate_gpu_action = setup_menu.addAction("Dedicate GPU Fan…", self._on_dedicate_gpu)
        self._dedicate_gpu_action.setObjectName("Controls_Act_dedicateGpu")
        self._dedicate_gpu_action.setToolTip(
            "Give the GPU fan its own curve so it can idle at 0 RPM when the GPU is cool"
        )
        self._dedicate_gpu_action.setVisible(False)
        self._setup_btn.setMenu(setup_menu)
        bar.addWidget(self._setup_btn)

        # DEC-233: discard unsaved edits, restoring the last saved profile.
        # Enabled only while there are unsaved changes (see _set_unsaved).
        self._revert_btn = make_button("Revert", "ghost", object_name="Controls_Btn_revert")
        self._revert_btn.setToolTip("Discard unsaved changes and restore the last saved version")
        self._revert_btn.clicked.connect(self._on_revert)
        self._revert_btn.setEnabled(False)
        bar.addWidget(self._revert_btn)

        self._save_btn = make_button("Save", "primary", object_name="Controls_Btn_save")
        self._save_btn.setToolTip("Save profile changes (Ctrl+S)")
        self._save_btn.clicked.connect(self._on_save_profile)
        bar.addWidget(self._save_btn)

        self._unsaved_label = QLabel("")
        self._unsaved_label.setProperty("class", "WarningChip")
        bar.addWidget(self._unsaved_label)

        return bar

    # ─── Profile management ──────────────────────────────────────────

    def select_profile(self, profile_id: str) -> None:
        """View + edit ``profile_id`` on the page (used by New/Duplicate + tests).

        The sidebar owns *activation* now (DEC-214); this only changes which
        profile the page renders. Clears the unsaved flag since a fresh profile
        is being loaded.
        """
        self._viewed_profile_id = profile_id
        self._refresh_all()
        self._set_unsaved(False)

    def has_unsaved_changes(self) -> bool:
        """Whether the viewed profile has in-progress, unsaved edits (DEC-214).

        Read by ``main_window`` to guard a sidebar profile switch — the unsaved-
        changes prompt relocated there from the removed page profile combo."""
        return self._has_unsaved

    def _on_active_profile_changed(self, _profile_id: str = "") -> None:
        """Follow a sidebar/service activation: view the now-active profile and
        rebuild (the rebuild releases live overrides — DEC-189). Clears unsaved
        because the switch is authoritative (the sidebar apply flow prompts
        first via ``has_unsaved_changes``)."""
        self._viewed_profile_id = None  # fall back to the active profile
        self._refresh_all()
        self._set_unsaved(False)

    def _on_profiles_changed(self) -> None:
        """Profiles created/renamed/deleted elsewhere — re-render the page."""
        self._refresh_all()

    def _on_fan_alias_changed(self, _fan_id: str, _display_name: str) -> None:
        """A fan was renamed somewhere — repaint member rows in place (DEC-228).

        Not ``_refresh_all``: that rebuilds the grid, and the rebuild releases
        every live manual override first (DEC-163). Renaming a fan must not cost
        the user an override they are holding.
        """
        for card in self._control_cards.values():
            card.refresh_member_names()

    def confirm_discard_unsaved(self) -> bool:
        """Ask whether to discard in-progress edits before switching profiles.

        Returns True to discard and proceed with the switch, False to keep
        editing. Isolated behind a method so tests can drive the decision
        without spinning a real modal (see the tests/conftest.py modal guard).
        """
        from PySide6.QtWidgets import QMessageBox

        result = QMessageBox.question(
            self,
            "Discard unsaved changes?",
            "This profile has unsaved changes. Discard them and switch profiles?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Discard

    def _on_manage_profiles(self) -> None:
        menu = QMenu(self)
        menu.addAction("New Profile", self._on_new_profile)
        menu.addAction("Rename Profile", self._on_rename_profile)
        menu.addAction("Duplicate Profile", self._on_duplicate_profile)
        menu.addSeparator()
        menu.addAction("Delete Profile", self._on_delete_profile)
        btn = self.findChild(QPushButton, "Controls_Btn_manageProfiles")
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_new_profile(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(
                self, "New Profile", "Profile name:", text="New Profile"
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        new_profile = self._profile_service.create_profile(name)
        # View the new draft so it can be edited without activating it (DEC-214).
        self.select_profile(new_profile.id)

    def _on_rename_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        name, ok = QInputDialog.getText(self, "Rename Profile", "New name:", text=profile.name)
        if ok and name.strip() and name.strip() != profile.name:
            profile.name = name.strip()
            self._profile_service.save_profile(profile)
            self._refresh_all()

    def _on_duplicate_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        new_profile = self._profile_service.duplicate_profile(profile.id, f"{profile.name} (copy)")
        if new_profile:
            self.select_profile(new_profile.id)

    def _on_delete_profile(self) -> None:
        current = self._get_current_profile()
        profile_id = current.id if current else ""
        if profile_id:
            reply = QMessageBox.question(
                self,
                "Delete Profile",
                f"Delete profile '{profile_id}'? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            # If we're deleting the daemon's active profile, tell it to
            # deactivate first so the curve stops driving fans the moment
            # the file is gone (DEC-097). Pre-DEC-097 the daemon kept the
            # in-memory profile until restart, leaving "phantom" curve
            # writes targeting a profile that no longer exists on disk.
            was_active_locally = self._profile_service.active_id == profile_id
            if was_active_locally and self._client is not None:
                try:
                    self._client.deactivate_profile()
                except DaemonError as exc:
                    self._log.warning("Daemon deactivate before delete failed: %s", exc)
                    # Continue with the local delete — the file is the
                    # canonical source for the next activation, and the
                    # daemon will surface the error itself.
            self._profile_service.delete_profile(profile_id)
            if was_active_locally and self._state is not None:
                self._state.set_active_profile("")
            # After deletion, fall back to viewing the active profile.
            self._viewed_profile_id = None
            self._refresh_all()

    def _on_save_profile(self) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        self._profile_service.save_profile(profile)
        self._set_unsaved(False)
        if self._profile_service.daemon_backed and not self._profile_service.is_published(
            profile.id
        ):
            # Written to the local cache but the daemon did not accept it
            # (offline, or rejected on upload) — an unpublished draft (6b).
            self._unsaved_label.setText("Saved locally — daemon offline, not published")
            self._unsaved_label.setProperty("class", "WarningChip")
        elif (
            self._client is not None
            and profile.id == self._profile_service.active_id
            and self._profile_service.is_published(profile.id)
        ):
            # DEC-188: saving the ACTIVE profile re-applies it. A store update
            # (PUT /profiles/{id}) only changes desired-state — it does NOT
            # hot-reload the daemon engine — so without an explicit re-activate an
            # edited curve would not take effect until the user clicked Activate.
            # Re-applying makes the daemon re-read the profile and re-anchor its
            # engine immediately (the daemon's activation epoch bypasses the 2°C
            # deadband so the change is visible on the next tick, not up to ~30 s
            # later).
            if self._reapply_active_profile(profile):
                self._unsaved_label.setText("Saved & reapplied to daemon")
                self._unsaved_label.setProperty("class", "SuccessChip")
            else:
                self._unsaved_label.setText("Saved — reapply failed (see log)")
                self._unsaved_label.setProperty("class", "WarningChip")
        else:
            self._unsaved_label.setText("Settings saved")
            self._unsaved_label.setProperty("class", "SuccessChip")
        repolish(self._unsaved_label)

    def _reapply_active_profile(self, profile) -> bool:
        """Re-activate the already-active profile so the daemon re-reads it and
        re-anchors its engine immediately (DEC-188).

        Called from :meth:`_on_save_profile` when the saved profile is the active
        one — which only happens when a client is present, so ``self._client`` is
        set here. A store update alone does not hot-reload the engine, so we
        re-apply to make the edit take effect now. Returns ``True`` on success;
        logs and returns ``False`` on any daemon error — the local save has
        already succeeded, so a failed re-apply never loses the edit.
        """
        assert self._client is not None  # guaranteed by the caller's guard
        profile_path = str(self._profile_service.profile_path(profile.id))
        try:
            result = self._client.activate_profile(profile_path)
        except DaemonError as exc:
            self._log.warning("Reapply of active profile %s failed: %s", profile.id, exc)
            return False
        if not result.activated:
            self._log.warning("Daemon rejected reapply of active profile %s", profile.id)
            return False
        self._log.info("Active profile %s reapplied to daemon", profile.id)
        return True

    # ─── Revert (DEC-233) ────────────────────────────────────────────

    def confirm_revert(self) -> bool:
        """Ask whether to discard unsaved edits before reverting.

        Isolated behind a method (like :meth:`confirm_discard_unsaved`) so tests
        can drive the decision without spinning a real modal.
        """
        result = QMessageBox.question(
            self,
            "Revert changes?",
            "Discard all unsaved changes and restore the last saved version of this profile?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Discard

    def _on_revert(self) -> None:
        """Discard in-progress edits, restoring the profile's last-saved state.

        Reloads the single profile from its store (daemon document when
        published, else the local cache), replacing the in-memory copy the page
        mutates, then rebuilds. A brand-new draft that was never saved has no
        stored version to fall back to — that is surfaced, not silently dropped.
        """
        if not self._has_unsaved:
            return
        profile = self._get_current_profile()
        if not profile:
            return
        if not self.confirm_revert():
            return
        reloaded = self._profile_service.reload_profile(profile.id)
        if reloaded is None:
            self._unsaved_label.setText("Nothing to revert — profile not yet saved")
            set_chip_class(self._unsaved_label, "WarningChip")
            return
        # The editor may hold a now-stale curve object from the discarded edits.
        self._close_editor()
        self._refresh_all()
        self._set_unsaved(False)
        self._unsaved_label.setText("Reverted to last saved")
        set_chip_class(self._unsaved_label, "InfoChip")

    def _on_connection_changed(self, conn: ConnectionState) -> None:
        """React to daemon connectivity (live mode only).

        Activation moved to the sidebar (DEC-214), so this no longer gates an
        Activate button; it only clears stale foreign-override chips on
        disconnect (DEC-169), since polling stops while offline.
        """
        if self._client is None:
            return
        if conn != ConnectionState.CONNECTED:
            # DEC-169: polling stops while offline, so nothing would clear a
            # stale "External" chip — revert them now. GUI-owned overrides
            # self-correct via the renew timer's rejected renew.
            self._clear_all_external_overrides()
            # 273-i: same reasoning — with polling stopped nothing would clear a
            # stale "Not controlled" chip, and a disconnected GUI does not know
            # whether the control is still skipped.
            self._clear_all_skipped()

    # ─── Refresh all ─────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        profile = self._get_current_profile()
        self._update_edited_profile_label(profile)  # DEC-233
        if not profile:
            return
        self._loaded_profile_id = profile.id
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)

    def _update_edited_profile_label(self, profile) -> None:
        """Show which profile these edits + Save apply to (DEC-233)."""
        self._edited_profile_label.setText(f"Editing: {profile.name}" if profile else "")

    # ─── Control cards grid ──────────────────────────────────────────

    def _refresh_controls_grid(self, profile) -> None:
        # Release any live overrides first: the cards are about to be destroyed
        # and rebuilt un-toggled, so a still-held daemon override would leave
        # card state diverged from the daemon (DEC-163).
        self._release_all_overrides()
        # Drop foreign-override tracking too — the cards are being rebuilt fresh;
        # the next poll re-adopts any still-active foreign override (DEC-169).
        self._external_overrides.clear()
        # 273-i: likewise — the next poll re-adopts any still-skipped control.
        self._skipped_controls.clear()
        # Clear existing
        self._controls_flow.clear_cards()
        self._control_cards.clear()

        # DEC-214: default the selection to the first role so exactly one card is
        # expanded (the mockup treatment); the rest collapse to the compact form.
        control_ids = [c.id for c in profile.controls]
        if self._selected_control_id not in control_ids:
            self._selected_control_id = control_ids[0] if control_ids else None

        tier = self._card_size_tier()
        for control in profile.controls:
            card = ControlCard(
                control,
                profile.curves,
                card_size=tier,
                user_size=self._stored_card_size(control.id),
                display_name=self._state.member_display_name,
            )
            card.selected.connect(self._on_control_selected)
            card.delete_requested.connect(self._on_delete_control)
            card.edit_role_requested.connect(self._on_edit_role)
            card.manual_toggled.connect(self._on_card_manual_toggled)
            card.manual_value_changed.connect(self._on_card_manual_value)
            card.resized.connect(self._on_card_user_resized)
            card.size_reset.connect(self._on_card_size_reset)
            # Rebuilt cards default to enabled; honour the last-known write
            # capability so a profile switch can't silently re-enable a card
            # the daemon reported as non-writable.
            card.setEnabled(self._cards_writable)
            card.set_selected(control.id == self._selected_control_id)
            self._control_cards[control.id] = card
            self._controls_flow.add_card(card, control.id)

        has_controls = len(profile.controls) > 0
        self._controls_empty.setVisible(not has_controls)
        self._controls_scroll.setVisible(has_controls)
        # Progressive disclosure: show curves section only when controls exist
        self._curves_section.setVisible(has_controls)
        self._no_controls_hint.setVisible(not has_controls)

    def _on_fan_wizard(self) -> None:
        """Open the Fan Configuration Wizard."""
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        spindown = 8
        if self._settings_service:
            spindown = self._settings_service.settings.wizard_spindown_seconds
        wizard = FanConfigWizard(
            state=self._state,
            client=self._client,
            spindown_seconds=spindown,
            parent=self,
        )
        # Alias persistence is handled by MainWindow via AppState.fan_alias_changed
        wizard.exec()

    def _on_configure_aio(self) -> None:
        """DEC-157: one-click liquid-cooler setup — build a constant-speed pump
        control + a coolant-bound radiator control via the shared creation path."""
        profile = self._get_current_profile()
        if not profile or not self._state:
            return
        from control_ofc.services.profile_service import (
            ControlMember,
            build_aio_controls,
            detect_aio_setup,
        )
        from control_ofc.ui.widgets.aio_config_dialog import AioConfigDialog

        overrides = self._state.sensor_class_overrides
        det = detect_aio_setup(self._state.hwmon_headers, self._state.sensors, overrides)
        pump_id = det.pump_member.member_id if det.pump_member else None
        preselect_ids = {m.member_id for m in det.radiator_members}

        candidates = build_radiator_candidates(
            self._state.fans,
            self._state.hwmon_headers,
            pump_id=pump_id,
            preselect_ids=preselect_ids,
            display_name=self._state.fan_display_name,
        )
        sensor_choices = build_sensor_choices(self._state.sensors, overrides)

        dlg = AioConfigDialog(
            pump_label=det.pump_member.member_label if det.pump_member else None,
            monitor_only=det.monitor_only,
            fan_candidates=candidates,
            sensor_choices=sensor_choices,
            default_sensor_id=det.coolant_sensor_id,
            parent=self,
        )
        if not dlg.exec():
            return
        res = dlg.get_result()
        # DEC-229: this was the one member-persisting path in the file that did
        # NOT go through _role_preserving_label, so it stored the alias-first
        # display name verbatim. A header whose real label carries a role word
        # (PUMP_2, CPU_OPT) that the user had renamed to "Front Rad" therefore
        # persisted a role-less member_label and fell from the 30% pump floor to
        # the 20% chassis one — on both sides, since the daemon mirrors this
        # classification rather than re-deriving it (DEC-095/162).
        radiator_members = [
            ControlMember(
                source=c["source"],
                member_id=c["id"],
                member_label=_role_preserving_label(
                    c["label"], self._state.fan_fallback_name(c["id"]), c["source"]
                ),
            )
            for c in res["radiator_members"]
        ]
        created = build_aio_controls(
            profile,
            pump_member=det.pump_member if res["pump_pct"] is not None else None,
            pump_pct=res["pump_pct"] or 0,
            radiator_members=radiator_members,
            radiator_sensor_id=res["radiator_sensor_id"],
        )
        if not created:
            return
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)
        # One-time pump-info popup when a pump control was created.
        if (
            res["pump_pct"] is not None
            and det.pump_member is not None
            and self._settings_service
            and self._settings_service.settings.show_aio_pump_info
        ):
            self._show_aio_pump_info()

    def _on_dedicate_gpu(self) -> None:
        """DEC-221: give a writable AMD GPU fan its own GPU-only control + 0-floor
        curve with the firmware zero-RPM idle-stop enabled, so it can sit at true
        0 RPM when the GPU is cool. Thin UI over ``build_gpu_control``."""
        profile = self._get_current_profile()
        if not profile or not self._state:
            return
        from control_ofc.services.profile_service import ControlMember, build_gpu_control
        from control_ofc.ui.widgets.gpu_dedicate_dialog import GpuDedicateDialog

        # The single writable AMD GPU fan (the kernel exposes one aggregate fan
        # entity per GPU). Defensive: the button only shows when one is present.
        gpu_fan = next((f for f in self._state.fans if f.source == "amd_gpu"), None)
        if gpu_fan is None:
            return
        gpu_label = self._state.fan_display_name(gpu_fan.id)

        # Sensor choices: every control-eligible sensor (DEC-193 drops WiFi-PHY
        # temps), with GPU temperatures flagged and preferred as the default so
        # the fan tracks the GPU's own heat. Prefer the GPU "edge" die temp.
        sensor_choices: list[dict] = []
        default_sensor_id: str | None = None
        for s in self._state.sensors:
            if not s.control_eligible:
                continue
            is_gpu_temp = s.kind in ("GpuTemp", "gpu_temp") or s.source == "amd_gpu"
            sensor_choices.append({"id": s.id, "label": s.label, "preferred": is_gpu_temp})
            if is_gpu_temp and default_sensor_id is None:
                default_sensor_id = s.id
            if is_gpu_temp and "edge" in (s.label or "").lower():
                default_sensor_id = s.id

        dlg = GpuDedicateDialog(
            gpu_label=gpu_label,
            sensor_choices=sensor_choices,
            default_sensor_id=default_sensor_id,
            default_zero_rpm=True,
            parent=self,
        )
        if not dlg.exec():
            return
        res = dlg.get_result()
        if not res["sensor_id"]:
            return  # never build a sensorless GPU curve (dialog also blocks this)
        gpu_member = ControlMember(
            source=gpu_fan.source, member_id=gpu_fan.id, member_label=gpu_label
        )
        created = build_gpu_control(
            profile,
            gpu_member=gpu_member,
            sensor_id=res["sensor_id"],
            zero_rpm=res["zero_rpm"],
        )
        if created is None:
            return
        self._refresh_controls_grid(profile)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)

    def _on_new_control_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Single Output Fan Role", lambda: self._on_new_control(single=True))
        menu.addAction("Group Fan Role (Multi-Fan)", lambda: self._on_new_control(single=False))
        btn = self._add_control_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_new_control(self, single: bool = False, name: str | None = None) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        if name is None:
            default_name = "New Fan Role" if single else "New Group"
            name, ok = QInputDialog.getText(
                self, "New Fan Role", "Control name:", text=default_name
            )
            if not ok or not name.strip():
                return
            name = name.strip()
        curve_id = profile.curves[0].id if profile.curves else ""
        control = LogicalControl(name=name, mode=ControlMode.CURVE, curve_id=curve_id)
        profile.controls.append(control)
        self._refresh_controls_grid(profile)
        self._set_unsaved(True)

    def _on_edit_role(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        control = next((c for c in profile.controls if c.id == control_id), None)
        if not control:
            return

        from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog

        dlg = FanRoleDialog(
            control,
            profile.curves,
            parent=self,
            display_name=self._state.member_display_name,
        )
        dlg.set_edit_members_callback(self._on_edit_members)
        if dlg.exec():
            result = dlg.get_result()
            # DEC-214: "Delete Role" routes to the existing card-delete path.
            if result.get("delete"):
                self._on_delete_control(control_id)
                return
            control.name = result["name"]
            control.mode = result["mode"]
            control.curve_id = result["curve_id"]
            control.manual_output_pct = result.get("manual_output_pct", control.manual_output_pct)
            # Persist per-GPU-member zero-RPM toggles back onto the members.
            zero_rpm_map = result.get("gpu_fan_zero_rpm", {}) or {}
            for member in control.members:
                if member.source == "amd_gpu" and member.member_id in zero_rpm_map:
                    member.fan_zero_rpm = bool(zero_rpm_map[member.member_id])
            card = self._control_cards.get(control_id)
            if card:
                card.update_control(control, profile.curves)
            self._set_unsaved(True)

    def _on_delete_control(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        profile.controls = [c for c in profile.controls if c.id != control_id]
        self._refresh_controls_grid(profile)
        self._set_unsaved(True)

    def focus_control(self, control_id: str) -> bool:
        """Select and reveal ``control_id``'s card (DEC-222 Dashboard deep-link).

        Returns True when the control exists on this page. A blank or unknown id
        (the Unassigned card, or a control deleted since the poll) is a no-op that
        returns False — the caller has still navigated here, which is the useful
        half of the action.
        """
        card = self._control_cards.get(control_id) if control_id else None
        if card is None:
            return False
        self._on_control_selected(control_id)
        # setFocus alone does NOT scroll a QScrollArea, and ControlCard is a
        # QFrame with the default NoFocus policy so it draws no focus ring — a
        # card below the fold would be silently left off-screen, which is the
        # opposite of what a Dashboard "Edit" deep-link should do.
        self._controls_scroll.ensureWidgetVisible(card)
        card.setFocus()
        return True

    def _on_control_selected(self, control_id: str) -> None:
        self._selected_control_id = control_id
        # DEC-214: expand the selected card's detail rows, collapse the rest.
        for cid, card in self._control_cards.items():
            card.set_selected(cid == control_id)

    def _on_edit_members(self, control_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        control = next((c for c in profile.controls if c.id == control_id), None)
        if not control:
            return

        available: list[dict] = []
        if self._state:
            available = build_member_candidates(
                self._state.fans,
                self._state.hwmon_headers,
                gpu_writable=(
                    self._state.capabilities is not None
                    and self._state.capabilities.amd_gpu.fan_write_supported
                ),
                display_name=self._state.fan_display_name,
                fallback_name=self._state.fan_fallback_name,
            )

        assigned_elsewhere = assigned_elsewhere_map(profile.controls, control_id)

        from control_ofc.ui.widgets.member_editor import MemberEditorDialog

        dlg = MemberEditorDialog(
            control.members,
            available,
            assigned_elsewhere,
            role_name=control.name,
            parent=self,
            display_name=self._state.member_display_name,
        )
        if dlg.exec():
            new_members = dlg.get_members()
            # Check if any NEW GPU fans were added — show zero-RPM info popup
            old_gpu_ids = {m.member_id for m in control.members if m.source == "amd_gpu"}
            new_gpu_ids = {m.member_id for m in new_members if m.source == "amd_gpu"}
            added_gpu = new_gpu_ids - old_gpu_ids
            if added_gpu and self._settings_service:
                settings = self._settings_service.settings
                if settings.show_gpu_zero_rpm_warning:
                    self._show_gpu_zero_rpm_info()

            control.members = new_members
            # Membership change can shift the role (chassis ↔ CPU/pump),
            # so reapply the role-aware floor before the next save.
            if apply_role_floor(control):
                self._log.info(
                    "Control '%s' minimum_pct raised to %.0f%% by role policy",
                    control.name,
                    control.minimum_pct,
                )
            card = self._control_cards.get(control_id)
            if card:
                card.update_control(control, profile.curves)
            self._set_unsaved(True)

    # ─── Curve cards grid ────────────────────────────────────────────

    def _refresh_curves_grid(self, profile) -> None:
        self._curves_flow.clear_cards()
        self._curve_cards.clear()

        tier = self._card_size_tier()
        for curve in profile.curves:
            card = CurveCard(
                curve,
                card_size=tier,
                user_size=self._stored_card_size(curve.id),
            )
            card.edit_requested.connect(self._on_edit_curve)
            card.delete_requested.connect(self._on_delete_curve)
            card.rename_requested.connect(self._on_rename_curve)
            card.duplicate_requested.connect(self._on_duplicate_curve)
            card.unlink_requested.connect(self._on_unlink_curve)
            card.resized.connect(self._on_card_user_resized)
            card.size_reset.connect(self._on_card_size_reset)
            self._curve_cards[curve.id] = card
            self._curves_flow.add_card(card, curve.id)

        # Set "Used by" on each curve card
        for _cid, ccard in self._curve_cards.items():
            role_names = [ctrl.name for ctrl in profile.controls if ctrl.curve_id == ccard.curve.id]
            ccard.set_used_by(role_names)

        # DEC-233: re-apply the editing highlight after a rebuild so the card open
        # in the editor stays "on the workbench". If that curve is gone (deleted,
        # or a profile switch replaced the grid) close the editor fully, so its
        # pane, title, and Test/Close actions can't strand on a curve that is no
        # longer shown — leaving the editor open on another profile's curve would
        # also mis-target Test Curve and Save. Idempotent; _on_delete_curve
        # already closes before rebuilding, so the delete path is unaffected.
        if self._editing_curve_id and self._editing_curve_id in self._curve_cards:
            self._curve_cards[self._editing_curve_id].set_editing(True)
        elif self._editing_curve_id is not None:
            self._close_editor()

        has_curves = len(profile.curves) > 0
        self._curves_empty.setVisible(not has_curves)
        self._curves_scroll.setVisible(has_curves)

    def _on_controls_reordered(self, new_order: list[str]) -> None:
        """Handle drag-to-reorder of fan role cards."""
        profile = self._get_current_profile()
        if not profile:
            return
        control_map = {c.id: c for c in profile.controls}
        profile.controls = [control_map[cid] for cid in new_order if cid in control_map]
        self._set_unsaved(True)

    def _on_curves_reordered(self, new_order: list[str]) -> None:
        """Handle drag-to-reorder of curve cards."""
        profile = self._get_current_profile()
        if not profile:
            return
        # Reorder profile.curves to match the new card order
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map[cid] for cid in new_order if cid in curve_map]
        self._set_unsaved(True)

    def _on_add_curve_menu(self) -> None:
        menu = QMenu(self)
        for ct in [
            CurveType.GRAPH,
            CurveType.STEPPED,
            CurveType.LINEAR,
            CurveType.FLAT,
            CurveType.TRIGGER,
            CurveType.MIX,
            CurveType.SYNC,
        ]:
            menu.addAction(f"{ct.value.title()} Curve", lambda t=ct: self._on_add_curve(t))
        btn = self._add_curve_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_add_curve(self, curve_type: CurveType = CurveType.GRAPH) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        from control_ofc.ui.widgets.curve_editor import PRESETS

        points = []
        if curve_type in (CurveType.GRAPH, CurveType.STEPPED):
            points = [CurvePoint(p.temp_c, p.output_pct) for p in PRESETS["Linear"]]
        curve = CurveConfig(name=f"New {curve_type.value.title()}", type=curve_type, points=points)
        profile.curves.append(curve)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)

    def _on_delete_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        # Unassign from any controls that reference this curve
        for ctrl in profile.controls:
            if ctrl.curve_id == curve_id:
                ctrl.curve_id = ""
        profile.curves = [c for c in profile.curves if c.id != curve_id]
        # Close editor if editing the deleted curve
        editing = self._curve_editor.get_curve()
        if editing and editing.id == curve_id:
            self._close_editor()
        self._refresh_curves_grid(profile)
        self._refresh_controls_grid(profile)  # update control cards (curve_id cleared)
        self._set_unsaved(True)

    def _on_rename_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        curve = profile.get_curve(curve_id)
        if not curve:
            return
        name, ok = QInputDialog.getText(self, "Rename Curve", "New name:", text=curve.name)
        if ok and name.strip():
            curve.name = name.strip()
            self._refresh_curves_grid(profile)
            self._refresh_controls_grid(profile)
            self._set_unsaved(True)

    def _on_duplicate_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        import uuid

        source = profile.get_curve(curve_id)
        if not source:
            return
        data = source.to_dict()
        data["id"] = str(uuid.uuid4())[:8]
        data["name"] = f"{source.name} (copy)"
        new_curve = CurveConfig.from_dict(data)
        profile.curves.append(new_curve)
        self._refresh_curves_grid(profile)
        self._set_unsaved(True)

    def _on_edit_curve(self, curve_id: str) -> None:
        profile = self._get_current_profile()
        if not profile:
            return
        curve = profile.get_curve(curve_id)
        if not curve:
            return

        # Parameter/composite curves open a modal dialog; Graph/Stepped use the
        # embedded point editor.
        if curve.type in (
            CurveType.LINEAR,
            CurveType.FLAT,
            CurveType.TRIGGER,
            CurveType.MIX,
            CurveType.SYNC,
        ):
            from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

            # Build sensor items from current state
            sensor_items = []
            if self._state:
                for s in self._state.sensors:
                    sensor_items.append((s.id, self._sensor_combo_label(s)))
            # Composite curves offer only cycle-free choices (DEC-150/151).
            mix_candidates = (
                mix_candidate_curves(profile, curve.id) if curve.type == CurveType.MIX else None
            )
            sync_candidates = (
                sync_candidate_controls(profile, curve.id) if curve.type == CurveType.SYNC else None
            )
            dlg = CurveEditDialog(
                curve,
                sensor_items,
                mix_candidates=mix_candidates,
                sync_candidates=sync_candidates,
                parent=self,
            )
            if dlg.exec():
                dlg.apply_to_curve()
                self._refresh_curves_grid(profile)
                self._set_unsaved(True)
        else:
            self._editor_title.setText(f"Editing: {curve.name}")
            # Clamp the editable floor to the strictest minimum_pct across all
            # controls referencing this curve. If a curve is shared between a
            # CPU pump role (30%) and a chassis role (20%), the editor enforces
            # 30% so the user cannot author a point that would be clamped at
            # write time for the stricter role.
            min_floor = self._curve_min_output_floor(profile, curve.id)
            self._curve_editor.set_min_output(min_floor)
            self._curve_editor.set_curve(curve)
            # Always-mounted editor (DEC-214): swap the placeholder for the editor.
            self._editor_placeholder.hide()
            self._curve_editor.show()
            # DEC-233: reveal the contextual header actions and light up the
            # source card so it's unmistakable which curve is on the workbench.
            self._test_curve_btn.setVisible(True)
            self._close_editor_btn.setVisible(True)
            self._set_curve_editing(curve.id)

    def _close_editor(self) -> None:
        """Return the always-mounted editor to its placeholder state (DEC-214).

        Also clears the DEC-233 editing highlight + contextual header actions. A
        no-op when the editor already shows its placeholder (Esc / repeat click).
        """
        self._curve_editor.hide()
        self._editor_placeholder.show()
        self._editor_title.setText("Editing: —")
        self._test_curve_btn.setVisible(False)
        self._close_editor_btn.setVisible(False)
        self._set_curve_editing(None)

    def _set_curve_editing(self, curve_id: str | None) -> None:
        """Highlight exactly the curve card open in the editor pane (DEC-233).

        Clears the highlight on every other card, so switching from editing one
        graph curve to another moves the glow with it.
        """
        self._editing_curve_id = curve_id
        for cid, card in self._curve_cards.items():
            card.set_editing(cid == curve_id)

    def _on_test_curve(self) -> None:
        """Test Curve (DEC-214): surface the curve's output at the *current* sensor
        temperature using the editor's existing live readout — no new capability.

        Re-pushes the latest sensor value so the dashed current-temp marker + the
        output readout refresh for the curve open in the editor. A no-op when no
        graph curve is loaded (composite curves edit in a dialog)."""
        if self._curve_editor.get_curve() is None:
            return
        value = getattr(self._curve_editor, "_current_sensor_value", None)
        if value is not None:
            self._curve_editor.set_current_sensor_value(value)

    def _on_unlink_curve(self, curve_id: str) -> None:
        """Unlink (DEC-214): detach this curve from every role using it — a real
        but minimal desired-state edit (``curve_id`` cleared + mode → MANUAL on the
        referencing controls). No daemon/schema/PWM write; saved via Save Profile."""
        profile = self._get_current_profile()
        if not profile:
            return
        changed = False
        for control in profile.controls:
            if control.curve_id == curve_id:
                control.curve_id = ""
                control.mode = ControlMode.MANUAL
                changed = True
        if changed:
            self._refresh_controls_grid(profile)
            self._refresh_curves_grid(profile)
            self._set_unsaved(True)

    def _curve_min_output_floor(self, profile, curve_id: str) -> float:
        """The strictest role floor for a curve — see
        :func:`controls_view.curve_min_output_floor`."""
        return curve_min_output_floor(profile, curve_id)

    def _on_curve_changed(self) -> None:
        self._set_unsaved(True)
        # Update control card output previews
        self._update_card_previews()
        # Refresh the curve card preview for the curve being edited
        curve = self._curve_editor.get_curve()
        if curve:
            card = self._curve_cards.get(curve.id)
            if card:
                card.update_curve(curve)

    # ─── Helpers ─────────────────────────────────────────────────────

    def _update_card_previews(self) -> None:
        """Re-evaluate output preview on all cards referencing the curve being edited."""
        profile = self._get_current_profile()
        editing = self._curve_editor.get_curve()
        if not profile or not editing or not self._state:
            return
        for _cid, card in self._control_cards.items():
            ctrl = card.control
            if ctrl.mode == ControlMode.CURVE and ctrl.curve_id == editing.id and editing.sensor_id:
                for s in self._state.sensors:
                    if s.id == editing.sensor_id:
                        output = editing.interpolate(s.value_c)
                        card.update_output_preview(editing.name, s.label, s.value_c, output)
                        break

    def _get_current_profile(self):
        # DEC-214: the page edits the viewed profile (a New/Duplicate draft) if
        # one is set, else the active/sidebar-selected profile.
        if self._viewed_profile_id:
            profile = self._profile_service.get_profile(self._viewed_profile_id)
            if profile is not None:
                return profile
        return self._profile_service.active_profile

    def _show_gpu_zero_rpm_info(self) -> None:
        """Show an informational popup explaining GPU zero-RPM behaviour."""
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("GPU Fan Control")
        msg.setText("GPU Zero-RPM Mode")
        msg.setInformativeText(
            "By default a GPU fan controlled by a curve keeps spinning at all "
            "temperatures \u2014 the GPU\u2019s zero-RPM idle stop is left off so "
            "the fan always responds. This is the safe default.\n\n"
            "To let the GPU fan stop completely at idle (true 0 RPM when the GPU "
            "is cool), enable zero-RPM for it: use \u201cDedicate GPU Fan\u201d "
            "(recommended) or tick \u201cAllow zero-RPM idle\u201d in the fan "
            "role. The daemon restores automatic zero-RPM control when it shuts "
            "down.\n\n"
            "This is normal behaviour for AMD RDNA3+ GPUs and matches how other "
            "Linux GPU control tools (e.g. LACT) operate."
        )

        dont_show = QCheckBox("Don\u2019t show this again")
        msg.setCheckBox(dont_show)
        msg.exec()

        if dont_show.isChecked() and self._settings_service:
            self._settings_service.update(show_gpu_zero_rpm_warning=False)

    def _show_aio_pump_info(self) -> None:
        """One-time popup explaining the AIO pump floor (DEC-157)."""
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("AIO Pump")
        msg.setText("The pump runs at a constant speed")
        msg.setInformativeText(
            "Your AIO pump is set to a constant speed with a 30% minimum floor.\n\n"
            "Pumps cool best at a steady speed — running a pump too low reduces "
            "coolant flow and cooling and can stress the pump. Keep it at a constant "
            "level (around 80% is a good default) rather than curving it down with "
            "temperature."
        )
        dont_show = QCheckBox("Don't show this again")
        msg.setCheckBox(dont_show)
        msg.exec()
        if dont_show.isChecked() and self._settings_service:
            self._settings_service.update(show_aio_pump_info=False)

    def _set_unsaved(self, unsaved: bool) -> None:
        self._has_unsaved = unsaved
        self._unsaved_label.setText("Unsaved changes" if unsaved else "")
        if unsaved:
            set_chip_class(self._unsaved_label, "WarningChip")
        # DEC-233: Revert is meaningful only while there are edits to discard.
        self._revert_btn.setEnabled(unsaved)

    def update_control_outputs(
        self,
        outputs: dict[str, float],
        member_outputs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        profile = self._get_current_profile()
        member_outputs = member_outputs or {}
        for control_id, output in outputs.items():
            card = self._control_cards.get(control_id)
            if not card:
                continue
            # Find the sensor driving this control for context
            sensor_name = ""
            sensor_value = None
            if profile and self._state:
                ctrl = card.control
                if ctrl.mode == ControlMode.CURVE:
                    curve = profile.get_curve(ctrl.curve_id)
                    if curve and curve.sensor_id:
                        for s in self._state.sensors:
                            if s.id == curve.sensor_id:
                                sensor_name = s.label
                                sensor_value = s.value_c
                                break
            gpu_output = divergent_gpu_output(
                card.control, output, member_outputs.get(control_id, {})
            )
            card.set_output(output, sensor_name, sensor_value, gpu_output_pct=gpu_output)

    def _base_font_pt(self) -> int:
        """Theme base font size, which the card metric scales with (DEC-260)."""
        from control_ofc.ui.theme import active_theme

        return active_theme().base_font_size_pt

    def _card_size_tier(self) -> str:
        """Current card-size density tier from settings (default comfortable)."""
        if self._settings_service is not None:
            return self._settings_service.settings.card_size
        return DEFAULT_CARD_SIZE

    # ─── Per-card user sizes (DEC-129) ───────────────────────────────

    def _stored_card_size(self, card_id: str) -> tuple[int, int] | None:
        """Persisted [w, h] override for a control/curve id, if any."""
        if self._settings_service is None:
            return None
        raw = self._settings_service.settings.controls_card_sizes.get(card_id)
        return parse_stored_card_size(raw)

    def _on_card_user_resized(self, card_id: str, width: int, height: int) -> None:
        """Grip drag finished: persist the snapped size for this card."""
        if self._settings_service is None:
            return
        sizes = dict(self._settings_service.settings.controls_card_sizes)
        sizes[card_id] = [width, height]
        self._prune_card_sizes(sizes)
        self._settings_service.update(controls_card_sizes=sizes)

    def _on_card_size_reset(self, card_id: str) -> None:
        """Grip double-click: the card already restored its theme size —
        drop the persisted override."""
        if self._settings_service is None:
            return
        sizes = dict(self._settings_service.settings.controls_card_sizes)
        if sizes.pop(card_id, None) is not None:
            self._settings_service.update(controls_card_sizes=sizes)

    def _prune_card_sizes(self, sizes: dict[str, list[int]]) -> None:
        """Drop overrides for ids that no longer exist in *any* known profile.

        Keyed across all profiles (not just the active one) so switching
        profiles never sheds the inactive profile's card sizes.
        """
        known: set[str] = set()
        for profile in self._profile_service.profiles:
            known.update(c.id for c in profile.controls)
            known.update(c.id for c in profile.curves)
        prune_card_sizes(sizes, known)

    def set_theme(self, tokens) -> None:
        """Forward theme updates to child widgets and re-apply card sizing.

        A theme change can alter the base font size, so every card re-derives
        its width + minimum height from the new base size and the current
        density tier (DEC-128). Cards with a DEC-129 user size keep it
        (re-clamped to the new content minimum, never cleared). Curve cards
        additionally repaint their preview in the new accent colour.
        """
        self._curve_editor.set_theme(tokens)
        base_pt = tokens.base_font_size_pt
        tier = self._card_size_tier()
        for card in self._control_cards.values():
            # Re-apply the card's inline-styled accents (link nub + role dot) so
            # a live theme switch repaints them, not just resizes the card.
            card.set_theme(tokens)
            card.apply_card_size(base_pt, tier)
        for card in self._curve_cards.values():
            card.set_theme(tokens)
            card.apply_card_size(base_pt, tier)

        # The panes must track the cards. Raising the live base font from 10pt
        # to 16pt grows a card from 299px to 437px; leaving the panes at the
        # 325px minimum derived at startup puts the flow container's minimum
        # past the viewport, and Qt answers with exactly the permanent
        # horizontal scrollbar and clipped resize grip DEC-260 removed.
        pane_min = card_pane_min_width(base_pt, tier)
        for pane in self._card_panes:
            pane.setMinimumWidth(pane_min)

    def _on_capabilities_updated(self, caps) -> None:
        # DEC-157/233: surface the Configure AIO entry (now a "Set up ▾" menu
        # action) only when a liquid cooler is detected (idempotent — capabilities
        # re-fire on every refresh).
        aio = getattr(caps, "aio_hwmon", None)
        self._configure_aio_action.setVisible(bool(getattr(aio, "present", False)))
        # DEC-221/233: surface "Dedicate GPU Fan" only for a present, writable,
        # zero-RPM-capable AMD GPU (idempotent — capabilities re-fire on refresh).
        gpu = getattr(caps, "amd_gpu", None)
        self._dedicate_gpu_action.setVisible(
            bool(gpu)
            and bool(getattr(gpu, "present", False))
            and bool(getattr(gpu, "fan_write_supported", False))
            and bool(getattr(gpu, "gpu_zero_rpm_available", False))
        )
        if not hasattr(caps, "features") or caps.features is None:
            return
        # Idempotent both ways: capabilities re-fire on every refresh and every
        # reconnect, so an incomplete snapshot must not leave cards stranded
        # disabled once write support returns.
        #
        # Override is a 2.0.0 daemon feature (DEC-163): its endpoints don't exist
        # on a pre-2.0 daemon, and against one the GUI has stood down (the
        # main-window control gate shows the upgrade banner). So cards are writable
        # only when the daemon is autonomous AND advertises a writable backend —
        # keeping the Manual toggles in step with the banner instead of offering an
        # override that can only 404. Demo advertises autonomous_control, so its
        # cards stay live.
        control = getattr(caps, "control", None)
        autonomous = bool(control and control.autonomous_control)
        # `control.manual_override` is the daemon's own statement that
        # `/control/{id}/override` exists. Until now the Manual toggle rode on
        # `autonomous_control` alone and worked purely by co-occurrence — every
        # 2.0.0+ daemon happened to advertise both — so a daemon that dropped the
        # override surface would have offered a toggle that could only 404. The
        # flag has been parsed since DEC-159/160 and simply never read.
        self._cards_writable = (
            autonomous
            and bool(control and control.manual_override)
            and bool(caps.features.openfan_write_supported or caps.features.hwmon_write_supported)
        )
        for card in self._control_cards.values():
            card.setEnabled(self._cards_writable)
        # Same reasoning for the identify wizard, which calls
        # `fan_identify(id, "stop"/"restore")` for every source (DEC-166). Hidden
        # rather than disabled-with-a-tooltip, matching how the AIO and GPU
        # actions above handle an absent capability.
        self._wizard_action.setVisible(bool(control and control.fan_identify))

    def _on_card_manual_toggled(self, control_id: str, active: bool, pct: int) -> None:
        """Per-card Manual toggle: pin or release one control transiently.

        In live mode this is a daemon-owned, expiring, fencing-guarded override
        (DEC-163): it reverts to autonomous curve control if the GUI stops
        renewing (daemon deadman), and the card reverts if a renew is rejected.
        In demo mode (no daemon client) the demo control loop owns the simulated
        manual state. Never touches the saved profile.
        """
        if self._client is not None:
            if active:
                self._take_override(control_id, pct)
            else:
                self._release_override(control_id)
        elif self._demo_controller is not None:
            if active:
                self._demo_controller.set_control_manual(control_id, float(pct))
            else:
                self._demo_controller.clear_control_manual(control_id)

    def _on_card_manual_value(self, control_id: str, pct: int) -> None:
        """Live slider drag while a card is in transient manual mode."""
        if self._client is not None:
            # Gate on intent (not confirmed `_overrides`) so a drag during a take
            # still in flight is coalesced too (DEC-220).
            if control_id in self._manual_intent:
                # Debounce: coalesce a drag into one re-pin (a new override_take
                # supersedes the prior token) instead of one call per pixel.
                self._override_pending[control_id] = pct
                self._override_value_timer.start()
        elif self._demo_controller is not None:
            self._demo_controller.set_control_manual(control_id, float(pct))

    # ── Manual override via the daemon API (DEC-163) ─────────────────────
    def _recompute_renew_interval(self) -> None:
        """Drive the shared renew timer from the MIN renew cadence across every
        held grant (F-2).

        One timer serves all overrides, and ``setInterval`` resets the running
        countdown, so it must fire fast enough for the shortest-TTL grant.
        Last-writer-wins on a heterogeneous set (a later, larger ``renew_secs``)
        could otherwise stretch a renew past an earlier override's shorter TTL
        and let it lapse. Grants missing ``renew_secs`` fall back to
        ``_OVERRIDE_RENEW_FALLBACK_MS``; the floor stays 1000 ms.
        """
        interval = renew_interval_ms(self._override_renew_secs, self._OVERRIDE_RENEW_FALLBACK_MS)
        # Qt6 `setInterval` on a running timer restarts the countdown, so calling
        # it every recompute (the cadence is unchanged across most polls) could
        # repeatedly reset an about-to-fire renew and let a short-TTL grant lapse.
        # Only re-arm when the cadence genuinely changes.
        if interval is not None and interval != self._override_renew_timer.interval():
            self._override_renew_timer.setInterval(interval)

    def _surface_override_rejection(self, control_id: str, exc: DaemonError) -> None:
        """Surface a *user-actionable* override rejection on the page status chip;
        benign races stay a quiet card revert (the decision is
        :func:`controls_view.override_rejection_feedback`, DEC-163). The card
        revert stays owned by the caller — this method never touches card state."""
        feedback = override_rejection_feedback(exc.code)
        if feedback is None:
            return
        message, css_class = feedback
        self._log.debug("Override on %s surfaced to user (%s)", control_id, exc.code)
        self._unsaved_label.setText(message)
        set_chip_class(self._unsaved_label, css_class)

    def _take_override(self, control_id: str, pct: int) -> None:
        """Pin a control to a fixed PWM on the daemon (dispatched off-thread —
        DEC-220). Records intent synchronously; ``_on_take_result`` applies the
        grant when the worker returns."""
        if self._override_worker is None:
            return
        self._manual_intent.add(control_id)
        # 277-q: forget any foreign-override figure we were tracking for this
        # control. Taking Manual nulls the CARD's `_external_pct`, and leaving the
        # PAGE's entry at the same pwm meant no later poll registered as a delta —
        # so if the take was then rejected, `_revert_card_manual` →
        # `clear_manual` → `_restore_daemon_chip` found `_external_pct is None`
        # and painted blank while a foreign override was still pinning those fans.
        # Real but bounded: both `_revert_card_manual` call sites pop
        # `self._overrides` first, so the next poll re-adopts and the chip returns
        # within ~1 s. Dropping it here keeps card and page in step from the
        # start rather than relying on that self-heal, which also required the
        # other client's pwm to be unchanged and still renewing.
        self._external_overrides.pop(control_id, None)
        self._request_take.emit(control_id, pct)

    def _on_take_result(self, control_id: str, _pct: int, grant: object, error: object) -> None:
        """Main-thread handler for a completed override_take (DEC-220)."""
        if self._is_shut_down:  # DEC-231: a result queued before cleanup()
            return
        if error is not None:
            self._manual_intent.discard(control_id)
            self._log.warning(
                "Override of control %s failed (%s): %s",
                control_id,
                error.code,
                error.message,
            )
            self._revert_card_manual(control_id)
            self._surface_override_rejection(control_id, error)
            return
        if control_id not in self._manual_intent:
            # The user released this control while the take was in flight — the
            # daemon granted it anyway, so release the orphan (the deadman would
            # too, but this reverts to the curve immediately).
            self._request_release.emit(control_id, grant.override_token)
            return
        self._overrides[control_id] = grant.override_token
        self._override_renew_secs[control_id] = grant.renew_secs
        # F-2: recompute from ALL held grants, not just this one — the shared
        # timer must renew on the tightest cadence held.
        self._recompute_renew_interval()
        if not self._override_renew_timer.isActive():
            self._override_renew_timer.start()
        # P2-1: reflect the value the daemon actually applied (floor/thermal-
        # clamped) onto the card so its slider/label can't claim a speed the fan
        # isn't running. With the slider already floor-clamped this usually equals
        # the request; it corrects any residual daemon clamp.
        card = self._control_cards.get(control_id)
        if card is not None:
            card.reflect_manual_applied(grant.pwm_percent)

    def _release_override(self, control_id: str) -> None:
        """Release a held override; the daemon reverts the control to its curve."""
        self._manual_intent.discard(control_id)
        self._override_pending.pop(control_id, None)
        token = self._overrides.pop(control_id, None)
        self._override_renew_secs.pop(control_id, None)
        self._renew_in_flight.discard(control_id)
        if not self._overrides:
            self._override_renew_timer.stop()
        if token is None or self._override_worker is None:
            # No confirmed token yet (take still in flight) — clearing the intent
            # above makes _on_take_result release the grant when it arrives.
            return
        self._request_release.emit(control_id, token)

    def _release_all_overrides(self) -> None:
        """Release every held override (e.g. before the card grid rebuilds) so
        card state never diverges from the daemon."""
        for control_id in list(self._overrides):
            self._release_override(control_id)
        # Also drop intent for controls whose take is still in flight, so their
        # grant is orphan-released by _on_take_result instead of being kept.
        self._manual_intent.clear()

    def _renew_overrides(self) -> None:
        """Dispatch a renew for every held override off-thread (DEC-220). A
        rejected renew (see ``_on_renew_result``) means the override lapsed."""
        if self._override_worker is None or not self._overrides:
            self._override_renew_timer.stop()
            return
        for control_id, token in list(self._overrides.items()):
            if control_id in self._renew_in_flight:
                # CONC-4: the previous renew is still on the worker — skip
                # this cycle rather than queue a duplicate. Harmless today
                # (double-renew is idempotent) but keeps the worker queue
                # bounded under a stalled daemon.
                continue
            self._renew_in_flight.add(control_id)
            self._request_renew.emit(control_id, token)

    def _on_renew_result(
        self, control_id: str, sent_token: object, new_token: object, error: object
    ) -> None:
        """Main-thread handler for a completed override_renew (DEC-220).

        ``sent_token`` is the token this renew was issued for. If the currently
        held token no longer matches it, the control was re-pinned (a slider drag
        supersedes the prior token, DEC-163) while the renew was in flight — the
        daemon rejects the stale renew with ``stale_fencing_token``, but the newer
        take is valid, so the rejection is a self-inflicted race and must be
        ignored, never treated as a lapse (else the card reverts while the daemon
        keeps the fan pinned until the deadman)."""
        if self._is_shut_down:  # DEC-231: a result queued before cleanup()
            return
        self._renew_in_flight.discard(control_id)
        if error is not None:
            if self._overrides.get(control_id) != sent_token:
                # DEC-231: the held token changed while this renew was in flight —
                # a newer re-pin (renews next cycle) or a release. Either way it is
                # not a lapse of a still-held override, so ignore the rejection.
                self._log.debug(
                    "Ignoring stale renew rejection on %s "
                    "(held token changed — re-pinned or released)",
                    control_id,
                )
                return
            self._log.info("Override on %s lapsed (%s) — reverting card", control_id, error.code)
            self._overrides.pop(control_id, None)
            self._override_renew_secs.pop(control_id, None)
            self._override_pending.pop(control_id, None)
            self._manual_intent.discard(control_id)
            self._revert_card_manual(control_id)
            self._surface_override_rejection(control_id, error)
            if not self._overrides:
                self._override_renew_timer.stop()
            return
        # Only advance the token if the held one is still the one we renewed — a
        # concurrent re-pin may have installed a newer token we must not clobber.
        if self._overrides.get(control_id) == sent_token:
            self._overrides[control_id] = new_token
        elif control_id not in self._overrides:
            # The user released while this renew was in flight. The worker is
            # sequential, so the renew went out FIRST and its answer lands here
            # after we already forgot the control.
            #
            # DEFENSIVE, not a live bug fix: today the daemon's `renew_override`
            # returns the SAME token it was given (it only extends expires_at —
            # see control_override.rs), so the release we already queued carries a
            # token that is still current and succeeds. But nothing in the API
            # contract promises that; the moment renew rotates the token — the
            # usual shape for a fencing scheme — the queued release would carry a
            # superseded one, be rejected (409 stale_fencing_token, suppressed),
            # and the daemon would keep pinning the fan under a token we never
            # recorded, until the ~15 s deadman, while the card already shows
            # curve control. Releasing whatever token came back costs one
            # idempotent call and closes that door in advance. Mirrors the orphan
            # handling in ``_on_take_result``.
            self._log.debug(
                "Releasing orphaned override token on %s (released mid-renew)",
                control_id,
            )
            self._request_release.emit(control_id, new_token)

    def _flush_override_values(self) -> None:
        """Apply the latest debounced slider value as a re-pin (which supersedes
        the prior token) off-thread. Skips controls released mid-drag;
        ``_on_take_result`` applies the new grant (DEC-220)."""
        pending = dict(self._override_pending)
        self._override_pending.clear()
        if self._override_worker is None:
            return
        for control_id, pct in pending.items():
            if control_id not in self._manual_intent:
                continue
            self._request_take.emit(control_id, pct)

    def _revert_card_manual(self, control_id: str) -> None:
        """Visually exit Manual on a card whose override lapsed/failed, without
        re-emitting manual_toggled (which would try to release it again)."""
        card = self._control_cards.get(control_id)
        if card is not None:
            card.clear_manual()

    # ── Foreign-override reconcile from /status (DEC-169) ────────────────
    def _on_status_reconcile(self, status: DaemonStatus) -> None:
        """Reconcile daemon-held overrides from the 1 Hz poll.

        Display-only: an override this session did not create carries no fencing
        token on `/status`, so it can only be *shown* (read-only "External"
        chip), never renewed or released. GUI-owned overrides (`self._overrides`)
        belong to the renew timer and are skipped here, so the two authorities
        never collide. Idempotent — acts only on the per-poll delta.
        """
        if self._client is None:
            return
        # `_manual_intent` as well as `_overrides`, and the second half is not
        # redundant. `_take_override` records intent SYNCHRONOUSLY but the grant
        # only lands in `_overrides` when the worker returns — a queued
        # cross-thread hop in production (`_OVERRIDE_USE_THREAD`). A poll landing
        # inside that window would otherwise classify THIS session's own override
        # as foreign and stamp `_external_pct` from it, so releasing Manual then
        # painted an "External N%" chip for an override the user owns. Tests could
        # not catch it: `conftest` forces the worker inline, which closes the
        # window by construction — so the pin below sets intent without a grant.
        foreign = {
            entry.control_id: entry.pwm_percent
            for entry in status.overrides
            if entry.control_id not in self._overrides
            and entry.control_id not in self._manual_intent
        }
        # Adopt new / changed foreign overrides onto their cards.
        for control_id, pwm in foreign.items():
            card = self._control_cards.get(control_id)
            if card is None:
                # No card for this control (a different profile is loaded). It
                # will be picked up on the next poll after a grid rebuild.
                continue
            if self._external_overrides.get(control_id) != pwm:
                card.set_external_override(pwm)
                self._external_overrides[control_id] = pwm
        # Drop tracked ones the daemon no longer reports (expired / released /
        # taken over by the user — which moves them into `self._overrides`).
        for control_id in list(self._external_overrides):
            if control_id not in foreign:
                self._clear_external_override(control_id)

        # 273-i: controls the daemon's engine cannot resolve, so is commanding
        # nothing. Same delta shape as the block above. Display-only — the GUI
        # never writes PWM (DEC-165), so there is nothing to do about it here
        # beyond telling the user their fan is not being driven.
        # No `isinstance` guard here, and its absence is now the deliberate part
        # (277-h). This comprehension carried one while its sibling `overrides`
        # above did not, which closed one door of about six and made the asymmetry
        # read as intentional. The posture is decided once at the parse boundary
        # instead: `_filter_fields` coerces every identity field to `str`, so a
        # malformed wire id arrives here as an unmatchable string rather than an
        # unhashable list.
        skipped = {entry.control_id: entry.reason for entry in status.skipped_controls}
        for control_id, reason in skipped.items():
            card = self._control_cards.get(control_id)
            if card is None:
                continue
            if self._skipped_controls.get(control_id) != reason:
                card.set_skipped(reason)
                self._skipped_controls[control_id] = reason
        for control_id in list(self._skipped_controls):
            if control_id not in skipped:
                self._clear_skipped(control_id)

        self._apply_live_outputs(status)

    def _apply_live_outputs(self, status: DaemonStatus) -> None:
        """Drive the cards' output figure from the 1 Hz poll (277-k).

        Until daemon 2.22.0 published `control_outputs[]` there was no live feed
        at all: `update_control_outputs` had exactly one production caller, wired
        to `DemoController.outputs_changed`, so a live card's output label sat at
        "—" for the whole session and only ever changed during a curve edit. That
        is a real gap against `CLAUDE.md § UX standards → Controls` ("what are the
        fans doing?"), and it is why several comments and changelog lines used to
        describe demo-only behaviour as if it were live.

        The rendering path already existed and was already correct — sensor
        context, and the DEC-119 GPU-divergence suffix via `divergent_gpu_output`
        — so this is wiring, not new display logic. It also makes
        `ControlCard.set_output`'s `_skipped_reason` guard live rather than dead
        in live mode, which is what that guard was kept for.

        **An absent control is not a zero.** The daemon omits any control it did
        not evaluate — no profile, a listed skip, or the whole of a thermal event,
        where the daemon publishes no per-control output at all. A
        card with no entry is left alone rather than being told `0`, so it keeps
        showing whatever it last had rather than claiming the fans stopped.
        """
        outputs = {entry.control_id: entry.output_pct for entry in status.control_outputs}

        # Cards whose curve is on the editor workbench are showing a live
        # "Preview: N%" from `_update_card_previews`, recomputed on every drag.
        # The poll must not stamp over it — with a 1 Hz feed the preview would
        # otherwise survive less than a second per edit, silently degrading a
        # feature that predates this one. Held on the PAGE, which owns the editor,
        # rather than as lifecycle state on the card.
        # `_editing_curve_id`, NOT `self._curve_editor.get_curve()`. The editor's
        # `_curve` is assigned only in `__init__` and `set_curve` and is never
        # reset, so `get_curve()` keeps returning the last-edited curve long after
        # `_close_editor` has hidden the pane — which permanently exempted the
        # last-edited control from the live feed and froze it on a stale
        # "Preview: N%", including through a thermal event. That is the exact
        # contract violation this method's reset loop exists to prevent,
        # reintroduced for one control by the exemption meant to protect it.
        # `_close_editor` clears `_editing_curve_id` (via `_set_curve_editing`),
        # so it is the state that actually tracks "an editor is open on X".
        previewing = self._editing_curve_id

        # **Absence is not "keep the last value".** `docs/08` states that a client
        # "must not carry a previous value forward ... Render absence as
        # 'unknown' (the reference GUI's '—'), never as 0". A control is absent
        # whenever the daemon did not evaluate it — no profile, a listed skip, or
        # the whole of a thermal event, where the daemon
        # publishes no per-control output at all. Carrying "Now: 42%" through a thermal emergency
        # while the fans run at 100% is precisely what that clause forbids, so
        # every card the daemon did not report is reset here rather than left.
        for control_id, card in self._control_cards.items():
            if control_id in outputs:
                continue
            if card.control.mode == ControlMode.CURVE and card.control.curve_id == previewing:
                continue
            card.clear_output()

        if not outputs:
            return
        # Per-member duty for the GPU-divergence suffix comes from each fan's own
        # `last_commanded_pwm` — the control-wide figure above cannot express a
        # member sitting below it on a floor or a diverging GPU (DEC-119).
        member_outputs: dict[str, dict[str, float]] = {}
        if self._state is not None:
            by_fan = {
                fan.id: float(fan.last_commanded_pwm)
                for fan in self._state.fans
                if fan.last_commanded_pwm is not None
            }
            for control_id in outputs:
                card = self._control_cards.get(control_id)
                if card is None:
                    continue
                members = {
                    m.member_id: by_fan[m.member_id]
                    for m in card.control.members
                    if m.member_id in by_fan
                }
                if members:
                    member_outputs[control_id] = members
        if previewing is not None:
            outputs = {
                cid: pct
                for cid, pct in outputs.items()
                if not (
                    (card := self._control_cards.get(cid)) is not None
                    and card.control.mode == ControlMode.CURVE
                    and card.control.curve_id == previewing
                )
            }
        self.update_control_outputs(outputs, member_outputs)

    def _clear_external_override(self, control_id: str) -> None:
        """Stop tracking a foreign override and revert its card (DEC-169)."""
        self._external_overrides.pop(control_id, None)
        card = self._control_cards.get(control_id)
        if card is not None:
            card.clear_external_override()

    def _clear_skipped(self, control_id: str) -> None:
        """Stop tracking a skipped control and clear its chip (273-i)."""
        self._skipped_controls.pop(control_id, None)
        card = self._control_cards.get(control_id)
        if card is not None:
            card.clear_skipped()

    def _clear_all_skipped(self) -> None:
        """Clear every "Not controlled" chip (e.g. on daemon disconnect)."""
        for control_id in list(self._skipped_controls):
            self._clear_skipped(control_id)

    def _clear_all_external_overrides(self) -> None:
        """Revert every foreign-override card (e.g. on daemon disconnect)."""
        for control_id in list(self._external_overrides):
            self._clear_external_override(control_id)

    def _sensor_combo_label(self, s) -> str:
        """Curve-editor sensor-combo label — see
        :func:`controls_view.sensor_combo_label` (DEC-157)."""
        overrides = self._state.sensor_class_overrides if self._state else {}
        return sensor_combo_label(s, overrides)

    def _on_sensor_values_updated(self, sensors) -> None:
        """Called ~1Hz. Rebuild sensor dropdown only when the sensor list changes."""
        current_ids = {s.id for s in sensors}
        if current_ids != self._prev_sensor_ids:
            self._prev_sensor_ids = current_ids
            seen = set()
            items = []
            for s in sensors:
                # DEC-193: never offer a control-ineligible sensor (e.g. an
                # ath12k WiFi temp) as a curve source — it would strand the curve
                # the moment the radio goes down. Existing curves already bound to
                # one keep working and still show their live value below.
                if not s.control_eligible:
                    continue
                if s.id not in seen:
                    items.append((s.id, self._sensor_combo_label(s)))
                    seen.add(s.id)
            self._curve_editor.set_available_sensors(items)

        # Update the live current-temp marker — but only while the page is
        # visible (DEC-214): the marker recreates pyqtgraph items each tick, so
        # skipping it while hidden keeps the always-mounted editor free during
        # gaming. The cheap dropdown/label updates above still run.
        if self._page_visible:
            curve = self._curve_editor.get_curve()
            if curve and curve.sensor_id:
                for s in sensors:
                    if s.id == curve.sensor_id:
                        self._curve_editor.set_current_sensor_value(s.value_c)
                        break
                else:
                    self._curve_editor.set_current_sensor_value(None)
            elif sensors:
                self._curve_editor.set_current_sensor_value(sensors[0].value_c)

        # Update curve card sensor value labels (cheap — dict lookup per card)
        sensor_map = {s.id: (s.label, s.value_c) for s in sensors}
        for _curve_id, ccard in self._curve_cards.items():
            sid = ccard.curve.sensor_id
            if sid and sid in sensor_map:
                label, val = sensor_map[sid]
                ccard.update_sensor_display(label, val)
            elif sid:
                pretty = sid.split(":")[-1] if ":" in sid else sid
                ccard.update_sensor_display(pretty)

    def _on_fan_rpm_updated(self, fans) -> None:
        """Update control card RPM displays. Wired to fans_updated signal."""
        fan_map = {f.id: f for f in fans}
        for _control_id, card in self._control_cards.items():
            ctrl = card.control
            rpms = []
            for m in ctrl.members:
                fan = fan_map.get(m.member_id)
                if fan and fan.rpm is not None:
                    rpms.append(fan.rpm)
            if rpms:
                avg = sum(rpms) // len(rpms)
                card.set_rpm(f"{avg} RPM")
            else:
                card.set_rpm("")
            # DEC-214: per-member live RPM for the compact card member rows.
            card.set_member_rpms(member_rpm_map(ctrl, fan_map))

        # DEC-233: refresh the actionable "Unassigned Fans (N)" button.
        profile = self._get_current_profile()
        controls = profile.controls if profile else []
        self._unassigned_fan_ids = unassigned_fan_ids(fans, controls)
        self._update_unassigned_button()

    # ─── Unassigned Fans (DEC-233) ───────────────────────────────────

    def _update_unassigned_button(self) -> None:
        """Reflect the current unassigned-fan count on the button."""
        n = len(self._unassigned_fan_ids)
        self._unassigned_btn.setText(f"Unassigned Fans ({n})" if n else "All fans assigned")
        self._unassigned_btn.setEnabled(n > 0)

    def _on_unassigned_clicked(self) -> None:
        """Pop the unassigned-fans menu at the button (DEC-233)."""
        menu = self._build_unassigned_menu()
        if menu is None:
            return
        menu.exec(self._unassigned_btn.mapToGlobal(self._unassigned_btn.rect().topLeft()))

    def _build_unassigned_menu(self) -> QMenu | None:
        """Build the "Unassigned Fans" menu: list the fans no role controls,
        offering to add each writable one to an existing role (DEC-233).

        Read-only headers and GPU fans are listed but not offered for quick-assign
        — GPU fans have their own dedicated flow. Returns ``None`` when there is
        nothing to show. Split from :meth:`_on_unassigned_clicked` so the contents
        are testable without spinning a modal menu.
        """
        if self._state is None or not self._unassigned_fan_ids:
            return None
        profile = self._get_current_profile()
        controls = profile.controls if profile else []
        menu = QMenu(self)
        heading = menu.addAction("Fans not controlled by any role")
        heading.setEnabled(False)
        menu.addSeparator()
        for fan_id in self._unassigned_fan_ids:
            # Untrusted names → escape "&" so QMenu doesn't eat it as a mnemonic
            # (matches collapsible_section.py). Not a rich-text/DEC-231 concern —
            # menu text is never rendered as rich text.
            name = (self._state.fan_display_name(fan_id) or fan_id).replace("&", "&&")
            member = self._make_member_for_fan(fan_id)
            if member is None:
                # Not quick-assignable (read-only header, or a GPU fan).
                entry = menu.addAction(f"{name}  (read-only)")
                entry.setEnabled(False)
                continue
            if not controls:
                entry = menu.addAction(f"{name} — create a role to assign")
                entry.setEnabled(False)
                continue
            submenu = menu.addMenu(name)
            for ctrl in controls:
                submenu.addAction(
                    f'Add to "{ctrl.name.replace("&", "&&")}"',
                    lambda _checked=False, cid=ctrl.id, m=member: self._assign_member_to_control(
                        cid, m
                    ),
                )
        return menu

    def _make_member_for_fan(self, fan_id: str):
        """Build a ``ControlMember`` for a quick-assignable fan, else ``None``.

        Quick-assign covers writable hwmon + OpenFan fans only; read-only headers
        (DEC-102) and GPU fans (which use the dedicated zero-RPM flow) return
        ``None``. Mirrors the member-building in :meth:`_on_edit_members`,
        including the DEC-228/229 role-preserving label and the DEC-157 AIO tag.
        """
        if self._state is None:
            return None
        header_by_id = {h.id: h for h in self._state.hwmon_headers}
        fan = next((f for f in self._state.fans if f.id == fan_id), None)
        header = header_by_id.get(fan_id)
        source = fan.source if fan is not None else ("hwmon" if header is not None else None)
        if source is None or source in ("amd_gpu", "intel_gpu", "nvidia_gpu"):
            return None
        if source == "hwmon" and (header is None or not header.is_writable):
            return None
        from control_ofc.services.profile_service import ControlMember

        label = self._state.fan_display_name(fan_id) or fan_id
        fallback = self._state.fan_fallback_name(fan_id) if header is not None else ""
        clean_label = _role_preserving_label(label, fallback, source)
        # DEC-157/233: mirror _on_edit_members — an AIO header's role lives in this
        # tag when its chip/label carry no cpu/pump/aio keyword, so quick-assign
        # must keep it or the pump falls from the 30% floor to the 20% one (the
        # daemon mirrors this label — DEC-162).
        if header is not None and header.is_aio:
            clean_label += aio_tag_for(label)
        return ControlMember(source=source, member_id=fan_id, member_label=clean_label)

    def _assign_member_to_control(self, control_id: str, member) -> None:
        """Append a fan to an existing role from the Unassigned quick-assign menu."""
        profile = self._get_current_profile()
        if not profile:
            return
        control = next((c for c in profile.controls if c.id == control_id), None)
        if control is None:
            return
        if any(m.member_id == member.member_id for m in control.members):
            return  # already a member (raced with another assign)
        control.members.append(member)
        # Membership can shift the role (chassis ↔ CPU/pump), so reapply the
        # role-aware floor before the next save (mirrors _on_edit_members).
        if apply_role_floor(control):
            self._log.info(
                "Control '%s' minimum_pct raised to %.0f%% by role policy",
                control.name,
                control.minimum_pct,
            )
        card = self._control_cards.get(control_id)
        if card:
            card.update_control(control, profile.curves)
        self._set_unsaved(True)
        # Drop the just-assigned fan from the unassigned set + button immediately
        # (the next poll would too, but this keeps the menu/count in step now).
        if member.member_id in self._unassigned_fan_ids:
            self._unassigned_fan_ids = [
                fid for fid in self._unassigned_fan_ids if fid != member.member_id
            ]
            self._update_unassigned_button()
