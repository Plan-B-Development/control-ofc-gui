"""DEC-314: headless tests for the Logs page's three new components.

The model, the row delegate and the activity histogram each ship their own test per
``CLAUDE.md § GUI component standard`` — outcome and painted state, not merely that
they construct.

**Paint assertions check the specific painted value**, never "the two renders differ":
DEC-273 recorded a disabled-state test that passed against an empty stylesheet because
Qt's own ``QPalette`` disabled group recoloured the text on its own. The severity edge
and the histogram bars are flat FILLs, where an exact colour match is both safe and
portable — unlike text, whose antialiasing makes an exact token hit depend on the
font stack.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem

from control_ofc.services.diagnostics_service import DiagEvent
from control_ofc.services.logs_view import (
    build_log_row,
    build_log_rows,
    collapse_repeats,
    time_span,
)
from control_ofc.services.logs_view import histogram_buckets as make_buckets
from control_ofc.ui.theme import ThemeTokens, active_theme, set_active_theme
from control_ofc.ui.widgets.activity_histogram import ActivityHistogram
from control_ofc.ui.widgets.log_event_model import ROW_ROLE, LogEventModel
from control_ofc.ui.widgets.log_row_delegate import (
    LogRowDelegate,
    message_color,
    meta_line,
    severity_color,
)

_BASE = 1_700_000_000.0


def _ev(i: int, level: str = "info", source: str = "gui", message: str = "m", **fields):
    return DiagEvent(
        timestamp=_BASE + i,
        level=level,
        source=source,
        message=message,
        seq=i + 1,
        fields=dict(fields),
    )


def _rows(*events):
    return build_log_rows(events)


@pytest.fixture
def default_theme():
    """Register a known theme cheaply and restore it afterwards.

    ``set_active_theme`` rather than ``apply_theme``: the latter re-polishes every
    live widget in the process and DEC-287 measured that at 507 ms. The painted
    widgets under test read ``active_theme()`` directly, so registration is all they
    need.
    """
    previous = active_theme()
    tokens = ThemeTokens()
    set_active_theme(tokens)
    yield tokens
    set_active_theme(previous)


# ── LogEventModel ──────────────────────────────────────────────────────────


def test_model_is_empty_until_rows_are_set(qtbot):
    model = LogEventModel()
    assert model.rowCount() == 0
    assert model.columnCount() == 1
    assert model.row_at(0) is None
    assert model.index_of_event(1) == -1


def test_model_exposes_the_view_model_under_its_own_role(qtbot):
    model = LogEventModel()
    rows = _rows(_ev(0, "error", "hwmon", "write failed"))
    model.set_rows(rows)

    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), ROW_ROLE) is rows[0]


def test_model_display_role_is_the_whole_log_line(qtbot):
    """The delegate paints the row and ignores DisplayRole, but Qt's accessibility
    bridge reads it — so a screen reader must not get an empty cell."""
    model = LogEventModel()
    model.set_rows(_rows(_ev(0, "warning", "fan", "stall detected")))

    text = model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole)
    assert "stall detected" in text and "WARN" in text and "fan" in text


def test_model_tooltip_is_the_message(qtbot):
    model = LogEventModel()
    model.set_rows(_rows(_ev(0, "info", "gui", "a very long message")))
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == "a very long message"


def test_model_rejects_a_child_parent_and_an_invalid_index(qtbot):
    """A flat table must report no children, or a tree view would recurse forever."""
    model = LogEventModel()
    model.set_rows(_rows(_ev(0)))
    top = model.index(0, 0)
    assert model.rowCount(top) == 0
    assert model.columnCount(top) == 0
    assert model.data(model.index(5, 0), ROW_ROLE) is None


def test_model_finds_a_row_by_stable_event_id(qtbot):
    """Selection is re-anchored through this after every reset (brief §4)."""
    model = LogEventModel()
    rows = _rows(_ev(0, message="a"), _ev(1, message="b"), _ev(2, message="c"))
    model.set_rows(list(reversed(rows)))

    assert model.index_of_event(rows[0].event_id) == 2
    assert model.index_of_event(rows[2].event_id) == 0
    assert model.index_of_event(9999) == -1


def test_model_set_rows_replaces_and_signals_a_reset(qtbot):
    model = LogEventModel()
    model.set_rows(_rows(_ev(0, message="first")))
    with qtbot.waitSignal(model.modelReset, timeout=500):
        model.set_rows(_rows(_ev(1, message="second")))
    assert [r.message for r in model.rows()] == ["second"]


def test_model_rows_returns_a_copy(qtbot):
    model = LogEventModel()
    model.set_rows(_rows(_ev(0)))
    model.rows().clear()
    assert model.rowCount() == 1, "a caller must not be able to empty the model"


# ── LogRowDelegate ─────────────────────────────────────────────────────────


def _paint_row(row, *, width=400, height=40, selected=False, theme=None):
    """Render one delegate row to an image and return it."""
    model = LogEventModel()
    model.set_rows([row])
    delegate = LogRowDelegate()
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor((theme or active_theme()).surface_1))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    if selected:
        option.state |= option.state.__class__.State_Selected
    painter = QPainter(image)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    return image


@pytest.mark.parametrize(
    "level,token",
    [("info", "status_ok"), ("warning", "status_warn"), ("error", "status_crit")],
)
def test_the_severity_edge_paints_the_exact_status_token(level, token, default_theme, qtbot):
    """A flat fill, so an exact colour comparison is safe and portable here."""
    image = _paint_row(build_log_row(_ev(0, level, "gui", "message")), theme=default_theme)
    edge = QColor(image.pixel(1, 20))
    assert edge.name() == QColor(getattr(default_theme, token)).name()


def test_a_selected_row_paints_the_selection_background(default_theme, qtbot):
    """Assert the painted value, not that two renders differ (DEC-273)."""
    row = build_log_row(_ev(0, "info", "gui", "message"))
    plain = _paint_row(row, theme=default_theme)
    picked = _paint_row(row, selected=True, theme=default_theme)

    far_right = 395  # past the text, so only the background is there
    assert QColor(picked.pixel(far_right, 20)).name() == QColor(default_theme.selected_bg).name()
    assert QColor(plain.pixel(far_right, 20)).name() == QColor(default_theme.surface_1).name()


def test_the_row_height_is_two_lines_of_the_themed_fonts(default_theme, qtbot):
    """A font metric, computed at runtime — never a literal (CLAUDE.md § Hard-won
    lessons: an exact-pixel layout threshold is not portable)."""
    model = LogEventModel()
    model.set_rows(_rows(_ev(0)))
    delegate = LogRowDelegate()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 400, 40)
    hint = delegate.sizeHint(option, model.index(0, 0))

    one_line = QFontMetrics(option.font).height()
    assert hint.height() >= 2 * one_line, "two text lines must fit"
    assert hint.height() < 4 * one_line, "…and the row stays dense"


def test_the_row_height_grows_with_the_themed_font_size(qtbot):
    """The relationship, measured from the same widget at two theme sizes — the
    portable form of "it scales with the user's font"."""
    model = LogEventModel()
    model.set_rows(_rows(_ev(0)))
    delegate = LogRowDelegate()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 400, 40)

    previous = active_theme()
    try:
        set_active_theme(ThemeTokens(base_font_size_pt=7))
        small = delegate.sizeHint(option, model.index(0, 0)).height()
        set_active_theme(ThemeTokens(base_font_size_pt=16))
        large = delegate.sizeHint(option, model.index(0, 0)).height()
    finally:
        set_active_theme(previous)
    assert large > small


def test_the_meta_line_carries_time_source_and_component():
    row = build_log_row(_ev(0, "warning", "fan", "stall", component="cpu_fan"))
    line = meta_line(row)
    assert row.time_str in line and "fan" in line and "cpu_fan" in line


def test_the_meta_line_omits_an_absent_component_and_marks_an_absent_source():
    assert "cpu_fan" not in meta_line(build_log_row(_ev(0, "info", "gui", "m")))
    assert "—" in meta_line(build_log_row(_ev(0, "info", "", "m")))


@pytest.mark.parametrize(
    "state,attr",
    [("crit", "status_crit"), ("warn", "status_warn"), ("ok", "status_ok"), ("weird", "status_ok")],
)
def test_severity_color_maps_states_to_status_tokens(state, attr, default_theme):
    assert severity_color(default_theme, state) == getattr(default_theme, attr)


def test_message_color_tints_only_warnings_and_errors(default_theme):
    """Preserved from the pre-DEC-314 table: dropping the tint would make an ERR
    line less visible than it is today, which brief §15 forbids."""
    assert message_color(default_theme, "crit") == default_theme.status_crit
    assert message_color(default_theme, "warn") == default_theme.status_warn
    assert message_color(default_theme, "ok") == default_theme.text_primary


# ── ActivityHistogram ──────────────────────────────────────────────────────


def _histogram(qtbot, events, *, buckets=4, width=400):
    widget = ActivityHistogram(object_name="Test_Histogram")
    qtbot.addWidget(widget)
    widget.resize(width, 60)
    rows = build_log_rows(events)
    widget.set_buckets(make_buckets(rows, span=time_span(rows), bucket_count=buckets))
    return widget


def test_histogram_bucket_capacity_follows_its_width(qtbot):
    widget = ActivityHistogram()
    qtbot.addWidget(widget)
    widget.resize(120, 60)
    narrow = widget.preferred_bucket_count()
    widget.resize(600, 60)
    wide = widget.preferred_bucket_count()

    assert wide > narrow, "a wider strip must offer more resolution (brief §3)"
    assert narrow >= 1, "and a very narrow one must still be usable"


def test_histogram_emits_capacity_changed_only_when_the_count_moves(qtbot):
    widget = ActivityHistogram()
    qtbot.addWidget(widget)
    widget.resize(400, 60)
    # Shown, because Qt defers the resize event for a widget that has never been
    # mapped — a hidden widget would record zero resizes and the test would pass
    # while asserting nothing.
    widget.show()
    qtbot.waitExposed(widget)
    seen: list[int] = []
    widget.capacity_changed.connect(lambda: seen.append(1))

    widget.resize(400, 90)  # height only — capacity is unchanged
    qtbot.wait(1)
    assert seen == []

    widget.resize(700, 90)
    qtbot.wait(1)
    assert seen == [1]


def test_histogram_click_selects_the_bucket_under_the_cursor(qtbot):
    widget = _histogram(qtbot, [_ev(i * 10) for i in range(4)])
    with qtbot.waitSignal(widget.bucket_clicked, timeout=500) as sig:
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(350, 30))
    assert sig.args == [3], "the right-hand quarter is the last bucket"


def test_clicking_the_selected_bucket_again_asks_to_clear(qtbot):
    widget = _histogram(qtbot, [_ev(i * 10) for i in range(4)])
    widget.set_selected_index(3)
    with qtbot.waitSignal(widget.bucket_clicked, timeout=500) as sig:
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(350, 30))
    assert sig.args == [-1]


def test_histogram_is_keyboard_operable(qtbot):
    """DEC-251: an interactive control that only a mouse can reach is a gap."""
    widget = _histogram(qtbot, [_ev(i * 10) for i in range(4)])
    widget.show()
    qtbot.waitExposed(widget)
    widget.setFocus()

    qtbot.keyClick(widget, Qt.Key.Key_Right)
    with qtbot.waitSignal(widget.bucket_clicked, timeout=500) as sig:
        qtbot.keyClick(widget, Qt.Key.Key_Space)
    assert sig.args == [1]

    widget.set_selected_index(1)
    with qtbot.waitSignal(widget.bucket_clicked, timeout=500) as sig:
        qtbot.keyClick(widget, Qt.Key.Key_Escape)
    assert sig.args == [-1], "Escape clears an applied window"


def test_histogram_cursor_stays_inside_the_bucket_range(qtbot):
    widget = _histogram(qtbot, [_ev(i * 10) for i in range(4)])
    widget.show()
    qtbot.waitExposed(widget)
    widget.setFocus()
    for _ in range(10):
        qtbot.keyClick(widget, Qt.Key.Key_Right)
    with qtbot.waitSignal(widget.bucket_clicked, timeout=500) as sig:
        qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert sig.args == [3], "never past the last bucket"


def test_histogram_drops_a_selection_that_no_longer_exists(qtbot):
    widget = _histogram(qtbot, [_ev(i * 10) for i in range(4)], buckets=4)
    widget.set_selected_index(3)
    rows = build_log_rows([_ev(0)])
    widget.set_buckets(make_buckets(rows, span=time_span(rows), bucket_count=2))
    assert widget.selected_index() is None


def test_histogram_paints_the_severity_token_for_its_events(qtbot, default_theme):
    """One bucket, all errors — the column must be painted in the crit token."""
    widget = _histogram(qtbot, [_ev(i, "error") for i in range(3)], buckets=1, width=100)
    image = QImage(100, 60, QImage.Format.Format_ARGB32)
    image.fill(QColor(default_theme.app_bg))
    widget.render(image)

    column = [QColor(image.pixel(50, y)).name() for y in range(60)]
    assert QColor(default_theme.status_crit).name() in column


def test_an_empty_histogram_says_so_rather_than_painting_nothing(qtbot):
    """Brief §12 — no large blank panels that look broken."""
    widget = ActivityHistogram()
    qtbot.addWidget(widget)
    widget.resize(200, 60)
    assert widget.buckets() == []
    widget.render(QImage(200, 60, QImage.Format.Format_ARGB32))  # must not raise


def test_histogram_height_is_a_font_metric(qtbot):
    """Not ``setMinimumHeight`` and not a literal: DEC-281 capped a pane 300 px below
    its content that way, and a literal derived on one machine is not portable."""
    widget = ActivityHistogram()
    qtbot.addWidget(widget)
    line = QFontMetrics(widget.font()).height()
    assert widget.minimumSizeHint().height() >= 2 * line
    assert widget.minimumSizeHint() == widget.sizeHint()


def test_histogram_tooltip_describes_the_bucket(qtbot):
    widget = _histogram(qtbot, [_ev(0, "error"), _ev(0, "info")], buckets=1, width=100)
    text = widget._describe(0)
    assert "2 events" in text and "error" in text and "info" in text
    assert widget._describe(99) == ""


def test_histogram_tooltip_reports_an_empty_bucket(qtbot):
    widget = _histogram(qtbot, [_ev(0), _ev(30)], buckets=4)
    empties = [i for i, b in enumerate(widget.buckets()) if b.total == 0]
    assert empties, "a sparse span must leave some columns empty"
    assert "no events" in widget._describe(empties[0])


def test_a_collapsed_run_is_still_counted_once_per_event_in_the_histogram(qtbot):
    """The histogram takes the UNCOLLAPSED rows so the bars show real volume — the
    reason its totals legitimately differ from the filter chips'."""
    events = [_ev(i, "warning", "fan", "stall") for i in range(5)]
    rows = build_log_rows(events)
    assert len(collapse_repeats(rows)) == 1, "precondition: these collapse to one row"
    buckets = make_buckets(rows, span=time_span(rows), bucket_count=2)
    assert sum(b.total for b in buckets) == 5
