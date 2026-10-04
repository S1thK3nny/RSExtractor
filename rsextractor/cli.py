"""Command line interface: ``python -m rsextractor ...``"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
import traceback

from .disc import Disc
from .anim import Runtime
from .rig import make_pool
from .extract import TEXTURE_FORMATS, Exporter, GameData, detect_game, export_images


def _filter(patterns):
    if not patterns:
        return None
    pats = [f"*{p.lower()}*" for p in patterns]
    return lambda label: any(fnmatch.fnmatch(label.lower(), p) for p in pats)


def cmd_info(args) -> int:
    with Disc(args.iso) as disc:
        print(f"{disc.game_id}  {disc.title}")
        game = GameData(disc)
        print(f"Rogue Squadron {'II' if game.game == 2 else 'III'}, {len(game.archives)} archives")
        for a in game.archives:
            files = list(a.files())
            print(f"  {a.base}: {len(files)} entries")
    return 0


def cmd_list(args) -> int:
    with Disc(args.iso) as disc:
        game = GameData(disc, log=lambda *_: None)
        for m in game.iter_models(_filter(args.filter)):
            print(f"{m.archive}/{m.entry}/{m.name}  [{' '.join(sorted(m.parts))}]")
    return 0


def cmd_extract(args) -> int:
    with Disc(args.iso) as disc:
        game_no = detect_game(disc)
        out = os.path.join(args.out, f"RS{game_no}")
        print(f"{disc.game_id}  {disc.title} -> {out}")
        game = GameData(disc)
        t0 = time.time()
        print("Indexing texture pools...")
        game.index_textures()
        runtime = pool = None
        if not args.no_skeletons and not args.no_animations:
            boot, game_dol = disc.read_dol(), disc.read_game_dol()
            jobs = args.jobs or os.cpu_count() or 1
            pool = make_pool(boot, game_dol, game.game, jobs)
            if pool is None:
                runtime = Runtime(boot, game_dol, game.game)
        exporter = Exporter(game, out, embed_textures=args.embed, texture_format=args.texture_format,
                            skeletons=not args.no_skeletons, animations=not args.no_animations,
                            runtime=runtime, pool=pool)
        failures = 0
        for m in game.iter_models(_filter(args.filter)):
            label = f"{m.archive}/{m.entry}/{m.name}"
            try:
                path = exporter.export_model(m)
                if path and args.verbose:
                    print(f"  {label}")
            except Exception as exc:  # keep going; report at the end
                failures += 1
                print(f"  FAILED {label}: {exc}", file=sys.stderr)
                if args.verbose:
                    traceback.print_exc()
        if pool is not None:
            pool.shutdown()
        images = 0
        if not args.no_images:
            print("Exporting standalone images...")
            images = export_images(game, out, _filter(args.filter), args.texture_format)
        s = exporter.stats
        if args.verbose and exporter.missing:
            print("Textures not found:")
            for line in sorted(set(exporter.missing)):
                print(f"  {line}")
        print(f"Done in {time.time() - t0:.0f}s: {s['models']} models ({s['skeletons']} with skeletons, "
              f"{s['animations']} animations), {s['textures']} textures, {s['normal_maps']} normal maps, {s['emissive_maps']} emissive maps, "
              f"{images} images, {s['skipped']} empty models skipped, "
              f"{s['missing_textures']} textures not found, {failures} failures")
    return 1 if failures else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rsextractor",
                                description="Extract models and textures from Star Wars Rogue Squadron II/III (GameCube).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="show disc and archive summary")
    pi.add_argument("iso")
    pi.set_defaults(func=cmd_info)

    pl = sub.add_parser("list", help="list models")
    pl.add_argument("iso")
    pl.add_argument("-f", "--filter", action="append", help="glob or substring on archive/entry/model")
    pl.set_defaults(func=cmd_list)

    pe = sub.add_parser("extract", help="export models (FBX) and textures (PNG or TGA)")
    pe.add_argument("iso")
    pe.add_argument("-o", "--out", default="output", help="output directory (default: ./output)")
    pe.add_argument("-f", "--filter", action="append", help="glob or substring on archive/entry/model")
    pe.add_argument("-t", "--texture-format", choices=sorted(TEXTURE_FORMATS), default="png",
                    help="file format for textures and standalone images (default: png)")
    pe.add_argument("--embed", action="store_true", help="embed textures inside the FBX files")
    pe.add_argument("--no-images", action="store_true", help="skip standalone images (menus, backdrops, fonts)")
    pe.add_argument("--no-animations", action="store_true", help="export skeletons and skins but no animation clips")
    pe.add_argument("--no-skeletons", action="store_true", help="no bones, skins or animation (parts still keep their pivots)")
    pe.add_argument("-j", "--jobs", type=int, default=0,
                    help="processes for evaluating animations (default: one per CPU core)")
    pe.add_argument("-v", "--verbose", action="store_true")
    pe.set_defaults(func=cmd_extract)

    args = p.parse_args(argv)
    return args.func(args)
