# OpenFan Controller

This page explains what an OpenFan Controller is, how Control-OFC works with one, and how to get it detected, identified, and controlled. If you do not have an OpenFan Controller you can skip this page — it does not apply to motherboard fan headers (see [Understanding Motherboard Fan Control](understanding-fan-control.md)) or to GPU fans.

## What the OpenFan Controller is

The **OpenFan Controller** (branded **OpenFAN**) is a USB fan controller that drives up to **10 fans** from a single board. It is an independent **open-source, open-hardware** project created by Sasa Karanovic (Karanovic Research):

- Project page: <https://sasakaranovic.com/projects/openfan-controller/>
- Store: <https://shop.sasakaranovic.com/products/openfan-pc-fan-controller>
- Source and hardware design (GitHub): <https://github.com/SasaKaranovic/OpenFanController>

**What this means:** the OpenFan Controller is **not** hardware made or sold by Control-OFC. Control-OFC is an independent, third-party way to drive it from Linux. For the device itself — its firmware releases, the on-device web UI, warranty, where to buy — use the official links above. Control-OFC can install a firmware release you have downloaded from there: see [Updating the OpenFAN firmware](#updating-the-openfan-firmware).

## How Control-OFC talks to it

The OpenFan Controller connects to your PC over **USB**, where it appears as a serial port (a USB CDC-ACM device — `/dev/ttyACM*` on Linux). Control-OFC reaches it through the daemon:

- The **control-ofc-daemon** opens the serial port, polls each channel's RPM about once a second, and is the **only** component that sends speed commands to the controller.
- The **control-ofc-gui** is a client: it shows the controller's fans and lets you label them, assign curves, and override speeds — but it never talks to the USB device directly.

This is the same boundary as the rest of Control-OFC: the daemon owns the hardware; the GUI sends intent. The OpenFan Controller offers up to **10 channels** (numbered 0–9); each populated channel appears as a fan you can monitor and control.

## Detection and device paths

The daemon **auto-detects** the controller at startup — in the common case there is nothing to configure. It lists the USB serial devices that exist (`/dev/ttyACM*`, and `/dev/ttyUSB*` for adapters that present that way), then opens each in turn and asks it to identify itself; only a device that answers as an OpenFan Controller is adopted.

If you have **other** USB-serial hardware attached — an Arduino, a 3D printer — it is worth knowing that identifying a device means opening it, and on Linux opening a serial port asserts DTR, which resets Arduino-class boards. The daemon opens each candidate at most once per attempt for that reason. Startup itself makes just one attempt and then gets out of the way; the search carries on in the background once the daemon is up, for 60 seconds by default and 180 if you have named a port. That background search only opens anything when the set of serial devices actually *changes*, so plugging the controller in during the window is picked up within a few seconds, while a machine whose devices never change is never re-probed.

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

The daemon needs read/write access to the serial port, and the package already handles it on every distribution: the daemon runs as root, so no serial group is involved, and its systemd service allows it the `/dev/ttyACM*` and `/dev/ttyUSB*` devices and loads the USB serial kernel module (`cdc_acm`). **No udev rule and no group setup is required** for normal use.

> Granting a service access to a device is a system change. The packaged defaults are scoped to serial devices only; if you adjust them, make sure you understand what you are allowing. This guidance is provided **as-is**; the project accepts **no liability** for changes made to your system (MIT License).

Two cases need a manual step:

- **Your controller appears as something other than `/dev/ttyACM*` or `/dev/ttyUSB*`** (a `/dev/serial/by-id/` link to one of those is fine): the service is not allowed to open it. Add a systemd drop-in: run `sudo systemctl edit control-ofc-daemon` and enter a `[Service]` line followed by a `DeviceAllow=` line for that device class, for example `DeviceAllow=char-ttyS rw`. Save, then `sudo systemctl restart control-ofc-daemon` — the running daemon keeps its old device rules until it restarts.
- **You want a specific group or mode on the device node**: the daemon repo ships an optional udev rules example (`99-control-ofc.rules`) you can copy and fill in with your device's USB vendor / product id. This is optional convenience, not a requirement. For a fixed path, use the `/dev/serial/by-id/` link above rather than a custom udev symlink: the daemon refuses a name such as `/dev/control-ofc-controller`.

## Identifying which fan is which

A channel number does not tell you which physical fan is on it. The [Fan Wizard](fan-wizard.md) solves this: it stops one fan at a time so you can see (or hear) which fan slows down, then lets you give it a friendly name. The wizard handles OpenFan channels the same way it handles motherboard and GPU fans, and the daemon automatically restores the fan if the process is interrupted.

The controller does not store channel names, so until you name one a channel shows
as **OpenFan CH0**, **OpenFan CH1**, and so on. If you already named your channels
while assigning them to a profile, those names are adopted automatically on first
run — you should see them rather than the channel numbers. Once you know which is which, you
can rename a channel directly wherever it appears — double-click it in the
Dashboard's Sensors panel, or right-click it there or in the Overview fan table.
See [Naming your fans](dashboard.md#naming-your-fans).

## Using it in profiles

OpenFan channels are first-class fans in Control-OFC. On the [Controls](controls.md) page you can:

- add a channel to a **fan role** (group fans that should behave together),
- assign a **curve** so the fan responds to a temperature sensor, and
- apply a **manual override** to pin a temporary speed.

How roles, curves, and profiles fit together is covered in [Profiles and Curves](profiles-and-curves.md).

If a pump is plugged into an OpenFan channel, set that channel's role to **Pump** under *OpenFan Channels* on the Hardware page (control-ofc-daemon 3.5.0 or newer). Until you do, the daemon has no way to know it is a pump: a curve can stop it, and fan identification stops it. With the role set it is held at or above the pump safety floor and never stopped. See [Diagnostics § OpenFan channels](diagnostics.md).

To find the lowest speed a particular fan will reliably run at, use **Calibrate OpenFan Channel…** on the Hardware page (control-ofc-daemon 3.1.0 or newer): it measures where the fan stops and where it starts again, and tells you the minimum to use. See [Hardware Troubleshooting § Calibrate OpenFan Channel](hardware-troubleshooting.md#calibrate-openfan-channel).

## Updating the OpenFAN firmware

**Update OpenFAN Firmware…**, in the Hardware page's **Hardware Diagnostics** section, installs a firmware file you have downloaded onto the controller without opening the case. It appears with control-ofc-daemon 3.8.0 or newer while an OpenFAN controller is present — or an OpenFAN board is on USB but does not answer (see *A board that does not answer* below) — and is disabled, with the reason in its tooltip, while the controller is not connected or a PWM Test Report runs. The firmware is the upstream project's: download it from the official releases page, <https://github.com/SasaKaranovic/OpenFanController/releases>. Control-OFC never downloads firmware. The daemon puts the board in update mode and back, and the file is written either by **you**, copying it onto the drive that appears, or — for a published release Control-OFC knows, once you have allowed it (see *Letting Control-OFC write the firmware* below) — by the daemon itself, which then reads every byte back.

**Before you start**

- Leave the computer idle — no game, render or compile — for the few minutes it takes.
- Every OpenFAN channel, **pumps included**, goes to 100 % when the update starts. Expect it to be loud.
- While the board is in update mode, Control-OFC cannot change its channels. Its fan chips should hold that 100 % — the board's design says so, though it has not yet been measured. Fans on motherboard headers stay under normal control, and the daemon's thermal emergency still forces them.
- When the new firmware starts, it runs every channel at its own default for a few seconds — in the published firmware about 1000 RPM, never below 40 % — until Control-OFC takes over again. **A pump on the board slows down for those seconds.** If your cooling cannot take that, move the pump to a motherboard header first, or use the upstream procedure below, which is done with the board unpowered.
- The window lists the board's channels and marks the ones the daemon protects as pumps.

**Steps**

1. Click **Update OpenFAN Firmware…**. The window shows the controller: its connection, USB serial number and port, and the hardware and firmware reports it gives.
2. **Choose firmware file…** The window checks the file before anything else happens: a UF2 file for the RP2040 chip the OpenFAN uses, complete and in order, carrying the OpenFAN's USB names. A file that fails is refused with the reason, and so is the 2023 FW_01 binary: it is a pre-production debug build that floods the serial link and drives no fan. The window also shows the file's hardware revision beside the board's (shown, never enforced) and says when the file is byte-for-byte a published release.
3. The checked file is copied to `~/.cache/control-ofc/firmware/`, as `OpenFAN-` and the first eight characters of its SHA-256 fingerprint. That private copy is the one you drag across if you copy the file, so it cannot change between the check and the copy.
4. The window says who writes the file. With control-ofc-daemon 3.8.0 or newer it hands the checked file to the daemon, which says whether it is a published release it would write itself — and the daemon writes one only once you have allowed it to open USB devices. Otherwise — another build, or no permission — you copy the file. The confirmation names who writes it.
5. Tick the confirmation and click **Start update**. The daemon checks that the board is the one you chose (by its USB serial number) and that nothing else is using it — a calibration, a PWM test, a validation recording, a thermal emergency, or another board already in update mode each refuse the start, with the reason. It then sets every channel to 100 % and asks the board to restart in update mode; if the board does not respond, it tries the standard 1200-baud signal on the same port.
6. **If Control-OFC writes the file**, there is nothing to copy. It first checks that the board in update mode is yours — by its flash chip's id, from which the firmware makes its USB serial number — and writes nothing otherwise. It then writes the file, with a progress bar, reads every byte back and restarts the board. If anything stops it before the restart, it gives the drive back and the window asks you to copy the prepared file as in the next step, saying why. When the board in update mode was not yours, nothing was written: copy the file only if you are sure the drive is the OpenFAN board's.
7. **If you copy the file**, a drive called **RPI-RP2** appears, and the window names it (for example `sdb`). Open it in your file manager — most desktops mount it by themselves — and drag the prepared file onto it, from the window's file handle or from **Open folder**. You have 15 minutes. If another board is also in update mode, the window names its drive too: do not copy to that one.
8. The board restarts — by itself once a copy has finished, or when Control-OFC restarts it after writing — and the drive disappears. The daemon waits for the board to come back on the same USB port, checks that it answers, compares what it reports with the file and with what it reported before, and gives the fans back to your profile. If the board comes back in update mode instead, the window says so and waits for a file again, within what is left of the 15 minutes; after the third time the update stops and the board needs recovery.

If the daemon does not answer **Start update** — it timed out, or the connection dropped — the window says the update may have started and asks the daemon. It follows the update if one began, and says it did not start only once the daemon has answered with none.

**Cancel** works only while the daemon is checking the board and parking the fans. Once it has asked the board to restart, the update can only be finished. **Closing the window does not stop an update**: reopen it from the same button, which stays available while an update runs or needs recovery. While it runs, the Dashboard shows one *OpenFAN firmware update in progress* warning in place of each OpenFAN fan's *telemetry stale* warning; a stalled fan, every other fan's warnings and the thermal banner are unaffected.

**The result**

| Result | What it means |
|---|---|
| **Update complete — exact build verified** | Control-OFC wrote the file, read every byte back, and the board restarted from it straight away and answered. The only result that names the exact build. |
| **Update complete** | Control is back, and the board's reports are consistent with the file. No check can prove the exact build: the firmware carries no build number, and two releases can report the same things. |
| **Update complete — firmware not confirmed** | Control is back, but its reports cannot tell the old firmware from the new — for example because the file reports exactly what the firmware it replaced did. |
| **The update was not applied** | Control is back, but the board reports the firmware it had before. Check that the right file went onto the right drive, then start again. |
| **No firmware change** / **Update cancelled** | Nothing changed, and the fans are back under your profile — for a board that does not answer, nothing on it was changed and it stays as it was. The window says why. |
| **Board back, fan control not confirmed** | The board answers, but its fan settings had not all landed in time. The daemon keeps trying: watch the OpenFAN fans on the Dashboard, and restart the daemon if they do not follow your profile within a few seconds. |
| **Firmware copied, board not back** | The drive went away, but the board did not come back answering. Wait a minute — the daemon keeps watching. If the board is back on USB but never answers, the new firmware may not understand Control-OFC's commands: a future firmware can change them. That is not a failed copy. Copy back the firmware you had before — the window offers to, once the daemon reports the board as not answering (see *A board that does not answer*) — or wait for a Control-OFC update. |
| **The board needs recovery** | The board is in update mode, or may be: no firmware was copied, or Control-OFC's own write stopped before the board restarted from it. The window says which. See below. |

**If the board needs recovery.** A board in update mode leaves it in one of three ways: a firmware file is copied onto its **RPI-RP2** drive (the prepared file, or the firmware you had before), its **RESET** button is pressed, or the PC is switched off and on. Control-OFC does not take it out by itself: it restarts a board only after writing a firmware and reading it back. If Control-OFC's own write stopped part-way, part of the flash is already rewritten: only copying a firmware file brings the board back, and RESET or switching off and on does not. If it had written and read back the whole file, RESET starts the new firmware. Until the board answers again the daemon leaves its channels alone, the window and an error alert say so, and the daemon keeps watching — after a daemon restart too — and takes the board back as soon as it answers. If no RPI-RP2 drive appears at all, use the upstream BOOT-button procedure below.

**A board that does not answer.** An OpenFAN board can be on USB and still not answer Control-OFC: it runs the 2023 FW_01 build, which floods its serial link; a firmware with other commands; a firmware that has hung; or a firmware an earlier update left it with. Its fans are then not under fan control, and the daemon's thermal emergency cannot reach them. Once the daemon has opened the board and had no answer, it reports it — never on the board's USB names alone — and the Dashboard shows one *OpenFAN board not answering* warning naming its USB port, in place of each OpenFAN fan's *telemetry stale* warning. **Update OpenFAN Firmware…** then updates that board, with control-ofc-daemon 3.8.0 or newer. This works as above, with these differences — and it has not yet been tried on real hardware:

- Nothing is set to 100 % first: the board takes no commands, so its channels stay wherever its firmware has them, before and during the update. The window says so, and the confirmation names it.
- The daemon asks the board once more; one that answers now is not updated but taken back under fan control. Otherwise it sends the standard 1200-baud signal and waits for update mode.
- If the board does not enter update mode by itself, the window asks you to put it there: **hold its BOOT button, press and release its RESET button, then release BOOT.** You have 10 minutes. **Cancel** stops the update without changing anything — unless the board is already restarting into update mode. Then the update goes on rather than leave the board there, and the window says the cancel came too late.
- Once the board answers again, the daemon takes it over as the controller. If it never does, the daemon keeps watching for it, as after any update that needs recovery.

**Letting Control-OFC write the firmware (optional).** The daemon may open USB devices only once you install a small systemd drop-in that ships with it, off by default. With it, any local account can start an update that writes one of the published releases Control-OFC knows — never another file; without it, every update is copied by hand. To install it:

```
sudo install -Dm644 /usr/share/doc/control-ofc-daemon/openfan-firmware-write.conf.example /etc/systemd/system/control-ofc-daemon.service.d/openfan-firmware-write.conf
sudo systemctl daemon-reload
sudo systemctl restart control-ofc-daemon
```

Then click **Read again** in the window. To remove it, delete `/etc/systemd/system/control-ofc-daemon.service.d/openfan-firmware-write.conf` with `sudo rm`, and run the last two commands again.

**Updating by hand.** Two ways that do not use the window:

- **The upstream procedure** (from the [firmware README](https://github.com/SasaKaranovic/OpenFanController/tree/master/Firmware)): disconnect all fans and power from the board, hold its **BOOT** button while connecting the USB cable, copy the firmware onto the drive that appears, and power-cycle the board afterwards.
- **From a terminal, with the board in place.** Stop the daemon first — it holds the serial port — with `sudo systemctl stop control-ofc-daemon`. While it is stopped, motherboard headers go back to the mode the daemon found them in — normally your BIOS's own control — and the OpenFAN keeps the speeds it was last given. List the board's serial interfaces with `ls /dev/serial/by-id/`: there are two, named `usb-Karanovic_Research_OpenFan_<serial>-if00` and `-if02`. Run `stty -F /dev/serial/by-id/usb-Karanovic_Research_OpenFan_<serial>-if00 1200`, with your board's serial. The board restarts in update mode and the RPI-RP2 drive appears; copy the firmware onto it, wait for the drive to disappear, and start the daemon again with `sudo systemctl start control-ofc-daemon`.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Controller not detected | Daemon started before the device was plugged in, or a non-standard port | Plug in the controller, then use **Rescan Hardware** in the footer — no restart needed. Confirm the device exists with `ls /dev/ttyACM*`. If it only appears under a non-standard path, set `[serial] port` explicitly (see above), which also makes the daemon retry for longer at boot |
| Detected, but no fans show RPM | Fans not connected to populated channels, or 3-pin fans with no tachometer | A `0` RPM on an empty or tach-less channel is normal. Connect a known-good 4-pin fan to confirm |
| Worked, then stopped after unplug / replug | USB re-enumeration | The daemon detects the dropout and **auto-reconnects** — after 5 consecutive failed reads it tries again on a backoff that doubles up to 30 poll intervals — about 1 s up to 30 s at the default poll interval, and proportionally shorter if you have lowered it — and resumes when it reappears. Each try opens only your configured port, the controller's own device, and a serial device that has just appeared, so other USB-serial hardware is left alone. A pinned `by-id` path is tried first on every attempt |
| Permission denied on the serial port | The service is not in the serial group (most likely on non-Arch distros) | Add the serial group via a systemd drop-in (see permissions above), then restart the daemon |
| *OpenFAN board not answering* warning | The board runs firmware Control-OFC cannot talk to — such as the 2023 FW_01 build — or has hung | Update it from **Update OpenFAN Firmware…** (see [A board that does not answer](#updating-the-openfan-firmware)). If it has hung, press its **RESET** button, then **Rescan Hardware** in the footer |
| A fan briefly stops, then restarts on its own | The controller will not hold a fan at 0% for more than a few seconds (a built-in safety) | Expected. Set a small non-zero minimum if you want the fan to keep spinning |

If the controller itself behaves oddly (the firmware's own behaviour, the on-device web UI, the hardware), that is a question for the upstream project — see the official links above. Installing a firmware release is covered in [Updating the OpenFAN firmware](#updating-the-openfan-firmware).

## Reference / Advanced

- [Serial device setup (daemon USER_GUIDE)](https://github.com/Plan-B-Development/control-ofc-daemon/blob/main/docs/USER_GUIDE.md#serial-device-setup-openfancontroller) — full daemon-side serial configuration
- [OpenFan Controller Integration — technical deep-dive](https://github.com/Plan-B-Development/control-ofc-gui/blob/main/docs/architecture/openfan-controller-integration.md) — the serial wire protocol, for firmware developers and integrators (a snapshot; daemon-side details have evolved since)
- Official project: [project page](https://sasakaranovic.com/projects/openfan-controller/) · [store](https://shop.sasakaranovic.com/products/openfan-pc-fan-controller) · [GitHub `SasaKaranovic/OpenFanController`](https://github.com/SasaKaranovic/OpenFanController)

---

Previous: [Understanding Motherboard Fan Control](understanding-fan-control.md) | Back to [Table of Contents](README.md)
