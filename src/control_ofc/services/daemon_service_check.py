"""Detect whether the system service backing the daemon is installed/enabled.

Used by the dashboard's disconnected state to surface an actionable hint when
a fresh user has installed both packages but never enabled the daemon. Works
on systemd-based Linux distributions; degrades gracefully (returns
``can_check=False``) when ``systemctl`` is not present or returns an
unexpected error.

This module deliberately performs no privileged operations and never executes
``systemctl enable`` itself. The hint shown to the user includes the command
to copy and run with ``sudo``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


SERVICE_NAME = "control-ofc-daemon.service"


@dataclass(frozen=True)
class DaemonServiceState:
    """Snapshot of the system view of ``control-ofc-daemon.service``.

    Attributes:
        socket_exists: True if the daemon's IPC socket is present on disk.
            A bound socket is the strongest signal that the daemon is up;
            absence does not, on its own, mean the service is disabled
            (it could simply be starting).
        service_enabled: True if ``systemctl is-enabled`` reports the unit
            is enabled, static, or alias. False for "disabled", "masked",
            "not-found", or any error path.
        service_active: True if ``systemctl is-active`` reports the unit
            is currently running.
        can_check: False if ``systemctl`` is not available on this system
            (non-systemd distro) or a probe failed for an unexpected reason.
            When False, callers should not display service-state hints —
            the data is unreliable.
    """

    socket_exists: bool
    service_enabled: bool
    service_active: bool
    can_check: bool

    @property
    def installed_but_not_enabled(self) -> bool:
        """True iff we have a reliable read AND the service exists but is
        disabled — the actionable case the dashboard's hint targets."""
        return self.can_check and not self.service_enabled and not self.service_active


def check_daemon_service_state(
    socket_path: str | Path,
    *,
    systemctl_path: str | None = None,
    timeout_secs: float = 1.5,
) -> DaemonServiceState:
    """Probe the local system for the daemon's runtime state.

    Args:
        socket_path: Path to the daemon's IPC socket (typically
            ``/run/control-ofc/control-ofc.sock``).
        systemctl_path: Override the resolved ``systemctl`` executable;
            primarily for tests. ``None`` uses ``shutil.which("systemctl")``.
        timeout_secs: Per-probe subprocess timeout. Two probes run in
            sequence so the worst-case wall time is roughly
            ``2 * timeout_secs`` on a stuck system.

    Returns:
        A :class:`DaemonServiceState` snapshot. This call is safe to make
        on the Qt main thread — both probes complete in single-digit
        milliseconds on a healthy system.
    """
    socket = Path(socket_path)
    socket_exists = socket.exists()

    resolved = systemctl_path if systemctl_path is not None else shutil.which("systemctl")
    if not resolved:
        return DaemonServiceState(
            socket_exists=socket_exists,
            service_enabled=False,
            service_active=False,
            can_check=False,
        )

    enabled = _query(resolved, "is-enabled", timeout_secs)
    active = _query(resolved, "is-active", timeout_secs)

    if enabled is None or active is None:
        return DaemonServiceState(
            socket_exists=socket_exists,
            service_enabled=False,
            service_active=False,
            can_check=False,
        )

    return DaemonServiceState(
        socket_exists=socket_exists,
        service_enabled=enabled in {"enabled", "static", "alias", "enabled-runtime"},
        service_active=active == "active",
        can_check=True,
    )


def _query(systemctl: str, verb: str, timeout_secs: float) -> str | None:
    """Run ``systemctl <verb> <unit>`` and return the trimmed stdout, or
    ``None`` if the probe was unreliable.

    ``is-enabled``/``is-active`` exit non-zero when the unit is
    disabled/inactive — that's a successful probe, not an error. We treat
    timeouts and OS-level execution errors as ``None`` so the caller can
    surface "can_check=False" rather than guessing from absent state.
    """
    try:
        result = subprocess.run(
            [systemctl, verb, SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.debug("systemctl %s probe failed: %s", verb, exc)
        return None
    return result.stdout.strip()


ENABLE_COMMAND = "sudo systemctl enable --now control-ofc-daemon"
