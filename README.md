# Kurzweil K250 MIDI ROM Dumper

Dump every soldered-in Sound ROM and CPU OS EPROM from a live **Kurzweil K250** over standard **MIDI Out** — with **zero desoldering** and bit-exact factory checksum verification.

- **No desoldering required**: Replace only the two socketed CPU OS EPROMs (**`U54`** and **`U38`**) with two `27C256` EPROMs running this standalone 68000 firmware.
- **Full 6 MB Sound ROM coverage (240 / 240 slices)**: Reads all 4 quarters (`1-Mbit / 128 KB` per chip) across **Basic Sounds Row F & Row E** (including missing `U106`, checksum `0x0F81`) and **Sound Blocks D, C, B, and A** — including the 2-bit **`CPSOUND` (`0x1B1000`) LSB** chips — plus **CPU OS ROMs `U53/U37..U51/U35`**.
- **Unattended super-batches**: Power on and press **`YES`** to dump all **246 chips** (~90 min), **Basic Sounds** (`80` slices, ~29 min), or **Sound Blocks A–D** (`160` slices, ~58 min) unattended.
- **Zero-dependency macOS + Cross-platform**: Built-in `ctypes` CoreMIDI receiver on macOS (`0` external packages), optional `python-rtmidi` for Windows/Linux, and `.syx` decoding for SysEx Librarian / MIDI-OX / `amidi`.
- **Complete toolkit (`k250-dump`)**: Build/verify firmware, capture live, decode `.syx`, audit all 248 factory checksums, and combine 32 KB quarter slices into 128 KB (1-Mbit) physical EPROM images (or split back for `K250Emulator`).

---

## Quick Start

You need: a K250 you are willing to open, an EPROM programmer, two blank/erased `27C256` EPROMs, a MIDI interface with a MIDI IN port connected to your computer, and Python 3.9+.

### 1. Install the CLI

No third-party dependencies are needed on macOS or for `.syx` / `verify` / `combine` / `build`:

```bash
git clone https://github.com/sgtbilge/k250-rom-dumper.git
cd k250-rom-dumper
pip install -e .
```

On **Windows or Linux** (for live MIDI capture):

```bash
pip install -e ".[midi]"
```

> Or run without installing: `PYTHONPATH=src python3 -m k250_rom_dumper <command>`.

### 2. Burn the Dumper EPROMs (`U54` & `U38`)

Pre-built, hardware-verified binaries are included in [`firmware/`](firmware/):

- [`firmware/K250_Dumper_U54.bin`](firmware/K250_Dumper_U54.bin) -> **`27C256`** for socket **`U54`** (even bytes, Checksum-16 **`0xF017`**)
- [`firmware/K250_Dumper_U38.bin`](firmware/K250_Dumper_U38.bin) -> **`27C256`** for socket **`U38`** (odd bytes, Checksum-16 **`0x00ED`**)

Program both `27C256` chips and confirm each programmer buffer sum matches the value above (the firmware can be rebuilt/checked with `k250-dump build --check --out-dir firmware`). With the K250 powered off and unplugged, remove the original OS EPROMs from `U54` and `U38` (keep them safe; you need them to restore the machine), install the Dumper chips in those sockets with the same orientation as the neighbouring EPROMs, and connect **K250 MIDI OUT -> your computer's MIDI IN**. See [docs/HARDWARE_GUIDE.md](docs/HARDWARE_GUIDE.md) for the full step-by-step guide.

### 3. Capture ROMs Over MIDI

Start the receiver first, then power on the K250 and press the front-panel button:

```bash
# List MIDI input ports, then pick the index (or a name substring) of the one the K250 is on
k250-dump list-midi

# Live capture: writes one verified 32 KB .bin per chip plus a raw .syx backup. Ctrl+C when done.
k250-dump capture --port 0 --out-dir ./dumped_roms --save-syx session.syx
```

On the K250 press **`YES`** (or **`SELECT`**) to start `001/274 ALL 246 ROMS` (about 90 minutes). Each chip prints `FACTORY MATCH!` when its checksum agrees with the factory value. `k250-dump wizard` offers the same steps interactively.

Already recorded a `.syx` file in Snoize SysEx Librarian, MIDI-OX, or `amidi`?

