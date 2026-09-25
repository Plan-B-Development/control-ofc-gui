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

1. Start the GUI and open the **System State** page.
2. Click **Rescan Hardware**.
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
| Gigabyte (2019+, most AORUS) | ITE IT8686E/IT8688E/IT8689E/IT8696E (+ an IT8792E or IT87952E secondary on dual-chip boards) | `it87-dkms-git` (AUR) |
| MSI (B550/A520, Intel 600-series and newer) | Nuvoton NCT6687D | `nct6687d-dkms-git` (AUR) |
| MSI (AM4 300/400-series, the original 2019 X570 boards) | Nuvoton NCT6795D / NCT6797D | **none** — mainline `nct6775`. Do **not** install `nct6687d` here |
| ASUS (AM4 500, Intel 600/700) | Nuvoton NCT6798D | usually **none** — mainline `nct6775` |
| ASUS (AM5, Intel Z890/B860) | Nuvoton NCT6799D (AM5 600) or NCT6701D (AM5 800, Z890/B860), both reported as `nct6799` | usually **none** — mainline `nct6775` |
| ASUS (AM4 300/400-series, e.g. PRIME X470-PRO) | ITE IT8665E | `it87-dkms-git` (AUR) — mainline has no IT8665E driver |
| ASRock | Nuvoton NCT67xx, and on many boards an NCT6686D/NCT6683D carrying some or all fans | the NCT67xx needs none; the NCT668x fans are read-only in the kernel driver and need a board-specific out-of-tree driver — see the [ASRock notes](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) |

> **Don't guess.** Installing the wrong out-of-tree driver can actively harm: the `nct6687`/`nct6775` chip-ID collision has bricked a CPU fan header in the wild (see the CRITICAL banner the **System State** page raises if both are loaded). Only install a driver the readiness report or the compatibility matrix recommends for your identified chip.

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

