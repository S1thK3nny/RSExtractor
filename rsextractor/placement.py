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
splines, effects) are skipped here. A height of -FLT_MAX puts the object on the
ground below it (``Placed.on_ground``).

Indoor levels (RS3) place their rooms in ``portals`` instead (``parse_portals``).

Capital ships, towers and platforms do not place their parts by pose: their
spawn code (``MOUNTS``) puts each part on a node (RS2) or a named locator (RS3)
of the host model; the part record only lends its model and scale. RS2 asteroid
rocks (class 140) are the models the asteroid fields pick from at random and
have no place of their own.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import numpy as np


@dataclass
class Placed:
    cls: int
    names: Tuple[str, ...]  # model name candidates, flight variant first
    matrix: np.ndarray      # 4x4, model space -> level; for a mounted part only its scale
    # (host's index in the list, host ``_su`` node (RS2) or locator name (RS3))
    mount: Optional[Tuple[int, Union[int, str]]] = None

    @property
    def on_ground(self) -> bool:
        """Height -FLT_MAX: stands on whatever is below it (the Academy radar dishes of RS2 ``l07``)."""
        return bool(self.matrix[1, 3] < -1e30)


def _nodes(*game_nodes: int) -> List[int]:
    """RS2 game node k is ``_su`` node k - 1 (k = 0 is the model root)."""
    return [k - 1 for k in game_nodes]


def _locators(names: List[str], *slots: int) -> List[str]:
    return [names[k] for k in slots]


_SD3 = ["loc_ball_l", "loc_ball_r", "loc_bball", "loc_cdeck"] + [f"loc_iongun_000{k}" for k in (5, 8, 6, 7, 2, 3, 1, 4)]     + [f"loc_sgun_{k:04d}" if k != 10 else "loc_sgun_00010" for k in range(1, 43)]
_CRASHED3 = [f"loc_ai_iongun{side}0{k}" for side in ("right", "left") for k in range(1, 5)]     + [f"loc_ai_smallgun_00{k}" for k in (22, 23, 25, 28, 29, 30, 31, 32, 33, 34, 35, 40, 41, 42)]
_BALLOON3 = [f"loc_turbolaser00{k}" for k in range(1, 7)] + ["loc_balloonengines"]
_PLATFORM3 = [f"loc_gascontainersmall00{k}" for k in range(1, 9)] + [f"loc_gascontainer{k:03d}" for k in range(1, 33)]
_GUNSHIP3 = ["loc_turretgun_left", "loc_turretgun_right", "loc_ballturret_left_01", "loc_ballturret_left_02",
             "loc_ballturret_right_01", "loc_ballturret_right_02", "loc_missile_left", "loc_missile_right"]
_STATION3 = [f"loc_ai_smallgun_{k:04d}" if k < 10 else f"loc_ai_smallgun_000{k}" for k in range(1, 20)]

# Hosts by game and class: (part, frames). A part is a record handle at a record
# offset (int) or, as ("first", class), the first record of that class in the level.
# RS3 hosts look their slots up by name in a list (object +0x124) among the model's
# locators.
MOUNTS: Dict[int, Dict[int, list]] = {
    2: {
        # Star Destroyer, FUN_7fd96a14 (tables 0x7FDD4194, 0x7FDD4174, 0x7FDD40D0)
        40: [(0xE0, _nodes(0x10)), (0xE4, _nodes(0x11)), (0xE8, _nodes(0x12)), (0xEC, _nodes(0x13)),
             (("first", 44), _nodes(0x08, 0x0F, 0x0D, 0x0E, 0x0C, 0x09, 0x0B, 0x0A)),
             (0xF0, _nodes(*range(0x14, 0x3D)))],
        # crashed Star Destroyer, FUN_7fd971dc (tables 0x7FDD4250, 0x7FDD4204)
        41: [(("first", 45), _nodes(5, 6, 7, 8, 4, 1, 3, 2)),
             (0xA8, _nodes(0x09, 0x0A, 0x0B, 0x17, 0x0C, 0x0D, 0x16, 0x1B, 0x19, 0x18, 0x1A, 0x15, 0x14, 0x13, 0x12))],
        # Bespin balloon, FUN_7fd97d00 and FUN_7fd0e47c (table 0x7FDD4B68: 3 guns, 3 more up close)
        58: [(0x84, _nodes(1)), (0x88, _nodes(2, 4, 6, 3, 5, 7))],
        # Bespin gas platform, FUN_7fd98064 (tables 0x8005D754, 0x8005D774)
        60: [(0x50, _nodes(*range(0x33, 0x3B))), (0x54, _nodes(*range(0x13, 0x33)))],
    },
    3: {
        # Star Destroyer, FUN_7f2cb768 (names 0x7F7073F0, tables 0x7F704FB4, 0x7F704FC4, 0x7F704FE4)
        52: [(0xF4, _locators(_SD3, 0)), (0xF8, _locators(_SD3, 1)), (0xFC, _locators(_SD3, 2)),
             (0x100, _locators(_SD3, 3)), (("first", 0x38), _SD3[4:12]), (0x104, _SD3[12:54])],
        # crashed Star Destroyer, FUN_7f2cc20c (names 0x7F7053F8, tables 0x7F7054C8, 0x7F7054E8)
        53: [(("first", 0x39), _CRASHED3[:8]), (0xB4, _CRASHED3[8:22])],
        # Bespin balloon, FUN_7f263594 and FUN_7f2636a8 (names 0x7F66985C, table 0x7F6AB8A8:
        # 3 guns, 3 more up close)
        70: [(0x8C, _locators(_BALLOON3, 6)), (0x90, _BALLOON3[:6])],
        # gas platform, FUN_7f263ad8 (names 0x7F6698A8, tables 0x7F6ABCF0, 0x7F6ABD10; with
        # +0x58 set the game keeps 7 of the 32 big containers at random)
        72: [(0x50, _PLATFORM3[:8]), (0x54, _PLATFORM3[8:40])],
        # Republic gunship, FUN_7f2bc43c (names 0x7F6F8200, tables 0x7F6F8248 .. 0x7F6F825C)
        263: [(0x234, _locators(_GUNSHIP3, 2, 3)), (0x238, _locators(_GUNSHIP3, 4, 5)),
              (0x23C, _locators(_GUNSHIP3, 0)), (0x240, _locators(_GUNSHIP3, 1))],
        # Fondor station, FUN_7f2ed164 (names 0x7F72D324, table 0x7F72D3FC)
        328: [(0x50, _STATION3)],
    },
}
ASTEROID_ROCK = 140


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
    records = {}  # record offset -> (class, names, pose values)
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
        records[rec] = (cls, names, struct.unpack_from(">9f", dat, rec + 0x10), end)
    mounts = _mounts(dat, game, records)
    parts = {part for _host, part, _nodes in mounts}
    out: List[Placed] = []
    position = {}
    for rec, (cls, names, values, _end) in records.items():
        if not names or rec in parts or (game == 2 and cls == ASTEROID_ROCK):
            continue
        position[rec] = len(out)
        out.append(Placed(cls, names, euler_matrix(values[3:6], values[0:3], values[6:9])))
    for host, part, frames in mounts:
        cls, names, values, _end = records[part]
        if host in position and names:
            for frame in frames:
                out.append(Placed(cls, names, np.diag([*values[6:9], 1.0]), (position[host], frame)))
    return out


