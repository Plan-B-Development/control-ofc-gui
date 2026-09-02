"""Qt-free view-model layer for the Logs page (DEC-210, extended by DEC-314).

Pure builders that turn the ``DiagnosticsService`` event feed (``DiagEvent``) into
frozen ``LogRowVM`` dataclasses the thin ``LogsPage`` renderer consumes, plus the pure
derivations the List + Inspector workflow needs: repeat collapsing, filtering (severity,
source, search, time window), facet counts, histogram bucketing and related-event
correlation.

**Everything derived lives here, and nothing here imports Qt.** That is the point: the
page is a renderer over these functions, so filtering and collapsing are unit-testable
without a ``QApplication`` and cannot drift into a paint handler (brief §10).

**The pipeline runs in feed order** — oldest first — and is reversed once, at the very
end, by :func:`newest_first`. Order matters between the stages:

``build_log_rows`` → ``collapse_repeats`` → ``filter_log_rows`` → ``newest_first``

Collapsing runs **before** filtering, deliberately. "Consecutive" means consecutive in
the real feed; collapsing after a filter would merge events that a hidden row sat
between, which is exactly the "do not collapse unrelated events merely because their
text is similar" failure. It also makes a run's repeat count independent of the filter, so
the number does not change as you type in the search box.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from control_ofc.services.diagnostics_service import DiagEvent

# Level → mockup-faithful abbreviated label (INFO/WARN/ERR) used for the row + pill.
_LEVEL_LABEL: dict[str, str] = {
    "info": "INFO",
    "warning": "WARN",
    "error": "ERR",
}

# Level → StatusPill semantic state. Matches the ``.Pill_*`` state vocabulary in
# ``components/badges.py`` (info→ok/green, warning→warn/amber, error→crit/red).
_LEVEL_STATE: dict[str, str] = {
    "info": "ok",
    "warning": "warn",
    "error": "crit",
}

# The repeat-run marker, written as an escape so the source carries no character
# ruff's confusable check would (rightly) flag as a possible homoglyph typo. Shared
# with the row delegate so the list and the copy text cannot disagree.
REPEAT_MARK = "\u00d7"

# Filter-facet display order. Severity, not alphabetical, so the chips read
# INFO · WARN · ERR left to right at every theme and font size.
LEVEL_ORDER: tuple[str, ...] = ("info", "warning", "error")


def level_state(level: str) -> str:
    """Severity → ``StatusPill`` state, for widgets that colour by raw level.

    The public reader for ``_LEVEL_STATE``. The activity histogram works in raw
    levels (it buckets by ``row.level``) while the row delegate works in states, and
    both must resolve to the same colour — so the mapping is exported rather than
    reached into or, worse, restated.
    """
    return _LEVEL_STATE.get(level, "neutral")


@dataclass(frozen=True)
class LogRowVM:
    """One **visible** row: a single event, or a collapsed run of equivalent ones.

    ``event_id`` is the row's stable identity and is the ``seq`` of the run's
    **first** event, so a run that grows keeps the id the selection is anchored to
    (brief §4). It is never the row index — the index changes on every append under
    newest-first ordering.

    ``fields`` is a tuple of pairs rather than a dict so the dataclass stays genuinely
    immutable and comparable; insertion order is the emitter's order and is preserved
    for display.
    """

    event_id: int
    timestamp: float  # most recent occurrence — what ordering and bucketing use
    first_timestamp: float  # first occurrence of the run (== timestamp when N == 1)
    time_str: str  # "%H:%M:%S" — the row's meta line
    detail_time_str: str  # "%Y-%m-%d %H:%M:%S" — the inspector's precise timestamp
    first_time_str: str  # ditto, for the run's first occurrence
    level: str  # raw "info" | "warning" | "error" — the FILTER key
    level_label: str  # "INFO" | "WARN" | "ERR"
    level_state: str  # "ok" | "warn" | "crit" — StatusPill / severity-edge state
    source: str
    message: str
    fields: tuple[tuple[str, str], ...] = ()
    repeat_count: int = 1
    raw: str = ""  # verbatim source line where one genuinely exists; see format_raw_record

    @property
    def component(self) -> str:
        """The subsystem-specific correlation key, or ``""`` — tier 2 of brief §7.1."""
        return dict(self.fields).get("component", "")


@dataclass(frozen=True)
class HistogramBucket:
    """One column of the activity overview: a half-open ``[start, end)`` time slice."""

    start: float
    end: float
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class RelatedEvents:
    """Related rows plus the *honest* description of what made them related."""

    rows: tuple[LogRowVM, ...]
    label: str
    source: str  # what "Filter to these" should set as the source filter
    component: str  # …and as the search term ("" when correlating on source alone)


# ── Construction ─────────────────────────────────────────────────────


def build_log_row(ev: DiagEvent) -> LogRowVM:
    """Derive a ``LogRowVM`` from a single ``DiagEvent`` (no collapsing)."""
    level = ev.level
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.timestamp))
    return LogRowVM(
        event_id=ev.seq,
        timestamp=ev.timestamp,
        first_timestamp=ev.timestamp,
        time_str=ev.time_str,
        detail_time_str=stamp,
        first_time_str=stamp,
        level=level,
        level_label=_LEVEL_LABEL.get(level, level.upper()),
        level_state=_LEVEL_STATE.get(level, "neutral"),
        source=ev.source,
        message=ev.message,
        fields=tuple(ev.fields.items()),
    )


def build_log_rows(events: Iterable[DiagEvent]) -> list[LogRowVM]:
    """Derive ``LogRowVM``s for a sequence of events (insertion order preserved)."""
    return [build_log_row(ev) for ev in events]


def collapse_repeats(rows: Sequence[LogRowVM]) -> list[LogRowVM]:
    """Collapse **consecutive** rows sharing ``(level, source, message)`` (brief §6).

    The surviving row keeps the run's **first** ``event_id`` and ``first_timestamp``
    and takes the latest ``timestamp``, so:

    - selection stays anchored while the run grows under the user's cursor, and
    - the inspector can report count, first occurrence and most recent occurrence,
      which is precisely the trio brief §6 asks for.

    ``fields`` are taken from the first event of the run. A run is by definition the
    same message from the same source, so the alternatives (merge, or keep the last)
    would either invent a combined record or make the pane flicker as repeats land.
    """
    out: list[LogRowVM] = []
    for row in rows:
        if out:
            prev = out[-1]
            if (prev.level, prev.source, prev.message) == (row.level, row.source, row.message):
                out[-1] = LogRowVM(
                    event_id=prev.event_id,
                    timestamp=row.timestamp,
                    first_timestamp=prev.first_timestamp,
                    time_str=row.time_str,
                    detail_time_str=row.detail_time_str,
                    first_time_str=prev.first_time_str,
                    level=prev.level,
                    level_label=prev.level_label,
                    level_state=prev.level_state,
                    source=prev.source,
                    message=prev.message,
                    fields=prev.fields,
                    repeat_count=prev.repeat_count + 1,
                    raw=row.raw,
                )
                continue
        out.append(row)
    return out


def newest_first(rows: Sequence[LogRowVM]) -> list[LogRowVM]:
    """Reverse feed order for display (brief §4). The single ordering flip."""
    return list(reversed(rows))


# ── Filtering ────────────────────────────────────────────────────────


def filter_log_rows(
    rows: Sequence[LogRowVM],
    *,
    levels: set[str],
    source: str = "",
    search: str = "",
    window: tuple[float, float] | None = None,
) -> list[LogRowVM]:
    """Return rows matching every active filter, in the order given.

    - ``levels`` is a severity **set**: a row survives only if ``row.level`` is a
      member. An **empty** set therefore rejects every row (all toggles off → zero
      rows), the opposite of an "empty means show all" convention. That is
      deliberate and long-standing; the page's "All" chip sets every level rather
      than clearing the set.
    - ``source`` empty ⇒ no source restriction; otherwise an **exact** match.
    - ``search`` is a case-insensitive substring over ``f"{message} {source}"``.
    - ``window`` is a half-open ``[start, end)`` epoch range from the activity
      histogram, or ``None`` for no time restriction. A collapsed run is matched on
      its **most recent** occurrence, which is the timestamp the row displays.
    """
    needle = search.strip().lower()
    result: list[LogRowVM] = []
    for row in rows:
        if row.level not in levels:
            continue
        if source and row.source != source:
            continue
        if window is not None and not (window[0] <= row.timestamp < window[1]):
            continue
        if needle:
            hay = f"{row.message} {row.source}".lower()
            if needle not in hay:
                continue
        result.append(row)
    return result


# ── Facets and aggregates ────────────────────────────────────────────


def level_counts(rows: Sequence[LogRowVM]) -> dict[str, int]:
    """Rows per severity, for the filter chips.

    Counts **rows**, not events: the chips describe what checking them will show you,
    so a run collapsed to one row counts once. The activity histogram beside them
    counts events instead — each number matches the widget it sits in, and a row
    carrying a repeat marker shows the reader why the two differ.

    Call this with the other filters applied but **all** levels enabled, or each
    chip's own count drops to zero the moment it is unchecked.
    """
    counts = dict.fromkeys(LEVEL_ORDER, 0)
    for row in rows:
        counts[row.level] = counts.get(row.level, 0) + 1
    return counts


def source_names(rows: Sequence[LogRowVM]) -> list[str]:
    """Sorted distinct non-empty sources present in ``rows``."""
    return sorted({r.source for r in rows if r.source})


def time_span(rows: Sequence[LogRowVM]) -> tuple[float, float] | None:
    """``(earliest, latest)`` over ``rows``, or ``None`` when there are none."""
    if not rows:
        return None
    stamps = [r.timestamp for r in rows]
    return (min(stamps), max(stamps))


def histogram_buckets(
    rows: Sequence[LogRowVM],
    *,
    span: tuple[float, float] | None,
    bucket_count: int,
) -> list[HistogramBucket]:
    """Bucket ``rows`` into ``bucket_count`` equal slices across ``span`` (brief §3).

    Feed this the **uncollapsed** rows: every event then lands in the slice it
    actually occurred in, so the bars show real volume rather than a run's worth of
    events piled into whichever bucket its most recent occurrence fell in. That is
    also why the bar counts and the chip counts legitimately differ.

    A zero-width span (one event, or several within the same instant) still yields
    buckets — the last one gets everything — rather than dividing by zero.
    """
    if bucket_count <= 0 or span is None:
        return []
    start, end = span
    width = (end - start) / bucket_count
    buckets = [
        HistogramBucket(
            start=start + i * width,
            end=(start + (i + 1) * width) if i < bucket_count - 1 else end,
            counts=dict.fromkeys(LEVEL_ORDER, 0),
        )
        for i in range(bucket_count)
    ]
    for row in rows:
        if width > 0:
            idx = int((row.timestamp - start) / width)
            idx = max(0, min(bucket_count - 1, idx))
        else:
            idx = bucket_count - 1
        buckets[idx].counts[row.level] = buckets[idx].counts.get(row.level, 0) + 1
    return buckets


def index_for_window(
    buckets: Sequence[HistogramBucket], window: tuple[float, float] | None
) -> int | None:
    """Which column currently represents ``window``, or ``None``.

    The selected column has to be **re-derived** rather than remembered, because the
    buckets are recomputed whenever the feed changes and their boundaries move with the
    span. Keeping the integer index across that would leave the highlight on a column
    that no longer covers the range actually being filtered on — the filter stays
    correct while the picture stops matching it, which is the one thing brief §3's
    "a selected time window should be visually apparent" rules out.

    Matched on the window's **start**, which is a bucket boundary by construction.
    """
    if window is None:
        return None
    for i, b in enumerate(buckets):
        # `b.start <= window[0]` guards BOTH branches: without it the final bucket's
        # relaxed upper bound matches any window that starts before the span entirely,
        # and an out-of-range window silently lights the last column.
        if b.start <= window[0] < b.end:
            return i
        if i == len(buckets) - 1 and b.start <= window[0] <= b.end:
            # The last bucket's range is closed at the top: `bucket_window` nudges its
            # end past the newest event so that event is not filtered out of its own
            # column, and that nudged start must still resolve back to this column.
            return i
    return None


def bucket_window(buckets: Sequence[HistogramBucket], index: int) -> tuple[float, float] | None:
    """The ``[start, end)`` filter window for one bucket, or ``None`` if out of range.

    The final bucket's end is nudged forward by an epsilon so the newest event —
    which sits exactly on ``span[1]`` and would fall outside a half-open range — is
    included in its own bucket.
    """
    if not (0 <= index < len(buckets)):
        return None
    b = buckets[index]
    end = b.end + 1e-6 if index == len(buckets) - 1 else b.end
    return (b.start, end)


# ── Correlation ──────────────────────────────────────────────────────


def related_rows(
    rows: Sequence[LogRowVM], selected: LogRowVM | None, *, limit: int = 8
) -> RelatedEvents | None:
    """Rows related to ``selected``, with the correlation actually used (brief §7.1).

    The brief's preferred order is (1) a stable sensor/channel/device identifier,
    (2) a subsystem-specific identifier, (3) source as a fallback. **Tier 1 does not
    exist** — no emitter attaches a sensor or channel id to an event — so this
    resolves tier 2 (``fields["component"]``, populated for alert-derived events) and
    otherwise tier 3. The label says which, because "Related" that silently means
    "same source tag" is a claim the data does not support.
    """
    if selected is None:
        return None
    component = selected.component
    if component:
        matches = [r for r in rows if r.component == component and r.event_id != selected.event_id]
        return RelatedEvents(
            rows=tuple(matches[:limit]),
            label=f"Same component · {component}",
            source=selected.source,
            component=component,
        )
    if not selected.source:
        return None
    matches = [r for r in rows if r.source == selected.source and r.event_id != selected.event_id]
    return RelatedEvents(
        rows=tuple(matches[:limit]),
        label=f"Same source · {selected.source}",
        source=selected.source,
        component="",
    )


# ── Text rendering (copy actions, Raw tab) ───────────────────────────


def format_row_line(row: LogRowVM) -> str:
    """One log line, as the Copy actions emit it."""
    suffix = f" ({REPEAT_MARK}{row.repeat_count})" if row.repeat_count > 1 else ""
    return f"[{row.time_str}] [{row.level_label}] [{row.source}] {row.message}{suffix}"


def format_raw_record(row: LogRowVM) -> str:
    """The event's original representation, for the inspector's Raw tab (brief §7.3).

    When a row carries a genuine verbatim source line it is returned **untouched**.
    A GUI-emitted event has none: it was never a line of text before it was an
    event, so the stored record *is* the original content, and this serialises that
    record in full — every field, nothing truncated, nothing reformatted away.

    What it must never do is assemble a syslog-shaped string out of display fields
    and present it as though it came off the wire. Brief §7.3 forbids exactly that,
    and the honest version is more useful anyway, because it shows the structured
    fields the message text does not contain.
    """
    if row.raw:
        return row.raw
    lines = [
        f"timestamp : {row.detail_time_str}",
        f"level     : {row.level}",
        f"source    : {row.source}",
        f"message   : {row.message}",
    ]
    if row.repeat_count > 1:
        lines.append(f"repeats   : {row.repeat_count} (first at {row.first_time_str})")
    if row.fields:
        width = max(len(k) for k, _ in row.fields)
        lines.append("fields    :")
        lines.extend(f"    {k.ljust(width)} = {v}" for k, v in row.fields)
    return "\n".join(lines)


def format_event_with_context(row: LogRowVM, related: RelatedEvents | None) -> str:
    """The "Copy event + context" payload (brief §8): the record plus its neighbours."""
    parts = [format_raw_record(row)]
    if related is not None and related.rows:
        parts.append(f"\n{related.label}:")
        parts.extend(f"    {format_row_line(r)}" for r in related.rows)
    return "\n".join(parts)
