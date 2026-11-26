# Hardware & Front-Panel Guide

This guide walks through burning the Dumper EPROMs, installing them safely in a Kurzweil K250, capturing ROMs over MIDI, and restoring your machine afterward.

---

## 1. What You Need

- **2 × `27C256` EPROMs** (256 Kbit / 32 KB × 8). No speed grade has been characterised; use a fast part (the K250's own OS EPROMs are the safest reference).
- **EPROM programmer** that supports `27C256`, plus a UV eraser if reusing windowed chips.
- **MIDI interface** (USB-MIDI or similar) that passes long SysEx messages reliably (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).
- **1 × 5-pin DIN MIDI cable** from **K250 MIDI OUT** to your interface's **MIDI IN**.
- **Python 3.9+** with `k250-dump` installed (see the [README](../README.md)).

---

## 2. Burn & Verify the EPROM Pair

Program the two 32,768-byte images from [`firmware/`](../firmware/):

| File | Socket | Byte Lane | Programmer 16-bit Sum | SHA-256 (prefix) |
| :--- | :---: | :---: | :---: | :--- |
| `K250_Dumper_U54.bin` | **`U54`** | Even bytes (`D15:D8`) | **`0xF017`** | `3b9a0895405fec65...` |
| `K250_Dumper_U38.bin` | **`U38`** | Odd bytes (`D7:D0`) | **`0x00ED`** | `e14428d32f0ac634...` |

Always read back and verify each chip in your EPROM programmer, and label the chips **`U54`** and **`U38`** immediately so they are never swapped.

> Note: `U54` and `U38` are the even and odd byte halves of the 68000 program. If they are swapped the firmware will not run.

---

## 3. Install on the K250 CPU Board

1. **Power off and unplug** the Kurzweil K250 from AC mains, let it discharge, and ground yourself against ESD.
2. Open the instrument to access the **CPU Board**.
3. Locate the 8 CPU OS EPROM sockets: even bytes **`U54`**, `U53`, `U52`, `U51` and odd bytes **`U38`**, `U37`, `U36`, `U35`.
4. Carefully extract only **`U54`** and **`U38`** (your existing OS EPROMs) using an IC puller or gentle even leverage, and set them aside in anti-static foam.
   - **Leave `U53/U37`, `U52/U36`, and `U51/U35` installed** if you also want to dump them over MIDI.
5. Insert the Dumper **`U54`** and **`U38`** EPROMs into sockets `U54` and `U38`.
   - Orient the notch the same way as the neighbouring CPU EPROMs / the board silkscreen.
   - Inspect every pin to confirm none are bent under or overhanging the socket.
6. Connect **K250 MIDI OUT** to your USB-MIDI interface **MIDI IN**.

---

## 4. Start the Host Receiver

Start the receiver on your computer **before** pressing `YES` on the K250 (power on the K250 first so its MIDI port is quiet; the Dumper only transmits after you press a button):

```bash
k250-dump list-midi
k250-dump capture --port 0 --out-dir ./dumped_roms --save-syx session.syx
```

Or record with **Snoize SysEx Librarian** (macOS: *Record Many*), **MIDI-OX** (Windows), or `amidi` (Linux), save a `.syx` file, and decode afterward with:

```bash
k250-dump decode session.syx --out-dir ./dumped_roms
```

---

## 5. K250 Front-Panel Controls

On power-up, the Dumper initializes the audio DACs (muting all CGP channels so there is no pop/noise), configures the `MC6840` 500 kHz MIDI baud clock and `MC6850` ACIA (`31,250 bps 8N1`), lights the **`YES`**, **`SELECT`**, and **`NO`** LEDs, and displays the unattended full-dump batch on the 2×24 LCD:

```text
001/274 ALL 246 ROMS
BATCH   YES:DMP NO:GRP
```

### Button Map

| Front-Panel Button | Action |
| :--- | :--- |
| **`YES`** or **`SELECT`** | Start dumping the selected chip or batch (`SEND` LED lights; ~22 seconds per 32 KB slice) |
| **`UP ▲`** or **`RIGHT ▶`** | Next target (`001` -> `274`) |
| **`DOWN ▼`** or **`LEFT ◀`** | Previous target (`274` <- `001`) |
| **`NO`** (in menu) | Jump to the **next ROM group** (e.g. jump straight from the batches to the first single-chip group) |
| **`NO`** (hold briefly during dump) | Cleanly abort between 256-byte packets and send a SysEx `ABORT` frame |

### Menu Structure (274 Targets Across 26 Groups)

1. **Group 0 — Unattended Super-Batches (`001`–`003`)**:
   - **`001/274 ALL 246 ROMS`** (~90 min): Dumps all 80 Basic Sounds slices + 6 CPU OS EPROMs (`U53/U37..U51/U35`) + all 160 Sound Block D/C/B/A slices.
   - **`002/274 ALL 80 BASIC`** (`▶` once, ~29 min): Dumps all 8 quarters of Row E and Row F (2 MB Basic Sounds).
   - **`003/274 ALL 160 BLOCKS`** (`▶` twice, ~58 min): Dumps all 16 quarters of Sound Blocks D, C, B, and A (4 MB Sound Blocks).
2. **Group 1 — Basic Sounds Row E Quarter 2 (`0x380000`)**:
   - Pressing **`NO`** once from startup jumps to the first entry, **`004/274 U106 E38 L0`** (`CS:0F81`).
   - Followed by the remaining 9 chips of Row E Q2 and `014/274 ALL ROWE 38 (10)`.
3. **Groups 2–8 — Remaining Basic Sounds Quarters**:
   - Row F primary (`0x280000`, `U125..U116`), Row E `0x30/34/3C`, Row F `0x20/24/2C`. Each group ends with an `ALL ... (10)` batch entry.
4. **Group 9 — CPU OS EPROMs**:
   - `U53`, `U37`, `U52`, `U36`, `U51`, `U35`, plus `ALL CPU OS (6)`.
   - (`U54` and `U38` hold the Dumper firmware itself; read them on your EPROM programmer if you want to back up your original `U54`/`U38` OS chips.)
5. **Groups 10–25 — Sound Blocks D, C, B, and A** (`0x400000..0x7FFFFF`, 4 quarters each).
   - If a Sound Block is not installed in your K250, those addresses return open-bus/blank bytes (`0xFF`). `k250-dump verify` automatically recognizes unpopulated optional Sound Blocks.

---

## 6. Restore Original EPROMs

1. Verify your capture before opening the machine again:
   ```bash
   k250-dump verify ./dumped_roms
   ```
2. Power off the K250, unplug AC mains, remove the Dumper `U54` / `U38` EPROMs, and reinstall your original OS `U54` / `U38` EPROMs (same orientation as removed).
3. Power on and confirm the K250 boots normally into its OS.
