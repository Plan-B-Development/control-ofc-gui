"""DEC-208: glow/LED primitives + the decorative-animation controller.

The performance contract: every decorative animation must pause when the app is
not the active window (or the widget is hidden), and tear down deterministically.
"""

from __future__ import annotations

from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from control_ofc.ui.components.glow import (
    PulsingLed,
    animation_controller,
    apply_glow,
    remove_glow,
)


def test_pulsing_led_runs_when_active_and_visible(qtbot):
    led = PulsingLed(color_role="ok")
    qtbot.addWidget(led)
    led.set_app_active(True)
    led.show()
    assert led.isVisible()
    assert led._timer.isActive()


def test_pulsing_led_pauses_when_app_inactive(qtbot):
    led = PulsingLed()
    qtbot.addWidget(led)
    led.show()
    led.set_app_active(True)
    assert led._timer.isActive()
    # Simulate the app losing focus (e.g. a fullscreen game takes over).
    led.set_app_active(False)
    assert not led._timer.isActive()
    led.set_app_active(True)
    assert led._timer.isActive()


def test_pulsing_led_pauses_when_hidden(qtbot):
    led = PulsingLed()
    qtbot.addWidget(led)
    led.set_app_active(True)
    led.show()
    assert led._timer.isActive()
    led.hide()
    assert not led._timer.isActive()


def test_pulsing_led_cleanup_is_idempotent(qtbot):
    led = PulsingLed()
    qtbot.addWidget(led)
    led.set_app_active(True)
    led.show()
    led.cleanup()
    assert led._cleaned
    assert not led._timer.isActive()
    led.cleanup()  # second call must be a no-op, not raise
    assert led._cleaned


def test_set_pulsing_off_stops_animation(qtbot):
    led = PulsingLed()
    qtbot.addWidget(led)
    led.set_app_active(True)
    led.show()
    led.set_pulsing(False)
    assert not led._timer.isActive()


def test_animation_controller_broadcasts_and_unregisters(qtbot):
    class FakeTarget:
        def __init__(self):
            self.states = []

        def set_app_active(self, active):
            self.states.append(active)

    ctrl = animation_controller()
    target = FakeTarget()
    ctrl.register(target)  # syncs the current state once
    ctrl.pause_all()
    assert target.states[-1] is False
    ctrl.resume_all()
    assert target.states[-1] is True
    ctrl.unregister(target)
    snapshot = list(target.states)
    ctrl.pause_all()
    assert target.states == snapshot  # no longer notified after unregister


def test_apply_and_remove_static_glow(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    effect = apply_glow(w, "#1FB88A", radius=14)
    assert isinstance(w.graphicsEffect(), QGraphicsDropShadowEffect)
    assert effect.blurRadius() == 14
    assert effect.offset().x() == 0 and effect.offset().y() == 0
    remove_glow(w)
    assert w.graphicsEffect() is None
    remove_glow(w)  # idempotent


def test_animated_glow_registers_and_cleans_up(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    apply_glow(w, "#1FB88A", animated=True)
    assert w._glow_pulse is not None
    pulse = w._glow_pulse
    remove_glow(w)
    assert w._glow_pulse is None
    assert pulse._cleaned
