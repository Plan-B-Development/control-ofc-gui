"""DEC-314: LogsPage — activity strip, event list, tabbed inspector.

Constructs the page directly on the shared ``DiagnosticsService`` feed and drives its
handlers. Avoids spinning the event loop for the journal thread — the worker is
unit-tested in isolation and the page handler is driven directly.

**What changed from the DEC-210/282 suite.** Half of the old file tested the seam
between two code paths that decided what was on screen (a bulk ``_rebuild_table`` and a
hand-written ``_append_row``), including a fuzz oracle whose whole job was to prove they
had not drifted. DEC-314 deleted that seam: there is one derivation, ``_refresh_view``,
over the pure functions in ``services.logs_view``. Those tests are replaced by tests of
the properties that *survive* — the store is still capped, selection still survives an
eviction, and the model still agrees with the pure derivation — plus the properties the
model/view move exists to give: no per-row widgets, and stable selection by identity.
"""

from __future__ import annotations

import random
import re

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from control_ofc.api.models import ConnectionState
from control_ofc.constants import PAGE_SYSTEM_STATE
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import MAX_EVENTS, DiagnosticsService
from control_ofc.services.logs_view import (
    build_log_rows,
    collapse_repeats,
    filter_log_rows,
    newest_first,
)
from control_ofc.ui.pages.logs_page import (
    _TAB_DIAGNOSTICS,
    _TAB_JOURNAL,
    LogsPage,
    _JournalWorker,
)


def _diag() -> DiagnosticsService:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    return DiagnosticsService(s)


def _page(qtbot, diag=None):
    diag = diag or _diag()
    page = LogsPage(diagnostics_service=diag)
    qtbot.addWidget(page)
    return page, diag


def _messages(page) -> list[str]:
    return [r.message for r in page._model.rows()]


# ── Population + ordering ──────────────────────────────────────────────────


def test_backfill_populates_the_list_from_the_deque(qtbot):
    diag = _diag()
    diag.log_event("info", "gui", "before the page existed")
    page, _ = _page(qtbot, diag)
    assert _messages(page) == ["before the page existed"]


def test_live_append_grows_the_list(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "one")
    diag.log_event("warning", "polling", "two")
    assert page._model.rowCount() == 2


def test_the_list_is_newest_first(qtbot):
    """Brief §4. The inverse of the pre-DEC-314 oldest-first table."""
    page, diag = _page(qtbot)
    for i in range(4):
        diag.log_event("info", "gui", f"event {i}")
    assert _messages(page) == ["event 3", "event 2", "event 1", "event 0"]


def test_probe_previews_cap_block_count(qtbot):
    """The diagnostic probes cap at 2000 blocks so a large daemon snapshot can't
    grow the widget unbounded (audit 2026-07-15 Phase 5). They moved into the
    inspector's Diagnostics tab; the cap moved with them."""
    page, _ = _page(qtbot)
    assert page._daemon_preview.maximumBlockCount() == 2000
    assert page._controller_preview.maximumBlockCount() == 2000
    assert page._gpu_preview.maximumBlockCount() == 2000
    assert page._journal_preview.maximumBlockCount() == 2000


def test_no_permanent_bottom_diagnostics_pane_remains(qtbot):
    """Brief §2: the Diagnostic tools strip must not occupy a permanent section.

    Asserting the absence is only meaningful alongside the presence — the probes
    must have *moved*, not been deleted (brief §15).
    """
    page, _ = _page(qtbot)
    assert page.findChild(QWidget, "Logs_Section_diagnostics") is None
    assert page.findChild(QWidget, "Logs_Text_daemonStatus") is not None
    assert page.findChild(QWidget, "Logs_Tabs_inspector") is not None


# ── Filtering (brief §17.1-§17.5) ──────────────────────────────────────────


def test_toggles_filter_by_level(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "an info")
    diag.log_event("warning", "gui", "a warning")
    diag.log_event("error", "gui", "an error")

    page._toggle_info.setChecked(False)
    assert _messages(page) == ["an error", "a warning"]

    page._toggle_warn.setChecked(False)
    assert _messages(page) == ["an error"]

    page._toggle_error.setChecked(False)
    assert page._model.rowCount() == 0, "every toggle off means no rows, not all rows"


def test_the_all_chip_re_enables_every_severity(qtbot):
    """Brief §5 wants an All control; §15 forbids losing independent toggles. The
    chip is a shortcut over the three, not a fourth mutually-exclusive mode."""
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "an info")
    page._toggle_info.setChecked(False)
    page._toggle_warn.setChecked(False)
    assert page._model.rowCount() == 0

    page._all_btn.click()

    assert all(b.isChecked() for b in page._level_toggles().values())
    assert _messages(page) == ["an info"]


