# 20 — Sensor Interpretation Guide

**Last updated:** 2026-04-22

**See also:** `22_AMD_Sensor_Interpretation_Deep_Dive.md` for a comprehensive
user-facing explanation of sensor classes, confidence levels, and quirks.

## Purpose

This document defines the sensor classification model used by the GUI to provide
truthful, provenance-aware descriptions of hardware temperature readings. The
model is implemented in `src/control_ofc/ui/sensor_knowledge.py` and drives
tooltips, series panel annotations, and diagnostic displays.

The goal is to tell the user what a sensor reading *actually represents* at the
hardware level, with an honest confidence level. The GUI should never claim more
certainty about a sensor's identity than the kernel driver and board documentation
support.

## Classification model

Each sensor reading from the daemon includes three metadata fields used for
classification:

| Field | Source | Example |
|---|---|---|
| `chip_name` | hwmon driver name from sysfs (`name` file) | `k10temp`, `nct6798`, `it8689` |
| `label` | Driver-provided label (`tempN_label` sysfs) | `Tctl`, `SYSTIN`, `temp1` |
| `temp_type` | Thermistor type code (`tempN_type` sysfs), nullable | `3` (diode), `4` (thermistor), `5` (AMD TSI), `6` (PECI) |

The classification function signature:

```python
classify_sensor(chip_name, label, temp_type=None, board_vendor="")
    -> SensorClassification(source_class, display_description, confidence, notes)
```

The `source_class` field is a machine-readable category (e.g. `cpu_die`,
`board_thermistor`, `vendor_labeled`). The `display_description` is the
human-readable string shown in tooltips. The `confidence` field indicates how
certain the classification is.

## Driver-specific rules

### k10temp (AMD CPU internal sensors)

Kernel docs: https://docs.kernel.org/hwmon/k10temp.html

| Label | source_class | Confidence | Notes |
|---|---|---|---|
| `Tdie` | `cpu_die` | high | Primary CPU die temperature. Prefer this over Tctl. |
| `Tctl` | `cpu_control` | high | Platform cooling reference. Not a direct physical reading. May differ from Tdie by a designed offset on some SKUs. |
| `Tccd1`, `Tccd2`, ... | `cpu_ccd` | high | Per-CCD (core complex die) temperature. Available on Zen 2+ with multiple CCDs. |
| Other | `cpu_internal` | high | Generic fallback for any other k10temp label. |

**Key quirk:** Tctl is not a physical temperature. It is a control value used by
the platform to drive cooling decisions. On some AMD CPUs, Tctl = Tdie + offset
(the offset varies by SKU). The GUI should always prefer Tdie when available and
should never present Tctl as the "actual" CPU temperature.

### sbtsi_temp (AMD SB-TSI board-side interface)

Kernel docs: https://docs.kernel.org/hwmon/sbtsi_temp.html

All readings classify as `amd_tsi` at `medium_high` confidence.

SB-TSI (SideBand Temperature Sensor Interface) provides a board/firmware-
accessible CPU temperature feed over SMBus. It is a separate data path from
k10temp and may report different values. The GUI notes that this is "not the
same source as k10temp Tdie."

The SB-TSI address is normally `98h` for socket 0 and `90h` for socket 1
(8-bit I2C addresses; 7-bit equivalents are 0x4C and 0x48), but may vary
with hardware address select pins. On desktop Ryzen boards (single-socket),
98h is standard. SB-TSI readings often surface through Super I/O chip
drivers (nct6683, nct6775) as labels like `AMD TSI Addr 98h` rather than
through a standalone `sbtsi_temp` device.

### nct6775 family (Nuvoton Super I/O)

Kernel docs: https://docs.kernel.org/hwmon/nct6775.html

Covers: nct6775, nct6776, nct6779, nct6791, nct6792, nct6793, nct6795, nct6796,
nct6797, nct6798, nct6799.

