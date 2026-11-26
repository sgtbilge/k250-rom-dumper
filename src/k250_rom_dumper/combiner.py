"""Combine 4 x 32 KB quarter dumps into 1-Mbit (128 KB) physical EPROM images and split back."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .catalog import (
    FACTORY_CATALOG,
    PHYSICAL_SOCKETS,
    PhysicalSocketSpec,
    checksum16,
    compute_spec_checksum,
)

ROW_FILTER_MAP: Dict[str, int] = {
    "f": 0, "rowf": 0, "row-f": 0,
    "e": 1, "rowe": 1, "row-e": 1,
    "d": 2, "blockd": 2, "block-d": 2,
    "c": 3, "blockc": 3, "block-c": 3,
    "b": 4, "blockb": 4, "block-b": 4,
    "a": 5, "blocka": 5, "block-a": 5,
}


@dataclass(frozen=True)
class CombinedChipRecord:
    key: str
    row_name: str
    socket: str
    out_path: Path
    checksum16: int
    sha256: str


def _parse_row_filter(rows: Optional[Sequence[str]]) -> Optional[set[int]]:
    if not rows:
        return None
    slots: set[int] = set()
    for r in rows:
        norm = r.strip().lower()
        if norm == "all":
            return None
        if norm not in ROW_FILTER_MAP:
            raise ValueError(f"Unknown row/block selector '{r}'. Expected one of: F, E, D, C, B, A, all.")
        slots.add(ROW_FILTER_MAP[norm])
    return slots


def combine_quarters(
    in_dir: Path,
    out_dir: Path,
    rows: Optional[Sequence[str]] = None,
    skip_verify: bool = False,
) -> List[CombinedChipRecord]:
    """Combines 4 x 32 KB quarter slices into 128 KB (1-Mbit) physical chip `.bin` files."""
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slot_filter = _parse_row_filter(rows)
    targets = [s for s in PHYSICAL_SOCKETS if slot_filter is None or s.slot in slot_filter]

    records: List[CombinedChipRecord] = []
    manifest_lines: List[str] = [
        "# Kurzweil K250 1-Mbit (131,072-byte) Physical Sound ROM Images",
        "# Quarter order: Q0 (+0x00000) | Q1 (+0x40000) | Q2 (+0x80000) | Q3 (+0xC0000)",
        "",
    ]

    for psock in targets:
        quarters: List[bytes] = []
        missing: List[str] = []
        for q_tok in psock.quarter_tokens:
            q_path = in_dir / f"{q_tok}.bin"
            if not q_path.is_file():
                missing.append(q_tok)
                continue
            q_bytes = q_path.read_bytes()
            if len(q_bytes) != 32768:
                raise ValueError(f"{q_path}: expected 32768 bytes, got {len(q_bytes)}")
            if not skip_verify:
                spec = FACTORY_CATALOG[q_tok]
                actual = compute_spec_checksum(spec, q_bytes)
                if actual != spec.expected_checksum:
                    raise ValueError(
                        f"{q_path}: checksum 0x{actual:04X} != factory 0x{spec.expected_checksum:04X}"
                    )
            quarters.append(q_bytes)

        if missing:
            if len(missing) == 4:
                continue  # Optional unpopulated row/socket skipped cleanly
            raise FileNotFoundError(
                f"Incomplete quarters for {psock.key} in {in_dir}: missing {missing}"
            )

        combined = b"".join(quarters)
        assert len(combined) == 131072
        out_path = out_dir / f"{psock.key}.bin"
        out_path.write_bytes(combined)
        cs16 = checksum16(combined)
        sha = hashlib.sha256(combined).hexdigest()
        records.append(
            CombinedChipRecord(
                key=psock.key,
                row_name=psock.row_name,
                socket=psock.socket,
                out_path=out_path,
                checksum16=cs16,
                sha256=sha,
            )
        )
        manifest_lines.append(f"{out_path.name:<16s}  CS16=0x{cs16:04X}  SHA256={sha}")

    if not records:
        raise FileNotFoundError(f"No complete 4-quarter Sound ROM sets found in {in_dir}")

    (out_dir / "1MBIT_CHECKSUMS.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    return records


def split_mbit_roms(
    in_dir: Path,
    out_dir: Path,
    rows: Optional[Sequence[str]] = None,
    skip_verify: bool = False,
) -> List[Path]:
    """Splits 128 KB (1-Mbit) physical chip images back into 4 x 32 KB quarter `.bin` files."""
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slot_filter = _parse_row_filter(rows)
    targets = [s for s in PHYSICAL_SOCKETS if slot_filter is None or s.slot in slot_filter]

    written: List[Path] = []
    for psock in targets:
        in_path = in_dir / f"{psock.key}.bin"
        if not in_path.is_file():
            continue
        data = in_path.read_bytes()
        if len(data) != 131072:
            raise ValueError(f"{in_path}: expected 131072 bytes, got {len(data)}")

        for q_idx, q_tok in enumerate(psock.quarter_tokens):
            q_bytes = data[q_idx * 32768 : (q_idx + 1) * 32768]
            if not skip_verify:
                spec = FACTORY_CATALOG[q_tok]
                actual = compute_spec_checksum(spec, q_bytes)
                if actual != spec.expected_checksum:
                    raise ValueError(
                        f"{in_path} Q{q_idx} ({q_tok}): checksum 0x{actual:04X} "
                        f"!= factory 0x{spec.expected_checksum:04X}"
                    )
            out_path = out_dir / f"{q_tok}.bin"
            out_path.write_bytes(q_bytes)
            written.append(out_path)

    if not written:
        raise FileNotFoundError(f"No 1-Mbit ROM files (e.g. RowF_U125.bin) found in {in_dir}")
    return written
