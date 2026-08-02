# -*- coding: utf-8 -*-
"""
Generate ebsplit.ico - a "sheet being split" application icon.

Self-contained: writes PNG and ICO byte streams directly, so the build needs no
imaging library (Pillow is not available on an offline hospital PC).

    python make_icon.py            -> ebsplit.ico next to this file
    python make_icon.py out.ico    -> explicit path
"""

import os
import struct
import sys
import zlib

SS = 4  # supersampling factor for anti-aliasing
SIZES = (16, 24, 32, 48, 64, 128, 256)

# --- geometry, normalised to the 0..1 icon square -------------------------- #
SHEET_A = (0.075, 0.055, 0.455, 0.795)      # left half, sitting high
SHEET_B = (0.545, 0.205, 0.925, 0.945)      # right half, slid down
BORDER = 0.030                               # sheet outline thickness
SPLIT_X = 0.500                              # dashed cut line
SPLIT_W = 0.042
DASH_PERIOD = 0.115
DASH_ON = 0.068

WHITE = (0xFF, 0xFF, 0xFF)
INK = (0x25, 0x2A, 0x31)                     # sheet outline / rules
RULE = (0xB4, 0xBC, 0xC6)                    # faint content lines on the sheets
RED = (0xD8, 0x17, 0x17)                     # cut line


def _in(x, y, r):
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


def _on_border(x, y, r, t):
    if not _in(x, y, r):
        return False
    return (x - r[0] < t or r[2] - x < t or y - r[1] < t or r[3] - y < t)


def _sample(x, y):
    """Colour + alpha at normalised point (x, y). Returns (r, g, b, a)."""
    # cut line wins over everything so the split reads clearly
    if abs(x - SPLIT_X) <= SPLIT_W / 2.0 and 0.02 <= y <= 0.98:
        if (y % DASH_PERIOD) < DASH_ON:
            return RED + (255,)

    for r in (SHEET_A, SHEET_B):
        if _in(x, y, r):
            if _on_border(x, y, r, BORDER):
                return INK + (255,)
            # faint horizontal rules to read as a drawing/document
            inner_h = r[3] - r[1]
            rel = (y - r[1]) / inner_h
            if 0.12 < rel < 0.92:
                step = 0.115
                if ((rel - 0.12) % step) < 0.030:
                    return RULE + (255,)
            return WHITE + (255,)
    return (0, 0, 0, 0)


def render_rgba(size):
    """Anti-aliased RGBA bytes for one square size."""
    n = size * SS
    inv = 1.0 / n
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            ar = ag = ab = aa = 0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * inv
                for sx in range(SS):
                    x = (px * SS + sx + 0.5) * inv
                    r, g, b, a = _sample(x, y)
                    ar += r * a; ag += g * a; ab += b * a; aa += a
            k = SS * SS
            if aa == 0:
                row += b"\x00\x00\x00\x00"
            else:
                row += bytes((int(ar / aa), int(ag / aa), int(ab / aa),
                              int(aa / k)))
        rows.append(bytes(row))
    return rows


def png_bytes(size, rows):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I",
                                                                zlib.crc32(body))
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_bytes(images):
    """images: list of (size, png_bytes). Returns a complete .ico file."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + 16 * count
    blobs = b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def build(path=None):
    out = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "ebsplit.ico")
    images = []
    for s in SIZES:
        images.append((s, png_bytes(s, render_rgba(s))))
    data = ico_bytes(images)
    with open(out, "wb") as fh:
        fh.write(data)
    return out, len(data), [s for s, _ in images]


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        p, n, sizes = build(target)
    except OSError as exc:
        sys.exit("Could not write the icon: %s" % exc)
    print("Wrote %s (%d bytes, sizes: %s)"
          % (p, n, ", ".join(str(s) for s in sizes)))