| Label pattern | source_class | Confidence | Notes |
|---|---|---|---|
| Contains `AMD TSI` or `TSI` | `amd_tsi` | medium_high | Board-side CPU temp via AMD TSI |
| Contains `PECI` | `cpu_peci` | medium_high | CPU temp via Intel PECI |
| `SYSTIN` | `board_system` | medium | System temperature input, exact placement is vendor-specific |
| `AUXTIN` / `AUXTINn` | `board_auxiliary` | medium | Auxiliary input, exact placement is vendor-specific |
| `CPUTIN` | `cpu_board_side` | medium | Board-side CPU temperature (see ASUS quirk below) |
| Named labels (mixed case or `_`) | `super_io_channel` | medium | Source configured by board firmware |
| Generic `tempN` | `super_io_channel` | low | No semantic information available |

These chips have configurable temperature source multiplexers. The actual physical
sensor connected to each channel is determined by the board firmware, not the
chip. The kernel driver exposes whatever label the firmware provides, which may
or may not be accurate.

### nct6683 / nct6686 / nct6687 (Nuvoton newer family)

Kernel docs: https://docs.kernel.org/hwmon/nct6683.html

AMD boards confirmed working in kernel documentation: ASRock X570, ASRock X670E,
ASRock B650 Steel Legend WiFi, MSI B550, MSI X670-P, MSI X870E.

These chips support `temp_type` codes that provide additional classification
signal beyond the label alone.

| temp_type | source_class | Confidence |
|---|---|---|
| 5 | `amd_tsi` | medium_high |
| 4 | `board_thermistor` | medium |
| 3 | `thermal_diode` | medium |
| 6 | `cpu_peci` | medium_high |

Label-based rules (applied when temp_type is absent or unknown):

| Label pattern | source_class | Confidence |
|---|---|---|
| Contains `DIMM` | `memory_dimm` | medium |
| Contains `SMBus` | `smbus_device` | medium |
| Contains `Virtual` | `virtual` | low |
| `Local` | `chip_local` | medium |

Full source label enumeration from the kernel source (`nct6683.c`):
`Local`, `Diode 0-2`, `Thermistor 0-13`, `AMD TSI Addr 90h-9dh`,
`PECI 0.0-3.1`, `PECI DIMM 0-3`, `SMBus 0-5`, `DIMM 0-3`, `Virtual 0-7`.

The temp_type codes are mapped from source ranges in the kernel:
0x02-0x07 -> type 3 (diode), 0x08-0x18 -> type 4 (thermistor),
0x42-0x49 -> type 5 (AMD TSI), 0x20-0x2b -> type 6 (Intel PECI).
Reference: `drivers/hwmon/nct6683.c`, `get_temp_type()` function.

### it87 family (ITE Super I/O)

Kernel docs: https://docs.kernel.org/hwmon/it87.html

Covers: it8625, it8686, it8688, it8689, it8696, and other `it8xxx` variants.

The it87 driver provides minimal labeling. Most sensors appear as generic
`tempN` channels with no semantic information. Classification is conservative:

| Label pattern | source_class | Confidence |
|---|---|---|
| `tempN` (numeric) | `super_io_channel` | low |
| Named label | `super_io_channel` | medium |

The driver does not expose which physical sensor type is connected to each
channel. Board-specific overrides are the primary path to higher confidence.

### asus_ec_sensors (ASUS Embedded Controller)

Kernel docs: https://docs.kernel.org/hwmon/asus_ec_sensors.html

High-confidence vendor-labeled sensors read directly from the ASUS embedded
controller. The kernel driver exposes semantic labels that map to specific
board features.

| Label pattern | source_class | Confidence |
|---|---|---|
| Contains `T_Sensor` | `external_probe` | high |
| Contains `VRM` | `vrm` | high |
| Contains `Water In` | `coolant_in` | high |
| Contains `Water Out` | `coolant_out` | high |
| Contains `Chipset` | `chipset` | high |
| Contains `CPU` + `Package` | `cpu_package` | high |
| Contains `Motherboard` | `board_ambient` | high |
| Other | `vendor_labeled` | high |

This driver only loads on explicitly supported ASUS boards (the kernel driver
has a board allowlist). All readings are high confidence because the EC provides
the identity mapping.

