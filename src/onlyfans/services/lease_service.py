"""Hwmon lease lifecycle — acquire, renew, release.

The daemon requires an exclusive lease for hwmon PWM writes (60s TTL).
This service manages the lease independently from the control loop.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from onlyfans.api.client import DaemonClient
from onlyfans.api.errors import DaemonError
from onlyfans.constants import LEASE_RENEW_INTERVAL_S

log = logging.getLogger(__name__)


class LeaseService(QObject):
    """Manages hwmon lease acquire / renew / release lifecycle."""

    lease_acquired = Signal(str)  # lease_id
    lease_lost = Signal(str)  # reason
    lease_renewed = Signal(str)  # lease_id

    # Maximum retries before declaring lease truly lost (P0-G2).
    _MAX_RENEW_RETRIES = 3

    def __init__(self, client: DaemonClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._lease_id: str | None = None
        self._renew_retry_count = 0

        self._renew_timer = QTimer(self)
        self._renew_timer.setInterval(LEASE_RENEW_INTERVAL_S * 1000)
        self._renew_timer.timeout.connect(self._renew)

    @property
    def lease_id(self) -> str | None:
        return self._lease_id

    @property
    def is_held(self) -> bool:
        return self._lease_id is not None

    def acquire(self) -> bool:
        """Take the hwmon lease. Returns True on success."""
        if self._lease_id:
            return True
        try:
            result = self._client.hwmon_lease_take("gui")
            self._lease_id = result.lease_id
            self._renew_timer.start()
            log.info("Lease acquired: %s (ttl=%ds)", result.lease_id, result.ttl_seconds)
            self.lease_acquired.emit(result.lease_id)
            return True
        except DaemonError as e:
            reason = f"{e.code}: {e.message}"
            log.warning("Lease acquire failed: %s", reason)
            self.lease_lost.emit(reason)
            return False

    def release(self) -> None:
        """Release the hwmon lease if held."""
        if not self._lease_id:
            return
        self._renew_timer.stop()
        lease_id = self._lease_id
        self._lease_id = None
        try:
            self._client.hwmon_lease_release(lease_id)
            log.info("Lease released: %s", lease_id)
        except DaemonError as e:
            log.warning("Lease release failed: %s", e.message)

    def _renew(self) -> None:
        """Renew the lease periodically with retry on failure (P0-G2).

        Thread safety: this method, acquire(), release(), and is_held all run
        on the Qt main thread (QTimer + direct calls from UI/control loop).
        No lock is needed because Qt's event loop is single-threaded.
        If multi-threaded lease access is ever added, a lock must be introduced.
        """
        if not self._lease_id:
            self._renew_timer.stop()
            return
        try:
            result = self._client.hwmon_lease_renew(self._lease_id)
            self._lease_id = result.lease_id
            self._renew_retry_count = 0
            log.debug("Lease renewed: %s (ttl=%ds)", result.lease_id, result.ttl_seconds)
            self.lease_renewed.emit(result.lease_id)
        except DaemonError as e:
            self._renew_retry_count += 1
            if self._renew_retry_count <= self._MAX_RENEW_RETRIES:
                backoff_ms = self._renew_retry_count * 5000
                log.warning(
                    "Lease renewal failed (retry %d/%d in %dms): %s",
                    self._renew_retry_count,
                    self._MAX_RENEW_RETRIES,
                    backoff_ms,
                    e.message,
                )
                QTimer.singleShot(backoff_ms, self._renew)
                return
            # All retries exhausted — lease truly lost
            log.error(
                "Lease renewal failed after %d retries: %s",
                self._MAX_RENEW_RETRIES,
                e.message,
            )
            self._lease_id = None
            self._renew_timer.stop()
            self._renew_retry_count = 0
            self.lease_lost.emit(
                f"renewal failed after {self._MAX_RENEW_RETRIES} retries: {e.message}"
            )

    def shutdown(self) -> None:
        """Release lease and stop timers."""
        self.release()
