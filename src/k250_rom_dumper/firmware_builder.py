#!/usr/bin/env python3
"""Standalone 68000 assembler and Kurzweil K250 ROM Dumper EPROM firmware builder.

Assembles a standalone 68000 firmware (no external `vasm`/`as` toolchain required)
that runs directly from CPU boot sockets U54/U38, initializes LCD, switch matrix,
and MIDI ACIA using Diagnostic ROM Rev D hardware sequences, lets the user pick any
K250 ROM (defaulting to U106) or whole ROM block on the front panel, computes its
Kurzweil 16-bit checksum, and dumps the exact 32,768 bytes (including 2-bit LSB
sound ROMs latched via CPSOUND 0x1B1000) over MIDI Out as framed SysEx.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Hardware Constants (verified against Diagnostic ROM Rev D & OS 6.1)
# ---------------------------------------------------------------------------
STACK_TOP        = 0x00107FFE

# Memory-mapped K250 peripherals
ADDR_AMPDAC_BASE = 0x00180081  # odd bytes 0x180081..0x1800DF (48 entries, step +2)
ADDR_GRP_AUDIO   = 0x001800E0  # word write 0x0000 to mute Group A/B audio
ADDR_LCD_CMD     = 0x00190001
ADDR_LCD_DATA    = 0x00192001
ADDR_PANEL_IO    = 0x00194001  # read: switch row (auto-inc); write: LED latch (auto-inc)
ADDR_PANEL_CTRL  = 0x00198001  # b0:rst swi, b1:rst led, b2:led on, b3:led off
ADDR_CGP_CHAN    = 0x001B0800  # word write 0x0000 to disable CGP channels
ADDR_CPSOUND     = 0x001B1000  # word write: SOUND=1; word read: D7:D6=LSB, SOUND=0
ADDR_PTM_CR13    = 0x001C0001  # MC6840 CR1 (when CR2.b0=1) / CR3 (when CR2.b0=0)
ADDR_PTM_CR2     = 0x001C0003  # MC6840 CR2 (O2 = 500 kHz MIDI TxC/RxC baud clock!)
ADDR_PTM_T1_MSB  = 0x001C0005  # MC6840 Timer 1 latch MSB
ADDR_PTM_T1_LSB  = 0x001C0007  # MC6840 Timer 1 latch LSB
ADDR_PTM_T2_MSB  = 0x001C0009  # MC6840 Timer 2 latch MSB
ADDR_PTM_T2_LSB  = 0x001C000B  # MC6840 Timer 2 latch LSB
ADDR_PTM_T3_MSB  = 0x001C000D  # MC6840 Timer 3 latch MSB
ADDR_PTM_T3_LSB  = 0x001C000F  # MC6840 Timer 3 latch LSB
ADDR_MIDI_CTRL   = 0x001D0001  # MC6850 control/status (0x03=reset, 0x15=31250bps 8N1)
ADDR_MIDI_DATA   = 0x001D0003  # MC6850 TX/RX data
ADDR_SYS_CTRL    = 0x001F0000  # System control latch (cleared on boot per Diag Rev D)
ADDR_SYS_STAT    = 0x001F0001  # System status register

# CPU SRAM globals (0x100100..0x1003FF)
VAR_CUR_IDX      = 0x00100100  # word: selected menu index (0..270)
VAR_CUR_CSUM     = 0x00100102  # word: checksum of current chip
VAR_ABORT        = 0x00100104  # word: 0=ok, 1=user NO abort, 2=MIDI TDRE timeout
VAR_MIDI_ERR_ST  = 0x00100106  # word: raw $001D0001 status byte on MIDI TX timeout
VAR_SW_ROWS      = 0x00100110  # 8 bytes: switch scan snapshot
VAR_SW_TMP       = 0x00100118  # 8 bytes: debounce temp snapshot
VAR_LCD_BUF      = 0x00100120  # 32 bytes: 24-char LCD line staging buffer
VAR_PKT_BUF      = 0x00100200  # 256 bytes: 1 ROM packet read buffer

# Switch scan codes (row, bit) verified from FrontPanel.swift / Diag Rev D
KEY_UP     = (2, 4)  # 0x14: UP arrow
KEY_DOWN   = (1, 0)  # 0x08: DOWN arrow
KEY_RIGHT  = (2, 0)  # 0x10: RIGHT arrow
KEY_LEFT   = (1, 1)  # 0x09: LEFT arrow
KEY_YES    = (3, 0)  # 0x18: YES
KEY_SELECT = (0, 0)  # 0x00: SELECT
KEY_NO     = (0, 7)  # 0x07: NO

# Key action IDs returned by wait_key
ACT_NEXT   = 1
ACT_PREV   = 2
ACT_GRP    = 3
ACT_DUMP   = 4

# Modes in descriptor table
MODE_MSB   = 0
MODE_LSB   = 1
MODE_BATCH = 2


# ---------------------------------------------------------------------------
# ROM Descriptor Table (274 entries, 26 groups)
# Grp 0 holds 3 one-button unattended super-batches; Grp 1..25 hold all 4 quarters
# per 1 MB Sound ROM row + CPU OS (246 real chips total).
# ---------------------------------------------------------------------------
def build_rom_entries() -> List[Tuple[int, int, int, str]]:
    """Returns list of (base_addr_or_batch, mode, group_id, name16)."""
    entries: List[Tuple[int, int, int, str]] = [
        (0, MODE_BATCH, 0, "ALL 246 ROMS    "),
        (0, MODE_BATCH, 0, "ALL 80 BASIC    "),
        (0, MODE_BATCH, 0, "ALL 160 BLOCKS  "),
    ]
    next_group_id = 1

    def add_sound_quarter(
        base: int,
        chip_names: List[str],
        tag: str,
        batch_label: str,
        put_l0_first: bool = False,
    ) -> None:
        nonlocal next_group_id
        group_id = next_group_id
        next_group_id += 1
        # chip_names order is standard K250 socket order:
        # [LSB0, L0, L1, L2, L3, LSB1, R0, R1, R2, R3]
        specs = [
            (base + 0x00000, MODE_LSB, "LSB0"),
            (base + 0x00000, MODE_MSB, "L0"),
            (base + 0x10000, MODE_MSB, "L1"),
            (base + 0x20000, MODE_MSB, "L2"),
            (base + 0x30000, MODE_MSB, "L3"),
            (base + 0x20000, MODE_LSB, "LSB1"),
            (base + 0x00001, MODE_MSB, "R0"),
            (base + 0x10001, MODE_MSB, "R1"),
            (base + 0x20001, MODE_MSB, "R2"),
            (base + 0x30001, MODE_MSB, "R3"),
        ]
        items = list(zip(chip_names, specs))
        if put_l0_first:
            # Swap L0 (idx 1) and LSB0 (idx 0) so U106 is index 0 right on boot!
            items[0], items[1] = items[1], items[0]
        start_idx = len(entries)
        for cname, (addr, mode, role) in items:
            label = f"{cname} {tag} {role}"[:16].ljust(16)
            entries.append((addr, mode, group_id, label))
        entries.append(((start_idx << 16) | 10, MODE_BATCH, group_id, batch_label[:16].ljust(16)))

    def suffixed_names(stems: List[str], qtag: str) -> List[str]:
        return [f"{s}_{qtag}" for s in stems]

    row_e_stems = ["U107", "U106", "U105", "U104", "U103", "U102", "U101", "U100", "U99", "U98"]
    row_f_stems = ["U125", "U124", "U123", "U122", "U121", "U120", "U119", "U118", "U117", "U116"]
    blk_d_stems = ["U86", "U85", "U84", "U83", "U82", "U81", "U80", "U79", "U78", "U77"]
    blk_c_stems = ["U66", "U65", "U64", "U63", "U62", "U61", "U60", "U59", "U58", "U57"]
    blk_b_stems = ["U47", "U46", "U45", "U44", "U43", "U42", "U41", "U40", "U39", "U38"]
    blk_a_stems = ["U20", "U19", "U18", "U17", "U16", "U15", "U14", "U13", "U12", "U11"]

    # Grp 1: Row E Quarter 2 (0x380000) -> U106 (CS:0F81) first inside Basic Sounds
    basic_start = len(entries)
    add_sound_quarter(0x380000, row_e_stems, "E38", "ALL ROWE 38 (10)", put_l0_first=True)
    # Grp 2: Row F Quarter 2 (0x280000) -> Baseline Basic Sounds Row F (U125..U116)
    add_sound_quarter(0x280000, row_f_stems, "F28", "ALL ROWF 28 (10)")
    # Grp 3..5: Row E other 3 quarters (0x30, 0x34, 0x3C)
    add_sound_quarter(0x300000, suffixed_names(row_e_stems, "30"), "E30", "ALL ROWE 30 (10)")
    add_sound_quarter(0x340000, suffixed_names(row_e_stems, "34"), "E34", "ALL ROWE 34 (10)")
    add_sound_quarter(0x3C0000, suffixed_names(row_e_stems, "3C"), "E3C", "ALL ROWE 3C (10)")
    # Grp 6..8: Row F other 3 quarters (0x20, 0x24, 0x2C)
    add_sound_quarter(0x200000, suffixed_names(row_f_stems, "20"), "F20", "ALL ROWF 20 (10)")
    add_sound_quarter(0x240000, suffixed_names(row_f_stems, "24"), "F24", "ALL ROWF 24 (10)")
    add_sound_quarter(0x2C0000, suffixed_names(row_f_stems, "2C"), "F2C", "ALL ROWF 2C (10)")
    basic_end = len(entries)

    # Grp 9: CPU OS ROMs (0x010000..0x03FFFF; U54/U38 contain this dumper itself)
    cpu_grp = next_group_id
    next_group_id += 1
    cpu_start = len(entries)
    for addr, label in [
        (0x010000, "U53 CPU1 EVN"),
        (0x010001, "U37 CPU1 ODD"),
        (0x020000, "U52 CPU2 EVN"),
        (0x020001, "U36 CPU2 ODD"),
        (0x030000, "U51 CPU3 EVN"),
        (0x030001, "U35 CPU3 ODD"),
    ]:
        entries.append((addr, MODE_MSB, cpu_grp, label[:16].ljust(16)))
    entries.append(((cpu_start << 16) | 6, MODE_BATCH, cpu_grp, "ALL CPU OS (6)  "))

    # Grp 10..13: Sound Block D (0x400000..0x4FFFFF; 0x40 primary)
    blocks_start = len(entries)
    add_sound_quarter(
        0x400000,
        ["107K250", "106K250", "105K250", "104K250", "103K250",
         "102K250", "101K250", "100K250", "099K250", "098K250"],
        "D40", "ALL BLKD 40 (10)",
    )
    add_sound_quarter(0x440000, suffixed_names(blk_d_stems, "44"), "D44", "ALL BLKD 44 (10)")
    add_sound_quarter(0x480000, suffixed_names(blk_d_stems, "48"), "D48", "ALL BLKD 48 (10)")
    add_sound_quarter(0x4C0000, suffixed_names(blk_d_stems, "4C"), "D4C", "ALL BLKD 4C (10)")

    # Grp 14..17: Sound Block C (0x500000..0x5FFFFF; 0x50 primary)
    add_sound_quarter(
        0x500000,
        ["086K250", "085K250", "084K250", "083K250", "082K250",
         "081K250", "080K250", "079K250", "078K250", "077K250"],
        "C50", "ALL BLKC 50 (10)",
    )
    add_sound_quarter(0x540000, suffixed_names(blk_c_stems, "54"), "C54", "ALL BLKC 54 (10)")
    add_sound_quarter(0x580000, suffixed_names(blk_c_stems, "58"), "C58", "ALL BLKC 58 (10)")
    add_sound_quarter(0x5C0000, suffixed_names(blk_c_stems, "5C"), "C5C", "ALL BLKC 5C (10)")

    # Grp 18..21: Sound Block B (0x600000..0x6FFFFF; 0x68 primary)
    add_sound_quarter(
        0x680000,
        ["066K250", "065K250", "064K250", "063K250", "062K250",
         "061K250", "060K250", "059K250", "058K250", "057K250"],
        "B68", "ALL BLKB 68 (10)",
    )
    add_sound_quarter(0x600000, suffixed_names(blk_b_stems, "60"), "B60", "ALL BLKB 60 (10)")
    add_sound_quarter(0x640000, suffixed_names(blk_b_stems, "64"), "B64", "ALL BLKB 64 (10)")
    add_sound_quarter(0x6C0000, suffixed_names(blk_b_stems, "6C"), "B6C", "ALL BLKB 6C (10)")

    # Grp 22..25: Sound Block A (0x700000..0x7FFFFF; 0x78 primary)
    add_sound_quarter(
        0x780000,
        ["047K250", "046K250", "045K250", "044K250", "043K250",
         "042K250", "041K250", "040K250", "039K250", "038K250"],
        "A78", "ALL BLKA 78 (10)",
    )
    add_sound_quarter(0x700000, suffixed_names(blk_a_stems, "70"), "A70", "ALL BLKA 70 (10)")
    add_sound_quarter(0x740000, suffixed_names(blk_a_stems, "74"), "A74", "ALL BLKA 74 (10)")
    add_sound_quarter(0x7C0000, suffixed_names(blk_a_stems, "7C"), "A7C", "ALL BLKA 7C (10)")
    all_end = len(entries)

    # Populate Grp 0 super-batches (loop skips nested MODE_BATCH items automatically)
    entries[0] = (((basic_start << 16) | (all_end - basic_start)), MODE_BATCH, 0, "ALL 246 ROMS    ")
    entries[1] = (((basic_start << 16) | (basic_end - basic_start)), MODE_BATCH, 0, "ALL 80 BASIC    ")
    entries[2] = (((blocks_start << 16) | (all_end - blocks_start)), MODE_BATCH, 0, "ALL 160 BLOCKS  ")

    return entries


# ---------------------------------------------------------------------------
# Minimal 2-Pass MC68000 Assembler (exact Motorola Programmer's Reference encodings)
# ---------------------------------------------------------------------------
COND_CODES = {
    "bra": 0x0, "bsr": 0x1, "bhi": 0x2, "bls": 0x3,
    "bcc": 0x4, "bhs": 0x4, "bcs": 0x5, "blo": 0x5,
    "bne": 0x6, "beq": 0x7, "bvc": 0x8, "bvs": 0x9,
    "bpl": 0xA, "bmi": 0xB, "bge": 0xC, "blt": 0xD,
    "bgt": 0xE, "ble": 0xF,
}


def _reg_mask(regs: List[str], predec: bool) -> int:
    """Builds 16-bit movem register mask."""
    idx_map = {
        **{f"d{i}": i for i in range(8)},
        **{f"a{i}": 8 + i for i in range(8)},
        "sp": 15,
    }
    mask = 0
    for r in regs:
        bit = idx_map[r.lower()]
        mask |= (1 << (15 - bit)) if predec else (1 << bit)
    return mask


class M68kAsm:
    def __init__(self) -> None:
        self.ops: List[Tuple[str, tuple]] = []
        self.labels: Dict[str, int] = {}
        self.pass_num = 1
        self.buf = bytearray()
        self.listing: List[str] = []

    @property
    def pc(self) -> int:
        return len(self.buf)

    def label(self, name: str) -> None:
        self.ops.append(("label", (name,)))

    def comment(self, text: str) -> None:
        self.ops.append(("comment", (text,)))

    def align2(self, fill: int = 0xFF) -> None:
        self.ops.append(("align2", (fill,)))

    def org(self, addr: int, fill: int = 0xFF) -> None:
        self.ops.append(("org", (addr, fill)))

    def dc_b(self, *vals: int) -> None:
        self.ops.append(("dc_b", vals))

    def dc_w(self, *vals: int) -> None:
        self.ops.append(("dc_w", vals))

    def dc_l(self, *vals: int | str) -> None:
        self.ops.append(("dc_l", vals))

    def ascii(self, s: str) -> None:
        self.ops.append(("dc_b", tuple(s.encode("ascii"))))

    def cstr(self, s: str) -> None:
        self.ops.append(("dc_b", tuple(s.encode("ascii") + b"\x00")))

    # --- Instructions ---
    def emit_w(self, *words: int, asm_text: str = "") -> None:
        self.ops.append(("words", (words, asm_text)))

    def nop(self) -> None:
        self.emit_w(0x4E71, asm_text="nop")

    def rts(self) -> None:
        self.emit_w(0x4E75, asm_text="rts")

    def rte(self) -> None:
        self.emit_w(0x4E73, asm_text="rte")

    def move_w_imm_sr(self, imm: int) -> None:
        self.emit_w(0x46FC, imm & 0xFFFF, asm_text=f"move.w #${imm:04X}, sr")

    def move_b_imm_absl(self, imm: int, addr: int) -> None:
        self.emit_w(0x13FC, imm & 0xFF, (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.b #${imm & 0xFF:02X}, ${addr:08X}")

    def move_w_imm_absl(self, imm: int, addr: int) -> None:
        self.emit_w(0x33FC, imm & 0xFFFF, (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.w #${imm & 0xFFFF:04X}, ${addr:08X}")

    def move_l_imm_absl(self, imm: int, addr: int) -> None:
        self.emit_w(0x23FC, (imm >> 16) & 0xFFFF, imm & 0xFFFF,
                    (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.l #${imm:08X}, ${addr:08X}")

    def move_b_absl_d(self, addr: int, dn: int) -> None:
        self.emit_w(0x1039 | (dn << 9), (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.b ${addr:08X}, d{dn}")

    def move_w_absl_d(self, addr: int, dn: int) -> None:
        self.emit_w(0x3039 | (dn << 9), (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.w ${addr:08X}, d{dn}")

    def move_b_d_absl(self, dn: int, addr: int) -> None:
        self.emit_w(0x13C0 | dn, (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.b d{dn}, ${addr:08X}")

    def move_w_d_absl(self, dn: int, addr: int) -> None:
        self.emit_w(0x33C0 | dn, (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"move.w d{dn}, ${addr:08X}")

    def move_b_an_ind_d(self, an: int, dn: int) -> None:
        self.emit_w(0x1010 | (dn << 9) | an, asm_text=f"move.b (a{an}), d{dn}")

    def move_b_an_post_d(self, an: int, dn: int) -> None:
        self.emit_w(0x1018 | (dn << 9) | an, asm_text=f"move.b (a{an})+, d{dn}")

    def move_b_d_an_ind(self, dn: int, an: int) -> None:
        self.emit_w(0x1080 | (an << 9) | dn, asm_text=f"move.b d{dn}, (a{an})")

    def move_b_d_an_post(self, dn: int, an: int) -> None:
        self.emit_w(0x10C0 | (an << 9) | dn, asm_text=f"move.b d{dn}, (a{an})+")

    def move_b_imm_an_post(self, imm: int, an: int) -> None:
        self.emit_w(0x10FC | (an << 9), imm & 0xFF, asm_text=f"move.b #${imm & 0xFF:02X}, (a{an})+")

    def move_b_disp_d(self, disp: int, an: int, dn: int) -> None:
        self.emit_w(0x1028 | (dn << 9) | an, disp & 0xFFFF, asm_text=f"move.b {disp}(a{an}), d{dn}")

    def move_w_disp_d(self, disp: int, an: int, dn: int) -> None:
        self.emit_w(0x3028 | (dn << 9) | an, disp & 0xFFFF, asm_text=f"move.w {disp}(a{an}), d{dn}")

    def move_l_disp_a(self, disp: int, src_an: int, dst_an: int) -> None:
        self.emit_w(0x2068 | (dst_an << 9) | src_an, disp & 0xFFFF,
                    asm_text=f"movea.l {disp}(a{src_an}), a{dst_an}")

    def move_b_d_d(self, src: int, dst: int) -> None:
        self.emit_w(0x1000 | (dst << 9) | src, asm_text=f"move.b d{src}, d{dst}")

    def move_w_d_d(self, src: int, dst: int) -> None:
        self.emit_w(0x3000 | (dst << 9) | src, asm_text=f"move.w d{src}, d{dst}")

    def move_l_d_d(self, src: int, dst: int) -> None:
        self.emit_w(0x2000 | (dst << 9) | src, asm_text=f"move.l d{src}, d{dst}")

    def move_l_a_a(self, src: int, dst: int) -> None:
        self.emit_w(0x2048 | (dst << 9) | src, asm_text=f"movea.l a{src}, a{dst}")

    def moveq(self, imm: int, dn: int) -> None:
        if not (-128 <= imm <= 127):
            raise ValueError(f"moveq immediate out of range: {imm}")
        self.emit_w(0x7000 | (dn << 9) | (imm & 0xFF), asm_text=f"moveq #{imm}, d{dn}")

    def move_w_imm_d(self, imm: int, dn: int) -> None:
        self.emit_w(0x303C | (dn << 9), imm & 0xFFFF, asm_text=f"move.w #${imm & 0xFFFF:04X}, d{dn}")

    def lea_absl(self, addr: int, an: int) -> None:
        self.emit_w(0x41F9 | (an << 9), (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"lea ${addr:08X}, a{an}")

    def lea_pc(self, target: str, an: int) -> None:
        self.ops.append(("lea_pc", (target, an)))

    def clr_b(self, dn: int) -> None:
        self.emit_w(0x4200 | dn, asm_text=f"clr.b d{dn}")

    def clr_w(self, dn: int) -> None:
        self.emit_w(0x4240 | dn, asm_text=f"clr.w d{dn}")

    def clr_l(self, dn: int) -> None:
        self.emit_w(0x4280 | dn, asm_text=f"clr.l d{dn}")

    def clr_w_absl(self, addr: int) -> None:
        self.emit_w(0x4279, (addr >> 16) & 0xFFFF, addr & 0xFFFF, asm_text=f"clr.w ${addr:08X}")

    def addq_w(self, q: int, dn: int) -> None:
        assert 1 <= q <= 8
        self.emit_w(0x5040 | ((q & 7) << 9) | dn, asm_text=f"addq.w #{q}, d{dn}")

    def addq_l(self, q: int, dn: int) -> None:
        assert 1 <= q <= 8
        self.emit_w(0x5080 | ((q & 7) << 9) | dn, asm_text=f"addq.l #{q}, d{dn}")

    def addq_l_a(self, q: int, an: int) -> None:
        assert 1 <= q <= 8
        self.emit_w(0x5088 | ((q & 7) << 9) | an, asm_text=f"addq.l #{q}, a{an}")

    def subq_w(self, q: int, dn: int) -> None:
        assert 1 <= q <= 8
        self.emit_w(0x5140 | ((q & 7) << 9) | dn, asm_text=f"subq.w #{q}, d{dn}")

    def add_w(self, src_dn: int, dst_dn: int) -> None:
        self.emit_w(0xD040 | (dst_dn << 9) | src_dn, asm_text=f"add.w d{src_dn}, d{dst_dn}")

    def add_l(self, src_dn: int, dst_dn: int) -> None:
        self.emit_w(0xD080 | (dst_dn << 9) | src_dn, asm_text=f"add.l d{src_dn}, d{dst_dn}")

    def adda_l_d(self, dn: int, an: int) -> None:
        self.emit_w(0xD1C0 | (an << 9) | dn, asm_text=f"adda.l d{dn}, a{an}")

    def adda_l_imm(self, imm: int, an: int) -> None:
        self.emit_w(0xD1FC | (an << 9), (imm >> 16) & 0xFFFF, imm & 0xFFFF,
                    asm_text=f"adda.l #${imm:08X}, a{an}")

    def addi_b(self, imm: int, dn: int) -> None:
        self.emit_w(0x0600 | dn, imm & 0xFF, asm_text=f"addi.b #${imm & 0xFF:02X}, d{dn}")

    def addi_w(self, imm: int, dn: int) -> None:
        self.emit_w(0x0640 | dn, imm & 0xFFFF, asm_text=f"addi.w #${imm & 0xFFFF:04X}, d{dn}")

    def andi_w(self, imm: int, dn: int) -> None:
        self.emit_w(0x0240 | dn, imm & 0xFFFF, asm_text=f"andi.w #${imm & 0xFFFF:04X}, d{dn}")

    def andi_l(self, imm: int, dn: int) -> None:
        self.emit_w(0x0280 | dn, (imm >> 16) & 0xFFFF, imm & 0xFFFF,
                    asm_text=f"andi.l #${imm & 0xFFFFFFFF:08X}, d{dn}")

    def cmpi_b(self, imm: int, dn: int) -> None:
        self.emit_w(0x0C00 | dn, imm & 0xFF, asm_text=f"cmpi.b #${imm & 0xFF:02X}, d{dn}")

    def cmpi_w(self, imm: int, dn: int) -> None:
        self.emit_w(0x0C40 | dn, imm & 0xFFFF, asm_text=f"cmpi.w #${imm & 0xFFFF:04X}, d{dn}")

    def cmp_w(self, src_dn: int, dst_dn: int) -> None:
        self.emit_w(0xB040 | (dst_dn << 9) | src_dn, asm_text=f"cmp.w d{src_dn}, d{dst_dn}")

    def tst_b(self, dn: int) -> None:
        self.emit_w(0x4A00 | dn, asm_text=f"tst.b d{dn}")

    def tst_w(self, dn: int) -> None:
        self.emit_w(0x4A40 | dn, asm_text=f"tst.w d{dn}")

    def btst_d(self, bit: int, dn: int) -> None:
        self.emit_w(0x0800 | dn, bit & 0x1F, asm_text=f"btst #{bit}, d{dn}")

    def btst_absl(self, bit: int, addr: int) -> None:
        self.emit_w(0x0839, bit & 7, (addr >> 16) & 0xFFFF, addr & 0xFFFF,
                    asm_text=f"btst #{bit}, ${addr:08X}")

    def bset_d(self, bit: int, dn: int) -> None:
        self.emit_w(0x08C0 | dn, bit & 0x1F, asm_text=f"bset #{bit}, d{dn}")

    def lsr_w(self, cnt: int, dn: int) -> None:
        assert 1 <= cnt <= 8
        self.emit_w(0xE048 | ((cnt & 7) << 9) | dn, asm_text=f"lsr.w #{cnt}, d{dn}")

    def lsl_w(self, cnt: int, dn: int) -> None:
        assert 1 <= cnt <= 8
        self.emit_w(0xE148 | ((cnt & 7) << 9) | dn, asm_text=f"lsl.w #{cnt}, d{dn}")

    def mulu_w_imm(self, imm: int, dn: int) -> None:
        self.emit_w(0xC0FC | (dn << 9), imm & 0xFFFF, asm_text=f"mulu.w #{imm}, d{dn}")

    def divu_w_imm(self, imm: int, dn: int) -> None:
        self.emit_w(0x80FC | (dn << 9), imm & 0xFFFF, asm_text=f"divu.w #{imm}, d{dn}")

    def swap(self, dn: int) -> None:
        self.emit_w(0x4840 | dn, asm_text=f"swap d{dn}")

    def movem_save(self, *regs: str) -> None:
        m = _reg_mask(list(regs), predec=True)
        self.emit_w(0x48E7, m, asm_text=f"movem.l {'/'.join(regs)}, -(sp)")

    def movem_restore(self, *regs: str) -> None:
        m = _reg_mask(list(regs), predec=False)
        self.emit_w(0x4CDF, m, asm_text=f"movem.l (sp)+, {'/'.join(regs)}")

    def bcc(self, mnemonic: str, target: str) -> None:
        cond = COND_CODES[mnemonic.lower()]
        self.ops.append(("bcc", (mnemonic.lower(), cond, target)))

    def bra(self, target: str) -> None:
        self.bcc("bra", target)

    def bsr(self, target: str) -> None:
        self.bcc("bsr", target)

    def beq(self, target: str) -> None:
        self.bcc("beq", target)

    def bne(self, target: str) -> None:
        self.bcc("bne", target)

    def bge(self, target: str) -> None:
        self.bcc("bge", target)

    def blt(self, target: str) -> None:
        self.bcc("blt", target)

    def ble(self, target: str) -> None:
        self.bcc("ble", target)

    def dbf(self, dn: int, target: str) -> None:
        self.ops.append(("dbf", (dn, target)))

    def _run_pass(self, pnum: int) -> None:
        self.pass_num = pnum
        self.buf = bytearray()
        if pnum == 2:
            self.listing = []

        for kind, args in self.ops:
            if kind == "label":
                (name,) = args
                if pnum == 1:
                    if name in self.labels:
                        raise ValueError(f"Duplicate label: {name}")
                    self.labels[name] = self.pc
                else:
                    self.listing.append(f"{self.pc:06X}:                  {name}:")

            elif kind == "comment":
                if pnum == 2:
                    self.listing.append(f"                         ; {args[0]}")

            elif kind == "align2":
                (fill,) = args
                if len(self.buf) & 1:
                    self.buf.append(fill & 0xFF)

            elif kind == "org":
                addr, fill = args
                if self.pc > addr:
                    raise ValueError(f".org ${addr:06X} passed by PC=${self.pc:06X}")
                self.buf.extend([fill & 0xFF] * (addr - self.pc))

            elif kind == "dc_b":
                start = self.pc
                for v in args:
                    self.buf.append(v & 0xFF)
                if pnum == 2 and len(args) <= 24:
                    hx = " ".join(f"{v & 0xFF:02X}" for v in args[:8])
                    self.listing.append(f"{start:06X}: {hx:<16s} .dc.b ({len(args)} bytes)")

            elif kind == "dc_w":
                if self.pc & 1:
                    raise ValueError(f"Unaligned dc.w at ${self.pc:06X}")
                start = self.pc
                for v in args:
                    self.buf.extend(struct.pack(">H", v & 0xFFFF))
                if pnum == 2:
                    hx = " ".join(f"{v & 0xFFFF:04X}" for v in args[:4])
                    self.listing.append(f"{start:06X}: {hx:<16s} .dc.w")

            elif kind == "dc_l":
                if self.pc & 1:
                    raise ValueError(f"Unaligned dc.l at ${self.pc:06X}")
                start = self.pc
                for v in args:
                    val = (self.labels[v] if isinstance(v, str) else v) if pnum == 2 else 0
                    self.buf.extend(struct.pack(">I", val & 0xFFFFFFFF))
                if pnum == 2:
                    self.listing.append(f"{start:06X}:                  .dc.l ({len(args)} longs)")

            elif kind == "words":
                if self.pc & 1:
                    raise ValueError(f"Unaligned instruction at ${self.pc:06X}: {args[1]}")
                words, asm_text = args
                start = self.pc
                for w in words:
                    self.buf.extend(struct.pack(">H", w & 0xFFFF))
                if pnum == 2:
                    hx = "".join(f"{w & 0xFFFF:04X} " for w in words)
                    self.listing.append(f"{start:06X}: {hx:<20s} {asm_text}")

            elif kind == "lea_pc":
                if self.pc & 1:
                    raise ValueError(f"Unaligned lea_pc at ${self.pc:06X}")
                target, an = args
                start = self.pc
                disp = (self.labels[target] - (start + 2)) if pnum == 2 else 0
                if pnum == 2 and not (-32768 <= disp <= 32767):
                    raise ValueError(f"lea_pc disp out of range for {target}: {disp}")
                w0 = 0x41FA | (an << 9)
                w1 = disp & 0xFFFF
                self.buf.extend(struct.pack(">HH", w0, w1))
                if pnum == 2:
                    self.listing.append(f"{start:06X}: {w0:04X} {w1:04X}            lea {target}(pc), a{an}")

            elif kind == "bcc":
                if self.pc & 1:
                    raise ValueError(f"Unaligned bcc at ${self.pc:06X}")
                mnemonic, cond, target = args
                start = self.pc
                disp = (self.labels[target] - (start + 2)) if pnum == 2 else 0
                if pnum == 2 and (disp == 0 or not (-32768 <= disp <= 32767)):
                    raise ValueError(f"{mnemonic} disp invalid for {target}: {disp}")
                w0 = 0x6000 | (cond << 8)
                w1 = disp & 0xFFFF
                self.buf.extend(struct.pack(">HH", w0, w1))
                if pnum == 2:
                    self.listing.append(f"{start:06X}: {w0:04X} {w1:04X}            {mnemonic}.w {target}")

            elif kind == "dbf":
                if self.pc & 1:
                    raise ValueError(f"Unaligned dbf at ${self.pc:06X}")
                dn, target = args
                start = self.pc
                disp = (self.labels[target] - (start + 2)) if pnum == 2 else 0
                if pnum == 2 and not (-32768 <= disp <= 32767):
                    raise ValueError(f"dbf disp out of range for {target}: {disp}")
                w0 = 0x51C8 | dn
                w1 = disp & 0xFFFF
                self.buf.extend(struct.pack(">HH", w0, w1))
                if pnum == 2:
                    self.listing.append(f"{start:06X}: {w0:04X} {w1:04X}            dbf d{dn}, {target}")

    def assemble(self) -> bytes:
        self.labels.clear()
        self._run_pass(1)
        self._run_pass(2)
        return bytes(self.buf)


# ---------------------------------------------------------------------------
# Firmware Assembly Program
# ---------------------------------------------------------------------------
def build_dumper_firmware() -> Tuple[bytes, List[str], List[Tuple[int, int, int, str]]]:
    entries = build_rom_entries()
    assert len(entries) == 274, f"Expected 274 entries, got {len(entries)}"
    tokens = [name16.split()[0] for (_, mode, _, name16) in entries if mode != MODE_BATCH]
    assert len(tokens) == len(set(tokens)), "Duplicate single-chip tokens in menu!"

    def _span_chip_count(batch_idx: int) -> int:
        val, mode, _, _ = entries[batch_idx]
        assert mode == MODE_BATCH
        start, count = (val >> 16) & 0xFFFF, val & 0xFFFF
        return sum(1 for i in range(start, start + count) if entries[i][1] != MODE_BATCH)

    assert _span_chip_count(0) == 246
    assert _span_chip_count(1) == 80
    assert _span_chip_count(2) == 160

    group_starts = []
    seen_groups = set()
    for i, (_, _, grp, _) in enumerate(entries):
        if grp not in seen_groups:
            seen_groups.add(grp)
            group_starts.append(i)
    assert len(group_starts) == 26, f"Expected 26 groups, got {len(group_starts)}"

    a = M68kAsm()

    # -----------------------------------------------------------------------
    # 68000 Exception Vector Table (0x000000..0x0000FF)
    # -----------------------------------------------------------------------
    a.comment("68000 Exception Vector Table")
    a.dc_l(STACK_TOP)
    a.dc_l("entry")
    for _ in range(62):
        a.dc_l("trap_halt")

    a.org(0x000100, fill=0xFF)
    a.label("trap_halt")
    a.move_w_imm_sr(0x2700)
    a.bra("trap_halt")

    # -----------------------------------------------------------------------
    # Startup Initialization
    # -----------------------------------------------------------------------
    a.label("entry")
    a.move_w_imm_sr(0x2700)  # mask all interrupts

    a.comment("Mute CGP voices, AMPDACs, Group A/B audio, clear SYS_CTRL, and init MC6840 500 kHz MIDI clock (Diag Rev D @00DE4+@00CD6)")
    a.move_b_absl_d(ADDR_SYS_STAT, 0)
    a.move_b_imm_absl(0x03, ADDR_MIDI_CTRL)
    a.move_b_imm_absl(0x00, ADDR_SYS_CTRL)
    a.move_w_imm_absl(0x0000, ADDR_CGP_CHAN)
    a.move_w_imm_absl(0x0000, ADDR_GRP_AUDIO)
    a.lea_absl(ADDR_AMPDAC_BASE, 0)
    a.moveq(47, 0)
    a.label("init_ampdac_loop")
    a.move_b_imm_an_post(0xFF, 0)
    a.addq_l_a(1, 0)
    a.dbf(0, "init_ampdac_loop")

    a.comment("MC6840 PTM init (Diag Rev D @00CD6): Timer 2 O2 outputs 500 kHz TxC/RxC for MC6850 MIDI ACIA")
    a.move_b_imm_absl(0x82, ADDR_PTM_CR2)
    a.move_b_imm_absl(0x86, ADDR_PTM_CR13)
    a.move_b_imm_absl(0x83, ADDR_PTM_CR2)
    a.move_b_imm_absl(0x83, ADDR_PTM_CR13)
    a.move_b_imm_absl(0x00, ADDR_PTM_T1_MSB)
    a.move_b_imm_absl(0x00, ADDR_PTM_T1_LSB)
    a.move_b_imm_absl(0x00, ADDR_PTM_T2_MSB)
    a.move_b_imm_absl(0x00, ADDR_PTM_T2_LSB)
    a.move_b_imm_absl(0x00, ADDR_PTM_T3_MSB)
    a.move_b_imm_absl(0x00, ADDR_PTM_T3_LSB)
    a.move_b_imm_absl(0x82, ADDR_PTM_CR13)  # release preset -> timers run!

    a.comment("Initialize MC6850 MIDI ACIA (0x1D0001/3): reset with 500 kHz clock running, then 31250bps 8N1 polled")
    a.move_b_imm_absl(0x03, ADDR_MIDI_CTRL)
    a.nop()
    a.nop()
    a.move_l_imm_absl(0x00000000, VAR_CUR_IDX)
    a.move_w_imm_absl(0x0000, VAR_ABORT)
    a.move_w_imm_absl(0x0000, VAR_MIDI_ERR_ST)
    a.move_b_imm_absl(0x15, ADDR_MIDI_CTRL)

    a.comment("Initialize Front Panel LEDs (light YES + SELECT + NO)")
    a.moveq(0x70, 0)
    a.moveq(0x00, 1)
    a.bsr("set_leds_state")

    a.comment("Initialize Optrex / HD44780 2x24 LCD (cold-boot safe)")
    a.bsr("lcd_init")

    # -----------------------------------------------------------------------
    # Main UI Loop
    # -----------------------------------------------------------------------
    a.label("main_loop")
    a.bsr("draw_menu_and_csum")
    a.bsr("wait_key_action")
    # d0 returns: ACT_NEXT(1), ACT_PREV(2), ACT_GRP(3), ACT_DUMP(4)

    a.cmpi_w(ACT_NEXT, 0)
    a.bne("chk_prev")
    a.move_w_absl_d(VAR_CUR_IDX, 1)
    a.addq_w(1, 1)
    a.cmpi_w(len(entries), 1)
    a.blt("save_idx")
    a.clr_w(1)
    a.bra("save_idx")

    a.label("chk_prev")
    a.cmpi_w(ACT_PREV, 0)
    a.bne("chk_grp")
    a.move_w_absl_d(VAR_CUR_IDX, 1)
    a.subq_w(1, 1)
    a.bge("save_idx")
    a.move_w_imm_d(len(entries) - 1, 1)
    a.bra("save_idx")

    a.label("chk_grp")
    a.cmpi_w(ACT_GRP, 0)
    a.bne("chk_dump")
    a.move_w_absl_d(VAR_CUR_IDX, 0)
    a.bsr("get_entry_ptr")  # a6 = descriptor
    a.moveq(0, 2)
    a.move_b_disp_d(5, 6, 2)  # group_id 0..6
    a.addq_w(1, 2)
    a.cmpi_w(len(group_starts), 2)
    a.blt("grp_ok")
    a.clr_w(2)
    a.label("grp_ok")
    a.lea_pc("tbl_group_starts", 0)
    a.add_w(2, 2)  # *2 word offset
    a.andi_l(0xFFFF, 2)
    a.adda_l_d(2, 0)
    a.move_w_disp_d(0, 0, 1)
    a.label("save_idx")
    a.move_w_d_absl(1, VAR_CUR_IDX)
    a.bra("main_loop")

    a.label("chk_dump")
    a.cmpi_w(ACT_DUMP, 0)
    a.bne("main_loop")
    a.bsr("execute_selected_dump")
    a.bra("main_loop")

    # -----------------------------------------------------------------------
    # draw_menu_and_csum: draws Line 1 + live checksum + Line 2
    # -----------------------------------------------------------------------
    a.label("draw_menu_and_csum")
    a.movem_save("d0", "d1", "d2", "d3", "d6", "a0", "a1", "a6")

    a.move_w_absl_d(VAR_CUR_IDX, 0)
    a.bsr("get_entry_ptr")  # a6 = descriptor

    a.comment("Format Line 1: 'NNN/TTT <16-char name>'")
    a.lea_absl(VAR_LCD_BUF, 1)
    a.move_w_absl_d(VAR_CUR_IDX, 0)
    a.addq_w(1, 0)
    a.bsr("fmt_dec3")
    a.move_b_imm_an_post(ord('/'), 1)
    a.move_w_imm_d(len(entries), 0)
    a.bsr("fmt_dec3")
    a.move_b_imm_an_post(ord(' '), 1)
    a.move_l_a_a(6, 0)
    a.addq_l_a(8, 0)  # +8 = 16-char name
    a.moveq(15, 2)
    a.label("dm_name_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "dm_name_cp")
    a.bsr("lcd_write_line1_buf")

    a.comment("Check if entry is BATCH")
    a.move_b_disp_d(4, 6, 0)
    a.cmpi_b(MODE_BATCH, 0)
    a.bne("dm_single")
    a.lea_pc("str_batch_line2", 0)
    a.bsr("lcd_write_line2_str")
    a.bra("dm_done")

    a.label("dm_single")
    a.lea_pc("str_reading_line2", 0)
    a.bsr("lcd_write_line2_str")

    a.bsr("compute_chip_checksum")  # a6 -> d6.w = 16-bit checksum
    a.move_w_d_absl(6, VAR_CUR_CSUM)

    a.lea_absl(VAR_LCD_BUF, 1)
    a.move_b_imm_an_post(ord('C'), 1)
    a.move_b_imm_an_post(ord('S'), 1)
    a.move_b_imm_an_post(ord(':'), 1)
    a.move_w_d_d(6, 0)
    a.bsr("fmt_hex4")
    a.lea_pc("str_menu_tail", 0)
    a.moveq(16, 2)  # 17 chars: "  YES:DMP NO:GRP "
    a.label("dm_tail_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "dm_tail_cp")
    a.bsr("lcd_write_line2_buf")

    a.label("dm_done")
    a.movem_restore("d0", "d1", "d2", "d3", "d6", "a0", "a1", "a6")
    a.rts()

    # -----------------------------------------------------------------------
    # execute_selected_dump: dumps single chip or entire group batch
    # -----------------------------------------------------------------------
    a.label("execute_selected_dump")
    a.movem_save("d0", "d2", "d3", "d4", "d5", "d6", "a0", "a1", "a6")
    a.clr_w_absl(VAR_ABORT)

    a.comment("Light SEND (latch 4 bit 2 = 0x04) + YES/SELECT/NO")
    a.moveq(0x70, 0)
    a.moveq(0x04, 1)
    a.bsr("set_leds_state")

    a.move_w_absl_d(VAR_CUR_IDX, 0)
    a.bsr("get_entry_ptr")
    a.move_b_disp_d(4, 6, 0)
    a.cmpi_b(MODE_BATCH, 0)
    a.beq("esd_batch")

    a.comment("Single chip dump")
    a.bsr("dump_one_chip")  # a6 = desc; returns d6.w = csum
    a.move_w_absl_d(VAR_ABORT, 0)
    a.tst_w(0)
    a.bne("esd_aborted")
    a.lea_absl(VAR_LCD_BUF, 1)
    a.move_b_imm_an_post(ord('D'), 1)
    a.move_b_imm_an_post(ord('O'), 1)
    a.move_b_imm_an_post(ord('N'), 1)
    a.move_b_imm_an_post(ord('E'), 1)
    a.move_b_imm_an_post(ord(' '), 1)
    a.move_b_imm_an_post(ord('C'), 1)
    a.move_b_imm_an_post(ord('S'), 1)
    a.move_b_imm_an_post(ord(':'), 1)
    a.move_w_d_d(6, 0)
    a.bsr("fmt_hex4")
    a.lea_pc("str_anykey_tail", 0)
    a.moveq(11, 2)  # 12 chars: "   ANY KEY  "
    a.label("esd_tail_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "esd_tail_cp")
    a.bsr("lcd_write_line2_buf")
    a.bra("esd_wait_finish")

    a.label("esd_batch")
    a.move_w_disp_d(0, 6, 4)  # d4 = start_idx (high word of base_addr)
    a.move_w_disp_d(2, 6, 5)  # d5 = count (low word of base_addr)
    a.subq_w(1, 5)
    a.label("esd_batch_loop")
    a.move_w_d_d(4, 0)
    a.bsr("get_entry_ptr")
    a.move_b_disp_d(4, 6, 0)
    a.cmpi_b(MODE_BATCH, 0)
    a.beq("esd_batch_skip")
    a.bsr("dump_one_chip")
    a.move_w_absl_d(VAR_ABORT, 0)
    a.tst_w(0)
    a.bne("esd_aborted")
    a.comment("Pause ~300ms between batch chips")
    a.moveq(15, 0)
    a.bsr("delay_ms20_n")
    a.label("esd_batch_skip")
    a.addq_w(1, 4)
    a.dbf(5, "esd_batch_loop")
    a.lea_pc("str_batch_done", 0)
    a.bsr("lcd_write_line2_str")
    a.bra("esd_wait_finish")

    a.label("esd_aborted")
    a.move_w_absl_d(VAR_ABORT, 0)
    a.cmpi_w(2, 0)
    a.beq("esd_midi_err")
    a.lea_pc("str_aborted_line2", 0)
    a.bsr("lcd_write_line2_str")
    a.bra("esd_wait_finish")

    a.label("esd_midi_err")
    a.lea_absl(VAR_LCD_BUF, 1)
    a.lea_pc("str_midi_err_pfx", 0)
    a.moveq(11, 2)  # 12 chars: "MIDI ERR ST:"
    a.label("eme_pfx")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "eme_pfx")
    a.move_w_absl_d(VAR_MIDI_ERR_ST, 0)
    a.bsr("fmt_hex2")
    a.lea_pc("str_midi_err_tail", 0)
    a.moveq(9, 2)   # 10 chars: "  ANY KEY "
    a.label("eme_tail")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "eme_tail")
    a.bsr("lcd_write_line2_buf")

    a.label("esd_wait_finish")
    a.moveq(0x70, 0)
    a.moveq(0x00, 1)
    a.bsr("set_leds_state")
    a.bsr("wait_any_key_release")
    a.movem_restore("d0", "d2", "d3", "d4", "d5", "d6", "a0", "a1", "a6")
    a.rts()

    # -----------------------------------------------------------------------
    # dump_one_chip(a6 = descriptor ptr) -> returns d6.w = total checksum
    # Emits START (0x01), 128 x DATA (0x02), END (0x03), or ABORT (0x04)
    # -----------------------------------------------------------------------
    a.label("dump_one_chip")
    a.movem_save("d0", "d1", "d2", "d3", "d4", "d5", "d7", "a0", "a1", "a2")

    a.comment("Update LCD Line 1: 'DUMPING <16-char name>'")
    a.lea_absl(VAR_LCD_BUF, 1)
    a.lea_pc("str_dumping_pfx", 0)
    a.moveq(7, 2)  # 8 chars: "DUMPING "
    a.label("doc_pfx_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "doc_pfx_cp")
    a.move_l_a_a(6, 0)
    a.addq_l_a(8, 0)
    a.moveq(15, 2)
    a.label("doc_name_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "doc_name_cp")
    a.bsr("lcd_write_line1_buf")

    a.comment("Pre-compute expected checksum d6.w")
    a.bsr("compute_chip_checksum")  # d6.w

    a.comment("Send SysEx START (0x01)")
    a.moveq(0x01, 0)
    a.bsr("midi_tx_sysex_prefix")
    a.clr_w(7)  # chk7 accumulator
    a.moveq(0x01, 0)
    a.bsr("midi_tx_and_chk7")
    a.move_w_disp_d(6, 6, 0)  # chip_id
    a.bsr("midi_tx_and_chk7")
    a.move_b_disp_d(4, 6, 0)  # mode
    a.bsr("midi_tx_and_chk7")
    a.move_l_a_a(6, 0)
    a.addq_l_a(8, 0)
    a.moveq(15, 2)
    a.label("doc_start_nm")
    a.move_b_an_post_d(0, 0)
    a.bsr("midi_tx_and_chk7")
    a.dbf(2, "doc_start_nm")
    a.move_w_d_d(6, 0)
    a.bsr("midi_tx_hex4_chk7")
    a.move_w_d_d(7, 0)
    a.andi_w(0x7F, 0)
    a.bsr("midi_tx_byte")
    a.moveq(0xF7 & 0xFF if False else -9, 0)  # wait: 0xF7 = -9 signed!
    a.bsr("midi_tx_byte")
    a.move_w_absl_d(VAR_ABORT, 0)
    a.tst_w(0)
    a.bne("doc_exit")

    a.comment("Loop seq d4 = 0..127 (128 packets x 256 bytes = 32768 bytes)")
    a.clr_w(4)  # seq = 0
    a.clr_w(6)  # running total 16-bit checksum d6

    a.label("doc_pkt_loop")
    a.comment("Update LCD progress every 8 packets (d4 & 7 == 0)")
    a.move_w_d_d(4, 0)
    a.andi_w(0x0007, 0)
    a.bne("doc_skip_lcd")
    a.move_w_d_d(4, 0)
    a.bsr("draw_pkt_progress")
    a.label("doc_skip_lcd")

    a.comment("Read 256 ROM bytes at offset (seq << 8) into VAR_PKT_BUF -> d3.w = pkt_sum")
    a.move_w_d_d(4, 0)
    a.lsl_w(8, 0)  # start_idx = seq * 256
    a.lea_absl(VAR_PKT_BUF, 2)
    a.moveq(1, 1)
    a.lsl_w(8, 1)  # d1 = 256
    a.bsr("read_rom_block")  # returns d3.w = block 16-bit sum
    a.add_w(3, 6)  # accumulate into total 16-bit checksum d6

    a.comment("Send SysEx DATA packet (0x02)")
    a.moveq(0x02, 0)
    a.bsr("midi_tx_sysex_prefix")
    a.moveq(0x02, 0)
    a.bsr("midi_tx_byte")
    a.move_w_disp_d(6, 6, 0)  # chip_id
    a.bsr("midi_tx_byte")
    a.move_w_d_d(4, 0)
    a.lsr_w(4, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_byte")  # seq_hi
    a.move_w_d_d(4, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_byte")  # seq_lo

    a.lea_absl(VAR_PKT_BUF, 2)
    a.moveq(127, 5)
    a.addi_w(128, 5)  # d5 = 255 (256 iterations via dbf)
    a.label("doc_tx_nibs")
    a.move_b_an_post_d(2, 1)
    a.move_b_d_d(1, 0)
    a.lsr_w(4, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_byte")
    a.move_b_d_d(1, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_byte")
    a.dbf(5, "doc_tx_nibs")

    a.move_w_d_d(3, 0)  # pkt 16-bit sum as 4 nibbles
    a.bsr("midi_tx_hex4")
    a.moveq(-9, 0)  # 0xF7
    a.bsr("midi_tx_byte")
    a.move_w_absl_d(VAR_ABORT, 0)
    a.tst_w(0)
    a.bne("doc_exit")

    a.comment("Brief ~6ms inter-packet delay for USB-MIDI interfaces + check NO for abort")
    a.moveq(125, 0)
    a.mulu_w_imm(50, 0)  # 6250 iterations (~6.2ms @ 10MHz)
    a.label("doc_pkt_delay")
    a.dbf(0, "doc_pkt_delay")

    a.bsr("scan_switches")
    a.lea_absl(VAR_SW_ROWS, 0)
    a.move_b_disp_d(KEY_NO[0], 0, 0)
    a.btst_d(KEY_NO[1], 0)
    a.beq("doc_no_abort")
    a.move_w_imm_absl(1, VAR_ABORT)
    a.moveq(0x04, 0)
    a.bsr("midi_tx_sysex_prefix")
    a.moveq(0x04, 0)
    a.bsr("midi_tx_byte")
    a.move_w_disp_d(6, 6, 0)
    a.bsr("midi_tx_byte")
    a.moveq(-9, 0)  # 0xF7
    a.bsr("midi_tx_byte")
    a.bra("doc_exit")

    a.label("doc_no_abort")
    a.addq_w(1, 4)
    a.cmpi_w(128, 4)
    a.blt("doc_pkt_loop")

    a.moveq(127, 0)
    a.addq_w(1, 0)  # 128
    a.bsr("draw_pkt_progress")

    a.comment("Send SysEx END (0x03)")
    a.moveq(0x03, 0)
    a.bsr("midi_tx_sysex_prefix")
    a.clr_w(7)
    a.moveq(0x03, 0)
    a.bsr("midi_tx_and_chk7")
    a.move_w_disp_d(6, 6, 0)
    a.bsr("midi_tx_and_chk7")
    a.move_w_d_d(6, 0)
    a.bsr("midi_tx_hex4_chk7")
    a.move_w_d_d(7, 0)
    a.andi_w(0x7F, 0)
    a.bsr("midi_tx_byte")
    a.moveq(-9, 0)  # 0xF7
    a.bsr("midi_tx_byte")

    a.label("doc_exit")
    a.movem_restore("d0", "d1", "d2", "d3", "d4", "d5", "d7", "a0", "a1", "a2")
    a.rts()

    # -----------------------------------------------------------------------
    # draw_pkt_progress(d0.w = pkt_num 0..128)
    # -----------------------------------------------------------------------
    a.label("draw_pkt_progress")
    a.movem_save("d0", "d1", "d2", "d3", "a0", "a1")
    a.lea_absl(VAR_LCD_BUF, 1)
    a.move_b_imm_an_post(ord('P'), 1)
    a.move_b_imm_an_post(ord('K'), 1)
    a.move_b_imm_an_post(ord('T'), 1)
    a.move_b_imm_an_post(ord(' '), 1)
    a.bsr("fmt_dec3")
    a.lea_pc("str_pkt_tail", 0)
    a.moveq(16, 2)  # 17 chars: "/128   NO:ABORT  "
    a.label("dpp_cp")
    a.move_b_an_post_d(0, 3)
    a.move_b_d_an_post(3, 1)
    a.dbf(2, "dpp_cp")
    a.bsr("lcd_write_line2_buf")
    a.movem_restore("d0", "d1", "d2", "d3", "a0", "a1")
    a.rts()

    # -----------------------------------------------------------------------
    # compute_chip_checksum(a6 = descriptor ptr) -> d6.w = 16-bit Kurzweil sum
    # -----------------------------------------------------------------------
    a.label("compute_chip_checksum")
    a.movem_save("d0", "d1", "d3", "d4", "a2")
    a.clr_w(6)
    a.clr_w(4)  # pkt 0..127
    a.label("ccc_loop")
    a.move_w_d_d(4, 0)
    a.lsl_w(8, 0)  # start_idx = pkt * 256
    a.moveq(1, 1)
    a.lsl_w(8, 1)  # count = 256
    a.lea_absl(VAR_PKT_BUF, 2)
    a.bsr("read_rom_block")  # returns d3.w = 16-bit sum of 256 bytes
    a.add_w(3, 6)
    a.addq_w(1, 4)
    a.cmpi_w(128, 4)
    a.blt("ccc_loop")
    a.movem_restore("d0", "d1", "d3", "d4", "a2")
    a.rts()

    # -----------------------------------------------------------------------
    # read_rom_block(a6=desc_ptr, d0.w=start_idx, d1.w=count, a2=dst_buf) -> d3.w sum
    # Uses exact CPSOUND latch (0x1B1000) protocol for MODE_LSB chips!
    # -----------------------------------------------------------------------
    a.label("read_rom_block")
    a.movem_save("d0", "d1", "d2", "d4", "a0", "a1", "a2")
    a.clr_w(3)       # running 16-bit sum
    a.subq_w(1, 1)   # dbf counter = count - 1
    a.andi_l(0xFFFF, 0)
    a.add_l(0, 0)    # d0 = 2 * start_idx (word offset)
    a.move_l_disp_a(0, 6, 0)  # a0 = desc.base_addr
    a.adda_l_d(0, 0)          # a0 = desc.base_addr + 2*start_idx

    a.move_b_disp_d(4, 6, 4)
    a.cmpi_b(MODE_LSB, 4)
    a.beq("rrb_lsb")

    a.comment("MODE_MSB (Sound ROM MSB or CPU OS ROM): stride +2")
    a.label("rrb_msb_loop")
    a.move_b_an_ind_d(0, 2)
    a.addq_l_a(2, 0)
    a.move_b_d_an_post(2, 2)
    a.andi_w(0x00FF, 2)
    a.add_w(2, 3)
    a.dbf(1, "rrb_msb_loop")
    a.bra("rrb_done")

    a.comment("MODE_LSB (Sound ROM 2-bit LSB via CPSOUND 0x1B1000 word accesses)")
    a.label("rrb_lsb")
    a.move_l_a_a(0, 1)
    a.adda_l_imm(0x00010000, 1)  # a1 = q1 partner (+64 KB bus / +32 KB samples)
    a.label("rrb_lsb_loop")
    a.clr_w(2)  # reconstructed byte d2

    # Pair 0: (a0) -> D7->b0, D6->b1
    a.move_w_imm_absl(0x0000, ADDR_CPSOUND)
    a.move_b_an_ind_d(0, 4)
    a.move_w_absl_d(ADDR_CPSOUND, 4)
    a.btst_d(7, 4)
    a.beq("rrb_p0_b6")
    a.bset_d(0, 2)
    a.label("rrb_p0_b6")
    a.btst_d(6, 4)
    a.beq("rrb_p1")
    a.bset_d(1, 2)

    # Pair 1: 1(a0) -> D7->b2, D6->b3
    a.label("rrb_p1")
    a.move_w_imm_absl(0x0000, ADDR_CPSOUND)
    a.move_b_disp_d(1, 0, 4)
    a.move_w_absl_d(ADDR_CPSOUND, 4)
    a.btst_d(7, 4)
    a.beq("rrb_p1_b6")
    a.bset_d(2, 2)
    a.label("rrb_p1_b6")
    a.btst_d(6, 4)
    a.beq("rrb_p2")
    a.bset_d(3, 2)

    # Pair 2: (a1) -> D7->b4, D6->b5
    a.label("rrb_p2")
    a.move_w_imm_absl(0x0000, ADDR_CPSOUND)
    a.move_b_an_ind_d(1, 4)
    a.move_w_absl_d(ADDR_CPSOUND, 4)
    a.btst_d(7, 4)
    a.beq("rrb_p2_b6")
    a.bset_d(4, 2)
    a.label("rrb_p2_b6")
    a.btst_d(6, 4)
    a.beq("rrb_p3")
    a.bset_d(5, 2)

    # Pair 3: 1(a1) -> D7->b6, D6->b7
    a.label("rrb_p3")
    a.move_w_imm_absl(0x0000, ADDR_CPSOUND)
    a.move_b_disp_d(1, 1, 4)
    a.move_w_absl_d(ADDR_CPSOUND, 4)
    a.btst_d(7, 4)
    a.beq("rrb_p3_b6")
    a.bset_d(6, 2)
    a.label("rrb_p3_b6")
    a.btst_d(6, 4)
    a.beq("rrb_lsb_store")
    a.bset_d(7, 2)

    a.label("rrb_lsb_store")
    a.addq_l_a(2, 0)
    a.addq_l_a(2, 1)
    a.move_b_d_an_post(2, 2)
    a.add_w(2, 3)  # high byte of d2 is already 0
    a.dbf(1, "rrb_lsb_loop")

    a.label("rrb_done")
    a.movem_restore("d0", "d1", "d2", "d4", "a0", "a1", "a2")
    a.rts()

    # -----------------------------------------------------------------------
    # get_entry_ptr(d0.w = index) -> a6 = descriptor address
    # -----------------------------------------------------------------------
    a.label("get_entry_ptr")
    a.movem_save("d0")
    a.andi_l(0xFFFF, 0)
    a.mulu_w_imm(24, 0)
    a.lea_pc("tbl_rom_entries", 6)
    a.adda_l_d(0, 6)
    a.movem_restore("d0")
    a.rts()

    # -----------------------------------------------------------------------
    # Switch Matrix & Debounce Routines
    # -----------------------------------------------------------------------
    a.label("scan_switches")
    a.movem_save("d0", "d1", "a0")
    a.move_b_imm_absl(0x01, ADDR_PANEL_CTRL)  # reset switch scan row counter
    a.lea_absl(VAR_SW_ROWS, 0)
    a.moveq(7, 1)
    a.label("ss_rd_loop")
    a.move_b_absl_d(ADDR_PANEL_IO, 0)
    a.move_b_d_an_post(0, 0)
    a.dbf(1, "ss_rd_loop")
    a.movem_restore("d0", "d1", "a0")
    a.rts()

    a.comment("wait_key_action -> d0.w = ACT_NEXT/PREV/GRP/DUMP (with debounce)")
    a.label("wait_key_action")
    a.movem_save("d1", "a0")
    a.label("wka_wait_rel")
    a.bsr("scan_switches")
    a.bsr("any_key_down")
    a.tst_w(0)
    a.bne("wka_wait_rel")
    a.moveq(1, 0)
    a.bsr("delay_ms20_n")  # 20ms release settle

    a.label("wka_poll")
    a.bsr("scan_switches")
    a.bsr("decode_key_action")
    a.tst_w(0)
    a.beq("wka_poll")
    a.move_w_d_d(0, 1)  # save candidate action
    a.moveq(1, 0)
    a.bsr("delay_ms20_n")  # 20ms press debounce
    a.bsr("scan_switches")
    a.bsr("decode_key_action")
    a.cmp_w(1, 0)
    a.bne("wka_poll")
    a.movem_restore("d1", "a0")
    a.rts()

    a.label("wait_any_key_release")
    a.movem_save("d0")
    a.label("wak_rel1")
    a.bsr("scan_switches")
    a.bsr("any_key_down")
    a.tst_w(0)
    a.bne("wak_rel1")
    a.moveq(1, 0)
    a.bsr("delay_ms20_n")
    a.label("wak_press")
    a.bsr("scan_switches")
    a.bsr("any_key_down")
    a.tst_w(0)
    a.beq("wak_press")
    a.label("wak_rel2")
    a.bsr("scan_switches")
    a.bsr("any_key_down")
    a.tst_w(0)
    a.bne("wak_rel2")
    a.movem_restore("d0")
    a.rts()

    a.label("any_key_down")
    a.movem_save("d1", "a0")
    a.lea_absl(VAR_SW_ROWS, 0)
    a.moveq(7, 1)
    a.label("akd_loop")
    a.move_b_an_post_d(0, 0)
    a.tst_b(0)
    a.bne("akd_yes")
    a.dbf(1, "akd_loop")
    a.clr_w(0)
    a.bra("akd_done")
    a.label("akd_yes")
    a.moveq(1, 0)
    a.label("akd_done")
    a.movem_restore("d1", "a0")
    a.rts()

    a.label("decode_key_action")
    a.movem_save("a0")
    a.lea_absl(VAR_SW_ROWS, 0)
    # UP / RIGHT -> ACT_NEXT
    a.move_b_disp_d(KEY_UP[0], 0, 0)
    a.btst_d(KEY_UP[1], 0)
    a.bne("dka_next")
    a.move_b_disp_d(KEY_RIGHT[0], 0, 0)
    a.btst_d(KEY_RIGHT[1], 0)
    a.bne("dka_next")
    # DOWN / LEFT -> ACT_PREV
    a.move_b_disp_d(KEY_DOWN[0], 0, 0)
    a.btst_d(KEY_DOWN[1], 0)
    a.bne("dka_prev")
    a.move_b_disp_d(KEY_LEFT[0], 0, 0)
    a.btst_d(KEY_LEFT[1], 0)
    a.bne("dka_prev")
    # NO -> ACT_GRP
    a.move_b_disp_d(KEY_NO[0], 0, 0)
    a.btst_d(KEY_NO[1], 0)
    a.bne("dka_grp")
    # YES / SELECT -> ACT_DUMP
    a.move_b_disp_d(KEY_YES[0], 0, 0)
    a.btst_d(KEY_YES[1], 0)
    a.bne("dka_dump")
    a.move_b_disp_d(KEY_SELECT[0], 0, 0)
    a.btst_d(KEY_SELECT[1], 0)
    a.bne("dka_dump")
    a.clr_w(0)
    a.bra("dka_done")
    a.label("dka_next")
    a.moveq(ACT_NEXT, 0)
    a.bra("dka_done")
    a.label("dka_prev")
    a.moveq(ACT_PREV, 0)
    a.bra("dka_done")
    a.label("dka_grp")
    a.moveq(ACT_GRP, 0)
    a.bra("dka_done")
    a.label("dka_dump")
    a.moveq(ACT_DUMP, 0)
    a.label("dka_done")
    a.movem_restore("a0")
    a.rts()

    # -----------------------------------------------------------------------
    # LED Driver: d0.b = latch 0 (YES/SELECT/NO), d1.b = latch 4 (SEND=0x04)
    # -----------------------------------------------------------------------
    a.label("set_leds_state")
    a.move_b_imm_absl(0x08, ADDR_PANEL_CTRL)  # disable LEDs
    a.move_b_imm_absl(0x02, ADDR_PANEL_CTRL)  # reset LED latch counter
    a.move_b_d_absl(0, ADDR_PANEL_IO)       # latch 0: YES/SELECT/NO
    a.move_b_imm_absl(0x00, ADDR_PANEL_IO)    # latch 1
    a.move_b_imm_absl(0x00, ADDR_PANEL_IO)    # latch 2
    a.move_b_imm_absl(0x00, ADDR_PANEL_IO)    # latch 3
    a.move_b_d_absl(1, ADDR_PANEL_IO)       # latch 4: SEND (0x04)
    a.move_b_imm_absl(0x04, ADDR_PANEL_CTRL)  # enable LEDs
    a.rts()

    # -----------------------------------------------------------------------
    # HD44780 / Optrex 2x24 LCD Driver
    # -----------------------------------------------------------------------
    a.label("lcd_init")
    a.movem_save("d0")
    a.moveq(2, 0)
    a.bsr("delay_ms20_n")  # ~40ms cold power-on settle
    for cmd in (0x38, 0x38, 0x38, 0x0C, 0x06, 0x01, 0x02):
        a.moveq(cmd, 0)
        a.bsr("lcd_cmd")
    a.movem_restore("d0")
    a.rts()

    a.label("lcd_cmd")
    a.movem_save("d1")
    a.move_b_d_absl(0, ADDR_LCD_CMD)
    a.moveq(125, 1)
    a.mulu_w_imm(20, 1)  # 2500 (~2.5ms @ 10MHz)
    a.label("lcd_cmd_wait")
    a.dbf(1, "lcd_cmd_wait")
    a.movem_restore("d1")
    a.rts()

    a.label("lcd_data")
    a.movem_save("d1")
    a.move_b_d_absl(0, ADDR_LCD_DATA)
    a.moveq(100, 1)  # ~100us @ 10MHz
    a.label("lcd_data_wait")
    a.dbf(1, "lcd_data_wait")
    a.movem_restore("d1")
    a.rts()

    a.label("lcd_write_line1_buf")
    a.lea_absl(VAR_LCD_BUF, 0)
    a.moveq(-128, 0)  # 0x80 = Line 1 DDRAM 0x00
    a.bra("lcd_write_24")

    a.label("lcd_write_line2_buf")
    a.lea_absl(VAR_LCD_BUF, 0)
    a.label("lcd_write_line2_str")
    a.moveq(-64, 0)   # 0xC0 = Line 2 DDRAM 0x40 (Diag Rev D verified!)
    a.label("lcd_write_24")
    a.movem_save("d0", "d1", "a0")
    a.bsr("lcd_cmd")
    a.moveq(23, 1)
    a.label("lw24_loop")
    a.move_b_an_post_d(0, 0)
    a.bsr("lcd_data")
    a.dbf(1, "lw24_loop")
    a.movem_restore("d0", "d1", "a0")
    a.rts()

    # -----------------------------------------------------------------------
    # ASCII / Hex Formatters writing to (a1)+
    # -----------------------------------------------------------------------
    a.label("fmt_dec2")
    a.movem_save("d0", "d2")
    a.andi_l(0xFFFF, 0)
    a.divu_w_imm(10, 0)
    a.addi_b(ord('0'), 0)
    a.move_b_d_an_post(0, 1)
    a.swap(0)
    a.addi_b(ord('0'), 0)
    a.move_b_d_an_post(0, 1)
    a.movem_restore("d0", "d2")
    a.rts()

    a.label("fmt_dec3")
    a.movem_save("d0")
    a.andi_l(0xFFFF, 0)
    a.divu_w_imm(100, 0)
    a.addi_b(ord('0'), 0)
    a.move_b_d_an_post(0, 1)
    a.swap(0)
    a.bsr("fmt_dec2")
    a.movem_restore("d0")
    a.rts()

    a.label("fmt_hex2")
    a.movem_save("d0", "d2", "d3")
    a.move_w_d_d(0, 2)
    a.lsl_w(8, 2)
    a.moveq(1, 3)
    a.bra("fh4_loop")

    a.label("fmt_hex4")
    a.movem_save("d0", "d2", "d3")
    a.move_w_d_d(0, 2)
    a.moveq(3, 3)
    a.label("fh4_loop")
    a.move_w_d_d(2, 0)
    a.lsr_w(8, 0)
    a.lsr_w(4, 0)
    a.andi_w(0x0F, 0)
    a.cmpi_b(9, 0)
    a.ble("fh4_digit")
    a.addi_b(7, 0)
    a.label("fh4_digit")
    a.addi_b(ord('0'), 0)
    a.move_b_d_an_post(0, 1)
    a.lsl_w(4, 2)
    a.dbf(3, "fh4_loop")
    a.movem_restore("d0", "d2", "d3")
    a.rts()

    # -----------------------------------------------------------------------
    # MIDI SysEx TX Helpers (with ~1.5s TDRE timeout + raw ACIA status diagnostic)
    # -----------------------------------------------------------------------
    a.label("midi_tx_byte")
    a.movem_save("d1", "d2")
    a.move_w_absl_d(VAR_ABORT, 1)
    a.tst_w(1)
    a.bne("mtb_done")
    a.moveq(7, 2)
    a.label("mtb_outer")
    a.move_w_imm_d(0xFFFF, 1)
    a.label("mtb_poll")
    a.btst_absl(1, ADDR_MIDI_CTRL)  # bit 1 = TDRE
    a.bne("mtb_ready")
    a.dbf(1, "mtb_poll")
    a.dbf(2, "mtb_outer")
    a.comment("TDRE timed out: capture raw $001D0001 status register and set VAR_ABORT=2")
    a.move_b_absl_d(ADDR_MIDI_CTRL, 1)
    a.andi_w(0x00FF, 1)
    a.move_w_d_absl(1, VAR_MIDI_ERR_ST)
    a.move_w_imm_absl(2, VAR_ABORT)
    a.bra("mtb_done")
    a.label("mtb_ready")
    a.move_b_d_absl(0, ADDR_MIDI_DATA)
    a.label("mtb_done")
    a.movem_restore("d1", "d2")
    a.rts()

    a.label("midi_tx_and_chk7")
    a.movem_save("d1")
    a.move_w_d_d(0, 1)
    a.andi_w(0x7F, 1)
    a.add_w(1, 7)
    a.move_b_d_d(1, 0)
    a.bsr("midi_tx_byte")
    a.movem_restore("d1")
    a.rts()

    a.label("midi_tx_sysex_prefix")
    a.movem_save("d0")
    for b in (0xF0, 0x7D, 0x4B, 0x32, 0x50):  # F0 7D 'K' '2' 'P'
        sb = b - 256 if b >= 128 else b
        a.moveq(sb, 0)
        a.bsr("midi_tx_byte")
    a.movem_restore("d0")
    a.rts()

    a.label("midi_tx_hex4")
    a.movem_save("d0", "d2", "d3")
    a.move_w_d_d(0, 2)
    a.moveq(3, 3)
    a.label("mth4_loop")
    a.move_w_d_d(2, 0)
    a.lsr_w(8, 0)
    a.lsr_w(4, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_byte")
    a.lsl_w(4, 2)
    a.dbf(3, "mth4_loop")
    a.movem_restore("d0", "d2", "d3")
    a.rts()

    a.label("midi_tx_hex4_chk7")
    a.movem_save("d0", "d2", "d3")
    a.move_w_d_d(0, 2)
    a.moveq(3, 3)
    a.label("mth4c_loop")
    a.move_w_d_d(2, 0)
    a.lsr_w(8, 0)
    a.lsr_w(4, 0)
    a.andi_w(0x0F, 0)
    a.bsr("midi_tx_and_chk7")
    a.lsl_w(4, 2)
    a.dbf(3, "mth4c_loop")
    a.movem_restore("d0", "d2", "d3")
    a.rts()

    # -----------------------------------------------------------------------
    # delay_ms20_n(d0.w = count of ~20ms intervals @ 10MHz)
    # -----------------------------------------------------------------------
    a.label("delay_ms20_n")
    a.movem_save("d0", "d1")
    a.subq_w(1, 0)
    a.blt("dly_done")
    a.label("dly_outer")
    a.moveq(100, 1)
    a.mulu_w_imm(200, 1)  # 20000 iters * 10 cycles = 200,000 cycles = 20ms @ 10MHz
    a.label("dly_inner")
    a.dbf(1, "dly_inner")
    a.dbf(0, "dly_outer")
    a.label("dly_done")
    a.movem_restore("d0", "d1")
    a.rts()

    # -----------------------------------------------------------------------
    # Constant Tables & Strings (24 chars each)
    # -----------------------------------------------------------------------
    a.align2(0xFF)
    a.label("tbl_group_starts")
    for gs in group_starts:
        a.dc_w(gs)

    a.align2(0xFF)
    a.label("tbl_rom_entries")
    for idx, (addr, mode, grp, name16) in enumerate(entries):
        a.dc_l(addr)
        a.dc_b(mode, grp)
        a.dc_w(idx & 0x7F)  # 7-bit safe inside MIDI SysEx!
        a.ascii(name16)

    a.label("str_reading_line2")
    a.ascii("CS:....  READING...     ")
    a.label("str_batch_line2")
    a.ascii("BATCH   YES:DMP NO:GRP  ")
    a.label("str_batch_done")
    a.ascii("BATCH DONE    ANY KEY   ")
    a.label("str_aborted_line2")
    a.ascii("ABORTED       ANY KEY   ")
    a.label("str_midi_err_pfx")
    a.ascii("MIDI ERR ST:")       # 12 bytes
    a.label("str_midi_err_tail")
    a.ascii("  ANY KEY ")         # 10 bytes (12 + 2 + 10 = 24)
    a.label("str_menu_tail")
    a.ascii("  YES:DMP NO:GRP ")  # 17 bytes
    a.label("str_anykey_tail")
    a.ascii("   ANY KEY  ")       # 12 bytes
    a.label("str_dumping_pfx")
    a.ascii("DUMPING ")           # 8 bytes
    a.label("str_pkt_tail")
    a.ascii("/128   NO:ABORT  ")  # 17 bytes

    a.align2(0xFF)
    a.label("rom_code_end")
    a.org(0x10000, fill=0xFF)

    full_rom = a.assemble()
    assert len(full_rom) == 0x10000, f"Expected 64KB, got {len(full_rom)}"
    return full_rom, a.listing, entries


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


import hashlib


GOLDEN_FIRMWARE = {
    "K250_Dumper_U54.bin": {
        "size": 32768,
        "checksum16": 0xF017,
        "sha256": "3b9a0895405fec65dbc1b7e01b71a18c2a7a8961de7722df249e2b3b32c7c137",
    },
    "K250_Dumper_U38.bin": {
        "size": 32768,
        "checksum16": 0x00ED,
        "sha256": "e14428d32f0ac634822d6a78d8bbfaa4c50ace7eb88df6ad8e93f844cb952475",
    },
    "K250_Dumper_full.bin": {
        "size": 65536,
        "checksum16": 0xF104,
        "sha256": "e60f8c6a26ec781148c2af96fb9eddb7841e0035397d95cc9b8f87a9d98c6d04",
    },
}


def assemble_firmware_images() -> Tuple[bytes, bytes, bytes, List[str], List[Tuple[int, int, int, str]]]:
    """Assembles the 64 KB ROM and splits it into U54 (even), U38 (odd), and full images."""
    full_rom, listing, entries = build_dumper_firmware()
    u54 = full_rom[0::2]
    u38 = full_rom[1::2]
    assert len(u54) == 32768 and len(u38) == 32768
    return u54, u38, full_rom, listing, entries


def build_firmware_files(
    out_dir: Path,
    write_listing: bool = False,
    write_manifest: bool = True,
) -> Dict[str, object]:
    """Builds U54, U38, and full ROM binaries plus optional CHECKSUMS.txt and assembly listing."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    u54, u38, full_rom, listing, entries = assemble_firmware_images()

    u54_path = out_dir / "K250_Dumper_U54.bin"
    u38_path = out_dir / "K250_Dumper_U38.bin"
    full_path = out_dir / "K250_Dumper_full.bin"

    u54_path.write_bytes(u54)
    u38_path.write_bytes(u38)
    full_path.write_bytes(full_rom)

    lst_path: Optional[Path] = None
    if write_listing:
        lst_path = out_dir / "K250_Dumper.lst"
        lst_path.write_text("\n".join(listing) + "\n", encoding="utf-8")

    cs_u54 = checksum16(u54)
    cs_u38 = checksum16(u38)
    cs_full = checksum16(full_rom)
    sha_u54 = hashlib.sha256(u54).hexdigest()
    sha_u38 = hashlib.sha256(u38).hexdigest()
    sha_full = hashlib.sha256(full_rom).hexdigest()

    manifest_path: Optional[Path] = None
    if write_manifest:
        manifest_path = out_dir / "CHECKSUMS.txt"
        manifest_lines = [
            "# Kurzweil K250 MIDI ROM Dumper Firmware Checksums",
            "# EPROM target: 2x 27C256 (32 KB each)",
            f"# Targets: {len(entries)} menu entries across 26 groups (246 physical chips)",
            "",
            f"K250_Dumper_U54.bin   size={len(u54)}  CS16=0x{cs_u54:04X}  SHA256={sha_u54}",
            f"K250_Dumper_U38.bin   size={len(u38)}  CS16=0x{cs_u38:04X}  SHA256={sha_u38}",
            f"K250_Dumper_full.bin  size={len(full_rom)}  CS16=0x{cs_full:04X}  SHA256={sha_full}",
        ]
        manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    return {
        "entry_count": len(entries),
        "u54_path": u54_path,
        "u38_path": u38_path,
        "full_path": full_path,
        "lst_path": lst_path,
        "manifest_path": manifest_path,
        "u54_cs16": cs_u54,
        "u38_cs16": cs_u38,
        "full_cs16": cs_full,
        "u54_sha256": sha_u54,
        "u38_sha256": sha_u38,
        "full_sha256": sha_full,
    }


