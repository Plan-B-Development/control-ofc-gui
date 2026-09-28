"""DEC-443: the coolant limit on the Settings page.

At this coolant temperature the daemon forces every fan and pump to 100 % until
the coolant is 5 °C cooler. The control is **capability-gated** on
``control.cooling_failure_detection``: an older daemon 404s
``POST /config/coolant-limit`` and has no coolant emergency at all, so an
editable control there would promise protection that does not exist.

Like the exit minimum, the key being ABSENT from ``GET /config`` must not leave
the control enabled — for this key absence means "this daemon cannot".
"""

from __future__ import annotations

from control_ofc.api.models import Capabilities, ControlCapability
from control_ofc.services.daemon_features import unsupported_feature_message
from control_ofc.ui.pages.settings_page import SettingsPage

from .test_daemon_config_dec243 import _config, _ConfigClient, _default_config, _key

KEY = "safety.coolant_limit_c"


def _page(app_state, settings_service, *, supported: bool, config=None):
    app_state.capabilities = Capabilities(
        control=ControlCapability(exit_floor=True, cooling_failure_detection=supported)
    )
    client = _ConfigClient(config=config)
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    page._refresh_daemon_config()
    return page, client


def _with(on_disk: int, running: int):
    cfg = _default_config()
    at = next(i for i, k in enumerate(cfg.keys) if k.key == KEY)
    cfg.keys[at] = _key(KEY, on_disk, running_value=running, mutable=True)
    return cfg


def _config_without_the_key():
    """What a pre-DEC-443 daemon reports: every key but the coolant limit."""
    return _config(*[k for k in _default_config().keys if k.key != KEY])


class TestSupportedDaemon:
    def test_the_control_shows_the_daemons_value_and_is_editable(
        self, qapp, app_state, settings_service
    ):
        page, _client = _page(app_state, settings_service, supported=True, config=_with(55, 55))
        assert page._coolant_limit_spin.value() == 55
        assert page._coolant_limit_spin.isEnabled()

    def test_editing_it_writes_through_its_own_signal(self, qapp, app_state, settings_service):
        page, client = _page(app_state, settings_service, supported=True)
        page._coolant_limit_spin.setValue(52)
        page._coolant_limit_spin.editingFinished.emit()
        assert (KEY, 52) in client.writes

    def test_the_range_is_the_daemons(self, qapp, app_state, settings_service):
        """40-70 °C whole degrees (the user's Q2/extra answer); the daemon
        rejects anything else with 400, so the control must not offer it."""
        page, _client = _page(app_state, settings_service, supported=True)
        spin = page._coolant_limit_spin
        assert (spin.minimum(), spin.maximum(), spin.singleStep()) == (40, 70, 1)

    def test_a_focus_out_without_an_edit_writes_nothing(self, qapp, app_state, settings_service):
        page, client = _page(app_state, settings_service, supported=True)
        page._coolant_limit_spin.editingFinished.emit()
        assert not [w for w in client.writes if w[0] == KEY]


class TestUnsupportedDaemon:
    def test_an_older_daemon_gets_no_control_and_is_told_why(
        self, qapp, app_state, settings_service
    ):
        page, _client = _page(
            app_state, settings_service, supported=False, config=_config_without_the_key()
        )
        assert not page._coolant_limit_spin.isEnabled()
        note = page._daemon_row_notes[KEY]
        assert note.text() == unsupported_feature_message("cooling_failure_detection")
        assert note.isVisibleTo(page)

    def test_the_flag_decides_even_when_the_key_is_reported(
        self, qapp, app_state, settings_service
    ):
        page, _client = _page(app_state, settings_service, supported=False)
        assert not page._coolant_limit_spin.isEnabled()

    def test_the_flag_alone_is_not_enough_without_the_key(self, qapp, app_state, settings_service):
        page, _client = _page(
            app_state, settings_service, supported=True, config=_config_without_the_key()
        )
        assert not page._coolant_limit_spin.isEnabled()

    def test_the_exit_minimum_is_gated_on_its_own_flag(self, qapp, app_state, settings_service):
        """The shared gate must key each row on its own feature: standing the
        coolant row down must not take the exit minimum with it."""
        page, _client = _page(
            app_state, settings_service, supported=False, config=_config_without_the_key()
        )
        assert not page._coolant_limit_spin.isEnabled(), "precondition"
        assert page._exit_floor_spin.isEnabled()


class TestValueInForce:
    def test_the_row_shows_the_running_value_and_says_the_files_differ(
        self, qapp, app_state, settings_service
    ):
        cfg = _with(on_disk=50, running=65)
        page, _client = _page(app_state, settings_service, supported=True, config=cfg)
        entry = cfg.get(KEY)
        assert page._coolant_limit_spin.value() == entry.running_value
        note = page._daemon_row_notes[KEY]
        assert note.isVisibleTo(page)
        assert f"{entry.value} °C" in note.text()
        assert "reload" in note.text()

    def test_a_focus_out_on_a_diverged_row_writes_nothing(self, qapp, app_state, settings_service):
        page, client = _page(app_state, settings_service, supported=True, config=_with(50, 65))
        page._coolant_limit_spin.editingFinished.emit()
        assert not [w for w in client.writes if w[0] == KEY]

    def test_an_agreeing_row_says_nothing_about_the_files(self, qapp, app_state, settings_service):
        page, _client = _page(app_state, settings_service, supported=True, config=_with(60, 60))
        assert "configuration files say" not in page._daemon_row_notes[KEY].text()
