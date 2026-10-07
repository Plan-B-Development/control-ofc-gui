# Getting Started

## What You Need

Control-OFC requires:

- **Linux** with Python 3.12 or newer
- **control-ofc-daemon** running as a systemd service (provides the hardware interface)
- A supported fan controller (OpenFan Controller, motherboard hwmon headers, or AMD GPU)

The GUI never accesses hardware directly. All reads and writes go through the daemon's API over a local Unix socket.

### Supported coolers and controllers

The daemon drives hardware only through Linux's own drivers — it never talks to a USB
device directly and does not use `liquidctl` — so what it can reach is what the kernel
exposes.

| Hardware | What Control-OFC can do |
|---|---|
| **OpenFan Controller** (USB) | Full control of every channel — [OpenFan Controller](openfan-controller.md) |
| **Motherboard fan headers** — ITE and Nuvoton Super-I/O chips | Full control on most boards; some need an out-of-tree driver or a BIOS setting first — [Driver Setup](driver-setup.md) |
| **Dell laptops and some Dell desktops** (`dell_smm`) | Control on models the kernel driver allows; many share one BIOS switch across every fan — [Driver Setup](driver-setup.md) |
| **AMD discrete GPUs** | RX 7000/9000: full control through the firmware fan curve, which needs a one-time kernel setting — [Driver Setup](driver-setup.md#amd-gpu-fan-control-prerequisite-rdna3). RX 6000 and older: monitored, and the fan can be tested and reset, but a profile does not drive it — the card's own fan curve stays in charge |
| **Intel Arc and NVIDIA GPUs** | Monitor only: temperature and fan speed |
| **NZXT Kraken X53/X63/X73, Z53/Z63/Z73, Kraken 2023, 2023 Elite and 2024 Elite** (`nzxt-kraken3`; the 2024 Elite needs kernel 7.3 or newer) | Pump and fans controllable, coolant temperature read |
| **NZXT Kraken X42/X52/X62/X72** (`nzxt-kraken2`) | Monitor only: the driver cannot set its speeds |
| **Aquacomputer D5 Next** (`aquacomputer_d5next`) | Pump and fan controllable, coolant temperature read |
| **Aquacomputer High Flow Next and Leakshield** | Coolant temperature read; they drive no fan |
| **Fan controllers:** Corsair Commander Pro, NZXT Smart Device V2 / RGB & Fan Controller, Aquacomputer Octo, Quadro and Aquaero | Fan channels controllable |
| **Any AIO whose pump plugs into a motherboard header** | Controlled as a motherboard header; tell **Configure AIO** which header is the pump so it gets its protection — [Controls](controls.md#if-your-aio-is-plugged-into-the-motherboard) |

A cooler with no mainline kernel driver — most USB coolers not listed here — is not
visible to Control-OFC. Plugging its pump into a motherboard header, where the cooler
allows it, is the usual way round that.

### Daemon prerequisites

The daemon has its own prerequisites — kernel modules for your motherboard's
Super I/O chip, possibly an AUR DKMS driver on newer Gigabyte / MSI / ASRock
boards (2022+), and (for RDNA3+ AMD GPUs) the kernel parameter
`amdgpu.ppfeaturemask=0xffffffff`. See the
[daemon prerequisites guide](https://github.com/Plan-B-Development/control-ofc-daemon#prerequisites)
before installing the daemon — it covers BIOS settings, kernel modules,
and per-bootloader steps for the kernel parameter.

If you have already installed the daemon, the quickest way to discover
what your specific system needs is to launch the GUI and open the
**System State** page, which inspects your hardware and recommends the exact
AUR packages or kernel parameters required; the **Hardware** page's readiness
checklist gives the daemon's own go/no-go answer beside it.

For the complete ordered path — install → verify sensors → readiness check →
drivers/BIOS/GPU branch → verify control → first profile — follow the
[Setup Checklist](setup-checklist.md).

New to Linux and told you need a driver? The [Driver Setup](driver-setup.md)
page of this manual is a copy-paste beginner walkthrough — identify the
chip, install the right DKMS package, verify it works, and roll it all
back if needed.

Want the bigger picture first? [Understanding Motherboard Fan Control](understanding-fan-control.md)
is a plain-English primer on how Linux controls motherboard fans — hwmon,
Super I/O chips, drivers, and BIOS settings — and why each setup step is
asked of you. Using an [OpenFan Controller](openfan-controller.md)? That
USB fan controller has its own page covering detection, permissions, and
troubleshooting.

## Installation

### Arch Linux — signed pacman repository (recommended)

Set it up once; both packages then upgrade with your normal `sudo pacman -Syu`.

#### The easy way: the bootstrap script

There is a signed script that does the whole setup for you. It trusts the signing
key (checking the fingerprint first), adds the repository, installs both packages
and enables the daemon. It is safe to re-run.

Verify its signature before running it — that is the point of signing it, and the
step is what stops a tampered script from being the thing that installs your
system packages:

```bash
base=https://github.com/Plan-B-Development/pacman-repo/releases/download/repo
curl -fsSLO "$base/bootstrap.sh"
curl -fsSLO "$base/bootstrap.sh.sig"
curl -fsSL https://raw.githubusercontent.com/Plan-B-Development/pacman-repo/main/keys/control-ofc.gpg | gpg --import
gpg --verify bootstrap.sh.sig bootstrap.sh   # expect 4AAD6D2DE40D0D10773BF770BC27C5EB2831FCDA
less bootstrap.sh                            # read it — you are about to run it as root
bash ./bootstrap.sh
```

The install is a full `sudo pacman -Syu` and asks you to confirm the transaction
once, so it may upgrade more than control-ofc. If you would rather see every step,
use the manual path below — the script does exactly the same things.

#### By hand

```bash
# 1. trust the signing key
curl -fsSL https://raw.githubusercontent.com/Plan-B-Development/pacman-repo/main/keys/control-ofc.gpg \
  | sudo pacman-key --add -
sudo pacman-key --lsign-key 4AAD6D2DE40D0D10773BF770BC27C5EB2831FCDA

# 2. add the repository — run once; `tee -a` would append a duplicate block
grep -q '^\[control-ofc\]' /etc/pacman.conf || sudo tee -a /etc/pacman.conf <<'EOF'

[control-ofc]
SigLevel = Required
Server = https://github.com/Plan-B-Development/pacman-repo/releases/download/repo
EOF

# 3. install — the daemon comes along as a dependency
sudo pacman -Syu control-ofc-gui
sudo systemctl enable --now control-ofc-daemon
```

`SigLevel = Required` means pacman refuses any package or database not signed by
that key. Details, upgrade and removal instructions:
[Plan-B-Development/pacman-repo](https://github.com/Plan-B-Development/pacman-repo).

### One-off install, without touching `pacman.conf`

Every release also attaches the same clean-room-built package the CI pipeline
verifies:

```bash
gh release download --repo Plan-B-Development/control-ofc-daemon --pattern '*.pkg.tar.zst'
gh release download --repo Plan-B-Development/control-ofc-gui    --pattern '*.pkg.tar.zst'
sudo pacman -U ./control-ofc-daemon-*.pkg.tar.zst ./control-ofc-gui-*.pkg.tar.zst
```

Upgrading then means repeating those commands — which is the chore the
repository above exists to remove.

> **The AUR package is no longer updated.** `control-ofc-gui` was published to
> the AUR through v2.34.0 and is frozen there. If you installed with
> `paru -S control-ofc-gui`, either path above upgrades it in place — it is the
> same package name, so pacman simply replaces the AUR copy. This applies to
> *this* package only; the out-of-tree DKMS drivers on the
> [Driver Setup](driver-setup.md) page are separate third-party AUR packages
> and are still installed from the AUR.

### From Source

```bash
git clone https://github.com/Plan-B-Development/control-ofc-gui.git
cd control-ofc-gui
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Arch and CachyOS refuse a `pip install` outside a virtual environment (their Python
is "externally managed", PEP 668), so the venv is not optional there. The
`control-ofc-gui` command then exists only inside it: activate the venv first, or
run `.venv/bin/control-ofc-gui`. A source install does not include the daemon —
install `control-ofc-daemon` as above.

## First Launch

```bash
control-ofc-gui
```

On first launch, Control-OFC will:

1. Attempt to connect to the daemon at `/run/control-ofc/control-ofc.sock` (`control-ofc-gui --socket <path>` points it elsewhere — for a daemon started with a non-default socket)
2. If the daemon is reachable, fetch hardware capabilities and begin polling
3. If the daemon is not reachable, show a "Disconnected" state (or enter demo mode if configured)
4. Open the **Dashboard** page

### Demo Mode

If you want to explore the interface without hardware or a running daemon:

```bash
control-ofc-gui --demo
```

Demo mode generates synthetic sensor temperatures and fan speeds. You can create profiles, edit curves and explore the UI, but not everything works as it does against a real daemon: the PWM Test Report will not start, hardware advisories and some checks are skipped, and Import Config and the profiles and themes folder settings are unavailable. **System State** and the Hardware page's **Voltages** table show a synthetic Gigabyte X870E AORUS MASTER, and a support bundle exported in demo carries those synthetic readings. **Profiles you create or edit in demo are not saved** — they last until demo ends. The top ribbon and the status banner read *Demo — simulated* where they would show the daemon connection, a **DEMO** badge appears in the status banner, and the footer's mode reads *Demo mode*, so you always know when synthetic data is being shown.

You can also enable "Start in demo mode when daemon is unavailable" in Settings so the GUI falls back to demo automatically.

### Only One Window

Launching Control-OFC while it is already running brings the existing window to
the front rather than opening a second copy. This applies however you start it —
the system tray, the application menu, or a terminal.

Demo mode is treated separately, so `control-ofc-gui --demo` still opens its own
window even if a normal Control-OFC is already running.

On Wayland the compositor decides whether a window may jump to the front. Usually
it does; occasionally it will flag the task-bar entry as needing attention
instead of raising the window. Clicking that entry brings it up.

### The System Tray

If you are on KDE Plasma, a Control-OFC icon appears in your system tray. It is
installed with the daemon and starts when you log in.

- **Left click** — open Control-OFC.
- **Right click** — see which daemon version is running, switch profiles, stop
  profile control, or open Control-OFC.

The tray never controls fans itself; it asks the daemon, exactly as this
application does. Closing it changes nothing about cooling.

To turn it off: **System Settings → Autostart**. For anything more, see
`man control-ofc-tray`.

## The Status Bars

Three strips frame every page:

| Strip | What it shows |
|-------|---------------|
| **Top ribbon** (every page) | The daemon's connection state with a status light (*Demo — simulated* in demo mode), the daemon's uptime, a thermal pill, and **Alerts** with a count — click it to jump to the **Logs** page, the single surface that lists them |
| **Status banner** (under the ribbon, on every page except the Dashboard, which has its own status strip) | Connection — green "Connected", yellow "Degraded" or red "Disconnected" ("Demo — simulated" in demo mode) — the active profile (or "No profile"), the number of warnings (not clickable; use **Alerts**), and a **DEMO** badge in demo mode |
| **Footer** (every page) | How long ago the last poll arrived, the mode — "Automatic" (the daemon runs your fans), "Read-only" (the daemon is not connected; it clears on the next successful poll) or "Demo mode" — the thermal state (click it for the detail), a hardware-readiness chip, a health light, **Rescan Hardware** and **Export Support Bundle** |

Two banners can appear across the top of the window when the GUI and the daemon do not fit together:

- **"Daemon upgrade required"** — the daemon is older than 2.0.0, which this GUI cannot control, so the GUI stands down. Upgrade `control-ofc-daemon`.
- **"This GUI is older than the daemon supports"** — the daemon declares a minimum GUI version above this one. Fan control is unaffected — the daemon drives the fans itself — but some screens may not show everything the daemon can do. Upgrade `control-ofc-gui`.

### Keyboard shortcuts

| Where | Keys |
|-------|------|
| **Controls** page | `Ctrl+S` saves the profile; `Esc` closes the curve editor while it has focus |
| Curve editor | `Ctrl+Z` / `Ctrl+Shift+Z` undo and redo; `Delete` or `Backspace` removes the selected point |
| **Logs** page (list focused) | `/` jumps to the search box; `f` toggles follow; `Esc` closes the inspector |
| Dashboard **Sensors** panel | `F2` renames the selected fan |

> If the daemon's API version does not match the version this GUI was built for (an out-of-lockstep package upgrade), the Dashboard shows a warning banner asking you to align the `control-ofc-daemon` and `control-ofc-gui` package versions. This is non-fatal — the GUI keeps working — but some features may misbehave until the versions match.

## Navigation

The left sidebar provides access to all of the application's pages:

| Page | Purpose |
|------|---------|
| **Dashboard** | At-a-glance monitoring: temperatures, fan speeds, charts |
| **Overview** | Daemon health, device discovery, and live sensor and fan status |
| **Controls** | Profile management, fan grouping, curve editing |
| **System State** | The health report: chip and driver detection, BIOS interference, thermal and GPU limits, and advanced fan and GPU tests |
| **Hardware** | The daemon's readiness checklist, your coolers and every PWM header (**Cooling Hardware**), the fan tests and the **PWM Test Report** (**Hardware Diagnostics**), and the Super-I/O architecture |
| **Settings** | Application preferences and backup/restore |
| **Theme** | Fonts, sizes, and the colour-token editor |
| **Logs** | Event log and current alerts (the support bundle is exported from the footer) |

An **About** button at the bottom of the sidebar shows version and credit information:

![About dialog](https://raw.githubusercontent.com/Plan-B-Development/control-ofc-gui/main/screenshots/auto/09_about_dialog.png)

---

Next: [Setup Checklist](setup-checklist.md)