def check_firmware_files(firmware_dir: Path) -> Tuple[bool, List[str]]:
    """Verifies that binaries in `firmware_dir` match freshly assembled output and golden hashes."""
    firmware_dir = Path(firmware_dir)
    u54, u38, full_rom, _, _ = assemble_firmware_images()
    expected_map = {
        "K250_Dumper_U54.bin": u54,
        "K250_Dumper_U38.bin": u38,
        "K250_Dumper_full.bin": full_rom,
    }
    messages: List[str] = []
    ok = True
    for fname, assembled in expected_map.items():
        path = firmware_dir / fname
        golden = GOLDEN_FIRMWARE[fname]
        sha_asm = hashlib.sha256(assembled).hexdigest()
        cs_asm = checksum16(assembled)
        if sha_asm != golden["sha256"] or cs_asm != golden["checksum16"]:
            ok = False
            messages.append(
                f"[FAIL] Assembled {fname} differs from golden release hash "
                f"(CS16=0x{cs_asm:04X}, SHA256={sha_asm})"
            )
            continue
        if not path.is_file():
            ok = False
            messages.append(f"[FAIL] Missing {path}")
            continue
        on_disk = path.read_bytes()
        if on_disk != assembled:
            ok = False
            messages.append(f"[FAIL] On-disk {path} differs from freshly assembled {fname}")
        else:
            messages.append(f"[OK] {fname:<22s} ({len(on_disk)} B, CS16=0x{cs_asm:04X}, SHA256={sha_asm[:16]}...)")
    return ok, messages


def main(out_dir: Optional[Path] = None) -> None:
    target = out_dir if out_dir is not None else Path.cwd()
    res = build_firmware_files(target, write_listing=True, write_manifest=True)
    print(f"Built K250 ROM Dumper firmware ({res['entry_count']} ROM targets):")
    print(f"  U54 (even): {res['u54_path']}  (32768 bytes, Checksum-16 = 0x{res['u54_cs16']:04X})")
    print(f"  U38 (odd) : {res['u38_path']}  (32768 bytes, Checksum-16 = 0x{res['u38_cs16']:04X})")
    print(f"  Full 64KB : {res['full_path']}")
    if res["lst_path"]:
        print(f"  Listing   : {res['lst_path']}")
