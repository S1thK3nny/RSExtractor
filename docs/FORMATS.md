# Rogue Squadron II / III file formats

Notes from reverse engineering the GameCube releases (`GSWE64` Rogue Leader,
`GLRE64` Rebel Strike, both USA). Function names refer to the relocated game
code (see "Executables").

All multi-byte values are big-endian.

## Executables

Both `main.dol` files are small loader stubs plus the GameCube SDK. The game
itself is a second DOL that runs from MMU-mapped virtual memory:

| Game | Inner DOL location | Load address |
|------|--------------------|--------------|
| RS2  | uncompressed, stub section T2 (file 0x6D740, 0x1E6520 bytes) | `0x7FC00000` |
| RS3  | zlib in stub section D0 (header: u32 packed size, u32 unpacked size) | `0x7F200000` |

Load the inner DOL at its real addresses (for example wrapped in an ELF) or no
pointers resolve. RS2 game code calls SDK functions in the stub at `0x8000xxxx`.

## Archives: `*.dir` + `*.dat`

`*.dir` is CRLF text: `name<spaces>offset\tlength\tattrib\tdepth`, one header
line. `attrib & 0x80` = folder. Leaves in `*.dat` are zlib streams (RS3 adds a
random-length, 4-byte-aligned junk prefix before the `78 xx` header). A few
leaves are stored raw.

## Packages

Most decompressed resources are packages: a blob of parts plus a directory.

* **RS2**: directory at the end; 0x30-byte entries `{char name[0x20]; u32 offset,
  length, attrib, end}`. The final all-zero entry holds the directory size in its
  last word. A folder's `end` is the directory offset where its children end.
* **RS3**: `u32 data_start`, then 0x14-byte entries `{u32 name_offset, offset
  (relative to data_start), length, attrib, end}`, a terminator whose last word
  is the string table offset, the string table, then the data.

RS3 levels reference most of their models instead of holding them: a 20-byte
`X_dref` part is the key of a package in `Rogue3/bData.pool`:

```
u32 count, u32, u32
count x {sha1[20], u32 offset, u32 size, u32 size}   offset/size into the file
```

Each entry is a zlib packed package (RS3 layout) holding one or more models
(`grndATAT_ai` with its clips, destroyed variants, tiles) or, without any
geometry, a clip set shared by a level's characters (`ss2HgunAnims`,
`ssSharedAnims`, `ewokAnimation`, `AImainAnimation` ...). A level uses such a
set for every character whose `_su` channel count matches the clips' curve count
(69 for troopers and officers, 57 for Ewoks, Jawas and Ugnaughts, 171 for Han).
The same name can stand for different packages in different levels (`tile05`).

Besides models and clips the pool holds three more part types (counts for
the US disc):

* `X_cs` (255, in cutscene packages): the cast of a cutscene. `u32 strings,
  u32, u32, u32 count, u32 actor offsets[count]`, actors of 0x2C bytes `{u32
  locator, u32 variant ("default"), u32, u32 model, u32 clip, u32 flags, f32,
  f32, u32 matrix, u32, u32}` whose names are offsets into the string table at
  `strings`: the locator of the cutscene's own rig (`characterlukeik`), the
  model to place there (`LS_PLY_Farmboy`, any model of the level or pool) and
  the clip it plays (`luke_dance`), which the cutscene package carries. 115 of
  the 370 clips in cutscene packages belong to such actors, not to the
  cutscene's own rig.
* `X_aw` (16, player and wingman characters, Yoda): `u32 joints, u32
  transforms, f32 weight[joints], f32 weight[transforms]`, values 0, 0.2, 0.4
  and 1 per node, likely the weights of an aiming or upper body layer.
* `t###_v_lm` (136): building placement on a city tile, 3x4 matrices (0x30
  bytes) with a translation and a yaw of whole quarter turns (`FUN_7f35c620`
  counts them; the scale of `_1_lm` copies is ignored, only `t###_0_lm` is
  read, `FUN_7f35c388`). See "Tile maps".

A model named `X` consists of `X_su` (setup), `X_ge` (geometry), `X_si`
(materials), optional `X_an` (animation), `X_cl` (collision) and, in RS3, `X_nm`
(names). Texture pools are `*_tpi` + `*_tpd`.

## Tile maps

Several RS3 levels are built from square tiles on a grid (Death Star surface
and trench, Executor hull, Fondor, the Academy, Ralltiir, Bespin's city; RS3
`FUN_7f35cfb0` sets it up). The level package holds `tmap` and `bmap` and
references packages `tile##` and `bldg##` in `bData.pool` with tile models
`t###_v` and building models `b###_v` (`v` = level of detail, 0 = full).

