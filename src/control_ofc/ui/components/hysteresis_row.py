"""The per-curve slow-down band editor row (DEC-489).

One component for both curve editors — the embedded point editor (graph,
stepped) and the curve dialog (linear and the types without a band) — so the
wording, bounds and applicability rule cannot drift between them. The rules
themselves live Qt-free in ``services/curve_hysteresis.py``.

The row never decides whether the daemon supports the band; its owner hides it
unless the daemon advertises ``control.curve_hysteresis``.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.curve_hysteresis import (
    HYSTERESIS_DEFAULT_C,
    HYSTERESIS_MAX_C,
    HYSTERESIS_STEP_C,
    curve_uses_hysteresis,
    effective_hysteresis_c,
    not_applicable_reason,
)
from control_ofc.services.profile_service import CurveConfig
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.qt_util import block_signals

DEMO_NOTE = "Demo mode does not simulate the slow-down band; the daemon applies it."

SPIN_TOOLTIP = (
    "While the temperature falls, the fans keep their speed until it has dropped "
    "this far below where the speed last changed, so they do not step down at every "
    "small dip. A rise speeds them up at once, a steady temperature lets the speed "
    "settle after a short hold, and the fans never run slower than the curve asks. "
    f"Off follows the curve exactly. Default: {HYSTERESIS_DEFAULT_C:g} °C."
)


class HysteresisRow(QWidget):
    """Label + spin box + "Apply to all curves", with a caption line below.

    ``value_changed`` fires only for a user edit (loading a curve is silent), so
    an owner that writes the value back on it never turns an untouched curve's
    absent band into an explicit one.

    With ``apply_all_checkable`` the button is a toggle read on save
    (``apply_all_requested``) — for the modal dialog, whose edits all land on
    Save. Otherwise it is an action that fires ``apply_all_clicked`` at once.
    """

    value_changed = Signal(float)
    apply_all_clicked = Signal(float)

    def __init__(
        self,
        *,
        object_name: str,
        apply_all_checkable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._touched = False
        self._applicable = True
        self._demo = False
        self._reason = ""  # why the loaded curve has no band; empty when it has one

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        row = QHBoxLayout()
        self._label = QLabel("Slow-down band:")
        row.addWidget(self._label)

        self._spin = QDoubleSpinBox()
        self._spin.setObjectName(f"{object_name}_Spin")
        self._spin.setRange(0.0, HYSTERESIS_MAX_C)
        self._spin.setSingleStep(HYSTERESIS_STEP_C)
        self._spin.setDecimals(1)
        self._spin.setSuffix(" °C")
        self._spin.setSpecialValueText("Off")
        self._spin.setValue(HYSTERESIS_DEFAULT_C)
        self._spin.setToolTip(SPIN_TOOLTIP)
        name_value_control(self._spin, self._label)
        self._spin.valueChanged.connect(self._on_spin_changed)
        row.addWidget(self._spin)

        self._apply_btn = make_button(
            "Apply to all curves", "ghost", object_name=f"{object_name}_Btn_applyAll"
        )
        self._apply_btn.setCheckable(apply_all_checkable)
        self._apply_btn.setToolTip(
            "Use this band on every graph, stepped and linear curve in the profile"
            + (" when you save." if apply_all_checkable else ".")
        )
        if not apply_all_checkable:
            self._apply_btn.clicked.connect(lambda: self.apply_all_clicked.emit(self._spin.value()))
        row.addWidget(self._apply_btn)
        row.addStretch()
        outer.addLayout(row)

        self._caption = QLabel("")
        self._caption.setObjectName(f"{object_name}_Label_caption")
        self._caption.setProperty("class", "PageSubtitle")
        self._caption.setWordWrap(True)
        self._caption.hide()
        outer.addWidget(self._caption)

    # ─── Public API ───────────────────────────────────────────────────

    def set_curve(self, curve: CurveConfig) -> None:
        """Load *curve*'s band without signalling, and enable the row for it."""
        self._touched = False
        self._applicable = curve_uses_hysteresis(curve.type)
        with block_signals(self._spin):
            self._spin.setValue(effective_hysteresis_c(curve))
        self._spin.setEnabled(self._applicable)
        self._apply_btn.setEnabled(self._applicable)
        if not self._apply_btn.isCheckable() or not self._applicable:
            self._apply_btn.setChecked(False)
        self._refresh_caption(curve)

    def set_demo(self, demo: bool) -> None:
        self._demo = demo
        self._refresh_caption(None)

    def value(self) -> float:
        return self._spin.value()

    def is_applicable(self) -> bool:
        return self._applicable

    def is_touched(self) -> bool:
        """Whether the user changed the value since the curve was loaded."""
        return self._touched

    def apply_all_requested(self) -> bool:
        """Checkable mode: whether the user asked to apply the band to all curves."""
        return self._applicable and self._apply_btn.isChecked()

    # ─── Internals ────────────────────────────────────────────────────

    def _on_spin_changed(self, value: float) -> None:
        self._touched = True
        self.value_changed.emit(value)

    def _refresh_caption(self, curve: CurveConfig | None) -> None:
        if curve is not None:
            self._reason = not_applicable_reason(curve.type)
        text = self._reason if not self._applicable else (DEMO_NOTE if self._demo else "")
        self._caption.setText(text)
        self._caption.setVisible(bool(text))
        self._spin.setToolTip(self._reason or SPIN_TOOLTIP)
