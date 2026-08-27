"""Shared fixtures for GUI integration / click tests."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

import pytest

from control_ofc.api.errors import DaemonUnavailable
from control_ofc.api.models import (
    ActiveProfileInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    GpuFanResetResult,
    OperationMode,
    OverrideGrant,
    OverrideReleaseResult,
    OverrideRenewResult,
    SensorHistory,
    SensorReading,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import ProfileService

# ---------------------------------------------------------------------------
# Fake daemon client that records calls and returns canned data
# ---------------------------------------------------------------------------


@dataclass
class _PersistentError:
    """Wrapper to distinguish persistent errors from one-shot errors in FakeDaemonClient."""

    exception: Exception


@dataclass
class FakeDaemonClient:
    """Drop-in replacement for DaemonClient that records calls.

    Supports error injection via ``simulate_error`` / ``simulate_unavailable``.
    """

    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    _errors: dict[str, Exception] = field(default_factory=dict)
    # Real DaemonClient always exposes a socket_path; mirror that. Diagnostics
    # worker-path tests inject their fake into the worker itself, so this value
    # is not dialled — it only needs to be truthy like production.
    socket_path: str = "/tmp/control-ofc-fake-daemon.sock"

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append((method, args, kwargs))

    def _maybe_raise(self, method: str) -> None:
        err = self._errors.get(method)
        if err is None:
            return
        if isinstance(err, _PersistentError):
            raise err.exception
        # One-shot: remove after first raise
        del self._errors[method]
        raise err

    def simulate_error(self, method: str, exception: Exception) -> None:
        """Configure *method* to raise *exception* on next call (one-shot)."""
        self._errors[method] = exception

    def simulate_persistent_error(self, method: str, exception: Exception) -> None:
        """Configure *method* to raise *exception* on every call until cleared."""
        self._errors[method] = _PersistentError(exception)

    def clear_errors(self) -> None:
        """Remove all injected errors."""
        self._errors.clear()

    def simulate_unavailable(self) -> None:
        """Set every method to persistently raise DaemonUnavailable.

        Covers the live-intent override endpoints too (DEC-163): a genuine
        "daemon is down" simulation must make ``override_take``/``renew``/
        ``release`` fail the same way the read endpoints do, so a Controls-page
        override flow driven off this fake exercises the rejection path.
        """
        exc = DaemonUnavailable()
        for name in (
            "capabilities",
            "status",
            "sensors",
            "fans",
            "hwmon_headers",
            "hwmon_rescan",
            "sensor_history",
            "override_take",
            "override_renew",
            "override_release",
        ):
            self._errors[name] = _PersistentError(exc)

    # -- read endpoints --

    def capabilities(self) -> Capabilities:
        self._record("capabilities")
        self._maybe_raise("capabilities")
        return Capabilities(daemon_version="0.2.0")

    def status(self) -> DaemonStatus:
        self._record("status")
        self._maybe_raise("status")
        return DaemonStatus(overall_status="ok", daemon_version="0.2.0")

    def sensors(self) -> list[SensorReading]:
        self._record("sensors")
        self._maybe_raise("sensors")
        return [
            SensorReading(
                id="hwmon:k10temp:0:Tctl",
                kind="CpuTemp",
                label="Tctl",
                value_c=45.0,
                source="hwmon",
                age_ms=100,
            ),
            SensorReading(
                id="hwmon:amdgpu:0:edge",
                kind="GpuTemp",
                label="edge",
                value_c=38.0,
                source="hwmon",
                age_ms=100,
            ),
        ]

    def fans(self) -> list[FanReading]:
        self._record("fans")
        self._maybe_raise("fans")
        return [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=1200, last_commanded_pwm=128, age_ms=100
            ),
            FanReading(
                id="openfan:ch01", source="openfan", rpm=1100, last_commanded_pwm=128, age_ms=100
            ),
        ]

    def poll(self) -> tuple:
        self._record("poll")
        self._maybe_raise("poll")
        return self.status(), self.sensors(), self.fans()

    def hwmon_headers(self) -> list:
        self._record("hwmon_headers")
        self._maybe_raise("hwmon_headers")
        return []

    def hwmon_rescan(self) -> list:
        self._record("hwmon_rescan")
        self._maybe_raise("hwmon_rescan")
        return []

    def sensor_history(self, entity_id: str, last: int = 250) -> SensorHistory:
        self._record("sensor_history", entity_id, last)
        self._maybe_raise("sensor_history")
        return SensorHistory(entity_id=entity_id, points=[])

    def active_profile(self) -> ActiveProfileInfo | None:
        self._record("active_profile")
        self._maybe_raise("active_profile")
        return ActiveProfileInfo(active=False)

    def reset_gpu_fan(self, gpu_id: str, *, timeout: float | None = None) -> GpuFanResetResult:
        del timeout
        self._record("reset_gpu_fan", gpu_id)
        self._maybe_raise("reset_gpu_fan")
        return GpuFanResetResult(gpu_id=gpu_id, reset=True)

    # -- live manual-override intent (DEC-163) --

    def override_take(
        self,
        control_id: str,
        pwm_percent: int,
        *,
        ttl_secs: int | None = None,
        timeout: float | None = None,
    ) -> OverrideGrant:
        del ttl_secs, timeout
        self._record("override_take", control_id, pwm_percent)
        self._maybe_raise("override_take")
        return OverrideGrant(
            control_id=control_id,
            override_token=1,
            pwm_percent=pwm_percent,
            ttl_secs=15,
            renew_secs=5,
            expires_in_secs=15,
        )

    def override_renew(
        self, control_id: str, override_token: int, *, timeout: float | None = None
    ) -> OverrideRenewResult:
        del timeout
        self._record("override_renew", control_id, override_token)
        self._maybe_raise("override_renew")
        return OverrideRenewResult(
            control_id=control_id,
            override_token=override_token,
            ttl_secs=15,
            expires_in_secs=15,
        )

    def override_release(
        self, control_id: str, override_token: int, *, timeout: float | None = None
    ) -> OverrideReleaseResult:
        del timeout
        self._record("override_release", control_id, override_token)
        self._maybe_raise("override_release")
        return OverrideReleaseResult(control_id=control_id, released=True)


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _flush_deferred_deletes():
    """Destroy each test's Qt widget tree deterministically at teardown (DEC-230).

    pytest-qt's cleanup calls ``close()`` + ``deleteLater()`` + ``processEvents()``
    — but ``processEvents()`` does **not** dispatch ``DeferredDelete``, so the C++
    widgets outlive their own test (a full-suite probe found ~5,100 leaked
    top-level widgets, incl. 64 ``MainWindow``). Trapped in reference cycles, they
    are finalized later by the cyclic GC in cycle-arbitrary order; when a parent
    wrapper is freed before a child's, ``~QWidget -> deleteChildren()`` makes
    shiboken's ``releaseWrapper`` dereference freed memory — the intermittent,
    site-drifting SIGSEGV that reddened the py3.12 CI leg.

    Flushing the posted ``DeferredDelete`` events here destroys those trees now,
    top-down with wrappers consistent, instead of leaving them for the GC. This
    fixture is defined **first in this file** so it is set up first among the
    fixtures here and therefore torn down last — after qtbot has posted the
    ``deleteLater()`` events this dispatches. Net-new test scaffolding; no
    production code changes.

    One fixture still outlives it, deliberately: ``_isolate_user_config`` in the
    **repo-root** ``conftest.py``. Root conftests are set up before package ones,
    so the config sandbox is torn down after this flush — meaning the widget
    destruction below runs with ``HOME``/XDG still redirected, and a settings
    write during destruction cannot reach the real config (DEC-244).
    """
    yield
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _reap_orphaned_top_levels():
    """Delete top-level widgets a test created but never registered (DEC-287).

    ``_flush_deferred_deletes`` above destroys trees that were *already
    scheduled* for deletion — it dispatches the ``DeferredDelete`` events
    pytest-qt posts for widgets passed to ``qtbot.addWidget``. A widget
    constructed with no parent and never registered is never scheduled, so it is
    never flushed: nothing owns it, nothing deletes it, and it survives to the
    end of the session. Measured before this fixture: **420 orphaned top-level
    trees holding 20,412 widgets** by the end of the run, from just two classes
    (336 ``QFrame``, 84 ``SettingsPage``).

    That is not merely untidy. Every application-wide Qt operation is O(live
    widgets) because Qt re-polishes each one, so the residue is charged to
    whichever test happens to run last: one ``apply_theme`` call cost **507ms**
    at the end of the suite, and a theme test making ten of them blew its
    timeout on CI and blocked a release. The cost lands on an innocent test and
    grows every time a feature adds widgets somewhere else entirely.

    Reaping per test keeps the population flat instead. Three details matter:

    * **Strong references, compared with ``is``.** Holding the pre-existing
      wrappers alive for the test's duration is what makes the comparison sound:
      a wrapper that were allowed to be collected could have its ``id()``
      recycled onto a widget created during the test, which would make a
      *pre-existing* widget look new and get it deleted out from under its
      owner.
    * **``deleteLater()``, never ``shiboken.delete()``.** Destroying a live C++
      object out from under its wrapper is precisely the use-after-free DEC-230
      exists to prevent. Posting the event lets the flush above destroy the tree
      top-down with wrappers consistent.
    * **Defined *after* ``_flush_deferred_deletes``**, so it is set up second and
      therefore torn down *first* — the deletes posted here are still pending
      when that fixture dispatches them. Reversing the two would leave every
      reaped tree queued until the next test, which defeats the purpose.

    Safe because no fixture **in this repository** outlives a test: `tests/` and
    the root `conftest.py` declare no module-, class- or session-scoped fixtures
    (``grep -c 'scope="module"\\|scope="session"\\|scope="class"'`` returns 0), so
    a top-level widget absent at setup is owned by the test that just finished
    and by nothing else.

    That argument is deliberately scoped to this repository, because it is the
    part we control. A third-party plugin holding a session-scoped ``QWidget``
    would be reaped on the first test and break. **pytest-qt does not** — its
    ``qapp``/``qtbot`` machinery holds the ``QApplication`` singleton, which is
    not a widget and never appears in ``topLevelWidgets()`` — so this is a
    constraint on future plugin choices, not a live hazard. If a plugin ever does
    introduce one, exempt it here by name; `285-i` records the limit.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    pre_existing = list(app.topLevelWidgets()) if app is not None else []

    yield

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if any(widget is known for known in pre_existing):
            continue
        # RuntimeError: the C++ side is already gone and only the wrapper
        # remains, so there is nothing left to schedule. See 285-i — this is
        # broader than that one case by construction, and narrowing it would
        # mean matching on the exception message, which is its own fragility.
        with contextlib.suppress(RuntimeError):
            widget.deleteLater()


