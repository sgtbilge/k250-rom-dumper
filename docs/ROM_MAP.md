# Kurzweil K250 ROM Map & Factory Checksums

## 1. Memory Map Overview

| Region | Address Range | Contents | Physical Chips |
| :--- | :--- | :--- | :--- |
| CPU OS ROM | `0x000000..0x03FFFF` | OS 6.1 (4 × 64 KB even/odd pairs) | 8 × `27C256` (`U54/U38`, `U53/U37`, `U52/U36`, `U51/U35`) |
| Sound Row F | `0x200000..0x2FFFFF` | Basic Sounds 1 | 10 × `1-Mbit` (`U125..U116`) = 40 × 32 KB slices |
| Sound Row E | `0x300000..0x3FFFFF` | Basic Sounds 2 | 10 × `1-Mbit` (`U107..U98`) = 40 × 32 KB slices |
| Sound Block D | `0x400000..0x4FFFFF` | Optional Sound Block D | 10 × `1-Mbit` (`U86..U77`) = 40 × 32 KB slices |
| Sound Block C | `0x500000..0x5FFFFF` | Optional Sound Block C | 10 × `1-Mbit` (`U66..U57`) = 40 × 32 KB slices |
| Sound Block B | `0x600000..0x6FFFFF` | Optional Sound Block B | 10 × `1-Mbit` (`U47..U38`) = 40 × 32 KB slices |
| Sound Block A | `0x700000..0x7FFFFF` | Optional Sound Block A | 10 × `1-Mbit` (`U20..U11`) = 40 × 32 KB slices |

---

## 2. How 1-Mbit Sound ROM Quarters Work

Real K250 Sound ROMs are **1-Mbit (128 KB × 8)** ROMs. Each 1 MB Sound Row divides into **4 × 256 KB quarters** selected by bus lines `A19:A18`:

- **Q0**: `row_base + 0x00000`
- **Q1**: `row_base + 0x40000`
- **Q2**: `row_base + 0x80000`
- **Q3**: `row_base + 0xC0000`

Inside each 256 KB quarter, the 10 sockets map in standard K250 order:

| Index | Role | Bus Access | Checksum Algorithm |
| :---: | :--- | :--- | :--- |
| 0 | `LSB0` | `CPSOUND` (`0x1B1000`) for `q=0,1` | Diagnostic Rev D `@0x665C` (`diag_lsb_checksum`) |
| 1 | `L0` | Even bytes, `+0x00000` | Kurzweil Checksum-16 (`sum & 0xFFFF`) |
| 2 | `L1` | Even bytes, `+0x10000` | Kurzweil Checksum-16 |
| 3 | `L2` | Even bytes, `+0x20000` | Kurzweil Checksum-16 |
| 4 | `L3` | Even bytes, `+0x30000` | Kurzweil Checksum-16 |
| 5 | `LSB1` | `CPSOUND` (`0x1B1000`) for `q=2,3` | Diagnostic Rev D `@0x665C` (`diag_lsb_checksum`) |
| 6 | `R0` | Odd bytes, `+0x00001` | Kurzweil Checksum-16 |
| 7 | `R1` | Odd bytes, `+0x10001` | Kurzweil Checksum-16 |
| 8 | `R2` | Odd bytes, `+0x20001` | Kurzweil Checksum-16 |
| 9 | `R3` | Odd bytes, `+0x30001` | Kurzweil Checksum-16 |

Use `k250-dump combine` to concatenate `[Q0, Q1, Q2, Q3]` of each physical socket back into its 128 KB (131,072-byte) physical chip image, or `k250-dump split` to break 128 KB chip images into 32 KB quarter files.

---

## 3. CPU OS 6.1 Factory Checksums (8 EPROMs)

| Socket | Lane | Address | Checksum-16 |
| :---: | :--- | :---: | :---: |
| `U54` | Even | `0x000000` | `0x72F7` |
| `U38` | Odd | `0x000001` | `0xD312` |
| `U53` | Even | `0x010000` | `0x178A` |
| `U37` | Odd | `0x010001` | `0x940F` |
| `U52` | Even | `0x020000` | `0xE478` |
| `U36` | Odd | `0x020001` | `0x6B0A` |
| `U51` | Even | `0x030000` | `0x3C38` |
| `U35` | Odd | `0x030001` | `0x88AE` |

*(Note: `U54` and `U38` hold the ROM Dumper itself during dumping, so live MIDI dumps capture the 6 remaining CPU OS ROMs `U53/U37..U51/U35`.)*

---

## 4. Sound ROM Factory Checksums (240 Slices = 6 MB)

For LSB columns (`LSB0` and `LSB1`), checksums are Diagnostic Rev D `@0x665C` values; all other columns are standard 16-bit byte sums (`sum & 0xFFFF`).

### Basic Sounds — Row F (`0x200000..0x2FFFFF`)

| Quarter | `U125` (LSB0) | `U124` (L0) | `U123` (L1) | `U122` (L2) | `U121` (L3) | `U120` (LSB1) | `U119` (R0) | `U118` (R1) | `U117` (R2) | `U116` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Q0 (`+00`, `_20`) | `658E` | `C043` | `F18C` | `CD97` | `1E0D` | `0F3D` | `F755` | `1905` | `DF60` | `274D` |
| Q1 (`+40`, `_24`) | `92B8` | `26B9` | `9C32` | `5BCA` | `53AE` | `9A46` | `1E0B` | `6DD2` | `6902` | `4842` |
| **Q2 (`+80`, primary)** | `66BD` | `9D4A` | `CEA2` | `3B45` | `F5C6` | `8FA7` | `CD37` | `AC82` | `24E9` | `F38D` |
| Q3 (`+C0`, `_2C`) | `0B2F` | `DAB7` | `3EBE` | `7931` | `0B2C` | `1788` | `3530` | `050F` | `78F7` | `05ED` |

