#!/usr/bin/env python3
"""
Exports K250 Sound Block D (reconstructed), Sound Block C (reconstructed),
and reference intact Basic Sounds Row F waveforms as:
  - 16-bit mono 25 kHz PCM WAVs (*_full.wav and *_looped.wav)
  - Full CSV sample dumps (*.csv)
  - 1400x760 diagnostic PNG waveform plots (*.png) highlighting 0x0D runs & seams
  - Self-contained dark-mode HTML gallery (ExportedWaveforms/index.html)
"""

import argparse
import csv
import html
import math
import os
import re
import struct
import wave
from PIL import Image, ImageDraw, ImageFont

ROMS_BASE = os.environ.get("K250_SOUNDS_BINARIES_DIR", ".")
OUT_BASE = os.path.abspath("./ExportedWaveforms")

SAMPLE_RATE = 25000

BLOCKS = [
    {
        "id": "block_d",
        "title": "Sound Block D (Reconstructed — hobchim1)",
        "folder": "SoundBlockD_Reconstructed",
        "dir": os.path.join(ROMS_BASE, "K250 Sound Block D (Reconstructed)"),
        "chips": [
            "107K250", "106K250", "105K250", "104K250", "103K250",
            "102K250", "101K250", "100K250", "099K250", "098K250",
        ],
        "wdir_offset": 0x3A06,
        "max_waves": 12,
        "is_reference": False,
        "has_blank_083": False,
    },
    {
        "id": "block_c",
        "title": "Sound Block C (Reconstructed — RKBLKB1B)",
        "folder": "SoundBlockC_Reconstructed",
        "dir": os.path.join(ROMS_BASE, "K250 Sound Block C (Reconstructed)"),
        "chips": [
            "086K250", "085K250", "084K250", "083K250", "082K250",
            "081K250", "080K250", "079K250", "078K250", "077K250",
        ],
        "wdir_offset": 0x6000,
        "max_waves": 8,
        "is_reference": False,
        "has_blank_083": True,
    },
    {
        "id": "row_f",
        "title": "Reference: Intact Basic Sounds (Row F — Piano / Strings)",
        "folder": "Reference_BasicSounds_RowF",
        "dir": os.path.join(ROMS_BASE, "K250 Basic Sounds"),
        "chips": [
            "U125.hex", "U124.hex", "U123.hex", "U122.hex", "U121.hex",
            "U120.hex", "U119.hex", "U118.hex", "U117.hex", "U116.hex",
        ],
        "wdir_offset": 0x0400,
        "max_waves": 4,
        "is_reference": True,
        "has_blank_083": False,
    },
]

# Palette (PIL RGB)
BG_DARK = (20, 22, 26)
PANEL_BG = (27, 30, 36)
BORDER_CLR = (54, 60, 72)
ZERO_CLR = (72, 78, 92)
TEXT_MAIN = (232, 236, 242)
TEXT_MUTED = (154, 164, 180)
WAVE_FULL_CLR = (76, 224, 179)     # #4CE0B3
WAVE_ATK_CLR = (124, 198, 254)     # #7CC6FE
WAVE_LOOP_CLR = (244, 162, 97)     # #F4A261
SEG_LINE_CLR = (244, 208, 63)      # Yellow
MARKER_0D_CLR = (235, 77, 75)      # Red
CALLOUT_BG_CLR = (150, 28, 32)


def get_font(size):
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/Library/Fonts/Courier New.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


FONT_TITLE = get_font(15)
FONT_SUB = get_font(12)
FONT_SMALL = get_font(11)


