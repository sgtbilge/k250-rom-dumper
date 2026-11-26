# Troubleshooting

## K250 Front Panel Issues

### Blank LCD or Solid Blocks on Power-Up
- **Swapped `U54` and `U38`**: `U54` holds even bytes (`D15:D8`) and `U38` holds odd bytes (`D7:D0`). Power off and swap them if reversed.
- **Wrong chip orientation**: Confirm the notch matches the neighbouring CPU EPROMs / board silkscreen.
- **Bent pin or slow EPROM**: Check that no pin folded underneath during insertion and try a faster `27C256`.
- **Wrong programmer checksum**: Re-verify in your programmer that `U54` sums to `0xF017` and `U38` sums to `0x00ED`.

### `MIDI ERR ST:xx` on LCD
The `MC6850` ACIA failed to report Transmit Data Register Empty (`TDRE`, bit 1) within ~1.5 seconds. `ST:xx` displays the raw status byte read from `0x1D0001`:
- **`ST:00` or `ST:01`** (bit 1 = `0`): Check `MC6840` PTM / `MC6850` clock lines (`O2` 500 kHz TxC/RxC) or verify `U54`/`U38` are seated cleanly.
- If the problem persists across resets, run Kurzweil **Diagnostic ROM Rev D** MIDI loopback / ACIA tests.

---

## MIDI Capture & USB-MIDI Issues

### Missing Packets or `DATA packet wrong length`
Some MIDI interfaces drop or truncate multi-hundred-byte SysEx packets.
- The Dumper already adds a ~6.2 ms delay between each 526-byte `DATA` packet, to help USB-MIDI interfaces keep up.
- If packets are missing or truncated, try a different (preferably class-compliant, known-good) MIDI interface; very cheap adapters are a common source of dropped SysEx.
- Always pass `--save-syx session.syx` when capturing so you have a raw capture backup if you need to inspect or re-decode.

### `No MIDI input sources found` or `python-rtmidi` Missing
- **macOS**: Uses CoreMIDI directly via `ctypes` with **zero dependencies**. Confirm your USB-MIDI interface shows up in **Audio MIDI Setup -> MIDI Studio**.
- **Windows / Linux**: Install the optional MIDI extra:
  ```bash
  pip install "k250-rom-dumper[midi]"
  ```
- **Alternative on any OS**: Capture with a dedicated SysEx utility and decode afterward with `k250-dump decode capture.syx`:
  - **macOS**: [Snoize SysEx Librarian](https://www.snoize.com/SysExLibrarian/) (*Record Many*)
  - **Windows**: [MIDI-OX](http://www.midiox.com/) (record the incoming SysEx to a `.syx` file)
  - **Linux**: `amidi -p hw:1,0,0 -r capture.syx`

---

## Verification & Sound Block Questions

### Sound Blocks D / C / B / A Show `0xFF` or `NOT INSTALLED`
That is completely normal if your physical K250 does not have those optional Sound Blocks installed on the CGP board. Unpopulated sockets read `0xFF` across all 32,768 bytes, which `k250-dump verify` reports as `NOT INSTALLED (optional)` rather than a corruption error.

### `CPU OS 6.1` Shows `6/8 OK (U54/U38 hold Dumper)`
During MIDI dumping, sockets `U54` and `U38` contain the Dumper EPROMs rather than your original OS 6.1 EPROMs, so the K250 only exposes `U53/U37`, `U52/U36`, and `U51/U35` on the bus. If you want a complete 8/8 OS backup, read your original `U54` and `U38` chips directly on your EPROM programmer.

### Non-6.1 OS Versions
The 8 CPU OS checksums in `verify` correspond specifically to **OS 6.1**. A machine with a different OS version will report CPU OS mismatches; the Sound ROM checksums are unaffected, and no other OS version has been tested.
