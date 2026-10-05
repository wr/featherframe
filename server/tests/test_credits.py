"""Who drew each plate (W-984): the corner names the plate's own credit, read
off it in the open dataset, never the folio's lead artist."""
from __future__ import annotations

import yaml

from featherframe import credits


def test_the_corner_names_each_plates_own_artist():
    assert credits.drawn_by("havell", 159) == "John James Audubon"
    assert credits.drawn_by(None, 159) == "John James Audubon"           # an index with no folio is Havell's
    assert credits.drawn_by("havell", 64) == "Lucy Audubon"               # "Drawn from Nature by Lucy Audubon."
    assert credits.drawn_by("gould_europe", 10) == "Edward Lear"
    assert credits.drawn_by("gould_europe", 1) == "John & Elizabeth Gould"
    assert credits.drawn_by("gould_asia", 1, 1) == "Joseph Wolf & H. C. Richter"
    assert credits.drawn_by("gould_australia", 1, "Supp.") is not None
    assert credits.drawn_by("gould_australia", 1, 1) == credits.drawn_by("gould_australia", 1, "I")


def test_a_plate_whose_credit_was_not_read_names_no_one():
    assert credits.drawn_by("gould_europe", 26) is None
    assert credits.drawn_by("no_such_folio", 1) is None
    assert credits.drawn_by("havell", None) is None


def test_names_are_set_as_the_corner_writes_them():
    assert credits.byline(["John Gould", "Elizabeth Gould"]) == "John & Elizabeth Gould"
    assert credits.byline(["John Gould", "Henry Constantine Richter"]) == "John Gould & H. C. Richter"
    assert credits.byline(["Joseph Wolf", "William Matthew Hart"]) == "Joseph Wolf & William Hart"
    assert credits.byline([]) is None


def test_every_pinned_havell_plate_has_its_artist():
    from pathlib import Path
    folio = yaml.safe_load((Path(__file__).resolve().parents[1] / "scripts" / "folios" / "havell.yaml").read_text())
    plates = {e["plate"] for e in folio["species"] if isinstance(e.get("plate"), int)}
    assert plates and all(credits.drawn_by("havell", p) for p in plates)
