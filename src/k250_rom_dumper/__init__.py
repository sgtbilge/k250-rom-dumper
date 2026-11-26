"""Kurzweil K250 MIDI ROM Dumper firmware builder, receiver, and verification toolkit."""

__version__ = "1.0.0"

from .catalog import (
    CPU_OS61_SPECS,
    FACTORY_CATALOG,
    PHYSICAL_SOCKETS,
    SOUND_ROM_SPECS,
    ChipSpec,
    PhysicalSocketSpec,
    checksum16,
    diag_lsb_checksum,
    identify_rom_bytes,
    verify_chip_bytes,
)
from .protocol import DumperDecoder, MAGIC

__all__ = [
    "__version__",
    "CPU_OS61_SPECS",
    "FACTORY_CATALOG",
    "PHYSICAL_SOCKETS",
    "SOUND_ROM_SPECS",
    "ChipSpec",
    "PhysicalSocketSpec",
    "DumperDecoder",
    "MAGIC",
    "checksum16",
    "diag_lsb_checksum",
    "identify_rom_bytes",
    "verify_chip_bytes",
]
