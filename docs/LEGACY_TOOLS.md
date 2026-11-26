# Legacy Script Wrappers (`tools/`)

In addition to the main `k250-dump` CLI, the `tools/` directory provides wrappers that keep the original script names and flags working.

- **`tools/build_rom_dumper.py`**: Rebuilds `firmware/K250_Dumper_U54.bin`, `U38.bin`, `full.bin`, `CHECKSUMS.txt`, and `K250_Dumper.lst`.
- **`tools/receive_rom_sysex.py`**: Preserves the original CLI flags (`--list-midi`, `--live`, `--port`, `--syx`, `--save-syx`, `--out-dir`) backed by `k250_rom_dumper`.
