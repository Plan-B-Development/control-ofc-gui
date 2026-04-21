"""Chip-family knowledge base for hardware readiness guidance.

Maps Super I/O chip name prefixes to driver information, BIOS tips,
known manufacturer quirks, and external documentation links.
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
        chip_prefix="nct6683",
        driver_name="nct6683",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6683.html",
        notes="Nuvoton NCT6683 — mainline support, less common.",
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
        ],
        known_issues=[
            "Out-of-tree driver required — not in mainline kernel.",
        ],
        notes="ITE IT8689E — found on Gigabyte Z390/Z490 boards.",
    ),
    ChipGuidance(
        chip_prefix="it8696",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte boards: enable 'Full Speed' fan mode in BIOS.",
        ],
        notes="ITE IT8696E — found on newer Gigabyte boards.",
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
