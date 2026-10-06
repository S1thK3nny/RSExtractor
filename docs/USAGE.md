# Usage

```sh
python -m rsextractor <command> <disc image> [options]
```

After `pip install .` the same commands run as `rsextractor <command> ...`. The
game is detected from the disc, so the same commands work for both games.

## Commands

| Command | What it does |
| --- | --- |
| `info` | Show the disc and a summary of its archives |
| `list` | List the models on the disc (`-f` narrows the list) |
| `extract` | Export models (FBX), textures and images |

## `extract` options

| Option | Effect |
| --- | --- |
| `-o`, `--out DIR` | Output directory (default `./output`); the game writes into `DIR/RS2` or `DIR/RS3` |
| `-f`, `--filter PATTERN` | Glob or substring on `archive/entry/model`; repeat for several |
| `-t`, `--texture-format {png,tga}` | File format for textures and standalone images (default `png`) |
| `--embed` | Embed the textures inside the FBX files instead of referencing them |
| `--no-images` | Skip standalone images (menus, backdrops, fonts) |
| `--no-tile-levels` | Skip assembling tile levels (Death Star, Bespin city ...) |
| `--no-object-levels` | Skip placing the objects of each level resource |
| `--no-terrain` | Skip the terrain of open levels |
| `--no-maps` | Skip the full map of each level resource |
| `--no-animations` | Export skeletons and skins but no animation clips (much faster) |
| `--no-skeletons` | Static meshes only: no bones, skins or animation (parts still keep their pivots) |
| `-j`, `--jobs N` | Processes for evaluating animations (default: one per CPU core) |
| `-v`, `--verbose` | Print tracebacks of failed models and the list of textures not found |

## Examples

```sh
# everything (models + textures + images)
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

## Filtering level content

The generated level files have fixed suffixes, so a filter on the suffix exports
only that kind of file:

| Filter | Exports |
| --- | --- |
| `-f /terrain` | The terrain of every open level |
| `-f /tiles` | The assembled tile levels |
| `-f /objects` | The placed objects of every level resource |
| `-f /map` | The full map of every level resource |
| `-f l08/02/map` | The full map of one level resource (terrain, tiles, objects, sky) |

In Rebel Strike, `-f` with the name of any level that uses a model from the
shared pool selects that model too. See [OUTPUT.md](OUTPUT.md) for what each
file contains and [LEVELS.md](LEVELS.md) for which folder is which mission.

## Time and size

Animation clips are evaluated by running the game's own setup code in a small
PowerPC interpreter, which is CPU heavy. It runs in parallel on all cores; a full
Rebel Strike extraction with its ~1100 clips writes about 10 GB in roughly half
an hour. `--no-animations` skips the clips, and the `--no-...` options for level
files cut the size a lot (the largest maps are several hundred MB each).
