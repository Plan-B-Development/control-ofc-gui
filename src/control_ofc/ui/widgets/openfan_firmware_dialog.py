"""The "Update OpenFAN Firmware" window (DEC-481, DEC-483).

A thin renderer over ``services.openfan_firmware_view``; the checks on the file
are ``services.uf2``. Every decision about wording lives there and is unit-tested
headlessly. This window owns the file picker, the prepared copy it offers for
dragging, the upload that asks the daemon whether it would write the file
itself, the confirmation, the 1 Hz poll while a run is followed, and nothing
else.

**Modeless, one at a time.** The update runs daemon-side for up to a quarter of
an hour while the user works in a file manager, so the window must not block the
application; the Hardware page raises the one already open rather than opening a
second. **Closing it never stops the update**, and a reopened window picks the
run up again from ``GET /fans/openfan/maintenance``.

The GUI never touches the board: the daemon puts it in update mode, watches it
and — for a published release it knows, with the opt-in USB access — writes the
file itself; otherwise the user copies the file.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import (
    OpenFanDevice,
    OpenFanFirmwareStaged,
    OpenFanMaintenanceRecord,
    OpenFanMaintenanceSummary,
)
from control_ofc.services.openfan_firmware_view import (
    CLOSE_NOTE,
    COOLING_WINDOWS,
    INTRO,
    LEAVE_UPDATE_MODE,
    PREPARED_NOTE,
    ROW_CURRENT,
    ROW_DONE,
    ROW_STOPPED,
    TONE_CRIT,
    TONE_INFO,
    TONE_OK,
    TONE_WARN,
    WRITE_DAEMON,
    WRITE_MANUAL,
    ChannelLine,
    RunView,
    WritePlan,
    build_run_view,
    confirm_text,
    device_rows,
    evidence_rows,
    file_findings,
    file_rows,
    info_rows,
    outcome_view,
    pump_note,
    start_block_reason,
    write_plan,
    write_rows,
)
from control_ofc.services.uf2 import (
    RELEASES_URL,
    FirmwareFileError,
    PreparedFileError,
    Uf2Inspection,
    inspect_uf2,
    prepare_firmware,
    prepared_file_for,
    read_firmware_file,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import set_chip_class

POLL_INTERVAL_MS = 1000

MODE_SETUP = "setup"
MODE_RUN = "run"
MODE_RESULT = "result"

_TONE_CHIP = {
    TONE_OK: "SuccessChip",
    TONE_INFO: "InfoChip",
    TONE_WARN: "WarningChip",
    TONE_CRIT: "CriticalChip",
}
_ROW_GLYPH = {ROW_DONE: "✓", ROW_CURRENT: "▶", ROW_STOPPED: "✖"}


def _clear_layout(layout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        widget = child.widget()
        if widget is not None:
            widget.deleteLater()


def _wrapped(text: str, object_name: str, css_class: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    # Plain text: the daemon's messages and the file's strings are data.
    label.setTextFormat(Qt.TextFormat.PlainText)
    if css_class:
        label.setProperty("class", css_class)
    return label


def _inline_button(text: str, object_name: str, accessible: str) -> QPushButton:
    return make_button(text, "secondary", object_name=object_name, accessible_name=accessible)


def _heading(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("class", "CardTitle")
    return label


class FileDragHandle(QLabel):
    """The prepared copy, as something to drag onto the ``RPI-RP2`` drive.

    Dragging hands the file manager a file URL and copying is the file
    manager's job — the GUI writes nothing to the drive. The "Open folder"
    button beside it is the keyboard route to the same file.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OfwDialog_Label_dragFile")
        self.setProperty("class", "InfoChip")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self._path: Path | None = None
        self._press: QPoint | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def set_path(self, path: Path | None) -> None:
        self._path = path
        self.setText(f"⇲  {path.name} — drag this onto the RPI-RP2 drive" if path else "")
        self.setToolTip(str(path) if path else "")

    def mime_data(self) -> QMimeData | None:
        if self._path is None:
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self._path))])
        return mime

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._press is None
            or not event.buttons() & Qt.MouseButton.LeftButton
            or (event.position().toPoint() - self._press).manhattanLength()
            < QApplication.startDragDistance()
        ):
            return
        mime = self.mime_data()
        self._press = None
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class OpenFanFirmwareDialog(ModalDialog):
    """Prepares, starts and follows one OpenFAN firmware update."""

    device_requested = Signal()
    #: The checked file's bytes, for the daemon's verdict on it (DEC-483).
    stage_requested = Signal(object)
    #: Expected USB serial, firmware claim, and the file's bytes when the
    #: daemon is to write it (else None).
    start_requested = Signal(str, dict, object)
    poll_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        *,
        channels: list[ChannelLine],
        prepared_dir: Path,
        daemon_write_supported: bool,
        demo: bool = False,
        parent: QWidget | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        super().__init__("Update OpenFAN Firmware", parent, modal=False)
        self.setObjectName("OpenFanFirmwareDialog")
        self._demo = demo
        #: ``control.openfan_firmware_write``: the daemon can be asked to write.
        self._write_supported = daemon_write_supported and not demo
        self._prepared_dir = prepared_dir
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._mode = MODE_SETUP
        self._device: OpenFanDevice | None = None
        self._live_link: str | None = None
        self._live_update: OpenFanMaintenanceSummary | None = None
        self._external_block = ""
        self._inspection: Uf2Inspection | None = None
        self._prepared: Path | None = None
        #: The checked file's bytes, kept for the daemon to write (DEC-483).
        self._data: bytes | None = None
        #: The daemon's answer about this file, or why it could not be asked.
        self._staged: OpenFanFirmwareStaged | None = None
        self._stage_error = ""
        #: The run this window follows; "" when none.
        self._run_id = ""
        #: The run that was current when Start was clicked, ignored until the
        #: start resolves: a poll queued before the start can still answer with it.
        self._stale_run_id = ""
        self._starting = False
        self._start_clicked_ms = 0
        #: The run the daemon's ``202`` named; "" until it answers.
        self._started_run_id = ""
        #: The start got no answer and may have made a run anyway: the daemon's
        #: next answers decide (``apply_run``).
        self._unconfirmed = False
        self._unconfirmed_misses = 0
        self._poll_in_flight = False
        self._last_record: OpenFanMaintenanceRecord | None = None

        body = self.body_layout()
        body.addWidget(_wrapped(INTRO, "OfwDialog_Label_intro"))

        # ── Setup ──
        self._setup = QWidget()
        self._setup.setObjectName("OfwDialog_Widget_setup")
        setup = QVBoxLayout(self._setup)
        setup.setContentsMargins(0, 0, 0, 0)

        controller_row = QHBoxLayout()
        controller_row.addWidget(_heading("Controller", "OfwDialog_Label_controllerHeading"), 1)
        self._refresh_btn = _inline_button(
            "Read again", "OfwDialog_Btn_refresh", "Read the controller again"
        )
        self._refresh_btn.clicked.connect(self._on_refresh)
        controller_row.addWidget(self._refresh_btn)
        setup.addLayout(controller_row)
        self._device_grid = QGridLayout()
        setup.addLayout(self._device_grid)

        file_row = QHBoxLayout()
        file_row.addWidget(_heading("Firmware file", "OfwDialog_Label_fileHeading"), 1)
        self._choose_btn = _inline_button(
            "Choose file…", "OfwDialog_Btn_choose", "Choose an OpenFAN firmware file"
        )
        self._choose_btn.clicked.connect(self._on_choose)
        file_row.addWidget(self._choose_btn)
        setup.addLayout(file_row)
        self._file_path_lbl = _wrapped("No file chosen.", "OfwDialog_Label_filePath", "CardMeta")
        setup.addWidget(self._file_path_lbl)
        self._file_grid = QGridLayout()
        setup.addLayout(self._file_grid)
        self._findings = QVBoxLayout()
        setup.addLayout(self._findings)
        self._write_lbl = _wrapped("", "OfwDialog_Label_writer")
        self._write_lbl.setVisible(False)
        setup.addWidget(self._write_lbl)
        releases = QLabel(f'Official releases: <a href="{RELEASES_URL}">{RELEASES_URL}</a>')
        releases.setObjectName("OfwDialog_Label_releases")
        releases.setTextFormat(Qt.TextFormat.RichText)
        releases.setOpenExternalLinks(True)
        releases.setProperty("class", "CardMeta")
        setup.addWidget(releases)

        setup.addWidget(_heading("Cooling during the update", "OfwDialog_Label_coolingHeading"))
        for index, text in enumerate(COOLING_WINDOWS):
            setup.addWidget(_wrapped(f"•  {text}", f"OfwDialog_Label_cooling{index}"))
        self._channels_lbl = _wrapped("", "OfwDialog_Label_channels", "CardMeta")
        setup.addWidget(self._channels_lbl)
        self._pump_lbl = _wrapped("", "OfwDialog_Label_pump", "WarningChip")
        setup.addWidget(self._pump_lbl)

        setup.addWidget(_heading("Before you start", "OfwDialog_Label_confirmHeading"))
        setup.addWidget(_wrapped(LEAVE_UPDATE_MODE, "OfwDialog_Label_leave"))
        setup.addWidget(_wrapped(CLOSE_NOTE, "OfwDialog_Label_close", "CardMeta"))
        self._confirm = QCheckBox(confirm_text(WRITE_MANUAL))
        self._confirm.setObjectName("OfwDialog_Check_confirm")
        self._confirm.toggled.connect(self._refresh_start)
        setup.addWidget(self._confirm)
        self._last_lbl = _wrapped("", "OfwDialog_Label_last", "CardMeta")
        setup.addWidget(self._last_lbl)
        body.addWidget(self._setup)

        # ── The prepared copy: in setup once checked, and while the file is wanted ──
        self._prepared_box = QWidget()
        self._prepared_box.setObjectName("OfwDialog_Widget_prepared")
        prepared = QVBoxLayout(self._prepared_box)
        prepared.setContentsMargins(0, 0, 0, 0)
        prepared.addWidget(_heading("The file to copy", "OfwDialog_Label_preparedHeading"))
        drag_row = QHBoxLayout()
        self._drag = FileDragHandle()
        drag_row.addWidget(self._drag, 1)
        self._open_folder_btn = _inline_button(
            "Open folder", "OfwDialog_Btn_openFolder", "Open the folder holding the file to copy"
        )
        self._open_folder_btn.clicked.connect(self._on_open_folder)
        drag_row.addWidget(self._open_folder_btn)
        prepared.addLayout(drag_row)
        self._prepared_lbl = _wrapped(PREPARED_NOTE, "OfwDialog_Label_prepared", "CardMeta")
        prepared.addWidget(self._prepared_lbl)
        self._prepared_box.setVisible(False)
        body.addWidget(self._prepared_box)

        # ── Progress ──
        self._progress = QWidget()
        self._progress.setObjectName("OfwDialog_Widget_progress")
        progress = QVBoxLayout(self._progress)
        progress.setContentsMargins(0, 0, 0, 0)
        self._headline = _wrapped("", "OfwDialog_Label_headline", "CardTitle")
        progress.addWidget(self._headline)
        self._stage_box = QVBoxLayout()
        progress.addLayout(self._stage_box)
        self._instruction = _wrapped("", "OfwDialog_Label_instruction")
        progress.addWidget(self._instruction)
        self._write_bar = QProgressBar()
        self._write_bar.setObjectName("OfwDialog_Progress_write")
        self._write_bar.setAccessibleName("Firmware written, or read back")
        self._write_bar.setVisible(False)
        progress.addWidget(self._write_bar)
        self._time_left = _wrapped("", "OfwDialog_Label_timeLeft", "CardMeta")
        progress.addWidget(self._time_left)
        self._warnings = _wrapped("", "OfwDialog_Label_warnings", "WarningChip")
        progress.addWidget(self._warnings)
        self._notes = _wrapped("", "OfwDialog_Label_notes", "CardMeta")
        progress.addWidget(self._notes)
        self._progress.setVisible(False)
        body.addWidget(self._progress)

        # ── Result ──
        self._result = QWidget()
        self._result.setObjectName("OfwDialog_Widget_result")
        result = QVBoxLayout(self._result)
        result.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        self._outcome_pill = StatusPill("", "neutral", object_name="OfwDialog_Pill_outcome")
        title_row.addWidget(self._outcome_pill)
        self._outcome_title = _wrapped("", "OfwDialog_Label_outcomeTitle", "CardTitle")
        title_row.addWidget(self._outcome_title, 1)
        result.addLayout(title_row)
        self._outcome_summary = _wrapped("", "OfwDialog_Label_outcomeSummary")
        result.addWidget(self._outcome_summary)
        self._outcome_steps = _wrapped("", "OfwDialog_Label_outcomeSteps")
        result.addWidget(self._outcome_steps)
        self._outcome_detail = _wrapped("", "OfwDialog_Label_outcomeDetail", "CardMeta")
        result.addWidget(self._outcome_detail)
        self._evidence_grid = QGridLayout()
        result.addLayout(self._evidence_grid)
        self._info_table = QTableWidget(0, 4)
        self._info_table.setObjectName("OfwDialog_Table_reports")
        self._info_table.setHorizontalHeaderLabels(["Report", "Before", "After", "File"])
        self._info_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_dense_table(self._info_table)
        self._info_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._info_table.setAccessibleName("The board's reports before and after the update")
        result.addWidget(self._info_table)
        self._result.setVisible(False)
        body.addWidget(self._result)

        self._blocked = _wrapped("", "OfwDialog_Label_blocked", "CardMeta")
        body.addWidget(self._blocked)
        self._status = _wrapped("", "OfwDialog_Label_status")
        self._status.setVisible(False)
        body.addWidget(self._status)
        body.addStretch(1)

        self._start_btn = self.add_footer_button(
            "Start update", "primary", object_name="OfwDialog_Btn_start"
        )
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = self.add_footer_button(
            "Cancel update", "secondary", object_name="OfwDialog_Btn_cancel"
        )
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._again_btn = self.add_footer_button(
            "New update", "secondary", object_name="OfwDialog_Btn_again"
        )
        self._again_btn.clicked.connect(self._on_again)
        self._close_btn = self.add_footer_button(
            "Close", "ghost", object_name="OfwDialog_Btn_close"
        )
        self._close_btn.clicked.connect(self.reject)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._on_poll_tick)

        self.set_channels(channels)
        self._render_device()
        self._set_mode(MODE_SETUP)

    # ── state the page feeds in ──────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def following(self) -> str:
        """The run id this window follows, or ``""``."""
        return self._run_id

    def set_channels(self, channels: list[ChannelLine]) -> None:
        names = ", ".join(line.text for line in channels)
        self._channels_lbl.setText(
            f"OpenFAN channels: {names}." if names else "No OpenFAN channel is reporting."
        )
        note = pump_note(channels)
        self._pump_lbl.setText(note)
        self._pump_lbl.setVisible(bool(note))

    def set_live_status(self, link: str | None, update: OpenFanMaintenanceSummary | None) -> None:
        """The poll's view of the controller, every second.

        A link that comes back connected re-reads the controller, so the
        refusals shown are current; an update this window is not following —
        started elsewhere, or before the window opened — is picked up.
        """
        was = self._live_link
        self._live_link = link
        self._live_update = update
        if self._mode == MODE_SETUP and link == "connected" and was != "connected":
            self.device_requested.emit()
        if (
            update is not None
            and update.state == "running"
            and update.run_id != self._run_id
            and (not self._starting or self._may_be_this_start(update.run_id))
        ):
            self._request_poll()
            self._timer.start()
        self._refresh_start()

    def set_external_block(self, reason: str) -> None:
        """Something outside this window stops a start — a PWM Test Report run."""
        self._external_block = reason
        self._refresh_start()

    def request_initial(self) -> None:
        """Ask for the controller and for the current or last run."""
        if self._demo:
            return
        self.device_requested.emit()
        self._request_poll()

    # ── the controller ───────────────────────────────────────────────

    @Slot(object)
    def apply_device(self, device: OpenFanDevice) -> None:
        self._device = device
        self._render_device()
        self._render_findings()
        self._render_write_plan()
        self._refresh_start()

    @Slot(str, str)
    def apply_device_error(self, _category: str, message: str) -> None:
        self._show_status(f"The controller could not be read: {message}")

    def _render_device(self) -> None:
        if self._demo:
            rows = [("Controller", "Demo mode has no OpenFAN controller.")]
        else:
            rows = device_rows(self._device)
        self._fill_grid(self._device_grid, rows, "OfwDialog_Device")

    # ── the file ─────────────────────────────────────────────────────

    @Slot()
    def _on_choose(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose OpenFAN firmware", str(Path.home()), "UF2 firmware (*.uf2);;All files (*)"
        )
        if path:
            self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        self._inspection = None
        self._prepared = None
        self._data = None
        self._staged = None
        self._stage_error = ""
        self._confirm.setChecked(False)
        self._file_path_lbl.setText(str(path))
        try:
            data = read_firmware_file(path)
        except FirmwareFileError as exc:
            self._fill_grid(self._file_grid, [], "OfwDialog_File")
            self._set_findings_text(str(exc), TONE_CRIT)
            self._render_write_plan()
            self._refresh_start()
            return
        inspection = inspect_uf2(data)
        self._inspection = inspection
        if inspection.ok:
            # Prepared whoever writes it: the copy is the daemon write's fallback.
            try:
                self._prepared = prepare_firmware(data, inspection, self._prepared_dir)
            except PreparedFileError as exc:
                self._show_status(str(exc))
            self._data = data
            self._request_stage()
        self._fill_grid(self._file_grid, file_rows(inspection), "OfwDialog_File")
        self._render_findings()
        self._render_prepared(self._prepared)
        self._render_write_plan()
        self._refresh_start()

    # ── who writes it (DEC-483) ──────────────────────────────────────

    def _request_stage(self) -> None:
        """Hand the daemon the checked file and ask what it would do with it."""
        if not self._write_supported or self._data is None:
            return
        self._staged = None
        self._stage_error = ""
        self.stage_requested.emit(self._data)

    @Slot(object)
    def apply_staged(self, staged: OpenFanFirmwareStaged) -> None:
        if self._inspection is None or staged.sha256 != self._inspection.sha256:
            return  # about a file chosen before this one
        self._staged = staged
        self._stage_error = ""
        self._render_write_plan()
        self._refresh_start()

    @Slot(str, str, str)
    def apply_stage_error(self, _category: str, message: str, sha256: str) -> None:
        if self._inspection is None or sha256 != self._inspection.sha256:
            return
        self._staged = None
        self._stage_error = message or "no answer"
        self._render_write_plan()
        self._refresh_start()

    def _plan(self) -> WritePlan:
        if self._demo:
            return WritePlan(WRITE_MANUAL)
        return write_plan(
            supported=self._write_supported,
            device=self._device,
            inspection=self._inspection,
            staged=self._staged,
            stage_error=self._stage_error,
        )

    def _render_write_plan(self) -> None:
        plan = self._plan()
        self._write_lbl.setText(plan.text)
        set_chip_class(self._write_lbl, _TONE_CHIP.get(plan.tone, "CardMeta"))
        self._write_lbl.setVisible(bool(plan.text))
        # The confirmation names who writes the file, so a change of writer
        # takes it back.
        text = confirm_text(plan.method)
        if self._confirm.text() != text:
            self._confirm.setText(text)
            self._confirm.setChecked(False)
        if self._mode == MODE_SETUP:
            # Nothing to copy while the daemon writes; offered again on a fallback.
            self._prepared_box.setVisible(
                self._prepared is not None and plan.method != WRITE_DAEMON
            )

    def _render_findings(self) -> None:
        _clear_layout(self._findings)
        if self._inspection is None:
            return
        for index, finding in enumerate(file_findings(self._inspection, self._device)):
            label = _wrapped(finding.text, f"OfwDialog_Label_finding{index}")
            label.setProperty("class", _TONE_CHIP.get(finding.tone, "CardMeta"))
            self._findings.addWidget(label)

    def _set_findings_text(self, text: str, tone: str) -> None:
        _clear_layout(self._findings)
        label = _wrapped(text, "OfwDialog_Label_finding0")
        label.setProperty("class", _TONE_CHIP.get(tone, "CardMeta"))
        self._findings.addWidget(label)

    def _render_prepared(self, path: Path | None, *, missing_sha: str = "") -> None:
        self._drag.set_path(path)
        self._drag.setVisible(path is not None)
        self._open_folder_btn.setVisible(path is not None)
        if path is not None:
            self._prepared_lbl.setText(PREPARED_NOTE)
        elif missing_sha:
            self._prepared_lbl.setText(
                "The checked copy is not on this computer any more. Copy the same firmware "
                f"file (SHA-256 {missing_sha[:16]}…) onto the drive."
            )
        self._prepared_box.setVisible(path is not None or bool(missing_sha))

    @Slot()
    def _on_open_folder(self) -> None:
        path = self._drag.path
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ── starting ─────────────────────────────────────────────────────

    def _start_reason(self) -> str:
        return start_block_reason(
            demo=self._demo,
            device=self._device,
            live_link=self._live_link,
            external_block=self._external_block,
            inspection=self._inspection,
            plan=self._plan(),
            prepared=self._prepared is not None,
            confirmed=self._confirm.isChecked(),
            starting=self._starting,
        )

    def _refresh_start(self, *_args) -> None:
        reason = self._start_reason() if self._mode == MODE_SETUP else ""
        self._start_btn.setEnabled(self._mode == MODE_SETUP and not reason)
        self._start_btn.setToolTip(reason)
        self._blocked.setText(reason)
        self._blocked.setVisible(self._mode == MODE_SETUP and bool(reason))

    @Slot()
    def _on_start(self) -> None:
        # The button is disabled whenever a reason stands; checked again here
        # because the request it sends is the user's go-ahead.
        if self._mode != MODE_SETUP or self._start_reason():
            return
        if self._inspection is None or self._device is None or self._device.usb is None:
            return
        serial = self._device.usb.serial
        if not serial:
            return
        self._starting = True
        self._stale_run_id = self._last_record.run_id if self._last_record else ""
        self._start_clicked_ms = self._now_ms()
        self._started_run_id = ""
        self._unconfirmed = False
        self._unconfirmed_misses = 0
        self._show_status("Starting…")
        self._refresh_start()
        write = self._data if self._plan().method == WRITE_DAEMON else None
        self.start_requested.emit(serial, self._inspection.claim(), write)
        self._timer.start()

    @Slot(str)
    def apply_started(self, run_id: str) -> None:
        """The daemon accepted the start, and its ``202`` names the run.

        The read that follows renders it; until then an answer is matched on
        this id rather than on the time of the click.
        """
        if self._starting:
            self._started_run_id = run_id

    @Slot(str, str)
    def apply_start_error(self, _category: str, message: str) -> None:
        """The daemon answered and refused the start: nothing began."""
        self._end_start(f"The update did not start: {message}")

    @Slot(str, str)
    def apply_start_unconfirmed(self, _category: str, message: str) -> None:
        """No answer to the start, so it may have made a run: ask the daemon."""
        if not self._starting:
            return  # a poll has already found the run this Start made
        self._unconfirmed = True
        self._unconfirmed_misses = 0
        # A sentence from the transport, or the backstop's bare clause.
        said = message.rstrip(".")
        said = said[:1].upper() + said[1:]
        self._show_status(f"{said}. The update may have started — checking with the daemon…")
        self._request_poll()

    def _end_start(self, message: str) -> None:
        """No run came of this Start."""
        self._starting = False
        self._unconfirmed = False
        self._stale_run_id = ""
        self._started_run_id = ""
        if not self._run_id:
            self._timer.stop()
        self._show_status(message)
        self.device_requested.emit()
        self._refresh_start()

    def _is_this_start(self, record: OpenFanMaintenanceRecord) -> bool:
        """Whether *record* is the run this Start made."""
        if self._started_run_id:
            return record.run_id == self._started_run_id
        # No id from the daemon: never the run current before the click, nor any
        # older one, which a poll queued earlier can still return.
        return (
            record.run_id != self._stale_run_id and record.started_unix_ms >= self._start_clicked_ms
        )

    def _may_be_this_start(self, run_id: str) -> bool:
        """Whether a run the poll reports could be the one this Start made."""
        if self._started_run_id:
            return run_id == self._started_run_id
        return run_id != self._stale_run_id

    # ── following a run ──────────────────────────────────────────────

    def _request_poll(self) -> None:
        if self._poll_in_flight or self._demo:
            return
        self._poll_in_flight = True
        self.poll_requested.emit()

    @Slot()
    def _on_poll_tick(self) -> None:
        self._request_poll()

    @Slot(object)
    def apply_run(self, record: OpenFanMaintenanceRecord | None) -> None:
        """Render the daemon's current or last run. ``None``: it has none."""
        self._poll_in_flight = False
        if self._starting:
            if record is None or not self._is_this_start(record):
                if self._unconfirmed:
                    # The first answer may be to a poll sent before the start
                    # reached the daemon; a second, sent after it, settles it.
                    self._unconfirmed_misses += 1
                    if self._unconfirmed_misses >= 2:
                        self._end_start("The update did not start: the daemon has no run from it.")
                return
            self._starting = False
            self._unconfirmed = False
            self._run_id = record.run_id
            self._show_status("")
        elif record is None:
            if self._run_id:
                self._run_id = ""
                self._timer.stop()
                self._set_mode(MODE_SETUP)
                self._show_status(
                    "The daemon no longer has a record of this update — it may have "
                    "restarted without its journal."
                )
            return
        elif record.run_id != self._run_id:
            needs_recovery = (
                self._live_update is not None
                and self._live_update.needs_recovery
                and self._live_update.run_id == record.run_id
            )
            if not (record.is_running or needs_recovery):
                # A finished run nobody here followed: remembered, not shown.
                self._last_record = record
                self._render_last()
                if not self._run_id:
                    self._timer.stop()
                return
            self._run_id = record.run_id
        self._last_record = record
        self._render_run(record)

    @Slot(str, str)
    def apply_run_error(self, _category: str, message: str) -> None:
        """A failed read or cancel. A followed run is the daemon's: keep polling.

        Cancel comes back only while the run last seen was still cancellable, so
        a cancel refused as too late is not offered again.
        """
        self._poll_in_flight = False
        record = self._last_record
        self._cancel_btn.setEnabled(
            self._mode == MODE_RUN and record is not None and record.cancellable
        )
        if self._unconfirmed:
            message = f"Still checking whether the update started: {message}"
        self._show_status(message)

    def _render_last(self) -> None:
        record = self._last_record
        view = outcome_view(record) if record is not None else None
        when = ""
        if record is not None and record.finished_unix_ms:
            when = time.strftime(
                " (%Y-%m-%d %H:%M)", time.localtime(record.finished_unix_ms / 1000)
            )
        self._last_lbl.setText(f"Last update: {view.title}{when}." if view else "")
        self._last_lbl.setVisible(bool(view))

    def _render_run(self, record: OpenFanMaintenanceRecord) -> None:
        view = build_run_view(record, self._now_ms())
        self._set_mode(MODE_RUN if view.running else MODE_RESULT)
        if view.running:
            self._timer.start()
        else:
            self._timer.stop()
            self.device_requested.emit()
        self._render_progress(view)
        if view.wants_file:
            sha = record.firmware.sha256
            path = (
                self._prepared
                if self._prepared is not None
                and self._inspection is not None
                and self._inspection.sha256 == sha
                else prepared_file_for(sha, self._prepared_dir)
            )
            self._render_prepared(path, missing_sha="" if path else sha)
        else:
            self._prepared_box.setVisible(False)
        self._cancel_btn.setEnabled(view.can_cancel)
        self._cancel_btn.setToolTip(
            ""
            if view.can_cancel
            else "Once the board is asked to enter update mode, the update cannot be cancelled."
        )
        if view.outcome is not None:
            self._render_outcome(record, view)

    def _render_progress(self, view: RunView) -> None:
        self._headline.setText(view.headline)
        _clear_layout(self._stage_box)
        for index, row in enumerate(view.stages):
            glyph = _ROW_GLYPH.get(row.state, "·")
            text = f"{glyph}  {row.label}" + (f" — {row.duration}" if row.duration else "")
            label = _wrapped(text, f"OfwDialog_Label_stage{index}")
            if row.state == ROW_CURRENT:
                label.setProperty("class", "CardTitle")
            elif row.state == ROW_STOPPED:
                label.setProperty("class", "CriticalChip")
            else:
                label.setProperty("class", "CardMeta")
            label.setAccessibleName(f"{row.label}: {row.state}")
            self._stage_box.addWidget(label)
        self._instruction.setText(view.instruction)
        self._instruction.setVisible(bool(view.instruction))
        if view.progress is not None:
            done, total = view.progress
            self._write_bar.setRange(0, total)
            self._write_bar.setValue(done)
            self._write_bar.setFormat(f"{done // 1024} of {total // 1024} KiB")
        self._write_bar.setVisible(view.progress is not None)
        self._time_left.setText(view.time_left)
        self._time_left.setVisible(bool(view.time_left))
        self._warnings.setText("\n".join(view.warnings))
        self._warnings.setVisible(bool(view.warnings))
        self._notes.setText("\n".join(view.notes))
        self._notes.setVisible(bool(view.notes))

    def _render_outcome(self, record: OpenFanMaintenanceRecord, view: RunView) -> None:
        outcome = view.outcome
        if outcome is None:
            return
        tone_word = {
            TONE_OK: "Done",
            TONE_INFO: "Info",
            TONE_WARN: "Check",
            TONE_CRIT: "Action needed",
        }
        self._outcome_pill.set_text(tone_word.get(outcome.tone, ""))
        self._outcome_pill.set_state(outcome.tone)
        self._outcome_title.setText(outcome.title)
        set_chip_class(self._outcome_summary, _TONE_CHIP.get(outcome.tone, "CardMeta"))
        self._outcome_summary.setText(outcome.summary)
        self._outcome_steps.setText("\n".join(f"•  {s}" for s in outcome.steps))
        self._outcome_steps.setVisible(bool(outcome.steps))
        self._outcome_detail.setText(outcome.detail)
        self._outcome_detail.setVisible(bool(outcome.detail))
        self._fill_grid(
            self._evidence_grid, write_rows(record) + evidence_rows(record), "OfwDialog_Evidence"
        )
        rows = info_rows(record) if record.board_answered or record.after else []
        self._info_table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, text in enumerate(values):
                self._info_table.setItem(r, c, QTableWidgetItem(text))
        self._info_table.setVisible(bool(rows))

    # ── actions ──────────────────────────────────────────────────────

    @Slot()
    def _on_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._show_status("Cancelling…")
        self.cancel_requested.emit()

    @Slot()
    def _on_refresh(self) -> None:
        self.device_requested.emit()
        # Ask about the file again too: the daemon may have restarted since,
        # with the drop-in installed, and it keeps the file only in memory.
        self._request_stage()
        self._render_write_plan()
        self._refresh_start()

    @Slot()
    def _on_again(self) -> None:
        """Back to the start page for another update."""
        self._run_id = ""
        self._confirm.setChecked(False)
        self._show_status("")
        self._render_last()
        self._set_mode(MODE_SETUP)
        self._render_prepared(self._prepared)
        self._render_write_plan()
        self.device_requested.emit()

    # ── helpers ──────────────────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._setup.setVisible(mode == MODE_SETUP)
        self._progress.setVisible(mode in (MODE_RUN, MODE_RESULT))
        self._result.setVisible(mode == MODE_RESULT)
        if mode == MODE_SETUP:
            self._prepared_box.setVisible(
                self._prepared is not None and self._plan().method != WRITE_DAEMON
            )
        self._start_btn.setVisible(mode == MODE_SETUP)
        self._cancel_btn.setVisible(mode == MODE_RUN)
        self._again_btn.setVisible(mode == MODE_RESULT)
        self._refresh_start()

    def _show_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _fill_grid(self, grid: QGridLayout, rows: list[tuple[str, str]], prefix: str) -> None:
        _clear_layout(grid)
        for row, (name, value) in enumerate(rows):
            label = _wrapped(name, f"{prefix}Label_{row}", "CardMeta")
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            text = _wrapped(value, f"{prefix}Value_{row}")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(1, 1)

    def stop_polling(self) -> None:
        self._timer.stop()

    def reject(self) -> None:  # Qt override — Close, Escape and the title-bar X
        # Never asks: closing does not stop the update, and the window says so.
        self._timer.stop()
        super().reject()
