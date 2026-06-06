"""Chip-family knowledge base for hardware readiness guidance.

Maps Super I/O chip name prefixes to driver information, BIOS tips,
known manufacturer quirks, and external documentation links.
Also provides vendor+chip specific quirk entries for boards where
BIOS firmware actively interferes with Linux fan control.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Shown beneath every "To fix" block across the diagnostics UI (DEC-113).
# Lives here (the lowest-level guidance module) so both the dual-chip warning
# and the readiness "To fix" guidance can share it without a circular import.
REMEDIATION_DISCLAIMER = (
    "These steps change kernel parameters or driver/module configuration. "
    "Apply them at your own risk and back up your configuration first — an "
    "incorrect kernel parameter can stop the system booting."
)


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
    """Board-vendor + chip combination with known BIOS interference.

    `platform` (DEC-110) optionally scopes a quirk to one CPU vendor:
    ``"intel"`` / ``"amd"`` / ``None`` (matches any). Used to distinguish
    e.g. the MSI Z890 NCT6687DR ``msi_alt1`` quirk from MSI AMD X870E
    boards that ship the same chip but a different BIOS surface. Empty
    or ``None`` preserves the pre-DEC-110 behaviour (matches all).

    `board_pattern` (DEC-110) optionally scopes a quirk to a board-name
    substring (case-insensitive) on top of vendor + chip. Used when a
    chip name appears on boards from the same vendor across multiple
    platforms (e.g. NCT6687D auto-detected on MSI Z690/Z790 vs.
    NCT6687DR ``msi_alt1`` on MSI Z890). Empty string preserves the
    pre-DEC-110 behaviour (no board scoping).
    """

    vendor_pattern: str
    chip_prefix: str
    severity: str  # "critical" | "high" | "medium" | "info"
    summary: str
    details: list[str] = field(default_factory=list)
    platform: str | None = None  # "intel" | "amd" | None (DEC-110)
    board_pattern: str = ""  # case-insensitive substring (DEC-110)


CHIP_GUIDANCE_DB: list[ChipGuidance] = [
    # DEC-106: narrower nct679x entries take precedence over the generic
    # nct679 fallthrough below thanks to longest-prefix matching in
    # `lookup_chip_guidance`. Each entry calls out a chip-specific quirk
    # or supported-board hint without changing the underlying driver
    # binding (still `nct6775` in-kernel for all of them).
    ChipGuidance(
        chip_prefix="nct6799",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "ASRock X870E Taichi Lite uses NCT6799D as the SECONDARY chip "
            "(alongside an NCT6686 primary). Both `nct6775` and `nct6687d` "
            "are legitimately loaded on that board — see the ASRock + "
            "dual-Nuvoton vendor quirk.",
        ],
        notes=(
            "Nuvoton NCT6799D — mainline kernel support. Shipped on some "
            "AM5 800-series ASRock boards (e.g. X870E Taichi Lite) as the "
            "secondary Super-I/O chip."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6798",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "Chip ID 0xd428 — distinct from NCT6797D's 0xd450. Out-of-tree "
            "`nct6687` does not target this chip, so the DEC-105 brick-risk "
            "module collision does not apply to a single-chip NCT6798D board.",
        ],
        notes=(
            "Nuvoton NCT6798D — mainline kernel support. Common on AM4 "
            "500-series and AM5 600-series ASUS / ASRock boards "
            "(e.g. ASRock B550 Steel Legend, ASUS TUF GAMING X570-PLUS)."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6796",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "NCT6796D-S variant appears on ASRock X870 Nova (Fred78290/nct6687d "
            "issue #153). The in-kernel `nct6775` driver binds it cleanly.",
        ],
        notes=(
            "Nuvoton NCT6796D / NCT6796D-S — mainline kernel support. Shipped "
            "on some AM5 800-series ASRock boards."
        ),
    ),
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
            "ACPI conflicts: keep the driver current first — 2026-03+ builds default "
            "MMIO on, which sidesteps the port claim on this chip generation "
            "(frankcrawford/it87 issue #92). If the bind still fails, prefer the "
            "driver-local 'ignore_resource_conflict=1' over the system-wide "
            "'acpi_enforce_resources=lax' kernel parameter.",
        ],
        known_issues=[
            "Out-of-tree driver required for fan control. Mainline gains IT8689E "
            "*sensor* support in kernel 7.1 (commit 66b8eaf, 2026-03-31) — no "
            "released stable kernel ships it yet, and Gigabyte fan control still "
            "needs the DKMS build.",
            "IT8689E Rev 1 (e.g. X670E Aorus Master): the EC's vector-curve control "
            "overrides the chip's manual-mode register, so PWM writes are silently "
            "accepted with zero effect while a normal BIOS curve is active. Upstream "
            "now documents a working fix (frankcrawford/it87 issue #96, 2026-03): a "
            "flat 7-point BIOS curve (PWM 40/40/40/40/40/40 with the final point at "
            "100) restores driver manual control.",
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
        notes=(
            "ITE IT8625E — requires out-of-tree driver. Mainlining is in "
            "flight (lore v2 patch series) but not landed as of 2026-06 "
            "(kernel 7.1-rc)."
        ),
    ),
    # DEC-144: IT87952E — the secondary Super-I/O on dual-chip Gigabyte
    # AORUS boards (X870E/X670E/Z690/Z790/Z890 generations). Mainline
    # gained the chip ID in kernel 6.4 (torvalds/linux d44cb4c), so the
    # in-kernel driver can *enumerate* it — but secondary-chip fan
    # control on these boards comes from the DKMS build's ISA-bridge
    # MMIO/H2RAM access path (frankcrawford/it87 PR #102, issue #64).
    ChipGuidance(
        chip_prefix="it87952",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in); control needs it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        known_issues=[
            "Mainline kernel ≥ 6.4 enumerates IT87952E (sensors/RPM), but on "
            "dual-chip Gigabyte boards fan CONTROL of this secondary chip needs "
            "the frankcrawford/it87 DKMS build — its ISA-bridge MMIO/H2RAM path "
            "(merged 2026-04, PR #102) plus the smartfan-enable handling "
            "(issue #64, closed 2025-12) made these headers writable.",
            "Older DKMS builds (pre-2026-03) need 'options it87 mmio=on'; "
            "current builds default MMIO on (PR #95).",
        ],
        notes=(
            "ITE IT87952E — secondary chip on dual-IO Gigabyte AORUS boards. "
            "Enumeration is mainline ≥ 6.4; reliable fan control comes from a "
            "current it87-dkms-git build."
        ),
    ),
    # DEC-144: IT8665E (X399/TR4-era boards, e.g. ASUS ROG Zenith Extreme)
    # is NOT in the mainline it87 enum — it needs the DKMS build. Current
    # master (2026-03+) defaults mmio=on, and that default BREAKS IT8665E
    # PWM writes (maintainer-confirmed broken legacy FEAT_MMIO path,
    # frankcrawford/it87 issue #106, open). `mmio=off` is the remediation.
    ChipGuidance(
        chip_prefix="it8665",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        known_issues=[
            "2026-03+ DKMS builds default mmio=on, which BREAKS IT8665E fan "
            "control: PWM writes are mangled (writing 180 stores ~4). "
            "Maintainer-confirmed regression in the legacy FEAT_MMIO path "
            "(frankcrawford/it87 issue #106 — open, no fix merged as of "
            "2026-06).",
            "Remediation: disable MMIO for this chip — create "
            "/etc/modprobe.d/it87.conf with 'options it87 mmio=off' and "
            "reboot.",
        ],
        notes=(
            "ITE IT8665E — X399/TR4-era boards (e.g. ASUS ROG Zenith "
            "Extreme). Requires the out-of-tree driver; run it with "
            "mmio=off on current builds (issue #106)."
        ),
    ),
    # DEC-144: IT8622E is in the mainline it87 enum (verified against
    # torvalds/linux drivers/hwmon/it87.c v6.17 `enum chips`) — no DKMS
    # build required. Listed so boards with this chip resolve to honest
    # "built-in" guidance instead of the generic it87 fallthrough.
    ChipGuidance(
        chip_prefix="it8622",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/it87.html",
        notes="ITE IT8622E — supported in the mainline kernel it87 driver.",
    ),
    # DEC-106 (D4.A), refreshed DEC-144: IT8883 is a new ITE chip that
    # ships on Gigabyte X870 AORUS STEALTH ICE as the secondary Super-I/O
    # (alongside IT8696E; dmesg shows DEVIDs 0x8696 + 0x8883). Re-checked
    # 2026-06: still NO Linux driver — zero matches in frankcrawford/it87
    # master and mainline `it87`. This entry exists so the GUI can name
    # the chip and explain the situation rather than rendering "Unknown
    # chip" and leaving users guessing. Tracking: frankcrawford/it87
    # issue #81 (open). Re-evaluate when a driver ships.
    ChipGuidance(
        chip_prefix="it8883",
        driver_name="(none — chip unsupported on Linux as of 2026-06)",
        in_mainline=False,
        driver_package="(no driver available)",
        driver_url="https://github.com/frankcrawford/it87/issues/81",
        known_issues=[
            "No Linux driver currently supports this chip — fan headers "
            "and sensors wired through IT8883 are not visible to Linux.",
            "Some Gigabyte X870 boards (X870 AORUS STEALTH ICE) pair this "
            "chip as a secondary alongside IT8696E. On current (2026-04+) "
            "it87-dkms-git builds the primary IT8696E headers — including "
            "ones that previously refused control — are fully controllable; "
            "IT8883-attached headers (e.g. the water-pump header) remain "
            "unreachable.",
            "Tracking upstream: frankcrawford/it87 issue #81 (open as of 2026-06).",
        ],
        notes=(
            "ITE IT8883 — preliminary entry (DEC-106 / D4.A, refreshed "
            "DEC-144). No driver available as of 2026-06. Users with this "
            "chip should not expect Linux fan control on its headers."
        ),
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
            "Kernel-documented AM4 boards: PRIME X470-PRO, ROG STRIX B450-E "
            "GAMING, ROG STRIX B450-F GAMING, ROG STRIX B450-I GAMING, "
            "ROG STRIX X470-F GAMING, ROG STRIX X470-I GAMING.",
            "More frequent polling increases the risk of triggering firmware bugs.",
        ],
        notes=(
            "ASUS WMI Sensors — exposes extra sensors via BIOS WMI interface. "
            "Read-only. Poll conservatively to avoid firmware bugs."
        ),
    ),
    ChipGuidance(
        chip_prefix="asus_atk0110",
        driver_name="asus_atk0110",
        in_mainline=True,
        driver_package="linux (built-in)",
        # No kernel.org hwmon doc page exists for asus_atk0110 (verified
        # absent from https://docs.kernel.org/hwmon/index.html). Link to
        # the mainline driver source instead.
        driver_url=("https://github.com/torvalds/linux/blob/master/drivers/hwmon/asus_atk0110.c"),
        known_issues=[
            "Sensor-enrichment driver only — does NOT provide PWM write capability.",
            "Loaded automatically on many ASUS boards via ACPI ATK0110 device.",
            "Look for nct6775, it87, or another Super I/O driver as the actual PWM write path.",
        ],
        notes=(
            "ASUS ATK0110 ACPI hwmon — exposes board sensors via the ACPI ATK0110 "
            "method. Read-only. Found on a wide range of ASUS boards spanning "
            "AM3/AM4/AM5 generations."
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
            "IT8689E Rev 1 (e.g. X670E Aorus Master): the EC's vector-curve "
            "control overrides the chip's manual-mode register, so PWM writes "
            "are silently accepted with zero hardware effect while a normal "
            "BIOS fan curve is active. Upstream now documents a working fix "
            "(frankcrawford/it87 issue #96, 2026-03): configure a FLAT "
            "7-point BIOS curve — PWM 40/40/40/40/40/40 with the final point "
            "at 100 — and driver manual control works again.",
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
            "Keep it87-dkms-git current before reaching for parameters: "
            "2026-03+ builds default MMIO on, which sidesteps the ACPI I/O "
            "port claim on newer ITE chips (frankcrawford/it87 issue #81 "
            "discussion), and master carries a built-in DMI ACPI-exemption "
            "table (it87_acpi_ignore) for known-safe boards.",
            "If ACPI I/O port conflicts still block the bind, prefer the "
            "driver-local 'ignore_resource_conflict=1' parameter for the "
            "it87 module over the system-wide 'acpi_enforce_resources=lax' "
            "kernel parameter.",
            "Add 'options it87 ignore_resource_conflict=1' to "
            "/etc/modprobe.d/it87.conf to persist across reboots.",
            "Note: this is still inherently risky as ACPI and the driver may "
            "access the chip concurrently — use only when needed.",
            "Do NOT use 'force_id' as a workaround — it is intended for "
            "testing only and should not be used in production.",
        ],
    ),
    # ── AM4 400-series additions (DEC-105) ──────────────────────────
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6797",
        severity="critical",
        summary=(
            "MSI AM4 + NCT6797D — out-of-tree nct6687 can mis-claim this chip "
            "and corrupt fan registers"
        ),
        details=[
            "Older out-of-tree nct6687 builds declare chip ID 0xd450 — the "
            "same ID assigned to the legitimate NCT6797D found on MSI AM4 "
            "boards (B450M MORTAR, X470 GAMING PRO CARBON, MAG B450 TOMAHAWK "
            "MAX, and similar). When both nct6687 and nct6775 are loaded, "
            "whichever driver binds first claims the chip and the other may "
            "write into the wrong registers. The 0xd450 claim was removed "
            "upstream in Fred78290/nct6687d PR #164 (2026), so updating the "
            "driver removes this mechanism — but already-loaded modules and "
            "not-yet-updated packages remain at risk.",
            "Public incident: a user lost their CPU fan header on an MSI MAG "
            "X570 TOMAHAWK WIFI because nct6687 wrote into NCT6797D's "
            "non-volatile state. Same chip family is used on AM4 400-series "
            "MSI boards.",
            "If diagnostics detected the (nct6687, nct6775) collision, DO NOT "
            "write PWM until you have resolved the load ordering.",
            "Workaround: identify which chip the board actually has "
            "(cat /sys/class/hwmon/hwmon*/name on a known-good kernel) and "
            "blacklist the wrong driver. For NCT6797D, blacklist nct6687: "
            "echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf",
            "The Bazzite report (ublue-os/bazzite #4498) documents a bricked "
            "CPU_FAN header from this exact collision and requests a default "
            "nct6687 blacklist; as of writing that blacklist is not yet "
            "shipped, so do not assume your distro handles this for you.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6798",
        severity="critical",
        summary=(
            "MSI + NCT6798D — out-of-tree nct6687 can mis-claim this chip and corrupt fan registers"
        ),
        details=[
            "Same trap as NCT6797D: the out-of-tree nct6687 driver overlaps "
            "the chip ID space, so concurrent loading with nct6775 can leave "
            "the wrong driver bound and writes can scribble into non-volatile "
            "fan registers.",
            "If diagnostics detected the (nct6687, nct6775) collision, DO NOT "
            "write PWM until you have resolved the load ordering.",
            "Workaround: blacklist nct6687 unless you are intentionally "
            "running the out-of-tree driver on a board that needs it.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6795",
        severity="info",
        summary="MSI AM4 + NCT6795D — mainline kernel coverage is solid",
        details=[
            "NCT6795D is the chip on common AM4 400-series MSI boards such "
            "as the X470 GAMING PRO. The in-kernel nct6775 driver supports "
            "monitoring and PWM writes out of the box.",
            "Do NOT install the out-of-tree nct6687 driver on these boards — "
            "it overlaps the chip ID space of NCT6797D (a different chip on "
            "other MSI SKUs) and can race with nct6775 on systems that load "
            "both.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="asus_wmi_sensors",
        severity="high",
        summary=("ASUS AM4 + asus_wmi_sensors — kernel-documented buggy WMI on specific boards"),
        details=[
            "Kernel docs explicitly list these AM4 boards as supported AND "
            "warn about firmware bugs: PRIME X470-PRO, ROG STRIX B450-E "
            "GAMING, ROG STRIX B450-F GAMING, ROG STRIX B450-I GAMING, "
            "ROG STRIX X470-F GAMING, ROG STRIX X470-I GAMING.",
            "PRIME X470-PRO is called out specifically as triggering fans "
            "stopping, fans stuck at maximum, or sensors freezing under "
            "heavy polling.",
            "The daemon polls at 1 Hz which is within the kernel-documented "
            "safe band. Avoid running additional tools (Open Hardware Monitor, "
            "lm-sensors GUIs, fan-control daemons) against these sensors at "
            "the same time.",
            "asus_wmi_sensors is sensor enrichment ONLY — it never provides "
            "the PWM write path. Look for nct6775 (NCT6798D etc.) as the "
            "actual fan-control driver.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="asus_ec_sensors",
        severity="info",
        summary="ASUS AM4 + asus_ec_sensors — PRIME X470-PRO sensor enrichment",
        details=[
            "PRIME X470-PRO is the only AM4 400-series board on the kernel "
            "asus_ec_sensors list (the rest are X570/X670/X870 territory).",
            "Sensor enrichment only — NOT a PWM write path. Look elsewhere "
            "(typically nct6798) for actual fan control.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="asus_atk0110",
        severity="info",
        summary="ASUS + asus_atk0110 — ACPI sensor read-only path",
        details=[
            "asus_atk0110 exposes board sensors via the ACPI ATK0110 method. It is read-only.",
            "If you see this driver loaded but no controllable PWM headers, "
            "the PWM path is on a separate Super I/O driver (nct6775, it87, "
            "or similar). Check that driver's binding status.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6779",
        severity="info",
        summary="ASRock AM4 + NCT6779D — mainline kernel coverage is solid",
        details=[
            "NCT6779D is the chip on common AM4 400-series ASRock boards. "
            "The in-kernel nct6775 driver supports monitoring and PWM writes "
            "out of the box.",
            "If headers appear read-only, the cause is usually a BIOS "
            "'Smart Fan' override rather than a driver problem — disable "
            "Smart Fan for the affected header in BIOS.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6792",
        severity="info",
        summary="ASRock AM4 + NCT6792D — mainline kernel coverage is solid",
        details=[
            "NCT6792D is the chip on AM4 400-series ASRock ITX/AC boards "
            "(e.g. B450 Gaming ITX/AC). The in-kernel nct6775 driver "
            "supports monitoring and PWM writes.",
            "Fan headers: CPU_FAN1, CHA_FAN1, CHA_FAN2 per the upstream "
            "lm-sensors config — values come through libsensors-resolved "
            "labels rather than the in-repo fallback table.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8686",
        severity="info",
        summary=("Gigabyte AM4 400-series AORUS + IT8686E — dual-chip board (secondary IT8792E)"),
        details=[
            "AM4 400-series AORUS boards (X470 AORUS ULTRA GAMING, X470 "
            "AORUS GAMING 5/7 WIFI, B450 AORUS PRO/PRO-CF) pair the primary "
            "IT8686E with a secondary IT8792E for additional fan headers.",
            "If the diagnostics page reports a missing chip, update "
            "it87-dkms-git first — 2026-03+ builds default mmio=on and merge "
            "the ISA-bridge MMIO path that fixes secondary-chip enumeration "
            "(frankcrawford/it87 PR #95/#102). On older builds set "
            "'options it87 mmio=on' in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot.",
            "The secondary IT8792E was historically read-only on some "
            "Gigabyte AM4 boards; verify per-header writability before "
            "assigning fans to it in profiles.",
        ],
    ),
    # ── DEC-106: AM4 500-series, AM5 600-series, AM5 800-series ──
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8688",
        severity="info",
        summary="Gigabyte AM4 500-series AORUS + IT8688E — common dual-chip topology",
        details=[
            "Most AM4 500-series Gigabyte AORUS boards (X570 AORUS MASTER/"
            "PRO/PRO WIFI/ULTRA, B550 VISION D) pair the primary IT8688E "
            "with a secondary IT8792E for additional fan headers. "
            "Single-chip variants (B550M AORUS PRO) ship only the IT8688E.",
            "If the diagnostics page reports a missing secondary chip, "
            "update it87-dkms-git first — 2026-03+ builds default mmio=on "
            "and merge the ISA-bridge MMIO path that fixes secondary-chip "
            "enumeration (frankcrawford/it87 PR #95/#102). On older builds "
            "set 'options it87 mmio=on' in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot — it can leave "
            "the SuperIO bridge in configuration mode (frankcrawford/it87 "
            "issue #70).",
            "X570-generation boards can lose IT8792E fan control after "
            "suspend/resume (frankcrawford/it87 issue #99) — still "
            "reproducible on current driver builds as of 2026-05, with no "
            "confirmed upstream fix. The daemon re-asserts pwm_enable after "
            "resume; if headers stay stuck, a reboot is the reliable reset.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="info",
        summary="MSI AM5 800-series + nct6687d — msi_alt1 auto-allowlist",
        details=[
            "nct6687d v2.x ships an auto-enabled board allowlist covering "
            "33 MSI AM5 boards across B840 / B850 / X870 / Z890 (see "
            "Fred78290/nct6687d source: `nct6687.c::msi_alt1_dmi_table`). "
            "On listed boards the driver enables the alt1 register layout "
            "automatically — no module parameter required.",
            "If your MSI X870/B850 board is NOT on the allowlist and "
            "system fans don't respond to PWM writes, try loading with "
            "msi_alt1=1 (or msi_fan_brute_force=1 on older driver builds): "
            "`sudo modprobe -r nct6687 && sudo modprobe nct6687 msi_alt1=1`. "
            "Persist via /etc/modprobe.d/nct6687.conf.",
            "Per the same upstream source, `msi_fan_brute_force=1` remains "
            "the manual override for unlisted boards.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="medium",
        summary="MSI AM4 500-series + NCT6687-R — out-of-tree driver path",
        details=[
            "MSI AM4 500-series boards with NCT6687-R (MAG B550 TOMAHAWK, "
            "MAG B550 A-PRO, MPG X570 variants) need the out-of-tree "
            "`nct6687d` driver from Fred78290/nct6687d. The in-kernel "
            "`nct6683` driver may surface monitoring but PWM writes "
            "typically don't take effect.",
            "BIOS: disable 'Smart Fan Mode' in Hardware Monitor for the "
            "fan headers you want to control from Linux, otherwise headers "
            "may appear read-only.",
            "Reminder: NCT6687-R has chip ID 0xd590 (no overlap with "
            "NCT6797D's 0xd450) — loading nct6687d alongside the kernel's "
            "nct6775 on a genuine NCT6687-R board is safe. The DEC-105 "
            "brick risk applies only to single-chip boards where the chip "
            "is actually NCT6797D and nct6687d mis-claims it.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6799",
        severity="info",
        summary="ASRock X870E Taichi Lite — legitimate dual-Nuvoton config",
        details=[
            "ASRock X870E Taichi Lite ships TWO Super-I/O chips: NCT6686 "
            "at I/O 0x0a20 (bound by nct6687d) and NCT6799 at I/O 0x0290 "
            "(bound by mainline nct6775). Both drivers MUST be loaded "
            "concurrently to control all fan headers.",
            "DEC-106 refines the daemon's collision detector so this "
            "configuration is no longer flagged CRITICAL. The brick risk "
            "from DEC-105 only applies to SINGLE-chip boards where the "
            "chip ID 0xd450 (NCT6797D) is ambiguously claimed; on Taichi "
            "Lite each driver binds to its own physical chip.",
            "If the diagnostics page does surface a (nct6687, nct6775) "
            "collision banner on this board, it means only one nct6 chip "
            "enumerated — verify both chips appear in "
            "`cat /sys/class/hwmon/hwmon*/name` before changing module "
            "blacklists.",
            "References: Fred78290/nct6687d issue #155, "
            "Level1Techs ASRock Taichi X870E forum thread.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6798",
        severity="info",
        summary="ASRock AM4 500-series + NCT6798D — mainline kernel coverage",
        details=[
            "ASRock AM4 500-series boards with NCT6798D (B550 Steel Legend, "
            "X570 Taichi non-Razer-Edition, B550 PG Velocita) are covered "
            "by the in-kernel `nct6775` driver. No out-of-tree driver "
            "needed.",
            "If headers appear read-only the usual cause is BIOS 'Smart "
            "Fan' overriding manual mode — disable it for the affected "
            "header in BIOS, or set fan mode to 'Full Speed'.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6796",
        severity="info",
        summary="ASRock X870 Nova + NCT6796D-S — mainline kernel coverage",
        details=[
            "ASRock X870 Nova ships NCT6796D-S (per Fred78290/nct6687d "
            "issue #153). The in-kernel `nct6775` driver binds it cleanly; "
            "no out-of-tree driver needed.",
            "Do NOT load nct6687d on this board — it can mis-claim Nuvoton "
            "chips at the contested chip-ID space.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asus",
        chip_prefix="nct6798",
        severity="info",
        summary="ASUS AM4 500-series & AM5 600-series + NCT6798D — mainline",
        details=[
            "ASUS AM4 500-series (TUF GAMING X570-PLUS, ROG STRIX X570/"
            "B550 series) and AM5 600-series boards commonly ship NCT6798D, "
            "covered by mainline `nct6775`. Many also expose extra sensors "
            "via `asus_ec_sensors` — that is a READ-ONLY enrichment path, "
            "not the PWM control path.",
            "Check `asus_ec_sensors` and `asus_wmi_sensors` mainline "
            "allowlists for your specific board: kernel docs at "
            "docs.kernel.org/hwmon/asus_ec_sensors.html.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="medium",
        summary="Gigabyte X870 AORUS STEALTH ICE + IT8883 — unsupported secondary chip",
        details=[
            "Gigabyte X870 AORUS STEALTH ICE pairs the primary IT8696E "
            "(supported via it87-dkms-git) with a SECONDARY IT8883 chip "
            "that has NO Linux driver as of 2026-06 (dmesg shows DEVIDs "
            "0x8696 + 0x8883). Fan headers wired through IT8883 are "
            "uncontrollable from Linux.",
            "On current (2026-04+) it87-dkms-git builds the primary "
            "IT8696E headers — including ones that previously refused "
            "control on this board — are fully controllable.",
            "Tracking upstream: frankcrawford/it87 issue #81 (open).",
            "Practical advice: use only the primary-chip fan headers, or "
            "attach IT8883-wired fans (e.g. the water pump) to an "
            "OpenFanController / external controller.",
        ],
    ),
    # ── DEC-144: B650 GAMING X AX V2 ACPI bind failure ──────────────
    # frankcrawford/it87 issue #92: this board's firmware claims the
    # Super-I/O ports via ACPI, so `modprobe it87` fails with "Device or
    # resource busy". The driver's built-in DMI ACPI-exemption table
    # (it87_acpi_ignore) does NOT include this board as of 2026-06, so
    # the driver-local parameter remains the documented remediation.
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="medium",
        platform="amd",
        board_pattern="B650 GAMING X AX V2",
        summary="Gigabyte B650 GAMING X AX V2 — ACPI conflict can block the it87 bind",
        details=[
            "This board's firmware claims the Super-I/O I/O ports via ACPI, "
            "so `modprobe it87` can fail with 'Device or resource busy' "
            "(frankcrawford/it87 issue #92; IT8689E rev 2 at 0x0a40).",
            "Update it87-dkms-git first: 2026-03+ builds default MMIO on, "
            "which per the same issue sidesteps the port conflict on this "
            "chip.",
            "If the bind still fails (or on older builds), use the "
            "driver-local parameter: 'options it87 "
            "ignore_resource_conflict=1' in /etc/modprobe.d/it87.conf — "
            "preferred over the system-wide acpi_enforce_resources=lax.",
            "The driver's built-in DMI ACPI-exemption table does NOT "
            "include this board as of 2026-06, so do not assume a driver "
            "update alone removes the need for the parameter when MMIO is "
            "disabled.",
        ],
    ),
    # ── DEC-110: Intel platform quirks (LGA1700 / LGA1851) ─────────
    # Each entry is platform-scoped so that boards from the same vendor on
    # the opposite platform (e.g. MSI AMD X870E) do not match. Sources
    # cited in DEC-110 / docs/23.
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="asus_ec_sensors",
        severity="info",
        platform="intel",
        summary="ASUS Intel Z690/Z790 + asus_ec_sensors — kernel-documented allowlist",
        details=[
            "Kernel docs (docs.kernel.org/hwmon/asus_ec_sensors.html) "
            "list the following Intel LGA1700 boards as natively "
            "supported by the in-tree asus_ec_sensors driver: ROG "
            "MAXIMUS Z690 FORMULA, ROG STRIX Z690-A GAMING WIFI D4, "
            "ROG STRIX Z690-E GAMING WIFI, ROG STRIX Z790-E GAMING "
            "WIFI II, ROG STRIX Z790-H GAMING WIFI, ROG STRIX Z790-I "
            "GAMING WIFI. The driver provides semantic sensor labels "
            "(VRM, T_Sensor, Water_In/Out, Chipset).",
            "asus_ec_sensors is sensor enrichment only — it never "
            "provides the PWM write path. Fan control on these boards "
            "still uses nct6798 / nct6799 via the mainline nct6775 "
            "driver. If the diagnostics page lists no controllable "
            "headers, check that nct6775 is loaded.",
            "Unlike the AMD side, ASUS Intel WMI sensor bugs (PRIME "
            "X470-PRO etc.) DO NOT apply here — asus_wmi_sensors is "
            "AMD-only per upstream kernel docs.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asustek",
        chip_prefix="nct6798",
        severity="info",
        platform="intel",
        summary="ASUS Intel Z690/Z790 + NCT6798D — mainline kernel coverage",
        details=[
            "ASUS LGA1700 boards (ROG STRIX Z690/Z790, TUF GAMING "
            "Z690/Z790, PRIME Z690/Z790 series) commonly ship NCT6798D "
            "as the primary Super-I/O chip. The in-kernel nct6775 "
            "driver supports monitoring and PWM writes out of the box.",
            "The DEC-105 chip-ID overlap warning (NCT6797D vs out-of-"
            "tree nct6687) does NOT apply on these Intel boards — they "
            "ship NCT6798D (chip ID 0xd428), not NCT6797D (0xd450). "
            "Do NOT install the out-of-tree nct6687d driver on ASUS "
            "LGA1700 boards.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="info",
        platform="intel",
        summary="MSI Intel Z690/Z790 + NCT6687D (plain) — auto-detect, no msi_alt1",
        details=[
            "Per Fred78290/nct6687d (nct6687.c::msi_alt1_dmi_table), "
            "the plain NCT6687D chip on MSI Intel Z690/Z790 boards "
            "(MAG MORTAR, MPG EDGE, MEG ACE, PRO-A) is auto-detected "
            "without `msi_alt1` — the default register mapping is "
            "correct for this generation.",
            "If system fans don't respond to PWM writes despite the "
            "driver loading cleanly, the most common cause is BIOS "
            "Smart Fan overriding manual mode. Disable Smart Fan Mode "
            "in BIOS → Hardware Monitor for each header you want to "
            "control from Linux.",
            "Distinct from MSI Z890 NCT6687DR which needs `msi_alt1` — "
            "see the Z890-scoped quirk for that case.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="high",
        platform="intel",
        board_pattern="Z890",
        summary="MSI Z890 + NCT6687DR — needs msi_alt1 module parameter",
        details=[
            "MSI Z890 boards ship NCT6687DR (NCT6687D-Refresh). Per "
            "Fred78290/nct6687d (nct6687.c::msi_alt1_dmi_table), the "
            "alt1 register layout is required for correct PWM and "
            "fan-tach register addressing. v2.x of the out-of-tree "
            "driver auto-enables it on the Z890 allowlist.",
            "If your specific Z890 SKU is NOT yet on the upstream "
            "allowlist, load the driver with msi_alt1=1: "
            "`sudo modprobe -r nct6687 && sudo modprobe nct6687 "
            "msi_alt1=1`. Persist via /etc/modprobe.d/nct6687.conf: "
            "`options nct6687 msi_alt1=1`.",
            "Symptoms of msi_alt1 being needed-but-missing: PWM writes "
            "are accepted but fan RPM does not change, or fan-tach "
            "values read back as 0 / 65535. Check `dmesg | grep nct6687`.",
            "Same NCT6687DR chip ships on MSI AMD X870/X870E boards, "
            "but this quirk is Intel-scoped — the AMD case is covered "
            "by the existing AM5 800-series MSI quirk.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="high",
        platform="intel",
        summary="Gigabyte Intel Z690/Z790 AORUS + IT8689E — dual-chip with IT87952E",
        details=[
            "Gigabyte Intel Z690/Z790 AORUS boards (Z690 AORUS PRO, "
            "Z790 AORUS ELITE AX, Z790 AORUS MASTER, Z790 AORUS "
            "XTREME) pair the primary IT8689E with a secondary "
            "IT87952E for additional fan headers — same dual-chip "
            "topology as the AMD X670E AORUS family.",
            "If the diagnostics page reports a missing secondary chip, "
            "update it87-dkms-git first (2026-03+ builds default mmio=on "
            "and fix secondary-chip enumeration and control via the "
            "ISA-bridge MMIO path — PR #95/#102). On older builds set "
            "`options it87 mmio=on` in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot "
            "(frankcrawford/it87 issue #70).",
            "BIOS: Gigabyte SmartFan 6 actively overrides PWM unless "
            "fan mode is set to 'Full Speed' or a degenerate curve is "
            "configured. The pwm_enable watchdog detects and "
            "re-asserts manual mode, but BIOS configuration is the "
            "reliable fix.",
            "IT8689E Rev 1 boards may exhibit the silent-PWM-writes "
            "behaviour even on Intel; check `cat /sys/.../in0_input` "
            "for a revision indicator and verify writes effective "
            "before relying on Linux fan control.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="high",
        platform="intel",
        summary="Gigabyte Intel Z890 AORUS + IT8696E — dual-chip with IT87952E",
        details=[
            "Gigabyte Intel Z890 AORUS boards (Z890 AORUS MASTER, Z890 "
            "AORUS PRO, Z890 AORUS ELITE) ship the same IT8696E + "
            "IT87952E topology as their AMD X870E counterparts.",
            "Apply the same dual-chip remediation if the secondary chip "
            "fails to enumerate: update it87-dkms-git first (2026-03+ "
            "builds default mmio=on); on older builds set "
            "`options it87 mmio=on`. The daemon's expected_chips lookup "
            "covers the Z690/Z790 AORUS topologies; Z890 entries are "
            "added per-board as they are verified upstream.",
            "BIOS: SmartFan 6 'Full Speed' setting is required for "
            "Linux to keep manual fan control; otherwise the EC "
            "reclaims pwm_enable within seconds.",
        ],
    ),
    VendorQuirk(
        vendor_pattern="asrock",
        chip_prefix="nct6798",
        severity="info",
        platform="intel",
        summary="ASRock Intel Z690/Z790 + NCT6798D — mainline kernel coverage",
        details=[
            "ASRock LGA1700 boards (Z690 Steel Legend, Z690 Taichi, "
            "Z790 Steel Legend WIFI, Z790 Taichi) ship NCT6798D as the "
            "primary chip. The in-kernel nct6775 driver supports "
            "monitoring and PWM writes — no out-of-tree driver needed.",
            "If headers appear read-only, the typical cause is BIOS "
            "'Smart Fan' overriding manual mode. Disable Smart Fan for "
            "the affected header in BIOS, or set fan mode to 'Full "
            "Speed' / 'Performance'.",
            "Some ASRock Z690 Taichi-class boards expose monitoring "
            "but not PWM writes via the in-kernel driver; if writes "
            "are silently ignored, follow the verify-result diagnosis "
            "in the Diagnostics page.",
        ],
    ),
]


def lookup_vendor_quirks(
    board_vendor: str,
    chip_name: str,
    *,
    cpu_vendor: str = "",
    board_name: str = "",
) -> list[VendorQuirk]:
    """Find vendor+chip specific quirks matching a board and chip.

    DEC-110 additions:
        - ``cpu_vendor`` (``"Intel"``/``"AMD"``/``""``) filters quirks with
          a non-``None`` ``platform`` field. Empty / unknown disables
          platform filtering (matches the pre-DEC-110 behaviour).
        - ``board_name`` filters quirks with a non-empty
          ``board_pattern`` (case-insensitive substring). Empty disables.

    A quirk matches when every non-default scope field also matches —
    so pre-DEC-110 quirks (``platform=None``, ``board_pattern=""``)
    continue to match purely on vendor + chip exactly as before.
    """
    if not board_vendor or not chip_name:
        return []
    vendor_lower = board_vendor.lower()
    chip_lower = chip_name.lower()
    cpu_vendor_lower = cpu_vendor.lower()  # "intel" | "amd" | ""
    board_name_lower = board_name.lower()

    matches: list[VendorQuirk] = []
    for q in VENDOR_QUIRKS_DB:
        if q.vendor_pattern not in vendor_lower:
            continue
        if not chip_lower.startswith(q.chip_prefix):
            continue
        # Platform scope: when a quirk declares one, the caller must supply a
        # matching cpu_vendor. Unknown cpu_vendor (empty) suppresses
        # platform-scoped quirks rather than firing them indiscriminately —
        # the truthful direction is "we don't know, so don't claim".
        if q.platform is not None and (
            not cpu_vendor_lower or cpu_vendor_lower != q.platform.lower()
        ):
            continue
        # Board scope: when set, only fire if the board name contains the
        # substring (case-insensitive). Same suppression rule: empty
        # board_name skips board-scoped quirks rather than firing.
        if q.board_pattern and (
            not board_name_lower or q.board_pattern.lower() not in board_name_lower
        ):
            continue
        matches.append(q)
    return matches


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
    # DEC-105: GUI-side fallback for daemons that predate the daemon's
    # `module_collisions` field. When the daemon DOES emit module_collisions,
    # diagnostics_page.py suppresses this banner so the user does not see
    # two warnings for the same problem.
    (
        "nct6687",
        "nct6775",
        "nct6687 (out-of-tree, MSI legacy) and nct6775 (in-kernel) are both "
        "loaded. nct6687 declares chip ID 0xd450 which overlaps the legitimate "
        "NCT6797D chip — on AM4 400/500-series MSI boards the wrong driver can "
        "scribble into non-volatile fan registers (CPU_FAN has been bricked by "
        "this in the wild). Do NOT write PWM until resolved. "
        "(1) Identify the chip FIRST: cat /sys/class/hwmon/hwmon*/name. "
        "(2) For NCT6687-R (genuine MSI 500/600-series chip), blacklist "
        "nct6775. "
        "(3) For NCT6797D / NCT6798D (common on AM4 400/500 MSI boards e.g. "
        "B450M MORTAR, X470 GAMING PRO CARBON), blacklist nct6687: "
        "echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf. "
        "Blacklisting the wrong driver removes the working fan-control path.",
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
# AMD GPU advisory database (DEC-098)
#
# Knowledge entries for AMD GPU + kernel combinations. Keyed by the
# `KernelWarning.id` the daemon emits in `amd_gpu.kernel_warnings`, so the
# GUI can render a longer guidance text alongside the daemon's pre-formatted
# message. Distinct from `ChipGuidance` (Super I/O chips) so the two
# concerns don't bleed into each other.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmdGpuGuidance:
    """Per-warning-ID guidance text the GUI renders next to a kernel advisory.

    `warning_id` matches `KernelWarning.id` on the daemon. `summary` is a
    short headline; `details` is a multi-line list of bullets. References
    point at upstream sources so the user can verify and follow the
    diagnosis themselves.
    """

    warning_id: str
    summary: str
    details: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


AMD_GPU_GUIDANCE_DB: list[AmdGpuGuidance] = [
    AmdGpuGuidance(
        warning_id="rdna_hang_kernel_6_18_6_19",
        summary=(
            "Linux 6.18.x and 6.19.x on RDNA3/RDNA4 hard-hang the system. "
            "Pin to a 6.15-6.17 longterm kernel — do NOT roll back to 6.18."
        ),
        details=[
            "Phoronix (EOY 2025) reported an unbisected amdgpu regression that "
            "hard-hangs RDNA3 (RX 7000) and RDNA4 (RX 9000) GPUs under load on "
            "both kernel 6.18.x and 6.19.x — not 6.19 alone.",
            "Roll back to a 6.15-6.17 longterm kernel. 6.18 is NOT a safe "
            "target: ROCm #6101 reports kernel panics on both 6.18.20 and "
            "6.19.10, and no upstream fix or revert is confirmed.",
            "Recovery from a hang typically requires a hard reboot — running "
            "fan control on a kernel that can hang is not safe.",
        ],
        references=[
            "https://www.phoronix.com/review/old-amdgpu-eoy2025",
            "https://community.frame.work/t/attn-critical-bugs-in-amdgpu-driver-included-with-kernel-6-18-x-6-19-x/79221",
            "https://github.com/ROCm/ROCm/issues/6101",
        ],
    ),
    AmdGpuGuidance(
        warning_id="smu_mismatch_navi48_r9700",
        summary=(
            "R9700 (Navi 48 0x7551) has no working PMFW fan-control path on "
            "current kernels — an SMU interface-version mismatch (ROCm #6101)."
        ),
        details=[
            "ROCm Issue #6101 documents R9700 boards reporting SMU interface "
            "0x32 (50) while the amdgpu driver supports 0x2e (46). With no "
            "matching interface there is no usable write path: pwm1 is "
            "read-only and commanded fan changes have no effect, while the GPU "
            "can reach 109 °C under load with no dmesg 'fan failed' line.",
            "The mismatch is device-scoped (PCI 0x7551), not kernel-7.0-scoped "
            "— it is reported across every tested kernel (6.14, 6.17, 7.0). Use "
            "automatic mode (POST /gpu/{bdf}/fan/reset) until the amdgpu driver "
            "ships SMU iface 0x32.",
            "The RX 9070 XT (PCI 0x7550, revision 0xC0) and RX 9070 (0x7550, "
            "revision 0xC3) are not affected — they are device 0x7550, distinct "
            "from the R9700's 0x7551.",
        ],
        references=[
            "https://github.com/ROCm/ROCm/issues/6101",
            "https://github.com/ROCm/ROCm/issues/6155",
        ],
    ),
]


def lookup_amd_gpu_guidance(warning_id: str) -> AmdGpuGuidance | None:
    """Find the GUI-side guidance entry for a daemon-emitted kernel warning."""
    if not warning_id:
        return None
    for entry in AMD_GPU_GUIDANCE_DB:
        if entry.warning_id == warning_id:
            return entry
    return None


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
                "IT8689E Rev 1 boards (e.g. X670E Aorus Master), the EC's vector-curve "
                "control overrides manual mode while a normal BIOS fan curve is active. "
                "Fix: configure a FLAT 7-point BIOS curve (PWM 40/40/40/40/40/40 with "
                "the final point at 100) — driver manual control then works "
                "(frankcrawford/it87 issue #96). Alternatively use a different fan "
                "header or an external fan controller."
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


# ---------------------------------------------------------------------------
# Dual-chip board warning (DEC-101)
# ---------------------------------------------------------------------------


# Pretty model names for the chips we surface in the warning. Falls back to
# the upper-cased chip name for anything not in the table.
_CHIP_PRETTY_NAMES: dict[str, str] = {
    "it8688": "IT8688E",
    "it8689": "IT8689E",
    "it8696": "IT8696E",
    "it8792": "IT8792E/IT8795E",
    "it87952": "IT87952E",
    "it8686": "IT8686E",
}


def _pretty_chip(chip: str) -> str:
    return _CHIP_PRETTY_NAMES.get(chip.lower(), chip.upper())


def dual_chip_warning_html(
    board_name: str,
    expected_chips: list[str],
    detected_chip_names: list[str],
) -> str | None:
    """Return rich-text HTML for the dual-chip board warning, or None.

    Returns None when:
        - ``expected_chips`` is empty (daemon does not know this board)
        - every expected chip is in ``detected_chip_names`` (the kernel
          enumerated the full set — nothing to warn about)

    When some expected chips are missing, returns an HTML string with the
    remediation steps (driver update first; `mmio=on` modparam on
    pre-2026-03 builds; post-boot warnings — DEC-144) suitable
    for display in a `Qt.RichText` label. The wording is deliberately
    explicit about *which* chip is missing so users can correlate with
    their hardware docs.

    *board_name* is the DMI ``board_name`` (used only for the heading);
    callers should pass the empty string when DMI is unavailable and the
    function will use a generic heading instead.
    """
    if not expected_chips:
        return None

    detected_lower = {c.lower() for c in detected_chip_names}
    missing = [c for c in expected_chips if c.lower() not in detected_lower]
    if not missing:
        return None

    expected_count = len(expected_chips)
    detected_count = expected_count - len(missing)

    # Heading uses the board name verbatim when available so users
    # immediately recognise their machine.
    if board_name.strip():
        heading = (
            f"<b>Dual-chip board detected — missing PWM headers</b><br>"
            f"This board ({board_name}) is expected to expose {expected_count} ITE "
            f"Super-IO chips, but the kernel only enumerated {detected_count}: "
        )
    else:
        heading = (
            f"<b>Missing PWM headers detected</b><br>"
            f"This board is expected to expose {expected_count} ITE Super-IO chips, "
            f"but the kernel only enumerated {detected_count}: "
        )

    expected_pretty = ", ".join(f"<b>{_pretty_chip(c)}</b>" for c in expected_chips)
    missing_pretty = ", ".join(f"<b>{_pretty_chip(c)}</b>" for c in missing)
    chip_summary = (
        f"expected {expected_pretty}; missing {missing_pretty}.<br><br>"
        f"<b>Most likely cause:</b> an outdated it87 driver build, or the "
        f"SuperIO bridge left in configuration mode by an earlier process "
        f"(typically a previous run of <code>sensors-detect</code>). Current "
        f"<code>it87-dkms-git</code> builds (2026-03 onwards) reach the "
        f"secondary chip through the ISA-bridge MMIO path by default and "
        f"both enumerate <i>and</i> control it; older builds need the "
        f"<code>mmio=on</code> module parameter.<br><br>"
        f"<b>To fix:</b><br>"
        f"&nbsp;&nbsp;1. Update the driver (<code>yay -S it87-dkms-git</code> "
        f"rebuilds the current upstream snapshot against your kernel).<br>"
        f"&nbsp;&nbsp;2. Only on older (pre-2026-03) builds: create "
        f"<code>/etc/modprobe.d/it87.conf</code> with: "
        f"<code>options it87 mmio=on</code><br>"
        f"&nbsp;&nbsp;3. Avoid running <code>sensors-detect</code> after boot "
        f"(it can leave the SuperIO bridge in a bad state).<br>"
        f"&nbsp;&nbsp;4. Reboot the machine.<br>"
        f"&nbsp;&nbsp;5. Click <i>Refresh Hardware Diagnostics</i> to re-check.<br>"
        f"<i>⚠ {REMEDIATION_DISCLAIMER}</i><br><br>"
        f"<b>Still missing after reboot?</b> The frankcrawford/it87 "
        f'<a href="https://github.com/frankcrawford/it87/issues/70">issue #70</a> '
        f"thread documents the same failure mode on similar boards. See also "
        f"the project's "
        f'<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
        f'docs/19_Hardware_Compatibility.md">Hardware Compatibility Guide</a> '
        f"and the manual's "
        f'<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
        f'manual/driver-setup.md">Driver Setup guide</a>.'
    )
    return heading + chip_summary


def is_known_dual_chip_board(expected_chips: list[str]) -> bool:
    """Cheap check used by post-verify guidance (DEC-101 / 2F).

    Any board where the daemon emitted ≥2 expected chips is considered a
    dual-chip target. Single-chip lookup hits (or empty lookups) return
    False so the verify-result wording stays unchanged on those boards.
    """
    return len(expected_chips) >= 2


def dual_chip_verify_hint(
    result: str,
    expected_chips: list[str],
    detected_chip_names: list[str],
) -> str | None:
    """Return a one-line follow-up note for the verify result panel
    when the verify outcome could plausibly be tied to the dual-chip
    enumeration problem (DEC-101 / 2F).

    Triggers only on `pwm_value_clamped` and `no_rpm_effect` results
    AND when the board is a known dual-chip target with at least one
    chip missing — the union of "verify suggests something off" and
    "we know about a board-level enumeration gap that would explain
    fewer headers being available than the user expected".

    Returns None when:
        - the result is `effective` (working correctly — no dual-chip
          confusion to explain)
        - the result is `pwm_enable_reverted` or `rpm_unavailable` —
          those failures are clearly BIOS/EC-driven or wiring-driven
          and adding a dual-chip hint would just be noise
        - the board is not a dual-chip target
        - no chips are missing (all expected chips already detected)
    """
    if result not in ("pwm_value_clamped", "no_rpm_effect"):
        return None
    if not is_known_dual_chip_board(expected_chips):
        return None
    detected_lower = {c.lower() for c in detected_chip_names}
    missing = [c for c in expected_chips if c.lower() not in detected_lower]
    if not missing:
        return None
    return (
        "If you also have fan headers missing from the list (your board has "
        f"{len(expected_chips)} ITE chips but only "
        f"{len(expected_chips) - len(missing)} were enumerated), see the "
        "dual-chip notice on the Troubleshooting tab — fixing the enumeration may also "
        "make this header behave."
    )
