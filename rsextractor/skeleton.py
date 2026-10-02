"""Node hierarchy (``_su``) evaluation and the per-model render matrix palette.

Setup header (u32 words): ``[0]`` node command stream, ``[11]``/``[12]`` transform
node count/offset, ``[13]``/``[14]`` joint count/offset.

The command stream (RS2 FUN_7fd24ef4) is a u16 list ending in 0xFFFF:

* ``0x8000 | k``  - parent for following nodes = output matrix k-1 (0 = none)
* ``0x4000 | i``  - evaluate joint i
* ``i``           - evaluate transform node i

Every evaluated node appends one world matrix; meshes refer to them through the
geometry block chain (``ge+0x28``, count ``ge+0x26``): block ``{next, u16 node
(1-based, 0 = model root), u16 inverse bind index (0xFFFF = none), u16 index}``
gives ``palette[index] = world[node-1] * inverse_bind``. A mesh-list node's
u16 at +8 selects its palette entry, so with an inverse bind the vertices are in
model space and without one they are local to the node.

Transform node (Maya transform, 0x68 bytes RS2 / 0x6C RS3)::

    0 scale pivot, 3 scale, 6 scale pivot translate, 9 rotate pivot,
    12 rotate axis quaternion, 16 euler rotation, 19 rotate pivot translate,
    22 translate, then the rotation order (RS2 u32 at +0x64, RS3 u32 at +0x68)
    v' = R * (S * (v - sp) + sp + spt - rp) + rp + rpt + t

Joint (0x54 bytes)::

    0 scale, 3 rotate axis quaternion, 7 euler rotation, 10 joint orient quaternion,
    14 parent scale (inverted), 17 translate, 20 rotation order
    M = T * inverse(parent scale) * (orient * euler * axis) * S
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Quat = Sequence[float]  # (x, y, z, w)


def qmul(a: Quat, b: Quat) -> tuple:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + bw * ax + (ay * bz - az * by),
            aw * by + bw * ay + (az * bx - ax * bz),
            aw * bz + bw * az + (ax * by - ay * bx),
            aw * bw - (ax * bx + ay * by + az * bz))


def quat_matrix(q: Quat) -> np.ndarray:
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    s = 2.0 / n if n else 0.0
    m = np.eye(4)
    m[:3, :3] = [[1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
                 [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
                 [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)]]
    return m


def euler_quat(r: Sequence[float], order: int) -> tuple:
    """Rotation order codes as in RS2 FUN_7fd249c4 (0 = X first, then Y, then Z)."""
    hx, hy, hz = (0.5 * v for v in r)
    qx = (math.sin(hx), 0.0, 0.0, math.cos(hx))
    qy = (0.0, math.sin(hy), 0.0, math.cos(hy))
    qz = (0.0, 0.0, math.sin(hz), math.cos(hz))
    if order == 1:
        return qmul(qy, qmul(qz, qx))
    if order == 2:
        return qmul(qz, qmul(qx, qy))
    if order == 3:
        return qmul(qx, qmul(qz, qy))
    if order == 4:
        return qmul(qx, qmul(qy, qz))
    if order == 5:
        return qmul(qy, qmul(qx, qz))
    return qmul(qz, qmul(qy, qx))


def _translate(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m


def _scale(v) -> np.ndarray:
    return np.diag([v[0], v[1], v[2], 1.0])


@dataclass
class NodeRef:
    """One evaluated node of the command stream."""
    joint: bool        # joint (True) or transform node
    index: int         # index into the joint or transform array
    parent: int        # output index of the parent node, -1 = model root


def node_hierarchy(su: bytes, game: int) -> List[NodeRef]:
    """Evaluation order and parents of the ``_su`` nodes; a node's position in
    the returned list is its output matrix index."""
    if len(su) < 0x40:
        return []
    h = struct.unpack_from(">15I", su, 0)
    stride = 0x68 if game == 2 else 0x6C
    n_xf, p_xf, n_jt, p_jt = h[11], h[12], h[13], h[14]
    if p_xf + n_xf * stride > len(su) or p_jt + n_jt * 0x54 > len(su) or h[0] >= len(su):
        return []
    nodes: List[NodeRef] = []
    parent = -1
    p = h[0]
    while p + 2 <= len(su):
        cmd = struct.unpack_from(">H", su, p)[0]
        p += 2
        if cmd == 0xFFFF:
            break
        if cmd & 0x8000:
            k = cmd & 0x3FFF
            parent = k - 1 if 0 < k <= len(nodes) else -1
            continue
        nodes.append(NodeRef(bool(cmd & 0x4000), cmd & 0x3FFF, parent))
    return nodes


def local_matrices(su: bytes, game: int, xf_values: Optional[bytes] = None,
                   jt_values: Optional[bytes] = None) -> List[np.ndarray]:
    """Local matrix of every node in evaluation order. ``xf_values`` and
    ``jt_values`` replace the bind-pose attributes with animated ones (the
    setup code's attribute arrays: 0x64 (RS2) / 0x68 (RS3) byte transforms,
    0x50 byte joints)."""
    h = struct.unpack_from(">15I", su, 0)
    stride = 0x68 if game == 2 else 0x6C
    vstride = 0x64 if game == 2 else 0x68
    n_xf, p_xf, n_jt, p_jt = h[11], h[12], h[13], h[14]
    out: List[np.ndarray] = []
    for node in node_hierarchy(su, game):
        i = node.index
        if node.joint:
            if i >= n_jt:
                out.append(np.eye(4))
                continue
            src, off = (jt_values, i * 0x50) if jt_values is not None else (su, p_jt + i * 0x54)
            r = struct.unpack_from(">20f", src, off)
            order = struct.unpack_from(">I", su, p_jt + i * 0x54 + 0x50)[0]
            q = qmul(r[10:14], qmul(euler_quat(r[7:10], order if order < 6 else 0), r[3:7]))
            inv_parent_scale = [1.0 / v if v else 1.0 for v in r[14:17]]
            out.append(_translate(r[17:20]) @ _scale(inv_parent_scale) @ quat_matrix(q) @ _scale(r[0:3]))
        else:
            if i >= n_xf:
                out.append(np.eye(4))
                continue
            base = p_xf + i * stride
            src, off = (xf_values, i * vstride) if xf_values is not None else (su, base)
            a = struct.unpack_from(">25f", src, off)
            order = struct.unpack_from(">I", su, base + (0x64 if game == 2 else 0x68))[0]
            q = qmul(euler_quat(a[16:19], order if order < 6 else 0), a[12:16])
            sp, st, rp = np.array(a[0:3]), np.array(a[6:9]), np.array(a[9:12])
            rt, t = np.array(a[19:22]), np.array(a[22:25])
            out.append(_translate(t + rp + rt) @ quat_matrix(q) @ _translate(sp + st - rp)
                       @ _scale(a[3:6]) @ _translate(-sp))
    return out


def evaluate_nodes(su: bytes, game: int, xf_values: Optional[bytes] = None,
                   jt_values: Optional[bytes] = None) -> List[np.ndarray]:
    """World matrices of all nodes in evaluation order (bind pose unless
    animated attribute arrays are given)."""
    nodes = node_hierarchy(su, game)
    worlds: List[np.ndarray] = []
    for node, local in zip(nodes, local_matrices(su, game, xf_values, jt_values)):
        worlds.append(local if node.parent < 0 else worlds[node.parent] @ local)
    return worlds


def render_palette(ge: bytes, worlds: List[np.ndarray]) -> Dict[int, np.ndarray]:
    """Bind-pose matrix for each geometry block index (see module docstring)."""
    def u32(o: int) -> int:
        return struct.unpack_from(">I", ge, o)[0]

    inv_bind = u32(0)
    count = struct.unpack_from(">H", ge, 0x26)[0]
    palette: Dict[int, np.ndarray] = {}
    p = 0x28
    for _ in range(count):
        if not p or p + 10 > len(ge):
            break
        node, bind, index = struct.unpack_from(">HHH", ge, p + 4)
        m = worlds[node - 1] if 0 < node <= len(worlds) else np.eye(4)
        if bind != 0xFFFF and inv_bind and inv_bind + 48 * (bind + 1) <= len(ge):
            ib = np.eye(4)
            ib[:3, :] = np.array(struct.unpack_from(">12f", ge, inv_bind + 48 * bind)).reshape(3, 4)
            m = m @ ib
        palette[index] = m
        p = u32(p)
    return palette


def name_tables(nm: bytes) -> Dict[str, List[Tuple[int, int, str]]]:
    """RS3 ``_nm`` part: ``u32 count``, ``count`` offsets of section headers
    ``{u32 data offset, u32 entries, name}``; entries are ``{u8, u16 key, u16
    value, name}``. ``matrixarray`` maps node output indices (value) to names."""
    tables: Dict[str, List[Tuple[int, int, str]]] = {}
    try:
        count = struct.unpack_from(">I", nm, 0)[0]
        for k in range(count):
            h = struct.unpack_from(">I", nm, 4 + 4 * k)[0]
            data, n = struct.unpack_from(">II", nm, h)
            end = nm.index(b"\0", h + 8)
            section = nm[h + 8:end].decode("latin-1")
            entries = []
            p = data
            for _ in range(n):
                key, value = struct.unpack_from(">HH", nm, p + 1)
                e = nm.index(b"\0", p + 5)
                entries.append((key, value, nm[p + 5:e].decode("latin-1")))
                p = e + 1
            tables[section] = entries
    except (struct.error, ValueError):
        pass
    return tables


Influence = Tuple[int, Optional[int], float]  # (node output index or -1 for the root, inverse bind, weight)


def inverse_bind(ge: bytes, index: int) -> Optional[np.ndarray]:
    """Inverse bind matrix ``index`` of the ``ge+0x00`` table."""
    base = struct.unpack_from(">I", ge, 0)[0]
    if not base or base + 48 * (index + 1) > len(ge):
        return None
    m = np.eye(4)
    m[:3, :] = np.array(struct.unpack_from(">12f", ge, base + 48 * index)).reshape(3, 4)
    return m


def skin_palette(ge: bytes, game: int) -> Tuple[Dict[int, List[Influence]], Dict[int, List[Influence]]]:
    """Bone influences of every render palette entry, and of every CPU skinning
    matrix (RS3 skin runs).

    A block without weight lists binds its palette entry rigidly to its node.
    RS2 blocks with ``u16 n @+0x0A`` hold n entries ``{u16, u16 count, u32
    records}`` for palette entries index .. index+n-1; RS3 blocks with ``u16 a
    @+0x0C, u16 b @+0x0E`` hold a + b pairs ``{u32 count, u32 records}`` at
    +0x10: a palette entries, then b CPU skinning matrices. Records are
    ``{u16 joint (1-based), u16 inverse bind, f32 weight}``.
    """
    def u32(o: int) -> int:
        return struct.unpack_from(">I", ge, o)[0]

    def records(ptr: int, n: int) -> List[Influence]:
        out = []
        for j in range(n):
            if ptr + 8 * j + 8 > len(ge):
                break
            joint, bind, w = struct.unpack_from(">HHf", ge, ptr + 8 * j)
            out.append((joint - 1, None if bind == 0xFFFF else bind, w))
        return out

    count = struct.unpack_from(">H", ge, 0x26)[0]
    palette: Dict[int, List[Influence]] = {}
    cpu: Dict[int, List[Influence]] = {}
    p = 0x28
    for _ in range(count):
        if not p or p + 0x10 > len(ge):
            break
        node, bind, index = struct.unpack_from(">HHH", ge, p + 4)
        if game == 2:
            entries = [struct.unpack_from(">HHI", ge, p + 12 + 8 * k)[1:]
                       for k in range(struct.unpack_from(">H", ge, p + 10)[0])]
            cpu_entries = []
        else:
            a, b = struct.unpack_from(">HH", ge, p + 12)
            pairs = [struct.unpack_from(">II", ge, p + 0x10 + 8 * k) for k in range(a + b)]
            entries, cpu_entries = pairs[:a], pairs[a:]
        for k, (n, ptr) in enumerate(entries):
            palette[index + k] = records(ptr, n)
        for k, (n, ptr) in enumerate(cpu_entries):
            cpu[k] = records(ptr, n)
        if not entries and not cpu_entries:
            palette[index] = [(node - 1, None if bind == 0xFFFF else bind, 1.0)]
        p = u32(p)
    return palette, cpu


def vertex_influences(mesh, palette: Dict[int, List[Influence]],
                      cpu: Dict[int, List[Influence]]) -> List[List[Influence]]:
    """Influences of every vertex of a parsed mesh: RS3 skin runs over the
    position array (CPU skinning matrices; a single run is drawn with that
    palette entry instead, RS3 FUN_7f399520), RS2 per-vertex matrix indices,
    or the mesh's own palette entry."""
    count = len(mesh.positions)
    if len(mesh.skin_runs) == 1:
        return [palette.get(mesh.skin_runs[0][0], [])] * count
    if mesh.skin_runs and mesh.pos_index is not None:
        run_of = []
        for entry, n_pos, _n_nrm in mesh.skin_runs:
            run_of += [entry] * n_pos
        return [cpu.get(run_of[i] if i < len(run_of) else -1, []) for i in mesh.pos_index.tolist()]
    if mesh.vertex_matrix is not None and mesh.matrix_nodes:
        slots = mesh.matrix_nodes
        return [palette.get(slots[s] if 0 <= s < len(slots) else -1, []) for s in mesh.vertex_matrix.tolist()]
    return [palette.get(mesh.node_index, [])] * count


