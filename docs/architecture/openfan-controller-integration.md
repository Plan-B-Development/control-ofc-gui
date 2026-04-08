# OpenFan Controller Integration — Technical Deep-Dive

**For:** OpenFan Controller firmware developers and hardware integrators
**Version:** Daemon v0.2.0
**Evidence level:** All claims verified against actual Rust source code

---

## 1. Physical Connection

- **Interface:** USB CDC-ACM (appears as `/dev/ttyACMn` on Linux)
- **Baud rate:** 115,200 bps
- **Data bits:** 8
- **Parity:** None
- **Stop bits:** 1
- **Flow control:** None
- **Library:** `serialport` crate v4.9.0 (wraps POSIX termios)

The daemon uses a stable device path via `/dev/serial/by-id/` (recommended) or auto-detects by scanning `/dev/ttyACM0`–`/dev/ttyACM9`.

---

## 2. Discovery and Connection

### Startup Detection
1. Check `serial.port` in config (explicit path)
2. If not configured: auto-detect via `auto_detect_port()` (libudev or `/dev/ttyACM*` scan)
3. Open port with `RealSerialTransport::open(path, timeout)`
4. Port timeout set at open time (default 500ms per read operation)

### Retry on Failure
- **5 retries** with exponential backoff: 1s, 2s, 4s, 8s, 16s
- If all retries fail: daemon starts without OpenFan (degrades gracefully)
- No auto-reconnect at runtime — requires daemon restart if device disconnects

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

---

## 4. Command Reference

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

### 0x03: SetAllPwm(pwm_raw)

Set open-loop PWM on all 10 channels simultaneously.

```
Command:  >03{pwm:02X}\n
Example:  >03FF\n     (all channels to 100%)
Response: <03|00:HHHH;01:HHHH;...;09:HHHH;\r\n
```

### 0x04: SetTargetRpm(channel, rpm)

Set closed-loop RPM target (uses EMC2305).

```
Command:  >04{ch:02X}{rpm:04X}\n
Example:  >040503E8\n  (channel 5, target 1000 RPM)
Response: <04|05:03E8;\r\n
```

---

## 5. RPM Pairs Format

Each response contains one or more channel:rpm pairs:

```
NN:HHHH;NN:HHHH;...;
```

- `NN` — channel number in **decimal** (00–09)
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

### GUI-Driven Writes
- **SetPwm** sent on-demand when GUI control loop determines a write is needed (~1s cycles)
- **Coalescing:** If the channel is already at the requested PWM, the write is skipped (not sent to firmware)
- **Write suppression:** If PWM delta from last commanded value is < 1%, the write is suppressed (GUI-side)

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

### Stop Timeout (per-channel)
- **Rule:** 0% PWM cannot be held for more than **8 seconds** on any channel
- **Tracking:** Per-channel `stop_started_at: Option<Instant>`
- **Enforcement:** `apply_safety()` called before every `set_pwm()` and `set_pwm_all()`
- **Violation:** Returns `FanControlError::Validation` — command is rejected

### Thermal Emergency (global)
- **Trigger:** CPU Tctl ≥ 105°C
- **Action:** Force all OpenFan channels to 100% PWM
- **Hold:** Until Tctl ≤ 80°C
- **Recovery:** 60% PWM floor for 1 cycle, then resume profile control
- **Implementation:** `ThermalSafetyRule.evaluate()` called every 1s in profile engine

### Command Safety Guards
- **MAX_DEBUG_LINES = 50:** Aborts if firmware emits 50+ non-response lines
- **Wall-clock deadline:** Total operation bounded by the timeout parameter
- **Channel validation:** 0–9 only (10 channels)
- **PWM range:** 0–100% (mapped to 0–255 raw)
- **RPM range:** 0–5000 (soft cap in API validation)

---

## 8. What the Daemon Assumes About Firmware

1. **Line-based protocol** — one command per line, one response per line
2. **Response starts with `<`** — anything else is debug output
3. **Response echoes command code** — parsed and validated
4. **Response includes RPM readings** — even after SetPwm commands
5. **Real firmware omits closing `>`** — daemon accepts both formats
6. **Channel numbers in response are decimal** (not hex) — `05:04B0` means channel 5
7. **Firmware handles PWM 0–255 internally** — daemon converts percent→raw
8. **EMC2305 chip available** for closed-loop RPM mode
9. **Device is exclusively owned** — no other process should access the serial port

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

### Transport Parameters
| Parameter | Value | Changeable? |
|-----------|-------|-------------|
| Baud rate | 115,200 | Only via config change |
| Line terminator (command) | `\n` | Hardcoded |
| Line terminator (response) | `\r\n` or `\n` | Both accepted |
| Max response time | 500ms | Configurable |
| Max debug lines | 50 | Hardcoded constant |
| Channel count | 10 (0–9) | Hardcoded constant |

---

## 10. Failure Modes

| Failure | Detection | Recovery | Impact |
|---------|-----------|----------|--------|
| Device not found at startup | Port open fails | 5 retries with backoff | Daemon runs without OpenFan |
| Device disconnects during operation | Next read/write returns I/O error | **No auto-reconnect** — must restart daemon | Fan control stops |
| Firmware enters debug loop | 50 debug lines exceeded | Command fails with Protocol error | Affected write skipped |
| Response timeout | 500ms per read_line | SerialError::Timeout returned | Write skipped for this cycle |
| Malformed response | Protocol parsing fails | SerialError::Protocol returned | Write skipped |
| 0% PWM held > 8 seconds | Stop timeout check | Further 0% commands rejected | Fan restarts at last non-zero |

---

## 11. Golden Test Vectors

### Command Encoding

| Command | Input | Wire |
|---------|-------|------|
| ReadAllRpm | — | `>00\n` |
| ReadRpm(5) | ch=5 | `>0105\n` |
| SetPwm(5, 128) | ch=5, raw=0x80 | `>020580\n` |
| SetPwm(0, 255) | ch=0, raw=0xFF | `>0200FF\n` |
| SetAllPwm(0) | raw=0x00 | `>0300\n` |
| SetTargetRpm(5, 1000) | ch=5, rpm=0x03E8 | `>040503E8\n` |

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
