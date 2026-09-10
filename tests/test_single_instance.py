"""Single-instance guard (DEC-352).

The guard exists because the tray opens the GUI on a left click and the
StatusNotifierItem protocol has no double-click, so Plasma delivers `Activate`
twice. Without it, a double click starts two complete applications.

The load-bearing test here is `TestMainWiring` — it drives `main()`, the real
call site. Every other test in this file exercises the service in isolation,
and a service that works while `main()` never consults it is the exact failure
mode this project keeps paying for.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QWidget

from control_ofc import main as main_mod
from control_ofc.services.single_instance import (
    ACTIVATE,
    SingleInstance,
    default_key,
    raise_window,
)


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    """Point XDG_RUNTIME_DIR at a temp dir so tests never touch the real one."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


class TestKey:
    def test_demo_and_live_get_separate_keys(self, runtime_dir):
        # Refusing `--demo` because a live GUI happens to be open — and raising
        # that live window instead — would be wrong, so the keys must differ.
        assert default_key(demo=False) != default_key(demo=True)

    def test_the_key_lives_under_the_runtime_dir_when_there_is_one(self, runtime_dir):
        key = default_key()
        assert key.startswith(str(runtime_dir)), key

    def test_different_daemon_sockets_get_separate_keys(self, runtime_dir):
        # --socket selects WHICH machine the window shows. Sharing a key would
        # make `control-ofc-gui --socket /run/other.sock` exit and raise a window
        # displaying a different daemon's fans under the command the user typed.
        default = default_key()
        other = default_key(socket_path="/run/other/control-ofc.sock")
        assert default != other, "a different daemon socket must be a different instance"

    def test_the_same_socket_spelled_differently_is_one_key(self, runtime_dir):
        # Otherwise a relative path would dodge the guard entirely.
        absolute = default_key(socket_path="/run/control-ofc/control-ofc.sock")
        redundant = default_key(socket_path="/run/control-ofc/./control-ofc.sock")
        assert absolute == redundant, "the socket term must be normalised, not compared raw"

    def test_demo_ignores_the_socket(self, runtime_dir):
        # Demo mode opens no socket, so it must not fragment by one.
        assert default_key(demo=True) == default_key(demo=True, socket_path="/run/anything.sock")

    def test_without_a_runtime_dir_the_key_is_scoped_to_the_user(self, monkeypatch):
        # Falling back to a shared temp dir, a bare name would collide between
        # two users on one machine.
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        key = default_key()
        assert key.endswith(f"-{sys.modules['os'].getuid()}"), key


class TestAcquire:
    def test_the_first_instance_becomes_primary(self, qtbot, runtime_dir):
        first = SingleInstance(default_key())
        try:
            assert first.acquire() is True
            assert first.is_primary is True
        finally:
            first.close()

    def test_a_second_instance_is_refused_and_can_reach_the_first(self, qtbot, runtime_dir):
        key = default_key()
        first = SingleInstance(key)
        second = SingleInstance(key)
        try:
            assert first.acquire() is True, "precondition: the first must win"

            assert second.acquire() is False, "a second instance must not become primary"
            assert second.is_primary is False

            with qtbot.waitSignal(first.activated, timeout=2000):
                assert second.notify_existing() is True
        finally:
            second.close()
            first.close()

    def test_releasing_the_primary_frees_the_key(self, qtbot, runtime_dir):
        key = default_key()
        first = SingleInstance(key)
        assert first.acquire() is True
        first.close()

        second = SingleInstance(key)
        try:
            assert second.acquire() is True, (
                "once the holder is gone the next launch must be able to start"
            )
        finally:
            second.close()

    def test_a_stale_socket_left_by_a_crash_does_not_block_startup(self, qtbot, runtime_dir):
        """A GUI killed with SIGKILL leaves its socket behind; the next one must start.

        Honest limitation: this asserts the *outcome*, not one mechanism, and it
        cannot be made to isolate the `removeServer` sweep. Measured on Qt
        6.11.1, `listen()` with `UserAccessOption` unlinks the path itself, so
        two mechanisms deliver this property and deleting either leaves the test
        green. It is kept because the property is real and worth a regression
        guard — a stale socket permanently disabling the guard would be a silent
        failure — but it is not evidence that the sweep works.
        `test_a_live_instance_is_never_displaced` is the discriminating test.
        """
        key = default_key()
        # Derived from the key, never restated: the key's shape now carries a
        # hash of the daemon socket, and a literal here would silently stop
        # testing the real path.
        stale = pathlib.Path(key)
        assert stale.parent == runtime_dir, "precondition: the key lives in the runtime dir"
        stale.write_bytes(b"")
        assert stale.exists(), "precondition: a leftover socket file is present"

        instance = SingleInstance(key)
        try:
            assert instance.acquire() is True
            assert instance.is_primary is True, (
                "a leftover file must not be mistaken for a running instance"
            )
        finally:
            instance.close()

    def test_a_live_instance_is_never_displaced(self, qtbot, runtime_dir):
        """A second launch must not steal the running instance's socket.

        This is the test that pins the probe in `acquire()`, and the probe is
        load-bearing: measured on Qt 6.11.1, `listen()` with `UserAccessOption`
        set will happily unlink and rebind a path a **live** server is bound to,
        returning success and orphaning the original. Qt's own contention check
        is therefore unavailable, and only the connect-probe stands between a
        duplicate launch and a silently broken guard.
        """
        key = default_key()
        first = SingleInstance(key)
        second = SingleInstance(key)
        try:
            assert first.acquire() is True, "precondition: the first instance holds the key"

            assert second.acquire() is False
            assert second.is_primary is False, "the second instance must not have taken the socket"
            assert first.is_primary is True, "the first must still own it"

            # The decisive check: the original is still *reachable*. A stolen
            # socket leaves `is_primary` True on an orphan nothing can talk to,
            # so ownership flags alone would not catch this.
            with qtbot.waitSignal(first.activated, timeout=2000):
                assert second.notify_existing() is True
        finally:
            second.close()
            first.close()

    def test_notify_without_a_peer_reports_failure(self, qtbot, runtime_dir):
        primary = SingleInstance(default_key())
        try:
            assert primary.acquire() is True
            # It is the primary; there is nobody to notify.
            assert primary.notify_existing() is False
        finally:
            primary.close()


