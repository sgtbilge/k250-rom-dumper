"""Verification and completeness audit for dumped Kurzweil K250 ROM directories."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .catalog import (
    CPU_OS61_SPECS,
    SOUND_ROM_SPECS,
    ChipSpec,
    compute_spec_checksum,
)


@dataclass(frozen=True)
class ChipVerifyResult:
    """Verification outcome for a single 32 KB ROM slice."""

    token: str
    row_name: str
    slot: Optional[int]
    quarter: Optional[int]
    role: str
    is_lsb: bool
    status: str                  # ok | mismatch | blank | bad_size | missing
    expected_checksum: int
    actual_checksum: Optional[int]
    byte_count: Optional[int]
    path: Optional[str]


@dataclass
class RowSummary:
    """Aggregated verification counts for a logical ROM row or CPU OS group."""

    row_name: str
    slot: Optional[int]
    expected_count: int
    ok_count: int
    mismatch_count: int
    blank_count: int
    bad_size_count: int
    missing_count: int
    is_optional_block: bool

    @property
    def is_complete(self) -> bool:
        return self.ok_count == self.expected_count

    @property
    def is_unpopulated(self) -> bool:
        return (
            self.is_optional_block
            and self.ok_count == 0
            and self.mismatch_count == 0
            and self.bad_size_count == 0
            and (self.blank_count + self.missing_count) == self.expected_count
        )


@dataclass
class VerificationReport:
    """Full audit report for a ROM dump directory."""

    dump_dir: str
    chips: List[ChipVerifyResult]
    rows: List[RowSummary]
    sound_ok: int
    sound_total: int
    cpu_ok: int
    cpu_total: int
    u106_ok: bool
    emulator_6mb_ready: bool
    has_errors: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "dump_dir": self.dump_dir,
            "sound_ok": self.sound_ok,
            "sound_total": self.sound_total,
            "cpu_ok": self.cpu_ok,
            "cpu_total": self.cpu_total,
            "u106_ok": self.u106_ok,
            "emulator_6mb_ready": self.emulator_6mb_ready,
            "has_errors": self.has_errors,
            "rows": [
                {
                    "row_name": r.row_name,
                    "slot": r.slot,
                    "expected_count": r.expected_count,
                    "ok_count": r.ok_count,
                    "mismatch_count": r.mismatch_count,
                    "blank_count": r.blank_count,
                    "bad_size_count": r.bad_size_count,
                    "missing_count": r.missing_count,
                    "is_optional_block": r.is_optional_block,
                    "is_complete": r.is_complete,
                    "is_unpopulated": r.is_unpopulated,
                }
                for r in self.rows
            ],
            "chips": [asdict(c) for c in self.chips],
        }


def _find_chip_file(dump_dir: Path, token: str) -> Optional[Path]:
    for candidate in (
        f"{token}.bin",
        token,
        f"{token}.hex",
        f"{token.lower()}.bin",
        token.lower(),
    ):
        p = dump_dir / candidate
        if p.is_file():
            return p
    return None


def _is_blank_rom(data: bytes) -> bool:
    if not data:
        return False
    first = data[0]
    return first in (0x00, 0xFF) and all(b == first for b in data)


def _check_spec(dump_dir: Path, spec: ChipSpec) -> ChipVerifyResult:
    path = _find_chip_file(dump_dir, spec.token)
    if path is None:
        return ChipVerifyResult(
            token=spec.token,
            row_name=spec.row_name,
            slot=spec.slot,
            quarter=spec.quarter,
            role=spec.role,
            is_lsb=spec.is_lsb,
            status="missing",
            expected_checksum=spec.expected_checksum,
            actual_checksum=None,
            byte_count=None,
            path=None,
        )

    data = path.read_bytes()
    if len(data) != 32768:
        return ChipVerifyResult(
            token=spec.token,
            row_name=spec.row_name,
            slot=spec.slot,
            quarter=spec.quarter,
            role=spec.role,
            is_lsb=spec.is_lsb,
            status="bad_size",
            expected_checksum=spec.expected_checksum,
            actual_checksum=None,
            byte_count=len(data),
            path=str(path),
        )

    actual = compute_spec_checksum(spec, data)
    if actual == spec.expected_checksum:
        status = "ok"
    elif _is_blank_rom(data):
        status = "blank"
    else:
        status = "mismatch"

    return ChipVerifyResult(
        token=spec.token,
        row_name=spec.row_name,
        slot=spec.slot,
        quarter=spec.quarter,
        role=spec.role,
        is_lsb=spec.is_lsb,
        status=status,
        expected_checksum=spec.expected_checksum,
        actual_checksum=actual,
        byte_count=len(data),
        path=str(path),
    )


def verify_dump_directory(dump_dir: Path, require_cpu: bool = False) -> VerificationReport:
    """Verifies a directory of dumped K250 `.bin` files against the 248-chip factory catalog."""
    dump_dir = Path(dump_dir)
    all_specs = list(SOUND_ROM_SPECS) + list(CPU_OS61_SPECS)
    results = [_check_spec(dump_dir, spec) for spec in all_specs]

    row_order: List[str] = [
        "Row F (Basic Sounds 1)",
        "Row E (Basic Sounds 2)",
        "Sound Block D",
        "Sound Block C",
        "Sound Block B",
        "Sound Block A",
        "CPU OS 6.1",
    ]

    rows: List[RowSummary] = []
    for rname in row_order:
        items = [r for r in results if r.row_name == rname]
        slot = items[0].slot if items else None
        is_opt = slot in (2, 3, 4, 5)
        rows.append(
            RowSummary(
                row_name=rname,
                slot=slot,
                expected_count=len(items),
                ok_count=sum(1 for r in items if r.status == "ok"),
                mismatch_count=sum(1 for r in items if r.status == "mismatch"),
                blank_count=sum(1 for r in items if r.status == "blank"),
                bad_size_count=sum(1 for r in items if r.status == "bad_size"),
                missing_count=sum(1 for r in items if r.status == "missing"),
                is_optional_block=is_opt,
            )
        )

    sound_results = [r for r in results if r.slot is not None]
    cpu_results = [r for r in results if r.slot is None]
    sound_ok = sum(1 for r in sound_results if r.status == "ok")
    cpu_ok = sum(1 for r in cpu_results if r.status == "ok")
    u106_ok = any(r.token == "U106" and r.status == "ok" for r in sound_results)

    # Corruptions (mismatch / bad_size) are always errors.
    # Missing Basic Sounds (Row F / Row E) are errors; missing CPU OS 6.1 is only an error if require_cpu=True
    # (note U54/U38 hold the ROM Dumper itself during dumping, so 6/8 CPU ROMs is normal).
    has_corruption = any(r.status in ("mismatch", "bad_size") for r in results)
    basic_incomplete = any(r.slot in (0, 1) and r.status != "ok" for r in sound_results)
    cpu_incomplete = require_cpu and (cpu_ok < len(cpu_results))

    # Partial optional Sound Block (some OK, some missing/blank) is flagged as incomplete
    partial_optional_block = any(
        rs.is_optional_block and not rs.is_complete and not rs.is_unpopulated
        for rs in rows
    )

    has_errors = has_corruption or basic_incomplete or cpu_incomplete or partial_optional_block

    return VerificationReport(
        dump_dir=str(dump_dir),
        chips=results,
        rows=rows,
        sound_ok=sound_ok,
        sound_total=len(sound_results),
        cpu_ok=cpu_ok,
        cpu_total=len(cpu_results),
        u106_ok=u106_ok,
        emulator_6mb_ready=(sound_ok == len(sound_results)),
        has_errors=has_errors,
    )


def format_verification_report(report: VerificationReport, verbose: bool = False) -> str:
    """Formats a human-readable verification summary table."""
    lines: List[str] = [
        f"K250 ROM Dump Verification: {report.dump_dir}",
        "=" * 72,
        f"{'Row / Group':<26s} {'OK':>6s} {'Blank':>6s} {'Bad':>5s} {'Miss':>5s}  Status",
        "-" * 72,
    ]
    for r in report.rows:
        bad = r.mismatch_count + r.bad_size_count
        if r.is_complete:
            status = "COMPLETE"
        elif r.is_unpopulated:
            status = "NOT INSTALLED (optional)"
        elif r.row_name == "CPU OS 6.1" and r.ok_count == 6 and bad == 0 and r.missing_count == 2:
            status = "6/8 OK (U54/U38 hold Dumper)"
        else:
            status = "INCOMPLETE" if bad == 0 else "ERRORS DETECTED"
        lines.append(
            f"{r.row_name:<26s} {r.ok_count:2d}/{r.expected_count:<3d} "
            f"{r.blank_count:6d} {bad:5d} {r.missing_count:5d}  {status}"
        )

    lines.extend([
        "-" * 72,
        f"Sound ROMs : {report.sound_ok}/{report.sound_total} verified "
        f"(U106={'OK' if report.u106_ok else 'MISSING'})",
        f"CPU OS 6.1 : {report.cpu_ok}/{report.cpu_total} verified",
        f"6 MB Ready : {'YES (240/240 full K250 Sound ROM set)' if report.emulator_6mb_ready else 'NO'}",
    ])

    bad_chips = [c for c in report.chips if c.status in ("mismatch", "bad_size")]
    if bad_chips:
        lines.append("\nCorrupted / Mismatched Chips:")
        for c in bad_chips:
            if c.status == "bad_size":
                lines.append(f"  - {c.token:<10s} ({c.row_name}): invalid size {c.byte_count} bytes (expected 32768)")
            else:
                lines.append(
                    f"  - {c.token:<10s} ({c.row_name}): checksum 0x{c.actual_checksum:04X} "
                    f"!= expected 0x{c.expected_checksum:04X}"
                )

    if verbose:
        missing_sound = [c for c in report.chips if c.slot is not None and c.status == "missing"]
        if missing_sound:
            lines.append(f"\nMissing Sound ROM Slices ({len(missing_sound)}):")
            for c in missing_sound:
                lines.append(f"  - {c.token:<10s} ({c.row_name} Q{c.quarter} @ 0x{c.quarter_addr:06X})")

    return "\n".join(lines)
