# 22 — AMD Sensor Interpretation Deep Dive

**Status:** Reference guide, revised as behaviour changes — [CHANGELOG.md](../CHANGELOG.md) records release-by-release changes and wins where this document disagrees with it.

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

Tctl is the value the platform uses to drive cooling decisions. The driver's
offset table (`tctl_offset_table` in `k10temp.c`, checked at 7.3-rc4) covers only
Family 17h parts: Ryzen 5 1600X and Ryzen 7 1700X / 1800X (Tctl = Tdie + 20 °C),
Ryzen 7 2700X (+10 °C) and Threadripper 19xx / 29xx (+27 °C). Later CPUs publish no
offset and no separate Tdie.

**GUI classification:** `cpu_control` at `high` confidence, with a note
explaining that this is not a direct physical reading.

**Common pitfall:** Many monitoring tools and users interpret Tctl as "the
CPU temperature." This is partly true for cooling purposes but misleading
when the offset exists. The GUI labels Tctl a control value, but nothing in it
prefers Tdie when it picks a sensor: the preferred-CPU recommendation is the
daemon's `default_cpu`, which ranks Tctl first (then package, then Tdie), and the
AIO setup's CPU fallback takes the first package/Tctl/Tdie label in list order.

#### Tdie — CPU Die Temperature

When exported by the driver, this is the real measured CPU die temperature.
The driver exports it (`temp2`) **only** for the Family 17h offset SKUs above —
Zen 2 and later CPUs, including every Ryzen 3000–9000 part, publish Tctl and the
Tccd readings but no Tdie.

**GUI classification:** `cpu_die` at `high` confidence.

#### TccdN — Per-CCD Temperatures

Per-CCD (Core Complex Die) temperatures, available on Zen 2+ CPUs (Tccd1,
Tccd2, …). The kernel doc still says "up to 8", but the source labels up to
Tccd16 from 7.3 (Turin-class parts have 16 CCDs). Not all CPU variants expose
these.

