"""Bump maps -> tangent space normal maps, self lit masks -> emissive maps,
highlight masks -> specular maps.

Both games bump map in two ways (see docs/FORMATS.md, "Bump maps"):

* tilt maps, read as an indirect texture whose offsets tilt the normal along the
  vertex tangent and binormal (RS3 FUN_7f3d074c). Two channels give the tilt
  towards +u and +v (zero at 127.5): RS2 IA4 intensity and inverted alpha (alpha
  tilts down the image), RS3 CMPR red (= blue) and green.
* height maps, embossed by sampling the texture twice with a light shifted texture
  coordinate (RS2 FUN_7fd3767c, RS3 FUN_7f3cdf28). Often the 1 bit alpha of the
  color texture, which then marks the grooves (alpha 0).

The game scales either with tweakable per level constants, so the strengths here
are a neutral choice (full tilt = 45 degrees). Output follows the OpenGL
convention (green = +v), which Blender, Unity and glTF expect.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

TILT = "tilt"
HEIGHT = "height"

# Tilt maps put 0 at 128 (CMPR) or between IA4 nibbles 7 and 8 (127.5 +- 8.5).
TILT_ZERO = 127.5
# Normal tilt of a height map per unit of height change across one texel.
HEIGHT_STRENGTH = 2.0


def _encode(tu: np.ndarray, tv: np.ndarray) -> np.ndarray:
    """Normal map (RGBA8) of normals tilted by ``tu`` towards +u and ``tv`` towards +v (v up)."""
    n = np.stack([tu, tv, np.ones_like(tu)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rgb = np.clip(np.rint((n * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    return np.concatenate([rgb, np.full(tu.shape + (1,), 255, np.uint8)], -1)


def tilt_to_normal(img: np.ndarray, game: int) -> np.ndarray:
    px = (img.astype(np.float32) - TILT_ZERO) / TILT_ZERO
    if game == 2:
        return _encode(px[..., 0], -px[..., 3])
    return _encode(px[..., 0], px[..., 1])


def channel(img: np.ndarray, alpha: bool) -> np.ndarray:
    """A map's value in [0, 1]: the texture's alpha, or its color as intensity."""
    if alpha:
        return img[..., 3].astype(np.float32) / 255.0
    return img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32) / 255.0


def gray(values: np.ndarray) -> np.ndarray:
    """Opaque RGBA8 image of values in [0, 1]."""
    v = np.clip(np.rint(values * 255.0), 0, 255).astype(np.uint8)
    return np.stack([v, v, v, np.full_like(v, 255)], -1)


def opaque(img: np.ndarray) -> np.ndarray:
    out = img.copy()
    out[..., 3] = 255
    return out


def height_to_normal(h: np.ndarray) -> np.ndarray:
    du = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
    dv = (np.roll(h, 1, 0) - np.roll(h, -1, 0)) * 0.5  # rows run down the image, v runs up
    return _encode(-du * HEIGHT_STRENGTH, -dv * HEIGHT_STRENGTH)


def emissive(color: Optional[np.ndarray], mask: np.ndarray) -> np.ndarray:
    """The color map where ``mask`` (in [0, 1]) makes it shine, black elsewhere."""
    if color is None:
        return gray(mask)
    if color.shape[:2] != mask.shape:
        mask = np.asarray(Image.fromarray(mask, "F").resize((color.shape[1], color.shape[0]), Image.BILINEAR))
    rgb = np.rint(color[..., :3].astype(np.float32) * mask[..., None]).astype(np.uint8)
    return np.concatenate([rgb, np.full(mask.shape + (1,), 255, np.uint8)], -1)


def specular(color: np.ndarray, tint: tuple) -> np.ndarray:
    """Specular color map: ``tint`` where the color map's green channel lets the
    highlight through, ``clamp(4 * (green - 0.25))`` as the game masks it."""
    mask = np.clip(4.0 * (color[..., 1].astype(np.float32) / 255.0 - 0.25), 0.0, 1.0)
    rgb = np.rint(mask[..., None] * np.asarray(tint[:3], np.float32) * 255.0).astype(np.uint8)
    return np.concatenate([rgb, np.full(mask.shape + (1,), 255, np.uint8)], -1)
