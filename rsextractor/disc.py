"""GameCube disc image reader (plain .iso / .gcm and NKit .nkit.iso).

NKit GameCube images keep a valid FST whose offsets point into the NKit
file itself, so they read exactly like a regular image.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Dict, Iterator, Optional


@dataclass
class DiscFile:
    path: str
    offset: int
    size: int


class Disc:
    def __init__(self, path: str):
        self.path = path
        self._f: BinaryIO = open(path, "rb")
        header = self._read(0, 0x440)
        self.game_id = header[:6].decode("ascii", "replace")
        self.title = header[0x20:0x60].split(b"\0")[0].decode("ascii", "replace")
        self.dol_offset, fst_offset, fst_size = struct.unpack(">III", header[0x420:0x42C])
        self.files: Dict[str, DiscFile] = {}
        self._parse_fst(self._read(fst_offset, fst_size))

    def _read(self, offset: int, size: int) -> bytes:
        self._f.seek(offset)
        return self._f.read(size)

    def _parse_fst(self, fst: bytes) -> None:
        count = struct.unpack_from(">I", fst, 8)[0]
        strings = count * 12

        def name_at(off: int) -> str:
            end = fst.index(b"\0", strings + off)
            return fst[strings + off:end].decode("latin-1")

        stack = [(count, "")]
        for i in range(1, count):
            while stack and i >= stack[-1][0]:
                stack.pop()
            word0, a, b = struct.unpack_from(">III", fst, i * 12)
            is_dir = word0 >> 24
            name = name_at(word0 & 0xFFFFFF)
            parent = stack[-1][1] if stack else ""
            path = f"{parent}/{name}" if parent else name
            if is_dir:
                stack.append((b, path))
            else:
                self.files[path.lower()] = DiscFile(path, a, b)

    def __iter__(self) -> Iterator[DiscFile]:
        return iter(self.files.values())

    def find(self, name: str) -> DiscFile:
        """Find a file by path or bare file name (case-insensitive)."""
        key = name.lower()
        if key in self.files:
            return self.files[key]
        for path, entry in self.files.items():
            if path.rsplit("/", 1)[-1] == key:
                return entry
        raise FileNotFoundError(f"{name} not found on disc {self.game_id}")

    def exists(self, name: str) -> bool:
        try:
            self.find(name)
            return True
        except FileNotFoundError:
            return False

    def read(self, name: str) -> bytes:
        entry = self.find(name)
        return self._read(entry.offset, entry.size)

    def read_range(self, name: str, offset: int, size: int) -> bytes:
        entry = self.find(name)
        if offset + size > entry.size:
            raise ValueError(f"range {offset:#x}+{size:#x} outside {entry.path}")
        return self._read(entry.offset + offset, size)

    def read_dol(self) -> bytes:
        header = self._read(self.dol_offset, 0x100)
        offs = struct.unpack_from(">18I", header, 0)
        sizes = struct.unpack_from(">18I", header, 0x90)
        end = max(o + s for o, s in zip(offs, sizes) if s)
        return self._read(self.dol_offset, end)

    def read_game_dol(self) -> Optional[bytes]:
        """The game's own DOL wrapped inside the boot DOL: stored raw as text
        section 2 (Rogue Leader) or zlib packed in data section 0 (Rebel Strike,
        after a ``u32 packed size, u32 unpacked size`` header)."""
        dol = self.read_dol()
        offs = struct.unpack_from(">18I", dol, 0)
        sizes = struct.unpack_from(">18I", dol, 0x90)
        for i in (2, 7):
            o, s = offs[i], sizes[i]
            if not s or o + s > len(dol):
                continue
            blob = dol[o:o + s]
            if i == 7 and len(blob) > 8:
                packed, unpacked = struct.unpack_from(">II", blob, 0)
                try:
                    blob = zlib.decompress(blob[8:8 + packed])
                except zlib.error:
                    continue
                if len(blob) != unpacked:
                    continue
            if len(blob) > 0x100 and struct.unpack_from(">I", blob, 0)[0] == 0x100:
                return blob
        return None

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "Disc":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
