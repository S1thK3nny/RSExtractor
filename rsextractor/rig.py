"""Skeletons, skin weights and baked animation for the exporters.

Every node of the ``_su`` command stream becomes a bone under a root bone.
Mesh vertices are skinned through the render palette (``skeleton.skin_palette``):
an influence (node, inverse bind B, weight) moves a stored vertex by
``world(node) * B`` (B = identity for node-local parts). A bone's skin bind
matrix ("link") is the inverse of its inverse bind where it has one, else its
rest world matrix, and every vertex is stored as ``link * B * v`` of its
heaviest influence so FBX skinning (``world * inverse(link)``) reproduces it.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .anim import Clip, EmulationError, FPS, Runtime, SetupProgram
from .scene import Scene, SceneAnimation, SceneNode
from .skeleton import (Influence, NodeRef, evaluate_nodes, inverse_bind, local_matrices, name_tables,
                       node_hierarchy, skin_palette, vertex_influences)

MAX_INFLUENCES = 4


@dataclass
class Rig:
    su: bytes
    ge: bytes
    game: int
    nodes: List[NodeRef]
    names: List[str]
    rest_local: List[np.ndarray]
    rest_world: List[np.ndarray]
    palette: Dict[int, List[Influence]]
    cpu: Dict[int, List[Influence]]
    links: List[np.ndarray]

    @classmethod
    def build(cls, su: Optional[bytes], ge: bytes, game: int, nm: Optional[bytes] = None) -> Optional["Rig"]:
        if not su:
            return None
        nodes = node_hierarchy(su, game)
        if not nodes:
            return None
        rest_local = local_matrices(su, game)
        rest_world = evaluate_nodes(su, game)
        tables = name_tables(nm) if nm else {}
        by_index = {value: name for _key, value, name in tables.get("matrixarray", [])}
        xf_names = {value: name for _key, value, name in tables.get("transformarray", [])}
        jt_names = {value: name for _key, value, name in tables.get("jointarray", [])}
        names = []
        used = set()
        for i, n in enumerate(nodes):
            name = by_index.get(i) or (jt_names if n.joint else xf_names).get(n.index)
            if not name:
                name = f"{'joint' if n.joint else 'node'}{n.index:02d}"
            base, k = name, 2
            while name.lower() in used:
                name, k = f"{base}_{k}", k + 1
            used.add(name.lower())
            names.append(name)
        palette, cpu = skin_palette(ge, game)
        links: List[Optional[np.ndarray]] = [None] * len(nodes)
        for influences in list(palette.values()) + list(cpu.values()):
            for node, bind, _w in influences:
                if 0 <= node < len(nodes) and bind is not None and links[node] is None:
                    ib = inverse_bind(ge, bind)
                    if ib is not None and abs(np.linalg.det(ib)) > 1e-12:
                        links[node] = np.linalg.inv(ib)
        # Some skins were bound on a uniformly scaled skeleton (Luke: x13). Bind
        # matrices with scale do not survive DCC imports, so move the whole bind
        # space by G = scale(1/s): links G * inverse(IB), vertices G * v.
        scales = [abs(np.linalg.det(lk[:3, :3])) ** (1.0 / 3.0) for lk in links if lk is not None]
        if scales:
            s = float(np.median(scales))
            if abs(s - 1.0) > 1e-3 and all(abs(x / s - 1.0) < 1e-2 for x in scales):
                g = np.diag([1.0 / s, 1.0 / s, 1.0 / s, 1.0])
                links = [g @ lk if lk is not None else None for lk in links]
        links = [lk if lk is not None else rest_world[i] for i, lk in enumerate(links)]
        return cls(su, ge, game, nodes, names, rest_local, rest_world, palette, cpu, links)

    @property
    def has_joints(self) -> bool:
        return any(n.joint for n in self.nodes)

    def add_bones(self, scene: Scene, root_name: str) -> int:
        """Add the root bone and one bone per node; returns the root's index.
        Node ``i`` becomes scene node ``root + 1 + i``."""
        root = len(scene.nodes)
        scene.nodes.append(SceneNode(root_name, is_bone=True))
        for i, n in enumerate(self.nodes):
            parent = root if n.parent < 0 else root + 1 + n.parent
            scene.nodes.append(SceneNode(self.names[i], parent=parent, matrix=self.rest_local[i],
                                         is_bone=True, bind=self.links[i]))
        return root

    def skin(self, mesh, root: int) -> Tuple[np.ndarray, Optional[np.ndarray], np.ndarray, np.ndarray]:
        """(positions, normals, bone indices, weights) of a parsed mesh, with
        vertices moved into skin bind space."""
        infl = vertex_influences(mesh, self.palette, self.cpu)
        count = len(mesh.positions)
        bones = np.full((count, MAX_INFLUENCES), -1, dtype=np.int32)
        weights = np.zeros((count, MAX_INFLUENCES), dtype=np.float64)
        pos = mesh.positions.astype(np.float64).copy()
        nrm = mesh.normals.astype(np.float64).copy() if mesh.normals is not None else None
        ib_cache: Dict[int, Optional[np.ndarray]] = {}
        moved: Dict[Tuple[int, Optional[int]], np.ndarray] = {}
        for v, lst in enumerate(infl):
            lst = sorted((x for x in lst if x[2] > 0), key=lambda x: -x[2])[:MAX_INFLUENCES]
            if not lst:
                bones[v, 0], weights[v, 0] = root, 1.0
                continue
            total = sum(w for _n, _b, w in lst)
            for k, (node, _bind, w) in enumerate(lst):
                bones[v, k] = root + 1 + node if 0 <= node < len(self.nodes) else root
                weights[v, k] = w / total
            node, bind, _w = lst[0]
            key = (node, bind)
            m = moved.get(key)
            if m is None:
                if not 0 <= node < len(self.nodes):
                    m = np.eye(4)
                else:
                    b = np.eye(4)
                    if bind is not None:
                        if bind not in ib_cache:
                            ib_cache[bind] = inverse_bind(self.ge, bind)
                        b = ib_cache[bind] if ib_cache[bind] is not None else np.eye(4)
                    m = self.links[node] @ b
                moved[key] = m
            if not np.allclose(m, np.eye(4), atol=1e-6):
                pos[v] = m[:3, :3] @ pos[v] + m[:3, 3]
                if nrm is not None:
                    n = np.linalg.inv(m[:3, :3]).T @ nrm[v]
                    ln = np.linalg.norm(n)
                    nrm[v] = n / ln if ln > 1e-9 else n
        return pos, nrm, bones, weights

    def is_articulated(self, meshes) -> bool:
        """True when meshes are skinned or sit on more than the root."""
        for mesh in meshes:
            if mesh.skin_runs or mesh.vertex_matrix is not None:
                return True
            for node, bind, _w in self.palette.get(mesh.node_index, []):
                if 0 <= node < len(self.nodes) and not np.allclose(self.rest_world[node], np.eye(4), atol=1e-5):
                    return True
        return False

    def bake(self, clip_data: bytes, runtime: Optional[Runtime]) -> "BakeResult":
        return bake_clip(self.su, self.game, clip_data, runtime)


BakeResult = Tuple[Optional[float], Optional[np.ndarray], Optional[str]]  # (start frame, frames, error)
_ERRORS = (EmulationError, struct.error, ZeroDivisionError, OverflowError, ValueError, IndexError)


def bake_clip(su: bytes, game: int, clip_data: bytes, runtime: Optional[Runtime]) -> BakeResult:
    """Local matrices (frames, nodes, 4, 4) of every frame of a clip; frames is
    None when the clip does not fit the model or nothing moves."""
    program = SetupProgram(su, game, runtime)
    if not program.runnable:
        return None, None, None
    clip = Clip.parse(clip_data, game, program.channels)
    if clip is None:
        return None, None, None
    frames = np.arange(clip.start, clip.end + 1e-4, 1.0)
    if not len(frames):
        frames = np.array([clip.start])
    try:
        values = program.evaluate(clip, frames)
        out = np.array([local_matrices(su, game, xf, jt) for xf, jt in values])
    except _ERRORS as exc:
        return clip.start, None, str(exc)
    rest = np.array(local_matrices(su, game))
    if np.abs(out - rest[None]).max() < 1e-5:
        return clip.start, None, None
    return clip.start, out, None


# --- worker processes: each holds its own console runtime
_WORKER_RUNTIME: Optional[Runtime] = None


def _init_worker(boot: Optional[bytes], game_dol: Optional[bytes], game: int) -> None:
    global _WORKER_RUNTIME
    _WORKER_RUNTIME = Runtime(boot, game_dol, game) if boot else None


def _bake_job(su: bytes, game: int, clip_data: bytes) -> BakeResult:
    return bake_clip(su, game, clip_data, _WORKER_RUNTIME)


def make_pool(boot: Optional[bytes], game_dol: Optional[bytes], game: int, workers: int):
    """Process pool for clip evaluation (None for a single worker)."""
    if workers <= 1:
        return None
    from concurrent.futures import ProcessPoolExecutor
    return ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                               initargs=(boot, game_dol, game))


def add_animations(scene: Scene, rig: Rig, root: int, clips: Sequence[Tuple[str, bytes]],
                   runtime: Optional[Runtime], log=None, pool=None) -> int:
    """Evaluate every compatible clip and add it to ``scene``; returns how many."""
    if not SetupProgram(rig.su, rig.game).runnable or not clips:
        return 0
    if pool is not None:
        futures = [pool.submit(_bake_job, rig.su, rig.game, data) for _name, data in clips]
        results = [f.result() for f in futures]
    else:
        results = [rig.bake(data, runtime) for _name, data in clips]
    added = 0
    nodes = [root + 1 + i for i in range(len(rig.nodes))]
    for (name, _data), (start, frames, error) in zip(clips, results):
        if error and log:
            log(f"  animation {name} failed: {error}")
        if frames is None:
            continue
        scene.animations.append(SceneAnimation(name, FPS, start, nodes, frames))
        added += 1
    return added
