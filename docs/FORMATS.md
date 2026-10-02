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

A model named `X` consists of `X_su` (setup), `X_ge` (geometry), `X_si`
(materials), optional `X_an` (animation), `X_cl` (collision) and, in RS3, `X_nm`
(names). Texture pools are `*_tpi` + `*_tpd`.

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
type, flags (`0x400`/`0x800` = clamp S/T), two RGBA colors, texture count at
+0x30 (u32 RS2, u8 RS3), texture records at +0x34 of 0x34 (RS2) / 0x48 (RS3)
bytes: `u16 width, u16 height, u8 mips, u8 GX format`, SHA-1 at +0x14 (RS2) /
+0x34 (RS3).

## Textures

`SMMJ` index: `"SMMJ", u32 count`, entries `{sha1[20], u32 offset}` (RS2, bytes)
or `{sha1[20], u32 offset/32, u8 missing_top_mips, u8, u16}` (RS3). Data is the
GX mip chain in the matching `_tpd`.

RS3 streams full-resolution top mips from `gTexPool.cData`: `"ctp0", u32 count`,
entries `{pool_offset, file_offset, packed_a, packed_b}`; `pool/global_tpool` is
its SMMJ index (offsets/32 = `pool_offset`). Part A (top mips, may be empty) is at
`file_offset`, part B at the next 32-byte boundary. Both use Factor 5 LZ
(`FUN_7f3e9ffc`): 4-byte header, byte 3 selects the variant.

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
