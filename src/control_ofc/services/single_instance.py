"""Single-instance guard: a second launch raises the first window.

Why this exists
---------------
The tray (``control-ofc-tray``, daemon repo) opens the GUI on a left click, and
the StatusNotifierItem protocol has **no double-click**: Plasma routes a left
click straight to ``Activate``, so a double click delivers ``Activate`` twice.
Without a guard that starts two complete PySide6 applications against one
daemon. The same defect already existed for the application-menu entry — this
fixes both.

Deduplication belongs here rather than in the tray. The tray cannot know about
a GUI it did not launch (started from a terminal, or from the app menu), so a
tray-side child-PID guard would still spawn a duplicate in exactly the case a
user is most likely to hit: clicking the tray while the window is already open
behind something else.

Transport
---------
``QLocalServer``/``QLocalSocket`` over a socket in ``$XDG_RUNTIME_DIR`` — which
is per-user, mode 0700, and cleared at logout, so the name cannot collide
between users and cannot outlive a session.

Demo and live instances are deliberately kept **separate** (different keys).
Blocking ``--demo`` because a live GUI happens to be open, and silently raising
that live window instead, would be actively wrong.

Wayland caveat, deliberately not papered over
---------------------------------------------
Raising a window the user did not just interact with is subject to the
compositor's focus-stealing prevention. Under KWin's default setting this
usually succeeds; where it does not, the window is marked as demanding
attention in the task bar instead of being raised. Nothing here can force it:
SNI's ``Activate`` carries no xdg-activation token to hand over, so the GUI has
no proof the request came from a user action.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
import time

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from control_ofc.constants import DEFAULT_SOCKET_PATH

log = logging.getLogger(__name__)

#: Message a secondary instance sends to ask the primary to show itself.
ACTIVATE = b"ACTIVATE\n"

#: The primary's reply, written only once it has actually *read* ``ACTIVATE``.
#:
#: This is what makes ``notify_existing`` able to tell a primary that handled the
#: request from one that is merely alive (``T1-j``): ``waitForBytesWritten``
#: returns when the kernel has taken the bytes, which a process stuck outside its
#: event loop also satisfies.
ACK = b"OK\n"

#: How long to wait for a peer. Generous enough for a loaded machine, short
#: enough that a stale socket does not visibly delay startup.
CONNECT_TIMEOUT_MS = 500
WRITE_TIMEOUT_MS = 1000
#: Bounded, so a peer that connects and then says nothing cannot stall the
#: running GUI's event loop. Also bounds the secondary's wait for :data:`ACK`.
READ_TIMEOUT_MS = 1000


def _private_socket_dir() -> str | None:
    """A directory in the system temp dir that only this user can reach.

    Used **only** when there is no ``$XDG_RUNTIME_DIR``. The bare uid-suffixed
    name this replaced sat directly in the shared temp dir, where any local user
    could pre-create a listener: ``acquire()`` treats anything that accepts a
    connection as "our primary", so the victim's GUI connected, reported success
    and exited 0 with only an info line — it never opened at all (``T1-i``).

    Returns ``None`` rather than raising when the directory is not private. It
    **verifies and never repairs**, which matters: an earlier draft called
    ``os.chmod(path, 0o700)`` before checking for a symlink, and ``chmod`` follows
    symlinks — so a squatter could have pointed the name at any directory *we*
    own and had us tighten that instead. Repairing is also unnecessary, because
    ``makedirs(mode=0o700)`` cannot produce anything looser (measured across
    umasks ``000``/``022``/``077``/``777``: never more than ``0700``; a umask of
    ``777`` yields ``0000``, which simply fails later at ``listen()`` and degrades
    to no guard).

    Each check catches a distinct *pre-existing* squat, which is the only way this
    path is reachable at all:

    * ``lstat`` (not ``stat``) plus ``S_ISDIR`` catches a symlink pointing at a
      directory that would otherwise pass — ``makedirs(exist_ok=True)`` follows
      one happily;
    * ``st_uid`` catches a real directory belonging to someone else;
    * the mode bits catch one of ours that is group- or world-accessible.
    """
    uid = os.getuid()
    parent = tempfile.gettempdir()
    # The PARENT decides whether the name can be swapped underneath us. Verifying
    # only the child leaves a rename race: with a world-writable, non-sticky
    # ``TMPDIR`` another user can move our verified 0700 directory aside between
    # the check below and ``QLocalServer.listen()`` and leave their own listener at
    # the same path — reinstating ``T1-i`` exactly. Sticky (``/tmp`` is ``1777``)
    # is what makes that impossible, because only an entry's owner may rename it;
    # a directory others cannot write to is equally safe.
    try:
        pst = os.lstat(parent)
    except OSError as exc:
        log.warning("Could not inspect the temporary directory %s (%s)", parent, exc)
        return None
    if not pst.st_mode & stat.S_ISVTX and pst.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        log.warning(
            "%s is writable by other users and not sticky (mode %o); not putting a socket in it",
            parent,
            pst.st_mode & 0o7777,
        )
        return None
    path = os.path.join(parent, f"control-ofc-{uid}")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
    except OSError as exc:
        log.warning("Could not prepare a private socket directory at %s (%s)", path, exc)
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        log.warning(
            "%s is not private to this user (uid %s, mode %o); not using it",
            path,
            st.st_uid,
            st.st_mode & 0o7777,
        )
        return None
    return path


def default_key(demo: bool = False, socket_path: str | None = None) -> str:
    """Socket name for this user, this mode, and this daemon.

    A full path under ``$XDG_RUNTIME_DIR`` when there is one — Qt then uses it
    verbatim, which keeps the socket per-user without depending on Qt's own
    choice of temporary directory. Otherwise a path inside a
    :func:`_private_socket_dir`, which is the same property reconstructed by
    hand; a bare name in the shared temp dir is ``T1-i``.

    Three things separate one instance from another:

    * **the user**, via ``$XDG_RUNTIME_DIR`` (or the private directory in the
      fallback);
    * **demo vs live**, because refusing ``--demo`` on the grounds that a live
      GUI is open — and raising that live window instead — would be wrong;
    * **which daemon**, because ``--socket`` selects the machine being shown.
      Without this last term, ``control-ofc-gui --socket /run/other.sock``
      against an already-running default-socket GUI would exit and raise that
      window, presenting a *different* daemon's fans and sensors under the
      command the user typed. The socket is hashed rather than embedded so the
      name stays a legal, bounded filename whatever path is passed.

    Demo deliberately ignores the socket: demo mode never opens one.
    """
    if demo:
        name = "control-ofc-gui-demo"
    else:
        target = os.path.abspath(socket_path or DEFAULT_SOCKET_PATH)
        name = "control-ofc-gui-" + hashlib.sha256(target.encode()).hexdigest()[:12]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, f"{name}.sock")
    private = _private_socket_dir()
    if private is not None:
        return os.path.join(private, f"{name}.sock")
    # Nowhere private to put it — in practice only when someone has squatted the
    # name above. Use a directory nobody else can predict, so the guard degrades
    # to "no deduplication" (one extra window, the documented failure) instead of
    # to "a stranger can stop this GUI from starting", which is the defect.
    #
    # This leaks one empty directory per launch on that path. Accepted: it is
    # reachable only under an active squat, and an empty directory in the system
    # temp dir is what systemd-tmpfiles exists to sweep.
    fallback = tempfile.mkdtemp(prefix="control-ofc-")
    log.warning("Using an unshared socket directory %s; deduplication is disabled", fallback)
    return os.path.join(fallback, f"{name}.sock")


class SingleInstance(QObject):
    """Owns the instance socket and reports whether this process is primary."""

    #: Emitted on the primary when another launch asked it to show itself.
    activated = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self._peer: QLocalSocket | None = None

    @property
    def key(self) -> str:
        return self._key

    @property
    def is_primary(self) -> bool:
        return self._server is not None

    def acquire(self) -> bool:
        """Become the primary instance, or detect that one already exists.

        Returns ``True`` when this process should go on to build a window.
        ``False`` means another instance is live and is holding a connection
        ready for :meth:`notify_existing`.
        """
        # Probe first, and understand that this probe is what makes the whole
        # guard work — it is not merely an optimisation.
        #
        # MEASURED (Qt 6.11.1): with `UserAccessOption` set, `listen()` unlinks
        # and rebinds the path **even when a live server is bound to it**. It
        # returns True and the original instance is silently orphaned. Without
        # the option `listen()` correctly fails with "Address in use". So the
        # option that exists to restrict socket permissions also disables Qt's
        # own contention check, and `listen()` cannot be relied on to detect a
        # running instance.
        #
        # Reaching a live peer is therefore the only thing that establishes
        # another instance exists. The socket file's presence never did — a
        # crash leaves one behind.
        #
        # Residual race, accepted: two launches within the probe window can
        # both fail to connect and both listen, and the second displaces the
        # first. The cost is one extra window, once. The cases this guard is
        # for — a tray double-click, and the app-menu entry — deliver their
        # second launch long after the first is listening, so the probe catches
        # them.
        peer = QLocalSocket(self)
        peer.connectToServer(self._key)
        if peer.waitForConnected(CONNECT_TIMEOUT_MS):
            self._peer = peer
            return False
        peer.abort()

        # Nobody answered, so anything at that path is a corpse. Kept as the
        # explicit, documented Qt idiom even though `UserAccessOption` happens
        # to unlink as well on current Qt: depending on that side effect would
        # mean a future Qt release silently turning one crash into a guard that
        # stays disabled until the next logout.
        QLocalServer.removeServer(self._key)

        server = QLocalServer(self)
        # Restricts the socket to this user. It matters in the fallback path,
        # where the socket lands in a shared temp dir rather than in the 0700
        # $XDG_RUNTIME_DIR; without it any local user could ask this GUI to
        # raise itself.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._key):
            # Degrade to no guard rather than refusing to start. A GUI the user
            # cannot open is a worse outcome than a duplicate one.
            log.warning(
                "Could not claim the single-instance socket %s (%s); starting without the guard",
                self._key,
                server.errorString(),
            )
            return True

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def notify_existing(self) -> bool:
        """Ask the already-running instance to show itself.

        Returns ``True`` only once the primary has **acknowledged**. Waiting on
        the write alone was not enough: ``waitForBytesWritten`` returns when the
        kernel has accepted the bytes, which a primary that is alive but not
        running its event loop also satisfies, so this reported success against a
        GUI that would never act on it and ``main``'s fall-through could not fire
        (``T1-j``).
        """
        peer = self._peer
        if peer is None:
            return False
        peer.write(ACTIVATE)
        if not peer.waitForBytesWritten(WRITE_TIMEOUT_MS):
            log.warning("Could not reach the running instance: %s", peer.errorString())
            return False
        peer.flush()
        if not self._wait_for_ack(peer):
            log.warning(
                "The running instance accepted the request but did not acknowledge it: %s",
                peer.errorString(),
            )
            return False
        if ACK.strip() not in bytes(peer.readAll().data()):
            log.warning("The running instance answered with something unexpected")
            return False
        peer.disconnectFromServer()
        self._peer = None
        return True

    @staticmethod
    def _wait_for_ack(peer: QLocalSocket) -> bool:
        """Wait up to :data:`READ_TIMEOUT_MS` for the primary's reply.

        `waitForReadyRead` is the obvious call here and it is the wrong one: it
        blocks inside the socket engine and dispatches **no queued signals**, so
        where the primary happens to live in this same process its
        `newConnection` slot can never run and the wait always times out. That is
        not a hypothetical — it is how `TestMainWiring` drives the real `main()`,
        and the first draft of this fix reddened four tests that way.

        A nested event loop waits cooperatively instead, and against a primary in
        another process — the real case — the two are equivalent, because that
        primary makes progress on its own.

        It waits for the **whole** of :data:`ACK`, re-entering the loop on the
        remaining budget if a ``readyRead`` carries only part of it. Returning on
        the first byte would have been weaker than the acceptance test in
        :meth:`notify_existing`, which needs the full token — and since that test
        calls ``readAll``, a partial byte would have been consumed and then
        rejected with no way to retry, costing a duplicate window.
        """
        deadline = time.monotonic() + READ_TIMEOUT_MS / 1000
        while peer.bytesAvailable() < len(ACK):
            if peer.state() != QLocalSocket.LocalSocketState.ConnectedState:
                # Gone: whatever arrived is all there will ever be.
                return False
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return False
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            peer.readyRead.connect(loop.quit)
            peer.disconnected.connect(loop.quit)
            timer.start(remaining_ms)
            try:
                loop.exec()
            finally:
                timer.stop()
                peer.readyRead.disconnect(loop.quit)
                peer.disconnected.disconnect(loop.quit)
        return True

    def _on_new_connection(self) -> None:
        """Drain pending activation requests.

        Read **synchronously** rather than wiring `readyRead`/`deleteLater`.
        The payload is nine bytes that the peer has already written by the time
        this fires, so there is nothing to gain by deferring — and deferring
        costs a real crash: a `deleteLater` scheduled from a signal lambda
        destroys the `QLocalSocket` from a posted DeferredDelete event, which
        under PySide6 reaches the C++ object after its Python wrapper has gone
        and segfaults in `~QLocalSocket` (the shiboken teardown use-after-free
        family behind DEC-230; reproduced here before this was rewritten).

        The sockets stay parented to the server, so they are freed with it.
        """
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                continue
            try:
                if conn.waitForReadyRead(READ_TIMEOUT_MS):
                    payload = bytes(conn.readAll().data())
                    if ACTIVATE.strip() in payload:
                        # Acknowledge BEFORE emitting, and this order is the
                        # point. Reaching here already proves what the secondary
                        # needs to know — this event loop is running and has read
                        # the request — so acking first lets the secondary exit
                        # promptly instead of waiting out `raise_window`. The
                        # emit below cannot be skipped after it: there is no
                        # early return, and PySide6 does not let a slot exception
                        # escape `emit()`.
                        # `flush`, never `waitForBytesWritten`. This slot runs on
                        # the RUNNING GUI's event loop, where the existing
                        # `waitForReadyRead` above is already a bounded stall that
                        # the constants deliberately cap; adding a second
                        # blocking wait would double the worst case for no gain.
                        # `flush` cannot block, and three bytes into an empty
                        # AF_UNIX buffer always leave completely — and
                        # `disconnectFromServer` flushes anything pending anyway.
                        conn.write(ACK)
                        conn.flush()
                        self.activated.emit()
            finally:
                conn.disconnectFromServer()
                conn.close()

    def close(self) -> None:
        if self._peer is not None:
            self._peer.abort()
            self._peer = None
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._key)
            self._server = None


def raise_window(window) -> None:
    """Bring an existing main window to the front.

    ``show()`` alone is not enough for a minimised window: the minimised bit
    has to be cleared explicitly or the window is restored still minimised.
    """
    window.setWindowState(
        (window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    )
    window.show()
    window.raise_()
    window.activateWindow()
