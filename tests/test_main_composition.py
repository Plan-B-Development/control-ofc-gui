"""``main()``'s bootstrap composition, driven end to end (``AU-c``).

Why this exists
---------------
``main()`` is 240 lines of composition and, until this file, **no test called
it past the single-instance guard**. ``_resolve_demo_mode``, ``_probe_daemon``
and ``_resolve_startup_theme`` are extracted and thoroughly unit-tested in
``test_startup_resolution.py``; ``TestMainWiring`` in ``test_single_instance.py``
drives ``main()`` but deliberately halts it at ``register_bundled_fonts()``,
the first statement after the guard. So the settings load, the path overrides,
the bundled-theme install, the theme application, the demo decision, the service
wiring, the window construction and the whole exit path executed in nothing.

That is ``CLAUDE.md § Hard-won lessons``' untested-gatherer trap (DEC-340)
exactly: a handler split into *decide* and *compose* gets tests on the pure half
because it is the easy one, and a mis-wiring of the composition — a helper's
result discarded, a service built before its dependency, an argument transposed
— passes the entire suite.

What it asserts, and how
------------------------
Every assertion here is a **relationship against the helper's own return**, never
a literal: ``active_theme()`` against ``_resolve_startup_theme(s.theme_name)``,
``demo_mode`` against ``_resolve_demo_mode(...)``, and the objects the window was
handed against the objects the polling service was handed. A literal would be
satisfied by a call site that ignores the helper and recomputes the same answer
by luck, which is the defect this file exists to catch.

Three arms, because one cannot discriminate
-------------------------------------------
``--demo`` (local-only), a reachable daemon (live: client + polling), and the
DEC-139 fallback (probe fails → demo). The third is the sample that can move:
with only the first two, a ``main()`` that ignored the probe entirely would still
pass.

Containment
-----------
``main()`` writes a lot of process-global state — ``sys.excepthook``, a Qt message
handler, the SIGINT handler, ``paths._overrides``, the application palette,
stylesheet, font, name, version and window icon, and ``main._diagnostics``. An
earlier attempt at this test (recorded in ``TestMainWiring._isolate_main``) let
``set_path_overrides`` and an app-wide ``apply_theme`` escape and reddened a
splitter test in a different file. The ``contained_main`` fixture below saves and
restores every one of them; that containment is what makes running the whole
bootstrap affordable.
"""

from __future__ import annotations

import json
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from control_ofc import main as main_mod
from control_ofc import paths as paths_mod
from control_ofc.api.errors import DaemonUnavailable
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.single_instance import SingleInstance
from control_ofc.ui import theme as theme_mod
from control_ofc.ui.main_window import MainWindow

#: Distinctive, so "the exit code propagated" cannot be satisfied by a 0 that
#: some other path produced.
EXIT_CODE = 7

#: A bundled preset, installed by ``main()`` itself into the *overridden* themes
#: directory. Using a preset rather than the fallback is what makes the theme
#: assertion discriminate — see ``_assert_theme_was_resolved_and_applied``.
PERSISTED_THEME = "Classic Blue"
PERSISTED_THEME_FILE = "classic_blue.json"


class _RecordingClient:
    """Stands in for ``DaemonClient`` at both sites ``main()`` builds one.

    ``main()`` reaches ``DaemonClient`` twice — once inside ``_probe_daemon``
    (with a ``timeout``) and once for the live-mode client it hands to
    ``ProfileService`` and ``MainWindow`` — and both resolve the same module
    global, so one patch covers both. ``list_profiles`` raises
    ``DaemonUnavailable`` so ``ProfileService.load()`` takes its documented
    offline fallback to the (empty) local store: deterministic, and it keeps this
    file's subject the composition rather than the profile store.
    """

    def __init__(self, socket_path: str, timeout: float | None = None, *, reachable: bool = True):
        self.socket_path = socket_path
        self.timeout = timeout
        self.reachable = reachable
        self.closed = False
        self.order: list[str] | None = None

    def status(self) -> object:
        if not self.reachable:
            raise DaemonUnavailable()
        return object()

    def list_profiles(self) -> list[dict]:
        raise DaemonUnavailable()

    def close(self) -> None:
        self.closed = True
        if self.order is not None:
            # The probe's own client is closed inside `_probe_daemon`, long
            # before teardown; labelling it separately keeps the teardown
            # sequence below about the long-lived client it is really pinning.
            self.order.append("probe.close" if self.timeout is not None else "client.close")


