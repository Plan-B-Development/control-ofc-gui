"""DEC-219: the System State page's four display cards, carved out of the
former god-class page (widget decomposition, Phase 7.1).

Each card owns its widgets and a ``render(vm)`` method fed the Qt-free
view-models from ``services.system_state_view``; the page composes the cards and
routes the VM. Qt-UI only — no logic lives here. objectNames are preserved
verbatim from the page (the DEC-219 golden-master pins them).
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import BracketCard, Card, ContentSizedCard, SectionHeader
from control_ofc.ui.components.gauges import RadialGauge
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

_REGISTRY_COLS = ["Status", "Chip / Component", "Driver", "Driver Status", "Mainline", "Headers"]
_REG_STATUS = 0
_REG_DRIVER_STATUS = 3
_REG_MAINLINE = 4

#: Width of the throwaway rect handed to ``SE_ItemViewItemText`` when measuring
#: the style's cell inset (`ACK-q`). Any width wider than the padding works —
#: the answer taken from it is ``probe - text_rect.width()``, so the value
#: cancels out; it is named rather than inlined so it cannot be read as a
#: layout constant.
_INSET_PROBE_WIDTH = 200

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


def _silence_actions(silence, prefix: str, key: str, on_ack, on_dismiss) -> QHBoxLayout | None:
    """The Acknowledge/Dismiss row, shared by every silenceable surface (DEC-359).

    Extracted from ``_make_note_row`` rather than merged with it. The plan for
    this change proposed fusing ``_make_issue_card`` and ``_make_note_row`` into
    one builder; reading them shows that is wrong — one is a ``BracketCard``
    with a description and a doc button, the other a ``Card`` with evidence
    text, a collapsible detail and greying. Fusing them would add conditionals,
    not remove them. What they genuinely share is *this*, and `CLAUDE.md`'s rule
    is the one that applies: when a second surface needs a rule, extraction is
    the change.

    Returns ``None`` when nothing can be pressed, so no caller has to reproduce
    the "only add the row if it has buttons" test.
    """
    if not silence.token or not (silence.can_acknowledge or silence.can_dismiss):
        return None
    actions = QHBoxLayout()
    actions.addStretch(1)
    if silence.can_acknowledge:
        label = "Unacknowledge" if silence.acknowledged else "Acknowledge"
        btn = make_button(label, "ghost", object_name=f"{prefix}AckBtn_{key}")
        btn.clicked.connect(
            lambda _=False, t=silence.token, on=not silence.acknowledged: on_ack(t, on)
        )
        actions.addWidget(btn)
    if silence.can_dismiss and not silence.dismissed:
        btn = make_button("Dismiss", "ghost", object_name=f"{prefix}DismissBtn_{key}")
        btn.clicked.connect(lambda _=False, t=silence.token: on_dismiss(t))
        actions.addWidget(btn)
    return actions


def _make_issue_card(vm, on_ack=None, on_dismiss=None) -> QWidget:
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
    # `ACK-r`: the caption shares its row with an "Acknowledged" marker, the
    # same shape and the same words `_make_note_row` already uses — the VM has
    # neutralised `severity_state` by this point, so without the pill the card
    # just goes quietly grey and nothing on screen says why.
    #
    # The marker keys on `acknowledged` and the demotion on `quiet`, and that is
    # deliberate rather than an oversight: the pill NAMES an action, so it must
    # not appear for a silence taken some other way, while the greying follows
    # whatever made the card quiet. The two coincide today because a dismissed
    # condition is filtered out of the list before it reaches this renderer. If
    # that ever changes, the pill needs wording for the other case — it must not
    # simply be re-pointed at `quiet`.
    head = QHBoxLayout()
    head.setSpacing(8)
    caption = QLabel(f"{vm.severity_glyph} {vm.severity_word}")
    caption.setObjectName(f"SystemState_IssueSeverity_{vm.key}")
    caption.setStyleSheet(f"color: {color}; font-weight: bold;")
    head.addWidget(caption)
    head.addStretch(1)
    if vm.silence.acknowledged:
        head.addWidget(
            StatusPill("Acknowledged", "neutral", object_name=f"SystemState_IssueAck_{vm.key}")
        )
    v.addLayout(head)
    title = QLabel(vm.title)
    title.setObjectName(f"SystemState_IssueTitle_{vm.key}")
    title.setWordWrap(True)
    # An acknowledged condition stays legible but stops competing for attention
    # — the board note's rule, applied to the surface above it.
    title.setStyleSheet(
        f"color: {theme.text_muted if vm.silence.quiet else theme.text_primary}; font-weight: 600;"
    )
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
    if on_ack is not None and on_dismiss is not None:
        actions = _silence_actions(vm.silence, "SystemState_IssueCard", vm.key, on_ack, on_dismiss)
        if actions is not None:
            v.addLayout(actions)
    row.addWidget(body, 1)
    return card


def _make_note_row(vm, on_ack, on_dismiss) -> QWidget:
    """One board note: severity caption, evidence, title, collapsed detail.

    DEC-357 restores the two DEC-158 rules the DEC-211 page migration dropped.

    * **Progressive disclosure.** The detail lives in a ``CollapsibleSection``
      opened from ``SeverityDisplay.default_expanded`` — the field that had no
      production consumer at all while a test went on asserting its values. An
      always-expanded detail box per advisory is what made this panel 1336px of
      prose on a healthy machine (Nielsen: defer rarely-needed detail, and no
      more than two levels).
    * **Four hues.** The caption takes the themed chip class directly, so
      MEDIUM/LOW paint amber and INFO blue instead of all three borrowing HIGH's
      orange from the pill vocabulary. Via ``set_chip_class`` rather than an
      interpolated inline stylesheet, so it survives a live theme change.
    """
    theme = active_theme()
    card = Card()
    # `Card` takes no object_name parameter (unlike SectionHeader/RadialGauge/
    # make_button). Set here rather than widening a shared primitive in this
    # change — recorded as register row `SSN-j`.
    card.setObjectName(f"SystemState_BoardNote_{vm.key}")
    v = QVBoxLayout(card)
    v.setSpacing(4)

    head = QHBoxLayout()
    head.setSpacing(8)
    caption = QLabel(f"{vm.severity_glyph} {vm.severity_word}")
    caption.setObjectName(f"SystemState_BoardNoteSeverity_{vm.key}")
    set_chip_class(caption, vm.severity_css)
    head.addWidget(caption)
    evidence = QLabel(vm.evidence_text)
    evidence.setObjectName(f"SystemState_BoardNoteEvidence_{vm.key}")
    evidence.setProperty("class", "CardMeta")
    head.addWidget(evidence)
    head.addStretch(1)
    if vm.silence.acknowledged:
        head.addWidget(
            StatusPill("Acknowledged", "neutral", object_name=f"SystemState_BoardNoteAck_{vm.key}")
        )
    v.addLayout(head)

    title = QLabel(vm.title)
    title.setObjectName(f"SystemState_BoardNoteTitle_{vm.key}")
    title.setWordWrap(True)
    # An acknowledged note stays legible but stops competing for attention.
    title.setStyleSheet(
        f"color: {theme.text_muted if vm.silence.acknowledged else theme.text_primary};"
        " font-weight: 600;"
    )
    v.addWidget(title)

    if vm.detail:
        section = CollapsibleSection(
            "Details",
            object_name=f"SystemState_BoardNoteDetails_{vm.key}",
            # An acknowledged note always starts closed: the user has said they
            # have read it, and collapsing it is the whole point of the button.
            # A runtime expansion the user made is carried back over this by
            # `HealthCard._rebuild_board_notes` (`ACK-v`).
            expanded=vm.default_expanded and not vm.silence.acknowledged,
        )
        box = QLabel(vm.detail)
        box.setObjectName(f"SystemState_BoardNoteDetail_{vm.key}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setWordWrap(True)
        box.setOpenExternalLinks(True)
        box.setStyleSheet(
            f"background: {theme.surface_2}; border: 1px solid {theme.border_default};"
            " border-radius: 4px; padding: 6px;"
        )
        section.add_widget(box)
        v.addWidget(section)

    # The same row, and now the same carrier, as every other silenceable
    # surface (`ACK-o`). This used to adapt four flat `BoardNoteVM` fields into
    # a throwaway `SilenceVM` here — one concept in two shapes, with this call
    # the place they could drift. The objectNames are unchanged: they key off
    # `vm.key`, which the reshape never touched.
    actions = _silence_actions(vm.silence, "SystemState_BoardNote", vm.key, on_ack, on_dismiss)
    if actions is not None:
        v.addLayout(actions)
    return card


# ── cards ────────────────────────────────────────────────────────────────


class HealthCard(ContentSizedCard):
    """System Health Overview — issue-count pill, summary line, the
    severity-sorted condition cards, and the collapsed board-notes section
    (DEC-357). The page also pushes fetch/error text through ``set_summary()``.

    The two halves answer different questions and are deliberately not ranked
    together. A **condition** was measured on this machine and clears itself
    when the user fixes it. A **board note** is reference material keyed on
    which motherboard you bought; it can never clear, so presenting it as an
    alarm trains the reader to ignore the stack that holds the real ones.
    """

    #: (ack_key, acknowledged) — the user (un)acknowledged one note.
    note_acknowledged = Signal(str, bool)
    #: (ack_key) — the user dismissed one note.
    note_dismissed = Signal(str)
    #: The user asked to settle an unverified note by testing fan control.
    verify_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_health")
        #: note key -> was it acknowledged at the previous render (`ACK-v`).
        self._note_was_acknowledged: dict[str, bool] = {}
        v = QVBoxLayout(self)
        header = SectionHeader(
            "System Health Overview", object_name="SystemState_SectionHeader_health"
        )
        self._issue_pill = StatusPill("—", "neutral")
        self._issue_pill.setObjectName("SystemState_Pill_issueCount")
        header.add_trailing(self._issue_pill)
        self._header = header
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

        # Board notes — one collapsed section, below the conditions. The header
        # stays visible so the information is never lost, only deferred; the
        # count is on it so the reader knows what is behind it before opening.
        self._notes_section = CollapsibleSection(
            "Board notes", object_name="SystemState_Section_boardNotes", expanded=False
        )
        self._notes_subtitle = QLabel("")
        self._notes_subtitle.setObjectName("SystemState_Label_boardNotesSubtitle")
        self._notes_subtitle.setProperty("class", "CardMeta")
        self._notes_subtitle.setWordWrap(True)
        self._notes_section.add_widget(self._notes_subtitle)

        self._verify_btn = make_button(
            "Test fan control", "secondary", object_name="SystemState_Btn_verifyBoardNotes"
        )
        self._verify_btn.clicked.connect(self.verify_requested)
        self._verify_row = QWidget()
        self._verify_row.setObjectName("SystemState_Row_verifyBoardNotes")
        verify_layout = QHBoxLayout(self._verify_row)
        verify_layout.setContentsMargins(0, 0, 0, 0)
        self._verify_hint = QLabel("")
        self._verify_hint.setObjectName("SystemState_Label_verifyHint")
        self._verify_hint.setProperty("class", "CardMeta")
        self._verify_hint.setWordWrap(True)
        verify_layout.addWidget(self._verify_hint, 1)
        verify_layout.addWidget(self._verify_btn)
        self._notes_section.add_widget(self._verify_row)

        self._notes_container = QWidget()
        self._notes_layout = QVBoxLayout(self._notes_container)
        self._notes_layout.setContentsMargins(0, 0, 0, 0)
        self._notes_layout.setSpacing(8)
        self._notes_section.add_widget(self._notes_container)
        v.addWidget(self._notes_section)
        v.addStretch(1)

    def add_header_action(self, widget) -> None:
        """Put a control on this card's header, right-aligned.

        The card owns its header, so the page cannot reach past it — DEC-358
        needed the report and refresh buttons on the one card that is always
        on screen, and reaching into ``_header`` from the page would have been
        the shared-primitive rule broken from the other side.
        """
        self._header.add_action(widget)

    def set_summary(self, text: str) -> None:
        self._summary_label.setText(text)

    def render(self, vm) -> None:
        self._issue_pill.set_text(vm.issue_count_label)
        self._issue_pill.set_state(vm.issue_count_state)
        summary = vm.summary_line
        if vm.board_line:
            summary = f"{summary}  ·  {vm.board_line}"
        self._summary_label.setText(summary)
        self._rebuild_issue_cards(vm.issue_cards, vm.conditions_hidden_count)
        self._rebuild_board_notes(vm.board_notes)

    def _rebuild_issue_cards(self, cards, hidden: int = 0) -> None:
        _clear_layout(self._issues_layout)
        if not cards:
            ok = QLabel(
                "No active conditions — fan control is available."
                if not hidden
                # Never "all clear" while something is merely hidden. The pill
                # above still counts the dismissed conditions, so claiming the
                # machine is healthy here would make the card contradict its own
                # header — and the pill is the half that is right.
                else "No conditions shown — every active one is dismissed."
            )
            ok.setObjectName("SystemState_Label_noIssues")
            ok.setProperty("class", "CardMeta")
            self._issues_layout.addWidget(ok)
        else:
            for vm in cards:
                self._issues_layout.addWidget(
                    _make_issue_card(vm, self.note_acknowledged.emit, self.note_dismissed.emit)
                )
        if hidden:
            # What reconciles the pill with a shorter list. Without it the two
            # simply disagree and the user has no way to find out why.
            note = QLabel(f"{hidden} dismissed — restore in Settings ▸ Prompts & Dismissals.")
            note.setObjectName("SystemState_Label_hiddenConditions")
            note.setProperty("class", "CardMeta")
            self._issues_layout.addWidget(note)

    def _carried_note_expansion(self, vm) -> dict[str, bool]:
        """Which notes the user has open right now, to survive the rebuild.

        `ACK-v`. Every re-render destroys and recreates these sections, so an
        open detail snapped shut under the reader. DEC-358 made that reachable
        with no user action at all — a `normal`/`recovery`/`emergency`
        transition re-renders the page — but it was already reachable by
        acknowledging one note while another was open, so the carry is keyed on
        the section rather than on which trigger fired.

        The case that must NOT carry is a note whose acknowledgement the user
        has just **changed**, in either direction: collapsing it is what
        Acknowledge buys, and re-opening a note that opens by default is what
        Unacknowledge gives back. Both are decided here, against the previous
        render's flags, because by the time ``_make_note_row`` runs a state the
        user chose and a state a button imposed look identical.

        The reverse edge was missed in the first draft and found by
        ``ofc:python-gui-reviewer``: carrying it restored the collapse that
        Acknowledge had imposed, so Unacknowledge left a HIGH note shut when it
        used to reopen it — the `ACK-r` shape (a button whose only visible
        effect is on itself) reintroduced by the fix for `ACK-v`.
        """
        carried: dict[str, bool] = {}
        for i in range(self._notes_layout.count()):
            row = self._notes_layout.itemAt(i).widget()
            if row is None:
                continue
            for section in row.findChildren(CollapsibleSection):
                key = section.objectName().removeprefix("SystemState_BoardNoteDetails_")
                carried[key] = section.is_expanded()
        for note in vm.notes:
            was = self._note_was_acknowledged.get(note.key)
            if was is not None and was != note.silence.acknowledged:
                carried.pop(note.key, None)
        self._note_was_acknowledged = {n.key: n.silence.acknowledged for n in vm.notes}
        return carried

    def _rebuild_board_notes(self, vm) -> None:
        carried = self._carried_note_expansion(vm)
        _clear_layout(self._notes_layout)
        self._notes_section.set_title(vm.title)
        parts = [vm.subtitle]
        if vm.related_count:
            n = vm.related_count
            parts.append(f"{n} relate{'s' if n == 1 else ''} to a condition above")
        if vm.hidden_count:
            parts.append(f"{vm.hidden_count} dismissed (restore in Settings)")
        if vm.acknowledged_count:
            parts.append(f"{vm.acknowledged_count} acknowledged")
        self._notes_subtitle.setText(" · ".join(parts))
        # Only offer the test when it would actually settle something.
        self._verify_row.setVisible(vm.unverified_count > 0)
        if vm.unverified_count:
            n = vm.unverified_count
            self._verify_hint.setText(
                f"{n} note{'' if n == 1 else 's'} can only be confirmed or ruled out by "
                "writing to a fan header."
            )
        self._notes_section.setVisible(vm.total > 0)
        for note in vm.notes:
            row = _make_note_row(note, self.note_acknowledged.emit, self.note_dismissed.emit)
            if note.key in carried:
                section = row.findChild(
                    CollapsibleSection, f"SystemState_BoardNoteDetails_{note.key}"
                )
                if section is not None:
                    section.set_expanded(carried[note.key])
            self._notes_layout.addWidget(row)


class InterferenceCard(ContentSizedCard):
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
        # Wraps because this text is an *unbounded* content minimum otherwise.
        # A contended header id is a stable hwmon id — `hwmon:nct6687:platform-
        # nct6687.2592:pwm3:CPU_FAN` — rendered in a monospace label with no
        # wrap and no elision, so the label's minimum width is the full string:
        # measured 345px, taking this card's minimum from 205 to 367 and the
        # status column's from 205 to 419 the moment any contention is detected.
        # That floor exceeded the column's own share and stole width from the
        # health card beside it, which is a share of the squeeze this layout
        # change exists to remove. QLabel breaks an over-long unbreakable token
        # anywhere it must, so wrapping bounds the card at 252px (measured) and
        # keeps the id fully readable rather than eliding it.
        self._header_id_label.setWordWrap(True)
        v.addWidget(self._header_id_label)

        self._interference_explain = QLabel("")
        self._interference_explain.setObjectName("SystemState_Label_interferenceExplain")
        self._interference_explain.setProperty("class", "CardMeta")
        self._interference_explain.setWordWrap(True)
        v.addWidget(self._interference_explain)
        self._actions_layout = QVBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self._actions_layout)

    #: (token, acknowledged) / (token) — DEC-359, same shape as HealthCard's.
    note_acknowledged = Signal(str, bool)
    note_dismissed = Signal(str)

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
        # Demote-not-delete: the gauge, the count and the header keep rendering
        # exactly as they did; `severity_state` is what the VM neutralises. The
        # reading stays true and only the alarm stops (DEC-359).
        _clear_layout(self._actions_layout)
        actions = _silence_actions(
            vm.silence,
            "SystemState_Interference",
            "monitor",
            self.note_acknowledged.emit,
            self.note_dismissed.emit,
        )
        if actions is not None:
            self._actions_layout.addLayout(actions)


class SafetyCard(ContentSizedCard):
    """Safety & GPU limits — CPU thermal state, GPU rows, firmware speed range."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SystemState_Card_safety")
        v = QVBoxLayout(self)
        v.addWidget(
            SectionHeader("Safety & GPU Limits", object_name="SystemState_SectionHeader_safety")
        )

        self._thermal_actions = QVBoxLayout()
        self._thermal_actions.setContentsMargins(0, 0, 0, 0)
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
        v.addLayout(self._thermal_actions)

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

    #: (token, acknowledged) / (token) — DEC-359, same shape as HealthCard's.
    note_acknowledged = Signal(str, bool)
    note_dismissed = Signal(str)

    def render(self, vm) -> None:
        # DEMOTE, NEVER REMOVE. The state and the per-machine limit stay on
        # screen whatever the user has silenced — `vm.thermal_state` is already
        # neutralised by the VM, so quietening drops the colour and nothing
        # else. Deleting a thermal reading to make the page quieter would make
        # it less true, and the live emergency reaches the user through
        # StatusBanner / footer / ribbon regardless, none of which consults a
        # silencing list (DEC-165 — the daemon acts either way).
        thermal = vm.thermal_text
        if vm.thermal_limit_text:
            thermal = f"{thermal}  ({vm.thermal_limit_text})"
        self._thermal_label.setText(thermal)
        self._thermal_pill.set_text(vm.thermal_text)
        self._thermal_pill.set_state(vm.thermal_state)
        _clear_layout(self._thermal_actions)
        thermal_actions = _silence_actions(
            vm.thermal_silence,
            "SystemState_Thermal",
            "cpu",
            self.note_acknowledged.emit,
            self.note_dismissed.emit,
        )
        if thermal_actions is not None:
            self._thermal_actions.addLayout(thermal_actions)

        _clear_layout(self._gpu_rows_layout)
        self._gpu_model_label.setText(vm.gpu_model if vm.has_gpu else "No discrete GPU detected")
        theme = active_theme()
        for i, r in enumerate(vm.gpu_rows):
            label = QLabel(f"{r.label}: {r.value}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_row_state_color(r.state, theme)};")
            self._gpu_rows_layout.addWidget(label)
            row_actions = _silence_actions(
                r.silence,
                "SystemState_GpuRow",
                str(i),
                self.note_acknowledged.emit,
                self.note_dismissed.emit,
            )
            if row_actions is not None:
                self._gpu_rows_layout.addLayout(row_actions)

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
        header = self._registry_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # `ACK-q`, second half. The six columns wanted 812px against a 753px
        # viewport at the default window size, so `Headers` was clipped to
        # "Hea…" on the shipped release screenshot. The surplus all sits in
        # `Driver Status`, whose widest real value is a whole sentence
        # ("it87 loaded (out-of-tree: it87-dkms-git (AUR))", 307px), and
        # `ResizeToContents` sizes a column to the longest cell in it — so that
        # one string set the table's width and every other column paid for it.
        # Stretching it instead hands it whatever is left, the default delegate
        # elides what does not fit, and `render` puts the full text in the
        # cell's tooltip. Measured after the change: 753px total, zero overflow.
        #
        # `setStretchLastSection` has to go with it — two sections cannot both
        # absorb the surplus, and `Headers` is the narrow one that should keep
        # its content width.
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_REG_DRIVER_STATUS, QHeaderView.ResizeMode.Stretch)
        # Fixed, because the width this column needs is the one thing
        # `ResizeToContents` cannot see — see `_status_column_width`.
        header.setSectionResizeMode(_REG_STATUS, QHeaderView.ResizeMode.Fixed)
        v.addWidget(self._registry_table)

    def _status_column_width(self) -> int:
        """Narrowest the Status column can be without clipping its pill.

        `ACK-q`. Measured on the reference board at the shipped window size:
        ``ResizeToContents`` sized this column to **81px** against a **101px**
        need (the widest pill's 69px hint, plus the holder's 12px of margins,
        plus the style's 20px inset), so the pill was handed 49px of the 66px
        it asked for and the release screenshot shipped `LOADE` and `MODU`.
        The mechanism is that the column holds a *cell widget*, and
        ``sizeHintForColumn`` asks the delegate about the ITEM — which is empty
        here — so the widget's own requirement never reaches the header. The
        same family as DEC-314: the unit test proves you answered, never that
        you were asked.

        Three terms, each measured rather than written down:

        * the widest pill actually rendered, from its own ``sizeHint``;
        * the holder layout's margins, read off that layout;
        * the item inset the style applies to a cell widget, taken from
          ``SE_ItemViewItemText``. That is the QSS ``.DenseTable::item``
          padding (10px each side today) and it tracks the theme — measured
          against a placed widget as ``visualRect().width() - holder.width()``
          and the two agree at 20.

        Falls back to the header's own hint before the first render, when there
        is no pill to measure.
        """
        header = self._registry_table.horizontalHeader()
        widest = 0
        for row in range(self._registry_table.rowCount()):
            holder = self._registry_table.cellWidget(row, _REG_STATUS)
            if holder is None:
                continue
            layout = holder.layout()
            pill = layout.itemAt(0).widget()
            margins = layout.contentsMargins()
            widest = max(widest, pill.sizeHint().width() + margins.left() + margins.right())
        if not widest:
            return header.sectionSizeHint(_REG_STATUS)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, _INSET_PROBE_WIDTH, 0)
        text_rect = self._registry_table.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, option, self._registry_table
        )
        inset = _INSET_PROBE_WIDTH - text_rect.width()
        return max(header.sectionSizeHint(_REG_STATUS), widest + inset)

    def content_min_width(self) -> int:
        """Narrowest this card can be and still show all six column headers.

        DERIVED from the table rather than written down, for the DEC-258 reason:
        a literal silently tracks today's column set and the theme's base font,
        and drifts the moment either moves. ``sectionSizeHint`` is each column's
        content-based hint and is stable at every widget width — unlike
        ``QHeaderView.length()``, which is the *realised* width of the sections
        and is inflated by whatever free space the table happens to have
        (measured 638 against a true 540), so length() would bake that surplus
        into the floor. `ACK-q` moved which column absorbs that surplus — it is
        `Driver Status` under ``Stretch`` now, rather than the last section —
        but not the fact that it exists, so this reasoning is unchanged.

        Rows wider than the floor still scroll inside the table. The floor only
        keeps the *headers* — Status, Chip / Component, Driver, Driver Status,
        Mainline, Headers — from being dragged or resized out of reach.

        `ACK-q`: Status is the one column whose real requirement is not its
        header, because a pill has to fit inside it, so it contributes
        :meth:`_status_column_width` instead. Without that the floor promised
        room for a header while the cell under it clipped.
        """
        header = self._registry_table.horizontalHeader()
        columns = sum(
            self._status_column_width() if c == _REG_STATUS else header.sectionSizeHint(c)
            for c in range(header.count())
        )
        margins = self.layout().contentsMargins()
        chrome = margins.left() + margins.right() + 2 * self._registry_table.frameWidth()
        # The scrollbar takes its width out of the viewport as soon as there are
        # more rows than fit, so a floor computed without it leaves the pane a
        # scrollbar-width short at the boundary — the same shape as the pane
        # literal in `widgets/card_metrics.card_pane_min_width`. Measured from the
        # real scrollbar rather than written down as a literal.
        #
        # It has to be THAT widget's own hint. Asking the card's or the table's
        # style for PM_ScrollBarExtent returns Qt's unstyled 14px, because the
        # theme sets the width through a `QScrollBar:vertical` QSS rule and QSS
        # is resolved per-widget — the scrollbar this table will actually grow
        # is 8px wide. Both numbers look equally plausible in isolation; only
        # the one taken from the scrollbar itself tracks the theme.
        scrollbar = self._registry_table.verticalScrollBar().sizeHint().width()
        return columns + chrome + scrollbar

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
            # `ACK-q`: Driver Status now stretches and therefore elides, so the
            # full sentence has to be recoverable. It leads the tooltip; the
            # chip guidance, where there is any, keeps its place below it.
            #
            # Composed from the VM alone, never by reading the item's own
            # previous tooltip. `_ensure_items` REUSES items across renders, and
            # the loop above only overwrites them when `vm.tooltip` is truthy —
            # which it never is for a kernel-module row — so a read-then-prepend
            # accumulated one copy per render, and this page re-renders on every
            # acknowledgement, dismissal and theme change (measured: four
            # renders, four copies). Found by `ofc:python-gui-reviewer`.
            if vm.driver_status:
                detail = f"{vm.driver_status}\n\n{vm.tooltip}" if vm.tooltip else vm.driver_status
                self._registry_table.item(i, _REG_DRIVER_STATUS).setToolTip(detail)
            _set_pill(self._registry_table, i, _REG_STATUS, vm.status_label, vm.status_state)
        # After the pills exist, because the width is derived from them, and the
        # card's floor has to move with it — the page re-asserts that floor only
        # on a theme change, so a card that never re-derived here would promise
        # a pane narrow enough to clip what it just rendered.
        self._registry_table.setColumnWidth(_REG_STATUS, self._status_column_width())
        self.setMinimumWidth(self.content_min_width())
