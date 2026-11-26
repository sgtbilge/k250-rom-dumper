# MIDI SysEx & Hardware Protocol Specification

This document describes the wire-level MIDI SysEx protocol emitted by the `k250-rom-dumper` 68000 firmware and the K250 hardware sequences used to read Sound ROMs (including 2-bit `CPSOUND` LSBs).

---

## 1. SysEx Framing

All messages use the Universal Non-Commercial Manufacturer ID **`0x7D`** followed by ASCII **`K2P`** (`0x4B 0x32 0x50`):

```text
F0 7D 4B 32 50 <packet_type> <payload...> F7
```

Every payload byte is strictly **7-bit safe** (`0x00..0x7F`). Binary ROM bytes are transmitted as high-nibble then low-nibble (`0x00..0x0F`).

### `0x01` — START (30 bytes total)

Sent once before a 32 KB ROM slice begins:

| Offset | Length | Field | Description |
| :---: | :---: | :--- | :--- |
| `0` | 1 | `0xF0` | SysEx start |
| `1..4` | 4 | `7D 4B 32 50` | Magic header (`7D 'K' '2' 'P'`) |
| `5` | 1 | `0x01` | Packet type = `START` |
| `6` | 1 | `chip_id` | 7-bit target index (`idx & 0x7F`) |
| `7` | 1 | `mode` | `0` = MSB / CPU (`stride +2`), `1` = `CPSOUND` 2-bit LSB |
| `8..23` | 16 | `name16` | Space-padded 16-char ASCII label (first token becomes `<TOKEN>.bin`) |
| `24..27` | 4 | `pre_csum[4]` | Pre-computed 16-bit unsigned byte sum as 4 nibbles (big-endian) |
| `28` | 1 | `chk7` | `sum(bytes[5..27]) & 0x7F` |
| `29` | 1 | `0xF7` | SysEx end |

### `0x02` — DATA (526 bytes total, 128 packets per chip)

Each packet carries **256 ROM bytes** (`seq = 0..127`, totaling `128 × 256 = 32,768` bytes):

| Offset | Length | Field | Description |
| :---: | :---: | :--- | :--- |
| `0..4` | 5 | `F0 7D 4B 32 50` | SysEx header + magic |
| `5` | 1 | `0x02` | Packet type = `DATA` |
| `6` | 1 | `chip_id` | Matches `START.chip_id` |
| `7..8` | 2 | `seq_hi`, `seq_lo` | Packet sequence `0..127` as 2 nibbles (`(seq >> 4) & 0xF`, `seq & 0xF`) |
| `9..520` | 512 | `nibbles[512]` | 256 bytes encoded as `(byte >> 4) & 0xF`, `byte & 0xF` |
| `521..524` | 4 | `pkt_csum[4]` | `sum(256_raw_bytes) & 0xFFFF` as 4 nibbles (big-endian) |
| `525` | 1 | `0xF7` | SysEx end |

There is a ~6.2 ms inter-packet delay on the K250 between `DATA` packets so USB-MIDI interfaces never drop bytes (~22 seconds per 32 KB slice).

### `0x03` — END (13 bytes total)

Sent after all 128 `DATA` packets complete:

| Offset | Length | Field | Description |
| :---: | :---: | :--- | :--- |
| `0..4` | 5 | `F0 7D 4B 32 50` | SysEx header + magic |
| `5` | 1 | `0x03` | Packet type = `END` |
| `6` | 1 | `chip_id` | Matches `START.chip_id` |
| `7..10` | 4 | `end_csum[4]` | Running 16-bit byte sum of transmitted bytes as 4 nibbles |
| `11` | 1 | `chk7` | `sum(bytes[5..10]) & 0x7F` |
| `12` | 1 | `0xF7` | SysEx end |

The host decoder verifies:
1. All 128 `DATA` packets (`seq = 0..127`) arrived with valid per-packet 16-bit checksums.
2. `sum(32768_bytes) & 0xFFFF == START.pre_csum == END.end_csum`.
3. Factory checksum matches the 248-chip catalog (using `Checksum-16` for MSB/CPU slices and Diagnostic Rev D `@0x665C` for `CPSOUND` LSB slices).

### `0x04` — ABORT (8 bytes total)

Sent if the user presses **`NO`** during a transfer:

```text
F0 7D 4B 32 50 04 <chip_id> F7
```

---

## 2. K250 Hardware Sequences

### MIDI Baud Clock (`MC6840` PTM + `MC6850` ACIA)

On cold boot, the `MC6850` MIDI ACIA (`0x1D0001`/`0x1D0003`) has **no external TX/RX clock** until the `MC6840` Programmable Timer Module (`0x1C0001..0x1C000F`) is initialized with the Diagnostic ROM Rev D sequence (`@00CD6`):
- Timer 2 (`O2`) generates the **500 kHz** clock (`31,250 bps × 16`) for `MC6850` `TxC`/`RxC`.
- The firmware then resets `MC6850` (`0x03`) and sets `0x15` (`divide-by-16`, `8N1`, interrupts disabled, polled TDRE bit 1).
- If `TDRE` does not assert within ~1.5 seconds, the firmware aborts and displays `MIDI ERR ST:xx` with the raw `0x1D0001` status byte.

### Reading 2-bit `CPSOUND` LSB ROMs (`0x1B1000`)

Each 256 KB Sound ROM quarter stores 8 MSB ROMs (`L0..L3` at even bus addresses, `R0..R3` at odd bus addresses) plus 2 `CPSOUND` 2-bit LSB ROMs (`LSB0` covering `q=0..1`, `LSB1` covering `q=2..3`).

To reconstruct one 8-bit byte of an LSB ROM at word index `idx` (`0..32767`):
1. `a0 = quarter_base + 2 * idx`, `a1 = a0 + 0x10000` (partner quarter `+64 KB` bus / `+32 KB` samples).
2. For each of the 4 sample pairs `((a0), 1(a0), (a1), 1(a1))`:
   - Write word `0x0000` to `ADDR_CPSOUND` (`0x1B1000`) -> sets `SOUND = 1`.
   - Read byte from `a0`/`a1` -> latches the 2 LSBs of that sample into the `CPSOUND` read register.
   - Read word from `ADDR_CPSOUND` (`0x1B1000`) -> clears `SOUND = 0` and returns `D7` (even bit) and `D6` (odd bit).
3. Pack the 4 pairs into `[b0..b7]` of the reconstructed LSB EPROM byte.

### Diagnostic ROM Rev D `@0x665C` LSB Checksum

In Kurzweil Diagnostic ROM Rev D, LSB EPROM checksums are **not** plain byte sums; instead, each byte `b` is bit-reversed (`b -> rev`), split into high and low nibbles, and summed:

```python
rev = int(f"{b:08b}"[::-1], 2)
total = (total + (rev & 0xF0) + (rev & 0x0F)) & 0xFFFF
```

`k250-rom-dumper` implements this in [`diag_lsb_checksum()`](../src/k250_rom_dumper/catalog.py) and verifies all 48 `CPSOUND` LSB slices automatically.
