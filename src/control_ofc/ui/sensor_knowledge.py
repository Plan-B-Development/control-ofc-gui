"""Sensor reading interpretation knowledge base.

Maps (chip_name, label, temp_type) -> rich, truthful descriptions for
display in tooltips and sensor panels. Separate from hwmon_guidance.py
which handles PWM writing quirks.

Also exposes :func:`temp_type_label` and :func:`kernel_doc_url_for_chip`
helpers that the Diagnostics > Sensors detail dialog and table column
both rely on, so the two surfaces can't drift on the labels.

Classification is based on verified Linux kernel documentation:
- k10temp: https://docs.kernel.org/hwmon/k10temp.html
- sbtsi_temp: https://docs.kernel.org/hwmon/sbtsi_temp.html
- nct6775: https://docs.kernel.org/hwmon/nct6775.html
- nct6683: https://docs.kernel.org/hwmon/nct6683.html
- it87: https://docs.kernel.org/hwmon/it87.html
- asus_ec_sensors: https://docs.kernel.org/hwmon/asus_ec_sensors.html
- asus_wmi_sensors: https://docs.kernel.org/hwmon/asus_wmi_sensors.html
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SensorClassification:
    """Rich interpretation of a sensor reading."""

    source_class: (
        str  # e.g. "cpu_die", "cpu_control", "amd_tsi", "board_thermistor", "vendor_labeled"
    )
    display_description: str  # Human-readable description for tooltip
    confidence: str  # "high", "medium", "low"
    notes: list[str] = field(default_factory=list)  # Additional context/caveats


# Quirk: ASUS boards with NCT6776F can report bogus CPUTIN
# Kernel docs: "On various ASUS boards with NCT6776F, CPUTIN is not really connected"
_ASUS_CPUTIN_BOGUS_CHIPS = {"nct6776"}


def classify_sensor(
    chip_name: str,
    label: str,
    temp_type: int | None = None,
    board_vendor: str = "",
) -> SensorClassification:
    """Classify a sensor based on driver, label, type, and board vendor.

    Returns a SensorClassification with source_class, description,
    confidence level, and contextual notes.
    """
    lower_label = label.lower()
    lower_vendor = board_vendor.lower()

    # -- k10temp: CPU internal sensors --------------------------------
    if chip_name == "k10temp":
        return _classify_k10temp(label, lower_label)

    # -- coretemp: Intel CPU internal sensors -------------------------
    if chip_name == "coretemp":
        return SensorClassification(
            source_class="cpu_die",
            display_description="CPU core temperature (internal Intel sensor)",
            confidence="high",
        )

    # -- sbtsi_temp: AMD SB-TSI board-side CPU interface --------------
    if chip_name == "sbtsi_temp":
        return SensorClassification(
            source_class="amd_tsi",
            display_description="Board-side CPU temperature via AMD SB-TSI",
            confidence="medium_high",
            notes=[
                "Board/firmware-accessible CPU temperature feed",
                "Not the same source as k10temp Tdie",
            ],
        )

    # -- amdgpu: GPU sensors ------------------------------------------
    if chip_name == "amdgpu":
        return _classify_amdgpu(label, lower_label)

    # -- xe / i915: Intel discrete GPU sensors (DEC-121) --------------
    if chip_name in ("xe", "i915"):
        return _classify_intel_gpu(chip_name, label)

    # -- nvme: Disk sensors -------------------------------------------
    if chip_name == "nvme":
        return SensorClassification(
            source_class="disk_composite",
            display_description=f"NVMe drive temperature ({label})",
            confidence="high",
        )

    # -- asus_ec_sensors: High-confidence ASUS EC labels --------------
    if chip_name == "asus_ec_sensors":
        return _classify_asus_ec(label, lower_label)

    # -- asus_wmi_sensors: ASUS WMI labels ----------------------------
    if chip_name == "asus_wmi_sensors":
        return _classify_asus_wmi(label, lower_label)

    # -- nct6775 family -----------------------------------------------
    if chip_name in (
        "nct6775",
        "nct6776",
        "nct6779",
        "nct6791",
        "nct6792",
        "nct6793",
        "nct6795",
        "nct6796",
        "nct6797",
        "nct6798",
        "nct6799",
    ):
        return _classify_nct6775(label, lower_label, chip_name, lower_vendor)

    # -- nct6683 family -----------------------------------------------
    if chip_name in ("nct6683", "nct6686", "nct6687"):
        return _classify_nct6683(label, lower_label, temp_type)

    # -- ITE it87 family (covers it8625, it8686, it8688, it8689, it8696, etc.)
    if chip_name.startswith("it8"):
        return _classify_it87(label, lower_label)

    # -- gigabyte_wmi -------------------------------------------------
    if chip_name in ("gigabyte_wmi", "gigabyte-wmi"):
        return SensorClassification(
            source_class="vendor_wmi_unlabeled",
            display_description=f"Gigabyte WMI temperature channel ({label})",
            confidence="low",
            notes=[
                "Linux driver does not expose a semantic label",
                "Exact mapping may differ by board and BIOS version",
            ],
        )

    # -- Unknown driver fallback --------------------------------------
    return SensorClassification(
        source_class="unknown",
        display_description=f"Temperature sensor ({label})",
        confidence="low",
        notes=[f"Driver: {chip_name}" if chip_name else "Unknown driver"],
    )


def _classify_k10temp(label: str, lower_label: str) -> SensorClassification:
    if lower_label == "tdie":
        return SensorClassification(
            source_class="cpu_die",
            display_description="CPU die temperature (internal sensor)",
            confidence="high",
            notes=["Primary CPU temperature — prefer this over Tctl"],
        )
    if lower_label == "tctl":
        return SensorClassification(
            source_class="cpu_control",
            display_description="CPU control temperature (platform cooling reference)",
            confidence="high",
            notes=[
                "Not a direct physical die reading",
                "Used by firmware for cooling decisions",
                "May differ from Tdie by design on some CPUs",
            ],
        )
    if lower_label.startswith("tccd"):
        return SensorClassification(
            source_class="cpu_ccd",
            display_description=f"CPU core-complex die temperature ({label})",
            confidence="high",
        )
    return SensorClassification(
        source_class="cpu_internal",
        display_description=f"CPU internal sensor ({label})",
        confidence="high",
    )


def _classify_amdgpu(label: str, lower_label: str) -> SensorClassification:
    if lower_label == "edge":
        return SensorClassification(
            source_class="gpu_edge",
            display_description="GPU edge temperature",
            confidence="high",
        )
    if lower_label == "junction":
        return SensorClassification(
            source_class="gpu_junction",
            display_description="GPU junction (hotspot) temperature",
            confidence="high",
            notes=["Hottest point on the GPU die"],
        )
    if lower_label == "mem":
        return SensorClassification(
            source_class="gpu_memory",
            display_description="GPU memory temperature",
            confidence="high",
        )
    return SensorClassification(
        source_class="gpu_other",
        display_description=f"GPU temperature ({label})",
        confidence="high",
    )


def _classify_intel_gpu(chip_name: str, label: str) -> SensorClassification:
    """Classify Intel discrete GPU temperature sensors (DEC-121).

    The xe/i915 drivers expose unlabelled ``tempN_input`` files, so the GUI
    sees generic labels like ``temp2``. The index→meaning mapping is taken
    verbatim from the kernel ABI doc
    ``Documentation/ABI/testing/sysfs-driver-intel-xe-hwmon`` (verified): on
    ``xe`` temps start at ``temp2`` (package), ``temp3`` VRAM,
    ``temp4`` memory-controller average, ``temp5`` GPU PCIe, ``temp6+`` VRAM
    channels. The i915 driver exposes a single ``temp1`` package sensor.
    """
    # xe: map the documented temp index to its meaning.
    xe_map = {
        "temp2": ("gpu_package", "GPU package temperature"),
        "temp3": ("gpu_memory", "GPU VRAM temperature"),
        "temp4": ("gpu_memory", "GPU memory controller temperature"),
        "temp5": ("gpu_other", "GPU PCIe temperature"),
    }
    if chip_name == "xe":
        if label in xe_map:
            source_class, desc = xe_map[label]
            return SensorClassification(
                source_class=source_class, display_description=desc, confidence="high"
            )
        if label.startswith("temp"):
            return SensorClassification(
                source_class="gpu_memory",
                display_description="GPU VRAM channel temperature",
                confidence="medium_high",
            )
    elif chip_name == "i915" and label == "temp1":
        return SensorClassification(
            source_class="gpu_package",
            display_description="GPU package temperature",
            confidence="high",
        )
    return SensorClassification(
        source_class="gpu_other",
        display_description=f"Intel GPU temperature ({label})",
        confidence="high",
    )


def _classify_asus_ec(label: str, lower_label: str) -> SensorClassification:
    """ASUS EC sensors -- high-confidence vendor-labeled data."""
    if "t_sensor" in lower_label:
        return SensorClassification(
            source_class="external_probe",
            display_description="ASUS T_Sensor header (external probe)",
            confidence="high",
            notes=["User-attached temperature probe header"],
        )
    if "vrm" in lower_label:
        return SensorClassification(
            source_class="vrm",
            display_description="VRM temperature (ASUS EC)",
            confidence="high",
        )
    if "water in" in lower_label:
        return SensorClassification(
            source_class="coolant_in",
            display_description="Water coolant inlet temperature",
            confidence="high",
            notes=["Liquid cooling probe header"],
        )
    if "water out" in lower_label:
        return SensorClassification(
            source_class="coolant_out",
            display_description="Water coolant outlet temperature",
            confidence="high",
            notes=["Liquid cooling probe header"],
        )
    if "chipset" in lower_label:
        return SensorClassification(
            source_class="chipset",
            display_description="Chipset temperature (ASUS EC)",
            confidence="high",
        )
    if "cpu" in lower_label and "package" in lower_label:
        return SensorClassification(
            source_class="cpu_package",
            display_description="CPU package temperature (ASUS EC)",
            confidence="high",
        )
    if "motherboard" in lower_label:
        return SensorClassification(
            source_class="board_ambient",
            display_description="Motherboard temperature (ASUS EC)",
            confidence="high",
            notes=["Vendor-defined board reference point"],
        )
    return SensorClassification(
        source_class="vendor_labeled",
        display_description=f"ASUS EC sensor ({label})",
        confidence="high",
    )


def _classify_asus_wmi(label: str, lower_label: str) -> SensorClassification:
    """ASUS WMI sensors -- trust labels but note polling caveat.

    Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html — the
    kernel doc explicitly cites the BIOS-update remedy for the PRIME
    X470-PRO firmware bug, so the user note carries that pointer.
    """
    base_notes = [
        "Some ASUS WMI implementations may stick with aggressive polling",
        "Durable fix is a BIOS update to WMI method version ≥ 2 "
        "(per kernel doc asus_wmi_sensors.html)",
    ]
    if "cpu" in lower_label:
        return SensorClassification(
            source_class="cpu_board_side",
            display_description=f"CPU temperature (ASUS WMI: {label})",
            confidence="medium_high",
            notes=base_notes,
        )
    if "vrm" in lower_label:
        return SensorClassification(
            source_class="vrm",
            display_description=f"VRM temperature (ASUS WMI: {label})",
            confidence="medium_high",
            notes=base_notes,
        )
    if "chipset" in lower_label:
        return SensorClassification(
            source_class="chipset",
            display_description=f"Chipset temperature (ASUS WMI: {label})",
            confidence="medium_high",
            notes=base_notes,
        )
    if "t_sensor" in lower_label:
        return SensorClassification(
            source_class="external_probe",
            display_description="ASUS T_Sensor header (ASUS WMI)",
            confidence="medium_high",
            notes=[*base_notes, "External temperature probe header"],
        )
    if "water" in lower_label:
        return SensorClassification(
            source_class="coolant",
            display_description=f"Liquid cooling sensor (ASUS WMI: {label})",
            confidence="medium_high",
            notes=base_notes,
        )
    if "motherboard" in lower_label:
        return SensorClassification(
            source_class="board_ambient",
            display_description=f"Motherboard temperature (ASUS WMI: {label})",
            confidence="medium_high",
            notes=base_notes,
        )
    return SensorClassification(
        source_class="vendor_labeled",
        display_description=f"ASUS WMI sensor ({label})",
        confidence="medium",
        notes=base_notes,
    )


def _classify_nct6775(
    label: str,
    lower_label: str,
    chip_name: str,
    lower_vendor: str,
) -> SensorClassification:
    """Nuvoton nct6775 family -- configurable sources, use label."""
    # Quirk: ASUS + NCT6776F -> CPUTIN is often bogus
    if chip_name in _ASUS_CPUTIN_BOGUS_CHIPS and "asus" in lower_vendor and lower_label == "cputin":
        return SensorClassification(
            source_class="bogus",
            display_description="CPUTIN (likely unreliable on this ASUS board)",
            confidence="low",
            notes=[
                "Kernel docs: on ASUS boards with NCT6776F, CPUTIN is often"
                " not connected or connected to a non-standard device",
                "May report unreasonably high temperatures"
                " or decline when actual temperature rises",
                "Prefer PECI 0 or TSI 0 for CPU temperature",
            ],
        )

    if "amd tsi" in lower_label or "tsi" in lower_label:
        return SensorClassification(
            source_class="amd_tsi",
            display_description=f"Board-side CPU temperature via AMD TSI ({label})",
            confidence="medium_high",
            notes=["Board/firmware-accessible CPU temperature feed"],
        )
    # DEC-110: Intel PECI labels on nct6775-family chips. The kernel driver
    # exposes Intel PECI as "PECI Agent 0", "PECI 0", or similar — without
    # a leading "CPU" keyword, so the cpu-keyword fallback below would miss
    # them. Match the "peci" substring explicitly and tag as Intel PECI
    # specifically so the tooltip says "Intel PECI" rather than the generic
    # PECI wording the AMD case used.
    if "peci" in lower_label:
        return SensorClassification(
            source_class="cpu_peci",
            display_description=f"CPU temperature via Intel PECI ({label})",
            confidence="medium_high",
            notes=["Intel CPU temperature reported via the PECI bus"],
        )
    if lower_label == "systin":
        return SensorClassification(
            source_class="board_system",
            display_description="System temperature input (SYSTIN)",
            confidence="medium",
            notes=["Board system temperature — exact placement is vendor-specific"],
        )
    if lower_label == "auxtin" or lower_label.startswith("auxtin"):
        return SensorClassification(
            source_class="board_auxiliary",
            display_description=f"Auxiliary temperature input ({label})",
            confidence="medium",
            notes=["Auxiliary board sensor — exact placement is vendor-specific"],
        )
    if lower_label == "cputin":
        return SensorClassification(
            source_class="cpu_board_side",
            display_description="CPU temperature input (board-side, CPUTIN)",
            confidence="medium",
        )
    return SensorClassification(
        source_class="super_io_channel",
        display_description=f"Super I/O temperature channel ({label})",
        confidence="medium" if label != label.lower() or "_" in label else "low",
        notes=["Source configured by board firmware"] if lower_label.startswith("temp") else [],
    )


def _classify_nct6683(
    label: str,
    lower_label: str,
    temp_type: int | None,
) -> SensorClassification:
    """Nuvoton nct6683/6686/6687 -- rich source labels and type codes."""
    # Type-based classification (from nct6683.c kernel source)
    if temp_type == 5 or "amd tsi" in lower_label:
        addr_info = f" ({label})" if "addr" in lower_label else ""
        return SensorClassification(
            source_class="amd_tsi",
            display_description=f"Board-side CPU temperature via AMD TSI{addr_info}",
            confidence="medium_high",
            notes=[
                "Board/firmware-accessible CPU temperature feed",
                "Not the same source as k10temp Tdie",
            ],
        )
    if temp_type == 4 or "thermistor" in lower_label:
        return SensorClassification(
            source_class="board_thermistor",
            display_description=f"Board thermistor channel ({label})",
            confidence="medium",
            notes=["Exact physical placement is vendor-specific"],
        )
    if temp_type == 3 or "diode" in lower_label:
        return SensorClassification(
            source_class="thermal_diode",
            display_description=f"Thermal diode channel ({label})",
            confidence="medium",
        )
    if temp_type == 6 or "peci" in lower_label:
        return SensorClassification(
            source_class="cpu_peci",
            display_description=f"CPU temperature via Intel PECI ({label})",
            confidence="medium_high",
        )
    if "dimm" in lower_label:
        return SensorClassification(
            source_class="memory_dimm",
            display_description=f"DIMM / memory temperature ({label})",
            confidence="medium",
        )
    if "smbus" in lower_label:
        return SensorClassification(
            source_class="smbus_device",
            display_description=f"SMBus temperature device ({label})",
            confidence="medium",
            notes=["Exact device identity depends on board wiring"],
        )
    if "virtual" in lower_label:
        return SensorClassification(
            source_class="virtual",
            display_description=f"Virtual / derived temperature ({label})",
            confidence="low",
            notes=["Not a direct physical sensor"],
        )
    if lower_label == "local":
        return SensorClassification(
            source_class="chip_local",
            display_description="Super I/O chip local temperature",
            confidence="medium",
        )
    return SensorClassification(
        source_class="super_io_channel",
        display_description=f"Super I/O temperature channel ({label})",
        confidence="low" if lower_label.startswith("temp") else "medium",
    )


def _classify_it87(label: str, lower_label: str) -> SensorClassification:
    """ITE it87 family -- conservative classification."""
    if lower_label.startswith("temp") and lower_label[4:].isdigit():
        return SensorClassification(
            source_class="super_io_channel",
            display_description=f"ITE Super I/O temperature channel ({label})",
            confidence="low",
            notes=["Exact physical placement not provided by the Linux driver"],
        )
    return SensorClassification(
        source_class="super_io_channel",
        display_description=f"ITE sensor ({label})",
        confidence="medium" if label != lower_label else "low",
    )


# -- Board-specific override database (future expansion) ----------


@dataclass(frozen=True)
class BoardSensorOverride:
    """Board-specific sensor identity override.

    Used when exact physical placement is known from vendor documentation,
    BIOS labels, or controlled validation. Keyed by (board_vendor_pattern,
    board_model_pattern, sensor_label).
    """

    vendor_pattern: str
    model_pattern: str
    label_pattern: str
    source_class: str
    display_description: str
    confidence: str = "high"
    notes: list[str] = field(default_factory=list)


# Citation constants used by the override entries below. Pinning them at
# module level keeps long URLs out of each entry and means a doc-page
# rename only needs to update one line.
_ASUS_EC_KERNEL_DOC_URL = "https://docs.kernel.org/hwmon/asus_ec_sensors.html"
_SBTSI_KERNEL_DOC_URL = "https://docs.kernel.org/hwmon/sbtsi_temp.html"

# Documented board-specific overrides.
# Add entries here when exact sensor placement is validated from:
# - kernel documentation
# - vendor manuals / BIOS labels
# - controlled load testing / correlation
#
# Format: BoardSensorOverride(vendor, model, label, source_class, description, confidence, notes)
# vendor/model use case-insensitive substring matching.
BOARD_SENSOR_OVERRIDES: list[BoardSensorOverride] = [
    # -- ASUS EC boards (kernel-documented sensor identities) ------
    # These boards are explicitly listed in the kernel asus_ec_sensors doc.
    # The EC driver already provides semantic labels, so these overrides
    # serve as validation anchors confirming the label -> placement mapping.
    # ASUS ROG CROSSHAIR VIII series (X570)
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="crosshair viii",
        label_pattern="T_Sensor",
        source_class="external_probe",
        display_description="T_Sensor header — external temperature probe",
        # Physical pin location varies by SKU and revision; the kernel
        # asus_ec_sensors doc only confirms the channel is exposed, not
        # where the header sits on the PCB. Consult the per-SKU ASUS
        # board manual for the exact location rather than assuming.
        notes=[
            "Header physical location varies by Crosshair VIII SKU — "
            "see the ASUS board manual for the exact pin location",
            f"Channel validated via {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    # ASUS ROG STRIX X670E series
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix x670e",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature",
        notes=[f"Validated via asus_ec_sensors kernel driver: {_ASUS_EC_KERNEL_DOC_URL}"],
    ),
    # -- ASRock X670E -- nct6686D thermistor mapping ---------------
    # ASRock X670E boards commonly use nct6686D. Channel assignments
    # are not individually documented in kernel, but the chip family
    # is listed as supported. These entries note the KNOWN UNKNOWNS.
    BoardSensorOverride(
        vendor_pattern="asrock",
        model_pattern="x670e",
        label_pattern="AMD TSI Addr 98h",
        source_class="amd_tsi",
        display_description="CPU temperature (board-side AMD TSI, socket 0)",
        confidence="high",
        # The kernel sbtsi_temp doc confirms "The SB-TSI address is
        # normally 98h for socket 0 and 90h for socket 1." Verified
        # 2026-06-03 at the URL below.
        notes=[
            "SB-TSI address 98h is normally socket 0 per kernel sbtsi_temp doc",
            f"Source: {_SBTSI_KERNEL_DOC_URL}",
        ],
    ),
    # -- Gigabyte B550/X570 -- gigabyte_wmi channel hints ----------
    # These are NOT confirmed placements -- they document the ABSENCE
    # of reliable mapping. Included so the override database explicitly
    # records what we've investigated and found unresolvable.
    BoardSensorOverride(
        vendor_pattern="gigabyte",
        model_pattern="b550",
        label_pattern="temp1",
        source_class="vendor_wmi_unlabeled",
        display_description="Gigabyte WMI channel 1 (identity unknown)",
        confidence="low",
        notes=[
            "Gigabyte Windows software may label this differently",
            "No reliable Linux-side mapping available",
        ],
    ),
    # -- ASUS AM4 400-series — kernel asus_wmi_sensors entry boards --
    # The kernel asus_wmi_sensors driver explicitly supports PRIME X470-PRO
    # and ROG STRIX B450-E/F/I + X470-F/I GAMING. PRIME X470-PRO is called
    # out in kernel docs as triggering fans stopping or being pinned at
    # maximum under heavy polling. We do not add a label_pattern entry
    # here because that requires a specific sensor name to override.
    # Vendor-level warnings live in hwmon_guidance.py
    # (`asustek + asus_wmi_sensors` quirk).
    # -- DEC-110: ASUS Intel asus_ec_sensors kernel allowlist (LGA1700) --
    # The kernel driver provides semantic labels (VRM, T_Sensor, Chipset)
    # natively, so these entries serve as validation anchors confirming
    # the label → placement mapping when present, rather than overriding.
    # Sourced verbatim from docs.kernel.org/hwmon/asus_ec_sensors.html
    # board list, filtered to LGA1700-era boards (Z690/Z790). Older
    # Z270/Z390/Z490 boards omitted — kept conservative per DEC-110 D4
    # ("only verified entries").
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="maximus z690 formula",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature (ASUS EC)",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix z690-a gaming wifi",
        label_pattern="T_Sensor",
        source_class="external_probe",
        display_description="T_Sensor header — external temperature probe",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            "DDR4 variant — D5 SKUs use a different EC path",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix z690-e gaming wifi",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature (ASUS EC)",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix z790-e gaming wifi ii",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature (ASUS EC)",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix z790-h gaming wifi",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature (ASUS EC)",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
    BoardSensorOverride(
        vendor_pattern="asus",
        model_pattern="strix z790-i gaming wifi",
        label_pattern="VRM",
        source_class="vrm",
        display_description="VRM heatsink area temperature (ASUS EC)",
        notes=[
            "Validated via asus_ec_sensors kernel driver allowlist",
            f"Source: {_ASUS_EC_KERNEL_DOC_URL}",
        ],
    ),
]


def lookup_board_override(
    board_vendor: str,
    board_model: str,
    label: str,
) -> BoardSensorOverride | None:
    """Find a board-specific override if one exists."""
    lower_vendor = board_vendor.lower()
    lower_model = board_model.lower()
    lower_label = label.lower()
    for override in BOARD_SENSOR_OVERRIDES:
        if (
            override.vendor_pattern.lower() in lower_vendor
            and override.model_pattern.lower() in lower_model
            and override.label_pattern.lower() in lower_label
        ):
            return override
    return None


# Sysfs `tempN_type` integer → human label.
#
# The current kernel hwmon sysfs interface docs
# (https://docs.kernel.org/hwmon/sysfs-interface.html) describe `tempN_type`
# as "Sensor type selection" without enumerating the integer codes
# user-facing. The enumeration below is the canonical mapping observed in
# driver source. Most-load-bearing values for our supported chip range
# (3 = diode, 4 = thermistor, 5 = AMD TSI, 6 = Intel PECI) come from the
# nct6683 driver source:
#   https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6683.c
#   (function `get_temp_type` / source-range mapping).
# Values 1 (CPU embedded diode), 2 (3904 transistor), 7 (AMD SB-TSI) are
# part of the older hwmon ABI surviving in driver source; they're rarely
# returned by drivers we currently classify but are kept here so a future
# daemon that does report them won't render as a bare integer.
_TEMP_TYPE_LABELS: dict[int, str] = {
    1: "CPU embedded diode (1)",
    2: "3904 transistor (2)",
    3: "thermal diode (3)",
    4: "thermistor (4)",
    5: "AMD TSI (5)",
    6: "Intel PECI (6)",
    7: "AMD SB-TSI (7)",
}


def temp_type_label(temp_type: int | None) -> str:
    """Render the sysfs ``tempN_type`` integer as a human label.

    Values are sourced from driver-level enumerations (see the comment on
    :data:`_TEMP_TYPE_LABELS` for the citations). Returns ``"—"`` when the
    sensor exposes no type file so the Diagnostics column never silently
    shows a misleading category.
    """
    if temp_type is None:
        return "—"
    return _TEMP_TYPE_LABELS.get(temp_type, f"unknown ({temp_type})")


# Chip-family → authoritative driver-documentation URL. Used by the Sensor
# Detail dialog to render a "Driver documentation" link. Keys are normalised
# lowercase chip name *prefixes*; lookup uses the first matching prefix so
# families like ``it87`` cover all ``it8xxx`` variants.
#
# Where a kernel.org hwmon doc exists we link to that. Where the driver has
# no published doc page (gigabyte_wmi, nvme, asus_atk0110 — verified absent
# from https://docs.kernel.org/hwmon/index.html), we link to the driver
# source in the mainline Linux tree, which IS authoritative for behaviour
# even when no user-facing doc exists.
_CHIP_DOC_URL_PREFIXES: list[tuple[str, str]] = [
    ("k10temp", "https://docs.kernel.org/hwmon/k10temp.html"),
    ("coretemp", "https://docs.kernel.org/hwmon/coretemp.html"),
    ("sbtsi_temp", "https://docs.kernel.org/hwmon/sbtsi_temp.html"),
    ("nct6775", "https://docs.kernel.org/hwmon/nct6775.html"),
    ("nct6683", "https://docs.kernel.org/hwmon/nct6683.html"),
    ("nct668", "https://docs.kernel.org/hwmon/nct6683.html"),
    ("nct679", "https://docs.kernel.org/hwmon/nct6775.html"),
    ("nct67", "https://docs.kernel.org/hwmon/nct6775.html"),
    ("it87", "https://docs.kernel.org/hwmon/it87.html"),
    ("it8", "https://docs.kernel.org/hwmon/it87.html"),
    ("asus_ec_sensors", "https://docs.kernel.org/hwmon/asus_ec_sensors.html"),
    ("asus_wmi_sensors", "https://docs.kernel.org/hwmon/asus_wmi_sensors.html"),
    ("amdgpu", "https://docs.kernel.org/gpu/amdgpu/thermal.html"),
    # No kernel.org hwmon doc page — link to mainline driver source so the
    # "Driver documentation" button doesn't 404.
    (
        "gigabyte_wmi",
        "https://github.com/torvalds/linux/blob/master/drivers/platform/x86/gigabyte-wmi.c",
    ),
    (
        "nvme",
        "https://github.com/torvalds/linux/blob/master/drivers/nvme/host/hwmon.c",
    ),
    (
        "asus_atk0110",
        "https://github.com/torvalds/linux/blob/master/drivers/hwmon/asus_atk0110.c",
    ),
]


def kernel_doc_url_for_chip(chip_name: str) -> str | None:
    """Return the kernel.org hwmon doc URL for ``chip_name``, or ``None``.

    Match is by case-insensitive prefix so e.g. ``nct6798`` resolves to
    the nct6775 family page. Returns ``None`` for unknown chips so callers
    can suppress the link rather than render a broken one.
    """
    if not chip_name:
        return None
    lower = chip_name.lower()
    for prefix, url in _CHIP_DOC_URL_PREFIXES:
        if lower.startswith(prefix):
            return url
    return None


def format_sensor_tooltip(
    classification: SensorClassification,
    sensor_id: str = "",
    chip_name: str = "",
    session_min: float | None = None,
    session_max: float | None = None,
    rate_c_per_s: float | None = None,
) -> str:
    """Build a multi-line tooltip for a sensor in the series panel."""
    lines: list[str] = []

    lines.append(classification.display_description)

    if session_min is not None and session_max is not None:
        lines.append(f"Session: {session_min:.1f}°C - {session_max:.1f}°C")

    if rate_c_per_s is not None and abs(rate_c_per_s) >= 0.1:
        direction = "+" if rate_c_per_s > 0 else ""
        lines.append(f"Rate: {direction}{rate_c_per_s:.1f}°C/s")

    if chip_name:
        lines.append(f"Driver: {chip_name}")

    confidence_labels = {
        "high": "High",
        "medium_high": "Medium-High",
        "medium": "Medium",
        "low": "Low",
    }
    conf_label = confidence_labels.get(classification.confidence, classification.confidence)
    lines.append(f"Confidence: {conf_label}")

    if classification.notes:
        lines.append("")
        for note in classification.notes[:3]:  # Cap at 3 notes for readability
            lines.append(f"• {note}")

    if sensor_id:
        lines.append(f"\nID: {sensor_id}")

    return "\n".join(lines)