class _RecordingPolling:
    """Stands in for ``PollingService``; records construction and lifecycle.

    A real one spawns a worker thread, which this file has no use for — the
    subject is *what main() handed it*, which is precisely the wiring a
    transposed argument would break.
    """

    def __init__(self, *args, order: list[str], **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self._order = order

    def start(self) -> None:
        self.started = True
        self._order.append("polling.start")

    def shutdown(self) -> None:
        self._order.append("polling.shutdown")


class _AppProxy:
    """The test's own ``QApplication``, with ``exec()`` stubbed.

    ``main()`` builds a ``QApplication``; a second one in the same process is not
    possible, so the factory hands back the session's instance wrapped in this
    proxy. Everything except ``exec`` delegates, so the application name, version
    and window icon are really set on the real object (and really restored by the
    fixture).
    """

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self.exec_calls = 0

    def exec(self) -> int:
        self.exec_calls += 1
        return EXIT_CODE

    def __getattr__(self, name: str):
        return getattr(self._app, name)


@dataclass
class _Harness:
    tmp: Path
    themes_override: Path
    socket_path: str
    app: _AppProxy
    order: list[str] = field(default_factory=list)
    clients: list[_RecordingClient] = field(default_factory=list)
    pollings: list[_RecordingPolling] = field(default_factory=list)
    window_kwargs: dict = field(default_factory=dict)
    window: MainWindow | None = None
    #: What the next ``_RecordingClient`` reports from ``status()``. The probe's
    #: answer, in other words — the one input ``_resolve_demo_mode`` cannot see
    #: from the settings alone.
    daemon_reachable: bool = True

    def settings_as_main_loaded_them(self):
        """Re-read the settings file main() read, for relationship assertions."""
        svc = AppSettingsService()
        svc.load()
        return svc.settings


@pytest.fixture
def contained_main(monkeypatch, tmp_path, qtbot):
    """Run the real ``main()`` with every global it writes saved and restored."""
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)

    # The themes directory the settings file points `set_path_overrides` at. It
    # must already exist: `ensure_bundled_themes_installed` is a no-op on a
    # missing directory, and `ensure_dirs()` runs BEFORE the override is applied
    # so it creates the default location, not this one. A returning user's
    # override directory does exist, which is the case being modelled.
    themes_override = tmp_path / "custom-themes"
    themes_override.mkdir()

    app = QApplication.instance()
    saved_palette = QPalette(app.palette())
    saved_sheet = app.styleSheet()
    saved_font = app.font()
    saved_theme = theme_mod._active_theme
    saved_icon = app.windowIcon()
    saved_app_name = app.applicationName()
    saved_app_version = app.applicationVersion()
    saved_overrides = dict(paths_mod._overrides)
    saved_excepthook = sys.excepthook
    saved_sigint = signal.getsignal(signal.SIGINT)
    saved_diagnostics = main_mod._diagnostics
    saved_close = SingleInstance.close
    saved_log_event = DiagnosticsService.log_event

    h = _Harness(
        tmp=tmp_path,
        themes_override=themes_override,
        socket_path=str(tmp_path / "control-ofc.sock"),
        app=_AppProxy(app),
    )

    monkeypatch.setattr(main_mod, "QApplication", lambda *a, **k: h.app)

    def _client_factory(socket_path, timeout=None):
        client = _RecordingClient(socket_path, timeout, reachable=h.daemon_reachable)
        client.order = h.order
        h.clients.append(client)
        return client

    monkeypatch.setattr(main_mod, "DaemonClient", _client_factory)

    def _polling_factory(*args, **kwargs):
        polling = _RecordingPolling(*args, order=h.order, **kwargs)
        h.pollings.append(polling)
        return polling

    monkeypatch.setattr(main_mod, "PollingService", _polling_factory)

    def _window_factory(**kwargs):
        h.window_kwargs = dict(kwargs)
        window = MainWindow(**kwargs)
        qtbot.addWidget(window)
        h.window = window
        return window

    monkeypatch.setattr(main_mod, "MainWindow", _window_factory)

    def _recording_close(self):
        h.order.append("instance.close")
        return saved_close(self)

    monkeypatch.setattr(SingleInstance, "close", _recording_close)

    def _recording_log_event(self, level, source, message, **kwargs):
        if message == "GUI exiting":
            h.order.append("diagnostics.exiting")
        return saved_log_event(self, level, source, message, **kwargs)

    monkeypatch.setattr(DiagnosticsService, "log_event", _recording_log_event)

    try:
        yield h
    finally:
        app.setPalette(saved_palette)
        app.setStyleSheet(saved_sheet)
        app.setFont(saved_font)
        app.setWindowIcon(saved_icon)
        app.setApplicationName(saved_app_name)
        app.setApplicationVersion(saved_app_version)
        theme_mod._active_theme = saved_theme
        paths_mod._overrides.clear()
        paths_mod._overrides.update(saved_overrides)
        sys.excepthook = saved_excepthook
        signal.signal(signal.SIGINT, saved_sigint)
        main_mod._diagnostics = saved_diagnostics
        qInstallMessageHandler(None)