def assemble_rom_block(directory, chip_names):
    """Assembles 256 KB Sound ROM block matching K250SoundMemory.dmaReadSampleWord."""
    chips = []
    for name in chip_names:
        path = os.path.join(directory, name)
        with open(path, "rb") as f:
            data = f.read()
        if len(data) != 32768:
            raise ValueError(f"Expected 32768 bytes in {path}, got {len(data)}")
        chips.append(data)

    msb = bytearray(262144)
    w16 = [0] * 262144
    s10 = [0] * 262144

    for addr in range(262144):
        q = (addr >> 16) & 3
        idx = (addr >> 1) & 0x7FFF
        odd = addr & 1
        m = chips[(1 if odd == 0 else 6) + q][idx]
        lsb_byte = chips[0 if q < 2 else 5][idx]
        pair_idx = ((q & 1) << 1) | odd
        b7 = (lsb_byte >> (2 * pair_idx)) & 1
        b6 = (lsb_byte >> (2 * pair_idx + 1)) & 1
        u16 = (m << 8) | (b7 << 7) | (b6 << 6)
        val16 = u16 - 65536 if u16 >= 32768 else u16
        msb[addr] = m
        w16[addr] = val16
        s10[addr] = val16 >> 6

    return bytes(msb), w16, s10


def safe_filename(name):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return cleaned if cleaned else "UNNAMED"


def find_0d_runs(samples):
    """Returns list of (start_idx, length) for contiguous msb == 0x0D runs."""
    runs = []
    i = 0
    n = len(samples)
    while i < n:
        if samples[i]["msb"] == 0x0D:
            j = i
            while j < n and samples[j]["msb"] == 0x0D:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    return runs


def parse_wave_directory(msb, wdir_offset, max_waves):
    count = msb[wdir_offset + 1]
    limit = min(count, max_waves)
    entries = []
    for i in range(limit):
        off = wdir_offset + 2 + i * 32
        num, hi, lo = struct.unpack(">HHH", msb[off : off + 6])
        raw_name = msb[off + 6 : off + 32].split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", "replace").strip()
        ptr = (hi << 16) | lo
        entries.append((num, name, ptr))
    return entries


def parse_wave_descriptor(msb, ptr, has_blank_083=False):
    """
    Validates descriptor at `ptr` and returns (segments, parse_note, raw_desc_hex).
    segments: list of (start_addr, end_addr) inclusive.
    """
    if ptr + 32 > len(msb):
        return [], f"Descriptor pointer 0x{ptr:06X} out of 256KB range", ""

    raw_desc_hex = msb[ptr : min(len(msb), ptr + 44)].hex()
    seg_tbl = struct.unpack(">I", msb[ptr + 6 : ptr + 10])[0]
    b10, b11, b12, b13 = msb[ptr + 10 : ptr + 14]
    addr_tbl = struct.unpack(">I", msb[ptr + 14 : ptr + 18])[0]

    if addr_tbl >= 0x40000 or seg_tbl > 0x40000 or addr_tbl >= seg_tbl:
        note = (
            f"Descriptor byte-aligned/corrupted at 0x{ptr:06X} "
            f"(addr_tbl=0x{addr_tbl:08X}, seg_tbl=0x{seg_tbl:08X}, b10..13={b10:02X} {b11:02X} {b12:02X} {b13:02X}); "
            f"raw={raw_desc_hex}"
        )
        return [], note, raw_desc_hex

    avail_pairs = (seg_tbl - addr_tbl) // 8
    n_pairs = min(b12, avail_pairs) if b12 > 0 else avail_pairs
    if n_pairs <= 0:
        note = (
            f"No valid segment pairs at 0x{ptr:06X} "
            f"(addr_tbl=0x{addr_tbl:06X}, seg_tbl=0x{seg_tbl:06X}, b12={b12}); raw={raw_desc_hex}"
        )
        return [], note, raw_desc_hex

    segments = []
    notes = []
    if (seg_tbl - addr_tbl) != b12 * 8:
        notes.append(f"Table size mismatch: (seg_tbl-addr_tbl)={seg_tbl - addr_tbl} vs b12*8={b12 * 8}")

    q2_overlap = False
    for k in range(n_pairs):
        p = addr_tbl + k * 8
        s, e = struct.unpack(">II", msb[p : p + 8])
        if s >= 0x40000 or e >= 0x40000 or e < s or (e - s + 1) > 65536:
            notes.append(f"Skipped invalid seg{k} [0x{s:08X}..0x{e:08X}]")
            continue
        segments.append((s, e))
        if max(s, 0x020000) <= min(e, 0x02FFFF):
            q2_overlap = True

    if has_blank_083 and q2_overlap:
        notes.append("Overlaps Q2 (0x020000..0x02FFFF): even-address bytes come from blank 083K250 (0xFB)")

    if not segments:
        notes.append(f"No valid segments decoded from 0x{ptr:06X}; raw={raw_desc_hex}")

    return segments, "; ".join(notes), raw_desc_hex


