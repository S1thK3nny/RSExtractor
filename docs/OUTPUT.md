# Output

Units are metres (one game unit is one metre) and Y points up.

This page covers the [layout and naming](#layout-and-naming) of the output, the
[models](#models) with their levels of detail, moving parts, skeletons and
wireframes, the [materials and textures](#materials-and-textures), and the
generated [level files](#levels) for objects, tile levels, terrain and maps.

## Layout and naming

```
output/RS<n>/<archive>/<resource>/<model>.fbx
output/RS<n>/<archive>/<resource>/textures/<model>_<material>_<sha1 prefix>[_normal|_height|_emissive|_specular].png
output/RS<n>/<archive>/<resource>/envmap/{rgt,lft,top,bot,frt,bck,panorama}.png
output/RS<n>/LEVELS.md
```

The discs keep no texture names, so the material name (often a Maya default like
`phong31`) is the best label there is. Player craft live under `data/plCraft/...`,
level content under `lvlPackNN/<level>/<resource>/...` (see [LEVELS.md](LEVELS.md)).

Each level resource can also get these generated files next to its models:

| File | Contents |
| --- | --- |
| `<level>_<resource>_objects.fbx` | Every object the resource places ([Objects](#objects)) |
| `<level>_<resource>_tiles.fbx` | The assembled tile layout ([Tile levels](#tile-levels)) |
| `<level>_<resource>_terrain.fbx` + `.json` | The terrain mesh and its blend layers ([Terrain](#terrain)) |
| `<level>_<resource>_map.fbx` | All of the above plus the sky in one file ([Maps and skies](#maps-and-skies)) |

## Models

### Levels of detail

Models with levels of detail get one empty per level (`<model>_LOD0`,
`<model>_LOD1`, ...) holding that level's meshes, which carry the same suffix
(`<model>_07_LOD1`; `_LOD2_3` = shown in LOD2 and LOD3). Meshes drawn at every
distance hang directly under the model root. LOD0 is the full detail version.

### Moving parts

Models with moving parts but no skinning (S-foils, cockpits, turrets, wing
hinges) keep the game's part hierarchy as empties: every part hangs under an
empty placed on its pivot (the Maya rotate pivot), so rotating that empty swings
the part around its real hinge. Meshes that do not belong to a part stay under
the model root.

### Skeletons and animation

Animated models and anything with joints or skinned meshes (characters,
walkers) get a skeleton instead: a root bone named after the model and
one bone per node (Rebel Strike has the original Maya names, Rogue Leader
numbered ones). Every animation clip of the model becomes an animation take
(Blender: one action per clip), baked at 24 frames per second. Clips are
evaluated by running the game's own per-model animation code (curves,
expressions, IK) in a small PowerPC interpreter, so walkers keep their feet
planted exactly as in the game.

Rebel Strike capes lie flat as stored; the game simulates their cloth at run time.

### Wireframes

Briefing and HUD wireframes (Rogue Leader `data/briefing/gfxWIREOverlays*`,
Rebel Strike `wireOverlay*` next to each craft, character and weapon) are drawn by
the game from the model's collision hull; their visible geometry is just a flat
quad. The FBX carries that hull as an extra mesh `<model>_wire` with the game's
own polygons (not triangulated), so showing only its edges gives the game's lines
(see [BLENDER.md](BLENDER.md#wireframes)).

## Materials and textures

Textures are written at full resolution (including RS3's streamed high-resolution
mips) as 32-bit PNG or TGA, linked from the FBX materials or embedded with `--embed`.

| Game feature | Exported as |
| --- | --- |
| Color map | `<...>.png`; alpha kept only where the game uses it as transparency (opacity maps are baked into it), otherwise opaque |
| Bump map | Tangent space normal map `*_normal` (OpenGL convention) linked to the material, plus the gray height map `*_height` |
| Self lit mask (cockpit lights, windows) | Emissive map `*_emissive` linked to the material |
| Highlights | Specular color, shininess (as a Blinn-Phong exponent) and, where the game masks them with the color map, a specular map `*_specular` |
| Reflectivity | The FBX reflection factor (Blender: metallic) |

### Vertex color tints

Some materials multiply their color by the model's vertex colors before lighting
(the reddish patches on the Mon Calamari cruiser hull, scorch marks on destroyed
buildings and debris). FBX has no way to say that, so these materials carry the
custom property `rsVertexTint` and the vertex colors are in the mesh's color layer.
[BLENDER.md](BLENDER.md#vertex-color-tints) has a script that sets up the tint.

### Additive materials

Materials the game blends by adding their color (star layers, horizon bands,
explosions) are written with alpha equal to their brightest channel, so black
adds nothing and the rest looks as in the game over a dark sky.

### Environment cubes

Reflective materials (canopies, chrome, medals, the Death Star core) mirror the
environment cube of the level they are drawn in. Each level resource writes its
cube to `.../<resource>/envmap/` (`rgt`, `lft`, `top`, `bot`, `frt`, `bck` and
an equirectangular `panorama`); Rogue Leader's loose cubes land in
`data/images/envmap/<name>/`. Models outside levels (`data/plCraft`) have no cube
of their own; any level's will do. See [BLENDER.md](BLENDER.md#reflections) for
using the panorama as the world.

## Levels

The objects, tile, terrain and map files of a level resource all share the same
coordinates, so importing several of them lines them up (the trench with its
turrets and barriers, the terrain under the placed buildings). Files identical to
an earlier one of the same level are written once, under the first.

### Pool models (Rebel Strike)

Rebel Strike keeps most level models (walkers, troopers, buildings, props) in
the shared `bData.pool`, referenced from the levels. Each one is written once,
next to the first level resource that uses it (the AT-AT lands in
`lvlPack02/L_03_Ralltiir_lvl/01c01/fltATAT_ai.fbx` and
`lvlPack02/L_06_Hoth_lvl/01/grndATAT_ai.fbx`); `-f` with the name of any level
that uses a model selects it too. The few pool models no level references go to
`bData/<model>/`.

Characters also get the shared clip sets their levels load (run, crouch,
death ... for stormtroopers, officers, Ewoks, Jawas). The clips cutscenes play on
their cast (Luke's dance in the credits, the stormtroopers of the Yavin
cutscenes ...) go to that model too, to its copy in the cutscene's level when the
level has one; the cutscene model keeps only its own rig's clip.

### Tile levels

Both games build several levels from square tiles with buildings on them
(Rebel Strike: both Death Star surfaces, the trench run, the Executor hull,
Fondor, the Academy, Ralltiir, Bespin's cloud city; Rogue Leader: the Death
Star surfaces of both Death Stars, the Imperial Academy, Cloud City). Each such
level resource gets the assembled layout, `<level>_<resource>_tiles.fbx`, with
every tile and building copied into place at full detail; copies of the same mesh
are merged per tile or building type, so Blender imports a few hundred objects.
They are big (up to ~170 MB for the largest Death Star map). A resource with the
same layout as an earlier one of its level (Rogue Leader `l07/02`, `l10/03`) is
written once, under the first.

### Objects

Every level resource of both games gets the objects it places (turrets,
walkers, buildings, crates, troopers, rocks, asteroids ...) in
`<level>_<resource>_objects.fbx`: each placed model copied into place at full
detail, copies of the same mesh merged, one empty per model. Characters stand in
their bind pose. Resources that place exactly the same objects as an earlier one
of their level (Rogue Leader's four Tatooine variants `l00/01` .. `04`, which
differ only in lighting) are written once, under the first.

* The parts the game mounts on a host (the Star Destroyer's sensor balls, command
  deck and guns, the Bespin balloons' turbolasers, the gas platforms' containers)
  sit on the host's mount points.
* Objects placed at height -FLT_MAX (the Academy's radar dishes) stand on the
  tiles or terrain below them.
* Rebel Strike's indoor levels (hangars, Echo Base, the Death Star escape, the
  Yavin temple, Endor's bunker ...) build their walls and floors from rooms that a
  separate room list places; those rooms go into the same file under
  `<level>_<resource>_rooms`.
* Rogue Leader's asteroid rocks are left out, since the asteroid fields scatter
  them at random while the level runs. Sound emitters and effects are not placed
  either.

### Terrain

Open levels of both games (Tatooine, Hoth, Yavin, Dantooine, Kothlis, Endor ...)
get their terrain as `<level>_<resource>_terrain.fbx`: one mesh in metres, built
from every level of detail the disc stores (the finest data wherever it exists,
coarser rings around it, no cracks between them; up to 3.4 million vertices for
Rebel Strike's Hoth battle).

The game textures the ground by blending tiling layers (sand, rock, snow) with
small blend masks, which no single image can hold at full detail over 10 to 28 km.
So the FBX shows a baked color texture (`textures/terrain_<id>_color`, up to 4096
pixels), and next to it the export keeps the original layer textures, their blend
weights (`terrain_<id>_weights<n>`, four layers per RGBA image) and
`<level>_<resource>_terrain.json` describing how they combine.
[BLENDER.md](BLENDER.md#terrain) has a script that builds the full detail
material from it.

A terrain identical to one earlier in its level (Rogue Leader's four Tatooine
lighting variants) is written once; levels sharing a terrain get hard links.

### Maps and skies

Every level resource also gets its whole map in one file,
`<level>_<resource>_map.fbx`: the terrain, tiles and objects above, each under its
own empty, plus the sky. The maps repeat the geometry of the separate files
(Rebel Strike's Hoth battle map is ~420 MB). A map identical to an earlier one of
its level is written once.

Both games draw the sky around the camera (domes about 140 m across, star layers,
cloud rings, planets), so the map puts the sky under `<level>_<resource>_map_sky`,
an empty centred on the level and scaled until the dome reaches 1.5 times past the
level's edge; scale that empty to taste. Rogue Leader's skies are the models the
game loads for the level and resource (`sky##` and its extras from the resource or
`X/00`); Rebel Strike's are the sky packages and sky objects of the resource and
`X_lvl/00`, without the horizon bands the game scales by code and with Tatooine's
day sky of its four times of day. The skies leave the object FBXs.
