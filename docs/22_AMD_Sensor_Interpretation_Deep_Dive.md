# 22 — AMD Sensor Interpretation Deep Dive

**Last updated:** 2026-04-22 (v1.5.0)

## Purpose

This guide explains in detail what each type of sensor reading means on AMD
desktop systems under Linux, how the GUI classifies and presents sensor data,
and what the confidence levels mean in practice. It is a user-facing companion
to the technical specification in `20_Sensor_Interpretation_Guide.md`.

The central question this guide answers: **When the GUI shows a temperature
reading, how certain are we about what it actually measures?**

## The key insight: sensor identity depends on the driver, not the chipset

On AMD desktop Linux systems, the same user-facing concept — "CPU temperature"
— can mean very different things depending on which kernel driver produced the
reading:

| Source | What it actually measures | Common label |
|---|---|---|
| `k10temp` Tctl | A control value used by platform firmware for cooling decisions; not a physical temperature | "CPU" in many tools |
| `k10temp` Tdie | The actual measured CPU die temperature | "Tdie" |
| `nct6683` AMD TSI Addr 98h | Board-side CPU temperature read via the AMD SB-TSI interface | "AMD TSI Addr 98h" |
| `nct6775` CPUTIN | Board-side CPU temperature input on the Super I/O chip; may be unconnected on some boards | "CPUTIN" |
| `asus_ec_sensors` CPU Package | CPU package temperature read from the ASUS embedded controller | "CPU" or "CPU Package" |

The GUI uses the combination of **driver name + label + temp_type code +
board vendor** to classify each reading. This four-tuple is the only reliable
way to determine what a sensor reading represents.

---

## Sensor classes in detail

### k10temp: AMD CPU internal sensors

**Kernel docs:** https://docs.kernel.org/hwmon/k10temp.html

This is the **highest-confidence** source for CPU thermals on Ryzen systems.
The data comes directly from the CPU's internal thermal monitoring hardware
via MSR (Model-Specific Register) reads.

#### Tctl — CPU Control Temperature

The kernel documentation is explicit:

> "Tctl is a non-physical temperature on an arbitrary scale measured in
> degrees. It does not represent an actual physical temperature like die
> or case temperature."

Tctl is the value the platform uses to drive cooling decisions. On some AMD
CPU SKUs (particularly older Threadripper), Tctl = Tdie + a designed offset
(e.g., +27C). On most consumer Ryzen CPUs, Tctl and Tdie are identical.

**GUI classification:** `cpu_control` at `high` confidence, with a note
explaining that this is not a direct physical reading.

**Common pitfall:** Many monitoring tools and users interpret Tctl as "the
CPU temperature." This is partly true for cooling purposes but misleading
when the offset exists. The GUI always prefers Tdie when both are available.

#### Tdie — CPU Die Temperature

When exported by the driver, this is the real measured CPU die temperature.
Not all CPU variants expose Tdie separately from Tctl.

**GUI classification:** `cpu_die` at `high` confidence.

#### TccdN — Per-CCD Temperatures

Per-CCD (Core Complex Die) temperatures, available on Zen 2+ CPUs with
multiple CCDs (Tccd1, Tccd2, up to Tccd8). Not all CPU variants expose
these.

**GUI classification:** `cpu_ccd` at `high` confidence.

#### What to expect

| CPU | Tdie | Tctl | TccdN | Offset |
|---|---|---|---|---|
| Most Ryzen 5000/7000/9000 | Yes | Yes (= Tdie) | Varies by SKU | None |
| Threadripper (some SKUs) | Yes | Yes (= Tdie + offset) | Yes | +27C typical |
| Older Ryzen (some SKUs) | May be absent | Yes | Varies | Varies |

---

### sbtsi_temp: AMD SB-TSI board-side interface

**Kernel docs:** https://docs.kernel.org/hwmon/sbtsi_temp.html

SB-TSI (SideBand Temperature Sensor Interface) is an SMBus-compatible
temperature sensor interface on AMD SoCs. It provides a **board-side /
firmware-accessible** CPU temperature feed, separate from the CPU-internal
k10temp path.

#### I2C addresses

