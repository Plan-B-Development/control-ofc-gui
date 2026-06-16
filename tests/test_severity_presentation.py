"""Pure-function tests for the advisory severity presentation layer (DEC-158).

Covers the shared mapping that drives both the inline Troubleshooting panel and
the pop-out report — badge word / glyph / colour-class / weight, ordering rank,
default open state, unknown-severity degradation — plus the prose-vs-bullet
detail formatter and the shared ``advisory_rows`` dedupe + ordering. No Qt.

These are the load-bearing decisions behind "each severity is noticeable as its
own type", so they are pinned independently of the widget rendering path.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
)
from control_ofc.ui.hwmon_guidance import advisory_detail_html, severity_display
from control_ofc.ui.widgets.readiness_report import advisory_rows


class TestSeverityDisplay:
    def test_known_tiers_map_to_distinct_colour_classes(self):
        # The core fix: each tier gets its OWN colour class — INFO is not orange.
        classes = {s: severity_display(s).css_class for s in ("critical", "high", "medium", "info")}
        assert classes == {
            "critical": "CriticalChip",
            "high": "WarningChip",
            "medium": "CautionChip",
            "info": "InfoChip",
        }
        assert len(set(classes.values())) == 4  # all four genuinely distinct

    def test_words_are_uppercase_tier_names(self):
        assert severity_display("critical").word == "CRITICAL"
        assert severity_display("high").word == "HIGH"
        assert severity_display("medium").word == "MEDIUM"
        assert severity_display("info").word == "INFO"

    def test_rank_orders_most_severe_highest(self):
        ranks = [severity_display(s).rank for s in ("info", "medium", "high", "critical")]
        assert ranks == sorted(ranks)  # strictly increasing severity
        assert len(set(ranks)) == 4

    def test_default_expanded_only_for_high_and_critical(self):
        assert severity_display("critical").default_expanded is True
        assert severity_display("high").default_expanded is True
        assert severity_display("medium").default_expanded is False
        assert severity_display("info").default_expanded is False

    def test_badge_bold_only_for_high_and_critical(self):
        # Weight is the non-colour cue that keeps the hierarchy readable even
        # though amber (MEDIUM) is brighter than orange (HIGH).
        assert severity_display("critical").bold is True
        assert severity_display("high").bold is True
        assert severity_display("medium").bold is False
        assert severity_display("info").bold is False

    def test_each_tier_has_a_glyph(self):
        for s in ("critical", "high", "medium", "info"):
            assert severity_display(s).glyph.strip(), f"{s} has no glyph"

    def test_warn_shares_high_presentation_but_keeps_its_word(self):
        # detect_readiness_problems emits "warn"; it must style like HIGH but
        # still read "WARN" in the issue checklist.
        warn = severity_display("warn")
        assert warn.css_class == "WarningChip"
        assert warn.word == "WARN"

    @pytest.mark.parametrize("unknown", ["low", "bogus", "", "   "])
    def test_unknown_severity_degrades_to_calm_info_treatment(self, unknown):
        # D1: unknown / not-yet-emitted severities get the calm INFO treatment
        # rather than masquerading as a warning.
        disp = severity_display(unknown)
        assert disp.css_class == "InfoChip"
        assert disp.default_expanded is False
        assert disp.bold is False

    def test_unknown_severity_keeps_its_own_word(self):
        # A future "low" must not be mislabelled "INFO".
        assert severity_display("low").word == "LOW"

    def test_case_insensitive(self):
        assert severity_display("CRITICAL").css_class == "CriticalChip"
        assert severity_display("Medium").css_class == "CautionChip"


class TestAdvisoryDetailHtml:
    def test_empty_returns_empty(self):
        assert advisory_detail_html([]) == ""
        assert advisory_detail_html(["", "   "]) == ""

    def test_single_item_is_prose_not_a_bullet(self):
        out = advisory_detail_html(["Disable Smart Fan in BIOS."])
        assert out == "Disable Smart Fan in BIOS."
        assert "&#8226;" not in out

    def test_two_items_are_prose_paragraphs(self):
        out = advisory_detail_html(["First sentence.", "Second sentence."])
        assert "&#8226;" not in out
        assert "<br><br>" in out  # paragraph break, not a list

    def test_three_short_items_become_bullets(self):
        out = advisory_detail_html(["alpha", "beta", "gamma"])
        assert out.count("&#8226;") == 3

    def test_three_long_items_stay_prose(self):
        long = "x" * 120
        out = advisory_detail_html([long, long, long])
        assert "&#8226;" not in out  # too long to read as a scannable list

    def test_items_are_html_escaped(self):
        out = advisory_detail_html(["a < b & c > d"])
        assert "&lt;" in out and "&amp;" in out and "&gt;" in out
        assert "< b" not in out


class TestAdvisoryRows:
    def _diag(self, vendor: str, chips: list[str]) -> HardwareDiagnosticsResult:
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name=c, expected_driver="it87", header_count=5)
                    for c in chips
                ],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor=vendor, name=""),
        )

    def test_empty_when_no_vendor_match(self):
        assert advisory_rows(self._diag("Unknown Vendor", ["it8696"])) == []

    def test_sorted_most_severe_first(self):
        rows = advisory_rows(self._diag("Gigabyte Technology Co., Ltd.", ["it8696"]))
        assert rows  # Gigabyte IT8696E matches both a HIGH and a MEDIUM quirk
        ranks = [severity_display(q.severity).rank for q in rows]
        assert ranks == sorted(ranks, reverse=True)

    def test_deduped_by_summary(self):
        # The same chip listed twice must not double the advisories.
        once = advisory_rows(self._diag("Gigabyte Technology Co., Ltd.", ["it8696"]))
        twice = advisory_rows(self._diag("Gigabyte Technology Co., Ltd.", ["it8696", "it8696"]))
        assert once and len(once) == len(twice)
