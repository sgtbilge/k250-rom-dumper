#!/usr/bin/env python3
"""Backwards-compatible CLI wrapper matching the legacy `receive_rom_sysex.py` interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from k250_rom_dumper.midi_io import list_midi_sources, run_live_capture
from k250_rom_dumper.protocol import DumperDecoder


def main() -> int:
    ap = argparse.ArgumentParser(description="Kurzweil K250 ROM Dumper MIDI SysEx Receiver")
    ap.add_argument("--syx", type=Path, help="Decode saved .syx file")
    ap.add_argument("--live", action="store_true", help="Listen live via MIDI")
    ap.add_argument("--port", help="MIDI input port index or substring (with --live)")
    ap.add_argument("--list-midi", action="store_true", help="List available MIDI input sources")
    ap.add_argument("--backend", choices=("auto", "coremidi", "rtmidi"), default="auto", help="MIDI backend")
    ap.add_argument("--save-syx", type=Path, help="Also save raw received MIDI bytes to .syx (with --live)")
    ap.add_argument("--out-dir", type=Path, default=Path("./dumped_roms"), help="Output directory for decoded 32KB .bin files")
    args = ap.parse_args()

    if args.list_midi:
        sources = list_midi_sources(backend=args.backend)
        if not sources:
            print("No MIDI input sources detected.")
        else:
            print(f"MIDI Input Sources ({sources[0].backend}):")
            for src in sources:
                print(f"  [{src.index}] {src.name}")
        return 0

    decoder = DumperDecoder(args.out_dir)

    if args.syx:
        data = args.syx.read_bytes()
        decoder.feed_stream_bytes(data)
        print(f"\nSummary: {len(decoder.completed)} chip(s) decoded cleanly, {len(decoder.errors)} error(s).")
        return 0 if decoder.completed and not decoder.errors else 1

    if args.live:
        return run_live_capture(args.port, decoder, args.save_syx, backend=args.backend)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