def place_static_meshes(meshes, su: Optional[bytes], ge: bytes, game: int) -> int:
    """Move node-local meshes into model space (in place). Returns how many moved.

    Skinned meshes are left alone: their palette matrices are identity in the
    bind pose, so their stored positions already are model space.
    """
    if not su:
        return 0
    worlds = evaluate_nodes(su, game)
    if not worlds:
        return 0
    palette = render_palette(ge, worlds)
    moved = 0
    for mesh in meshes:
        if mesh.skin_runs or mesh.matrix_nodes or mesh.vertex_matrix is not None:
            continue
        m = palette.get(mesh.node_index)
        if m is None or np.allclose(m, np.eye(4), atol=1e-6):
            continue
        mesh.positions = (mesh.positions.astype(np.float64) @ m[:3, :3].T + m[:3, 3]).astype(np.float32)
        if mesh.normals is not None:
            nm = np.linalg.inv(m[:3, :3]).T
            n = mesh.normals.astype(np.float64) @ nm.T
            lens = np.linalg.norm(n, axis=1, keepdims=True)
            mesh.normals = (n / np.where(lens > 1e-9, lens, 1.0)).astype(np.float32)
        if np.linalg.det(m[:3, :3]) < 0:
            mesh.triangles = mesh.triangles[:, [0, 2, 1]]
        moved += 1
    return moved
