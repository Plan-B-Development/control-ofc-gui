"""Check an OpenFAN firmware file before an update (DEC-481).

Pure Python, no Qt and no dependency. The daemon never sees the file: the user
copies it onto the board's ``RPI-RP2`` drive. So this is the only check its bytes
get, and it runs before anything changes. A file that fails any check is refused
outright, with the reasons in plain words.

What a file must be (`UF2 <https://github.com/microsoft/uf2>`_, RP2040 boot ROM):

- a whole number of 512-byte blocks, at most :data:`MAX_FILE_BYTES`;
- every block marked for the RP2040's main flash, with 256 bytes of payload,
  numbered in order with one constant total;
- 256-byte-aligned addresses inside the board's 4 MiB flash, none repeated, the
  lowest at the start of flash;
- a first boot stage whose CRC-32/MPEG-2 checks, or the board could not start it;
- the OpenFAN USB names, which every OpenFAN build compiles in.

What it reads out, as evidence the daemon compares with the board after the
update (``openfan_maintenance::evidence``): the USB configuration descriptor the
image contains, and the ``KEY:VALUE`` strings the firmware answers ``>05``/``>06``
with. Neither proves which build runs — two builds can share both — and the
result says so. The Pico SDK's binary information (program name, build date,
SDK version) is read for display only.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# ── UF2 ───────────────────────────────────────────────────────────────

BLOCK_SIZE = 512
PAYLOAD_SIZE = 256
MAGIC_START0 = 0x0A324655
MAGIC_START1 = 0x9E5D5157
MAGIC_END = 0x0AB16F30
FLAG_NOT_MAIN_FLASH = 0x00000001
FLAG_FAMILY_ID_PRESENT = 0x00002000
RP2040_FAMILY_ID = 0xE48BFF56

#: The daemon's own bound on the size it is told (``MAX_FIRMWARE_BYTES``).
MAX_FILE_BYTES = 1024 * 1024

#: The RP2040's flash window, and the OpenFAN board's W25Q32 (4 MiB).
FLASH_BASE = 0x10000000
FLASH_SIZE = 4 * 1024 * 1024

#: The first boot stage: 252 bytes of code and a CRC-32/MPEG-2 of them.
BOOT2_SIZE = 256

#: The USB manufacturer and product names every OpenFAN build compiles in.
OPENFAN_USB_NAMES = (b"OpenFan", b"Karanovic Research")

#: At most this many problems are listed; the rest are counted.
MAX_LISTED_PROBLEMS = 6

# ── Pico SDK binary information ───────────────────────────────────────

_BI_MARKER_START = 0x7188EBF2
_BI_MARKER_END = 0xE71AA390
_BI_TYPE_ID_AND_STRING = 6
_BI_TAG_RP = 0x5052
_BI_PROGRAM_NAME = 0x02031C86
_BI_BUILD_DATE = 0x9DA22254
_BI_SDK_VERSION = 0x5360B3AB
_BI_BUILD_ATTRIBUTE = 0x4275F0D3
#: The header sits just after the first boot stage; this bounds the search.
_BI_HEADER_SEARCH = 0x1000
_BI_MAX_ENTRIES = 256
_BI_MAX_STRING = 128

# ── The firmware's own information strings ────────────────────────────

#: One ``KEY:VALUE\r\n`` reply line as the firmware stores it, starting a string
#: (the byte before is not printable). The daemon keeps the same shape: a key of
#: upper-case letters, digits and ``_`` up to 32, a printable value up to 64.
_INFO_LINE = re.compile(rb"(?<![\x20-\x7e])([A-Z0-9_]{1,32}):([\x20-\x7e]{0,64})\r\n")
#: The daemon accepts at most this many (``validated`` in its start handler).
MAX_INFO_ENTRIES = 16

_CONFIG_MAX = 512


@dataclass(frozen=True)
class KnownRelease:
    """A published OpenFAN firmware file, by its SHA-256 (Q5).

    Fingerprints of the files as published, computed by Control-OFC; the
    maintainer publishes none. A match says the file is byte-for-byte that
    published file — nothing more.
    """

    sha256: str
    size: int
    name: str
    where: str


#: Q5: the published OpenFAN firmware files Control-OFC knows.
KNOWN_RELEASES: tuple[KnownRelease, ...] = (
    KnownRelease(
        sha256="86187e7833cfccc6bb3f1d4bf4e7c03af2a154c17fa35123d73d7708f524ba7b",
        size=85504,
        name="2023-09-29 release (FW_01)",
        where="Firmware/Release Binaries/2023-09-29_OpenFAN_FW_01.uf2 in the OpenFanController "
        "repository",
    ),
    KnownRelease(
        sha256="6614f66db6754cb598665a5da2b263acef749db4952e3925eab43bf8329d1cc4",
        size=79360,
        name="2026-09-13 release",
        where="OpenFAN_Firmware.uf2 on the 2026-09-13 GitHub release",
    ),
    KnownRelease(
        sha256="79a3c951beb69ed5b30161ac34eb3e3d8ba70499b0761460028bf609da6feee0",
        size=79360,
        name="2026-09-27 release",
        where="OpenFAN_Firmware.uf2 on the 2026-09-27 GitHub release",
    ),
)

#: Where the maintainer publishes releases.
RELEASES_URL = "https://github.com/SasaKaranovic/OpenFanController/releases"


def known_release(sha256: str) -> KnownRelease | None:
    """The published file with this fingerprint, or ``None``."""
    return next((r for r in KNOWN_RELEASES if r.sha256 == sha256.lower()), None)


@dataclass(frozen=True)
class Uf2Inspection:
    """What a firmware file is, and whether it may be used."""

    size: int
    sha256: str
    problems: tuple[str, ...] = ()
    blocks: int = 0
    flash_start: int | None = None
    #: One past the last byte written.
    flash_end: int | None = None
    program_name: str | None = None
    build_date: str | None = None
    sdk_version: str | None = None
    build_type: str | None = None
    #: The ``KEY:VALUE`` strings the firmware answers ``>05``/``>06`` with, or
    #: ``None`` when they cannot be read unambiguously.
    info: Mapping[str, str] | None = None
    usb_config_descriptor_hex: str | None = None
    release: KnownRelease | None = field(default=None)

    @property
    def ok(self) -> bool:
        return not self.problems

    def claim(self) -> dict:
        """The ``firmware`` object of ``POST /fans/openfan/maintenance``."""
        out: dict = {"sha256": self.sha256, "size": self.size}
        if self.usb_config_descriptor_hex:
            out["usb_config_descriptor_hex"] = self.usb_config_descriptor_hex
        if self.info:
            out["info"] = dict(self.info)
        return out


class FirmwareFileError(Exception):
    """A firmware file that could not be read. The message is for the user."""


def read_firmware_file(path: Path) -> bytes:
    """Read at most one byte past :data:`MAX_FILE_BYTES` from a regular file.

    Opened non-blocking and checked to be a regular file first: a FIFO or a
    device chosen in the file picker would otherwise block or never end.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise FirmwareFileError(f"The file could not be opened: {exc.strerror}.") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FirmwareFileError("That is not a regular file.")
        chunks: list[bytes] = []
        left = MAX_FILE_BYTES + 1
        while left > 0:
            chunk = os.read(fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise FirmwareFileError(f"The file could not be read: {exc.strerror}.") from exc
    finally:
        os.close(fd)


def crc32_mpeg2(data: bytes) -> int:
    """CRC-32/MPEG-2: polynomial 0x04C11DB7, initial 0xFFFFFFFF, no reflection,
    no final XOR — the checksum the RP2040 boot ROM checks the first boot stage
    with."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


def inspect_uf2(data: bytes) -> Uf2Inspection:
    """Check *data* as an OpenFAN firmware file and read out its evidence."""
    sha256 = hashlib.sha256(data).hexdigest()
    base = {"size": len(data), "sha256": sha256, "release": known_release(sha256)}
    if not data:
        return Uf2Inspection(**base, problems=("The file is empty.",))
    if len(data) > MAX_FILE_BYTES:
        return Uf2Inspection(
            **base,
            problems=("The file is larger than 1 MiB. No OpenFAN firmware is that big.",),
        )
    if len(data) % BLOCK_SIZE:
        return Uf2Inspection(
            **base,
            problems=(
                "This is not a UF2 firmware file: its size is not a whole number of "
                "512-byte blocks.",
            ),
        )

    problems: list[str] = []
    payloads: dict[int, bytes] = {}
    count = len(data) // BLOCK_SIZE
    for index in range(count):
        block = data[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        m0, m1, flags, addr, size, number, total, family = struct.unpack_from("<8I", block)
        (m_end,) = struct.unpack_from("<I", block, BLOCK_SIZE - 4)
        n = index + 1
        if (m0, m1, m_end) != (MAGIC_START0, MAGIC_START1, MAGIC_END):
            problems.append(f"Block {n} is not a UF2 block: its markers are wrong.")
            continue
        if flags & FLAG_NOT_MAIN_FLASH:
            problems.append(f"Block {n} is not meant for the board's flash.")
            continue
        if flags != FLAG_FAMILY_ID_PRESENT:
            problems.append(
                f"Block {n} has flags 0x{flags:08x}. An RP2040 firmware image has "
                "only the family flag."
            )
            continue
        if family != RP2040_FAMILY_ID:
            problems.append(
                f"Block {n} is for chip family 0x{family:08x}, not the RP2040 "
                f"(0x{RP2040_FAMILY_ID:08x}) the OpenFAN board uses."
            )
            continue
        if size != PAYLOAD_SIZE:
            problems.append(
                f"Block {n} carries {size} bytes; an RP2040 image carries {PAYLOAD_SIZE} per block."
            )
            continue
        if number != index or total != count:
            problems.append(
                f"Block {n} is numbered {number + 1} of {total}; the file holds {count} "
                "blocks in order."
            )
            continue
        if addr % PAYLOAD_SIZE:
            problems.append(f"Block {n}'s address 0x{addr:08x} is not 256-byte aligned.")
            continue
        if not FLASH_BASE <= addr <= FLASH_BASE + FLASH_SIZE - PAYLOAD_SIZE:
            problems.append(f"Block {n}'s address 0x{addr:08x} is outside the board's 4 MiB flash.")
            continue
        if addr in payloads:
            problems.append(f"Block {n} writes 0x{addr:08x} again.")
            continue
        payloads[addr] = block[32 : 32 + PAYLOAD_SIZE]

    if problems:
        return Uf2Inspection(**base, problems=_listed(problems), blocks=count)

    start, end = min(payloads), max(payloads) + PAYLOAD_SIZE
    image = bytearray(b"\xff" * (end - start))
    for addr, payload in payloads.items():
        image[addr - start : addr - start + PAYLOAD_SIZE] = payload
    image = bytes(image)
    found = {"blocks": count, "flash_start": start, "flash_end": end}

    if start != FLASH_BASE:
        problems.append(
            f"The image starts at 0x{start:08x}, not at the start of flash "
            f"(0x{FLASH_BASE:08x}), so the board would not start it."
        )
    else:
        (stored,) = struct.unpack_from("<I", image, BOOT2_SIZE - 4)
        if crc32_mpeg2(image[: BOOT2_SIZE - 4]) != stored:
            problems.append(
                "The image's first boot stage fails its checksum, so the board could not start it."
            )
    missing = [n.decode() for n in OPENFAN_USB_NAMES if n not in image]
    if missing:
        names = " and ".join(f"'{n}'" for n in missing)
        problems.append(
            f"The image does not contain the OpenFAN USB name {names}, so it is not "
            "OpenFAN firmware."
        )
    if problems:
        return Uf2Inspection(**base, problems=tuple(problems), **found)

    program, date, sdk, build = _binary_info(image, start)
    return Uf2Inspection(
        **base,
        **found,
        program_name=program,
        build_date=date,
        sdk_version=sdk,
        build_type=build,
        info=_info_strings(image),
        usb_config_descriptor_hex=_config_descriptor(image),
    )


def _listed(problems: list[str]) -> tuple[str, ...]:
    if len(problems) <= MAX_LISTED_PROBLEMS:
        return tuple(problems)
    more = len(problems) - MAX_LISTED_PROBLEMS
    return (*problems[:MAX_LISTED_PROBLEMS], f"…and {more} more.")


def _info_strings(image: bytes) -> dict[str, str] | None:
    """The firmware's ``KEY:VALUE`` reply strings, as the daemon would read them.

    ``None`` when there are none, too many, or one key with two values: then
    the strings cannot be compared with the board's answers and are left out.
    """
    found: dict[str, str] = {}
    for match in _INFO_LINE.finditer(image):
        key = match.group(1).decode()
        value = match.group(2).decode().strip()
        if found.get(key, value) != value:
            return None
        found[key] = value
    if not found or len(found) > MAX_INFO_ENTRIES:
        return None
    return found


def _config_descriptor(image: bytes) -> str | None:
    """The USB configuration descriptor the image contains, as lower-case hex.

    A candidate is ``09 02`` with a plausible total length, whose bytes are a
    chain of descriptors ending exactly at that length and holding at least one
    interface. ``None`` unless exactly one distinct candidate exists — two would
    leave the comparison guessing.
    """
    found: set[bytes] = set()
    at = image.find(b"\x09\x02")
    while at >= 0:
        if at + 9 <= len(image):
            total = image[at + 2] | image[at + 3] << 8
            interfaces = image[at + 4]
            if 9 <= total <= _CONFIG_MAX and 1 <= interfaces <= 32 and at + total <= len(image):
                candidate = image[at : at + total]
                if _descriptor_chain(candidate):
                    found.add(candidate)
        at = image.find(b"\x09\x02", at + 1)
    if len(found) != 1:
        return None
    return found.pop().hex()


def _descriptor_chain(config: bytes) -> bool:
    at, interface = 0, False
    while at < len(config):
        length = config[at]
        if length < 2 or at + length > len(config):
            return False
        interface = interface or config[at + 1] == 0x04
        at += length
    return at == len(config) and interface


def _binary_info(image: bytes, base: int) -> tuple[str | None, str | None, str | None, str | None]:
    """Program name, build date, SDK version and build type, where present.

    Every pointer is bounds-checked against the image; anything outside it, or
    a string that is not plain printable text, reads as absent.
    """

    def word(addr: int) -> int | None:
        at = addr - base
        if 0 <= at <= len(image) - 4:
            return struct.unpack_from("<I", image, at)[0]
        return None

    def text(addr: int) -> str | None:
        at = addr - base
        if not 0 <= at < len(image):
            return None
        end = image.find(b"\0", at, at + _BI_MAX_STRING)
        if end < 0:
            return None
        raw = image[at:end]
        if not raw or any(b < 0x20 or b > 0x7E for b in raw):
            return None
        return raw.decode()

    strings: dict[int, str] = {}
    for at in range(0, min(len(image), _BI_HEADER_SEARCH) - 19, 4):
        if struct.unpack_from("<I", image, at)[0] != _BI_MARKER_START:
            continue
        first, last, _mapping, marker = struct.unpack_from("<4I", image, at + 4)
        if marker != _BI_MARKER_END or not first <= last <= first + 4 * _BI_MAX_ENTRIES:
            continue
        for pointer in range(first, last, 4):
            entry = word(pointer)
            head = None if entry is None else word(entry)
            if entry is None or head is None:
                continue
            kind, tag = head & 0xFFFF, head >> 16
            if kind != _BI_TYPE_ID_AND_STRING or tag != _BI_TAG_RP:
                continue
            ident, string = word(entry + 4), word(entry + 8)
            if ident is None or string is None:
                continue
            value = text(string)
            if value is not None:
                strings.setdefault(ident, value)
        break
    return (
        strings.get(_BI_PROGRAM_NAME),
        strings.get(_BI_BUILD_DATE),
        strings.get(_BI_SDK_VERSION),
        strings.get(_BI_BUILD_ATTRIBUTE),
    )


class PreparedFileError(Exception):
    """The prepared copy could not be written, or did not read back the same."""


def prepared_name(sha256: str) -> str:
    return f"OpenFAN-{sha256[:8]}.uf2"


def prepare_firmware(data: bytes, inspection: Uf2Inspection, directory: Path) -> Path:
    """Write the checked bytes to an owner-only copy and read them back.

    The copy is what the user drags onto ``RPI-RP2``: a fixed name in a private
    directory, so what they copy is what was checked, not whatever the original
    path holds by then. Read back and fingerprinted again before it is offered.
    """
    from control_ofc.paths import atomic_write_bytes

    if not inspection.ok or hashlib.sha256(data).hexdigest() != inspection.sha256:
        raise PreparedFileError("Only a checked file can be prepared.")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        target = directory / prepared_name(inspection.sha256)
        atomic_write_bytes(target, data)
        written = target.read_bytes()
    except OSError as exc:
        raise PreparedFileError(f"The prepared copy could not be written: {exc}") from exc
    if hashlib.sha256(written).hexdigest() != inspection.sha256:
        raise PreparedFileError("The prepared copy did not read back the same.")
    return target


def prepared_file_for(sha256: str, directory: Path) -> Path | None:
    """The prepared copy of the file with this fingerprint, if it is still there
    and still that file — so a reopened window can offer it again."""
    target = directory / prepared_name(sha256)
    try:
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return None
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha256.lower():
            return None
    except OSError:
        return None
    return target
