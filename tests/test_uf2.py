"""The OpenFAN firmware file check (DEC-481, ``services/uf2.py``).

Synthetic UF2 files, built here block by block, one defect per case: the real
firmware is GPL-3.0 upstream and the 2023 binary matches no published source, so
none is committed. ``OFC_FIRMWARE_DIR`` opts in to a run over real files.
"""

from __future__ import annotations

import os
import stat
import struct

import pytest

from control_ofc.services import uf2
from control_ofc.services.uf2 import (
    FLASH_BASE,
    KNOWN_RELEASES,
    MAX_FILE_BYTES,
    RP2040_FAMILY_ID,
    FirmwareFileError,
    PreparedFileError,
    crc32_mpeg2,
    inspect_uf2,
    known_release,
    prepare_firmware,
    prepared_file_for,
    read_firmware_file,
)

# A configuration descriptor: header, one interface, two endpoints (32 bytes).
CONFIG = bytes.fromhex("09022000010100809609040000020a0000000705010240000007058102400000")
INFO = {"HW_REV": "03", "MCU": "PICO2040", "FW_REV": "01", "PROTOCOL_VERSION": "01"}

BI_START, BI_END = 0x7188EBF2, 0xE71AA390
BI_STRINGS = {
    0x02031C86: b"OpenFAN_Firmware",
    0x9DA22254: b"Sep 27 2026",
    0x5360B3AB: b"2.3.0",
    0x4275F0D3: b"Release",
}


def image(
    *,
    size: int = 0x1000,
    names: bool = True,
    info: dict[str, str] | None = None,
    extra_info: bytes = b"",
    configs: tuple[bytes, ...] = (CONFIG,),
    binary_info: bool = True,
    good_crc: bool = True,
) -> bytes:
    """A small flash image with a valid first boot stage and the pieces under test."""
    img = bytearray(size)
    img[:252] = bytes(i * 7 % 256 for i in range(252))
    crc = crc32_mpeg2(bytes(img[:252]))
    struct.pack_into("<I", img, 252, crc if good_crc else crc ^ 1)
    if binary_info:
        entries, records, strings = 0x200, 0x240, 0x300
        struct.pack_into(
            "<5I", img, 0x100, BI_START, FLASH_BASE + entries,
            FLASH_BASE + entries + 4 * len(BI_STRINGS), 0, BI_END,
        )  # fmt: skip
        at = strings
        for n, (ident, text) in enumerate(BI_STRINGS.items()):
            img[at : at + len(text) + 1] = text + b"\0"
            struct.pack_into("<I", img, entries + 4 * n, FLASH_BASE + records + 12 * n)
            struct.pack_into("<HHII", img, records + 12 * n, 6, 0x5052, ident, FLASH_BASE + at)
            at += len(text) + 1
    if names:
        blob = b"\0OpenFan\0Karanovic Research\0"
        img[0x400 : 0x400 + len(blob)] = blob
    lines = b"".join(
        b"\0" + f"{k}:{v}\r\n".encode() for k, v in (INFO if info is None else info).items()
    )
    lines += extra_info
    img[0x500 : 0x500 + len(lines) + 1] = lines + b"\0"
    for n, config in enumerate(configs):
        img[0x700 + 0x80 * n : 0x700 + 0x80 * n + len(config)] = config
    return bytes(img)


def uf2_file(
    img: bytes,
    *,
    base: int = FLASH_BASE,
    family: int = RP2040_FAMILY_ID,
    flags: int = 0x2000,
    payload: int = 256,
    edit=None,
) -> bytes:
    """The image as UF2 blocks; *edit(index, fields)* may change one block's header."""
    count = len(img) // 256
    out = bytearray()
    for i in range(count):
        fields = {
            "m0": 0x0A324655, "m1": 0x9E5D5157, "flags": flags, "addr": base + 256 * i,
            "size": payload, "no": i, "total": count, "family": family, "end": 0x0AB16F30,
        }  # fmt: skip
        if edit is not None:
            edit(i, fields)
        block = bytearray(512)
        struct.pack_into(
            "<8I", block, 0, fields["m0"], fields["m1"], fields["flags"], fields["addr"],
            fields["size"], fields["no"], fields["total"], fields["family"],
        )  # fmt: skip
        block[32 : 32 + 256] = img[256 * i : 256 * (i + 1)]
        struct.pack_into("<I", block, 508, fields["end"])
        out += block
    return bytes(out)


def problems(data: bytes) -> str:
    result = inspect_uf2(data)
    assert not result.ok, "precondition: the file is refused"
    return " ".join(result.problems)


