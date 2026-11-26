#!/usr/bin/env python3
"""Compatibility wrapper for building K250 ROM Dumper EPROM images."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from k250_rom_dumper.firmware_builder import main


if __name__ == "__main__":
    main(out_dir=Path(__file__).resolve().parent.parent / "firmware")