### asus_wmi_sensors (ASUS WMI interface)

Kernel docs: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

Same label vocabulary as `asus_ec_sensors` but accessed via WMI (Windows
Management Instrumentation) ACPI methods. Classification follows the same
label-matching rules but at `medium_high` confidence (one step lower) because
the WMI interface has known polling reliability issues on some boards.

All classifications carry a standing note about potential WMI polling issues.

### gigabyte_wmi (Gigabyte WMI interface)

All readings classify as `vendor_wmi_unlabeled` at `low` confidence. The Linux
driver does not expose semantic labels for Gigabyte WMI temperature channels.
The exact physical mapping may differ by board model and BIOS version. The
Gigabyte Windows software (System Information Viewer) may show different labels,
but those cannot be reliably mapped to the Linux sysfs channels.

### amdgpu (AMD GPU driver)

| Label | source_class | Confidence |
|---|---|---|
| `edge` | `gpu_edge` | high |
| `junction` | `gpu_junction` | high |
| `mem` | `gpu_memory` | high |
| Other | `gpu_other` | high |

GPU sensors are high confidence because the amdgpu kernel driver defines
exact label semantics. Junction is the hottest point on the die.

### nvme (NVMe drive controller)

All readings classify as `disk_composite` at `high` confidence. NVMe drives
report a composite temperature per the NVMe specification. The label from the
kernel is included in the description.

### coretemp (Intel CPU internal sensors)

All readings classify as `cpu_die` at `high` confidence. The coretemp driver
reads per-core DTS (Digital Thermal Sensor) values from Intel CPUs.

## Confidence levels

| Level | Meaning | When to use |
|---|---|---|
| `high` | Identity is certain from kernel documentation or hardware specification | k10temp labels, amdgpu labels, asus_ec_sensors, nvme, coretemp |
| `medium_high` | Identity is very likely correct but depends on board firmware or an indirect interface | PECI/TSI-typed channels, sbtsi_temp, asus_wmi_sensors |
| `medium` | Identity is plausible based on label conventions but cannot be confirmed without board docs | Named nct6775 channels (SYSTIN, CPUTIN), nct6683 thermistor/diode type, named ITE labels |
| `low` | Identity is unknown or unreliable | Generic `tempN` labels, gigabyte_wmi channels, virtual sensors |

## Documented quirks

### Tctl is not a physical temperature

AMD's Tctl (Control Temperature) is a derived value used by the platform cooling
algorithm. On some SKUs it equals Tdie; on others it has a designed positive
offset. Displaying Tctl as the "CPU temperature" can mislead users into thinking
their CPU is hotter than it actually is. The GUI should prefer Tdie when both are
available and should annotate Tctl with a note explaining its nature.

Reference: https://docs.kernel.org/hwmon/k10temp.html

### ASUS CPUTIN bogus on NCT6776F

The kernel documentation states: "On various ASUS boards with NCT6776F, CPUTIN
is not really connected and reports unreasonable temperatures."

When `chip_name` is `nct6776` and `board_vendor` contains "ASUS", the CPUTIN
channel is classified as `bogus` with `low` confidence and notes explaining the
issue. This is a well-documented kernel driver quirk, not a GUI assumption.

Reference: https://docs.kernel.org/hwmon/nct6775.html

### ASUS WMI polling bugs

Some ASUS BIOS implementations respond badly to high-frequency WMI temperature
polling. Symptoms include fan stops, fan max, or stuck sensor readings. The GUI
carries a standing note on all `asus_wmi_sensors` classifications and the vendor
quirk system can raise a high-severity alert for affected boards.

Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

### Gigabyte WMI unlabeled channels

The `gigabyte_wmi` Linux driver does not provide semantic labels for temperature
channels. The Gigabyte System Information Viewer (Windows) may show names like
"CPU", "System", etc., but there is no reliable mapping between those Windows
labels and the Linux sysfs channel numbers. All channels are classified at `low`
confidence.

### sensors-detect lag

