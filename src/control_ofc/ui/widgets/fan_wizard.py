"""Fan Configuration Wizard — guided fan identification and labelling.

Changes one controllable fan at a time so the user can observe which physical
fan responded, then assign a human-readable label. Labels persist via
AppSettings.fan_aliases and propagate across the entire UI.

Ordinary fans are stopped. A header the daemon knows to be a pump is **never**
stopped (DEC-311) — it shifts the speed instead, so coolant keeps flowing. The
daemon makes that decision from the header's role and reports which it did; this
wizard only mirrors it, gated on ``control.header_roles`` so the copy stays
truthful against an older daemon that stops everything.

Note the limit, because it is the normal case on many boards: a header the daemon
cannot classify is an ordinary fan to it, and *is* stopped. Assigning the pump
role is what changes that. DEC-312 gave the Configure-AIO dialog that affordance;
**DEC-319 puts it in this wizard too** (``CoolingDevicePage``), on the reasoning
that a wizard which is about to stop fans is the last useful moment to ask, and a
user who never opens the Controls page would otherwise never be asked at all. The
cost — role-writing logic in two surfaces — is recorded as register row
``AIO7-b``.

Uses QWizard for standard multi-step navigation with Back/Next/Finish/Cancel.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import ConnectionState
from control_ofc.constants import THERMAL_ABORT_C
from control_ofc.services.controls_view import (
    build_pump_role_candidates,
    build_radiator_candidates,
)
from control_ofc.services.cooling_device_view import (
    DEFAULT_COOLING_DEVICE_ID,
    CoolingMembership,
    cooling_member_index,
    find_cooling_device,
    merge_cooling_device_payload,
)
from control_ofc.services.pump_protection import (
    header_is_pump_protected,
    pump_identify_warning,
)
from control_ofc.ui.components.a11y import name_value_control

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

# Fallback spindown duration when the wizard cannot read the controller's actual
# stop-delay.  Five seconds covers the longest seen in practice.
_FALLBACK_SPINDOWN_S = 5

_LABEL_PRESETS = [
    "",
    "CPU Cooler",
    "Rear Exhaust",
    "Front Intake Top",
    "Front Intake Bottom",
    "Top Exhaust Left",
    "Top Exhaust Right",
    "Bottom Intake",
    "Radiator Top",
    "Radiator Front",
    "Side Intake",
    "Case Fan",
    "Pump",
]

# Page IDs. `PAGE_COOLING` is numbered 4 rather than inserted at 1 on purpose:
# QWizard ids are arbitrary and `nextId()` is overridden, so a high id adds the
# page without renumbering PAGE_TEST/PAGE_REVIEW — which would silently
# invalidate every existing test and objectName that names them.
PAGE_INTRO = 0
PAGE_DISCOVERY = 1
PAGE_TEST = 2
PAGE_REVIEW = 3
PAGE_COOLING = 4


def _slug(fan_id: str) -> str:
    """A fan id reduced to something safe for an objectName suffix.

    Every shared/repeated widget here needs a UNIQUE objectName — a fixed one
    collides the moment a second row exists and breaks `findChild` in tests
    (CLAUDE.md § GUI component standard).
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", fan_id).strip("_")


def _hot_cpu_sensor(sensors):
    """The first CPU sensor over the thermal-abort threshold, or ``None``.

    The single copy of the wizard's thermal-safety rule — used by both the
    pre-flight gate (IntroPage) and the live per-tick guard (IdentifyFanPage), so
    the threshold lives in exactly one place.
    """
    for s in sensors:
        if s.kind.lower().startswith("cpu") and s.value_c > THERMAL_ABORT_C:
            return s
    return None