The kernel documentation states:

> "The SB-TSI address is normally 98h for socket 0 and 90h for socket 1,
> but it could vary based on hardware address select pins."

Note: 98h and 90h are 8-bit I2C addresses. The 7-bit equivalents are
0x4C (socket 0) and 0x48 (socket 1).

On desktop Ryzen boards (single-socket), you will almost always see 98h.
The 90h address is relevant for dual-socket server/workstation boards.

#### What SB-TSI is not

SB-TSI readings are **not** the same as:
- k10temp Tdie (different data path, may report different values)
- A random motherboard thermistor (this is a CPU-specific interface)
- A VRM or chipset sensor

#### How SB-TSI appears in hwmon

SB-TSI readings often surface through Super I/O chip drivers (nct6683,
nct6775) as labels like `AMD TSI Addr 98h` rather than through a standalone
`sbtsi_temp` device. When they appear through nct6683, they use temp_type
code 5.

**GUI classification:** `amd_tsi` at `medium_high` confidence.

---

### nct6775 family: Nuvoton configurable Super I/O

**Kernel docs:** https://docs.kernel.org/hwmon/nct6775.html

This family (NCT6775, NCT6776, NCT6779, NCT6791-NCT6799) is common on
many desktop boards, especially ASUS.

#### Why the label matters

These chips support **up to 25 temperature monitoring sources** with
**configurable source multiplexers**. The physical sensor connected to each
channel is determined by the board firmware, not the chip. A `tempN_input`
from nct6775 is meaningless without its corresponding `tempN_label`.

The driver exposes these label categories:

| Label | Meaning | Confidence |
|---|---|---|
| `SYSTIN` | System temperature input — exact placement is vendor-specific | medium |
| `CPUTIN` | Board-side CPU temperature input — may be unconnected on some boards | medium |
| `AUXTIN` / `AUXTINn` | Auxiliary temperature input — exact placement is vendor-specific | medium |
| Contains `TSI` or `AMD TSI` | Board-side CPU temp via AMD SB-TSI interface | medium_high |
| Contains `PECI` | CPU temp via Intel PECI (uncommon on AMD boards) | medium_high |
| Named label with `_` or mixed case | Firmware-configured source, meaning varies | medium |
| Generic `tempN` | No semantic information — driver did not provide a label | low |

#### Critical ASUS quirk: bogus CPUTIN

The kernel documentation explicitly states:

> "On various ASUS boards with NCT6776F, CPUTIN is not really connected to
> anything and floats, or that it is connected to some non-standard
> temperature measurement device."

Symptoms: unreasonably high temperatures, or temperature that declines when
the actual CPU temperature rises.

The kernel recommends ignoring CPUTIN on affected ASUS boards and using
PECI 0 or TSI 0 instead.

**GUI handling:** When the chip is `nct6776` and the board vendor is ASUS,
the GUI classifies CPUTIN as `bogus` at `low` confidence with an explanatory
note.

---

### nct6683 / nct6686 / nct6687: Nuvoton newer family

**Kernel docs:** https://docs.kernel.org/hwmon/nct6683.html

This family is especially important on recent AMD boards from ASRock and MSI.

#### AMD boards confirmed working in kernel docs

Despite a historical note about initial Intel-only testing, the kernel
documentation explicitly lists these AMD boards as reported working:

- ASRock X570
- ASRock X670E
- ASRock B650 Steel Legend WiFi
- MSI B550
- MSI X670-P
- MSI X870E

#### Source label enumeration

The kernel source (`drivers/hwmon/nct6683.c`) defines a rich set of
temperature source labels:

| Source type | Labels | temp_type |
|---|---|---|
| Chip-local | `Local` | — |
| Thermal diode | `Diode 0`, `Diode 1`, `Diode 2` | 3 |
| Thermistor | `Thermistor 0` through `Thermistor 13` | 4 |
| AMD TSI | `AMD TSI Addr 90h` through `AMD TSI Addr 9dh` | 5 |
| Intel PECI | `PECI 0.0` through `PECI 3.1` | 6 |
| PECI DIMM | `PECI DIMM 0` through `PECI DIMM 3` | — |
| SMBus | `SMBus 0` through `SMBus 5` | — |
| DIMM | `DIMM 0` through `DIMM 3` | — |
| Virtual | `Virtual 0` through `Virtual 7` | — |

