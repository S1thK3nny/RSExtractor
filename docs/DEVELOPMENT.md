# Development

## Smoke test

`tools/smoketest.py` extracts a handful of models per game that together cover
every feature (skeletons, IK, pool models, streamed textures, every bump, opacity
and self lit case, tile levels, objects, terrain and maps) into `output/_test` in
about four minutes, instead of a full run (Rebel Strike alone writes ~10 GB in
~30 min). It fails on any failed model or missing texture; arguments after `--` go
to `extract`:

```sh
python tools/smoketest.py rs2.iso rs3.iso -o output/_test -- -v
```

## FBX validator

`tools/fbxdump.py` is an independent FBX reader that validates the binary
structure of exported files:

```sh
python tools/fbxdump.py model.fbx --tree
```

## Code layout

| Module | Role |
| --- | --- |
| `cli.py`, `__main__.py` | Command line interface |
| `disc.py` | GameCube disc image reader (`.iso`, `.gcm`, `.nkit.iso`) |
| `archive.py` | Resource archives (`*.dir` + `*.dat`) and packages |
| `extract.py` | End-to-end extraction: disc image to FBX models and textures |
| `model.py` | Geometry (`_ge`) |
| `collision.py` | Collision hulls (`_cl`), used for wireframes |
| `material.py` | Materials (`_si`) |
| `texture.py`, `gxtexture.py` | Texture pools and GX texture decoding |
| `bump.py` | Bump, self lit and highlight masks to normal, emissive and specular maps |
| `envmap.py` | Environment cubes to panoramas |
| `skeleton.py`, `rig.py` | Node hierarchy (`_su`), skeletons and skin weights |
| `anim.py`, `ppc.py` | Animation clips (`_an`) evaluated by a small PowerPC interpreter |
| `placement.py` | Level objects (`dat`) |
| `tiles.py` | Tile levels (`tmap` + `bmap`) |
| `terrain.py` | Terrain heightmaps and blend layers |
| `sky.py` | Which sky models a level shows and where maps put them |
| `levels.py` | Mission titles, written as `LEVELS.md` |
| `scene.py` | Format-neutral scene handed to the exporters |
| `fbx.py` | Binary FBX 7.4 writer |

The file formats behind these modules are described in [FORMATS.md](FORMATS.md).

## Alternative output formats

FBX is the default. The exporter works from a format-neutral scene
(`rsextractor/scene.py`), so glTF 2.0 (`.glb`) or OBJ/MTL writers can be
added without touching the game parsers. glTF would be the better choice for
web or engine pipelines.
