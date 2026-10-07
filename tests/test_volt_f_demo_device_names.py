"""`VOLT-f` (decision `U19`): the demo names its hwmon devices as a daemon does.

A daemon builds every hwmon id as ``hwmon:<chip>:<device_id>:…`` from the
basename of the chip's ``device`` link (``device_id_from_path``) and reports the
same ``device_id`` on the chip and on each header. The demo used to describe the
chip there ("ITE IT8696E"), disagreed with itself (``pci0`` in the header ids,
``it87.2624`` in the sensor id), put the NVMe drive at the NVIDIA GPU's PCI
address, and gave the Kraken pump header no fan reading.
"""

from __future__ import annotations

import re

from control_ofc.services.demo_service import DemoService

# A sysfs device basename: `it87.2624`, `0000:00:18.3`, `0003:1E71:3008.0001`,
# `nvme0`. Never a description with spaces in it.
_DEVICE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _demo_hwmon_ids() -> list[str]:
    """Every hwmon id the demo serves, from every surface that carries one."""
    demo = DemoService()
    diag = demo.hardware_diagnostics()
    ids = [h.id for h in demo.hwmon_headers()]
    ids += [s.id for s in demo.sensors()]
    ids += [f.id for f in demo.fans()]
    ids += [r.id for r in diag.voltages]
    ids += list(diag.hwmon.enable_revert_counts)
    ids += list(DemoService.fan_zones())
    ids += list(DemoService.fan_aliases())
    return [i for i in ids if i.startswith("hwmon:")]


def test_every_demo_hwmon_id_embeds_its_chips_reported_device_name():
    chips = DemoService().hardware_diagnostics().hwmon.chips_detected
    ids = _demo_hwmon_ids()
    for chip in chips:
        chip_ids = [i for i in ids if i.split(":")[1] == chip.chip_name]
        assert chip_ids, f"no demo id names chip {chip.chip_name}"
        prefix = f"hwmon:{chip.chip_name}:{chip.device_id}:"
        wrong = [i for i in chip_ids if not i.startswith(prefix)]
        assert not wrong, f"{chip.chip_name} reports {chip.device_id!r} but ids say {wrong}"


def test_demo_headers_carry_their_chips_device_id():
    demo = DemoService()
    by_chip = {c.chip_name: c.device_id for c in demo.hardware_diagnostics().hwmon.chips_detected}
    headers = demo.hwmon_headers()
    assert headers
    for header in headers:
        assert header.device_id == by_chip[header.chip_name], header.id


def test_demo_device_ids_are_device_names_not_descriptions():
    chips = DemoService().hardware_diagnostics().hwmon.chips_detected
    assert chips
    for chip in chips:
        assert _DEVICE_NAME.match(chip.device_id), (chip.chip_name, chip.device_id)


def test_no_demo_device_name_is_claimed_by_two_chips():
    """One sysfs device carries one hwmon chip here; the demo had the NVMe drive
    and the NVIDIA GPU's nouveau node at the same PCI address."""
    chips_by_device: dict[str, set[str]] = {}
    for hwmon_id in _demo_hwmon_ids():
        chip, _, rest = hwmon_id.removeprefix("hwmon:").partition(":")
        # The device is everything before the last segment (sensor label, `inN`)
        # or before `:pwmN:` on a header.
        device = rest.partition(":pwm")[0] if ":pwm" in rest else rest.rpartition(":")[0]
        chips_by_device.setdefault(device, set()).add(chip)
    assert chips_by_device
    shared = {d: c for d, c in chips_by_device.items() if len(c) > 1}
    assert not shared, shared


def test_every_demo_header_has_a_fan_reading():
    """The Hardware page showed the Kraken pump with no RPM: the header existed
    but no fan reading carried its id."""
    demo = DemoService()
    rpm_by_id = {f.id: f.rpm for f in demo.fans()}
    headers = demo.hwmon_headers()
    assert any(h.is_aio for h in headers), "the demo should keep its AIO pump header"
    for header in headers:
        assert rpm_by_id.get(header.id), f"{header.id} has no demo fan reading"


def test_demo_pump_has_an_alias_and_reads_faster_than_a_fan_at_the_same_duty():
    demo = DemoService()
    pump = next(h for h in demo.hwmon_headers() if h.is_aio)
    fan = next(h for h in demo.hwmon_headers() if not h.is_aio)
    assert DemoService.fan_aliases()[pump.id]
    demo.set_fan_pwm(pump.id, 50)
    demo.set_fan_pwm(fan.id, 50)
    readings = {f.id: f for f in demo.fans()}
    assert readings[pump.id].last_commanded_pwm == 50
    assert readings[pump.id].rpm > readings[fan.id].rpm
