"""Error types for daemon API communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DaemonError(Exception):
    """Raised when the daemon returns an error envelope.

    ``details`` is the envelope's optional structured payload. The daemon types
    it as ``Option<serde_json::Value>`` but every value it actually emits is a
    JSON **object** — today the sole producer is ``validation_with_details``,
    which sends ``{"field_violations": [...]}`` (DEC-160). The annotation
    records that contract rather than the wider ``Any`` it replaced (OPEN-02
    item 02-d, from the DEC-091 P3 review): ``Any`` told a reader nothing, and
    the one consumer in the GUI —
    :func:`control_ofc.api.models.parse_field_violations` — has always assumed a
    mapping.

    Nothing *enforces* the shape at parse time: :meth:`DaemonClient._handle`
    passes whatever JSON arrived straight through, so a non-conforming daemon
    can still land a string or a list here. That is deliberate — dropping the
    value would discard diagnostic information the envelope meant to carry —
    and ``parse_field_violations`` re-checks with ``isinstance`` before reading
    it, so a wrong shape degrades to "no field violations", never a crash.
    """

    code: str
    message: str
    retryable: bool = False
    source: str = ""
    status: int = 0
    details: dict[str, Any] | None = None
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
