"""Background QThread workers for the Diagnostics page.

Extracted from ``diagnostics_page.py`` (Cluster C maintainability split). Each is
a self-contained ``QObject`` that runs one blocking daemon call off the UI thread
and reports via signals. It takes only a socket path and lazily builds its own
per-thread ``DaemonClient``, so it holds no back-reference to the page and the
heavy ``api.client`` / ``api.errors`` imports stay inside the methods (avoiding
import cycles).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient

log = logging.getLogger(__name__)


class _VerifyWorker(QObject):
    """Runs in a QThread — executes the blocking ~3s verify_hwmon_pwm call off
    the UI thread so the rest of the GUI (polling, splitter, menus) keeps
    reacting during the hardware probe."""

    verify_ok = Signal(object)  # HwmonVerifyResult
    verify_error = Signal(str, str)  # category ('unavailable'|'error'), message

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot(str)
    def do_verify(self, header_id: str) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().verify_hwmon_pwm(header_id)
            self.verify_ok.emit(result)
        except DaemonTimeout:
            # DEC-098: a verify timeout means the daemon was slow — the write
            # may still have landed. Don't say "unavailable", which implies
            # the daemon is gone. The category stays "unavailable" so the
            # main_window's resume-writes path (paired with verify_completed)
            # still fires; only the message is rewritten.
            self.verify_error.emit(
                "unavailable",
                "Verify timed out (>8s). The daemon may have completed the "
                "write — re-check the fan and re-run if needed.",
            )
        except DaemonUnavailable:
            self.verify_error.emit("unavailable", "Daemon unavailable during verify")
        except DaemonError as e:
            self.verify_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Verify worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.verify_error.emit("unavailable", "Connection lost during verify")

    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


class _GpuVerifyWorker(QObject):
    """Runs in a QThread — executes the blocking GPU fan calls off the UI
    thread: the ~6s ``verify_gpu_fan`` probe (DEC-120) and the
    ``reset_gpu_fan`` restore-to-automatic (DEC-147), mirroring
    :class:`_VerifyWorker`."""

    verify_ok = Signal(object)  # GpuVerifyResult
    # category ('unavailable' | 'error' | 'unsupported'), message
    verify_error = Signal(str, str)
    reset_ok = Signal(object)  # GpuFanResetResult
    reset_error = Signal(str, str)  # category ('unavailable' | 'error'), message

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot(str)
    def do_verify(self, gpu_id: str) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().verify_gpu_fan(gpu_id)
            self.verify_ok.emit(result)
        except DaemonTimeout:
            self.verify_error.emit(
                "unavailable",
                "GPU verify timed out (>10s). The daemon may have completed the "
                "test — re-check the fan and re-run if needed.",
            )
        except DaemonUnavailable:
            self.verify_error.emit("unavailable", "Daemon unavailable during GPU verify")
        except DaemonError as e:
            # An old daemon predating the route answers 404 not_found — signal
            # 'unsupported' so the page hides the control for the session.
            if getattr(e, "status", None) == 404 or getattr(e, "code", "") == "not_found":
                self.verify_error.emit(
                    "unsupported",
                    "This daemon version does not support GPU fan verification.",
                )
            else:
                self.verify_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("GPU verify worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.verify_error.emit("unavailable", "Connection lost during GPU verify")

    @Slot(str)
    def do_reset(self, gpu_id: str) -> None:
        """Restore the GPU fan to the firmware's automatic curve (DEC-147).

        Unlike ``do_verify`` there is no ``unsupported`` category: the reset
        route predates every supported daemon, so a 404 here means the GPU id
        itself was not found — a real error, not a version gap.
        """
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().reset_gpu_fan(gpu_id)
            self.reset_ok.emit(result)
        except DaemonTimeout:
            self.reset_error.emit(
                "unavailable",
                "GPU restore timed out. The daemon may still have completed "
                "the reset — check the fan behaviour and re-run if needed.",
            )
        except DaemonUnavailable:
            self.reset_error.emit("unavailable", "Daemon unavailable during GPU restore")
        except DaemonError as e:
            self.reset_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("GPU restore worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.reset_error.emit("unavailable", "Connection lost during GPU restore")

    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


class _HwDiagWorker(QObject):
    """Runs in a QThread — executes the blocking GET /diagnostics/hardware call
    off the UI thread. The daemon performs several sysfs/procfs reads to build
    the report, so a synchronous fetch on a slow/contended daemon would freeze
    the GUI — notably the once-per-session auto-fetch when the Fans tab is first
    shown.

    Also hosts the POST /hwmon/rescan call (DEC-147) — the daemon re-walks
    ``/sys/class/hwmon`` synchronously to rebuild the header list, and the
    rescan's natural follow-up is a diagnostics refetch on this same thread.
    """

    fetch_ok = Signal(object)  # HardwareDiagnosticsResult
    fetch_error = Signal(str, str)  # category ('unavailable'|'error'), message
    rescan_ok = Signal(object)  # list[HwmonHeader]
    rescan_error = Signal(str, str)  # category ('unavailable'|'error'), message

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    @Slot()
    def do_fetch(self) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().hardware_diagnostics()
            self.fetch_ok.emit(result)
        except DaemonTimeout:
            self.fetch_error.emit("unavailable", "Diagnostics fetch timed out")
        except DaemonUnavailable:
            self.fetch_error.emit("unavailable", "Daemon unavailable — cannot fetch diagnostics")
        except DaemonError as e:
            self.fetch_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("HW diagnostics worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.fetch_error.emit("unavailable", "Connection lost during diagnostics fetch")

    @Slot()
    def do_rescan(self) -> None:
        """Re-enumerate hwmon devices via POST /hwmon/rescan (DEC-147)."""
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            headers = self._ensure_client().hwmon_rescan()
            self.rescan_ok.emit(headers)
        except DaemonTimeout:
            self.rescan_error.emit("unavailable", "Hardware rescan timed out")
        except DaemonUnavailable:
            self.rescan_error.emit("unavailable", "Daemon unavailable — cannot rescan hardware")
        except DaemonError as e:
            self.rescan_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Hwmon rescan worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.rescan_error.emit("unavailable", "Connection lost during hardware rescan")

    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None
