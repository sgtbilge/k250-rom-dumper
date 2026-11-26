"""Cross-platform live MIDI capture backends (zero-dependency macOS CoreMIDI + optional python-rtmidi)."""

from __future__ import annotations

import ctypes
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .protocol import DumperDecoder


@dataclass(frozen=True)
class MidiSource:
    """Represents an available MIDI input port."""

    index: int
    name: str
    backend: str
    handle: int = 0


# ---------------------------------------------------------------------------
# macOS CoreMIDI via ctypes (Zero External Dependencies)
# ---------------------------------------------------------------------------
def _load_coremidi():
    cm = ctypes.CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
    cf.CFRunLoopRunInMode.restype = ctypes.c_int32

    kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")

    cm.MIDIGetNumberOfSources.argtypes = []
    cm.MIDIGetNumberOfSources.restype = ctypes.c_ulong
    cm.MIDIGetSource.argtypes = [ctypes.c_ulong]
    cm.MIDIGetSource.restype = ctypes.c_uint32
    cm.MIDIObjectGetStringProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    cm.MIDIObjectGetStringProperty.restype = ctypes.c_int32

    MIDIReadProc = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    cm.MIDIClientCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    cm.MIDIClientCreate.restype = ctypes.c_int32
    cm.MIDIClientDispose.argtypes = [ctypes.c_uint32]
    cm.MIDIClientDispose.restype = ctypes.c_int32
    cm.MIDIInputPortCreate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, MIDIReadProc, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    cm.MIDIInputPortCreate.restype = ctypes.c_int32
    cm.MIDIPortDispose.argtypes = [ctypes.c_uint32]
    cm.MIDIPortDispose.restype = ctypes.c_int32
    cm.MIDIPortConnectSource.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    cm.MIDIPortConnectSource.restype = ctypes.c_int32
    cm.MIDIPortDisconnectSource.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cm.MIDIPortDisconnectSource.restype = ctypes.c_int32

    kMIDIPropertyDisplayName = ctypes.c_void_p.in_dll(cm, "kMIDIPropertyDisplayName")
    kMIDIPropertyName = ctypes.c_void_p.in_dll(cm, "kMIDIPropertyName")

    return cm, cf, MIDIReadProc, kCFRunLoopDefaultMode, kMIDIPropertyDisplayName, kMIDIPropertyName


def _list_coremidi_sources() -> List[MidiSource]:
    cm, cf, _, _, kMIDIPropertyDisplayName, kMIDIPropertyName = _load_coremidi()
    count = cm.MIDIGetNumberOfSources()
    out: List[MidiSource] = []
    buf = ctypes.create_string_buffer(256)
    for i in range(count):
        src = cm.MIDIGetSource(i)
        sref = ctypes.c_void_p()
        err = cm.MIDIObjectGetStringProperty(src, kMIDIPropertyDisplayName, ctypes.byref(sref))
        if err != 0 or not sref.value:
            err = cm.MIDIObjectGetStringProperty(src, kMIDIPropertyName, ctypes.byref(sref))
        name = f"MIDI Source {i}"
        if err == 0 and sref.value:
            if cf.CFStringGetCString(sref, buf, 256, 0x08000100):  # kCFStringEncodingUTF8
                name = buf.value.decode("utf-8", errors="replace")
            cf.CFRelease(sref)
        out.append(MidiSource(index=i, name=name, backend="coremidi", handle=int(src)))
    return out


def _list_rtmidi_sources() -> List[MidiSource]:
    try:
        import rtmidi  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "python-rtmidi is required for live MIDI capture on this platform. "
            "Install it with: pip install 'k250-rom-dumper[midi]' (or: pip install python-rtmidi)"
        ) from exc

    midi_in = rtmidi.MidiIn()
    try:
        ports = midi_in.get_ports()
        return [MidiSource(index=i, name=name, backend="rtmidi") for i, name in enumerate(ports)]
    finally:
        del midi_in


def resolve_backend(backend: str = "auto") -> str:
    """Resolves `'auto'` to `'coremidi'` on macOS or `'rtmidi'` elsewhere."""
    b = backend.lower()
    if b == "auto":
        return "coremidi" if platform.system() == "Darwin" else "rtmidi"
    if b in ("coremidi", "rtmidi"):
        if b == "coremidi" and platform.system() != "Darwin":
            raise RuntimeError("CoreMIDI backend is only available on macOS.")
        return b
    raise ValueError(f"Unsupported MIDI backend: {backend}")


def list_midi_sources(backend: str = "auto") -> List[MidiSource]:
    """Lists available MIDI input ports using the selected backend."""
    resolved = resolve_backend(backend)
    if resolved == "coremidi":
        return _list_coremidi_sources()
    return _list_rtmidi_sources()


def _select_source(sources: List[MidiSource], port_arg: Optional[str]) -> Optional[MidiSource]:
    if not sources:
        return None
    if port_arg is None:
        if len(sources) == 1:
            return sources[0]
        print("Available MIDI Input Sources:")
        for src in sources:
            print(f"  [{src.index}] {src.name}")
        sel = input("Select port index: ").strip()
        idx = int(sel)
        return next((s for s in sources if s.index == idx), None)
    if port_arg.isdigit():
        idx = int(port_arg)
        return next((s for s in sources if s.index == idx), None)
    low = port_arg.lower()
    return next((s for s in sources if low in s.name.lower()), None)


