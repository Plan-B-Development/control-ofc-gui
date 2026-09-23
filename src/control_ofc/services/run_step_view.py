"""Timing words for a running diagnostic's ``current_step`` (`P8-bg`).

The characterisation sweep and control-path discovery both publish a result
only when a hold ends — up to 26 s apart — and since daemon 2.55.0 they also
publish what they are holding right now (:class:`~control_ofc.api.models.RunStep`).
Each dialog words its own phases; this is the one place the elapsed-of-bound
arithmetic lives, so the two cannot disagree about how long a hold has run.

Qt-free, and ``now_ms`` is a parameter so tests are deterministic.
"""

from __future__ import annotations

import time

from ..api.models import RunStep


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seconds(ms: int) -> int:
    """Whole seconds, rounded up, so a hold with 300 ms left never reads ``0s``."""
    return max(0, -(-ms // 1000))


def step_timing(step: RunStep, now_ms: int | None = None) -> str:
    """``"4s of up to 12s"`` — how long this phase has been held, of its bound.

    The daemon's clock and ours are the same host's, so the elapsed time is
    real; it is clamped to the bound because a phase can overrun by I/O, and
    "13s of up to 12s" would read as a stall that is not one. A step with no
    start stamp says only the bound.
    """
    bound = _seconds(step.max_ms)
    if step.started_unix_ms <= 0:
        return f"up to {bound}s"
    now = _now_ms() if now_ms is None else now_ms
    elapsed_ms = min(max(0, now - step.started_unix_ms), step.max_ms)
    return f"{elapsed_ms // 1000}s of up to {bound}s"
