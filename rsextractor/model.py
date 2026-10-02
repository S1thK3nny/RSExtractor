"""Factor 5 model geometry (``_ge``) for Rogue Squadron II and III.

``_ge`` is a relocatable memory image: pointers are offsets from the start of
the part and 0 means NULL.  Layout, as walked by the renderer::

    root +0x04  -> format list (opaque)      root +0x08 -> format list (translucent)
    format node: { next, material_list, u8 vat_count @+0x0B, u32 vat[vat_count] @+0x0C }
    material node: { next, u16 material_index @+0x04, mesh_list @+0x08 }
    mesh-list node: { next, mesh, u16 node_index @+0x08 }

RS2 mesh (FUN_7fd28ab0 / FUN_7fd288d4 / FUN_7fd289d0)::

    +0x00 u16 matrix_count   +0x04 u16 flags   +0x06 u8 relocated
    +0x0C matrix_list  +0x10 positions  +0x14 normals  +0x18 colors  +0x1C uvs
    +0x20 info  +0x24 display_list  +0x28 display_list_size

RS3 mesh (FUN_7f397af4 / FUN_7f3991e8 / FUN_7f399b08): same fields shifted to
+0x14 .. +0x30, ``+0x09`` = extra texture coordinate sets, flag 0x0800 enables
normals and a NULL normal array means "index the global quantized normal
table" stored in the boot DOL.  Array pointers with the high bit set refer to
an external vertex buffer (not used by packaged models).

Level of detail (RS2 FUN_7fd25e08): ``mesh+2`` indexes the ``ge+0x1C`` table
(0x14 byte entries, 0xFFFF = always drawn) whose ``+0x10`` points to the switch
distances. The mesh is drawn between ``dist[near-1]`` and ``dist[far-1]`` with
``near = mesh+7`` (0 = from the camera) and ``far = mesh+8`` (0xFF = no limit),
so chains such as (0,1) (1,2) (2,0xFF) are LOD0, LOD1 and LOD2.

Mesh flags (GXSetVtxDesc): 0x2000 per-vertex matrix index, 0x1000 NBT instead of
NRM, 0x4000 color0, 0x8000 tex0; bits 0/1/2/3 select 16-bit indices for
pos/normal/color/uv.  The VAT word of the format node (picked by the display
list opcode's low 3 bits) gives component types and fixed-point shifts.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

# GX primitive opcodes (high 5 bits)
GX_QUADS, GX_TRIANGLES, GX_TRIANGLESTRIP, GX_TRIANGLEFAN = 0x80, 0x90, 0x98, 0xA0
GX_LINES, GX_LINESTRIP, GX_POINTS = 0xA8, 0xB0, 0xB8
_PRIMITIVES = (GX_QUADS, GX_TRIANGLES, GX_TRIANGLESTRIP, GX_TRIANGLEFAN, GX_LINES, GX_LINESTRIP, GX_POINTS)

MF_POS16, MF_NRM16, MF_CLR16, MF_TEX16 = 0x1, 0x2, 0x4, 0x8
MF_NRM_RS3, MF_NBT, MF_PNMTX, MF_CLR, MF_TEX = 0x0800, 0x1000, 0x2000, 0x4000, 0x8000


def u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


@dataclass
class VertexFormat:
    """Decoded VAT word (RS2 FUN_7fd28640, RS3 FUN_7f398ef0)."""
    pos_type: int    # 0 s8, 1 s16, 2 f32
    pos_shift: int
    nrm_type: int
    nbt_mode: int    # 0 NRM, 1 NBT (one index, 3 vectors), 2 NBT3 (three indices)
    uv_type: int
    uv_shift: int

    @classmethod
    def from_word(cls, w: int, game: int = 2) -> "VertexFormat":
        nbt = (w >> 21) & 3 if game == 2 else (2 if w & 0x600000 else 0)
        return cls(w & 3, (w >> 6) & 0x1F, (w >> 2) & 3, nbt, (w >> 4) & 3, (w >> 16) & 0x1F)

    @staticmethod
    def _size(t: int) -> int:
        return {0: 1, 1: 2, 2: 4}.get(t, 4)

    @property
    def pos_stride(self) -> int:
        return 3 * self._size(self.pos_type)

    @property
    def nrm_stride(self) -> int:
        return 3 * self._size(self.nrm_type) * (3 if self.nbt_mode == 1 else 1)

    @property
    def uv_stride(self) -> int:
        return 2 * self._size(self.uv_type)


def _read_vec(b: bytes, off: int, n: int, t: int, shift: int) -> Tuple[float, ...]:
    if t == 2:
        return struct.unpack_from(">%df" % n, b, off)
    vals = struct.unpack_from((">%dh" if t == 1 else ">%db") % n, b, off)
    scale = 1.0 / (1 << shift)
    return tuple(v * scale for v in vals)


@dataclass
class Mesh:
    node_index: int                    # node used when not skinned
    material_index: int
    translucent: bool
    positions: np.ndarray              # (N, 3)
    normals: Optional[np.ndarray]      # (N, 3)
    colors: Optional[np.ndarray]       # (N, 4) uint8
    uvs: Optional[np.ndarray]          # (N, 2) first texture coordinate set
    triangles: np.ndarray              # (T, 3) indices into the vertex arrays
    matrix_nodes: List[int] = field(default_factory=list)      # skinning palette -> node index
    vertex_matrix: Optional[np.ndarray] = None                  # (N,) palette slot per vertex
    extra_uvs: List[np.ndarray] = field(default_factory=list)   # further UV sets (RS3)
    pos_index: Optional[np.ndarray] = None   # (N,) index into the mesh position array per vertex
    nrm_index: Optional[np.ndarray] = None   # (N,) index into the normal array per vertex
    # RS3 CPU skinning: (bone, position count, normal count) runs over the position/normal arrays
    skin_runs: List[Tuple[int, int, int]] = field(default_factory=list)
    lod_entry: Optional[int] = None   # mesh+2: ge+0x1C LOD table entry (None = always drawn)
    lod_near: int = 0                 # mesh+7: first LOD distance band
    lod_far: int = 0xFF               # mesh+8: end of the band (0xFF = no limit)


@dataclass
class Geometry:
    meshes: List[Mesh]


def _iter_list(b: bytes, head: int):
    seen = set()
    while head and head not in seen and head + 8 <= len(b):
        seen.add(head)
        yield head
        head = u32(b, head)


def parse_geometry(ge: bytes, game: int = 2, normal_table: Optional[bytes] = None) -> Geometry:
    """Decode a ``_ge`` part. ``game`` is 2 (Rogue Leader) or 3 (Rebel Strike).

    ``normal_table`` is RS3's global s8 normal table, used by meshes that have no
    normal array of their own.
    """
    meshes: List[Mesh] = []
    for translucent, head_off in ((False, 4), (True, 8)):
        for fmt_node in _iter_list(ge, u32(ge, head_off)):
            vat_count = ge[fmt_node + 0x0B]
            vats = [VertexFormat.from_word(u32(ge, fmt_node + 0x0C + 4 * i), game) for i in range(vat_count)]
            if not vats:
                continue
            for mat_node in _iter_list(ge, u32(ge, fmt_node + 4)):
                material_index = u16(ge, mat_node + 4)
                for mesh_node in _iter_list(ge, u32(ge, mat_node + 8)):
                    mesh = _parse_mesh(ge, u32(ge, mesh_node + 4), vats, u16(ge, mesh_node + 8),
                                       material_index, translucent, game, normal_table)
                    if mesh is not None:
                        meshes.append(mesh)
    return Geometry(meshes)


def _parse_mesh(ge: bytes, off: int, vats: List[VertexFormat], node_index: int,
                material_index: int, translucent: bool, game: int,
                normal_table: Optional[bytes]) -> Optional[Mesh]:
    matrix_count = u16(ge, off)
    lod_entry = u16(ge, off + 2)
    flags = u16(ge, off + 4)
    if game == 2:
        mtx_list, pos_arr, nrm_arr, clr_arr, uv_arr, _info, dl, dl_size = struct.unpack_from(">8I", ge, off + 0x0C)
        has_normals = True
        uv_sets = 1
    else:
        mtx_list, pos_arr, nrm_arr, clr_arr, uv_arr, _info, dl, dl_size = struct.unpack_from(">8I", ge, off + 0x14)
        if any(a & 0x80000000 for a in (pos_arr, nrm_arr, clr_arr, uv_arr)):
            return None
        has_normals = bool(flags & (MF_NRM_RS3 | MF_NBT))
        uv_sets = ge[off + 9] + 1
    if not pos_arr or not dl:
        return None
    skin_runs: List[Tuple[int, int, int]] = []
    if game == 3:
        # RS3 (0x8003BD44): matrix_count runs of {u16 bone, u16 n_pos, u16 n_nrm}
        if mtx_list:
            skin_runs = [struct.unpack_from(">3H", ge, mtx_list + 6 * i) for i in range(matrix_count)]
        matrix_nodes = [r[0] for r in skin_runs]
    else:
        matrix_nodes = [u16(ge, mtx_list + 2 * i) for i in range(matrix_count)] if mtx_list else []

    pos_idx: List[int] = []
    nrm_idx: List[int] = []
    clr_idx: List[int] = []
    uv_idx: List[List[int]] = [[] for _ in range(uv_sets)]
    mtx_idx: List[int] = []
    vat_of_vertex: List[int] = []
    tris: List[Tuple[int, int, int]] = []

    def index(p: int, wide: bool) -> Tuple[int, int]:
        return (u16(ge, p), p + 2) if wide else (ge[p], p + 1)

    p = dl
    end = min(dl + dl_size, len(ge))
    while p + 3 <= end:
        op = ge[p]
        if op == 0:
            p += 1
            continue
        prim, vat = op & 0xF8, op & 7
        if prim not in _PRIMITIVES:
            break
        count = u16(ge, p + 1)
        p += 3
        fmt = vats[vat] if vat < len(vats) else vats[0]
        n_normals = (3 if (flags & MF_NBT and fmt.nbt_mode == 2) else 1) if has_normals else 0
        first = len(pos_idx)
        for _ in range(count):
            if flags & MF_PNMTX:
                mtx_idx.append(ge[p])
                p += 1
            v, p = index(p, bool(flags & MF_POS16))
            pos_idx.append(v)
            for k in range(n_normals):
                v, p = index(p, bool(flags & MF_NRM16))
                if k == 0:
                    nrm_idx.append(v)
            if flags & MF_CLR:
                v, p = index(p, bool(flags & MF_CLR16))
                clr_idx.append(v)
            if flags & MF_TEX:
                for uv_list in uv_idx:
                    v, p = index(p, bool(flags & MF_TEX16))
                    uv_list.append(v)
            vat_of_vertex.append(vat)
        tris.extend(_triangulate(prim, first, count))

    if not pos_idx:
        return None

    def fmt_of(vi: int) -> VertexFormat:
        return vats[vi] if vi < len(vats) else vats[0]

    def gather(arr_off: int, idx: List[int], stride, reader) -> np.ndarray:
        return np.array([reader(arr_off + i * stride(fmt_of(vi)), fmt_of(vi))
                         for i, vi in zip(idx, vat_of_vertex)], dtype=np.float32)

    positions = gather(pos_arr, pos_idx, lambda f: f.pos_stride,
                       lambda o, f: _read_vec(ge, o, 3, f.pos_type, f.pos_shift))

    normals = None
    if has_normals and nrm_idx:
        if nrm_arr:
            nshift = {0: 6, 1: 14, 2: 0}  # GX uses fixed shifts for normals
            normals = gather(nrm_arr, nrm_idx, lambda f: f.nrm_stride,
                             lambda o, f: _read_vec(ge, o, 3, f.nrm_type, nshift.get(f.nrm_type, 0)))
        elif normal_table is not None:
            table = np.frombuffer(normal_table, dtype=np.int8)
            table = table[:len(table) // 3 * 3].reshape(-1, 3)
            normals = table[np.clip(np.array(nrm_idx), 0, len(table) - 1)].astype(np.float32) / 64.0

    colors = None
    if clr_arr and clr_idx:
        colors = np.array([tuple(ge[clr_arr + 4 * i:clr_arr + 4 * i + 4]) for i in clr_idx], dtype=np.uint8)

    uvs = None
    extra_uvs: List[np.ndarray] = []
    if uv_arr and uv_idx[0]:
        sets = [gather(uv_arr, lst, lambda f: f.uv_stride,
                       lambda o, f: _read_vec(ge, o, 2, f.uv_type, f.uv_shift)) for lst in uv_idx]
        uvs, extra_uvs = sets[0], sets[1:]

    return Mesh(
        node_index=node_index,
        material_index=material_index,
        translucent=translucent,
        positions=positions,
        normals=normals,
        colors=colors,
        uvs=uvs,
        triangles=np.array(tris, dtype=np.int32).reshape(-1, 3),
        matrix_nodes=matrix_nodes,
        vertex_matrix=np.array(mtx_idx, dtype=np.int32) // 3 if mtx_idx else None,
        extra_uvs=extra_uvs,
        pos_index=np.array(pos_idx, dtype=np.int32),
        nrm_index=np.array(nrm_idx, dtype=np.int32) if nrm_idx else None,
        skin_runs=skin_runs,
        lod_entry=None if lod_entry == 0xFFFF else lod_entry,
        lod_near=ge[off + 7],
        lod_far=ge[off + 8],
    )


def _triangulate(prim: int, first: int, count: int) -> List[Tuple[int, int, int]]:
    out = []
    if prim == GX_TRIANGLES:
        for i in range(0, count - 2, 3):
            out.append((first + i, first + i + 1, first + i + 2))
    elif prim == GX_TRIANGLESTRIP:
        for i in range(count - 2):
            a, b, c = first + i, first + i + 1, first + i + 2
            out.append((a, b, c) if i % 2 == 0 else (b, a, c))
    elif prim == GX_TRIANGLEFAN:
        for i in range(1, count - 1):
            out.append((first, first + i, first + i + 1))
    elif prim == GX_QUADS:
        for i in range(0, count - 3, 4):
            q = first + i
            out.append((q, q + 1, q + 2))
            out.append((q, q + 2, q + 3))
    return out
