"""Accessible naming for controls that carry no words of their own (273-g).

A combo box, spin box, slider or line edit shows its *value*, never what the
value is for. Sighted users read that from the label beside it; a screen-reader
user gets "combo box, Dashboard" and no clue what "Dashboard" sets. The fix is
to attach the label's words to the control — and on the platform this app ships
to, that takes **two** calls, not one.

This module is the one place that rule lives. It was written inline in
``SettingsPage._setting_row`` (DEC-268 → DEC-269 → DEC-271), which named that
page correctly and left every other surface untouched, because a private method
on one page cannot be reused by a dialog. Extracting it is what let the rest of
the app be swept.

**Why two calls.** ``QAccessible::Text::Name`` and ``::Value`` are separate
queries, so a name is *added* to the announcement ("Poll interval, spin button,
1000 ms") rather than replacing the value — DEC-269 established that, refuting
DEC-268's premise. It holds for ``QSpinBox`` and ``QLineEdit``. It does **not**
hold for a non-editable ``QComboBox``: Qt's Unix ``QAccessibleComboBox::text
(Name)`` falls through to the current item, so ``setAccessibleName`` alone is
silently discarded. ``setBuddy`` is what survives — it publishes a
``RelationFlag.Label`` that AT-SPI exposes as *labelled-by*, which is what Orca
actually reads. Set both: the name for platforms that honour it, the buddy for
the one we run on.

Testing this by reading back ``accessibleName()`` is a guaranteed false green for
exactly that reason, and shipped as one once. Assert the **announced** name — see
``TestAccessibleNames._announced_label``.

**Buttons are deliberately out of scope.** A ``QPushButton``'s visible text
already *is* its accessible name, and overwriting "Clear overrides" with
"Sensor classification overrides" would make the spoken name disagree with the
printed one. A button whose label is a bare glyph takes ``accessible_name=`` on
:func:`~control_ofc.ui.components.buttons.make_button` instead (DEC-269).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from control_ofc.ui.components.toggle_switch import ToggleSwitch

# Everything whose announcement is a bare value with no indication of purpose.
#
# `QAbstractSpinBox`, not `QSpinBox`: `QDoubleSpinBox` is a sibling rather than a
# subclass, so the narrower check would silently miss the first fractional
# setting anyone adds. `QAbstractSlider` covers `QSlider` and `QScrollBar` for
# the same reason.
VALUE_CONTROLS = (
    QComboBox,
    QAbstractSpinBox,
    QAbstractSlider,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)


#: objectName suffix every hidden buddy label carries. Reuse is matched on this
#: rather than on the full derived name, so a second call with different text
#: finds the existing proxy instead of minting a rival one (277-l). Tests that
#: scrape a page for visible text filter on it too.
_PROXY_SUFFIX = "_A11yLabel"


def name_value_control(control: QWidget, label: str | QLabel) -> None:
    """Give *control* an accessible name taken from *label*.

    Pass the ``QLabel`` itself wherever one exists — that is what enables the
    ``setBuddy`` half, and the buddy is the half that works on Linux. A plain
    string is the fallback for a control with no visible label at all (a search
    field carrying only placeholder text, say), and names it as well as it can be
    named.

    A trailing colon is stripped from the *property*: row labels are written
    ``"Sensor:"`` for layout, and "Sensor colon" is not an improvement. Note this
    cannot reach the buddy path — where a real ``QLabel`` is passed, assistive
    tech reads that label's own visible text, punctuation included, and silently
    rewriting a label the user can see would be a worse trade than a spoken
    colon. So a combo buddied to ``QLabel("Sensor:")`` announces "Sensor:", while
    a spin box named from the same label announces "Sensor".

    Idempotent, and safe to call on a control type that needs no naming — a
    ``ToggleSwitch`` is routed to its own ``set_accessible_label``, and anything
    else is left alone rather than being given a name Qt would ignore.
    """
    text = label.text() if isinstance(label, QLabel) else label
    text = text.strip().removesuffix(":").strip()
    if not text:
        return

    if isinstance(control, ToggleSwitch):
        control.set_accessible_label(text)
        return

    if not isinstance(control, VALUE_CONTROLS):
        return

    control.setAccessibleName(text)

    # The buddy is the half that actually works on Linux, so every control gets
    # one — including those named from a bare string. MEASURED, not assumed: a
    # non-editable QComboBox carrying only `setAccessibleName("Theme")` announces
    # nothing at all (Qt's QAccessibleComboBox::text(Name) falls through to the
    # current item, so the queried Name equals the Value and the property is
    # discarded). The first version of this helper had exactly that bug, and the
    # app-wide sweep test is what found it.
    #
    # With no visible label to borrow, a hidden QLabel parented to the control
    # publishes the same RelationFlag.Label that AT-SPI exports as labelled-by —
    # verified to announce identically to a visible buddy. It is parented to the
    # control so it shares its lifetime, and hidden so it changes no layout.
    if isinstance(label, QLabel):
        label.setBuddy(control)
        return

    # Reuse an existing proxy rather than adding a second one. The docstring
    # promises idempotence and the string path was not: a second call minted
    # another QLabel with an IDENTICAL objectName, breaking the unique-objectName
    # rule and any `findChild` on it. No call site does this today, which is
    # exactly why it needed pinning rather than trusting.
    #
    # Matched by SUFFIX, not by the derived name (277-l). Looking one up by
    # `_proxy_name(control, text)` only found a proxy carrying the SAME text, so
    # a second call with *different* text missed the reuse and minted a second
    # hidden label — leaving the control with two `RelationFlag.Label` relations
    # and whichever Qt enumerates first winning the announcement. That is the
    # exact case the text fallback exists to serve (a control with no
    # objectName), so it was the untested one: both idempotence tests set an
    # objectName first, which makes `_proxy_name` stable and hides the bug.
    existing = next(
        (c for c in control.findChildren(QLabel) if c.objectName().endswith(_PROXY_SUFFIX)),
        None,
    )
    if existing is not None:
        # Re-derive the objectName too: with no objectName on the control it is
        # derived from the text, so leaving it stale would break `findChild` on
        # the name this helper itself just computed.
        existing.setObjectName(_proxy_name(control, text))
        existing.setText(text)
        existing.setBuddy(control)
        return

    proxy = QLabel(text, control)
    proxy.setObjectName(_proxy_name(control, text))
    proxy.hide()
    proxy.setBuddy(control)


def _proxy_name(control: QWidget, text: str) -> str:
    """objectName for the hidden buddy label.

    From the control if it has one, else from the label text — never a fixed
    string, which would collide the moment two controls took this path. The text
    fallback matters because the objectName may not be set YET: this helper runs
    at construction and a caller that names the control on the NEXT line would
    otherwise get the generic form. That happened once, on the sensor-series
    search box.
    """
    slug = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    return f"{control.objectName() or slug}{_PROXY_SUFFIX}"
