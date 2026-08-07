"""DEC-243: the Settings page reads daemon configuration instead of guessing it.

Before `GET /config` the writable daemon knobs were write-only. The GUI kept a
local mirror in ``AppSettings.daemon_startup_delay_secs`` and pushed it on save,
so a fresh GUI against a daemon already set to 10 s displayed **0 s** — the field
was a guess that could silently disagree with the daemon.

The rules these tests hold the card to:

* Values come from the daemon, never from local memory.
* ``restart_pending`` is the daemon's verdict (on-disk vs running config), not
  something re-derived here from what we happened to POST.
* A toggle whose feature also needs a root systemd drop-in must never read as
  "on" on the strength of the config flag alone.
* A pre-2.16.0 daemon makes the card stand down rather than invent values.
"""

from __future__ import annotations

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    DaemonConfig,
    DaemonConfigKey,
    HardwareReadiness,
    SuperIoReport,
    parse_config_write,
    parse_daemon_config,
)
from control_ofc.ui.pages.settings_page import SettingsPage


def _key(key, value, **kw):
    return DaemonConfigKey(key=key, value=value, **kw)


def _config(*keys, restart_pending=False):
    return DaemonConfig(
        api_version=1,
        admin_config_path="/etc/control-ofc/daemon.toml",
        runtime_config_path="/var/lib/control-ofc/runtime.toml",
        restart_pending=restart_pending,
        keys=list(keys),
    )


def _default_config(**overrides):
    keys = [
        _key("polling.poll_interval_ms", 1000, mutable=True, requires_restart=True),
        _key("serial.port", None, mutable=True, requires_restart=True),
        _key("serial.timeout_ms", 500, mutable=True, requires_restart=True),
        _key("startup.delay_secs", 10, mutable=True, requires_restart=True),
        _key(
            "detection.allow_port_probe",
            False,
            mutable=True,
            requires_restart=True,
            requires_privilege="also requires the CAP_SYS_RAWIO systemd drop-in",
        ),
        _key(
            "detection.enable_nvidia_telemetry",
            False,
            mutable=True,
            requires_restart=True,
            requires_privilege="also requires the /dev/nvidia* systemd drop-in",
        ),
        _key("ipc.socket_path", "/run/control-ofc/control-ofc.sock"),
        _key("state.state_dir", "/var/lib/control-ofc"),
    ]
    cfg = _config(*keys, **overrides)
    return cfg


class _ConfigClient:
    """Stub daemon exposing the DEC-243 surface and recording writes."""

    def __init__(self, config=None, probe_available=True, probe_reason=""):
        self._config = config if config is not None else _default_config()
        self.writes: list[tuple[str, object]] = []
        self.socket_path = "/tmp/x.sock"
        self._probe_available = probe_available
        self._probe_reason = probe_reason

    def get_daemon_config(self):
        return self._config

    def hardware_readiness(self):
        return HardwareReadiness(
            superio=SuperIoReport(
                port_probe_available=self._probe_available,
                port_probe_reason=self._probe_reason,
            )
        )

    def set_poll_interval(self, ms):
        self.writes.append(("polling.poll_interval_ms", ms))
        return parse_config_write({"updated": True, "key": "polling.poll_interval_ms", "value": ms})

    def set_serial_port(self, port):
        self.writes.append(("serial.port", port))
        return parse_config_write({"updated": True, "key": "serial.port", "value": port})

    def set_serial_timeout(self, ms):
        self.writes.append(("serial.timeout_ms", ms))
        return parse_config_write({"updated": True, "key": "serial.timeout_ms", "value": ms})

    def set_allow_port_probe(self, enabled):
        self.writes.append(("detection.allow_port_probe", enabled))
        return parse_config_write(
            {
                "updated": True,
                "key": "detection.allow_port_probe",
                "value": enabled,
                "requires_privilege": "also requires the CAP_SYS_RAWIO systemd drop-in",
            }
        )

    def set_nvidia_telemetry(self, enabled):
        self.writes.append(("detection.enable_nvidia_telemetry", enabled))
        return parse_config_write(
            {"updated": True, "key": "detection.enable_nvidia_telemetry", "value": enabled}
        )


@pytest.fixture()
def page(qapp, app_state, settings_service):
    client = _ConfigClient()
    p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    p._refresh_port_probe_availability()
    p._refresh_daemon_config()
    return p, client


# ── Parsing ───────────────────────────────────────────────────────────