`tmap`: `u16, u16 grids, u16, u16 tile types`, then grids of 0x154 bytes:

```
+0x00 f32 origin[3]      +0x0C f32 axis_u[3]    +0x18 f32 axis_v[3]
+0x24 f32 cell size[3]   (x and z used: 420 Bespin/Ralltiir, 128 Academy, 100 Death Star)
+0x30 u32 width, u32 height, u32 cells (offset from the grid record)
cells: u8[width * height], row major (index = column + row * width)
       0x1F = empty, else bits 0-4 tile type, bits 6-7 quarter turns
```

A tile is drawn at `origin + column * size.x * axis_u + row * size.z * axis_v`
(its centre; tiles span -size/2 .. size/2), rotated about Y by its quarter
turns: one turn maps +X to -Z and +Z to +X (`FUN_7f35c8ec` precomputes the four
matrices, `FUN_7f35def0` draws). `bmap` has one 0x24 byte record per tile type,
`{u32 count, u32 building[8]}` (building type in the low half), and the tile's
`t###_0_lm` gives the matrix of each building relative to the tile.

## Geometry `_ge`

Relocatable image: pointers are offsets from the part start, 0 = NULL.
Renderer: RS2 `FUN_7fd276cc` → `FUN_7fd28ab0`; RS3 `FUN_7f397640` / `FUN_7f397738`.

```
root +0x04 -> format list (opaque), +0x08 -> format list (translucent)
format node   { next, material_list, ..., u8 vat_count @0x0B, u32 vat[vat_count] @0x0C }
material node { next, u16 material_index @0x04, mesh_list @0x08 }
mesh-list node{ next, mesh, u16 node_index @0x08 }
```

Mesh, RS2 (RS3 shifts the array block to +0x14 .. +0x30):

```
+0x00 u16 matrix_count    +0x02 u16 LOD entry  +0x04 u16 flags    +0x06 u8 relocated flag
+0x07 u8 LOD near         +0x08 u8 LOD far
+0x0C u16 *matrix_list    +0x10 positions    +0x14 normals   +0x18 colors (RGBA8)
+0x1C uvs                 +0x20 info         +0x24 display list   +0x28 display list size
RS3: +0x09 u8 extra UV sets
```

Flags → GXSetVtxDesc: `0x2000` per-vertex PNMTXIDX, `0x1000` NBT, `0x0800`
normals (RS3 only), `0x4000` color, `0x8000` texcoords; bits 0..3 = 16-bit
index for pos/normal/color/uv. RS3 meshes without a normal array index a global
s8 normal table at `0x80052C00` in the boot DOL.

Level of detail (RS2 `FUN_7fd25e08`, RS3 `FUN_7f395998`): `mesh+2` indexes the `ge+0x1C` table
(0x14 byte entries, `0xFFFF` = always drawn); entry `+0x10` points to float
switch distances. A mesh is drawn between `dist[near-1]` (near 0 = from the
camera) and `dist[far-1]` (far `0xFF` = no limit) and cross fades near the
edges. Bands form chains such as (0,1) (1,2) (2,0xFF), so the near index is the
LOD level; a few meshes span several levels, e.g. (2,0xFF) next to (2,3) (3,0xFF).
Typical distances: 25/120 m for craft, 600 or 10000 m for level objects. A model
may use several table entries with chains of different lengths. RS3 runs the
same test and additionally skips meshes whose node is hidden at run time.

VAT word (RS2 `FUN_7fd28640`, RS3 `FUN_7f398ef0`), one per vertex format index
(display list opcode & 7):

| bits | meaning |
|------|---------|
| 0-1  | position type: 0 s8, 1 s16, 2 f32 |
| 2-3  | normal type (same codes) |
| 4-5  | texcoord type |
| 6-10 | position fixed-point shift |
| 11-15| normal shift (hardware ignores it) |
| 16-20| texcoord shift |
| 21-22| RS2: 1 NBT, 2 NBT3; RS3: any → NBT3 |
| 23   | RS3: positions dequantized on the CPU |

Display lists are standard GX (`0x98` strips, `0x90` triangles, `0xA0` fans, `0x80` quads).

## Materials `_si` ("ABME")

