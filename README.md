# RSExtractor

Extracts 3D models, materials and textures from the GameCube games
**Star Wars Rogue Squadron II: Rogue Leader** and **Star Wars Rogue Squadron
III: Rebel Strike**, straight from your own disc images.

* Models → binary **FBX 7.4** (meshes, normals, UVs, vertex colors, materials,
  texture links). Imports into Blender, Maya, 3ds Max, Unity and Unreal.
* Textures → **PNG** or **TGA** (32-bit with alpha) next to each model
  (`textures/`), full resolution (including RS3's streamed high-resolution mips).
* Standalone images (menus, backdrops, gallery art, HUD, fonts) → PNG or TGA.

## Requirements

* Python 3.9+
* `numpy` and `Pillow` (`pip install -r requirements.txt` or `pip install .`)
* Your own disc images: plain `.iso`/`.gcm` or NKit `.nkit.iso`.
  Tested with `GSWE64` (Rogue Leader, USA) and `GLRE64` (Rebel Strike, USA).

## Usage

```sh
# everything (models + textures + images), output in ./output/RS2 or ./output/RS3
python -m rsextractor extract "Star Wars - Rogue Squadron II - Rogue Leader (USA).nkit.iso"

# only some models: substring/glob on archive/entry/model
python -m rsextractor extract rs3.iso -o out -f plCraft/xwing -f falcon

# list the models on a disc
python -m rsextractor list rs2.iso -f lvlPack01

# write textures and images as TGA instead of PNG
python -m rsextractor extract rs2.iso -t tga

# embed the textures inside the FBX files instead of referencing them
python -m rsextractor extract rs2.iso --embed
```

Output layout: `output/RS<n>/<archive>/<resource>/<model>.fbx` with the
model's textures in `.../<resource>/textures/`. Player craft live under
`data/plCraft/...`, level content under `lvlPackNN/...`.

Units are metres (1 game unit = 1 m), Y up.

## Status

| Feature | RS2 | RS3 |
|---------|-----|-----|
| Static models (all meshes, normals, UVs, vertex colors) | yes | yes |
| Materials + textures | yes | yes |
| Standalone images | yes | yes |
| Articulated models (walkers, troopers, turrets) assembled from their node hierarchy | yes | yes |
| Skeletons / skin weights in the FBX | not yet; characters export in bind pose | not yet; characters export in bind pose, capes lie flat as stored |
| Animations (`_an`) | no | no |
| Terrain heightmaps (`h16_*.img`), level object placement (`dref`) | no | no |
| Sounds | no | no |

Format notes: [docs/FORMATS.md](docs/FORMATS.md).

### Alternative output formats

FBX is the default. The exporter works from a format-neutral scene
(`rsextractor/scene.py`), so glTF 2.0 (`.glb`) or OBJ/MTL writers can be
added without touching the game parsers. glTF would be the better choice for
web or engine pipelines once skeletons and animation are supported.

## Development

`tools/fbxdump.py` is an independent FBX reader that validates the binary
structure of exported files (`python tools/fbxdump.py model.fbx --tree`).

This project contains no game data. You need your own copies of the games.
