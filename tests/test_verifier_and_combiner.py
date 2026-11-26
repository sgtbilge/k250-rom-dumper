"""Tests for dump directory verification and 1-Mbit quarter combiner/splitter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from k250_rom_dumper.catalog import CPU_OS61_SPECS, SOUND_ROM_SPECS, diag_lsb_checksum
from k250_rom_dumper.combiner import combine_quarters, split_mbit_roms
from k250_rom_dumper.verifier import format_verification_report, verify_dump_directory


def _synth_chip(target: int, is_lsb: bool) -> bytes:
    buf = bytearray(32768)
    rem = target & 0xFFFF
    i = 0
    if is_lsb:
        while rem >= 128 and i < 32768:
            buf[i] = 0x01
            rem -= 128
            i += 1
        while rem > 0 and i < 32768:
            buf[i] = 0x80
            rem -= 1
            i += 1
        assert diag_lsb_checksum(bytes(buf)) == target
    else:
        while rem > 0 and i < 32768:
            step = min(0xFF, rem)
            buf[i] = step
            rem -= step
            i += 1
        assert (sum(buf) & 0xFFFF) == target
    return bytes(buf)


class VerifierAndCombinerTests(unittest.TestCase):
    def _populate_basic_and_optional(self, dump_dir: Path, include_blocks: bool = True, blank_block_a: bool = False) -> None:
        for spec in SOUND_ROM_SPECS:
            if not include_blocks and spec.slot in (2, 3, 4, 5):
                continue
            if blank_block_a and spec.slot == 5:
                (dump_dir / f"{spec.token}.bin").write_bytes(b"\xFF" * 32768)
                continue
            (dump_dir / f"{spec.token}.bin").write_bytes(_synth_chip(spec.expected_checksum, spec.is_lsb))
        for spec in CPU_OS61_SPECS:
            if spec.token in ("U54", "U38"):
                continue  # Simulate live MIDI dump where U54/U38 hold dumper
            (dump_dir / f"{spec.token}.bin").write_bytes(_synth_chip(spec.expected_checksum, False))

    def test_verify_full_set_and_combine_split_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dump_dir = root / "dumps"
            comb_dir = root / "combined"
            resplit_dir = root / "resplit"
            dump_dir.mkdir()

            self._populate_basic_and_optional(dump_dir, include_blocks=True)

            rep = verify_dump_directory(dump_dir, require_cpu=False)
            self.assertEqual(rep.sound_ok, 240)
            self.assertEqual(rep.cpu_ok, 6)
            self.assertTrue(rep.u106_ok)
            self.assertTrue(rep.emulator_6mb_ready)
            self.assertFalse(rep.has_errors)
            txt = format_verification_report(rep)
            self.assertIn("240/240 verified", txt)

            records = combine_quarters(dump_dir, comb_dir)
            self.assertEqual(len(records), 60)
            self.assertTrue((comb_dir / "1MBIT_CHECKSUMS.txt").is_file())
            self.assertEqual((comb_dir / "RowF_U125.bin").stat().st_size, 131072)

            split_files = split_mbit_roms(comb_dir, resplit_dir)
            self.assertEqual(len(split_files), 240)
            rep2 = verify_dump_directory(resplit_dir, require_cpu=False)
            self.assertEqual(rep2.sound_ok, 240)
            self.assertFalse(rep2.has_errors)

    def test_verify_allows_unpopulated_blank_optional_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dump_dir = Path(td)
            self._populate_basic_and_optional(dump_dir, include_blocks=True, blank_block_a=True)
            rep = verify_dump_directory(dump_dir, require_cpu=False)
            self.assertEqual(rep.sound_ok, 200)
            self.assertFalse(rep.emulator_6mb_ready)
            self.assertFalse(rep.has_errors)
            blk_a = next(r for r in rep.rows if r.row_name == "Sound Block A")
            self.assertTrue(blk_a.is_unpopulated)

    def test_verify_flags_corrupted_chip_and_missing_basic_rom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dump_dir = Path(td)
            self._populate_basic_and_optional(dump_dir, include_blocks=False)
            # Corrupt U106
            (dump_dir / "U106.bin").write_bytes(b"\x12" * 32768)
            rep = verify_dump_directory(dump_dir)
            self.assertTrue(rep.has_errors)
            self.assertFalse(rep.u106_ok)


if __name__ == "__main__":
    unittest.main()
