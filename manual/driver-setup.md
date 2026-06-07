# Driver Setup (Beginner Walkthrough)

This page walks a **new Linux user** from "my motherboard fans don't show up" to "verified working fan headers", one copy-paste step at a time. It also covers the one [kernel parameter](#amd-gpu-fan-control-prerequisite-rdna3) that AMD RDNA3+ GPU fan control needs, and [what to do when Secure Boot blocks a driver](#secure-boot-and-dkms-modules). It targets **Arch Linux and CachyOS** (the platforms Control-OFC is packaged for); the concepts carry to other distributions but the commands will differ.

If you already know your way around DKMS and modprobe, the condensed reference lives in the [AMD](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) and [Intel](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) fan-control guides.

> ## ⚠ Read this first
>
> These steps change kernel parameters, driver/module configuration, or firmware (UEFI/BIOS) settings. Apply them at your own risk and back up your configuration first — an incorrect kernel parameter can stop the system booting.
>
> All commands on this page are provided **as-is, without warranty of any kind**. You run them at your own risk. The Control-OFC project and its contributors are **not responsible** for hardware, firmware, or data damage, boot failures, or any other consequence of following this guide. The drivers installed here are **third-party, out-of-tree kernel modules** maintained by their respective upstream projects, not by Control-OFC.
>
> If anything here conflicts with what your hardware vendor or distribution documents, prefer their guidance.

## Step 0 — Do you even need this?

Many boards work out of the box with mainline kernel drivers. Check first:

1. Start the GUI and open **Diagnostics → Troubleshooting**.
2. Click **Refresh Hardware Diagnostics**.
3. Look at the **Hardware Readiness** summary line.

If it reports your PWM headers with a non-zero **writable** count and no issues, you are done — no driver work needed. If it reports *"No hwmon chips detected"*, *"All PWM headers are read-only"*, or a chips-table row whose status says **"not loaded — install …"**, continue below.

## Step 1 — Identify your board and chip

The readiness report's **Board info** row shows what DMI reports (e.g. `Gigabyte Technology Co., Ltd. — X870E AORUS MASTER`), and the **chips table** lists every detected Super-I/O chip with the driver it needs. The report is the easiest path because it already cross-references the project's chip knowledge base.

From a terminal, the same facts come from:

```bash
# Board identity
cat /sys/class/dmi/id/board_vendor /sys/class/dmi/id/board_name

# Which hwmon chips the kernel currently exposes
cat /sys/class/hwmon/hwmon*/name

# What the it87 driver saw at probe time (ITE boards)
sudo dmesg | grep -i 'it87'
```

Rule of thumb by vendor (full matrix: [Hardware Compatibility](../docs/19_Hardware_Compatibility.md)):

| Board vendor | Typical chip | Driver you likely need |
|---|---|---|
| Gigabyte (2019+, most AORUS) | ITE IT8686E/IT8688E/IT8689E/IT8696E (+ IT87952E secondary) | `it87-dkms-git` (AUR) |
| MSI (B550 and newer) | Nuvoton NCT6687-R | `nct6687d-dkms-git` (AUR) |
| ASUS | Nuvoton NCT6798D/NCT6799D | usually **none** — mainline `nct6775` |
| ASRock | Nuvoton NCT6798D or NCT6686D | usually none; NCT6686D boards are board-specific — see the [ASRock notes](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) |

> **Don't guess.** Installing the wrong out-of-tree driver can actively harm: the `nct6687`/`nct6775` chip-ID collision has bricked a CPU fan header in the wild (see the CRITICAL banner the Diagnostics page raises if both are loaded). Only install a driver the readiness report or the compatibility matrix recommends for your identified chip.

## Step 2 — Prerequisites (DKMS + kernel headers)

Out-of-tree drivers are rebuilt against your kernel by **DKMS**, which needs the **headers for the exact kernel you boot**:

```bash
# See which kernel you are running
uname -r

# Arch mainline kernel
sudo pacman -S --needed dkms linux-headers

# Arch LTS kernel
sudo pacman -S --needed dkms linux-lts-headers

# CachyOS — match your installed kernel flavour
sudo pacman -S --needed dkms linux-cachyos-headers
# or: linux-cachyos-bore-headers / linux-cachyos-lts-headers / linux-cachyos-deckify-headers
```

To confirm the headers match: `pacman -Q | grep -- -headers` and compare against `uname -r`. A mismatch (e.g. headers for `linux` while booting `linux-cachyos`) is the single most common reason a DKMS build "succeeds" but the module never loads.

> **CachyOS-LTS / Tumbleweed path quirk:** the it87 DKMS config has a known module-install-path bug on some kernels — the module builds but lands in a directory the kernel does not search ([frankcrawford/it87 issue #94](https://github.com/frankcrawford/it87/issues/94)). If `modprobe` says *module not found* right after a clean DKMS build, run `dkms status` and check where the module was installed versus `/lib/modules/$(uname -r)/`.

## Step 3 — Install the driver

Install from the AUR with your helper of choice (examples use `yay`; building manually with `makepkg` works the same way — see the [Arch wiki AUR page](https://wiki.archlinux.org/title/Arch_User_Repository)):

```bash
# Gigabyte / ITE chips
yay -S it87-dkms-git

# MSI / Nuvoton NCT6687-R
yay -S nct6687d-dkms-git
```

Both are `-git` packages: every install/reinstall builds the **current upstream snapshot**. That matters — many historical workarounds are already fixed upstream (for the it87 driver: secondary-chip fan control, MMIO on by default since the 2026-03 builds, ACPI-conflict sidestepping). The version number shown on the AUR page is stale `-git` metadata; what installs is upstream HEAD at build time. If you installed the driver months ago and something misbehaves, **reinstalling the package is the first remediation, not the last**.

## Step 4 — Load and verify

```bash
# Load the module now (no reboot needed the first time)
sudo modprobe it87        # Gigabyte/ITE
# or
sudo modprobe nct6687     # MSI

# Did it bind? Your chip name should now appear:
cat /sys/class/hwmon/hwmon*/name

# And lm_sensors should show fan RPMs / temperatures:
sensors
```

(`sensors` comes from the `lm_sensors` package — `sudo pacman -S lm_sensors` if the command is missing.)

If `modprobe` fails with *Key was rejected by service* (or a lockdown "unsigned module" error), Secure Boot is blocking the unsigned module — see [Secure Boot and DKMS modules](#secure-boot-and-dkms-modules) below.

Boot-time loading is already handled for you: the `control-ofc-daemon` package ships `/etc/modules-load.d/control-ofc.conf`, which loads the common Super-I/O modules at boot.

Then verify end-to-end in the GUI:

1. **Diagnostics → Troubleshooting → Refresh Hardware Diagnostics** — the chips table should show your chip as *loaded* and the header count should match what the board physically has.
2. Run **Test PWM Control** on a *non-critical chassis fan* header (not CPU/pump). A **"PWM control is working correctly"** result is the finish line.
3. If the test reports the BIOS reverting control, go to Step 5.

## Secure Boot and DKMS modules

With UEFI **Secure Boot** enabled, the kernel only loads modules signed by a key it trusts. Out-of-tree DKMS modules are unsigned by default, so the install *builds* fine but the module is rejected at **load** time ([Ubuntu wiki: UEFI/SecureBoot/DKMS](https://wiki.ubuntu.com/UEFI/SecureBoot/DKMS)) — a classic dead end, because `dkms status` says *installed* while `modprobe` fails with one of:

```text
modprobe: ERROR: could not insert 'it87': Key was rejected by service
modprobe: ERROR: could not insert 'it87': Required key not available
Lockdown: modprobe: unsigned module loading is restricted
```

(The "Key was rejected by service" wording in the wild: [Arch forums thread](https://bbs.archlinux.org/viewtopic.php?id=283289) — an NVIDIA module in that case, but the kernel emits the same error for any unsigned module.)

Check whether Secure Boot is the cause:

```bash
bootctl status | grep -i "secure boot"   # systemd tool, present on every Arch/CachyOS install
# or
mokutil --sb-state                       # needs the mokutil package
```

Two ways out — read the trade-offs before picking:

1. **Disable Secure Boot in firmware setup** (the straightforward path, and the first remediation most distro documentation lists for unsigned modules): reboot into UEFI setup and disable Secure Boot, usually under *Boot* or *Security*. Understand what you are trading away first:
   - Secure Boot exists to block tampered boot components — disabling it **reduces boot-chain security**.
   - **Dual-booting Windows with BitLocker?** Toggling Secure Boot can make BitLocker demand its **recovery key** on the next Windows boot. [Have your recovery key ready](https://support.microsoft.com/en-us/windows/find-your-bitlocker-recovery-key-6b71ad27-0b89-ea08-f143-056f5ab347d6) *before* changing the setting.
2. **Sign the modules (advanced):** DKMS can automatically sign every module it builds — set `mok_signing_key` / `mok_certificate` in `/etc/dkms/framework.conf` ([dkms README](https://github.com/dkms-project/dkms)) and enroll the certificate with your firmware. Enrollment mechanics (sbctl-managed keys, or shim + `mokutil`) are distribution-specific — follow [Arch Wiki: Signed kernel modules](https://wiki.archlinux.org/title/Signed_kernel_modules) and, on CachyOS, the [CachyOS Secure Boot guide](https://wiki.cachyos.org/configuration/secure_boot_setup/).

> **CachyOS caveat (as of June 2026):** `linux-cachyos` kernels are built with IMA disabled, which prevents MOK certificates from being trusted for module signing — **MOK-signed DKMS modules fail to load even after correct enrollment** ([linux-cachyos #862](https://github.com/CachyOS/linux-cachyos/issues/862), open at the time of writing, with a proposed fix in PR #863). Until that lands, **disabling Secure Boot is the only reliable way** to run these drivers on CachyOS kernels.

## Step 5 — BIOS settings (the half people skip)

A correctly-installed driver still loses to BIOS firmware that keeps rewriting fan registers. One-time BIOS setup by vendor:

- **Gigabyte (Smart Fan 5/6):** set each header you want Linux to control to **Full Speed** mode, and make sure "FAN Control by" is **not** set to "Temperature". On IT8689E **Rev 1** boards (e.g. X670E AORUS MASTER), manual control only works after configuring a **flat 7-point BIOS curve** — PWM `40/40/40/40/40/40` with the final point at `100` ([issue #96](https://github.com/frankcrawford/it87/issues/96)).
- **MSI:** BIOS → Hardware Monitor → **disable "Smart Fan Mode"** for each header, or all headers report read-only.
- **ASUS / ASRock:** set Q-Fan / Smart Fan to manual or full speed for headers that appear read-only.

Fans will run at full speed after these BIOS changes **until the daemon/GUI takes over** — that is expected.

## Module parameters you may actually need

Most users on current driver builds need **none** of these. The exceptions, all persisted via a file in `/etc/modprobe.d/` (e.g. `it87.conf`):

| Situation | Parameter | Source |
|---|---|---|
| Dual-chip Gigabyte board, **old (pre-2026-03)** it87 build, secondary chip missing | `options it87 mmio=on` | [DEC-101 / issue #70](https://github.com/frankcrawford/it87/issues/70) — current builds default this on; update the driver instead |
| **IT8665E** board (X399 era, e.g. ROG Zenith Extreme) — PWM writes garbled on current builds | `options it87 mmio=off` | [issue #106](https://github.com/frankcrawford/it87/issues/106) |
| `modprobe it87` fails with *Device or resource busy* (ACPI conflict, e.g. B650 GAMING X AX V2) | `options it87 ignore_resource_conflict=1` | [issue #92](https://github.com/frankcrawford/it87/issues/92) — prefer this driver-local option over the system-wide `acpi_enforce_resources=lax` |
| MSI X870/B850/Z890 system fans ignore writes and the board is not on the driver's auto-allowlist | `options nct6687 msi_alt1=1` | [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) |

Two warnings: never use the it87 `force_id` parameter outside testing, and never run `sensors-detect` after boot on a dual-chip Gigabyte board (it can wedge the SuperIO bridge so the secondary chip vanishes until reboot).

## Rollback — undoing everything

Every change above is reversible:

```bash
# 1. Unload the module
sudo modprobe -r it87          # or: nct6687

# 2. Remove the package (DKMS uninstalls the module from all kernels)
sudo pacman -R it87-dkms-git   # or: nct6687d-dkms-git

# 3. Remove any module-parameter files you created
sudo rm -f /etc/modprobe.d/it87.conf /etc/modprobe.d/nct6687.conf

# 4. Reboot to return to the clean pre-driver state
sudo systemctl reboot
```

BIOS changes are rolled back in BIOS setup (restore Smart Fan / Q-Fan to its default profile). If you use snapshots (e.g. `snapper` / Timeshift on CachyOS), taking one before Step 3 gives you a one-command rollback as well.

## Staying current

- **Kernel updates:** DKMS rebuilds the module automatically when a new kernel + matching headers are installed. If fans disappear right after a kernel update, the usual cause is missing/mismatched headers — re-check Step 2.
- **Driver updates:** `-git` AUR packages only pick up upstream fixes when *reinstalled* (`yay -S it87-dkms-git`). Do this before troubleshooting any fan-control regression. (If a current build *fails to compile*, see upstream [issue #108](https://github.com/frankcrawford/it87/issues/108) for a known `-Werror=unused-function` toolchain failure.)

## AMD GPU fan control prerequisite (RDNA3+)

The rest of this page is about motherboard headers; AMD GPU fan control has exactly one prerequisite of its own. RDNA3 and newer cards (RX 7000 / RX 9000 series) only accept fan-curve writes through the PMFW interface, which the kernel locks behind an *overdrive* feature bit. Pre-RDNA3 cards (RX 6000 and older) need none of this.

Check first — the readiness report's **GPU diagnostics** row (Diagnostics → Troubleshooting) says whether the bit is set, or from a terminal:

```bash
cat /sys/module/amdgpu/parameters/ppfeaturemask
```

If bit 14 (`0x4000`, `PP_OVERDRIVE_MASK` — [kernel amdgpu module-parameters documentation](https://docs.kernel.org/gpu/amdgpu/module-parameters.html)) is not set, add this to the kernel command line:

```text
amdgpu.ppfeaturemask=0xffffffff
```

That value is what the daemon's diagnostics, this GUI, and most distro guides standardise on; any narrower mask also works as long as bit 14 is set — [CoolerControl documents OR-ing `0x4000` into your current mask](https://docs.coolercontrol.org/hardware-support.html) as the minimal alternative. Background: [Hardware Compatibility § ppfeaturemask](../docs/19_Hardware_Compatibility.md).

How to add a kernel parameter, per bootloader — the same steps as `man control-ofc-daemon`; see also the [Arch Wiki](https://wiki.archlinux.org/title/Kernel_parameters) and, for Limine, the [CachyOS boot-manager guide](https://wiki.cachyos.org/configuration/boot_manager_configuration/):

| Bootloader | Edit | Then run |
|---|---|---|
| **GRUB** | append to `GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub` | `sudo grub-mkconfig -o /boot/grub/grub.cfg` |
| **systemd-boot** | append to the `options` line of the active entry under `/boot/loader/entries/` | — |
| **rEFInd** | append to the options in `/boot/refind_linux.conf` (or the kernel argument list in `refind.conf`) | — |
| **Limine** (as set up on CachyOS) | append to `KERNEL_CMDLINE` in `/etc/default/limine` | `sudo limine-mkinitcpio` |

Reboot, confirm the parameter took effect with `cat /proc/cmdline`, then run **Test GPU Fan Control** (Diagnostics → Troubleshooting) to verify end-to-end. A wrong kernel command line can prevent the system from booting — the warning at the top of this page applies here in full.

## Where to go next

- [Setup Checklist](setup-checklist.md) — the ordered end-to-end setup path this page slots into
- [Hardware Troubleshooting](hardware-troubleshooting.md) — readiness report, dual-chip warning, Test PWM Control results
- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — full chip/driver matrix with sources
- [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) / [Intel Guide](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) — vendor-by-vendor depth

---

Previous: [Hardware Troubleshooting](hardware-troubleshooting.md) | Back to [Table of Contents](README.md)
