"""CLI integration tests for `k250-dump`."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from k250_rom_dumper.catalog import FACTORY_CATALOG
from k250_rom_dumper.cli import main as cli_main
from k250_rom_dumper.protocol import encode_chip_sysex

REPO_ROOT = Path(__file__).resolve().parent.parent


class CLITests(unittest.TestCase):
    def test_build_check_passes(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli_main(["build", "--check", "--out-dir", str(REPO_ROOT / "firmware")])
        self.assertEqual(rc, 0)
        self.assertIn("K250_Dumper_U54.bin", buf.getvalue())

    def test_decode_info_and_verify_json(self) -> None:
        # Build a valid U106 ROM image
        target = FACTORY_CATALOG["U106"].expected_checksum
        u106 = bytearray(32768)
        rem = target
        i = 0
        while rem > 0:
            step = min(0xFF, rem)
            u106[i] = step
            rem -= step
            i += 1

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            syx_path = root / "u106.syx"
            out_dir = root / "out"
            syx_path.write_bytes(encode_chip_sysex(4, 0, "U106 E38 L0     ", bytes(u106)))

            rc_info_syx = cli_main(["info", str(syx_path)])
            self.assertEqual(rc_info_syx, 0)

            rc_dec = cli_main(["decode", str(syx_path), "--out-dir", str(out_dir), "--quiet"])
            self.assertEqual(rc_dec, 0)
            self.assertTrue((out_dir / "U106.bin").is_file())

            rc_info_bin = cli_main(["info", str(out_dir / "U106.bin")])
            self.assertEqual(rc_info_bin, 0)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_ver = cli_main(["verify", str(out_dir), "--json"])
            self.assertEqual(rc_ver, 1)  # Basic Sounds incomplete
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["sound_ok"], 1)
            self.assertTrue(payload["u106_ok"])

    def test_tools_scripts_compile_cleanly(self) -> None:
        for script in (REPO_ROOT / "tools").glob("*.py"):
            compile(script.read_text(encoding="utf-8"), str(script), "exec")


if __name__ == "__main__":
    unittest.main()
