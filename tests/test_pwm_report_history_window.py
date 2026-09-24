"""PWM Test Report window — the Reports page, comparison and exports
(DEC-404 Stage 5).

Driven the way a user drives it: buttons by ``.click()``, menu entries by
``QAction.trigger()``, file dialogs answered by monkeypatch. Every window a
fixture builds is bound and used (DASH-h).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QPushButton, QTableWidget

from control_ofc.services.pwm_report import store
from control_ofc.services.pwm_report.view import build_report_view
from control_ofc.ui.pages.pwm_report_controller import PwmReportController
from control_ofc.ui.widgets.pwm_report_history import SOURCE_FILE, PwmReportHistoryPage
from control_ofc.ui.widgets.pwm_report_window import (
    PAGE_COMPARE,
    PAGE_HISTORY,
    PAGE_REPORT,
    PwmReportWindow,
)
from tests.pwm_report_fixtures import complete_doc
from tests.test_pwm_report_window import _state


def _needs_reapply(doc: dict) -> dict:
    doc["actions"] = {"reapply_profile": True}
    return doc


@pytest.fixture()
def hist(qtbot, tmp_path, settings_service, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    folder = tmp_path / "reports"
    store.save_report(complete_doc(report_id="older", started_at="2026-09-01T00:00:00Z"), folder)
    store.save_report(
        _needs_reapply(complete_doc(report_id="newer", started_at="2026-09-20T00:00:00Z")),
        folder,
    )
    state = _state()
    controller = PwmReportController(state, "/tmp/fake.sock", directory=folder)
    window = PwmReportWindow(controller, state, settings_service)
    qtbot.addWidget(window)
    yield window, folder, tmp_path
    controller.shutdown()


def _btn(window, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert button is not None, name
    return button


def _action(window, name: str) -> QAction:
    action = window.findChild(QAction, name)
    assert action is not None, name
    return action


def _label(window, name: str) -> QLabel:
    label = window.findChild(QLabel, name)
    assert label is not None, name
    return label


def _save_to(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))


def _answer(monkeypatch, answer) -> list[str]:
    asked: list[str] = []

    def question(_parent, _title, text, *a, **k):
        asked.append(text)
        return answer

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def _messages(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    for kind in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            kind,
            lambda _p, _t, text, *a, _kind=kind, **k: seen.append((_kind, text)),
        )
    return seen


# ── The Reports page ─────────────────────────────────────────────────────────


def test_the_window_opens_on_the_reports_page_newest_first(hist):
    window, *_ = hist
    assert window.current_page() == PAGE_HISTORY
    rows = window.history_page.rows()
    assert [r.entry.report_id for r in rows] == ["newer", "older"]
    assert _btn(window, "PwmReport_Btn_new").isVisibleTo(window)
    assert not _btn(window, "PwmReport_Btn_history").isVisibleTo(window)
    assert not _btn(window, "PwmReport_Btn_export").isVisibleTo(window)


def test_open_and_compare_follow_the_selection(hist):
    window, *_ = hist
    page = window.history_page
    open_btn, compare_btn = page.open_btn, page.compare_btn
    assert not open_btn.isEnabled() and not compare_btn.isEnabled()
    page.select_rows([0])
    assert open_btn.isEnabled() and not compare_btn.isEnabled()
    page.select_rows([0, 1])
    assert compare_btn.isEnabled() and not open_btn.isEnabled()


def test_a_reopened_report_never_offers_reapply(hist):
    """S5-7: the button acts on the machine NOW; a reopened report is evidence
    from THEN. Presence first — this report did ask for a re-apply."""
    window, folder, _ = hist
    page = window.history_page
    page.select_rows([0])
    doc, _ = store.load_report(store.report_path("newer", folder))
    assert build_report_view(doc).can_reapply, "precondition: the report asked for it"
    page.open_btn.click()
    assert window.current_page() == PAGE_REPORT
    assert window.findChild(QPushButton, "PwmReport_Btn_reapply") is None
    assert _label(window, "PwmReport_Label_reapplyReopened").isVisibleTo(window)
    assert "opened from" in _label(window, "PwmReport_Label_savedPath").text()
    # And "All reports" takes the user back.
    _btn(window, "PwmReport_Btn_history").click()
    assert window.current_page() == PAGE_HISTORY


def test_compare_two_reports_and_export_the_comparison(hist, monkeypatch, tmp_path):
    window, *_ = hist
    page = window.history_page
    page.select_rows([0, 1])
    page.compare_btn.click()
    assert window.current_page() == PAGE_COMPARE
    cmp = window.compare_page.comparison
    assert cmp is not None and (cmp.earlier.report_id, cmp.later.report_id) == ("older", "newer")
    table = window.findChild(QTableWidget, "PwmReport_Table_compare_response")
    assert table is not None and table.rowCount() == len(cmp.in_category("response")) > 0
    assert _btn(window, "PwmReport_Btn_exportCompare").isVisibleTo(window)
    for fmt, marker in (("markdown", "# PWM Test Report comparison"), ("html", "<!DOCTYPE html>")):
        target = tmp_path / f"cmp-{fmt}"
        _save_to(monkeypatch, target)
        _action(window, f"PwmReport_Action_exportCompare_{fmt}").trigger()
        written = target.with_name(target.name + (".md" if fmt == "markdown" else ".html"))
        assert written.read_text().startswith(marker)


def test_delete_asks_first_and_removes_only_on_yes(hist, monkeypatch):
    window, folder, _ = hist
    page = window.history_page
    path = store.report_path("older", folder)
    page.select_rows([1])
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    page.delete_btn.click()
    assert asked and path.exists()
    page.select_rows([1])
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    page.delete_btn.click()
    assert not path.exists()
    assert [r.entry.report_id for r in page.rows()] == ["newer"]


def test_a_file_from_elsewhere_is_shown_but_never_saved_here(hist, monkeypatch):
    window, folder, tmp = hist
    other = tmp / "elsewhere"
    path = store.save_report(complete_doc(report_id="foreign"), other)
    before = sorted(p.name for p in folder.iterdir())
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    page = window.history_page
    page.import_btn.click()
    assert window.current_page() == PAGE_REPORT
    assert "not saved on this computer" in _label(window, "PwmReport_Label_savedPath").text()
    assert sorted(p.name for p in folder.iterdir()) == before
    window.show_history()
    rows = page.rows()
    assert rows[0].imported and rows[0].entry.report_id == "foreign"
    table = window.findChild(QTableWidget, "PwmReport_Table_history")
    assert table.item(0, 4).text() == SOURCE_FILE
    page.select_rows([0])
    assert page.open_btn.isEnabled()
    assert not page.delete_btn.isEnabled()


def test_a_malformed_file_is_refused_with_a_message(hist, monkeypatch):
    window, _, tmp = hist
    bad = tmp / "bad.json"
    bad.write_text('{"kind": "not a report"}')
    seen = _messages(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    window.history_page.import_btn.click()
    assert seen and seen[0][0] == "warning"
    assert window.current_page() == PAGE_HISTORY
    assert len(window.history_page.rows()) == 2


def test_the_report_being_written_cannot_be_opened_or_deleted(qtbot, tmp_path):
    folder = tmp_path / "reports"
    store.save_report(complete_doc(report_id="live"), folder)
    page = PwmReportHistoryPage(directory=folder, running_report_id=lambda: "live")
    qtbot.addWidget(page)
    page.select_rows([0])
    assert page.rows()[0].entry.report_id == "live"
    for button in (page.open_btn, page.export_btn, page.delete_btn):
        assert not button.isEnabled()
    table = page.findChild(QTableWidget, "PwmReport_Table_history")
    assert table.item(0, 3).text() == "In progress (running now)"


# ── The Export menu ──────────────────────────────────────────────────────────


def _open_first(window) -> None:
    window.history_page.select_rows([0])
    window.history_page.open_btn.click()
    assert window.current_page() == PAGE_REPORT


@pytest.mark.parametrize(
    ("fmt", "suffix", "marker"),
    [
        ("json", ".json", '{"kind":"control-ofc.pwm-test-report"'),
        ("markdown", ".md", "# PWM Test Report"),
        ("html", ".html", "<!DOCTYPE html>"),
    ],
)
def test_each_single_file_format_writes_its_file(hist, monkeypatch, fmt, suffix, marker):
    window, _, tmp = hist
    _open_first(window)
    target = tmp / f"out{suffix}"
    _save_to(monkeypatch, target)
    _action(window, f"PwmReport_Action_export_{fmt}").trigger()
    assert target.read_text().startswith(marker)


def test_csv_writes_one_file_per_table_and_says_which(hist, monkeypatch):
    window, _, tmp = hist
    _open_first(window)
    out = tmp / "csv"
    out.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(out))
    seen = _messages(monkeypatch)
    _action(window, "PwmReport_Action_export_csv").trigger()
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "pwm-report-newer-probe.csv",
        "pwm-report-newer-sweep.csv",
        "pwm-report-newer-trace.csv",
    ]
    kind, text = seen[-1]
    assert kind == "information" and all(n in text for n in names)
    # A second export into the same folder asks before replacing anything.
    for p in out.iterdir():
        p.write_text("keep")
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    _action(window, "PwmReport_Action_export_csv").trigger()
    assert asked and all(p.read_text() == "keep" for p in out.iterdir())


def test_export_from_the_reports_page_uses_the_selected_report(hist, monkeypatch):
    window, _, tmp = hist
    window.history_page.select_rows([1])
    target = tmp / "picked.md"
    _save_to(monkeypatch, target)
    _action(window, "PwmReport_Action_historyExport_markdown").trigger()
    assert "older" in target.read_text().splitlines()[0]


@pytest.fixture()
def restore_app_theme(qtbot):
    """Save/restore everything ``apply_theme`` mutates (mirrors the fixture in
    test_theme_typography_r30.py, per DEC-363: mirrored per file so it cannot leak)."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from control_ofc.ui import theme as theme_mod

    app = QApplication.instance()
    saved = (QPalette(app.palette()), app.styleSheet(), app.font(), theme_mod._active_theme)
    try:
        yield app
    finally:
        app.setPalette(saved[0])
        app.setStyleSheet(saved[1])
        app.setFont(saved[2])
        theme_mod._active_theme = saved[3]


