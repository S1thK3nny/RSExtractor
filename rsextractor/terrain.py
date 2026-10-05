"""Terrain: heightmaps and their blended ground textures.

Both games draw the ground of open levels as a heightfield in nested levels of
detail, each level at twice the spacing of the one inside it, and texture it by
blending tiling layer textures (sand, rock, snow ...) with small blend masks.

Rogue Leader keeps the terrain in the level's ``dat`` (record list at ``+0x58``,
read by ``FUN_7fd8a5d8``): one byte per vertex for the detailed centre and four
rings of strips around it (``FUN_7fcad488`` reads a height). Its textures are the
``mixmaps`` parts (``FUN_7fd8ad84``, ``FUN_7fcc068c``).

Rebel Strike streams its terrain from ``h16_*.img`` files that ``h16/streamInfo``
describes (``FUN_7f3534f0`` relocates it, ``FUN_7f351e58`` unpacks the bits): up to
eight levels, each stored in blocks of 8 x 8 vertices. Its blend masks and layer
materials sit in ``streamInfo`` too (``FUN_7f3550c8``).

Heights are given in "base units": the vertex grid of the finest level (level 0).
``Terrain.to_world`` maps ``(bx, height, bz, 1)`` to metres. See docs/FORMATS.md
"Terrain" for the record layouts.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import gxtexture as gx
from .material import TextureRef

MASK = 32  # blend masks are 32 x 32 texels spanning their cell edge to edge


@dataclass
class HeightLevel:
    step: int            # vertex spacing in base units (a power of two)
    x0: int              # base coordinates of heights[0, 0]
    z0: int
    heights: np.ndarray  # float32 (rows = z, columns = x), NaN = no terrain


@dataclass
class Layer:
    texture: TextureRef
    uv: np.ndarray       # 2 x 3: texture (u, v) = uv @ (bx, bz, 1), v pointing down the image


@dataclass
class Cells:
    """Blend cells: which layers a cell mixes and the 32 x 32 mask doing it."""
    layers: np.ndarray   # (cells, 3) layer indices, -1 = unused
    masks: np.ndarray    # (cells,) index into ``mask_images``, -1 = none (one layer)
    mask_images: np.ndarray  # (n, 32, 32, 2) float32: intensity, alpha


@dataclass
class Terrain:
    levels: List[HeightLevel]  # finest first
    to_world: np.ndarray       # 3 x 4: world = to_world @ (bx, h, bz, 1)
    layers: List[Layer] = field(default_factory=list)
    # The blend cells cover the texture rectangle (base units) in a grid; ``cell_at``
    # turns base coordinates into (cell index, position inside the cell 0..1).
    rect: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # bx0, bz0, bx1, bz1
    density: float = 1.0       # native blend texels per base unit
    cells: Optional[Cells] = None
    cell_at: Optional[object] = None

    def weights(self, bx: np.ndarray, bz: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Layer indices (N, 3) and their weights (N, 3) at base coordinates.

        One layer: weight 1. Two: mask intensity I for the first, 1 - I for the
        second. Three: I, A and 1 - I - A (both games' TEV setups, RS2
        ``FUN_7fd2fd60``/``FUN_7fd2ff30``)."""
        cell, fx, fz = self.cell_at(bx, bz)
        ok = cell >= 0
        ids = np.full((len(bx), 3), -1, np.int32)
        w = np.zeros((len(bx), 3), np.float32)
        ids[ok] = self.cells.layers[cell[ok]]
        count = (ids >= 0).sum(1)
        mask = np.where(ok, self.cells.masks[np.maximum(cell, 0)], -1)
        intensity = np.ones(len(bx), np.float32)
        alpha = np.zeros(len(bx), np.float32)
        m = mask >= 0
        if m.any():
            img = self.cells.mask_images
            mx, mz = fx[m] * (MASK - 1), fz[m] * (MASK - 1)
            x0 = np.minimum(mx.astype(np.int32), MASK - 2)
            z0 = np.minimum(mz.astype(np.int32), MASK - 2)
            tx, tz = (mx - x0)[:, None], (mz - z0)[:, None]
            k = mask[m]
            s = (img[k, z0, x0] * (1 - tx) * (1 - tz) + img[k, z0, x0 + 1] * tx * (1 - tz)
                 + img[k, z0 + 1, x0] * (1 - tx) * tz + img[k, z0 + 1, x0 + 1] * tx * tz)
            intensity[m], alpha[m] = s[:, 0], s[:, 1]
        one, two, three = count == 1, count == 2, count == 3
        w[one, 0] = 1.0
        w[two, 0], w[two, 1] = intensity[two], 1.0 - intensity[two]
        w[three, 0], w[three, 1] = intensity[three], alpha[three]
        w[three, 2] = np.clip(1.0 - intensity[three] - alpha[three], 0.0, 1.0)
        return ids, w