def write_wav(path, int16_samples):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        pcm = struct.pack(f"<{len(int16_samples)}h", *int16_samples)
        wf.writeframes(pcm)


def write_csv(path, samples):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_idx", "seg_idx", "addr_hex", "msb_hex", "s10", "is_0x0D"])
        for i, s in enumerate(samples):
            writer.writerow([
                i,
                s["seg_idx"],
                f"0x{s['addr']:06X}",
                f"0x{s['msb']:02X}",
                s["s10"],
                1 if s["msb"] == 0x0D else 0,
            ])


def y_for_s10(val, y_top, y_bot):
    mid = 0.5 * (y_top + y_bot)
    half = 0.5 * (y_bot - y_top) - 4.0
    norm = max(-512, min(511, val)) / 512.0
    return mid - norm * half


def draw_dashed_vline(draw, x, y0, y1, color, dash=5, gap=4):
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y1, y + dash))], fill=color, width=1)
        y += dash + gap


def render_wave_png(
    path,
    block_title,
    wave_num,
    wave_name,
    segments,
    samples,
    seg_boundaries,
    runs_0d,
    loop_samples,
    wrap_jump,
    parse_note,
):
    W, H = 1400, 760
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img, "RGBA")

    # Header bar
    count_0d = sum(1 for s in samples if s["msb"] == 0x0D)
    max_0d_run = max((r[1] for r in runs_0d), default=0)
    seg_desc = ", ".join(f"[{s:05X}..{e:05X}]" for s, e in segments[:4])
    if len(segments) > 4:
        seg_desc += f" (+{len(segments) - 4} more)"

    title_txt = f"{block_title}  |  Wave #{wave_num} {wave_name}"
    stats_txt = (
        f"Segs: {len(segments)} {seg_desc}  |  Total: {len(samples)} smp ({len(samples)/SAMPLE_RATE:.2f}s)  |  "
        f"0x0D cnt: {count_0d}  |  Max 0x0D run: {max_0d_run}  |  Loop wrap jump: {wrap_jump}"
    )
    draw.text((20, 10), title_txt, fill=TEXT_MAIN, font=FONT_TITLE)
    draw.text((20, 32), stats_txt, fill=MARKER_0D_CLR if max_0d_run >= 4 else TEXT_MUTED, font=FONT_SUB)
    if parse_note:
        draw.text((20, 46), f"Note: {parse_note[:155]}", fill=(244, 180, 80), font=FONT_SMALL)

    px0, px1 = 56, W - 24
    pw = px1 - px0

    # --- Panel 1: Full Waveform (y=60..280) ---
    p1_y0, p1_y1 = 62, 280
    draw.rectangle([px0, p1_y0, px1, p1_y1], fill=PANEL_BG, outline=BORDER_CLR)
    z1 = int(y_for_s10(0, p1_y0, p1_y1))
    draw.line([(px0, z1), (px1, z1)], fill=ZERO_CLR, width=1)
    draw.text((px0 + 6, p1_y0 + 4), "FULL WAVEFORM (concatenated segments, red = 0x0D)", fill=TEXT_MUTED, font=FONT_SMALL)

    n = len(samples)
    if n > 0:
        # Translucent 0x0D sample markers
        marked_px = set()
        for idx, s in enumerate(samples):
            if s["msb"] == 0x0D:
                xp = px0 + int((idx / max(1, n - 1)) * (pw - 1))
                if xp not in marked_px:
                    marked_px.add(xp)
                    draw.line([(xp, p1_y0 + 1), (xp, p1_y1 - 1)], fill=(235, 77, 75, 55), width=1)

        # Min/max span per x-pixel
        for px_i in range(pw):
            i0 = int(px_i * n / pw)
            i1 = max(i0 + 1, int((px_i + 1) * n / pw))
            chunk = [samples[k]["s10"] for k in range(i0, min(n, i1))]
            if not chunk:
                continue
            y_hi = int(y_for_s10(max(chunk), p1_y0, p1_y1))
            y_lo = int(y_for_s10(min(chunk), p1_y0, p1_y1))
            if y_lo == y_hi:
                y_lo += 1
            draw.line([(px0 + px_i, y_hi), (px0 + px_i, y_lo)], fill=WAVE_FULL_CLR, width=1)

        # Yellow dashed segment boundaries
        for b_idx in seg_boundaries[1:]:
            xp = px0 + int((b_idx / max(1, n - 1)) * (pw - 1))
            draw_dashed_vline(draw, xp, p1_y0 + 1, p1_y1 - 1, SEG_LINE_CLR)

        # Callouts for runs >= 3
        for r_start, r_len in runs_0d:
            if r_len >= 3:
                x_mid = px0 + int(((r_start + r_len * 0.5) / max(1, n - 1)) * (pw - 1))
                lbl = f"0x0D x{r_len}"
                bx = min(px1 - 74, max(px0 + 4, x_mid - 30))
                by = p1_y0 + 20
                draw.rectangle([bx - 3, by - 2, bx + len(lbl) * 7 + 4, by + 13], fill=CALLOUT_BG_CLR, outline=MARKER_0D_CLR)
                draw.text((bx, by), lbl, fill=(255, 240, 240), font=FONT_SMALL)

    # --- Panel 2: Attack Start Zoom (first 256 samples of seg0, y=310..500) ---
    p2_y0, p2_y1 = 310, 500
    draw.rectangle([px0, p2_y0, px1, p2_y1], fill=PANEL_BG, outline=BORDER_CLR)
    z2 = int(y_for_s10(0, p2_y0, p2_y1))
    draw.line([(px0, z2), (px1, z2)], fill=ZERO_CLR, width=1)
    atk_slice = samples[:256]
    draw.text(
        (px0 + 6, p2_y0 + 4),
        f"ZOOM: ATTACK START (first {len(atk_slice)} samples of seg0, red dot = 0x0D)",
        fill=TEXT_MUTED,
        font=FONT_SMALL,
    )
    if len(atk_slice) >= 2:
        pts = []
        for idx, s in enumerate(atk_slice):
            xp = px0 + 8 + int((idx / max(1, len(atk_slice) - 1)) * (pw - 16))
            yp = int(y_for_s10(s["s10"], p2_y0, p2_y1))
            pts.append((xp, yp))
        draw.line(pts, fill=WAVE_ATK_CLR, width=2)
        for idx, (xp, yp) in enumerate(pts):
            if atk_slice[idx]["msb"] == 0x0D:
                draw.ellipse([xp - 4, yp - 4, xp + 4, yp + 4], fill=MARKER_0D_CLR, outline=(255, 255, 255))
            else:
                draw.ellipse([xp - 1, yp - 1, xp + 1, yp + 1], fill=(210, 235, 255))

    # --- Panel 3: Final Segment Loop Seam Zoom (y=530..720) ---
    p3_y0, p3_y1 = 530, 720
    draw.rectangle([px0, p3_y0, px1, p3_y1], fill=PANEL_BG, outline=BORDER_CLR)
    z3 = int(y_for_s10(0, p3_y0, p3_y1))
    draw.line([(px0, z3), (px1, z3)], fill=ZERO_CLR, width=1)

    tail_len = min(128, len(loop_samples))
    head_len = min(128, len(loop_samples))
    seam_slice = loop_samples[-tail_len:] + loop_samples[:head_len] if loop_samples else []
    draw.text(
        (px0 + 6, p3_y0 + 4),
        f"ZOOM: LOOP SEAM (last {tail_len} -> first {head_len} of final seg | end={loop_samples[-1]['s10'] if loop_samples else 0} -> start={loop_samples[0]['s10'] if loop_samples else 0}, jump={wrap_jump})",
        fill=TEXT_MUTED,
        font=FONT_SMALL,
    )
    if len(seam_slice) >= 2:
        pts = []
        for idx, s in enumerate(seam_slice):
            xp = px0 + 8 + int((idx / max(1, len(seam_slice) - 1)) * (pw - 16))
            yp = int(y_for_s10(s["s10"], p3_y0, p3_y1))
            pts.append((xp, yp))
        seam_x = px0 + 8 + int((tail_len / max(1, len(seam_slice) - 1)) * (pw - 16))
        draw.line([(seam_x, p3_y0 + 1), (seam_x, p3_y1 - 1)], fill=(255, 195, 0), width=2)
        draw.line(pts, fill=WAVE_LOOP_CLR, width=2)
        for idx, (xp, yp) in enumerate(pts):
            if seam_slice[idx]["msb"] == 0x0D:
                draw.ellipse([xp - 4, yp - 4, xp + 4, yp + 4], fill=MARKER_0D_CLR, outline=(255, 255, 255))
            else:
                draw.ellipse([xp - 1, yp - 1, xp + 1, yp + 1], fill=(255, 220, 180))

    # Y-axis scale hints
    for py0, py1 in [(p1_y0, p1_y1), (p2_y0, p2_y1), (p3_y0, p3_y1)]:
        draw.text((8, py0 + 2), "+511", fill=TEXT_MUTED, font=FONT_SMALL)
        draw.text((18, int(0.5 * (py0 + py1)) - 6), "  0", fill=TEXT_MUTED, font=FONT_SMALL)
        draw.text((8, py1 - 14), "-512", fill=TEXT_MUTED, font=FONT_SMALL)

    draw.text(
        (px0, H - 24),
        "Scale: 10-bit signed PCM (-512..+511) @ 25.0 kHz  |  Red markers = reconstructed 0x0D bytes",
        fill=TEXT_MUTED,
        font=FONT_SMALL,
    )
    img.save(path, format="PNG")


