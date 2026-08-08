"""Persist and restore every splitter's pane sizes (DEC-245).

One adopter for the whole window rather than nine hand-placed call sites. It
discovers splitters with ``findChildren`` and keys them by ``objectName``, which
every ``QSplitter`` in the app already sets uniquely — so a splitter added later
is covered without anyone remembering to register it. That is the same reasoning
DEC-234 used to put handle *styling* in one shared helper; DEC-244 is the standing
reminder of what per-site duplication costs.

The clamp lives in the Qt-free ``services.layout_state`` so it can be tested
without a widget; this file is the thin renderer half.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QSplitter, QWidget

from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.layout_state import clamp_restored_sizes

log = logging.getLogger(__name__)

# Dragging a handle emits splitterMoved continuously. Coalesce the burst into one
# write instead of rewriting the whole settings file per pixel.
_WRITE_DEBOUNCE_MS = 400


class SplitterPersistence(QObject):
    """Restores saved pane sizes on adoption and writes changes back, debounced."""

    def __init__(self, settings_service: AppSettingsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings_service
        self._splitters: dict[str, QSplitter] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_WRITE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)

    def adopt(self, root: QWidget) -> int:
        """Track every named splitter under *root*. Returns how many were found."""
        for splitter in root.findChildren(QSplitter):
            name = splitter.objectName()
            if not name or name in self._splitters:
                # An unnamed splitter has no stable key, so its sizes would collide
                # with the next unnamed one. Skip rather than persist nonsense.
                continue
            self._splitters[name] = splitter
            splitter.splitterMoved.connect(self._on_moved)
        # Restore after the event loop has laid the widgets out: before the first
        # layout pass sum(sizes) is 0 and every restore would be refused.
        QTimer.singleShot(0, self.restore_all)
        return len(self._splitters)

    def restore_all(self) -> None:
        saved = self._settings.settings.splitter_sizes
        for name, splitter in self._splitters.items():
            try:
                sizes = clamp_restored_sizes(saved.get(name), splitter.sizes())
            except RuntimeError:  # underlying C++ widget already destroyed
                continue
            if sizes is not None:
                splitter.setSizes(sizes)

    def _on_moved(self, _pos: int, _index: int) -> None:
        self._timer.start()

    def _flush(self) -> None:
        current = dict(self._settings.settings.splitter_sizes)
        changed = False
        for name, splitter in self._splitters.items():
            try:
                sizes = list(splitter.sizes())
            except RuntimeError:
                continue
            if sizes and current.get(name) != sizes:
                current[name] = sizes
                changed = True
        if changed:
            self._settings.update(splitter_sizes=current)

    def reset(self) -> None:
        """Forget every saved layout and hand the panes back to Qt.

        The other half of the DEC-245 bargain: the clamp stops a pane coming back
        unusable, this is the way out for a layout the user simply dislikes.
        """
        self._settings.update(splitter_sizes={})
        for splitter in self._splitters.values():
            try:
                count = splitter.count()
                if count:
                    total = sum(splitter.sizes())
                    splitter.setSizes([total // count] * count)
            except RuntimeError:
                continue

    def stop(self) -> None:
        """Flush and stop the timer (call from closeEvent).

        Flushes unconditionally rather than only when the timer is pending: a
        drag that ended just as the window closed can leave the sizes changed
        with the timer already fired-and-cleared, and `_flush` is a no-op when
        nothing actually differs.
        """
        self._timer.stop()
        self._flush()
