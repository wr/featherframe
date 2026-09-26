"""The season of a date, for the generated collage's bough (W-881).

Meteorological seasons, three months each, and a stage within one: the first
month is early, the second mid, the third late. The southern hemisphere is the
same calendar six months on. A household's hemisphere comes from its detection
source's latitude where the source reports one, else from its Region.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

SEASONS = ("winter", "spring", "summer", "autumn")
STAGES = ("early", "mid", "late")

# Regions whose folios are southern (config.REGIONS); every other is northern.
SOUTHERN_REGIONS = frozenset({"australia"})


def season_of(day: date, southern: bool = False) -> tuple[str, str]:
    """(stage, season) for `day`: Dec–Feb is northern winter, Mar–May spring,
    Jun–Aug summer, Sep–Nov autumn; `southern` shifts that by six months."""
    month = day.month
    if southern:
        month = (month + 5) % 12 + 1
    i = month % 12  # Dec 0, Jan 1, Feb 2, Mar 3, …, Nov 11
    return STAGES[i % 3], SEASONS[i // 3]


# The state a temperate tree is in at each stage, for the collage's bough: one
# condition per stage, never a list of things to paint (a season named alone
# came back as whatever the model associates with it: late winter was dead oak
# leaves and lichen, no snow).
TREE_STATE = {
    ("early", "winter"): "dormant, the first snow resting along its limbs",
    ("mid", "winter"): "deep in dormancy, snow lying thick along its limbs",
    ("late", "winter"): "still dormant, a heavy late snow lying along its limbs, its buds just swelling",
    ("early", "spring"): "its buds breaking into the first small leaves",
    ("mid", "spring"): "in blossom, its new leaves unfolding",
    ("late", "spring"): "in fresh, full young leaf",
    ("early", "summer"): "in full new leaf, its green at its freshest",
    ("mid", "summer"): "in deep, full summer leaf",
    ("late", "summer"): "in heavy late-summer leaf, its fruit ripening",
    ("early", "autumn"): "its leaves turning to their autumn color",
    ("mid", "autumn"): "in full autumn color",
    ("late", "autumn"): "letting go the last of its autumn leaves",
}


def season_phrase(day: date, southern: bool = False) -> str:
    """'late winter', 'mid spring' — how the sidecar names it."""
    stage, name = season_of(day, southern)
    return f"{stage} {name}"


# W-882: when the day's weather is known, snow comes from it alone, so a
# winter bough with no snow that day is bare wood; each kind of weather is one
# state of the tree, like the stages, and it lies on the wood, never across the
# sheet (rain streaks or a blizzard would bury the numerals). Wind was tried and
# does not read on a perched composition, even as a gale.
WINTER_BARE = {
    "early": "dormant, its buds held tight",
    "mid": "deep in dormancy, its buds held tight",
    "late": "still dormant, its buds just swelling",
}
WEATHER_STATE = {
    # A heavy fall has to outweigh the sparse branch every collage is drawn
    # with, or a 25 cm snowstorm paints lighter than an ordinary winter day.
    "heavy_snow": ("buried in that day's heavy snowfall, the snow lying deep and "
                   "unbroken along the top of every limb and twig, the whole tree "
                   "white with it"),
    "snowing": "snow falling that day and gathering thick along its limbs",
    "snow": "the snow of the days before lying along its limbs",
    "rain": ("standing in that day's heavy rain, the falling rain itself drawn in "
             "plain sight as close ruled lines of the engraver's burin slanting across "
             "the open paper behind the figures"),
}


def tree_state(day: date, southern: bool = False,
               weather: Optional[str] = None) -> str:
    """'late winter, still dormant under late snow, …' — the bough's season as
    the collage prompt states it. `weather` is the day's kind of weather
    (`weather.kind_of`, "" for a quiet day) or None when it is unknown, and
    then the season's own snow stands."""
    stage, name = season_of(day, southern)
    if weather is None:
        return f"{stage} {name}, {TREE_STATE[(stage, name)]}"
    state = WINTER_BARE[stage] if name == "winter" else TREE_STATE[(stage, name)]
    extra = WEATHER_STATE.get(weather)
    return f"{stage} {name}, {state}" + (f", {extra}" if extra else "")


def is_southern(latitude: Optional[float], region: str = "") -> bool:
    """A reported latitude decides; without one, the household's Region."""
    if latitude is not None:
        return latitude < 0
    return region in SOUTHERN_REGIONS
