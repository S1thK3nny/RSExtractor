"""``_si`` material blocks ("ABME") for Rogue Squadron II and III.

Layout (RS2 FUN_7fd2c3c8)::

    "ABME", u32 count, then ``count`` materials of 0x110 (RS2) / 0x120 (RS3) bytes
    material +0x00 name[16]
    RS2: +0x10 u32 type, +0x14 u32 flags, +0x18 RGBA color, +0x1C RGBA color
    RS3: +0x10 u32 type, +0x14 u32 flags, +0x1C RGBA color, +0x20 RGBA color
    +0x30 texture count (u32 in RS2, u8 in RS3)
    +0x34 texture records, 0x34 (RS2) / 0x48 (RS3) bytes each:
        +0x00 u16 width, +0x02 u16 height, +0x04 u8 mip count, +0x05 u8 GX format
        (RS3 tags one CMPR texture, the Bespin sky, as 15)
        RS2 +0x14 / RS3 +0x34: SHA-1 of the texture (key into the texture pools)
    RS2 +0x104: six u16 slots mapping map types to texture indices (0xFF = unused)

Flags 0x400 / 0x800 select clamped addressing in S / T (wrap otherwise).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List

CMPR = 14
FORMAT_ALIASES = {15: CMPR}  # data laid out exactly as GX CMPR

@dataclass
class TextureRef:
    width: int
    height: int
    mips: int
    format: int
    hash: bytes


@dataclass
class Material:
    name: str
    type: int
    flags: int
    color0: tuple
    color1: tuple
    textures: List[TextureRef] = field(default_factory=list)

    @property
    def clamp_s(self) -> bool:
        return bool(self.flags & 0x400)

    @property
    def clamp_t(self) -> bool:
        return bool(self.flags & 0x800)


def _rgba(b: bytes, o: int) -> tuple:
    return tuple(c / 255.0 for c in b[o:o + 4])


def parse_materials(si: bytes, game: int = 2) -> List[Material]:
    if len(si) < 8 or si[:4] != b"ABME":
        return []
    count = struct.unpack_from(">I", si, 4)[0]
    size, rec, hash_off = (0x110, 0x34, 0x14) if game == 2 else (0x120, 0x48, 0x34)
    color_off = 0x18 if game == 2 else 0x1C
    out: List[Material] = []
    for i in range(count):
        base = 8 + i * size
        if base + size > len(si):
            break
        name = si[base:base + 16].split(b"\0")[0].decode("latin-1") or f"material{i}"
        mtype, flags = struct.unpack_from(">II", si, base + 0x10)
        ntex = struct.unpack_from(">I", si, base + 0x30)[0] if game == 2 else si[base + 0x30]
        textures = []
        for t in range(min(ntex, 4)):
            r = base + 0x34 + t * rec
            if r + rec > base + size:
                break
            w, h, mips, fmt = struct.unpack_from(">HHBB", si, r)
            textures.append(TextureRef(w, h, mips, FORMAT_ALIASES.get(fmt, fmt),
                                       si[r + hash_off:r + hash_off + 20]))
        out.append(Material(name, mtype, flags, _rgba(si, base + color_off), _rgba(si, base + color_off + 4),
                            textures))
    return out
