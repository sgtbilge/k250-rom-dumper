# Changelog

All notable changes to `k250-rom-dumper` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-25

### Added
- Standalone Motorola 68000 ROM Dumper firmware generator (`k250-dump build`) producing bit-exact `27C256` EPROM images (`K250_Dumper_U54.bin` / `K250_Dumper_U38.bin`) with 274 menu targets across 26 groups and 3 unattended super-batches (`ALL 246 ROMS`, `ALL 80 BASIC`, `ALL 160 BLOCKS`).
- Pre-built hardware-verified firmware binaries in `firmware/` with `CHECKSUMS.txt` (Kurzweil Checksum-16 and SHA-256).
- Full 248-chip factory ROM catalog (`240` Sound ROM 32 KB quarter slices across Row F, Row E, and Sound Blocks D/C/B/A + `8` CPU OS 6.1 EPROMs), including Diagnostic ROM Rev D `@0x665C` LSB checksum verification for all 48 `CPSOUND` 2-bit LSB slices.
- Cross-platform MIDI SysEx capture and `.syx` decoder (`k250-dump capture`, `k250-dump decode`, `k250-dump list-midi`) with zero-dependency macOS CoreMIDI support and optional `python-rtmidi` support for Windows/Linux.
- Dump completeness and health verifier (`k250-dump verify`) with optional Sound Block blank-socket (`0xFF`) detection and JSON reporting.
- 1-Mbit (128 KB) physical EPROM combiner and splitter (`k250-dump combine`, `k250-dump split`) and single-file inspector (`k250-dump info`).
- Interactive guided workflow (`k250-dump wizard`).
- Backwards-compatible wrappers in `tools/` for the original `build_rom_dumper.py` and `receive_rom_sysex.py` scripts.
- Comprehensive hardware, protocol, ROM map, troubleshooting, and legacy-wrapper documentation in `docs/`.
- GitHub Actions CI and tag-triggered release workflows.
