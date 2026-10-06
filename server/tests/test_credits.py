"""Who drew each plate (W-984): the corner names the plate's own credit, read
off it in the open dataset, never the folio's lead artist."""
from __future__ import annotations

import yaml

from featherframe import credits


def test_the_corner_names_each_plates_own_artist():
    assert credits.drawn_by("havell", 159) == "J. J. Audubon"
    assert credits.drawn_by(None, 159) == "J. J. Audubon"                 # an index with no folio is Havell's
    assert credits.drawn_by("havell", 64) == "Lucy Audubon"               # "Drawn from Nature by Lucy Audubon."
    assert credits.drawn_by("gould_europe", 10) == "E. Lear"
    assert credits.drawn_by("gould_europe", 1) == "J. & E. Gould"
    assert credits.drawn_by("gould_asia", 1, 1) == "J. Wolf & H. C. Richter"
    assert credits.drawn_by("gould_australia", 1, "Supp.") is not None
    assert credits.drawn_by("gould_australia", 1, 1) == credits.drawn_by("gould_australia", 1, "I")


def test_a_plate_whose_credit_was_not_read_names_no_one():
    assert credits.drawn_by("gould_europe", 26) is None
    assert credits.drawn_by("no_such_folio", 1) is None
    assert credits.drawn_by("havell", None) is None


def test_names_are_set_as_the_plates_engrave_them():
    assert credits.byline(["John Gould", "Elizabeth Gould"]) == "J. & E. Gould"
    assert credits.byline(["John Gould", "Henry Constantine Richter"]) == "J. Gould & H. C. Richter"
    assert credits.byline(["Joseph Wolf", "William Matthew Hart"]) == "J. Wolf & W. Hart"
    assert credits.byline([]) is None
    assert credits.corner_name("Mary Anne Someone") == "M. A. Someone"


def test_every_artist_credited_has_its_engraved_form_and_fits_the_corner():
    from featherframe.render import theme, typography
    names = {n for folio in credits._credits().values() for plate in folio.values() for n in plate}
    assert names <= set(credits.CORNER_NAMES)
    widest = max((credits.byline(plate) for folio in credits._credits().values()
                  for plate in folio.values()),
                 key=lambda b: typography.script_width(b, theme.CORNER_SIZE))
    assert widest == theme.ARTIST_MARK_WIDEST[0]       # every credit set at full size


def test_every_pinned_havell_plate_has_its_artist():
    from pathlib import Path
    folio = yaml.safe_load((Path(__file__).resolve().parents[1] / "scripts" / "folios" / "havell.yaml").read_text())
    plates = {e["plate"] for e in folio["species"] if isinstance(e.get("plate"), int)}
    assert plates and all(credits.drawn_by("havell", p) for p in plates)
