# Pre-built Kurzweil K250 ROM Dumper EPROM Images

These ready-to-burn binary images run directly from the Motorola 68000 CPU boot sockets **`U54`** (even bytes) and **`U38`** (odd bytes) on a Kurzweil K250.

## Files

| File | Target EPROM | Size | Checksum-16 | SHA-256 |
| :--- | :--- | :---: | :---: | :--- |
| `K250_Dumper_U54.bin` | `27C256` -> Socket **`U54`** | 32,768 B | `0xF017` | `3b9a0895405fec65dbc1b7e01b71a18c2a7a8961de7722df249e2b3b32c7c137` |
| `K250_Dumper_U38.bin` | `27C256` -> Socket **`U38`** | 32,768 B | `0x00ED` | `e14428d32f0ac634822d6a78d8bbfaa4c50ace7eb88df6ad8e93f844cb952475` |
| `K250_Dumper_full.bin` | Combined 64 KB image (emulator / inspection) | 65,536 B | `0xF104` | `e60f8c6a26ec781148c2af96fb9eddb7841e0035397d95cc9b8f87a9d98c6d04` |
| `CHECKSUMS.txt` | Machine-readable verification manifest | — | — | — |

## Quick Burning Checklist

1. Use two **`27C256`** EPROMs (32 KB). Speed grade is not specified by the original project; a fast part is a sensible choice.
2. Program **`K250_Dumper_U54.bin`** into chip 1, verify in your programmer that the 16-bit buffer checksum is **`F017`**, and label the chip **`U54`**.
3. Program **`K250_Dumper_U38.bin`** into chip 2, verify that the 16-bit buffer checksum is **`00ED`**, and label the chip **`U38`**.
4. Power off the K250, remove the original `U54` and `U38` OS EPROMs (note their orientation), and install the Dumper EPROMs the same way.

To verify or rebuild from source:

```bash
k250-dump build --check --out-dir firmware
```

See [../docs/HARDWARE_GUIDE.md](../docs/HARDWARE_GUIDE.md) for the complete installation and front-panel walkthrough.