def _run_live_coremidi(chosen: MidiSource, decoder: DumperDecoder, save_syx: Optional[Path]) -> int:
    cm, cf, MIDIReadProc, kCFRunLoopDefaultMode, _, _ = _load_coremidi()

    client = ctypes.c_uint32(0)
    in_port = ctypes.c_uint32(0)
    cname = cf.CFStringCreateWithCString(None, b"K250ROMDumper", 0x08000100)
    pname = cf.CFStringCreateWithCString(None, b"K250MIDIIn", 0x08000100)

    captured_chunks: List[bytes] = []
    sysex_buf = bytearray()
    in_sysex = False
    is_arm64 = platform.machine().lower() in ("arm64", "aarch64")

    def on_midi_packets(pkt_list_ptr: int, _read_ref: int, _src_ref: int) -> None:
        nonlocal sysex_buf, in_sysex
        num_packets = ctypes.c_uint32.from_address(pkt_list_ptr).value
        off = pkt_list_ptr + 4
        for _ in range(num_packets):
            pkt_len = ctypes.c_uint16.from_address(off + 8).value
            raw = (ctypes.c_ubyte * pkt_len).from_address(off + 10)
            chunk = bytes(raw)
            captured_chunks.append(chunk)
            for b in chunk:
                if b >= 0xF8:
                    continue
                if b == 0xF0:
                    sysex_buf = bytearray([0xF0])
                    in_sysex = True
                elif b == 0xF7:
                    if in_sysex:
                        sysex_buf.append(0xF7)
                        decoder.feed_sysex_message(bytes(sysex_buf))
                        sysex_buf.clear()
                        in_sysex = False
                elif b >= 0x80:
                    in_sysex = False
                    sysex_buf.clear()
                elif in_sysex:
                    sysex_buf.append(b)
            next_off = off + 10 + pkt_len
            if is_arm64:
                next_off = (next_off + 3) & ~3
            off = next_off

    cb = MIDIReadProc(on_midi_packets)
    try:
        if cm.MIDIClientCreate(cname, None, None, ctypes.byref(client)) != 0:
            print("Failed to create CoreMIDI client.", file=sys.stderr)
            return 1
        if cm.MIDIInputPortCreate(client, pname, cb, None, ctypes.byref(in_port)) != 0:
            print("Failed to create CoreMIDI input port.", file=sys.stderr)
            return 1
        if cm.MIDIPortConnectSource(in_port, chosen.handle, None) != 0:
            print(f"Failed to connect MIDI source [{chosen.index}] {chosen.name}", file=sys.stderr)
            return 1

        print(f"Listening on CoreMIDI [{chosen.index}] {chosen.name} -> saving ROMs to {decoder.out_dir}")
        print("Press YES/SELECT on the K250 front panel to dump. Press Ctrl+C when finished.")
        try:
            while True:
                cf.CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.2, False)
        except KeyboardInterrupt:
            print("\nStopped listening.")
    finally:
        if in_port.value:
            cm.MIDIPortDisconnectSource(in_port, chosen.handle)
            cm.MIDIPortDispose(in_port)
        if client.value:
            cm.MIDIClientDispose(client)
        if pname:
            cf.CFRelease(pname)
        if cname:
            cf.CFRelease(cname)
        if save_syx is not None and captured_chunks:
            save_syx.parent.mkdir(parents=True, exist_ok=True)
            save_syx.write_bytes(b"".join(captured_chunks))
            print(f"Saved raw SysEx stream to {save_syx}")
    return 1 if decoder.errors else 0


def _run_live_rtmidi(chosen: MidiSource, decoder: DumperDecoder, save_syx: Optional[Path]) -> int:
    try:
        import rtmidi  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            "python-rtmidi is required for live MIDI capture on this platform.\n"
            "Install it with: pip install 'k250-rom-dumper[midi]' (or: pip install python-rtmidi)",
            file=sys.stderr,
        )
        return 1

    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
    captured_chunks: List[bytes] = []

    def on_message(event, _data=None) -> None:
        message, _delta = event
        chunk = bytes(message)
        captured_chunks.append(chunk)
        decoder.feed_stream_bytes(chunk)

    midi_in.open_port(chosen.index)
    midi_in.set_callback(on_message)

    print(f"Listening on RtMidi [{chosen.index}] {chosen.name} -> saving ROMs to {decoder.out_dir}")
    print("Press YES/SELECT on the K250 front panel to dump. Press Ctrl+C when finished.")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopped listening.")
    finally:
        midi_in.close_port()
        del midi_in
        if save_syx is not None and captured_chunks:
            save_syx.parent.mkdir(parents=True, exist_ok=True)
            save_syx.write_bytes(b"".join(captured_chunks))
            print(f"Saved raw SysEx stream to {save_syx}")
    return 1 if decoder.errors else 0


def run_live_capture(
    port_arg: Optional[str],
    decoder: DumperDecoder,
    save_syx: Optional[Path] = None,
    backend: str = "auto",
) -> int:
    """Connects to a MIDI input port and streams SysEx ROM dumps until Ctrl+C."""
    resolved = resolve_backend(backend)
    sources = list_midi_sources(resolved)
    if not sources:
        print("No MIDI input sources found. Connect your USB-MIDI interface and retry.", file=sys.stderr)
        return 1

    chosen = _select_source(sources, port_arg)
    if chosen is None:
        print(f"MIDI port matching '{port_arg}' not found.", file=sys.stderr)
        return 1

    if resolved == "coremidi":
        return _run_live_coremidi(chosen, decoder, save_syx)
    return _run_live_rtmidi(chosen, decoder, save_syx)