# MSI / Nuvoton NCT6687D
yay -S nct6687d-dkms-git
```

Both are `-git` packages: every install/reinstall builds the **current upstream snapshot**. That matters — many historical workarounds are already fixed upstream (for the it87 driver: MMIO on by default since the 2026-03 builds, ACPI-conflict sidestepping). The version number shown on the AUR page is stale `-git` metadata; what installs is upstream HEAD at build time. If you installed the driver months ago and something misbehaves, **reinstalling the package is the first remediation, not the last** — but read the next section first if you have a Gigabyte board.

With the `nct6687d` driver, also blacklist the in-kernel `nct6683` (`echo 'blacklist nct6683' | sudo tee /etc/modprobe.d/nct6683_blacklist.conf`, then reboot). Both drivers can bind the same chip, which garbles readings and makes PWM writes fail. The in-kernel one also names its device `nct6687` and publishes it read-only.

### it87 v2.0 renames your chips

Builds of `it87-dkms-git` made on or after **2026-09-09** (the driver's v2.0 release, [PR #132](https://github.com/frankcrawford/it87/pull/132)) name Gigabyte chips after the board's own ID whenever the driver can read it — for example `it8696_a008090a` instead of `it8696`. The driver keeps working, but everything that is keyed on the chip name changes with it:

- **Every fan header gets a new id.** Profile members, fan names you gave, header role assignments — **including a pump role you assigned** — and cooling-device members all point at ids that no longer exist. **Until you re-check them, your profile no longer controls those fans:** they stay under the BIOS's control, and a control whose fans were all on those chips shows as *Not controlled* on the Controls page. After the first boot on the new build, re-assign pump roles first, then re-check fan names and each profile's members.
- The built-in header labels for boards in Control-OFC's table (for example the X870E AORUS MASTER's `SYS_FAN5_PUMP`) stop applying, so those headers show as `pwm1`, `pwm2`…
- The dual-chip warning on **System State** reports both chips missing although both are working.

Control-OFC does not match the new names yet. Until it does, you can stay on the old names by building commit **`c567739`** (2026-08-25). It has the same driver code as the last build before the rename (only a build-script change came between them), including the [PR #128](https://github.com/frankcrawford/it87/pull/128) fixes:

```bash
yay -G it87-dkms-git          # or: git clone https://aur.archlinux.org/it87-dkms-git.git
cd it87-dkms-git
# pin the source to the commit:
sed -i 's|it87.git"|it87.git#commit=c567739c639533177abd66894a6a8d561337285f"|' PKGBUILD
makepkg -si
```

A later `yay -Syu` will offer to "update" the package back to the current master. Skip it (or add `it87-dkms-git` to `IgnorePkg` in `/etc/pacman.conf`) until you are ready to re-check your ids.

> **Secondary-chip fan control on dual-Super-I/O Gigabyte boards is bound by board pairing, not fixed for the family.** Some boards work — the X870E AORUS ELITE X3D reports both chips and controls both. On others the secondary can be masked by an ITE eSPI→LPC bridge latched in configuration mode, measured on an X870E AORUS MASTER. **That is recoverable, but not the way you would guess**: reinstalling, `mmio=on` and `force_id` all change nothing. The latch is written by the `nct6775`/`w83627ehf` modules (or `sensors-detect`). Stop those, reboot, and if the chip is still missing, power down at the wall. Follow the ladder in [Hardware Troubleshooting → *Some of my fan headers are missing*](hardware-troubleshooting.md#some-of-my-fan-headers-are-missing--only-5-of-8-show-up).

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

1. **Restart the daemon** so it adopts the new chip's PWM headers: `sudo systemctl restart control-ofc-daemon`. (A **Rescan Hardware** click in the global footer page is enough when you only need the chip's *sensors* — fan-control headers are discovered at daemon startup only.)
2. Click **Rescan Hardware** in the global footer, then open **System State** — the chips table should show your chip as *loaded* and the header count should match what the board physically has.
3. Run **Test PWM Control** on a *non-critical chassis fan* header (not CPU/pump). A **"PWM control is working correctly"** result is the finish line.
4. If the test reports the BIOS reverting control, go to Step 5.

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

> **CachyOS caveat (re-checked 2026-08-23, still current):** `linux-cachyos` kernels are built with IMA disabled, which prevents MOK certificates from being trusted for module signing — **MOK-signed DKMS modules fail to load even after correct enrollment**. [linux-cachyos #862](https://github.com/CachyOS/linux-cachyos/issues/862) remains **open** (never closed since it was filed in May 2026) and its fix, [PR #863](https://github.com/CachyOS/linux-cachyos/pull/863), remains **open and unmerged** — though still active, last touched 2026-08-16. Note the fix *has* landed for the Fedora COPR packaging; it is the **Arch** PKGBUILD, which is what CachyOS ships here, that has not taken it. Until it does, **disabling Secure Boot is the only reliable way** to run these drivers on CachyOS kernels.

## Step 5 — BIOS settings (the half people skip)

A correctly-installed, current driver usually takes each header over from the BIOS on its own. What the BIOS keeps is the fans at boot and whenever the daemon is not controlling them — so there is one rule for everyone, and the rest is per vendor:

- **Every vendor:** never give a BIOS fan curve a **0% point**. The BIOS curve runs the fans at boot, and whenever the daemon is not controlling them, so a 0% point means stopped fans — pump and CPU fan included — in exactly those moments.
- **Gigabyte (Smart Fan 5/6):** a current `it87-dkms-git` build usually needs **no BIOS change at all**. For a 4-pin fan, set the header's **FAN Control Mode** to **PWM**. Leave **Fan Speed Control** on a normal curve. *Full Speed* is a fail-safe, not a fix: the firmware runs that fan at 100% whenever it owns it, but on some boards it also locks Linux out of the header ([issue #115](https://github.com/frankcrawford/it87/issues/115)). On IT8689E **Rev 1** boards (e.g. X670E AORUS MASTER), **update `it87-dkms-git` first**. The fix in [PR #128](https://github.com/frankcrawford/it87/pull/128) merged on 2026-08-24, and three users reported working IT8689E control the day before, including on **Rev 1** (a Z790 AORUS MASTER, with fan speed measurably tracking duty). More boards have reported working since, some with **no BIOS changes at all**. So update, then **verify with Test PWM Control** rather than assuming. Only on a build older than 2026-08-24 did the driver need the fork's old BIOS stopgap: PWM 40,40,40,40,40,40,100 at temperatures 0,90,90,90,90,90,90, lowering 90 to your BIOS maximum ([issue #96](https://github.com/frankcrawford/it87/issues/96)). Even that only reliably restored the CPU-fan header. ([PR #114](https://github.com/frankcrawford/it87/pull/114), an earlier candidate fix, was rejected on 2026-08-25.)
- **MSI:** no BIOS setting makes the headers writable. If every header reads as read-only, the in-kernel `nct6683` is bound instead of `nct6687d` — blacklist it (see Step 3). On B840/B850/B860/X870/X870E/Z890 boards, system fans that ignore writes need the driver options in the table below, not a BIOS change. For 3-pin fans, set the header's fan type to DC.
- **ASUS:** no BIOS setting unlocks the headers either — under `nct6775` they are always writable, and the daemon switches each one to manual itself. In *Q-Fan Control*, match each header's mode to the fan (**DC Mode** for 3-pin, **PWM Mode** for 4-pin). *Q-Fan Tuning* is a one-shot calibration, not a mode switch. On some 800-series boards the firmware has been measured taking a header straight back from manual mode; run **Test PWM Control** to see whether yours does.
- **ASRock:** headers on the NCT67xx chip are always writable under `nct6775`. Headers on an NCT6686D/NCT6683D are read-only in the kernel driver whatever the BIOS says, and need an out-of-tree driver. Set each header's fan type to match the fan (DC for 3-pin, PWM for 4-pin).

## Module parameters you may actually need

Most users on current driver builds need **none** of these. The exceptions, all persisted via a file in `/etc/modprobe.d/` (e.g. `it87.conf`):

| Situation | Parameter | Source |
|---|---|---|
| Dual-chip Gigabyte board, **old (pre-2026-03)** it87 build, secondary chip missing | `options it87 mmio=on` | [issue #70](https://github.com/frankcrawford/it87/issues/70) — current builds default this on; update the driver instead. **On a current build, a missing secondary is not a parameter problem** — it is a blocked Super-I/O that needs `nct6775`/`w83627ehf` kept away, a reboot and, if that is not enough, a full power cut — see [Hardware Troubleshooting](hardware-troubleshooting.md#some-of-my-fan-headers-are-missing--only-5-of-8-show-up) |
| **IT8665E** board (ASUS AM4 300/400-series such as the PRIME X470-PRO, and X399-era boards like the ROG Zenith Extreme) — PWM writes garbled on **builds predating 2026-07-22 only** | `options it87 mmio=off` | [issue #106](https://github.com/frankcrawford/it87/issues/106) — **closed, fixed upstream by [PR #120](https://github.com/frankcrawford/it87/pull/120) (merged 2026-07-22), which removed MMIO for this chip in the driver.** On a current build no parameter is needed; update the driver instead |
| `modprobe it87` fails with *Device or resource busy* (ACPI conflict, e.g. B650 GAMING X AX V2) | `options it87 ignore_resource_conflict=1` | [issue #92](https://github.com/frankcrawford/it87/issues/92) — prefer this driver-local option over the system-wide `acpi_enforce_resources=lax` |
| MSI **B840/B850/B860/X870/X870E/Z890** board (the alternate "msi_alt1" register map — monitoring tools call these boards *NCT6687DR*) whose system fans ignore writes and which is **not** on the driver's auto-allowlist | `options nct6687 fan_config=msi_alt1` | [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) — see the warning below |
| `modprobe nct6687` fails with *Device or resource busy* (ACPI conflict) | `acpi_enforce_resources=lax` **as a kernel parameter** | `nct6687` and `nct6775` expose **no** driver-local `ignore_resource_conflict` equivalent, so unlike it87 the system-wide parameter is the only kernel-side remedy — use it deliberately |
| Boot log shows *"nct6687: EC base I/O port unconfigured"* or `modprobe` fails with *No such device* | `softdep nct6687 pre: i2c_i801` in `/etc/modprobe.d/nct6687.conf` | upstream README — the EC is not addressable until the SMBus driver has loaded |
| MSI B840/B850/B860/X870/X870E/Z890 system fans accept PWM writes but do not change speed, or writes fail with an I/O error (EIO) | `options nct6687 msi_fan_brute_force=1` **plus** `blacklist nct6683` | upstream marks this BETA and requires the blacklist; without it `nct6683` can bind the same chip and writes fail. It writes the duty into all 7 BIOS curve points (the original curve is saved and restored), for system fans only |

> **Do not force `fan_config=msi_alt1` on an MSI board outside the B840/B850/B860/X870/X870E/Z890 series.** B650, B660, X670, Z690 and Z790 boards use the *default* register mapping and are auto-detected correctly — the chip is the same NCT6687D; only the EC's register layout differs. Forcing alt1 there makes the driver read EC offsets that read zero on those boards, so **every system fan reports 0 RPM** while the CPU fan keeps working. Check which mapping is active with `sudo dmesg | grep 'active fan config'` — that line will also reveal a setting left behind from an earlier attempt.
>
> **Never load `nct6687` with `force=1` on a board whose chip is an NCT679x** (MSI AM4 300/400-series and the original X570 boards). Since [nct6687d PR #174](https://github.com/Fred78290/nct6687d/pull/174), `force=1` attaches to any Nuvoton chip ID from 0xD000 to 0xDFFF. That reopens the chip-ID collision that has bricked a CPU fan header.

Two warnings: never use the it87 `force_id` parameter outside testing (upstream: *"should only be used for testing"*), and never run `sensors-detect` after boot on a dual-chip Gigabyte board — it can wedge the Super-I/O bridge so the secondary chip vanishes, and a reboot may not bring it back — power down at the wall, because the bridge keeps standby power. The recovery is in [Hardware Troubleshooting](hardware-troubleshooting.md#some-of-my-fan-headers-are-missing--only-5-of-8-show-up).

There is also nothing to gain by running it on these boards. As of 2026-08-26 `sensors-detect` has **no entry for device IDs 0x8688, 0x8689, 0x8696 or 0x8698**, so it cannot identify an IT8688E, IT8689E, IT8696E or IT8698E — the primary chip on essentially every modern Gigabyte board — and it has no NCT6686D entry either. On exactly the boards where running it can do harm, it has nothing useful to tell you.

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

BIOS changes are rolled back in BIOS setup (restore Smart Fan / Q-Fan to its default profile). If you pinned `it87-dkms-git` to a commit, remove it from `IgnorePkg` too. If you use snapshots (e.g. `snapper` / Timeshift on CachyOS), taking one before Step 3 gives you a one-command rollback as well.

## Staying current

- **Kernel updates:** DKMS rebuilds the module automatically when a new kernel + matching headers are installed. If fans disappear right after a kernel update, the usual cause is missing/mismatched headers — re-check Step 2.
- **Driver updates:** `-git` AUR packages only pick up upstream fixes when *reinstalled* (`yay -S it87-dkms-git`). Do this before troubleshooting any fan-control regression. (If a current build *fails to compile*, see upstream [issue #108](https://github.com/frankcrawford/it87/issues/108) for a known `-Werror=unused-function` toolchain failure.)

## AMD GPU fan control prerequisite (RDNA3+)

The rest of this page is about motherboard headers; AMD GPU fan control has exactly one prerequisite of its own. RDNA3 and newer cards (RX 7000 / RX 9000 series) only accept fan-curve writes through the PMFW interface, which the kernel locks behind an *overdrive* feature bit. Pre-RDNA3 cards (RX 6000 and older) need none of this.

Check first — the readiness report's **GPU diagnostics** row (on the **System State** page) says whether the bit is set, or from a terminal:

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

Reboot, confirm the parameter took effect with `cat /proc/cmdline`, then run **Test GPU Fan Control** (on the **System State** page) to verify end-to-end. A wrong kernel command line can prevent the system from booting — the warning at the top of this page applies here in full.

## Where to go next

- [Setup Checklist](setup-checklist.md) — the ordered end-to-end setup path this page slots into
- [Hardware Troubleshooting](hardware-troubleshooting.md) — readiness report, dual-chip warning, Test PWM Control results
- [Hardware Compatibility](../docs/19_Hardware_Compatibility.md) — full chip/driver matrix with sources
- [AMD Motherboard Fan Control Guide](../docs/21_AMD_Motherboard_Fan_Control_Guide.md) / [Intel Guide](../docs/23_Intel_Motherboard_Fan_Control_Guide.md) — vendor-by-vendor depth

---

Previous: [Hardware Troubleshooting](hardware-troubleshooting.md) | Next: [Understanding Motherboard Fan Control](understanding-fan-control.md) | Back to [Table of Contents](README.md)
