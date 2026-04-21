"""Chip-family knowledge base for hardware readiness guidance.

Maps Super I/O chip name prefixes to driver information, BIOS tips,
known manufacturer quirks, and external documentation links.
Also provides vendor+chip specific quirk entries for boards where
BIOS firmware actively interferes with Linux fan control.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChipGuidance:
    chip_prefix: str
    driver_name: str
    in_mainline: bool
    driver_package: str
    driver_url: str
    bios_tips: list[str] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class VendorQuirk:
    """Board-vendor + chip combination with known BIOS interference."""

    vendor_pattern: str
    chip_prefix: str
    severity: str  # "critical" | "high" | "medium" | "info"
    summary: str
    details: list[str] = field(default_factory=list)


CHIP_GUIDANCE_DB: list[ChipGuidance] = [
    ChipGuidance(
        chip_prefix="nct679",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        bios_tips=[
            "Disable ACPI hardware monitoring (AMW0) if the driver fails to bind.",
            "Set 'Smart Fan Mode' to 'Manual' or 'Full Speed' if headers appear read-only.",
        ],
        known_issues=[
            "ASUS boards may have ACPI OpRegion conflicts on I/O ports 0x0290-0x0299.",
            "MSI boards may need 'acpi_enforce_resources=lax' kernel parameter.",
        ],
        notes="Nuvoton NCT679x series — widely supported in mainline kernel.",
    ),
    ChipGuidance(
        chip_prefix="nct677",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        bios_tips=[
            "Disable ACPI hardware monitoring if the driver fails to bind.",
        ],
        notes="Nuvoton NCT677x series — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="nct6686",
        driver_name="nct6683",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6683.html",
        bios_tips=[
            "ASRock boards: disable 'Smart Fan' in BIOS if PWM writes have no effect.",
        ],
        known_issues=[
            "In-kernel nct6683 driver supports NCT6686D monitoring, but PWM write "
            "support on many ASRock AM5 boards is incomplete or non-functional.",
            "If reads work but writes don't, consider out-of-tree drivers: "
            "nct6686d (github.com/s25g5d4/nct6686d) or "
            "asrock-nct6683 (github.com/branchmispredictor/asrock-nct6683).",
        ],
        notes=(
            "Nuvoton NCT6686D — common on ASRock A620/B650/X670 boards. "
            "Upstream nct6683 driver may only provide monitoring without working "
            "PWM writes. Out-of-tree drivers exist for specific board models."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6683",
        driver_name="nct6683",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6683.html",
        bios_tips=[
            "MSI boards: if monitoring works but PWM writes do not, try the "
            "out-of-tree nct6687d driver instead.",
        ],
        known_issues=[
            "Covers NCT6683D/NCT6686D/NCT6687D chip family. Monitoring usually "
            "works, but manual fan control is often incomplete on modern AMD boards.",
            "MSI boards: nct6683 may expose sensors but not functional PWM writes — "
            "nct6687d-dkms-git is the common fix.",
            "ASRock boards: read-vs-write mismatch is common — sensors visible but "
            "PWM writes may be silently ignored.",
        ],
        notes=(
            "Nuvoton NCT6683 family — in-kernel driver covering NCT6683D/NCT6686D/"
            "NCT6687D. Monitoring usually works before write control does."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6687",
        driver_name="nct6687",
        in_mainline=False,
        driver_package="nct6687d-dkms-git (AUR)",
        driver_url="https://github.com/Fred78290/nct6687d",
        bios_tips=[
            "MSI boards: disable 'Smart Fan Mode' in BIOS to allow PWM writes.",
        ],
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "Some MSI boards report all headers as read-only until Smart Fan Mode is disabled.",
        ],
        notes=(
            "Nuvoton NCT6687-R — common on MSI B550/X570/B650/X670 boards. "
            "Requires out-of-tree driver."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8688",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte boards: enable 'Full Speed' fan mode in BIOS → Smart Fan 5 settings.",
            "Ensure 'FAN Control by' is NOT set to 'Temperature' in BIOS.",
        ],
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "Gigabyte X570/B550: headers may appear read-only "
            "unless BIOS fan mode is 'Full Speed'.",
        ],
        notes=(
            "ITE IT8688E — common on Gigabyte boards. "
            "Requires out-of-tree frankcrawford/it87 driver."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8689",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte boards: enable 'Full Speed' fan mode in BIOS.",
            "If 'Full Speed' is unavailable, configure a degenerate BIOS fan curve: "
            "set all 7 points to PWM 40 / Temp 0-90-90-90-90-90-90 with the final "
            "point at PWM 100 / Temp 90. This disables the EC's own curve evaluation.",
            "Prefer driver-local 'ignore_resource_conflict=1' over the system-wide "
            "'acpi_enforce_resources=lax' kernel parameter if ACPI conflicts arise.",
        ],
        known_issues=[
            "Out-of-tree driver required — not in mainline kernel.",
            "IT8689E Rev 1 (e.g. X670E Aorus Master): PWM writes are silently accepted "
            "but have zero effect on fan speed. No known software workaround.",
            "IT8689E Rev 2 (e.g. B650 Eagle AX): BIOS overrides PWM values unless "
            "'Full Speed' or degenerate fan curve is configured.",
            "Some Gigabyte boards have a separate fan-control chip — Linux can read "
            "RPMs but not change speeds. This is a hardware limitation.",
        ],
        notes="ITE IT8689E — found on Gigabyte Z390/Z490/Z690/B650/X670E boards.",
    ),
    ChipGuidance(
        chip_prefix="it8696",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte boards: enable 'Full Speed' fan mode in BIOS → Smart Fan 6 settings.",
            "Ensure 'FAN Control by' is NOT set to 'Temperature' in BIOS.",
            "If 'Full Speed' is unavailable, try the degenerate-curve workaround: "
            "set all temperature points identical, 0% PWM except final point at 100%.",
        ],
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "Gigabyte SmartFan 6 may override PWM values even when driver is loaded. "
            "The daemon's pwm_enable watchdog detects and compensates for this, but "
            "BIOS configuration is the most reliable fix.",
        ],
        notes="ITE IT8696E — found on newer Gigabyte boards (X870E, B850, etc.).",
    ),
    ChipGuidance(
        chip_prefix="it8686",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte boards: enable 'Full Speed' fan mode in BIOS.",
        ],
        notes="ITE IT8686E — found on Gigabyte boards. Requires out-of-tree driver.",
    ),
    ChipGuidance(
        chip_prefix="it8625",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        notes="ITE IT8625E — requires out-of-tree driver.",
    ),
    ChipGuidance(
        chip_prefix="it87",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/it87.html",
        notes="ITE IT87xx (older models) — supported in mainline kernel.",
    ),
    ChipGuidance(
        chip_prefix="f71882",
        driver_name="f71882fg",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/f71882fg.html",
        notes="Fintek F71882FG — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="f718",
        driver_name="f71882fg",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/f71882fg.html",
        notes="Fintek F718xx series — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="sch5627",
        driver_name="sch5627",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/sch5627.html",
        notes="SMSC SCH5627 — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="sch5636",
        driver_name="sch5636",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/sch5636.html",
        notes="SMSC SCH5636 — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="asus_ec_sensors",
        driver_name="asus_ec_sensors",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://docs.kernel.org/hwmon/asus_ec_sensors.html",
        known_issues=[
            "Provides extra board sensors (temperatures, RPMs) via ASUS EC registers. "
            "This is a sensor-enrichment driver, NOT a PWM write path.",
            "Do not use this driver for fan control — look for nct6775 or another "
            "Super I/O driver as the actual PWM write endpoint.",
        ],
        notes=(
            "ASUS EC Sensors — exposes additional motherboard sensors on supported "
            "ASUS boards (ROG, PRIME, ProArt, TUF series). Read-only sensor data."
        ),
    ),
    ChipGuidance(
        chip_prefix="asus_wmi_sensors",
        driver_name="asus_wmi_sensors",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://docs.kernel.org/hwmon/asus_wmi_sensors.html",
        bios_tips=[
            "Do NOT poll this driver at high frequency — some ASUS BIOS WMI "
            "implementations are buggy and frequent polling can trigger fan stop, "
            "fan max, or stuck sensor readings.",
        ],
        known_issues=[
            "Sensor-enrichment driver only — does NOT provide PWM write capability.",
            "PRIME X470-PRO is specifically called out in upstream docs as having "
            "buggy WMI that can cause fans to stop or get stuck at maximum.",
            "More frequent polling increases the risk of triggering firmware bugs.",
        ],
        notes=(
            "ASUS WMI Sensors — exposes extra sensors via BIOS WMI interface. "
            "Read-only. Poll conservatively to avoid firmware bugs."
        ),
    ),
]


def lookup_chip_guidance(chip_name: str) -> ChipGuidance | None:
    """Find the best-matching guidance entry for a chip name.

    Matches are checked from most-specific prefix to least-specific,
    so "it8688" matches the IT8688E entry before the generic "it87" entry.
    """
    lower = chip_name.lower()
    best: ChipGuidance | None = None
    best_len = 0
    for entry in CHIP_GUIDANCE_DB:
        if lower.startswith(entry.chip_prefix) and len(entry.chip_prefix) > best_len:
            best = entry
            best_len = len(entry.chip_prefix)
    return best


def format_driver_status(chip_name: str, loaded: bool) -> str:
    """Human-readable one-liner for driver load state."""
    guidance = lookup_chip_guidance(chip_name)
    if guidance is None:
        return f"Unknown chip '{chip_name}' — driver status unavailable"

    if loaded and guidance.in_mainline:
        return f"{guidance.driver_name} loaded (mainline kernel)"
    if loaded and not guidance.in_mainline:
        return f"{guidance.driver_name} loaded (out-of-tree: {guidance.driver_package})"
    if not loaded and guidance.in_mainline:
        return f"{guidance.driver_name} not loaded — try: sudo modprobe {guidance.driver_name}"
    return f"{guidance.driver_name} not loaded — install {guidance.driver_package}"


# ---------------------------------------------------------------------------
# Vendor + chip quirk database
# ---------------------------------------------------------------------------

VENDOR_QUIRKS_DB: list[VendorQuirk] = [
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="critical",
        summary="Gigabyte SmartFan 6 + IT8689E — BIOS actively overrides fan control",
        details=[
            "IT8689E Rev 1 (e.g. X670E Aorus Master): PWM writes are silently "
            "accepted with zero hardware effect. No known software workaround — "
            "consider using a different fan header or external fan controller.",
            "IT8689E Rev 2 (e.g. B650 Eagle AX): BIOS overrides unless a "
            "degenerate fan curve is configured in BIOS Smart Fan settings.",
            "Workaround: In BIOS → Smart Fan 6, set all temperature points to "
            "the same value (e.g. 40°C) and all duty/PWM to 0% except the "
            "final point at 100%. This effectively disables the EC curve.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="high",
        summary="Gigabyte SmartFan 6 + IT8696E — BIOS may override fan control",
        details=[
            "The EC firmware continuously evaluates its own fan curves and can "
            "overwrite PWM values set by Linux within seconds.",
            "The daemon's pwm_enable watchdog detects and re-writes manual mode "
            "when the BIOS reclaims it, but BIOS configuration is more reliable.",
            "Workaround: In BIOS → Smart Fan 6, set fan mode to 'Full Speed' for "
            "all headers you want to control from Linux. Fans will run at 100% "
            "until the GUI/daemon takes over.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8688",
        severity="high",
        summary="Gigabyte SmartFan 5 + IT8688E — BIOS may override fan control",
        details=[
            "SmartFan 5 on Gigabyte X570/B550 boards actively overrides PWM unless "
            "BIOS fan mode is set to 'Full Speed'.",
            "Headers may appear read-only until this BIOS change is made.",
            "Workaround: In BIOS → Smart Fan 5, enable 'Full Speed' for each header. "
            "Ensure 'FAN Control by' is NOT set to 'Temperature'.",
            "Note: Secondary ITE controller (IT8792E/IT87952E) on these boards "
            "is always read-only from Linux — only the primary 3 headers are controllable.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8686",
        severity="high",
        summary="Gigabyte SmartFan 5 + IT8686E — BIOS may override fan control",
        details=[
            "Same behaviour as IT8688E: SmartFan 5 overrides PWM unless 'Full Speed' "
            "is enabled in BIOS.",
            "Workaround: In BIOS → Smart Fan 5, enable 'Full Speed' for each header.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="medium",
        summary="MSI Smart Fan + NCT6687 — headers read-only until BIOS changed",
        details=[
            "MSI boards with NCT6687-R report all fan headers as read-only "
            "while 'Smart Fan Mode' is enabled in BIOS.",
            "Workaround: In BIOS → Hardware Monitor, disable 'Smart Fan Mode'. "
            "This allows the out-of-tree nct6687d driver to write PWM values.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="nct679",
        severity="medium",
        summary="ASUS + NCT679x — ACPI I/O port conflict may prevent driver loading",
        details=[
            "ASUS boards frequently claim Super I/O I/O port ranges (0x0290-0x0299) "
            "via ACPI OperationRegions, preventing the nct6775 driver from binding.",
            "Workaround: Add 'acpi_enforce_resources=lax' to kernel boot parameters. "
            "On newer kernels (5.17+), the nct6775 driver supports ACPI mutex-based "
            "access that avoids this conflict.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="asus_wmi",
        severity="high",
        summary="ASUS WMI sensors — high-frequency polling may cause fan/sensor failure",
        details=[
            "Some ASUS BIOS WMI implementations are buggy: frequent polling can "
            "trigger fans stopping, fans stuck at maximum, or sensor readings "
            "getting stuck at a fixed value.",
            "PRIME X470-PRO is specifically called out in upstream kernel docs "
            "as particularly affected.",
            "The daemon polls at 1 Hz which is generally safe, but avoid any "
            "additional tools polling these sensors simultaneously.",
            "These drivers provide sensor enrichment only — they are NOT the "
            "PWM write path. Look for nct6775 or another Super I/O driver "
            "for actual fan control.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="high",
        summary="MSI X870/B850 — system fans may not respond to single PWM writes",
        details=[
            "Newer MSI X870/B850-class boards require writing all 7 BIOS fan-curve "
            "points rather than a single PWM register write for system fans to respond.",
            "CPU_FAN and PUMP_FAN headers typically work first; SYS_FAN support "
            "may lag behind or require the brute-force module parameter.",
            "Workaround: Load the nct6687d driver with 'msi_fan_brute_force=1': "
            "sudo modprobe nct6687 msi_fan_brute_force=1",
            "Add 'options nct6687 msi_fan_brute_force=1' to "
            "/etc/modprobe.d/nct6687.conf to persist across reboots.",
            "3-pin DC chassis fans may remain problematic even when 4-pin PWM fans work.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6686",
        severity="medium",
        summary="ASRock + NCT6686D — monitoring works but PWM writes may not",
        details=[
            "ASRock A620/B650/X670 boards with NCT6686D commonly show sensors "
            "and RPMs, but manual PWM writes are often silently ignored.",
            "The in-kernel nct6683 driver provides monitoring but incomplete "
            "write support on these boards.",
            "Workaround options (board-specific — try in order):\n"
            "  1. nct6686d driver: github.com/s25g5d4/nct6686d\n"
            "  2. asrock-nct6683 driver: github.com/branchmispredictor/asrock-nct6683\n"
            "  3. nct6687d driver: some ASRock boards respond to this driver",
            "Test write capability on a non-critical chassis fan header first.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6683",
        severity="medium",
        summary="ASRock + NCT6683 — sensors visible but PWM control may be incomplete",
        details=[
            "In-kernel nct6683 driver often gives visibility of temperatures and "
            "RPMs on ASRock boards, but manual PWM writes may not behave correctly.",
            "If the PWM verification test shows 'no RPM effect', the write path "
            "is likely incomplete for this board model.",
            "Consider board-specific out-of-tree drivers — ASRock boards are a "
            "strong candidate for a per-model driver selection rather than a "
            "single driver rule.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it87",
        severity="info",
        summary="Gigabyte ITE — prefer driver-local conflict resolution over system-wide",
        details=[
            "If ACPI I/O port conflicts are detected, prefer the driver-local "
            "'ignore_resource_conflict=1' parameter for the it87 module over "
            "the system-wide 'acpi_enforce_resources=lax' kernel parameter.",
            "Add 'options it87 ignore_resource_conflict=1' to "
            "/etc/modprobe.d/it87.conf to persist across reboots.",
            "Note: this is still inherently risky as ACPI and the driver may "
            "access the chip concurrently — use only when needed.",
            "Do NOT use 'force_id' as a workaround — it is intended for "
            "testing only and should not be used in production.",
        ],
    ),
]


def lookup_vendor_quirks(board_vendor: str, chip_name: str) -> list[VendorQuirk]:
    """Find vendor+chip specific quirks matching a board and chip."""
    if not board_vendor or not chip_name:
        return []
    vendor_lower = board_vendor.lower()
    chip_lower = chip_name.lower()
    return [
        q
        for q in VENDOR_QUIRKS_DB
        if q.vendor_pattern in vendor_lower and chip_lower.startswith(q.chip_prefix)
    ]


# ---------------------------------------------------------------------------
# Module conflict detection
# ---------------------------------------------------------------------------

CONFLICTING_MODULE_SETS: list[tuple[str, str, str]] = [
    (
        "nct6683",
        "nct6687",
        "Both nct6683 (in-kernel) and nct6687 (out-of-tree) are loaded. "
        "They may compete for the same hwmon device, causing PWM writes to fail. "
        "Blacklist nct6683 if using nct6687d: "
        "echo 'blacklist nct6683' | sudo tee /etc/modprobe.d/blacklist-nct6683.conf",
    ),
]


@dataclass(frozen=True)
class ModuleConflict:
    """Two loaded modules that may interfere with each other."""

    module_a: str
    module_b: str
    explanation: str


def detect_module_conflicts(loaded_modules: list[str]) -> list[ModuleConflict]:
    """Check for known conflicting driver combinations among loaded modules."""
    loaded_set = {m.lower() for m in loaded_modules}
    conflicts = []
    for mod_a, mod_b, explanation in CONFLICTING_MODULE_SETS:
        if mod_a in loaded_set and mod_b in loaded_set:
            conflicts.append(ModuleConflict(mod_a, mod_b, explanation))
    return conflicts


# ---------------------------------------------------------------------------
# Post-verification guidance
# ---------------------------------------------------------------------------


def verification_guidance(
    result: str,
    board_vendor: str,
    chip_name: str,
) -> str | None:
    """Return actionable next-step text based on a PWM verify result and board context.

    *result* is one of the daemon's verify outcomes: "effective",
    "pwm_enable_reverted", "pwm_value_clamped", "no_rpm_effect",
    "rpm_unavailable".
    """
    if result == "effective":
        return None

    vendor_lower = (board_vendor or "").lower()
    chip_lower = (chip_name or "").lower()

    if result == "pwm_enable_reverted":
        if "gigabyte" in vendor_lower and chip_lower.startswith("it8"):
            return (
                "The BIOS reclaimed fan control (pwm_enable reverted). On Gigabyte "
                "boards, set fan mode to 'Full Speed' in BIOS Smart Fan settings, "
                "or configure a degenerate fan curve to disable the EC's own curve."
            )
        if "micro-star" in vendor_lower:
            return (
                "The BIOS reclaimed fan control. On MSI boards, disable 'Smart Fan "
                "Mode' in BIOS → Hardware Monitor. For X870/B850 boards, also try "
                "loading nct6687 with 'msi_fan_brute_force=1'."
            )
        return (
            "The BIOS or EC firmware reclaimed fan control (pwm_enable reverted to "
            "automatic). Check BIOS settings — look for 'Smart Fan', 'Fan Mode', or "
            "'Fan Control' options and set the affected headers to manual or full speed."
        )

    if result == "no_rpm_effect":
        if "gigabyte" in vendor_lower and chip_lower.startswith("it8689"):
            return (
                "PWM writes were accepted but fan speed did not change. On Gigabyte "
                "IT8689E Rev 1 boards (e.g. X670E Aorus Master), this is a known "
                "hardware limitation with no software workaround. Consider using a "
                "different fan header or an external fan controller."
            )
        if "asrock" in vendor_lower and chip_lower.startswith("nct6"):
            return (
                "PWM writes were accepted but fan speed did not change. On ASRock "
                "boards, the in-kernel nct6683 driver often has incomplete write "
                "support. Try an out-of-tree driver: nct6686d, asrock-nct6683, or "
                "nct6687d (see Diagnostics guidance for links)."
            )
        return (
            "PWM writes were accepted but the fan did not respond. This could mean "
            "the driver's write path is incomplete for this board, the fan is "
            "disconnected, or the BIOS is overriding the value. Check BIOS fan "
            "settings and consider running the verification test on a different header."
        )

    if result == "pwm_value_clamped":
        return (
            "The PWM value was changed by the hardware after writing. The BIOS or "
            "EC may be clamping fan speeds to its own range. Check BIOS fan curve "
            "settings — the EC may override values outside its configured range."
        )

    if result == "rpm_unavailable":
        return (
            "The PWM value was written but RPM feedback is not available on this "
            "header, so the actual effect cannot be confirmed. Listen for fan speed "
            "changes or check another monitoring tool to verify control is working."
        )

    return None
