"""W-883: where a Havell plate's art runs down past the caption band (a stump,
a stalk, a cone), the engraver set the caption beside it, up inside the part
the fixed trim keeps: "Cedar Bird / BOMBYCILLA CAROLINENSIS." sat inside the
illustration. The trim now lifts an isolated block of type from the bottom as
it does the corner line from the top, and a folio can paint out, per plate,
lettering that touches the art (`mask`)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from featherframe.render import plate

W, H = 1800, 2200
PAPER = 236
L, T, R, B = plate.HAVELL_MARGINS


def _sheet():
    img = Image.new("L", (W, H), PAPER)
    return img, ImageDraw.Draw(img)


def _line(d, x, y, n=22, ink=40, h=22):
    """A line of letter-sized strokes, spaced like type."""
    for i in range(n):
        d.rectangle((x + i * 16, y, x + i * 16 + 3, y + h), fill=ink)


def _caption(d, x, y):
    _line(d, x, y, n=20, h=34)          # the script title
    _line(d, x + 60, y + 60, n=16)      # the Latin name


def _ink(img, box):
    px = img.load()
    x0, y0, x1, y1 = box
    return sum(1 for y in range(y0, y1, 2) for x in range(x0, x1, 2) if px[x, y] < 150)


def _stump(d):
    """The art, running down past the caption band on the left."""
    d.ellipse((300, 300, 1300, 1500), fill=70)
    d.polygon([(500, 1400), (700, 1400), (560, int(H * 0.93)), (440, int(H * 0.93))], fill=60)


def test_a_caption_beside_the_art_is_lifted():
    img, d = _sheet()
    _stump(d)
    _caption(d, 900, int(H * 0.855))
    out = plate._trim_marginalia(img)
    y = int(H * 0.845) - int(H * T)
    assert _ink(out, (850, y, out.width, out.height)) == 0          # no caption
    assert _ink(out, (440, y, 560, out.height)) > 100               # the stump is whole


def test_art_the_caption_touches_is_left_alone():
    """A stroke joined to the picture is part of the picture: never painted out."""
    img, d = _sheet()
    _stump(d)
    _caption(d, 900, int(H * 0.855))
    d.line((1100, 1400, 1110, int(H * 0.86)), fill=60, width=5)     # a stem hanging into the title
    out = plate._trim_marginalia(img)
    y = int(H * 0.845) - int(H * T)
    assert _ink(out, (850, y, out.width, out.height)) > 50


def test_something_solid_at_the_foot_of_the_sheet_survives():
    """A fallen leaf or a pebble is isolated too, but it is solid, not type."""
    img, d = _sheet()
    _stump(d)
    d.ellipse((1000, int(H * 0.87), 1400, int(H * 0.9)), fill=60)
    out = plate._trim_marginalia(img)
    y = int(H * 0.86) - int(H * T)
    assert _ink(out, (1000, y, 1400, out.height)) > 500


def test_a_mask_paints_the_sheet_with_its_paper_first():
    img, d = _sheet()
    _stump(d)
    d.rectangle((1400, int(H * 0.6), 1600, int(H * 0.62)), fill=40)  # a sketch beside the art
    box = [1400 / W - 0.01, 0.59, 1600 / W + 0.01, 0.63]
    kept = plate._trim_marginalia(img)
    out = plate._trim_marginalia(img, mask=[box])
    sy, sx = int(H * 0.6) - int(H * T), 1400 - int(W * L)
    assert _ink(kept, (sx, sy, sx + 200, sy + 40)) > 0
    assert _ink(out, (sx, sy, sx + 200, sy + 40)) == 0
    assert _ink(out, (300, sy, 1200, sy + 40)) == _ink(kept, (300, sy, 1200, sy + 40))   # nothing else
    assert out.size == kept.size


def test_the_colour_twin_is_masked_in_the_same_place():
    img = Image.new("RGB", (W, H), (PAPER, PAPER - 4, PAPER - 14))
    ImageDraw.Draw(img).rectangle((900, 1300, 1100, 1340), fill=(40, 30, 20))
    out = plate._trim_marginalia(img, mask=[[0.49, 0.58, 0.62, 0.62]])
    assert out.getpixel((1000 - int(W * L), 1320 - int(H * T))) == (PAPER, PAPER - 4, PAPER - 14)


def test_the_mask_rides_from_the_index_to_the_crop(tmp_path):
    """havell.yaml → index.json → PlateMatch → plate.extract, and the library
    keys a masked crop, or one whose caption was lifted, apart from the crop
    published before under the same plate."""
    import json

    from featherframe import plate_library
    from featherframe.names import SpeciesIndex

    img, d = _sheet()
    _stump(d)
    _caption(d, 900, int(H * 0.855))
    (tmp_path / "img").mkdir()
    img.convert("RGB").save(tmp_path / "img" / "plate-43-cedar-bird.jpg", quality=95)
    mask = [[0.49, 0.58, 0.62, 0.62]]
    entry = {"common": "Cedar Waxwing", "scientific": "Bombycilla cedrorum", "plate": 43,
             "image": "plate-43-cedar-bird.jpg", "mask": mask}
    assert SpeciesIndex([entry], images_dir=tmp_path / "img").match(
        "Cedar Waxwing", "Bombycilla cedrorum").mask == mask

    assert plate.lifts_caption(tmp_path / "img" / "plate-43-cedar-bird.jpg")
    bare = {k: v for k, v in entry.items() if k != "mask"}
    keys = {plate_library.entry_key(bare), plate_library.entry_key(entry),
            plate_library.entry_key(bare, caption=True)}
    assert len(keys) == 3

    index = tmp_path / "index.json"
    index.write_text(json.dumps({"generated_at": "x", "species": [entry]}))
    plate_library.build(tmp_path / "lib", index, tmp_path / "img")
    lib = json.loads((tmp_path / "lib" / "library.json").read_text())
    assert lib["species"][0]["library"] == plate_library.entry_key(entry, caption=True)


def test_the_havell_masks_are_boxes_of_the_sheet():
    from pathlib import Path

    import yaml

    folio = yaml.safe_load((Path(__file__).parents[1] / "scripts" / "folios" / "havell.yaml").read_text())
    masked = [e for e in folio["species"] if e.get("mask")]
    assert {e["plate"] for e in masked} >= {39, 43, 113, 198}
    for e in masked:
        for box in e["mask"]:
            l, t, r, b = box[:4]
            assert 0 <= l < r <= 1 and 0 <= t < b <= 1, e["common"]
            assert len(box) == 4 or box[4:] == ["all"], e["common"]
        if e.get("margins"):
            l, t, r, b = e["margins"]
            assert 0 <= l < r <= 1 and 0 <= t < b <= 1, e["common"]


def test_a_stroke_that_crosses_the_mask_is_the_art_and_stays():
    img, d = _sheet()
    _stump(d)
    y = int(H * 0.87)
    _caption(d, 900, y)
    d.line((1000, 1400, 1000, H - 10), fill=60, width=6)            # a stalk down through the title
    box = [850 / W, (y - 20) / H, 1300 / W, (y + 110) / H]
    out = plate._trim_marginalia(img, mask=[box], caption=False)
    oy = y - int(H * T)
    assert _ink(out, (1000 - 3 - int(W * L), oy, 1000 + 4 - int(W * L), oy + 80)) > 30   # the stalk
    assert _ink(out, (1010 - int(W * L), oy, 1300 - int(W * L), oy + 90)) == 0          # the words


def test_an_all_box_is_painted_whole():
    img, d = _sheet()
    _stump(d)
    out = plate._trim_marginalia(img, mask=[[500 / W, 0.6, 700 / W, 0.62, "all"]], caption=False)
    y = int(H * 0.605) - int(H * T)
    assert _ink(out, (500 - int(W * L) + 2, y, 700 - int(W * L) - 2, y + 30)) == 0     # through the stump


def test_a_hidden_legend_line_stays_in_the_transcription_but_not_under_the_illustration():
    """Swainson's Warbler's sheet keys the sketches the mask paints out."""
    import importlib.util
    from pathlib import Path

    from featherframe import legends

    rec = legends.load()[198]
    assert "Bill hind toe & claw of the present species" in rec["lines"]    # the dataset's
    spec = importlib.util.spec_from_file_location(
        "fp", Path(__file__).parents[1] / "scripts" / "fetch_plates.py")
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    shown = fp.species_legend({"audubon_title": "Brown headed Worm eating Warbler"}, 198, {198: rec})
    assert shown == ["Azalea Calendulacea. Orange-coloured Azalea."]
