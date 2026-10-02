"""GameCube GX texture formats: size computation and decoding to RGBA8."""
from __future__ import annotations

import numpy as np

I4, I8, IA4, IA8, RGB565, RGB5A3, RGBA8, C4, C8, C14X2, CMPR = 0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 14

# format -> (block width, block height, bytes per block)
BLOCK = {
    I4: (8, 8, 32), I8: (8, 4, 32), IA4: (8, 4, 32), IA8: (4, 4, 32),
    RGB565: (4, 4, 32), RGB5A3: (4, 4, 32), RGBA8: (4, 4, 64),
    C4: (8, 8, 32), C8: (8, 4, 32), C14X2: (4, 4, 32), CMPR: (8, 8, 32),
}

NAMES = {I4: "I4", I8: "I8", IA4: "IA4", IA8: "IA8", RGB565: "RGB565", RGB5A3: "RGB5A3",
         RGBA8: "RGBA8", C4: "C4", C8: "C8", C14X2: "C14X2", CMPR: "CMPR"}


def level_size(fmt: int, width: int, height: int) -> int:
    bw, bh, bb = BLOCK[fmt]
    return ((width + bw - 1) // bw) * ((height + bh - 1) // bh) * bb


def texture_size(fmt: int, width: int, height: int, mips: int) -> int:
    total = 0
    for level in range(max(mips, 1)):
        total += level_size(fmt, max(width >> level, 1), max(height >> level, 1))
    return total


def _unblock(blocks: np.ndarray, width: int, height: int, bw: int, bh: int) -> np.ndarray:
    """blocks: (nby, nbx, bh, bw, C) -> image (height, width, C)."""
    nby, nbx = blocks.shape[:2]
    img = blocks.transpose(0, 2, 1, 3, 4).reshape(nby * bh, nbx * bw, -1)
    return img[:height, :width]


def _blocks_u8(data: bytes, width: int, height: int, fmt: int) -> tuple:
    bw, bh, bb = BLOCK[fmt]
    nbx, nby = (width + bw - 1) // bw, (height + bh - 1) // bh
    need = nbx * nby * bb
    buf = np.frombuffer(data[:need].ljust(need, b"\0"), dtype=np.uint8)
    return buf.reshape(nby, nbx, bb), nbx, nby


def _rgb565(v: np.ndarray) -> np.ndarray:
    r = ((v >> 11) & 0x1F).astype(np.uint16)
    g = ((v >> 5) & 0x3F).astype(np.uint16)
    b = (v & 0x1F).astype(np.uint16)
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.uint8)


def _rgb5a3(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.uint16)
    opaque = (v & 0x8000) != 0
    r5, g5, b5 = (v >> 10) & 0x1F, (v >> 5) & 0x1F, v & 0x1F
    r4, g4, b4, a3 = (v >> 8) & 0xF, (v >> 4) & 0xF, v & 0xF, (v >> 12) & 0x7
    r = np.where(opaque, (r5 << 3) | (r5 >> 2), r4 * 17)
    g = np.where(opaque, (g5 << 3) | (g5 >> 2), g4 * 17)
    b = np.where(opaque, (b5 << 3) | (b5 >> 2), b4 * 17)
    a = np.where(opaque, 255, (a3 << 5) | (a3 << 2) | (a3 >> 1))
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def _decode_cmpr(data: bytes, width: int, height: int) -> np.ndarray:
    blocks, nbx, nby = _blocks_u8(data, width, height, CMPR)
    # each 8x8 block = 2x2 DXT1 sub-blocks of 8 bytes (big-endian colors)
    sub = blocks.reshape(-1, 8)
    c0 = (sub[:, 0].astype(np.uint16) << 8) | sub[:, 1]
    c1 = (sub[:, 2].astype(np.uint16) << 8) | sub[:, 3]
    p0 = _rgb565(c0).astype(np.int32)
    p1 = _rgb565(c1).astype(np.int32)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    rgb = np.stack([p0, p1, p2, p3], 1)  # (N, 4, 3)
    alpha = np.full((len(sub), 4, 1), 255, np.int32)
    alpha[:, 3, 0] = np.where(four[:, 0], 255, 0)
    palette = np.concatenate([rgb, alpha], -1).astype(np.uint8)  # (N, 4, 4)
    shifts = np.array([6, 4, 2, 0], dtype=np.uint8)
    idx = ((sub[:, 4:8, None] >> shifts) & 3).reshape(-1, 16)  # row-major 4x4
    pix = palette[np.arange(len(sub))[:, None], idx]  # (N, 16, 4)
    # (nby, nbx, sy, sx, 4, 4, 4) -> (nby, nbx, 8, 8, 4)
    pix = pix.reshape(nby, nbx, 2, 2, 4, 4, 4).transpose(0, 1, 2, 4, 3, 5, 6)
    return _unblock(pix.reshape(nby, nbx, 8, 8, 4), width, height, 8, 8)


