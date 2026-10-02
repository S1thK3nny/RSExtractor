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
    22 translate, 25 rotation order (RS2 u32, RS3 byte at +0x65)
    v' = R * (S * (v - sp) + sp + spt - rp) + rp + rpt + t

Joint (0x54 bytes)::

    0 scale, 3 rotate axis quaternion, 7 euler rotation, 10 joint orient quaternion,
    14 parent scale (inverted), 17 translate, 20 rotation order
    M = T * inverse(parent scale) * (orient * euler * axis) * S
"""
from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Sequence

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


def evaluate_nodes(su: bytes, game: int) -> List[np.ndarray]:
    """Bind-pose world matrices of all nodes, in command stream order."""
    if len(su) < 0x40:
        return []
    h = struct.unpack_from(">15I", su, 0)
    stride = 0x68 if game == 2 else 0x6C
    n_xf, p_xf, n_jt, p_jt = h[11], h[12], h[13], h[14]
    if p_xf + n_xf * stride > len(su) or p_jt + n_jt * 0x54 > len(su) or h[0] >= len(su):
        return []
    worlds: List[np.ndarray] = []
    parent: Optional[np.ndarray] = None
    p = h[0]
    while p + 2 <= len(su):
        cmd = struct.unpack_from(">H", su, p)[0]
        p += 2
        if cmd == 0xFFFF:
            break
        if cmd & 0x8000:
            k = cmd & 0x3FFF
            parent = worlds[k - 1] if 0 < k <= len(worlds) else None
            continue
        i = cmd & 0x3FFF
        if cmd & 0x4000:
            if i >= n_jt:
                local = np.eye(4)
            else:
                r = struct.unpack_from(">20f", su, p_jt + i * 0x54)
                order = struct.unpack_from(">I", su, p_jt + i * 0x54 + 0x50)[0]
                q = qmul(r[10:14], qmul(euler_quat(r[7:10], order if order < 6 else 0), r[3:7]))
                inv_parent_scale = [1.0 / v if v else 1.0 for v in r[14:17]]
                local = _translate(r[17:20]) @ _scale(inv_parent_scale) @ quat_matrix(q) @ _scale(r[0:3])
        else:
            if i >= n_xf:
                local = np.eye(4)
            else:
                base = p_xf + i * stride
                a = struct.unpack_from(">25f", su, base)
                order = struct.unpack_from(">I", su, base + 100)[0] if game == 2 else su[base + 101]
                q = qmul(euler_quat(a[16:19], order if order < 6 else 0), a[12:16])
                sp, st, rp = np.array(a[0:3]), np.array(a[6:9]), np.array(a[9:12])
                rt, t = np.array(a[19:22]), np.array(a[22:25])
                local = (_translate(t + rp + rt) @ quat_matrix(q) @ _translate(sp + st - rp)
                         @ _scale(a[3:6]) @ _translate(-sp))
        worlds.append(local if parent is None else parent @ local)
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