```bash
k250-dump decode capture.syx --out-dir ./dumped_roms
```

### 4. Verify & Combine

```bash
# Audit all 240 Sound ROM slices + 8 CPU OS 6.1 EPROMs against factory checksums.
# Expect 6/8 CPU OS (U54/U38 hold the Dumper). Add --strict to exit non-zero unless all 240 Sound ROM slices pass.
k250-dump verify ./dumped_roms

# Combine 4 x 32 KB quarter slices into 128 KB (1-Mbit) physical EPROM images
k250-dump combine --in-dir ./dumped_roms --out-dir ./combined_roms
```

---

## CLI Reference (`k250-dump`)

| Command | Purpose |
| :--- | :--- |
| `k250-dump wizard` | Interactive setup and capture assistant |
| `k250-dump build` | Assemble `K250_Dumper_U54.bin`, `U38.bin`, `full.bin`, and `CHECKSUMS.txt` (`--check` verifies against release hashes) |
| `k250-dump list-midi` | List MIDI input ports (`--backend auto\|coremidi\|rtmidi`) |
| `k250-dump capture` | Live MIDI SysEx receiver with real-time packet & factory checksum verification |
| `k250-dump decode` | Decode one or more `.syx` files into verified 32 KB `.bin` files |
| `k250-dump verify` | Audit a dump directory across Row F, Row E, Sound Blocks D–A, and CPU OS 6.1 (`--json`, `--strict`, `--verbose`) |
| `k250-dump combine` | Combine 4 × 32 KB quarters into 128 KB (1-Mbit) physical chip images |
| `k250-dump split` | Split 128 KB (1-Mbit) physical chip images back into 32 KB quarter `.bin` files |
| `k250-dump info` | Inspect any `.bin` (32 KB / 128 KB) or `.syx` file and match against factory checksums |

---

## Repository Layout

```text
k250-rom-dumper/
├── firmware/                  # Pre-built U54/U38 27C256 EPROMs + CHECKSUMS.txt
├── src/k250_rom_dumper/       # Standalone 68000 assembler, SysEx codec, MIDI backends, verifier, combiner, CLI
├── tools/                     # Legacy-compatible wrappers for the original script names
├── docs/                      # Hardware guide, SysEx protocol spec, ROM map, troubleshooting
├── tests/                     # Unit tests for firmware, protocol, catalog, verifier, combiner, and CLI
└── pyproject.toml
```

---

## Documentation

- **[Hardware & Front-Panel Guide](docs/HARDWARE_GUIDE.md)** — EPROM requirements, burning, CPU board `U54`/`U38` installation, front-panel navigation, and batch timings.
- **[MIDI SysEx & Hardware Protocol](docs/PROTOCOL.md)** — Wire framing (`F0 7D 4B 32 50`), `CPSOUND` (`0x1B1000`) 2-bit LSB readback, `MC6840` 500 kHz baud clock, and Diagnostic Rev D LSB checksums.
- **[K250 ROM Map & Factory Checksums](docs/ROM_MAP.md)** — Complete 6 MB Sound ROM + CPU OS 6.1 memory map, 1-Mbit quarter addressing, and all 248 factory checksums.
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** — `MIDI ERR ST:xx` diagnostics, MIDI interface reliability, alternate capture tools, and blank Sound Block detection.
- **[Legacy Script Wrappers](docs/LEGACY_TOOLS.md)** — `build_rom_dumper.py` and `receive_rom_sysex.py`, kept in `tools/` with their original flags.

---

Finally, restore your original `U54` / `U38` chips (see [Hardware Guide](docs/HARDWARE_GUIDE.md#6-restore-original-eproms)). To back up those two OS chips, read them in your EPROM programmer.

## Using Dumps with `K250Emulator`

The author's K250 emulator reads the verified `.bin` set from the directory named by `K250_HARDWARE_DUMPS_DIR`:

```bash
export K250_HARDWARE_DUMPS_DIR=/path/to/dumped_roms
```

## License

MIT License — see [LICENSE](LICENSE). Note: Kurzweil K250 ROM contents are not included in this repository (only 16-bit checksums and firmware written for this project); this project provides only the dumper firmware and host tooling to back up ROMs from hardware you own.
