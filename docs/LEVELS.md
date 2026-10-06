# Which level is which

Level folders keep the disc's names: `lvlPackNN/<level>/<resource>`. `lvlPackNN` is
only the archive file a level is stored in. In each level, `00` is what the whole
level shares, `01`, `02` ... are its parts (the game loads `00` plus one part and
moves on to the next during the mission), and `NNcMM` is cutscene `MM` of part `NN`.

Every extraction also writes `LEVELS.md` into `output/RS<n>` with each level
folder, its mission title from the game's own text, and its resources.

## Rogue Leader

Rogue Leader numbers its levels; the mission select uses the same numbers:

| Level | Mission |
| --- | --- |
| `l00` | Tatooine Training |
| `l01` | Death Star Attack |
| `l02` | Ison Corridor Ambush |
| `l03` | Battle of Hoth |
| `l04` | Prisons of the Maw |
| `l05` | Razor Rendezvous |
| `l06` | Vengeance on Kothlis |
| `l07` | Imperial Academy Heist |
| `l08` | Raid on Bespin |
| `l09` | Battle of Endor |
| `l10` | Strike at the Core |
| `l11` | Death Star Escape (bonus) |
| `l12` | The Asteroid Field (bonus) |
| `l13` | Sky and a TIE fighter cutscene |
| `l14` | Ending celebration cutscene |
| `l15` | Endurance (bonus) |
| `l16` | Triumph of the Empire (bonus) |
| `l17` | Revenge on Yavin (bonus) |
| `l18` | Hoth hangar |
| `l19` | Mon Calamari hangar |
| `l20` | Yavin hangar |
| `l21` | Imperial hangar |
| `l22` | Death Star scene loaded at start up |
| `l23` | Sky only |
| `l26` | Ending celebration cutscene |

## Rebel Strike

Rebel Strike names its level folders; `L_` levels are Luke's, `W_` Wedge's, `E_`
the Endor finale, `B_` bonus missions, `coop_NN` the Rogue Leader mission of level
`lNN` for two players, and `DOG_`, `RAC_`, `RAM_`, `TAG_` the versus levels:

| Folder | Mission |
| --- | --- |
| `Training_lvl` | Tatooine Training |
| `L_01_Yavin_lvl` | Revenge of the Empire |
| `L_02_Dantooine_lvl` | Defiance on Dantooine |
| `L_03_Ralltiir_lvl` | Defenders of Ralltiir |
| `L_04_Ralltiir_Part2_lvl` | Extraction from Ralltiir |
| `L_06_Hoth_lvl` | Battlefield Hoth |
| `W_01_Bakura_lvl` | Raid at Bakura |
| `W_02_Ep2_lvl` | Relics of Geonosis |
| `W_03_Omega3and4_lvl` | Deception at Destrillion |
| `W_04_Omega4_lvl` | Guns of Dubrillion |
| `W_05_FondorShipyards_lvl` | Fondor Shipyard Assault |
| `E_01_Luke_lvl` | Speeder Bike Pursuit |
| `E_02_Han_lvl` | Triumph of the Rebellion |
| `B_01_DeathStarEscape_lvl` | Death Star Rescue |
| `B_02_Hoth_lvl` | Escape from Hoth |
| `B_03_Bespin_lvl` | Flight from Bespin |
| `B_04_Dagobah_lvl` | Trials of a Jedi |
| `B_05_SarlaccPit_lvl` | The Sarlacc Pit |
| `B_06_Executor_lvl` | Attack on the Executor |
| `B_07_Endurance_lvl` | Rebel Endurance |

How the titles are found in the games' text is described in
[FORMATS.md](FORMATS.md#levels-and-mission-titles).
