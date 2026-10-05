"""End-to-end extraction: disc image -> FBX models + PNG/TGA textures."""
from __future__ import annotations

import os
import re
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image

from .archive import Archive, DataPool, Entry, decompress, parse_package, FOLDER
from .bump import TILT, channel, emissive, gray, height_to_normal, opaque, specular, tilt_to_normal
from .collision import parse_collision
from .disc import Disc
from .envmap import FACES, panorama
from .fbx import write_fbx
from .material import Material, parse_materials, specular_exponent
from .anim import Runtime
from .model import Mesh, parse_geometry
from .placement import Placed, parse_dat
from .rig import Rig, add_animations
from .scene import Scene, SceneMaterial, SceneMesh, SceneNode
from .skeleton import place_static_meshes
from .texture import TextureLibrary
from .tiles import parse_bmap, parse_lm, parse_tmap, tile_cells

Log = Callable[[str], None]

# Texture file formats: name -> Pillow save options. TGA is written uncompressed with
# its 8-bit alpha channel, which every DCC tool and engine reads.
TEXTURE_FORMATS: Dict[str, dict] = {
    "png": {"format": "PNG", "optimize": False},
    "tga": {"format": "TGA", "rle": False},
}


def save_image(img: np.ndarray, path: str, fmt: str) -> None:
    """Save an RGBA8 array as ``fmt`` (a key of ``TEXTURE_FORMATS``)."""
    Image.fromarray(img, "RGBA").save(path, **TEXTURE_FORMATS[fmt])


# GX formats whose alpha is real (IA4, IA8, RGB5A3, RGBA8); CMPR alpha is a 1 bit mask.
ALPHA_FORMATS = (2, 3, 5, 6)

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
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "unnamed"


def level_of(label: str) -> str:
    """``lvlPack02/L_06_Hoth_lvl/01/x`` -> ``lvlPack02/L_06_Hoth_lvl``."""
    return "/".join(label.split("/")[:2])


@dataclass
class ModelParts:
    name: str
    archive: str          # e.g. "data"
    entry: str            # archive entry path, e.g. "plCraft/xwing_base"
    parts: Dict[str, bytes] = field(default_factory=dict)  # suffix -> bytes
    clips: List[Tuple[str, bytes]] = field(default_factory=list)  # animation clips next to the model
    envmap: Dict[str, bytes] = field(default_factory=dict)  # face -> image: the package's environment cube


@dataclass
class TileLevel:
    """A level resource laid out from tiles (``tmap`` + ``bmap``) and the tile and
    building models it uses (``t###_0``, ``b###_0``, by name)."""
    archive: str
    entry: str
    tmap: bytes
    bmap: bytes
    models: Dict[str, Dict[str, bytes]] = field(default_factory=dict)  # name -> suffix -> part


@dataclass
class ObjectLevel:
    """A level resource's placed objects (``dat``) and the models they name, by lower
    case name, from the resources of the same level and their ``bData.pool`` packages."""
    archive: str
    entry: str
    objects: List[Placed]
    models: Dict[str, Dict[str, bytes]] = field(default_factory=dict)


TILE_MODEL = re.compile(r"[tb]\d{3}_0$")
# Briefing and HUD wireframes (RS2 gfxWIREOverlays*, RS3 wireOverlay*): drawn from the collision hull.
WIRE_OVERLAY = re.compile(r"wireoverlay", re.I)


def cutscene_cast(cs: bytes) -> List[Tuple[str, str, str]]:
    """``X_cs``: the actors of a cutscene as (locator, model, clip); clip is "" for actors
    that only stand at their locator."""
    strings, count = struct.unpack_from(">I", cs, 0)[0], struct.unpack_from(">I", cs, 12)[0]

    def name(offset: int) -> str:
        start = strings + offset
        if offset == 0xFFFFFFFF or start >= len(cs):
            return ""
        return cs[start:cs.index(b"\0", start)].decode("latin-1")

    cast = []
    for i in range(count):
        actor = struct.unpack_from(">I", cs, 16 + 4 * i)[0]
        locator, _variant, _, model, clip = struct.unpack_from(">5I", cs, actor)
        cast.append((name(locator), name(model), name(clip)))
    return cast


def actor_clip_names(files: Dict[str, bytes]) -> set:
    """Clips a package's cutscene casts play on their actors (lower case)."""
    return {clip.lower() for path, cs in files.items() if path.endswith("_cs")
            for _, _, clip in cutscene_cast(cs) if clip}


def clip_channels(clip: bytes) -> Optional[int]:
    if len(clip) < 4:
        return None
    obj = struct.unpack_from(">I", clip, 0)[0]
    return struct.unpack_from(">I", clip, obj + 20)[0] if obj + 24 <= len(clip) else None


def envmap_faces(files: Dict[str, bytes]) -> Dict[str, bytes]:
    """The environment cube of a package (``extra/envMap/<face>``, RS2 front end and
    briefing ``envMap/<face>``), or nothing when a face is missing."""
    faces = {path.rsplit("/", 1)[1]: data for path, data in files.items()
             if path.lower().rsplit("/", 2)[-2:-1] == ["envmap"] and path.rsplit("/", 1)[1] in FACES}
    return faces if len(faces) == len(FACES) else {}


