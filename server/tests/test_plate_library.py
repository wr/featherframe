"""The shared plate library (W-842): a plate drawn from the library is the
plate drawn from the scan, pixel for pixel, gray and colour."""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from featherframe import plate_library
from featherframe.names import SpeciesIndex
from featherframe.render.provider import PlateProvider

ENTRIES = [
    {"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis", "plate": 159,
     "image": "plate-159-cardinal-grosbeak.jpg", "legend": ["1. Male. 2. Female."]},
    # Two species on one composite plate share one crop…
    {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "plate": 102,
     "image": "plate-102-blue-jay.jpg", "composite": True},
    {"common": "Steller's Jay", "scientific": "Cyanocitta stelleri", "plate": 102,
     "image": "plate-102-blue-jay.jpg", "composite": True},
    # …and a curated box is a crop of its own.
    {"common": "Canada Jay", "scientific": "Perisoreus canadensis", "plate": 102,
     "image": "plate-102-blue-jay.jpg", "crop_box": [0.1, 0.1, 0.5, 0.5]},
    {"common": "Veery", "scientific": "Catharus fuscescens", "plate": "none"},
]


def _scan(path, seed):
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (1500, 2000), (236, 228, 208))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.integers(300, 1100), rng.integers(400, 1500)
        d.ellipse([x, y, x + rng.integers(20, 200), y + rng.integers(20, 200)],
                  fill=tuple(int(v) for v in rng.integers(20, 180, 3)))
    img.save(path, quality=90)


@pytest.fixture
def plates(tmp_path):
    img = tmp_path / "plates" / "img"
    img.mkdir(parents=True)
    _scan(img / "plate-159-cardinal-grosbeak.jpg", 1)
    _scan(img / "plate-102-blue-jay.jpg", 2)
    index = tmp_path / "plates" / "index.json"
    index.write_text(json.dumps({"generated_at": "x", "species": ENTRIES}))
    return index, img


def _same(a, b):
    assert a.mode == b.mode and a.size == b.size
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_a_library_plate_is_the_scan_plate(plates, tmp_path):
    index, img = plates
    out = tmp_path / "library"
    stats = plate_library.build(out, index, img)
    assert stats == {"made": 3, "kept": 1, "entries": 5}      # the composite is cut once
    scans = PlateProvider(SpeciesIndex(ENTRIES, images_dir=img))
    lib = plate_library.LibraryProvider(plate_library.PlateLibrary(str(out), tmp_path / "cache"))
    for common, sci in [(e["common"], e["scientific"]) for e in ENTRIES[:4]]:
        a, b = scans.artwork(common, sci), lib.artwork(common, sci)
        _same(a.image, b.image)
        for x, y in zip(a.color_pair(), b.color_pair()):
            _same(x, y)
        assert (a.plate, a.folio, a.composite, a.legend) == (b.plate, b.folio, b.composite, b.legend)
    assert lib.artwork("Veery", "Catharus fuscescens") is None     # never a wrong bird
    assert lib.artwork("House Sparrow", "Passer domesticus") is None
    assert plate_library.build(out, index, img)["kept"] == 4        # idempotent


def test_a_remote_library_is_fetched_once_and_kept(plates, tmp_path, monkeypatch):
    index, img = plates
    out = tmp_path / "library"
    plate_library.build(out, index, img)
    asked = []

    class Resp:
        def __init__(self, body):
            self.content = body
        def raise_for_status(self):
            pass
        def json(self):
            return json.loads(self.content)

    def get(url, timeout):
        rel = url.split("https://plates.example/", 1)[1]
        asked.append(rel)
        return Resp((out / rel).read_bytes())

    monkeypatch.setattr(plate_library.requests, "get", get)
    lib = plate_library.LibraryProvider(plate_library.PlateLibrary("https://plates.example/", tmp_path / "cache"))
    lib.artwork("Northern Cardinal", "Cardinalis cardinalis")
    lib.artwork("Northern Cardinal", "Cardinalis cardinalis").color_pair()
    lib.artwork("Northern Cardinal", "Cardinalis cardinalis")
    assert asked.count("library.json") == 1
    assert len([a for a in asked if a.endswith(".gray.png")]) == 1
    assert len([a for a in asked if a.endswith(".color.webp")]) == 1


def test_the_env_puts_the_library_in_place_of_the_scans(plates, tmp_path, monkeypatch):
    index, img = plates
    plate_library.build(tmp_path / "library", index, img)
    monkeypatch.setenv("FEATHERFRAME_PLATE_LIBRARY", str(tmp_path / "library"))
    assert plate_library.from_env().species_count == 5     # the index, as PlateProvider counts it
    monkeypatch.delenv("FEATHERFRAME_PLATE_LIBRARY")
    assert plate_library.from_env() is None


def test_the_library_carries_every_folio(tmp_path):
    """A second folio's plate goes into the library like Havell's, and asks
    after it (W-702); the folio headers ride along, catalog left behind."""
    img = tmp_path / "img"
    (img / "gould").mkdir(parents=True)
    _scan(img / "gould" / "gould-europe-180-house-sparrow.webp", 3)
    _scan(img / "plate-159-cardinal-grosbeak.jpg", 1)
    entries = ENTRIES + [{"folio": "gould", "common": "House Sparrow",
                          "scientific": "Passer domesticus", "plate": 180,
                          "image": "gould/gould-europe-180-house-sparrow.webp",
                          "margins": [0.05, 0.03, 0.95, 0.8], "tight": True}]
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"generated_at": "x", "species": entries, "folios": {
        "havell": {"title": "The Birds of America", "catalog": [{"plate": 1}]},
        "gould": {"title": "The Birds of Europe"}}}))
    out = tmp_path / "library"
    plate_library.build(out, index, img)
    data = json.loads((out / "library.json").read_text())
    assert data["folios"] == {"havell": {"title": "The Birds of America"},
                              "gould": {"title": "The Birds of Europe"}}
    lib = plate_library.LibraryProvider(plate_library.PlateLibrary(str(out), tmp_path / "cache"))
    art = lib.artwork("House Sparrow", "Passer domesticus")
    assert (art.folio, art.plate) == ("gould", 180)
    # The folio's own cut is taken exactly as the scans take it, and keyed apart.
    scan = PlateProvider(SpeciesIndex(entries, images_dir=img)).artwork("House Sparrow", "Passer domesticus")
    _same(art.image, scan.image)
    assert plate_library.entry_key(entries[-1]) != plate_library.entry_key(
        {**entries[-1], "margins": None})
    assert lib.artwork("Northern Cardinal", "Cardinalis cardinalis").folio == "havell"


def test_a_build_against_a_published_library_takes_only_what_it_lacks(plates, tmp_path):
    index, img = plates
    first = tmp_path / "published"
    plate_library.build(first, index, img)
    have = plate_library.published_keys(str(first))
    assert len(have) == 3
    out = tmp_path / "update"
    stats = plate_library.build(out, index, img, published=have)
    assert stats["made"] == 0 and not list((out / "lib").iterdir())
    data = json.loads((out / "library.json").read_text())
    assert {e.get("library") for e in data["species"]} - {None} == have    # still named
