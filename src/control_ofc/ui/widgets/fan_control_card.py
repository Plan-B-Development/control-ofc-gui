"""Dashboard fan tile (DEC-222, densified in DEC-238) — one per logical control.

Renders a :class:`~control_ofc.services.fan_cards_view.FanCardVM`: the control's
name, a **read-only** state chip, the RPM / SPEED / TEMP triple, a lightweight
curve preview, and an Edit button that deep-links to the Controls page.

DEC-238 rebuilt the geometry without changing what the tile says. Four things
were spending height: ``.Card``'s 12px QSS padding and the layout's own 12/10
margins were both charged, putting 25px between the border and the text; the
readings used the 2.2x hero type role; and the curve and the Edit button each
held a full row of their own. Now a scoped ``density="tile"`` rule zeroes the QSS
padding so the layout owns the inset once, the readings use a 1.5x role, Edit
rides in the title row as a ghost button, and the curve is a full-bleed band
pinned to the bottom inner edge. Measured 267x244 -> 235x135 at 10pt.

The band is a one-slot stack, so a tile whose curve paints a sparkline and a tile
whose curve reads as text (or has no curve at all) are exactly the same height —
the grid stays even instead of every tile inheriting the tallest one's slack.

The card is deliberately read-only. The daemon's live-intent API is control-keyed
and its take/renew/release session (deadman + monotonic fencing, DEC-163) is
owned by ``controls_page``; duplicating that here would mean two independent
sessions racing for the same control and two implementations of the same safety
logic. Editing therefore navigates to the surface that already owns it.

The curve preview reuses :class:`~control_ofc.ui.widgets.curve_card.CurvePreview`
— an owner-drawn painter with a constant, font-derived ``sizeHint``, so the
render→hint→grant→render ratchet that plagued the old pixmap preview cannot
recur here either.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.fan_cards_view import FanCardVM, FanState
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card
from control_ofc.ui.components.labels import ElidedLabel
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.card_metrics import fan_tile_width
from control_ofc.ui.widgets.curve_card import CurvePreview

# FanState → (chip text, chip QSS class). Text pairs with colour so the state is
# never colour-only (WCAG 1.4.1). "Auto" means the daemon's curve is driving it —
# the resting, healthy case.
_STATE_CHIP: dict[FanState, tuple[str, str]] = {
    FanState.NORMAL: ("Auto", "SuccessChip"),
    FanState.OVERRIDE: ("Override active", "WarningChip"),
    FanState.LOW_RPM: ("Low RPM", "WarningChip"),
    FanState.STALE: ("Stale", "WarningChip"),
    FanState.STALL: ("Stall", "CriticalChip"),
    FanState.OFFLINE: ("Offline", "CriticalChip"),
}


def _safe_tooltip(text: str) -> str:
    """Escape *text* for a tooltip and force Qt down the rich-text path.

    Escaping alone is not enough. Qt picks plain vs rich text with
    ``mightBeRichText()``, which looks for a ``<`` — and escaping removes every
    one, so an escaped string is rendered *plain* and the entities show through:
    a control named ``CPU & AIO`` displayed as ``CPU &amp; AIO``. ``&`` is common
    in fan names ("Front & Top"); ``<`` is not, so the failure mode is the
    ordinary case, not the adversarial one.

    The ``<html>`` wrapper makes Qt parse it, which both decodes the entities
    back to the literal characters and keeps the escaping doing its real job —
    untrusted profile/alias text can still never be interpreted as markup.
    """
    return f"<html>{escape(text)}</html>"


def _card_slug(control_id: str) -> str:
    """objectName-safe token for a control id (the Unassigned card has none).

    Read-only fan cards are keyed by fan id, which contains ``:`` separators —
    sanitise them so every card still gets a unique, well-formed objectName.
    """
    token = control_id or "unassigned"
    return "".join(c if c.isalnum() else "_" for c in token).strip("_") or "unassigned"


class _CurveBand(CurvePreview):
    """The tile's curve preview, sized as a band rather than a block (DEC-238).

    Only the *instance* geometry differs from :class:`CurvePreview` — the Controls
    page's CurveCard uses the same painter and is untouched. The hint is a
    function of the font alone, never of what was last painted, which is the
    invariant that keeps the DEC-129 render->hint->grant->render ratchet
    impossible here too.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Below the parent's 24px floor at small theme sizes: the whole tile
        # scales down with the font, so a fixed floor would leave the band
        # disproportionately tall at 7pt.
        self.setMinimumHeight(16)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(0, fm.height() + 6)