class GameData:
    """Indexes one disc: archives, packages, models and texture pools."""

    def __init__(self, disc: Disc, log: Log = print):
        self.disc = disc
        self.game = detect_game(disc)
        self.log = log
        self.archives: List[Archive] = [Archive(disc, f.path[:-4]) for f in disc if f.path.lower().endswith(".dir")]
        self.textures = TextureLibrary()
        self.normal_table = rs3_normal_table(disc) if self.game == 3 else None
        self.data_pool = DataPool(disc) if disc.exists("bData.pool") else None
        self._pool_refs: Optional[Dict[bytes, List[str]]] = None
        self._shared_clips: Optional[Dict[Tuple[str, int], List[Tuple[str, bytes]]]] = None
        self._outside_models: Optional[Dict[str, Tuple[Archive, Entry, str]]] = None
        # model name (lower case) -> [(levels or None for any, channels, clip name, clip)]
        self._actor_clips: Dict[str, List[Tuple[Optional[set], int, str, bytes]]] = {}
        self._cache: "OrderedDict[Tuple[str, str], bytes]" = OrderedDict()
        self.position = (0, 0)  # (entries done, entries) while iter_models runs

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
        refs: Dict[bytes, List[str]] = {}
        for archive, entry in self.iter_entries():
            name = entry.name
            if name.endswith(("_tpi", "_tpd")) and entry.length:
                standalone.setdefault(f"{archive.base}:{name[:-4]}", {})[name[-3:]] = (archive, entry)
                continue
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                continue
            self._add_pool_refs(refs, f"{self.archive_label(archive)}/{name}", data, pkg)
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
        if self._pool_refs is None:
            self._pool_refs = refs
        self.log(f"  indexed {len(self.textures)} textures")

    def _add_pool_refs(self, refs: Dict[bytes, List[str]], label: str, data: bytes, pkg) -> None:
        if self.data_pool is None:
            return
        for p in pkg:
            if p.path.endswith("_dref") and p.length == 20:
                key = data[p.offset:p.offset + 20]
                if key in self.data_pool:
                    refs.setdefault(key, []).append(f"{label}/{p.path[:-5]}")

    def pool_refs(self) -> Dict[bytes, List[str]]:
        """``bData.pool`` key -> labels (``archive/entry/dref``) of its references in disc order."""
        if self._pool_refs is None:
            refs: Dict[bytes, List[str]] = {}
            if self.data_pool is not None:
                for archive, entry in self.iter_entries():
                    data = self.read_entry(archive, entry)
                    pkg = parse_package(data)
                    if pkg:
                        self._add_pool_refs(refs, f"{self.archive_label(archive)}/{entry.name}", data, pkg)
            self._pool_refs = refs
        return self._pool_refs

    def _pool_loader(self, archive: Archive, entry: Entry, off: int, length: int):
        def load() -> bytes:
            return self.read_entry(archive, entry)[off:off + length]
        return load

    def _package_clips(self, files: Dict[str, bytes]) -> Dict[str, List[Tuple[str, bytes]]]:
        """Standalone animation clips of a package, by folder."""
        clips: Dict[str, List[Tuple[str, bytes]]] = {}
        actors = actor_clip_names(files)
        for path, data in files.items():
            if path.endswith("_an") and path[:-3] + "_ge" not in files:
                folder, _, name = path[:-3].rpartition("/")
                if name.lower() in actors:  # played by a cutscene actor, not the cutscene's rig
                    continue
                clips.setdefault(folder, []).append((name, data))
        return clips

    def _sibling_clips(self, archive: Archive, entry: Entry) -> List[Tuple[str, bytes]]:
        """RS3 keeps character clips in a sibling entry: ``X_base`` -> ``X_anim``."""
        if not entry.name.endswith("_base"):
            return []
        sibling = archive.get(entry.name[:-5] + "_anim")
        if sibling is None:
            return []
        data = decompress(archive.read_raw(sibling))
        pkg = parse_package(data)
        if not pkg:
            return []
        files = {p.path: data[p.offset:p.offset + p.length] for p in pkg if not p.attrib & FOLDER}
        return [clip for group in self._package_clips(files).values() for clip in group]

    def _loose_model(self, archive: Archive, entry: Entry) -> Optional[ModelParts]:
        """RS2 keeps a few models as loose archive entries (``folder/name_ge`` ...)."""
        stem = entry.name[:-3]
        folder, _, name = stem.rpartition("/")
        parts: Dict[str, bytes] = {}
        for sfx in ("su", "ge", "si", "an", "cl", "nm"):
            e = archive.get(f"{stem}_{sfx}")
            if e is not None and e.length:
                parts[sfx] = self.read_entry(archive, e) if sfx == "ge" else decompress(archive.read_raw(e))
        clips = []
        for e in archive.files():
            other = e.name[:-3]
            if (e.name.endswith("_an") and other.rpartition("/")[0] == folder and other != stem
                    and archive.get(other + "_ge") is None):
                clips.append((other.rpartition("/")[2], decompress(archive.read_raw(e))))
        return ModelParts(name, self.archive_label(archive), folder, parts, clips)

    def _read_pool_package(self, key: bytes) -> Dict[str, bytes]:
        data = self.data_pool.read(key)
        return {p.path: data[p.offset:p.offset + p.length] for p in parse_package(data) or ()
                if not p.attrib & FOLDER}

    def shared_clips(self) -> Dict[Tuple[str, int], List[Tuple[str, bytes]]]:
        """Clip sets of ``bData.pool`` (packages without geometry, such as ``ss2HgunAnims``)
        by (level, channel count). A level loads them for all its characters with that rig."""
        if self._shared_clips is None:
            self._scan_pool()
        return self._shared_clips

    def actor_clips(self, name: str, channels: int, levels: set) -> List[Tuple[str, bytes]]:
        """Cutscene clips the casts (``_cs``) play on model ``name`` in one of ``levels``."""
        if self._shared_clips is None:
            self._scan_pool()
        return [(clip_name, clip) for where, ch, clip_name, clip in self._actor_clips.get(name.lower(), ())
                if ch == channels and (where is None or where & levels)]

    def _scan_pool(self) -> None:
        shared: Dict[Tuple[str, int], Dict[str, bytes]] = {}
        refs = self.pool_refs()
        model_levels: Dict[str, set] = {}  # pool model name (lower case) -> levels
        casts = []  # (levels, cast, the actors' clips by lower case name)
        for key in self.data_pool or ():
            files = self._read_pool_package(key)
            levels = {level_of(ref) for ref in refs.get(key, ())}
            for path in files:
                if path.endswith("_ge"):
                    model_levels.setdefault(path[:-3].rsplit("/", 1)[-1].lower(), set()).update(levels)
                elif path.endswith("_cs"):
                    cast = cutscene_cast(files[path])
                    wanted = {clip.lower() for _, _, clip in cast if clip}
                    clips = {name.lower(): (name, data) for name, data in
                             ((p[:-3].rsplit("/", 1)[-1], d) for p, d in files.items() if p.endswith("_an"))
                             if name.lower() in wanted}
                    casts.append((levels, cast, clips))
            if not files or any(path.endswith("_ge") for path in files):
                continue
            for path, clip in files.items():
                channels = clip_channels(clip) if path.endswith("_an") else None
                if channels is None:
                    continue
                for ref in refs.get(key, ()):
                    shared.setdefault((level_of(ref), channels), {})[path[:-3].rsplit("/", 1)[-1]] = clip
        self._shared_clips = {k: sorted(v.items()) for k, v in shared.items()}
        # An actor plays its clip on the model of that name in the cutscene's level, or,
        # when the level has none in the pool (player characters), on every model of that name.
        actors: Dict[str, Dict[str, Tuple[Optional[set], int, bytes]]] = {}
        for levels, cast, clips in casts:
            for _locator, model, clip in cast:
                if not clip or clip.lower() not in clips:
                    continue
                clip_name, data = clips[clip.lower()]
                channels = clip_channels(data)
                where = levels if model_levels.get(model.lower(), set()) & levels else None
                known = actors.setdefault(model.lower(), {})
                if clip_name in known and known[clip_name][2] != data:
                    clip_name = f"{clip_name}_{sorted(levels)[0].rsplit('/', 1)[-1] if levels else 'pool'}"
                if clip_name in known:
                    old = known[clip_name][0]
                    where = None if old is None or where is None else old | where
                known[clip_name] = (where, channels, data)
        self._actor_clips = {model: [(where, ch, name, data) for name, (where, ch, data) in sorted(clips.items())]
                             for model, clips in actors.items()}

    def iter_models(self, filt: Optional[Callable[[str], bool]] = None) -> Iterator[ModelParts]:
        """Every model on the disc. A ``bData.pool`` package is listed once, with the
        resource that references it first; a filter may match any of its references."""
        refs = self.pool_refs()
        entries = list(self.iter_entries())
        unreferenced = [key for key in self.data_pool or () if key not in refs]
        total = len(entries) + len(unreferenced)
        for done, (archive, entry) in enumerate(entries):
            self.position = (done, total)
            label = f"{self.archive_label(archive)}/{entry.name}"
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                if entry.name.endswith("_ge") and (filt is None or filt(label[:-3])):
                    yield self._loose_model(archive, entry)
                continue
            files = {p.path: data[p.offset:p.offset + p.length] for p in pkg if not p.attrib & FOLDER}
            groups = [(files, [label], True)]  # package files, labels to filter on, own package
            for path, part in files.items():
                ref = f"{label}/{path[:-5]}"
                if path.endswith("_dref") and refs.get(part, [None])[0] == ref:
                    groups.append((self._read_pool_package(part), refs[part], False))
            envmap = envmap_faces(files)
            for model in self._package_models(groups, archive, entry, filt):
                model.envmap = envmap
                yield model
        for done, key in enumerate(unreferenced, len(entries)):
            self.position = (done, total)
            files = self._read_pool_package(key)
            stems = sorted(path[:-3] for path in files if path.endswith("_ge"))
            if stems:
                yield from self._package_models([(files, [f"bData/{stems[0]}"], False)], None,
                                                Entry(stems[0], 0, 0, 0, 0), filt, "bData")
        self.position = (total, total)

    def iter_tile_levels(self, filt: Optional[Callable[[str], bool]] = None) -> Iterator[TileLevel]:
        """Level resources with a tile map, matched by ``filt`` on ``archive/entry/tiles``."""
        for archive, entry in self.iter_entries():
            label = f"{self.archive_label(archive)}/{entry.name}"
            if entry.name.endswith(("_tpi", "_tpd")) or (filt is not None and not filt(f"{label}/tiles")):
                continue
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                continue
            files = {p.path: data[p.offset:p.offset + p.length] for p in pkg if not p.attrib & FOLDER}
            if "tmap" not in files or "bmap" not in files:
                continue
            level = TileLevel(self.archive_label(archive), entry.name, files["tmap"], files["bmap"])
            sources = self._level_sources(archive, entry, files)
            for source in sources:
                for path in source:
                    stem = path[:-3]
                    name = stem.rsplit("/", 1)[-1]
                    if path.endswith("_ge") and TILE_MODEL.match(name) and name not in level.models:
                        level.models[name] = {sfx: source[f"{stem}_{sfx}"] for sfx in ("su", "ge", "si", "nm", "lm")
                                              if f"{stem}_{sfx}" in source}
            yield level

    def _level_sources(self, archive: Archive, entry: Entry, files: Dict[str, bytes]) -> List[Dict[str, bytes]]:
        """A level resource's package, the other resources of its level (the tiles of
        ``X_lvl/01`` may be referenced by ``X_lvl/00``) and the pool packages they reference."""
        directory = entry.name.split("/")[0] + "/"
        siblings = [files]
        for other in archive.files():
            if other.name.startswith(directory) and other.name != entry.name and not other.name.endswith(("_tpi", "_tpd")):
                other_data = self.read_entry(archive, other)
                siblings.append({p.path: other_data[p.offset:p.offset + p.length]
                                 for p in parse_package(other_data) or () if not p.attrib & FOLDER})
        sources = []
        for package in siblings:
            sources.append(package)
            sources += [self._read_pool_package(part) for path, part in package.items()
                        if path.endswith("_dref") and self.data_pool is not None and part in self.data_pool]
        return sources

    def iter_object_levels(self, filt: Optional[Callable[[str], bool]] = None) -> Iterator[ObjectLevel]:
        """Level resources with placed objects, matched by ``filt`` on ``archive/entry/objects``."""
        for archive, entry in self.iter_entries():
            label = f"{self.archive_label(archive)}/{entry.name}"
            if entry.name.endswith(("_tpi", "_tpd")) or (filt is not None and not filt(f"{label}/objects")):
                continue
            data = self.read_entry(archive, entry)
            pkg = parse_package(data)
            if not pkg:
                continue
            files = {p.path: data[p.offset:p.offset + p.length] for p in pkg if not p.attrib & FOLDER}
            objects = parse_dat(files.get("dat", b""), self.game)
            if not objects:
                continue
            level = ObjectLevel(self.archive_label(archive), entry.name, objects)
            available: Dict[str, Tuple[Dict[str, bytes], str]] = {}
            for source in self._level_sources(archive, entry, files):
                for path in source:
                    if path.endswith("_ge"):
                        available.setdefault(path[:-3].rsplit("/", 1)[-1].lower(), (source, path[:-3]))
            for obj in objects:
                name = self._object_model(obj, available)
                if name is None or name in level.models:
                    continue
                if name in available:
                    source, stem = available[name]
                    level.models[name] = {sfx: source[f"{stem}_{sfx}"] for sfx in ("su", "ge", "si", "nm")
                                          if f"{stem}_{sfx}" in source}
                else:
                    level.models[name] = self._outside_model(name)
            yield level

    def _object_model(self, obj: Placed, available) -> Optional[str]:
        """The model an object shows: the first of its names (flight variant first) the
        level has, else one found outside the levels (``data``)."""
        names = [n.lower() for n in obj.names]
        for name in names:
            if name in available:
                return name
        outside = self.outside_models()
        return next((name for name in names if name in outside), None)

    def outside_models(self) -> Dict[str, Tuple[Archive, Entry, str]]:
        """Models of packages outside the levels (``data/plCraft``, ``data/staticGame``), by lower case name."""
        if self._outside_models is None:
            self._outside_models = {}
            for archive, entry in self.iter_entries():
                if "_lvl/" in entry.name or entry.name.endswith(("_tpi", "_tpd")):
                    continue
                pkg = parse_package(self.read_entry(archive, entry))
                for p in pkg or ():
                    if p.path.endswith("_ge"):
                        self._outside_models.setdefault(p.path[:-3].rsplit("/", 1)[-1].lower(),
                                                        (archive, entry, p.path[:-3]))
        return self._outside_models

    def _outside_model(self, name: str) -> Dict[str, bytes]:
        archive, entry, stem = self.outside_models()[name]
        data = self.read_entry(archive, entry)
        files = {p.path: data[p.offset:p.offset + p.length] for p in parse_package(data) if not p.attrib & FOLDER}
        return {sfx: files[f"{stem}_{sfx}"] for sfx in ("su", "ge", "si", "nm") if f"{stem}_{sfx}" in files}

    def _package_models(self, groups, archive: Optional[Archive], entry: Entry, filt,
                        archive_label: Optional[str] = None) -> Iterator[ModelParts]:
        archive_label = archive_label or self.archive_label(archive)
        models = [(stem, files, labels, own) for files, labels, own in groups
                  for stem in (path[:-3] for path in files if path.endswith("_ge"))]
        # output may land on a case-insensitive file system
        lowered = [stem.rsplit("/", 1)[-1].lower() for stem, *_ in models]
        sibling: Optional[List[Tuple[str, bytes]]] = None
        clips_of: Dict[int, Dict[str, List[Tuple[str, bytes]]]] = {}
        for (stem, files, labels, own), low in zip(models, lowered):
            name = stem.rsplit("/", 1)[-1]
            # Same model name twice in one resource: keep the folder or the reference in the name.
            if lowered.count(low) > 1:
                name = stem.replace("/", "__") if own else f"{labels[0].rsplit('/', 1)[-1]}__{name}"
            listed = f"{archive_label}/{entry.name}/{stem}"  # as ``list`` prints it
            if filt is not None and not (filt(listed) or any(filt(f"{lb}/{stem}") for lb in labels)):
                continue
            parts = {sfx: files[stem + "_" + sfx] for sfx in ("su", "ge", "si", "an", "cl", "nm")
                     if stem + "_" + sfx in files}
            if id(files) not in clips_of:
                clips_of[id(files)] = self._package_clips(files)
            model_clips = list(clips_of[id(files)].get(stem.rpartition("/")[0], []))
            if own and "su" in parts and archive is not None:
                if sibling is None:
                    sibling = self._sibling_clips(archive, entry)
                model_clips += sibling
            if "su" in parts and len(parts["su"]) >= 36:
                channels = struct.unpack_from(">I", parts["su"], 32)[0]
                seen = {clip_name for clip_name, _ in model_clips}
                for level in dict.fromkeys(level_of(lb) for lb in labels):
                    for clip_name, clip in self.shared_clips().get((level, channels), ()):
                        if clip_name not in seen:
                            seen.add(clip_name)
                            model_clips.append((clip_name, clip))
                levels = {level_of(lb) for lb in labels}
                for clip_name, clip in self.actor_clips(stem.rsplit("/", 1)[-1], channels, levels):
                    if clip_name not in seen:
                        seen.add(clip_name)
                        model_clips.append((clip_name, clip))
            yield ModelParts(name, archive_label, entry.name, parts, model_clips)


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


