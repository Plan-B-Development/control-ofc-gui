"""DEC-287: an orphaned top-level widget must not survive its own test.

The suite used to end with 420 orphaned top-level trees holding 20,412 widgets —
widgets built with no parent and never passed to ``qtbot.addWidget``, so nothing
owned them and nothing deleted them. That is not merely untidy: every
application-wide Qt operation re-polishes every live widget, so the residue made
one ``apply_theme`` call cost 507ms, and it was charged to whichever test ran
last. A theme test paying it ten times blew its timeout and blocked a release.

``tests/conftest.py::_reap_orphaned_top_levels`` fixes that. This pins it,
because the fixture is autouse and invisible: delete it and every other test in
the suite still passes, which is exactly the shape of an untested rule.

**The two tests below are order-dependent by construction** — the property is
"does it survive into the *next* test", which cannot be observed from inside one.
pytest preserves definition order within a file and no ordering plugin is
installed (``pytest-randomly`` is deliberately absent), so this is deterministic.
Keep them adjacent and in this order.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QFrame

# ``qapp`` (pytest-qt) is requested purely to guarantee a QApplication exists —
# constructing a QWidget without one aborts the interpreter. ``qtbot`` is
# deliberately NOT used to register the probe widget: registering it would let
# pytest-qt clean it up and the reaper under test would never be exercised.

#: Held deliberately, to model how the real leak survives. An unparented QWidget
#: is owned by its Python wrapper, so a local would be collected at test exit and
#: take the C++ object with it — proving nothing. The 420 real orphans survived
#: precisely because something (a reference cycle, a signal connection) kept them
#: reachable, so the probe has to do the same or it tests the wrong mechanism.
_kept: list[QFrame] = []

_MARKER = "Dec287_OrphanProbe"


def _orphans() -> list[QFrame]:
    """Live top-level widgets carrying our marker.

    Reads ``topLevelWidgets()`` rather than ``_kept``: it lists only widgets whose
    **C++** side is alive, which is the thing being asserted. A reaped widget
    leaves its Python wrapper behind in ``_kept`` and simply stops appearing here.
    """
    return [w for w in QApplication.topLevelWidgets() if w.objectName() == _MARKER]


class TestOrphanedTopLevelsAreReaped:
    def test_an_unregistered_top_level_widget_is_created(self, qapp):
        """Assert the PRESENCE first, so the absence below cannot pass vacuously.

        `CLAUDE.md § Hard-won lessons`: "a test asserting an absence must first
        assert the presence" — a probe that never created the widget would sail
        through the next test having demonstrated nothing.
        """
        widget = QFrame()
        widget.setObjectName(_MARKER)
        _kept.append(widget)

        assert _orphans(), (
            "the probe widget should be a live top-level widget at this point — "
            "if it is not, the next test proves nothing"
        )

    def test_the_orphan_does_not_survive_into_the_next_test(self, qapp):
        """The reaping itself. Fails if `_reap_orphaned_top_levels` is removed."""
        survivors = _orphans()
        _kept.clear()
        assert not survivors, (
            f"{len(survivors)} orphaned top-level widget(s) survived the test that "
            "created them. `tests/conftest.py::_reap_orphaned_top_levels` should "
            "have posted deleteLater() for them and `_flush_deferred_deletes` "
            "should have dispatched it. If that fixture was removed or reordered "
            "so it tears down AFTER the flush, the suite silently goes back to "
            "accumulating widgets and every application-wide Qt call gets slower."
        )


def test_the_suite_is_not_accumulating_top_level_widgets(qapp):
    """A standing bound on the population, independent of the probe above.

    The regression this guards is gradual, not a single event: without reaping the
    count climbs all run and only the last tests feel it. Asserting a bound here
    catches a reaper that silently stops working, in whichever file happens to run
    after it. The threshold is deliberately loose — this pins "not accumulating
    hundreds", not an exact figure, because an exact one would be a portability
    trap of its own.
    """
    tops = QApplication.topLevelWidgets()
    assert len(tops) < 50, (
        f"{len(tops)} top-level widgets are alive. The suite ended with 420 before "
        "DEC-287 and 0 after; anything approaching the old figure means orphans are "
        "accumulating again and application-wide Qt operations are paying for them."
    )
