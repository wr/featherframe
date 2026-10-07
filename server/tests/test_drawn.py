"""A picture drawn once is never drawn again (W-999).

Everything drawn is kept once in the drawn store, named by what it was drawn
from, and pictures, frames and viewers point at it. A species that comes back
composes its gray sheet again, lands on the same ETag, and finds its colour
twin, every frame's output and every view already made.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

import pytest
from PIL import Image, ImageDraw

from featherframe import drawn
from featherframe.render import compose, framebuffer, pipeline
from featherframe.render.provider import Artwork
from featherframe.sources import Detection
from tests._frames import EE02_PANEL, EE03_PANEL, add_kit, frame_bytes

NOW = datetime(2026, 10, 6, 8, 0)
GRAY, COLOUR = "AA:AA:AA:00:00:03", "BB:BB:BB:00:00:02"
SPECIES = {"Northern Cardinal": ("Cardinalis cardinalis", (200, 30, 30), 159),
           "Blue Jay": ("Cyanocitta cristata", (30, 60, 200), 102)}


class _Art:
    """One disc per species, with a colour twin."""
    name = "stub"

    def __init__(self):
        self.colour_fails = False
        self.plate_offset = 0

    def artwork(self, common, scientific):
        _, rgb_fill, plate = SPECIES[common]
        rgb = Image.new("RGB", (900, 900), "white")
        ImageDraw.Draw(rgb).ellipse((150, 150, 750, 750), fill=rgb_fill)
        gray = rgb.convert("L")

        def colour():
            if self.colour_fails:
                raise OSError("no colour today")
            return gray, rgb
        return Artwork(gray, plate + self.plate_offset, folio="havell", color_loader=colour)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    service = FeatherframeService()
    service._clock = lambda: NOW
    service.config.quiet_hours_mode = "off"
    service.reload_config = lambda: None
    service.provider = _Art()
    add_kit(service, GRAY, EE03_PANEL)
    add_kit(service, COLOUR, EE02_PANEL)
    yield service


def _show(svc, common, reason="detection"):
    sci = SPECIES[common][0]
    det = Detection(rowid=1, date="2026-10-06", time="08:00:00", common_name=common,
                    scientific_name=sci, confidence=0.9)
    svc._render_single(det, svc._clock(), reason=reason)
    svc._tick_frames()


def _store(svc) -> dict:
    """Every file in the drawn store: its bytes' identity and when it was written."""
    return {p: (p.stat().st_size, p.stat().st_mtime_ns)
            for p in drawn.root().rglob("*") if p.is_file()}


class _Spy:
    def __init__(self, monkeypatch, svc):
        self.finished, self.views, self.composed = [], [], []
        render_image, render_view = pipeline.render_image, pipeline.render_view
        monkeypatch.setattr(pipeline, "render_image",
                            lambda *a, **k: self.finished.append(1) or render_image(*a, **k))
        monkeypatch.setattr(pipeline, "render_view",
                            lambda *a, **k: self.views.append(1) or render_view(*a, **k))
        compose_color = svc._compose_color
        svc._compose_color = lambda r: self.composed.append(1) or compose_color(r)


COLOUR_VIEW = pipeline.View(600, 800, "color")
GRAY_VIEW = pipeline.View(758, 1024, "gray16")


# -- a species that comes back ----------------------------------------------
def test_a_species_that_comes_back_finds_everything_already_drawn(svc, monkeypatch):
    _show(svc, "Northern Cardinal")
    cardinal, outputs = svc.pictures["plates"].etag, dict(svc._out)
    assert svc.pictures["plates"].has_color()          # the colour kit's twin
    views = [svc.view_png(v)[2] for v in (COLOUR_VIEW, GRAY_VIEW)]
    _show(svc, "Blue Jay")
    assert svc.pictures["plates"].etag != cardinal
    for v in (COLOUR_VIEW, GRAY_VIEW):
        svc.view_png(v)
    kept = _store(svc)

    spy = _Spy(monkeypatch, svc)
    _show(svc, "Northern Cardinal")
    assert svc.pictures["plates"].etag == cardinal
    assert svc._out == outputs
    assert [svc.view_png(v)[2] for v in (COLOUR_VIEW, GRAY_VIEW)] == views
    # Nothing finished, nothing composed in colour, nothing written.
    assert (spy.finished, spy.views, spy.composed) == ([], [], [])
    assert _store(svc) == kept


