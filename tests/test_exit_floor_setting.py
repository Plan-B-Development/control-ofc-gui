"""DEC-388: the exit minimum on the Settings page.

On a clean stop the daemon leaves every fan it cannot hand back to firmware —
OpenFan channels, and headers with no mode switch — at no less than this. The
control is **capability-gated**: an older daemon 404s ``POST /config/exit-floor``
and leaves its fans at their last speed whatever the control says, so an editable
control there would promise something that does not happen.

Unlike the other daemon rows, the key being ABSENT from ``GET /config`` must not
leave the control enabled: for this key absence means "this daemon cannot", not
"this daemon predates reporting it".
"""

from __future__ import annotations

from control_ofc.api.models import Capabilities, ControlCapability
from control_ofc.services.daemon_features import unsupported_feature_message
from control_ofc.ui.pages.settings_page import SettingsPage

from .test_daemon_config_dec243 import _config, _ConfigClient, _default_config, _key


def _page(app_state, settings_service, *, exit_floor: bool, config=None):
    # `exit_floor` is a plain `bool` on `ControlCapability`, so an older daemon
    # that sends no flag reads as False — there is no "did not say" state for it
    # (`daemon_supports` returns None only for the five `WIRE-k` flags). One
    # value therefore models both "an older daemon" and "said no".
    app_state.capabilities = Capabilities(control=ControlCapability(exit_floor=exit_floor))
    client = _ConfigClient(config=config)
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    page._refresh_daemon_config()
    return page, client


def _config_without_the_key():
    """What a pre-DEC-388 daemon reports: every key but the exit floor."""
    keys = [k for k in _default_config().keys if k.key != "shutdown.exit_floor_pct"]
    return _config(*keys)


class TestSupportedDaemon:
    def test_the_control_shows_the_daemons_value_and_is_editable(
        self, qapp, app_state, settings_service
    ):
        cfg = _default_config()
        at = next(i for i, k in enumerate(cfg.keys) if k.key == "shutdown.exit_floor_pct")
        cfg.keys[at] = _key("shutdown.exit_floor_pct", 70, running_value=70, mutable=True)
        page, _client = _page(app_state, settings_service, exit_floor=True, config=cfg)

        assert page._exit_floor_spin.value() == 70
        assert page._exit_floor_spin.isEnabled()

    def test_editing_it_writes_through_its_own_signal(self, qapp, app_state, settings_service):
        page, client = _page(app_state, settings_service, exit_floor=True)
        assert page._exit_floor_spin.isEnabled(), "precondition: the control is live"

        page._exit_floor_spin.setValue(80)
        page._exit_floor_spin.editingFinished.emit()

        assert ("shutdown.exit_floor_pct", 80) in client.writes

    def test_a_focus_out_without_an_edit_writes_nothing(self, qapp, app_state, settings_service):
        page, client = _page(app_state, settings_service, exit_floor=True)
        page._exit_floor_spin.editingFinished.emit()
        assert not [w for w in client.writes if w[0] == "shutdown.exit_floor_pct"]


class TestUnsupportedDaemon:
    def test_an_older_daemon_gets_no_control_and_is_told_why(
        self, qapp, app_state, settings_service
    ):
        page, _client = _page(
            app_state, settings_service, exit_floor=False, config=_config_without_the_key()
        )

        assert not page._exit_floor_spin.isEnabled()
        note = page._daemon_row_notes["shutdown.exit_floor_pct"]
        assert note.text() == unsupported_feature_message("exit_floor")
        assert note.isVisibleTo(page)

    def test_the_flag_decides_even_when_the_key_is_reported(
        self, qapp, app_state, settings_service
    ):
        """Without this case the two checks are indistinguishable — the older-
        daemon fixture lacks the flag and the key at once."""
        page, _client = _page(app_state, settings_service, exit_floor=False)
        assert not page._exit_floor_spin.isEnabled()

    def test_the_flag_alone_is_not_enough_without_the_key(self, qapp, app_state, settings_service):
        """The other half of the pair: without the key there is no value to show
        and nothing the card could honestly edit."""
        page, _client = _page(
            app_state, settings_service, exit_floor=True, config=_config_without_the_key()
        )
        assert not page._exit_floor_spin.isEnabled()

    def test_the_rest_of_the_card_stays_editable(self, qapp, app_state, settings_service):
        """Standing one row down must not take the card with it."""
        page, _client = _page(
            app_state, settings_service, exit_floor=False, config=_config_without_the_key()
        )
        assert not page._exit_floor_spin.isEnabled(), "precondition"
        assert page._poll_interval_spin.isEnabled()
