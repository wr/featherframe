"""W-874: a folio numbered per volume, and a volume's own margins.

Gould's Australia, Asia and Great Britain number their plates within each
volume ("Australia, ii. pl. 18"), and on some volumes the binding survives
the folio's margin. Both are resolved at fetch time into the index."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from featherframe import plate_library
from featherframe.names import SpeciesIndex
from featherframe.render.provider import PlateProvider

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_plates.py"


@pytest.fixture(scope="module")
def fp():
    spec = importlib.util.spec_from_file_location("fetch_plates", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HEADER = {
    "title": "The Birds of Australia", "plates_per_volume": True,
    "margins": [0.02, 0.02, 0.98, 0.97],
    "volume_margins": {"birdsAustraliav2Goul": [0.02, 0.02, 0.96, 0.97]},
    "scans": "https://example.invalid/{volume}/{leaf:04d}.jp2",
}
SPECIES = [
    # The folio's margins.
    {"common": "Wedge-tailed Eagle", "scientific": "Aquila audax", "plate": 1,
     "volume_no": 1, "volume": "birdsAustraliav1Goul", "leaf": 140},
    # Its volume's.
    {"common": "Laughing Kookaburra", "scientific": "Dacelo novaeguineae", "plate": 18,
     "volume_no": 2, "volume": "birdsAustraliav2Goul", "leaf": 80},
    # Its own, over its volume's.
    {"common": "Azure Kingfisher", "scientific": "Ceyx azureus", "plate": 25,
     "volume_no": 2, "volume": "birdsAustraliav2Goul", "leaf": 108,
     "margins": [0.02, 0.02, 0.96, 0.9]},
]


def _fetch(fp, tmp_path, header, species):
    """fetch_scans over scans already on disk: nothing touches the network."""
    for e in species:
        dest = tmp_path / fp.scan_filename("gould_australia", e)
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 400), (236, 228, 208)).save(dest)
    args = SimpleNamespace(force=False, dry_run=False)
    records, counts, _ = fp.fetch_scans("gould_australia")(None, species, args, tmp_path, {}, header)
    assert counts["downloaded"] == len(species)
    return {r["common"]: r for r in records}


def test_margins_are_the_plates_then_its_volumes_then_the_folios(fp):
    e = {"volume": "birdsAustraliav2Goul"}
    assert fp.scan_margins(e, HEADER) == [0.02, 0.02, 0.96, 0.97]
    assert fp.scan_margins({**e, "margins": [0, 0, 1, 0.9]}, HEADER) == [0, 0, 1, 0.9]
    assert fp.scan_margins({"volume": "birdsAustraliav1Goul"}, HEADER) == HEADER["margins"]
    assert fp.scan_margins(e, {"margins": [0.1, 0.1, 0.9, 0.9]}) == [0.1, 0.1, 0.9, 0.9]
    assert fp.scan_margins(e, {}) is None                    # Havell's own


def test_the_index_carries_the_resolved_margins_and_the_volume(fp, tmp_path):
    rec = _fetch(fp, tmp_path, HEADER, SPECIES)
    assert rec["Wedge-tailed Eagle"]["margins"] == HEADER["margins"]
    assert rec["Laughing Kookaburra"]["margins"] == [0.02, 0.02, 0.96, 0.97]
    assert rec["Azure Kingfisher"]["margins"] == [0.02, 0.02, 0.96, 0.9]
    assert [rec[c]["volume_no"] for c in ("Wedge-tailed Eagle", "Laughing Kookaburra")] == [1, 2]


def test_a_folio_numbered_straight_through_carries_no_volume(fp, tmp_path):
    header = {k: v for k, v in HEADER.items() if k != "plates_per_volume"}
    rec = _fetch(fp, tmp_path, header, SPECIES[:1])
    assert rec["Wedge-tailed Eagle"]["volume_no"] is None


def test_the_volume_rides_the_index_the_library_and_the_artwork(fp, tmp_path):
    img = tmp_path / "img"
    entries = list(_fetch(fp, img, HEADER, SPECIES).values())
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"generated_at": "x", "species": entries}))
    idx = SpeciesIndex(entries, images_dir=img)
    m = idx.match("Laughing Kookaburra", "Dacelo novaeguineae")
    assert (m.folio, m.volume_no, m.plate_number) == ("gould_australia", 2, 18)

    out = tmp_path / "library"
    plate_library.build(out, index, img)
    scans = PlateProvider(idx)
    lib = plate_library.LibraryProvider(plate_library.PlateLibrary(str(out), tmp_path / "cache"))
    for e in SPECIES:
        a, b = scans.artwork(e["common"], e["scientific"]), lib.artwork(e["common"], e["scientific"])
        assert (a.folio, a.volume_no, a.plate) == (b.folio, b.volume_no, b.plate) \
            == ("gould_australia", e["volume_no"], e["plate"])


def test_a_havell_entry_has_no_volume():
    idx = SpeciesIndex([{"common": "Northern Cardinal", "scientific": "Cardinalis cardinalis",
                         "plate": 159, "image": "x.jpg"}])
    assert idx.entry("Northern Cardinal")["plate"] == 159
    assert idx.entry("Northern Cardinal").get("volume_no") is None
