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
    """Raised when the daemon socket is unreachable."""

    code: str = field(default="daemon_unavailable")
    message: str = field(default="daemon not reachable")
    retryable: bool = field(default=True)
    source: str = field(default="connection")
