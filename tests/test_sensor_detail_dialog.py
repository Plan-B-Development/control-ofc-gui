"""Tests for :mod:`control_ofc.ui.widgets.sensor_detail_dialog` (DEC-117).

Covers the rich-text rendering pipeline (identity / state / classification /
thresholds / driver doc link), kernel.org URL resolution for the major chip
families we target, and the DEC-106 HTML-escape discipline against hostile
daemon strings.
"""

from __future__ import annotations

from html import escape

from control_ofc.api.models import (
    BoardInfo,
    SensorReading,
    SensorThresholds,
)
from control_ofc.ui.sensor_knowledge import kernel_doc_url_for_chip, temp_type_label
from control_ofc.ui.widgets.sensor_detail_dialog import (
    SensorDetailDialog,
    build_sensor_detail_html,
)


def _reading(**kw) -> SensorReading:
    return SensorReading(
        id=kw.get("id", "hwmon:k10temp:0000:00:18.3:Tctl"),
        kind=kw.get("kind", "cpu_temp"),
        label=kw.get("label", "Tctl"),
        value_c=kw.get("value_c", 55.0),
        source=kw.get("source", "hwmon"),
        age_ms=kw.get("age_ms", 250),
        rate_c_per_s=kw.get("rate_c_per_s"),
        session_min_c=kw.get("session_min_c"),
        session_max_c=kw.get("session_max_c"),
        chip_name=kw.get("chip_name", "k10temp"),
        temp_type=kw.get("temp_type"),
        thresholds=kw.get("thresholds"),
    )


# ─── temp_type_label helper ──────────────────────────────────────────────


class TestTempTypeLabel:
    def test_amd_tsi_label(self):
        assert "AMD TSI" in temp_type_label(5)

    def test_intel_peci_label(self):
        assert "Intel PECI" in temp_type_label(6)

    def test_thermistor_label(self):
        assert "thermistor" in temp_type_label(4)

    def test_none_renders_emdash(self):
        assert temp_type_label(None) == "—"

    def test_unknown_type_renders_int(self):
        assert "99" in temp_type_label(99)


# ─── kernel_doc_url_for_chip ─────────────────────────────────────────────


class TestKernelDocUrl:
    def test_k10temp_url(self):
        assert kernel_doc_url_for_chip("k10temp") == "https://docs.kernel.org/hwmon/k10temp.html"

    def test_coretemp_url(self):
        assert kernel_doc_url_for_chip("coretemp") == "https://docs.kernel.org/hwmon/coretemp.html"

    def test_nct6798_falls_into_nct6775_family(self):
        # nct67[0-9]{2} all map to the nct6775 family doc page.
        assert "nct" in kernel_doc_url_for_chip("nct6798")

    def test_it8689_falls_into_it87_family(self):
        assert "it87" in kernel_doc_url_for_chip("it8689")

    def test_unknown_chip_returns_none(self):
        # No URL for an invented chip — caller suppresses the link.
        assert kernel_doc_url_for_chip("totally_unknown_chip") is None

    def test_empty_chip_name_returns_none(self):
        assert kernel_doc_url_for_chip("") is None


# ─── HTML rendering ──────────────────────────────────────────────────────


