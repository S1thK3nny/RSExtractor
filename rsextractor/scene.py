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


@dataclass
class SceneMaterial:
    name: str
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    ambient: tuple = (0.0, 0.0, 0.0)
    specular: tuple = (0.0, 0.0, 0.0)
    shininess: float = 0.0
    texture: Optional[str] = None       # path relative to the exported file
    alpha_texture: bool = False         # texture carries meaningful alpha
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
    # skinning: one bone (node index) per vertex, weight 1.0 (rigid GX matrix skinning)
    vertex_bones: Optional[np.ndarray] = None


@dataclass
class Scene:
    name: str
    nodes: List[SceneNode] = field(default_factory=list)
    meshes: List[SceneMesh] = field(default_factory=list)
    materials: List[SceneMaterial] = field(default_factory=list)

    def world_matrix(self, index: int) -> np.ndarray:
        m = np.eye(4)
        while index >= 0:
            node = self.nodes[index]
            m = node.matrix @ m
            index = node.parent
        return m