def test_comparison_tables_are_as_tall_as_their_rows_when_shown(hist, qtbot, restore_app_theme):
    """Realised geometry, themed (DEC-363): a short table shows every row with no
    scrollbar, and a long one stops at the cap and scrolls."""
    from control_ofc.ui.theme import apply_theme, default_dark_theme
    from control_ofc.ui.widgets.pwm_report_compare import MAX_TABLE_HEIGHT

    apply_theme(default_dark_theme())
    window, *_ = hist
    window.resize(980, 760)
    window.show()
    page = window.history_page
    page.select_rows([0, 1])
    page.compare_btn.click()
    qtbot.waitExposed(window)
    tables = window.compare_page.findChildren(QTableWidget)
    assert tables
    capped = 0
    for t in tables:
        need = (
            t.horizontalHeader().height()
            + sum(t.rowHeight(r) for r in range(t.rowCount()))
            + 2 * t.frameWidth()
        )
        if need <= MAX_TABLE_HEIGHT:
            assert t.height() >= need, t.objectName()
            assert not t.verticalScrollBar().isVisible(), t.objectName()
        else:
            capped += 1
            assert t.height() == MAX_TABLE_HEIGHT
    assert capped < len(tables), "precondition: at least one table fits"


def test_a_file_whose_entries_are_not_objects_is_refused_at_load(hist, monkeypatch):
    """A file from elsewhere is untrusted past its top level (DEC-409): the schema
    check refuses non-object entries before any renderer calls `.get` on them."""
    import json

    window, _, tmp = hist
    doc = complete_doc(report_id="crafted")
    doc["steps"].append("not an object")
    bad = tmp / "crafted.json"
    bad.write_text(json.dumps(doc))
    seen = _messages(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    window.history_page.import_btn.click()
    assert seen and "not an object" in seen[0][1]
    assert window.current_page() == PAGE_HISTORY


def test_a_render_failure_returns_to_the_list_with_a_message(hist, monkeypatch):
    window, *_ = hist
    seen = _messages(monkeypatch)
    doc = complete_doc(report_id="odd")
    monkeypatch.setattr(
        "control_ofc.ui.widgets.pwm_report_window.build_report_view",
        lambda _doc: (_ for _ in ()).throw(TypeError("boom")),
    )
    window._open_report(doc, Path("/x/odd.json"), True)
    assert seen and seen[0][0] == "warning" and "TypeError" in seen[0][1]
    assert window.current_page() == PAGE_HISTORY


# ── Review remediation (DEC-409) ─────────────────────────────────────────────


def test_a_deeply_nested_file_in_the_folder_does_not_stop_the_window_opening(
    qtbot, tmp_path, settings_service, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    folder = tmp_path / "reports"
    store.save_report(complete_doc(report_id="fine"), folder)
    (folder / "pwm-report-deep.json").write_text("[" * 100_000 + "]" * 100_000)
    state = _state()
    controller = PwmReportController(state, "/tmp/fake.sock", directory=folder)
    window = PwmReportWindow(controller, state, settings_service)
    qtbot.addWidget(window)
    rows = window.history_page.rows()
    assert {r.entry.report_id for r in rows if not r.entry.error} == {"fine"}
    bad = [i for i, r in enumerate(rows) if r.entry.error]
    window.history_page.select_rows(bad)
    assert window.history_page.delete_btn.isEnabled(), "the bad file can be deleted in-app"
    controller.shutdown()


def test_importing_a_deeply_nested_file_shows_a_message(hist, monkeypatch):
    window, _, tmp = hist
    deep = tmp / "deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000)
    seen = _messages(monkeypatch)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(deep), ""))
    window.history_page.import_btn.click()
    assert seen and "nested too deeply" in seen[0][1]


