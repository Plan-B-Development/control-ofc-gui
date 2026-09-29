# OpenFan Controller Integration — Technical Deep-Dive

> **Part snapshot, part current — and the split is exact.** Everything here is
> the v0.2.0 snapshot **except § 2 (Discovery and Connection), § 10 (Failure
> Modes) and § 1's closing detection sentence**, which were re-verified against
> daemon v2.48.0, and **§ 3's reply correlation, § 4, § 5's channel field, § 7's
> command guards, § 8, § 9 and § 11**, re-verified against daemon 2.56.3's
> `serial/protocol.rs`, `serial/transport.rs` and `constants.rs` (DEC-440). The
> rest of the snapshot remains accurate because it is firmware-side: wire format,
> baud rate and calibration sweep mechanics have not changed. The re-verified portion had to be rewritten because
> DEC-291 and DEC-361 rebuilt detection and boot adoption, and **the 5-retry
> 1–16 s ladder it used to describe no longer exists**. Other daemon-side details
> (error codes, endpoint paths, lock granularity) have evolved through many
> releases and are **not** tracked here; see `daemon.md` § Module Map, § Startup
> Sequence and the daemon `CHANGELOG.md`.

**For:** OpenFan Controller firmware developers and hardware integrators
**Snapshot taken at:** Daemon v0.2.0 (protocol) · daemon v2.48.0 (§ 1 tail, § 2, § 10) · daemon 2.56.3 (§ 3 correlation, § 4, § 5 channel, § 7 guards, § 8, § 9, § 11)
**Evidence level:** Protocol claims verified against the v0.2.0 Rust source; the
re-verified sections against `main.rs`, `serial/adoption.rs`,
`serial/real_transport.rs`, `api/handlers/openfan.rs` and `polling.rs` at
daemon v2.48.0

---

## 1. Physical Connection

- **Interface:** USB CDC-ACM (appears as `/dev/ttyACMn` on Linux)
- **Baud rate:** 115,200 bps (`SERIAL_BAUD_RATE`, a constant — not configurable)
- **Data bits:** 8
- **Parity:** None
- **Stop bits:** 1
- **Flow control:** None
- **Library:** `serialport` crate v4.9.0 (wraps POSIX termios)

The daemon uses a stable device path via `/dev/serial/by-id/` (recommended) or auto-detects from the enumerated `/dev/ttyACM*` and `/dev/ttyUSB*` candidates.

---

## 2. Discovery and Connection

> **Re-verified against daemon v2.48.0.** This section, unlike the protocol
> sections below, is *not* the v0.2.0 snapshot — discovery and adoption were
> rebuilt by DEC-291 (enumerate without opening) and DEC-361 (boot makes one
> attempt; the rest of the search is detached).

### Startup Detection — exactly ONE attempt
1. **Enumerate**, do not probe: `serial_port_candidates_enumerated()` lists
   `/dev/ttyACM*` / `/dev/ttyUSB*` via libudev, falling back to a `Path::exists`
   scan of `/dev/ttyACM0`–`9` and `/dev/ttyUSB0`–`9`. A configured
   `serial.port` goes first but is never the *only* candidate (DEC-250).
   **Nothing is opened here** — the split exists because `open(2)` on a tty
   asserts DTR, which resets Arduino-class boards (DEC-291).
2. **Identify**: `first_openfan_port()` opens each candidate in turn — **at most
   once per candidate** — and accepts only one that answers the `ReadAllRpm`
   handshake (DEC-250). Openability is not identity: a modem or 3D printer on
   that tty would otherwise accept every write with `Ok`.
3. Port timeout set at open time (`serial.timeout_ms`, default 500ms per read)
4. **Then boot moves on.** This used to be a ladder of up to six attempts
   sleeping 1+2+4+8+16 s that ran *ahead* of the API server, both poll loops and
   the profile engine — so the daemon answered nothing and evaluated no thermal
   safety for ~31 s. DEC-361 removed it.

### If Nothing Was Adopted — the detached search
The profile engine and the IPC server start regardless; the daemon is fully
serving throughout. `post_boot_adoption_loop` is then spawned — **only** if boot
adopted nothing — and drives `POST /fans/openfan/rescan`'s own handler rather
than probing directly, so it cannot skip the identity handshake or the poll-loop
spawn.

