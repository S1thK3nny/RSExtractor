"""Which mission each level folder is, written as ``LEVELS.md`` next to the output.

The output keeps the disc's own paths: ``lvlPackNN`` is the archive file the level sits
in (only storage, about four levels each), the next folder the level, then its
resources. Rogue Leader loads ``l%02d/%02d`` (level, resource), Rebel Strike
``<Level>_lvl/%02d``. Resource ``00`` is what the whole level shares, ``01`` and up
the parts the game loads one at a time on top of it (the next part follows in the same
mission), and ``NNcMM`` the cutscene ``MM`` of part ``NN`` (``l%02d/%02dc%02d``, RS2
``FUN_7fd52a58``; its rig is ``cut<level><part - 1><MM>``).

Rogue Leader titles: the mission select picks a level number and shows the text
``FrntEnd/rbr/lvl/<key>`` from the table at ``0x7fde2a1c`` indexed by that same number
(``FUN_7fd73690`` stores it as the level to load, ``FUN_7fd73dac`` reads the title), so
levels 0..10 are the missions in story order and 11, 12, 15, 16, 17 the bonus
missions. Texts are in ``data/text/English``. Level 14 is loaded by a front end menu
entry (``FUN_7fd76204``) like 13 and 23 (``FUN_7fd7618c``, ``FUN_7fd76114``); 22 at
start up (``FUN_7fc032bc``) and when a level ends in mode 5 (byte ``r13 - 0x4ab1``,
``FUN_7fc002c0``).

Rebel Strike keeps its text as packages (``txtTables``, ``txtData``) zlib packed inside
the boot DOL, one per language. Its title table at ``0x7f6841e4`` lists
``frntend/rbr/lvl2/<key>`` for the story levels in the order of ``levelInfo``
(``Training``, ``E_01``, ``E_02``, the hangars without a title, ``L_01`` ..), the key
being the folder's letter and number, and the Rogue Leader titles (``rbr/lvl``) for the
co-op levels, whose numbers are the Rogue Leader level numbers. Versus levels are named
by ``FrntEnd/versus/levelSelect/levelNames/<folder>``.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Dict, List, Tuple

# level number -> (title, what it is)
RS2_LEVELS: Dict[int, Tuple[str, str]] = {
    0: ("Tatooine Training", "Training (the game picks one of its four times of day)"),
    1: ("Death Star Attack", "Mission 1"),
    2: ("Ison Corridor Ambush", "Mission 2"),
    3: ("Battle of Hoth", "Mission 3"),
    4: ("Prisons of the Maw", "Mission 4"),
    5: ("Razor Rendezvous", "Mission 5"),
    6: ("Vengeance on Kothlis", "Mission 6"),
    7: ("Imperial Academy Heist", "Mission 7"),
    8: ("Raid on Bespin", "Mission 8"),
    9: ("Battle of Endor", "Mission 9"),
    10: ("Strike at the Core", "Mission 10 (parts 03 and 04 instead of 01 and 02 for one of the craft)"),
    11: ("Death Star Escape", "Bonus mission"),
    12: ("The Asteroid Field", "Bonus mission"),
    13: ("", "Sky and a TIE fighter cutscene, started from a front end menu"),
    14: ("", "Ending celebration cutscene, started from a front end menu"),
    15: ("Endurance", "Bonus mission"),
    16: ("Triumph of the Empire", "Bonus mission"),
    17: ("Revenge on Yavin", "Bonus mission"),
    18: ("", "Hoth hangar"),
    19: ("", "Mon Calamari hangar"),
    20: ("", "Yavin hangar"),
    21: ("", "Imperial hangar"),
    22: ("", "Death Star scene loaded at start up, most likely the title backdrop"),
    23: ("", "Sky only, started from a front end menu"),
    26: ("", "Copy of the ending celebration cutscene"),
}

# folder without ``_lvl`` -> (title, what it is)
RS3_LEVELS: Dict[str, Tuple[str, str]] = {
    "frontend": ("", "Front end menus"),
    "arcade": ("", "Special features arcade game"),
    "Training": ("Tatooine Training", "Training"),
    "L_01_Yavin": ("Revenge of the Empire", "Luke"),
    "L_02_Dantooine": ("Defiance on Dantooine", "Luke"),
    "L_03_Ralltiir": ("Defenders of Ralltiir", "Luke"),
    "L_04_Ralltiir_Part2": ("Extraction from Ralltiir", "Luke"),
    "L_06_Hoth": ("Battlefield Hoth", "Luke"),
    "W_01_Bakura": ("Raid at Bakura", "Wedge"),
    "W_02_Ep2": ("Relics of Geonosis", "Wedge"),
    "W_03_Omega3and4": ("Deception at Destrillion", "Wedge"),
    "W_04_Omega4": ("Guns of Dubrillion", "Wedge"),
    "W_05_FondorShipyards": ("Fondor Shipyard Assault", "Wedge"),
    "E_01_Luke": ("Speeder Bike Pursuit", "Endor, Luke"),
    "E_02_Han": ("Triumph of the Rebellion", "Endor, Han"),
    "B_01_DeathStarEscape": ("Death Star Rescue", "Bonus mission"),
    "B_02_Hoth": ("Escape from Hoth", "Bonus mission"),
    "B_03_Bespin": ("Flight from Bespin", "Bonus mission"),
    "B_04_Dagobah": ("Trials of a Jedi", "Bonus mission"),
    "B_05_SarlaccPit": ("The Sarlacc Pit", "Bonus mission"),
    "B_06_Executor": ("Attack on the Executor", "Bonus mission"),
    "B_07_Endurance": ("Rebel Endurance", "Bonus mission"),
    "hangarCalamari": ("", "Mon Calamari hangar"),
    "hangarHoth": ("", "Hoth hangar"),
    "hangarImperial": ("", "Imperial hangar"),
    "hangarYavin": ("", "Yavin hangar"),
    "DOG_F_Asteroids": ("Geonosis Dogfight", "Versus"),
    "DOG_F_Bespin": ("Bespin Dogfight", "Versus"),
    "DOG_F_DeathStar2": ("Death Star Dogfight", "Versus"),
    "DOG_F_EndorSpace": ("Dogfight Over Endor", "Versus"),
    "DOG_F_Hoth": ("Dogfight Over Hoth", "Versus"),
    "DOG_F_Ison": ("Ison Corridor Dogfight", "Versus"),
    "RAC_F_DeathStarTrench": ("Death Star Trench Race", "Versus"),
    "RAC_S_EndorSurface": ("Endor Speeder Bike Race", "Versus"),
    "RAC_S_Tatooine": ("Beggar's Canyon Race", "Versus"),
    "RAM_F_Asteroids": ("Geonosis Asteroid Rampage", "Versus"),
    "RAM_F_DeathStar": ("Death Star Rampage", "Versus"),
    "RAM_F_GasPlatform": ("Gas Platform Rampage", "Versus"),
    "RAM_F_Hoth": ("Rampage Over Hoth", "Versus"),
    "RAM_F_Kothlis": ("Kothlis Rampage", "Versus"),
    "RAM_S_Hoth": ("Hoth Speeder Rampage", "Versus"),
    "RAM_W_Endor": ("Endor Walker Rampage", "Versus"),
    "RAM_W_Tatooine": ("Tatooine Walker Rampage", "Versus"),
    "TAG_F_Academy": ("Prefsbelt IV Tag and Defend", "Versus"),
    "TAG_F_Asteroids": ("Asteroid Tag and Defend", "Versus"),
    "TAG_F_Kothlis": ("Kothlis Tag and Defend", "Versus"),
    "TAG_F_Maw": ("Maw Tag and Defend", "Versus"),
    "TAG_F_Tatooine": ("Tatooine Tag and Defend", "Versus"),
    "TAG_S_Hoth": ("Hoth Tag and Defend", "Versus"),
}

RS2_FOLDER = re.compile(r"l(\d\d)$")
RS3_COOP = re.compile(r"coop_(\d\d)_")


def describe(game: int, level: str) -> Tuple[str, str]:
    """(title, what it is) of a level folder (``l12``, ``L_02_Dantooine_lvl``); empty when unknown."""
    if game == 2:
        m = RS2_FOLDER.match(level)
        return RS2_LEVELS.get(int(m.group(1)), ("", "")) if m else ("", "")
    name = level[:-4] if level.endswith("_lvl") else level
    m = RS3_COOP.match(name)
    if m:  # the Rogue Leader mission of that level number, played by two
        title, _ = RS2_LEVELS.get(int(m.group(1)), ("", ""))
        return title, "Co-op (Rogue Leader mission)"
    return RS3_LEVELS.get(name, ("", ""))


def disc_levels(game_data) -> "OrderedDict[Tuple[str, str], List[str]]":
    """(archive, level folder) -> its resources, in disc order."""
    levels: "OrderedDict[Tuple[str, str], List[str]]" = OrderedDict()
    for archive in game_data.archives:
        label = game_data.archive_label(archive)
        if not label.lower().startswith("lvlpack"):
            continue
        for entry in archive.files():
            level, _, resource = entry.name.partition("/")
            if resource and "/" not in resource:
                levels.setdefault((label, level), []).append(resource)
    return levels


LEGEND = """\
Folders follow the disc: `lvlPackNN/<level>/<resource>/`.

* `lvlPackNN`: the archive file on the disc the level is stored in. It only bundles
  levels and says nothing about the mission.
* `<level>`: one level, see the table.
* `00`: what the whole level shares (terrain, sky, common models).
* `01`, `02`, ...: the parts of the level. The game loads `00` plus one part and moves
  on to the next part as the mission goes on.
* `NNcMM`: cutscene `MM` of part `NN`, with the models only that cutscene uses.
* `data/`: everything outside the levels (player craft, pilots, effects).
"""


def level_index(game: int, levels) -> str:
    name = "Rogue Leader" if game == 2 else "Rebel Strike"
    lines = [f"# {name} levels", "", LEGEND, "| Folder | Mission | What it is | Resources |",
             "| --- | --- | --- | --- |"]
    for (archive, level), resources in levels.items():
        title, what = describe(game, level)
        lines.append(f"| `{archive}/{level}` | {title} | {what} | {', '.join(resources)} |")
    return "\n".join(lines) + "\n"


def write_level_index(game_data, out_dir: str) -> str:
    """Write ``LEVELS.md`` for the disc into ``out_dir``; returns its path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "LEVELS.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(level_index(game_data.game, disc_levels(game_data)))
    return path