def render_corrupted_placeholder_png(path, block_title, wave_num, wave_name, note):
    W, H = 1400, 220
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 16, W - 20, H - 16], fill=(42, 24, 28), outline=MARKER_0D_CLR)
    draw.text((36, 32), f"{block_title}  |  Wave #{wave_num} {wave_name}", fill=TEXT_MAIN, font=FONT_TITLE)
    draw.text((36, 68), "UNABLE TO PARSE WAVE SEGMENTS (Descriptor misaligned / corrupted in Q0):", fill=MARKER_0D_CLR, font=FONT_SUB)
    for idx in range(0, len(note), 135):
        draw.text((36, 98 + (idx // 135) * 18), note[idx : idx + 135], fill=TEXT_MUTED, font=FONT_SMALL)
    img.save(path, format="PNG")


def export_block(spec):
    out_dir = os.path.join(OUT_BASE, spec["folder"])
    os.makedirs(out_dir, exist_ok=True)
    msb, w16, s10 = assemble_rom_block(spec["dir"], spec["chips"])
    entries = parse_wave_directory(msb, spec["wdir_offset"], spec["max_waves"])

    exported = []
    for wave_num, wave_name, ptr in entries:
        segments, parse_note, raw_desc_hex = parse_wave_descriptor(
            msb, ptr, has_blank_083=spec["has_blank_083"]
        )
        prefix = f"{wave_num:03d}_{safe_filename(wave_name)}"
        png_name = f"{prefix}.png"
        png_path = os.path.join(out_dir, png_name)

        if not segments:
            render_corrupted_placeholder_png(png_path, spec["title"], wave_num, wave_name, parse_note)
            exported.append({
                "num": wave_num,
                "name": wave_name,
                "ptr": ptr,
                "segments": [],
                "total_samples": 0,
                "count_0d": 0,
                "max_0d_run": 0,
                "runs_0d_ge3": [],
                "wrap_jump": 0,
                "parse_note": parse_note,
                "raw_desc_hex": raw_desc_hex,
                "folder": spec["folder"],
                "png": png_name,
                "full_wav": None,
                "loop_wav": None,
                "csv": None,
            })
            continue

        samples = []
        seg_boundaries = []
        loop_samples = []
        for seg_idx, (s_addr, e_addr) in enumerate(segments):
            seg_boundaries.append(len(samples))
            seg_list = []
            for addr in range(s_addr, e_addr + 1):
                item = {
                    "seg_idx": seg_idx,
                    "addr": addr,
                    "msb": msb[addr],
                    "w16": w16[addr],
                    "s10": s10[addr],
                }
                samples.append(item)
                seg_list.append(item)
            if seg_idx == len(segments) - 1:
                loop_samples = seg_list

        runs_0d = find_0d_runs(samples)
        runs_ge3 = [(pos, ln) for pos, ln in runs_0d if ln >= 3]
        count_0d = sum(ln for _, ln in runs_0d)
        max_0d_run = max((ln for _, ln in runs_0d), default=0)
        wrap_jump = abs(loop_samples[-1]["s10"] - loop_samples[0]["s10"]) if loop_samples else 0

        # Build looped sample buffer: seg0..seg_{n-2} + reps of seg_{n-1}
        prefix_w16 = [s["w16"] for s in samples[: seg_boundaries[-1]]] if len(segments) > 1 else []
        loop_w16 = [s["w16"] for s in loop_samples]
        reps = 10 if len(loop_w16) <= 8192 else 4
        looped_w16 = prefix_w16 + loop_w16 * reps
        full_w16 = [s["w16"] for s in samples]

        full_wav_name = f"{prefix}_full.wav"
        loop_wav_name = f"{prefix}_looped.wav"
        csv_name = f"{prefix}.csv"

        write_wav(os.path.join(out_dir, full_wav_name), full_w16)
        write_wav(os.path.join(out_dir, loop_wav_name), looped_w16)
        write_csv(os.path.join(out_dir, csv_name), samples)
        render_wave_png(
            png_path,
            spec["title"],
            wave_num,
            wave_name,
            segments,
            samples,
            seg_boundaries,
            runs_0d,
            loop_samples,
            wrap_jump,
            parse_note,
        )

        exported.append({
            "num": wave_num,
            "name": wave_name,
            "ptr": ptr,
            "segments": segments,
            "total_samples": len(samples),
            "count_0d": count_0d,
            "max_0d_run": max_0d_run,
            "runs_0d_ge3": runs_ge3,
            "wrap_jump": wrap_jump,
            "parse_note": parse_note,
            "raw_desc_hex": raw_desc_hex,
            "folder": spec["folder"],
            "png": png_name,
            "full_wav": full_wav_name,
            "loop_wav": loop_wav_name,
            "csv": csv_name,
        })

    return exported


def write_html_gallery(results):
    total_waves = sum(len(items) for _, items in results)
    flagged_waves = []
    for spec, items in results:
        if spec["is_reference"]:
            continue
        for w in items:
            if w["max_0d_run"] >= 4 or not w["segments"] or w["parse_note"]:
                flagged_waves.append((spec, w))

    parts = ["""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kurzweil K250 Exported Waveforms</title>
<style>
  body { background: #101216; color: #e8ecf2; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 0; padding: 24px 32px; }
  h1 { margin: 0 0 8px 0; font-size: 22px; }
  h2 { margin-top: 34px; border-bottom: 1px solid #2c313c; padding-bottom: 6px; font-size: 18px; color: #7cc6fe; }
  .summary { background: #191c22; border: 1px solid #2e3440; border-radius: 8px; padding: 14px 18px; margin-bottom: 24px; line-height: 1.5; font-size: 13px; }
  .card { background: #16181e; border: 1px solid #2a2f3a; border-radius: 8px; padding: 14px 16px; margin-bottom: 18px; }
  .card.warn { border-color: #9e3236; }
  .card-hdr { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 10px; }
  .wave-title { font-weight: 700; font-size: 15px; color: #fff; margin-right: 8px; }
  .badge { background: #232833; border: 1px solid #384050; border-radius: 4px; padding: 2px 7px; font-size: 11px; color: #c8d0dc; }
  .badge.alert { background: #5a1c20; border-color: #eb4d4b; color: #ffd6d6; font-weight: 700; }
  .badge.note { background: #4a3519; border-color: #f4a261; color: #ffe2bd; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; margin-bottom: 10px; font-size: 12px; color: #9aa4b4; }
  audio { height: 30px; vertical-align: middle; }
  a { color: #4ce0b3; text-decoration: none; }
  a:hover { text-decoration: underline; }
  img { width: 100%; max-width: 1400px; height: auto; border-radius: 4px; border: 1px solid #272c36; display: block; }
  .segs { font-size: 11px; color: #9aa4b4; margin-bottom: 8px; }
</style>
</head>
<body>
"""]
    parts.append(f"<h1>Kurzweil K250 Sound ROM Waveform Export ({total_waves} Waves)</h1>\n")
    parts.append("""<div class="summary">
  <strong>Inspection Summary:</strong> Exported raw 10-bit CGP DMA waveforms directly from the K250 Sound ROM EPROM layouts (25,000 Hz, signed 10-bit in 16-bit PCM).<br>
  <ul>
    <li><strong>Intact Factory Baseline (Row F):</strong> Contiguous <code>0x0D</code> runs never exceed <strong>2 samples</strong> across all intact factory EPROMs.</li>
    <li><strong>Reconstructed Blocks D &amp; C Anomalies:</strong> Where the C Viterbi solver inserted <code>0x0D</code> bytes in contiguous clumps (<code>max 0x0D run &ge; 4</code>, visible as flat DC staircases at <code>+13</code> MSB / <code>+52</code> in 10-bit plots), phase/sample boundaries shifted relative to descriptors, producing audible glitches or misaligned wave segments (plus Block C Q2 even bytes from blank <code>083K250</code> and 2 corrupted Q0 descriptors).</li>
  </ul>
""".replace("  <strong>Flagged</strong>", ""))
    parts.append(f"  <div>Flagged anomalies across reconstructed blocks: <strong>{len(flagged_waves)}</strong> waves.</div>\n</div>\n")

    for spec, items in results:
        parts.append(f"<h2>{html.escape(spec['title'])}</h2>\n")
        for w in items:
            is_warn = (not spec["is_reference"]) and (w["max_0d_run"] >= 4 or not w["segments"] or bool(w["parse_note"]))
            cls = "card warn" if is_warn else "card"
            parts.append(f'<div class="{cls}">\n  <div class="card-hdr">\n')
            parts.append(f'    <span class="wave-title">#{w["num"]} {html.escape(w["name"])}</span>\n')
            parts.append(f'    <span class="badge">Ptr 0x{w["ptr"]:06X}</span>\n')
            parts.append(f'    <span class="badge">Segs: {len(w["segments"])}</span>\n')
            parts.append(f'    <span class="badge">Samples: {w["total_samples"]}</span>\n')
            parts.append(f'    <span class="badge">0x0D count: {w["count_0d"]}</span>\n')
            run_cls = "badge alert" if (not spec["is_reference"] and w["max_0d_run"] >= 4) else "badge"
            parts.append(f'    <span class="{run_cls}">Max 0x0D run: {w["max_0d_run"]}</span>\n')
            parts.append(f'    <span class="badge">Loop wrap jump: {w["wrap_jump"]}</span>\n')
            if w["runs_0d_ge3"]:
                run_str = ", ".join(f"@{pos}x{ln}" for pos, ln in w["runs_0d_ge3"][:5])
                parts.append(f'    <span class="badge alert">Clumps: {html.escape(run_str)}</span>\n')
            if w["parse_note"]:
                parts.append(f'    <span class="badge note">{html.escape(w["parse_note"])}</span>\n')
            parts.append("  </div>\n")

            if w["segments"]:
                seg_txt = ", ".join(f"seg{i}=[0x{s:06X}..0x{e:06X}] ({e - s + 1})" for i, (s, e) in enumerate(w["segments"]))
                parts.append(f'  <div class="segs">{html.escape(seg_txt)}</div>\n')
                full_rel = f"{w['folder']}/{w['full_wav']}"
                loop_rel = f"{w['folder']}/{w['loop_wav']}"
                csv_rel = f"{w['folder']}/{w['csv']}"
                parts.append('  <div class="controls">\n')
                parts.append(f'    <div>Full: <audio controls preload="none" src="{html.escape(full_rel)}"></audio></div>\n')
                parts.append(f'    <div>Looped: <audio controls preload="none" src="{html.escape(loop_rel)}"></audio></div>\n')
                parts.append(f'    <div><a href="{html.escape(csv_rel)}">Download CSV</a></div>\n')
                parts.append("  </div>\n")

            png_rel = f"{w['folder']}/{w['png']}"
            parts.append(f'  <a href="{html.escape(png_rel)}" target="_blank"><img src="{html.escape(png_rel)}" alt="Wave #{w["num"]} {html.escape(w["name"])}"></a>\n')
            parts.append("</div>\n")

    parts.append("</body>\n</html>\n")
    with open(os.path.join(OUT_BASE, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(parts))


def main() -> int:
    global ROMS_BASE, OUT_BASE
    ap = argparse.ArgumentParser(
        description="Export K250 Sound ROM waveforms as 25 kHz WAVs, CSVs, PNG plots, and an HTML gallery."
    )
    ap.add_argument(
        "--roms-dir",
        default=os.environ.get("K250_SOUNDS_BINARIES_DIR", "."),
        help="Base directory containing \"K250 Basic Sounds\" and reconstructed Sound Block directories",
    )
    ap.add_argument(
        "--out-dir",
        default="./ExportedWaveforms",
        help="Output directory for exported WAV/CSV/PNG/HTML files (default: ./ExportedWaveforms)",
    )
    ap.add_argument(
        "--blocks",
        nargs="+",
        choices=("block_d", "block_c", "row_f"),
        default=["block_d", "block_c", "row_f"],
        help="Which ROM blocks to export (default: all three)",
    )
    args = ap.parse_args()

    ROMS_BASE = args.roms_dir
    OUT_BASE = args.out_dir
    dir_map = {
        "block_d": os.path.join(ROMS_BASE, "K250 Sound Block D (Reconstructed)"),
        "block_c": os.path.join(ROMS_BASE, "K250 Sound Block C (Reconstructed)"),
        "row_f": os.path.join(ROMS_BASE, "K250 Basic Sounds"),
    }
    selected = []
    for spec in BLOCKS:
        if spec["id"] in args.blocks:
            s = dict(spec)
            s["dir"] = dir_map[spec["id"]]
            selected.append(s)

    os.makedirs(OUT_BASE, exist_ok=True)
    results = []
    for spec in selected:
        print(f"Exporting {spec['title']}...")
        items = export_block(spec)
        results.append((spec, items))
        for w in items:
            print(
                f"  #{w['num']:3d} {w['name']:10s} segs={len(w['segments']):2d} "
                f"smp={w['total_samples']:5d} 0x0D={w['count_0d']:3d} max_run={w['max_0d_run']:3d} "
                f"wrap_jump={w['wrap_jump']:3d}"
                + (f"  NOTE={w['parse_note']}" if w["parse_note"] else "")
            )
    write_html_gallery(results)
    print(f"\nDone! Wrote gallery to {os.path.join(OUT_BASE, 'index.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