`"ABME", u32 count`, then materials of 0x110 (RS2) / 0x120 (RS3) bytes: name[16],
type, flags (`0x400`/`0x800` = clamp S/T), diffuse and specular RGBA (RS2 +0x18,
+0x1C; RS3 +0x1C, +0x20), shininess (f32, RS2 +0x20, RS3 +0x24), reflectivity
(RS2 f32 +0x24, RS3 u8 +0x2D / 255), texture count at
+0x30 (u32 RS2, u8 RS3), texture records at +0x34 of 0x34 (RS2) / 0x48 (RS3)
bytes: `u16 width, u16 height, u8 mips, u8 GX format`, SHA-1 at +0x14 (RS2) /
+0x34 (RS3). RS3 tags one CMPR texture (the Bespin sky) as format 15; its data is
plain CMPR.

Map slots after the texture records give the texture index used for each map
type (`0xFF` = unused): RS2 six `{u8 texture, u8 mode}` at +0x104, RS3 five
`{u8 texture, u8 swap, u16}` at +0x10C. RS2 mode 3 means the map is the
texture's alpha; RS3 swap 1 means a color channel instead of alpha.

| map | RS2 | RS3 | use |
|---|---|---|---|
| color | 0 | 0 | |
| opacity | 1 | 2 | output alpha for alpha test (flag `0x4`) or blending (`0x4000`, additive `0x8000`); RS2 only reads it with flag `0x10` and a color map without full alpha (`FUN_7fd3c5ec`), RS3 `FUN_7f3cafec` |
| self lit | 2 | 3 | `(lighting + K * (1 - alpha)) * color`: alpha 0 shines (RS2 `FUN_7fd35a3c`, `FUN_7fd37098`) |
| bump | 3, 4 and 5 | 4 | see below |

RS2 picks the shader from a table at `0x7fdd96a0` indexed by the material type
(`FUN_7fd34f94`), e.g. type 8 cockpit, 24 emboss, 27 emboss + highlight (player
craft hulls). RS3 types are bit sets (`0x80000000 | bits`) that `FUN_7f3d6430`
maps to the same shader numbers (bit 0 lit, 2 textured, 3 masked highlight,
1 self lit, 5 tilt bump, 6 emboss; flag `0x100` picks 28-33, `0x4000` 26/27);
its shader table is at `0x7f672aa8`. A map naming the color texture reads its alpha, so the 1
bit alpha of a CMPR color map is a mask whose meaning depends on the slot: cut out
or blended transparency (trees, engine glow sprites), lit screens and buttons
(alpha 0) or emboss grooves (alpha 0, hulls). Without such a slot the game draws
the texture opaque and ignores its alpha. Neither game stores texture names:
materials keep the Maya shader name (`phong31`), textures only a SHA-1.

### Highlights and reflections

