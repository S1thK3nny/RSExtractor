"""Skies: which sky models a level resource shows, and where a map file puts them.

Both games draw their skies around the camera: the domes span about 140 units while
the levels span thousands, so a map file centres them on the level and scales them up
(see ``sky_frame``).

Rogue Leader (``FUN_7fd8bed4``, the sky object ``FUN_7fd8c8fc`` makes) loads the model
``sky%02d`` of the level number and adds models by level (``DAT_7fde822c``) and by the
level's resource (``DAT_7fde8230``, resource number - 1), listed in ``rs2_sky_models``.
The horizon (``FUN_7fd8c9b0``) adds the cloud rings ``sky08hori1..3`` (Bespin skips the
second) and procedural cloud layers, which have no model.

Rebel Strike builds its skies in per mission code from sky objects (class 249,
``FUN_7f33a1dc(0xf9, name)``, also placed by ``dat``) whose models sit in ``bData.pool``
packages the level references as ``<name>sky_dref`` or ``extra/<Name>Sky_dref``, in the
resource or in ``X_lvl/00``. Their horizon bands (``*_horizonband``) are scaled by code
(``FUN_7f3713dc``) and left out. Tatooine's four skies are one per time of day
(``FUN_7f326760``); the day sky is the default.
"""
from __future__ import annotations

import re
from typing import List, Optional

import numpy as np

SKY_CLASS = 249  # Rebel Strike sky objects

# Radius of the sky domes of both games (sky00 .. sky23, HothSky, R3StarSky: 137 .. 142).
SKY_RADIUS = 140.0

_HORIZON = ["sky08hori1", "sky08hori2", "sky08hori3"]
_ENDOR = ["sky09_pl", "xptSkyEndorSky", "sky09_st", "sky09_ds", "xptSkyEndorSun", "xptSkyEndorRebels"]


def rs2_sky_models(level: int, resource: int) -> List[str]:
    """Models of the Rogue Leader sky of level ``lNN`` resource ``resource``, in load order."""
    sub = resource - 1
    names = [f"sky{level:02d}"]
    if level in (1, 16):
        names += ["sky01_pl", "sky01_st", "sky01_rm"]
    elif level == 2:
        names += ["sky02_nb"]
    elif level == 4:
        names += ["sky04_as1", "sky04_as2"]
        names += (["gfxSkyMawPlanet", "gfxSkyMawHoleOuter", "gfxSkyMawHoleInner"] if sub == 0 else
                  ["gfxSkyMaw2Atmosphere", "gfxSkyMaw2HoleOuter", "gfxSkyMaw2HoleInner"])
    elif level == 5:
        names += ["sky05_pl"]
    elif level == 6:
        names += _HORIZON + ["xptSkyKothlisSurfaceCloudsMd"]
    elif level == 7:
        names += (["xptSkyPrefbeltDayCloudsOut", "xptSkyPrefbeltDayCloudsMdl", "xptSkyPrefbeltDayCloudsInr"]
                  if sub == 1 else ["gfxSkyPrefsbeltAtmosphere"] if sub == 0 else [])
    elif level == 8:
        names += ["sky08hori1", "sky08hori3"] + (["gfxSkyBespinCityVista"] if sub == 0 else [])
    elif level in (9, 10):
        names += _ENDOR
    elif level == 12:
        names += ["gfxSkyAsteroidfieldField01", "gfxSkyAsteroidfieldField02"]
    elif level == 17:
        names += ["gfxSkyYavinSurfaceYavin", "gfxSkyYavinSurfaceCrown"] + _HORIZON
    elif level in (0, 3):
        names += _HORIZON
    return names


RS2_LEVEL = re.compile(r"^l(\d+)/(\d+)$")

# Skies chosen by the time of day option instead of TatooineDaySky.
_TIME_OF_DAY = {"tatooinemorningsky", "tatooinesunsetsky", "tatooinenightsky"}


def rs3_sky_package(dref_path: str) -> bool:
    """Whether a ``_dref`` part (``extra/HothSky_dref``, ``r3starsky_dref``) is a sky package."""
    name = dref_path[:-5].rsplit("/", 1)[-1].lower()
    return name.endswith("sky") and name not in _TIME_OF_DAY


def rs3_sky_model(name: str) -> bool:
    """Whether a model of a sky package is drawn as it is (not a code scaled horizon band)."""
    return "horizonband" not in name.lower()


def sky_frame(low: Optional[np.ndarray], high: Optional[np.ndarray]) -> np.ndarray:
    """Matrix putting a sky dome (model space, centred on the camera) around a level
    spanning ``low`` .. ``high``: centred on it and scaled so the dome reaches 1.5 times
    the level's horizontal half diagonal (at least the dome's own size)."""
    if low is None or high is None:
        return np.eye(4)
    centre = (low + high) / 2
    reach = float(np.linalg.norm((high - low)[[0, 2]])) / 2
    scale = max(1.5 * reach / SKY_RADIUS, 1.0)
    m = np.diag([scale, scale, scale, 1.0])
    m[:3, 3] = centre
    return m
