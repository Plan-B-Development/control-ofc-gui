"""Persist and restore every splitter's pane sizes (DEC-245).

One adopter for the whole window rather than nine hand-placed call sites. It
discovers splitters with ``findChildren`` and keys them by ``objectName``, which
every ``QSplitter`` in the app already sets uniquely — so a splitter added later
is covered without anyone remembering to register it. That is the same reasoning
DEC-234 used to put handle *styling* in one shared helper; DEC-244 is the standing
reminder of what per-site duplication costs.

**Everything here turns on one Qt fact:** ``QStackedLayout`` in its default
``StackOne`` mode calls ``setGeometry`` only on the *current* page. A splitter on
a page the user has not opened is therefore never laid out, and reports sizes
derived from Qt's 640x480 default rather than anything real.

The first cut of this class ignored that and was wrong twice over. It restored
every splitter once, a single event-loop turn after ``adopt()`` — which reached
only the startup page, while the other eight were scaled against a phantom total
and saturated by their panes' minimums, destroying the saved ratio. And on close
it flushed *all* of them, writing those phantom sizes over layouts the user had
genuinely dragged on pages they simply had not visited that session. Seven of
nine splitters drifted per launch; a user could lose their Logs layout without
ever opening Logs.

So both halves are now gated on a splitter having actually been laid out:

* **restore** happens on a splitter's first show, per splitter, via an event
  filter — the only moment its real geometry exists; and
* **persist** skips any splitter that has never reached that state, so an
  unvisited page can never overwrite what the user saved.
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QSplitter, QWidget

from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.layout_state import clamp_restored_sizes

log = logging.getLogger(__name__)

# Dragging a handle emits splitterMoved continuously. Coalesce the burst into one
# write instead of rewriting the whole settings file per pixel.
_WRITE_DEBOUNCE_MS = 400


class SplitterPersistence(QObject):
    """Restores saved pane sizes on first show and writes changes back, debounced."""

    def __init__(self, settings_service: AppSettingsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings_service
        self._splitters: dict[str, QSplitter] = {}
        # Splitters that have had real geometry at least once. Both the restore
        # and the flush are gated on membership here — see the module docstring.
        self._laid_out: set[str] = set()
        # Each page's own designed proportions, captured at real size just before
        # the first restore overwrites them. This is what "Reset layout" restores;
        # inventing an even split instead produced a layout the app never ships
        # (Logs is 820:320, Controls 300:900).
        self._defaults: dict[str, list[int]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_WRITE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)

    def adopt(self, root: QWidget) -> int:
        """Track every named splitter under *root*. Returns how many were found.

        Pass the **window**, not a splitter: ``findChildren`` returns a widget's
        descendants and never the widget itself.
        """
        for splitter in root.findChildren(QSplitter):
            name = splitter.objectName()
            if not name or name in self._splitters:
                # An unnamed splitter has no stable key, so its sizes would collide
                # with the next unnamed one. Skip rather than persist nonsense.
                continue
            self._splitters[name] = splitter
            splitter.splitterMoved.connect(self._on_moved)
            splitter.installEventFilter(self)
        return len(self._splitters)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            name = obj.objectName()
            if name in self._splitters and name not in self._laid_out:
                # Show arrives *before* the layout pass assigns geometry, so defer
                # a turn; until then sizes() is still the phantom.
                QTimer.singleShot(0, lambda n=name: self._restore_one(n))
        return super().eventFilter(obj, event)

    def _restore_one(self, name: str) -> None:
        splitter = self._splitters.get(name)
        if splitter is None or name in self._laid_out:
            return
        try:
            current = list(splitter.sizes())
        except RuntimeError:  # underlying C++ widget already destroyed
            return
        if sum(current) <= 0:
            return  # still not laid out; a later show will try again
        self._laid_out.add(name)
        self._defaults.setdefault(name, current)
        sizes = clamp_restored_sizes(self._settings.settings.splitter_sizes.get(name), current)
        if sizes is not None:
            with contextlib.suppress(RuntimeError):
                splitter.setSizes(sizes)

    def restore_all(self) -> None:
        """Restore every splitter that currently has real geometry."""
        for name in list(self._splitters):
            self._restore_one(name)

    def _on_moved(self, _pos: int, _index: int) -> None:
        # A drag is proof of real geometry even if no Show was observed (headless
        # tests), so it also arms the splitter for persistence.
        sender = self.sender()
        if sender is not None and sender.objectName():
            name = sender.objectName()
            self._laid_out.add(name)
            self._defaults.setdefault(name, list(sender.sizes()))
        self._timer.start()

    def _flush(self) -> None:
        current = dict(self._settings.settings.splitter_sizes)
        changed = False
        for name in self._laid_out:
            splitter = self._splitters.get(name)
            if splitter is None:
                continue
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
        """Forget every saved layout and put the panes back to the page defaults.

        The other half of the DEC-245 bargain: the clamp stops a pane coming back
        unusable, this is the way out for a layout the user simply dislikes. It
        restores what the page's own constructor asked for — a control labelled
        "Reset" that produced an even split would be showing an arrangement the
        application has never shipped.
        """
        # Stop the debounce first. A drag within the last 400 ms leaves a pending
        # _flush that would fire straight after this and write all nine entries
        # back, so "forget every saved layout" would end with a full map.
        self._timer.stop()
        self._settings.update(splitter_sizes={})
        for name in self._laid_out:
            splitter = self._splitters.get(name)
            default = self._defaults.get(name)
            if splitter is None or not default:
                continue
            with contextlib.suppress(RuntimeError):
                # Through the clamp so a window resized since first show gets the
                # default rescaled to its current total, not stale pixels.
                splitter.setSizes(clamp_restored_sizes(default, splitter.sizes()) or default)

    def stop(self) -> None:
        """Flush and stop the timer (call from closeEvent).

        Flushes unconditionally rather than only when the timer is pending: a
        drag that ended just as the window closed can leave the sizes changed
        with the timer already fired-and-cleared, and ``_flush`` is a no-op when
        nothing actually differs.
        """
        self._timer.stop()
        self._flush()