def decode(data: bytes, fmt: int, width: int, height: int, palette: bytes | None = None,
           palette_fmt: int = RGB5A3) -> np.ndarray:
    """Decode one mip level into an (height, width, 4) uint8 RGBA array."""
    if fmt == CMPR:
        return _decode_cmpr(data, width, height)
    bw, bh, _ = BLOCK[fmt]
    blocks, nbx, nby = _blocks_u8(data, width, height, fmt)
    if fmt in (I4, C4):
        hi, lo = blocks >> 4, blocks & 0xF
        vals = np.stack([hi, lo], -1).reshape(nby, nbx, bh, bw)
        if fmt == I4:
            i = (vals * 17).astype(np.uint8)
            px = np.stack([i, i, i, i], -1)
        else:
            px = _palette_lookup(vals, palette, palette_fmt)
    elif fmt in (I8, C8):
        vals = blocks.reshape(nby, nbx, bh, bw)
        px = np.stack([vals] * 4, -1) if fmt == I8 else _palette_lookup(vals, palette, palette_fmt)
    elif fmt == IA4:
        v = blocks.reshape(nby, nbx, bh, bw)
        i, a = (v & 0xF) * 17, (v >> 4) * 17
        px = np.stack([i, i, i, a], -1).astype(np.uint8)
    elif fmt == IA8:
        v = blocks.reshape(nby, nbx, bh, bw, 2)
        a, i = v[..., 0], v[..., 1]
        px = np.stack([i, i, i, a], -1)
    elif fmt in (RGB565, RGB5A3, C14X2):
        v = blocks.reshape(nby, nbx, bh * bw, 2)
        v = ((v[..., 0].astype(np.uint16) << 8) | v[..., 1]).reshape(nby, nbx, bh, bw)
        if fmt == RGB565:
            rgb = _rgb565(v)
            px = np.concatenate([rgb, np.full(rgb.shape[:-1] + (1,), 255, np.uint8)], -1)
        elif fmt == RGB5A3:
            px = _rgb5a3(v)
        else:
            px = _palette_lookup(v & 0x3FFF, palette, palette_fmt)
    elif fmt == RGBA8:
        v = blocks.reshape(nby, nbx, 2, 16, 2)  # AR plane then GB plane
        a, r = v[:, :, 0, :, 0], v[:, :, 0, :, 1]
        g, b = v[:, :, 1, :, 0], v[:, :, 1, :, 1]
        px = np.stack([r, g, b, a], -1).reshape(nby, nbx, 4, 4, 4)
    else:
        raise ValueError(f"unsupported GX texture format {fmt}")
    return _unblock(px.astype(np.uint8), width, height, bw, bh)


def _palette_lookup(idx: np.ndarray, palette: bytes | None, palette_fmt: int) -> np.ndarray:
    if palette is None:
        g = (idx & 0xFF).astype(np.uint8)
        return np.stack([g, g, g, np.full_like(g, 255)], -1)
    pal = np.frombuffer(palette, dtype=">u2")
    if palette_fmt == RGB565:
        rgb = _rgb565(pal)
        colors = np.concatenate([rgb, np.full((len(pal), 1), 255, np.uint8)], -1)
    elif palette_fmt == RGB5A3:
        colors = _rgb5a3(pal)
    else:  # IA8
        a, i = (pal >> 8).astype(np.uint8), (pal & 0xFF).astype(np.uint8)
        colors = np.stack([i, i, i, a], -1)
    return colors[np.clip(idx, 0, len(colors) - 1)]
