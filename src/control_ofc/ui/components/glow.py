"""Glow + pulsing-LED primitives and the decorative-animation controller (DEC-208).

Qt style sheets cannot do ``box-shadow``, so element glow uses
``QGraphicsDropShadowEffect`` (offset 0 + a coloured blur) and the pulsing status
dot is custom-painted (animating an effect's ``blurRadius`` is jittery —
QTBUG-86856 — so :class:`PulsingLed` animates a paint-time intensity instead).

Every decorative animation registers with the shared :class:`AnimationController`,
which pauses *all* of them whenever the application is not the active foreground
window (and each widget additionally pauses when hidden). That keeps paint cost
off the CPU/GPU while the user is in a game or another app — the hard performance
constraint the redesign must honour. Motion is capped to ~25 fps, and teardown is
deterministic (stop timers + unregister) to stay clear of the Python-3.14
offscreen-Qt teardown segfault (mirrors DEC-180).
"""

from __future__ import annotations

import math
import weakref

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from control_ofc.ui.theme import active_theme

_PULSE_INTERVAL_MS = 40  # ~25 fps — capped so decorative motion stays cheap
_PULSE_STEP = 0.16  # radians advanced per frame → ~2.4 s cycle

_ROLE_TOKENS = ("ok", "warn", "crit", "info", "neutral")


# ─── Animation controller ─────────────────────────────────────────────────


class AnimationController(QObject):
    """Pauses/resumes every registered decorative animation with app focus.

    Targets must expose ``set_app_active(bool)``. Held weakly so a destroyed
    widget drops out without an explicit unregister (though widgets should still
    ``cleanup()`` for deterministic teardown).
    """

    def __init__(self) -> None:
        super().__init__()
        self._targets: weakref.WeakSet = weakref.WeakSet()
        self._app_active = True
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_state_changed)
            self._app_active = app.applicationState() == Qt.ApplicationState.ApplicationActive

    @property
    def app_active(self) -> bool:
        return self._app_active

    def register(self, target: object) -> None:
        self._targets.add(target)
        target.set_app_active(self._app_active)

    def unregister(self, target: object) -> None:
        self._targets.discard(target)

    def pause_all(self) -> None:
        self._broadcast(False)

    def resume_all(self) -> None:
        self._broadcast(True)

    def _broadcast(self, active: bool) -> None:
        self._app_active = active
        for target in list(self._targets):
            target.set_app_active(active)

    def _on_state_changed(self, state) -> None:
        self._broadcast(state == Qt.ApplicationState.ApplicationActive)


_controller: AnimationController | None = None


def animation_controller() -> AnimationController:
    """Return the shared :class:`AnimationController` (created on first use)."""
    global _controller
    if _controller is None:
        _controller = AnimationController()
    return _controller


# ─── Pulsing LED ───────────────────────────────────────────────────────────


class PulsingLed(QWidget):
    """A small status dot with a soft pulsing halo (custom-painted).

    Colour follows the active theme by role (``ok``/``warn``/``crit``/``info``/
    ``neutral``). Pulsing pauses when the app is unfocused or the widget hidden.
    """

    def __init__(
        self, color_role: str = "ok", diameter: int = 10, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PulsingLed")
        self._role = color_role if color_role in _ROLE_TOKENS else "ok"
        self._diameter = diameter
        self._intensity = 1.0
        self._phase = 0.0
        self._pulsing = True
        self._app_active = True
        self._cleaned = False
        span = int(diameter * 2.4)  # room around the dot for the halo
        self.setFixedSize(span, span)
        self._timer = QTimer(self)
        self._timer.setInterval(_PULSE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        animation_controller().register(self)  # syncs set_app_active + running

    # -- public API --

    def set_color_role(self, role: str) -> None:
        if role in _ROLE_TOKENS:
            self._role = role
            self.update()

    def set_pulsing(self, on: bool) -> None:
        self._pulsing = on
        if not on:
            self._intensity = 1.0
            self.update()
        self._sync_running()

    def set_app_active(self, active: bool) -> None:
        self._app_active = active
        self._sync_running()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        animation_controller().unregister(self)

    # -- Qt events --

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_running()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_running()

    def closeEvent(self, event) -> None:
        self.cleanup()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = self.rect()
        center = QPointF(rect.width() / 2.0, rect.height() / 2.0)
        color = self._color()
        halo_r = rect.width() / 2.0
        dot_r = self._diameter / 2.0
        grad = QRadialGradient(center, halo_r)
        inner = QColor(color)
        inner.setAlphaF(0.5 * self._intensity)
        mid = QColor(color)
        mid.setAlphaF(0.18 * self._intensity)
        edge = QColor(color)
        edge.setAlpha(0)
        grad.setColorAt(0.0, inner)
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, edge)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(center, halo_r, halo_r)
        painter.setBrush(color)
        painter.drawEllipse(center, dot_r, dot_r)
        painter.end()

    # -- internals --

    def _tick(self) -> None:
        self._phase += _PULSE_STEP
        self._intensity = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._phase))
        self.update()

    def _sync_running(self) -> None:
        should_run = self._pulsing and self._app_active and self.isVisible() and not self._cleaned
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _color(self) -> QColor:
        t = active_theme()
        return QColor(
            {
                "ok": t.status_ok,
                "warn": t.status_warn,
                "crit": t.status_crit,
                "info": t.status_info,
                "neutral": t.text_muted,
            }.get(self._role, t.status_ok)
        )


# ─── Element glow ──────────────────────────────────────────────────────────


def apply_glow(
    widget: QWidget, color: str, *, radius: int = 12, animated: bool = False
) -> QGraphicsDropShadowEffect:
    """Attach a coloured 0-offset drop-shadow (a glow) to *widget*.

    ``color`` is a hex string (a theme-token value). With ``animated=True`` the
    glow softly pulses via the colour *alpha* (never ``blurRadius``) and pauses
    with app focus. Replaces any glow already on the widget. Do not attach to
    widgets that repaint every tick (fan cards / value labels / the chart).
    """
    remove_glow(widget)
    effect = QGraphicsDropShadowEffect(widget)
    effect.setOffset(0, 0)
    effect.setBlurRadius(radius)
    effect.setColor(QColor(color))
    widget.setGraphicsEffect(effect)
    if animated:
        widget._glow_pulse = _GlowPulse(widget, effect, QColor(color))
    return effect


def remove_glow(widget: QWidget) -> None:
    """Remove a glow previously applied by :func:`apply_glow` (idempotent)."""
    pulse = getattr(widget, "_glow_pulse", None)
    if pulse is not None:
        pulse.cleanup()
        widget._glow_pulse = None
    widget.setGraphicsEffect(None)


class _GlowPulse(QObject):
    """Drives the alpha pulse of an ``apply_glow(animated=True)`` effect."""

    def __init__(self, widget: QWidget, effect: QGraphicsDropShadowEffect, base: QColor) -> None:
        super().__init__(widget)
        self._effect = effect
        self._base = QColor(base)
        self._phase = 0.0
        self._cleaned = False
        self._timer = QTimer(self)
        self._timer.setInterval(_PULSE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        animation_controller().register(self)

    def set_app_active(self, active: bool) -> None:
        if active and not self._cleaned and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        animation_controller().unregister(self)

    def _tick(self) -> None:
        self._phase += _PULSE_STEP
        f = 0.5 + 0.5 * math.sin(self._phase)
        c = QColor(self._base)
        c.setAlphaF(0.35 + 0.65 * f)
        self._effect.setColor(c)