# Placed.cls of the rooms of indoor levels (``portals``), which are no ``dat`` records.
ROOM = -1
ROOM_MIRRORED = 8  # room flag: a copy flipped under its floor for reflective floors


def parse_portals(data: bytes) -> List[Placed]:
    """Rooms of an indoor level (RS3 ``portals``, ``FUN_7f3a74b0``): hangars, bunkers,
    corridors. The part starts with ``u16 zones, models, portals, rooms`` and offsets
    (relative to the part) at +0x14 (portal outline points), +0x18 (zones, 0x38
    bytes), +0x1C (models, 0x54 bytes: name[0x40]), +0x20 (portals, 0x2C bytes),
    +0x24 (rooms, 0xA0 bytes). A room is ``{u16 index, u16 model, u16 first portal,
    u16 portals, u16 flags, u16, f32 model -> level[3][4], f32 level -> model[3][4],
    ...}`` (``FUN_7f3a5ad4`` boxes it). Rooms with flag 8 are mirror images of another
    room, flipped under its floor for reflections (``FUN_7f3a74b0``), and left out."""
    if len(data) < 0x28:
        return []
    _zones, models, _portals, rooms = struct.unpack_from(">4H", data, 0)
    model_table, = struct.unpack_from(">I", data, 0x1C)
    room_table, = struct.unpack_from(">I", data, 0x24)
    if model_table + 0x54 * models > len(data) or room_table + 0xA0 * rooms > len(data):
        return []
    names = [_name(data, model_table + 0x54 * i, model_table + 0x54 * i + 0x40) or ""
             for i in range(models)]
    out = []
    for i in range(rooms):
        room = room_table + 0xA0 * i
        model, = struct.unpack_from(">H", data, room + 2)
        flags, = struct.unpack_from(">H", data, room + 8)
        if flags & ROOM_MIRRORED or model >= models:
            continue
        m = np.eye(4)
        m[:3] = np.array(struct.unpack_from(">12f", data, room + 0xC)).reshape(3, 4)
        out.append(Placed(ROOM, (names[model],), m))
    return out


def _mounts(dat: bytes, game: int, records) -> List[Tuple[int, int, list]]:
    """(host record, part record, frames) of every host in ``MOUNTS``. Handles resolve
    through the index of all records (count, offset: RS2 ``+0x50``, RS3 ``+0x6C``);
    only records of the objects list (``+0x02`` byte 0) count, RS3 handles with bit 15
    are records made at run time."""
    hosts = MOUNTS.get(game, {})
    header = 0x50 if game == 2 else 0x6C
    if not any(cls in hosts for cls, *_ in records.values()) or header + 8 > len(dat):
        return []
    count, table = struct.unpack_from(">II", dat, header)
    if table + 4 * count > len(dat):
        return []
    index = [struct.unpack_from(">I", dat, table + 4 * i)[0] for i in range(count)]
    index = [rec if rec in records and dat[rec + 2] == 0 else None for rec in index]

    def handle(value: int) -> Optional[int]:
        k = value & 0xFFFF
        if not 0 < k <= len(index) or value >> 16 & 0xFF:
            return None
        return index[k - 1]

    out = []
    for host, (cls, _names, _values, end) in records.items():
        for source, frames in hosts.get(cls, ()):
            if isinstance(source, tuple):
                part = next((rec for rec in index if rec is not None and records[rec][0] == source[1]), None)
            elif host + source + 4 <= end:
                part = handle(struct.unpack_from(">I", dat, host + source)[0])
            else:
                part = None
            if part is not None and part != host:
                out.append((host, part, frames))
    return out
