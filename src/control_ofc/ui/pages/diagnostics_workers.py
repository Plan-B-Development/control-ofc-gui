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


def _is_soft_safety_refusal(err: object) -> bool:
    """True for a daemon refusal that is protection, not failure (DEC-201/297).

    Two codes mean the same thing to a user: the daemon declined to disturb a fan
    because of thermal state, and it will accept the same request later.

    - ``thermal_abort`` — above the 85 degC verify limit (DEC-201).
    - ``validation_error`` with ``retryable`` — the thermal ladder is actively
      forcing a duty (DEC-297). The 85 degC test cannot see this: the emergency
      latches at a trip point of at least 105 degC and releases only at 80 degC,
      so the band between is hot enough to be forcing and cool enough to pass the
      limit check. Since DEC-308 the trip point is per-machine (derived from the
      CPU's own reported ceiling, floored at 105), which only widens that band.

    Keyed on ``retryable`` rather than on the message text, which is daemon prose
    and not part of the contract. Shared by both verify workers so the two cannot
    drift on what counts as a refusal.
    """
    code = getattr(err, "code", "")
    if code == "thermal_abort":
        return True
    return code == "validation_error" and bool(getattr(err, "retryable", False))


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
            # A safety refusal is not a failure — show the daemon's message
            # verbatim (soft), not as an error. See `_is_soft_safety_refusal`.
            if _is_soft_safety_refusal(e):
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
            elif _is_soft_safety_refusal(e):
                # Safety refusal — show the daemon's message verbatim, not as an
                # error. See `_is_soft_safety_refusal`.
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
    # list[HwmonHeader], adopted OpenFan port ("" when none) — DEC-266
    rescan_ok = Signal(object, str)
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
        """Re-enumerate hwmon devices via POST /hwmon/rescan (DEC-147).

        DEC-265: also asks the daemon to look for an OpenFanController, when it
        advertises that route. "Rescan Hardware" is the action a user reaches for
        when a device is missing, and an OpenFan controller that enumerated after
        the daemon booted is exactly that case — offering a second, separate
        button for it would mean knowing in advance which kind of hardware went
        missing. The OpenFan leg is best-effort: it never fails the hwmon rescan,
        which is the part with a UI contract.
        """
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            client = self._ensure_client()
            # DEC-266: the contracted leg runs FIRST. The OpenFan leg can spend
            # seconds probing serial candidates, so running it first held the
            # user's "Rescanning..." chip open for an opportunistic extra. It
            # also reused the same client afterwards, so a close() racing
            # teardown surfaced as an httpx RuntimeError out of the second call
            # instead of a clean DaemonUnavailable out of the first.
            headers = client.hwmon_rescan()
            adopted_port = self._try_openfan_rescan(client)
            self.rescan_ok.emit(headers, adopted_port)
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
        except Exception as e:
            # DEC-266: backstop. The handlers above are the daemon-error family,
            # but `hwmon_rescan()` can raise outside it — `parse_hwmon_headers`
            # gives `TypeError` on a `"headers": null` body, and httpx raises a
            # bare `RuntimeError` if the client is closed by teardown between
            # entering this slot and issuing the request. Either escaped every
            # handler, so NEITHER signal fired; the page clears
            # `_rescan_in_flight` only in those two slots, so Rescan Hardware
            # stayed dead for the rest of the session with the chip stuck on
            # "Rescanning hardware…". Fixing that on the OpenFan leg alone left
            # the same wedge reachable through this one.
            log.exception("Hwmon rescan worker failed unexpectedly")
            self.rescan_error.emit("error", f"Hardware rescan failed: {e}")

    def _try_openfan_rescan(self, client) -> str:
        """Best-effort OpenFan adoption alongside the hwmon rescan (DEC-265).

        Returns the port a controller was adopted on, or ``""`` for every other
        outcome. The caller reports an adoption to the user: DEC-266 — the result
        line otherwise still said "New fan-control hardware still requires a
        daemon restart", which is precisely what this feature makes untrue, so
        the one case it exists for was the one case the UI mis-advised.

        Capability-gated, so an older daemon is never asked and never 404s. Every
        failure is swallowed deliberately: this is an opportunistic extra, and a
        serial probe that finds nothing is the *normal* outcome on a machine with
        no OpenFan hardware — surfacing that as a rescan failure would report the
        hwmon rescan as broken on every such machine.
        """
        try:
            caps = client.capabilities()
            if not caps.control.openfan_rescan:
                return ""
            result = client.openfan_rescan()
            if result.get("adopted"):
                port = str(result.get("port") or "")
                log.info("OpenFanController adopted via rescan: %s", port)
                return port
        except Exception as e:
            # DEC-266: deliberately broad, matching the precedent in
            # `services/polling.py`. The narrow tuple this replaced did not cover
            # what `parse_capabilities` raises on a malformed body —
            # `AttributeError`/`TypeError` — which escaped BOTH this handler and
            # `do_rescan`'s. Neither `rescan_ok` nor `rescan_error` then fired, so
            # `_rescan_in_flight` never cleared and Rescan Hardware was dead for
            # the rest of the session. The docstring above claimed "every failure
            # is swallowed"; now it is true. Cost of catching broadly here is nil
            # — the leg has no side effect the caller depends on.
            log.debug("OpenFan rescan leg did not adopt a controller: %s", e)
            if isinstance(e, (ConnectionError, OSError)):
                # This leg now runs LAST, so a socket that dies here is no longer
                # seen by `do_rescan`'s connection handler — the hwmon call has
                # already returned. Without this the stale client survives, the
                # user gets a green "Rescan complete", and the very next call
                # (the chained diagnostics refetch) fails instead.
                with contextlib.suppress(Exception):
                    if self._client is not None:
                        self._client.close()
                self._client = None
        return ""


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


