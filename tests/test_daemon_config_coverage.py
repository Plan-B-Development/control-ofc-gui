"""DEC-285: Settings must be a complete map of the DAEMON's configuration too.

DEC-237 built this guarantee for ``AppSettings`` — every field classified, every
declared objectName resolved against a constructed page — and 14 of 29 orphaned
GUI settings were found the day it landed. Nothing did the same for the other
half of the Settings page, and the cost was exact: ``profiles.search_dirs`` was
``mutable: true`` from the day ``GET /config`` shipped, the GUI fetched it and
discarded it, and meanwhile ``services/polling.py`` added an entry to it on every
connect and the directory picker added another on every change. The daemon's
search path only ever grew, appeared in no UI, and could be pruned only by hand-
editing a root-owned ``runtime.toml``. Nothing failed when that key appeared,
because nothing was looking.

Three halves, and all three are needed:

* **Coverage** — every ``mutable: true`` key in the declared surface has a
  control, and every ``mutable: false`` key has a read-only home. A key with
  neither fails the suite.
* **Realisation** — each declared objectName resolves to a widget on a
  constructed page, so the maps describe the page as built rather than as
  intended.
* **Wiring** — each control actually POSTs when driven. A control that exists but
  whose ``connect()`` was lost is a total, silent failure of the setting, and is
  precisely the gap ``CLAUDE.md § Hard-won lessons`` records five separate
  recurrences of: *extracting a rule into a testable function does not test the
  call site.*

The declared surface lives in ``tests/fixtures/daemon_config_keys.json`` and is
pinned on the daemon side by
``daemon/tests/ipc_integration.rs::get_config_key_set_and_mutability_are_pinned``.
Neither copy can drift alone: a new daemon key reds that Rust test, and updating
the fixture to match then reds this one until the key has a control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLineEdit, QListWidget, QSpinBox, QWidget

from control_ofc.api.models import Capabilities, ControlCapability
from control_ofc.ui.components.toggle_switch import ToggleSwitch
from control_ofc.ui.pages.settings_page import (
    DAEMON_CONFIG_READONLY_WIDGETS,
    DAEMON_CONFIG_WIDGETS,
    SettingsPage,
)

from .test_daemon_config_dec243 import _ConfigClient

FIXTURE = Path(__file__).parent / "fixtures" / "daemon_config_keys.json"


def _declared() -> list[dict]:
    keys = json.loads(FIXTURE.read_text())["keys"]
    assert keys, "the declared daemon config surface must not be empty"
    return keys


def _mutable() -> set[str]:
    return {k["key"] for k in _declared() if k["mutable"]}


def _immutable() -> set[str]:
    return {k["key"] for k in _declared() if not k["mutable"]}


@pytest.fixture()
def page(qapp, app_state, settings_service):
    app_state.capabilities = Capabilities(control=ControlCapability(profile_search_dir_remove=True))
    client = _ConfigClient()
    p = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    p._refresh_daemon_config()
    return p, client


# ── Coverage ──────────────────────────────────────────────────────────


def test_every_mutable_daemon_key_has_a_control():
    """A daemon setting the GUI can neither show nor change is the defect."""
    missing = _mutable() - set(DAEMON_CONFIG_WIDGETS)
    assert not missing, (
        f"daemon config keys the daemon says are writable but Settings does not "
        f"offer: {sorted(missing)}. Add a control (and an entry in "
        f"DAEMON_CONFIG_WIDGETS), or — if it is genuinely not for the GUI — make "
        f"the daemon report it as mutable: false and record it in "
        f"DAEMON_CONFIG_READONLY_WIDGETS."
    )


def test_every_immutable_daemon_key_is_still_shown():
    """Read-only by design is not the same as hidden — both are diagnostic."""
    missing = _immutable() - set(DAEMON_CONFIG_READONLY_WIDGETS)
    assert not missing, f"daemon config keys with no read-only surface: {sorted(missing)}"


def test_no_stale_daemon_key_classifications():
    declared = _mutable() | _immutable()
    stale = (set(DAEMON_CONFIG_WIDGETS) | set(DAEMON_CONFIG_READONLY_WIDGETS)) - declared
    assert not stale, (
        f"classified keys the daemon no longer reports: {sorted(stale)}. Either the "
        f"fixture is behind the daemon, or the GUI is offering a key that is gone."
    )


def test_a_key_is_editable_or_read_only_but_never_both():
    overlap = set(DAEMON_CONFIG_WIDGETS) & set(DAEMON_CONFIG_READONLY_WIDGETS)
    assert not overlap, f"keys classified twice: {sorted(overlap)}"


# ── Realisation ───────────────────────────────────────────────────────


def test_every_declared_widget_exists_on_the_page(page):
    """The maps must describe the page as built, not as intended.

    Without this they degrade into a list of promises that stays green after the
    control they name is deleted.
    """
    p, _client = page
    declared = {**DAEMON_CONFIG_WIDGETS, **DAEMON_CONFIG_READONLY_WIDGETS}
    missing = {key: name for key, name in declared.items() if p.findChild(QWidget, name) is None}
    assert not missing, f"declared widgets that do not exist: {missing}"


def test_editable_controls_have_unique_object_names():
    names = list(DAEMON_CONFIG_WIDGETS.values())
    assert len(names) == len(set(names)), "duplicate objectName in DAEMON_CONFIG_WIDGETS"


# ── Wiring ────────────────────────────────────────────────────────────


def _drive(page, widget, monkeypatch) -> None:
    """Make a real, changed edit through *widget*'s own signal.

    Deliberately never calls the private handler: invoking it directly skips the
    ``connect()``, which is the thing most likely to be broken.
    """
    if isinstance(widget, QSpinBox):
        widget.setValue(widget.value() + widget.singleStep())
        widget.editingFinished.emit()
    elif isinstance(widget, QLineEdit):
        widget.setText("/dev/ttyACM9")
        widget.editingFinished.emit()
    elif isinstance(widget, ToggleSwitch):
        widget.setChecked(not widget.isChecked())
    elif isinstance(widget, QListWidget):
        # A list is edited by its buttons, not by itself.
        monkeypatch.setattr(
            "control_ofc.ui.pages.settings_page.QFileDialog.getExistingDirectory",
            lambda *a, **k: "/home/u/wiring-probe",
        )
        page._add_search_dir_btn.click()
    else:  # pragma: no cover - a new control type needs a driver here
        raise AssertionError(f"no driver for {type(widget).__name__}; add one")


def test_every_mutable_key_actually_writes_when_its_control_is_used(page, monkeypatch):
    """Existence is not wiring.

    ``test_every_declared_widget_exists_on_the_page`` passes for a control whose
    ``connect()`` was deleted; the setting is then completely inert with nothing
    on screen to say so. This drives each control through its own signal and
    requires the daemon to see something.
    """
    p, client = page
    unwired = []
    for key in sorted(_mutable()):
        widget = p.findChild(QWidget, DAEMON_CONFIG_WIDGETS[key])
        before = len(client.writes) + len(client.edits)
        _drive(p, widget, monkeypatch)
        if len(client.writes) + len(client.edits) == before:
            unwired.append(key)
    assert not unwired, (
        f"these controls exist but changing them reaches no daemon endpoint, so "
        f"the setting is inert: {unwired}"
    )


def test_read_only_keys_are_rendered_but_not_written(page):
    """The two immutable keys are shown so they stay diagnosable, and there is no
    path from the page to writing them — a bad socket path would lock every
    client out permanently."""
    p, client = page
    text = p._daemon_paths_label.text()
    for key in _immutable():
        assert DAEMON_CONFIG_READONLY_WIDGETS[key] == "Settings_Label_daemonPaths"
    assert "/run/control-ofc/control-ofc.sock" in text
    assert "/var/lib/control-ofc" in text
    assert client.writes == [], "rendering must write nothing"