def test_chip_counts_are_faceted_over_the_other_filters(qtbot):
    """A chip's own count must not collapse to zero when it is unchecked, or the
    control tells you there is nothing to re-enable."""
    page, diag = _page(qtbot)
    # Distinct messages on purpose: three identical consecutive events would collapse
    # into one row, and the chips count rows.
    for i in range(3):
        diag.log_event("info", "gui", f"an info {i}")
    diag.log_event("error", "gui", "an error")

    page._toggle_info.setChecked(False)

    assert page._model.rowCount() == 1, "the filter really is applied"
    assert "3" in page._toggle_info.text(), f"INFO chip lost its count: {page._toggle_info.text()}"
    assert "1" in page._toggle_error.text()


def test_search_filters_over_message_and_source(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "alpha")
    diag.log_event("info", "polling", "beta")

    page._search_edit.setText("poll")
    assert _messages(page) == ["beta"], "matches the source, not only the message"

    page._search_edit.setText("ALPHA")
    assert _messages(page) == ["alpha"], "case-insensitive"


def test_selecting_a_source_narrows_the_list(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "from gui")
    diag.log_event("info", "polling", "from polling")

    page._source_combo.setCurrentText("polling")
    assert _messages(page) == ["from polling"]


def test_filters_combine(qtbot):
    """Brief §17.4 — each filter narrows the result of the others, not the raw feed."""
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "keep me")
    diag.log_event("error", "gui", "wrong level")
    diag.log_event("info", "polling", "wrong source")
    diag.log_event("info", "gui", "wrong text")

    page._toggle_error.setChecked(False)
    page._source_combo.setCurrentText("gui")
    page._search_edit.setText("keep")

    assert _messages(page) == ["keep me"]


def test_a_histogram_bucket_applies_a_time_window(qtbot):
    """Brief §3/§17.5. Driven through the page's own handler so the wiring from the
    histogram's signal to the filter is what is under test, not just the pure
    bucketing (which ``test_logs_view`` covers)."""
    page, diag = _page(qtbot)
    for i in range(6):
        diag.log_event("info", "gui", f"event {i}")
    # Stamp distinct times so the events land in different buckets; the service uses
    # time.time() and a test loop can complete inside one clock tick.
    for i, ev in enumerate(diag._events):
        ev.timestamp = 1_000_000.0 + i * 60
    page._all_rows.clear()
    page._all_rows.extend(build_log_rows(diag.events))
    page._refresh_view()

    buckets = page._histogram.buckets()
    assert buckets, "a span with six events must produce buckets"
    populated = [i for i, b in enumerate(buckets) if b.total]
    page._on_bucket_clicked(populated[0])

    assert page._window is not None
    assert page._model.rowCount() < 6, "the window really narrowed the list"
    # `isVisibleTo`, not `isVisible`: the page is never shown here, so `isVisible` is
    # False for every widget on it and the check would be vacuous. (It was written as
    # `isVisible() or True`, which asserted nothing at all.)
    assert page._clear_window_btn.isVisibleTo(page), "the escape hatch must appear with it"

    page._on_bucket_clicked(-1)
    assert page._window is None
    assert page._model.rowCount() == 6, "clearing the window restores every row"
    assert not page._clear_window_btn.isVisibleTo(page)


def test_clicking_the_selected_bucket_again_clears_the_window(qtbot):
    page, diag = _page(qtbot)
    for i in range(4):
        diag.log_event("info", "gui", f"event {i}")
    for i, ev in enumerate(diag._events):
        ev.timestamp = 1_000_000.0 + i * 60
    page._all_rows.clear()
    page._all_rows.extend(build_log_rows(diag.events))
    page._refresh_view()

    idx = next(i for i, b in enumerate(page._histogram.buckets()) if b.total)
    page._on_bucket_clicked(idx)
    assert page._window is not None
    page._histogram.bucket_clicked.emit(-1)
    assert page._window is None


def test_clear_empties_the_list(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "one")
    page._clear_btn.click()
    assert page._model.rowCount() == 0
    assert len(page._all_rows) == 0


# ── Repeat collapsing (brief §17.7-§17.8) ──────────────────────────────────


def test_consecutive_equivalent_events_collapse_into_one_row(qtbot):
    page, diag = _page(qtbot)
    for _ in range(3):
        diag.log_event("warning", "fan", "stall detected")

    assert page._model.rowCount() == 1
    assert page._model.row_at(0).repeat_count == 3


def test_non_consecutive_equivalent_events_do_not_collapse(qtbot):
    """Brief §6: "do not collapse unrelated events merely because their text is
    similar". An intervening event ends the run."""
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall detected")
    diag.log_event("info", "gui", "something else")
    diag.log_event("warning", "fan", "stall detected")

    assert page._model.rowCount() == 3
    assert all(r.repeat_count == 1 for r in page._model.rows())


def test_the_inspector_reports_repeat_count_and_both_timestamps(qtbot):
    """Brief §6 asks for count, first occurrence and most recent occurrence."""
    page, diag = _page(qtbot)
    for _ in range(4):
        diag.log_event("warning", "fan", "stall detected")
    for i, ev in enumerate(diag._events):
        ev.timestamp = 1_000_000.0 + i * 60
    page._all_rows.clear()
    page._all_rows.extend(build_log_rows(diag.events))
    page._refresh_view()

    page._table.selectRow(0)
    assert page._insp_repeat.isVisibleTo(page)
    text = page._insp_repeat.text()
    assert "4" in text
    row = page._selected_row
    assert row.first_time_str in text and row.detail_time_str in text
    assert row.first_time_str != row.detail_time_str, "the run really does span time"