class TestParsing:
    def test_parses_a_full_payload(self):
        cfg = parse_daemon_config(
            {
                "api_version": 1,
                "admin_config_path": "/etc/x.toml",
                "runtime_config_path": "/var/lib/y.toml",
                "restart_pending": True,
                "keys": [
                    {
                        "key": "startup.delay_secs",
                        "value": 10,
                        "running_value": 0,
                        "source": "runtime",
                        "mutable": True,
                        "requires_restart": True,
                        "restart_pending": True,
                    }
                ],
            }
        )
        assert cfg.restart_pending is True
        entry = cfg.get("startup.delay_secs")
        assert entry.value == 10
        assert entry.running_value == 0
        assert entry.source == "runtime"

    def test_drops_malformed_entries_without_failing_the_read(self):
        cfg = parse_daemon_config(
            {"keys": ["nonsense", {"no_key": 1}, {"key": "ok.key", "value": 1}]}
        )
        assert [k.key for k in cfg.keys] == ["ok.key"]

    def test_unknown_fields_are_ignored(self):
        """A newer daemon must not break an older GUI."""
        cfg = parse_daemon_config({"keys": [{"key": "a.b", "value": 1, "invented_later": "boom"}]})
        assert cfg.get("a.b").value == 1

    def test_missing_running_value_falls_back_to_value(self):
        """The daemon omits running_value when it equals value."""
        entry = DaemonConfigKey(key="a.b", value=7)
        assert entry.effective_running_value == 7

    def test_empty_payload_is_safe(self):
        cfg = parse_daemon_config({})
        assert cfg.keys == []
        assert cfg.get("anything") is None


# ── Rendering ─────────────────────────────────────────────────────────


class TestRendering:
    def test_controls_reflect_daemon_values(self, page):
        p, _client = page
        assert p._poll_interval_spin.value() == 1000
        assert p._serial_timeout_spin.value() == 500
        assert p._serial_port_edit.text() == ""
        assert p._port_probe_toggle.isChecked() is False

    def test_startup_delay_comes_from_the_daemon_not_local_settings(
        self, qapp, app_state, settings_service
    ):
        """The exact defect: a local mirror of 0 while the daemon is set to 10."""
        assert settings_service.settings.daemon_startup_delay_secs == 0
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        assert p._startup_delay_spin.value() == 0, "pre-fetch it can only show the local guess"

        p._refresh_daemon_config()
        assert p._startup_delay_spin.value() == 10, "after the read it must show daemon truth"

    def test_read_only_paths_are_displayed(self, page):
        p, _client = page
        text = p._daemon_paths_label.text()
        assert "/run/control-ofc/control-ofc.sock" in text
        assert "/var/lib/control-ofc" in text
        assert "/etc/control-ofc/daemon.toml" in text

    def test_rendering_does_not_write_back_to_the_daemon(self, page):
        """Populating the controls must not fire their change handlers."""
        p, client = page
        client.writes.clear()
        p._refresh_daemon_config()
        assert client.writes == []


