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

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from control_ofc.constants import DEFAULT_SOCKET_PATH

log = logging.getLogger(__name__)

#: Message a secondary instance sends to ask the primary to show itself.
ACTIVATE = b"ACTIVATE\n"

#: How long to wait for a peer. Generous enough for a loaded machine, short
#: enough that a stale socket does not visibly delay startup.
CONNECT_TIMEOUT_MS = 500
WRITE_TIMEOUT_MS = 1000
#: Bounded, so a peer that connects and then says nothing cannot stall the
#: running GUI's event loop.
READ_TIMEOUT_MS = 1000


def default_key(demo: bool = False, socket_path: str | None = None) -> str:
    """Socket name for this user, this mode, and this daemon.

    A full path under ``$XDG_RUNTIME_DIR`` when there is one — Qt then uses it
    verbatim, which keeps the socket per-user without depending on Qt's own
    choice of temporary directory. Otherwise a uid-suffixed plain name, which
    Qt places in the temp dir; the suffix is what stops two users colliding
    there.

    Three things separate one instance from another:

    * **the user**, via ``$XDG_RUNTIME_DIR`` (or the uid suffix in the fallback);
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
    return f"{name}-{os.getuid()}"


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
        """Ask the already-running instance to show itself."""
        peer = self._peer
        if peer is None:
            return False
        peer.write(ACTIVATE)
        if not peer.waitForBytesWritten(WRITE_TIMEOUT_MS):
            log.warning("Could not reach the running instance: %s", peer.errorString())
            return False
        peer.flush()
        peer.disconnectFromServer()
        self._peer = None
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
