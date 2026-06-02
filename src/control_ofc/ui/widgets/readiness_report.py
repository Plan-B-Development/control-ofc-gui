"""Hardware-readiness verdict, "To fix" guidance, and pop-out report (DEC-113).

Three things live here so the inline Fans-tab card and the pop-out window share
one source of truth (no drift):

* :func:`detect_readiness_problems` — the single problem-detection pass. Both
  the verdict banner and the "To fix" guidance derive from it.
* :func:`readiness_verdict` — the one-line status shown at the top of the card.
* :func:`build_fix_guidance_html` — GUI-authored "To fix" bullets (disclaimer +
  clickable doc links). Deliberately contains **no daemon-supplied strings**, so
  it is safe to render as rich text without the escaping dance DEC-106 requires.
* :func:`build_readiness_report_html` + :class:`ReadinessReportDialog` — the full
  scrollable report shown in its own window; daemon strings ARE escaped here.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.hwmon_guidance import (
    REMEDIATION_DISCLAIMER,
    detect_module_conflicts,
    dual_chip_warning_html,
    lookup_vendor_quirks,
)
from control_ofc.ui.theme import active_theme

if TYPE_CHECKING:
    from control_ofc.api.models import HardwareDiagnosticsResult

_HW_COMPAT_URL = (
    "https://github.com/Plan-B-Development/control-ofc-gui/blob/main/"
    "docs/19_Hardware_Compatibility.md"
)
# Reclaim count at/above which BIOS interference is treated as critical
# (mirrors classify_reclaim_severity's HIGH bucket in diagnostics_page).
_RECLAIM_HIGH = 10


def _link(url: str, title: str) -> str:
    """Render a clickable anchor with an inline, theme-derived colour.

    The colour is set inline (not via palette/stylesheet) because both the
    inline ``QLabel`` and the pop-out ``QTextBrowser`` inherit the app-wide
    stylesheet, which overrides the palette Link role — inline style is the
    only reliably-applied path for readable link contrast.
    """
    return f'<a href="{url}" style="color:{active_theme().status_info}">{title}</a>'


def detect_readiness_problems(diag: HardwareDiagnosticsResult) -> list[dict]:
    """Return the detected readiness problems, in display order.

    Each problem is ``{key, label, fix, doc_url, doc_title, severity}`` where
    every string is GUI-authored (no daemon input). ``severity`` is ``"warn"``
    or ``"critical"``.
    """
    hw = diag.hwmon
    board = diag.board
    problems: list[dict] = []

    collisions = getattr(diag, "module_collisions", []) or []
    if collisions:
        problems.append(
            {
                "key": "module_collision",
                "label": "Driver module collision",
                "fix": (
                    "Two drivers are fighting for the same chip. Unload one and "
                    "blacklist it (see the per-pair remediation in the alert), "
                    "then reboot."
                ),
                "doc_url": "https://wiki.archlinux.org/title/Fan_speed_control",
                "doc_title": "Arch Wiki: Fan speed control",
                "severity": "critical",
            }
        )
    elif detect_module_conflicts([m.name for m in diag.kernel_modules if m.loaded]):
        problems.append(
            {
                "key": "module_conflict",
                "label": "Conflicting driver modules loaded",
                "fix": (
                    "Blacklist all but one of the conflicting modules in "
                    "/etc/modprobe.d/ and reboot so a single driver owns the chip."
                ),
                "doc_url": "https://wiki.archlinux.org/title/Fan_speed_control",
                "doc_title": "Arch Wiki: Fan speed control",
                "severity": "critical",
            }
        )

    detected = [c.chip_name for c in hw.chips_detected]
    if dual_chip_warning_html(board.name, list(diag.expected_chips), detected):
        problems.append(
            {
                "key": "dual_chip",
                "label": "Super-I/O chip not enumerated",
                "fix": (
                    "Create /etc/modprobe.d/it87.conf with "
                    "'options it87 mmio=on', avoid running sensors-detect after "
                    "boot, then reboot (full steps in the alert above)."
                ),
                "doc_url": "https://github.com/frankcrawford/it87/issues/70",
                "doc_title": "frankcrawford/it87 issue #70",
                "severity": "warn",
            }
        )

    quirks = []
    for chip in hw.chips_detected:
        quirks.extend(
            lookup_vendor_quirks(
                board.vendor,
                chip.chip_name,
                cpu_vendor=diag.cpu_vendor,
                board_name=board.name,
            )
        )
    # Info-level quirks are FYI enrichment notes (e.g. asus_ec_sensors), not
    # problems — they still show in the alert stack, but they must not make a
    # healthy board read as "needs attention".
    actionable_quirks = [q for q in quirks if q.severity != "info"]
    if actionable_quirks:
        problems.append(
            {
                "key": "vendor_quirk",
                "label": "Board/chip quirk detected",
                "fix": (
                    "Review the quirk notes above — most are addressed in BIOS "
                    "fan settings or by the chip's documented driver options."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": (
                    "critical"
                    if any(q.severity in ("critical", "high") for q in actionable_quirks)
                    else "warn"
                ),
            }
        )

    if diag.acpi_conflicts:
        has_it87 = any(c.conflicts_with_driver == "it87" for c in diag.acpi_conflicts)
        fix = (
            "Add 'options it87 ignore_resource_conflict=1' to "
            "/etc/modprobe.d/it87.conf (preferred for ITE chips), or add "
            "'acpi_enforce_resources=lax' to the kernel command line."
            if has_it87
            else "Add 'acpi_enforce_resources=lax' to the kernel command line, "
            "or disable hardware monitoring in BIOS."
        )
        problems.append(
            {
                "key": "acpi",
                "label": "ACPI I/O port conflict",
                "fix": fix,
                "doc_url": "https://wiki.archlinux.org/title/Lm_sensors",
                "doc_title": "Arch Wiki: lm_sensors",
                "severity": "warn",
            }
        )

    reverts = getattr(hw, "enable_revert_counts", None) or {}
    if reverts and max(reverts.values()) > 0:
        problems.append(
            {
                "key": "bios_revert",
                "label": "BIOS/EC reclaiming fan control",
                "fix": (
                    "Disable the BIOS's automatic fan control (Q-Fan / Smart Fan "
                    "/ Fan Xpert) for the affected headers, or set them to full "
                    "manual, then re-test."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": "critical" if max(reverts.values()) >= _RECLAIM_HIGH else "warn",
            }
        )

    gpu = diag.gpu
    if gpu and gpu.ppfeaturemask and not gpu.ppfeaturemask_bit14_set:
        problems.append(
            {
                "key": "gpu_ppfeaturemask",
                "label": "GPU fan control disabled (ppfeaturemask)",
                "fix": (
                    "Add 'amdgpu.ppfeaturemask=0xffffffff' to your kernel "
                    "command line and reboot to enable PMFW fan control."
                ),
                "doc_url": "https://wiki.archlinux.org/title/AMDGPU#Fan_control",
                "doc_title": "Arch Wiki: AMDGPU fan control",
                "severity": "warn",
            }
        )
    elif gpu and gpu.fan_control_method == "read_only" and not gpu.ppfeaturemask:
        problems.append(
            {
                "key": "gpu_readonly",
                "label": "GPU fan control unavailable",
                "fix": (
                    "RDNA3+ cards need 'amdgpu.ppfeaturemask=0xffffffff' on the "
                    "kernel command line; add it and reboot."
                ),
                "doc_url": "https://wiki.archlinux.org/title/AMDGPU#Fan_control",
                "doc_title": "Arch Wiki: AMDGPU fan control",
                "severity": "warn",
            }
        )

    if hw.total_headers > 0 and hw.writable_headers == 0:
        problems.append(
            {
                "key": "all_readonly",
                "label": "All PWM headers are read-only",
                "fix": (
                    "Check BIOS fan settings and confirm the correct hwmon "
                    "driver is loaded; run Test PWM Control to confirm."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": "warn",
            }
        )
    if len(hw.chips_detected) == 0:
        problems.append(
            {
                "key": "no_chips",
                "label": "No hwmon chips detected",
                "fix": (
                    "Motherboard fan control may require a kernel driver for "
                    "your Super-I/O chip — see the modules table and the guide."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": "warn",
            }
        )

    return problems


def readiness_verdict(diag: HardwareDiagnosticsResult) -> tuple[str, str]:
    """Return ``(verdict_text, css_class)`` for the readiness banner."""
    hw = diag.hwmon
    problems = detect_readiness_problems(diag)
    ts = diag.thermal_safety
    thermal = f"thermal safety {ts.state}" if ts and ts.state else "thermal safety unknown"
    if not problems:
        return (
            f"✓ System ready — {hw.total_headers} PWM header(s), "
            f"{hw.writable_headers} writable · {thermal}",
            "SuccessChip",
        )
    n = len(problems)
    phrase = "issue needs" if n == 1 else "issues need"
    critical = any(p["severity"] == "critical" for p in problems)
    cls = "CriticalChip" if critical else "WarningChip"
    return (
        f"⚠ {n} {phrase} attention — review the alerts below, "
        f"expand 'Guidance & documentation', or open the full report",
        cls,
    )


def build_fix_guidance_html(diag: HardwareDiagnosticsResult) -> str | None:
    """Return the "To fix" block (rich text), or ``None`` when nothing is wrong.

    GUI-authored content only — safe to render as rich text with external
    links enabled (no daemon strings are interpolated; DEC-106).
    """
    problems = detect_readiness_problems(diag)
    if not problems:
        return None
    parts = ["<b>To fix:</b>"]
    for p in problems:
        parts.append(
            f"&nbsp;&nbsp;• <b>{p['label']}</b> — {p['fix']} {_link(p['doc_url'], p['doc_title'])}"
        )
    parts.append(f"<br><i>⚠ {REMEDIATION_DISCLAIMER}</i>")
    return "<br>".join(parts)


def build_readiness_report_html(diag: HardwareDiagnosticsResult) -> str:
    """Build the full, self-contained HTML report for the pop-out window.

    Daemon-supplied strings are HTML-escaped; GUI guidance text is trusted.
    """
    t = active_theme()
    hw = diag.hwmon
    board = diag.board
    verdict_text, verdict_cls = readiness_verdict(diag)
    sev_color = {
        "SuccessChip": t.status_ok,
        "WarningChip": t.status_warn,
        "CriticalChip": t.status_crit,
    }.get(verdict_cls, t.text_primary)

    def h(title: str) -> str:
        return f'<h3 style="color:{t.text_primary};margin-bottom:2px">{escape(title)}</h3>'

    out: list[str] = []
    out.append(
        f'<div style="color:{sev_color};font-size:large;font-weight:bold">'
        f"{escape(verdict_text)}</div>"
    )

    # Board + summary
    out.append(h("Summary"))
    bits = [b for b in (board.vendor, board.name) if b]
    if board.bios_version:
        bits.append(f"BIOS {board.bios_version}")
    if bits:
        out.append(f"<div>Board: {escape(' — '.join(bits))}</div>")
    out.append(
        f"<div>{hw.total_headers} PWM header(s) detected, {hw.writable_headers} writable.</div>"
    )

    # Detected chips
    if hw.chips_detected:
        out.append(h("Detected hardware"))
        rows = [
            '<tr><th align="left">Chip</th><th align="left">Driver</th>'
            '<th align="left">Mainline</th><th align="left">Headers</th></tr>'
        ]
        for c in hw.chips_detected:
            rows.append(
                f"<tr><td>{escape(c.chip_name)}</td>"
                f"<td>{escape(c.expected_driver)}</td>"
                f"<td>{'Yes' if c.in_mainline_kernel else 'No (out-of-tree)'}</td>"
                f"<td>{c.header_count}</td></tr>"
            )
        out.append(f'<table cellpadding="4">{"".join(rows)}</table>')

    # Kernel modules
    if diag.kernel_modules:
        out.append(h("Kernel modules"))
        rows = ['<tr><th align="left">Module</th><th align="left">Loaded</th></tr>']
        for m in diag.kernel_modules:
            rows.append(
                f"<tr><td>{escape(m.name)}</td>"
                f"<td>{'Loaded' if m.loaded else 'Not loaded'}</td></tr>"
            )
        out.append(f'<table cellpadding="4">{"".join(rows)}</table>')

    # Thermal + GPU
    ts = diag.thermal_safety
    if ts:
        out.append(h("Thermal safety"))
        out.append(
            f"<div>State: {escape(ts.state)} · CPU sensor: "
            f"{'found' if ts.cpu_sensor_found else 'NOT found'} · "
            f"emergency {ts.emergency_threshold_c:.0f}°C · "
            f"release {ts.release_threshold_c:.0f}°C</div>"
        )
    if diag.gpu:
        g = diag.gpu
        out.append(h("GPU"))
        out.append(
            f"<div>{escape(g.model_name or 'AMD D-GPU')} (PCI {escape(g.pci_bdf)}) · "
            f"fan control: {escape(g.fan_control_method)}</div>"
        )

    # To fix
    fix = build_fix_guidance_html(diag)
    if fix:
        out.append(h("To fix"))
        out.append(f'<div style="color:{t.status_warn}">{fix}</div>')

    out.append(
        f'<hr><div style="color:{t.text_secondary};font-size:small">'
        f"For full hardware-compatibility detail see the "
        f"{_link(_HW_COMPAT_URL, 'Hardware Compatibility Guide')}.</div>"
    )

    body = "".join(out)
    return f'<div style="color:{t.text_primary}">{body}</div>'


class ReadinessReportDialog(QDialog):
    """A themed, resizable window showing the full hardware-readiness report.

    Uses a ``QTextBrowser`` so the report scrolls for arbitrary volume and all
    links open externally with a single click.
    """

    def __init__(self, html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReadinessReport_Dialog")
        self.setWindowTitle("Hardware Readiness — Full Report")
        self.resize(720, 640)

        layout = QVBoxLayout(self)

        self._browser = QTextBrowser()
        self._browser.setObjectName("ReadinessReport_Browser")
        self._browser.setOpenExternalLinks(True)
        self._browser.setHtml(html)
        layout.addWidget(self._browser, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("ReadinessReport_Btn_close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def set_html(self, html: str) -> None:
        """Replace the report contents (used when reopened with fresh data)."""
        self._browser.setHtml(html)
