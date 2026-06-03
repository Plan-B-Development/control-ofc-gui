"""Tests for the readiness verdict, "To fix" guidance, report, and combo arrow (DEC-113)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QTextBrowser

from control_ofc.api.models import (
    AcpiConflictInfo,
    BoardInfo,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ModuleCollisionInfo,
    ThermalSafetyInfo,
)
from control_ofc.ui.widgets.readiness_report import (
    ReadinessReportDialog,
    board_identity_line,
    build_fix_guidance_html,
    build_readiness_report_html,
    chip_rows,
    detect_readiness_problems,
    header_summary_line,
    module_rows,
    readiness_verdict,
    thermal_line,
)


def _healthy(**ov) -> HardwareDiagnosticsResult:
    defaults = dict(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(
                    chip_name="nct6779",
                    expected_driver="nct6775",
                    in_mainline_kernel=True,
                    header_count=5,
                ),
            ],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor="", name="Generic"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
    )
    defaults.update(ov)
    return HardwareDiagnosticsResult(**defaults)


class TestVerdict:
    def test_healthy_is_success(self):
        text, cls = readiness_verdict(_healthy())
        assert cls == "SuccessChip"
        assert "System ready" in text
        assert "5" in text

    def test_all_readonly_is_problem(self):
        text, cls = readiness_verdict(
            _healthy(hwmon=HwmonDiagnostics(total_headers=3, writable_headers=0))
        )
        assert cls in ("WarningChip", "CriticalChip")
        assert "attention" in text

    def test_singular_grammar(self):
        # Exactly one problem → "1 issue needs attention". Chips present (so
        # 'no_chips' does not also fire), but all headers read-only.
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
                ],
                total_headers=3,
                writable_headers=0,
            )
        )
        problems = detect_readiness_problems(diag)
        assert len(problems) == 1
        text, _ = readiness_verdict(diag)
        assert "1 issue needs attention" in text

    def test_critical_revert_makes_verdict_critical(self):
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
                ],
                total_headers=5,
                writable_headers=5,
                enable_revert_counts={"pwm1": 25},
            )
        )
        _, cls = readiness_verdict(diag)
        assert cls == "CriticalChip"


class TestDetectProblems:
    def test_info_quirk_not_counted(self):
        # ASUS + NCT6798 yields an info-level asus_ec_sensors quirk; it must
        # NOT register as a problem needing attention.
        diag = _healthy(
            board=BoardInfo(vendor="ASUS", name="ProArt X670"),
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="nct6798", expected_driver="nct6775", header_count=5)
                ],
                total_headers=5,
                writable_headers=5,
            ),
        )
        keys = {p["key"] for p in detect_readiness_problems(diag)}
        assert "vendor_quirk" not in keys

    def test_acpi_detected(self):
        diag = _healthy(
            acpi_conflicts=[
                AcpiConflictInfo(io_range="0x290", claimed_by="ACPI", conflicts_with_driver="it87")
            ]
        )
        keys = {p["key"] for p in detect_readiness_problems(diag)}
        assert "acpi" in keys

    def test_module_collision_is_critical(self):
        diag = _healthy(
            module_collisions=[
                ModuleCollisionInfo(
                    module_a="nct6687",
                    module_b="nct6775",
                    severity="critical",
                    summary="race",
                    remediation="blacklist one",
                )
            ]
        )
        problems = detect_readiness_problems(diag)
        coll = [p for p in problems if p["key"] == "module_collision"]
        assert coll and coll[0]["severity"] == "critical"

    def test_gpu_ppfeaturemask_detected(self):
        diag = _healthy(
            gpu=GpuDiagnosticsInfo(
                pci_bdf="0000:03:00.0",
                model_name="9070XT",
                fan_control_method="read_only",
                ppfeaturemask="0xabcd",
                ppfeaturemask_bit14_set=False,
            )
        )
        keys = {p["key"] for p in detect_readiness_problems(diag)}
        assert "gpu_ppfeaturemask" in keys


class TestFixGuidance:
    def test_none_when_healthy(self):
        assert build_fix_guidance_html(_healthy()) is None

    def test_has_disclaimer_and_link_when_problem(self):
        diag = _healthy(hwmon=HwmonDiagnostics(total_headers=3, writable_headers=0))
        html = build_fix_guidance_html(diag)
        assert html is not None
        assert "To fix" in html
        assert "at your own risk" in html  # disclaimer
        assert 'href="' in html  # clickable link
        assert "color:" in html  # link has an explicit colour for contrast


class TestReport:
    def test_contains_sections_and_links(self):
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="it8689", expected_driver="it87", header_count=2)
                ],
                total_headers=2,
                writable_headers=0,
            )
        )
        html = build_readiness_report_html(diag)
        assert "Summary" in html
        assert "Detected hardware" in html
        assert "To fix" in html
        assert 'href="' in html

    def test_escapes_daemon_strings(self):
        # DEC-106: a hostile chip name must not inject markup into the report.
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="<script>evil</script>",
                        expected_driver="it87",
                        header_count=1,
                    )
                ],
                total_headers=1,
                writable_headers=1,
            )
        )
        html = build_readiness_report_html(diag)
        assert "<script>evil</script>" not in html
        assert "&lt;script&gt;" in html


class TestSharedFormatters:
    """DEC-115: the card and the report build their section bodies from one set
    of pure formatters, so the report regained the chip Status + module Mainline
    columns it had silently dropped."""

    def test_chip_rows_fields(self):
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="it8689",
                        expected_driver="it87",
                        in_mainline_kernel=False,
                        header_count=2,
                    )
                ],
                total_headers=2,
                writable_headers=2,
            ),
            kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        )
        rows = chip_rows(diag)
        assert len(rows) == 1
        r = rows[0]
        assert (r.chip, r.driver, r.mainline, r.headers) == (
            "it8689",
            "it87",
            "No (out-of-tree)",
            "2",
        )
        assert r.status  # format_driver_status produced a status string

    def test_module_rows_fields(self):
        diag = _healthy(
            kernel_modules=[
                KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True),
                KernelModuleInfo(name="it87", loaded=False, in_mainline=False),
            ]
        )
        rows = module_rows(diag)
        assert (rows[0].name, rows[0].loaded, rows[0].mainline) == ("nct6775", "Loaded", "Yes")
        assert (rows[1].name, rows[1].loaded, rows[1].mainline) == ("it87", "Not loaded", "No")

    def test_board_identity_and_summary(self):
        diag = _healthy(board=BoardInfo(vendor="ASUS", name="X670E", bios_version="1654"))
        assert board_identity_line(diag) == "ASUS — X670E — BIOS 1654"
        assert header_summary_line(diag.hwmon) == "5 PWM header(s) detected, 5 writable"

    def test_board_identity_none_when_empty(self):
        assert board_identity_line(_healthy(board=BoardInfo(vendor="", name=""))) is None

    def test_thermal_line(self):
        diag = _healthy(
            thermal_safety=ThermalSafetyInfo(
                state="normal",
                cpu_sensor_found=True,
                emergency_threshold_c=105.0,
                release_threshold_c=80.0,
            )
        )
        line = thermal_line(diag.thermal_safety)
        assert "normal" in line
        assert "105°C" in line
        assert "80°C" in line

    def test_report_regains_status_and_mainline_columns(self):
        diag = _healthy(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="nct6779",
                        expected_driver="nct6775",
                        in_mainline_kernel=True,
                        header_count=5,
                    )
                ],
                total_headers=5,
                writable_headers=5,
            ),
            kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        )
        html = build_readiness_report_html(diag)
        assert ">Status<" in html  # chip table regained its Status column
        assert html.count(">Mainline<") == 2  # one Mainline header per table


class TestDialog:
    def test_dialog_basics(self, qtbot):
        dlg = ReadinessReportDialog("<div>hello</div>")
        qtbot.addWidget(dlg)
        assert dlg.objectName() == "ReadinessReport_Dialog"
        browser = dlg.findChild(QTextBrowser, "ReadinessReport_Browser")
        assert browser is not None
        assert browser.openExternalLinks() is True
        assert "hello" in browser.toPlainText()

    def test_set_html_replaces_content(self, qtbot):
        dlg = ReadinessReportDialog("<div>first</div>")
        qtbot.addWidget(dlg)
        dlg.set_html("<div>second</div>")
        browser = dlg.findChild(QTextBrowser, "ReadinessReport_Browser")
        assert "second" in browser.toPlainText()
        assert "first" not in browser.toPlainText()


class TestComboArrow:
    def test_svg_generated_with_color(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from control_ofc.ui.theme import combo_arrow_svg_path

        path = combo_arrow_svg_path("#abcdef")
        assert path is not None
        content = Path(path).read_text(encoding="utf-8")
        assert "<svg" in content
        assert "#abcdef" in content  # colour is baked into the stroke

    def test_stylesheet_includes_down_arrow_rule(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from control_ofc.ui.theme import build_stylesheet, default_dark_theme

        css = build_stylesheet(default_dark_theme())
        assert "QComboBox::down-arrow" in css
        assert "image: url(" in css

    def test_returns_none_when_cache_unwritable(self, tmp_path, monkeypatch):
        # Point the cache root at a regular file so mkdir() raises OSError;
        # the helper must degrade gracefully rather than crash theming.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x")
        monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
        from control_ofc.ui.theme import combo_arrow_svg_path

        assert combo_arrow_svg_path("#123456") is None