class _CharacterizationWorker(_SocketWorker):
    """Runs the PWM/RPM characterisation calls off the UI thread (AIO-MB Phase 3).

    All three are short: the daemon returns ``202`` immediately and runs the
    sweep itself, so nothing here blocks for the length of a run. The polling
    cadence is the dialog's QTimer, matching the GUI's poll-only architecture —
    there is no long-lived request to hold open, and a GUI that dies mid-sweep
    does not strand the header because the restore is the daemon's job.
    """

    run_updated = Signal(object)  # CharacterizationRun | None
    run_error = Signal(str, str)  # category ('unavailable'|'error'), message

    def _guard(self, call, what: str) -> None:
        from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable

        try:
            self.run_updated.emit(call())
        except DaemonTimeout:
            self.run_error.emit(
                "unavailable",
                f"The daemon did not answer the {what} in time. The sweep may "
                "still be running — it restores the header either way.",
            )
        except DaemonUnavailable:
            self.run_error.emit("unavailable", f"Daemon unavailable during {what}")
        except DaemonError as e:
            # Reuses the shared refusal taxonomy: `thermal_abort` and a retryable
            # `validation_error` are protection, not failure, and this endpoint
            # returns exactly those two for the same reasons a verify does.
            if _is_soft_safety_refusal(e):
                self.run_error.emit("unavailable", e.message)
            else:
                self.run_error.emit("error", e.message)
        except (ConnectionError, OSError) as e:
            log.warning("Characterization worker connection error: %s", e)
            with contextlib.suppress(Exception):
                if self._client is not None:
                    self._client.close()
            self._client = None
            self.run_error.emit("unavailable", f"Connection lost during {what}")

    @Slot(str, object, object)
    def do_start(self, header_id: str, points: object, settle: object) -> None:
        self._guard(
            lambda: self._ensure_client().start_characterization(
                header_id,
                points_pct=points if isinstance(points, list) else None,
                settle_seconds=settle if isinstance(settle, int) else None,
            ),
            "characterisation start",
        )

    @Slot()
    def do_poll(self) -> None:
        self._guard(lambda: self._ensure_client().characterization_status(), "status poll")

    @Slot()
    def do_cancel(self) -> None:
        self._guard(lambda: self._ensure_client().cancel_characterization(), "cancellation")
