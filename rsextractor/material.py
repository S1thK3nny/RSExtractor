"""``_si`` material blocks ("ABME") for Rogue Squadron II and III.

Layout (RS2 FUN_7fd2c3c8)::

    "ABME", u32 count, then ``count`` materials of 0x110 (RS2) / 0x120 (RS3) bytes
    material +0x00 name[16]
    RS2: +0x10 u32 type, +0x14 u32 flags, +0x18 RGBA diffuse, +0x1C RGBA specular,
         +0x20 f32 shininess, +0x24 f32 reflectivity
    RS3: +0x10 u32 type, +0x14 u32 flags, +0x1C RGBA diffuse, +0x20 RGBA specular,
         +0x24 f32 shininess, +0x2D u8 reflectivity (/ 255)
    +0x30 texture count (u32 in RS2, u8 in RS3)
    +0x34 texture records, 0x34 (RS2) / 0x48 (RS3) bytes each:
        +0x00 u16 width, +0x02 u16 height, +0x04 u8 mip count, +0x05 u8 GX format
        (RS3 tags one CMPR texture, the Bespin sky, as 15)
        RS2 +0x14 / RS3 +0x34: SHA-1 of the texture (key into the texture pools)
    map slots, texture index per map type (0xFF = unused):
        RS2 +0x104: six {u8 texture, u8 mode}  (mode 3: the map is the texture's alpha)
        RS3 +0x10C: five {u8 texture, u8 swap, u16}  (swap 1: a color channel, not alpha)

    map type      RS2      RS3      shader (RS2 table 0x7fdd96a0 by material type)
    color         0        0
    opacity       1        2        output alpha for alpha test (flag 0x4) or blending
                                    (0x4000, additive 0x8000); RS2 FUN_7fd3c5ec, RS3 FUN_7f3cafec
    self lit      2        3        lit + K * (1 - alpha): alpha 0 shines (FUN_7fd35a3c, FUN_7fd37098)
    bump tilt     3, 4     4 CMPR   indirect normal offsets (FUN_7f3d074c)
    bump height   5        4        emboss (FUN_7fd3767c, FUN_7fd37f70, FUN_7f3cdf28)

    A map naming the color texture reads its alpha, so the 1 bit alpha of a CMPR
    color map is a mask whose meaning depends on the slot: transparency, lit screens
    (alpha 0) or emboss grooves (alpha 0, craft hulls). Without such a slot it is unused.

Flags 0x400 / 0x800 select clamped addressing in S / T (wrap otherwise).

The shader is picked by the type (RS2) or by type bits (RS3, FUN_7f3d6430, mapped
to the same numbering), see :func:`shader_index`. Lit shaders 4-8 and 16-19 have a
reflective variant (+5 and +4, RS3 also 30-33 -> 34-37, tables 0x7fdd89d8 /
0x7f67259c) used when the reflectivity exceeds 0.05 (RS2) or 20/255 (RS3). It
lights the color map with ``lerp(lighting, environment, reflectivity)``; the
environment is the level's ``envMap`` cube rendered into a sphere map. Shaders with
a shininess add a highlight from a texture looked up by the view space normal
(RS2 ``envmap/hilite``), scaled by ``0.2 + 3.8 * shininess`` around the half
vector (FUN_7fd2f7f0, RS3 FUN_7f3c2b74) and tinted by the specular color. Odd
variants (6, 8, 17, 19, 31, 33 and their reflective forms) and the craft hull
(27) mask it with ``4 * (green - 0.25)`` of the color map (TEV swap table 1 puts
green into alpha, FUN_7fd34898).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .bump import HEIGHT, TILT

# map type -> slot per game
OPACITY = {2: 1, 3: 2}
SELF_LIT = {2: 2, 3: 3}

CMPR = 14
FORMAT_ALIASES = {15: CMPR}  # data laid out exactly as GX CMPR

# Shaders with a reflective variant, and the threshold above which it is used.
REFLECTIVE_SHADERS = set(range(4, 9)) | set(range(16, 20)) | set(range(30, 34))
MIN_REFLECTIVITY = {2: 0.05, 3: 19.5 / 255}  # RS2 f32 > 0.05, RS3 byte >= 20
# Shaders that mask the highlight with the color map's green channel.
MASKED_SPECULAR = {6, 8, 17, 19, 27, 31, 33}


def shader_index(mtype: int, flags: int) -> int:
    """Index into the shader table (RS2 the type itself; RS3 FUN_7f3d6430 maps type bits)."""
    if mtype < 0x80000000:
        return mtype
    if not mtype & 1:
        return 4 if mtype & 4 else 1
    if mtype & 0x40:
        if not flags & 0x4000:
            return 0x19 if mtype & 2 else 0x18
        return 0x1B if mtype & 4 else 0x1A
    if mtype & 4:
        base = 0x1E if flags & 0x100 else 0x10 if mtype & 0x20 else 5
        return base + (1 if mtype & 8 else 0) + (2 if mtype & 2 else 0)
    if not mtype & 0x20:
        return 3 if mtype & 2 else 2
    if not flags & 0x100:
        return 0xF if mtype & 2 else 0xE
    return 0x1D if mtype & 2 else 0x1C


def specular_exponent(shininess: float) -> float:
    """Blinn-Phong exponent with the same half width as the game's highlight.

    The highlight texture falls off linearly to the edge, ``1 - k * sin(angle to the
    half vector)`` with ``k = 0.2 + 3.8 * shininess``, so it is half bright at
    ``sin = 0.5 / k``; ``cos^n`` is half there for the returned ``n``."""
    k = 0.2 + 3.8 * min(max(shininess, 0.0), 1.0)
    sin_half = min(0.5 / k, 1.0)
    cos_half = math.sqrt(1.0 - sin_half * sin_half)
    if cos_half <= 0.0:
        return 1.0
    return max(1.0, math.log(0.5) / math.log(cos_half))

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
    slots: List[int] = field(default_factory=list)  # texture index per map type, -1 = unused
    slot_modes: List[int] = field(default_factory=list)  # RS2 mode / RS3 swap byte per slot
    game: int = 2
    shininess: float = 0.0       # 0 = no highlight
    reflectivity: float = 0.0    # as stored; see ``reflection``

    @property
    def shader(self) -> int:
        return shader_index(self.type, self.flags)

    @property
    def specular(self) -> bool:
        return self.shininess > 0.0

    @property
    def specular_masked(self) -> bool:
        return self.specular and self.shader in MASKED_SPECULAR

    @property
    def reflection(self) -> float:
        """Share of the environment in the lighting (0 when the shader has no reflection)."""
        if self.shader in REFLECTIVE_SHADERS and self.reflectivity > MIN_REFLECTIVITY[self.game]:
            return min(self.reflectivity, 1.0)
        return 0.0

    def slot(self, n: int) -> int:
        i = self.slots[n] if n < len(self.slots) else -1
        return i if 0 <= i < len(self.textures) else -1

    def reads_alpha(self, n: int) -> bool:
        """Whether slot ``n`` uses its texture's alpha (else its color, as intensity)."""
        mode = self.slot_modes[n] if n < len(self.slot_modes) else 0
        return mode == 3 or self.textures[self.slot(n)].format in (0, 1) if self.game == 2 else mode != 1

    @property
    def bump(self) -> Optional[Tuple[int, str, bool]]:
        """(texture index, ``TILT`` or ``HEIGHT``, height read from alpha) of the bump map."""
        if self.game == 2:
            if self.slot(3) >= 0:
                return self.slot(3), TILT, False
            return (self.slot(5), HEIGHT, self.reads_alpha(5)) if self.slot(5) >= 0 else None
        i = self.slot(4)
        if i < 0:
            return None
        if i > 0 and self.textures[i].format == CMPR:
            return i, TILT, False
        return i, HEIGHT, self.reads_alpha(4)

    def _map(self, slots: dict) -> Optional[Tuple[int, bool]]:
        n = slots[self.game]
        return (self.slot(n), self.reads_alpha(n)) if self.slot(n) >= 0 else None

    @property
    def opacity(self) -> Optional[Tuple[int, bool]]:
        """(texture index, read alpha) of the map that makes the material see through."""
        return self._map(OPACITY)

    @property
    def self_lit(self) -> Optional[Tuple[int, bool]]:
        """(texture index, read alpha) of the mask whose 0 values make the color map shine."""
        return self._map(SELF_LIT)

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
    slot_off, slot_size, nslots = (0x104, 2, 6) if game == 2 else (0x10C, 4, 5)
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
        slots = [si[base + slot_off + k * slot_size] for k in range(nslots)]
        modes = [si[base + slot_off + k * slot_size + 1] for k in range(nslots)]
        if game == 2:
            shininess, reflectivity = struct.unpack_from(">ff", si, base + 0x20)
        else:
            shininess, reflectivity = struct.unpack_from(">f", si, base + 0x24)[0], si[base + 0x2D] / 255.0
        if not math.isfinite(shininess):
            shininess = 0.0
        out.append(Material(name, mtype, flags, _rgba(si, base + color_off), _rgba(si, base + color_off + 4),
                            textures, [-1 if t == 0xFF else t for t in slots], modes, game,
                            shininess, reflectivity))
    return out
