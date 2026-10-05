"""Who drew each plate (W-984): the corner of an illustration names them.

The credit lines are read off every plate in the open dataset
(github.com/wr/historical-bird-plates, each folio's credits.csv, W-926), and
`scripts/export_dataset.py credits` writes the plates' "drew" credits into
plate_credits.json beside this module: {folio: {plate label: [name, …]}}, a
plate labelled by its number, or "II.18" where a folio numbers per volume.
`export_dataset.py check` fails while the copy differs from the dataset.

A plate whose credit line was not read (a line lost in the binding) names no
one: the corner says nothing rather than guess from the folio.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from .names import DEFAULT_FOLIO

_PATH = Path(__file__).with_name("plate_credits.json")

# How the corner writes a name, where the plate's own form is shorter than
# the full one and is the one the man is known by.
CORNER_NAMES = {"Henry Constantine Richter": "H. C. Richter",
                "William Matthew Hart": "William Hart"}

_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}


@lru_cache(maxsize=1)
def _credits() -> dict:
    try:
        return json.loads(_PATH.read_text())
    except (OSError, ValueError):
        return {}


def plate_label(plate: int, volume_no: Union[int, str, None] = None) -> str:
    """A plate as the credits key it: "159", or "II.18" / "Supp.12"."""
    if volume_no in (None, ""):
        return str(int(plate))
    v = str(volume_no).rstrip(".")
    v = _ROMAN.get(int(v), v) if v.isdigit() else v
    return f"{v}.{int(plate)}"


def byline(names: list[str]) -> Optional[str]:
    """The names as the corner sets them: "John James Audubon", "John Gould &
    H. C. Richter", and two of one surname as "John & Elizabeth Gould"."""
    names = [CORNER_NAMES.get(n, n) for n in names if n]
    if not names:
        return None
    if len(names) == 2:
        (*a, last_a), (*b, last_b) = names[0].split(), names[1].split()
        if last_a == last_b and a and b:
            return f"{' '.join(a)} & {' '.join(b)} {last_a}"
    return " & ".join(names)


def drawn_by(folio: Optional[str], plate: Optional[int],
             volume_no: Union[int, str, None] = None) -> Optional[str]:
    """Who drew this plate, as its corner names them; None when no credit
    line of it was read."""
    if not plate:
        return None
    names = _credits().get(folio or DEFAULT_FOLIO, {}).get(plate_label(plate, volume_no))
    return byline(names or [])
