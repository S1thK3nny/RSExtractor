"""Factor 5 collision hulls (``_cl``) for Rogue Squadron II and III.

A relocatable image like ``_ge`` (pointers are offsets from the part start)::

    +0x00 u32 -> vertices (f32[3] each, up to the normals)
    +0x04 u32 -> plane normals (f32[3] each, up to the end of the part)
    +0x1C u32 list count (RS2 4, RS3 8)   +0x20 u32 -> list[count]
    list:   u32 n, then n x { u32, u32 -> hull }
    hull:   f32 bbox_min[3], f32 bbox_max[3], then the octree root node
    node:   u32 -> child[8] (0 = none), u32, u32, u32 record count, records
    record: u8 flags, u8 n, u16 plane, u16 0xFFFF, u16 0,
            n x { u16 vertex, u16 edge plane } (flags bit 0) or n x u16 vertex (RS3, 2 byte aligned)

Every polygon runs clockwise around its plane normal (GX front faces). A polygon
that straddles octree cells is listed in each of them. The wire overlays (RS2
``gfxWIREOverlays*``, RS3 ``wireOverlay*``) keep their whole wireframe here; their
``_ge`` is a single quad.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class Hull:
    positions: np.ndarray            # (N, 3)
    polygons: List[Tuple[int, ...]]  # counter clockwise (FBX front faces)
    normals: np.ndarray              # (P, 3) one plane normal per polygon


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def parse_collision(cl: bytes) -> Hull:
    vertices, planes = _u32(cl, 0), _u32(cl, 4)
    positions = np.frombuffer(cl, ">f4", (planes - vertices) // 4, vertices).astype(np.float64).reshape(-1, 3)
    normals = np.frombuffer(cl, ">f4", (len(cl) - planes) // 12 * 3, planes).astype(np.float64).reshape(-1, 3)
    found: Dict[Tuple[int, ...], int] = {}
    seen = set()

    def walk(node: int) -> None:
        if node in seen or node + 0x2C > len(cl):
            return
        seen.add(node)
        p = node + 0x2C
        for _ in range(_u32(cl, node + 0x28)):
            flags, n, plane = cl[p], cl[p + 1], _u16(cl, p + 2)
            stride = 4 if flags & 1 else 2
            ring = tuple(_u16(cl, p + 8 + stride * i) for i in range(n))
            p += 8 + stride * n
            if n >= 3 and max(ring) < len(positions) and plane < len(normals):
                start = ring.index(min(ring))
                found.setdefault(ring[start:] + ring[:start], plane)
        for i in range(8):
            child = _u32(cl, node + 4 * i)
            if child:
                walk(child)

    for k in range(_u32(cl, 0x1C)):
        head = _u32(cl, 0x20 + 4 * k)
        for i in range(_u32(cl, head)):
            walk(_u32(cl, head + 8 + 8 * i) + 0x18)
    polygons = [ring[::-1] for ring in found]
    return Hull(positions, polygons, normals[list(found.values())].reshape(-1, 3))
