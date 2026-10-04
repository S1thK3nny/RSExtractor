# RSExtractor

Extracts 3D models, materials and textures from the GameCube games
**Star Wars Rogue Squadron II: Rogue Leader** and **Star Wars Rogue Squadron
III: Rebel Strike**, straight from your own disc images.

* Models → binary **FBX 7.4** (meshes, normals, UVs, vertex colors, materials,
  texture links, skeletons, skin weights and animation clips). Imports into
  Blender, Maya, 3ds Max, Unity and Unreal.
* Textures → **PNG** or **TGA** (32-bit with alpha) next to each model
  (`textures/`), full resolution (including RS3's streamed high-resolution mips).
* Bump maps → tangent space **normal maps** (`*_normal`, OpenGL convention) linked
  to the FBX materials, height maps also as gray `*_height` images.
* Self lit masks (cockpit lights, windows) → **emissive maps** (`*_emissive`) linked
  to the FBX materials. Color textures keep alpha only where the game uses it as
  transparency (opacity maps are baked into it); otherwise they are opaque.
* Highlights → specular color, shininess (as a Blinn-Phong exponent) and, where
  the game masks them with the color map, **specular maps** (`*_specular`).
* Reflections → the material's reflectivity as the FBX reflection factor
  (Blender: metallic), and every level's environment cube as six faces plus an
  equirectangular **panorama** (`envmap/panorama`) to use as the world.
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

# skeletons and skins but no animation clips (much faster), or static meshes only
python -m rsextractor extract rs3.iso --no-animations
python -m rsextractor extract rs3.iso --no-skeletons
```

Output layout: `output/RS<n>/<archive>/<resource>/<model>.fbx` with the
model's textures in `.../<resource>/textures/`, named
`<model>_<material>_<sha1 prefix>[_normal|_height|_emissive|_specular]`. The discs keep no
texture names, so the material name (often a Maya default like `phong31`) is the
best label there is. Player craft live under
`data/plCraft/...`, level content under `lvlPackNN/...`.

Rebel Strike keeps most level models (walkers, troopers, buildings, props) in
the shared `bData.pool`, referenced from the levels. Each one is written once,
next to the first level resource that uses it (the AT-AT lands in
`lvlPack02/L_03_Ralltiir_lvl/01c01/fltATAT_ai.fbx` and
`lvlPack02/L_06_Hoth_lvl/01/grndATAT_ai.fbx`); `-f` with the name of any level
that uses a model selects it too. The few pool models no level references go to
`bData/<model>/`. Characters also get the shared clip sets their levels load
(run, crouch, death ... for stormtroopers, officers, Ewoks, Jawas).

Rebel Strike builds several levels from square tiles with buildings on them
(both Death Star surfaces, the trench run, the Executor hull, Fondor, the
Academy, Ralltiir, Bespin's cloud city). Each such level resource also gets the
assembled layout, `<level>_<resource>_tiles.fbx`, with every tile and building
copied into place at full detail; copies of the same mesh are merged per tile
or building type, so Blender imports a few hundred objects. They are big (up to
~170 MB for the largest Death Star map); `--no-tile-levels` skips them, and
`-f /tiles` exports only them.

Models with levels of detail get one empty per level (`<model>_LOD0`,
`<model>_LOD1`, ...) holding that level's meshes, which carry the same suffix
(`<model>_07_LOD1`; `_LOD2_3` = shown in LOD2 and LOD3). Meshes drawn at every
distance hang directly under the model root. LOD0 is the full detail version.

Models with moving parts but no skinning (S-foils, cockpits, turrets, wing
hinges) keep the game's part hierarchy as empties: every part hangs under an
empty placed on its pivot (the Maya rotate pivot), so rotating that empty swings
the part around its real hinge. Meshes that do not belong to a part stay under
the model root.

Animated models and anything with joints or skinned meshes (characters,
walkers) get a skeleton instead: a root bone named after the model and
one bone per node (Rebel Strike has the original Maya names, Rogue Leader
numbered ones). Every animation clip of the model becomes an animation take
(Blender: one action per clip), baked at 24 frames per second. Clips are
evaluated by running the game's own per-model animation code (curves,
expressions, IK) in a small PowerPC interpreter, so walkers keep their feet
planted exactly as in the game. This is CPU heavy: animation evaluation runs in
parallel on all cores (`-j` to change), a full Rebel Strike extraction with its
~1100 clips takes a while; `--no-animations` skips it.

Reflective materials (canopies, chrome, medals, the Death Star core) mirror the
environment cube of the level they are drawn in. Each level resource writes its
cube to `.../<resource>/envmap/` (`rgt`, `lft`, `top`, `bot`, `frt`, `bck` and
`panorama`); Rogue Leader's loose cubes land in `data/images/envmap/<name>/`.
The panorama is laid out for Blender: use it in an Environment Texture node of
the world and the imported models reflect it the way they do in the game. Models
outside levels (`data/plCraft`) have no cube of their own; any level's will do.

Units are metres (1 game unit = 1 m), Y up.

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
| Tile levels assembled from their tile maps | n/a | yes |
| Terrain heightmaps (`h16_*.img`), other level object placement | no | no |
| Sounds | no | no |

Format notes: [docs/FORMATS.md](docs/FORMATS.md).

### Alternative output formats

FBX is the default. The exporter works from a format-neutral scene
(`rsextractor/scene.py`), so glTF 2.0 (`.glb`) or OBJ/MTL writers can be
added without touching the game parsers. glTF would be the better choice for
web or engine pipelines.

## Development

`tools/smoketest.py` extracts a handful of models per game that together cover
every feature (skeletons, IK, pool models, streamed textures, every bump, opacity
and self lit case) into `output/_test` in about a minute, instead of a full run
(Rebel Strike alone writes ~10 GB in ~30 min). It fails on any failed model or
missing texture:

```
python tools/smoketest.py rs2.iso rs3.iso
```

`tools/fbxdump.py` is an independent FBX reader that validates the binary
structure of exported files (`python tools/fbxdump.py model.fbx --tree`).

This project contains no game data. You need your own copies of the games.
