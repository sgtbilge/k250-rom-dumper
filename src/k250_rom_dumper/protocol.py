"""SysEx protocol encoder/decoder for the Kurzweil K250 ROM Dumper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .catalog import FACTORY_CATALOG, checksum16, compute_spec_checksum, diag_lsb_checksum

MAGIC = bytes([0x7D, 0x4B, 0x32, 0x50])  # 7D 'K' '2' 'P'
PKT_TYPE_START = 0x01
PKT_TYPE_DATA = 0x02
PKT_TYPE_END = 0x03
PKT_TYPE_ABORT = 0x04


def _nibbles_to_u16(nibs: bytes | List[int]) -> int:
    if len(nibs) != 4 or any(n > 0x0F for n in nibs):
        raise ValueError(f"Invalid nibble word: {list(nibs)}")
    return (nibs[0] << 12) | (nibs[1] << 8) | (nibs[2] << 4) | nibs[3]


def _u16_to_nibbles(val: int) -> bytes:
    return bytes([
        (val >> 12) & 0x0F,
        (val >> 8) & 0x0F,
        (val >> 4) & 0x0F,
        val & 0x0F,
    ])


class ActiveDump:
    """In-progress SysEx chip capture state."""

    def __init__(self, chip_id: int, mode: int, name16: str, pre_csum: int) -> None:
        self.chip_id = chip_id
        self.mode = mode
        self.name16 = name16.strip()
        self.token = self.name16.split()[0] if self.name16 else f"CHIP_{chip_id:02d}"
        self.pre_csum = pre_csum
        self.packets: Dict[int, bytes] = {}
        self.pkt_errors: List[str] = []


@dataclass(frozen=True)
class CompletedChipDump:
    """Result record for one successfully decoded and checksum-verified 32 KB ROM slice."""

    token: str
    out_path: Path
    raw_checksum16: int
    factory_checksum: Optional[int]
    expected_checksum: Optional[int]
    factory_match: bool
    is_lsb: bool


class DumperDecoder:
    """Stateful SysEx stream parser for K250 ROM Dumper messages."""

    def __init__(self, out_dir: Path, verbose: bool = True) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.active: Optional[ActiveDump] = None
        self.completed: List[CompletedChipDump] = []
        self.errors: List[str] = []

    def feed_sysex_message(self, msg: bytes) -> None:
        """Handles a single complete SysEx message including F0 and F7."""
        if len(msg) < 7 or msg[0] != 0xF0 or msg[-1] != 0xF7:
            return
        if msg[1:5] != MAGIC:
            return
        ptype = msg[5]
        body = msg[5:-1]  # starts at packet type

        if ptype == PKT_TYPE_START:
            if len(msg) != 30:
                self.errors.append(f"START packet wrong length: {len(msg)}")
                return
            chk_calc = sum(body[:-1]) & 0x7F
            chk_recv = body[-1]
            if chk_calc != chk_recv:
                self.errors.append(f"START chk7 mismatch: calc={chk_calc:02X} recv={chk_recv:02X}")
                return
            chip_id = msg[6]
            mode = msg[7]
            name16 = msg[8:24].decode("ascii", errors="replace")
            pre_csum = _nibbles_to_u16(msg[24:28])
            self.active = ActiveDump(chip_id, mode, name16, pre_csum)
            if self.verbose:
                mode_str = "LSB (CPSOUND)" if mode == 1 else "MSB/CPU"
                print(
                    f"[START] id={chip_id:02d} {self.active.name16}  "
                    f"mode={mode_str}  K250_CSUM=0x{pre_csum:04X}"
                )

        elif ptype == PKT_TYPE_DATA:
            if self.active is None:
                self.errors.append("Received DATA packet without START")
                return
            if len(msg) != 526:
                self.active.pkt_errors.append(f"DATA packet wrong length: {len(msg)}")
                return
            chip_id = msg[6]
            if chip_id != self.active.chip_id:
                self.active.pkt_errors.append(f"chip_id mismatch: {chip_id} != {self.active.chip_id}")
                return
            seq = ((msg[7] & 0x0F) << 4) | (msg[8] & 0x0F)
            nibs = msg[9:521]
            if any(b > 0x0F for b in nibs):
                self.active.pkt_errors.append(f"seq {seq}: invalid nibble byte > 0x0F")
                return
            raw = bytes(((nibs[2 * i] << 4) | nibs[2 * i + 1]) for i in range(256))
            pkt_sum_calc = sum(raw) & 0xFFFF
            pkt_sum_recv = _nibbles_to_u16(msg[521:525])
            if pkt_sum_calc != pkt_sum_recv:
                self.active.pkt_errors.append(
                    f"seq {seq}: packet checksum mismatch calc=0x{pkt_sum_calc:04X} recv=0x{pkt_sum_recv:04X}"
                )
                return
            self.active.packets[seq] = raw
            if self.verbose and ((seq + 1) % 16 == 0 or seq == 0 or seq == 127):
                print(
                    f"\r  Received {len(self.active.packets):3d}/128 packets "
                    f"(seq={seq:3d}, pkt_csum=0x{pkt_sum_calc:04X})...",
                    end="",
                    flush=True,
                )

        elif ptype == PKT_TYPE_END:
            if self.verbose:
                print()
            if self.active is None:
                self.errors.append("Received END packet without START")
                return
            if len(msg) != 13:
                self.errors.append(f"END packet wrong length: {len(msg)}")
                self.active = None
                return
            chk_calc = sum(body[:-1]) & 0x7F
            chk_recv = body[-1]
            if chk_calc != chk_recv:
                self.errors.append(f"END chk7 mismatch: calc={chk_calc:02X} recv={chk_recv:02X}")
                self.active = None
                return
            end_csum = _nibbles_to_u16(msg[7:11])
            dump = self.active
            self.active = None

            missing = [s for s in range(128) if s not in dump.packets]
            if missing or dump.pkt_errors:
                msg_err = f"[FAIL] {dump.token}: missing={missing[:10]} errors={dump.pkt_errors[:5]}"
                if self.verbose:
                    print(msg_err)
                self.errors.append(msg_err)
                return

            rom_bytes = b"".join(dump.packets[s] for s in range(128))
            assert len(rom_bytes) == 32768
            calc_csum = checksum16(rom_bytes)
            if calc_csum != dump.pre_csum or calc_csum != end_csum:
                msg_err = (
                    f"[FAIL] {dump.token}: checksum mismatch file=0x{calc_csum:04X} "
                    f"start=0x{dump.pre_csum:04X} end=0x{end_csum:04X}"
                )
                if self.verbose:
                    print(msg_err)
                self.errors.append(msg_err)
                return

            out_path = self.out_dir / f"{dump.token}.bin"
            out_path.write_bytes(rom_bytes)

            spec = FACTORY_CATALOG.get(dump.token)
            if spec is not None:
                factory_csum = compute_spec_checksum(spec, rom_bytes)
                expected_csum = spec.expected_checksum
                match = (factory_csum == expected_csum)
                kind_note = "DiagLSB " if spec.is_lsb else ""
                tag = (
                    f"FACTORY MATCH! ({kind_note}0x{factory_csum:04X})"
                    if match
                    else f"FACTORY MISMATCH ({kind_note}got 0x{factory_csum:04X}, expected 0x{expected_csum:04X})!"
                )
                if not match:
                    self.errors.append(f"{dump.token}: {tag}")
            else:
                factory_csum = diag_lsb_checksum(rom_bytes) if dump.mode == 1 else calc_csum
                expected_csum = None
                match = True
                tag = "TRANSPORT VERIFIED"

            if self.verbose:
                print(
                    f"[OK #{len(self.completed) + 1:03d}] {dump.token:<8s} -> {out_path} "
                    f"(32768 bytes, CS=0x{calc_csum:04X}) [{tag}]"
                )
            self.completed.append(
                CompletedChipDump(
                    token=dump.token,
                    out_path=out_path,
                    raw_checksum16=calc_csum,
                    factory_checksum=factory_csum,
                    expected_checksum=expected_csum,
                    factory_match=match,
                    is_lsb=(spec.is_lsb if spec is not None else dump.mode == 1),
                )
            )

        elif ptype == PKT_TYPE_ABORT:
            if self.verbose:
                print()
                print(f"[ABORT] Dump aborted by K250 front panel ({self.active.token if self.active else '?'})")
            self.active = None

    def feed_stream_bytes(self, data: bytes) -> None:
        """Feeds an arbitrary MIDI byte stream containing SysEx messages."""
        buf = bytearray()
        in_sysex = False
        for b in data:
            if b >= 0xF8:
                continue  # Ignore MIDI real-time bytes
            if b == 0xF0:
                buf = bytearray([0xF0])
                in_sysex = True
            elif b == 0xF7:
                if in_sysex:
                    buf.append(0xF7)
                    self.feed_sysex_message(bytes(buf))
                    buf.clear()
                    in_sysex = False
            elif b >= 0x80:
                in_sysex = False
                buf.clear()
            elif in_sysex:
                buf.append(b)


def encode_chip_sysex(chip_id: int, mode: int, name16: str, rom_bytes: bytes) -> bytes:
    """Encodes a 32,768-byte ROM buffer into K250 ROM Dumper SysEx messages (START + 128 DATA + END)."""
    if len(rom_bytes) != 32768:
        raise ValueError(f"Expected 32768 ROM bytes, got {len(rom_bytes)}")
    name_bytes = name16.encode("ascii", errors="replace")[:16].ljust(16, b" ")
    total_csum = checksum16(rom_bytes)
    cid = chip_id & 0x7F
    m = mode & 0x01

    out = bytearray()

    # START (0x01)
    start_body = bytearray([PKT_TYPE_START, cid, m]) + name_bytes + _u16_to_nibbles(total_csum)
    start_chk = sum(start_body) & 0x7F
    out.extend(b"\xF0" + MAGIC + start_body + bytes([start_chk, 0xF7]))

    # 128 x DATA (0x02)
    for seq in range(128):
        blk = rom_bytes[seq * 256 : (seq + 1) * 256]
        pkt_sum = checksum16(blk)
        nibs = bytearray(512)
        for i, byte_val in enumerate(blk):
            nibs[2 * i] = (byte_val >> 4) & 0x0F
            nibs[2 * i + 1] = byte_val & 0x0F
        out.extend(
            b"\xF0"
            + MAGIC
            + bytes([PKT_TYPE_DATA, cid, (seq >> 4) & 0x0F, seq & 0x0F])
            + nibs
            + _u16_to_nibbles(pkt_sum)
            + b"\xF7"
        )

    # END (0x03)
    end_body = bytearray([PKT_TYPE_END, cid]) + _u16_to_nibbles(total_csum)
    end_chk = sum(end_body) & 0x7F
    out.extend(b"\xF0" + MAGIC + end_body + bytes([end_chk, 0xF7]))
    return bytes(out)


def inspect_syx_file(syx_path: Path) -> Dict[str, object]:
    """Scans a `.syx` file and summarizes all K250 ROM Dumper messages without writing `.bin` files."""
    data = syx_path.read_bytes()
    msgs: List[bytes] = []
    buf = bytearray()
    in_sysex = False
    for b in data:
        if b >= 0xF8:
            continue
        if b == 0xF0:
            buf = bytearray([0xF0])
            in_sysex = True
        elif b == 0xF7 and in_sysex:
            buf.append(0xF7)
            msgs.append(bytes(buf))
            buf.clear()
            in_sysex = False
        elif b >= 0x80:
            in_sysex = False
            buf.clear()
        elif in_sysex:
            buf.append(b)

    k250_msgs = [m for m in msgs if len(m) >= 7 and m[1:5] == MAGIC]
    starts = [m for m in k250_msgs if m[5] == PKT_TYPE_START and len(m) == 30]
    data_pkts = [m for m in k250_msgs if m[5] == PKT_TYPE_DATA and len(m) == 526]
    ends = [m for m in k250_msgs if m[5] == PKT_TYPE_END and len(m) == 13]
    aborts = [m for m in k250_msgs if m[5] == PKT_TYPE_ABORT]
    chips = [m[8:24].decode("ascii", errors="replace").strip().split()[0] for m in starts]

    return {
        "file": str(syx_path),
        "bytes": len(data),
        "total_sysex_messages": len(msgs),
        "k250_dumper_messages": len(k250_msgs),
        "starts": len(starts),
        "data_packets": len(data_pkts),
        "ends": len(ends),
        "aborts": len(aborts),
        "chips": chips,
    }
