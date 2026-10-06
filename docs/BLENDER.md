# Blender

The FBX files import with Blender's own FBX importer. A few game features need a
little setup afterwards; the two scripts in `tools/` do it for you.

## Vertex color tints

Materials marked `rsVertexTint` (the Mon Calamari cruiser hull, scorched wrecks
and debris) multiply their color by the vertex colors. `tools/blender_vertex_tint.py`
imports FBX files and puts a Color Attribute multiply in front of the Base Color
of every such material:

```sh
blender --python tools/blender_vertex_tint.py -- output/RS2/lvlPack02/l09/01/gfxcalamaricruiser.fbx
```

Run without files (or from Blender's Text Editor) it fixes the materials already
in the scene.

## Terrain

The terrain FBX shows a baked color texture. `tools/blender_terrain.py` reads the
`_terrain.json` next to it, imports the FBX if it is not in the scene yet and gives
it a material that blends the original layers the way the game does, so the
ground keeps its full detail up close:

```sh
blender --python tools/blender_terrain.py -- output/RS2/lvlPack00/l00/01/l00_01_terrain.json
```

From the Text Editor, set `JSON` at the top of the script and run it.

## Reflections

Use a level's `envmap/panorama` in an Environment Texture node of the world and
the imported models reflect it the way they do in the game. Models outside levels
(`data/plCraft`) have no cube of their own; any level's will do.

## Wireframes

The `<model>_wire` mesh of briefing and HUD wireframes holds the game's own
polygons. Add a Wireframe modifier (or set the object to display as wire) and give
the `wireframe` material an emission color to get the game's lines.

## Maps

Maps span many kilometres, far beyond the viewport's default clip distance of
1 km, so most of a map stays hidden after import. Open the sidebar with `N`, and on
its View tab raise Clip End until the whole map shows. The sky hangs under the
`<level>_<resource>_map_sky` empty; scale that empty to taste.