@pytest.fixture(autouse=True)
def _neutralize_modals(monkeypatch):
    """Stop any modal dialog from blocking the test run.

    Per pytest-qt's note on modal dialogs, ``QDialog.exec()`` and the static
    ``QMessageBox`` / ``QFileDialog`` / ``QInputDialog`` helpers spin a nested
    event loop and block until the user responds — in a headless test that is
    forever (this is what hung the suite on the delete-profile confirmation).
    We patch each blocking entry point on its shared Qt class: every UI module
    does ``from PySide6.QtWidgets import QMessageBox`` (etc.), so they all
    reference the same class object and one patch covers every importer.

    Defaults are deliberately **safe**: confirmations are *declined*, file
    pickers return "cancelled", and ``exec()`` returns ``Rejected``. A test
    that unexpectedly pops a modal therefore fails by NOT performing the action
    rather than hanging or silently doing something destructive. Tests that
    exercise the accept path override the relevant method explicitly (e.g.
    ``monkeypatch.setattr(QMessageBox, "question", lambda *a, **k:
    QMessageBox.StandardButton.Yes)``) — that wins because it is applied after
    this fixture.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    sb = QMessageBox.StandardButton
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: sb.No, raising=False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: sb.Ok, raising=False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""), raising=False)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False), raising=False)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False), raising=False)
    monkeypatch.setattr(
        QDialog, "exec", lambda self, *a, **k: QDialog.DialogCode.Rejected, raising=False
    )


@pytest.fixture(autouse=True)
def _sync_override_dispatch(monkeypatch):
    """DEC-220: manual-override HTTP (Controls page) defaults to a worker thread.
    Run it inline in every test so the suite stays synchronous and never leaks an
    unjoined QThread (which crashes at interpreter teardown). The one test that
    needs the real threaded path — test_controls_dec220 — opts back in."""
    from control_ofc.ui.pages.controls_page import ControlsPage

    monkeypatch.setattr(ControlsPage, "_OVERRIDE_USE_THREAD", False)


@pytest.fixture(autouse=True)
def _isolate_libsensors(monkeypatch):
    """Keep the host's ``/etc/sensors.d`` out of every test (DEC-229).

    ``load_libsensors_configs(paths=[])`` does **not** isolate, which is the
    trap: the body reads ``search_paths = paths or LIBSENSORS_CONFIG_PATHS``, so
    an empty list is falsy and means "use the system defaults". Tests that
    passed ``[]`` — including the resolver's own pre-existing ones — therefore
    read the real ``/etc/sensors3.conf`` and their results depended on the
    machine running them. They pass on this host only because its ``it87-*``
    block happens to carry no fan labels.

    Emptying the module constant is what actually isolates. Tests that want
    specific config files still monkeypatch the constant themselves or pass a
    non-empty ``paths=`` / ``sensors_paths=``; both win over this fixture.
    """
    from control_ofc.knowledge import hwmon_label_resolver as r

    monkeypatch.setattr(r, "LIBSENSORS_CONFIG_PATHS", [])
    r.clear_libsensors_cache()
    yield
    r.clear_libsensors_cache()


@pytest.fixture()
def fake_client():
    return FakeDaemonClient()


@pytest.fixture()
def fake_client_unavailable():
    """FakeDaemonClient pre-configured to raise DaemonUnavailable on all calls."""
    client = FakeDaemonClient()
    client.simulate_unavailable()
    return client


@pytest.fixture()
def app_state():
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def settings_service(tmp_path, monkeypatch):
    """A service wired exactly the way ``main.py`` wires the real one.

    The ``load()`` is load-bearing, not ceremony: since DEC-244 an unloaded
    service refuses every write, so without it ``save()`` is a permanent no-op
    and any test expecting a real round-trip through this fixture would silently
    assert nothing. Mirrors the ``profile_service`` fixture below.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    return svc
