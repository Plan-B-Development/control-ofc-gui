"""Saving a PWM Test Report or a comparison to a file (DEC-404 Stage 5).

The thin Qt half of the exporters in ``services/pwm_report``: a menu, a file or
folder dialog, and ``atomic_write``. What goes *in* each file is decided by the
Qt-free ``export_markdown`` / ``export_html`` / ``export_csv`` modules.

S5-9: one Export drop-down menu per report. S5-2: CSV asks for a folder and writes one
file per table that has rows, then names what it wrote. S5-10: a comparison
exports as Markdown or HTML.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QPushButton, QWidget

from control_ofc.paths import atomic_write, export_default_dir
from control_ofc.services.pwm_report import export_csv, export_html, export_markdown
from control_ofc.services.pwm_report.compare import Comparison
from control_ofc.services.pwm_report.store import serialise

FORMAT_JSON = "json"
FORMAT_MARKDOWN = "markdown"
FORMAT_HTML = "html"
FORMAT_CSV = "csv"

#: ``(format, menu label)`` in menu order.
REPORT_FORMATS: Sequence[tuple[str, str]] = (
    (FORMAT_JSON, "JSON (the full record)…"),
    (FORMAT_MARKDOWN, "Markdown (for an issue)…"),
    (FORMAT_HTML, "HTML with charts…"),
    (FORMAT_CSV, "CSV tables (choose a folder)…"),
)
COMPARISON_FORMATS: Sequence[tuple[str, str]] = (
    (FORMAT_MARKDOWN, "Markdown (for an issue)…"),
    (FORMAT_HTML, "HTML…"),
)

_SUFFIX = {FORMAT_JSON: ".json", FORMAT_MARKDOWN: ".md", FORMAT_HTML: ".html"}
_FILTER = {
    FORMAT_JSON: "JSON (*.json)",
    FORMAT_MARKDOWN: "Markdown (*.md)",
    FORMAT_HTML: "HTML (*.html)",
}


def attach_export_menu(
    button: QPushButton,
    formats: Sequence[tuple[str, str]],
    handler: Callable[[str], None],
    object_prefix: str,
) -> QMenu:
    """Give *button* a drop-down of *formats*; picking one calls ``handler(fmt)``.

    Each action carries ``<object_prefix>_<format>`` as its objectName, so a test
    can trigger it the way a user does.
    """
    menu = QMenu(button)
    menu.setObjectName(f"{object_prefix}_Menu")
    for fmt, label in formats:
        action = menu.addAction(label)
        action.setObjectName(f"{object_prefix}_{fmt}")
        action.triggered.connect(lambda _checked=False, f=fmt: handler(f))
    button.setMenu(menu)
    return menu


def _safe_id(value: object) -> str:
    return "".join(ch for ch in str(value or "report") if ch.isalnum() or ch in "-_")


def report_text(doc: Mapping, fmt: str) -> str:
    if fmt == FORMAT_JSON:
        return serialise(dict(doc))
    if fmt == FORMAT_MARKDOWN:
        return export_markdown.report_markdown(doc)
    if fmt == FORMAT_HTML:
        return export_html.report_html(doc)
    raise ValueError(f"not a single-file report format: {fmt}")


def _save_as(parent: QWidget, title: str, default_name: str, fmt: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(
        parent, title, str(export_default_dir() / default_name), _FILTER[fmt]
    )
    if not path:
        return None
    target = Path(path)
    if target.suffix.lower() != _SUFFIX[fmt]:
        target = target.with_name(target.name + _SUFFIX[fmt])
        # The dialog confirmed an overwrite of the name it was given, not of this
        # one, so ask here before replacing a file the user never saw named.
        if target.exists():
            answer = QMessageBox.question(
                parent,
                "Replace file?",
                f"{target.name} already exists in that folder and will be replaced.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
    return target


def export_report(parent: QWidget, doc: Mapping, fmt: str) -> list[Path]:
    """Ask where, then write. Returns the files written (empty if cancelled or
    failed — a failure is shown to the user here)."""
    rid = _safe_id(doc.get("report_id"))
    if fmt == FORMAT_CSV:
        return _export_csv(parent, doc)
    target = _save_as(parent, "Export PWM Test Report", f"pwm-report-{rid}{_SUFFIX[fmt]}", fmt)
    if target is None:
        return []
    try:
        atomic_write(target, report_text(doc, fmt))
    except Exception as e:  # a report from elsewhere is untrusted beyond its schema
        QMessageBox.warning(parent, "Export failed", f"Could not write the report: {e}")
        return []
    return [target]


def _export_csv(parent: QWidget, doc: Mapping) -> list[Path]:
    try:
        tables = export_csv.csv_tables(doc)
    except Exception as e:  # as above
        QMessageBox.warning(parent, "Export failed", f"Could not build the CSV files: {e}")
        return []
    files = {export_csv.csv_file_name(doc, t): text for t, text in tables.items()}
    if not files:
        QMessageBox.information(
            parent, "Nothing to export", "This report has no sweep, probe or trace data."
        )
        return []
    chosen = QFileDialog.getExistingDirectory(
        parent, "Choose a folder for the CSV files", str(export_default_dir())
    )
    if not chosen:
        return []
    folder = Path(chosen)
    existing = [name for name in files if (folder / name).exists()]
    if existing:
        answer = QMessageBox.question(
            parent,
            "Replace files?",
            "These files already exist in that folder and will be replaced:\n"
            + "\n".join(existing),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return []
    written: list[Path] = []
    try:
        for name, text in files.items():
            atomic_write(folder / name, text)
            written.append(folder / name)
    except (OSError, ValueError) as e:
        done = "\n".join(p.name for p in written) or "none"
        QMessageBox.warning(
            parent, "Export failed", f"Could not write the CSV files: {e}\nWritten: {done}"
        )
        return written
    missing = [suffix for suffix, _ in export_csv.TABLES if suffix not in tables]
    note = (
        f"\n\nNo file for: {', '.join(missing)} — this report has no rows for it."
        if missing
        else ""
    )
    QMessageBox.information(
        parent,
        "CSV files written",
        f"Wrote to {folder}:\n" + "\n".join(p.name for p in written) + note,
    )
    return written


def export_comparison(parent: QWidget, cmp: Comparison, fmt: str) -> list[Path]:
    name = (
        f"pwm-report-compare-{_safe_id(cmp.earlier.report_id)}-vs-{_safe_id(cmp.later.report_id)}"
    )
    target = _save_as(parent, "Export comparison", f"{name}{_SUFFIX[fmt]}", fmt)
    if target is None:
        return []
    try:
        text = (
            export_markdown.comparison_markdown(cmp)
            if fmt == FORMAT_MARKDOWN
            else export_html.comparison_html(cmp)
        )
        atomic_write(target, text)
    except Exception as e:  # as above
        QMessageBox.warning(parent, "Export failed", f"Could not write the comparison: {e}")
        return []
    return [target]
