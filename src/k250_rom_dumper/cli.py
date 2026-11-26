"""Unified CLI (`k250-dump`) for building, capturing, decoding, verifying, and combining K250 ROMs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .catalog import (
    FACTORY_CATALOG,
    PHYSICAL_SOCKETS,
    checksum16,
    diag_lsb_checksum,
    identify_rom_bytes,
)
from .combiner import combine_quarters, split_mbit_roms
from .firmware_builder import build_firmware_files, check_firmware_files
from .midi_io import list_midi_sources, run_live_capture
from .protocol import DumperDecoder, inspect_syx_file
from .verifier import format_verification_report, verify_dump_directory


def _cmd_build(args: argparse.Namespace) -> int:
    if args.check:
        ok, msgs = check_firmware_files(args.out_dir)
        for m in msgs:
            print(m)
        return 0 if ok else 1

    res = build_firmware_files(
        args.out_dir,
        write_listing=args.listing,
        write_manifest=not args.no_manifest,
    )
    print(f"Built K250 ROM Dumper firmware ({res['entry_count']} ROM targets -> {args.out_dir}):")
    print(f"  U54 (even): {res['u54_path']}  (32768 B, CS16=0x{res['u54_cs16']:04X}, SHA256={res['u54_sha256'][:16]}...)")
    print(f"  U38 (odd) : {res['u38_path']}  (32768 B, CS16=0x{res['u38_cs16']:04X}, SHA256={res['u38_sha256'][:16]}...)")
    print(f"  Full 64KB : {res['full_path']}  (65536 B, CS16=0x{res['full_cs16']:04X})")
    if res["manifest_path"]:
        print(f"  Manifest  : {res['manifest_path']}")
    if res["lst_path"]:
        print(f"  Listing   : {res['lst_path']}")
    return 0


def _cmd_list_midi(args: argparse.Namespace) -> int:
    try:
        sources = list_midi_sources(backend=args.backend)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print("No MIDI input sources detected.")
        return 0
    print(f"Available MIDI Input Sources ({sources[0].backend}):")
    for src in sources:
        print(f"  [{src.index}] {src.name}")
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    decoder = DumperDecoder(args.out_dir, verbose=not args.quiet)
    try:
        return run_live_capture(
            port_arg=args.port,
            decoder=decoder,
            save_syx=args.save_syx,
            backend=args.backend,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_decode(args: argparse.Namespace) -> int:
    decoder = DumperDecoder(args.out_dir, verbose=not args.quiet)
    for syx_path in args.syx_files:
        if not syx_path.is_file():
            print(f"Error: file not found: {syx_path}", file=sys.stderr)
            return 1
        decoder.feed_stream_bytes(syx_path.read_bytes())
    print(f"\nSummary: {len(decoder.completed)} chip(s) decoded cleanly, {len(decoder.errors)} error(s).")
    if decoder.errors:
        for err in decoder.errors[:20]:
            print(f"  ERROR: {err}", file=sys.stderr)
    return 0 if decoder.completed and not decoder.errors else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    if not args.dump_dir.is_dir():
        print(f"Error: directory not found: {args.dump_dir}", file=sys.stderr)
        return 1
    report = verify_dump_directory(args.dump_dir, require_cpu=args.require_cpu)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_verification_report(report, verbose=args.verbose))

    if report.has_errors:
        return 1
    if args.strict and not report.sound_set_complete:
        return 1
    return 0


def _cmd_combine(args: argparse.Namespace) -> int:
    try:
        records = combine_quarters(
            in_dir=args.in_dir,
            out_dir=args.out_dir,
            rows=args.rows,
            skip_verify=args.skip_verify,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Combined {len(records)} physical 1-Mbit (128 KB) ROM(s) -> {args.out_dir}")
    print(f"Manifest written to {args.out_dir / '1MBIT_CHECKSUMS.txt'}")
    return 0


def _cmd_split(args: argparse.Namespace) -> int:
    try:
        written = split_mbit_roms(
            in_dir=args.in_dir,
            out_dir=args.out_dir,
            rows=args.rows,
            skip_verify=args.skip_verify,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Split {len(written)} 32 KB quarter ROM(s) -> {args.out_dir}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    path: Path = args.path
    if not path.is_file():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    if path.suffix.lower() == ".syx":
        summary = inspect_syx_file(path)
        print(f"SysEx File: {summary['file']}")
        print(f"  Size              : {summary['bytes']} bytes")
        print(f"  SysEx messages    : {summary['total_sysex_messages']} ({summary['k250_dumper_messages']} K250 Dumper)")
        print(f"  START / DATA / END: {summary['starts']} / {summary['data_packets']} / {summary['ends']} (aborts={summary['aborts']})")
        chips = summary["chips"]
        if chips:
            preview = ", ".join(chips[:12]) + (f" ... (+{len(chips) - 12} more)" if len(chips) > 12 else "")
            print(f"  Chips             : {preview}")
        return 0

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    cs16 = checksum16(data)
    print(f"ROM File: {path}")
    print(f"  Size       : {len(data)} bytes")
    print(f"  Checksum-16: 0x{cs16:04X}")
    print(f"  SHA-256    : {sha}")

    if len(data) == 32768:
        dlsb = diag_lsb_checksum(data)
        print(f"  DiagLSB CS : 0x{dlsb:04X}")
        matches = identify_rom_bytes(data)
        if matches:
            print("  Factory Match(es):")
            for m in matches:
                kind = "LSB" if m.is_lsb else "MSB/CPU"
                print(f"    - {m.token:<10s} [{m.row_name}, {m.role}, {kind}] @ 0x{m.quarter_addr:06X}")
        else:
            print("  Factory Match(es): None")
    elif len(data) == 131072:
        print("  1-Mbit Quarter Analysis:")
        for q_idx in range(4):
            q_bytes = data[q_idx * 32768 : (q_idx + 1) * 32768]
            matches = identify_rom_bytes(q_bytes)
            m_str = ", ".join(f"{m.token} ({m.row_name})" for m in matches) if matches else "Unknown"
            print(f"    Q{q_idx} (+0x{q_idx * 0x40000:05X}): CS16=0x{checksum16(q_bytes):04X} -> {m_str}")
    return 0


def _cmd_wizard(_args: argparse.Namespace) -> int:
    print("Kurzweil K250 ROM Dumper — Interactive Setup Wizard")
    print("=" * 56)
    print("What would you like to do?")
    print("  1) Build or verify U54 / U38 dumper EPROM binaries")
    print("  2) Capture ROMs live from a K250 over MIDI")
    print("  3) Decode an existing .syx capture file (e.g. from SysEx Librarian)")
    print("  4) Verify a directory of dumped .bin files")
    print("  5) Combine 32 KB quarter dumps into 128 KB (1-Mbit) chip images")
    choice = input("\nEnter choice [1-5]: ").strip()

    if choice == "1":
        out_str = input("Output directory [./firmware]: ").strip() or "./firmware"
        ns = argparse.Namespace(out_dir=Path(out_str), listing=False, no_manifest=False, check=False)
        return _cmd_build(ns)

    if choice == "2":
        try:
            sources = list_midi_sources("auto")
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not sources:
            print("No MIDI input sources found. Connect your USB-MIDI interface and retry.")
            return 1
        print("\nDetected MIDI Input Ports:")
        for src in sources:
            print(f"  [{src.index}] {src.name}")
        port_arg = input("Select port index [0]: ").strip() or "0"
        out_dir = Path(input("Directory to save decoded .bin ROMs [./dumped_roms]: ").strip() or "./dumped_roms")
        syx_in = input("Optional raw .syx backup path [./session.syx]: ").strip() or "./session.syx"
        ns = argparse.Namespace(
            port=port_arg,
            out_dir=out_dir,
            save_syx=Path(syx_in) if syx_in else None,
            backend="auto",
            quiet=False,
        )
        return _cmd_capture(ns)

    if choice == "3":
        syx_path = Path(input("Path to .syx file: ").strip())
        out_dir = Path(input("Output directory for .bin ROMs [./dumped_roms]: ").strip() or "./dumped_roms")
        ns = argparse.Namespace(syx_files=[syx_path], out_dir=out_dir, quiet=False)
        return _cmd_decode(ns)

    if choice == "4":
        dump_dir = Path(input("Directory of dumped .bin files [./dumped_roms]: ").strip() or "./dumped_roms")
        ns = argparse.Namespace(dump_dir=dump_dir, strict=False, require_cpu=False, json=False, verbose=True)
        return _cmd_verify(ns)

    if choice == "5":
        in_dir = Path(input("Directory of 32 KB quarter .bin files [./dumped_roms]: ").strip() or "./dumped_roms")
        out_dir = Path(input("Output directory for 128 KB 1-Mbit images [./combined_roms]: ").strip() or "./combined_roms")
        ns = argparse.Namespace(in_dir=in_dir, out_dir=out_dir, rows=None, skip_verify=False)
        return _cmd_combine(ns)

    print("Invalid selection.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="k250-dump",
        description="Kurzweil K250 MIDI ROM Dumper: firmware builder, SysEx receiver, verifier, and 1-Mbit combiner.",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    p_wiz = sub.add_parser("wizard", help="Interactive step-by-step setup & capture assistant")
    p_wiz.set_defaults(func=_cmd_wizard)

    p_build = sub.add_parser("build", help="Build (or verify) U54/U38 27C256 EPROM images")
    p_build.add_argument("--out-dir", type=Path, default=Path("./firmware"), help="Output directory (default: ./firmware)")
    p_build.add_argument("--listing", action="store_true", help="Also write K250_Dumper.lst 68000 disassembly listing")
    p_build.add_argument("--no-manifest", action="store_true", help="Do not write CHECKSUMS.txt")
    p_build.add_argument("--check", action="store_true", help="Verify existing binaries in --out-dir match freshly built output")
    p_build.set_defaults(func=_cmd_build)

    p_list = sub.add_parser("list-midi", help="List available MIDI input ports")
    p_list.add_argument("--backend", choices=("auto", "coremidi", "rtmidi"), default="auto", help="MIDI backend")
    p_list.set_defaults(func=_cmd_list_midi)

    p_cap = sub.add_parser("capture", help="Capture live MIDI SysEx ROM dumps from a K250")
    p_cap.add_argument("--port", help="MIDI input port index or name substring")
    p_cap.add_argument("--out-dir", type=Path, default=Path("./dumped_roms"), help="Output directory for 32 KB .bin files")
    p_cap.add_argument("--save-syx", type=Path, help="Also save raw captured MIDI stream to a .syx file")
    p_cap.add_argument("--backend", choices=("auto", "coremidi", "rtmidi"), default="auto", help="MIDI backend")
    p_cap.add_argument("--quiet", action="store_true", help="Suppress per-packet progress output")
    p_cap.set_defaults(func=_cmd_capture)

    p_dec = sub.add_parser("decode", help="Decode one or more saved .syx capture files into verified 32 KB .bin files")
    p_dec.add_argument("syx_files", nargs="+", type=Path, help="Input .syx file(s)")
    p_dec.add_argument("--out-dir", type=Path, default=Path("./dumped_roms"), help="Output directory for 32 KB .bin files")
    p_dec.add_argument("--quiet", action="store_true", help="Suppress per-packet progress output")
    p_dec.set_defaults(func=_cmd_decode)

    p_ver = sub.add_parser("verify", help="Audit a directory of dumped ROMs against all 240+8 factory checksums")
    p_ver.add_argument("dump_dir", type=Path, nargs="?", default=Path("./dumped_roms"), help="Directory containing dumped .bin files")
    p_ver.add_argument("--strict", action="store_true", help="Exit non-zero unless all 240 Sound ROM slices verify")
    p_ver.add_argument("--require-cpu", action="store_true", help="Require all 8 CPU OS 6.1 EPROMs to be present")
    p_ver.add_argument("--verbose", action="store_true", help="List individual missing Sound ROM slices")
    p_ver.add_argument("--json", action="store_true", help="Emit JSON report")
    p_ver.set_defaults(func=_cmd_verify)

    p_comb = sub.add_parser("combine", help="Combine 4 x 32 KB quarter slices into 128 KB (1-Mbit) physical EPROM images")
    p_comb.add_argument("--in-dir", type=Path, default=Path("./dumped_roms"), help="Directory of 32 KB quarter .bin files")
    p_comb.add_argument("--out-dir", type=Path, default=Path("./combined_roms"), help="Output directory for 128 KB .bin files")
    p_comb.add_argument("--rows", nargs="+", help="Filter rows/blocks (e.g. F E D C B A)")
    p_comb.add_argument("--skip-verify", action="store_true", help="Skip factory checksum check before combining")
    p_comb.set_defaults(func=_cmd_combine)

    p_spl = sub.add_parser("split", help="Split 128 KB (1-Mbit) physical EPROM images back into 4 x 32 KB quarters")
    p_spl.add_argument("--in-dir", type=Path, default=Path("./combined_roms"), help="Directory of 128 KB .bin files")
    p_spl.add_argument("--out-dir", type=Path, default=Path("./dumped_roms"), help="Output directory for 32 KB quarter .bin files")
    p_spl.add_argument("--rows", nargs="+", help="Filter rows/blocks (e.g. F E D C B A)")
    p_spl.add_argument("--skip-verify", action="store_true", help="Skip factory checksum check while splitting")
    p_spl.set_defaults(func=_cmd_split)

    p_info = sub.add_parser("info", help="Inspect a .bin (32 KB / 128 KB) or .syx file")
    p_info.add_argument("path", type=Path, help="Path to .bin or .syx file")
    p_info.set_defaults(func=_cmd_info)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "command", None):
        ap.print_help()
        return 0
    return args.func(args)
