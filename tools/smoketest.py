"""Quick regression run: extract a few models per game that together cover every
feature, instead of the whole disc (a full Rebel Strike run writes ~10 GB in ~30 min).

    python tools/smoketest.py <rs2.iso> [<rs3.iso>] [-o output/_test] [-- extra extract args]

Each disc goes to ``<out>/RS<n>`` (replaced on every run). Fails when a model fails
or a texture is missing.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

# filter -> what it covers
SAMPLES = {
    2: {
        "plCraft/awing_base": "craft: emboss from color alpha (type 27), self lit cockpit, animation",
        "plCraft/tie_base": "opacity map (engine glow sprite)",
        "gfxYavinTreeTop1_1": "opacity map (cut out leaves)",
        "dsTurDest": "IA4 tilt bump map",
        "pilots/pilLuke": "skinned skeleton bound at scale, animation",
        "l03/00/atatAI": "walker animation",
        "lvlPack01/l05/00/stardest": "417 meshes, textures stored as lower mips",
        "l00/01/sky00": "RGBA8 sky",
        "l02/00/XWing_wm": "reflective canopy, level environment cube",
        "frontend/goldMedal": "reflective, front end environment cube",
        "gfxWIREOverlaysXWing": "briefing wireframe from the collision hull",
        "l08/02/tiles": "RS2 tile level: _tile##/_bldg## models, buildings from the tile _lm",
        "l05/01/objects": "level objects (dat, RS2 header), Star Destroyer parts on hull nodes",
        "l08/02/terrain": "terrain from dat (centre and rings), mixmaps blend layers and bake",
        "l08/02/map": "map: terrain, tiles, objects, sky08 with horizon rings 1 and 3 from l08/00",
        "l07/02/map": "map: sky models of the second resource, Academy radar dishes dropped on the ground",
    },
    3: {
        "plCraft/xwing_base": "craft + cockpit: emboss from color alpha, self lit, animation, wireframe (compact hull records)",
        "plCraft/hanEmpire_base": "CMPR tilt bump, character skeleton and clips",
        "plCraft/tie_base": "opacity map",
        "plCraft/atst_base": "walker IK solver",
        "L_03_Ralltiir_lvl/01c00/StarDestroyerBigBall": "I4 height bump",
        "L_03_Ralltiir_lvl/01c01/fltATAT_ai": "bData.pool model, shared clips",
        "coop_08_Bespin_lvl/01/BespinCityVista": "opacity, streamed gTexPool mips",
        "coop_08_Bespin_lvl/01/BespinSkyClouds": "material format 15 (CMPR)",
        "E_01_Luke_lvl/00/EndorSky": "RGBA8 sky",
        "B_02_Hoth_lvl/01/grndHothMedicalCenter": "I4 opacity and bump textures",
        "hangarCalamari_lvl/00/Awing_hangar": "reflective, masked highlight, level environment cube",
        "coop_08_Bespin_lvl/02/tiles": "tile level layout (tmap, bmap, building matrices)",
        "frontend_lvl/05c00/LS_PLY_Farmboy": "cutscene actor clip (luke_dance from the credits cast)",
        "RAC_F_DeathStarTrench_lvl/01/objects": "level objects (dat): wall turrets need X before Z, flt models",
        "B_02_Hoth_lvl/03/objects": "Star Destroyer parts on named hull locators",
        "RAC_F_DeathStarTrench_lvl/01/tiles": "tile layout half a cell in from the grid corner (matches the objects)",
        "Training_lvl/01/terrain": "h16 terrain, levels 4..7, I4 blend masks",
        "B_03_Bespin_lvl/02/terrain": "mirrored h16 terrain (Bespin's underside)",
        "W_02_Ep2_lvl/02/map": "map: sky object (class 249) package, additive star layer, horizon band left out",
        "B_02_Hoth_lvl/03/map": "map: space level, sky package referenced twice",
        "hangarCalamari_lvl/01/map": "map: indoor rooms placed by portals",
    },
}

GAME_IDS = {"GSW": 2, "GLR": 3}


def game_of(iso: str) -> int:
    with open(iso, "rb") as f:
        head = f.read(6).decode("ascii", "replace")
    if head[:3] not in GAME_IDS:
        sys.exit(f"{iso}: not Rogue Squadron II or III ({head})")
    return GAME_IDS[head[:3]]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("isos", nargs="+")
    p.add_argument("-o", "--out", default=os.path.join("output", "_test"))
    # everything after -- goes to extract (an argparse remainder would also swallow -o)
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)
    args, extra = p.parse_args(argv[:cut]), argv[cut + 1:]
    ok = True
    for iso in args.isos:
        game = game_of(iso)
        target = os.path.join(args.out, f"RS{game}")
        shutil.rmtree(target, ignore_errors=True)
        print(f"== RS{game}: {len(SAMPLES[game])} samples -> {target}")
        cmd = [sys.executable, "-m", "rsextractor", "extract", iso, "-o", args.out, "-t", "tga", "--no-images"]
        for f in SAMPLES[game]:
            cmd += ["-f", f]
        t0 = time.time()
        lines = []
        with subprocess.Popen(cmd + extra, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"}) as proc:
            for line in proc.stdout:
                print(line, end="", flush=True)
                lines.append(line)
        done = re.search(r"Done in .*", "".join(lines), re.S)
        fine = (proc.returncode == 0 and done is not None
                and "\t- 0 textures not found\n" in done.group() and "\t- 0 failures" in done.group())
        print(f"   {'OK' if fine else 'FAILED'} in {time.time() - t0:.0f}s")
        ok &= fine
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
