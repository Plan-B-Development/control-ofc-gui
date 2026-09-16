"""`OFN-m`: an unrecognised fan source must not inherit the OpenFan label.

The Sensors-rail chart grouping filed fans by an else-fallback — GPU sources to
``fans_gpu``, anything containing "hwmon" to ``fans_hwmon``, and *everything
else* to ``fans_openfan``. Benign while ``openfan`` is the only remaining
source, and a confident wrong answer the moment a fourth one exists: the new
hardware would be labelled as OpenFan rather than failing visibly. Same family
as `CLAUDE.md`'s DEC-334 registry lesson — a lookup that silently absorbs
unknown ids produces a confident wrong answer instead of an error.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading
from control_ofc.services.app_state import AppState
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.sensor_series_panel import (
    _GROUP_LABELS,
    _GROUP_ORDER,
    SensorSeriesPanel,
)

UNKNOWN_SOURCE = "some-future-bus"
OPENFAN_ID = "openfan:ch00"
UNKNOWN_ID = "mystery:0"


def _panel(qtbot, fans: list[FanReading]) -> SensorSeriesPanel:
    """A panel showing exactly ``fans``.

    The returned widget MUST be bound by the caller and asserted on: dropping
    the last Python reference lets shiboken collect the wrapper and silently
    drops every bound-method connection made in ``__init__`` (DEC-356 / `DASH-h`).
    """
    state = AppState()
    state.fans = fans
    panel = SensorSeriesPanel(SeriesSelectionModel(), state=state)
    qtbot.addWidget(panel)
    panel.update_fans(fans)
    # Presence before absence: the DEC-047 displayability filter drops a fan
    # with no evidence of activity, and every assertion below would then be
    # about an empty tree.
    assert set(panel._fan_items) == {f.id for f in fans}
    return panel


def _group_headers(panel: SensorSeriesPanel) -> set[str]:
    """The realised top-level header text, not the map it was looked up from."""
    tree = panel._tree
    return {tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())}


def test_an_unrecognised_fan_source_is_not_labelled_openfan(qtbot):
    """The arm that discriminates: only the new path can produce this group.

    With the fix removed the fan lands in ``fans_openfan`` and the header reads
    "Fans — OpenFan" — which is the mislabel, and is what this fails on.
    """
    panel = _panel(qtbot, [FanReading(id=UNKNOWN_ID, source=UNKNOWN_SOURCE, rpm=1200)])
    assert set(panel._group_items) == {"fans_other"}
    headers = _group_headers(panel)
    assert _GROUP_LABELS["fans_other"] in headers
    assert _GROUP_LABELS["fans_openfan"] not in headers


def test_a_real_openfan_fan_still_files_under_openfan(qtbot):
    """The opposite arm — without it a predicate stuck at ``fans_other`` passes."""
    panel = _panel(qtbot, [FanReading(id=OPENFAN_ID, source="openfan", rpm=1182)])
    assert set(panel._group_items) == {"fans_openfan"}
    assert _GROUP_LABELS["fans_openfan"] in _group_headers(panel)
    assert _GROUP_LABELS["fans_other"] not in _group_headers(panel)


def test_the_two_sources_are_separated_in_one_pass(qtbot):
    """Both present together, which is the state the mislabel actually merges."""
    panel = _panel(
        qtbot,
        [
            FanReading(id=OPENFAN_ID, source="openfan", rpm=1182),
            FanReading(id=UNKNOWN_ID, source=UNKNOWN_SOURCE, rpm=1200),
        ],
    )
    assert panel._fan_items[OPENFAN_ID].parent() is panel._group_items["fans_openfan"]
    assert panel._fan_items[UNKNOWN_ID].parent() is panel._group_items["fans_other"]


def test_the_other_group_is_registered_for_ordering_and_label(qtbot):
    """``_rebuild_fan_items`` indexes ``_GROUP_LABELS`` directly, and an
    unregistered key sorts to the end of the tree by the ``len(_GROUP_ORDER)``
    fallback — so registration is what makes the group appear where it says it
    will, rather than merely not crashing."""
    assert "fans_other" in _GROUP_ORDER
    assert _GROUP_ORDER.index("fans_other") > _GROUP_ORDER.index("fans_openfan")
    assert _GROUP_LABELS["fans_other"] != _GROUP_LABELS["fans_openfan"]
