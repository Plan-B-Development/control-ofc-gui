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


class _SocketWorker(QObject):
    """Shared base for the Diagnostics QThread workers.

    Holds the per-thread client machinery every worker duplicated: it stores
    only the daemon socket path (no back-reference to the page), lazily builds
    its own ``DaemonClient`` on first use so the heavy ``api.client`` import
    stays out of the module top-level and off the UI thread, and closes that
    client on :meth:`shutdown`. Subclasses declare only their own result
    signals and ``do_*`` slots.
    """

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None

    def _ensure_client(self) -> DaemonClient:
        from control_ofc.api.client import DaemonClient as _DaemonClient

        if self._client is None:
            self._client = _DaemonClient(socket_path=self._socket_path)
        return self._client

    def shutdown(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


class _VerifyWorker(_SocketWorker):
    """Runs in a QThread — executes the blocking ~3s verify_hwmon_pwm call off
    the UI thread so the rest of the GUI (polling, splitter, menus) keeps
    reacting during the hardware probe."""

    verify_ok = Signal(object)  # HwmonVerifyResult
    verify_error = Signal(str, str)  # category ('unavailable'|'error'), message

    @Slot(str)
    def do_verify(self, header_id: str) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().verify_hwmon_pwm(header_id)
            self.verify_ok.emit(result)
        except DaemonTimeout:
            # DEC-098: a verify timeout means the daemon was slow — the write
            # may still have landed. Don't say "unavailable", which implies
            # the daemon is gone. The category stays "unavailable" so the page's
            # error handler shows this softened message verbatim instead of
            # prefixing it as a hard "Verify error"; only the message is rewritten.
            self.verify_error.emit(
                "unavailable",
                "Verify timed out (>8s). The daemon may have completed the "
                "write — re-check the fan and re-run if needed.",
            )
        except DaemonUnavailable:
            self.verify_error.emit("unavailable", "Daemon unavailable during verify")
        except DaemonError as e:
            # DEC-201: a thermal_abort is a safety refusal, not a failure — show
            # the daemon's "let it cool" message verbatim (soft), not as an error.
            if getattr(e, "code", "") == "thermal_abort":
                self.verify_error.emit("unavailable", e.message)
            else:
                self.verify_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Verify worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.verify_error.emit("unavailable", "Connection lost during verify")


class _GpuVerifyWorker(_SocketWorker):
    """Runs in a QThread — executes the blocking GPU fan calls off the UI
    thread: the ~6s ``verify_gpu_fan`` probe (DEC-120) and the
    ``reset_gpu_fan`` restore-to-automatic (DEC-147), mirroring
    :class:`_VerifyWorker`."""

    verify_ok = Signal(object)  # GpuVerifyResult
    # category ('unavailable' | 'error' | 'unsupported'), message
    verify_error = Signal(str, str)
    reset_ok = Signal(object)  # GpuFanResetResult
    reset_error = Signal(str, str)  # category ('unavailable' | 'error'), message

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
            elif getattr(e, "code", "") == "thermal_abort":
                # DEC-201: safety refusal — show the "let it cool" message verbatim.
                self.verify_error.emit("unavailable", e.message)
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


class _HwDiagWorker(_SocketWorker):
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


class _HardwareReadinessWorker(_SocketWorker):
    """Runs in a QThread — executes the blocking GET /inventory/hardware-readiness
    call off the UI thread (the daemon runs a sysfs/procfs scan to build the
    combined readiness + Super-I/O snapshot). DEC-207. Also hosts the opt-in ACTIVE
    Super-I/O port probe (POST), whose result updates only the Super-I/O section
    (dedicated ``probe_*`` signals) so the readiness snapshot is left untouched.
    """

    fetch_ok = Signal(object)  # HardwareReadiness
    # category ('unavailable' | 'error' | 'unsupported'), message
    fetch_error = Signal(str, str)
    probe_ok = Signal(object)  # SuperIoReport (enriched with probe hits)
    probe_error = Signal(str, str)  # category ('unavailable' | 'error'), message

    @Slot()
    def do_fetch(self) -> None:
        """First-open / auto fetch — serves the daemon's cached assessment."""
        self._fetch(force=False)

    @Slot()
    def do_refresh(self) -> None:
        """The page's "Refresh hardware assessment" action — forces a fresh scan."""
        self._fetch(force=True)

    def _fetch(self, *, force: bool) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().hardware_readiness(force=force)
            self.fetch_ok.emit(result)
        except DaemonTimeout:
            self.fetch_error.emit("unavailable", "Hardware readiness fetch timed out")
        except DaemonUnavailable:
            self.fetch_error.emit(
                "unavailable", "Daemon unavailable — cannot fetch hardware readiness"
            )
        except DaemonError as e:
            # A daemon predating /inventory/hardware-readiness answers 404 not_found
            # — signal 'unsupported' so the page shows an unavailable state.
            if getattr(e, "status", None) == 404 or getattr(e, "code", "") == "not_found":
                self.fetch_error.emit(
                    "unsupported",
                    "This daemon version does not provide the combined hardware-readiness report.",
                )
            elif (
                getattr(e, "status", None) == 503
                or getattr(e, "code", "") == "hardware_unavailable"
            ):
                # DEC-231: 503 hardware_unavailable is a transient, retryable soft
                # state (the daemon's hardware scan is momentarily busy / not
                # ready), not a hard error — surface it as 'unavailable' so it
                # reads as "try again", consistent with the timeout/daemon-down
                # arms above, rather than a dead-end error.
                self.fetch_error.emit(
                    "unavailable",
                    "Hardware readiness temporarily unavailable — try again shortly.",
                )
            else:
                self.fetch_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Hardware readiness worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.fetch_error.emit("unavailable", "Connection lost during hardware readiness fetch")

    @Slot()
    def do_probe(self) -> None:
        """DEC-203/207: the opt-in ACTIVE Super-I/O port probe (POST). Returns a
        SuperIoReport enriched with any probe-detected chips — emitted on the
        dedicated ``probe_*`` signals so the page refreshes only its Super-I/O
        section. A daemon-disabled probe returns a normal report, not an error."""
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            result = self._ensure_client().superio_probe()
            self.probe_ok.emit(result)
        except DaemonTimeout:
            self.probe_error.emit("unavailable", "Super-I/O port probe timed out")
        except DaemonUnavailable:
            self.probe_error.emit("unavailable", "Daemon unavailable — cannot run the port probe")
        except DaemonError as e:
            # A 404 on the PROBE endpoint must NOT flip the panel's unsupported flag
            # (that hides the whole page even though the passive GET works). Report
            # it as a transient error so the page survives (CON review, DEC-203).
            if getattr(e, "status", None) == 404 or getattr(e, "code", "") == "not_found":
                self.probe_error.emit(
                    "error", "This daemon version does not support the active port probe."
                )
            else:
                self.probe_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Hardware readiness probe worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.probe_error.emit("unavailable", "Connection lost during Super-I/O port probe")