def test_a_name_typed_without_its_suffix_asks_before_replacing(hist, monkeypatch):
    window, _, tmp = hist
    _open_first(window)
    existing = tmp / "notes.md"
    existing.write_text("mine")
    _save_to(monkeypatch, tmp / "notes")  # the dialog returns the name as typed
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    _action(window, "PwmReport_Action_export_markdown").trigger()
    assert asked and existing.read_text() == "mine"
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    _action(window, "PwmReport_Action_export_markdown").trigger()
    assert existing.read_text().startswith("# PWM Test Report")


def test_an_export_that_cannot_be_built_says_so(hist, monkeypatch):
    window, _, tmp = hist
    _open_first(window)
    seen = _messages(monkeypatch)
    _save_to(monkeypatch, tmp / "out.html")
    monkeypatch.setattr(
        "control_ofc.ui.widgets.pwm_report_export.report_text",
        lambda *_a: (_ for _ in ()).throw(TypeError("crafted")),
    )
    _action(window, "PwmReport_Action_export_html").trigger()
    assert seen and seen[-1][0] == "warning" and "crafted" in seen[-1][1]
    assert not (tmp / "out.html").exists()


# ── `PTA-g`: the empty landing page keeps its text together ──────────────────


def test_the_empty_reports_page_keeps_its_text_at_the_top(qtbot, tmp_path, restore_app_theme):
    """Realised geometry, themed, at the window's own size. With no rows the
    table (the page's only stretch) is hidden; the surplus height must go to
    the empty label, whose text sits at its top — never be shared out between
    the header and the description, which then drift apart down the window."""
    from PySide6.QtCore import Qt

    from control_ofc.ui.components.cards import SectionHeader
    from control_ofc.ui.theme import apply_theme, default_dark_theme

    apply_theme(default_dark_theme())
    page = PwmReportHistoryPage(directory=tmp_path / "empty")
    qtbot.addWidget(page)
    page.resize(1200, 860)
    page.show()
    qtbot.waitExposed(page)
    empty = page.findChild(QLabel, "PwmReport_Label_historyEmpty")
    intro = page.findChild(QLabel, "PwmReport_Label_historyIntro")
    header = page.findChild(SectionHeader, "PwmReport_Header_history")
    assert page.rows() == [] and empty.isVisible(), "precondition: the empty state"
    natural = sum(w.sizeHint().height() for w in (header, intro, empty))
    assert page.height() > natural + 200, "precondition: there is surplus to hand out"

    def natural_height(w) -> int:
        return w.heightForWidth(w.width()) if w.hasHeightForWidth() else w.sizeHint().height()

    for w in (header, intro):
        assert w.height() <= natural_height(w), f"{w.objectName()} took the surplus"
    assert empty.height() > natural_height(empty), "the empty label holds the surplus"
    assert empty.alignment() & Qt.AlignmentFlag.AlignTop, "and draws its text at the top"
    # Adjacent, not spread: the gap below the description is the layout's own spacing.
    assert empty.geometry().top() - intro.geometry().bottom() - 1 == page.layout().spacing()


# ── `PTR-x`: a row the head check passed is corrected when opening fails ─────


def test_opening_a_report_with_a_broken_trace_marks_its_row_unreadable(hist, monkeypatch):
    window, folder, _ = hist
    doc = complete_doc(report_id="broken", started_at="2026-09-10T00:00:00Z")
    doc["trace"] = {"t_ms": "not a list"}
    store.save_report(doc, folder)
    page = window.history_page
    page.refresh()
    ids = [r.entry.report_id for r in page.rows()]
    assert not page.rows()[ids.index("broken")].entry.error, "precondition: listed readable"
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    page.select_rows([ids.index("broken")])
    page.open_btn.click()
    assert warned, "opening it said nothing"
    row = next(r for r in page.rows() if r.entry.path == store.report_path("broken", folder))
    assert row.entry.error, "the row still claims a report that cannot be opened is readable"
    table = page.findChild(QTableWidget, "PwmReport_Table_history")
    assert table.item(page.rows().index(row), 3).text() == "Unreadable"
