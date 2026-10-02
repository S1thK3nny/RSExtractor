"""End-to-end extraction: disc image -> FBX models + PNG textures."""
from __future__ import annotations

import os
import re
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image

from .archive import Archive, Entry, decompress, parse_package, FOLDER
from .disc import Disc
from .fbx import write_fbx
from .material import Material, parse_materials
from .model import parse_geometry
from .scene import Scene, SceneMaterial, SceneMesh, SceneNode
from .texture import TextureLibrary

Log = Callable[[str], None]

# RS3 keeps a global table of quantized normals in the boot DOL (data section 4).
RS3_NORMAL_TABLE_ADDR = 0x80052C00


def detect_game(disc: Disc) -> int:
    if disc.game_id.startswith("GSW"):
        return 2
    if disc.game_id.startswith("GLR"):
        return 3
    raise ValueError(f"unsupported game {disc.game_id} ({disc.title})")


def rs3_normal_table(disc: Disc) -> Optional[bytes]:
    dol = disc.read_dol()
    offs = struct.unpack_from(">18I", dol, 0)
    addrs = struct.unpack_from(">18I", dol, 0x48)
    sizes = struct.unpack_from(">18I", dol, 0x90)
    for o, a, s in zip(offs, addrs, sizes):
        if s and a <= RS3_NORMAL_TABLE_ADDR < a + s:
            start = o + RS3_NORMAL_TABLE_ADDR - a
            return dol[start:o + s]
    return None


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "unnamed"


@dataclass
class ModelParts:
    name: str
    archive: str          # e.g. "data"
    entry: str            # archive entry path, e.g. "plCraft/xwing_base"
    parts: Dict[str, bytes] = field(default_factory=dict)  # suffix -> bytes


class GameData:
    """Indexes one disc: archives, packages, models and texture pools."""

    def __init__(self, disc: Disc, log: Log = print):
        self.disc = disc
        self.game = detect_game(disc)
        self.log = log
        self.archives: List[Archive] = [Archive(disc, f.path[:-4]) for f in disc if f.path.lower().endswith(".dir")]
        self.textures = TextureLibrary()
        self.normal_table = rs3_normal_table(disc) if self.game == 3 else None
        self._cache: "OrderedDict[Tuple[str, str], bytes]" = OrderedDict()

    def archive_label(self, archive: Archive) -> str:
        return archive.base.rsplit("/", 1)[-1]

    def read_entry(self, archive: Archive, entry: Entry) -> bytes:
        key = (archive.base, entry.name)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        data = decompress(archive.read_raw(entry))
        self._cache[key] = data
        while len(self._cache) > 4:
            self._cache.popitem(last=False)
        return data

    def iter_entries(self, filt: Optional[Callable[[str], bool]] = None) -> Iterator[Tuple[Archive, Entry]]:
        for archive in self.archives:
            for entry in archive.files():
                label = f"{self.archive_label(archive)}/{entry.name}"
                if filt is None or filt(label):
                    yield archive, entry

    def index_textures(self) -> None:
        """Register every texture pool on the disc (lazy loaders, data read on demand)."""
        standalone: Dict[str, Dict[str, Tuple[Archive, Entry]]] = {}
        for archive, entry in self.iter_entries():
            name = entry.name
            if name.endswith(("_tpi", "_tpd")) and entry.length:
                standalone.setdefault(f"{archive.base}:{name[:-4]}", {})[name[-3:]] = (archive, entry)
                continue
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                continue
            files = {p.path: p for p in pkg if not p.attrib & FOLDER}
            for path, p in files.items():
                if not path.endswith("_tpi"):
                    continue
                d = files.get(path[:-4] + "_tpd")
                if d is None:
                    continue
                tpi = data[p.offset:p.offset + p.length]
                self.textures.add_pool(tpi, self._pool_loader(archive, entry, d.offset, d.length), self.game)
        for pair in standalone.values():
            if "tpi" in pair and "tpd" in pair:
                a, e = pair["tpi"]
                tpi = self.read_entry(a, e)
                da, de = pair["tpd"]
                self.textures.add_pool(tpi, lambda da=da, de=de: self.read_entry(da, de), self.game)
        if self.game == 3 and self.disc.exists("gTexPool.cData"):
            data_arc = next((a for a in self.archives if a.base.lower().endswith("/data")), None)
            if data_arc is not None and data_arc.get("pool/global_tpool") is not None:
                index = data_arc.read("pool/global_tpool")
                count = struct.unpack_from(">I", self.disc.read_range("gTexPool.cData", 4, 4))[0]
                header = self.disc.read_range("gTexPool.cData", 0, 8 + 16 * count)
                self.textures.add_global_pool(index, header,
                                              lambda o, n: self.disc.read_range("gTexPool.cData", o, n))
        self.log(f"  indexed {len(self.textures)} textures")

    def _pool_loader(self, archive: Archive, entry: Entry, off: int, length: int):
        def load() -> bytes:
            return self.read_entry(archive, entry)[off:off + length]
        return load

    def iter_models(self, filt: Optional[Callable[[str], bool]] = None) -> Iterator[ModelParts]:
        for archive, entry in self.iter_entries():
            label = f"{self.archive_label(archive)}/{entry.name}"
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                continue
            files = {p.path: data[p.offset:p.offset + p.length] for p in pkg if not p.attrib & FOLDER}
            stems = [path[:-3] for path in files if path.endswith("_ge")]
            short = [s.rsplit("/", 1)[-1] for s in stems]
            lowered = [s.lower() for s in short]  # output may land on a case-insensitive file system
            for stem, name in zip(stems, short):
                # Same model name in two package folders: keep the folder in the name.
                if lowered.count(name.lower()) > 1:
                    name = stem.replace("/", "__")
                model_label = f"{label}/{stem}"
                if filt is not None and not filt(model_label):
                    continue
                parts = {sfx: files[stem + "_" + sfx] for sfx in ("su", "ge", "si", "an", "cl", "nm")
                         if stem + "_" + sfx in files}
                yield ModelParts(name, self.archive_label(archive), entry.name, parts)


