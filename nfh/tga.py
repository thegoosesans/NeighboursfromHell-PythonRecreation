"""Decoder for the game's .tga files.

They are *not* standard TGAs, which is why generic loaders (Pillow included) give wrong colours:
  * 16 bit, uncompressed, and the descriptor's alpha-bit count says which layout is used:
      alpha bits = 4  ->  ARGB4444   (sprites with transparency, 4258 files)
      alpha bits = 0  ->  RGB565     (opaque GUI / house pieces, 78 files)
  * 24 bit BGR for `loading.tga`.
"""
from __future__ import annotations
import struct
import numpy as np


def _expand(v, bits):
    v = v.astype(np.uint16)
    return ((v << (8 - bits)) | (v >> (2 * bits - 8))).astype(np.uint8)


def _unrle(data: bytes, off: int, px: int, count: int) -> bytes:
    """Expand run-length encoded pixel data (TGA image type 10) to `count` raw pixels."""
    out = bytearray()
    n = 0
    while n < count and off < len(data):
        head = data[off]
        off += 1
        run = (head & 0x7F) + 1
        if head & 0x80:
            out += data[off:off + px] * run
            off += px
        else:
            out += data[off:off + px * run]
            off += px * run
        n += run
    return bytes(out[:count * px])


def decode_tga(data: bytes) -> np.ndarray:
    """Return an (h, w, 4) uint8 RGBA array."""
    idlen, cmap, itype = data[0], data[1], data[2]
    if itype == 10 and not cmap:                            # RLE true colour (most files in gfxdata.bnd)
        w, h = struct.unpack("<HH", data[12:16])
        px = data[16] // 8
        data = data[:2] + b"" + data[3:18 + idlen] + _unrle(data, 18 + idlen, px, w * h)
        itype = 2
    if cmap or itype != 2:
        raise ValueError(f"unsupported TGA (colour map={cmap}, type={itype})")
    w, h = struct.unpack("<HH", data[12:16])
    bpp, desc = data[16], data[17]
    off = 18 + idlen
    if bpp == 16:
        raw = np.frombuffer(data, dtype="<u2", count=w * h, offset=off).reshape(h, w)
        if (desc & 0x0F) == 4:                              # ARGB4444
            img = np.stack([(raw >> 8) & 15, (raw >> 4) & 15, raw & 15, (raw >> 12) & 15], -1)
            img = (img * 17).astype(np.uint8)
        else:                                               # RGB565
            rgb = np.stack([_expand((raw >> 11) & 31, 5), _expand((raw >> 5) & 63, 6),
                            _expand(raw & 31, 5)], -1)
            img = np.dstack([rgb, np.full((h, w), 255, np.uint8)])
    elif bpp in (24, 32):
        px = bpp // 8
        raw = np.frombuffer(data, dtype=np.uint8, count=w * h * px, offset=off).reshape(h, w, px)
        rgb = raw[..., 2::-1]
        a = raw[..., 3:4] if bpp == 32 else np.full((h, w, 1), 255, np.uint8)
        img = np.concatenate([rgb, a], -1)
    else:
        raise ValueError(f"unsupported bit depth {bpp}")
    return img if desc & 0x20 else img[::-1].copy()        # bit 5 = top-left origin


def load_tga(path: str):
    """Load a game .tga (a file or 'archive.bnd@@name') as a Pillow RGBA image."""
    from PIL import Image
    from .source import read_path
    return Image.fromarray(decode_tga(read_path(path)), "RGBA")
