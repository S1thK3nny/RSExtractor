"""Factor 5 resource archives (``*.dir`` + ``*.dat``) and resource packages.

``*.dir`` is a CRLF text table::

    resname <padding> offset<TAB>length<TAB>attrib<TAB>depth

``attrib & 0x80`` marks a folder (its range covers its children).  Leaf
entries are zlib streams; Rogue Squadron III prepends a random-length,
4-byte aligned junk prefix before the stream.  Some leaves are stored raw.

A decompressed resource can itself be a *package*: a data blob followed by
a binary directory of 0x30-byte entries (name[0x20], offset, length,
attrib, end).  Packages hold the model parts (``name_su``, ``name_ge``,
``name_si``, ``name_an``, ``name_cl``).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional

from .disc import Disc

FOLDER = 0x80


@dataclass
class Entry:
    name: str
    offset: int
    length: int
    attrib: int
    depth: int

    @property
    def is_folder(self) -> bool:
        return bool(self.attrib & FOLDER)


def parse_dir(text: bytes) -> List[Entry]:
    entries = []
    for line in text.decode("latin-1").split("\r\n")[1:]:
        if not line.strip():
            continue
        head, length, attrib, depth = line.split("\t")[:4]
        head = head.rstrip()
        cut = head.rfind(" ")
        entries.append(Entry(head[:cut].strip(), int(head[cut + 1:]), int(length),
                             int(attrib), int(depth)))
    return entries


def decompress(raw: bytes, max_prefix: int = 0x1000) -> bytes:
    """Inflate a resource, skipping an RS3 junk prefix. Returns ``raw`` if not zlib."""
    limit = min(len(raw) - 2, max_prefix)
    for pos in range(0, limit, 4):
        if raw[pos] != 0x78 or ((raw[pos] << 8) | raw[pos + 1]) % 31:
            continue
        inflater = zlib.decompressobj()
        try:
            data = inflater.decompress(raw[pos:])
        except zlib.error:
            continue
        if inflater.eof:
            return data
    return raw


class Archive:
    """One ``.dir``/``.dat`` pair on a disc."""

    def __init__(self, disc: Disc, base: str):
        self.disc = disc
        self.base = base
        self.dat_name = base + ".dat"
        self.entries = parse_dir(disc.read(base + ".dir"))
        self.by_name: Dict[str, Entry] = {e.name.lower(): e for e in self.entries}

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def files(self) -> Iterator[Entry]:
        return (e for e in self.entries if not e.is_folder)

    def get(self, name: str) -> Optional[Entry]:
        return self.by_name.get(name.lower())

    def read_raw(self, entry: Entry) -> bytes:
        return self.disc.read_range(self.dat_name, entry.offset, entry.length)

    def read(self, entry: Entry | str) -> bytes:
        if isinstance(entry, str):
            found = self.get(entry)
            if found is None:
                raise KeyError(f"{entry} not in {self.base}")
            entry = found
        return decompress(self.read_raw(entry))


@dataclass
class PackageEntry:
    name: str
    offset: int
    length: int
    attrib: int
    path: str  # folder-qualified name, e.g. "plCraft/plCraft_ge"


def parse_package(data: bytes) -> Optional[List[PackageEntry]]:
    """Return the internal directory of a package (RS2 or RS3 layout), or None."""
    return _parse_package_rs2(data) or _parse_package_rs3(data)


def _parse_package_rs3(data: bytes) -> Optional[List[PackageEntry]]:
    """RS3: ``u32 data_start`` then 0x14-byte entries {name_off, off, len, attrib, end}.

    Offsets are relative to ``data_start``; names live in a string table whose
    offset is the last word of the all-zero terminator entry.
    """
    if len(data) < 0x2C:
        return None
    data_start = struct.unpack_from(">I", data, 0)[0]
    if data_start < 0x2C or data_start > len(data) or data_start % 4:
        return None
    raw = []
    pos = 4
    while pos + 0x14 <= data_start:
        name_off, off, length, attrib, end = struct.unpack_from(">5I", data, pos)
        if name_off == 0:
            break
        if name_off >= data_start or attrib & ~0xFF or data_start + off + length > len(data):
            return None
        raw.append((pos - 4, name_off, off, length, attrib, end))
        pos += 0x14
    else:
        return None
    if not raw:
        return None
    entries: List[PackageEntry] = []
    folders: List[tuple] = []
    for rel, name_off, off, length, attrib, end in raw:
        term = data.find(b"\x00", name_off)
        name_raw = data[name_off:term]
        if not name_raw or not all(32 < c < 127 for c in name_raw):
            return None
        while folders and rel + 4 >= folders[-1][0]:
            folders.pop()
        name = name_raw.decode("ascii")
        prefix = "/".join(f[1] for f in folders)
        entries.append(PackageEntry(name, data_start + off, length, attrib,
                                    f"{prefix}/{name}" if prefix else name))
        if attrib & FOLDER:
            folders.append((end, name))
    return entries


def _parse_package_rs2(data: bytes) -> Optional[List[PackageEntry]]:
    """RS2: the directory is the tail of the package, 0x30-byte entries.

    Its final all-zero entry stores the directory size in its last word; a folder
    entry's last word is the directory offset where its children end.
    """
    if len(data) < 0x60 or any(data[-0x30:-4]):
        return None
    dir_size = struct.unpack_from(">I", data, len(data) - 4)[0]
    if dir_size < 0x60 or dir_size % 0x30 or dir_size > len(data):
        return None
    dir_start = len(data) - dir_size
    entries: List[PackageEntry] = []
    folders: List[tuple] = []  # (end offset in directory, name)
    for rel in range(0, dir_size - 0x30, 0x30):
        pos = dir_start + rel
        name_raw = data[pos:pos + 0x20].split(b"\x00")[0]
        off, length, attrib, end = struct.unpack_from(">IIII", data, pos + 0x20)
        if not name_raw or not all(32 < c < 127 for c in name_raw):
            return None
        if not attrib & FOLDER and off + length > dir_start:
            return None
        while folders and rel >= folders[-1][0]:
            folders.pop()
        name = name_raw.decode("ascii")
        prefix = "/".join(f[1] for f in folders)
        entries.append(PackageEntry(name, off, length, attrib, f"{prefix}/{name}" if prefix else name))
        if attrib & FOLDER:
            folders.append((end, name))
    return entries


def package_files(data: bytes) -> Dict[str, bytes]:
    """Map leaf name -> bytes for a package; empty dict if not a package."""
    entries = parse_package(data)
    if not entries:
        return {}
    return {e.name: data[e.offset:e.offset + e.length] for e in entries if not e.attrib & FOLDER}