| | Value |
|---|---|
| Window | **60s** auto-detect · **180s** when `serial.port` is configured |
| Tick | 5s (`POST_BOOT_ADOPTION_INTERVAL`) |
| Probes when | the enumerated candidate set **differs** from what boot last tried |
| Handshake retries | 3, for a device that enumerates before its firmware answers — spent only on a probe that actually ran (`OFN-w`) |
| Stops on | first adoption, window expiry, or shutdown |

Both windows are far longer than the ~31 s ladder they replace, because waiting
now costs nothing. A machine whose serial devices never change is never
re-probed, so an unrelated Arduino on the bus is not reset once per tick.

Do **not** attribute that skipping to the rescan cooldown: its predicate is
`elapsed < COOLDOWN && same_port_set(..)`, an **AND**, so it *spaces* repeat
probes to one per ten seconds and never skips one. The loop owns its own
candidate-set comparison for exactly that reason (DEC-361).

### Runtime auto-reconnect (R43)
After **5 consecutive read errors** the OpenFan poll loop enters reconnect mode,
recovering a disconnected device without a daemon restart. Its backoff doubles
to one attempt per 30 poll cycles and never gives up — `POST /fans/openfan/rescan`
refuses while a controller is installed, so this is the only route back for one
that dropped off.

**What an attempt opens (DEC-436, `DC-ae`; daemon 2.56.4).** Until DEC-436 this path re-ran
`auto_detect_port()`, which opened every `ttyACM`/`ttyUSB` node to find one — the
last caller of the opening detection DEC-291 moved boot and rescan off. The
reasoning for leaving it ("a known device to re-find rather than a bus to
survey") did not hold: it surveyed the whole bus on every attempt, for as long as
the controller stayed away, resetting every Arduino-class board on it each time.
`auto_detect_port` is gone. Each attempt now opens, in order and each node once,
only what `ReconnectSurvey` (`serial/adoption.rs`) plans:

1. the configured `[serial] port`, when it resolves;
2. the node the controller was adopted on, while it is still that node — same
   `(st_dev, st_ino)`. devtmpfs re-creates a node for a new device, so a name
   another device has taken is not mistaken for it; once seen gone or re-created
   it is never probed again for that drop. **This re-probe cannot rescue a
   controller wedged on its node today:** serialport opens with an exclusive
   `flock`, which root does not bypass, and the loop holds the old port until a
   replacement is swapped in. It resets nothing and is kept; closing the old port
   first is register row `DC-ct`. Such a controller needs a daemon restart;
3. each node that appeared after the survey began: on every attempt for its first
   60 s (at least 4 opens), then once per 5 minutes while it stays — never given
   up on, because this probe is the only way back (the rescan endpoint refuses
   while a controller is installed). A returning controller always arrives as a
   new node, even when two devices swap names.

The survey is built at the adoption from the candidate list it was made from
(boot's or the rescan's) and re-seeded on each reconnect, so a tty present all
along is never opened. A device plugged in after adoption is a new node and is
opened on that schedule while the controller stays away — at most once per
5 minutes after its first minute, where the retired sweep reset every tty about
every 30 s. A re-opened transport is
re-verified for identity (DEC-250/255) and the write-coalescing cache is
invalidated before it goes live (DEC-256). A single channel's cache entry is also
dropped whenever one of its commands fails, since the frame may already have reached
the device (DEC-383).

### Assumptions About Device
- Device responds within 500ms per line
- Device speaks the Karanovic OpenFan line-based protocol
- Device may emit 0–3 debug lines at startup before responding to commands
- Device has 10 controllable fan channels (0–9)

---

## 3. Wire Protocol

### Framing
- **Line-based:** Each command/response is one line
- **Command terminator:** `\n` (LF only)
- **Response terminator:** `\r\n` or `\n` (daemon accepts both)
- **Encoding:** ASCII text with hex-encoded numeric values

### Command Format

```
>{CC}{params}\n
```

- `>` — start marker (required)
- `CC` — 2-digit hex command code
- `params` — command-specific hex parameters
- `\n` — line feed terminator

### Response Format

```
<{CC}|{data}\r\n
```

- `<` — start marker (required — any line not starting with `<` is debug output)
- `CC` — 2-digit hex command code (echoes the command that was executed)
- `|` — separator (required)
- `data` — RPM pairs format: `NN:HHHH;NN:HHHH;...;`
- Optional closing `>` — real Karanovic firmware omits this; daemon accepts both

### Debug Output
Any line not starting with `<` is treated as debug output and skipped. The daemon skips up to **50 debug lines** before timing out with an error.

### Reply correlation (DEC-301, daemon 2.24.1)
A reply is accepted only if it answers **the command just sent** (`Command::matches_reply`):
- its command code must equal the command's opcode, and
- for `ReadRpm` and `SetPwm`, it must also carry a reading **for the channel the command
  addressed**. `ReadAllRpm` needs the opcode only.

A well-formed frame that fails either test is treated as a stale reply left by an earlier
exchange and discarded; the daemon drains up to **15** of them (`MAX_STALE_FRAMES` = 16,
checked before each read) before failing the exchange with a protocol error. So a firmware
whose `SetPwm` reply omitted the channel, or echoed a different one, would have **every**
write fail — see § 9. Before DEC-301 the daemon took whatever frame arrived next, which
put the link one frame behind and cached `SetPwm` acknowledgements as tachometer readings.

---

## 4. Command Reference

The daemon sends **three** commands, and only these (`serial/protocol.rs::Command`):
`ReadAllRpm`, `ReadRpm` and `SetPwm`. The firmware also implements `0x03` SetAllPwm
(one duty on every channel) and `0x04` SetTargetRpm (closed-loop RPM through its EMC2305);
the daemon has sent neither since `5d8847c` (first released in daemon 2.5.1), and a
thermal emergency writes each channel with its own `SetPwm`. They are listed at the end of
this section for firmware developers, not as daemon behaviour.

### 0x00: ReadAllRpm

Read RPM from all 10 channels.

```
Command:  >00\n
Response: <00|00:HHHH;01:HHHH;02:HHHH;...;09:HHHH;\r\n
```

### 0x01: ReadRpm(channel)

Read RPM from a single channel.

```
Command:  >01{ch:02X}\n
Example:  >0105\n     (channel 5)
Response: <01|05:04B0;\r\n
```

### 0x02: SetPwm(channel, pwm_raw)

Set open-loop PWM on one channel. Firmware echoes RPM reading.

```
Command:  >02{ch:02X}{pwm:02X}\n
Example:  >020580\n   (channel 5, PWM=128/255 ≈ 50%)
Response: <02|05:04B0;\r\n
```

**PWM conversion:**
```
percent_to_raw(pct) = (pct * 255 + 50) / 100
0%   → 0x00
50%  → 0x80
100% → 0xFF
```

### Firmware commands the daemon does not send

| Opcode | Command | Wire | Note |
|---|---|---|---|
| `0x03` | SetAllPwm(pwm_raw) | `>03{pwm:02X}\n` | one open-loop duty on all 10 channels |
| `0x04` | SetTargetRpm(channel, rpm) | `>04{ch:02X}{rpm:04X}\n` | closed-loop RPM target through the EMC2305 |

The daemon's API has no route that reaches either: the `target_rpm` route was retired at
2.0.0 and `SetAllPwm` was removed from the daemon with `5d8847c`.

---

## 5. RPM Pairs Format

Each response contains one or more channel:rpm pairs:

```
NN:HHHH;NN:HHHH;...;
```

- `NN` — channel number, two digits (00–09). The firmware emits it with `%02X` and the
  daemon parses it as decimal; the two agree because every channel is a single digit
- `:` — separator
- `HHHH` — RPM value as **4-digit uppercase hex** (u16)
- `;` — pair delimiter (trailing `;` expected)
- Empty segments (`;;`) are safely skipped

**Examples:**
| Hex | Decimal RPM |
|-----|------------|
| `04B0` | 1200 |
| `044C` | 1100 |
| `0BB8` | 3000 |
| `0000` | 0 (fan stopped) |
| `FFFF` | 65535 |

---

## 6. Communication Cadence

### Polling (steady state)
- **ReadAllRpm** sent every **1 second** (configurable via `polling.poll_interval_ms`)
- One command per poll cycle
- Response expected within 500ms (serial timeout)

### GUI-Driven Writes (retired at 2.0.0)
The GUI no longer issues SetPwm — the daemon's profile engine is the sole writer (DEC-165). See *Profile Engine Writes* below for the current write path. (Pre-2.0 the GUI sent SetPwm on ~1 s control-loop cycles with 1% write suppression and per-channel coalescing.)

### Profile Engine Writes
- Profile engine evaluates curves at **1 Hz**
- Writes OpenFan PWM for each fan member that needs updating
- Same coalescing applies (via FanController)

### Calibration
- PWM sweep: 2–20 steps, 2–15 seconds hold per step
- Exclusive: `AtomicBool` guard prevents concurrent sweeps
- Pre-calibration PWM recorded and restored afterward

---

## 7. Safety Logic

### Stop Timeout (per-channel) — defence in depth, not a cap on a stop
- **A held 0% is not time-limited.** `set_pwm()` coalesces a command equal to the channel's
  last commanded duty **before** the timeout is checked (CONC-2), so a curve or identify stop
  holding 0% writes nothing and meets no timeout. The fan stays stopped for as long as it is
  commanded; nothing restarts it. (This section said 0% "cannot be held for more than 8
  seconds" until DEC-426, `DC-b`.)
- **Tracking:** Per-channel `stop_started_at: Option<Instant>`, set by a 0% write that lands
  and cleared by a non-zero write, a failed reply (DEC-383) and a reconnect
- **What it refuses:** `apply_safety()` rejects, with `FanControlError::Validation`, a 0% that
  would reach the wire while `stop_started_at` is at least `STOP_TIMEOUT` (8 s) old. No normal
  command sequence produces that state; it guards against tracking drift
- **Advertised as** `limits.openfan_stop_timeout_s`. A client need not size anything from it

### Thermal Emergency (global)
- **Trigger:** CPU Tctl ≥ the trip point — at least 105°C, raised per-machine to
  `min(CPU-reported design ceiling + 5°C, 115°C)` where the kernel publishes the ceiling
  (DEC-308). The 115°C cap is unconditional; read `emergency_threshold_c` from
  `GET /diagnostics/hardware` rather than deriving it
- **Action:** Force every OpenFan channel the machine has to 100% PWM — and every
  writable hwmon header it has too, because the rule is not OpenFan-specific. GPU fans
  are excluded by design (DEC-130)
- **Hold:** Until a fresh Tctl reading ≤ 80°C — held at 100% while the sensor is stale or gone (DEC-386)
- **Release:** Straight back to profile control (no recovery rung since DEC-386). Every other fan the emergency took is given back at the release — an OpenFan channel to its pre-emergency duty raised to the exit floor (DEC-451), an hwmon header to the mode it had before the daemon took it (DEC-382)
- **Implementation:** `ThermalSafetyRule.evaluate()` called every 1s in profile engine

### Command Safety Guards
- **MAX_DEBUG_LINES = 50:** Aborts if firmware emits 50+ non-response lines
- **Wall-clock deadline:** Total operation bounded by the timeout parameter
- **MAX_STALE_FRAMES = 16:** At most 15 replies to another command or channel are drained per exchange (§ 3)
- **Channel validation:** 0–9 only (10 channels)
- **PWM range:** 0–100% (mapped to 0–255 raw)

### Unknown duty after a reconnect or resume
A serial reconnect or a host resume makes every channel's last duty **unknown** until the
daemon next writes that channel (DEC-256; the accessor honours it since DEC-393, daemon
2.51.1). Two safety paths read it:
- **The no-sensor floor.** A control skipped under the 40% floor normally keeps its fans at
  their last duty; a channel whose duty a reconnect or resume lost goes to **100%** instead
  (DEC-401, daemon 2.51.3, `lost_to_reconnect`). A duty unknown for any other reason takes
  the bare 40%.
- **The emergency's give-back.** A channel whose duty was unknown when the emergency began
  has nothing to be given back to, so a channel no control commands stays at 100% when the
  emergency ends (DEC-393).

---

## 8. What the Daemon Assumes About Firmware

1. **Line-based protocol** — one command per line, one response per line
2. **Response starts with `<`** — anything else is debug output
3. **Response echoes the command code, and for `ReadRpm`/`SetPwm` the channel** — both are
   checked before a reply is accepted (§ 3, DEC-301)
4. **Response includes RPM readings** — even after SetPwm commands
5. **Real firmware omits closing `>`** — daemon accepts both formats
6. **Channel numbers in response are decimal** (not hex) — `05:04B0` means channel 5
7. **Firmware handles PWM 0–255 internally** — daemon converts percent→raw
8. **Device is exclusively owned** — no other process should access the serial port

---

## 9. Compatibility Notes for Firmware Developers

### Safe to Change
- Add new debug output lines at startup (daemon skips up to 50)
- Change response line ending from `\r\n` to `\n`
- Add closing `>` to responses (already accepted)

### Risky Changes
- Changing `|` separator to another character → **breaks parsing**
- Changing hex RPM to decimal → **breaks parsing**
- Changing command start marker from `>` → **breaks encoding**
- Changing response start marker from `<` → **debug output infinite loop**
- Adding binary frames → **breaks line-based reading**
- Changing channel numbering from 0-based → **breaks channel mapping**
- Removing RPM echo from SetPwm response → **daemon treats as protocol error (no readings)**
- Omitting or changing the **channel** in a `ReadRpm`/`SetPwm` reply → **every such command
  fails**: the reply no longer matches the command (§ 3), is drained as stale, and the
  exchange ends in a protocol error or a timeout
- Changing a reply's **command code** → the same: the reply is never matched

### Transport Parameters
| Parameter | Value | Changeable? |
|-----------|-------|-------------|
| Baud rate | 115,200 | Hardcoded constant (`SERIAL_BAUD_RATE`) |
| Line terminator (command) | `\n` | Hardcoded |
| Line terminator (response) | `\r\n` or `\n` | Both accepted |
| Max response time | 500ms | Configurable |
| Max debug lines | 50 | Hardcoded constant |
| Channel count | 10 (0–9) | Hardcoded constant |

---

## 10. Failure Modes

| Failure | Detection | Recovery | Impact |
|---------|-----------|----------|--------|
| Device not found at startup | No enumerated candidate answers the `ReadAllRpm` handshake | One boot attempt, then a detached 60s / 180s search that re-probes only when the candidate set changes | Daemon runs without OpenFan, fully serving throughout; adopts without a restart if the device appears |
| Device disconnects during operation | Next read/write returns I/O error | **Auto-reconnect (R43)** — after 5 consecutive errors the daemon re-detects and reconnects with backoff; no restart needed | Fan control pauses until reconnect |
| Firmware enters debug loop | 50 debug lines exceeded | Command fails with Protocol error | Affected write skipped |
| Response timeout | 500ms per read_line | SerialError::Timeout returned | Write skipped for this cycle |
| Malformed response | Protocol parsing fails | SerialError::Protocol returned | Write skipped |
| Reply for another command or channel (link out of step) | Reply fails correlation (§ 3) | Drained as stale — up to 15 per exchange — until the reply that matches; the link is usually back in step after one exchange | None if the matching reply arrives; otherwise a protocol error or timeout and that command is skipped |
| Firmware whose reply omits the addressed channel | No `ReadRpm`/`SetPwm` reply ever matches | None — a firmware incompatibility | **Every** per-channel write fails; `ReadAllRpm` polling still works |
| Wire-bound 0% against a stop timer ≥ 8 s old (no normal sequence reaches this; a held 0% coalesces) | Stop timeout check | That command rejected | None; the channel keeps whatever it last took |

---

## 11. Golden Test Vectors

### Command Encoding

| Command | Input | Wire |
|---------|-------|------|
| ReadAllRpm | — | `>00\n` |
| ReadRpm(5) | ch=5 | `>0105\n` |
| SetPwm(5, 128) | ch=5, raw=0x80 | `>020580\n` |
| SetPwm(0, 255) | ch=0, raw=0xFF | `>0200FF\n` |

### Response Decoding (Real Firmware)

```
Input: <00|00:0546;01:0541;02:054A;03:051C;04:04F1;05:055E;06:0548;07:0521;08:0557;09:04DF;\r\n

Parsed:
  command_code = 0x00
  channel 0: RPM = 0x0546 = 1350
  channel 1: RPM = 0x0541 = 1345
  channel 2: RPM = 0x054A = 1354
  ...
  channel 9: RPM = 0x04DF = 1247
```