**Kernel 7.3-rc1 to rc3 reported false Tccd readings on Zen 5 mobile** (Strix
Point, family 1Ah models 20h–2Fh): a Turin CCD range wrongly included those
models, producing values such as `Tccd4: +148.6°C`. Fixed in 7.3-rc4 by
[`451b1c19dc7c`](https://git.kernel.org/torvalds/c/451b1c19dc7c); stable 7.2.y was
never affected. Because the daemon treats every `k10temp` channel as a CPU
temperature, a Strix Point machine on those release candidates would trip the
thermal emergency and run its fans at 100%.

**GUI classification:** `cpu_ccd` at `high` confidence.

#### What to expect

| CPU | Tdie | Tctl | TccdN | Offset |
|---|---|---|---|---|
| Ryzen 5 1600X, Ryzen 7 1700X / 1800X | Yes | Yes (= Tdie + 20 °C) | — | 20 °C |
| Ryzen 7 2700X | Yes | Yes (= Tdie + 10 °C) | — | 10 °C |
| Threadripper 19xx / 29xx | Yes | Yes (= Tdie + 27 °C) | — | 27 °C |
| Other Zen / Zen+ | No | Yes | — | none published |
| Zen 2 and later (Ryzen 3000–9000, Threadripper 3000+) | No | Yes | Yes, varies by SKU | none published |

---

### sbtsi_temp: AMD SB-TSI board-side interface

**Kernel docs:** https://docs.kernel.org/hwmon/sbtsi_temp.html

The hwmon device is named **`sbtsi`** (only the module is `sbtsi_temp`), and from
kernel 7.3 the driver depends on ARM / ARM64 — it is meant for the BMC, not the
managed host — so x86 desktops stop seeing it. SB-TSI readings on a desktop
usually arrive through the Super-I/O driver instead (`AMD TSI Addr 98h`).

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

**GUI handling:** When the chip is any of the 11-chip nct6775 family (`nct6775`
through `nct6799`, DEC-294) and the board vendor is ASUS,
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
| Thermal diode | `Diode 0 (curr)` through `Diode 2 (curr)`, `Diode 0 (volt)` through `Diode 2 (volt)` | 3 |
| Thermistor | `Thermistor 0` through `Thermistor 16` | 4 |
| AMD TSI | `AMD TSI Addr 90h` through `AMD TSI Addr 9dh` | 5 |
| Intel PECI | `PECI 0.0` through `PECI 3.1` | 6 |
| PECI DIMM (memory, read over PECI) | `PECI DIMM 0` through `PECI DIMM 3` | 6 |
| PCH | `PCH CPU`, `PCH CHIP`, `PCH CHIP CPU MAX`, `PCH MCH`, `PCH DIMM 0` through `PCH DIMM 3` | — |
| SMBus | `SMBus 0` through `SMBus 5` | — |
| DIMM | `DIMM 0` through `DIMM 3` | — |
| Virtual | `Virtual 0` through `Virtual 7` | — |

**`PECI DIMM` carries the PECI type code, and it is not the CPU** (`DC-f`). Its
source falls in the kernel's PECI range, so `temp_type` 6 cannot tell a DIMM from
the CPU. The GUI classifies any `DIMM` label as `memory_dimm` before it looks at
the type code, and the daemon reports it as `mb_temp`, which keeps it out of the
thermal ladder's CPU input. Until DEC-429 both called it the CPU. The daemon does
report `PCH CPU` and `PCH CHIP CPU MAX` as `cpu`, deliberately: each reads at
least the CPU's own temperature.

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

#### force=1 and customer IDs

The in-kernel `nct6683` driver instantiates only for EC customer IDs it knows —
Intel, Mitac, several MSI IDs (from 5.11 on), AMD (the BC-250, 6.15) and a growing
set of ASRock IDs (6.7, 6.14, 7.0, 7.1, 7.2) — unless `force=1` is given. With
`force=1` it reads an unknown board too, but **the PWM files stay read-only**: the
driver makes them writable only on Mitac OEM systems, and has no `pwmN_enable`.
So `force=1` buys monitoring, never control. (Do not confuse it with the
out-of-tree `nct6687` driver's `force=1`, which attaches to any Nuvoton ID in
0xD000–0xDFFF and can mis-claim an NCT679x chip — see doc 19.)

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

> "Note: This is inherently risky since it means that both ACPI and this driver
> may access the chip at the same time. This can result in race conditions and,
> worst case, result in unexpected system reboots."

(The same doc says the parameter exists because "system-wide
acpi_enfore_resources=lax can result in boot failures on some systems" — which
is why it is preferred over the kernel parameter.)

See the Fan Control Guide (doc 21) for details on when and how to use this.

#### Newer ITE chips

Some recent boards pair two ITE Super-I/O chips (e.g. IT8689E + IT87952E on
Gigabyte AORUS boards). Mainline `it87` gained IT8689E fan *control* in kernel
7.1 (commit `66b8eaf`; six PWM channels, `FEAT_FANCTL_ONOFF`), and the
out-of-tree `frankcrawford/it87` DKMS driver has driven the secondary chip on
**many** dual-IO Gigabyte boards since its 2026-03 MMIO merge (PR #95 / #102) —
but not all of them, and the difference is per board rather than per family. On
the X870E AORUS MASTER the secondary can answer device-ID `0x8883` — an ITE
eSPI→LPC bridge latched in configuration mode by `nct6775`/`w83627ehf`, which
clears once those are suppressed and mains power is removed (measured
2026-09-05, DEC-332, superseding DEC-326's "no local fix"); on the X870E AORUS
ELITE the same IT8696E + IT87952E pairing is owner-confirmed working (it87 #89). lm-sensors issue #454 tracked the earlier, incomplete state;
docs 19 and 23 carry the current per-chip support matrix.

Reference: https://github.com/lm-sensors/lm-sensors/issues/454

---

### asus_ec_sensors: ASUS Embedded Controller

**Kernel docs:** https://docs.kernel.org/hwmon/asus_ec_sensors.html

The hwmon device is named **`asusec`**; `asus_ec_sensors` is the module.

This is one of the best data sources for sensor metadata on Linux. The
ASUS embedded controller provides **semantic labels** that map directly to
specific board features.

#### Sensor labels and their meanings

| Label | Physical meaning | Confidence |
|---|---|---|
| `T_Sensor` | External temperature sensor header (user-attached probe) | high |
| `VRM` / `VRM temperature` | VRM heatsink area | high |
| `Water_In` | Liquid cooling loop inlet temperature probe header | high |
| `Water_Out` | Liquid cooling loop outlet temperature probe header | high |
| `Water_Block_In` | Coolant temperature entering the CPU water block | high |
| `Water_Block_Out` | Coolant temperature leaving the CPU water block | high |
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
methods. Found on 16 older ASUS AMD boards, listed by exact name: X370, X470,
B450 and X399 boards (no X570 board is on the list).

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

The kernel names no safe polling rate, so this doc no longer claims one (DEC-421's retraction, missed here until DEC-424). The driver reads each WMI sensor group from the BIOS at most about once a second, however many programs read its files, so extra readers add no WMI calls. The kernel's advice is a soak test while polling before you leave the machine unattended.

#### Supported boards (AMD, from kernel docs)

- PRIME X470-PRO
- ROG CROSSHAIR VII HERO
- ROG CROSSHAIR VII HERO (WI-FI)
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
with no labels. The reporter closed the issue themselves; labels are not
available through the WMI interface.

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
| `nct6775`-family `CPUTIN` on ASUS | low (bogus) | Known kernel-documented issue |

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

**GUI handling:** Annotates Tctl with an explanatory note and never presents it
as "actual CPU temperature." It does **not** prefer Tdie when it picks a sensor:
the preferred-CPU recommendation is the daemon's `default_cpu`, which ranks Tctl
first (`hwmon/classify.rs::cpu_class_rank`: Tctl, then package, then Tdie), so on
the offset parts (1600X, 1700X, 1800X, 2700X, Threadripper 19xx/29xx) it
recommends the offset Tctl; the AIO setup's CPU fallback takes the first
package/Tctl/Tdie label in the daemon's list order.

Reference: https://docs.kernel.org/hwmon/k10temp.html

### Quirk 2: ASUS CPUTIN bogus on the nct6775 family

**Applies to:** Some ASUS boards with any nct6775-family chip — `nct6775`, `nct6776`,
`nct6779`, `nct6791`, `nct6792`, `nct6793`, `nct6795`, `nct6796`, `nct6797`, `nct6798`,
`nct6799` (DEC-294 widened this from `nct6776` alone; both code legs carry the same 11)

**Symptom:** CPUTIN reports unreasonably high temperatures (e.g., 115C at
idle) or temperatures that move inversely to actual CPU load.

**Explanation:** The CPUTIN pin is not connected or is connected to a
non-standard measurement device on affected ASUS boards.

**GUI handling:** Classifies as `bogus` with `low` confidence and a note
citing the kernel documentation.

**Daemon handling (DEC-294):** classifies the same
chip+vendor+label as `mb` rather than `cpu`, so it is excluded from the thermal
ladder's hottest-CPU reduction. Before this the 115 C symptom above was not
merely cosmetic — it is plausible enough to pass the reader's range check, so it
latched the thermal emergency permanently (release requires <= 80 C) and pinned
every fan at 100%. The chip's `PECI`/`TSI` channels became `cpu` in the same
change.

Reference: https://docs.kernel.org/hwmon/nct6775.html

### Quirk 3: ASUS WMI polling can cause hardware misbehaviour

**Applies to:** Some ASUS boards using asus_wmi_sensors

**Symptom:** Fans stop, fans get stuck at maximum, or temperature readings
freeze.

**Explanation:** Buggy BIOS WMI implementations cannot handle frequent
polling. The PRIME X470-PRO is the most-documented affected board.

**GUI handling:** All asus_wmi_sensors classifications carry a standing note
about potential polling issues. The kernel names no safe polling rate, so this
guide does not call the daemon's default 1 Hz poll safe. The kernel's advice is an
extended soak test while polling before you leave the machine unattended, and
it says a BIOS whose WMI method version is 2 or later should fix the
misbehaviour.

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
inventing a location. A board-specific override, where one is validated, is
shown in its own section of the Sensor Detail dialog; it is display-only and does
not change the channel's classification or confidence anywhere else in the GUI.

---

## Known kernel-version regressions affecting AMD GPU sensors

Beyond chip- and driver-level quirks, recent Linux kernels have shipped
regressions that affect amdgpu specifically. The daemon ships a curated
catalogue (`hwmon/kernel_warnings.rs`, DEC-098) and surfaces matches via
`GET /capabilities` (`devices.amd_gpu.kernel_warnings`). The GUI raises
a one-time popup; acknowledged warnings are remembered in
`app_settings.acknowledged_kernel_warnings`.

| `id` | Affected kernels | Affected hardware | Severity | Symptom |
|---|---|---|---|---|
| `rdna_mes_hang_drm_amd_4765` (DEC-422) | 6.18.0–6.18.6, and 6.17.9–6.17.13 | RDNA3 / RDNA3.5 / RDNA4, integrated GPUs included | Critical | Evicting a process on a MES GPU suspends the whole MES scheduler, so a compute job running alongside a 3D workload can hang the GPU ([drm/amd #4765](https://gitlab.freedesktop.org/drm/amd/-/issues/4765)). Fixed in 6.18.7 and 6.19.0. The 6.17 backport was never fixed, and 6.12 / 6.6 never had the bug. The advice is the latest 6.18 LTS or a current 7.x kernel — never 6.15–6.17. |
| `rdna_hang_kernel_6_18_6_19` (retired by DEC-422; daemon ≤ v2.56.0) | 6.18.x **and** 6.19.x | RDNA3 and RDNA4 | Critical | Keyed on an unbisected report ([Phoronix, EOY 2025](https://www.phoronix.com/review/old-amdgpu-eoy2025)), and its advice (pin 6.15–6.17) sent users to kernels that were never longterm, 6.17.9 onward of which carries the hang above. |
| `smu_mismatch_navi48_r9700` (retired by DEC-422; daemon ≤ v2.56.0) | all current kernels | R9700 (PCI `0x7551`) | Critical | **Premise refuted (DEC-421):** the SMU interface-version message appears on every Navi 48 card, the RX 9070 XT included, and is not a fault (kernel 7.0 removed it as confusing). `pwm1` is read-only on all RDNA4 by design; the PMFW `fan_curve` path works on at least some R9700s. A few R9700 units have unresolved per-unit fan faults ([ROCm #6101](https://github.com/ROCm/ROCm/issues/6101)). |

For mitigation guidance, see `docs/19_Hardware_Compatibility.md` § Known kernel-version regressions.

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
