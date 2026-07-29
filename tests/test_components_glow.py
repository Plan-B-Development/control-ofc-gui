"""DEC-208: pulsing-LED primitive + the decorative-animation controller.

The performance contract: every decorative animation must pause when the app is
not the active window (or the widget is hidden), and tear down deterministically.
(The unused element-glow API — ``apply_glow``/``remove_glow`` — was removed in
the 2026-07-21 audit dead-code sweep along with its tests.)
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from control_ofc.ui.components.glow import PulsingLed, animation_controller


def test_pulsing_led_object_name_param(qtbot):
    # Default keeps the shared name; a caller sets a unique one so two LEDs on
    # one page don't collide (matches the shared-component convention).
    default = PulsingLed("ok")
    qtbot.addWidget(default)
    assert default.objectName() == "PulsingLed"
    named = PulsingLed("ok", object_name="Foo_Led_bar")
    qtbot.addWidget(named)
    assert named.objectName() == "Foo_Led_bar"


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


def test_animation_controller_broadcasts_and_unregisters(qtbot):
    """The controller's real driver is Qt's applicationStateChanged — exercise
    the broadcast through that path (the pause_all/resume_all convenience
    wrappers were removed unused in the 2026-07-21 sweep)."""

    class FakeTarget:
        def __init__(self):
            self.states = []

        def set_app_active(self, active):
            self.states.append(active)

    ctrl = animation_controller()
    target = FakeTarget()
    ctrl.register(target)  # syncs the current state once
    ctrl._on_state_changed(Qt.ApplicationState.ApplicationInactive)
    assert target.states[-1] is False
    ctrl._on_state_changed(Qt.ApplicationState.ApplicationActive)
    assert target.states[-1] is True
    ctrl.unregister(target)
    snapshot = list(target.states)
    ctrl._on_state_changed(Qt.ApplicationState.ApplicationInactive)
    assert target.states == snapshot  # no longer notified after unregister
    # Leave the shared controller in the active state for other tests.
    ctrl._on_state_changed(Qt.ApplicationState.ApplicationActive)
