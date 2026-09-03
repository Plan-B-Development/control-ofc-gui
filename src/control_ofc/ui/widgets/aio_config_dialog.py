"""One-click "Configure AIO" dialog (DEC-157, rewritten by DEC-312).

Gathers the user's intent for a guided AIO setup — which header drives the pump,
how the pump should be driven, and a radiator-fan group bound to a sensor — and
returns it. The actual control/curve creation is done by
``profile_service.build_aio_controls`` so this stays a thin, testable UI layer.

Two things changed with DEC-312, both because a motherboard-connected AIO is not
a USB cooler:

* **The pump has to be named, not detected.** A pump plugged into an ``AIO_PUMP``
  header hangs off the Super-I/O chip like every other fan, and on the boards this
  matters for the chip publishes no ``pwmN_label`` files at all — so nothing can
  infer it. The first step therefore *asks*, and posts the answer to
  ``POST /config/header-role``. That is a chicken-and-egg fix as much as a UX one:
  a flow gated on a pump already being known could never be reached on the boards
  that need it most.
* **The pump is not assumed to want a constant speed.** Whether a pump should hold
  a fixed speed or follow temperature is a property of the cooler, not a fact
  about pumps — vendors disagree with each other on their own hardware — so all
  three strategies are offered and the copy asserts none of them as truth.

Read-only / monitor-only coolers still degrade gracefully (no pump section).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.profile_service import (
    AIO_PUMP_DEFAULT_PCT,
    AIO_PUMP_DEFAULT_STRATEGY,
    AIO_PUMP_PRESETS,
    AIO_PUMP_STRATEGY_AUTOMATIC,
    AIO_PUMP_STRATEGY_CUSTOM,
    AIO_PUMP_STRATEGY_FIXED,
)
from control_ofc.ui.components.a11y import name_value_control

# Order shown in the dialog, with the label and the one-line explanation each
# strategy carries. Automatic leads because it is the default (DEC-312) — but the
# copy deliberately states no strategy as correct: the enthusiast/vendor consensus
# favours a fixed speed, and at least one cooler vendor explicitly recommends
# against a fixed speed for its own pumps. Only the cooler's documentation knows.
_STRATEGY_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        AIO_PUMP_STRATEGY_AUTOMATIC,
        "Automatic",
        "Follows temperature on a gentle curve that never drops below the 30% floor.",
    ),
    (
        AIO_PUMP_STRATEGY_FIXED,
        "Fixed speed",
        "Holds one speed regardless of temperature.",
    ),
    (
        AIO_PUMP_STRATEGY_CUSTOM,
        "Custom curve",
        "Starts from the automatic curve and opens the editor so you can shape it.",
    ),
)

_NO_PUMP_DATA = ""

#: Default name for the cooling device this dialog creates (AIO-MB Phase 4).
DEFAULT_COOLING_DEVICE_NAME = "AIO Cooling System"

#: Wire token for the device kind this dialog always produces. Exact-case — the
#: daemon rejects an unrecognised token rather than defaulting it.
COOLING_DEVICE_KIND_AIO = "aio_liquid"


class AioConfigDialog(QDialog):
    """Collect a pump header + pump strategy + radiator fans + sensor for AIO setup."""

    def __init__(
        self,
        *,
        pump_label: str | None,
        monitor_only: bool,
        fan_candidates: list[dict],  # [{id, source, label, preselect}]
        sensor_choices: list[dict],  # [{id, label, preferred}]
        default_sensor_id: str | None = None,
        default_pump_pct: int = AIO_PUMP_DEFAULT_PCT,
        default_pump_strategy: str = AIO_PUMP_DEFAULT_STRATEGY,
        pump_candidates: list[dict] | None = None,  # [{id, label, role, role_source}]
        detected_pump_id: str | None = None,
        has_coolant: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AioConfigDialog")
        self.setWindowTitle("Configure AIO")
        self.setMinimumWidth(480)

        self._pump_buttons = QButtonGroup(self)
        self._strategy_buttons = QButtonGroup(self)
        self._pump_combo: QComboBox | None = None
        self._detected_pump_id = detected_pump_id or _NO_PUMP_DATA
        self._monitor_only = monitor_only
        # Role assignment is offered only when the daemon supports it; the page
        # passes an empty list against a pre-2.28.0 daemon, and the flow then
        # behaves exactly as it did before this change.
        self._pump_candidates = list(pump_candidates or [])
        self._has_detected_pump = bool(pump_label) and not monitor_only
        # Kept for `get_result`: whether the chosen sensor is genuinely a
        # coolant reading decides whether the saved topology may claim
        # coolant telemetry at all.
        self._has_coolant = has_coolant

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Set up your liquid cooler in one step: a pump control and a "
            "radiator-fan group bound to a temperature sensor."
        )
        intro.setObjectName("AioConfig_Label_intro")
        intro.setWordWrap(True)
        intro.setProperty("class", "PageSubtitle")
        layout.addWidget(intro)

        # ── Device name (AIO-MB Phase 4) ──────────────────────────────
        # The one new input this phase adds. Everything else the topology
        # needs — pump, radiators, sensor — the dialog already collected and
        # then threw away; only the assembly's *name* had nowhere to come from.
        name_row = QHBoxLayout()
        name_label = QLabel("Name")
        name_label.setObjectName("AioConfig_Label_deviceName")
        self._name_edit = QLineEdit(DEFAULT_COOLING_DEVICE_NAME)
        self._name_edit.setObjectName("AioConfig_Edit_deviceName")
        self._name_edit.setPlaceholderText(DEFAULT_COOLING_DEVICE_NAME)
        self._name_edit.setToolTip(
            "What to call this cooler. Naming is presentation only — it does not "
            "change how the pump or fans are controlled."
        )
        # DEC-251/276: the shared helper, not a hand-rolled setAccessibleName —
        # it also wires `setBuddy`, which is the half that actually works on
        # Linux, and routes control types Qt would ignore a name on.
        name_value_control(self._name_edit, name_label)
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        # ── Pump section ──────────────────────────────────────────────
        pump_group = QGroupBox("Pump")
        pump_group.setObjectName("AioConfig_Group_pump")
        pump_layout = QVBoxLayout(pump_group)

        if self._pump_candidates:
            self._build_pump_picker(pump_layout, pump_label)
        elif self._has_detected_pump:
            pump_layout.addWidget(QLabel(f"Detected pump: {pump_label}"))
        else:
            mon = QLabel(
                "No controllable pump detected (monitor-only cooler). The coolant "
                "temperature is shown for monitoring; control the pump with your "
                "cooler's vendor tooling."
            )
            mon.setObjectName("AioConfig_Label_monitorOnly")
            mon.setWordWrap(True)
            pump_layout.addWidget(mon)

        self._strategy_box = self._build_strategy_box(default_pump_strategy, default_pump_pct)
        pump_layout.addWidget(self._strategy_box)

        self._pump_hint = QLabel("")
        self._pump_hint.setObjectName("AioConfig_Label_pumpHint")
        self._pump_hint.setWordWrap(True)
        self._pump_hint.setProperty("class", "PageSubtitle")
        pump_layout.addWidget(self._pump_hint)

        layout.addWidget(pump_group)

        # ── Radiator section ──────────────────────────────────────────
        rad_group = QGroupBox("Radiator fans")
        rad_group.setObjectName("AioConfig_Group_radiator")
        rad_layout = QVBoxLayout(rad_group)
        rad_layout.addWidget(QLabel("Include these fans in the radiator group:"))
        self._fan_list = QListWidget()
        self._fan_list.setObjectName("AioConfig_List_radiatorFans")
        self._fan_list.setMaximumHeight(140)
        for cand in fan_candidates:
            item = QListWidgetItem(f"[{cand['source']}] {cand['label']}")
            item.setData(Qt.ItemDataRole.UserRole, cand)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if cand.get("preselect") else Qt.CheckState.Unchecked
            )
            self._fan_list.addItem(item)
        if not fan_candidates:
            empty = QLabel("No controllable fans available to assign yet.")
            empty.setProperty("class", "PageSubtitle")
            rad_layout.addWidget(empty)
        rad_layout.addWidget(self._fan_list)

        sensor_label = QLabel("Bind the radiator-fan curve to:")
        rad_layout.addWidget(sensor_label)
        self._sensor_combo = QComboBox()
        self._sensor_combo.setObjectName("AioConfig_Combo_radiatorSensor")
        name_value_control(self._sensor_combo, sensor_label)
        for ch in sensor_choices:
            prefix = "★ " if ch.get("preferred") else ""
            self._sensor_combo.addItem(f"{prefix}{ch['label']}", ch["id"])
        if default_sensor_id:
            idx = self._sensor_combo.findData(default_sensor_id)
            if idx >= 0:
                self._sensor_combo.setCurrentIndex(idx)
        # A motherboard-connected AIO publishes no coolant temperature — the cooler
        # is not a USB device and reports nothing — so CPU temperature is the
        # normal binding there, not a downgrade. Saying so matters: presenting the
        # usual "coolant is recommended" note on a machine that cannot have one
        # reads as a misconfiguration the user is expected to go and fix.
        if has_coolant:
            sensor_note_text = (
                "★ Coolant temperature is recommended — the radiator's job is to "
                "cool the loop. CPU temperature also works but is spikier."
            )
        else:
            sensor_note_text = (
                "No coolant sensor on this machine — CPU temperature is used instead. "
                "That is normal for an AIO connected to the motherboard, which "
                "reports no coolant reading of its own."
            )
        sensor_note = QLabel(sensor_note_text)
        sensor_note.setObjectName("AioConfig_Label_sensorNote")
        sensor_note.setWordWrap(True)
        sensor_note.setProperty("class", "PageSubtitle")
        rad_layout.addWidget(self._sensor_combo)
        rad_layout.addWidget(sensor_note)
        layout.addWidget(rad_group)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Create controls")
        ok_btn.setObjectName("AioConfig_Btn_create")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("AioConfig_Btn_cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._sync_pump_enabled()

    # ── construction helpers ─────────────────────────────────────────

    def _build_pump_picker(self, pump_layout, pump_label: str | None) -> None:
        """The 'which header is the pump?' step (DEC-312)."""
        picker_label = QLabel("Which header is the pump connected to?")
        pump_layout.addWidget(picker_label)
        self._pump_combo = QComboBox()
        self._pump_combo.setObjectName("AioConfig_Combo_pumpHeader")
        name_value_control(self._pump_combo, picker_label)
        self._pump_combo.addItem("No pump on a motherboard header", _NO_PUMP_DATA)
        for cand in self._pump_candidates:
            self._pump_combo.addItem(cand["label"], cand["id"])
        if self._detected_pump_id:
            idx = self._pump_combo.findData(self._detected_pump_id)
            if idx >= 0:
                self._pump_combo.setCurrentIndex(idx)
        self._pump_combo.currentIndexChanged.connect(self._sync_pump_enabled)
        pump_layout.addWidget(self._pump_combo)

        note = QLabel(
            "Telling the daemon which header drives the pump is what earns it the "
            "30% minimum speed and stops it ever being stopped for fan "
            "identification. On many boards nothing else can work this out."
        )
        note.setObjectName("AioConfig_Label_roleNote")
        note.setWordWrap(True)
        note.setProperty("class", "PageSubtitle")
        pump_layout.addWidget(note)
        if pump_label and not self._pump_combo.currentData():
            pump_layout.addWidget(QLabel(f"Detected pump: {pump_label}"))

    def _build_strategy_box(self, default_strategy: str, default_pump_pct: int) -> QWidget:
        box = QWidget()
        box.setObjectName("AioConfig_Box_strategy")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)

        note = QLabel(
            "Pump speed can be fixed or follow temperature — check your cooler's "
            "documentation. Some pumps are designed to run at a constant speed; "
            "others are designed to be controlled automatically. Either way the "
            "pump is never driven below 30%."
        )
        note.setObjectName("AioConfig_Label_pumpNote")
        note.setWordWrap(True)
        note.setProperty("class", "PageSubtitle")
        box_layout.addWidget(note)

        if default_strategy not in {row[0] for row in _STRATEGY_ROWS}:
            default_strategy = AIO_PUMP_DEFAULT_STRATEGY
        for token, title, blurb in _STRATEGY_ROWS:
            rb = QRadioButton(title)
            rb.setObjectName(f"AioConfig_Radio_strategy_{token}")
            rb.setProperty("strategy", token)
            rb.setToolTip(blurb)
            if token == default_strategy:
                rb.setChecked(True)
            self._strategy_buttons.addButton(rb)
            box_layout.addWidget(rb)
            hint = QLabel(blurb)
            hint.setObjectName(f"AioConfig_Label_strategy_{token}")
            hint.setWordWrap(True)
            hint.setProperty("class", "PageSubtitle")
            box_layout.addWidget(hint)
            if token == AIO_PUMP_STRATEGY_FIXED:
                self._preset_row = QWidget()
                self._preset_row.setObjectName("AioConfig_Box_presets")
                row = QHBoxLayout(self._preset_row)
                row.setContentsMargins(0, 0, 0, 0)
                for name, pct in AIO_PUMP_PRESETS:
                    preset = QRadioButton(f"{name} ({pct}%)")
                    preset.setObjectName(f"AioConfig_Radio_pump{pct}")
                    preset.setProperty("pump_pct", pct)
                    if pct == default_pump_pct:
                        preset.setChecked(True)
                    self._pump_buttons.addButton(preset, pct)
                    row.addWidget(preset)
                box_layout.addWidget(self._preset_row)
        self._strategy_buttons.buttonToggled.connect(self._sync_pump_enabled)
        return box

    # ── state ────────────────────────────────────────────────────────

    def selected_pump_id(self) -> str:
        """The header the user says drives the pump ("" when there is none).

        Can legitimately be "" even when a pump IS being set up: a caller that
        passes only ``pump_label`` (no ``detected_pump_id``) knows a pump exists
        without telling the dialog which header it is. Presence and identity are
        separate questions — see :meth:`_has_pump`, and do not collapse them.
        """
        if self._pump_combo is not None:
            return self._pump_combo.currentData() or _NO_PUMP_DATA
        return self._detected_pump_id

    def _has_pump(self) -> bool:
        """Whether a pump will be set up at all — NOT whether its id is known."""
        if self._pump_combo is not None:
            return bool(self._pump_combo.currentData())
        return self._has_detected_pump

    def _selected_strategy(self) -> str:
        checked = self._strategy_buttons.checkedButton()
        if checked is None:
            return AIO_PUMP_DEFAULT_STRATEGY
        return str(checked.property("strategy"))

    def _sync_pump_enabled(self, *_args) -> None:
        """Keep the strategy controls, presets and hint truthful about the state."""
        has_pump = self._has_pump()
        self._strategy_box.setEnabled(has_pump)
        if hasattr(self, "_preset_row"):
            self._preset_row.setEnabled(
                has_pump and self._selected_strategy() == AIO_PUMP_STRATEGY_FIXED
            )
        if has_pump:
            self._pump_hint.setText("")
        elif self._monitor_only:
            self._pump_hint.setText(
                "This cooler reports its coolant temperature but exposes no writable "
                "pump header, so only the radiator fans can be set up here."
            )
        elif self._pump_combo is not None:
            self._pump_hint.setText(
                "No pump selected — only the radiator fans will be set up. Choose the "
                "header above if your pump is connected to the motherboard."
            )
        else:
            self._pump_hint.setText("")

    # ── result ───────────────────────────────────────────────────────

    def get_result(self) -> dict:
        """Return the chosen setup (call after ``exec()`` returns accepted).

        ``pump_strategy`` is ``None`` when no pump will be set up; ``pump_pct`` is
        meaningful only for the ``fixed`` strategy. ``role_assignments`` is the
        list of ``(header_id, role)`` calls the caller must POST — ``role`` is
        ``None`` for a clear. It is empty unless the user actually changed the
        assignment, so accepting the dialog unchanged writes nothing.
        """
        pump_id = self.selected_pump_id()
        has_pump = self._has_pump()

        pump_pct: int | None = None
        strategy: str | None = None
        if has_pump:
            strategy = self._selected_strategy()
            if strategy == AIO_PUMP_STRATEGY_FIXED:
                checked = self._pump_buttons.checkedButton()
                if checked is not None:
                    pump_pct = int(checked.property("pump_pct"))

        radiator_members: list[dict] = []
        for i in range(self._fan_list.count()):
            item = self._fan_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                radiator_members.append(item.data(Qt.ItemDataRole.UserRole))

        sensor_id = self._sensor_combo.currentData() or ""
        return {
            "pump_pct": pump_pct,
            "pump_strategy": strategy,
            "pump_member_id": pump_id or None,
            "role_assignments": self._role_assignments(pump_id),
            "radiator_members": radiator_members,
            "radiator_sensor_id": sensor_id,
            # AIO-MB Phase 4: the assembly itself. The dialog has always
            # collected every part of this and discarded it — the topology was
            # reconstructible from the created controls only by re-inferring it
            # from labels. `coolant_sensor` is set only when the chosen sensor
            # really is a coolant reading, so a CPU-package fallback is not
            # mislabelled as coolant telemetry the machine does not have.
            "cooling_device": {
                "name": self._name_edit.text().strip() or DEFAULT_COOLING_DEVICE_NAME,
                "kind": COOLING_DEVICE_KIND_AIO,
                "pump_member": pump_id or None,
                "radiator_members": [m.get("id", "") for m in radiator_members if m.get("id")],
                "preferred_sensor": sensor_id or None,
                "coolant_sensor": sensor_id if self._has_coolant and sensor_id else None,
            },
        }

    def _role_assignments(self, pump_id: str) -> list[tuple[str, str | None]]:
        """The header-role writes implied by the picker, as ``(id, role|None)``.

        Only ever one assignment and at most one clear, and the clear is
        restricted to a role the *user* previously assigned (``role_source ==
        "user_assigned"``). A role the daemon inferred from the hardware label or
        the chip is not ours to remove — and clearing it would not remove it
        anyway, since a clear only drops the stored assignment and falls back to
        exactly that inference.

        **The assign is always emitted before the clear, and the order is the
        safety property** — not an artefact of candidate iteration order, which is
        what it was until the DEC-312 review found it. The caller applies these
        sequentially with no rollback and aborts on a failed assign, so
        clear-then-assign meant that moving the pump from a lower to a higher pwm
        index and *then* hitting a `503` left the old header stripped of its role
        and the new one never given it: the old pump loses its identify
        protection, and the dialog says "no controls were created". Assign-first
        makes both branches safe — a failed assign has changed nothing at all, and
        a failed clear only ever leaves a stale role behind, which can add a floor
        but never remove one.
        """
        if self._pump_combo is None:
            return []
        assigns: list[tuple[str, str | None]] = []
        clears: list[tuple[str, str | None]] = []
        for cand in self._pump_candidates:
            is_selected = cand["id"] == pump_id
            was_user_pump = (
                cand.get("role") == "pump" and cand.get("role_source") == "user_assigned"
            )
            if is_selected and cand.get("role") != "pump":
                assigns.append((cand["id"], "pump"))
            elif not is_selected and was_user_pump:
                clears.append((cand["id"], None))
        return assigns + clears
