"""Environment cube maps -> equirectangular panoramas.

Every level package holds the cube its reflective materials mirror
(``extra/envMap/<face>``, RS2 front end and briefing ``envMap/<face>``, RS2 loose
``envmap/<name>/<face>`` entries), six standalone images. The game renders the
cube into a sphere map each frame (RS2 FUN_7fd2ad30, RS3 FUN_7f3b58d4; the RS2
fallback is ``envmap/bespin``).

The faces are camera renders in Maya's envCube layout, Y up: ``frt`` looks
along +Z, ``bck`` -Z, ``rgt`` +X, ``lft`` -X (image right is ``forward x up``),
``top`` +Y with image up = +Z and ``bot`` -Y with image up = -Z. The seams between
the faces only meet in this layout. Each face has a one texel transparent border
(only its color is used here); the panorama is opaque.

The panorama follows Blender's equirectangular world mapping after the FBX
import turns Y up into Z up (Blender ``(x, y, z) = (X, -Z, Y)``):
``u = 0.5 - atan2(y, x) / 2pi``, so the image centre looks along +X (the game's
``rgt``) and +Y (the game's ``bck``) a quarter to the left; the top row is straight up.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

FACES = ("rgt", "lft", "top", "bot", "frt", "bck")


def _sample(img: np.ndarray, s: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Bilinear lookup at face coordinates ``s`` (right) and ``t`` (up) in [-1, 1]."""
    h, w = img.shape[:2]
    x = np.clip((s + 1.0) * 0.5 * w - 0.5, 0, w - 1)
    y = np.clip((1.0 - t) * 0.5 * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bottom = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def cube_lookup(faces: Dict[str, np.ndarray], d: np.ndarray) -> np.ndarray:
    """Colors (float RGBA) of the cube in directions ``d`` (N x 3, game axes)."""
    X, Y, Z = d[:, 0], d[:, 1], d[:, 2]
    ax, ay, az = np.abs(X), np.abs(Y), np.abs(Z)
    out = np.zeros((len(d), 4), np.float32)
    picks = [
        ("frt", (az >= ax) & (az >= ay) & (Z > 0), lambda: (-X / az, Y / az)),
        ("bck", (az >= ax) & (az >= ay) & (Z <= 0), lambda: (X / az, Y / az)),
        ("rgt", (ax > az) & (ax >= ay) & (X > 0), lambda: (Z / ax, Y / ax)),
        ("lft", (ax > az) & (ax >= ay) & (X <= 0), lambda: (-Z / ax, Y / ax)),
        ("top", (ay > ax) & (ay > az) & (Y > 0), lambda: (X / ay, Z / ay)),
        ("bot", (ay > ax) & (ay > az) & (Y <= 0), lambda: (X / ay, -Z / ay)),
    ]
    with np.errstate(divide="ignore", invalid="ignore"):
        for face, mask, coords in picks:
            if mask.any():
                s, t = coords()
                out[mask] = _sample(faces[face].astype(np.float32), s[mask], t[mask])
    return out


def panorama(faces: Dict[str, np.ndarray], height: Optional[int] = None) -> np.ndarray:
    """Equirectangular RGBA8 image (2:1) of a cube map, see the module notes."""
    size = max(f.shape[1] for f in faces.values())
    h = height or 2 * size
    w = 2 * h
    u = (np.arange(w) + 0.5) / w
    v = 1.0 - (np.arange(h) + 0.5) / h
    phi = (0.5 - u) * 2.0 * np.pi            # atan2(y, x) in Blender axes
    theta = (v - 0.5) * np.pi                # elevation
    phi, theta = np.meshgrid(phi, theta)
    bx, by, bz = np.cos(phi) * np.cos(theta), np.sin(phi) * np.cos(theta), np.sin(theta)
    d = np.stack([bx, bz, -by], -1).reshape(-1, 3)   # Blender (x, y, z) -> game (X, Y, Z)
    rgba = cube_lookup(faces, d).reshape(h, w, 4)
    rgba[..., 3] = 255
    return np.clip(np.rint(rgba), 0, 255).astype(np.uint8)