class TestBuildSensorDetailHtml:
    def test_renders_sensor_id_and_label(self):
        html = build_sensor_detail_html(
            _reading(id="hwmon:k10temp:Tctl", label="Tctl"),
            BoardInfo(vendor="ASRock", name="X670E Steel Legend", bios_version="3.20"),
        )
        # The full ID is shown verbatim, the label appears in the header.
        assert "hwmon:k10temp:Tctl" in html
        assert "Tctl" in html

    def test_renders_classification_notes(self):
        html = build_sensor_detail_html(
            _reading(id="hwmon:k10temp:Tctl", label="Tctl"),
            None,
        )
        # Tctl classification has a "Used by firmware for cooling decisions" note.
        assert "cooling" in html.lower()

    def test_thresholds_section_when_present(self):
        sensor = _reading(
            thresholds=SensorThresholds(crit_c=105.0, max_c=95.0, crit_alarm=False),
        )
        html = build_sensor_detail_html(sensor, None)
        assert "105.0" in html
        assert "95.0" in html
        assert "Critical" in html

    def test_thresholds_section_emits_placeholder_when_missing(self):
        sensor = _reading(thresholds=None)
        html = build_sensor_detail_html(sensor, None)
        # Section header still appears so the user knows we looked; body
        # explains why nothing is shown rather than appearing as a phantom gap.
        assert "Thresholds" in html
        assert "did not report" in html.lower()

    def test_thresholds_only_renders_supplied_fields(self):
        """Sensor exposes only crit_c — the others must not appear as
        '—' filler rows. DEC-117 §D8 'never invent absent values'."""
        sensor = _reading(thresholds=SensorThresholds(crit_c=105.0))
        html = build_sensor_detail_html(sensor, None)
        assert "Critical" in html
        assert "Emergency" not in html
        assert "Lower critical" not in html

    def test_headroom_to_crit_rendered(self):
        sensor = _reading(value_c=80.0, thresholds=SensorThresholds(crit_c=105.0))
        html = build_sensor_detail_html(sensor, None)
        # 25 °C headroom: should report "below crit"
        assert "below crit" in html.lower()

    def test_driver_doc_link_rendered(self):
        html = build_sensor_detail_html(_reading(chip_name="k10temp"), None)
        assert "docs.kernel.org/hwmon/k10temp.html" in html

    def test_unknown_chip_omits_driver_doc_link(self):
        html = build_sensor_detail_html(_reading(chip_name="bogus_unknown"), None)
        assert "docs.kernel.org" not in html

    def test_board_context_rendered_when_supplied(self):
        html = build_sensor_detail_html(
            _reading(),
            BoardInfo(vendor="ASRock", name="X670E Steel Legend", bios_version="3.20"),
        )
        assert "ASRock" in html
        assert "X670E Steel Legend" in html
        assert "3.20" in html


# ─── DEC-106 HTML-escape discipline ──────────────────────────────────────


class TestHtmlEscaping:
    def test_hostile_chip_name_is_escaped(self):
        """A chip name containing HTML-special characters must not break out
        of its cell — DEC-106 pattern, mirrored from readiness_report."""
        hostile = '<script>alert("xss")</script>'
        html = build_sensor_detail_html(
            _reading(chip_name=hostile, id=hostile, label=hostile), None
        )
        assert "<script>" not in html
        # The escaped form must appear instead.
        assert escape(hostile) in html

    def test_hostile_board_vendor_is_escaped(self):
        hostile = 'ASUS"><script>evil()</script>'
        html = build_sensor_detail_html(
            _reading(),
            BoardInfo(vendor=hostile, name="Z790-A", bios_version="1.0"),
        )
        assert "<script>" not in html
        assert escape(hostile) in html


# ─── Dialog widget construction ──────────────────────────────────────────


class TestSensorDetailDialog:
    def test_dialog_titles_with_sensor_label(self, qtbot):
        sensor = _reading(label="Tctl")
        dlg = SensorDetailDialog(sensor, None)
        qtbot.addWidget(dlg)
        assert "Tctl" in dlg.windowTitle()

    def test_dialog_object_names_for_test_lookup(self, qtbot):
        dlg = SensorDetailDialog(_reading(), None)
        qtbot.addWidget(dlg)
        assert dlg.objectName() == "Diagnostics_SensorDetail_Dialog"
        assert dlg._browser.objectName() == "Diagnostics_SensorDetail_Browser"

    def test_set_sensor_updates_in_place(self, qtbot):
        dlg = SensorDetailDialog(_reading(label="Tctl"), None)
        qtbot.addWidget(dlg)
        dlg.set_sensor(_reading(label="Tccd1"), None)
        assert "Tccd1" in dlg.windowTitle()
