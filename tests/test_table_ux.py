"""Fan-row tooltip content (migrated from the retired Diagnostics Fans-tab table).

The old ``test_table_ux.py`` asserted table-widget UX (Interactive column resize,
the hardware-tables splitter, header tooltips, guidance doc-links) on the
now-retired Diagnostics page. Those tables moved to the live pages over Qt-free
view-models, so the behaviour is owned elsewhere:

  * fan freshness / control-method / PWM-only synthesis →
    ``tests/test_overview_view.py`` (``build_fan_rows``) +
    ``tests/test_overview_page.py``;
  * chip/module registry table →
    ``tests/test_system_state_view.py::test_build_registry_rows_unifies_chips_and_modules``
    + ``tests/test_system_state_page.py::test_registry_table_has_status_pills``;
  * guidance doc-links → chip tooltips
    (``tests/test_system_state_view.py::test_registry_chip_tooltip_from_guidance``)
    and issue-card doc buttons
    (``tests/test_system_state_page.py::test_doc_link_button_opens_url``).

The one behaviour those live tests do not pin is the *content* of the per-fan row
tooltip (ID / chip+driver / PWM-only wording), so it is re-vehicled here directly
onto the ``overview_view.build_fan_rows`` builder that now produces it.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.overview_view import build_fan_rows


def test_fan_row_tooltip_contains_id():
    rows = build_fan_rows(
        [FanReading(id="fan1", source="openfan", rpm=1200, age_ms=100)], [], None, lambda x: x
    )
    assert "fan1" in rows[0].row_tooltip


def test_hwmon_fan_row_tooltip_shows_chip_and_driver():
    headers = [HwmonHeader(id="hwmon0_fan1", chip_name="nct6798", is_writable=True)]
    rows = build_fan_rows(
        [FanReading(id="hwmon0_fan1", source="hwmon", rpm=800, age_ms=50)],
        headers,
        None,
        lambda x: x,
    )
    assert "nct6798" in rows[0].row_tooltip  # chip name
    assert "nct6775" in rows[0].row_tooltip  # resolved driver name via lookup_chip_guidance


def test_pwm_only_row_tooltip_says_no_rpm_and_read_only():
    rows = build_fan_rows(
        [],
        [HwmonHeader(id="hwmon0_pwm3", chip_name="nct6798", is_writable=False)],
        None,
        lambda x: x,
    )
    assert rows[0].is_pwm_only
    assert "PWM output only" in rows[0].row_tooltip
    assert "read-only" in rows[0].row_tooltip
