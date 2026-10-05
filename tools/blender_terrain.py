"""Build the full detail ground material of an exported terrain in Blender.

The terrain FBX shows a baked color texture. Next to it ``<name>.json`` lists the
game's tiling layer textures, how each is mapped over the terrain and which weight
map channel blends it in. This script imports the FBX (unless an object of that name
already exists) and gives it a material that blends the layers the way the game
does, so the ground keeps its full detail up close.

    blender --python tools/blender_terrain.py -- path/to/l00_01_terrain.json

or open it in Blender's Text Editor, set ``JSON`` below and run it.
"""
import json
import os
import sys

import bpy

JSON = ""


def build(path: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8") as f:
        info = json.load(f)
    obj = bpy.data.objects.get(stem) or bpy.data.objects.get(info["mesh"])
    if obj is None or obj.type != "MESH":
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=os.path.join(folder, stem + ".fbx"))
        meshes = [o for o in set(bpy.data.objects) - before if o.type == "MESH"]
        if not meshes:
            raise RuntimeError("no mesh in " + stem + ".fbx")
        obj = meshes[0]

    mat = bpy.data.materials.new(stem + "_layers")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (1400, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (1100, 0)
    bsdf.inputs["Roughness"].default_value = 1.0
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    uvmap = nodes.new("ShaderNodeUVMap")
    uvmap.location = (-1400, 0)
    split = nodes.new("ShaderNodeSeparateXYZ")
    split.location = (-1200, 0)
    links.new(uvmap.outputs["UV"], split.inputs["Vector"])
    # the JSON maps (U, V down the image); Blender's UV map has V up
    flip = nodes.new("ShaderNodeMath")
    flip.operation = "SUBTRACT"
    flip.inputs[0].default_value = 1.0
    flip.location = (-1000, -100)
    links.new(split.outputs["Y"], flip.inputs[1])
    u_in, v_in = split.outputs["X"], flip.outputs["Value"]

    def image(rel, color):
        img = bpy.data.images.load(os.path.join(folder, rel), check_existing=True)
        if not color:
            img.colorspace_settings.name = "Non-Color"
        return img

    weight_nodes = []
    for k, rel in enumerate(info["weights"]):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image(rel, False)
        tex.extension = "EXTEND"
        tex.location = (-700, -400 - 300 * k)
        links.new(uvmap.outputs["UV"], tex.inputs["Vector"])
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = (-400, -400 - 300 * k)
        links.new(tex.outputs["Color"], sep.inputs["Color"])
        weight_nodes.append((sep, tex))

    total = None
    y = 600
    for layer in info["layers"]:
        if not layer["texture"]:
            continue
        coords = []
        for row in layer["uv"]:  # (u, v) = row . (U, V, 1)
            a = nodes.new("ShaderNodeMath")
            a.operation = "MULTIPLY_ADD"
            a.location = (-800, y)
            links.new(u_in, a.inputs[0])
            a.inputs[1].default_value = row[0]
            a.inputs[2].default_value = row[2]
            b = nodes.new("ShaderNodeMath")
            b.operation = "MULTIPLY_ADD"
            b.location = (-600, y)
            links.new(v_in, b.inputs[0])
            b.inputs[1].default_value = row[1]
            links.new(a.outputs["Value"], b.inputs[2])
            coords.append(b)
        neg = nodes.new("ShaderNodeMath")  # image v points down, Blender's up
        neg.operation = "MULTIPLY"
        neg.inputs[1].default_value = -1.0
        neg.location = (-400, y - 100)
        links.new(coords[1].outputs["Value"], neg.inputs[0])
        vec = nodes.new("ShaderNodeCombineXYZ")
        vec.location = (-200, y)
        links.new(coords[0].outputs["Value"], vec.inputs["X"])
        links.new(neg.outputs["Value"], vec.inputs["Y"])
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image(layer["texture"], True)
        tex.location = (0, y)
        links.new(vec.outputs["Vector"], tex.inputs["Vector"])
        map_index, channel = layer["weights"]
        sep, wtex = weight_nodes[map_index]
        weight = wtex.outputs["Alpha"] if channel == "A" else sep.outputs["RGB".index(channel)]
        scale = nodes.new("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        scale.location = (300, y)
        links.new(tex.outputs["Color"], scale.inputs[0])
        links.new(weight, scale.inputs["Scale"])
        if total is None:
            total = scale
        else:
            add = nodes.new("ShaderNodeVectorMath")
            add.operation = "ADD"
            add.location = (550, y)
            links.new(total.outputs["Vector"], add.inputs[0])
            links.new(scale.outputs["Vector"], add.inputs[1])
            total = add
        y -= 350
    if total is not None:
        links.new(total.outputs["Vector"], bsdf.inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    for p in args or [JSON]:
        if p:
            build(p)