def test_a_single_event_shows_no_repeat_row(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "just the one")
    page._table.selectRow(0)
    assert not page._insp_repeat.isVisibleTo(page)


def test_a_growing_run_keeps_its_identity_and_updates_the_inspector(qtbot):
    """The row a user is inspecting must not be replaced when the run it represents
    grows — the id is the run's *first* event precisely so it stays put."""
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall detected")
    page._table.selectRow(0)
    first_id = page._selected_event_id

    diag.log_event("warning", "fan", "stall detected")

    assert page._selected_event_id == first_id, "selection re-anchored to a different event"
    assert page._selected_row.repeat_count == 2, "but its detail is current"
    assert "2" in page._insp_repeat.text()


# ── Selection (brief §17.6) ────────────────────────────────────────────────


def test_row_select_populates_the_inspector(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("error", "hwmon", "write failed")
    page._table.selectRow(0)

    assert page._insp_message.toPlainText() == "write failed"
    assert page._insp_source.text() == "hwmon"
    assert page._insp_pill.text() == "ERR"
    assert not page._insp_empty.isVisibleTo(page)


def test_nothing_selected_shows_the_empty_inspector_state(qtbot):
    """Brief §12."""
    page, _ = _page(qtbot)
    assert page._insp_empty.isVisibleTo(page)
    assert page._insp_empty.text() == "Select an event to inspect"


def test_selection_survives_live_appends(qtbot):
    """Brief §9: "new incoming events must not replace the selected older event".

    Under newest-first this is the load-bearing case — every append changes the
    selected row's *index*, so anything keyed on position silently drifts.
    """
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "the one I am reading")
    page._table.selectRow(0)
    assert page._selected_row.message == "the one I am reading"

    for i in range(5):
        diag.log_event("info", "polling", f"later {i}")

    assert page._selected_row.message == "the one I am reading"
    assert page._insp_message.toPlainText() == "the one I am reading"
    selected = page._table.selectionModel().selectedRows()
    assert selected and page._model.row_at(selected[0].row()).message == "the one I am reading"


def test_selection_detail_survives_being_filtered_out_of_view(qtbot):
    """DEC-210's rule, preserved: a row leaving the view keeps the pane you are
    reading. The highlight goes; the detail does not."""
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "readable")
    diag.log_event("error", "gui", "an error")
    page._table.selectRow(1)
    assert page._selected_row.message == "readable"

    page._toggle_info.setChecked(False)

    assert page._model.index_of_event(page._selected_event_id) == -1, "it left the view"
    assert page._insp_message.toPlainText() == "readable", "and the pane still shows it"
    assert not page._table.selectionModel().selectedRows(), "with no stale highlight"


