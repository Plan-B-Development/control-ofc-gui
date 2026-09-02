# Control-OFC GUI

**Latest release:** v2.53.1 — 2026-09-02. Pairs with `control-ofc-daemon` ≥ v2.11.0 (v2.16.0 or newer for the Settings ▸ Daemon Configuration card, v2.17.0 for the Dashboard's fan-control-engine health banner, v2.18.0 for Rescan Hardware to also look for an OpenFan controller, v2.21.0 for the Controls page's "Not controlled" badge, v2.22.0 for the live output figure on each Controls card and the Dashboard's unresolved-control warning, v2.23.0 for removing a profile search directory, v2.24.0 for the thermal-forcing verify refusal shown as a soft notice, v2.24.2 for the subsystem age shown as data freshness rather than poll time, v2.25.0 for the daemon to also distrust the widened `nct67xx` `CPUTIN` set the GUI warns about and to quarantine an implausibly-low CPU reading, v2.26.0 for the thermal ladder's forced duties to act as floors over profile output rather than replacements for it and for the emergency trip point to be derived per machine from the CPU's own reported ceiling, v2.27.0 for fan telemetry to be reported only where it was actually measured, v2.28.0 for per-channel PWM header roles and pump-safe fan identify; all stand down gracefully on older daemons). The v2.26.0 and v2.27.0 behaviours need no GUI support — nothing on the wire changed. The trip point is reported in a field the GUI already renders verbatim, and `rpm` was already optional with a fan's absence already meaning "not currently readable". v2.50.0 consumes the v2.28.0 `control.header_roles` capability: where the daemon classifies a header as a pump, the Fan Wizard says the pump's speed will change rather than that it will stop — and against any older daemon it keeps the "stop" wording, because that is what an older daemon actually does. v2.51.0 goes further and *writes* that capability: Configure AIO asks which header the pump is connected to and records it via `POST /config/header-role`, which is the only way a motherboard-connected AIO can be set up at all — its pump hangs off the Super-I/O chip like any other fan, so nothing can infer it. The same release offers three pump strategies (Automatic, Fixed speed, Custom curve) in place of fixed presets only, and retracts the claim that a pump must run at a constant speed. Against a pre-2.28.0 daemon the role step is hidden and the flow behaves exactly as v2.50.0 did. v2.52.0 adds **Characterise PWM Response** on the System State page — a deeper diagnostic beside the existing quick "Test PWM Control", which holds a header at a series of duties and reports **command acceptance, PWM readback and physical fan response as three separate verdicts**. That separation is the feature: a pump whose firmware drives itself during startup reports a perfectly correct PWM readback while its speed ignores the command, and a single pass/fail would call that a broken fan. It needs daemon v2.29.0 or newer and is hidden entirely without it, so against an older daemon this release behaves exactly as v2.51.0 did. v2.53.0 rebuilds the **Logs** page as a List + Inspector workflow: an activity strip showing event volume over time (click a column to filter to that slice), a dense two-line event list, newest first, with repeated events collapsed into one row, and a tabbed inspector holding the event's detail, its raw stored record, and the Daemon Status / Controller / GPU probes and system journal that used to sit in a strip along the bottom. Events now carry structured fields where the emitter genuinely had them — an alert's component, an exception's type, a rescan's header count — so related events can be correlated on something real rather than a source tag. **This release needs nothing new from the daemon**: no API, schema or contract change, and your saved log filters carry over. v2.53.1 fixes three defects found by an audit of that work: Characterise PWM Response could render a *different* header's sweep as this header's result, because the daemon serves one run at a time and the dialog never checked whose it was; the app's minimum window size was set to a width no page has ever fitted in, which is what squeezed the Logs toolbar's search box down to "Sea…" (**the minimum window width therefore grows to roughly 1400px**); and a restore the daemon deliberately skipped was reported as "Restoring the original speed failed — re-activate your profile", which is precisely the wrong advice when thermal safety is what skipped it. The last of those reads the daemon's new `restore_outcome` field and so words each reason properly against v2.30.0 or newer, keeping the previous message against anything older. See [`CHANGELOG.md`](CHANGELOG.md) for the full history.

Desktop fan control interface for Linux. Communicates with the [`control-ofc-daemon`](https://github.com/Plan-B-Development/control-ofc-daemon) service to monitor temperatures, manage fan speeds, and apply custom fan curves.

![Dashboard](screenshots/auto/01_dashboard.png)

## What is Control-OFC?

Control-OFC keeps your computer quiet and cool by managing its fans for you. Instead of fans that are always loud or always guessing, you tell Control-OFC how each fan should respond to temperature — and it handles the rest, automatically, in the background.

This is the **desktop app** (`control-ofc-gui`). It pairs with a small background service (`control-ofc-daemon`) that does the actual hardware work: the app is where you see what's happening and set up how your fans behave; the service keeps your fans controlled even when the app is closed.

Control-OFC controls fans in a few different places, and it helps to know which one you have:

- **OpenFan Controller** — a USB fan controller you plug fans into. Full control. → [OpenFan Controller guide](manual/openfan-controller.md)
- **Motherboard fan headers** — the fan connectors on your motherboard, reached through Linux's hwmon interface. Full control on most boards; some need a driver and/or one BIOS setting first. → [Understanding Motherboard Fan Control](manual/understanding-fan-control.md)
- **AMD GPU fans** — the fan on a discrete AMD graphics card. Full control (RDNA3+ cards need a one-time kernel setting).
- **Intel Arc GPU fans** — **monitor only.** Control-OFC shows their temperature and RPM, but Intel's firmware owns the fan and no Linux tool can set its speed.
- **NVIDIA GPU fans** — **monitor only.** Control-OFC shows their temperature and RPM (via the open `nouveau` driver or NVIDIA's NVML), but the fan is not driven through this daemon.

Just curious, or have no hardware yet? Explore the whole app with **demo mode** (`control-ofc-gui --demo`) — no daemon or hardware required.

## Features

- **Dashboard** — real-time sensor temperatures, fan RPM, active profile, system health with per-sensor freshness indicators; a dual-axis telemetry chart with latest-value markers and a hover tooltip
- **Controls** — profile switching, seven curve types (graph, stepped, linear, flat, trigger, plus mix/sync composites), fan roles, manual override; drag-resizable cards that snap to a shared grid (double-click the grip to reset)
- **Multi-source fan control** — OpenFan Controller channels, motherboard hwmon headers (daemon-managed), and AMD discrete GPU fans (PMFW `fan_curve` / legacy `pwm1`), with a one-click **Dedicate GPU Fan** setup for true 0-RPM idle when the GPU is cool
- **GPU monitoring** — AMD, Intel Arc, and NVIDIA discrete GPU temperatures and fan RPM (Intel Arc and NVIDIA fans are read-only — the GPU firmware owns them)
- **Settings** — GUI preferences, daemon runtime config, full theme editor with contrast checking, import/export
- **Overview · System State · Hardware · Logs** — connection health, subsystem status, 8-column sensor table, Test PWM Control / Test GPU Fan Control, Restore GPU Fan to Automatic, hardware rescan, hardware-readiness reporting, support bundle export
- **Fan Wizard** — guided fan identification and labelling
- **Demo mode** — full UI without hardware (`--demo`)

## Which path should I follow?

| Your situation | Start here |
|---|---|
| **I have an OpenFan Controller** | [OpenFan Controller guide](manual/openfan-controller.md) — plug it in and it auto-detects; then the [Setup Checklist](manual/setup-checklist.md) for profiles |
| **I want to control my motherboard fans** | [Setup Checklist](manual/setup-checklist.md) for the ordered path, [Understanding Motherboard Fan Control](manual/understanding-fan-control.md) for the "why", and [Driver Setup](manual/driver-setup.md) if your board needs a driver |
| **I want to control an AMD GPU fan** | [Setup Checklist](manual/setup-checklist.md), then [Driver Setup — AMD GPU prerequisite](manual/driver-setup.md#amd-gpu-fan-control-prerequisite-rdna3) for the one-time kernel setting RDNA3+ cards need |
| **I only want to try the GUI in demo mode** | `control-ofc-gui --demo` — the full interface with simulated hardware, no daemon required ([Getting Started → Demo Mode](manual/getting-started.md#demo-mode)) |

## Install

**Signed pacman repository (recommended).** Set it up once; both packages then
upgrade with your normal `sudo pacman -Syu`. Arch / x86_64.

**Bootstrap script (easiest).** Download it, verify its signature, read it, run
it. It trusts the signing key (checking the fingerprint first), adds the
repository, installs both packages, and enables the daemon — and it is safe to
re-run. The install is a full `pacman -Syu` and asks you to confirm the
transaction once, so it may upgrade more than control-ofc.

```bash
base=https://github.com/Plan-B-Development/pacman-repo/releases/download/repo
curl -fsSLO "$base/bootstrap.sh"
curl -fsSLO "$base/bootstrap.sh.sig"
curl -fsSL https://raw.githubusercontent.com/Plan-B-Development/pacman-repo/main/keys/control-ofc.gpg | gpg --import
gpg --verify bootstrap.sh.sig bootstrap.sh   # expect 4AAD6D2DE40D0D10773BF770BC27C5EB2831FCDA
less bootstrap.sh
bash ./bootstrap.sh
```

**Or by hand:**

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

**One-off install without touching `pacman.conf`:** every release also attaches
the same clean-room-built package the CI pipeline verifies.

```bash
# Both packages in one transaction — the GUI depends on the daemon
gh release download --repo Plan-B-Development/control-ofc-daemon --pattern '*.pkg.tar.zst'
gh release download --repo Plan-B-Development/control-ofc-gui    --pattern '*.pkg.tar.zst'
sudo pacman -U ./control-ofc-daemon-*.pkg.tar.zst ./control-ofc-gui-*.pkg.tar.zst
```

Upgrading then means repeating those commands — which is the chore the
repository above exists to remove. Each package additionally carries a keyless
[Sigstore](https://www.sigstore.dev/) build provenance attestation:

```bash
gh attestation verify ./control-ofc-gui-*.pkg.tar.zst \
  --repo Plan-B-Development/control-ofc-gui
```

**Build the package yourself** from the in-repo `PKGBUILD` instead — same
result, and it does not trust a prebuilt binary:

```bash
git clone https://github.com/Plan-B-Development/control-ofc-gui.git
cd control-ofc-gui/packaging
makepkg -si
```

> The in-repo `sha256sums` is `SKIP` rather than a pinned hash, so no
> `updpkgsums` step is needed. It cannot be a real hash: the tarball GitHub
> generates for a tag *contains* that `PKGBUILD`, so writing a sum into it
> changes the archive the sum is pinning. `makepkg` therefore trusts the HTTPS
> fetch from this repository's own tag. For a build whose input is pinned and
> verifiable, use the release asset and check its Sigstore attestation with the
> `gh attestation verify` command above.

> **The AUR package is no longer updated.** `control-ofc-gui` was published to
> the AUR through v2.34.0 and is frozen there. The AUR is a third-party service
> that goes read-only for maintenance without warning — the 2026-08-02 freeze
> took the *entire* AUR down to two accepted pushes in a day — so releases now
> go to GitHub only. If you installed with `paru -S control-ofc-gui`, the
> prebuilt-package command above upgrades it in place: it is the same
> `control-ofc-gui` package name, so `pacman -U` simply replaces the AUR copy,
> and no AUR helper will try to pull you back to the older frozen version.

**From source (development install):**

```bash
git clone https://github.com/Plan-B-Development/control-ofc-gui.git
cd control-ofc-gui
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# 1. Ensure daemon is running
systemctl is-active control-ofc-daemon

# 2. Launch the GUI
control-ofc-gui

# 3. Or try demo mode (no daemon required)
control-ofc-gui --demo
```

> **First-time daemon setup?** The daemon has its own prerequisites — kernel
> modules for your motherboard's Super I/O chip, possibly an AUR DKMS driver
> on newer Gigabyte / MSI / ASRock boards (2022+), and a kernel parameter
> for RDNA3+ AMD GPU fan control. Follow the ordered
> [Setup Checklist](manual/setup-checklist.md), see the
> [daemon prerequisites guide](https://github.com/Plan-B-Development/control-ofc-daemon#prerequisites),
> or open the **Hardware** page in the GUI after install — its Hardware
> Readiness report identifies your chip and recommends the exact
> AUR package.

## CLI

```
control-ofc-gui [OPTIONS]

Options:
  --socket <path>   Daemon socket path (default: /run/control-ofc/control-ofc.sock)
  --demo            Run in demo mode with simulated hardware
```

## Requirements

System:
- Python >= 3.12 (developed on 3.14)
- Linux (primary target: Arch Linux / CachyOS, KDE Plasma)
- A running `control-ofc-daemon` instance (or use `--demo`)
- `hicolor-icon-theme` (for the application icon to be picked up by launchers)

Python runtime dependencies (resolved automatically by `pip install` or
the Arch package — listed here for transparency):
- `PySide6 >= 6.6` — Qt6 bindings (UI toolkit)
- `httpx >= 0.27` — HTTP client used for the daemon's Unix-socket API
- `pyqtgraph >= 0.14` — chart rendering (timeline + curve editor)
- `numpy >= 2.0` — numerical helpers behind chart maths
- `colorama >= 0.4` — required transitively at `import pyqtgraph` time;
  pyqtgraph imports it unconditionally even on Linux

Development extras (`pip install -e ".[dev]"`):
- `pytest >= 8.0`, `pytest-qt >= 4.3`, `pytest-timeout >= 2.3`, `pytest-cov >= 5`, `ruff >= 0.4`, `pip-audit >= 2.7`, `mutmut >= 3.5`

## Configuration

| Location | Contents |
|----------|----------|
| `~/.config/control-ofc/app_settings.json` | GUI preferences |
| `~/.config/control-ofc/profiles/` | Local profile draft cache (daemon holds the canonical copies) |
| `~/.config/control-ofc/themes/` | Custom themes |

Daemon configuration: see the [daemon repo](https://github.com/Plan-B-Development/control-ofc-daemon) and the [Operations Guide](docs/18_Operations_Guide.md).

## Architecture

The GUI is a **daemon API client only**. All hardware access goes through the daemon's HTTP-over-Unix-socket API.

As of **2.0.0** the daemon owns all runtime control — it evaluates curves and is the sole writer of every fan backend. The GUI authors profiles, polls, and renders; it never writes PWM (DEC-159, DEC-165).

| Owned by GUI | Owned by daemon |
|-------------|----------------|
| Profile authoring + validation UX | Fan curve evaluation + all PWM writes |
| Theme / settings / alias persistence | Hardware access (hwmon, serial, GPU PMFW) |
| Local profile draft cache | Profile storage of record + thermal safety |
| User-facing presentation | Hwmon lease, sensor polling, socket/IPC |

See the [architecture docs](docs/02_System_Architecture_and_Boundaries.md) and [API contract](docs/08_API_Integration_Contract.md) for details.

## Documentation

- **[User Manual](manual/README.md)** — installation, features, and usage guide
- **[Setup Checklist](manual/setup-checklist.md)** — ordered path from fresh install to verified sensors and fan control
- **[Hardware Troubleshooting](manual/hardware-troubleshooting.md)** — Hardware Readiness, Test PWM Control, vendor quirks
- **[OpenFan Controller](manual/openfan-controller.md)** — the OpenFan USB fan controller: detection, serial access, channels, profiles, and troubleshooting
- **[Understanding Motherboard Fan Control](manual/understanding-fan-control.md)** — plain-English primer on hwmon, sysfs, Super I/O, and PWM, and why drivers/BIOS settings matter
- **[Hardware Compatibility](docs/19_Hardware_Compatibility.md)** — chip support matrix, kernel drivers, ACPI conflicts
- **[AMD Motherboard Fan Control Guide](docs/21_AMD_Motherboard_Fan_Control_Guide.md)** — vendor-by-vendor BIOS notes (Gigabyte, ASUS, MSI, ASRock)
- **[Intel Motherboard Fan Control Guide](docs/23_Intel_Motherboard_Fan_Control_Guide.md)** — Intel LGA1700/1851 Super-I/O fan control, per-vendor notes
- **[Sensor Interpretation Guide](docs/20_Sensor_Interpretation_Guide.md)** — what each sensor name means and which to trust
- **[AMD Sensor Interpretation Deep Dive](docs/22_AMD_Sensor_Interpretation_Deep_Dive.md)** — Tctl/Tdie, edge/junction, and common AMD-specific traps
- **[Architecture](docs/00_README_START_HERE.md)** — design docs and specs
- **[API Contract](docs/08_API_Integration_Contract.md)** — daemon endpoint reference
- **[Changelog](CHANGELOG.md)** — version history
- **[Contributing](CONTRIBUTING.md)** — build, test, and PR guidelines

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for build instructions, quality gates, and PR guidelines.

## License

MIT
