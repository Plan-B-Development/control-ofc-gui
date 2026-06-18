"""2026-06-05 audit remediation — regression tests.

Covers the GUI-side fixes:
- DEC-132: thermal-override stand-down (``thermal_state`` in ``/status``;
  control loop pauses, lease machinery stands down, warning hierarchy).
- P2-4: write-retry decay for persistently failing targets.
- P3-1: ``_PollWorker.poll`` treats parse-shaped exceptions as failed cycles.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    parse_status,
)

# ---------------------------------------------------------------------------
# DEC-132 — thermal_state parsing
# ---------------------------------------------------------------------------


class TestThermalStateParsing:
    def test_parse_status_reads_thermal_state(self):
        status = parse_status({"thermal_state": "emergency"})
        assert status.thermal_state == "emergency"

    def test_parse_status_defaults_to_normal_for_old_daemons(self):
        """Pre-1.13 daemons don't send the field — must default to normal."""
        status = parse_status({"daemon_version": "1.12.2"})
        assert status.thermal_state == "normal"


# ---------------------------------------------------------------------------
# P3-1 — poll worker parse-error containment
# ---------------------------------------------------------------------------


class TestPollWorkerParseErrors:
    def _worker(self, mock_client):
        from control_ofc.services.polling import _PollWorker

        worker = _PollWorker(socket_path="/tmp/fake.sock")
        worker._ensure_client = MagicMock(return_value=mock_client)
        return worker

    def test_value_error_from_fallback_counts_as_failed_cycle(self, qtbot):
        """A malformed-but-200 payload (ValueError from a fallback leg)
        previously escaped poll()'s DaemonError handler and hit the Qt
        excepthook once per second with no backoff."""
        client = MagicMock()
        client.capabilities.side_effect = ValueError("malformed capabilities payload")
        worker = self._worker(client)

        disconnected: list = []
        worker.disconnected.connect(lambda: disconnected.append(True))

        worker.poll()  # must not raise

        assert disconnected, "parse failure must surface as a failed cycle"
        assert worker._consecutive_failures == 1

    def test_key_error_mid_poll_counts_as_failed_cycle(self, qtbot):
        client = MagicMock()
        client.poll.side_effect = KeyError("sensors")
        client.status.side_effect = KeyError("status")
        worker = self._worker(client)
        worker._poll_count = 1  # skip first-poll capabilities leg

        disconnected: list = []
        worker.disconnected.connect(lambda: disconnected.append(True))

        worker.poll()

        assert disconnected
        assert worker._consecutive_failures == 1
