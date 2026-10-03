"""The one path every GUI surface takes to write header roles (`ROLE-c`).

Configure AIO (Controls page), the Fan Wizard's liquid-cooling step and the
header-role picker (Hardware card and PWM Test Report) each used to carry their
own loop over ``POST /config/header-role``: its own ordering, error handling and
header re-read, sharing only the confirmation. The ordering is a safety
property (DEC-312), so it lives here once. Qt-free: the confirmation is passed
in as a callback, and each caller keeps its own wording.

Every requested write is sorted into one of three kinds by
``header_role_view.plan_role_change``, and they run in this order:

1. **Assigns** — any role that removes no pump protection. These are the
   writes a caller depends on, so the first that fails stops everything: no
   clear and no pump removal follows it. A failure can therefore only ever
   leave MORE protection in place than intended, never less.
2. **Clears** — dropping an assignment that carries no floor (a chassis,
   radiator or CPU-fan role the user set). A failure is tolerated and reported.
3. **Pump removals** — clearing or downgrading a pump role the USER assigned,
   the only role write that can lower protection. Reached only once every
   assign has landed, and only after ``confirm`` agrees. A failure is
   tolerated: a stale pump role over-protects, never under-protects.

A write that would change nothing daemon-side is skipped, never sent. The
headers are re-read once after any write was attempted — a timeout proves
nothing either way, so the re-read is what shows what actually landed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from ..api.errors import DaemonError
from ..api.models import HeaderRoleResult, HwmonHeader
from .header_role_view import plan_role_change

log = logging.getLogger(__name__)

KIND_ASSIGN = "assign"
KIND_CLEAR = "clear"
KIND_REMOVE_PUMP = "remove_pump"

#: What a role write or the re-read can raise. ``DaemonError`` covers the error
#: envelope, an unreachable socket and a timeout; the rest a malformed answer —
#: the set the poll worker tolerates (``polling.py``).
WRITE_ERRORS = (DaemonError, OSError, KeyError, ValueError, TypeError)


class RoleWriteClient(Protocol):
    def set_header_role(self, header_id: str, role: str | None) -> HeaderRoleResult: ...

    def hwmon_headers(self) -> list[HwmonHeader]: ...


@dataclass(frozen=True)
class RoleWrite:
    header_id: str
    role: str | None
    kind: str


@dataclass(frozen=True)
class LandedWrite:
    write: RoleWrite
    result: HeaderRoleResult


@dataclass(frozen=True)
class FailedWrite:
    write: RoleWrite
    error: Exception


@dataclass(frozen=True)
class RoleWriteOutcome:
    """What happened to each requested write. Callers word it themselves."""

    landed: tuple[LandedWrite, ...] = ()
    #: The assign that stopped the run; nothing after it was sent.
    failed_assign: FailedWrite | None = None
    #: Tolerated failures: clears and pump removals.
    failed: tuple[FailedWrite, ...] = ()
    #: Pump removals not sent because the confirmation was declined.
    declined: tuple[RoleWrite, ...] = ()
    #: Header ids whose write would have changed nothing, so was not sent.
    skipped: tuple[str, ...] = ()
    #: The re-read headers; ``None`` when no write was attempted or it failed.
    headers: list[HwmonHeader] | None = None

    @property
    def attempted(self) -> bool:
        return bool(self.landed or self.failed or self.failed_assign)

    def landed_ids(self) -> frozenset[str]:
        return frozenset(w.write.header_id for w in self.landed)

    def result_for(self, header_id: str) -> HeaderRoleResult | None:
        return next((w.result for w in self.landed if w.write.header_id == header_id), None)


def plan_role_writes(
    headers: Iterable[HwmonHeader], requested: Sequence[tuple[str, str | None]]
) -> tuple[list[RoleWrite], list[str]]:
    """Classify each ``(header_id, role)``; return the writes in run order and
    the skipped no-ops. Run order keeps the caller's order within each kind.

    A header missing from ``headers`` cannot be planned, so a clear on it is
    treated as a pump removal and asks first — the conservative reading — and a
    set is an assign, which the daemon refuses for an unknown id.
    """
    by_id = {h.id: h for h in headers}
    buckets: dict[str, list[RoleWrite]] = {KIND_ASSIGN: [], KIND_CLEAR: [], KIND_REMOVE_PUMP: []}
    skipped: list[str] = []
    for header_id, role in requested:
        header = by_id.get(header_id)
        if header is None:
            kind = KIND_ASSIGN if role is not None else KIND_REMOVE_PUMP
        else:
            plan = plan_role_change(header, role)
            if plan.noop:
                skipped.append(header_id)
                continue
            if plan.removes_user_pump:
                kind = KIND_REMOVE_PUMP
            else:
                kind = KIND_ASSIGN if role is not None else KIND_CLEAR
        buckets[kind].append(RoleWrite(header_id, role, kind))
    ordered = buckets[KIND_ASSIGN] + buckets[KIND_CLEAR] + buckets[KIND_REMOVE_PUMP]
    return ordered, skipped


def apply_role_writes(
    client: RoleWriteClient,
    headers: Iterable[HwmonHeader],
    requested: Sequence[tuple[str, str | None]],
    *,
    confirm: Callable[[list[str]], bool],
    publish_headers: Callable[[list[HwmonHeader]], None] | None = None,
) -> RoleWriteOutcome:
    """Run ``requested`` in the module's order; see the module docstring.

    ``confirm`` receives the header ids of the pump removals and is called at
    most once, only when there are some and every assign landed.
    ``publish_headers`` receives the re-read headers (usually
    ``AppState.set_hwmon_headers``), so every page shows the new roles rather
    than waiting for the ~300 s refresh.
    """
    writes, skipped = plan_role_writes(headers, requested)
    landed: list[LandedWrite] = []
    failed: list[FailedWrite] = []
    declined: list[RoleWrite] = []
    failed_assign: FailedWrite | None = None

    def send(write: RoleWrite) -> FailedWrite | None:
        try:
            landed.append(LandedWrite(write, client.set_header_role(write.header_id, write.role)))
        except WRITE_ERRORS as exc:
            log.warning("Could not set role %s on %s: %s", write.role, write.header_id, exc)
            return FailedWrite(write, exc)
        return None

    for write in (w for w in writes if w.kind == KIND_ASSIGN):
        failed_assign = send(write)
        if failed_assign is not None:
            break
    if failed_assign is None:
        for write in (w for w in writes if w.kind == KIND_CLEAR):
            if (failure := send(write)) is not None:
                failed.append(failure)
        removals = [w for w in writes if w.kind == KIND_REMOVE_PUMP]
        if removals and confirm([w.header_id for w in removals]):
            for write in removals:
                if (failure := send(write)) is not None:
                    failed.append(failure)
        else:
            declined = removals

    outcome = RoleWriteOutcome(
        landed=tuple(landed),
        failed_assign=failed_assign,
        failed=tuple(failed),
        declined=tuple(declined),
        skipped=tuple(skipped),
    )
    if not outcome.attempted:
        return outcome
    try:
        reread = client.hwmon_headers()
    except WRITE_ERRORS as exc:
        log.warning("Header re-fetch after a role change failed: %s", exc)
        return outcome
    if publish_headers is not None:
        publish_headers(reread)
    return replace(outcome, headers=reread)