class TestRestartPending:
    def test_banner_appears_with_the_systemctl_command(self, qapp, app_state, settings_service):
        cfg = _default_config(restart_pending=True)
        cfg.keys[0] = _key(
            "polling.poll_interval_ms",
            2500,
            running_value=1000,
            source="runtime",
            mutable=True,
            requires_restart=True,
            restart_pending=True,
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        assert not p._daemon_restart_banner.isHidden()
        assert "systemctl restart control-ofc-daemon" in p._daemon_restart_banner.text()

    def test_row_note_states_what_the_daemon_is_actually_running(
        self, qapp, app_state, settings_service
    ):
        cfg = _default_config(restart_pending=True)
        cfg.keys[0] = _key(
            "polling.poll_interval_ms",
            2500,
            running_value=1000,
            source="runtime",
            mutable=True,
            requires_restart=True,
            restart_pending=True,
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        note = p._daemon_row_notes["polling.poll_interval_ms"].text()
        assert "restart required" in note
        assert "1000" in note, "the note must name the value actually in effect"

    def test_no_banner_when_nothing_is_pending(self, page):
        p, _client = page
        assert p._daemon_restart_banner.isHidden()

    def test_source_is_reported(self, qapp, app_state, settings_service):
        cfg = _default_config()
        cfg.keys[2] = _key(
            "serial.timeout_ms", 750, source="admin", mutable=True, requires_restart=True
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        assert "daemon.toml" in p._daemon_row_notes["serial.timeout_ms"].text()


class TestPrivilegeGatedOptIns:
    def test_toggle_never_reads_as_on_without_the_drop_in(self, page):
        """The config flag is half the requirement; say so."""
        p, _client = page
        for key in ("detection.allow_port_probe", "detection.enable_nvidia_telemetry"):
            note = p._daemon_row_notes[key].text()
            assert "drop-in" in note
            assert not p._daemon_row_notes[key].isHidden()

    def test_unavailable_probe_reason_is_surfaced(self, qapp, app_state, settings_service):
        client = _ConfigClient(
            probe_available=False,
            probe_reason="disabled — set [detection] allow_port_probe = true",
        )
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_port_probe_availability()
        p._refresh_daemon_config()
        assert "allow_port_probe" in p._daemon_row_notes["detection.allow_port_probe"].text()

    def test_write_reports_the_outstanding_requirement(self, page):
        p, client = page
        p._port_probe_toggle.setChecked(True)
        assert ("detection.allow_port_probe", True) in client.writes
        assert "drop-in" in p._daemon_cfg_result.text()


class TestWrites:
    def test_spin_change_posts(self, page):
        p, client = page
        p._poll_interval_spin.setValue(2500)
        p._poll_interval_spin.editingFinished.emit()
        assert ("polling.poll_interval_ms", 2500) in client.writes

    def test_blank_serial_port_clears_the_override(self, page):
        p, client = page
        p._serial_port_edit.setText("   ")
        p._write_serial_port()
        assert ("serial.port", None) in client.writes

    def test_serial_port_value_is_sent(self, page):
        p, client = page
        p._serial_port_edit.setText("/dev/ttyACM0")
        p._write_serial_port()
        assert ("serial.port", "/dev/ttyACM0") in client.writes

    def test_rejected_write_reports_and_reverts_to_daemon_truth(
        self, qapp, app_state, settings_service
    ):
        class _Rejecting(_ConfigClient):
            def set_poll_interval(self, ms):
                raise DaemonError(code="validation_error", message="out of range", status=400)

        client = _Rejecting()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._poll_interval_spin.setValue(9999)
        p._poll_interval_spin.editingFinished.emit()

        assert "out of range" in p._daemon_cfg_result.text()
        assert p._poll_interval_spin.value() == 1000, "control must revert to the daemon's value"

    def test_unreachable_daemon_reports(self, qapp, app_state, settings_service):
        class _Dead(_ConfigClient):
            def set_serial_timeout(self, ms):
                raise ConnectionError("gone")

        client = _Dead()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        p._serial_timeout_spin.setValue(750)
        p._serial_timeout_spin.editingFinished.emit()
        assert "unavailable" in p._daemon_cfg_result.text().lower()


class TestVersionGate:
    def test_404_stands_the_card_down(self, qapp, app_state, settings_service):
        """A pre-2.16.0 daemon must not be shown invented values."""

        class _Old(_ConfigClient):
            def get_daemon_config(self):
                raise DaemonError(code="not_found", message="no route", status=404)

        client = _Old()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        assert p._daemon_config_unsupported is True
        assert "too old" in p._daemon_cfg_note.text().lower()
        assert not p._poll_interval_spin.isEnabled()
        assert not p._port_probe_toggle.isEnabled()

    def test_404_is_asked_only_once(self, qapp, app_state, settings_service):
        calls: list[int] = []

        class _Old(_ConfigClient):
            def get_daemon_config(self):
                calls.append(1)
                raise DaemonError(code="not_found", message="no route", status=404)

        client = _Old()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        p._refresh_daemon_config()
        p._refresh_daemon_config()
        assert len(calls) == 1, "the version gate must latch, not re-probe every refresh"

    def test_writes_are_suppressed_once_unsupported(self, qapp, app_state, settings_service):
        class _Old(_ConfigClient):
            def get_daemon_config(self):
                raise DaemonError(code="not_found", message="no route", status=404)

        client = _Old()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        client.writes.clear()
        p._write_daemon_key("serial.port", lambda c: c.set_serial_port("/dev/ttyACM0"))
        assert client.writes == []

    def test_no_client_is_safe(self, qapp, app_state, settings_service):
        p = SettingsPage(state=app_state, settings_service=settings_service)
        p._refresh_daemon_config()
        p._refresh_port_probe_availability()
        assert not p._poll_interval_spin.isEnabled()

    def test_transport_failure_does_not_latch_the_version_gate(
        self, qapp, app_state, settings_service
    ):
        """A disconnect is not a version gap — the card must recover on reconnect."""

        class _Flaky(_ConfigClient):
            fail = True

            def get_daemon_config(self):
                if _Flaky.fail:
                    raise ConnectionError("gone")
                return super().get_daemon_config()

        client = _Flaky()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        assert p._daemon_config_unsupported is False

        _Flaky.fail = False
        p._refresh_daemon_config()
        assert p._poll_interval_spin.isEnabled()
        assert p._poll_interval_spin.value() == 1000