def write_envmap(faces: Dict[str, bytes], folder: str, texture_format: str) -> int:
    """Write an environment cube as ``folder/<face>`` images (opaque) and an
    equirectangular ``folder/panorama``; returns 1 when written."""
    images = {face: decode_image_resource(data) for face, data in faces.items()}
    if any(img is None for img in images.values()):
        return 0
    os.makedirs(folder, exist_ok=True)
    for face, img in images.items():
        save_image(opaque(img), os.path.join(folder, f"{face}.{texture_format}"), texture_format)
    save_image(panorama(images), os.path.join(folder, f"panorama.{texture_format}"), texture_format)
    return 1


def export_images(game: GameData, out_dir: str, filt=None, texture_format: str = "png",
                  progress: Optional[Callable[[str, float], None]] = None) -> int:
    """Write every standalone image resource under ``out_dir/<archive>/images``.
    ``progress(label, fraction done)`` is called for every entry looked at. Six
    ``envmap/<name>/<face>`` images (RS2) also give an ``envmap/<name>/panorama``."""
    count = 0
    cubes: Dict[str, Dict[str, bytes]] = {}
    entries = list(game.iter_entries(filt))
    for done, (archive, entry) in enumerate(entries):
        if entry.name.endswith(("_tpi", "_tpd")):
            continue
        if progress is not None:
            progress(f"{game.archive_label(archive)}/{entry.name}", done / len(entries))
        data = game.read_entry(archive, entry)
        if parse_package(data):
            continue
        img = decode_image_resource(data)
        if img is None:
            continue
        parts = [safe_name(p) for p in entry.name.split("/")]
        target = os.path.join(out_dir, safe_name(game.archive_label(archive)), "images", *parts) + "." + texture_format
        os.makedirs(os.path.dirname(target), exist_ok=True)
        folder, _, face = entry.name.rpartition("/")
        if face in FACES and folder.lower().startswith("envmap/"):
            img = opaque(img)  # the transparent border only matters to the game's sphere map render
            cubes.setdefault(os.path.dirname(target), {})[face] = data
        save_image(img, target, texture_format)
        count += 1
    for folder, faces in cubes.items():
        if len(faces) == len(FACES):
            images = {face: decode_image_resource(data) for face, data in faces.items()}
            save_image(panorama(images), os.path.join(folder, f"panorama.{texture_format}"), texture_format)
            count += 1
    return count