def _write_settings(h: _Harness, **overrides) -> None:
    payload = {
        "theme_name": PERSISTED_THEME,
        "themes_dir_override": str(h.themes_override),
        "demo_on_disconnect": False,
    }
    payload.update(overrides)
    path = paths_mod.app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")


def _run(h: _Harness, argv: list[str], monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == EXIT_CODE, (
        "main() must return the event loop's exit code, not a code of its own"
    )
    assert h.app.exec_calls == 1, "the event loop must be entered exactly once"


# ---------------------------------------------------------------------------
# Shared composition assertions
# ---------------------------------------------------------------------------


def _assert_theme_was_resolved_and_applied(h: _Harness) -> None:
    """The applied theme is the RESOLVED one, and the overrides came first.

    Three links in one chain, and each would be satisfied by an accident if
    asserted alone: the override must be applied before ``themes_dir()`` is read,
    the bundled presets must be installed into *that* directory, and the theme
    ``_resolve_startup_theme`` returns must be the one ``apply_theme`` pushed.
    """
    s = h.settings_as_main_loaded_them()

    # Precondition, and it is deliberately read from the SETTINGS rather than
    # from `_resolve_startup_theme`. A precondition derived from the thing under
    # test becomes unsatisfiable the moment that thing breaks, and then the test
    # goes red saying the harness is wrong instead of naming the defect
    # (`CLAUDE.md § Hard-won lessons`, DEC-348). Measured: with
    # `set_path_overrides` moved after the theme install, the resolve-based form
    # of this line fired first and pointed at the fixture; this form lets the
    # override assertion below fire and name the real cause.
    assert s.theme_name == PERSISTED_THEME != theme_mod.default_dark_theme().name, (
        "precondition: the persisted setting must ask for a non-fallback theme"
    )

    assert paths_mod.themes_dir() == h.themes_override, (
        "set_path_overrides must have run before anything read themes_dir()"
    )
    assert not list((paths_mod.config_dir() / "themes").glob("*.json")), (
        "a preset in the default themes dir means the override was applied too late"
    )
    assert (h.themes_override / PERSISTED_THEME_FILE).exists(), (
        "the bundled presets must be installed into the OVERRIDDEN themes dir"
    )
    resolved = main_mod._resolve_startup_theme(s.theme_name)
    assert resolved.name == PERSISTED_THEME, (
        "the persisted theme must be resolvable from the installed presets, or "
        "the comparison below is default-against-default and asserts nothing"
    )
    assert theme_mod.active_theme().name == resolved.name, (
        "the applied theme must be the one _resolve_startup_theme returned"
    )
    assert h.app.styleSheet(), "apply_theme must have pushed the stylesheet channel"


def _assert_window_and_services_are_wired(h: _Harness) -> None:
    kw = h.window_kwargs
    assert h.window is not None, "main() must construct a window"
    # isVisibleTo(None), not isVisible(): the documented headless rule. Measured
    # on this platform, it is False before show() and True after, so it
    # discriminates — an assertion that cannot go False proves nothing.
    assert h.window.isVisibleTo(None) is True, "the window must be shown"

    assert kw["profile_service"]._client is kw["client"], (
        "the ProfileService and the window must hold the SAME client object"
    )
    # DEC-431 (`DC-q`): a demo session reads the real profiles and writes none.
    # A relationship, not a literal, so each of the three arms pins its side.
    assert kw["profile_service"]._persist is (not kw["demo_mode"]), (
        "the profile service must persist exactly when the window is not in demo mode"
    )
    assert kw["diagnostics_service"]._state is kw["state"], (
        "diagnostics must be built against the state the window renders"
    )
    assert main_mod._diagnostics is kw["diagnostics_service"], (
        "the uncaught-exception hook must be pointed at the real diagnostics "
        "service, or an escaped exception never reaches the support bundle"
    )
    assert sys.excepthook is main_mod._handle_uncaught
    assert h.app.applicationVersion() == main_mod.APP_VERSION


def _assert_demo_decision_was_applied(h: _Harness, *, cli_demo: bool) -> None:
    s = h.settings_as_main_loaded_them()
    # The probe only runs when it can change the answer, so a run that never
    # built a probe client is a run whose reachability term is the default True.
    probed = any(c.timeout is not None for c in h.clients)
    reachable = h.daemon_reachable if probed else True
    expected = main_mod._resolve_demo_mode(cli_demo, s.demo_on_disconnect, reachable)
    assert h.window_kwargs["demo_mode"] == expected, (
        "the window's mode must be the one _resolve_demo_mode decided"
    )
    return expected


# ---------------------------------------------------------------------------
# The three arms
# ---------------------------------------------------------------------------


def test_a_demo_launch_composes_a_local_only_application(contained_main, monkeypatch):
    h = contained_main
    _write_settings(h)

    _run(h, ["control-ofc-gui", "--demo"], monkeypatch)

    _assert_theme_was_resolved_and_applied(h)
    _assert_window_and_services_are_wired(h)
    assert _assert_demo_decision_was_applied(h, cli_demo=True) is True

    assert h.window_kwargs["client"] is None, "demo mode must not hand the window a client"
    assert h.clients == [], "demo mode must not build a daemon client at all"
    assert h.pollings == [], "demo mode must not start polling"
    assert h.order == ["diagnostics.exiting", "instance.close"], (
        f"unexpected teardown for a demo launch: {h.order}"
    )


def test_a_live_launch_composes_the_client_and_polling(contained_main, monkeypatch):
    h = contained_main
    h.daemon_reachable = True
    _write_settings(h, demo_on_disconnect=True)

    _run(h, ["control-ofc-gui", "--socket", h.socket_path], monkeypatch)

    _assert_theme_was_resolved_and_applied(h)
    _assert_window_and_services_are_wired(h)
    assert _assert_demo_decision_was_applied(h, cli_demo=False) is False

    # The probe ran (it carries a timeout) and a separate live client was built.
    probes = [c for c in h.clients if c.timeout is not None]
    live = [c for c in h.clients if c.timeout is None]
    assert len(probes) == 1, "a reachable-daemon launch must probe exactly once"
    assert probes[0].closed is True, "the probe client must always be closed"
    assert len(live) == 1, "live mode must build exactly one long-lived client"
    assert h.window_kwargs["client"] is live[0]
    assert live[0].socket_path == h.socket_path, "--socket must reach the live client"

    assert len(h.pollings) == 1, "live mode must construct the polling service"
    polling = h.pollings[0]
    assert polling.args[0] is h.window_kwargs["state"], (
        "polling and the window must share one AppState"
    )
    assert polling.args[1] == h.socket_path
    assert polling.kwargs["history"] is h.window_kwargs["history"], (
        "polling and the window must share one HistoryStore"
    )
    assert polling.kwargs["diagnostics"] is h.window_kwargs["diagnostics_service"], (
        "polling must write into the same diagnostics service the UI reads"
    )
    assert polling.started is True, "polling must be started after the window is shown"

    # DEC-257 / test_worker_teardown_p2_2: the client is closed AFTER the polling
    # workers are joined, and the instance lock is released last. Asserted as the
    # whole sequence, because the order is the load-bearing part.
    assert h.order == [
        "probe.close",
        "polling.start",
        "diagnostics.exiting",
        "polling.shutdown",
        "client.close",
        "instance.close",
    ], f"unexpected teardown order for a live launch: {h.order}"


def test_an_unreachable_daemon_falls_back_to_demo_at_the_composition(contained_main, monkeypatch):
    """DEC-139, driven through ``main()`` rather than through the helper.

    The arm that can move: with only the two above, a ``main()`` that discarded
    ``_probe_daemon``'s answer would still pass both.
    """
    h = contained_main
    h.daemon_reachable = False
    _write_settings(h, demo_on_disconnect=True)

    _run(h, ["control-ofc-gui", "--socket", h.socket_path], monkeypatch)

    _assert_theme_was_resolved_and_applied(h)
    _assert_window_and_services_are_wired(h)
    assert _assert_demo_decision_was_applied(h, cli_demo=False) is True

    assert [c for c in h.clients if c.timeout is not None], "the probe must have run"
    assert [c for c in h.clients if c.timeout is None] == [], (
        "an unreachable daemon must not leave a live client behind"
    )
    assert h.window_kwargs["client"] is None, (
        "falling back to demo must withhold the client from the window, or the "
        "UI offers real control against a daemon that is not there"
    )
    assert h.pollings == [], "demo mode must not poll a daemon it decided was absent"
    # The fallback path has a teardown shape of its own — a probe client that must
    # be closed inside `_probe_daemon`, and then neither a polling shutdown nor a
    # long-lived client close, because it built neither. Asserted as the whole
    # sequence like the other two arms, so a reorder confined to this path cannot
    # hide behind them.
    assert h.order == ["probe.close", "diagnostics.exiting", "instance.close"], (
        f"unexpected teardown for a demo-fallback launch: {h.order}"
    )