class FanConfigWizard(QWizard):
    """Guided wizard for identifying and labelling controllable fans."""

    labels_saved = Signal(dict)  # {fan_id: label}

    def __init__(
        self,
        state: AppState,
        client: DaemonClient | None = None,
        spindown_seconds: int = _FALLBACK_SPINDOWN_S,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fan Configuration Wizard")
        self.setMinimumSize(650, 500)

        self._state = state
        self._client = client
        self.spindown_seconds = max(5, min(12, spindown_seconds))

        # Build target list from current fan data
        self._targets = self._build_targets()
        self._selected_indices: list[int] = []  # set by discovery page
        self._labels: dict[str, str] = {}  # fan_id → label
        self._current_test_idx = 0
        # True once the user has entered the identify (test) page, so closing
        # the wizard restores any fan still stopped. Identify is per-fan and
        # daemon-owned (DEC-166): no global automation freeze, no hwmon lease.
        self._identify_active = False
        # What the daemon reported it ACTUALLY did on the last identify take
        # (DEC-311): "stop" | "pump_perturb", None before any take and from a
        # pre-2.28.0 daemon. Prediction is unavoidable BEFORE the call; after it,
        # this is ground truth and outranks the prediction.
        # Keyed by fan id, NOT a bare mode: `last_identify_perturbed_pump` takes a
        # fan_id, and a single wizard-wide value would let it answer for a fan the
        # caller did not ask about — a discriminator that looks load-bearing and
        # is not (the DEC-301 pattern).
        self._last_identify_mode: dict[str, str | None] = {}

        # Pages
        self._intro_page = IntroPage(state)
        self.setPage(PAGE_INTRO, self._intro_page)

        self._cooling_page = CoolingDevicePage(self)
        self.setPage(PAGE_COOLING, self._cooling_page)

        self._discovery_page = DiscoveryPage(self._targets, state, exclusions=self.excluded_reasons)
        self.setPage(PAGE_DISCOVERY, self._discovery_page)

        self._test_page = IdentifyFanPage(self)
        self.setPage(PAGE_TEST, self._test_page)

        self._review_page = ReviewPage(self)
        self.setPage(PAGE_REVIEW, self._review_page)

    def is_pump_target(self, fan_id: str) -> bool:
        """Whether the daemon will *perturb* this fan rather than stop it (DEC-311).

        Thin wrapper over the shared predicate in
        ``services.pump_protection``. The rule moved there when the AIO-MB
        Phase 3 characterisation dialog became its second consumer — a safety
        predicate living in a private method on one widget is one the other
        surfaces cannot follow (CLAUDE.md; DEC-276's precedent).

        The capability gate is inside the shared rule, and it is the point: a
        pre-2.28.0 daemon drives every identified fan to 0 — pumps included — so
        promising "the pump will briefly change speed" against one would be a
        lie. When the capability is absent this is False and the wizard keeps its
        original "the fan will stop" wording, which is what that daemon does.
        """
        header = next((h for h in self._state.hwmon_headers if h.id == fan_id), None)
        return header_is_pump_protected(header, self._state.capabilities)

    def identify_verb(self, fan_id: str) -> str:
        """ "change speed" for a pump, "stop" for everything else."""
        return "change speed" if self.is_pump_target(fan_id) else "stop"

    def last_identify_perturbed_pump(self, fan_id: str) -> bool:
        """Whether the daemon actually perturbed rather than stopped (DEC-312).

        Prefers the ``mode`` the daemon reported on the take just made — it is the
        only thing that truly knows, and reading it means the message shown at the
        moment of action cannot drift from the daemon's predicate. Falls back to
        the prediction only where the daemon reported nothing (pre-2.28.0, which
        always stops, and where ``is_pump_target`` is already False).
        """
        mode = self._last_identify_mode.get(fan_id)
        if mode is not None:
            return mode == "pump_perturb"
        return self.is_pump_target(fan_id)

    # ── Cooling-device awareness (AIO-MB Phase 7, DEC-319) ───────────────────

    def supports_cooling_step(self) -> bool:
        """Whether the daemon has a role model at all (``control.header_roles``).

        The gate is the capability, **not** whether anything looks like an AIO.
        Decision 10: the empty case is exactly where the step earns its keep —
        on a board whose Super-I/O publishes no ``pwmN_label`` files every header
        reports ``role: unknown``, so a detection-gated step would be invisible
        on precisely the hardware that needs it. Below the capability the step is
        skipped entirely and the wizard behaves exactly as it did before.
        """
        caps = getattr(self._state, "capabilities", None)
        control = getattr(caps, "control", None) if caps else None
        return bool(getattr(control, "header_roles", False))

    def cooling_membership(self) -> dict[str, CoolingMembership]:
        """Every fan the cooling stack claims, from the inventory then roles."""
        inventory = getattr(self._state, "cooling_devices", None)
        devices = list(getattr(inventory, "cooling_devices", []) or []) if inventory else []
        return cooling_member_index(devices, self._state.hwmon_headers, self._state.capabilities)

    def excluded_reasons(self) -> dict[str, str]:
        """``fan_id`` → why it is excluded from identification, per the AIO step.

        Empty when the step was skipped, which is what keeps a pre-2.28.0 daemon
        on the original wizard behaviour.
        """
        if not self.supports_cooling_step():
            return {}
        return self._cooling_page.excluded_reasons()

    def _build_targets(self) -> list[dict]:
        targets = []
        for fan in self._state.fans:
            if not fan.rpm:
                continue  # Skip fans without RPM or with RPM=0 (empty slots)
            # Skip amdgpu hwmon entries — GPU fans use PMFW, not hwmon pwm1
            if fan.source == "hwmon" and "amdgpu" in fan.id:
                continue
            # Skip read-only discrete GPU fans — Intel (firmware-managed,
            # DEC-121) and NVIDIA (DEC-204) have no userspace write path, so
            # they cannot be stopped for identification.
            if fan.source in ("intel_gpu", "nvidia_gpu"):
                continue
            targets.append(
                {
                    "id": fan.id,
                    "source": fan.source,
                    "rpm": fan.rpm,
                    "has_tach": True,
                    "existing_label": self._state.fan_display_name(fan.id),
                }
            )
        return targets

    def nextId(self) -> int:
        current = self.currentId()
        if current == PAGE_INTRO:
            return PAGE_COOLING if self.supports_cooling_step() else PAGE_DISCOVERY
        if current == PAGE_COOLING:
            return PAGE_DISCOVERY
        if current == PAGE_DISCOVERY:
            self._selected_indices = self._discovery_page.selected_indices()
            if self._selected_indices:
                self._current_test_idx = 0
                self._identify_active = True
                return PAGE_TEST
            return PAGE_REVIEW
        if current == PAGE_TEST:
            return PAGE_REVIEW
        return -1  # finish

    def current_target(self) -> dict | None:
        if not self._selected_indices:
            return None
        if self._current_test_idx >= len(self._selected_indices):
            return None
        idx = self._selected_indices[self._current_test_idx]
        return self._targets[idx]

    def advance_to_next_fan(self) -> bool:
        """Advance to the next fan target. Returns False if all fans are done."""
        self._current_test_idx += 1
        if self._current_test_idx >= len(self._selected_indices):
            return False
        self._test_page.initializePage()
        return True

    def _exit_override(self) -> None:
        """Restore every tested fan when leaving the wizard (idempotent).

        Identify-stop has a daemon-side deadman auto-restore (DEC-166), so this
        is belt-and-braces: it clears any fan the user left stopped without
        waiting for the deadman. There is no global automation freeze or hwmon
        lease to undo — identify is per-fan and daemon-owned.
        """
        if not self._identify_active:
            return
        self._identify_active = False
        self._restore_all_fans()

    def _restore_all_fans(self) -> None:
        """Restore (un-identify) every target fan. Restore is idempotent."""
        if not self._client:
            return
        for target in self._targets:
            try:
                self.restore_fan(target)
            except DaemonError as e:
                log.warning("Failed to restore fan %s: %s", target["id"], e.message)

    def stop_fan(self, target: dict) -> str | None:
        """Hold a single fan at its identify duty via the daemon's per-fan
        identify API (DEC-166). Returns an error message, or None on success.

        The daemon holds just this fan with a deadman auto-restore and keeps
        every other fan curve-controlled, so there is no global automation
        freeze and no hwmon lease. Works for every source type (openfan /
        amd_gpu / hwmon) addressed by fan id.

        **The daemon chooses the duty, not this method** (DEC-311). An ordinary
        fan is forced to 0, floor-exempt. A ``role: "pump"`` header is perturbed
        instead — shifted well clear of its baseline but never below the 30%
        pump floor and never to 0, because losing coolant flow to find a header
        is not a trade worth making. This supersedes DEC-166's "floor-exempt,
        even a pump". The request is unchanged either way, which is what makes
        an older GUI safe against a newer daemon: it cannot ask for a pump stop
        even by accident.
        """
        if not self._client:
            return "No daemon client available"
        fan_id = target["id"]
        log.info("Wizard: stopping fan %s for identification", fan_id)
        try:
            result = self._client.fan_identify(fan_id, "stop")
            self._last_identify_mode[fan_id] = result.mode
            return None
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Failed to stop fan %s: %s", fan_id, e)
            self._last_identify_mode.pop(fan_id, None)
            return str(e)

    def restore_fan(self, target: dict) -> None:
        """Restore (un-identify) a fan after identification via the daemon
        (DEC-166). Restore is idempotent — the engine recomputes the fan's
        curve value on the next tick, so the GUI never replays a prior PWM."""
        if not self._client:
            return
        fan_id = target["id"]
        log.info("Wizard: restoring fan %s", fan_id)
        try:
            self._client.fan_identify(fan_id, "restore")
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Failed to restore fan %s: %s", fan_id, e)

    def check_thermal_safe(self) -> bool:
        """Whether no CPU sensor exceeds the thermal-abort threshold."""
        return _hot_cpu_sensor(self._state.sensors) is None

    def accept(self) -> None:
        """Save labels and clean up on Finish."""
        self._exit_override()
        # Save labels
        for fan_id, label in self._labels.items():
            if label:
                self._state.set_fan_alias(fan_id, label)
        log.info("Wizard: saved %d fan label(s)", len(self._labels))
        self.labels_saved.emit(dict(self._labels))
        super().accept()

    def reject(self) -> None:
        """Clean up on Cancel — restore fans but don't save labels."""
        self._exit_override()
        log.info("Wizard: cancelled, labels not saved")
        super().reject()

    def done(self, result: int) -> None:
        """Ensure cleanup happens regardless of how the wizard closes."""
        self._exit_override()
        super().done(result)


class IntroPage(QWizardPage):
    """Page 1: Warning and pre-flight checks."""

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Fan Configuration Wizard")
        self.setSubTitle("Identify and label your fans")
        self._state = state

        layout = QVBoxLayout(self)

        # DEC-311 / `UDOC-i`: what happens to a PUMP is the daemon's guarantee,
        # not ours, so that one bullet is built from the capability at
        # `initializePage` time rather than frozen here. The label used to state
        # the protected outcome unconditionally, which lied to anyone on a
        # pre-2.28.0 daemon — the exact case `is_pump_target`'s docstring warns
        # about, a few hundred lines below.
        #
        # The separators are `<br>`, not `\n`: the text contains `<b>` tags, so
        # `Qt.AutoText` resolves it as rich text and newlines collapse to
        # spaces. With `\n` every bullet below ran together into one paragraph
        # — which would have hidden the warning this change adds.
        self._warning_label = QLabel("")
        self._warning_label.setWordWrap(True)
        self._warning_label.setObjectName("FanWizard_Lbl_introWarning")
        layout.addWidget(self._warning_label)
        # Seed it here as well as in `initializePage`, so the label is never
        # blank for a caller that has not shown the page yet. `initializePage`
        # is what keeps it CORRECT across a reconnect; this only keeps it
        # non-empty.
        self._warning_label.setText(self._intro_warning_text())

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._status_label)
        layout.addStretch()

    def _intro_warning_text(self) -> str:
        """Assemble the pre-flight warning, pump bullet included.

        Re-derived per page show so a reconnect to a different daemon cannot
        leave a stale promise on screen.
        """
        return (
            "This wizard will <b>change one fan at a time</b> for several "
            "seconds so you can observe which physical fan responded.<br><br>"
            "• Ordinary fans are <b>stopped</b> briefly.<br>"
            f"{pump_identify_warning(self._state.capabilities)}<br>"
            "• Your system should be <b>idle and cool</b> before starting.<br>"
            "• You can <b>abort at any time</b> — fans will be restored to their "
            "prior speed.<br>"
            "• Only one fan is tested at a time.<br>"
            "• Temperature is monitored during each test for safety."
        )

    def initializePage(self) -> None:
        self._warning_label.setText(self._intro_warning_text())
        errors = []
        if self._state.connection != ConnectionState.CONNECTED:
            errors.append("Daemon is not connected.")
        if not self._state.fans:
            errors.append("No controllable fan outputs detected.")
        hot = _hot_cpu_sensor(self._state.sensors)
        if hot is not None:
            errors.append(f"CPU temperature too high ({hot.value_c:.1f}°C > {THERMAL_ABORT_C}°C).")

        if errors:
            self._status_label.setText("Cannot proceed:\n• " + "\n• ".join(errors))
            self._status_label.setProperty("class", "CriticalChip")
        else:
            fan_count = len(self._state.fans)
            self._status_label.setText(f"Ready — {fan_count} controllable fan(s) detected.")
            self._status_label.setProperty("class", "SuccessChip")

    def isComplete(self) -> bool:
        if self._state.connection != ConnectionState.CONNECTED:
            return False
        return bool(self._state.fans)