def lod_labels(meshes: List[Mesh]) -> List[Optional[str]]:
    """Name the LOD band of every mesh, e.g. ``LOD0`` or ``LOD2_3`` for a mesh shown in
    both LOD2 and LOD3; None for meshes that are always drawn.

    Levels are the near indices of each LOD table entry's chain. Models with a single
    level only fade out with distance and get no labels.
    """
    top: Dict[int, int] = {}
    for mesh in meshes:
        if mesh.lod_entry is not None:
            top[mesh.lod_entry] = max(top.get(mesh.lod_entry, 0), mesh.lod_near)
    if not any(top.values()):
        return [None] * len(meshes)
    labels: List[Optional[str]] = []
    for mesh in meshes:
        if mesh.lod_entry is None:
            labels.append(None)
            continue
        last = top[mesh.lod_entry] if mesh.lod_far == 0xFF else max(mesh.lod_far - 1, mesh.lod_near)
        if mesh.lod_near == 0 and last >= top[mesh.lod_entry]:
            labels.append(None)
        elif last == mesh.lod_near:
            labels.append(f"LOD{mesh.lod_near}")
        else:
            labels.append(f"LOD{mesh.lod_near}_{last}")
    return labels


def _transform(positions: np.ndarray, normals: Optional[np.ndarray], m: np.ndarray):
    """Apply a 4x4 matrix to positions and (renormalised) normals."""
    if np.allclose(m, np.eye(4), atol=1e-9):
        return positions, normals
    positions = positions @ m[:3, :3].T + m[:3, 3]
    if normals is not None:
        n = normals.astype(np.float64) @ np.linalg.inv(m[:3, :3])
        lens = np.linalg.norm(n, axis=1, keepdims=True)
        normals = (n / np.where(lens > 1e-9, lens, 1.0)).astype(np.float32)
    return positions, normals


