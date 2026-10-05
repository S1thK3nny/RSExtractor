"""Command line interface: ``python -m rsextractor ...``"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
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


class Progress:
    """What is being extracted right now: one status line that rewrites itself on a
    terminal, or one line per item when the output goes to a file."""

    def __init__(self):
        self.tty = sys.stdout.isatty()
        self.t0 = time.time()
        self.shown = False

    def show(self, fraction: float, text: str) -> None:
        elapsed = int(time.time() - self.t0)
        line = f"  {fraction:4.0%} {elapsed // 60:3d}:{elapsed % 60:02d}  {text}"
        if not self.tty:
            print(line, flush=True)
            return
        width = shutil.get_terminal_size().columns - 1
        sys.stdout.write("\r" + line[:width].ljust(width))
        sys.stdout.flush()
        self.shown = True

    def clear(self) -> None:
        if self.shown:
            sys.stdout.write("\r" + " " * (shutil.get_terminal_size().columns - 1) + "\r")
            sys.stdout.flush()
            self.shown = False

    def log(self, *args) -> None:
        self.clear()
        print(*args, flush=True)


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
        progress = Progress()
        exporter = Exporter(game, out, embed_textures=args.embed, texture_format=args.texture_format,
                            skeletons=not args.no_skeletons, animations=not args.no_animations,
                            runtime=runtime, pool=pool, log=progress.log)
        failures = 0
        print("Exporting models...")
        for number, m in enumerate(game.iter_models(_filter(args.filter)), 1):
            label = f"{m.archive}/{m.entry}/{m.name}"
            done, total = game.position
            progress.show(done / max(total, 1), f"model {number}  {label}")
            try:
                exporter.export_model(m)
            except Exception as exc:  # keep going; report at the end
                failures += 1
                progress.clear()
                print(f"  FAILED {label}: {exc}", file=sys.stderr, flush=True)
                if args.verbose:
                    traceback.print_exc()
        progress.clear()
        if pool is not None:
            pool.shutdown()
        if not args.no_tile_levels:
            print("Assembling tile levels...")
            for level in game.iter_tile_levels(_filter(args.filter)):
                label = f"{level.archive}/{level.entry}"
                progress.show(1.0, f"tile level  {label}")
                try:
                    exporter.export_tile_level(level)
                except Exception as exc:
                    failures += 1
                    progress.clear()
                    print(f"  FAILED tile level {label}: {exc}", file=sys.stderr, flush=True)
                    if args.verbose:
                        traceback.print_exc()
            progress.clear()
        if not args.no_object_levels:
            print("Placing level objects...")
            for level in game.iter_object_levels(_filter(args.filter)):
                label = f"{level.archive}/{level.entry}"
                progress.show(1.0, f"objects  {label}")
                try:
                    exporter.export_object_level(level)
                except Exception as exc:
                    failures += 1
                    progress.clear()
                    print(f"  FAILED objects {label}: {exc}", file=sys.stderr, flush=True)
                    if args.verbose:
                        traceback.print_exc()
            progress.clear()
        if not args.no_terrain:
            print("Building terrain...")
            for level in game.iter_terrains(_filter(args.filter)):
                label = f"{level.archive}/{level.entry}"
                progress.show(1.0, f"terrain  {label}")
                try:
                    exporter.export_terrain(level)
                except Exception as exc:
                    failures += 1
                    progress.clear()
                    print(f"  FAILED terrain {label}: {exc}", file=sys.stderr, flush=True)
                    if args.verbose:
                        traceback.print_exc()
            progress.clear()
        images = 0
        if not args.no_images:
            print("Exporting standalone images...")
            images = export_images(game, out, _filter(args.filter), args.texture_format,
                                   lambda label, fraction: progress.show(fraction, f"image  {label}"))
            progress.clear()
        s = exporter.stats
        if args.verbose and exporter.missing:
            print("Textures not found:")
            for line in sorted(set(exporter.missing)):
                print(f"  {line}")
        summary = [
            f"{s['models']} models ({s['skeletons']} with skeletons, {s['animations']} animations)",
            f"{s['textures']} textures",
            f"{s['normal_maps']} normal maps",
            f"{s['emissive_maps']} emissive maps",
            f"{s['specular_maps']} specular maps",
            f"{s['environment_maps']} environment maps",
            f"{s['tile_levels']} tile levels",
            f"{s['object_levels']} object levels",
            f"{s['terrains']} terrains",
            f"{s['wireframes']} wireframes",
            f"{images} images",
            f"{s['skipped']} empty models skipped",
            f"{s['missing_textures']} textures not found",
            f"{failures} failures",
        ]
        print(f"Done in {time.time() - t0:.0f}s:")
        for line in summary:
            print(f"\t- {line}")
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
    pe.add_argument("--no-tile-levels", action="store_true",
                    help="skip assembling tile levels (Death Star, Bespin city ...) into one FBX each")
    pe.add_argument("--no-object-levels", action="store_true",
                    help="skip placing the objects of each level resource into one FBX each")
    pe.add_argument("--no-terrain", action="store_true",
                    help="skip the terrain of open levels (heightmap mesh, baked ground texture, blend layers)")
    pe.add_argument("--no-animations", action="store_true", help="export skeletons and skins but no animation clips")
    pe.add_argument("--no-skeletons", action="store_true", help="no bones, skins or animation (parts still keep their pivots)")
    pe.add_argument("-j", "--jobs", type=int, default=0,
                    help="processes for evaluating animations (default: one per CPU core)")
    pe.add_argument("-v", "--verbose", action="store_true")
    pe.set_defaults(func=cmd_extract)

    args = p.parse_args(argv)
    return args.func(args)
