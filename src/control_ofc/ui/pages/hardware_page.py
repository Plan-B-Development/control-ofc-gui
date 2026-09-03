"""Hardware page — readiness, cooling hardware, and diagnostics (DEC-212, DEC-318).

A thin renderer over the Qt-free ``services.hardware_view`` view-models (which
reuse the existing ``ui/cooling_readiness`` mapping), styled with the Stage-1
components. Presents the readiness checklist (verdict + grouped PASS/WARN rows —
no fabricated score), the recommended-action cards (with routed action buttons),
and the Super-I/O table (real per-chip columns only, with a per-chip "How to
enable" + copy-command) + the opt-in port probe.

Owns its own ``_HardwareReadinessWorker`` (fetch/refresh/probe) — the same
``_SocketWorker`` class the old tab uses. Presentation-only: no daemon/API/schema/
control/safety change. Action deep-links are re-pointed to the already-migrated
pages (System State / Overview / Settings). The old ``CoolingReadinessView`` +
Diagnostics Readiness tab are left untouched.

**AIO-MB Phase 6 (DEC-318)** adds two sections between Recommended Actions and
Super-I/O — *Cooling Hardware* (cooling-device assemblies + per-header cards) and
*Hardware Diagnostics* (PWM test, characterisation, lifecycle recording,
validation sessions) — making this the primary GUI entry point for PWM testing
(§7). Nothing existing is removed and System State keeps its own controls; both
pages launch the **same** implementation, which is why the verify wording lives
in ``services/verify_view`` and the workers/dialog are reused verbatim rather
than reimplemented (§21).

Still presentation-only. **Nothing on this page writes PWM.** Every active test
goes through a daemon diagnostic endpoint that already owns the hwmon lease, the
pump floor clamp, the thermal refusal and restore-on-drop, so the page cannot
lower a floor or stop a pump even by accident (§23).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.cooling_device_view import build_cooling_device_views
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.hardware_view import (
    build_checklist,
    build_readiness_summary,
    build_recommended_actions,
    build_superio_panel,
)
from control_ofc.services.header_inspector_view import build_header_inspector_views
from control_ofc.services.profile_service import ProfileService
from control_ofc.services.pump_protection import header_is_pump_protected
from control_ofc.services.verify_view import build_verify_result_view
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.cooling_readiness import build_readiness_items
from control_ofc.ui.pages.diagnostics_workers import (
    _CharacterizationWorker,
    _HardwareReadinessWorker,
    _ValidationWorker,
    _VerifyWorker,
)
from control_ofc.ui.readiness_merge import ACTION_NONE
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.cooling_device_card import CoolingDeviceCard
from control_ofc.ui.widgets.flow_layout import FlowLayout
from control_ofc.ui.widgets.pwm_characterization_dialog import PwmCharacterizationDialog
from control_ofc.ui.widgets.pwm_header_card import PwmHeaderCard
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from control_ofc.api.client import DaemonClient
    from control_ofc.api.models import (
        HardwareReadiness,
        HwmonVerifyResult,
        SuperIoReport,
    )
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

_SUPERIO_COLS = ["Chip", "Vendor", "Driver", "Module loaded", "Confidence", "Health", "Notes"]
_SIO_HEALTH = 5


class HardwarePage(QWidget):
    """The migrated Cooling Hardware Readiness content as a standalone page."""

    # Cross-page deep-links (routed by main_window).
    open_preferred_sensors = Signal(str)  # "cpu" | "mb"
    open_system_state = Signal()  # advanced troubleshooting shortcut
    open_overview = Signal()  # sensors
    #: "Edit Configuration" on a cooling-device card. The AIO configuration
    #: workflow lives on the Controls page and is REUSED, never duplicated here
    #: (§2: "do not duplicate profile configuration logic inside the Hardware
    #: page"; §21: reuse the existing AIO configuration workflow).
    open_controls = Signal()

    # Main-thread → worker-thread requests (queued).
    _readiness_request = Signal()
    _readiness_refresh_request = Signal()
    _readiness_probe_request = Signal()
    _verify_request = Signal(str)
    _char_start_request = Signal(str, object, object)
    _char_poll_request = Signal()
    _char_cancel_request = Signal()
    _validation_start_request = Signal(str, str, list, list, dict)
    _validation_poll_request = Signal()
    _validation_stop_request = Signal()
    _validation_cancel_request = Signal()
    _validation_marker_request = Signal(str, str)
    _validation_measurement_request = Signal(str, float, str, str, str)

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        client: DaemonClient | None = None,
        profile_service: ProfileService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Hardware_Root")
        self._state = state
        self._diag = diagnostics_service or DiagnosticsService(state)
        self._client = client
        # Read-only, and deliberately NOT defaulted to a fresh `ProfileService()`:
        # constructing one loads profiles from disk, which this page has no
        # business doing. `None` means "no profile information", and the pump
        # strategy degrades honestly to "Not controlled" rather than guessing.
        self._profile_service = profile_service

        self._readiness_thread: QThread | None = None
        self._readiness_worker: _HardwareReadinessWorker | None = None
        self._readiness_auto_fetched = False
        self._readiness_unsupported = False
        self._last_report: HardwareReadiness | None = None

        # AIO-MB Phase 6 workers. Each is created lazily by `_ensure_worker`
        # and torn down in `cleanup`, exactly like the readiness worker.
        self._verify_thread: QThread | None = None
        self._verify_worker: _VerifyWorker | None = None
        self._char_thread: QThread | None = None
        self._char_worker: _CharacterizationWorker | None = None
        self._char_dialog: PwmCharacterizationDialog | None = None
        self._validation_thread: QThread | None = None
        self._validation_worker: _ValidationWorker | None = None
        self._validation_dialog: ValidationSessionDialog | None = None

        # Live card registries, keyed by id so a poll updates in place rather
        # than rebuilding the section (which would drop scroll position and any
        # open "Details" disclosure on every tick).
        self._device_cards: dict[str, CoolingDeviceCard] = {}
        self._header_cards: dict[str, PwmHeaderCard] = {}

        self._build_ui()

        # Live data comes entirely from signals AppState already emits — no new
        # polling is introduced for any dynamic field (§19). Topology arrives on
        # the capabilities interval, not the 1 Hz poll.
        if self._state is not None:
            self._state.fans_updated.connect(self._refresh_cooling_section)
            self._state.headers_updated.connect(self._refresh_cooling_section)
            self._state.capabilities_updated.connect(self._refresh_cooling_section)
            self._state.cooling_devices_updated.connect(self._refresh_cooling_section)
        # The pump strategy is derived from the active profile's curve, so it
        # must re-render when that changes — a poll tick alone would leave the
        # line stale until the next fan update happened to arrive.
        if self._profile_service is not None:
            self._profile_service.active_changed.connect(self._refresh_cooling_section)
            self._profile_service.profiles_changed.connect(self._refresh_cooling_section)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._scroll.setWidget(body)

        # Header.
        header_row = QHBoxLayout()
        head = QVBoxLayout()
        title = QLabel("Hardware")
        title.setObjectName("Hardware_Label_title")
        title.setProperty("class", "PageTitle")
        head.addWidget(title)
        subtitle = QLabel("Cooling hardware · readiness · diagnostics · Super-I/O architecture")
        subtitle.setObjectName("Hardware_Label_subtitle")
        subtitle.setProperty("class", "PageSubtitle")
        head.addWidget(subtitle)
        header_row.addLayout(head)
        header_row.addStretch(1)
        self._refresh_btn = make_button("Re-scan", "secondary", object_name="Hardware_Btn_refresh")
        self._refresh_btn.clicked.connect(self._refresh_readiness)
        header_row.addWidget(self._refresh_btn)
        layout.addLayout(header_row)

        self._status_label = QLabel("")
        self._status_label.setObjectName("Hardware_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # Hero grid.
        hero = QHBoxLayout()
        hero.setSpacing(12)
        hero.addWidget(self._build_checklist_card(), 1)
        hero.addWidget(self._build_actions_card(), 1)
        layout.addLayout(hero)

        # Cooling Hardware + Hardware Diagnostics (AIO-MB Phase 6). Inserted
        # between the existing hero grid and Super-I/O, per the brief's page
        # structure; nothing above or below is disturbed.
        layout.addWidget(self._build_cooling_card())
        layout.addWidget(self._build_diagnostics_card())

        # Super-I/O.
        layout.addWidget(self._build_superio_card())
        layout.addStretch(1)

    def _build_checklist_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_checklist")
        v = QVBoxLayout(card)
        header = SectionHeader(
            "System Readiness Checklist", object_name="Hardware_SectionHeader_checklist"
        )
        self._verdict_pill = StatusPill("—", "neutral")
        self._verdict_pill.setObjectName("Hardware_Pill_verdict")
        header.add_trailing(self._verdict_pill)
        v.addWidget(header)

        self._top_label = QLabel("")
        self._top_label.setObjectName("Hardware_Label_topStep")
        self._top_label.setProperty("class", "CardMeta")
        self._top_label.setWordWrap(True)
        self._top_label.setVisible(False)
        v.addWidget(self._top_label)

        self._checklist_container = QWidget()
        self._checklist_layout = QVBoxLayout(self._checklist_container)
        self._checklist_layout.setContentsMargins(0, 0, 0, 0)
        self._checklist_layout.setSpacing(6)
        v.addWidget(self._checklist_container)

        self._scanned_label = QLabel("")
        self._scanned_label.setObjectName("Hardware_Label_scanned")
        self._scanned_label.setProperty("class", "SmallLabel")
        self._scanned_label.setWordWrap(True)
        v.addWidget(self._scanned_label)
        v.addStretch(1)
        return card

    def _build_actions_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_actions")
        v = QVBoxLayout(card)
        header = SectionHeader("Recommended Actions", object_name="Hardware_SectionHeader_actions")
        self._action_count_label = QLabel("—")
        self._action_count_label.setObjectName("Hardware_Label_actionCount")
        self._action_count_label.setProperty("class", "CardMeta")
        header.add_trailing(self._action_count_label)
        v.addWidget(header)

        self._actions_container = QWidget()
        self._actions_layout = QVBoxLayout(self._actions_container)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(8)
        v.addWidget(self._actions_container)

        self._summary_bar = QWidget()
        self._summary_bar.setObjectName("Hardware_Bar_summary")
        self._summary_bar_layout = QHBoxLayout(self._summary_bar)
        self._summary_bar_layout.setContentsMargins(0, 6, 0, 0)
        self._summary_bar_layout.setSpacing(8)
        v.addWidget(self._summary_bar)
        v.addStretch(1)
        return card

    def _build_superio_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_superio")
        self._superio_card = card
        v = QVBoxLayout(card)
        v.addWidget(
            SectionHeader("Super-I/O Architecture", object_name="Hardware_SectionHeader_superio")
        )
        self._superio_container = QWidget()
        self._superio_layout = QVBoxLayout(self._superio_container)
        self._superio_layout.setContentsMargins(0, 0, 0, 0)
        self._superio_layout.setSpacing(8)
        v.addWidget(self._superio_container)
        return card

    # ── Cooling Hardware + Diagnostics (AIO-MB Phase 6) ──────────────

    def _build_cooling_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_cooling")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        header = SectionHeader("Cooling Hardware", card, object_name="Hardware_Header_cooling")
        self._cooling_count = QLabel("", card)
        self._cooling_count.setObjectName("Hardware_Label_coolingCount")
        self._cooling_count.setProperty("class", "CardMeta")
        header.add_trailing(self._cooling_count)
        v.addWidget(header)

        # Cooling devices (assemblies). Absent on a machine with no configured
        # topology, which is normal and not an error (§20).
        self._device_container = QWidget(card)
        self._device_layout = QVBoxLayout(self._device_container)
        self._device_layout.setContentsMargins(0, 0, 0, 0)
        self._device_layout.setSpacing(10)
        v.addWidget(self._device_container)

        headers_label = QLabel("PWM Headers", card)
        headers_label.setObjectName("Hardware_Label_headersHeading")
        headers_label.setProperty("class", "CardMeta")
        v.addWidget(headers_label)

        # A flow layout, not a splitter: cards wrap to the available width, so
        # the section never forces the window wider. DEC-315 made the app's
        # minimum track its widest page, and a fixed multi-column grid here
        # would raise that minimum for every user.
        self._header_container = QWidget(card)
        self._header_flow = FlowLayout(self._header_container, margin=0, h_spacing=10, v_spacing=10)
        v.addWidget(self._header_container)

        self._cooling_empty = QLabel("", card)
        self._cooling_empty.setObjectName("Hardware_Label_coolingEmpty")
        self._cooling_empty.setProperty("class", "CardMeta")
        self._cooling_empty.setWordWrap(True)
        self._cooling_empty.setVisible(False)
        v.addWidget(self._cooling_empty)

        self._cooling_card = card
        self._refresh_cooling_section()
        return card

    def _build_diagnostics_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_diagnostics")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        v.addWidget(
            SectionHeader("Hardware Diagnostics", card, object_name="Hardware_Header_diagnostics")
        )

        intro = QLabel(
            "Active tests run through the daemon, which keeps the hwmon lease, "
            "the pump safety floor and thermal protection in force throughout. "
            "A pump is never stopped and no fan is driven below its floor.",
            card,
        )
        intro.setObjectName("Hardware_Label_diagnosticsIntro")
        intro.setWordWrap(True)
        v.addWidget(intro)

        self._diag_result = QLabel("", card)
        self._diag_result.setObjectName("Hardware_Label_diagResult")
        self._diag_result.setWordWrap(True)
        self._diag_result.setVisible(False)
        v.addWidget(self._diag_result)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._lifecycle_btn = make_button(
            "Startup / Lifecycle Recording",
            "secondary",
            object_name="Hardware_Btn_lifecycle",
            accessible_name="Record startup and lifecycle behaviour",
        )
        self._lifecycle_btn.clicked.connect(lambda: self._open_validation(lifecycle=True))
        actions.addWidget(self._lifecycle_btn)

        self._validation_btn = make_button(
            "AIO Validation",
            "secondary",
            object_name="Hardware_Btn_validation",
            accessible_name="Start an AIO validation session",
        )
        self._validation_btn.clicked.connect(lambda: self._open_validation(lifecycle=False))
        actions.addWidget(self._validation_btn)

        self._advanced_btn = make_button(
            "Advanced (System State)",
            "ghost",
            object_name="Hardware_Btn_advanced",
            accessible_name="Open the advanced troubleshooting shortcut on System State",
        )
        self._advanced_btn.clicked.connect(self.open_system_state.emit)
        actions.addWidget(self._advanced_btn)
        actions.addStretch(1)
        v.addLayout(actions)

        self._diagnostics_card = card
        self._sync_diagnostic_enablement()
        return card

    # ── Cooling-section rendering ────────────────────────────────────

    def _capabilities(self):
        return self._state.capabilities if self._state else None

    def _sensor_labels(self) -> dict[str, str]:
        return {s.id: (s.label or s.id) for s in (self._state.sensors if self._state else [])}

    def _sensor_values(self) -> dict[str, float]:
        return {
            s.id: s.value_c
            for s in (self._state.sensors if self._state else [])
            if s.value_c is not None
        }

    def _revert_counts(self) -> dict[str, int]:
        # Already fetched by the poll worker as part of `/diagnostics/hardware`
        # — read, never re-requested (§19).
        diag = getattr(self._diag, "last_hw_diagnostics", None)
        return dict(getattr(getattr(diag, "hwmon", None), "enable_revert_counts", {}) or {})

    def _refresh_cooling_section(self, *_args) -> None:
        """Re-render the cooling section from current AppState.

        Cards are updated in place and only created/destroyed when the hardware
        set actually changes, so an open "Details" disclosure survives a poll.
        """
        if not hasattr(self, "_header_flow"):
            return  # called from __init__ before the section exists
        state = self._state
        headers = list(state.hwmon_headers) if state else []
        readings = list(state.fans) if state else []
        caps = self._capabilities()

        inventory = getattr(state, "cooling_devices", None) if state else None
        devices = list(inventory.cooling_devices) if inventory else []

        device_views = build_cooling_device_views(
            devices,
            headers=headers,
            capabilities=caps,
            display_name=(state.member_display_name if state else None),
            sensor_labels=self._sensor_labels(),
            readings=readings,
            sensor_values=self._sensor_values(),
            profile=self._active_profile(),
        )
        self._sync_device_cards(device_views)

        header_views = build_header_inspector_views(
            headers,
            readings=readings,
            capabilities=caps,
            display_names={
                h.id: (state.member_display_name(h.id) if state else "") for h in headers
            },
            enable_revert_counts=self._revert_counts(),
        )
        self._sync_header_cards(header_views)

        self._cooling_count.setText(
            f"{len(device_views)} device(s) · {len(header_views)} PWM header(s)"
        )
        if not header_views and not device_views:
            # §20: a concise explanatory empty state, and the rest of the page
            # (readiness, Super-I/O) stays fully available.
            self._cooling_empty.setText(
                "No PWM cooling headers were discovered. The readiness checklist "
                "above explains why, and Super-I/O detection below shows which "
                "sensor chip your board needs."
            )
            self._cooling_empty.setVisible(True)
        elif not device_views:
            # §20: a pump with no topology is a normal, configurable state — not
            # a broken one. Point at the workflow instead of raising a warning.
            self._cooling_empty.setText(
                "No cooling device is configured. Headers are shown individually "
                "below; use “Configure AIO…” on the Controls page to group a "
                "pump and its radiator fans into one device."
            )
            self._cooling_empty.setVisible(True)
        else:
            self._cooling_empty.setVisible(False)

        self._sync_diagnostic_enablement()

    def _active_profile(self):
        """The active profile object, for the DERIVED pump strategy (§2).

        It comes from `ProfileService`, which is the only thing that holds a
        `Profile`. **`AppState` does not** — it carries `active_profile_name` and
        `active_profile_id`, both strings — so the first version of this method
        read `getattr(self._state, "active_profile", None)` and returned `None`
        on every call, pinning the strategy line to "Not controlled" for every
        user while the unit tests on `pump_strategy_text` stayed green. That is
        CLAUDE.md's most-repeated failure exactly: *extracting a rule into a
        testable function does not test the call site.* Caught in review; the
        call site is now covered by a test that renders the card.

        Still degrades honestly: no service, or no active profile, means
        "Not controlled" rather than a guess.
        """
        return self._profile_service.active_profile if self._profile_service else None

    def _sync_device_cards(self, views) -> None:
        seen = set()
        for index, view in enumerate(views):
            seen.add(view.device_id)
            card = self._device_cards.get(view.device_id)
            if card is None:
                card = CoolingDeviceCard(view, self._device_container)
                card.view_headers_requested.connect(self._scroll_to_headers)
                card.characterize_pump_requested.connect(self._open_characterization)
                card.start_validation_requested.connect(
                    lambda device_id: self._open_validation(lifecycle=False, device_id=device_id)
                )
                card.edit_requested.connect(lambda _id: self.open_controls.emit())
                card.forget_requested.connect(self._forget_device)
                self._device_cards[view.device_id] = card
                self._device_layout.insertWidget(index, card)
            else:
                card.set_view(view)
        for device_id in list(self._device_cards):
            if device_id not in seen:
                card = self._device_cards.pop(device_id)
                card.setParent(None)
                card.deleteLater()

    def _sync_header_cards(self, views) -> None:
        seen = set()
        for index, view in enumerate(views):
            seen.add(view.header_id)
            card = self._header_cards.get(view.header_id)
            if card is None:
                card = PwmHeaderCard(view, self._header_container)
                card.test_requested.connect(self._run_pwm_verify)
                card.characterize_requested.connect(self._open_characterization)
                self._header_cards[view.header_id] = card
                self._header_flow.insertWidget(index, card)
            else:
                card.set_view(view)
        for header_id in list(self._header_cards):
            if header_id not in seen:
                card = self._header_cards.pop(header_id)
                self._header_flow.removeWidget(card)
                card.setParent(None)
                card.deleteLater()

    def _scroll_to_headers(self, *_args) -> None:
        self._scroll.ensureWidgetVisible(self._header_container)

    def _sync_diagnostic_enablement(self) -> None:
        """Enable each action only where the capability actually exists (§11)."""
        if not hasattr(self, "_validation_btn"):
            return
        caps = self._capabilities()
        supported = bool(getattr(getattr(caps, "control", None), "validation_sessions", False))
        has_device = bool(self._device_cards)
        enabled = supported and has_device
        for button in (self._validation_btn, self._lifecycle_btn):
            button.setEnabled(enabled)
            if not supported:
                button.setToolTip("This daemon does not support validation sessions.")
            elif not has_device:
                button.setToolTip(
                    "Configure a cooling device first — a session records one named assembly."
                )
            else:
                button.setToolTip("")

    # ── Fetch + render ───────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._readiness_unsupported and not self._readiness_auto_fetched:
            self._readiness_auto_fetched = True  # latch BEFORE emit → no double-fetch
            self._fetch_readiness()

    def _fetch_readiness(self, *, force: bool = False) -> None:
        if self._readiness_unsupported:
            self._set_status(
                "Hardware readiness is unavailable — the daemon predates this feature."
            )
            return
        if not self._client:
            self._set_status("Cannot fetch readiness: no daemon connection")
            return
        if not self._ensure_readiness_worker():
            self._set_status("Cannot fetch readiness: no daemon socket path")
            return
        self._set_status(
            "Refreshing hardware assessment…" if force else "Fetching hardware readiness…"
        )
        (self._readiness_refresh_request if force else self._readiness_request).emit()

    def _refresh_readiness(self) -> None:
        self._fetch_readiness(force=True)

    @Slot(object)
    def _on_readiness_ok(self, result: HardwareReadiness) -> None:
        self._last_report = result
        self._status_label.setVisible(False)
        self._render(result)

    @Slot(str, str)
    def _on_readiness_error(self, category: str, message: str) -> None:
        if category == "unsupported":
            self._readiness_unsupported = True
            self._set_status(
                "Hardware readiness is unavailable — the daemon predates this feature."
            )
        else:
            self._set_status(f"Cannot fetch readiness: {message}")

    @Slot(object)
    def _on_readiness_probe_ok(self, result: SuperIoReport) -> None:
        # The probe enriches only the Super-I/O half — re-render that section.
        if self._last_report is not None:
            self._last_report.superio = result
        self._render_superio(build_superio_panel(result))

    @Slot(str, str)
    def _on_readiness_probe_error(self, _category: str, message: str) -> None:
        self._set_status(message)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(True)

    def _render(self, hw: HardwareReadiness) -> None:
        self._last_report = hw
        summary = build_readiness_summary(hw)
        items = build_readiness_items(hw)
        self._verdict_pill.set_text(summary.verdict_word)
        self._verdict_pill.set_state(summary.verdict_state)
        self._top_label.setText(summary.top_summary_line)
        self._top_label.setVisible(bool(summary.top_summary_line))
        self._scanned_label.setText(summary.scanned_age_line)
        self._render_checklist(build_checklist(items))
        self._render_actions(build_recommended_actions(items), summary)
        self._render_superio(build_superio_panel(hw.superio))

    def _render_checklist(self, groups) -> None:
        _clear_layout(self._checklist_layout)
        if not groups:
            empty = QLabel("No hardware checks reported.")
            empty.setObjectName("Hardware_Label_checksEmpty")
            empty.setProperty("class", "CardMeta")
            self._checklist_layout.addWidget(empty)
            return
        theme = active_theme()
        for group in groups:
            head = QLabel(group.name)
            head.setProperty("class", "SmallLabel")
            head.setStyleSheet(f"color: {theme.text_secondary}; font-weight: 600;")
            self._checklist_layout.addWidget(head)
            for row in group.rows:
                self._checklist_layout.addWidget(self._make_check_row(row))

    def _make_check_row(self, vm) -> QWidget:
        holder = QWidget()
        holder.setObjectName(f"Hardware_Check_{vm.code}")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(8)
        glyph = QLabel(vm.glyph)
        glyph.setStyleSheet(f"color: {_state_color(vm.severity_state, active_theme())};")
        line.addWidget(glyph)
        title = QLabel(vm.title)
        title.setWordWrap(True)
        line.addWidget(title, 1)
        pill = StatusPill(vm.badge_word, vm.severity_state)
        pill.setObjectName(f"Hardware_CheckBadge_{vm.code}")
        line.addWidget(pill)
        col.addLayout(line)
        if vm.expandable:
            section = CollapsibleSection(
                "Details", f"Hardware_CheckDetail_{vm.code}", expanded=False
            )
            detail = QLabel(vm.detail)
            detail.setProperty("class", "CardMeta")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(detail)
            col.addWidget(section)
        return holder

    def _render_actions(self, actions, summary) -> None:
        self._action_count_label.setText(f"{len(actions)} items · {summary.crit_count} critical")
        _clear_layout(self._actions_layout)
        if not actions:
            ok = QLabel("No recommended actions — hardware looks ready.")
            ok.setObjectName("Hardware_Label_actionsEmpty")
            ok.setProperty("class", "CardMeta")
            self._actions_layout.addWidget(ok)
        else:
            for vm in actions:
                self._actions_layout.addWidget(self._make_action_card(vm))
        _clear_layout(self._summary_bar_layout)
        for seg in summary.segments:
            self._summary_bar_layout.addWidget(
                StatusPill(
                    f"{seg.count} {seg.label}",
                    seg.state,
                    object_name=f"Hardware_Pill_summary_{seg.label}",
                )
            )
        self._summary_bar_layout.addStretch(1)

    def _make_action_card(self, vm) -> QWidget:
        card = QFrame()
        card.setObjectName(f"Hardware_Action_{vm.code}")
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        badge = StatusPill(vm.badge_word, vm.severity_state)
        badge.setObjectName(f"Hardware_Badge_{vm.code}")
        top.addWidget(badge)
        headline = QLabel(vm.headline)
        headline.setObjectName(f"Hardware_Headline_{vm.code}")
        headline.setWordWrap(True)
        headline.setTextFormat(Qt.TextFormat.PlainText)
        headline.setStyleSheet("font-weight: 600;")
        top.addWidget(headline, 1)
        if vm.component:
            comp = QLabel(vm.component)
            comp.setProperty("class", "SmallLabel")
            top.addWidget(comp)
        col.addLayout(top)

        if vm.impact_chips:
            chip_row = QHBoxLayout()
            chip_row.setSpacing(6)
            for chip in vm.impact_chips:
                chip_row.addWidget(
                    StatusPill(
                        chip.label,
                        chip.state,
                        object_name=f"Hardware_Pill_chip_{vm.code}_{chip.label}",
                    )
                )
            chip_row.addStretch(1)
            col.addLayout(chip_row)

        if vm.plain_detail:
            detail = QLabel(vm.plain_detail)
            detail.setProperty("class", "CardMeta")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(detail)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        if vm.action_kind != ACTION_NONE and vm.action_label:
            act = make_button(vm.action_label, "secondary", object_name=f"Hardware_Do_{vm.code}")
            act.clicked.connect(lambda _=False, t=vm.action_target: self._route_action(t))
            btn_row.addWidget(act)
        if vm.doc_url:
            doc = make_button(
                f"{vm.doc_title or 'Learn how'} ↗", "ghost", object_name=f"Hardware_Doc_{vm.code}"
            )
            doc.clicked.connect(lambda _=False, url=vm.doc_url: QDesktopServices.openUrl(QUrl(url)))
            btn_row.addWidget(doc)
        btn_row.addStretch(1)
        col.addLayout(btn_row)
        return card

    def _render_superio(self, panel) -> None:
        _clear_layout(self._superio_layout)
        if not panel.arch_supported:
            self._superio_layout.addWidget(
                _note_label(panel.arch_note, "Hardware_Label_superioNote")
            )
            return
        if not panel.has_chips:
            self._superio_layout.addWidget(
                _note_label(panel.empty_note, "Hardware_Label_superioNote")
            )
            if panel.notes_text:
                self._superio_layout.addWidget(
                    _note_label(panel.notes_text, "Hardware_Label_superioNotes")
                )
            self._superio_layout.addWidget(self._build_advanced(panel))
            return

        summary_pill = StatusPill(panel.summary_text, panel.summary_state)
        summary_pill.setObjectName("Hardware_Pill_superioSummary")
        holder = QWidget()
        holder_l = QHBoxLayout(holder)
        holder_l.setContentsMargins(0, 0, 0, 0)
        holder_l.addWidget(summary_pill)
        holder_l.addStretch(1)
        self._superio_layout.addWidget(holder)

        table = QTableWidget(len(panel.rows), len(_SUPERIO_COLS))
        table.setObjectName("Hardware_Table_superio")
        table.setHorizontalHeaderLabels(_SUPERIO_COLS)
        apply_dense_table(table)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Health holds a pill cell-widget: ResizeToContents measures the (empty)
        # item, not the widget, so pin a fixed width wide enough for the pill.
        header.setSectionResizeMode(_SIO_HEALTH, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_SIO_HEALTH, 112)
        header.setStretchLastSection(True)
        for i, row in enumerate(panel.rows):
            _ensure_items(table, i, len(_SUPERIO_COLS))
            table.item(i, 0).setText(row.chip)
            table.item(i, 1).setText(row.vendor)
            table.item(i, 2).setText(row.driver_text)
            table.item(i, 3).setText(row.module_text)
            table.item(i, 4).setText(row.confidence)
            table.item(i, 6).setText(row.notes)
            _set_pill(table, i, _SIO_HEALTH, row.health_word, row.health_state)
        self._superio_layout.addWidget(table)

        for row in panel.rows:
            if row.has_recommendation:
                self._superio_layout.addWidget(self._make_chip_detail(row))

        if panel.show_liability:
            self._superio_layout.addWidget(
                _note_label(panel.liability_text, "Hardware_Label_superioLiability")
            )
        if panel.notes_text:
            self._superio_layout.addWidget(
                _note_label(panel.notes_text, "Hardware_Label_superioNotes")
            )
        self._superio_layout.addWidget(self._build_advanced(panel))

    def _make_chip_detail(self, row) -> QWidget:
        section = CollapsibleSection(
            f"How to enable — {row.chip}", f"Hardware_ChipHow_{row.chip}", expanded=False
        )
        if row.reason:
            reason = QLabel(row.reason)
            reason.setProperty("class", "CardMeta")
            reason.setWordWrap(True)
            reason.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(reason)
        if row.copy_command:
            cmd_holder = QWidget()
            cl = QHBoxLayout(cmd_holder)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(8)
            mono = QLabel(row.copy_command)
            mono.setProperty("class", "MonoCommand")
            mono.setTextFormat(Qt.TextFormat.PlainText)
            mono.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cl.addWidget(mono, 1)
            copy = make_button("Copy command", "ghost", object_name=f"Hardware_CmdCopy_{row.chip}")
            copy.clicked.connect(lambda _=False, c=row.copy_command: _copy_to_clipboard(c))
            cl.addWidget(copy)
            section.add_widget(cmd_holder)
        if row.mainline_text:
            ml = StatusPill(
                row.mainline_text,
                row.mainline_state,
                object_name=f"Hardware_Pill_mainline_{row.chip}",
            )
            section.add_widget(ml)
        for note in row.risk_notes:
            rn = QLabel(f"⚠ {note}")
            rn.setProperty("class", "WarningChip")
            rn.setWordWrap(True)
            rn.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(rn)
        for cav in row.caveats:
            cv = QLabel(cav)
            cv.setProperty("class", "CardMeta")
            cv.setWordWrap(True)
            cv.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(cv)
        return section

    def _build_advanced(self, panel) -> QWidget:
        section = CollapsibleSection(
            "Advanced detection", "Hardware_Section_advanced", expanded=False
        )
        blurb = QLabel(
            "Active Super-I/O port probing reads hardware I/O ports directly. It is never "
            "run automatically — only when you request it below."
        )
        blurb.setProperty("class", "CardMeta")
        blurb.setWordWrap(True)
        section.add_widget(blurb)
        self._probe_btn = make_button(
            "Probe ports (advanced)", "secondary", object_name="Hardware_Btn_probe"
        )
        self._probe_btn.setEnabled(panel.probe_available)
        if not panel.probe_available and panel.probe_reason:
            self._probe_btn.setToolTip(panel.probe_reason)
        self._probe_btn.clicked.connect(self._confirm_probe)
        section.add_widget(self._probe_btn)
        return section

    # ── Actions / routing ────────────────────────────────────────────

    def _route_action(self, target: str) -> None:
        if target == "preferred_cpu":
            self.open_preferred_sensors.emit("cpu")
        elif target == "preferred_mb":
            self.open_preferred_sensors.emit("mb")
        elif target == "superio":
            self._scroll.ensureWidgetVisible(self._superio_card)
        elif target == "pwm_verify":
            # AIO-MB Phase 6 §7: Hardware is now the primary entry point for PWM
            # testing, so the recommended action lands in this page's own
            # Diagnostics section rather than deep-linking away. System State
            # keeps its controls as an advanced shortcut (§8), reachable from
            # the "Advanced (System State)" button in that same section.
            self._scroll.ensureWidgetVisible(self._diagnostics_card)
        elif target == "sensors":
            self.open_overview.emit()

    # ── Diagnostics glue (AIO-MB Phase 6) ────────────────────────────
    #
    # This is signal wiring ONLY. The workers (`_VerifyWorker`,
    # `_CharacterizationWorker`), the dialog (`PwmCharacterizationDialog`) and
    # the result wording (`services/verify_view`) are the existing
    # implementations, reused verbatim — §21 forbids duplicating "its daemon
    # calls or state-management logic", and connect-glue duplicates neither.

    def _run_pwm_verify(self, header_id: str) -> None:
        if not self._ensure_verify_worker():
            self._show_diag_message("Cannot test: no daemon connection.")
            return
        self._show_diag_message("Testing PWM control… (about 10 seconds)")
        self._verify_request.emit(header_id)

    @Slot(object)
    def _on_verify_ok(self, result: HwmonVerifyResult) -> None:
        header = None
        if self._state:
            header = next((h for h in self._state.hwmon_headers if h.id == result.header_id), None)
        view = build_verify_result_view(
            result, header=header, diagnostics=getattr(self._diag, "last_hw_diagnostics", None)
        )
        self._show_diag_message(view.text)

    @Slot(str, str)
    def _on_verify_error(self, category: str, message: str) -> None:
        # A soft safety refusal is protection working, not a failure — show the
        # daemon's own message rather than prefixing it as an error.
        self._show_diag_message(message if category == "unavailable" else f"Test failed: {message}")

    def _open_characterization(self, header_id: str) -> None:
        if not header_id or not self._state:
            return
        header = next((h for h in self._state.hwmon_headers if h.id == header_id), None)
        if header is None:
            return
        if not self._ensure_char_worker():
            self._show_diag_message("Cannot characterise: no daemon connection.")
            return
        label = self._state.member_display_name(header_id) or header.label or header_id
        dialog = PwmCharacterizationDialog(
            header_id,
            label,
            # The UNION predicate, never the wire `role` (DEC-312): a header the
            # user downgraded to `chassis_fan` that the hardware labels PUMP is
            # still pump-protected daemon-side, and the dialog's warnings must
            # say so.
            is_pump=header_is_pump_protected(header, self._capabilities()),
            header=header,
            parent=self,
        )
        dialog.start_requested.connect(self._char_start_request.emit)
        dialog.poll_requested.connect(self._char_poll_request.emit)
        dialog.cancel_requested.connect(self._char_cancel_request.emit)
        self._char_dialog = dialog
        try:
            dialog.exec()
        finally:
            dialog.stop_polling()
            self._char_dialog = None

    @Slot(object)
    def _on_char_update(self, run) -> None:
        if self._char_dialog is not None:
            self._char_dialog.apply_run(run)

    @Slot(str, str)
    def _on_char_error(self, category: str, message: str) -> None:
        if self._char_dialog is not None:
            self._char_dialog.apply_error(category, message)

    # ── Validation / lifecycle sessions ──────────────────────────────

    def _open_validation(self, *, lifecycle: bool, device_id: str = "") -> None:
        from control_ofc.api.models import (
            VALIDATION_KIND_LIFECYCLE,
            VALIDATION_KIND_VALIDATION,
        )

        if not self._ensure_validation_worker():
            self._show_diag_message("Cannot start a session: no daemon connection.")
            return
        device_id, device_name, members = self._resolve_device(device_id)
        if not device_id:
            self._show_diag_message(
                "Configure a cooling device first — a session records one named assembly."
            )
            return
        dialog = ValidationSessionDialog(
            device_id,
            device_name,
            kind=VALIDATION_KIND_LIFECYCLE if lifecycle else VALIDATION_KIND_VALIDATION,
            members=members,
            parent=self,
        )
        dialog.start_requested.connect(self._on_validation_start)
        dialog.poll_requested.connect(self._validation_poll_request.emit)
        dialog.stop_requested.connect(self._validation_stop_request.emit)
        dialog.cancel_requested.connect(self._validation_cancel_request.emit)
        dialog.marker_requested.connect(self._validation_marker_request.emit)
        dialog.measurement_requested.connect(self._validation_measurement_request.emit)
        dialog.export_requested.connect(self._export_session)
        self._validation_dialog = dialog
        # Show whatever the daemon already has: a session may still be recording
        # from an earlier visit, and opening the dialog must not look like a
        # fresh start.
        self._validation_poll_request.emit()
        dialog.start_polling()
        try:
            dialog.exec()
        finally:
            dialog.stop_polling()
            self._validation_dialog = None

    def _resolve_device(self, device_id: str = "") -> tuple[str, str, list[tuple[str, str]]]:
        """The device a session targets, plus its members for the pickers.

        Honours the id a cooling-device card emitted; falls back to the first
        device only for the section-level buttons, which name no device. The
        GUI writes a single fixed device id today, so the two coincide — but the
        inventory is a list and the daemon supports several, so picking
        ``devices[0]`` regardless of what the user clicked would be wrong by
        construction rather than merely unlucky.
        """
        inventory = getattr(self._state, "cooling_devices", None) if self._state else None
        devices = list(inventory.cooling_devices) if inventory else []
        if not devices:
            return "", "", []
        device = next((d for d in devices if d.id == device_id), devices[0])
        members: list[tuple[str, str]] = []
        for member_id in (
            ([device.pump_member] if device.pump_member else [])
            + list(device.radiator_members)
            + list(device.auxiliary_members)
        ):
            label = (self._state.member_display_name(member_id) if self._state else "") or member_id
            members.append((member_id, label))
        return device.id, device.name or device.id, members

    def _on_validation_start(
        self, device_id: str, kind: str, diagnostics: list, sweep: list, metadata: dict
    ) -> None:
        self._validation_start_request.emit(device_id, kind, diagnostics, sweep, metadata)
        if self._validation_dialog is not None:
            self._validation_dialog.start_polling()

    @Slot(object)
    def _on_validation_update(self, session) -> None:
        if self._validation_dialog is not None:
            self._validation_dialog.apply_session(session)

    @Slot(str, str)
    def _on_validation_error(self, category: str, message: str) -> None:
        if self._validation_dialog is not None:
            self._validation_dialog.apply_error(category, message)

    @Slot(str)
    def _on_validation_action_ok(self, message: str) -> None:
        if self._validation_dialog is not None:
            self._validation_dialog.apply_action_ok(message)

    def _export_session(self, fmt: str) -> None:
        """Write the session out through the Qt-free Phase 5 serializers (§16)."""
        from PySide6.QtWidgets import QFileDialog

        from control_ofc.services.validation_export import (
            session_json,
            session_samples_csv,
        )

        dialog = self._validation_dialog
        session = dialog.session() if dialog is not None else None
        if session is None:
            return
        if fmt == "csv":
            suggested, filt, body = (
                f"validation_{session.session_id}.csv",
                "CSV files (*.csv)",
                session_samples_csv(session),
            )
        else:
            suggested, filt, body = (
                f"validation_{session.session_id}.json",
                "JSON files (*.json)",
                session_json(session),
            )
        path, _ = QFileDialog.getSaveFileName(self, "Export validation session", suggested, filt)
        if not path:
            return
        try:
            from pathlib import Path

            from control_ofc.paths import atomic_write

            atomic_write(Path(path), body)
        except OSError as exc:
            log.warning("Validation export failed: %s", exc)
            if dialog is not None:
                dialog.apply_error("error", f"Could not write {path}: {exc}")
            return
        if dialog is not None:
            dialog.apply_action_ok(f"Exported to {path}")

    def _forget_device(self, device_id: str) -> None:
        """Delete a cooling device's topology (closes register row AIO4-d).

        Metadata only. The headers, their roles and every safety floor are
        untouched — the daemon derives pump protection from the ROLE union, not
        from device membership (DEC-316) — and no fan changes speed.
        """
        if not self._client:
            self._show_diag_message("Cannot forget device: no daemon connection.")
            return
        try:
            self._client.delete_cooling_device(device_id)
        except Exception as exc:
            log.warning("Failed to delete cooling device %s: %s", device_id, exc)
            self._show_diag_message(f"Could not forget the device: {exc}")
            return
        card = self._device_cards.pop(device_id, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        self._show_diag_message("Cooling device topology removed. No fan settings changed.")
        # DEC-319: push the inventory back into AppState, not just this page.
        # The Controls picker reserves a device's members, and the poller only
        # re-reads the inventory on the ~300 s capability interval — so without
        # this the fans of a device the user just forgot stay reserved, and
        # nothing on screen explains why.
        #
        # `set_cooling_devices` emits `cooling_devices_updated`, which is already
        # connected to `_refresh_cooling_section` (see __init__) — so it refreshes
        # this page too, and calling it again here would tear down and rebuild
        # every device card twice for one click. The explicit refresh is the
        # fallback for the paths that did NOT push to state.
        pushed = False
        if self._state is not None:
            try:
                self._state.set_cooling_devices(self._client.get_cooling_devices())
                pushed = True
            except Exception as exc:
                log.warning("Cooling-device re-fetch after a delete failed: %s", exc)
        if not pushed:
            self._refresh_cooling_section()

    def _show_diag_message(self, text: str) -> None:
        self._diag_result.setText(text)
        self._diag_result.setVisible(bool(text))
        self._scroll.ensureWidgetVisible(self._diagnostics_card)

    def _confirm_probe(self) -> None:
        answer = QMessageBox.question(
            self,
            "Probe Super-I/O ports?",
            "Active port probing reads hardware I/O ports directly (needs elevated "
            "privileges). It is read-only but touches the hardware. Proceed?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_readiness_probe()

    def _run_readiness_probe(self) -> None:
        if not self._client or not self._ensure_readiness_worker():
            return
        self._set_status("Probing Super-I/O ports…")
        self._readiness_probe_request.emit()

    # ── Worker lifecycle (ported) ────────────────────────────────────

    def _ensure_worker(self, worker, thread, worker_cls, connect: Callable) -> tuple:
        if worker is not None:
            return worker, thread, True
        socket_path = self._client.socket_path if self._client else None
        if not socket_path:
            return None, None, False
        thread = QThread(self)
        worker = worker_cls(socket_path)
        worker.moveToThread(thread)
        connect(worker)
        thread.start()
        return worker, thread, True

    def _ensure_readiness_worker(self) -> bool:
        def connect(w: _HardwareReadinessWorker) -> None:
            self._readiness_request.connect(w.do_fetch, Qt.ConnectionType.QueuedConnection)
            self._readiness_refresh_request.connect(
                w.do_refresh, Qt.ConnectionType.QueuedConnection
            )
            self._readiness_probe_request.connect(w.do_probe, Qt.ConnectionType.QueuedConnection)
            w.fetch_ok.connect(self._on_readiness_ok, Qt.ConnectionType.QueuedConnection)
            w.fetch_error.connect(self._on_readiness_error, Qt.ConnectionType.QueuedConnection)
            w.probe_ok.connect(self._on_readiness_probe_ok, Qt.ConnectionType.QueuedConnection)
            w.probe_error.connect(
                self._on_readiness_probe_error, Qt.ConnectionType.QueuedConnection
            )

        self._readiness_worker, self._readiness_thread, ok = self._ensure_worker(
            self._readiness_worker, self._readiness_thread, _HardwareReadinessWorker, connect
        )
        return ok

    def _ensure_verify_worker(self) -> bool:
        def connect(w: _VerifyWorker) -> None:
            self._verify_request.connect(w.do_verify, Qt.ConnectionType.QueuedConnection)
            w.verify_ok.connect(self._on_verify_ok, Qt.ConnectionType.QueuedConnection)
            w.verify_error.connect(self._on_verify_error, Qt.ConnectionType.QueuedConnection)

        self._verify_worker, self._verify_thread, ok = self._ensure_worker(
            self._verify_worker, self._verify_thread, _VerifyWorker, connect
        )
        return ok

    def _ensure_char_worker(self) -> bool:
        def connect(w: _CharacterizationWorker) -> None:
            self._char_start_request.connect(w.do_start, Qt.ConnectionType.QueuedConnection)
            self._char_poll_request.connect(w.do_poll, Qt.ConnectionType.QueuedConnection)
            self._char_cancel_request.connect(w.do_cancel, Qt.ConnectionType.QueuedConnection)
            w.run_updated.connect(self._on_char_update, Qt.ConnectionType.QueuedConnection)
            w.run_error.connect(self._on_char_error, Qt.ConnectionType.QueuedConnection)

        self._char_worker, self._char_thread, ok = self._ensure_worker(
            self._char_worker, self._char_thread, _CharacterizationWorker, connect
        )
        return ok

    def _ensure_validation_worker(self) -> bool:
        def connect(w: _ValidationWorker) -> None:
            self._validation_start_request.connect(w.do_start, Qt.ConnectionType.QueuedConnection)
            self._validation_poll_request.connect(w.do_poll, Qt.ConnectionType.QueuedConnection)
            self._validation_stop_request.connect(w.do_stop, Qt.ConnectionType.QueuedConnection)
            self._validation_cancel_request.connect(w.do_cancel, Qt.ConnectionType.QueuedConnection)
            self._validation_marker_request.connect(w.do_marker, Qt.ConnectionType.QueuedConnection)
            self._validation_measurement_request.connect(
                w.do_measurement, Qt.ConnectionType.QueuedConnection
            )
            w.session_updated.connect(
                self._on_validation_update, Qt.ConnectionType.QueuedConnection
            )
            w.session_error.connect(self._on_validation_error, Qt.ConnectionType.QueuedConnection)
            w.action_ok.connect(self._on_validation_action_ok, Qt.ConnectionType.QueuedConnection)

        self._validation_worker, self._validation_thread, ok = self._ensure_worker(
            self._validation_worker, self._validation_thread, _ValidationWorker, connect
        )
        return ok

    def _teardown_worker(self, worker: QObject | None, thread: QThread | None, label: str) -> None:
        # Close the client (worker.shutdown) BEFORE joining the thread — the verify
        # worker blocks synchronously and closing the client is the only way to
        # interrupt it so quit()/wait() can join (the slot absorbs the resulting
        # connection error). Audit F-1's close-after-join reorder hangs a blocking
        # join; kept close-first deliberately (mirrors system_state_page).
        if worker is not None:
            QObject.disconnect(worker, None, None, None)
            worker.shutdown()
        if thread is not None:
            thread.quit()
            if not thread.wait(2000):
                log.warning("%s thread did not stop within 2s, terminating", label)
                thread.terminate()
                thread.wait(1000)

    def cleanup(self) -> None:
        # Every worker tears down the same way, and in the same order: close the
        # client BEFORE joining (see `_teardown_worker`). The Phase 6 workers
        # block on hardware exactly as the readiness one does — a validation
        # start can be minutes long — so none of them may be joined first.
        for worker, thread, label in (
            (self._readiness_worker, self._readiness_thread, "Readiness"),
            (self._verify_worker, self._verify_thread, "Verify"),
            (self._char_worker, self._char_thread, "Characterization"),
            (self._validation_worker, self._validation_thread, "Validation"),
        ):
            self._teardown_worker(worker, thread, label)
        self._readiness_worker = self._readiness_thread = None
        self._verify_worker = self._verify_thread = None
        self._char_worker = self._char_thread = None
        self._validation_worker = self._validation_thread = None

    def set_theme(self, _tokens) -> None:
        if self._last_report is not None:
            self._render(self._last_report)


# ── Module helpers ───────────────────────────────────────────────────


def _state_color(state: str, theme) -> str:
    return {
        "ok": theme.status_ok,
        "warn": theme.status_warn,
        "crit": theme.status_crit,
        "info": theme.status_info,
    }.get(state, theme.text_secondary)


def _note_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("class", "CardMeta")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _copy_to_clipboard(text: str) -> None:
    clip = QApplication.clipboard()
    if clip is not None:
        clip.setText(text)


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
