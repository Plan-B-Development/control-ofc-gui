# 25 — GPU Support Rules

Detailed AMD GPU fan-control rules for this project. Extracted from `CLAUDE.md` so
that the ~5 KB of hardware detail is not loaded into every session, most of which
never touches GPU code.

**`CLAUDE.md § GPU support rules` keeps the four safety-critical rules inline and
points here.** Read this file before changing anything under GPU discovery, GPU fan
writes, PMFW handling, or GPU display naming.

## Discovery and identity

- AMD dGPU sensors report `source: "amd_gpu"` (not `"hwmon"`) — used for display grouping.
- GPU identity uses the PCI BDF address (stable across reboots), not the hwmon index.
- Exactly one fan entity per GPU — the kernel exposes only `fan1_input` (an aggregate
  RPM for all physical fans).
- Navi 48 (RX 9070 XT / 9070) has PCI device ID `0x7550`, distinguished by revision
  (`0xC0` = XT, `0xC3` = non-XT).
- Daemon hwmon discovery excludes `chip_name == "amdgpu"` (DEC-102) — see the
  safety rules in `CLAUDE.md`, which is where that one is stated authoritatively.

## Write path

- **RDNA3+ GPUs (RX 7000/9000 series) do NOT support `pwm1_enable=1` manual mode** —
  fan control MUST use the PMFW `fan_curve` sysfs interface. *(Safety-critical; also
  stated in `CLAUDE.md`.)*
- Pre-RDNA3 GPUs (RX 6000 and older) use traditional `pwm1_enable=1` + `pwm1` control.
- GPU fan writes use an imperative model (`set_static_speed` via a flat PMFW curve);
  no lease is required.
- The `amdgpu.ppfeaturemask` kernel parameter is required for PMFW `fan_curve` access.
- ppfeaturemask bit 14 (`0x4000`) is required for PMFW — diagnostics must explain this
  when it is missing.
- GPU PMFW fan writes use a **5% threshold** (not 1%) to avoid SMU firmware churn
  during gaming (DEC-070).
- The daemon auto-disables `fan_zero_rpm_enable` before writing a PMFW curve, and
  re-enables it on reset (DEC-053).
- `fan_zero_rpm_enable` sysfs returns multi-line formatted output — parse header+value,
  do not just `trim()`.
- **The daemon must reset GPU fan curves to automatic on shutdown.** *(Safety-critical;
  also stated in `CLAUDE.md`.)*

## Truthfulness

- **Do not claim GPU fan write support unless PMFW `fan_curve` or hwmon `pwm1` is
  actually available.** The control method must be truthful: `"read_only"` when no
  write path exists (no `pwm1_enable` AND no PMFW). *(Safety-critical; also stated in
  `CLAUDE.md`.)*
- Read-only GPU fans show a `(read-only)` suffix in fan-role member selection.

## Display and UX

- GPU display label: the specific model if the PCI device ID is recognised
  (e.g. "9070XT"), otherwise "AMD D-GPU".
- GPU fan display name: `"{model} Fan"` from capabilities, falling back to
  `"D-GPU Fan"` (DEC-050).
- GPU fans are **always** displayable on the dashboard — zero-RPM idle is normal, not
  a disconnected header (DEC-047).
- GPU fans participate in fan roles, curves, profiles, the dashboard fan table, and
  diagnostics.
- The GUI shows a one-time zero-RPM info popup when a GPU fan is added to a role
  (setting: `show_gpu_zero_rpm_warning`).
- The fan wizard's stop/restore must handle all source types: `openfan`, `amd_gpu`,
  `hwmon`.

## Dependencies and platform

- Do **not** adopt the `amdgpu-sysfs` crate — LGPL-3.0 licence friction, and our
  focused code suffices (DEC-043).
- The daemon socket must be `chmod 0666` after bind to allow non-root GUI connections
  (DEC-049).

## Related

- GPU-only controls and the 0-RPM idle feature: DEC-221 (`fan_zero_rpm` is the lever;
  a 0% floor is not the same thing as 0 RPM).
- GPU floor behaviour: DEC-119.
- Role-aware floors and `member_label` (the CPU/pump 30% rule): `CLAUDE.md`, and
  `docs/05_Controls_Profiles_and_Curves_Spec.md`.
