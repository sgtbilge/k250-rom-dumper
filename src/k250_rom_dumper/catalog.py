"""Factory ROM catalog and Kurzweil checksum algorithms for the Kurzweil K250."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ChipSpec:
    """Descriptor for a single 32 KB logical ROM slice (one quarter of a 1-Mbit Sound ROM or one CPU EPROM)."""

    token: str
    socket: str
    row_name: str
    slot: Optional[int]          # 0..5 for Sound ROM rows, None for CPU OS
    quarter: Optional[int]       # 0..3 for Sound ROM quarters, None for CPU OS
    quarter_addr: int            # 24-bit K250 bus base address
    role: str                    # LSB0, L0..L3, LSB1, R0..R3, or CPU_EVEN/CPU_ODD
    is_lsb: bool                 # True for CPSOUND 2-bit LSB slices
    expected_checksum: int       # Checksum-16 for MSB/CPU; Diag Rev D @0x665C for LSB
    raw_byte_sum: Optional[int] = None  # Optional Checksum-16 for primary LSB slices


@dataclass(frozen=True)
class PhysicalSocketSpec:
    """Descriptor for a physical 1-Mbit (128 KB) Sound ROM chip across its 4 quarters."""

    key: str                     # e.g. "RowF_U125"
    row_name: str                # e.g. "Row F (Basic Sounds 1)"
    slot: int
    socket: str                  # e.g. "U125"
    role: str                    # LSB0, L0..L3, LSB1, R0..R3
    is_lsb: bool
    quarter_tokens: Tuple[str, str, str, str]


def checksum16(data: bytes) -> int:
    """Computes the standard Kurzweil 16-bit unsigned byte sum modulo 65536."""
    return sum(data) & 0xFFFF


def _build_rev_lut() -> List[int]:
    lut = [0] * 256
    for b in range(256):
        rev = 0
        for bit in range(8):
            if (b >> bit) & 1:
                rev |= 1 << (7 - bit)
        lut[b] = ((rev & 0xF0) + (rev & 0x0F)) & 0xFF
    return lut


_DIAG_LSB_LUT = _build_rev_lut()


def diag_lsb_checksum(data: bytes) -> int:
    """Computes Diagnostic ROM Rev D (`@0x665C`) 16-bit checksum for 2-bit CPSOUND LSB ROMs.

    Each byte is bit-reversed (`b -> rev`), split into nibbles `(rev & 0xF0) + (rev & 0x0F)`,
    and accumulated modulo 65536.
    """
    lut = _DIAG_LSB_LUT
    total = 0
    for b in data:
        total += lut[b]
    return total & 0xFFFF


_CPU_OS61_ROWS: List[Tuple[str, int, str, int]] = [
    ("U54", 0x000000, "CPU0_EVEN", 0x72F7),
    ("U38", 0x000001, "CPU0_ODD",  0xD312),
    ("U53", 0x010000, "CPU1_EVEN", 0x178A),
    ("U37", 0x010001, "CPU1_ODD",  0x940F),
    ("U52", 0x020000, "CPU2_EVEN", 0xE478),
    ("U36", 0x020001, "CPU2_ODD",  0x6B0A),
    ("U51", 0x030000, "CPU3_EVEN", 0x3C38),
    ("U35", 0x030001, "CPU3_ODD",  0x88AE),
]

_ROLES_10 = ["LSB0", "L0", "L1", "L2", "L3", "LSB1", "R0", "R1", "R2", "R3"]

_ROW_F_STEMS = ["U125", "U124", "U123", "U122", "U121", "U120", "U119", "U118", "U117", "U116"]
_ROW_E_STEMS = ["U107", "U106", "U105", "U104", "U103", "U102", "U101", "U100", "U99", "U98"]
_BLK_D_STEMS = ["U86", "U85", "U84", "U83", "U82", "U81", "U80", "U79", "U78", "U77"]
_BLK_C_STEMS = ["U66", "U65", "U64", "U63", "U62", "U61", "U60", "U59", "U58", "U57"]
_BLK_B_STEMS = ["U47", "U46", "U45", "U44", "U43", "U42", "U41", "U40", "U39", "U38"]
_BLK_A_STEMS = ["U20", "U19", "U18", "U17", "U16", "U15", "U14", "U13", "U12", "U11"]

_ROW_META: Dict[int, Tuple[str, str, List[str]]] = {
    0: ("Row F (Basic Sounds 1)", "RowF", _ROW_F_STEMS),
    1: ("Row E (Basic Sounds 2)", "RowE", _ROW_E_STEMS),
    2: ("Sound Block D", "BlockD", _BLK_D_STEMS),
    3: ("Sound Block C", "BlockC", _BLK_C_STEMS),
    4: ("Sound Block B", "BlockB", _BLK_B_STEMS),
    5: ("Sound Block A", "BlockA", _BLK_A_STEMS),
}

# Raw byte-sum (Checksum-16) of primary-quarter LSB chips when read via CPSOUND
_PRIMARY_LSB_RAW_SUMS: Dict[str, int] = {
    "U125": 0x0E13, "U120": 0xE583,
    "U107": 0x3B15, "U102": 0x8912,
    "107K250": 0x359D, "102K250": 0x6FFD,
    "086K250": 0xF5BB, "081K250": 0xB6B0,
    "066K250": 0x16ED, "061K250": 0x45BC,
    "047K250": 0x4DB5, "042K250": 0x6158,
}


def _suf(stems: List[str], tag: str) -> List[str]:
    return [f"{s}_{tag}" for s in stems]


# The 24 Sound ROM quarters (6 slots x 4 quarters) of the full 6 MB set
_SOUND_QUARTER_SPECS: List[Tuple[int, int, int, List[str], List[int]]] = [
    # Slot 0: Row F (0x200000..0x2C0000)
    (0, 0, 0x200000, _suf(_ROW_F_STEMS, "20"), [0x658E, 0xC043, 0xF18C, 0xCD97, 0x1E0D, 0x0F3D, 0xF755, 0x1905, 0xDF60, 0x274D]),
    (0, 1, 0x240000, _suf(_ROW_F_STEMS, "24"), [0x92B8, 0x26B9, 0x9C32, 0x5BCA, 0x53AE, 0x9A46, 0x1E0B, 0x6DD2, 0x6902, 0x4842]),
    (0, 2, 0x280000, _ROW_F_STEMS,              [0x66BD, 0x9D4A, 0xCEA2, 0x3B45, 0xF5C6, 0x8FA7, 0xCD37, 0xAC82, 0x24E9, 0xF38D]),
    (0, 3, 0x2C0000, _suf(_ROW_F_STEMS, "2C"), [0x0B2F, 0xDAB7, 0x3EBE, 0x7931, 0x0B2C, 0x1788, 0x3530, 0x050F, 0x78F7, 0x05ED]),
    # Slot 1: Row E (0x300000..0x3C0000)
    (1, 0, 0x300000, _suf(_ROW_E_STEMS, "30"), [0xDBAF, 0xB166, 0x1715, 0x06CB, 0xA348, 0x637C, 0x58A8, 0x2968, 0x5329, 0xE873]),
    (1, 1, 0x340000, _suf(_ROW_E_STEMS, "34"), [0x88E2, 0x66F0, 0xFB2F, 0x0B9B, 0x004E, 0x77EF, 0x1208, 0xE2AF, 0x0F54, 0x2650]),
    (1, 2, 0x380000, _ROW_E_STEMS,              [0xC181, 0x0F81, 0x0A52, 0xA8CC, 0xAF9B, 0xADE6, 0x4751, 0xEB20, 0xB54A, 0xC3CD]),
    (1, 3, 0x3C0000, _suf(_ROW_E_STEMS, "3C"), [0x0C3F, 0x66F0, 0x1942, 0xA70A, 0x9ACB, 0x88C1, 0x8024, 0x10C7, 0x19B9, 0x700D]),
    # Slot 2: Sound Block D (0x400000..0x4C0000)
    (2, 0, 0x400000, ["107K250", "106K250", "105K250", "104K250", "103K250",
                      "102K250", "101K250", "100K250", "099K250", "098K250"],
                                               [0x9BFA, 0xF459, 0x4649, 0x20EB, 0xD191, 0x6A76, 0xC59B, 0x4B75, 0x2988, 0x1BFC]),
    (2, 1, 0x440000, _suf(_BLK_D_STEMS, "44"), [0xFA16, 0x0CF1, 0xE389, 0x227E, 0xDA1D, 0x94F5, 0xD0A0, 0x5E4B, 0x1BAD, 0xEA9D]),
    (2, 2, 0x480000, _suf(_BLK_D_STEMS, "48"), [0x9FEA, 0x179D, 0x5A9C, 0xA41C, 0xD444, 0x797B, 0xF363, 0x5675, 0x9EEF, 0xC219]),
    (2, 3, 0x4C0000, _suf(_BLK_D_STEMS, "4C"), [0xBF3B, 0xE8DC, 0x6789, 0xCEFD, 0x4DEF, 0x5098, 0xAA44, 0x6EC8, 0xC31B, 0x442F]),
    # Slot 3: Sound Block C (0x500000..0x5C0000)
    (3, 0, 0x500000, ["086K250", "085K250", "084K250", "083K250", "082K250",
                      "081K250", "080K250", "079K250", "078K250", "077K250"],
                                               [0x1BF6, 0x6AEE, 0xAA79, 0xD13D, 0x76BC, 0x7F14, 0x8F87, 0xAF56, 0x7B16, 0x7993]),
    (3, 1, 0x540000, _suf(_BLK_C_STEMS, "54"), [0xF239, 0x122C, 0x0E07, 0x345E, 0x7D45, 0xB85B, 0x071E, 0x2188, 0x2EC3, 0x7220]),
    (3, 2, 0x580000, _suf(_BLK_C_STEMS, "58"), [0x743B, 0x0FD4, 0x2B07, 0x2F5F, 0xAA9E, 0x34CE, 0xC6D7, 0x4FA6, 0xE22F, 0xC5D2]),
    (3, 3, 0x5C0000, _suf(_BLK_C_STEMS, "5C"), [0x82B4, 0x7CEA, 0x2342, 0x7C95, 0xC7A0, 0x2BC3, 0x5CCA, 0xE1A7, 0xBA7D, 0x7DAE]),
    # Slot 4: Sound Block B (0x600000..0x6C0000)
    (4, 0, 0x600000, _suf(_BLK_B_STEMS, "60"), [0xD74D, 0x62E0, 0x04A2, 0x45E8, 0x3210, 0x99F4, 0x351C, 0x5B73, 0x60CB, 0x68C9]),
    (4, 1, 0x640000, _suf(_BLK_B_STEMS, "64"), [0x45E3, 0x2B15, 0x2EEA, 0x2BF6, 0x94CD, 0x7F2E, 0x5972, 0x1533, 0x1A0B, 0x8604]),
    (4, 2, 0x680000, ["066K250", "065K250", "064K250", "063K250", "062K250",
                      "061K250", "060K250", "059K250", "058K250", "057K250"],
                                               [0x7368, 0x7EA2, 0x2D9A, 0xECA3, 0xE4DE, 0xF86E, 0x9E8E, 0x6C1F, 0xF783, 0xF87A]),
    (4, 3, 0x6C0000, _suf(_BLK_B_STEMS, "6C"), [0xA941, 0xDA2E, 0x8F0F, 0xC60E, 0xDE30, 0xDBA2, 0x42C0, 0x7C35, 0x9F52, 0x2378]),
    # Slot 5: Sound Block A (0x700000..0x7C0000)
    (5, 0, 0x700000, _suf(_BLK_A_STEMS, "70"), [0xC7BB, 0xA17E, 0xCCC2, 0x8967, 0xD909, 0x5DA2, 0x9CAA, 0x3D16, 0xDC3B, 0x3979]),
    (5, 1, 0x740000, _suf(_BLK_A_STEMS, "74"), [0xDC0A, 0x3DB6, 0xCDEC, 0x49C0, 0xE0E7, 0xB1B5, 0xAD97, 0xE6F0, 0x3706, 0xD186]),
    (5, 2, 0x780000, ["047K250", "046K250", "045K250", "044K250", "043K250",
                      "042K250", "041K250", "040K250", "039K250", "038K250"],
                                               [0xD0DE, 0xBDFD, 0x7E1E, 0x4C9A, 0xE492, 0xE8D3, 0xEC31, 0x7D29, 0x34DE, 0xD3AD]),
    (5, 3, 0x7C0000, _suf(_BLK_A_STEMS, "7C"), [0xA6D1, 0x632D, 0x48EE, 0xFC83, 0xCAF4, 0xC807, 0xB1C0, 0xC921, 0x0883, 0x7047]),
]


def _build_catalog() -> Tuple[List[ChipSpec], List[ChipSpec], Dict[str, ChipSpec], List[PhysicalSocketSpec]]:
    cpu_specs: List[ChipSpec] = []
    for socket, addr, role, csum in _CPU_OS61_ROWS:
        cpu_specs.append(
            ChipSpec(
                token=socket,
                socket=socket,
                row_name="CPU OS 6.1",
                slot=None,
                quarter=None,
                quarter_addr=addr,
                role=role,
                is_lsb=False,
                expected_checksum=csum,
                raw_byte_sum=csum,
            )
        )

    sound_specs: List[ChipSpec] = []
    # Map (slot, idx_in_10) -> {q: token}
    socket_quarters: Dict[Tuple[int, int], Dict[int, str]] = {}

    for slot, q, qaddr, tokens, csums in _SOUND_QUARTER_SPECS:
        row_name, _, stems = _ROW_META[slot]
        for idx in range(10):
            token = tokens[idx]
            role = _ROLES_10[idx]
            is_lsb = idx in (0, 5)
            socket = stems[idx]
            socket_quarters.setdefault((slot, idx), {})[q] = token
            sound_specs.append(
                ChipSpec(
                    token=token,
                    socket=socket,
                    row_name=row_name,
                    slot=slot,
                    quarter=q,
                    quarter_addr=qaddr,
                    role=role,
                    is_lsb=is_lsb,
                    expected_checksum=csums[idx],
                    raw_byte_sum=_PRIMARY_LSB_RAW_SUMS.get(token) if is_lsb else csums[idx],
                )
            )

    catalog: Dict[str, ChipSpec] = {}
    for spec in sound_specs + cpu_specs:
        if spec.token in catalog:
            raise AssertionError(f"Duplicate token in catalog: {spec.token}")
        catalog[spec.token] = spec

    physical_sockets: List[PhysicalSocketSpec] = []
    for slot in range(6):
        row_name, prefix, stems = _ROW_META[slot]
        for idx, stem in enumerate(stems):
            qmap = socket_quarters[(slot, idx)]
            physical_sockets.append(
                PhysicalSocketSpec(
                    key=f"{prefix}_{stem}",
                    row_name=row_name,
                    slot=slot,
                    socket=stem,
                    role=_ROLES_10[idx],
                    is_lsb=(idx in (0, 5)),
                    quarter_tokens=(qmap[0], qmap[1], qmap[2], qmap[3]),
                )
            )

    return cpu_specs, sound_specs, catalog, physical_sockets


CPU_OS61_SPECS, SOUND_ROM_SPECS, FACTORY_CATALOG, PHYSICAL_SOCKETS = _build_catalog()


def compute_spec_checksum(spec: ChipSpec, data: bytes) -> int:
    """Computes the factory checksum for `spec` (`diag_lsb_checksum` for LSB, `checksum16` for MSB/CPU)."""
    return diag_lsb_checksum(data) if spec.is_lsb else checksum16(data)


def verify_chip_bytes(token: str, data: bytes) -> Tuple[Optional[ChipSpec], int, bool]:
    """Verifies 32 KB `data` against the factory catalog entry for `token`.

    Returns `(spec, actual_checksum, is_match)`. If `token` is not in the factory catalog,
    returns `(None, checksum16(data), False)`.
    """
    spec = FACTORY_CATALOG.get(token)
    if spec is None:
        return None, checksum16(data), False
    actual = compute_spec_checksum(spec, data)
    return spec, actual, (actual == spec.expected_checksum)


def identify_rom_bytes(data: bytes) -> List[ChipSpec]:
    """Matches a 32 KB (or 128 KB physical) ROM buffer against known factory checksums."""
    matches: List[ChipSpec] = []
    if len(data) == 32768:
        cs16 = checksum16(data)
        dlsb = diag_lsb_checksum(data)
        for spec in SOUND_ROM_SPECS + CPU_OS61_SPECS:
            val = dlsb if spec.is_lsb else cs16
            if val == spec.expected_checksum:
                matches.append(spec)
    return matches
