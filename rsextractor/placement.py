"""Level objects: the ``dat`` part of a level resource (RS2 ``FUN_7fc1ded4``, RS3
``FUN_7f338410`` relocate it; RS3 ``FUN_7f33b35c`` spawns one entity per record by class).

``dat`` starts with (count, offset) pairs of record lists; the first (RS2 ``+0x00``,
RS3 ``+0x04``) is the level's objects: ``offset`` points at a table of ``count`` u32
offsets, each relative to the table, to one record. Every record starts with the
same header::

    +0x00 u16 class (index into the templates, RS2 list +0x60, RS3 +0x7C: 353 of them)
    +0x0C u32 -> name (relative to the record, 0 = none)
    +0x10 f32 position[3]
    +0x1C f32 rotation[3]   degrees, X then Y then Z (Maya XYZ)
    +0x28 f32 scale[3]
    +0x3C u32 flags         bit 1: +0x4C is not a name
    +0x4C u32 -> model name (relative to the record)
    +0x50 u32 -> second name: the ground variant of a flight model
                 (``fltrebhothturret`` / ``grndrebhothturret``) or, for
                 characters, their clip set

The class data follows (the game's per class table at ``0x7F66CF70`` holds the
offsets of each block). Records without a model (start points, triggers,
splines, effects) are skipped here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class Placed:
    cls: int
    names: Tuple[str, ...]  # model name candidates, flight variant first
    matrix: np.ndarray      # 4x4, model space -> level


def _name(dat: bytes, start: int, end: int) -> Optional[str]:
    if not start < end:
        return None
    stop = dat.find(b"\0", start, end)
    raw = dat[start:stop] if stop > start else b""
    if not 0 < len(raw) < 64 or not all(32 < c < 127 for c in raw):
        return None
    return raw.decode("ascii")


def euler_matrix(rotation, position, scale) -> np.ndarray:
    """4x4 matrix of a record pose: scale, then rotate about X, Y, Z (degrees), then move."""
    rx, ry, rz = np.radians(rotation)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = mz @ my @ mx @ np.diag(scale)
    m[:3, 3] = position
    return m


def parse_dat(dat: bytes, game: int) -> List[Placed]:
    if len(dat) < 12:
        return []
    count, table = struct.unpack_from(">II", dat, 0 if game == 2 else 4)
    if not count or table + 4 * count > len(dat):
        return []
    starts = sorted({table + struct.unpack_from(">I", dat, table + 4 * i)[0] for i in range(count)})
    out: List[Placed] = []
    for i, rec in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(dat)
        if rec + 0x54 > end:
            continue
        cls = struct.unpack_from(">H", dat, rec)[0]
        flags, = struct.unpack_from(">I", dat, rec + 0x3C)
        first, second = struct.unpack_from(">II", dat, rec + 0x4C)
        names = []
        if not flags & 2 and first >= 0x50:
            names.append(_name(dat, rec + first, end))
        if second >= 0x54:
            names.append(_name(dat, rec + second, end))
        names = tuple(n for n in names if n)
        if not names:
            continue
        values = struct.unpack_from(">9f", dat, rec + 0x10)
        out.append(Placed(cls, names, euler_matrix(values[3:6], values[0:3], values[6:9])))
    return out