def test_selection_survives_the_eviction_of_older_events(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("error", "gui", "the interesting one")
    page._table.selectRow(0)
    kept = page._selected_event_id

    for i in range(MAX_EVENTS + 10):
        diag.log_event("info", "polling", f"flood {i}")

    assert kept not in [r.event_id for r in page._model.rows()], "it aged out of the feed"
    assert page._selected_row.message == "the interesting one", "the pane still holds it"


def test_closing_the_inspector_clears_the_selection(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "one")
    page._table.selectRow(0)
    page._inspector_close.click()

    assert page._selected_row is None
    assert page._insp_empty.isVisibleTo(page)


# ── Inspector: fields, raw, related, actions ───────────────────────────────


def test_structured_fields_render_only_when_present(qtbot):
    """Brief §7.1 forbids empty placeholder rows to match the mock-up."""
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall", fields={"component": "cpu_fan", "rpm": "0"})
    diag.log_event("info", "gui", "no fields here")

    page._table.selectRow(1)  # newest-first: row 1 is the older, field-carrying event
    assert page._selected_row.message == "stall"
    assert page._fields_panel.isVisibleTo(page)
    rendered = {
        page._fields_grid.itemAtPosition(r, 0).widget().text(): (
            page._fields_grid.itemAtPosition(r, 1).widget().text()
        )
        for r in range(page._fields_grid.rowCount())
        if page._fields_grid.itemAtPosition(r, 0) is not None
    }
    assert rendered == {"component": "cpu_fan", "rpm": "0"}

    page._table.selectRow(0)
    assert not page._fields_panel.isVisibleTo(page)
    assert not page._fields_caption.isVisibleTo(page)


def test_the_raw_tab_shows_the_stored_record_not_a_synthesised_line(qtbot):
    """Brief §7.3 forbids reconstructing a "raw" line out of display fields.

    A GUI-emitted event was never a line of text, so the honest raw view is the
    stored record in full — including the structured fields the message does not
    contain, which is what proves it is not just the formatted row re-printed.
    """
    page, diag = _page(qtbot)
    diag.log_event("error", "hwmon", "write failed", fields={"errno": "EACCES"})
    page._table.selectRow(0)

    raw = page._raw_text.toPlainText()
    assert "write failed" in raw
    assert "errno" in raw and "EACCES" in raw
    assert "hwmon" in raw
    assert not raw.startswith("["), "a bracketed syslog-shaped line would be a fabrication"


def test_related_events_correlate_on_component_when_one_exists(qtbot):
    """Brief §7.1 tier 2. The label must say which correlation was used."""
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall", fields={"component": "cpu_fan"})
    diag.log_event("info", "gui", "unrelated")
    diag.log_event("info", "fan", "recovered", fields={"component": "cpu_fan"})

    page._table.selectRow(0)  # the newest: "recovered"
    assert "component" in page._related_label.text().lower()
    assert "cpu_fan" in page._related_label.text()
    assert page._related_list.count() == 1
    assert "stall" in page._related_list.item(0).text()


def test_related_events_fall_back_to_source(qtbot):
    """Tier 3, and it must be *labelled* as such — "Related" that silently means
    "same source tag" claims more than the data supports."""
    page, diag = _page(qtbot)
    diag.log_event("info", "polling", "connected")
    diag.log_event("info", "polling", "active profile: Quiet")

    page._table.selectRow(0)
    assert "source" in page._related_label.text().lower()
    assert page._related_list.count() == 1


def test_filter_to_these_narrows_the_list(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall", fields={"component": "cpu_fan"})
    diag.log_event("info", "gui", "unrelated")
    diag.log_event("info", "fan", "recovered", fields={"component": "cpu_fan"})

    page._table.selectRow(0)
    page._filter_related_btn.click()

    assert page._source_combo.currentText() == "fan"
    assert all(r.source == "fan" for r in page._model.rows())


def test_activating_a_related_event_selects_it(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "polling", "connected")
    diag.log_event("info", "polling", "disconnected")
    page._table.selectRow(0)

    page._related_list.itemClicked.emit(page._related_list.item(0))

    assert page._selected_row.message == "connected"


def test_the_contextual_action_appears_only_for_known_sources(qtbot):
    """Brief §8: actions come from known event types, and are omitted otherwise."""
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "no action for this")
    diag.log_event("error", "hwmon", "rescan failed")

    page._table.selectRow(0)
    assert page._action_btn.isVisibleTo(page)
    assert page._action_btn.text() == "Open Hardware"

    page._table.selectRow(1)
    assert not page._action_btn.isVisibleTo(page), "gui events have no known follow-up"


def test_the_contextual_action_requests_navigation(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("error", "hwmon", "rescan failed")
    page._table.selectRow(0)

    with qtbot.waitSignal(page.navigate_requested, timeout=500) as sig:
        page._action_btn.click()
    assert sig.args == [PAGE_SYSTEM_STATE]


def test_copy_event_with_context_includes_the_related_rows(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "stall", fields={"component": "cpu_fan"})
    diag.log_event("info", "fan", "recovered", fields={"component": "cpu_fan"})
    page._table.selectRow(0)

    page._copy_event_btn.click()

    text = QApplication.clipboard().text()
    assert "recovered" in text
    assert "cpu_fan" in text
    assert "stall" in text, "the related event travels with the copy"


def test_copy_raw_copies_the_record(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "copy me")
    page._table.selectRow(0)
    page._copy_raw_btn.click()
    assert "copy me" in QApplication.clipboard().text()


# ── Empty states (brief §17.12) ────────────────────────────────────────────


def test_the_empty_state_distinguishes_no_events_from_no_matches(qtbot):
    page, diag = _page(qtbot)
    assert page._empty_label.isVisibleTo(page)
    assert page._empty_label.text() == "Waiting for events…"

    diag.log_event("info", "gui", "something")
    assert not page._empty_label.isVisibleTo(page)

    page._search_edit.setText("no such text")
    assert page._empty_label.isVisibleTo(page)
    assert page._empty_label.text() == "No events match this filter"


# ── Lazy probes (brief §17.10-§17.11) ──────────────────────────────────────


def test_diagnostics_and_journal_do_not_load_on_construction(qtbot):
    """Brief §11: opening Logs must not run the probes, and must not shell out to
    journalctl. The Details tab is what a fresh page shows."""
    page, _ = _page(qtbot)
    assert page._tabs.currentIndex() == 0
    assert page._daemon_preview.toPlainText() == ""
    assert page._journal_preview.toPlainText() == ""
    assert page._journal_worker is None, "no journalctl thread was spawned"


def test_opening_the_diagnostics_tab_fills_the_probes_once(qtbot):
    page, _ = _page(qtbot)
    page._tabs.setCurrentIndex(_TAB_DIAGNOSTICS)

    assert page._daemon_preview.toPlainText()
    assert page._controller_preview.toPlainText()
    assert page._gpu_preview.toPlainText()

    # Re-selecting must not silently re-run them; Refresh is how a stale probe is
    # renewed (brief §7.4).
    page._daemon_preview.setPlainText("sentinel")
    page._tabs.setCurrentIndex(0)
    page._tabs.setCurrentIndex(_TAB_DIAGNOSTICS)
    assert page._daemon_preview.toPlainText() == "sentinel"


def test_the_probe_refresh_buttons_refill_from_the_service(qtbot):
    page, _ = _page(qtbot)
    for name in ("Logs_Btn_daemonStatus", "Logs_Btn_controllerStatus", "Logs_Btn_gpuStatus"):
        assert page.findChild(QPushButton, name) is not None
    page.findChild(QPushButton, "Logs_Btn_daemonStatus").click()
    assert page._daemon_preview.toPlainText()


def test_opening_the_journal_tab_triggers_one_fetch(qtbot, monkeypatch):
    page, _ = _page(qtbot)
    calls: list[int] = []
    monkeypatch.setattr(page, "_fetch_journal", lambda: calls.append(1))

    page._tabs.setCurrentIndex(_TAB_JOURNAL)
    assert calls == [1]

    page._tabs.setCurrentIndex(0)
    page._tabs.setCurrentIndex(_TAB_JOURNAL)
    assert calls == [1], "lazy means once, not once per activation"


# ── Journal worker ─────────────────────────────────────────────────────────


def test_journal_worker_emits_fetched(qtbot):
    diag = _diag()
    diag.fetch_journal_entries = lambda: "journal text"  # type: ignore[method-assign]
    worker = _JournalWorker(diag)
    with qtbot.waitSignal(worker.fetched, timeout=1000) as sig:
        worker.do_fetch()
    assert sig.args == ["journal text"]


def test_journal_handler_updates_preview_and_reenables(qtbot):
    page, _ = _page(qtbot)
    page._journal_btn.setEnabled(False)
    page._on_journal_fetched("some journal output")
    assert page._journal_preview.toPlainText() == "some journal output"
    assert page._journal_btn.isEnabled()


def test_fetch_journal_shows_fetching_and_disables(qtbot):
    page, _ = _page(qtbot)
    page._fetch_journal()
    assert page._journal_preview.toPlainText() == "Fetching…"
    assert not page._journal_btn.isEnabled()
    page.cleanup()


def test_cleanup_is_safe_without_worker(qtbot):
    page, _ = _page(qtbot)
    page.cleanup()
    assert page._journal_worker is None and page._journal_thread is None


def test_cleanup_tears_down_active_worker(qtbot):
    page, diag = _page(qtbot)
    diag.fetch_journal_entries = lambda: "x"  # type: ignore[method-assign]
    page._ensure_journal_worker()
    assert page._journal_thread is not None
    page.cleanup()
    assert page._journal_worker is None and page._journal_thread is None


# ── Copy / theme / export ──────────────────────────────────────────────────


def test_copy_visible_rows_to_clipboard(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "one")
    diag.log_event("warning", "polling", "two")
    page._copy_btn.click()

    lines = QApplication.clipboard().text().splitlines()
    assert len(lines) == 2
    assert "[WARN] [polling] two" in lines[0], "newest first, as displayed"
    assert "[INFO] [gui] one" in lines[1]


def test_copy_visible_marks_a_collapsed_run(qtbot):
    page, diag = _page(qtbot)
    for _ in range(3):
        diag.log_event("warning", "fan", "stall")
    page._copy_btn.click()
    assert "3" in QApplication.clipboard().text()


def test_set_theme_repaints_without_reconstructing(qtbot):
    """Brief §17: a theme change must not require page reconstruction."""
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "one")
    before = page._model.rows()
    page.set_theme(None)
    assert page._model.rows() == before
    assert page._model.rowCount() == 1


def test_export_bundle_invokes_service(qtbot, monkeypatch, tmp_path):
    page, diag = _page(qtbot)
    target = tmp_path / "bundle.json"
    monkeypatch.setattr(
        "control_ofc.ui.pages.logs_page.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    seen: list[str] = []
    diag.export_support_bundle = lambda p: seen.append(str(p))  # type: ignore[method-assign]
    page.export_bundle()
    assert seen == [str(target)]


def test_export_bundle_cancel_is_noop(qtbot, monkeypatch):
    page, diag = _page(qtbot)
    monkeypatch.setattr(
        "control_ofc.ui.pages.logs_page.QFileDialog.getSaveFileName", lambda *a, **k: ("", "")
    )
    called: list[int] = []
    diag.export_support_bundle = lambda p: called.append(1)  # type: ignore[method-assign]
    page.export_bundle()
    assert called == []


def test_export_bundle_failure_surfaces_into_feed(qtbot, monkeypatch, tmp_path):
    page, diag = _page(qtbot)
    monkeypatch.setattr(
        "control_ofc.ui.pages.logs_page.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(tmp_path / "b.json"), ""),
    )

    def boom(_p):
        raise OSError("disk full")

    diag.export_support_bundle = boom  # type: ignore[method-assign]
    page.export_bundle()
    assert any("export failed" in e.message for e in diag.events)
    assert any("export failed" in m for m in _messages(page))


# ── Accessibility ──────────────────────────────────────────────────────────
# Two kinds of test are needed and this is the second one. An AST lint proves the
# `name_value_control` call was written; only a runtime sweep proves Qt actually
# announces a name — DEC-269 established that `setAccessibleName` alone is discarded
# on a non-editable QComboBox, and the helper written to encode that rule has already
# reintroduced the bug once for a case its unit tests did not cover.


def _announced_name(page, widget) -> str:
    """What a screen reader would actually read for *widget*.

    Three sources, in Qt's own order of precedence: an explicit accessible name, a
    buddy label (how ``name_value_control`` names a QComboBox, whose own
    ``setAccessibleName`` Qt discards when it is not editable — DEC-269), or, for a
    button, its visible text.

    A button's text only counts if it contains an actual word. A bare glyph announces
    as the glyph, which is DEC-268's point: "✕" is not a name.
    """
    from PySide6.QtWidgets import QAbstractButton

    direct = widget.accessibleName()
    if direct:
        return direct
    for label in page.findChildren(QLabel):
        if label.buddy() is widget:
            return label.text() or label.accessibleName()
    if isinstance(widget, QAbstractButton) and re.search(r"[A-Za-z]{2,}", widget.text()):
        return widget.text()
    return ""


@pytest.mark.parametrize(
    "object_name",
    [
        "Logs_Edit_search",
        "Logs_Combo_source",
        "Logs_Toggle_follow",
        "Logs_Btn_newEvents",
        "Logs_Btn_inspectorClose",
        "Logs_Table_events",
        "Logs_Histogram_activity",
        "Logs_Text_raw",
        "Logs_List_related",
    ],
)
def test_every_new_control_announces_a_name(qtbot, object_name):
    page, _ = _page(qtbot)
    widget = page.findChild(QWidget, object_name)
    assert widget is not None, f"{object_name} is missing from the page"
    name = _announced_name(page, widget)
    assert name.strip(), f"{object_name} announces as an anonymous control"


def test_glyph_only_controls_do_not_rely_on_their_glyph(qtbot):
    """A bare ✕ or a dynamically-filled label is not a name (DEC-268)."""
    page, _ = _page(qtbot)
    close = page.findChild(QWidget, "Logs_Btn_inspectorClose")
    assert close.text() == "✕"
    assert _announced_name(page, close) == "Close inspector"

    jump = page.findChild(QWidget, "Logs_Btn_newEvents")
    assert jump.text() == "", "starts empty — its label is filled only when paused"
    assert _announced_name(page, jump) == "Jump to newest entries"


def test_new_controls_have_unique_object_names(qtbot):
    page, _ = _page(qtbot)
    # Qt names its own internals (qt_scrollarea_viewport and friends) and reuses
    # those names freely; only our own objectNames are ours to keep unique.
    names = [
        w.objectName()
        for w in page.findChildren(QWidget)
        if w.objectName() and not w.objectName().startswith("qt_")
    ]
    duplicated = {n for n in names if names.count(n) > 1}
    assert not duplicated, f"objectName collisions break findChild/click tests: {duplicated}"


def test_no_diagnostics_or_eventlog_objectnames_leak(qtbot):
    """The retired Diagnostics tab's names must not reappear on this page."""
    page, _ = _page(qtbot)
    leaked = [
        w.objectName()
        for w in page.findChildren(QWidget)
        if w.objectName().startswith(("Diagnostics_", "EventLog_"))
    ]
    assert not leaked, f"retired objectNames leaked onto Logs: {leaked}"


def test_no_context_data_field(qtbot):
    """DEC-210 dropped the mock's "Context Data" pane — the data does not exist."""
    page, _ = _page(qtbot)
    assert not [w for w in page.findChildren(QWidget) if "context" in w.objectName().lower()]


# ── Source filter ──────────────────────────────────────────────────────────


def _settings_service_with(**overrides):
    """A settings service seeded with *overrides* and unable to touch the real file.

    A default-constructed service still points ``save()`` at the user's actual
    config, and three tests once wiped the developer's settings that way (see
    AppSettingsService's docstring). Neutralising ``save`` is the guard.
    """
    from control_ofc.services.app_settings_service import AppSettings, AppSettingsService

    svc = AppSettingsService()
    svc._settings = AppSettings(**overrides)
    svc.save = lambda: None  # type: ignore[method-assign]
    return svc


def test_source_dropdown_is_built_from_the_sources_actually_present(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "gui", "a")
    diag.log_event("info", "polling", "b")
    items = [page._source_combo.itemText(i) for i in range(page._source_combo.count())]
    assert items == ["All sources", "gui", "polling"]


def test_a_restored_source_filter_survives_having_no_matching_rows_yet(qtbot):
    """DEC-245: the feed is a capped deque and a restored filter is applied before
    any matching event exists, so the selection must be kept as an option."""
    diag = _diag()
    page = LogsPage(
        diagnostics_service=diag,
        settings_service=_settings_service_with(logs_source_filter="fan"),
    )
    qtbot.addWidget(page)
    assert page._source_combo.currentText() == "fan"
    diag.log_event("info", "gui", "unrelated")
    assert page._source_combo.currentText() == "fan", "a live append must not reset it"


# ── Bounded feed and the model/view guarantees ─────────────────────────────


def test_the_pages_row_store_is_capped_like_the_service_feed(qtbot):
    """The page mirrors the service's capped feed, so it must be capped identically."""
    page, diag = _page(qtbot)

    for i in range(MAX_EVENTS + 50):
        diag.log_event("info", "gui", f"event {i}")

    assert len(diag.events) == MAX_EVENTS, "precondition: the service feed is capped"
    assert len(page._all_rows) == MAX_EVENTS, "the page's copy is capped to the same depth"
    # Not merely the right count — the right *window*: the oldest 50 aged out.
    assert page._all_rows[0].message == "event 50"
    assert page._all_rows[-1].message == f"event {MAX_EVENTS + 49}"
    assert _messages(page)[0] == f"event {MAX_EVENTS + 49}", "newest first"


def test_the_list_creates_no_per_row_widgets(qtbot):
    """The reason for the model/view move (brief §11).

    The old table put a real ``StatusPill`` child widget in every row, so a full feed
    meant 200 live widgets whose only job was to be painted. A delegate paints the
    rows on screen and owns nothing. Asserting **zero growth** rather than "not too
    many" is the point: a per-row widget scheme would grow here by construction.
    """
    page, diag = _page(qtbot)
    for i in range(5):
        diag.log_event("info", "gui", f"event {i}")
    baseline = len(page._table.findChildren(QWidget))

    for i in range(MAX_EVENTS):
        diag.log_event("info", "gui", f"more {i}")

    assert page._model.rowCount() == MAX_EVENTS, "precondition: the list really is full"
    assert len(page._table.findChildren(QWidget)) == baseline, "rows must own no widgets"


def test_the_model_always_agrees_with_the_pure_derivation(qtbot):
    """The oracle, ported. What the page shows must be exactly what the Qt-free
    pipeline computes — the property the old incremental/rebuild fuzz test was
    approximating, now assertable directly because there is only one path.

    Seeded, so a failure is reproducible, and driven across the eviction boundary
    with a mixed sequence of appends, toggles, searches, source changes, time-window
    selections and row selections.
    """
    rng = random.Random(20260902)
    page, diag = _page(qtbot)
    levels = ["info", "warning", "error"]
    sources = ["gui", "polling", "fan", "gpu", "sensor"]
    messages = ["alpha", "beta", "gamma"]

    for step in range(800):
        action = rng.random()
        if action < 0.78:
            # Repeats are drawn from a small message pool so consecutive runs really
            # do occur and the collapse path is exercised.
            diag.log_event(rng.choice(levels), rng.choice(sources), rng.choice(messages))
        elif action < 0.85:
            btn = rng.choice([page._toggle_info, page._toggle_warn, page._toggle_error])
            btn.setChecked(not btn.isChecked())
        elif action < 0.90:
            page._search_edit.setText(rng.choice(["", "alpha", "a", "zzz"]))
        elif action < 0.94:
            page._source_combo.setCurrentIndex(rng.randrange(page._source_combo.count()))
        elif action < 0.97:
            buckets = page._histogram.buckets()
            page._on_bucket_clicked(rng.randrange(len(buckets)) if buckets else -1)
        else:
            if page._model.rowCount():
                page._table.selectRow(rng.randrange(page._model.rowCount()))

        expected = newest_first(
            filter_log_rows(
                collapse_repeats(list(page._all_rows)),
                levels=page._active_levels(),
                source=page._selected_source(),
                search=page._search_edit.text(),
                window=page._window,
            )
        )
        assert page._model.rows() == expected, f"model diverged from the derivation at {step}"
        assert len(page._all_rows) <= MAX_EVENTS, f"overflow at step {step}"

    assert len(page._all_rows) == MAX_EVENTS, "the sequence must cross the eviction boundary"


def test_turning_follow_off_counts_pending_arrivals(qtbot):
    """The geometry-free half: Follow is an explicit state, not only an emergent one.

    Deliberately does not touch the scrollbar — a test whose precondition is "the
    list is tall enough to scroll" is a font/DPI-dependent test, and this project has
    shipped two of those into a red CI (see CLAUDE.md § Hard-won lessons).
    """
    page, diag = _page(qtbot)
    page._follow_btn.setChecked(False)
    assert not page._is_following()

    diag.log_event("info", "gui", "arrived while reading")

    assert page._pending_new == 1
    assert "1 new event" in page._new_events_btn.text()

    diag.log_event("info", "polling", "and another")
    assert "2 new events" in page._new_events_btn.text()

    page._new_events_btn.click()
    assert page._pending_new == 0
    assert page._follow_btn.isChecked()


def test_scrolling_away_from_the_tail_pauses_following(qtbot):
    """The geometry half, with the precondition asserted rather than assumed: if the
    list cannot scroll, the test proves nothing and must say so instead of passing."""
    page, diag = _page(qtbot)
    page.resize(900, 500)
    page.show()
    qtbot.waitExposed(page)
    for i in range(120):
        diag.log_event("info", "gui", f"event {i}")
    QApplication.processEvents()  # let the view lay its rows out before measuring

    bar = page._table.verticalScrollBar()
    assert bar.maximum() > 0, "precondition: the list must actually be scrollable"
    assert page._is_following(), "starts pinned to the tail (the top)"

    bar.setValue(bar.maximum())
    assert not page._is_following(), "scrolling to older events suspends repositioning"

    diag.log_event("info", "gui", "arrived while reading")
    assert page._pending_new >= 1

    page._resume_following()
    assert page._is_following() and page._pending_new == 0


# ── Layout and time-window integrity (remediation, DEC-314 review) ─────────


def test_the_inspector_keeps_a_substantial_share_of_the_width(qtbot):
    """Brief §1.2: the list takes the majority, the inspector a substantial but
    secondary share.

    A **relationship**, measured from the live widgets — never a pixel literal, which
    would not survive a different font stack (CLAUDE.md § Hard-won lessons). It fails
    with the seeding removed: stretch factors alone leave the splitter seeded at the
    pane minimums, and the inspector settles at ~0.18 at every window size.
    """
    page, diag = _page(qtbot)
    for i in range(30):
        diag.log_event("info", "gui", f"event {i}")
    page.resize(1600, 900)
    page.show()
    qtbot.waitExposed(page)
    QApplication.processEvents()

    list_w, inspector_w = page._splitter.sizes()
    total = list_w + inspector_w
    assert total > 0, "precondition: the splitter must actually be laid out"
    assert list_w > inspector_w, "the list must still dominate"
    assert inspector_w / total >= 0.25, (
        f"the inspector is not a substantial share ({inspector_w}/{total})"
    )


def test_the_inspector_cannot_be_crushed_below_its_own_tabs(qtbot):
    """A floor, raised above the panel's computed minimum rather than replacing it —
    `setMinimumWidth` overrides `minimumSizeHint` rather than flooring it (DEC-281),
    so the check is that the result is at least what the tabs ask for."""
    page, _ = _page(qtbot)
    assert page._inspector.minimumWidth() >= page._tabs.sizeHint().width()


def test_the_highlighted_bucket_still_matches_the_window_after_new_events(qtbot):
    """The buckets are recomputed whenever the feed changes and their boundaries move
    with the span, so a remembered index drifts off the range being filtered on.

    Fails with `index_for_window` removed: the highlight stays on the old column while
    the window it represents has moved.
    """
    page, diag = _page(qtbot)
    for i in range(6):
        diag.log_event("info", "gui", f"event {i}")
    for i, ev in enumerate(diag._events):
        ev.timestamp = 1_000_000.0 + i * 60
    page._all_rows.clear()
    page._all_rows.extend(build_log_rows(diag.events))
    page._refresh_view()

    populated = [i for i, b in enumerate(page._histogram.buckets()) if b.total]
    # Deliberately NOT the first populated column. Bucket 0 starts at the span start,
    # which does not move when the span grows — so a stale index would still satisfy
    # the assertion and the test would pass with the fix removed. (It did: the first
    # version of this test failed its own validity check for exactly that reason.)
    assert len(populated) >= 3, "precondition: need an interior column to select"
    idx = populated[len(populated) // 2]
    page._on_bucket_clicked(idx)
    window = page._window
    assert window is not None
    assert idx not in (0, len(page._histogram.buckets()) - 1), "an interior column"

    # A far later event extends the span, so every interior boundary moves.
    page._all_rows[-1] = type(page._all_rows[-1])(
        **{**page._all_rows[-1].__dict__, "timestamp": 1_000_000.0 + 6000}
    )
    page._refresh_view()

    assert page._window == window, "the user's chosen time range must not move"
    selected = page._histogram.selected_index()
    assert selected is not None, "the window is still applied, so a column must be lit"
    assert selected != idx, "precondition: the boundaries really did move"
    lit = page._histogram.buckets()[selected]
    assert lit.start <= window[0] < lit.end, (
        "the lit column no longer covers the window being filtered on"
    )


def test_related_events_use_the_feed_the_refresh_already_collapsed(qtbot):
    """One collapse per refresh, shared — two independently recomputed collapses could
    disagree about what a run is."""
    page, diag = _page(qtbot)
    for _ in range(3):
        diag.log_event("warning", "fan", "stall", fields={"component": "cpu_fan"})
    diag.log_event("info", "fan", "recovered", fields={"component": "cpu_fan"})

    page._table.selectRow(0)
    assert page._collapsed == collapse_repeats(build_log_rows(diag.events))
    assert page._related is not None
    assert [r.repeat_count for r in page._related.rows] == [3]
