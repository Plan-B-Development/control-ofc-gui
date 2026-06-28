# OpenFan Controller

This page explains what an OpenFan Controller is, how Control-OFC works with one, and how to get it detected, identified, and controlled. If you do not have an OpenFan Controller you can skip this page — it does not apply to motherboard fan headers (see [Understanding Motherboard Fan Control](understanding-fan-control.md)) or to GPU fans.

## What the OpenFan Controller is

The **OpenFan Controller** (branded **OpenFAN**) is a USB fan controller that drives up to **10 fans** from a single board. It is an independent **open-source, open-hardware** project created by Sasa Karanovic (Karanovic Research):

- Project page: <https://sasakaranovic.com/projects/openfan-controller/>
- Store: <https://shop.sasakaranovic.com/products/openfan-pc-fan-controller>
- Source and hardware design (GitHub): <https://github.com/SasaKaranovic/OpenFanController>

**What this means:** the OpenFan Controller is **not** hardware made or sold by Control-OFC. Control-OFC is an independent, third-party way to drive it from Linux. For the device itself — firmware updates, the on-device web UI, warranty, where to buy — use the official links above.

## How Control-OFC talks to it

The OpenFan Controller connects to your PC over **USB**, where it appears as a serial port (a USB CDC-ACM device — `/dev/ttyACM*` on Linux). Control-OFC reaches it through the daemon:

- The **control-ofc-daemon** opens the serial port, polls each channel's RPM about once a second, and is the **only** component that sends speed commands to the controller.
- The **control-ofc-gui** is a client: it shows the controller's fans and lets you label them, assign curves, and override speeds — but it never talks to the USB device directly.

This is the same boundary as the rest of Control-OFC: the daemon owns the hardware; the GUI sends intent. The OpenFan Controller offers up to **10 channels** (numbered 0–9); each populated channel appears as a fan you can monitor and control.

## Detection and device paths

The daemon **auto-detects** the controller at startup — in the common case there is nothing to configure. It scans for USB serial devices (`/dev/ttyACM*`, and `/dev/ttyUSB*` for adapters that present that way) and connects to the OpenFan Controller it finds.

For a setup that survives reboots and re-plugging, prefer a **stable device path**. A name like `ttyACM0` can change order between boots; the `/dev/serial/by-id/` path does not:

```bash
ls -la /dev/serial/by-id/
# e.g. usb-Karanovic_Research_OpenFan_...-if00 -> ../../ttyACM0
```

Set that path in the daemon's config (`/etc/control-ofc/daemon.toml`):

```toml
[serial]
port = "/dev/serial/by-id/usb-Karanovic_Research_OpenFan_...-if00"
```

**What this means:** auto-detect is fine for trying it out; pin the `by-id` path if you want the controller to come back reliably after a reboot. The daemon's [Serial device setup](https://github.com/Plan-B-Development/control-ofc-daemon/blob/main/docs/USER_GUIDE.md#serial-device-setup-openfancontroller) section has the full configuration reference.

## Serial / USB access (permissions)

The daemon needs read/write access to the serial port. On the supported Arch / CachyOS packages this is already handled: the systemd service ships with the right device permissions (the `uucp` serial group plus a device allow-list) and ensures the USB serial kernel module (`cdc_acm`) is loaded. **No udev rule is required** for normal use.

> Granting a service access to a device is a system change. The packaged defaults are scoped to serial devices only; if you adjust them, make sure you understand what you are allowing. This guidance is provided **as-is**; the project accepts **no liability** for changes made to your system (MIT License).

Two cases need a manual step:

- **Debian / Ubuntu** (where the serial group is `dialout`, not `uucp`): add a systemd drop-in — `sudo systemctl edit control-ofc-daemon` and set `SupplementaryGroups=uucp dialout`.
- **You want a fixed symlink** (e.g. `/dev/control-ofc-controller`): the daemon repo ships an optional udev rules example (`99-control-ofc.rules`) you can copy and fill in with your device's USB vendor / product id. This is optional convenience, not a requirement.

## Identifying which fan is which

A channel number does not tell you which physical fan is on it. The [Fan Wizard](fan-wizard.md) solves this: it stops one fan at a time so you can see (or hear) which fan slows down, then lets you give it a friendly name. The wizard handles OpenFan channels the same way it handles motherboard and GPU fans, and the daemon automatically restores the fan if the process is interrupted.

## Using it in profiles

OpenFan channels are first-class fans in Control-OFC. On the [Controls](controls.md) page you can:

- add a channel to a **fan role** (group fans that should behave together),
- assign a **curve** so the fan responds to a temperature sensor, and
- apply a **manual override** to pin a temporary speed.

How roles, curves, and profiles fit together is covered in [Profiles and Curves](profiles-and-curves.md).

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Controller not detected | Daemon started before the device was plugged in, or a non-standard port | Plug in the controller, then `sudo systemctl restart control-ofc-daemon`. Confirm the device exists with `ls /dev/ttyACM*`. If it only appears under a non-standard path, set `[serial] port` explicitly (see above) |
| Detected, but no fans show RPM | Fans not connected to populated channels, or 3-pin fans with no tachometer | A `0` RPM on an empty or tach-less channel is normal. Connect a known-good 4-pin fan to confirm |
| Worked, then stopped after unplug / replug | USB re-enumeration | The daemon detects the dropout and **auto-reconnects** — after 5 consecutive failed reads it re-scans for the device with a backoff (about 1 s, up to 30 s) and resumes when it reappears. Pinning the `by-id` path makes reconnection reliable |
| Permission denied on the serial port | The service is not in the serial group (most likely on non-Arch distros) | Add the serial group via a systemd drop-in (see permissions above), then restart the daemon |
| A fan briefly stops, then restarts on its own | The controller will not hold a fan at 0% for more than a few seconds (a built-in safety) | Expected. Set a small non-zero minimum if you want the fan to keep spinning |

If the controller itself behaves oddly (firmware, the on-device web UI, the hardware), that is a question for the upstream project — see the official links above.

## Reference / Advanced

- [Serial device setup (daemon USER_GUIDE)](https://github.com/Plan-B-Development/control-ofc-daemon/blob/main/docs/USER_GUIDE.md#serial-device-setup-openfancontroller) — full daemon-side serial configuration
- [OpenFan Controller Integration — technical deep-dive](https://github.com/Plan-B-Development/control-ofc-gui/blob/main/docs/architecture/openfan-controller-integration.md) — the serial wire protocol, for firmware developers and integrators (a snapshot; daemon-side details have evolved since)
- Official project: [project page](https://sasakaranovic.com/projects/openfan-controller/) · [store](https://shop.sasakaranovic.com/products/openfan-pc-fan-controller) · [GitHub `SasaKaranovic/OpenFanController`](https://github.com/SasaKaranovic/OpenFanController)

---

Previous: [Understanding Motherboard Fan Control](understanding-fan-control.md) | Back to [Table of Contents](README.md)
