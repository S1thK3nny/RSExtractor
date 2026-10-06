"""Tint materials by their vertex colors in Blender, the way the game lights them.

Materials with the game's vertex tint flag (the Mon Calamari cruiser hull, destroyed
buildings, debris) multiply their color by the vertex colors before lighting. The FBX
cannot express that, so the exporter marks them with the user property
``rsVertexTint``. This script imports the given FBX files and puts a Color Attribute
multiply in front of the Base Color of every marked material, in those files or, with
no files given, in the open scene.

    blender --python tools/blender_vertex_tint.py -- path/to/model.fbx [more.fbx ...]

or open it in Blender's Text Editor and run it after importing.
"""
import sys

import bpy

MARK = "rsVertexTint"  # material custom property from the FBX, also the mix node name


def tint(mat: bpy.types.Material) -> bool:
    """Multiply the Base Color by the vertex colors; False when already done or not possible."""
    if not mat.node_tree:
        return False
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    if MARK in nodes:
        return False
    bsdf = next((n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is None:
        return False
    base = bsdf.inputs["Base Color"]

    attr = nodes.new("ShaderNodeVertexColor")  # empty layer name = the mesh's render colors
    attr.location = (bsdf.location.x - 500, bsdf.location.y + 250)
    mix = nodes.new("ShaderNodeMix")
    mix.name = mix.label = MARK
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    mix.location = (bsdf.location.x - 250, bsdf.location.y + 150)
    if base.links:
        links.new(base.links[0].from_socket, mix.inputs["A"])
    else:
        mix.inputs["A"].default_value = base.default_value
    links.new(attr.outputs["Color"], mix.inputs["B"])
    links.new(mix.outputs["Result"], base)
    return True


def give_colors(objects) -> int:
    """White vertex colors for meshes of tinted materials that have none (else they turn black)."""
    count = 0
    for obj in objects:
        if obj.type != "MESH" or obj.data.color_attributes:
            continue
        if any(s.material and s.material.get(MARK) for s in obj.material_slots):
            layer = obj.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
            layer.data.foreach_set("color", [1.0] * (4 * len(layer.data)))
            count += 1
    return count


def apply(objects) -> None:
    materials = {s.material for o in objects for s in o.material_slots if s.material and s.material.get(MARK)}
    done = sum(tint(m) for m in materials)
    filled = give_colors(objects)
    print(f"vertex tint: {done} of {len(materials)} marked materials set up, {filled} meshes given white colors")


def main(files) -> None:
    if not files:
        apply(list(bpy.data.objects))
        return
    for path in files:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=path)
        apply(list(set(bpy.data.objects) - before))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
