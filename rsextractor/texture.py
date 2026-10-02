"""Texture pools: ``SMMJ`` indices (``*_tpi``) + data (``*_tpd``), and RS3's streamed
global pool ``gTexPool.cData``.

Textures are referenced from materials by SHA-1, so one library per game maps
hash -> raw GX texture bytes.

``SMMJ`` index::

    "SMMJ", u32 count, then entries
      RS2: { u8 sha1[20], u32 byte_offset }                       (24 bytes)
           offset bit 0x2000000: texture is shared from another pool (look up by hash)
      RS3: { u8 sha1[20], u32 offset_in_32_byte_units, u8, u8, u16 } (28 bytes)

RS3 local pools may hold only the lower mip levels of a texture (the u8 fields
count the missing top levels); the full chain lives in ``gTexPool.cData``::

    "ctp0", u32 count, then count x { u32 pool_offset, u32 file_offset,
                                       u32 packed_size_a, u32 packed_size_b }

Part A (top mips, may be empty) sits at ``file_offset``, part B at the next
32-byte boundary; both use the LZ scheme in :func:`lz_decompress`
(FUN_7f3e9ffc).  ``pool/global_tpool`` is the SMMJ index of the global pool.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from . import gxtexture as gx
from .material import TextureRef


def lz_decompress(src: bytes) -> bytes:
    """Factor 5 LZ (RS3 texture streaming). 4-byte header, byte 3 selects the variant."""
    if len(src) < 4 or src[3] not in (0, 1):
        raise ValueError("not an LZ stream")
    wide = src[3] == 1
    out = bytearray()
    p, n = 4, len(src)
    while p < n:
        b = src[p]
        if not b & 0x80:
            count = b + 1
            out += src[p + 1:p + 1 + count]
            p += 1 + count
            continue
        if wide:
            if p + 3 > n:
                break
            length = (b & 0x7F) + 4
            dist = ((src[p + 1] << 8) | src[p + 2]) + 1
            p += 3
        else:
            if p + 2 > n:
                break
            length = ((b >> 3) & 0xF) + 3
            dist = (((b & 7) << 8) | src[p + 1]) + 1
            p += 2
        start = len(out) - dist
        if start < 0:
            raise ValueError("LZ distance before start of output")
        if dist >= length:
            out += out[start:start + length]
        else:  # overlapping copy
            for i in range(length):
                out.append(out[start + i])
    return bytes(out)


@dataclass
class _Source:
    """Lazily resolved texture bytes."""
    loader: Callable[[], bytes]
    offset: int
    missing_mips: int = 0


class TextureLibrary:
    def __init__(self) -> None:
        self._local: Dict[bytes, _Source] = {}
        self._global: Dict[bytes, Tuple[int, int, int, int]] = {}  # hash -> ctp0 entry
        self._global_reader: Optional[Callable[[int, int], bytes]] = None
        self._cache: Dict[bytes, bytes] = {}

    def __len__(self) -> int:
        return len(set(self._local) | set(self._global))

    def __contains__(self, h: bytes) -> bool:
        return h in self._local or h in self._global

    # -- registration
    def add_pool(self, tpi: bytes, tpd: bytes | Callable[[], bytes], game: int) -> int:
        if len(tpi) < 8 or tpi[:4] != b"SMMJ":
            return 0
        count = struct.unpack_from(">I", tpi, 4)[0]
        size = 24 if game == 2 else 28
        loader = tpd if callable(tpd) else (lambda data=tpd: data)
        added = 0
        for i in range(count):
            e = 8 + i * size
            if e + size > len(tpi):
                break
            h = tpi[e:e + 20]
            off = struct.unpack_from(">I", tpi, e + 20)[0]
            if game == 2 and off & 0x2000000:
                continue  # data lives in another (resident) pool; resolved by hash elsewhere
            missing = 0
            if game == 3:
                off *= 32
                missing = tpi[e + 24]
            prev = self._local.get(h)
            if prev is None or missing < prev.missing_mips:
                self._local[h] = _Source(loader, off, missing)
                added += 1
        return added

    def add_global_pool(self, index: bytes, header: bytes, reader: Callable[[int, int], bytes]) -> None:
        """``index``: pool/global_tpool (SMMJ); ``header``: start of gTexPool.cData."""
        if index[:4] != b"SMMJ" or header[:4] != b"ctp0":
            return
        count = struct.unpack_from(">I", index, 4)[0]
        hcount = struct.unpack_from(">I", header, 4)[0]
        by_pool_offset = {}
        for i in range(hcount):
            entry = struct.unpack_from(">4I", header, 8 + 16 * i)
            by_pool_offset[entry[0]] = entry
        for i in range(count):
            e = 8 + i * 28
            h = index[e:e + 20]
            off = struct.unpack_from(">I", index, e + 20)[0] * 32
            if off in by_pool_offset:
                self._global[h] = by_pool_offset[off]
        self._global_reader = reader

    # -- lookup
    def raw(self, ref: TextureRef) -> Optional[bytes]:
        """Full mip chain for a texture reference, or None if not found."""
        h = ref.hash
        if h in self._cache:
            return self._cache[h]
        need = gx.texture_size(ref.format, ref.width, ref.height, ref.mips)
        data = None
        if h in self._global and self._global_reader is not None:
            _, file_off, size_a, size_b = self._global[h]
            parts = []
            if size_a:
                parts.append(lz_decompress(self._global_reader(file_off, size_a)))
            if size_b:
                parts.append(lz_decompress(self._global_reader(file_off + ((size_a + 31) & ~31), size_b)))
            data = b"".join(parts)
        if (data is None or len(data) < gx.level_size(ref.format, ref.width, ref.height)) and h in self._local:
            src = self._local[h]
            blob = src.loader()
            data = blob[src.offset:src.offset + need]  # starts at mip ``missing_mips``
        if data is not None:
            self._cache[h] = data
        return data

    def missing_mips(self, ref: TextureRef) -> int:
        """Number of top mip levels absent from the bytes returned by :meth:`raw`."""
        h = ref.hash
        if h in self._global:
            return 0
        src = self._local.get(h)
        return src.missing_mips if src else 0

    def decode(self, ref: TextureRef) -> Optional[np.ndarray]:
        """Decode the largest available mip level to RGBA."""
        data = self.raw(ref)
        if data is None:
            return None
        skip = self.missing_mips(ref)
        w, h = max(ref.width >> skip, 1), max(ref.height >> skip, 1)
        if len(data) < gx.level_size(ref.format, w, h):
            return None
        if ref.format not in gx.BLOCK or ref.format in (gx.C4, gx.C8, gx.C14X2):
            return None
        return gx.decode(data, ref.format, w, h)