# -- Rogue Leader --------------------------------------------------------------

def _rs2_mask_images(mix_info: bytes, data: bytes) -> np.ndarray:
    """``MixInfo_0``: u32 count, then per mask u32 offset into ``Data_0`` (bit 31: IA4
    with two weights, else I4), 32 x 32 with mipmaps."""
    count = struct.unpack_from(">I", mix_info, 0)[0]
    out = np.zeros((count, MASK, MASK, 2), np.float32)
    for i, e in enumerate(struct.unpack_from(f">{count}I", mix_info, 4)):
        img = gx.decode(data[e & 0x7FFFFFFF:], gx.IA4 if e >> 31 else gx.I4, MASK, MASK).astype(np.float32) / 255
        out[i, ..., 0], out[i, ..., 1] = img[..., 0], img[..., 3] if e >> 31 else 0.0
    return out


def parse_rs2(dat: bytes, files: Dict[str, bytes]) -> Optional[Terrain]:
    """The terrain of a Rogue Leader level resource, or None."""
    if len(dat) < 0x60:
        return None
    count, array = struct.unpack_from(">II", dat, 0x58)
    if not count or array + 0x4D4 > len(dat):
        return None
    rec = array  # pointers in the record are relative to the record array

    def u16(o):
        return struct.unpack_from(">H", dat, rec + o)[0]

    def u32(o):
        return struct.unpack_from(">I", dat, rec + o)[0]

    def f32(*offsets):
        return np.array([struct.unpack_from(">f", dat, rec + o)[0] for o in offsets])

    levels: List[HeightLevel] = []
    w, h = u16(0x30), u16(0x32)
    centre = np.frombuffer(dat, np.int8, w * h, array + u32(0x90)).reshape(h, w).astype(np.float32)
    levels.append(HeightLevel(1, 8 * u16(0x38), 8 * u16(0x3A), centre))
    # Rings of four strips (left, right, top, bottom) at 2, 4, 8, 16 times the spacing:
    # {u8 x[4], u8 z[4] (in blocks of 8 vertices), u16 stride[4], u16 rows[4], u32 data[4]}
    for ring in range(4):
        r = 0x94 + 0x28 * ring
        step = 2 << ring
        strips = []
        for side in range(4):
            stride, rows = u16(r + 8 + 2 * side), u16(r + 0x10 + 2 * side)
            if not stride or not rows:
                continue
            data = np.frombuffer(dat, np.int8, stride * rows, array + u32(r + 0x18 + 4 * side))
            strips.append((8 * dat[rec + r + side], 8 * dat[rec + r + 4 + side],
                           data.reshape(rows, stride).astype(np.float32)))
        if not strips:
            continue
        x0 = min(s[0] for s in strips)
        z0 = min(s[1] for s in strips)
        cols = max((s[0] - x0) // step + s[2].shape[1] for s in strips)
        rows = max((s[1] - z0) // step + s[2].shape[0] for s in strips)
        grid = np.full((rows, cols), np.nan, np.float32)
        for sx, sz, strip in strips:
            c, rr = (sx - x0) // step, (sz - z0) // step
            grid[rr:rr + strip.shape[0], c:c + strip.shape[1]] = strip
        levels.append(HeightLevel(step, x0, z0, grid))

    origin = f32(0x00, 0x04, 0x08)
    axis_u, axis_v = f32(0x0C, 0x10, 0x14), f32(0x18, 0x1C, 0x20)
    up = np.cross(axis_v, axis_u)
    cell = f32(0x24, 0x28, 0x2C)
    to_world = np.column_stack([axis_u * cell[0], up * cell[1], axis_v * cell[2], origin])
    terrain = Terrain(levels, to_world)

    meta, mix, data = (files.get(f"mixmaps/{n}_0") for n in ("MetaInfo", "MixInfo", "Data"))
    nbx, nbz = u16(0x34), u16(0x36)
    if meta is None or mix is None or data is None or len(meta) < 4 * nbx * nbz:
        return terrain
    # Layers: {sha1[20], u16 width, u16 height, u8 mips, ..., f32 scale u, scale v,
    # rotation (degrees), offset u, offset v at +0x1C}. Texture coordinates come from
    # the position in 1/16 vertex units minus (+0x478, +0x47A), scaled by 8 * scale / 128.
    shift = np.array([u16(0x478), u16(0x47A)], np.float64)
    table = array + u32(0x480)
    for i in range(u16(0x47C)):
        e = table + 0x30 * i
        tw, th, mips = struct.unpack_from(">HHB", dat, e + 0x14)
        su, sv, rot, ou, ov = struct.unpack_from(">5f", dat, e + 0x1C)
        a = math.radians(rot)
        m = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) @ np.diag([8 * su, 8 * sv])
        m = m * np.array([[1.0], [-1.0]])  # the game feeds z as its second axis and turns v around
        uv = np.zeros((2, 3))
        uv[:, :2] = m * 16 / 128
        uv[:, 2] = -(m @ shift) / 128 + np.array([ou, ov])
        terrain.layers.append(Layer(TextureRef(tw, th, max(mips, 1), gx.CMPR, dat[e:e + 20]), uv))
    # MetaInfo_0: u32 per block of 8 x 8 vertices, high half = mask, low half = layer
    # count (bits 0-3) and up to three layer indices (bits 4-7, 8-11, 12-15).
    codes = np.frombuffer(meta, ">u4", nbx * nbz).astype(np.int64)
    count = codes & 0xF
    layers = np.stack([(codes >> s) & 0xF for s in (4, 8, 12)], 1).astype(np.int32)
    layers[np.arange(3)[None, :] >= count[:, None]] = -1
    masks = np.where(count >= 2, codes >> 16, -1).astype(np.int32)
    terrain.cells = Cells(layers, masks, _rs2_mask_images(mix, data))
    terrain.rect = (0.0, 0.0, 8.0 * nbx, 8.0 * nbz)
    terrain.density = MASK / 8.0

    def cell_at(bx, bz):
        cx, cz = np.floor(bx / 8).astype(np.int64), np.floor(bz / 8).astype(np.int64)
        ok = (cx >= 0) & (cx < nbx) & (cz >= 0) & (cz < nbz)
        return np.where(ok, cx + cz * nbx, -1), bx / 8 - cx, bz / 8 - cz
    terrain.cell_at = cell_at
    return terrain


# -- Rebel Strike --------------------------------------------------------------

def _rs3_level(si: bytes, img: bytes, level: int) -> Optional[HeightLevel]:
    """One level of an ``h16`` image: rows of blocks (``+0x58``, 16 bytes each: s16 first
    and last block row, u16 spans, u16 first span, u32 bits per block row, u32 bit
    offset), each row a list of block column spans (``+0x54``: s16 first, last). A block
    is 8 x 8 vertices of ``+0x2C`` bits each, the height in the top ``+0x29`` bits."""
    n, height_bits, vertex_bits = si[0x28], si[0x29], si[0x2C]
    first, last = struct.unpack_from(">HH", si, 0x30 + 2 * level)
    if first == last:
        return None
    rows_table, spans = struct.unpack_from(">II", si, 0x58)[0], struct.unpack_from(">I", si, 0x54)[0]
    runs = []
    for e in range(first, last):
        r0, r1, count, span, row_bits, offset = struct.unpack_from(">hhHHII", si, rows_table + 16 * e)
        for k in range(r0, r1 + 1):
            pos = offset + (k - r0) * row_bits
            for s in range(count):
                c0, c1 = struct.unpack_from(">hh", si, spans + 4 * (span + s))
                runs.append((k, c0, c1, pos))
                pos += (c1 - c0 + 1) * n * n * vertex_bits
    if not runs:
        return None
    k0, k1 = min(r[0] for r in runs), max(r[0] for r in runs)
    c0, c1 = min(r[1] for r in runs), max(r[2] for r in runs)
    grid = np.full(((k1 - k0 + 1) * n, (c1 - c0 + 1) * n), np.nan, np.float32)
    bits = np.unpackbits(np.frombuffer(img, np.uint8))
    weights = (1 << np.arange(height_bits - 1, -1, -1)).astype(np.int64)
    for k, a, b, pos in runs:
        blocks = b - a + 1
        size = blocks * n * n * vertex_bits
        if pos + size > len(bits):
            continue
        vals = bits[pos:pos + size].reshape(blocks * n * n, vertex_bits)[:, :height_bits] @ weights
        vals = np.where(vals >= 1 << (height_bits - 1), vals - (1 << height_bits), vals) << (16 - height_bits)
        vals = vals.reshape(blocks, n, n).transpose(1, 0, 2).reshape(n, blocks * n)
        grid[(k - k0) * n:(k - k0 + 1) * n, (a - c0) * n:(b - c0 + 1) * n] = vals
    step = 1 << level
    return HeightLevel(step, c0 * n * step, k0 * n * step, grid)


def parse_rs3(si: bytes, img: bytes) -> Optional[Terrain]:
    """The terrain an ``h16/streamInfo`` part describes, with its ``h16`` image."""
    if len(si) < 0x2EC:
        return None
    levels = [lv for lv in (_rs3_level(si, img, level) for level in range(8)) if lv is not None]
    if not levels:
        return None
    # +0x2BC: 3 x 4 matrix from terrain grid units (8 level 0 vertices, height / 256)
    # to the world (its inverse at +0x28C maps the world for the collision code).
    m = np.array(struct.unpack_from(">12f", si, 0x2BC), np.float64).reshape(3, 4)
    to_world = m @ np.diag([1 / 8, 1 / 256, 1 / 8, 1.0])
    terrain = Terrain(levels, to_world)

    def u32(o):
        return struct.unpack_from(">I", si, o)[0]

    if not si[0x204]:
        return terrain
    grid_rec = u32(0x208)
    cw, ch, cells_at = struct.unpack_from(">HHI", si, grid_rec + 8)
    pages_at = u32(grid_rec + 0x14)
    combos_at, textures_at, materials_at = u32(0x210), u32(0x260), u32(0x264)
    entries = np.frombuffer(si, ">u4", cw * ch, cells_at).astype(np.int64)
    # Cells of 16 grid units; a cell whose top 10 bits are not 0x3FF is split into a
    # page of 16 x 16 cells of one grid unit each.
    paged = (entries >> 22) != 0x3FF
    page_count = int((entries[paged] >> 22).max()) + 1 if paged.any() else 0
    pages = np.frombuffer(si, ">u4", 256 * page_count, pages_at).astype(np.int64) if page_count else np.zeros(0, np.int64)
    every = np.concatenate([entries, pages])
    # Entry: bits 2-10 combination (u32 list at +0x210: bits 0-1 layer count, then
    # 7-bit material indices at bits 2, 9, 16), bits 11-21 blend mask.
    combo_index = (every >> 2) & 0x1FF
    combos = np.frombuffer(si, ">u4", int(combo_index.max()) + 1, combos_at).astype(np.int64)
    combo = combos[combo_index]
    count = combo & 3
    layers = np.stack([(combo >> s) & 0x7F for s in (2, 9, 16)], 1).astype(np.int32)
    layers[np.arange(3)[None, :] >= count[:, None]] = -1
    mask_count, mask_table = u32(0x268), u32(0x270)
    addresses = struct.unpack_from(f">{mask_count}I", si, mask_table) if mask_count else ()
    mask_index = ((every >> 11) & 0x7FF).astype(np.int32)
    masks = np.where((count >= 2) & (mask_index < mask_count), mask_index, -1).astype(np.int32)
    mask_images = np.zeros((max(mask_count, 1), MASK, MASK, 2), np.float32)
    three = set(mask_index[count == 3].tolist())
    for i, a in enumerate(addresses):
        fmt = gx.IA4 if i in three else gx.I4
        if a + gx.level_size(fmt, MASK, MASK) > len(si):
            continue
        im = gx.decode(si[a:], fmt, MASK, MASK).astype(np.float32) / 255
        mask_images[i, ..., 0], mask_images[i, ..., 1] = im[..., 0], im[..., 3] if fmt == gx.IA4 else 0.0
    terrain.cells = Cells(layers, masks, mask_images)
    # Materials (0x38 each): u16 flags, u16 texture (0x28 byte records at +0x260:
    # u16 width, height, u8 mips, format, sha1 at +8), ..., 2 x 4 texture matrix at
    # +0x18 applied to grid coordinates.
    for i in range(struct.unpack_from(">H", si, 0x256)[0]):
        e = materials_at + 0x38 * i
        t = textures_at + 0x28 * struct.unpack_from(">H", si, e + 2)[0]
        tw, th, mips, fmt = struct.unpack_from(">HHBB", si, t)
        mat = np.array(struct.unpack_from(">8f", si, e + 0x18), np.float64).reshape(2, 4)
        uv = np.column_stack([mat[:, 0] / 8, mat[:, 2] / 8, mat[:, 3]])
        terrain.layers.append(Layer(TextureRef(tw, th, max(mips, 1), fmt, si[t + 8:t + 28]), uv))
    unit = 16 * 8  # base units per cell
    terrain.rect = (0.0, 0.0, float(cw * unit), float(ch * unit))
    terrain.density = MASK / (8.0 if page_count else float(unit))
    base_cells = cw * ch

    def cell_at(bx, bz):
        gxf, gzf = bx / 8, bz / 8
        cx, cz = np.floor(gxf / 16).astype(np.int64), np.floor(gzf / 16).astype(np.int64)
        ok = (cx >= 0) & (cx < cw) & (cz >= 0) & (cz < ch)
        index = np.where(ok, cx + cz * cw, 0)
        fx, fz = gxf / 16 - cx, gzf / 16 - cz
        e = entries[index]
        sub = ok & ((e >> 22) != 0x3FF)
        if sub.any():
            sx, sz = np.floor(gxf[sub]).astype(np.int64) & 15, np.floor(gzf[sub]).astype(np.int64) & 15
            index[sub] = base_cells + (e[sub] >> 22) * 256 + sz * 16 + sx
            fx[sub], fz[sub] = gxf[sub] - np.floor(gxf[sub]), gzf[sub] - np.floor(gzf[sub])
        return np.where(ok, index, -1), fx, fz
    terrain.cell_at = cell_at
    return terrain


# -- mesh ------------------------------------------------------------------------

def build_mesh(terrain: Terrain) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vertices (N, 3) in base units (bx, height, bz) and triangles (T, 3).

    Every level fills the cells no finer level covers. Vertices of a finer level that
    lie on the edge of a coarser cell take the coarser edge's height, so the levels
    meet without cracks."""
    cells_of: List[Tuple[HeightLevel, np.ndarray]] = []
    covered: Optional[Tuple[HeightLevel, np.ndarray]] = None  # (level, cells filled by it and finer ones)
    for lv in terrain.levels:
        h = lv.heights
        valid = ~np.isnan(h)
        cell_ok = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
        filled = np.zeros_like(cell_ok)
        if covered is not None:
            fl, fcells = covered
            ratio = lv.step // fl.step
            # finer cell (r, c) lies in this level's cell ((z - z0) // step, (x - x0) // step)
            fz = fl.z0 + np.arange(fcells.shape[0]) * fl.step
            fx = fl.x0 + np.arange(fcells.shape[1]) * fl.step
            rz = (fz - lv.z0) // lv.step
            rx = (fx - lv.x0) // lv.step
            count = np.zeros(cell_ok.shape, np.int32)
            rr, cc = np.nonzero(fcells)
            zi, xi = rz[rr], rx[cc]
            keep = (zi >= 0) & (zi < count.shape[0]) & (xi >= 0) & (xi < count.shape[1])
            np.add.at(count, (zi[keep], xi[keep]), 1)
            filled = count >= ratio * ratio
        emit = cell_ok & ~filled
        cells_of.append((lv, emit))
        covered = (lv, emit | filled)

    verts: List[np.ndarray] = []
    tris: List[np.ndarray] = []
    offset = 0
    for lv, emit in cells_of:
        rr, cc = np.nonzero(emit)
        if not len(rr):
            continue
        corners = np.stack([np.stack([rr, cc], 1), np.stack([rr + 1, cc], 1),
                            np.stack([rr, cc + 1], 1), np.stack([rr + 1, cc + 1], 1)], 1).reshape(-1, 2)
        key, inverse = np.unique(corners[:, 0].astype(np.int64) * (1 << 32) + corners[:, 1], return_inverse=True)
        r, c = key >> 32, key & 0xFFFFFFFF
        pts = np.stack([lv.x0 + c * lv.step, lv.heights[r, c], lv.z0 + r * lv.step], 1).astype(np.float64)
        q = inverse.reshape(-1, 4) + offset  # (z, x), (z+1, x), (z, x+1), (z+1, x+1)
        # split along (x+1, z) - (x, z+1) as the game does; both face +y
        tris.append(np.concatenate([q[:, [0, 1, 2]], q[:, [2, 1, 3]]]))
        verts.append(pts)
        offset += len(pts)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0)
    pts = np.concatenate(verts)
    tri = np.concatenate(tris)
    # snap finer vertices on coarser cell edges
    level_of = np.concatenate([np.full(len(v), i) for i, v in enumerate(verts)])
    used = [i for i, (lv, emit) in enumerate(cells_of) if emit.any()]
    for li in used[1:]:
        lv, emit = cells_of[li]
        finer = level_of < li
        if not finer.any():
            continue
        x = (pts[finer, 0] - lv.x0) / lv.step
        z = (pts[finer, 2] - lv.z0) / lv.step
        h = lv.heights
        rows, cols = emit.shape

        def emitted(r, c):
            ok = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
            out = np.zeros(len(r), bool)
            out[ok] = emit[r[ok], c[ok]]
            return out

        xi, zi = np.floor(x).astype(np.int64), np.floor(z).astype(np.int64)
        on_x = x == xi  # on a vertical grid line of this level
        on_z = z == zi
        new = pts[finer, 1].copy()
        # vertical lines: the edge from (zi, xi) to (zi + 1, xi)
        m = on_x & (emitted(zi, xi - 1) | emitted(zi, xi))
        if m.any():
            t = z[m] - zi[m]
            a = h[np.clip(zi[m], 0, h.shape[0] - 1), np.clip(xi[m], 0, h.shape[1] - 1)]
            b = h[np.clip(zi[m] + 1, 0, h.shape[0] - 1), np.clip(xi[m], 0, h.shape[1] - 1)]
            new[m] = np.where(np.isnan(b) | (t == 0), a, a + (b - a) * t)
        m = on_z & ~on_x & (emitted(zi - 1, xi) | emitted(zi, xi))
        if m.any():
            t = x[m] - xi[m]
            a = h[np.clip(zi[m], 0, h.shape[0] - 1), np.clip(xi[m], 0, h.shape[1] - 1)]
            b = h[np.clip(zi[m], 0, h.shape[0] - 1), np.clip(xi[m] + 1, 0, h.shape[1] - 1)]
            new[m] = np.where(np.isnan(b), a, a + (b - a) * t)
        idx = np.nonzero(finer)[0]
        pts[idx, 1] = np.where(np.isnan(new), pts[idx, 1], new)
    # merge vertices shared between levels
    key = np.round(pts[:, 0]).astype(np.int64) * (1 << 32) + np.round(pts[:, 2]).astype(np.int64)
    key, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    return pts[first], inverse.reshape(-1)[tri], level_of[first]


def world_mesh(terrain: Terrain) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Positions (metres), triangles, vertex normals and texture rectangle UVs."""
    base, tri, _ = build_mesh(terrain)
    pos = np.column_stack([base, np.ones(len(base))]) @ terrain.to_world.T
    if np.linalg.det(terrain.to_world[:, :3]) < 0:  # mirrored (Bespin's underside)
        tri = tri[:, [0, 2, 1]]
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    face = np.cross(b - a, c - a)
    normals = np.zeros_like(pos)
    for k in range(3):
        np.add.at(normals, tri[:, k], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(length > 0, normals / np.maximum(length, 1e-12), np.array([0.0, 1.0, 0.0]))
    bx0, bz0, bx1, bz1 = terrain.rect
    uv = np.column_stack([(base[:, 0] - bx0) / (bx1 - bx0), (base[:, 2] - bz0) / (bz1 - bz0)])
    return pos, tri, normals, uv


# -- textures --------------------------------------------------------------------

def _mips(img: np.ndarray) -> List[np.ndarray]:
    levels = [img.astype(np.float32)]
    while min(levels[-1].shape[:2]) > 1:
        a = levels[-1]
        hh, ww = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        levels.append(a[:hh:2, :ww:2] * 0.25 + a[1:hh:2, :ww:2] * 0.25 + a[:hh:2, 1:ww:2] * 0.25 + a[1:hh:2, 1:ww:2] * 0.25)
    return levels


def _sample(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear, wrapping."""
    h, w = img.shape[:2]
    x, y = u * w - 0.5, v * h - 0.5
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0, y0 = x0.astype(np.int64) % w, y0.astype(np.int64) % h
    x1, y1 = (x0 + 1) % w, (y0 + 1) % h
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x1] * fx * (1 - fy)
            + img[y1, x0] * (1 - fx) * fy + img[y1, x1] * fx * fy)


def texture_size(terrain: Terrain, limit: int = 4096) -> Tuple[int, int]:
    """Pixels of the baked texture: the native blend mask density, halved until the
    longer side fits ``limit``."""
    bx0, bz0, bx1, bz1 = terrain.rect
    density = terrain.density
    while max(bx1 - bx0, bz1 - bz0) * density > limit:
        density /= 2
    return max(int(round((bx1 - bx0) * density)), 1), max(int(round((bz1 - bz0) * density)), 1)


def bake(terrain: Terrain, layer_images: List[Optional[np.ndarray]], size: Tuple[int, int],
         weight_size: Tuple[int, int]) -> Tuple[np.ndarray, List[np.ndarray]]:
    """The blended color over the texture rectangle (RGBA8, ``size``) and the layer
    weights packed four to an RGBA8 image (``weight_size``, a divisor of ``size``)."""
    width, height = size
    ww, wh = weight_size
    fx_, fz_ = width // ww, height // wh
    bx0, bz0, bx1, bz1 = terrain.rect
    n_layers = len(terrain.layers)
    color = np.zeros((height, width, 4), np.uint8)
    color[..., 3] = 255
    weights = [np.zeros((wh, ww, 4), np.uint8) for _ in range((n_layers + 3) // 4)]
    pyramids = [_mips(img[..., :3]) if img is not None else None for img in layer_images]
    px = (bx1 - bx0) / width  # base units per pixel
    xs = bx0 + (np.arange(width) + 0.5) * px
    chunk = max(fz_, (1 << 18) // width // fz_ * fz_)
    for row in range(0, height, chunk):
        rows = min(chunk, height - row)
        zs = bz0 + (np.arange(row, row + rows) + 0.5) * (bz1 - bz0) / height
        bx = np.tile(xs, rows)
        bz = np.repeat(zs, width)
        ids, w = terrain.weights(bx, bz)
        acc = np.zeros((len(bx), 3), np.float32)
        dense = np.zeros((len(bx), max(n_layers, 1)), np.float32)
        for k in range(3):
            ok = ids[:, k] >= 0
            np.add.at(dense, (np.nonzero(ok)[0], ids[ok, k]), w[ok, k])
        for li in range(n_layers):
            sel = dense[:, li] > 0
            pyr = pyramids[li]
            if not sel.any() or pyr is None:
                continue
            layer = terrain.layers[li]
            u = layer.uv[0, 0] * bx[sel] + layer.uv[0, 1] * bz[sel] + layer.uv[0, 2]
            v = layer.uv[1, 0] * bx[sel] + layer.uv[1, 1] * bz[sel] + layer.uv[1, 2]
            texels = np.hypot(layer.uv[0, 0], layer.uv[0, 1]) * px * pyr[0].shape[1]
            lod = int(min(max(round(math.log2(max(texels, 1.0))), 0), len(pyr) - 1))
            acc[sel] += dense[sel, li, None] * _sample(pyr[lod], u, v)
        color[row:row + rows, :, :3] = np.clip(acc, 0, 255).reshape(rows, width, 3).astype(np.uint8)
        # weights, averaged down to the weight map size
        d = dense.reshape(rows // fz_, fz_, ww, fx_, -1).mean((1, 3))
        for li in range(n_layers):
            weights[li // 4][row // fz_:(row + rows) // fz_, :, li % 4] = np.clip(d[..., li] * 255 + 0.5, 0, 255).astype(np.uint8)
    return color, weights