The `lm_sensors` tool `sensors-detect` must be run after kernel updates or
hardware changes to configure ISA-bus Super I/O drivers (nct6775, it87, etc.).
Until it runs, these drivers may not load and their sensors are invisible.
The daemon ships `/etc/modules-load.d/control-ofc.conf` to auto-load common
drivers, but some configurations still require explicit `sensors-detect`.

`sensors-detect` maintains its own chip ID database, updated independently
of the kernel. New chips may be supported by the kernel driver before
`sensors-detect` recognises them. For example, lm-sensors issue #521 reports
`sensors-detect` showing "unknown chip with ID 0xd592" on an MSI PRO Z790-P
WIFI DDR4, while manually loading `nct6683` works correctly.

References:
- https://github.com/lm-sensors/lm-sensors/issues/521
- https://github.com/lm-sensors/lm-sensors/issues/499
- https://github.com/lm-sensors/lm-sensors/issues/454

## Board-specific override database

### Structure

The override database is defined in `sensor_knowledge.py` as a list of
`BoardSensorOverride` entries:

```python
@dataclass(frozen=True)
class BoardSensorOverride:
    vendor_pattern: str     # Case-insensitive substring match on board vendor
    model_pattern: str      # Case-insensitive substring match on board model
    label_pattern: str      # Case-insensitive substring match on sensor label
    source_class: str       # Override classification
    display_description: str
    confidence: str = "high"
    notes: list[str] = field(default_factory=list)
```

Overrides are checked by `lookup_board_override(board_vendor, board_model, label)`
before the driver-based classification. If an override matches, it takes
precedence.

### Current entries

| Vendor | Model | Label | Classification | Confidence |
|---|---|---|---|---|
| ASUS | Crosshair VIII | T_Sensor | external_probe | high |
| ASUS | STRIX X670E | VRM | vrm | high |
| ASRock | X670E | AMD TSI Addr 98h | amd_tsi | high |
| Gigabyte | B550 | temp1 | vendor_wmi_unlabeled | low |

### How to add new entries

1. Verify the sensor identity from at least one of:
   - Linux kernel hwmon driver documentation
   - Board vendor manual or BIOS sensor labels
   - Controlled load testing showing clear temperature correlation
2. Add a `BoardSensorOverride` entry to `BOARD_SENSOR_OVERRIDES` in
   `src/control_ofc/ui/sensor_knowledge.py`
3. Set confidence to `high` only when source documentation is definitive
4. Include a note citing the verification source
5. Add a test in the test suite confirming the override is returned

## Safe vs unsafe claims

### The GUI should assert (safe)

- Sensor driver name (directly from sysfs `name` file via daemon)
- Label text (directly from sysfs `tempN_label` via daemon)
- Type code meaning (defined in kernel documentation)
- That Tctl is not a physical die temperature (kernel-documented)
- That a sensor is "from" a particular driver (factual)
- Confidence level (transparent about classification certainty)

### The GUI should NOT assert (unsafe)

- Exact physical location of a board thermistor without board-specific documentation
- That a generic `tempN` channel measures any specific component
- That CPUTIN always means "CPU temperature" (board-dependent)
- That Gigabyte WMI channels map to specific components
- That sensor readings from different drivers are directly comparable
- That any Super I/O channel is "connected" without verification

## References

- k10temp: https://docs.kernel.org/hwmon/k10temp.html
- coretemp: https://docs.kernel.org/hwmon/coretemp.html
- sbtsi_temp: https://docs.kernel.org/hwmon/sbtsi_temp.html
- nct6775: https://docs.kernel.org/hwmon/nct6775.html
- nct6683: https://docs.kernel.org/hwmon/nct6683.html
- it87: https://docs.kernel.org/hwmon/it87.html
- asus_ec_sensors: https://docs.kernel.org/hwmon/asus_ec_sensors.html
- asus_wmi_sensors: https://docs.kernel.org/hwmon/asus_wmi_sensors.html
- amdgpu: https://docs.kernel.org/gpu/amdgpu/thermal.html
- NVMe specification: https://nvmexpress.org/specifications/
- hwmon sysfs interface: https://docs.kernel.org/hwmon/sysfs-interface.html