### Basic Sounds — Row E (`0x300000..0x3FFFFF`)

| Quarter | `U107` (LSB0) | `U106` (L0) | `U105` (L1) | `U104` (L2) | `U103` (L3) | `U102` (LSB1) | `U101` (R0) | `U100` (R1) | `U99` (R2) | `U98` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Q0 (`+00`, `_30`) | `DBAF` | `B166` | `1715` | `06CB` | `A348` | `637C` | `58A8` | `2968` | `5329` | `E873` |
| Q1 (`+40`, `_34`) | `88E2` | `66F0` | `FB2F` | `0B9B` | `004E` | `77EF` | `1208` | `E2AF` | `0F54` | `2650` |
| **Q2 (`+80`, primary)** | `C181` | **`0F81`** | `0A52` | `A8CC` | `AF9B` | `ADE6` | `4751` | `EB20` | `B54A` | `C3CD` |
| Q3 (`+C0`, `_3C`) | `0C3F` | `66F0` | `1942` | `A70A` | `9ACB` | `88C1` | `8024` | `10C7` | `19B9` | `700D` |

### Sound Block D (`0x400000..0x4FFFFF`)

| Quarter | `U86` (LSB0) | `U85` (L0) | `U84` (L1) | `U83` (L2) | `U82` (L3) | `U81` (LSB1) | `U80` (R0) | `U79` (R1) | `U78` (R2) | `U77` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Q0 (`+00`, `107..098K250`)** | `9BFA` | `F459` | `4649` | `20EB` | `D191` | `6A76` | `C59B` | `4B75` | `2988` | `1BFC` |
| Q1 (`+40`, `_44`) | `FA16` | `0CF1` | `E389` | `227E` | `DA1D` | `94F5` | `D0A0` | `5E4B` | `1BAD` | `EA9D` |
| Q2 (`+80`, `_48`) | `9FEA` | `179D` | `5A9C` | `A41C` | `D444` | `797B` | `F363` | `5675` | `9EEF` | `C219` |
| Q3 (`+C0`, `_4C`) | `BF3B` | `E8DC` | `6789` | `CEFD` | `4DEF` | `5098` | `AA44` | `6EC8` | `C31B` | `442F` |

### Sound Block C (`0x500000..0x5FFFFF`)

| Quarter | `U66` (LSB0) | `U65` (L0) | `U64` (L1) | `U63` (L2) | `U62` (L3) | `U61` (LSB1) | `U60` (R0) | `U59` (R1) | `U58` (R2) | `U57` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Q0 (`+00`, `086..077K250`)** | `1BF6` | `6AEE` | `AA79` | `D13D` | `76BC` | `7F14` | `8F87` | `AF56` | `7B16` | `7993` |
| Q1 (`+40`, `_54`) | `F239` | `122C` | `0E07` | `345E` | `7D45` | `B85B` | `071E` | `2188` | `2EC3` | `7220` |
| Q2 (`+80`, `_58`) | `743B` | `0FD4` | `2B07` | `2F5F` | `AA9E` | `34CE` | `C6D7` | `4FA6` | `E22F` | `C5D2` |
| Q3 (`+C0`, `_5C`) | `82B4` | `7CEA` | `2342` | `7C95` | `C7A0` | `2BC3` | `5CCA` | `E1A7` | `BA7D` | `7DAE` |

### Sound Block B (`0x600000..0x6FFFFF`)

| Quarter | `U47` (LSB0) | `U46` (L0) | `U45` (L1) | `U44` (L2) | `U43` (L3) | `U42` (LSB1) | `U41` (R0) | `U40` (R1) | `U39` (R2) | `U38` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Q0 (`+00`, `_60`) | `D74D` | `62E0` | `04A2` | `45E8` | `3210` | `99F4` | `351C` | `5B73` | `60CB` | `68C9` |
| Q1 (`+40`, `_64`) | `45E3` | `2B15` | `2EEA` | `2BF6` | `94CD` | `7F2E` | `5972` | `1533` | `1A0B` | `8604` |
| **Q2 (`+80`, `066..057K250`)** | `7368` | `7EA2` | `2D9A` | `ECA3` | `E4DE` | `F86E` | `9E8E` | `6C1F` | `F783` | `F87A` |
| Q3 (`+C0`, `_6C`) | `A941` | `DA2E` | `8F0F` | `C60E` | `DE30` | `DBA2` | `42C0` | `7C35` | `9F52` | `2378` |

### Sound Block A (`0x700000..0x7FFFFF`)

| Quarter | `U20` (LSB0) | `U19` (L0) | `U18` (L1) | `U17` (L2) | `U16` (L3) | `U15` (LSB1) | `U14` (R0) | `U13` (R1) | `U12` (R2) | `U11` (R3) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Q0 (`+00`, `_70`) | `C7BB` | `A17E` | `CCC2` | `8967` | `D909` | `5DA2` | `9CAA` | `3D16` | `DC3B` | `3979` |
| Q1 (`+40`, `_74`) | `DC0A` | `3DB6` | `CDEC` | `49C0` | `E0E7` | `B1B5` | `AD97` | `E6F0` | `3706` | `D186` |
| **Q2 (`+80`, `047..038K250`)** | `D0DE` | `BDFD` | `7E1E` | `4C9A` | `E492` | `E8D3` | `EC31` | `7D29` | `34DE` | `D3AD` |
| Q3 (`+C0`, `_7C`) | `A6D1` | `632D` | `48EE` | `FC83` | `CAF4` | `C807` | `B1C0` | `C921` | `0883` | `7047` |
