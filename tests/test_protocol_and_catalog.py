"""Tests for the SysEx protocol codec and 248-chip factory ROM catalog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from k250_rom_dumper.catalog import (
    CPU_OS61_SPECS,
    FACTORY_CATALOG,
    PHYSICAL_SOCKETS,
    SOUND_ROM_SPECS,
    checksum16,
    diag_lsb_checksum,
    identify_rom_bytes,
    verify_chip_bytes,
)
from k250_rom_dumper.protocol import (
    MAGIC,
    DumperDecoder,
    encode_chip_sysex,
    inspect_syx_file,
)


def _make_rom_with_checksum16(target: int) -> bytes:
    buf = bytearray(32768)
    rem = target & 0xFFFF
    i = 0
    while rem > 0:
        step = min(0xFF, rem)
        buf[i] = step
        rem -= step
        i += 1
    assert (sum(buf) & 0xFFFF) == target
    return bytes(buf)


def _make_lsb_rom_with_diag_checksum(target: int) -> bytes:
    # Byte 0x80 -> bit-reversed 0x01 -> ((0x01 & 0xF0) + (0x01 & 0x0F)) = 1
    # Byte 0x01 -> bit-reversed 0x80 -> ((0x80 & 0xF0) + (0x80 & 0x0F)) = 0x80 (128)
    buf = bytearray(32768)
    rem = target & 0xFFFF
    i = 0
    while rem >= 128 and i < 32768:
        buf[i] = 0x01
        rem -= 128
        i += 1
    while rem > 0 and i < 32768:
        buf[i] = 0x80
        rem -= 1
        i += 1
    assert diag_lsb_checksum(bytes(buf)) == target
    return bytes(buf)


class CatalogAndProtocolTests(unittest.TestCase):
    def test_catalog_counts_and_physical_sockets(self) -> None:
        self.assertEqual(len(SOUND_ROM_SPECS), 240)
        self.assertEqual(len(CPU_OS61_SPECS), 8)
        self.assertEqual(len(FACTORY_CATALOG), 248)
        self.assertEqual(len(PHYSICAL_SOCKETS), 60)

        for ps in PHYSICAL_SOCKETS:
            self.assertEqual(len(ps.quarter_tokens), 4)
            for q_idx, tok in enumerate(ps.quarter_tokens):
                self.assertIn(tok, FACTORY_CATALOG)
                self.assertEqual(FACTORY_CATALOG[tok].quarter, q_idx)

    def test_diag_lsb_checksum_vectors(self) -> None:
        self.assertEqual(diag_lsb_checksum(bytes([0x00] * 32768)), 0)
        # 0xFF -> rev 0xFF -> (0xF0 + 0x0F) = 0xFF per byte -> 32768 * 0xFF & 0xFFFF = 0x8000
        self.assertEqual(diag_lsb_checksum(bytes([0xFF] * 32768)), 0x8000)
        # 0x01 -> rev 0x80 -> 0x80; 0x80 -> rev 0x01 -> 0x01
        self.assertEqual(diag_lsb_checksum(bytes([0x01, 0x80])), 0x81)

    def test_msb_and_lsb_sysex_round_trip_with_factory_match(self) -> None:
        u106_spec = FACTORY_CATALOG["U106"]
        u106_bytes = _make_rom_with_checksum16(u106_spec.expected_checksum)
        u125_20_spec = FACTORY_CATALOG["U125_20"]
        u125_20_bytes = _make_lsb_rom_with_diag_checksum(u125_20_spec.expected_checksum)

        stream = (
            encode_chip_sysex(4, 0, "U106 E38 L0     ", u106_bytes)
            + b"\xFE\xF8"  # MIDI Active Sensing / Clock real-time bytes should be ignored
            + encode_chip_sysex(44, 1, "U125_20 F20 LSB0", u125_20_bytes)
        )

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            dec = DumperDecoder(out_dir, verbose=False)
            dec.feed_stream_bytes(stream)

            self.assertEqual(dec.errors, [])
            self.assertEqual(len(dec.completed), 2)
            self.assertTrue(dec.completed[0].factory_match)
            self.assertFalse(dec.completed[0].is_lsb)
            self.assertTrue(dec.completed[1].factory_match)
            self.assertTrue(dec.completed[1].is_lsb)

            self.assertEqual((out_dir / "U106.bin").read_bytes(), u106_bytes)
            self.assertEqual((out_dir / "U125_20.bin").read_bytes(), u125_20_bytes)

            syx_path = out_dir / "test.syx"
            syx_path.write_bytes(stream)
            summary = inspect_syx_file(syx_path)
            self.assertEqual(summary["starts"], 2)
            self.assertEqual(summary["data_packets"], 256)
            self.assertEqual(summary["ends"], 2)
            self.assertEqual(summary["chips"], ["U106", "U125_20"])

    def test_corrupted_packet_checksum_and_abort(self) -> None:
        u106_bytes = _make_rom_with_checksum16(FACTORY_CATALOG["U106"].expected_checksum)
        raw_syx = bytearray(encode_chip_sysex(4, 0, "U106 E38 L0     ", u106_bytes))

        # Corrupt one nibble in the first DATA packet
        first_data_idx = raw_syx.index(b"\xF0" + MAGIC + b"\x02")
        raw_syx[first_data_idx + 12] ^= 0x01

        with tempfile.TemporaryDirectory() as td:
            dec = DumperDecoder(Path(td), verbose=False)
            dec.feed_stream_bytes(bytes(raw_syx))
            self.assertEqual(len(dec.completed), 0)
            self.assertTrue(dec.errors)

            # Test ABORT message clears active dump cleanly
            abort_stream = (
                bytes(raw_syx[: first_data_idx])
                + b"\xF0" + MAGIC + b"\x04\x04\xF7"
            )
            dec2 = DumperDecoder(Path(td), verbose=False)
            dec2.feed_stream_bytes(abort_stream)
            self.assertIsNone(dec2.active)
            self.assertEqual(dec2.errors, [])

    def test_identify_and_verify_chip_bytes(self) -> None:
        u54_spec = FACTORY_CATALOG["U54"]
        u54_bytes = _make_rom_with_checksum16(u54_spec.expected_checksum)
        spec, actual, match = verify_chip_bytes("U54", u54_bytes)
        self.assertEqual(spec, u54_spec)
        self.assertEqual(actual, u54_spec.expected_checksum)
        self.assertTrue(match)

        matches = identify_rom_bytes(u54_bytes)
        self.assertTrue(any(m.token == "U54" for m in matches))


if __name__ == "__main__":
    unittest.main()