def test_each_frame_is_served_the_output_it_had(svc):
    _show(svc, "Northern Cardinal")
    first = {fid: frame_bytes(svc, fid) for fid in (GRAY, COLOUR)}
    _show(svc, "Blue Jay")
    _show(svc, "Northern Cardinal")
    assert {fid: frame_bytes(svc, fid) for fid in (GRAY, COLOUR)} == first
    assert all(framebuffer.is_complete(b) for b in first.values())


# -- what makes it draw again -------------------------------------------------
def test_a_frames_own_settings_draw_its_output_again(svc, monkeypatch):
    _show(svc, "Northern Cardinal")
    before = dict(svc._out)
    svc.update_frame(GRAY, {"mat_inset_pct": 7})
    spy = _Spy(monkeypatch, svc)
    svc._tick_frames()
    assert spy.finished == [1]                                   # that frame only
    assert svc._out[GRAY]["file"] != before[GRAY]["file"]
    assert svc._out[COLOUR] == before[COLOUR]


def test_a_new_finish_version_draws_every_output_again(svc, monkeypatch):
    _show(svc, "Northern Cardinal")
    before = dict(svc._out)
    monkeypatch.setattr(pipeline, "FINISH_VERSION", "test")
    spy = _Spy(monkeypatch, svc)
    svc._tick_frames()
    assert len(spy.finished) == 2
    assert all(svc._out[f]["file"] != before[f]["file"] for f in (GRAY, COLOUR))


def test_a_new_colour_version_composes_the_twin_again(svc, monkeypatch):
    _show(svc, "Northern Cardinal")
    _show(svc, "Blue Jay")
    monkeypatch.setattr(compose, "COLOR_VERSION", 99)
    spy = _Spy(monkeypatch, svc)
    _show(svc, "Northern Cardinal")
    assert spy.composed == [1] and spy.finished == [1]          # the colour kit's
    assert svc.pictures["plates"].color_sheet_path.name.endswith("-c99.png")


def test_a_twin_whose_colour_did_not_load_is_not_kept(svc):
    svc.provider.colour_fails = True
    _show(svc, "Northern Cardinal")
    assert not svc.pictures["plates"].has_color()
    # The colour kit is finished from the gray sheet, as with no twin at all.
    assert f"|{svc.pictures['plates'].etag}.png|" in svc._out[COLOUR]["src"]


def test_a_twin_of_other_art_is_not_kept(svc):
    svc.frames.forget(COLOUR)                       # no colour screen: no twin yet
    _show(svc, "Northern Cardinal")
    assert not svc.pictures["plates"].has_color()
    svc.provider.plate_offset = 300                 # an illustration bought since
    svc.view_png(COLOUR_VIEW)                       # a colour screen asks
    assert not svc.pictures["plates"].has_color()


# -- the owner's Refresh -------------------------------------------------------
def test_refresh_draws_everything_again_in_place_and_deletes_nothing(svc, monkeypatch):
    _show(svc, "Northern Cardinal")
    svc.view_png(COLOUR_VIEW)
    before, outputs = set(_store(svc)), dict(svc._out)
    removed = []
    monkeypatch.setattr(drawn.hosted, "remove", lambda p: removed.append(p))
    spy = _Spy(monkeypatch, svc)
    svc.refresh_now()
    assert spy.composed == [1] and len(spy.finished) == 2      # before it returns
    assert svc._out == outputs and set(_store(svc)) == before and removed == []
    svc.view_png(COLOUR_VIEW)
    assert spy.views == [1]
    # Once: the next ask finds it.
    svc.view_png(COLOUR_VIEW)
    svc._tick_frames()
    assert spy.views == [1] and len(spy.finished) == 2