# Standalone image resources: 32-byte header {u16 width, u16 height, u8, u8 format,
# u8, u8 has_mips, ...} followed by GX texture data. Format codes are Factor 5's own.
F5_IMAGE_FORMATS = {0x00: 0, 0x01: 1, 0x02: 2, 0x03: 3, 0x09: 5, 0x0A: 6, 0x0B: 6, 0x0C: 14}


def decode_image_resource(data: bytes) -> Optional[np.ndarray]:
    from . import gxtexture as gx
    if len(data) < 64:
        return None
    w, h = struct.unpack_from(">HH", data, 0)
    fmt = F5_IMAGE_FORMATS.get(data[5])
    if fmt is None or not (0 < w <= 4096 and 0 < h <= 4096) or data[4] or data[6]:
        return None
    if len(data) - 32 < gx.level_size(fmt, w, h):
        return None
    return gx.decode(data[32:], fmt, w, h)


def export_images(game: GameData, out_dir: str, filt=None) -> int:
    """Write every standalone image resource as PNG under ``out_dir/<archive>/images``."""
    count = 0
    for archive, entry in game.iter_entries(filt):
        if entry.name.endswith(("_tpi", "_tpd")):
            continue
        data = game.read_entry(archive, entry)
        if parse_package(data):
            continue
        img = decode_image_resource(data)
        if img is None:
            continue
        parts = [safe_name(p) for p in entry.name.split("/")]
        target = os.path.join(out_dir, safe_name(game.archive_label(archive)), "images", *parts) + ".png"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        Image.fromarray(img, "RGBA").save(target)
        count += 1
    return count


class Exporter:
    def __init__(self, game: GameData, out_dir: str, embed_textures: bool = False, log: Log = print):
        self.g = game
        self.out_dir = out_dir
        self.embed = embed_textures
        self.log = log
        self.written_textures: Dict[str, str] = {}
        self.stats = {"models": 0, "skipped": 0, "textures": 0, "missing_textures": 0}
        self.missing: List[str] = []

    def model_dir(self, m: ModelParts) -> str:
        parts = [safe_name(p) for p in m.entry.split("/")]
        return os.path.join(self.out_dir, safe_name(m.archive), *parts)

    def export_model(self, m: ModelParts) -> Optional[str]:
        geo = parse_geometry(m.parts["ge"], self.g.game, self.g.normal_table)
        if not geo.meshes:
            self.stats["skipped"] += 1
            return None
        materials = parse_materials(m.parts.get("si", b""), self.g.game)
        folder = self.model_dir(m)
        os.makedirs(folder, exist_ok=True)

        scene = Scene(m.name)
        scene.nodes.append(SceneNode(safe_name(m.name)))
        mat_map: Dict[int, int] = {}
        for mesh_no, mesh in enumerate(geo.meshes):
            if mesh.material_index not in mat_map:
                mat_map[mesh.material_index] = len(scene.materials)
                mat = materials[mesh.material_index] if mesh.material_index < len(materials) else None
                scene.materials.append(self._material(mat, mesh.material_index, folder))
            uvs = [mesh.uvs] + list(mesh.extra_uvs) if mesh.uvs is not None else []
            scene.meshes.append(SceneMesh(
                name=f"{safe_name(m.name)}_{mesh_no:02d}",
                positions=mesh.positions.astype(np.float64),
                triangles=mesh.triangles,
                normals=mesh.normals,
                uvs=uvs,
                colors=mesh.colors,
                material=mat_map[mesh.material_index],
                node=0,
            ))
        path = os.path.join(folder, safe_name(m.name) + ".fbx")
        write_fbx(scene, path, embed_textures=self.embed)
        self.stats["models"] += 1
        return path

    def _material(self, mat: Optional[Material], index: int, folder: str) -> SceneMaterial:
        if mat is None:
            return SceneMaterial(f"material{index}")
        sm = SceneMaterial(safe_name(mat.name), diffuse=tuple(mat.color0), wrap_u=not mat.clamp_s,
                           wrap_v=not mat.clamp_t)
        if mat.textures:
            ref = mat.textures[0]
            rel = self._texture_file(ref, folder, mat.name)
            if rel:
                sm.texture = rel
                sm.alpha_texture = ref.format in (2, 3, 5, 6)  # IA4, IA8, RGB5A3, RGBA8
        return sm

    def _texture_file(self, ref, folder: str, mat_name: str) -> Optional[str]:
        """Write the texture PNG into ``folder/textures`` and return its relative path."""
        fname = f"{safe_name(mat_name)}_{ref.hash.hex()[:8]}.png"
        rel = f"textures/{fname}"
        target = os.path.join(folder, "textures", fname)
        if os.path.exists(target):
            return rel
        key = ref.hash.hex()
        if key not in self.written_textures:
            img = self.g.textures.decode(ref)
            if img is None:
                self.stats["missing_textures"] += 1
                self.missing.append(f"{mat_name} {ref.width}x{ref.height} fmt {ref.format} "
                                    f"{ref.hash.hex()[:8]} {'(not in any pool)' if ref.hash not in self.g.textures else ''}")
                self.written_textures[key] = ""
                return None
            os.makedirs(os.path.dirname(target), exist_ok=True)
            Image.fromarray(img, "RGBA").save(target, optimize=False)
            self.written_textures[key] = target
            self.stats["textures"] += 1
            return rel
        source = self.written_textures[key]
        if not source:
            return None
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(source, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
        return rel
