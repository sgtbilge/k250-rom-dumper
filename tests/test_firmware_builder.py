"""Tests for the standalone 68000 ROM Dumper firmware assembler."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from k250_rom_dumper.catalog import checksum16
from k250_rom_dumper.firmware_builder import (
    GOLDEN_FIRMWARE,
    MODE_BATCH,
    assemble_firmware_images,
    build_dumper_firmware,
    check_firmware_files,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = REPO_ROOT / "firmware"


class FirmwareBuilderTests(unittest.TestCase):
    def test_assembled_firmware_matches_golden_hashes_and_checksums(self) -> None:
        u54, u38, full_rom, listing, entries = assemble_firmware_images()
        self.assertEqual(len(entries), 274)
        self.assertGreater(len(listing), 100)

        actual = {
            "K250_Dumper_U54.bin": u54,
            "K250_Dumper_U38.bin": u38,
            "K250_Dumper_full.bin": full_rom,
        }
        for name, data in actual.items():
            golden = GOLDEN_FIRMWARE[name]
            self.assertEqual(len(data), golden["size"], name)
            self.assertEqual(checksum16(data), golden["checksum16"], name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), golden["sha256"], name)

    def test_menu_structure_has_26_groups_and_246_unique_chips(self) -> None:
        _, _, entries = build_dumper_firmware()
        groups = {grp for _, _, grp, _ in entries}
        self.assertEqual(len(groups), 26)
        singles = [name16.split()[0] for _, mode, _, name16 in entries if mode != MODE_BATCH]
        self.assertEqual(len(singles), 246)
        self.assertEqual(len(set(singles)), 246)

    def test_prebuilt_firmware_directory_matches_assembler(self) -> None:
        ok, msgs = check_firmware_files(FIRMWARE_DIR)
        self.assertTrue(ok, "\n".join(msgs))

    def test_check_firmware_files_detects_missing_or_modified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok, _ = check_firmware_files(Path(td))
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