# -- a torn file on a Pi that lost power -------------------------------------
def test_a_torn_output_is_drawn_again_not_reused(svc):
    _show(svc, "Northern Cardinal")
    fff = svc._out_paths(GRAY)[0]
    whole = fff.read_bytes()
    _show(svc, "Blue Jay")
    fff.write_bytes(whole[:100])
    _show(svc, "Northern Cardinal")
    assert frame_bytes(svc, GRAY) == whole


def test_a_torn_sheet_is_written_again(svc):
    _show(svc, "Northern Cardinal")
    sheet = svc.pictures["plates"].sheet_path
    whole = sheet.read_bytes()
    sheet.write_bytes(whole[:200])
    _show(svc, "Blue Jay")
    _show(svc, "Northern Cardinal")
    with Image.open(sheet) as im:
        im.load()


# -- the pruner ---------------------------------------------------------------
def _put(path, age_s=3600):
    path.write_bytes(b"x")
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _etag(i):
    return f"{i:016x}"


def test_the_pruner_keeps_what_is_pointed_at_and_the_most_recent(svc):
    state = {"used": {}, "outs": {}, "views": {}}
    n = drawn.KEEP_ETAGS + 10
    for i in range(n):
        state["used"][_etag(i)] = f"2026-10-01T00:{i:02d}:00"
        _put(drawn.sheet_path(_etag(i)))
    pinned = _etag(0)                                   # the oldest, but shown
    drawn.prune(state, {pinned}, set(), time.time())
    left = {p.stem for p in (drawn.root() / "sheets").glob("*.png")}
    assert left == {pinned} | {_etag(i) for i in range(10, n)}
    assert set(state["used"]) == left


def test_the_pruner_caps_variants_but_never_one_pointed_at(svc):
    e = _etag(1)
    state = {"used": {e: "2026-10-01T00:00:00"}, "outs": {}, "views": {}}
    paths = []
    for i in range(7):
        key = f"{i:012x}"
        state["outs"][key] = {"pic": e, "etag": "x", "at": f"2026-10-01T00:0{i}:00"}
        paths.append(_put(drawn.out_paths(e, key)[0]))
    pointed = {drawn.rel(paths[0])}                     # the oldest, but a frame shows it
    drawn.prune(state, {e}, pointed, time.time())
    left = sorted(p.stem.split("-")[1] for p in (drawn.root() / "out").glob("*.fff"))
    assert left == [f"{i:012x}" for i in (0, 3, 4, 5, 6)]
    assert sorted(state["outs"]) == left


def test_the_pruner_sweeps_a_stray_but_not_one_being_drawn(svc):
    state = {"used": {}, "outs": {}, "views": {}}
    old = _put(drawn.view_path(f"{_etag(2)}-x"))
    new = _put(drawn.view_path(f"{_etag(3)}-x"), age_s=5)
    tmp = drawn.root() / "views" / "y.png.1-2-3.tmp"
    tmp.write_bytes(b"half")
    drawn.prune(state, set(), set(), time.time())
    assert not old.exists() and new.exists() and tmp.exists()


def test_only_the_pruner_deletes_in_the_store(svc, monkeypatch):
    removed = []
    real = drawn.hosted.remove
    monkeypatch.setattr(drawn.hosted, "remove",
                        lambda p: removed.append(p) if drawn.root() in p.parents else real(p))
    monkeypatch.setattr(svc, "_prune_drawn", lambda: None)
    _show(svc, "Northern Cardinal")
    _show(svc, "Blue Jay")
    svc.frames.forget(GRAY)
    svc._tick_frames()
    svc._drop_picture("plates")
    assert removed == []


