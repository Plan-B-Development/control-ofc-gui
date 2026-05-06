"""Error types for daemon API communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DaemonError(Exception):
    """Raised when the daemon returns an error envelope."""

    code: str
    message: str
    retryable: bool = False
    source: str = ""
    status: int = 0
    details: Any = None
    endpoint: str = ""
    method: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass
class DaemonUnavailable(DaemonError):
    """Raised when the daemon socket is unreachable (connection refused, EOF)."""

    code: str = field(default="daemon_unavailable")
    message: str = field(default="daemon not reachable")
    retryable: bool = field(default=True)
    source: str = field(default="connection")


@dataclass
class DaemonTimeout(DaemonError):
    """Raised when an HTTP call to the daemon exceeds its per-call timeout.

    Distinct from `DaemonUnavailable` so callers can distinguish "the daemon
    isn't there" from "the daemon is slow / overloaded right now". This
    matters for the UI: a verify call that times out client-side may still
    have completed successfully on the daemon, so the user-facing message
    should not say "daemon unavailable".
    """

    code: str = field(default="daemon_timeout")
    message: str = field(default="daemon did not respond within the timeout")
    retryable: bool = field(default=True)
    source: str = field(default="connection")
