# RSExtractor

RSExtractor extracts 3D models, materials, textures and whole levels from the
GameCube games **Star Wars Rogue Squadron II: Rogue Leader** and **Star Wars
Rogue Squadron III: Rebel Strike**, straight from your own disc images.

* Models are written as binary FBX 7.4 files with their meshes, normals, UVs,
  vertex colors, materials, texture links, skeletons, skin weights and animation
  clips. They import into Blender, Maya, 3ds Max, Unity and Unreal.
* Textures are saved as *.png* or *.tga* (32-bit with alpha) at full resolution,
  including RS3's streamed high-resolution mips. Normal, height, emissive and
  specular maps are derived from the game's materials and linked to them.
* Every level gets its placed objects, its assembled tile levels, the terrain of
  open levels and one full map per level resource with its sky.
* Each level's environment cube is saved as six faces and as a panorama, so
  reflective materials can mirror it.
* Standalone images such as menus, backdrops, gallery art, the HUD and fonts are
  saved as .png or .tga too.

RSExtractor contains no game data and works only from your own copies of the games.
It was developed and tested with the USA releases, version 1.0 of Rogue Leader
(`GSWE64`) and version 1.1 of Rebel Strike (`GLRE64`); other versions and regions
are untested.

## Requirements

* Python 3.9 or newer.
* `numpy` and `Pillow`, installed with `pip install -r requirements.txt` or `pip install .`.
* Your own disc images, either plain `.iso`/`.gcm` files or NKit `.nkit.iso` files.

## Quick start

```sh
# everything, output in ./output/RS2 or ./output/RS3
python -m rsextractor extract "Star Wars - Rogue Squadron II - Rogue Leader (USA).nkit.iso"

# only some models (substring or glob on archive/entry/model)
python -m rsextractor extract rs3.iso -o out -f plCraft/xwing -f falcon

# list the models on a disc
python -m rsextractor list rs2.iso -f lvlPack01
```

A full Rebel Strike extraction writes about 10 GB and takes a while; see
[docs/USAGE.md](docs/USAGE.md) for all options and filters.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/USAGE.md](docs/USAGE.md) | Commands, options and filter examples |
| [docs/OUTPUT.md](docs/OUTPUT.md) | Folder layout, file naming and what each exported file contains |
| [docs/LEVELS.md](docs/LEVELS.md) | Which level folder is which mission in both games |
| [docs/BLENDER.md](docs/BLENDER.md) | Importing into Blender and the helper scripts in `tools/` |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Smoke test, FBX validator and code layout |
| [docs/FORMATS.md](docs/FORMATS.md) | Reverse engineered file formats of both games |

## Status

| Feature | RS2 | RS3 |
|---------|-----|-----|
| Static models (all meshes, normals, UVs, vertex colors) | yes | yes |
| Materials + textures | yes | yes |
| Bump maps (as normal maps) | yes | yes |
| Self lit masks (as emissive maps), opacity maps (as alpha) | yes | yes |
| Specular highlights, reflectivity, environment cubes (as panoramas) | yes | yes |
| Standalone images | yes | yes |
| Articulated models (walkers, troopers, turrets) assembled from their node hierarchy | yes | yes |
| Skeletons / skin weights in the FBX | yes | yes (capes lie flat as stored; the game simulates cloth) |
| Animations (`_an`), including IK driven walkers | yes | yes |
| Level models from the shared pool (`bData.pool`) | n/a | yes |
| Tile levels assembled from their tile maps | yes | yes |
| Briefing and HUD wireframes (from the collision hull) | yes | yes |
| Level objects placed from `dat`, capital ship parts on their mount points | yes | yes |
| Terrain (heightmaps, baked ground texture, blend layers) | yes | yes |
| Full maps (terrain, tiles, objects and sky in one FBX) | yes | yes |
| Sounds | no | no |

## Known issues

Sky domes get their color from their vertex colors. Blender imports those as a
color attribute but its materials don't use it, so the domes look white or grey
until you plug a Color Attribute node into their Base Color.

## License

RSExtractor is released under the [MIT License](LICENSE).