class CoolingDevicePage(QWizardPage):
    """Page: identify the liquid cooler BEFORE any fan is stopped (DEC-319).

    Two jobs, and the second is why the page exists at all rather than being a
    read-only summary:

    1. **List** what the daemon already knows about the cooling stack — a
       configured cooling device's members, plus any header carrying a ``pump``
       or ``radiator_fan`` role — each pre-ticked to be excluded from
       identification (Decision 2).
    2. **Nominate.** On a board whose Super-I/O exposes no ``pwmN_label`` files
       nothing can infer a pump: every header reports ``role: unknown`` and
       ``stop_permitted: true``, so the wizard would drive a real pump to 0
       looking for it. The user telling us IS the detection, so the nomination
       lives here, before the first stop, and writes
       ``POST /config/header-role`` (Decision 1) and the cooling-device topology
       (Decision 5).

    ``IntroPage`` keeps its one-line "a pump is never stopped" bullet, which is
    still true and is the right thing to say in a pre-flight summary; this page
    is where that claim is *explained and acted on*. An earlier revision of this
    docstring said the copy had moved here — it had not, and the two are
    deliberately both present.
    """

    def __init__(self, wizard: FanConfigWizard, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Liquid Cooling")
        self.setSubTitle("Identify your AIO before any fan is stopped")
        self._wizard = wizard
        # fan_id -> (checkbox, reason shown on the Detected Fans table)
        self._exclude_rows: dict[str, tuple[QCheckBox, str]] = {}

        layout = QVBoxLayout(self)

        intro = QLabel(
            "A pump must never be stopped to identify it. If this machine has a "
            "liquid cooler, say which header drives the pump — the daemon will "
            "then <b>shift its speed</b> instead of stopping it, and keep it above "
            "its safety floor.\n\n"
            "Many motherboards report no header names at all, so this cannot be "
            "detected. Nothing below is changed until you press <b>Apply</b>."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Wizard_Label_coolingIntro")
        layout.addWidget(intro)

        # ── Known members, each tickable to exclude ──────────────────────────
        self._members_label = QLabel("")
        self._members_label.setWordWrap(True)
        self._members_label.setObjectName("Wizard_Label_coolingMembers")
        layout.addWidget(self._members_label)

        self._members_container = QWidget(self)
        self._members_container.setObjectName("Wizard_Box_coolingMembers")
        self._members_layout = QVBoxLayout(self._members_container)
        self._members_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._members_container)

        # ── Nomination ───────────────────────────────────────────────────────
        nominate = QGroupBox("Tell the wizard about your cooler")
        nominate.setObjectName("Wizard_Group_nominate")
        nom_layout = QVBoxLayout(nominate)

        pump_row = QHBoxLayout()
        pump_row.addWidget(QLabel("Pump header:"))
        self._pump_combo = QComboBox()
        self._pump_combo.setObjectName("Wizard_Combo_pumpHeader")
        name_value_control(self._pump_combo, "Pump header")
        pump_row.addWidget(self._pump_combo, 1)
        nom_layout.addLayout(pump_row)

        nom_layout.addWidget(QLabel("Radiator fans (optional):"))
        self._radiator_list = QListWidget()
        self._radiator_list.setObjectName("Wizard_List_radiators")
        self._radiator_list.setAccessibleName("Radiator fans")
        self._radiator_list.setMaximumHeight(120)
        nom_layout.addWidget(self._radiator_list)

        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("Wizard_Btn_applyCooling")
        self._apply_btn.clicked.connect(self._apply_nomination)
        apply_row.addWidget(self._apply_btn)
        nom_layout.addLayout(apply_row)

        layout.addWidget(nominate)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("Wizard_Label_coolingStatus")
        layout.addWidget(self._status)
        layout.addStretch()

    # ── Population ───────────────────────────────────────────────────────────

    def initializePage(self) -> None:
        self._populate()

    def _populate(self) -> None:
        """Rebuild both halves from current state. Idempotent, and re-run after
        every successful Apply so the page reflects what the daemon now says."""
        membership = self._wizard.cooling_membership()

        # Members, each pre-ticked to exclude (Decision 2).
        previous = {fan_id: cb.isChecked() for fan_id, (cb, _) in self._exclude_rows.items()}
        while self._members_layout.count():
            item = self._members_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._exclude_rows = {}

        if membership:
            self._members_label.setText(
                "These are part of the cooling stack. Ticked items are "
                "<b>not</b> identified — untick one to identify it anyway."
            )
        else:
            self._members_label.setText(
                "No pump or radiator fan is known on this machine yet. "
                "If you have a liquid cooler, name its pump below."
            )

        for fan_id, member in sorted(membership.items()):
            name = self._wizard._state.fan_display_name(fan_id) or fan_id
            where = f" — {member.device_name}" if member.from_device else ""
            cb = QCheckBox(f"{name} · {member.role_label}{where}")
            cb.setObjectName(f"Wizard_Chk_exclude_{_slug(fan_id)}")
            # Default excluded; a choice the user already made on this page wins
            # over the default when the page is revisited.
            cb.setChecked(previous.get(fan_id, True))
            self._members_layout.addWidget(cb)
            reason = f"Part of {member.device_name}" if member.from_device else member.role_label
            self._exclude_rows[fan_id] = (cb, f"Excluded — {reason}")

        self._populate_nomination(membership)

    def _populate_nomination(self, membership: dict[str, CoolingMembership]) -> None:
        headers = self._wizard._state.hwmon_headers
        display = self._wizard._state.fan_display_name

        current_pump = self._pump_combo.currentData()
        self._pump_combo.clear()
        self._pump_combo.addItem("— none —", "")
        detected_pump = next(
            (mid for mid, m in membership.items() if m.role == "pump"),
            "",
        )
        for row in build_pump_role_candidates(headers, display_name=display):
            self._pump_combo.addItem(row["label"], row["id"])
        wanted = current_pump or detected_pump
        if wanted:
            index = self._pump_combo.findData(wanted)
            if index >= 0:
                self._pump_combo.setCurrentIndex(index)

        checked = {
            self._radiator_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._radiator_list.count())
            if self._radiator_list.item(i).checkState() == Qt.CheckState.Checked
        }
        preselect = {mid for mid, m in membership.items() if m.role == "radiator"} | checked
        self._radiator_list.clear()
        for row in build_radiator_candidates(
            self._wizard._state.fans,
            headers,
            pump_id=self._pump_combo.currentData() or None,
            preselect_ids=preselect,
            display_name=display,
        ):
            item = QListWidgetItem(f"[{row['source']}] {row['label']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if row["preselect"] else Qt.CheckState.Unchecked
            )
            self._radiator_list.addItem(item)

    # ── Exclusion, read by the wizard ────────────────────────────────────────

    def excluded_reasons(self) -> dict[str, str]:
        return {
            fan_id: reason for fan_id, (cb, reason) in self._exclude_rows.items() if cb.isChecked()
        }

    # ── Nomination, the only thing on this page that writes ──────────────────

    def _selected_radiators(self) -> list[str]:
        return [
            self._radiator_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._radiator_list.count())
            if self._radiator_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _offered_radiators(self) -> set[str]:
        """Every id the radiator picker actually showed.

        The complement matters: ``build_radiator_candidates`` iterates live fans
        and writable headers, so a device member that is momentarily undetected
        — an OpenFan channel that dropped off, a header not yet writable at boot
        — has no row. It cannot have been *unticked*, because it was never
        shown, and treating its absence as a deselection silently erases it from
        the device.
        """
        return {
            self._radiator_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._radiator_list.count())
        }

    def _stale_user_pumps(self, pump_id: str) -> list[str]:
        """Headers the USER previously named as the pump and no longer has selected.

        Restricted to ``role_source == "user_assigned"`` for the same reason
        ``AioConfigDialog`` restricts it: a role the daemon inferred from the
        hardware label or the chip is not ours to remove, and clearing it would
        not remove it anyway — a clear only drops the stored assignment and falls
        back to exactly that inference.
        """
        return [
            h.id
            for h in self._wizard._state.hwmon_headers
            if h.role == "pump" and h.role_source == "user_assigned" and h.id != pump_id
        ]

    def _confirm_clear(self, header_ids: list[str]) -> bool:
        """Confirm removing pump protection (Decision 11) — the ONLY operation on
        this page that can lower a floor, so it is the only one that asks.

        The copy names the protection being removed rather than implying it: a
        user who reads "clear the pump role" does not necessarily know that this
        is what stops the fan wizard driving it to 0.
        """
        names = "\n".join(f"• {self._wizard._state.fan_display_name(h) or h}" for h in header_ids)
        answer = QMessageBox.question(
            self,
            "Remove pump protection from a header?",
            "You previously named this as the pump:\n\n"
            f"{names}\n\n"
            "Clearing that role removes its pump protection — the daemon will no "
            "longer hold it above the 30% pump floor, and it may be stopped "
            "during fan identification.\n\n"
            "Clear it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _apply_nomination(self) -> None:
        client = self._wizard._client
        if client is None:
            self._status.setText("No daemon connection — nothing was changed.")
            return

        pump_id = self._pump_combo.currentData() or ""
        radiators = self._selected_radiators()
        if not pump_id and not radiators:
            self._status.setText("Nothing selected — choose a pump or a radiator fan first.")
            return

        header_ids = {h.id for h in self._wizard._state.hwmon_headers}
        assigns: list[tuple[str, str | None]] = []
        if pump_id:
            assigns.append((pump_id, "pump"))
        # OpenFan radiator members have no header and therefore no role to set;
        # they still belong to the device topology below.
        assigns += [(r, "radiator_fan") for r in radiators if r in header_ids and r != pump_id]

        # ASSIGN BEFORE CLEAR, and the order is the safety property, not an
        # artefact of iteration (DEC-312's review found this). No clear is ever
        # reached unless every assign succeeded, so a failure here can only ever
        # leave MORE protection in place than intended, never less.
        #
        # It does not follow that nothing changed: the assigns are separate
        # requests, so a failure at index N means the first N already landed.
        # Saying "nothing was changed" there would be false, and it is a role
        # write — exactly the kind of claim that must not be approximated.
        for done, (header_id, role) in enumerate(assigns):
            try:
                client.set_header_role(header_id, role)
            except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
                log.error("Wizard: could not set role %s on %s: %s", role, header_id, e)
                landed = (
                    "No roles were changed."
                    if done == 0
                    else f"{done} earlier role assignment(s) had already been saved "
                    "and are still in effect."
                )
                self._status.setText(
                    f"The daemon rejected the {role} assignment: {e}\n"
                    f"{landed} The cooler's layout was not saved."
                )
                # Re-read so the page shows what actually landed rather than
                # what was requested.
                self._refresh_state()
                self._populate()
                return

        stale = self._stale_user_pumps(pump_id) if pump_id else []
        cleared_note = ""
        if stale and self._confirm_clear(stale):
            for header_id in stale:
                try:
                    client.set_header_role(header_id, None)
                except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
                    # Tolerated: a stale pump role over-protects, never under.
                    log.warning("Wizard: could not clear role on %s: %s", header_id, e)
        elif stale:
            cleared_note = " The previous pump header kept its role."

        self._upsert_cooling_device(pump_id, radiators)
        self._refresh_state()
        self._populate()
        self._status.setText("Saved." + cleared_note)

    def _upsert_cooling_device(self, pump_id: str, radiators: list[str]) -> None:
        """Read-modify-write the cooling device (Decision 6).

        ``POST /config/cooling-device`` REPLACES by id, so posting only the
        topology would erase the name and advisory sensors a previous Configure
        AIO run stored. Best-effort like ``controls_page._save_cooling_device``:
        the topology is metadata the engine never reads, so failing it costs a
        presentation nicety, not the roles that were just assigned.
        """
        client = self._wizard._client
        caps = getattr(self._wizard._state, "capabilities", None)
        control = getattr(caps, "control", None) if caps else None
        if client is None or not getattr(control, "cooling_devices", False):
            return
        try:
            inventory = client.get_cooling_devices()
            existing = find_cooling_device(
                getattr(inventory, "cooling_devices", []), DEFAULT_COOLING_DEVICE_ID
            )
            # Carry forward any existing radiator member the picker never
            # offered — absence from an unticked list is not a deselection when
            # the row was never there to untick. The pump is excluded because it
            # has just been promoted out of the radiator set.
            offered = self._offered_radiators()
            kept = [
                m
                for m in (existing.radiator_members if existing else [])
                if m and m not in offered and m not in radiators and m != pump_id
            ]
            payload = merge_cooling_device_payload(
                existing,
                pump_member=pump_id or None,
                radiator_members=radiators + kept,
            )
            client.set_cooling_device(DEFAULT_COOLING_DEVICE_ID, **payload)
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Wizard: could not save cooling-device topology: %s", e)

    def _refresh_state(self) -> None:
        """Re-read headers and the device inventory so this page — and the
        Detected Fans table after it — reflect the write immediately. Both
        otherwise refresh on the ~300 s capability interval."""
        client = self._wizard._client
        state = self._wizard._state
        if client is None:
            return
        try:
            state.set_hwmon_headers(client.hwmon_headers())
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Wizard: header re-fetch after nomination failed: %s", e)
        caps = getattr(state, "capabilities", None)
        control = getattr(caps, "control", None) if caps else None
        if not getattr(control, "cooling_devices", False):
            return
        try:
            state.set_cooling_devices(client.get_cooling_devices())
        except (DaemonError, DaemonUnavailable, OSError, ConnectionError) as e:
            log.warning("Wizard: cooling-device re-fetch after nomination failed: %s", e)


class DiscoveryPage(QWizardPage):
    """Page 3: Show all targets with checkboxes.

    Numbered from the user's view, where DEC-319's Liquid Cooling step is page 2
    when it applies. The QWizard *id* is still ``PAGE_DISCOVERY = 1``.
    """

    def __init__(
        self,
        targets: list[dict],
        state: AppState,
        parent=None,
        exclusions: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setTitle("Detected Fans")
        self.setSubTitle("Select which fans to identify")
        self._targets = targets
        self._state = state
        self._checkboxes: list[QCheckBox] = []
        # ``fan_id -> reason``, re-read on every entry to the page (DEC-319).
        # A callable rather than a value: the AIO step runs BEFORE this one and
        # the user may go Back and change it, so a snapshot taken at
        # construction would be stale exactly when it mattered.
        self._exclusions: Callable[[], dict[str, str]] = exclusions or dict

        layout = QVBoxLayout(self)

        # Select all/none
        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.setObjectName("Wizard_Btn_selectAll")
        select_all.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(select_all)
        select_none = QPushButton("Select None")
        select_none.setObjectName("Wizard_Btn_selectNone")
        select_none.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Target table with checkboxes. The "Cooling" column is its own rather
        # than a suffix on an existing one: "Current Label" carries the user's
        # name for the fan, and overloading it with a status would make the
        # review table's labels lie.
        self._table = QTableWidget(len(targets), 6)
        self._table.setHorizontalHeaderLabels(
            ["", "ID", "Source", "RPM", "Current Label", "Cooling"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setObjectName("Wizard_Table_targets")
        from PySide6.QtWidgets import QHeaderView

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self._table, 1)
        self._populate()

    def initializePage(self) -> None:
        # Re-derive rather than trusting what `__init__` built: the AIO step
        # precedes this page, and Back → change the exclusion → forward must
        # produce a different table.
        self._populate()

    def _populate(self) -> None:
        """(Re)build the table from targets + the current exclusion set.

        The row order and count always mirror ``self._targets`` exactly, because
        ``selected_indices()`` returns indices INTO that list and
        ``FanConfigWizard.current_target()`` dereferences them. Excluded fans are
        therefore shown and disabled (Decision 9), never filtered out — filtering
        would silently shift every later index.
        """
        excluded = self._exclusions()
        self._checkboxes = []
        self._table.setRowCount(len(self._targets))

        for i, t in enumerate(self._targets):
            reason = excluded.get(t["id"], "")
            cb = QCheckBox()
            cb.setChecked(not reason)
            cb.setEnabled(not reason)
            if reason:
                cb.setToolTip(reason)
            self._checkboxes.append(cb)
            self._table.setCellWidget(i, 0, cb)
            self._table.setItem(i, 1, QTableWidgetItem(t["id"]))
            self._table.setItem(i, 2, QTableWidgetItem(t["source"]))
            rpm_text = str(t["rpm"]) if t["rpm"] is not None else "N/A"
            self._table.setItem(i, 3, QTableWidgetItem(rpm_text))
            self._table.setItem(i, 4, QTableWidgetItem(t["existing_label"]))
            self._table.setItem(i, 5, QTableWidgetItem(reason))

    def _set_all(self, checked: bool) -> None:
        # Skip excluded rows. `QCheckBox.setChecked` works on a DISABLED box, so
        # without this guard "Select All" would silently re-include the pump the
        # user just excluded — the control looks like it is doing nothing wrong.
        for cb in self._checkboxes:
            if cb.isEnabled():
                cb.setChecked(checked)

    def selected_indices(self) -> list[int]:
        # `isEnabled()` is the authoritative guard, not a belt-and-braces one:
        # it is what makes an excluded fan unreachable no matter how its
        # checkbox got into a checked state.
        return [i for i, cb in enumerate(self._checkboxes) if cb.isChecked() and cb.isEnabled()]


class IdentifyFanPage(QWizardPage):
    """Page 4: Test one fan at a time with countdown and label input."""

    def __init__(self, wizard: FanConfigWizard, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Identify Fan")
        self._wizard = wizard
        self._testing = False
        self._seconds_remaining = 0

        layout = QVBoxLayout(self)

        # Target info
        self._target_label = QLabel("")
        self._target_label.setProperty("class", "SectionTitle")
        layout.addWidget(self._target_label)

        self._source_label = QLabel("")
        self._source_label.setProperty("class", "PageSubtitle")
        layout.addWidget(self._source_label)

        # Test controls
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Start Test")
        self._test_btn.setObjectName("Wizard_Btn_startTest")
        self._test_btn.clicked.connect(self._start_test)
        test_row.addWidget(self._test_btn)

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setObjectName("Wizard_Btn_abort")
        self._abort_btn.clicked.connect(self._abort_test)
        self._abort_btn.setEnabled(False)
        test_row.addWidget(self._abort_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, self._wizard.spindown_seconds)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m seconds")
        layout.addWidget(self._progress)

        self._rpm_label = QLabel("RPM: —")
        layout.addWidget(self._rpm_label)

        self._status_msg = QLabel("")
        self._status_msg.setWordWrap(True)
        layout.addWidget(self._status_msg)

        # Label input
        label_group = QVBoxLayout()
        label_prompt = QLabel("Assign a label for this fan:")
        label_group.addWidget(label_prompt)

        label_row = QHBoxLayout()
        self._label_combo = QComboBox()
        self._label_combo.setObjectName("Wizard_Combo_labelPreset")
        name_value_control(self._label_combo, label_prompt)
        self._label_combo.setEditable(True)
        for preset in _LABEL_PRESETS:
            self._label_combo.addItem(preset)
        label_row.addWidget(self._label_combo, 1)
        label_group.addLayout(label_row)
        layout.addLayout(label_group)

        # Fan cycling buttons
        nav_row = QHBoxLayout()
        self._next_fan_btn = QPushButton("Save Label && Next Fan")
        self._next_fan_btn.setObjectName("Wizard_Btn_nextFan")
        self._next_fan_btn.clicked.connect(self._next_fan)
        nav_row.addWidget(self._next_fan_btn)

        self._skip_btn = QPushButton("Skip — couldn't identify")
        self._skip_btn.setObjectName("Wizard_Btn_skip")
        self._skip_btn.clicked.connect(self._skip_target)
        nav_row.addWidget(self._skip_btn)

        nav_row.addStretch()
        layout.addLayout(nav_row)

        self._all_done = False

        layout.addStretch()

        # Timer
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def isComplete(self) -> bool:
        return self._all_done

    def initializePage(self) -> None:
        target = self._wizard.current_target()
        if not target:
            return
        idx = self._wizard._current_test_idx
        total = len(self._wizard._selected_indices)
        self._target_label.setText(f"Testing fan {idx + 1} of {total}: {target['id']}")
        self._source_label.setText(
            f"Source: {target['source']} | "
            f"RPM: {target['rpm'] if target['rpm'] is not None else 'N/A'} | "
            f"Current label: {target['existing_label']}"
        )
        self._progress.setValue(0)
        self._rpm_label.setText("RPM: —")
        # DEC-311: name what will actually happen to THIS fan.
        verb = self._wizard.identify_verb(target["id"])
        self._status_msg.setText(f"Press 'Start Test' to {verb} this fan for identification.")
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._label_combo.setCurrentText("")
        self._testing = False
        self._all_done = False
        self._next_fan_btn.setVisible(True)
        self.completeChanged.emit()

    def cleanupPage(self) -> None:
        """Called when user presses Back — restore fan if testing."""
        if self._testing:
            self._abort_test()

    def _start_test(self) -> None:
        target = self._wizard.current_target()
        if not target:
            return
        # Thermal check
        if not self._wizard.check_thermal_safe():
            self._status_msg.setText(
                f"ABORTED: CPU temperature exceeds {THERMAL_ABORT_C}°C — too hot to test safely."
            )
            self._status_msg.setProperty("class", "CriticalChip")
            return

        self._testing = True
        self._seconds_remaining = self._wizard.spindown_seconds
        self._progress.setValue(0)
        self._test_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._status_msg.setStyleSheet("")

        error = self._wizard.stop_fan(target)
        if error:
            self._testing = False
            self._test_btn.setEnabled(True)
            self._abort_btn.setEnabled(False)
            verb = "change pump speed" if self._wizard.is_pump_target(target["id"]) else "stop fan"
            self._status_msg.setText(f"Failed to {verb}: {error}")
            self._status_msg.setProperty("class", "CriticalChip")
            log.warning("Wizard: test failed to start for %s: %s", target["id"], error)
            return

        # DEC-311: reported from the daemon's own answer where we have it, not
        # re-derived — the daemon decides stop-vs-perturb, so it is the only
        # thing that actually knows.
        if self._wizard.last_identify_perturbed_pump(target["id"]):
            self._status_msg.setText(
                "Pump speed changed — watch the RPM reading or listen for the change..."
            )
        else:
            self._status_msg.setText("Fan stopped — observe which physical fan changed...")
        self._timer.start()
        log.info("Wizard: test started for %s (%ds)", target["id"], self._wizard.spindown_seconds)

    def _tick(self) -> None:
        self._seconds_remaining -= 1
        elapsed = self._wizard.spindown_seconds - self._seconds_remaining
        self._progress.setValue(elapsed)

        # Update RPM from live state
        target = self._wizard.current_target()
        if target:
            for fan in self._wizard._state.fans:
                if fan.id == target["id"]:
                    rpm_text = str(fan.rpm) if fan.rpm is not None else "N/A"
                    self._rpm_label.setText(f"RPM: {rpm_text}")
                    break

        # Thermal check during test
        if not self._wizard.check_thermal_safe():
            self._abort_test()
            self._status_msg.setText(
                f"ABORTED: CPU temperature exceeded {THERMAL_ABORT_C}°C during test."
            )
            self._status_msg.setProperty("class", "CriticalChip")
            return

        if self._seconds_remaining <= 0:
            self._end_test()

    def _end_test(self) -> None:
        self._timer.stop()
        self._testing = False
        target = self._wizard.current_target()
        if target:
            self._wizard.restore_fan(target)
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._progress.setValue(self._wizard.spindown_seconds)
        self._status_msg.setText("Fan restored. Enter a label below, then click Next.")
        log.info("Wizard: test completed for %s", target["id"] if target else "unknown")

    def _abort_test(self) -> None:
        self._timer.stop()
        self._testing = False
        target = self._wizard.current_target()
        if target:
            self._wizard.restore_fan(target)
        self._test_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._status_msg.setText("Test aborted — fan restored.")
        log.info("Wizard: test aborted for %s", target["id"] if target else "unknown")

    def _save_current_label(self) -> None:
        """Save the label for the current fan target."""
        target = self._wizard.current_target()
        if target:
            label = self._label_combo.currentText().strip()
            if label:
                self._wizard._labels[target["id"]] = label

    def _next_fan(self) -> None:
        """Save label for current fan, advance to next fan or finish."""
        if self._testing:
            self._abort_test()
        self._save_current_label()
        if not self._wizard.advance_to_next_fan():
            # All fans tested — enable QWizard's Next button to go to Review
            self._all_done = True
            self._next_fan_btn.setVisible(False)
            self._skip_btn.setVisible(False)
            self._status_msg.setText("All fans tested. Click Next to review labels.")
            self.completeChanged.emit()

    def _skip_target(self) -> None:
        """Skip this fan without saving a label."""
        if self._testing:
            self._abort_test()
        if not self._wizard.advance_to_next_fan():
            self._all_done = True
            self._next_fan_btn.setVisible(False)
            self._skip_btn.setVisible(False)
            self._status_msg.setText("All fans tested. Click Next to review labels.")
            self.completeChanged.emit()

    def validatePage(self) -> bool:
        self._save_current_label()
        return True


class ReviewPage(QWizardPage):
    """Page 5: Review all labels before saving."""

    def __init__(self, wizard: FanConfigWizard, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Review Labels")
        self.setSubTitle("Review and edit labels before saving")
        self._wizard = wizard

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["ID", "Source", "New Label"])
        self._table.setObjectName("Wizard_Table_review")
        from PySide6.QtWidgets import QHeaderView

        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 1)

    def _included_targets(self) -> list[dict]:
        """Targets minus anything the AIO step excluded (DEC-319).

        Only reached when the user selected nothing on the Detected Fans page,
        where ``rows`` falls back to *every* target. Without this an excluded
        pump reappears in the review — the one place the wizard summarises what
        it did — and invites a label for a fan it deliberately never touched.
        """
        excluded = self._wizard.excluded_reasons()
        return [t for t in self._wizard._targets if t["id"] not in excluded]

    def initializePage(self) -> None:
        targets = self._wizard._targets
        selected = self._wizard._selected_indices
        labels = self._wizard._labels

        rows = [targets[i] for i in selected] if selected else self._included_targets()
        self._table.setRowCount(len(rows))

        for i, t in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(t["id"]))
            self._table.setItem(i, 1, QTableWidgetItem(t["source"]))
            label = labels.get(t["id"], "")
            item = QTableWidgetItem(label)
            self._table.setItem(i, 2, item)

    def validatePage(self) -> bool:
        # Read back any edits the user made in the review table
        targets = self._wizard._targets
        selected = self._wizard._selected_indices
        rows = [targets[i] for i in selected] if selected else self._included_targets()

        for i, t in enumerate(rows):
            label_item = self._table.item(i, 2)
            if label_item:
                label = label_item.text().strip()
                if label:
                    self._wizard._labels[t["id"]] = label
                elif t["id"] in self._wizard._labels:
                    del self._wizard._labels[t["id"]]
        return True