#### temp_type classification codes

The kernel source maps source ranges to standardised type codes:

| Source range | temp_type | Classification |
|---|---|---|
| 0x02–0x07 | 3 | Thermal diode |
| 0x08–0x18 | 4 | Thermistor |
| 0x42–0x49 | 5 | AMD TSI |
| 0x20–0x2b | 6 | Intel PECI |

Reference: `drivers/hwmon/nct6683.c`, `get_temp_type()` function

#### What these labels tell you (and what they do not)

The nct6683 driver tells you the **source class** (AMD TSI, thermistor,
diode, etc.) but usually **not** the exact physical board location.

| Label | What you know | What you do not know |
|---|---|---|
| `AMD TSI Addr 98h` | Board-side CPU temp feed via SB-TSI | — (identity is clear) |
| `Thermistor 7` | A thermistor is connected to channel 7 | Which board component it monitors |
| `Diode 1` | A thermal diode on channel 1 | Physical placement on the PCB |
| `Virtual 3` | A derived/computed value | What it is derived from |
| `SMBus 2` | A device on SMBus channel 2 | What device or component |

For thermistor channels, only vendor documentation, BIOS labels, or
controlled load testing can determine the physical placement (e.g., "VRM
heatsink", "chipset area", "rear I/O").

#### force=1 historical note

The nct6683 driver was initially tested with Intel firmware and defaults to
instantiating only on Intel boards unless `force=1` is used. However, many
AMD boards are now explicitly listed as supported. If your AMD board uses
an nct6683-family chip and the driver does not auto-detect it, `force=1`
may be needed, but check the kernel documentation's supported board list
first.

#### sensors-detect lag

`sensors-detect` (from lm_sensors) does not always recognise newer Nuvoton
chip IDs, even when manually loading `nct6683` works fine.

Example: lm-sensors issue #521 reports `sensors-detect` showing "unknown
chip with ID 0xd592" on an MSI PRO Z790-P WIFI DDR4, while manually loading
`nct6683` exposes all sensors correctly.

References:
- https://github.com/lm-sensors/lm-sensors/issues/521
- https://github.com/lm-sensors/lm-sensors/issues/499

**Implication:** The daemon trusts actually-loaded hwmon devices rather than
`sensors-detect` output.

---

### it87 family: ITE Super I/O

**Kernel docs:** https://docs.kernel.org/hwmon/it87.html

ITE Super I/O chips are common on Gigabyte boards and provide:
- 3 temperature sensors (more on newer variants)
- 3 fan rotation sensors (more on newer variants)
- 8 voltage sensors (16-bit tach on newer chips)

#### Conservative classification

The it87 driver provides **minimal labeling**. Most sensors appear as
generic `tempN` channels with no semantic information about what component
they measure. The GUI classifies all it87 temperature channels conservatively:

| Label | Classification | Confidence |
|---|---|---|
| `tempN` (numeric) | `super_io_channel` | low |
| Named label | `super_io_channel` | medium |

Without board-specific documentation, the GUI does not claim to know what
an it87 temperature channel measures.

#### Resource conflict warning

The `it87` driver has an `ignore_resource_conflict=1` parameter, but the
kernel documentation explicitly warns:

> "This is inherently risky because ACPI and the driver may access the
> chip simultaneously."

See the Fan Control Guide (doc 21) for details on when and how to use this.

#### Newer ITE chips

Some recent boards use ITE chips that are not yet fully supported by any
driver. lm-sensors issue #454 notes recent Gigabyte boards using IT8689E
and IT87952E where support is incomplete or evolving.

Reference: https://github.com/lm-sensors/lm-sensors/issues/454

---

### asus_ec_sensors: ASUS Embedded Controller

**Kernel docs:** https://docs.kernel.org/hwmon/asus_ec_sensors.html

This is one of the best data sources for sensor metadata on Linux. The
ASUS embedded controller provides **semantic labels** that map directly to
specific board features.

#### Sensor labels and their meanings

| Label | Physical meaning | Confidence |
|---|---|---|
| `T_Sensor` | External temperature sensor header (user-attached probe) | high |
| `VRM` / `VRM temperature` | VRM heatsink area | high |
| `Water In` | Liquid cooling loop inlet temperature probe header | high |
| `Water Out` | Liquid cooling loop outlet temperature probe header | high |
| `Chipset` / `PCH` | Chipset (PCH) area temperature | high |
| `CPU Package` | CPU package temperature (EC's reading, may differ from k10temp) | high |
| `Motherboard` | Vendor-defined board ambient/reference point | high |
| `CPU_Opt` (fan) | CPU optional fan header RPM | high |
| `VRM Heatsink` (fan) | VRM heatsink fan RPM | high |
| `Chipset` (fan) | Chipset fan RPM | high |
| `Water Flow` | Water flow meter RPM/rate | high |
| `CPU Current` | CPU current draw | high |
| `CPU Core Voltage` | CPU core voltage | high |

All readings are `high` confidence because the EC provides the identity
mapping. The driver only loads on boards in an explicit kernel allowlist.

#### ACPI mutex

The driver uses an ACPI mutex to coordinate access with the firmware. A
`mutex_path` parameter is exposed because ASUS may change the path in BIOS
updates. A special `:GLOBAL_LOCK` mode is also documented for edge cases.

---

### asus_wmi_sensors: ASUS WMI interface

**Kernel docs:** https://docs.kernel.org/hwmon/asus_wmi_sensors.html

Same label vocabulary as `asus_ec_sensors` (VRM, T_Sensor, Water In/Out,
etc.) but accessed via WMI (Windows Management Instrumentation) ACPI
methods. Typically found on older ASUS AMD boards (X470, B450, X570 era).

#### Confidence reduction

All classifications follow the same label-matching rules as
`asus_ec_sensors` but at **one confidence level lower** (`medium_high`
instead of `high`). This is because the WMI interface has documented
reliability issues.

#### Polling reliability warning

The kernel documentation documents three failure modes from aggressive polling:

1. **Fans stop** unexpectedly
2. **Fans get stuck at maximum** speed
3. **Temperature readings freeze** at a stale value

The PRIME X470-PRO is called out as particularly bad. The risk increases
with polling frequency. BIOS updates with method version >= 2 may improve
stability.

The daemon polls at 1 Hz, which is within safe limits for all known boards.

#### Supported boards (AMD, from kernel docs)

- PRIME X470-PRO
- ROG CROSSHAIR VII HERO (Wi-Fi)
- ROG STRIX B450-E / B450-F / B450-I GAMING
- ROG STRIX X470-F / X470-I GAMING

Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

---

### gigabyte_wmi: Gigabyte WMI temperature reporting

The `gigabyte-wmi` driver (mainline since Linux 5.13) exposes temperature
readings from Gigabyte motherboards via WMI. It is a **temperature reporting
driver only** — it provides no fan control, no voltage sensing, and no
semantic labels.

#### The label problem

The Linux driver defines only `HWMON_T_INPUT` for up to 6 channels. It
does **not** define `HWMON_T_LABEL`, does not implement `read_string`, and
provides no descriptive names. Users see only `temp1_input` through
`temp6_input`.

Gigabyte's Windows software (System Information Viewer) may show names like
"VRM MOS", "VSOC MOS", etc., but there is **no reliable mapping** between
those Windows labels and the Linux sysfs channel numbers. The channel
ordering may vary by board model and BIOS version.

#### Real-world example

A Gigabyte B550M DS3H AC user reported receiving `temp1` through `temp6`
with no labels. The issue was closed as "not planned" — the upstream driver
developers do not intend to add label support because the information is
not available through the WMI interface.

Reference: https://github.com/t-8ch/linux-gigabyte-wmi-driver/issues/19

**GUI classification:** All readings are `vendor_wmi_unlabeled` at `low`
confidence with a note that exact identity is not provided by the Linux
driver.

---

### amdgpu: AMD GPU sensors

GPU sensors are high confidence because the amdgpu kernel driver defines
exact label semantics:

| Label | Meaning | Notes |
|---|---|---|
| `edge` | GPU edge temperature | General die temperature |
| `junction` | GPU hotspot / junction temperature | Hottest point on the die; primary thermal limit |
| `mem` | GPU HBM / VRAM temperature | Memory temperature |

GPU sensors report `source: "amd_gpu"` (not `"hwmon"`) and use PCI BDF
address for stable identity across reboots.

---

## Board/vendor confidence patterns

### ASUS

| Driver | Typical confidence | Notes |
|---|---|---|
| `asus_ec_sensors` | high | Best-in-class semantic labels |
| `asus_wmi_sensors` | medium_high | Same labels but WMI polling risk |
| `nct6775` with labels | medium | Source configured by firmware |
| `nct6775` `CPUTIN` on NCT6776F | low (bogus) | Known kernel-documented issue |

### ASRock

| Driver | Typical confidence | Notes |
|---|---|---|
| `nct6683` with AMD TSI labels | medium_high | Source class is reliable |
| `nct6683` with thermistor labels | medium | Physical location unknown |

### MSI

| Driver | Typical confidence | Notes |
|---|---|---|
| `nct6683`/`nct6687` with labels | medium | Source class reliable, location often unknown |
| Generic `tempN` | low | No semantic information |

### Gigabyte

| Driver | Typical confidence | Notes |
|---|---|---|
| `k10temp` | high | CPU-internal, always reliable |
| `gigabyte_wmi` | low | No semantic labels |
| `it87` with labels | medium | Only when labels are present |
| `it87` `tempN` | low | No semantic information |

---

## What Linux can and cannot determine

### Realistically knowable from Linux alone

- Whether a reading comes from the CPU internally (k10temp) or board-side
- Whether a channel is Tdie, Tctl, TccdN, AMD TSI, thermistor, diode, etc.
- Whether a reading comes from a high-confidence vendor-labelled source
  (ASUS EC/WMI)
- Whether a channel is known bogus on a documented board/driver combination
- The source class of nct6683 readings via temp_type codes

### Often not knowable from Linux alone

- Exact PCB placement of a generic thermistor channel ("Thermistor 7" does
  not mean "VRM heatsink" unless separately validated)
- Exact identity of `gigabyte_wmi temp3` (the driver does not provide this)
- Whether a generic `temp2` is "VRM top", "chipset", or "ambient" unless
  vendor firmware labels it
- Exact correspondence between Windows monitoring tool labels and Linux
  sysfs channel numbers

The GUI makes this distinction visible to the user through confidence levels
and descriptive notes. When the identity is uncertain, the GUI says so
rather than guessing.

---

## The confidence model explained

### High confidence

The sensor identity is certain from kernel documentation or hardware
specification. The GUI displays a specific description without hedging.

**Examples:** k10temp Tdie, asus_ec_sensors T_Sensor, amdgpu junction, NVMe
composite, coretemp per-core.

### Medium-high confidence

The sensor identity is very likely correct but depends on board firmware or
an indirect interface. The GUI displays a specific description with a
qualifying note.

**Examples:** PECI/TSI-typed nct6683 channels, sbtsi_temp readings,
asus_wmi_sensors labels.

### Medium confidence

The sensor identity is plausible based on label conventions but cannot be
confirmed without board documentation. The GUI displays the source class
with a note about vendor-specific placement.

**Examples:** nct6775 SYSTIN/CPUTIN/AUXTIN, nct6683 thermistor/diode
channels, named it87 labels.

### Low confidence

The sensor identity is unknown or unreliable. The GUI clearly states that
the exact identity is not provided by the Linux driver.

**Examples:** generic `tempN` labels, gigabyte_wmi channels, virtual
sensors, known bogus channels.

---

## Documented quirks

### Quirk 1: Tctl is not a physical temperature

**Applies to:** All AMD CPUs via k10temp

**Symptom:** Users see a "CPU temperature" that is higher than the actual
die temperature.

**Explanation:** Tctl is a control value. On some SKUs it includes a
designed offset above Tdie. The platform uses this inflated value to trigger
cooling responses earlier.

**GUI handling:** Prefers Tdie when available. Annotates Tctl with an
explanatory note. Never presents Tctl as "actual CPU temperature."

Reference: https://docs.kernel.org/hwmon/k10temp.html

### Quirk 2: ASUS CPUTIN bogus on NCT6776F

**Applies to:** Some ASUS boards with nct6776 chip

**Symptom:** CPUTIN reports unreasonably high temperatures (e.g., 115C at
idle) or temperatures that move inversely to actual CPU load.

**Explanation:** The CPUTIN pin is not connected or is connected to a
non-standard measurement device on affected ASUS boards.

**GUI handling:** Classifies as `bogus` with `low` confidence and a note
citing the kernel documentation.

Reference: https://docs.kernel.org/hwmon/nct6775.html

### Quirk 3: ASUS WMI polling can cause hardware misbehaviour

**Applies to:** Some ASUS boards using asus_wmi_sensors

**Symptom:** Fans stop, fans get stuck at maximum, or temperature readings
freeze.

**Explanation:** Buggy BIOS WMI implementations cannot handle frequent
polling. The PRIME X470-PRO is the most-documented affected board.

**GUI handling:** All asus_wmi_sensors classifications carry a standing note
about potential polling issues. The daemon's 1 Hz polling rate is within
safe limits.

Reference: https://docs.kernel.org/hwmon/asus_wmi_sensors.html

### Quirk 4: sensors-detect lags behind kernel support

**Applies to:** Newer nct6683-family chips, some newer ITE chips

**Symptom:** `sensors-detect` reports "unknown chip" even though manually
loading the correct module exposes all sensors.

**Explanation:** `sensors-detect` maintains its own chip ID database which
is updated independently of the kernel. New chips may be supported by the
kernel driver before `sensors-detect` recognises them.

**GUI handling:** The daemon trusts loaded hwmon devices, not
`sensors-detect` output.

References:
- https://github.com/lm-sensors/lm-sensors/issues/521
- https://github.com/lm-sensors/lm-sensors/issues/499
- https://github.com/lm-sensors/lm-sensors/issues/454

### Quirk 5: Gigabyte WMI channels lack semantic labels

**Applies to:** All Gigabyte boards using the gigabyte_wmi driver

**Symptom:** Only `temp1` through `temp6` appear with no indication of what
each channel measures.

**Explanation:** The Linux driver does not implement label support because
the WMI interface does not expose label information. Windows-side tools
(Gigabyte SIV) use a proprietary mapping that is not available to Linux.

**GUI handling:** All channels classified as `vendor_wmi_unlabeled` at `low`
confidence.

### Quirk 6: Thermistor channels are real but physically unmappable

**Applies to:** nct6683, nct6775, it87

**Symptom:** Sensors show "Thermistor 7" or "temp2" with no indication of
physical placement.

**Explanation:** The driver knows a thermistor is connected to a channel
but cannot determine where on the board it is physically located. Only
vendor documentation, BIOS labels, or controlled load testing can determine
the mapping.

**GUI handling:** Classifies as "board thermistor channel" rather than
inventing a location. Board-specific overrides can provide higher-confidence
mappings where validated.

---

## Source references

### Linux kernel documentation
- k10temp: https://docs.kernel.org/hwmon/k10temp.html
- sbtsi_temp: https://docs.kernel.org/hwmon/sbtsi_temp.html
- nct6775: https://docs.kernel.org/hwmon/nct6775.html
- nct6683: https://docs.kernel.org/hwmon/nct6683.html
- it87: https://docs.kernel.org/hwmon/it87.html
- asus_ec_sensors: https://docs.kernel.org/hwmon/asus_ec_sensors.html
- asus_wmi_sensors: https://docs.kernel.org/hwmon/asus_wmi_sensors.html
- hwmon sysfs interface: https://docs.kernel.org/hwmon/sysfs-interface.html

### Linux kernel source
- nct6683.c: https://github.com/torvalds/linux/blob/master/drivers/hwmon/nct6683.c

### Community references
- lm-sensors issues: #521, #499, #454, #525
- gigabyte-wmi-driver issue #19 (unlabeled channels)