def _wind(triangles: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Triangles kept front facing under ``m``: a mirroring matrix reverses their winding."""
    return triangles[:, [0, 2, 1]] if np.linalg.det(m[:3, :3]) < 0 else triangles


def _merge_copies(mesh: Mesh, matrices: List[np.ndarray], name: str, material: int, node: int) -> SceneMesh:
    """One scene mesh holding a copy of ``mesh`` transformed by each matrix."""
    positions = np.asarray(mesh.positions, np.float64)
    n = len(positions)
    pos, nrm = [], []
    for m in matrices:
        pos.append(positions @ m[:3, :3].T + m[:3, 3])
        if mesh.normals is not None:
            nrm.append(np.asarray(mesh.normals, np.float32) @ m[:3, :3].T.astype(np.float32))
    copies = len(matrices)
    uvs = [mesh.uvs] + list(mesh.extra_uvs) if mesh.uvs is not None else []
    return SceneMesh(
        name=name,
        positions=np.concatenate(pos),
        triangles=np.concatenate([_wind(mesh.triangles, m) + k * n for k, m in enumerate(matrices)]),
        normals=np.concatenate(nrm) if nrm else None,
        uvs=[np.tile(uv, (copies, 1)) for uv in uvs],
        colors=np.tile(mesh.colors, (copies, 1)) if mesh.colors is not None else None,
        material=material,
        node=node,
    )


class Exporter:
    def __init__(self, game: GameData, out_dir: str, embed_textures: bool = False,
                 texture_format: str = "png", skeletons: bool = True, runtime: Optional[Runtime] = None,
                 animations: bool = True, pool=None, log: Log = print):
        self.g = game
        self.out_dir = out_dir
        self.embed = embed_textures
        self.texture_format = texture_format
        self.skeletons = skeletons
        self.animations = animations
        self.runtime = runtime
        self.pool = pool
        self.log = log
        self.written_textures: Dict[str, str] = {}
        self.stats = {"models": 0, "skipped": 0, "textures": 0, "normal_maps": 0, "emissive_maps": 0,
                      "specular_maps": 0, "environment_maps": 0, "tile_levels": 0, "object_levels": 0, "missing_textures": 0,
                      "skeletons": 0, "animations": 0, "wireframes": 0}
        self.missing: List[str] = []
        self.missing_hashes: set = set()
        self.envmap_folders: set = set()

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
        if m.envmap and folder not in self.envmap_folders:
            self.envmap_folders.add(folder)
            self.stats["environment_maps"] += write_envmap(m.envmap, os.path.join(folder, "envmap"),
                                                           self.texture_format)
        scene = Scene(m.name)
        base = safe_name(m.name)

        nodes = Rig.build(m.parts.get("su"), m.parts["ge"], self.g.game, m.parts.get("nm"))
        rig = nodes if self.skeletons else None
        root = 0
        if rig is not None:
            root = rig.add_bones(scene, base)
            if self.animations:
                clips = list(m.clips)
                if "an" in m.parts:
                    clips.insert(0, (m.name, m.parts["an"]))
                self.stats["animations"] += add_animations(scene, rig, root, clips, self.runtime, self.log,
                                                           self.pool)
            if not (scene.animations or rig.has_joints or rig.is_skinned(geo.meshes)):
                rig = None
                scene = Scene(m.name)
        layout = None
        if rig is None:
            scene.nodes.append(SceneNode(base))
            if nodes is not None:
                layout = nodes.part_layout(geo.meshes)
            else:
                place_static_meshes(geo.meshes, m.parts.get("su"), m.parts["ge"], self.g.game)
        else:
            self.stats["skeletons"] += 1
        # One null per LOD band so each level can be shown or hidden as a group.
        labels = lod_labels(geo.meshes)
        lod_nodes: Dict[str, int] = {}
        for label in sorted({lb for lb in labels if lb}, key=lambda lb: [int(x) for x in lb[3:].split("_")]):
            lod_nodes[label] = len(scene.nodes)
            scene.nodes.append(SceneNode(f"{base}_{label}", parent=-1 if rig is not None else 0))
        part_nodes: Dict[int, int] = {}
        if layout is not None:
            part_nodes = self._add_parts(scene, nodes, layout, labels, lod_nodes, base)
        mat_map: Dict[int, int] = {}
        for mesh_no, (mesh, label) in enumerate(zip(geo.meshes, labels)):
            if mesh.material_index not in mat_map:
                mat_map[mesh.material_index] = len(scene.materials)
                mat = materials[mesh.material_index] if mesh.material_index < len(materials) else None
                scene.materials.append(self._material(mat, mesh.material_index, folder, base))
            uvs = [mesh.uvs] + list(mesh.extra_uvs) if mesh.uvs is not None else []
            positions, normals = mesh.positions.astype(np.float64), mesh.normals
            triangles = mesh.triangles
            bones = weights = None
            parent = lod_nodes[label] if label else (-1 if rig is not None else 0)
            if rig is not None:
                positions, normals, bones, weights = rig.skin(mesh, root)
            elif layout is not None:
                node, matrix = layout[0][mesh_no], layout[1][mesh_no]
                positions, normals = _transform(positions, normals, matrix)
                triangles = _wind(triangles, matrix)
                if node >= 0:
                    parent = part_nodes[node]
            scene.meshes.append(SceneMesh(
                name=f"{base}_{mesh_no:02d}" + (f"_{label}" if label else ""),
                positions=positions,
                triangles=triangles,
                normals=normals,
                uvs=uvs,
                colors=mesh.colors,
                material=mat_map[mesh.material_index],
                node=parent,
                skin_bones=bones,
                skin_weights=weights,
            ))
        if "cl" in m.parts and WIRE_OVERLAY.search(m.name):
            self.stats["wireframes"] += self._add_wireframe(scene, m.parts["cl"], base, -1 if rig is not None else 0)
        path = os.path.join(folder, base + ".fbx")
        write_fbx(scene, path, embed_textures=self.embed)
        self.stats["models"] += 1
        return path

    @staticmethod
    def _add_wireframe(scene: Scene, cl: bytes, base: str, parent: int) -> bool:
        """The collision hull as its own mesh with the game's polygons (n-gons, so a
        wireframe view shows only the game's edges) and flat plane normals."""
        hull = parse_collision(cl)
        if not hull.polygons:
            return False
        sizes = [len(poly) for poly in hull.polygons]
        corners = np.array([v for poly in hull.polygons for v in poly])
        starts = np.cumsum([0] + sizes[:-1])
        scene.materials.append(SceneMaterial("wireframe"))
        scene.meshes.append(SceneMesh(
            name=f"{base}_wire",
            positions=hull.positions[corners],
            triangles=np.zeros((0, 3), dtype=np.int32),
            normals=np.repeat(hull.normals, sizes, axis=0),
            material=len(scene.materials) - 1,
            node=parent,
            polygons=[range(s, s + n) for s, n in zip(starts, sizes)],
        ))
        return True

    def export_tile_level(self, level: TileLevel) -> Optional[str]:
        """One FBX with the whole tile layout: every tile and building copied into
        place (full detail only). Copies of the same mesh are merged into one mesh per
        tile or building type, so the scene has a few hundred objects, not one per cell."""
        grids = parse_tmap(level.tmap)
        buildings = parse_bmap(level.bmap)
        if not grids:
            return None
        folder = os.path.join(self.out_dir, safe_name(level.archive), *[safe_name(p) for p in level.entry.split("/")])
        os.makedirs(folder, exist_ok=True)
        base = safe_name(f"{level.entry.split('/')[0]}_{level.entry.split('/')[-1]}_tiles")
        scene = Scene(base)
        scene.nodes.append(SceneNode(base))
        groups = {"t": len(scene.nodes)}
        scene.nodes.append(SceneNode(f"{base}_tiles", parent=0))
        groups["b"] = len(scene.nodes)
        scene.nodes.append(SceneNode(f"{base}_buildings", parent=0))
        protos: Dict[str, list] = {}
        placed: Dict[Tuple[str, int], List[np.ndarray]] = {}

        def place(name: str, matrix: np.ndarray) -> None:
            if name not in protos:
                protos[name] = self._prototype(level.models.get(name), name, folder, scene)
            for k in range(len(protos[name])):
                placed.setdefault((name, k), []).append(matrix)

        for grid in grids:
            for tile_type, matrix in tile_cells(grid):
                tile = f"t{tile_type:03d}_0"
                place(tile, matrix)
                spots = parse_lm(level.models.get(tile, {}).get("lm", b""))
                for building, spot in zip(buildings[tile_type] if tile_type < len(buildings) else [], spots):
                    place(f"b{building:03d}_0", matrix @ spot)
        for (name, k), matrices in placed.items():
            mesh, material = protos[name][k]
            scene.meshes.append(_merge_copies(mesh, matrices, f"{name}_{k:02d}", material, groups[name[0]]))
        if not scene.meshes:
            return None
        path = os.path.join(folder, base + ".fbx")
        write_fbx(scene, path, embed_textures=self.embed)
        self.stats["tile_levels"] += 1
        return path

    def export_object_level(self, level: ObjectLevel) -> Optional[str]:
        """One FBX with the objects a level resource places (``dat``): every placed model
        copied into place at full detail, copies of the same mesh merged into one mesh,
        under one empty per model."""
        folder = os.path.join(self.out_dir, safe_name(level.archive), *[safe_name(p) for p in level.entry.split("/")])
        os.makedirs(folder, exist_ok=True)
        base = safe_name(f"{level.entry.split('/')[0]}_{level.entry.split('/')[-1]}_objects")
        scene = Scene(base)
        scene.nodes.append(SceneNode(base))
        protos: Dict[str, list] = {}
        groups: Dict[str, int] = {}
        placed: Dict[Tuple[str, int], List[np.ndarray]] = {}
        for obj in level.objects:
            name = self.g._object_model(obj, level.models)
            if name is None:
                continue
            if name not in protos:
                protos[name] = self._prototype(level.models.get(name), name, folder, scene)
                groups[name] = len(scene.nodes)
                scene.nodes.append(SceneNode(safe_name(name), parent=0))
            for k in range(len(protos[name])):
                placed.setdefault((name, k), []).append(obj.matrix)
        for (name, k), matrices in placed.items():
            mesh, material = protos[name][k]
            scene.meshes.append(_merge_copies(mesh, matrices, f"{safe_name(name)}_{k:02d}", material, groups[name]))
        if not scene.meshes:
            return None
        path = os.path.join(folder, base + ".fbx")
        write_fbx(scene, path, embed_textures=self.embed)
        self.stats["object_levels"] += 1
        return path

    def _prototype(self, parts: Optional[Dict[str, bytes]], name: str, folder: str,
                   scene: Scene) -> List[Tuple[Mesh, int]]:
        """Full detail meshes of a model in model space (rigid parts at rest, skins in
        bind pose), with their scene materials."""
        if not parts or "ge" not in parts:
            self.log(f"  model {name} not found")
            return []
        geo = parse_geometry(parts["ge"], self.g.game, self.g.normal_table)
        materials = parse_materials(parts.get("si", b""), self.g.game)
        nodes = Rig.build(parts.get("su"), parts["ge"], self.g.game, parts.get("nm"))
        if nodes is not None:
            mesh_nodes, matrices, frames = nodes.part_layout(geo.meshes)
            for mesh, node, matrix in zip(geo.meshes, mesh_nodes, matrices):
                if node < 0 and nodes.is_skinned([mesh]):
                    mesh.positions, mesh.normals = nodes.skin(mesh, 0)[:2]
                    continue
                world = frames[node] @ matrix if node >= 0 else matrix
                mesh.positions, mesh.normals = _transform(mesh.positions.astype(np.float64), mesh.normals, world)
                mesh.triangles = _wind(mesh.triangles, world)
        else:
            place_static_meshes(geo.meshes, parts.get("su"), parts["ge"], self.g.game)
        out: List[Tuple[Mesh, int]] = []
        mat_map: Dict[int, int] = {}
        for mesh, label in zip(geo.meshes, lod_labels(geo.meshes)):
            if label and not label.startswith("LOD0"):
                continue
            if mesh.material_index not in mat_map:
                mat_map[mesh.material_index] = len(scene.materials)
                mat = materials[mesh.material_index] if mesh.material_index < len(materials) else None
                sm = self._material(mat, mesh.material_index, folder, name)
                sm.name = f"{name}_{sm.name}"
                scene.materials.append(sm)
            out.append((mesh, mat_map[mesh.material_index]))
        return out

    @staticmethod
    def _add_parts(scene: Scene, rig: Rig, layout, labels, lod_nodes: Dict[str, int], base: str) -> Dict[int, int]:
        """Add one empty per part node (on its pivot); returns node -> scene index.
        A top-level part goes under its LOD empty when all its meshes share one
        LOD band, otherwise under the model root."""
        mesh_nodes, _matrices, frames = layout
        subtree: Dict[int, set] = {n: set() for n in frames}
        for node, label in zip(mesh_nodes, labels):
            while node >= 0:
                subtree[node].add(label)
                node = rig.nodes[node].parent
        index: Dict[int, int] = {}
        for n in sorted(frames):
            parent = rig.nodes[n].parent
            if parent >= 0:
                scene_parent, parent_frame = index[parent], frames[parent]
            else:
                bands = subtree[n]
                band = next(iter(bands)) if len(bands) == 1 else None
                scene_parent = lod_nodes[band] if band else 0
                parent_frame = np.eye(4)
            index[n] = len(scene.nodes)
            scene.nodes.append(SceneNode(f"{base}_{rig.names[n]}", parent=scene_parent,
                                         matrix=np.linalg.inv(parent_frame) @ frames[n]))
        return index

    def _material(self, mat: Optional[Material], index: int, folder: str, model: str) -> SceneMaterial:
        """Scene material; its texture files are named ``<model>_<material>_<sha1 prefix>[_kind]``."""
        if mat is None:
            return SceneMaterial(f"material{index}")
        name = f"{model}_{mat.name}"
        sm = SceneMaterial(safe_name(mat.name), diffuse=tuple(mat.color0), wrap_u=not mat.clamp_s,
                           wrap_v=not mat.clamp_t, reflection=mat.reflection)
        if mat.specular:
            sm.specular, sm.specular_factor = tuple(mat.color1[:3]), 1.0
            sm.shininess = specular_exponent(mat.shininess)
            if mat.specular_masked and mat.textures:
                tint = mat.color1
                variant = bytes(round(c * 255) for c in tint[:3]).hex()
                sm.specular_map = self._texture_file(mat.textures[0], folder, name, "specular",
                                                     lambda img: specular(img, tint), variant)
        if mat.textures:
            # Keep alpha only where it is transparency; a CMPR color map's 1 bit alpha is
            # otherwise a mask for another slot (written as its own map below) or unused.
            ref = mat.textures[0]
            convert, variant = None, ""
            if mat.opacity is not None:
                index, alpha = mat.opacity
                if index != 0 or not alpha:
                    mask = mat.textures[index]
                    convert = lambda img: self._set_alpha(img, mask, alpha, name)
                    variant = f"_alpha{mask.hash.hex()}{alpha:d}"
            elif ref.format not in ALPHA_FORMATS:
                convert, variant = opaque, "_opaque"
            rel = self._texture_file(ref, folder, name, "", convert, variant)
            if rel:
                sm.texture = rel
                sm.alpha_texture = mat.opacity is not None or ref.format in ALPHA_FORMATS
        if mat.bump is not None:
            index, kind, alpha = mat.bump
            ref = mat.textures[index]
            if kind == TILT:
                sm.normal_map = self._texture_file(ref, folder, name, "normal",
                                                   lambda img: tilt_to_normal(img, self.g.game))
            else:
                variant = "a" if alpha else "i"
                self._texture_file(ref, folder, name, "height", lambda img: gray(channel(img, alpha)), variant)
                sm.normal_map = self._texture_file(ref, folder, name, "normal",
                                                   lambda img: height_to_normal(channel(img, alpha)), variant)
        if mat.self_lit is not None:
            sm.emissive_map = self._emissive_file(mat, folder, name)
        return sm

    def _set_alpha(self, img: np.ndarray, mask_ref, alpha: bool, name: str) -> np.ndarray:
        mask = self._decode(mask_ref, name)
        if mask is None:
            return img
        m = channel(mask, alpha)
        if m.shape != img.shape[:2]:
            m = np.asarray(Image.fromarray(m, "F").resize((img.shape[1], img.shape[0]), Image.BILINEAR))
        out = img.copy()
        out[..., 3] = np.clip(np.rint(m * 255.0), 0, 255).astype(np.uint8)
        return out

    def _decode(self, ref, name: str) -> Optional[np.ndarray]:
        img = self.g.textures.decode(ref)
        if img is None and ref.hash not in self.missing_hashes:
            self.missing_hashes.add(ref.hash)
            self.stats["missing_textures"] += 1
            self.missing.append(f"{name} {ref.width}x{ref.height} fmt {ref.format} "
                                f"{ref.hash.hex()[:8]} {'(not in any pool)' if ref.hash not in self.g.textures else ''}")
        return img

    def _texture_file(self, ref, folder: str, name: str, kind: str = "",
                      convert: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                      variant: str = "") -> Optional[str]:
        """Write the texture (``convert``ed, saved with a ``_kind`` suffix) into ``folder/textures``
        and return its relative path. ``variant`` tells apart conversions with the same kind."""
        suffix = f"_{kind}" if kind else ""

        def make() -> Optional[np.ndarray]:
            img = self._decode(ref, name)
            return img if img is None or convert is None else convert(img)
        return self._image_file(folder, f"{safe_name(name)}_{ref.hash.hex()[:8]}{suffix}",
                                ref.hash.hex() + suffix + variant, kind, make)

    def _emissive_file(self, mat: Material, folder: str, name: str) -> Optional[str]:
        """Color map where its self lit mask is 0, black elsewhere."""
        base = mat.textures[0]
        index, alpha = mat.self_lit
        mask_ref = mat.textures[index]
        key = f"{base.hash.hex()}_emissive{mask_ref.hash.hex()}{alpha:d}"

        def make() -> Optional[np.ndarray]:
            mask = self._decode(mask_ref, name)
            if mask is None:
                return None
            return emissive(self._decode(base, name), 1.0 - channel(mask, alpha))
        return self._image_file(folder, f"{safe_name(name)}_{base.hash.hex()[:8]}_emissive", key,
                                "emissive", make)

    def _image_file(self, folder: str, stem: str, key: str, kind: str,
                    make: Callable[[], Optional[np.ndarray]]) -> Optional[str]:
        """Write ``make()`` as ``folder/textures/stem`` once per ``key`` (later uses link or copy the file)."""
        fname = f"{stem}.{self.texture_format}"
        rel = f"textures/{fname}"
        target = os.path.join(folder, "textures", fname)
        if os.path.exists(target):
            return rel
        if key not in self.written_textures:
            img = make()
            if img is None:
                self.written_textures[key] = ""
                return None
            os.makedirs(os.path.dirname(target), exist_ok=True)
            save_image(img, target, self.texture_format)
            self.written_textures[key] = target
            self.stats[{"normal": "normal_maps", "emissive": "emissive_maps",
                        "specular": "specular_maps"}.get(kind, "textures")] += 1
            return rel
        source = self.written_textures[key]
        if not source:
            return None
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:  # a hard link keeps every folder complete without storing the image twice
            os.link(source, target)
        except OSError:
            with open(source, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
        return rel
