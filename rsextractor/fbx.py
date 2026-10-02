"""Minimal binary FBX 7.4 writer (meshes, materials, textures, skeletons, skins, animation).

Encoding follows the public description of the binary format (as implemented
by Blender's ``encode_bin.py``): 32-bit node headers for version 7400, a
13-byte null record closing nested lists, and the fixed footer.
"""
from __future__ import annotations

import math
import os
import struct
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .scene import Scene, SceneMesh

FBX_VERSION = 7400
_HEAD_MAGIC = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
# FileId/CreationTime pair accepted by the FBX SDK (same values Blender writes).
_FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
_CREATION_TIME = "1970-01-01 10:00:00:000"
_NULL_RECORD = b"\0" * 13
_ALWAYS_SENTINEL = {"AnimationStack", "AnimationLayer"}
_KTIME_PER_SECOND = 46186158000


# --------------------------------------------------------------------------- encoding

class Prop:
    __slots__ = ("code", "value")

    def __init__(self, code: str, value):
        self.code = code
        self.value = value

    def encode(self) -> bytes:
        c, v = self.code, self.value
        if c == "C":
            return b"C" + struct.pack("<?", bool(v))
        if c == "Y":
            return b"Y" + struct.pack("<h", v)
        if c == "I":
            return b"I" + struct.pack("<i", v)
        if c == "L":
            return b"L" + struct.pack("<q", v)
        if c == "F":
            return b"F" + struct.pack("<f", v)
        if c == "D":
            return b"D" + struct.pack("<d", v)
        if c in ("S", "R"):
            data = v.encode("utf-8") if isinstance(v, str) else bytes(v)
            return c.encode() + struct.pack("<I", len(data)) + data
        if c in ("i", "l", "f", "d", "b"):
            dtype = {"i": "<i4", "l": "<i8", "f": "<f4", "d": "<f8", "b": "<u1"}[c]
            arr = np.ascontiguousarray(np.asarray(v, dtype=dtype).ravel())
            raw = arr.tobytes()
            if len(raw) > 128:
                packed = zlib.compress(raw, 6)
                return c.encode() + struct.pack("<III", len(arr), 1, len(packed)) + packed
            return c.encode() + struct.pack("<III", len(arr), 0, len(raw)) + raw
        raise ValueError(c)


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name: str, *props: Prop):
        self.name = name
        self.props: List[Prop] = list(props)
        self.children: List["Node"] = []

    def add(self, name: str, *props: Prop) -> "Node":
        child = Node(name, *props)
        self.children.append(child)
        return child

    def encode(self, offset: int) -> bytes:
        props = b"".join(p.encode() for p in self.props)
        name = self.name.encode("ascii")
        header_len = 13 + len(name)
        body = bytearray()
        pos = offset + header_len + len(props)
        for child in self.children:
            enc = child.encode(pos)
            body += enc
            pos += len(enc)
        if self.children or (not self.props and self.name not in _ALWAYS_SENTINEL):
            body += _NULL_RECORD
        end = offset + header_len + len(props) + len(body)
        return struct.pack("<IIIB", end, len(self.props), len(props), len(name)) + name + props + bytes(body)


def S(v: str) -> Prop: return Prop("S", v)
def I(v: int) -> Prop: return Prop("I", v)
def L(v: int) -> Prop: return Prop("L", v)
def D(v: float) -> Prop: return Prop("D", v)
def C(v: bool) -> Prop: return Prop("C", v)


def _p70(parent: Node, name: str, ptype: str, label: str, flags: str, *values) -> None:
    props = [S(name), S(ptype), S(label), S(flags)]
    for v in values:
        if isinstance(v, bool):
            props.append(I(int(v)))
        elif isinstance(v, int):
            props.append(I(v) if ptype not in ("KTime", "ULongLong") else L(v))
        elif isinstance(v, float):
            props.append(D(v))
        else:
            props.append(S(str(v)))
    parent.add("P", *props)


def _name(name: str, cls: str) -> str:
    return f"{name}\x00\x01{cls}"


