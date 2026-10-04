"""Format-neutral scene description handed to the exporters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class SceneNode:
    name: str
    parent: int = -1                    # index into Scene.nodes, -1 = root
    matrix: np.ndarray = field(default_factory=lambda: np.eye(4, dtype=np.float64))  # local, column vectors
    is_bone: bool = False
    bind: Optional[np.ndarray] = None   # world matrix the skin was bound at (default: rest world)


@dataclass
class SceneMaterial:
    name: str
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    ambient: tuple = (0.0, 0.0, 0.0)
    specular: tuple = (0.0, 0.0, 0.0)
    shininess: float = 0.0
    texture: Optional[str] = None       # path relative to the exported file
    alpha_texture: bool = False         # texture carries meaningful alpha
    normal_map: Optional[str] = None    # tangent space (OpenGL) normal map, relative path
    emissive_map: Optional[str] = None  # emitted color, relative path
    double_sided: bool = False
    wrap_u: bool = True
    wrap_v: bool = True


@dataclass
class SceneMesh:
    name: str
    positions: np.ndarray               # (N, 3)
    triangles: np.ndarray               # (T, 3)
    normals: Optional[np.ndarray] = None
    uvs: List[np.ndarray] = field(default_factory=list)
    colors: Optional[np.ndarray] = None  # (N, 4) uint8
    material: int = -1
    node: int = -1                       # node the mesh hangs under
    # skinning: up to K influences per vertex (node index, -1 = unused) and their weights
    skin_bones: Optional[np.ndarray] = None    # (N, K) int
    skin_weights: Optional[np.ndarray] = None  # (N, K) float


@dataclass
class SceneAnimation:
    """Baked animation: local matrices of ``nodes`` for every frame."""
    name: str
    fps: float
    start: float                         # first frame number
    nodes: List[int]                     # animated scene node indices
    frames: np.ndarray                   # (F, len(nodes), 4, 4) local matrices


@dataclass
class Scene:
    name: str
    nodes: List[SceneNode] = field(default_factory=list)
    meshes: List[SceneMesh] = field(default_factory=list)
    materials: List[SceneMaterial] = field(default_factory=list)
    animations: List[SceneAnimation] = field(default_factory=list)

    def world_matrix(self, index: int) -> np.ndarray:
        m = np.eye(4)
        while index >= 0:
            node = self.nodes[index]
            m = node.matrix @ m
            index = node.parent
        return m

    def bind_matrix(self, index: int) -> np.ndarray:
        node = self.nodes[index]
        return node.bind if node.bind is not None else self.world_matrix(index)
