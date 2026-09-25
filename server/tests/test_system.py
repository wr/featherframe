"""The system voice on the glass (W-741): the firmware's pill, card, and plain
line, drawn by the server so a message never reads as a plate legend."""
from __future__ import annotations

from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw

from featherframe.render import compose, system, theme, typography, welcome
from featherframe.render.compose import SingleSpec
from featherframe.render.genart import vendor_label
from featherframe.render.provider import ArtProvider, Artwork


def _ink(img, box=None):
    arr = np.asarray(img if box is None else img.crop(box))
    return int((arr < 128).sum())


def _field():
    return Image.new("L", (theme.WIDTH, theme.HEIGHT), theme.FIELD)


def test_inter_is_bundled_and_loads():
    assert system.sans(34).getname()[0].startswith("Inter")


def test_solid_pill_is_the_toast_geometry():
    f = _field(); d = ImageDraw.Draw(f)
    x0, x1 = system.pill(d, theme.WIDTH / 2, 1000, "Up to date")
    assert abs((x0 + x1) / 2 - theme.WIDTH / 2) < 1
    band = f.crop((0, 1000 - system.PILL_H // 2 - 2, theme.WIDTH, 1000 + system.PILL_H // 2 + 2))
    assert _ink(band) > 0
    assert _ink(f, (0, 0, theme.WIDTH, 1000 - system.PILL_H // 2 - 3)) == 0   # nothing above it


def test_error_pill_is_black_and_carries_a_white_icon():
    f = _field(); d = ImageDraw.Draw(f)
    x0, x1 = system.pill(d, theme.WIDTH / 2, 1000, "Can't reach server", icon="cloud")
    plain = _field(); system.pill(ImageDraw.Draw(plain), theme.WIDTH / 2, 1000, "Can't reach server")
    assert _ink(f) > _ink(plain) * 0.9                     # the same black slab, a bit wider
    icon_box = (int(x0) + system.PILL_PAD, 1000 - 24, int(x0) + system.PILL_PAD + 56, 1000 + 24)
    assert (np.asarray(f.crop(icon_box)) > 128).sum() > 40 # the slashed cloud, in white


def test_pill_shrinks_its_type_to_fit_max_w():
    f = _field(); d = ImageDraw.Draw(f)
    text = "A very long message that would otherwise run right off the panel edge"
    x0, x1 = system.pill(d, theme.WIDTH / 2, 1000, text, max_w=700)
    assert x1 - x0 <= 700


def test_note_pill_sits_between_the_corner_marks():
    spec = SingleSpec(common_name="American Robin", scientific_name="Turdus migratorius",
                      when=datetime(2026, 9, 12, 8, 14), note="No detections since 11:27 pm", note_kind="quiet")

    class _Blank(ArtProvider):
        def artwork(self, c, s):
            return Artwork(image=Image.new("L", (600, 400), 255), plate=None)

    out = compose.render_single(spec, _Blank())
    marks = _field()
    typography.date_mark(marks, spec.when); typography.plate_mark(marks, 388)
    only = _field(); typography.note_line(only, spec.note, max_w=compose.note_width(), kind="quiet")
    note_px = np.asarray(only) < 128
    assert note_px.any() and not ((np.asarray(marks) < 128) & note_px).any()
    ys, xs = np.where(note_px)
    assert ys.max() < theme.HEIGHT - 8                     # inside the panel
    assert abs((xs.min() + xs.max()) / 2 - theme.WIDTH / 2) < 8
    assert _ink(out) > 0


def test_every_note_is_the_black_pill():
    quiet = _field(); typography.note_line(quiet, "No detections since 8 am", kind="quiet")
    outage = _field(); typography.note_line(outage, "No detections since 8 am", kind="outage")
    failed = _field(); typography.note_line(failed, "No detections since 8 am", kind="imagegen")
    assert _ink(outage) > _ink(quiet) * 0.9                # solid too, not an outline
    assert _ink(failed) == _ink(quiet)


def test_card_fits_its_lines_and_returns_its_bottom():
    f = _field(); d = ImageDraw.Draw(f)
    bottom = system.card(d, theme.WIDTH / 2, 700, [("No detections yet", 54, 600),
                                                     ("Listening since 11 September, 11:32 pm", 38, 500)])
    assert bottom > 700 + 2 * 54
    assert _ink(f, (0, 700, theme.WIDTH, int(bottom))) > 20000   # the black box


def test_welcome_uses_the_system_voice_not_the_script():
    since = datetime(2026, 9, 11, 23, 32)
    down = welcome.render_welcome(since, source_ok=False, now=since)
    up = welcome.render_welcome(since, source_ok=True, now=since)
    assert down.tobytes() != up.tobytes()
    assert welcome.HEADLINE == "No detections yet"
    assert welcome.since_words(since) == "Listening since 11 September, 11:32 pm"
    # The card is a black box centred on the panel: a solid band of ink at mid-height.
    mid = down.crop((0, theme.HEIGHT // 2 - 30, theme.WIDTH, theme.HEIGHT // 2 + 30))
    assert _ink(mid) > 10000
    # The fault pill rests where the firmware rests its toasts; no date footer.
    band = down.crop((0, system.TOAST_Y, theme.WIDTH, system.TOAST_Y + system.PILL_H))
    assert _ink(band) > 500
    assert _ink(down, (0, theme.HEIGHT - 40, theme.WIDTH, theme.HEIGHT)) == 0


def test_vendor_label_names_the_drawer():
    assert vendor_label("gpt-image-2.5-sunburst") == "OpenAI"
    assert vendor_label("imagen-4.0") == "Google"
    assert vendor_label("black-forest-labs/flux-1.1-pro") == "black-forest-labs/flux-1.1-pro"
    assert vendor_label("local:sdxl") == "sdxl"
    assert vendor_label(None) is None and vendor_label("") is None
