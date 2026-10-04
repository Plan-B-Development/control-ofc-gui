"""What a diagnostic dialog says when a request fails mid-run (`PTA-v`, `U11`).

The characterisation and control-path dialogs both watch a run the daemon owns.
Once the run is known to be live, a failed poll or cancel says nothing about
whether it is still going — the daemon keeps running it and restores the header
itself when it ends. So the dialogs keep polling and keep Cancel, and only the
wording escalates: a "connection problem" on the first failures, "lost contact"
after :data:`LOST_CONTACT_AFTER` in a row. This is the one place both the count
and the words live, so the two dialogs cannot disagree.

Qt-free.
"""

from __future__ import annotations

#: Consecutive failed requests, during a live run, before the dialog stops
#: calling it a hiccup. A slow reply is already several seconds per failure
#: (the client timeout), so three rides out a busy socket without waiting long
#: on a daemon that is really gone.
LOST_CONTACT_AFTER = 3


def live_run_error_text(failures: int, detail: str) -> str:
    """The status line for the ``failures``-th consecutive failure of a live run.

    ``detail`` is the dialog's own rendering of the error (the daemon's words for
    a safety refusal, prefixed otherwise), kept so the cause stays visible.
    """
    if failures >= LOST_CONTACT_AFTER:
        return (
            f"Lost contact with the daemon — {failures} requests in a row failed. "
            "The run carries on daemon-side, and the daemon restores the header "
            "itself when it ends; this window keeps trying. "
            f"Last error: {detail}"
        )
    return f"Connection problem — still watching the run. {detail}"
