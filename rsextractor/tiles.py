"""RS3 tile levels: ``tmap`` + ``bmap`` lay out a level from square tiles and the
buildings standing on them (RS3 FUN_7f35cfb0, see docs/FORMATS.md "Tile maps").

``tmap``::

    u16, u16 grids, u16, u16 tile types, then grids of 0x154 bytes:
    +0x00 f32 origin[3], +0x0C f32 axis_u[3], +0x18 f32 axis_v[3], +0x24 f32 size[3]
    +0x30 u32 width, u32 height, u32 cell offset (from the grid record)
    cells: u8[width * height], row major; 0x1F = empty, else bits 0-4 tile type,
    bits 6-7 quarter turns about Y

``bmap``: per tile type ``{u32 count, u32 building[8]}`` (0x24 bytes, type in the
low half). The tile's ``t###_0_lm`` holds one 3x4 matrix per building, relative
to the tile. Tile models are ``t###_v``, buildings ``b###_v`` (``v`` = level of
detail, 0 = full), kept in ``bData.pool`` packages the level references.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, List, Tuple

import numpy as np

EMPTY = 0x1F
GRID_SIZE = 0x154
BMAP_SIZE = 0x24


@dataclass
class TileGrid:
    origin: np.ndarray   # (3,)
    axis_u: np.ndarray   # (3,) direction of the columns
    axis_v: np.ndarray   # (3,) direction of the rows
    size: np.ndarray     # (3,) cell size, x and z used
    width: int
    height: int
    cells: bytes


def parse_tmap(data: bytes) -> List[TileGrid]:
    if len(data) < 8:
        return []
    count = struct.unpack_from(">H", data, 2)[0]
    grids = []
    for i in range(count):
        o = 8 + i * GRID_SIZE
        if o + 0x3C > len(data):
            break
        vec = lambda at: np.array(struct.unpack_from(">3f", data, o + at), np.float64)
        width, height, cells = struct.unpack_from(">3I", data, o + 0x30)
        if o + cells + width * height > len(data):
            continue
        grids.append(TileGrid(vec(0), vec(0x0C), vec(0x18), vec(0x24), width, height,
                              data[o + cells:o + cells + width * height]))
    return grids


def parse_bmap(data: bytes) -> List[List[int]]:
    """Building types standing on each tile type."""
    out = []
    for o in range(0, len(data) - BMAP_SIZE + 1, BMAP_SIZE):
        count = min(struct.unpack_from(">I", data, o)[0], 8)
        out.append([struct.unpack_from(">I", data, o + 4 + 4 * k)[0] & 0xFFFF for k in range(count)])
    return out


def parse_lm(data: bytes) -> List[np.ndarray]:
    """4x4 matrices of a ``_lm`` part (3x4 floats each, row major)."""
    out = []
    for o in range(0, len(data) - 0x2F, 0x30):
        m = np.eye(4)
        m[:3, :4] = np.array(struct.unpack_from(">12f", data, o), np.float64).reshape(3, 4)
        out.append(m)
    return out


def quarter_turns(k: int) -> np.ndarray:
    """Rotation about Y by ``k`` quarter turns; one turn maps +X to -Z and +Z to +X
    (FUN_7f35c8ec, FUN_7f35c620)."""
    m = np.eye(4)
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][k & 3]
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def tile_cells(grid: TileGrid) -> Iterator[Tuple[int, np.ndarray]]:
    """(tile type, world matrix) of every filled cell; tiles are centred on their cell."""
    for row in range(grid.height):
        for col in range(grid.width):
            cell = grid.cells[col + row * grid.width]
            if cell & 0x1F == EMPTY:
                continue
            m = quarter_turns(cell >> 6)
            m[:3, 3] = grid.origin + col * grid.size[0] * grid.axis_u + row * grid.size[2] * grid.axis_v
            yield cell & 0x1F, m