class TestAGoodFile:
    def test_it_passes_and_reads_out_its_evidence(self):
        data = uf2_file(image())
        r = inspect_uf2(data)
        assert r.ok, r.problems
        assert (r.size, r.blocks) == (len(data), len(data) // 512)
        assert (r.flash_start, r.flash_end) == (FLASH_BASE, FLASH_BASE + 0x1000)
        assert r.info == INFO
        assert r.usb_config_descriptor_hex == CONFIG.hex()
        assert (r.program_name, r.build_date, r.sdk_version, r.build_type) == (
            "OpenFAN_Firmware",
            "Sep 27 2026",
            "2.3.0",
            "Release",
        )

    def test_its_claim_is_what_the_daemon_validates(self):
        """The `firmware` object of the start request: the fingerprint, the size
        and the two pieces of evidence, never a path."""
        data = uf2_file(image())
        claim = inspect_uf2(data).claim()
        assert claim == {
            "sha256": inspect_uf2(data).sha256,
            "size": len(data),
            "usb_config_descriptor_hex": CONFIG.hex(),
            "info": INFO,
        }
        assert len(claim["sha256"]) == 64 and claim["size"] % 512 == 0

    def test_without_evidence_the_claim_carries_only_the_fingerprint(self):
        data = uf2_file(image(info={}, configs=()))
        r = inspect_uf2(data)
        assert r.ok
        assert r.info is None and r.usb_config_descriptor_hex is None
        assert set(r.claim()) == {"sha256", "size"}

    def test_a_file_without_binary_information_is_still_firmware(self):
        r = inspect_uf2(uf2_file(image(binary_info=False)))
        assert r.ok
        assert r.program_name is None and r.build_date is None


class TestRefusals:
    def test_an_empty_file(self):
        assert "empty" in problems(b"")

    def test_a_file_over_the_bound_is_refused_whatever_it_holds(self):
        assert "larger than 1 MiB" in problems(b"\0" * (MAX_FILE_BYTES + 512))

    def test_a_size_that_is_not_whole_blocks(self):
        assert "512-byte blocks" in problems(uf2_file(image())[:-1])

    def test_bad_markers(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(m1=0) if i == 2 else None)
        assert "Block 3 is not a UF2 block" in problems(data)

    def test_a_chip_other_than_the_rp2040(self):
        # The RP2350's ARM family id: right format, wrong chip.
        assert "chip family 0xe48bff59" in problems(uf2_file(image(), family=0xE48BFF59))

    def test_a_block_not_for_main_flash(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(flags=0x2001) if i == 0 else None)
        assert "not meant for the board's flash" in problems(data)

    def test_unexpected_flags(self):
        assert "flags 0x00006000" in problems(uf2_file(image(), flags=0x6000))

    def test_a_payload_other_than_256_bytes(self):
        assert "carries 476 bytes" in problems(uf2_file(image(), payload=476))

    def test_blocks_out_of_order(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(no=5) if i == 4 else None)
        assert "Block 5 is numbered 6" in problems(data)

    def test_a_total_that_changes(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(total=99) if i == 1 else None)
        assert "of 99" in problems(data)

    def test_a_misaligned_address(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(addr=f["addr"] + 4) if i == 3 else None)
        assert "not 256-byte aligned" in problems(data)

    def test_a_repeated_address(self):
        data = uf2_file(image(), edit=lambda i, f: f.update(addr=FLASH_BASE) if i == 3 else None)
        assert "writes 0x10000000 again" in problems(data)

    def test_an_address_outside_the_boards_flash(self):
        data = uf2_file(image(), base=FLASH_BASE + 4 * 1024 * 1024)
        assert "outside the board's 4 MiB flash" in problems(data)

    def test_an_image_that_does_not_start_at_the_start_of_flash(self):
        assert "not at the start of flash" in problems(uf2_file(image(), base=FLASH_BASE + 256))

    def test_a_first_boot_stage_that_fails_its_checksum(self):
        assert "fails its checksum" in problems(uf2_file(image(good_crc=False)))

    def test_firmware_for_another_board(self):
        assert "not OpenFAN firmware" in problems(uf2_file(image(names=False)))

    def test_many_problems_are_counted_not_all_listed(self):
        r = inspect_uf2(uf2_file(image(), family=0))
        assert r.blocks == 16 and len(r.problems) == uf2.MAX_LISTED_PROBLEMS + 1
        assert r.problems[-1] == f"…and {16 - uf2.MAX_LISTED_PROBLEMS} more."


class TestEvidence:
    def test_one_key_with_two_values_drops_the_strings(self):
        r = inspect_uf2(uf2_file(image(extra_info=b"\0HW_REV:01\r\n")))
        assert r.ok and r.info is None

    def test_the_same_string_twice_is_one_entry(self):
        r = inspect_uf2(uf2_file(image(extra_info=b"\0HW_REV:03\r\n")))
        assert r.info == INFO

    def test_more_strings_than_the_daemon_accepts_drops_them(self):
        many = {f"K{n}": "1" for n in range(uf2.MAX_INFO_ENTRIES + 1)}
        r = inspect_uf2(uf2_file(image(info=many)))
        assert r.ok and r.info is None

    def test_a_suffix_of_a_longer_string_is_not_a_report(self):
        """`xHW_REV:09` is one string; only a string that starts with the key is
        a reply line the firmware sends."""
        r = inspect_uf2(uf2_file(image(extra_info=b"\0xHW_REV:09\r\n")))
        assert r.info == INFO

    def test_two_different_descriptors_leave_the_comparison_out(self):
        other = CONFIG[:-1] + b"\x01"
        r = inspect_uf2(uf2_file(image(configs=(CONFIG, other))))
        assert r.ok and r.usb_config_descriptor_hex is None

    def test_a_descriptor_whose_chain_does_not_close_is_not_one(self):
        broken = CONFIG[:9] + b"\x09\x04" + CONFIG[11:-7] + b"\x08\x05\x01\x02\x40\x00\x00"
        r = inspect_uf2(uf2_file(image(configs=(broken,))))
        assert r.usb_config_descriptor_hex is None


class TestKnownReleases:
    def test_the_table_finds_a_published_file_by_its_fingerprint(self):
        assert known_release(KNOWN_RELEASES[1].sha256.upper()) is KNOWN_RELEASES[1]
        assert known_release("0" * 64) is None

    def test_every_entry_is_a_whole_uf2_file(self):
        for release in KNOWN_RELEASES:
            assert len(release.sha256) == 64 and release.size % 512 == 0
            assert release.size <= MAX_FILE_BYTES

    def test_an_unpublished_file_matches_nothing(self):
        assert inspect_uf2(uf2_file(image())).release is None


class TestReadAndPrepare:
    def test_reading_stops_one_byte_past_the_bound(self, tmp_path):
        path = tmp_path / "huge.uf2"
        path.write_bytes(b"\0" * (2 * MAX_FILE_BYTES))
        assert len(read_firmware_file(path)) == MAX_FILE_BYTES + 1

    def test_a_fifo_is_refused_without_blocking(self, tmp_path):
        path = tmp_path / "pipe.uf2"
        os.mkfifo(path)
        with pytest.raises(FirmwareFileError, match="not a regular file"):
            read_firmware_file(path)

    def test_a_directory_and_a_missing_file_are_refused(self, tmp_path):
        with pytest.raises(FirmwareFileError, match="not a regular file"):
            read_firmware_file(tmp_path)
        with pytest.raises(FirmwareFileError, match="could not be opened"):
            read_firmware_file(tmp_path / "nope.uf2")

    def test_the_prepared_copy_is_private_and_reads_back_the_same(self, tmp_path):
        data = uf2_file(image())
        r = inspect_uf2(data)
        folder = tmp_path / "firmware"
        path = prepare_firmware(data, r, folder)
        assert path.read_bytes() == data
        assert path.name == f"OpenFAN-{r.sha256[:8]}.uf2"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert prepared_file_for(r.sha256, folder) == path

    def test_a_copy_that_changed_since_is_not_offered(self, tmp_path):
        data = uf2_file(image())
        r = inspect_uf2(data)
        path = prepare_firmware(data, r, tmp_path)
        assert prepared_file_for(r.sha256, tmp_path) == path  # presence first
        path.write_bytes(data[:-512])
        assert prepared_file_for(r.sha256, tmp_path) is None

    def test_only_a_checked_file_is_prepared(self, tmp_path):
        data = uf2_file(image(names=False))
        with pytest.raises(PreparedFileError):
            prepare_firmware(data, inspect_uf2(data), tmp_path)
        good = uf2_file(image())
        with pytest.raises(PreparedFileError):
            prepare_firmware(good + b"\0" * 512, inspect_uf2(good), tmp_path)
        assert list(tmp_path.iterdir()) == []


REAL = os.environ.get("OFC_FIRMWARE_DIR")


@pytest.mark.skipif(not REAL, reason="set OFC_FIRMWARE_DIR to a folder of real .uf2 files")
def test_real_firmware_files_pass_and_published_ones_are_recognised():
    from pathlib import Path

    files = sorted(Path(REAL).glob("*.uf2"))
    assert files, "precondition: the folder holds firmware files"
    for path in files:
        r = inspect_uf2(read_firmware_file(path))
        assert r.ok, (path.name, r.problems)
        assert r.info and "FW_REV" in r.info, path.name
        assert r.usb_config_descriptor_hex, path.name
        if r.release is not None:
            assert r.size == r.release.size, path.name