Shaders 4-13, 16-23, 27 and RS3 30-37 add a highlight (all materials with a
shininess above 0). A second texture coordinate is the view space normal turned
towards the half vector of the main light and scaled by `0.2 + 3.8 *
clamp(shininess)` (RS2 `FUN_7fd2f7f0`, RS3 `FUN_7f3c2b74`; constants in the
boot DOL's small data, RS2 `r2 = 0x8009EA20`). It samples a highlight texture
(RS2 `envmap/hilite`, 128x128 IA4, alpha falling linearly from the centre to
the edge), so the highlight is `1 - k * sin(angle)`, tinted by the specular
color. Shaders 6, 8, 17, 19, 27, 31, 33 (and their reflective forms) multiply it
by `clamp(4 * (green - 0.25))` of the color map: TEV swap table 1 is set to
`(R, G, B, G)` (`FUN_7fd34898`) and these stages read the color texture's
"alpha" through it.

Shaders 4-8, 16-19 and RS3 30-33 switch to a reflective variant (+5, +4, +4;
tables RS2 `0x7fdd89d8`, RS3 `0x7f67259c`) when the reflectivity is above 0.05
(RS2) or at least 20/255 (RS3). The variant samples the environment sphere map
with the normal (post matrix `0x43`) and lights the color map with
`lerp(lighting, environment, reflectivity)` (`C0.alpha = 255 * reflectivity`,
`FUN_7fd3c594`), i.e. like a metal of that metalness. The extractor writes the
reflectivity as the FBX `ReflectionFactor`, the specular color with
`SpecularFactor` 1 and a Blinn-Phong exponent with the same half width as the
game's highlight; masked highlights become a `*_specular` map (mask x color).

### Environment cubes

Each level package carries the cube its reflections show, six standalone images
`extra/envMap/{rgt,lft,top,bot,frt,bck}` (RS2 front end and briefing
`envMap/...`; RS2 also has loose `data/envmap/<name>/...` sets, the fallback
being `envmap/bespin`). The game renders the cube onto a tessellated sphere into
a sphere map texture (RS2 `FUN_7fd2ad30`, RS3 `FUN_7f3b58d4`). The faces are 64 to
256 texels square, CMPR, with a one texel transparent border, rendered like Maya's
envCube with Y up: `frt` looks along +Z, `bck` -Z, `rgt` +X, `lft` -X, image
right = view direction x up; `top` (+Y) has +Z up in the image, `bot` (-Y) -Z.
Only this layout makes the seams meet.

### Bump maps

Both games bump map in two ways, picked by the slot:

| | tilt map (indirect) | height map (emboss) |
|---|---|---|
| RS2 | slots 3 and 4 (modes 0, 1), IA4 | slot 5, I4 or the color map |
| RS3 | slot 4, CMPR | slot 4, I4/IA4 or the color map |

Emboss (RS2 `FUN_7fd3767c`, RS3 `FUN_7f3cdf28`) samples the height map at the
UV and at a `GX_TG_BUMP*` light shifted UV and subtracts them; material flag
`0x1000` (RS2) / `0x2000` (RS3) swaps the two. Player craft emboss the 1 bit
alpha of their color map this way (type 27 `FUN_7fd37f70` reads TEXA).

Tilt maps (RS3 `FUN_7f3d074c`) are read as an indirect texture with dynamic S/T
matrices that scale texture coordinates generated from the vertex tangent and
binormal; the offsets are added to the normal lookup into the light map. Two
channels hold how far the normal leans towards +u and +v of the first UV set,
zero at 127.5, i.e. the X and Y of a tangent space normal map without Z (about 2%
of texels lie outside the unit circle, so they are offsets, not unit vectors):
RS2 IA4 intensity = +u, alpha = down the image (-v); RS3 CMPR red (= blue) = +u,
green = +v (up the image). Axes and relative signs follow from the maps being
curl free, the overall sign from integrating them (noses and rivets come out
raised). The game scales both kinds with per level constants.

The extractor turns every bump map into an OpenGL convention normal map
(`normalize(tilt_u, tilt_v, 1)`, so a full tilt is 45 degrees; height maps lean
by 2 x the height difference per texel) and writes the height channel as a gray
`*_height` image. Self lit masks become an `*_emissive` image (color x (1 - mask)).
The color image keeps alpha only as transparency: the opacity map (resized into
the alpha channel when it is its own texture), or the texture's own alpha for
IA4, IA8, RGB5A3 and RGBA8. Other color maps are written opaque.

## Textures

`SMMJ` index: `"SMMJ", u32 count`, entries `{sha1[20], u32 offset}` (RS2, bytes)
or `{sha1[20], u32 offset/32, u8 missing_top_mips, u8, u16}` (RS3). Data is the
GX mip chain in the matching `_tpd`.

CMPR is decoded as the GameCube does (and Dolphin), not as DXT1: the two in
between colors blend 5/8 + 3/8, and the transparent entry of a 3 color block keeps
the average color instead of black. The game relies on that color under its alpha
masks.

RS2 offsets carry a level count in their top bits: with `n = offset >> 25` the
texture is a lower mip of a larger texture in the same pool, so its data starts
after the first `n` levels of a `(width << n, height << n)` chain at `offset &
0x1FFFFFF`. A 256x256 texture with `n = 1` is the second level of the 512x512
texture stored there (Tatooine buildings, the Star Destroyer hull, the briefing
room).

RS3 offsets use the top byte the same way, one nibble per axis (`8 | shift`):
`0x99` is one level below the texture at the same offset. Its levels come from
that texture, including the top ones it streams from `gTexPool.cData`. Entries
with unequal shifts (`0x90`) also have their own copy in the global pool.

RS3 streams full-resolution top mips from `gTexPool.cData`: `"ctp0", u32 count`,
entries `{pool_offset, file_offset, packed_a, packed_b}`; `pool/global_tpool` is
its SMMJ index (offsets/32 = `pool_offset`). Part A (top mips, may be empty) is at
`file_offset`, part B at the next 32-byte boundary. They hold the levels the
local pools lack (the SMMJ `missing_top_mips`): B the lowest of them, A the one
above. Both use Factor 5 LZ (`FUN_7f3e9ffc`): 4-byte header, byte 3 selects the
variant. A part whose packed size equals its level's size is stored raw and read
straight into place (`FUN_7f3e9604`), as for a few Endor and Dantooine rocks and
trees.

* literal: `0nnnnnnn` → copy n+1 bytes
* variant 0 match: `1lllldd dddddddd` → length l+3, distance d+1 (11 bits)
* variant 1 match: `1lllllll dddddddd dddddddd` → length l+4, distance d+1

Standalone images: 32-byte header `{u16 width, u16 height, u8, u8 format, u8,
u8 has_mips}`; format codes `0` I4, `1` I8, `2` IA4, `3` IA8, `9` RGB5A3,
`0xA`/`0xB` RGBA8, `0xC` CMPR.

## Setup `_su` and node placement

Header (u32 words, offsets relative to the part):

| word | meaning |
|------|---------|
| 0 | node command stream |
| 1, 2 | compiled setup code: offset, size (see "Setup code") |
| 3, 4 | code relocations: table, count |
| 5, 6 | count, table of 8-byte entries (pointer at +4) |
| 8 | animation channel count |
| 9, 10 | local storage of the setup code: size, offset of its initial values |
| 11, 12 | transform nodes: count, offset |
| 13, 14 | joints: count, offset |
| 15, 16 | cameras: count, offset (0x20 bytes: focal length, film aperture ...) |
| 17, 18 | lights: count, offset (0x1C bytes: color ...) |
| 24, 25 | RS3: constraint solver data, pointer array (non-zero for IK rigs) |

The game relocates the part on load (RS2 `FUN_7fd283f0`, RS3 `FUN_7f3985f8`):
the pointer words above, the tables at words 6, 20, 22 (RS2) / 6, 20, 23, 25,
27, 29 (RS3) and the code. RS2 code relocations are `u32` offsets of 32-bit
addresses; RS3 entries are `{u8 type, u8, u16 low half, u32 offset}` with type
0 = 32-bit address, 1 = high half (`lis`), 2 = low half.

Command stream (RS2 `FUN_7fd24ef4`): u16 values until `0xFFFF`.
`0x8000|k` sets the parent of following nodes to output matrix k-1 (0 = none),
`0x4000|i` evaluates joint i, any other value evaluates transform node i. Each
evaluated node appends one world matrix.

Transform node (Maya transform; 0x68 bytes in RS2, 0x6C in RS3), floats:
0 scale pivot, 3 scale, 6 scale pivot translate, 9 rotate pivot, 12 rotate axis
quaternion, 16 euler rotation, 19 rotate pivot translate, 22 translate, then the
rotation order (RS2 u32 at +0x64, RS3 u32 at +0x68; RS3 local matrix
`FUN_7f3925cc`).
`v' = R * (S * (v - sp) + sp + spt - rp) + rp + rpt + t`, `R = euler * axis`.

Joint (0x54 bytes): 0 scale, 3 rotate axis quaternion, 7 euler rotation,
10 joint orient quaternion, 14 parent scale, 17 translate, 20 rotation order.
`M = T * inverse(parent scale) * (orient * euler * axis) * S`.

Euler order codes (`FUN_7fd249c4`, quaternions multiplied left to right):
0 `qz*qy*qx`, 1 `qy*qz*qx`, 2 `qz*qx*qy`, 3 `qx*qz*qy`, 4 `qx*qy*qz`, 5 `qy*qx*qz`.

Render palette (RS2 `FUN_7fd25e08`): blocks of the `ge+0x28` chain (count
`ge+0x26`) are `{next, u16 node (1-based, 0 = root), u16 inverse bind index
(0xFFFF = none), u16 block index, ...}`; `palette[index] = world[node-1] *
inverse_bind[ge+0x00][bind]`. A mesh-list node's u16 at +8 picks its palette
entry. With an inverse bind the vertices are model space, without one they are
local to the node (walkers, level troopers, turrets). `mesh+2` is an index into
the `ge+0x1C` LOD table (see Geometry), not a matrix.

## Skinning

Inverse bind matrices (3x4 floats) are at `ge+0x00`. Weighted palette blocks
add weight lists to the chain block; records are `{u16 joint (1-based), u16
inverse bind index, f32 weight}` and a palette matrix is
`sum(weight * world[joint-1] * inverse_bind[k])`.

* RS2 (`FUN_7fd26994`): `u16 n @+0x0A`, then n entries `{u16, u16 count, u32
  records}` defining palette entries `index .. index+n-1`. Meshes select
  entries per vertex (GX matrix index / 3 into the mesh's `matrix_list`).
* RS3: `u16 a @+0x0C, u16 b @+0x0E`, then `a + b` pairs `{u32 count, u32
  records}` at +0x10. The first a are palette entries `index ..`, the other b
  are matrices for CPU skinning. A mesh's `matrix_list` (count at `mesh+0x00`)
  holds `(matrix, n_positions, n_normals)` runs over its position/normal
  arrays; with one run the mesh is drawn with that palette entry, with more the
  CPU routine (`0x8003BD44`, boot DOL) transforms each run with its CPU
  skinning matrix (RS3 `FUN_7f399520`).

Some skins were bound to a uniformly scaled skeleton (Luke's pilot: x13), so
`inverse(inverse_bind)` is not the rest pose; the stored vertices are in that
bind space. Cloth (Darth Vader's cape in RS3) is stored flat; the game drapes
it at run time.

RS3 `_nm` holds name tables: `u32 count`, count offsets of section headers
`{u32 data, u32 entries, name}`, entries `{u8, u16 key, u16 value, name}`.
`matrixarray` names node output matrices (value = index), `jointarray` and
`transformarray` name joints and transform nodes, `locatormatrix` lists
locators (key `0x8000|i`, value = parent matrix).

## Animation

### Setup code

Each model's Maya dependency graph (animation curves feeding attributes,
expressions, constraints, IK) is compiled to PowerPC code in `_su`. The game
calls it with `r3` = context, `f1` = time / 24, `f2` = frame, `f3` = 24 and
`r2` = end of the code (its constant pool); RS2 `0x8000a410` and RS3
`0x80013c80` (boot DOLs) build the context:

```
+0x00 transform attribute array (0x64 bytes per node RS2, 0x68 RS3)
+0x04 joint attribute array (0x50 bytes per joint)
+0x08 camera array            +0x0C light array
+0x10 curve outputs (16 bytes per channel, value at +0)
+0x14 local storage (initialised from _su word 10)
+0x18 curve pointer table of the clip
+0x1C curve sampler (RS2 0x8000a3a4, RS3 0x8003a74c): r3 = curve, r4 = output, f1 = frame
+0x20 RS3: constraint solver entry (0x8003a6f8 -> 0x80014938)
+0x24 RS3: solver data (_su word 25)
```

The attribute arrays start as copies of the `_su` records (bind pose) and the
code overwrites what is animated; the node evaluator then builds the matrices
from them (RS2 `FUN_7fd24ef4` blends up to 8 such pose slots by weight). RS3's
solver needs the game's own code and globals: it reads the slot and `_su`
arrays through `0x800614e0` and keeps a per-evaluation node cache (bitset at
`0x800644f0`, cleared by `0x8001572c` before every call). The extractor runs
the setup code, and the solver, in a Gekko interpreter with both executables
mapped (`rsextractor/ppc.py`, `rsextractor/anim.py`).

### Clips `_an`

A model's own `_an` and its standalone clips share one format: `u32 object
offset`, timed event records (footsteps and similar), then the object.

* RS2 object: `{u32 end, f32 start, f32 end, u32 curve offsets[channels]}`,
  offsets relative to the object; the channel count comes from `_su` word 8.
* RS3 object: `{u32, u32, u32, f32 start, f32 end, u32 count, u32 curve
  offsets[count]}`. RS3 models' own `_an` parts are a bare object.

Frames run at 24 per second. RS2 keeps clips next to their model (also as
loose archive entries, `data/pilots/pilLuke/...`); RS3 keeps character clips in
a sibling entry (`hanCharacter_base` -> `hanCharacter_anim`).

Curve (sampler RS2 `0x80009ff8`, RS3 `0x8003a74c`):

```
u8 flags, u8, u16 segment count, f32 value scale, f32 end time, keys...
```

* flags `0x02`: constant, value = the f32 at +4.
* otherwise `count + 1` keys of `stride` halfwords, stride = (8 with flags
  `0x08`, 5 with `0x04`, else 4) + (2 if flags & 9 else 1). A key starts with
  its time: f32 when flags & 9, else u16 / 128. With flags `0x08` the closing
  key may be missing (the last segment runs to the end time).
* segment value `((a*u + b)*u + c)*u + d`, `u = t - t_key`:
  * flags `0x08`: a, b, c, d as f32;
  * else s16 d, then a and b as 24-bit floats (top three bytes of an f32) and,
    with flags `0x04`, s16 c. s16 values are scaled by `2^-14 * value scale`.
    Without c the segment ends on the next key's value:
    `c = (d_next - d) / dt - (a*dt + b) * dt`.