def test_two_threads_never_write_one_temporary_file(svc):
    target = drawn.root() / "sheets" / "x.png"
    names = []
    threads = [threading.Thread(target=lambda: names.append(drawn._tmp(target))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(names)) == 8


# -- from before W-999 --------------------------------------------------------
def test_a_server_from_before_moves_its_files_and_no_frame_paints_again(svc, monkeypatch):
    """Today's layout, rebuilt by hand from what this build draws: the new
    server finds every frame's output and draws nothing."""
    from featherframe import paths
    from featherframe.db import Database
    from featherframe.service import FeatherframeService, _OUT_KEY
    _show(svc, "Northern Cardinal")
    pic, served = svc.pictures["plates"], {f: frame_bytes(svc, f) for f in (GRAY, COLOUR)}
    etag = pic.etag
    legacy = paths.frames_dir() / "pictures" / "plates"
    legacy.mkdir(parents=True)
    os.replace(pic.sheet_path, legacy / "sheet.png")
    os.replace(pic.color_sheet_path, legacy / "sheet_color.png")
    out_dir = paths.frames_dir() / "out"
    out_dir.mkdir()
    old = {}
    for fid, st in svc._out.items():
        safe = fid.replace(":", "_")
        fff = paths.data_dir() / st["file"]
        os.replace(fff, out_dir / f"{safe}.fff")
        os.replace(fff.with_suffix(".png"), out_dir / f"{safe}.png")
        src = st["src"].replace(f"|{etag}.png|", "|sheet.png|").replace(f"|{etag}-c1.png|", "|sheet_color.png|")
        old[fid] = {"etag": st["etag"], "src": src}
    views = paths.views_dir()
    (views / f"{etag}-600x800-color-0.png").write_bytes(b"an old view")
    (views / "waiting-ABC-600x800-color-0.png").write_bytes(b"a waiting plate")
    db = Database()
    db.set(_OUT_KEY, old)
    db.set(drawn.KV, {})

    spy = _Spy(monkeypatch, svc)
    new = FeatherframeService(db)
    new._clock, new.provider, new.reload_config = svc._clock, svc.provider, (lambda: None)
    assert {f: frame_bytes(new, f) for f in (GRAY, COLOUR)} == served
    new._tick_frames()
    assert spy.finished == [] and spy.composed == []
    assert new.pictures["plates"].has_color()
    assert not (paths.frames_dir() / "pictures").exists() and not out_dir.exists()
    assert sorted(p.name for p in views.glob("*.png")) == ["waiting-ABC-600x800-color-0.png"]


def test_a_sheet_that_is_not_its_picture_is_not_moved(svc):
    from featherframe import paths
    from featherframe.db import Database
    from featherframe.service import FeatherframeService
    _show(svc, "Northern Cardinal")
    etag = svc.pictures["plates"].etag
    legacy = paths.frames_dir() / "pictures" / "plates"
    legacy.mkdir(parents=True)
    svc.pictures["plates"].sheet_path.unlink()
    Image.new("L", (8, 8), 0).save(legacy / "sheet.png")
    new = FeatherframeService(Database())
    assert not drawn.sheet_path(etag).exists()
    assert new.pictures["plates"].sheets() == []


# -- what naming a drawing costs ----------------------------------------------
def test_naming_an_output_imports_no_drawing_library():
    """W-1012: every wake names each frame's output, and asking numba for its
    version by importing it cost a Cloud wake ~0.7 CPU-s. The versions come
    from the installed packages' metadata, the same strings."""
    import importlib.util
    import subprocess
    import sys
    if importlib.util.find_spec("numba") is None:
        pytest.skip("numba is not installed here")
    code = ("import sys; from featherframe import drawn; drawn.fkey(); "
            "print(','.join(m for m in ('numba', 'numpy') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert "numba" not in out.stdout


def test_the_library_versions_are_the_modules_own():
    from importlib import metadata
    import numpy
    from PIL import Image as PILImage
    try:
        import numba
        nb = numba.__version__
    except ImportError:
        nb = "-"
    drawn._libs.cache_clear()
    assert drawn._libs() == f"{PILImage.__version__},{numpy.__version__},{nb}"
    assert metadata.version("Pillow") == PILImage.__version__