class FanControlCard(Card):
    """Compact read-only status card for one logical control."""

    # control_id of the card whose Edit was clicked ("" for the Unassigned card,
    # which has no control to focus — the page just opens Controls).
    edit_requested = Signal(str)
    # DEC-227: fan_id the user asked to rename. Read-only cards only — see
    # _renamable_fan_id for why a control card can never emit this.
    rename_requested = Signal(str)

    def __init__(self, vm: FanCardVM, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._control_id = vm.control_id
        self._rename_fan_id = self._renamable_fan_id(vm)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        slug = _card_slug(vm.card_key)
        self.setObjectName(f"FanCard_Root_{slug}")
        # Selects the scoped `.Card[density="tile"]` rule: zero QSS padding (the
        # layout below owns the inset instead, so the band can run full-bleed)
        # and a 6px radius. Set before first polish, so no repolish is needed.
        self.setProperty("density", "tile")
        self._apply_tile_width(active_theme())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The text rows carry the inset; the band deliberately sits outside it.
        body = QVBoxLayout()
        body.setContentsMargins(11, 9, 11, 7)
        body.setSpacing(6)
        layout.addLayout(body)

        # Row 1: name + the Edit deep-link. Edit rides here rather than owning a
        # row of its own — as a ghost button it reads as secondary to the
        # readings, and it stays a real focusable button rather than a hover-only
        # affordance. Only two things share this row: with the chip up here too,
        # the worst case ("Unassigned" + "Not controlled" + "Assign…") left the
        # name 62px and elided it to "Unas…", and the name is the identifier.
        head = QHBoxLayout()
        head.setSpacing(6)
        # Control names come from the profile and fan labels from user aliases —
        # untrusted text. ElidedLabel renders verbatim plain text (stray markup
        # can never be reinterpreted as rich text, matching warnings_view +
        # footer) and shrinks below its full width instead of widening the tile.
        self._name = ElidedLabel(vm.label, object_name=f"FanCard_Label_name_{slug}")
        self._name.setStyleSheet("font-weight: bold; background: transparent;")
        head.addWidget(self._name, 1)
        self._edit_btn = make_button("Edit", "ghost", object_name=f"FanCard_Btn_edit_{slug}")
        self._edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._control_id))
        # A read-only tile hides Edit, and the button is the tallest thing in this
        # row, so hiding it plainly leaves those tiles 4px short of their
        # neighbours in the grid. Retaining the hidden size fixes the height — but
        # it retains the *width* too, and read-only tiles are exactly the ones
        # with the longest labels (GPU model names), so ~44px sat blank while the
        # name lost the model number it needed to show. Retain the slot, then
        # collapse its width to nothing when the button is not there; the
        # remembered maximum restores it if a tile ever flips back.
        edit_policy = self._edit_btn.sizePolicy()
        edit_policy.setRetainSizeWhenHidden(True)
        self._edit_btn.setSizePolicy(edit_policy)
        self._edit_max_width = self._edit_btn.maximumWidth()
        head.addWidget(self._edit_btn)
        body.addLayout(head)

        # Row 2: how many fans this card actually moves — the honest blast radius
        # of anything done to this control — and the read-only state chip. The
        # count is one short token that was holding a ~180px row on its own, so
        # the chip costs no height by sitting at the other end of it, and lands
        # in the same right-hand column as Edit above.
        meta = QHBoxLayout()
        meta.setSpacing(6)
        self._count = QLabel("")
        self._count.setObjectName(f"FanCard_Label_fanCount_{slug}")
        self._count.setProperty("class", "CardMeta")
        self._count.setStyleSheet("background: transparent;")
        meta.addWidget(self._count)
        meta.addStretch(1)
        self._state_chip = QLabel("")
        self._state_chip.setObjectName(f"FanCard_Chip_state_{slug}")
        meta.addWidget(self._state_chip)
        body.addLayout(meta)

        # Row 3: the RPM / SPEED / TEMP triple, column-labelled above the values
        # and separated by 1px hairline rules (DEC-225). The values sit flush on
        # the card surface — no inset panel of a different tone behind them.
        # The three columns share the row equally (DEC-238) rather than each
        # taking its content width: with a fixed tile width that puts the two
        # hairlines at the same x on every tile, so the grid reads as columns
        # instead of three independently-ragged readings per card. What changed
        # the behaviour is dropping the trailing addStretch that used to swallow
        # the surplus; the explicit stretch factor here pins the split rather
        # than leaning on Qt's all-stretches-zero surplus heuristic to equalise.
        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        # Only SPEED's caption is retained: it is the one that changes at runtime
        # (SPEED ↔ DUTY). RPM and TEMP never relabel, so keeping handles to them
        # would be dead attributes.
        self._rpm_value, _ = self._add_metric(metrics, "RPM", f"FanCard_Value_rpm_{slug}")
        self._add_metric_divider(metrics, f"FanCard_Divider_rpmSpeed_{slug}")
        self._speed_value, self._speed_caption = self._add_metric(
            metrics, "SPEED", f"FanCard_Value_speed_{slug}"
        )
        self._add_metric_divider(metrics, f"FanCard_Divider_speedTemp_{slug}")
        self._temp_value, _ = self._add_metric(metrics, "TEMP", f"FanCard_Value_temp_{slug}")
        body.addLayout(metrics)

        # Row 4: the curve band — full-bleed to the tile's inner border, outside
        # the body inset. A one-slot stack, so the sparkline and the "nothing is
        # driving this" placeholder occupy the same space and every tile in the
        # grid ends up the same height whichever is showing.
        self._band = QStackedLayout()
        self._band.setContentsMargins(0, 0, 0, 0)
        self._preview = _CurveBand()
        self._preview.setObjectName(f"FanCard_Preview_curve_{slug}")
        self._preview.set_theme(active_theme())
        self._band.addWidget(self._preview)
        # Elided for the same reason as the name: the longest placeholder ("No fan
        # control available for this device") spans nearly the full band at 16pt,
        # and the band is full-bleed so there is no inset to absorb overflow.
        self._no_curve = ElidedLabel(
            "No curve assigned", object_name=f"FanCard_Label_noCurve_{slug}"
        )
        self._no_curve.setProperty("class", "CardMeta")
        self._no_curve.setStyleSheet("background: transparent;")
        self._no_curve.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_curve.setContentsMargins(6, 0, 6, 0)
        self._band.addWidget(self._no_curve)
        layout.addLayout(self._band)
        # NB for future tests: QStackedLayout lays out only the *current* widget,
        # so the other one keeps stale geometry. Asserting on
        # `_no_curve.elided_text()` / `_preview.painted_summary_text()` is only
        # meaningful for whichever the VM actually selected.

        self.update_vm(vm)

    def _apply_tile_width(self, tokens) -> None:
        """Pin the tile to the shared width for the theme's base font size.

        Fixed, not content-derived: content hints made a ragged run of tiles
        (267/267/267/251 for four), and a grid of near-but-not-quite-equal boxes
        reads as sloppy. The name elides rather than clipping, so a long control
        name costs legibility of the name alone, never the layout.
        """
        self.setFixedWidth(fan_tile_width(getattr(tokens, "base_font_size_pt", 10)))

    @staticmethod
    def _add_metric(row: QHBoxLayout, title: str, object_name: str) -> tuple[QLabel, QLabel]:
        """One labelled metric column; returns ``(value, caption)`` for updating."""
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        caption = QLabel(title)
        caption.setProperty("class", "CardMeta")
        caption.setStyleSheet("background: transparent;")
        value = QLabel("—")
        value.setObjectName(object_name)
        value.setProperty("class", "CardValue")
        value.setStyleSheet("background: transparent;")
        column_layout.addWidget(caption)
        column_layout.addWidget(value)
        row.addWidget(column, 1)
        return value, caption

    @staticmethod
    def _add_metric_divider(row: QHBoxLayout, object_name: str) -> None:
        """A 1px vertical hairline between two metric columns (DEC-225).

        Styled via the ``.CardDivider`` class so it tracks the active theme's
        border tone; the fixed 1px width keeps it a rule rather than a block."""
        divider = QFrame()
        divider.setObjectName(object_name)
        divider.setProperty("class", "CardDivider")
        divider.setFixedWidth(1)
        row.addWidget(divider)

    # ── updates ──────────────────────────────────────────────────────

    @staticmethod
    def _renamable_fan_id(vm: FanCardVM) -> str:
        """The fan this card can rename, or "" if renaming makes no sense here.

        Only a read-only card names a *fan*: it is built one-per-fan
        (``fan_cards_view``), so its label is that fan's ``fan_display_name`` and
        renaming it writes a GUI-owned alias. Every other card is titled with its
        control's ``name``, which is **profile data** — editing that is a profile
        write and stays on the Controls page (DEC-222).
        """
        if vm.is_read_only and len(vm.member_fan_ids) == 1:
            return vm.member_fan_ids[0]
        return ""

    def build_rename_menu(self) -> QMenu | None:
        """Build this card's context menu, or None when it names no fan.

        Split from showing it so the contents are assertable without a real popup.
        """
        if not self._rename_fan_id:
            return None
        menu = QMenu(self)
        rename = QAction("Rename fan…", self)
        rename.setObjectName("FanCard_Action_renameFan")
        rename.triggered.connect(lambda: self.rename_requested.emit(self._rename_fan_id))
        menu.addAction(rename)
        return menu

    def _on_context_menu(self, pos: QPoint) -> None:
        menu = self.build_rename_menu()
        if menu is not None:
            menu.exec(self.mapToGlobal(pos))

    def update_vm(self, vm: FanCardVM) -> None:
        """Re-render from a fresh VM. Cheap and idempotent (called each poll)."""
        self._control_id = vm.control_id
        self._rename_fan_id = self._renamable_fan_id(vm)
        self._name.setText(vm.label)
        # The tooltip is the only way back to a name the tile had to elide, so it
        # is set only when there is something to recover — an always-on tooltip
        # repeating a name that already fits is noise.
        self._name.setToolTip(
            _safe_tooltip(vm.label) if self._name.elided_text() != vm.label else ""
        )
        self._count.setText(
            "No fans assigned"
            if vm.fan_count == 0
            else f"{vm.fan_count} fan{'' if vm.fan_count == 1 else 's'}"
        )

        self._rpm_value.setText("—" if vm.rpm is None else str(vm.rpm))
        # Commanded PWM wins over measured duty when both exist; duty is labelled
        # so it is never misread as a value the daemon commanded (DEC-204). The
        # qualifier lives in the *caption* — the column header is what names the
        # quantity, and "37% duty" as a value was twice the width of its
        # neighbours, so the widest tile was set by the rarest reading.
        if vm.pwm_pct is not None:
            self._speed_caption.setText("SPEED")
            self._speed_value.setText(f"{vm.pwm_pct}%")
        elif vm.duty_pct is not None:
            self._speed_caption.setText("DUTY")
            self._speed_value.setText(f"{vm.duty_pct}%")
        else:
            self._speed_caption.setText("SPEED")
            self._speed_value.setText("—")
        self._speed_caption.setToolTip(
            "Measured duty cycle read back from the device — not a value the daemon commanded"
            if vm.pwm_pct is None and vm.duty_pct is not None
            else ""
        )
        self._temp_value.setText("—" if vm.temp_c is None else f"{vm.temp_c:.0f}°C")

        text, css = _STATE_CHIP.get(vm.state, ("Auto", "SuccessChip"))
        # "Auto" would be a lie for a fan nothing is driving — say what is
        # actually true and keep the chip informational.
        if vm.state == FanState.NORMAL:
            if vm.is_read_only:
                text, css = "Read-only", "InfoChip"
            elif vm.is_unassigned:
                text, css = "Not controlled", "InfoChip"
            elif vm.fan_count == 0:
                # A control the user just created, before assigning any fan.
                text, css = "No fans", "InfoChip"
        self._state_chip.setText(text)
        set_chip_class(self._state_chip, css, skip_if_unchanged=True)

        # The band shows one of its two widgets; the stack hides the other, so
        # the tile's height is identical either way.
        if vm.curve is not None:
            self._preview.set_curve(vm.curve)
            self._band.setCurrentWidget(self._preview)
        else:
            self._band.setCurrentWidget(self._no_curve)
            if vm.is_read_only:
                placeholder = "No fan control available for this device"
            elif vm.is_unassigned:
                placeholder = "Not assigned to a control"
            else:
                placeholder = "No curve assigned"
            self._no_curve.setText(placeholder)
            # The longest form elides in the band at 16pt; keep it reachable.
            self._no_curve.setToolTip(placeholder)

        # A read-only fan cannot be assigned to a control at all (the member
        # picker refuses it, DEC-102), so offering Edit would be a dead button.
        self._edit_btn.setVisible(not vm.is_read_only)
        self._edit_btn.setMaximumWidth(0 if vm.is_read_only else self._edit_max_width)
        self._edit_btn.setText("Assign…" if vm.is_unassigned else "Edit")
        self._edit_btn.setToolTip(
            "Open the Controls page to assign these fans to a control"
            if vm.is_unassigned
            else _safe_tooltip(f"Edit “{vm.label}” on the Controls page")
        )

    def set_theme(self, tokens) -> None:
        """Forward the palette to the owner-drawn preview (DEC-109).

        Also re-pins the tile width: a theme change can carry a new base font
        size, and a tile still sized for the old one would either clip its
        readings or sit out of column with its neighbours.
        """
        self._preview.set_theme(tokens)
        self._apply_tile_width(tokens)