def _flat(m: np.ndarray) -> List[float]:
    """4x4 column-vector matrix -> FBX column-major double array."""
    return [float(x) for x in np.asarray(m, dtype=np.float64).T.ravel()]


def _decompose(m: np.ndarray) -> Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]:
    """Matrix -> (translation, euler XYZ degrees, scale), FBX rotation order XYZ."""
    t = m[:3, 3]
    rs = m[:3, :3]
    s = np.linalg.norm(rs, axis=0)
    s[s == 0] = 1.0
    r = rs / s
    if np.linalg.det(r) < 0:
        s[0] = -s[0]
        r[:, 0] = -r[:, 0]
    # R = Rz * Ry * Rx (XYZ extrinsic order used by FBX "eEulerXYZ")
    sy = -r[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.999999:
        rx = math.atan2(r[2, 1], r[2, 2])
        rz = math.atan2(r[1, 0], r[0, 0])
    else:
        rx = math.atan2(-r[1, 2], r[1, 1])
        rz = 0.0
    return tuple(map(float, t)), tuple(math.degrees(a) for a in (rx, ry, rz)), tuple(map(float, s))


# --------------------------------------------------------------------------- scene export

class _Ids:
    def __init__(self):
        self.next = 1_000_000

    def __call__(self) -> int:
        self.next += 1
        return self.next


def _weld(mesh: SceneMesh) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Merge duplicate control points (same position and skin influences).
    Returns (points, remap, index of the source vertex of every point)."""
    key = np.ascontiguousarray(mesh.positions.astype(np.float32))
    if mesh.skin_bones is not None:
        key = np.concatenate([key, mesh.skin_bones.astype(np.float32),
                              mesh.skin_weights.astype(np.float32)], 1)
    key = np.ascontiguousarray(key)
    _, first, inverse = np.unique(key.view(np.dtype((np.void, key.dtype.itemsize * key.shape[1]))).ravel(),
                                  return_index=True, return_inverse=True)
    return mesh.positions[first], inverse.ravel(), first


def write_fbx(scene: Scene, path: str, embed_textures: bool = False) -> None:
    ids = _Ids()
    root_nodes: List[Node] = []

    hdr = Node("FBXHeaderExtension")
    hdr.add("FBXHeaderVersion", I(1003))
    hdr.add("FBXVersion", I(FBX_VERSION))
    hdr.add("EncryptionType", I(0))
    ts = hdr.add("CreationTimeStamp")
    for k, v in (("Version", 1000), ("Year", 1970), ("Month", 1), ("Day", 1), ("Hour", 10),
                 ("Minute", 0), ("Second", 0), ("Millisecond", 0)):
        ts.add(k, I(v))
    hdr.add("Creator", S("RSExtractor"))
    root_nodes += [hdr, Node("FileId", Prop("R", _FILE_ID)), Node("CreationTime", S(_CREATION_TIME)),
                   Node("Creator", S("RSExtractor"))]

    gs = Node("GlobalSettings")
    gs.add("Version", I(1000))
    p70 = gs.add("Properties70")
    for name, value in (("UpAxis", 1), ("UpAxisSign", 1), ("FrontAxis", 2), ("FrontAxisSign", 1),
                        ("CoordAxis", 0), ("CoordAxisSign", 1), ("OriginalUpAxis", 1), ("OriginalUpAxisSign", 1)):
        _p70(p70, name, "int", "Integer", "", value)
    _p70(p70, "UnitScaleFactor", "double", "Number", "", 100.0)       # 1 unit = 1 metre
    _p70(p70, "OriginalUnitScaleFactor", "double", "Number", "", 100.0)
    _p70(p70, "TimeMode", "enum", "", "", 11)
    root_nodes.append(gs)

    doc_id = ids()
    docs = Node("Documents")
    docs.add("Count", I(1))
    doc = docs.add("Document", L(doc_id), S(""), S("Scene"))
    dp = doc.add("Properties70")
    _p70(dp, "SourceObject", "object", "", "")
    _p70(dp, "ActiveAnimStackName", "KString", "", "", "")
    doc.add("RootNode", L(0))
    root_nodes += [docs, Node("References")]

    objects = Node("Objects")
    connections = Node("Connections")
    counts: Dict[str, int] = {}

    def count(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def connect(kind: str, child: int, parent: int, prop: Optional[str] = None) -> None:
        props = [S(kind), L(child), L(parent)]
        if prop:
            props.append(S(prop))
        connections.add("C", *props)

    def add_model(name: str, kind: str, matrix: np.ndarray) -> Tuple[int, Node]:
        mid = ids()
        model = objects.add("Model", L(mid), S(_name(name, "Model")), S(kind))
        model.add("Version", I(232))
        mp = model.add("Properties70")
        t, r, s = _decompose(matrix)
        _p70(mp, "Lcl Translation", "Lcl Translation", "", "A", *t)
        _p70(mp, "Lcl Rotation", "Lcl Rotation", "", "A", *r)
        _p70(mp, "Lcl Scaling", "Lcl Scaling", "", "A", *s)
        _p70(mp, "DefaultAttributeIndex", "int", "Integer", "", 0)
        _p70(mp, "InheritType", "enum", "", "", 1)
        model.add("Shading", C(True))
        model.add("Culling", S("CullingOff"))
        count("Model")
        return mid, model

    # --- nodes (bones / nulls)
    node_ids: List[int] = []
    for node in scene.nodes:
        kind = "LimbNode" if node.is_bone else "Null"
        mid, _ = add_model(node.name, kind, node.matrix)
        aid = ids()
        attr = objects.add("NodeAttribute", L(aid), S(_name(node.name, "NodeAttribute")), S(kind))
        if node.is_bone:
            attr.add("TypeFlags", S("Skeleton"))
        else:
            attr.add("TypeFlags", S("Null"))
        count("NodeAttribute")
        connect("OO", aid, mid)
        node_ids.append(mid)
    for i, node in enumerate(scene.nodes):
        connect("OO", node_ids[i], node_ids[node.parent] if node.parent >= 0 else 0)

    # --- materials & textures
    out_dir = os.path.dirname(os.path.abspath(path))
    material_ids: List[int] = []
    for mat in scene.materials:
        mid = ids()
        m = objects.add("Material", L(mid), S(_name(mat.name, "Material")), S(""))
        m.add("Version", I(102))
        m.add("ShadingModel", S("phong"))
        m.add("MultiLayer", I(0))
        mp = m.add("Properties70")
        _p70(mp, "DiffuseColor", "Color", "", "A", *map(float, mat.diffuse[:3]))
        _p70(mp, "DiffuseFactor", "Number", "", "A", 1.0)
        _p70(mp, "AmbientColor", "Color", "", "A", *map(float, mat.ambient))
        _p70(mp, "SpecularColor", "Color", "", "A", *map(float, mat.specular))
        _p70(mp, "Shininess", "double", "Number", "", float(mat.shininess))
        _p70(mp, "ShininessExponent", "double", "Number", "", float(mat.shininess))
        opacity = float(mat.diffuse[3]) if len(mat.diffuse) > 3 else 1.0
        _p70(mp, "Opacity", "double", "Number", "", opacity)
        _p70(mp, "TransparencyFactor", "Number", "", "A", 1.0 - opacity)
        count("Material")
        material_ids.append(mid)
        if mat.texture:
            rel = mat.texture.replace("\\", "/")
            absolute = os.path.normpath(os.path.join(out_dir, rel))
            vid = ids()
            video = objects.add("Video", L(vid), S(_name(mat.name, "Video")), S("Clip"))
            video.add("Type", S("Clip"))
            vp = video.add("Properties70")
            _p70(vp, "Path", "KString", "XRefUrl", "", absolute)
            video.add("UseMipMap", I(0))
            video.add("Filename", S(absolute))
            video.add("RelativeFilename", S(rel))
            if embed_textures and os.path.exists(absolute):
                with open(absolute, "rb") as f:
                    video.add("Content", Prop("R", f.read()))
            count("Video")
            tid = ids()
            tex = objects.add("Texture", L(tid), S(_name(mat.name, "Texture")), S(""))
            tex.add("Type", S("TextureVideoClip"))
            tex.add("Version", I(202))
            tex.add("TextureName", S(_name(mat.name, "Texture")))
            tp = tex.add("Properties70")
            _p70(tp, "UseMaterial", "bool", "", "", 1)
            _p70(tp, "WrapModeU", "enum", "", "", 0 if mat.wrap_u else 1)
            _p70(tp, "WrapModeV", "enum", "", "", 0 if mat.wrap_v else 1)
            tex.add("Media", S(_name(mat.name, "Video")))
            tex.add("FileName", S(absolute))
            tex.add("RelativeFilename", S(rel))
            tex.add("ModelUVTranslation", D(0.0), D(0.0))
            tex.add("ModelUVScaling", D(1.0), D(1.0))
            tex.add("Texture_Alpha_Source", S("Alpha_Black" if mat.alpha_texture else "None"))
            tex.add("Cropping", I(0), I(0), I(0), I(0))
            count("Texture")
            connect("OO", vid, tid)
            connect("OP", tid, mid, "DiffuseColor")
            if mat.alpha_texture:
                connect("OP", tid, mid, "TransparentColor")

    # --- meshes
    pose_entries: List[Tuple[int, np.ndarray]] = []
    for mesh in scene.meshes:
        if len(mesh.triangles) == 0:
            continue
        points, remap, source = _weld(mesh)
        tri = mesh.triangles
        poly_index = remap[tri].astype(np.int64)
        poly_index[:, 2] = -poly_index[:, 2] - 1

        gid = ids()
        geo = objects.add("Geometry", L(gid), S(_name(mesh.name, "Geometry")), S("Mesh"))
        geo.add("Properties70")
        geo.add("GeometryVersion", I(124))
        geo.add("Vertices", Prop("d", points.astype(np.float64)))
        geo.add("PolygonVertexIndex", Prop("i", poly_index.astype(np.int32)))
        layer_elems = []
        if mesh.normals is not None:
            ln = geo.add("LayerElementNormal", I(0))
            ln.add("Version", I(101))
            ln.add("Name", S(""))
            ln.add("MappingInformationType", S("ByPolygonVertex"))
            ln.add("ReferenceInformationType", S("Direct"))
            n = mesh.normals.astype(np.float64)
            lens = np.linalg.norm(n, axis=1, keepdims=True)
            n = np.where(lens > 1e-8, n / np.maximum(lens, 1e-8), [0.0, 1.0, 0.0])
            ln.add("Normals", Prop("d", n[tri].reshape(-1, 3)))
            layer_elems.append(("LayerElementNormal", 0))
        for k, uv in enumerate(mesh.uvs):
            lu = geo.add("LayerElementUV", I(k))
            lu.add("Version", I(101))
            lu.add("Name", S("UVMap" if k == 0 else f"UVMap{k + 1}"))
            lu.add("MappingInformationType", S("ByPolygonVertex"))
            lu.add("ReferenceInformationType", S("IndexToDirect"))
            flipped = np.stack([uv[:, 0], 1.0 - uv[:, 1]], 1).astype(np.float64)  # GX v points down
            lu.add("UV", Prop("d", flipped))
            lu.add("UVIndex", Prop("i", tri.astype(np.int32)))
            layer_elems.append(("LayerElementUV", k))
        if mesh.colors is not None:
            lc = geo.add("LayerElementColor", I(0))
            lc.add("Version", I(101))
            lc.add("Name", S("Col"))
            lc.add("MappingInformationType", S("ByPolygonVertex"))
            lc.add("ReferenceInformationType", S("IndexToDirect"))
            lc.add("Colors", Prop("d", mesh.colors.astype(np.float64) / 255.0))
            lc.add("ColorIndex", Prop("i", tri.astype(np.int32)))
            layer_elems.append(("LayerElementColor", 0))
        lm = geo.add("LayerElementMaterial", I(0))
        lm.add("Version", I(101))
        lm.add("Name", S(""))
        lm.add("MappingInformationType", S("AllSame"))
        lm.add("ReferenceInformationType", S("IndexToDirect"))
        lm.add("Materials", Prop("i", [0]))
        layer_elems.append(("LayerElementMaterial", 0))
        layer = geo.add("Layer", I(0))
        layer.add("Version", I(100))
        for kind, idx in layer_elems:
            if idx != 0:
                continue
            le = layer.add("LayerElement")
            le.add("Type", S(kind))
            le.add("TypedIndex", I(0))
        for kind, idx in layer_elems:
            if idx == 0:
                continue
            extra = geo.add("Layer", I(idx))
            extra.add("Version", I(100))
            le = extra.add("LayerElement")
            le.add("Type", S(kind))
            le.add("TypedIndex", I(idx))
        count("Geometry")

        mesh_world = scene.world_matrix(mesh.node) if mesh.node >= 0 else np.eye(4)
        model_id, _ = add_model(mesh.name, "Mesh", np.eye(4))
        connect("OO", model_id, node_ids[mesh.node] if mesh.node >= 0 else 0)
        connect("OO", gid, model_id)
        if 0 <= mesh.material < len(material_ids):
            connect("OO", material_ids[mesh.material], model_id)

        if mesh.skin_bones is not None and scene.nodes:
            bones = mesh.skin_bones[source]
            weights = mesh.skin_weights[source]
            skin_id = ids()
            skin = objects.add("Deformer", L(skin_id), S(_name(mesh.name, "Deformer")), S("Skin"))
            skin.add("Version", I(101))
            skin.add("Link_DeformAcuracy", D(50.0))
            count("Deformer")
            connect("OO", skin_id, gid)
            pose_entries.append((model_id, mesh_world))
            for bone in np.unique(bones):
                if bone < 0 or bone >= len(scene.nodes):
                    continue
                hit = (bones == bone) & (weights > 0)
                verts = np.nonzero(hit.any(axis=1))[0]
                if not len(verts):
                    continue
                vw = np.where(hit, weights, 0.0).sum(axis=1)[verts]
                link = scene.bind_matrix(int(bone))
                cid = ids()
                cl = objects.add("Deformer", L(cid), S(_name(f"{mesh.name}_{scene.nodes[bone].name}", "SubDeformer")),
                                 S("Cluster"))
                cl.add("Version", I(100))
                cl.add("UserData", S(""), S(""))
                cl.add("Indexes", Prop("i", verts.astype(np.int32)))
                cl.add("Weights", Prop("d", vw.astype(np.float64)))
                cl.add("Transform", Prop("d", _flat(np.linalg.inv(link) @ mesh_world)))
                cl.add("TransformLink", Prop("d", _flat(link)))
                count("Deformer")
                connect("OO", cid, skin_id)
                connect("OO", node_ids[bone], cid)

    if pose_entries:
        pid = ids()
        pose = objects.add("Pose", L(pid), S(_name("BindPose", "Pose")), S("BindPose"))
        pose.add("Type", S("BindPose"))
        pose.add("Version", I(100))
        entries = list(pose_entries) + [(node_ids[i], scene.bind_matrix(i)) for i in range(len(scene.nodes))]
        pose.add("NbPoseNodes", I(len(entries)))
        for oid, m in entries:
            pn = pose.add("PoseNode")
            pn.add("Node", L(oid))
            pn.add("Matrix", Prop("d", _flat(m)))
        count("Pose")

    # --- animations: one stack per clip, baked per-frame T/R/S curves
    take_names: List[Tuple[str, int, int]] = []
    for anim in scene.animations:
        n_frames = len(anim.frames)
        if not n_frames:
            continue
        times = np.array([round((anim.start + k) / anim.fps * _KTIME_PER_SECOND) for k in range(n_frames)],
                         dtype=np.int64)
        sid = ids()
        stack = objects.add("AnimationStack", L(sid), S(_name(anim.name, "AnimStack")), S(""))
        sp = stack.add("Properties70")
        _p70(sp, "LocalStart", "KTime", "Time", "", int(times[0]))
        _p70(sp, "LocalStop", "KTime", "Time", "", int(times[-1]))
        _p70(sp, "ReferenceStart", "KTime", "Time", "", int(times[0]))
        _p70(sp, "ReferenceStop", "KTime", "Time", "", int(times[-1]))
        count("AnimationStack")
        lid = ids()
        objects.add("AnimationLayer", L(lid), S(_name("BaseLayer", "AnimLayer")), S(""))
        count("AnimationLayer")
        connect("OO", lid, sid)
        take_names.append((anim.name, int(times[0]), int(times[-1])))
        for col, node_index in enumerate(anim.nodes):
            trs = [_decompose(anim.frames[k, col]) for k in range(n_frames)]
            channels = {
                "T": np.array([t for t, _, _ in trs]),
                "R": np.degrees(np.unwrap(np.radians(np.array([r for _, r, _ in trs])), axis=0)),
                "S": np.array([s for _, _, s in trs]),
            }
            rest = _decompose(scene.nodes[node_index].matrix)
            for kind, prop in (("T", "Lcl Translation"), ("R", "Lcl Rotation"), ("S", "Lcl Scaling")):
                values = channels[kind]
                ref = np.array(rest["TRS".index(kind)])
                if kind == "R":
                    diff = (values - ref + 180.0) % 360.0 - 180.0
                else:
                    diff = values - ref
                if np.abs(diff).max() < 1e-5:
                    continue
                cnid = ids()
                cn = objects.add("AnimationCurveNode", L(cnid), S(_name(kind, "AnimCurveNode")), S(""))
                cp = cn.add("Properties70")
                for axis in range(3):
                    _p70(cp, f"d|{'XYZ'[axis]}", "Number", "", "A", float(values[0, axis]))
                count("AnimationCurveNode")
                connect("OO", cnid, lid)
                connect("OP", cnid, node_ids[node_index], prop)
                for axis in range(3):
                    cid = ids()
                    curve = objects.add("AnimationCurve", L(cid), S(_name("", "AnimCurve")), S(""))
                    curve.add("Default", D(float(values[0, axis])))
                    curve.add("KeyVer", I(4009))
                    curve.add("KeyTime", Prop("l", times))
                    curve.add("KeyValueFloat", Prop("f", values[:, axis].astype(np.float32)))
                    curve.add("KeyAttrFlags", Prop("i", [0x4]))  # linear interpolation
                    curve.add("KeyAttrDataFloat", Prop("f", [0.0, 0.0, 0.0, 0.0]))
                    curve.add("KeyAttrRefCount", Prop("i", [n_frames]))
                    count("AnimationCurve")
                    connect("OP", cid, cnid, f"d|{'XYZ'[axis]}")

    defs = Node("Definitions")
    defs.add("Version", I(100))
    defs.add("Count", I(sum(counts.values()) + 1))
    gs_def = defs.add("ObjectType", S("GlobalSettings"))
    gs_def.add("Count", I(1))
    for kind, n in counts.items():
        ot = defs.add("ObjectType", S(kind))
        ot.add("Count", I(n))

    takes = Node("Takes")
    takes.add("Current", S(take_names[0][0] if take_names else ""))
    for name, t0, t1 in take_names:
        take = takes.add("Take", S(name))
        take.add("FileName", S(name.replace(" ", "_") + ".tak"))
        take.add("LocalTime", L(t0), L(t1))
        take.add("ReferenceTime", L(t0), L(t1))

    root_nodes += [defs, objects, connections, takes]

    out = bytearray(_HEAD_MAGIC + struct.pack("<I", FBX_VERSION))
    for node in root_nodes:
        out += node.encode(len(out))
    out += _NULL_RECORD
    out += _FOOT_ID + b"\0" * 4
    pad = ((len(out) + 15) & ~15) - len(out)
    out += b"\0" * (pad or 16)
    out += struct.pack("<I", FBX_VERSION) + b"\0" * 120 + _FOOT_MAGIC
    with open(path, "wb") as f:
        f.write(out)
