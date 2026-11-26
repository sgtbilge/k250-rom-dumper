# Companion Tools (`tools/`)

In addition to the main `k250-dump` CLI, the `tools/` directory provides backwards-compatible scripts and Sound ROM forensics utilities from the author's K250 emulator project.

## 1. Legacy ROM Dumper Wrappers

- **`tools/build_rom_dumper.py`**: Rebuilds `firmware/K250_Dumper_U54.bin`, `U38.bin`, `full.bin`, `CHECKSUMS.txt`, and `K250_Dumper.lst`.
- **`tools/receive_rom_sysex.py`**: Preserves the original CLI flags (`--list-midi`, `--live`, `--port`, `--syx`, `--save-syx`, `--out-dir`) backed by `k250_rom_dumper`.

## 2. `tools/reconstruct_sound_blocks.py`

Reconstructs historical **CR-stripped (`0x0D` deleted)** community EPROM archives for **Sound Block D** (`107K250..098K250`) and **Sound Block C** (`086K250..077K250`) using deterministic metadata alignment and a compiled C11 exact-count coupled even/odd Viterbi solver.

> Note: If you have a real K250 with Sound Blocks C/D installed, dumping them directly with `k250-dump` gives bit-exact hardware dumps without needing reconstruction. This tool is for analyzing or repairing legacy CR-stripped archives.

Requirements: a C compiler (`cc`) on `PATH`.

```bash
python3 tools/reconstruct_sound_blocks.py \
  --roms-dir "/path/to/K250 Sounds Binaries" \
  --block all
```

Flags:
- `--roms-dir`: Base folder containing `K250 Sound Block D` and `K250 Sound Block C` (or set `K250_SOUNDS_BINARIES_DIR`).
- `--block {all,d,c}`: Reconstruct Block D, Block C, or both.
- `--block-d-in` / `--block-c-in` / `--block-d-out` / `--block-c-out`: Override individual directories.
- `--cc`: Specify C compiler binary (default: `cc`).

## 3. `tools/export_waveforms.py`

Exports raw 10-bit CGP DMA waveforms from K250 Sound ROM layouts (25,000 Hz signed PCM) as:
- `*_full.wav` and `*_looped.wav`
- `*.csv` sample tables
- 1400×760 diagnostic `.png` plots highlighting `0x0D` runs and segment boundaries
- Self-contained dark-mode `index.html` waveform gallery

Requirements: `Pillow` (`pip install -e ".[waveforms]"`).

```bash
python3 tools/export_waveforms.py \
  --roms-dir "/path/to/K250 Sounds Binaries" \
  --out-dir ./ExportedWaveforms
```
