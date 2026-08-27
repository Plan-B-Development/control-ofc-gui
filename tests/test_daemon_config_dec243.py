"""DEC-243: the Settings page reads daemon configuration instead of guessing it.

Before `GET /config` the writable daemon knobs were write-only. The GUI kept a
local mirror in ``AppSettings.daemon_startup_delay_secs`` and pushed it on save,
so a fresh GUI against a daemon already set to 10 s displayed **0 s** — the field
was a guess that could silently disagree with the daemon. DEC-285 finished the
job: that mirror is gone from ``AppSettings`` and the spinner is an ordinary
daemon-config row on this card, written only by ``_write_daemon_key``.

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
    Capabilities,
    ControlCapability,
    DaemonConfig,
    DaemonConfigKey,
    HardwareReadiness,
    ProfileSearchDirsResult,
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
        # Appended last on purpose: several tests below replace a key by index,
        # so anything inserted ahead of them would silently retarget those edits.
        # `requires_restart` is False — this is the one key that applies live.
        _key(
            "profiles.search_dirs",
            ["/etc/control-ofc/profiles", "/home/u/.config/control-ofc/profiles"],
            running_value=["/etc/control-ofc/profiles", "/home/u/.config/control-ofc/profiles"],
            mutable=True,
        ),
    ]
    cfg = _config(*keys, **overrides)
    return cfg


class _ConfigClient:
    """Stub daemon exposing the DEC-243 surface and recording writes."""

    def __init__(self, config=None, probe_available=True, probe_reason=""):
        self._config = config if config is not None else _default_config()
        self.writes: list[tuple[str, object]] = []
        self.edits: list[tuple[list[str] | None, list[str] | None]] = []
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

    def set_startup_delay(self, delay_secs):
        self.writes.append(("startup.delay_secs", delay_secs))
        return parse_config_write(
            {
                "updated": True,
                "key": "startup.delay_secs",
                "value": delay_secs,
                "delay_secs": delay_secs,
                "note": "Takes effect on next daemon restart",
            }
        )

    def update_profile_search_dirs(self, add=None, remove=None):
        self.edits.append((add, remove))
        entry = self._config.get("profiles.search_dirs")
        dirs = list(entry.running_value) if entry else []
        for d in remove or []:
            if d in dirs:
                dirs.remove(d)
        for d in add or []:
            if d not in dirs:
                dirs.append(d)
        # Mirror the daemon: the reply, the next GET, and the live list all agree.
        if entry is not None:
            entry.running_value = list(dirs)
            entry.value = list(dirs)
        return ProfileSearchDirsResult(updated=True, search_dirs=list(dirs))


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

    def test_null_running_value_is_not_treated_as_absent(self):
        """REGRESSION: `running_value: null` must not read as "same as value".

        The daemon used to omit running_value when equal to value, and the GUI
        fell back to `value` on None. For `serial.port` — the one nullable key —
        a genuine null then rendered as the FILE's port, so the card claimed the
        daemon was running a port it had never been given. The daemon now always
        sends the field and None means exactly one thing: not set.
        """
        unset = DaemonConfigKey(key="serial.port", value="/dev/ttyACM0", running_value=None)
        assert unset.running_display == "not set"
        assert unset.running_display != str(unset.value)

        running = DaemonConfigKey(
            key="serial.port", value="/dev/ttyACM1", running_value="/dev/ttyACM0"
        )
        assert running.running_display == "/dev/ttyACM0"

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
        """The exact defect: a local mirror of 0 while the daemon is set to 10.

        DEC-285 removed the mirror outright, so there is no longer anywhere for a
        local guess to come from — assert that as the precondition, then that the
        daemon's value lands.
        """
        assert not hasattr(settings_service.settings, "daemon_startup_delay_secs"), (
            "a local mirror of a daemon-owned key must not exist"
        )
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()
        assert p._startup_delay_spin.value() == 10, "after the read it must show daemon truth"

    def test_startup_delay_is_written_through_the_guarded_daemon_path(
        self, qapp, app_state, settings_service
    ):
        """It is a daemon-config row now, so it inherits both halves of that path:
        the no-op-write guard, and the source/restart annotation."""
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        client.writes.clear()
        p._startup_delay_spin.editingFinished.emit()
        assert client.writes == [], "focus-out without an edit must not POST"

        p._startup_delay_spin.setValue(4)
        p._startup_delay_spin.editingFinished.emit()
        assert ("startup.delay_secs", 4) in client.writes

    def test_startup_delay_gets_a_row_note_like_every_other_daemon_key(
        self, qapp, app_state, settings_service
    ):
        """On the Operational Behavior card it had no note at all, because
        `_daemon_row_notes` is built from `_DAEMON_ROWS`."""
        cfg = _default_config(restart_pending=True)
        cfg.keys[3] = _key(
            "startup.delay_secs",
            10,
            running_value=0,
            source="runtime",
            mutable=True,
            requires_restart=True,
            restart_pending=True,
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        note = p._daemon_row_notes["startup.delay_secs"].text()
        assert "restart required" in note
        assert "0" in note, "the note must name the value actually in effect"

    def test_out_of_api_range_daemon_value_is_shown_not_clamped(
        self, qapp, app_state, settings_service
    ):
        """REGRESSION: daemon.toml has no upper bound; the API does.

        An admin may legitimately run poll_interval_ms = 3000. Letting the spin
        clamp it to its API maximum would display a number the daemon is not
        running — the exact dishonesty this card exists to remove — and would
        then make a focus-out write the clamp back, silently shadowing the
        admin's file.
        """
        cfg = _default_config()
        cfg.keys[0] = _key(
            "polling.poll_interval_ms", 3000, source="admin", mutable=True, requires_restart=True
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        assert p._poll_interval_spin.value() == 3000, "must show what the daemon runs"

        client.writes.clear()
        p._poll_interval_spin.editingFinished.emit()
        assert client.writes == [], "focus-out must not write a clamped value back"

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
        # Name the key, not just a count: a bare count reads as "1 change
        # pending" with nothing on screen explaining which one, and a future
        # daemon key may have no row on this card at all.
        assert "polling.poll_interval_ms" in p._daemon_restart_banner.text()

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
        p._poll_interval_spin.setValue(1500)
        p._poll_interval_spin.editingFinished.emit()
        assert ("polling.poll_interval_ms", 1500) in client.writes

    def test_focus_out_without_an_edit_does_not_post(self, page):
        """REGRESSION: editingFinished fires on focus-out regardless of edits.

        Without a guard, merely tabbing through the card POSTs every field,
        writing them into runtime.toml and permanently shadowing the operator's
        daemon.toml with values they never chose.
        """
        p, client = page
        client.writes.clear()
        p._poll_interval_spin.editingFinished.emit()
        p._serial_timeout_spin.editingFinished.emit()
        p._write_serial_port()
        assert client.writes == []

    def test_blank_serial_port_clears_a_set_override(self, qapp, app_state, settings_service):
        """Blanking a port that WAS set clears it; blanking an already-unset one is a no-op."""
        cfg = _default_config()
        cfg.keys[1] = _key("serial.port", "/dev/ttyACM0", mutable=True, requires_restart=True)
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

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

        p._poll_interval_spin.setValue(1750)
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
        p._write_daemon_key(
            "serial.port", "/dev/ttyACM0", lambda c: c.set_serial_port("/dev/ttyACM0")
        )
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


# ── DEC-285: the profile-search-dir editor ────────────────────────────
#
# `profiles.search_dirs` was `mutable: true` from the day `GET /config` shipped,
# and the GUI fetched it and threw it away. Its only surface was a sentence on
# the Path Management card that printed the *GUI's* directory — a different
# thing, and wrong whenever the two had diverged, which was always: the endpoint
# was add-only, `services/polling.py` re-registers on every connect and the
# directory picker added another entry on every change. The list only ever grew,
# was displayed nowhere, and could be pruned only by hand-editing a root-owned
# `runtime.toml`.


def _caps(remove_supported: bool) -> Capabilities:
    return Capabilities(control=ControlCapability(profile_search_dir_remove=remove_supported))


class TestProfileSearchDirs:
    def test_the_daemons_list_is_displayed(self, page):
        p, _client = page
        shown = [p._search_dirs_list.item(i).text() for i in range(p._search_dirs_list.count())]
        assert shown == [
            "/etc/control-ofc/profiles",
            "/home/u/.config/control-ofc/profiles",
        ]

    def test_the_live_list_wins_over_the_on_disk_one(self, qapp, app_state, settings_service):
        """This key applies immediately, so `running_value` is the answer to
        "where does the daemon look?" — `value` is only what a restart would give."""
        cfg = _default_config()
        cfg.keys[-1] = _key(
            "profiles.search_dirs",
            ["/etc/control-ofc/profiles", "/from/the/file"],
            running_value=["/etc/control-ofc/profiles", "/actually/live"],
            mutable=True,
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        shown = [p._search_dirs_list.item(i).text() for i in range(p._search_dirs_list.count())]
        assert shown == ["/etc/control-ofc/profiles", "/actually/live"]
        # …and the divergence the daemon does NOT flag (requires_restart is
        # false for this key, so it never raises restart_pending) is surfaced.
        assert "restart" in p._daemon_row_notes["profiles.search_dirs"].text()

    def test_no_divergence_note_when_the_files_and_the_process_agree(self, page):
        p, _client = page
        assert "restart" not in p._daemon_row_notes["profiles.search_dirs"].text()

    def test_the_daemons_injected_store_dir_is_not_a_divergence(
        self, qapp, app_state, settings_service
    ):
        """REGRESSION (review): the daemon prepends its own profile store into the
        RUNNING list at boot (`main.rs::with_store_dir`) and never writes it into
        the config files unless a runtime override happens to capture it. An
        equality test therefore reports a divergence on every fresh daemon and
        advises a restart that cannot resolve it — the note would be permanent
        and unactionable. Only entries the FILES list that the daemon is not
        using are a real pending change.
        """
        cfg = _default_config()
        cfg.keys[-1] = _key(
            "profiles.search_dirs",
            ["/etc/control-ofc/profiles"],  # what the files say
            running_value=[
                "/var/lib/control-ofc/profiles",  # injected at boot
                "/etc/control-ofc/profiles",
            ],
            mutable=True,
        )
        p = SettingsPage(
            state=app_state, settings_service=settings_service, client=_ConfigClient(cfg)
        )
        p._refresh_daemon_config()
        assert "restart" not in p._daemon_row_notes["profiles.search_dirs"].text(), (
            "an extra RUNNING entry is normal and no restart can remove it"
        )

    def test_remove_posts_a_remove_and_re_reads(self, qapp, app_state, settings_service):
        app_state.capabilities = _caps(True)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(1)
        p._remove_search_dir_btn.click()

        assert client.edits == [(None, ["/home/u/.config/control-ofc/profiles"])]
        shown = [p._search_dirs_list.item(i).text() for i in range(p._search_dirs_list.count())]
        assert shown == ["/etc/control-ofc/profiles"], "the card must re-read, not predict"

    def test_remove_is_disabled_without_the_capability(self, qapp, app_state, settings_service):
        """A pre-2.23.0 daemon does not 404 a `remove` — it parses only `add` and
        silently ignores the rest. Offering the button would report success
        having pruned nothing."""
        app_state.capabilities = _caps(False)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(1)
        assert not p._remove_search_dir_btn.isEnabled()
        assert "2.23.0" in p._remove_search_dir_btn.toolTip()

    def test_remove_is_refused_even_if_the_button_is_reached(
        self, qapp, app_state, settings_service
    ):
        """Belt and braces: the guard lives in the write path, not only in the
        button's enabled state."""
        app_state.capabilities = _caps(False)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._edit_search_dirs(remove=["/home/u/.config/control-ofc/profiles"], done="x")
        assert client.edits == []
        assert "2.23.0" in p._daemon_cfg_result.text()

    def test_the_system_directory_cannot_be_removed(self, qapp, app_state, settings_service):
        """The daemon refuses (it holds the admin-installed profiles); disabling
        is the honest form of the same rule."""
        app_state.capabilities = _caps(True)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(0)
        assert p._search_dirs_list.currentItem().text() == "/etc/control-ofc/profiles"
        assert not p._remove_search_dir_btn.isEnabled()
        # Assert WHICH rule fired: row 0 is also the store dir, so a bare
        # "disabled" assertion would pass for the wrong reason.
        assert "system profile directory" in p._remove_search_dir_btn.toolTip()

    def test_the_daemons_own_profile_store_cannot_be_removed(
        self, qapp, app_state, settings_service
    ):
        """REGRESSION (self-review P1): the daemon defines its profile store of
        record as the FIRST search dir, and it is the write target for profile
        create and delete. Removing it would silently redirect every profile
        write for the rest of the daemon's process life."""
        app_state.capabilities = _caps(True)
        cfg = _default_config()
        live = ["/var/lib/control-ofc/profiles", "/etc/control-ofc/profiles", "/home/u/extra"]
        cfg.keys[-1] = _key(
            "profiles.search_dirs", list(live), running_value=list(live), mutable=True
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(0)
        assert not p._remove_search_dir_btn.isEnabled()
        assert "profile store" in p._remove_search_dir_btn.toolTip()
        # A non-first, non-system entry is still removable — the guard is
        # specific, not a blanket refusal.
        p._search_dirs_list.setCurrentRow(2)
        assert p._remove_search_dir_btn.isEnabled()

    def test_this_guis_own_profiles_directory_cannot_be_removed(
        self, qapp, app_state, settings_service
    ):
        """The daemon would accept it and `services/polling.py` would register it
        again on the next connect — the removal would appear to work and then
        undo itself, which is the silent-partial-success failure this change
        exists to remove."""
        from control_ofc.paths import profiles_dir

        app_state.capabilities = _caps(True)
        cfg = _default_config()
        live = ["/var/lib/control-ofc/profiles", str(profiles_dir())]
        cfg.keys[-1] = _key(
            "profiles.search_dirs", list(live), running_value=list(live), mutable=True
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(1)
        assert not p._remove_search_dir_btn.isEnabled()
        assert "Path Management" in p._remove_search_dir_btn.toolTip()

    def test_the_own_directory_guard_survives_a_different_spelling(
        self, qapp, app_state, settings_service, tmp_path, monkeypatch
    ):
        """REGRESSION (review): the daemon persists whatever raw spelling the
        adder sent, so an exact string comparison misses a trailing slash or a
        `.` segment — and the removal then succeeds and undoes itself on the next
        connect, which is the silent partial success the guard exists to prevent.
        """
        from control_ofc.paths import profiles_dir, set_path_overrides

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        set_path_overrides()
        try:
            odd = f"{profiles_dir()}/./"  # same directory, different string
            app_state.capabilities = _caps(True)
            cfg = _default_config()
            live = ["/var/lib/control-ofc/profiles", odd]
            cfg.keys[-1] = _key(
                "profiles.search_dirs", list(live), running_value=list(live), mutable=True
            )
            p = SettingsPage(
                state=app_state, settings_service=settings_service, client=_ConfigClient(cfg)
            )
            p._refresh_daemon_config()

            p._search_dirs_list.setCurrentRow(1)
            assert odd != str(profiles_dir()), "precondition: the strings differ"
            assert not p._remove_search_dir_btn.isEnabled()
            assert "Path Management" in p._remove_search_dir_btn.toolTip()
        finally:
            set_path_overrides()

    def test_a_blocked_removal_is_refused_in_the_write_path_too(
        self, qapp, app_state, settings_service
    ):
        """The enabled state is a rendering of the rule; the rule itself must
        also live in the handler, or a future caller walks around it."""
        app_state.capabilities = _caps(True)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(0)  # the system dir
        p._remove_search_dir()  # called directly, bypassing the disabled button
        assert client.edits == []
        assert "Cannot remove" in p._daemon_cfg_result.text()

    def test_the_last_entry_cannot_be_removed(self, qapp, app_state, settings_service):
        """Emptying the search path is an unrecoverable soft-lock — profile
        activation resolves against this list."""
        app_state.capabilities = _caps(True)
        cfg = _default_config()
        cfg.keys[-1] = _key(
            "profiles.search_dirs",
            ["/home/u/only"],
            running_value=["/home/u/only"],
            mutable=True,
        )
        client = _ConfigClient(cfg)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(0)
        assert not p._remove_search_dir_btn.isEnabled()

    def test_add_posts_the_chosen_directory(self, qapp, app_state, settings_service, monkeypatch):
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        monkeypatch.setattr(
            "control_ofc.ui.pages.settings_page.QFileDialog.getExistingDirectory",
            lambda *a, **k: "/home/u/extra",
        )
        p._add_search_dir_btn.click()

        assert client.edits == [(["/home/u/extra"], None)]
        shown = [p._search_dirs_list.item(i).text() for i in range(p._search_dirs_list.count())]
        assert "/home/u/extra" in shown

    def test_a_cancelled_add_dialog_writes_nothing(
        self, qapp, app_state, settings_service, monkeypatch
    ):
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        monkeypatch.setattr(
            "control_ofc.ui.pages.settings_page.QFileDialog.getExistingDirectory",
            lambda *a, **k: "",
        )
        p._add_search_dir_btn.click()
        assert client.edits == []

    def test_a_rejected_edit_reports_and_re_reads(self, qapp, app_state, settings_service):
        class _Rejecting(_ConfigClient):
            def update_profile_search_dirs(self, add=None, remove=None):
                raise DaemonError(
                    code="validation_error",
                    message="must be within your home directory",
                    status=400,
                )

        app_state.capabilities = _caps(True)
        client = _Rejecting()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        p._search_dirs_list.setCurrentRow(1)
        p._remove_search_dir_btn.click()

        assert "home directory" in p._daemon_cfg_result.text()
        shown = [p._search_dirs_list.item(i).text() for i in range(p._search_dirs_list.count())]
        assert len(shown) == 2, "a rejected edit must leave the list as the daemon has it"

    def test_buttons_are_disabled_before_the_daemon_has_been_read(
        self, qapp, app_state, settings_service
    ):
        p = SettingsPage(state=app_state, settings_service=settings_service)
        assert not p._add_search_dir_btn.isEnabled()
        assert not p._remove_search_dir_btn.isEnabled()


class TestProfilesDirChangeRetiresTheOld:
    """The compounding leak: the picker added the new dir and never retired the
    old one, so the daemon's search path grew by one entry per change — forever,
    invisibly, and prunable only by hand-editing a root-owned runtime.toml."""

    def test_a_directory_change_is_a_move_not_an_add(self, qapp, app_state, settings_service):
        app_state.capabilities = _caps(True)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        err = p._sync_profile_search_dir("/home/u/old", "/home/u/new")
        assert err is None
        assert client.edits == [(["/home/u/new"], ["/home/u/old"])]

    def test_an_old_daemon_still_registers_the_new_directory(
        self, qapp, app_state, settings_service
    ):
        """Honest degradation: the add still happens, the old entry still leaks,
        and the capability flag is how we know which we got."""
        app_state.capabilities = _caps(False)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)

        assert p._sync_profile_search_dir("/home/u/old", "/home/u/new") is None
        assert client.edits == [(["/home/u/new"], None)]

    def test_no_retire_when_the_directory_did_not_actually_change(
        self, qapp, app_state, settings_service
    ):
        """Re-registering the directory you are already using must never be able
        to un-register it."""
        app_state.capabilities = _caps(True)
        client = _ConfigClient()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)

        p._sync_profile_search_dir("/home/u/same", "/home/u/same")
        assert client.edits == [(["/home/u/same"], None)]

    def test_a_refused_retire_never_discards_the_registration(
        self, qapp, app_state, settings_service
    ):
        """REGRESSION (review): the daemon rejects the WHOLE request when the
        `remove` half is refused, and `_handle_dir_change` has already moved the
        profile files by then. Losing the `add` would leave the daemon searching
        only the old, now-empty directory — so GUI-authored profiles, including
        the active one, would stop resolving. Before this change the add always
        succeeded, so this would have been a regression of working behaviour.
        """

        class _RefusesRemoves(_ConfigClient):
            def update_profile_search_dirs(self, add=None, remove=None):
                if remove:
                    # Record the attempt before refusing — the daemon sees the
                    # request either way, and the point of the assertion below is
                    # that BOTH calls happened in the right order.
                    self.edits.append((add, remove))
                    raise DaemonError(
                        code="validation_error",
                        message="must be within your home directory",
                        status=400,
                    )
                return super().update_profile_search_dirs(add=add, remove=remove)

        app_state.capabilities = _caps(True)
        client = _RefusesRemoves()
        p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
        p._refresh_daemon_config()

        assert p._sync_profile_search_dir("/mnt/outside-home", "/home/u/new") is None
        assert client.edits == [
            (["/home/u/new"], ["/mnt/outside-home"]),  # the combined attempt…
            (["/home/u/new"], None),  # …then the add alone
        ]
        # And the leak is reported rather than swallowed — it is exactly what
        # this change exists to stop, so an unqualified "updated" would be a lie.
        assert p._stale_search_dir == "/mnt/outside-home"

    def test_a_failing_add_is_still_reported(self, qapp, app_state, settings_service):
        """The fallback must not turn a genuinely failed registration into a
        success — only a failed *retire* is tolerable."""

        class _RefusesEverything(_ConfigClient):
            def update_profile_search_dirs(self, add=None, remove=None):
                raise DaemonError(code="validation_error", message="no", status=400)

        app_state.capabilities = _caps(True)
        p = SettingsPage(
            state=app_state, settings_service=settings_service, client=_RefusesEverything()
        )
        assert p._sync_profile_search_dir("/home/u/old", "/home/u/new") == "no"
        assert p._stale_search_dir == ""

    def test_a_daemon_error_is_returned_not_raised(self, qapp, app_state, settings_service):
        class _Rejecting(_ConfigClient):
            def update_profile_search_dirs(self, add=None, remove=None):
                raise DaemonError(code="validation_error", message="nope", status=400)

        app_state.capabilities = _caps(True)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=_Rejecting())
        assert p._sync_profile_search_dir("/home/u/old", "/home/u/new") == "nope"

    def test_a_transport_failure_is_returned_not_raised(self, qapp, app_state, settings_service):
        class _Dead(_ConfigClient):
            def update_profile_search_dirs(self, add=None, remove=None):
                raise ConnectionError("gone")

        app_state.capabilities = _caps(True)
        p = SettingsPage(state=app_state, settings_service=settings_service, client=_Dead())
        assert p._sync_profile_search_dir("/home/u/old", "/home/u/new") == "gone"
