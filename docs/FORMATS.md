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

Header (u32 words): `[0]` node command stream, `[1]`/`[2]` compiled code and
size (per model expression/constraint code, relocated by `[3]`/`[4]`),
`[11]`/`[12]` transform node count/offset, `[13]`/`[14]` joint count/offset.

Command stream (RS2 `FUN_7fd24ef4`): u16 values until `0xFFFF`.
`0x8000|k` sets the parent of following nodes to output matrix k-1 (0 = none),
`0x4000|i` evaluates joint i, any other value evaluates transform node i. Each
evaluated node appends one world matrix.

Transform node (Maya transform; 0x68 bytes in RS2, 0x6C in RS3), floats:
0 scale pivot, 3 scale, 6 scale pivot translate, 9 rotate pivot, 12 rotate axis
quaternion, 16 euler rotation, 19 rotate pivot translate, 22 translate, then the
rotation order (RS2 u32 at +0x64, RS3 byte at +0x65).
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
the `ge+0x1C` bounding volume table, not a matrix.

## RS3 skinning

* `ge+0x00` → 3x4 inverse bind matrices.
* Skin block (a chain block with `u16 a @+0x0C, u16 b @+0x0E`): `a + b` pairs
  `{u32 n, u32 ptr}`, each `ptr` → `n` records `{u16 joint (1-based), u16
  inverse bind index, f32 weight}`. Every pair yields one palette matrix
  `sum(weight * world[joint-1] * inverse_bind[k])`; at bind pose these are all
  identity, so stored positions are model space.
* Skinned mesh `matrix_list` (count at `mesh+0x00`) holds `(palette index,
  n_positions, n_normals)` runs over the position/normal arrays; the CPU routine
  at `0x8003BD44` (boot DOL) transforms each run with its palette matrix.
* Cloth (Darth Vader's cape) is stored flat in the bind pose; the game drapes it
  at run time.
