"""Diagnostics "Troubleshooting / readiness" logic, extracted from
diagnostics_page.py (Cluster C maintainability split).

Holds the pwm-reclaim severity helpers and the ``populate_hw_diagnostics``
routine that turns a ``HardwareDiagnosticsResult`` into the Troubleshooting
tab's widgets. The populate routine takes the ``DiagnosticsPage`` as ``page``
and drives its existing widgets/methods — this module imports only from the
shared helper modules (no import of diagnostics_page), so there is no cycle;
``DiagnosticsPage`` imports ``populate_hw_diagnostics`` and re-exports the
reclaim helpers for tests that reference them.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from control_ofc.api.models import HardwareDiagnosticsResult
from control_ofc.ui.hwmon_guidance import (
    detect_module_conflicts,
    dual_chip_warning_html,
    lookup_chip_guidance,
)
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.readiness_report import (
    advisory_rows,
    board_identity_line,
    build_readiness_report_html,
    chip_rows,
    detect_readiness_problems,
    header_summary_line,
    module_rows,
    readiness_verdict,
    thermal_line,
)

if TYPE_CHECKING:
    from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage


# Severity buckets for the per-header pwm_enable reclaim count surfaced from
# ``HardwareDiagnosticsResult.hwmon.enable_revert_counts``. Tuned to match the
# operator's mental model on AORUS-class boards: zero events means the daemon
# watchdog has nothing to do, occasional reverts mean BIOS interference is
# recoverable, and ≥10 events on a single header indicates a continuous
# tug-of-war between Linux and the EC firmware that BIOS configuration should
# resolve.
RECLAIM_SEVERITY_OK = "ok"
RECLAIM_SEVERITY_WARN = "warn"
RECLAIM_SEVERITY_HIGH = "high"


def classify_reclaim_severity(count: int) -> str:
    """Return the severity bucket for a pwm_enable reclaim count.

    Buckets:
      - ``"ok"``    → ``count <= 0`` (header is healthy, no BIOS interference).
      - ``"warn"``  → ``1 <= count < 10`` (occasional reclaim — daemon is
        recovering but the operator may want to check BIOS Smart Fan settings).
      - ``"high"``  → ``count >= 10`` (continuous reclaim — BIOS is fighting
        the daemon; recommend disabling Smart Fan or using a degenerate curve).

    Negative counts are treated as ``ok`` so callers do not have to defend
    against malformed daemon payloads. The buckets are deliberately coarse so
    the operator's eye is drawn to the *hot* header, not to small fluctuations.
    """
    if count <= 0:
        return RECLAIM_SEVERITY_OK
    if count < 10:
        return RECLAIM_SEVERITY_WARN
    return RECLAIM_SEVERITY_HIGH


def reclaim_severity_color(severity: str) -> str:
    """Return the theme hex colour for a reclaim severity bucket.

    Mirrors ``SuccessChip`` / ``WarningChip`` / ``CriticalChip`` so the per-row
    colours line up with the rest of the diagnostics UI even when this widget
    is rendered in rich-text mode (which doesn't pick up Qt CSS class styling).

    Reads from :func:`active_theme` on every call so a theme switch picks up
    the new status colours on the next render — pre-DEC-109 this was pinned
    to a module-level Default Dark snapshot.
    """
    theme = active_theme()
    if severity == RECLAIM_SEVERITY_OK:
        return theme.status_ok
    if severity == RECLAIM_SEVERITY_HIGH:
        return theme.status_crit
    return theme.status_warn


def render_reclaim_rows(reverts: dict[str, int] | None) -> str | None:
    """Render the per-header reclaim count card body as rich-text HTML.

    Returns ``None`` when there is nothing to surface (no payload, or every
    header reports zero reclaims) so the caller can hide the card entirely.
    Returns a non-empty HTML string otherwise — each header on its own row,
    coloured by ``classify_reclaim_severity``.

    The ``None``-tolerant signature is deliberate: older daemons (pre-1.3.x)
    don't include ``enable_revert_counts`` in the diagnostics payload, and the
    GUI must not crash when the key is absent.
    """
    if not reverts:
        return None
    # Hide the card if every header is at zero — the daemon won't normally
    # emit such a payload, but defending against it keeps the UI quiet when
    # a future daemon decides to surface healthy headers in the same map.
    if not any(count > 0 for count in reverts.values()):
        return None

    rows: list[str] = []
    for header_id in sorted(reverts):
        count = reverts[header_id]
        severity = classify_reclaim_severity(count)
        color = reclaim_severity_color(severity)
        # ``severity`` is a fixed enum string so it is safe to format raw;
        # header_id and count come from the daemon JSON and are escaped so
        # quirky chip names (e.g. "it87.2624") never break the markup.
        rows.append(
            f'<span style="color: {color};">'
            f"<b>{escape(header_id)}</b>: {count} revert(s) "
            f"[{severity.upper()}]"
            "</span>"
        )
    return "<br>".join(rows)


def populate_hw_diagnostics(page: DiagnosticsPage, diag: HardwareDiagnosticsResult) -> None:
    """Populate hardware readiness UI from a diagnostics result."""
    hw = diag.hwmon

    # Board info (shared formatter, DEC-115)
    board = diag.board
    identity = board_identity_line(diag)
    if identity:
        page._board_info_label.setText("Board: " + identity)
        page._board_info_label.setVisible(True)
    else:
        page._board_info_label.setVisible(False)

    # DEC-101: dual-chip board warning. Computed before chip-table render
    # so users see "missing chips" guidance above the table that will
    # otherwise look short. ``expected_chips`` is empty for boards the
    # daemon doesn't know about (and for daemons that predate DEC-101),
    # in which case the warning stays hidden.
    detected_chip_names = [c.chip_name for c in hw.chips_detected]
    dual_chip_html = dual_chip_warning_html(
        board.name,
        list(diag.expected_chips),
        detected_chip_names,
    )
    if dual_chip_html:
        page._dual_chip_warning_label.setText(dual_chip_html)
        page._set_class(page._dual_chip_warning_label, "WarningChip")
        page._dual_chip_warning_label.setVisible(True)
    else:
        page._dual_chip_warning_label.setVisible(False)

    # Advisories (DEC-158): board/chip vendor quirks rendered as per-severity
    # collapsible rows. advisory_rows() applies the same dedupe + most-severe-
    # first ordering the pop-out report uses (DEC-115), passing the
    # daemon-supplied CPU vendor + board name so DEC-110 platform-scoped Intel
    # quirks fire on real hardware. Older daemons without cpu_vendor send ""
    # → platform-scoped quirks are suppressed, not fired indiscriminately.
    page._render_advisories(advisory_rows(diag))

    summary_parts = [header_summary_line(hw)]
    if hw.total_headers > 0 and hw.writable_headers == 0:
        summary_parts.append("All headers are read-only. Check BIOS fan settings or driver status.")
    if len(hw.chips_detected) == 0:
        summary_parts.append(
            "No hwmon chips detected. Motherboard fan control may require "
            "a kernel driver — see the modules table below."
        )
    page._hw_ready_summary.setText("\n".join(summary_parts))

    # Chip table (shared rows, DEC-115 — single source of truth with the
    # pop-out report, including the Status column the report had dropped).
    crows = chip_rows(diag)
    page._chip_table.setRowCount(len(crows))
    for i, r in enumerate(crows):
        page._ensure_row_items(page._chip_table, i, 5)
        page._chip_table.item(i, 0).setText(r.chip)
        page._chip_table.item(i, 1).setText(r.driver)
        page._chip_table.item(i, 2).setText(r.status)
        page._chip_table.item(i, 3).setText(r.mainline)
        page._chip_table.item(i, 4).setText(r.headers)

    # Kernel modules table (shared rows, DEC-115)
    mrows = module_rows(diag)
    page._modules_table.setRowCount(len(mrows))
    for i, r in enumerate(mrows):
        page._ensure_row_items(page._modules_table, i, 3)
        page._modules_table.item(i, 0).setText(r.name)
        page._modules_table.item(i, 1).setText(r.loaded)
        page._modules_table.item(i, 2).setText(r.mainline)

    # ACPI conflicts
    if diag.acpi_conflicts:
        lines = ["ACPI I/O port conflicts detected:"]
        has_it87 = False
        for c in diag.acpi_conflicts:
            lines.append(
                f"  {c.io_range} claimed by '{c.claimed_by}' "
                f"— conflicts with {c.conflicts_with_driver}"
            )
            if c.conflicts_with_driver == "it87":
                has_it87 = True
        if has_it87:
            lines.append(
                "Tip (ITE chips): prefer driver-local 'ignore_resource_conflict=1' "
                "(add 'options it87 ignore_resource_conflict=1' to "
                "/etc/modprobe.d/it87.conf) over the system-wide "
                "'acpi_enforce_resources=lax' kernel parameter."
            )
        else:
            lines.append(
                "Tip: add 'acpi_enforce_resources=lax' to kernel parameters, "
                "or disable ACPI hardware monitoring in BIOS."
            )
        page._acpi_label.setText("\n".join(lines))
        page._set_class(page._acpi_label, "WarningChip")
        page._acpi_label.setVisible(True)
    else:
        page._acpi_label.setVisible(False)

    # DEC-105: daemon-reported module collisions (critical pairs that
    # race for the same chip, e.g. nct6687 + nct6775 → corrupted fan
    # registers). Rendered first so users see the most severe warning
    # at the top; the GUI-only fallback CONFLICTING_MODULE_SETS check
    # below covers older daemons that don't emit module_collisions.
    # All daemon-supplied strings are HTML-escaped before interpolating
    # into this RichText label — same defensive pattern as the
    # revert-counts banner. The daemon is the user's own process
    # today, but the trust model should not assume future networked
    # transports or compromised installs cannot ship hostile strings.
    daemon_collisions = getattr(diag, "module_collisions", []) or []
    if daemon_collisions:
        parts: list[str] = [
            "<b>Driver module collision detected — do not write PWM until resolved.</b><br>"
        ]
        for col in daemon_collisions:
            parts.append(
                f"<br><b>{escape(col.module_a)}</b> + "
                f"<b>{escape(col.module_b)}</b> "
                f"({escape(col.severity.upper())})<br>"
                f"{escape(col.summary)}<br>"
                f"<i>Remediation:</i> {escape(col.remediation)}"
            )
        page._module_collision_label.setText("".join(parts))
        page._set_class(page._module_collision_label, "CriticalChip")
        page._module_collision_label.setVisible(True)
    else:
        page._module_collision_label.setVisible(False)

    # Module conflicts (GUI-only fallback for older daemons that don't
    # emit module_collisions, plus any pairs that are not yet daemon-side).
    loaded_names = [m.name for m in diag.kernel_modules if m.loaded]
    mod_conflicts = detect_module_conflicts(loaded_names)
    # Suppress the fallback banner when the daemon already reported
    # the same pair via module_collisions — avoids two banners for
    # one underlying problem.
    if daemon_collisions:
        daemon_pairs = {tuple(sorted([c.module_a, c.module_b])) for c in daemon_collisions}
        mod_conflicts = [
            mc
            for mc in mod_conflicts
            if tuple(sorted([mc.module_a, mc.module_b])) not in daemon_pairs
        ]
    if mod_conflicts:
        lines = ["Driver module conflicts detected:"]
        for mc in mod_conflicts:
            lines.append(f"  {mc.module_a} + {mc.module_b}: {mc.explanation}")
        page._module_conflict_label.setText("\n".join(lines))
        page._set_class(page._module_conflict_label, "CriticalChip")
        page._module_conflict_label.setVisible(True)
    else:
        page._module_conflict_label.setVisible(False)

    # BIOS interference (revert counts)
    # Tolerates pre-1.3 daemons that omit ``enable_revert_counts`` — the
    # parser already defaults to {} (api/models.py:633), and ``getattr``
    # below guards against any future shape drift on the GUI side too.
    reverts = getattr(hw, "enable_revert_counts", None) or {}
    body_html = render_reclaim_rows(reverts)
    if body_html is None:
        # DEC-116: nothing to report — hide the whole sub-section, not just
        # its inner labels, so the user never expands an empty header.
        page._section_bios.setVisible(False)
        page._revert_headline_label.setVisible(False)
        page._revert_label.setVisible(False)
        page._revert_footnote_label.setVisible(False)
    else:
        page._section_bios.setVisible(True)
        max_count = max(reverts.values())
        top_severity = classify_reclaim_severity(max_count)
        severity_class = {
            RECLAIM_SEVERITY_HIGH: "CriticalChip",
            RECLAIM_SEVERITY_WARN: "WarningChip",
            RECLAIM_SEVERITY_OK: "SuccessChip",
        }[top_severity]

        headline = (
            "BIOS interference detected — the EC/BIOS reclaimed fan control "
            f"(highest: {max_count} reverts, {top_severity.upper()})"
        )
        page._revert_headline_label.setText(headline)
        page._set_class(page._revert_headline_label, severity_class)
        page._revert_headline_label.setVisible(True)

        page._revert_label.setText(body_html)
        page._revert_label.setVisible(True)

        page._revert_footnote_label.setText(
            "The daemon watchdog automatically re-enables manual mode on every "
            "reclaim. Persistently HIGH counts indicate ongoing BIOS contention — "
            "see the matching vendor guidance card above for the BIOS settings to "
            "change."
        )
        page._revert_footnote_label.setVisible(True)

        # DEC-112: a non-zero revert count is a real problem the user
        # must not miss, so surface the per-header detail by expanding
        # the section. Idempotent and never auto-collapses, so a manual
        # toggle on a healthy system is left untouched.
        page._section_bios.set_expanded(True)

    # Thermal safety (shared formatter, DEC-115)
    page._thermal_label.setText(thermal_line(diag.thermal_safety) or "")

    # GPU diagnostics
    lines: list[str] = []
    if diag.gpu:
        gpu = diag.gpu
        lines.append(f"GPU: {gpu.model_name or 'AMD D-GPU'} (PCI {gpu.pci_bdf})")
        lines.append(f"  Fan control: {gpu.fan_control_method}")
        lines.append(f"  Overdrive: {'enabled' if gpu.overdrive_enabled else 'disabled'}")
        if gpu.ppfeaturemask:
            bit14 = "set" if gpu.ppfeaturemask_bit14_set else "NOT set"
            lines.append(f"  ppfeaturemask: {gpu.ppfeaturemask} (bit 14: {bit14})")
            if not gpu.ppfeaturemask_bit14_set:
                lines.append(
                    "  Fan control requires bit 14 — add "
                    "'amdgpu.ppfeaturemask=0xffffffff' to kernel parameters"
                )
        elif gpu.fan_control_method == "read_only":
            # No ppfeaturemask kernel param at all, and no fan write path is
            # available. The most common cause on RDNA3+ (RX 7000/9000) is
            # the missing kernel parameter; pre-RDNA3 cards normally have
            # pwm1 working and would not land here without something else
            # being wrong. Surface the param as the first thing to try.
            lines.append("  ppfeaturemask: not set on kernel command line")
            lines.append(
                "  Tip: RDNA3+ fan control needs "
                "'amdgpu.ppfeaturemask=0xffffffff' (see man control-ofc-daemon)"
            )
        lines.append(f"  Zero-RPM: {'available' if gpu.zero_rpm_available else 'not available'}")
        # DEC-119: firmware-enforced OD_RANGE fan-speed minimum. This is the
        # real reason a PMFW GPU fan won't go to 0% via the curve — surface
        # it so the floor isn't mistaken for a GUI/daemon clamp.
        if gpu.fan_speed_min_pct is not None and gpu.fan_speed_max_pct is not None:
            lines.append(
                f"  Firmware fan-speed range: {gpu.fan_speed_min_pct}% to "
                f"{gpu.fan_speed_max_pct}% (values below {gpu.fan_speed_min_pct}% are "
                "clamped by the GPU firmware, not the daemon)"
            )
        if gpu.fan_minimum_pwm is not None:
            lines.append(f"  Firmware fan_minimum_pwm: {gpu.fan_minimum_pwm}%")
        # DEC-119: per-GPU kernel-regression advisories, mirrored from
        # /capabilities so the diagnostics export is page-contained.
        for kw in gpu.kernel_warnings:
            lines.append(f"  Advisory [{kw.severity}]: {kw.message}")

    # DEC-119: driver-bound status. Rendered even when there is no hwmon
    # GPU above, because an unbound/blacklisted/passed-through GPU produces
    # no hwmon node and would otherwise be completely invisible here.
    for dev in diag.amd_pci_devices:
        if dev.amdgpu_bound:
            continue
        drv = dev.driver or "none"
        lines.append(f"AMD GPU {dev.pci_bdf} present but amdgpu is NOT bound (driver: {drv}).")
        if not diag.amdgpu_module_loaded:
            lines.append(
                "  The amdgpu kernel module is not loaded — check for a modprobe "
                "blacklist or add amdgpu to your initramfs."
            )
        else:
            lines.append(
                "  The amdgpu module is loaded but did not bind this device — check "
                "for vfio-pci passthrough or an early KMS failure (see dmesg)."
            )

    # DEC-121: Intel discrete GPU diagnostics — read-only, firmware-managed.
    if diag.intel_gpu:
        ig = diag.intel_gpu
        lines.append(
            f"Intel GPU: {ig.model_name or 'Intel D-GPU'} (PCI {ig.pci_bdf}, driver {ig.driver})"
        )
        lines.append(f"  Fan control: {ig.fan_control_method} (firmware-managed)")
        if ig.fan_control_note:
            lines.append(f"  {ig.fan_control_note}")

    if lines:
        page._gpu_diag_label.setText("\n".join(lines))
        page._gpu_diag_label.setVisible(True)
    else:
        page._gpu_diag_label.setVisible(False)

    # DEC-120: toggle the GPU fan-control verify button now that we know the
    # GPU's write path and the daemon version.
    page._update_gpu_verify_availability(diag)

    # Guidance from chip knowledge base (HTML with clickable links)
    guidance_parts: list[str] = []
    seen_prefixes: set[str] = set()
    for chip in hw.chips_detected:
        g = lookup_chip_guidance(chip.chip_name)
        if g and g.chip_prefix not in seen_prefixes:
            seen_prefixes.add(g.chip_prefix)
            if g.bios_tips:
                guidance_parts.append(f"<b>{chip.chip_name} — BIOS tips:</b>")
                for tip in g.bios_tips:
                    guidance_parts.append(f"&nbsp;&nbsp;\u2022 {tip}")
            if g.known_issues:
                guidance_parts.append(f"<b>{chip.chip_name} — Known issues:</b>")
                for issue in g.known_issues:
                    guidance_parts.append(f"&nbsp;&nbsp;\u2022 {issue}")
            if g.driver_url:
                guidance_parts.append(
                    f'&nbsp;&nbsp;Driver docs: <a href="{g.driver_url}">{g.driver_url}</a>'
                )
    if guidance_parts:
        page._guidance_label.setText("<br>".join(guidance_parts))
        page._guidance_label.setVisible(True)
    else:
        page._guidance_label.setVisible(False)

    # Show docs link when any hardware chips were detected
    if hw.chips_detected:
        page._docs_link_label.setText(
            "For detailed hardware compatibility information, see the "
            '<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
            'docs/19_Hardware_Compatibility.md">Hardware Compatibility Guide</a>.'
        )
        page._docs_link_label.setVisible(True)
    else:
        page._docs_link_label.setVisible(False)

    # Populate verify header combo
    page._verify_combo.clear()
    if page._state:
        for h in page._state.hwmon_headers:
            if h.is_writable:
                label = h.label or h.id
                page._verify_combo.addItem(f"{label} ({h.id})", h.id)
    page._verify_btn.setEnabled(page._verify_combo.count() > 0)

    # DEC-113/DEC-124: readiness verdict banner + enable the full-report
    # pop-out now that a diagnostics result is available.
    verdict_text, verdict_cls = readiness_verdict(diag)
    page._readiness_verdict_label.setText(verdict_text)
    page._set_class(page._readiness_verdict_label, verdict_cls)

    # DEC-124: render the always-visible issue checklist (the promoted
    # "To fix" content) from the same GUI-authored problem list the verdict
    # and the pop-out report derive from. Healthy → a single "no issues"
    # line; a problem → one row per issue, so the detail is never hidden
    # behind a collapse.
    page._render_issue_list(detect_readiness_problems(diag))

    page._open_report_btn.setEnabled(True)
    # If the pop-out is already open, refresh it with the new data.
    if page._report_dialog is not None and page._report_dialog.isVisible():
        page._report_dialog.set_html(build_readiness_report_html(diag))