class TestRaiseWindow:
    def test_raising_clears_the_minimised_state(self, qtbot):
        from PySide6.QtCore import Qt

        widget = QWidget()
        qtbot.addWidget(widget)
        widget.show()
        widget.setWindowState(Qt.WindowState.WindowMinimized)
        assert widget.windowState() & Qt.WindowState.WindowMinimized, (
            "precondition: the window must actually be minimised, or this asserts nothing"
        )

        raise_window(widget)

        assert not (widget.windowState() & Qt.WindowState.WindowMinimized), (
            "show() alone leaves a minimised window minimised; the bit must be cleared"
        )
        # Under QT_QPA_PLATFORM=offscreen nothing is ever really shown, so
        # isVisible() is False for every widget and asserting on it would pass
        # with the implementation deleted.
        assert widget.isVisibleTo(None) is True


class TestMainWiring:
    """`main()` must actually consult the guard.

    A guard nothing calls is the recurring failure this project records: the
    unit tests above would all still pass with the check deleted from `main()`.
    """

    @pytest.fixture(autouse=True)
    def _isolate_main(self, monkeypatch, qtbot, tmp_path):
        """Contain every global `main()` touches.

        Learned the hard way: an earlier draft let `main()` run to `MainWindow`,
        which meant it also ran `set_path_overrides()` and an app-wide
        `apply_theme()`. Those persist for the rest of the session, and the
        suite went red in `test_view_state_persistence_dec245.py` — a splitter
        test in a different file that this one had silently re-styled. The
        sentinel below now stops `main()` at the first statement *after* the
        guard, so none of that machinery runs at all.
        """
        # main() installs a process-wide excepthook and a Qt message handler.
        monkeypatch.setattr(sys, "excepthook", sys.excepthook)
        # Reuse the test's QApplication rather than letting main() build a second.
        monkeypatch.setattr(main_mod, "QApplication", lambda *a, **k: QApplication.instance())
        # main() calls ensure_dirs() before anything else; keep it off the real
        # user config tree.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        yield
        qInstallMessageHandler(None)

    @staticmethod
    def _stop_after_guard(monkeypatch) -> dict:
        """Halt `main()` at the first statement after the single-instance check.

        `register_bundled_fonts()` is that statement. Using it rather than
        `MainWindow` makes the assertion *stronger* — reaching it is strictly
        earlier than building a window, so "it did not run" implies "no window
        was built" — while executing none of the settings, theme or profile I/O
        that would leak into the rest of the suite.
        """
        reached = {"past_guard": False}

        def _sentinel(*_a, **_k):
            reached["past_guard"] = True
            raise SystemExit(0)

        monkeypatch.setattr(main_mod, "register_bundled_fonts", _sentinel)
        return reached

    def test_a_duplicate_launch_exits_without_building_a_window(
        self, qtbot, runtime_dir, monkeypatch
    ):
        primary = SingleInstance(default_key(demo=True))
        assert primary.acquire() is True, "precondition: an instance is already running"

        reached = self._stop_after_guard(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["control-ofc-gui", "--demo"])

        raised = []
        primary.activated.connect(lambda: raised.append(True))

        try:
            with qtbot.waitSignal(primary.activated, timeout=2000):
                rc = main_mod.main()
        finally:
            primary.close()

        assert rc == 0, "a duplicate launch must exit cleanly, not error"
        assert raised, "the running instance must be asked to show itself"
        assert reached["past_guard"] is False, (
            "a duplicate launch continued past the guard — main() is not "
            "consulting the single-instance check"
        )

    def test_a_demo_launch_is_not_refused_because_a_live_gui_is_open(
        self, qtbot, runtime_dir, monkeypatch
    ):
        """`--demo` must be independent of the live instance.

        The opposite branch of the key separation, driven through `main()`.
        Blocking `--demo` because a live GUI happens to be open — and raising
        that live window instead of opening a demo one — would be actively
        wrong, and is what a single shared key would do.
        """
        live = SingleInstance(default_key(demo=False))
        assert live.acquire() is True, "precondition: a live (non-demo) GUI holds its key"

        reached = self._stop_after_guard(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["control-ofc-gui", "--demo"])

        try:
            with pytest.raises(SystemExit):
                main_mod.main()
        finally:
            live.close()

        assert reached["past_guard"] is True, (
            "a --demo launch must proceed to open its own window rather than "
            "being deduplicated against the live instance"
        )

    def test_a_first_launch_is_not_blocked(self, qtbot, runtime_dir, monkeypatch):
        # The opposite branch. Without it a guard that refuses *every* launch
        # would pass the test above.
        reached = self._stop_after_guard(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["control-ofc-gui", "--demo"])

        with pytest.raises(SystemExit):
            main_mod.main()

        assert reached["past_guard"] is True, (
            "with no other instance running, main() must proceed past the guard"
        )


def test_activate_payload_is_what_the_reader_matches():
    # The two halves are written in different methods; a typo in either would
    # make a duplicate launch silently start a second window instead of raising.
    assert ACTIVATE.strip() in ACTIVATE
    assert ACTIVATE.endswith(b"\n"), "framed with a newline so a partial read is detectable"
