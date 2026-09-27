#!/usr/bin/env python3
"""The printed pieces that go in each kit's box: the setup card and the label
for the frame's back. Writes print-ready PDFs through headless Chrome.

    ./.venv/bin/python scripts/print_kit.py card --out ~/Desktop/kit
    ./.venv/bin/python scripts/print_kit.py labels 10B41DE886D8:ee03 A0B1C2D3E4F5:ee02 --out ~/Desktop/kit
    ./.venv/bin/python scripts/print_kit.py labels --calibrate --out ~/Desktop/kit

The card is the same in every box: `card.pdf` is two Letter pages, the
outside then the inside, two cards to a sheet. Print it on both sides,
flipped on the long edge, at actual size; cut the sheet in half and fold
each card down the middle.

The label carries the kit's serial (its device id, as the webapp's Details
shows it) and model, eight to a Letter sheet of 2 x 3 in labels (Avery 22822
and its white twins). Where a sheet's labels sit differs by product, so the
grid is set by --top, --left, --gap-x and --gap-y (inches); --calibrate prints
the grid's outlines on plain paper to hold against a label sheet. --skip N
starts on the Nth label of a part-used sheet.

The setup screen on the card is drawn from the server's own pairing screen
(`welcome.render_pairing`), in the kit's frame and mat at their true
proportions, with the QR, the code and its lines as wireframes, so no one
takes the example for a real code.

The FCC text is what the module's grant (Z4T-XIAOESP32S3P, a single modular
approval) asks of a host: its FCC ID and the part 15 statement on the label,
and the 20 cm separation told to the owner on the card.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from featherframe import paths  # noqa: E402
from featherframe.render import theme, typography, welcome  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FONTS = Path(__file__).resolve().parents[1] / "featherframe" / "fonts"
WW_SVG = REPO / "site" / "public" / "img" / "wells-workshop.svg"

MODELS = {"ee03": "FF-EE03", "ee02": "FF-EE02"}
POWER = "5 V ⎓ 0.5 A"   # measured 27 Sep 2026: 0.08 A average, 0.15 A at boot
ADDRESS = "Wells Workshop LLC, 2389 Main St. Ste 100, Glastonbury CT 06033"
FCC_ID = "Z4T-XIAOESP32S3P"
HELP = "featherframe.app/help"

# Avery 22822: 2 x 3 in, 8 to a Letter sheet, two across and four down.
LABEL_W, LABEL_H = 3.0, 2.0
COLS, ROWS = 2, 4

# The kit's frame, measured from site/public/models/featherframe.glb (mm, with
# the model's 12 degree lean on its stand taken out).
FRAME_W, FRAME_H = 231.7, 295.2       # walnut, outside
RAIL = 12.5                            # its rail, a round bead across the face
CORNER = 7.2                           # outer corner radius
MAT_W, MAT_H = 206.7, 270.2            # the mat, inside the walnut's sight edge
OPEN_W, OPEN_H = 144.9, 195.7          # the mat's opening
PANEL_W, PANEL_H = 157.2, 209.6        # the panel's active area: the picture


# -- the setup screen, as the card shows it ------------------------------------

def _dashed(draw: ImageDraw.ImageDraw, box, fill, w=4, dash=18, gap=12) -> None:
    x0, y0, x1, y1 = box
    for a, b, c, e in [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]:
        n = max(abs(c - a), abs(e - b))
        t = 0
        while t < n:
            s, f = t, min(t + dash, n)
            draw.line([(a, b + s), (a, b + f)] if a == c else [(a + s, b), (a + f, b)], fill=fill, width=w)
            t += dash + gap


def setup_screen() -> Image.Image:
    """The pairing screen with its QR, code and lines drawn as wireframes."""
    code, expires = "EHT-AMD", "28 September, 10:32 AM"
    url = "HTTPS://CLOUD.FEATHERFRAME.APP/SETUP/ABCDEF/ABCDEFGHJK12"
    im = welcome.render_pairing(code, expires=expires, url=url).convert("L")
    im = im.point(lambda v: 255 if v >= 236 else round(v * 255 / 236))
    qr = welcome.setup_qr(url)
    lines = welcome._setup_lines(code, expires)
    qx, qy = welcome.setup_qr_origin(code, expires, url)
    left = qx + qr.width + welcome._ROW_GAP
    steps = [0] + [welcome._ROW_CODE_SIZE * 0.9] + [welcome._ROW_LINE_SIZE * 1.85] * (len(lines) - 2)
    y = qy + qr.height / 2 - sum(steps) / 2 + welcome._ROW_CODE_SIZE * 0.35

    draw = ImageDraw.Draw(im)
    draw.rectangle([qx - 10, qy - 20, im.width, im.height], fill=255)
    wire, bar = 140, 200
    ys, xs = np.where(np.array(qr.convert("L")) < 128)
    x0, y0, x1, y1 = qx + xs.min(), qy + ys.min(), qx + xs.max(), qy + ys.max()
    _dashed(draw, (x0, y0, x1, y1), wire)
    f = (x1 - x0) * 0.24
    for fx, fy in [(x0, y0), (x1 - f, y0), (x0, y1 - f)]:
        draw.rectangle([fx, fy, fx + f, fy + f], outline=wire, width=5)
        draw.rectangle([fx + f * 0.3, fy + f * 0.3, fx + f * 0.7, fy + f * 0.7], fill=bar)

    for i, ((text, size, _), step) in enumerate(zip(lines, steps)):
        y += step
        w = typography.engraved_width(text, size)
        if i:
            h = size * 0.5
            draw.rounded_rectangle([left, y - h - 2, left + w, y - 2], radius=h / 2, fill=bar)
            continue
        # The code: each letter's own width, cut from the code as the screen sets it.
        scratch = Image.new("L", im.size, 255)
        typography.draw_engraved(ImageDraw.Draw(scratch), left + w / 2, y, text, size, 0)
        ink = np.array(scratch) < 160
        rows = np.where(ink.any(axis=1))[0]
        cols = np.where(ink.any(axis=0))[0]
        runs, start = [], cols[0]
        for a, b in zip(cols, cols[1:]):
            if b != a + 1:
                runs.append((start, a))
                start = b
        runs.append((start, cols[-1]))
        for c0, c1 in runs:
            rr = np.where(ink[:, c0:c1 + 1].any(axis=1))[0]
            if rr.max() - rr.min() < (rows.max() - rows.min()) * 0.4:      # the dash
                mid = (rr.min() + rr.max()) / 2
                draw.line([(c0, mid), (c1, mid)], fill=wire, width=6)
            else:
                _dashed(draw, (c0, rows.min(), c1, rows.max()), wire, dash=12, gap=8)
    return im


def framed_screen() -> Image.Image:
    """The lower part of the kit in its walnut and mat, fading out at the top."""
    screen = setup_screen().point(lambda v: round(v * 232 / 255))   # e-paper beside the mat
    px = theme.WIDTH / PANEL_W
    mm = lambda v: round(v * px)  # noqa: E731
    w, h = mm(FRAME_W), mm(FRAME_H)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    qx = np.abs(xx + 0.5 - w / 2) - (w / 2 - mm(CORNER))
    qy = np.abs(yy + 0.5 - h / 2) - (h / 2 - mm(CORNER))
    sdf = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - mm(CORNER)
    bead = np.clip(np.sin(np.clip(-sdf / mm(RAIL), 0, 1) * np.pi), 0, 1) ** 0.6
    tone = (0.5 + 0.8 * bead)[..., None] * np.array([74, 52, 37], np.float32)
    out = Image.fromarray(np.where((sdf <= 0)[..., None], np.clip(tone, 0, 255), 255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(out)

    def box(bw: float, bh: float, fill) -> None:
        x0, y0 = mm((FRAME_W - bw) / 2), mm((FRAME_H - bh) / 2)
        draw.rectangle([x0, y0, x0 + mm(bw), y0 + mm(bh)], fill=fill)

    box(MAT_W, MAT_H, (246, 245, 243))
    box(OPEN_W + 3, OPEN_H + 3, (255, 255, 255))                    # the mat's white cut edge
    px0, py0 = mm((FRAME_W - PANEL_W) / 2), mm((FRAME_H - PANEL_H) / 2)
    ox0, oy0 = mm((FRAME_W - OPEN_W) / 2), mm((FRAME_H - OPEN_H) / 2)
    out.paste(screen.crop((ox0 - px0, oy0 - py0, ox0 - px0 + mm(OPEN_W), oy0 - py0 + mm(OPEN_H))).convert("RGB"),
              (ox0, oy0))

    crop = out.crop((0, py0 + 820, w, h))
    fade = int(crop.height * 0.36)
    mask = np.full((crop.height, crop.width), 255, "uint8")
    mask[:fade] = ((np.linspace(0, 1, fade) ** 1.6) * 255).astype("uint8")[:, None]
    crop = Image.composite(crop, Image.new("RGB", crop.size, "white"), Image.fromarray(mask))
    return crop.resize((crop.width // 2, crop.height // 2), Image.LANCZOS)


def bough() -> Image.Image:
    im = Image.open(paths.art_dir() / "bough_color.png").convert("RGB")
    box = Image.eval(im.convert("L"), lambda v: 255 if v < 235 else 0).getbbox()
    return im.crop(box)


# -- the pages -----------------------------------------------------------------

def _data_uri(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _ww() -> tuple[str, str]:
    """Wells Workshop: the whole logo, and the mark alone."""
    svg = WW_SVG.read_text()
    svg = re.sub(r' class="[^"]*"', "", svg)
    paths_ = re.findall(r'<path d="[^"]+" fill="#[0-9A-Fa-f]+"/>', svg)[:2]
    mark = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 318 250">' + "".join(paths_) + "</svg>"
    return svg, mark


def _head(title: str) -> str:
    font = lambda name: (FONTS / name).as_uri()  # noqa: E731
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title><style>
@font-face{{font-family:"Pinyon";src:url("{font('PinyonScript-Regular.ttf')}")}}
@font-face{{font-family:"Fell SC";src:url("{font('IMFellDoublePicaSC.ttf')}")}}
@font-face{{font-family:"Garamond";src:url("{font('EBGaramond[wght].ttf')}");font-weight:400 800}}
@font-face{{font-family:"Garamond";font-style:italic;src:url("{font('EBGaramond-Italic[wght].ttf')}")}}
@page{{size:8.5in 11in;margin:0}}
html,body{{margin:0;background:#fff;color:#1b1e23;font-family:Garamond,Georgia,serif;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}}
b{{font-weight:600}}
.sheet{{width:816px;height:1056px;position:relative;overflow:hidden;break-after:page}}
.sheet:last-child{{break-after:auto}}
.card{{width:816px;height:528px;display:flex;position:relative}}
.panel{{width:408px;height:528px;box-sizing:border-box;display:flex;flex-direction:column}}
.num{{font-family:"Fell SC",serif;font-size:26px;line-height:.95;color:#6b5a45;width:16px;flex-shrink:0}}
.colophon{{border-top:1px solid #d8d4cd;padding-top:8px;display:grid;grid-template-columns:1fr 1fr;column-gap:16px;
  font-size:8px;line-height:1.4;color:#6b655d;text-wrap:pretty}}
.colophon>div{{display:flex;flex-direction:column;gap:3px}}
.label{{position:absolute;width:288px;height:192px;box-sizing:border-box;padding:13px 15px 12px;
  display:flex;flex-direction:column;gap:10px;overflow:hidden}}
.cap{{font-family:"Fell SC",serif;font-size:7.5px;letter-spacing:.18em;color:#3d3a36}}
.val{{font-size:12px;letter-spacing:.06em;line-height:1.25;font-variant-numeric:lining-nums tabular-nums}}
.guide{{position:absolute;width:288px;height:192px;box-sizing:border-box;border:1px dashed #888;
  display:flex;align-items:center;justify-content:center;font-size:14px;color:#888}}
</style></head><body>"""


def card_outside(logo: str, mark: str, bough_uri: str) -> str:
    return f"""<div class="card">
<div class="panel" style="padding:44px 40px 30px;gap:14px">
  <div style="display:flex;flex-direction:column;gap:8px">
    <div style="height:13px;width:108px">{logo.replace('<svg ', '<svg style="height:13px;width:auto" ', 1)}</div>
    <div style="font-size:12px;line-height:1.45;color:#3d3a36">Designed in Glastonbury, Connecticut. Illustrations from John James Audubon’s <i>The Birds of America</i> (1827–1838) and the folios of John Gould, all in the public domain.</div>
  </div>
  <div style="flex-grow:1"></div>
  <div class="colophon">
    <div><div>© 2026 Wells Workshop LLC · featherframe.app</div><div>For indoor use. {POWER} over USB-C.</div></div>
    <div><div>Contains FCC ID: {FCC_ID}.</div><div>This equipment should be installed and operated with a minimum distance of 20 cm between the antenna and your body.</div></div>
  </div>
</div>
<div class="panel" style="position:relative;overflow:hidden">
  <img src="{bough_uri}" alt="" style="position:absolute;right:0;top:34px;width:296px;height:auto">
  <div style="position:absolute;left:0;right:0;top:399px;text-align:center;font-family:Pinyon,cursive;font-size:62px;line-height:1">Featherframe</div>
  <div style="position:absolute;bottom:26px;left:0;right:0;margin:0 auto;width:16px">{mark}</div>
</div>
</div>"""


def card_inside(screen_uri: str) -> str:
    step = lambda n, t: f'<li style="display:flex;gap:16px"><span class="num">{n}</span><span>{t}</span></li>'  # noqa: E731
    return f"""<div class="card">
<div class="panel" style="padding:52px 40px 40px 44px;gap:28px">
  <h1 style="margin:0;font-weight:500;font-size:30px;line-height:1.1;letter-spacing:-.005em">Set up your frame</h1>
  <ol style="margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:22px;font-size:17px;line-height:1.42">
    {step(1, "Plug the frame in with the USB-C cable.")}
    {step(2, "On your phone, join the Wi-Fi network <b>Featherframe-Setup</b> and follow the on-screen instructions to add your new frame to your home Wi-Fi network.")}
    {step(3, "When the frame shows a code, scan it with your phone, or enter the code at <b>cloud.featherframe.app</b>.")}
  </ol>
  <div style="flex-grow:1"></div>
  <div style="font-size:14px;line-height:1.45;color:#3d3a36;font-style:italic;width:219px">Your frame should show its first picture within about a minute of setup.</div>
</div>
<div class="panel" style="padding:44px 44px 40px 40px;gap:20px">
  <div style="display:flex;flex-direction:column;gap:8px">
    <img src="{screen_uri}" alt="" style="width:324px;height:auto;display:block">
    <div style="font-size:14px;line-height:1.45;color:#3d3a36">The code changes every day, so use the one on the frame.</div>
  </div>
  <div style="display:flex;flex-direction:column;gap:6px">
    <div style="font-size:16px;font-weight:600">After setup</div>
    <div style="font-size:14px;line-height:1.45">Manage your frame in Featherframe Cloud at <b>cloud.featherframe.app</b>. If you chose your own BirdNET-Pi or BirdNET-Go, connect it there under <b>Settings → Detection source</b>.</div>
  </div>
  <div style="display:flex;flex-direction:column;gap:6px">
    <div style="font-size:16px;font-weight:600">Help</div>
    <div style="font-size:14px;line-height:1.45">{HELP}</div>
  </div>
</div>
</div>"""


def card_html() -> str:
    logo, mark = _ww()
    outside = card_outside(logo, mark, _data_uri(bough()))
    inside = card_inside(_data_uri(framed_screen()))
    return (_head("Featherframe setup card")
            + f'<div class="sheet">{outside}{outside}</div>'
            + f'<div class="sheet">{inside}{inside}</div></body></html>')


def label(serial: str, model: str, mark: str) -> str:
    return f"""<div style="display:flex;justify-content:space-between;align-items:center;height:32px">
  <div style="font-family:Pinyon,cursive;font-size:27px;line-height:1">Featherframe</div>
  <div style="width:17px">{mark}</div>
</div>
<div style="display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid #1b1e23;border-bottom:1px solid #1b1e23;padding:10px 0">
  <div style="display:flex;flex-direction:column;gap:1px"><span class="cap">SERIAL</span><span class="val">{html.escape(serial)}</span></div>
  <div style="display:flex;flex-direction:column;gap:1px;align-items:flex-end"><span class="cap">MODEL</span><span class="val">{html.escape(model)}</span></div>
</div>
<div style="display:flex;flex-direction:column;gap:3px;font-size:8px;line-height:1.28;text-wrap:pretty">
  <div>{ADDRESS}. {POWER} USB-C. Contains FCC ID: {FCC_ID}.</div>
  <div>This device complies with part 15 of the FCC Rules. Operation is subject to the following two conditions: (1) This device may not cause harmful interference, and (2) this device must accept any interference received, including interference that may cause undesired operation.</div>
</div>"""


def grid(top: float, left: float, gap_x: float, gap_y: float) -> list[tuple[float, float]]:
    """Each label's top-left, in inches, in reading order."""
    return [(left + c * (LABEL_W + gap_x), top + r * (LABEL_H + gap_y)) for r in range(ROWS) for c in range(COLS)]


def labels_html(kits: list[tuple[str, str]], cells: list[tuple[float, float]], skip: int = 0,
                calibrate: bool = False) -> str:
    _, mark = _ww()
    slots = [None] * skip + list(kits)
    if calibrate:
        slots = [None] * len(cells)
    pages = []
    for i in range(0, max(len(slots), 1), len(cells)):
        items = []
        for n, ((x, y), kit) in enumerate(zip(cells, slots[i:i + len(cells)])):
            pos = f"left:{x * 96:.2f}px;top:{y * 96:.2f}px"
            if calibrate:
                items.append(f'<div class="guide" style="{pos}">{n + 1}</div>')
            elif kit:
                items.append(f'<div class="label" style="{pos}">{label(*kit, mark)}</div>')
        pages.append(f'<div class="sheet">{"".join(items)}</div>')
    return _head("Featherframe labels") + "".join(pages) + "</body></html>"


# -- the command ---------------------------------------------------------------

def parse_kit(arg: str, default: str) -> tuple[str, str]:
    """"10B41DE886D8:ee03" (or a MAC with colons) -> ("10B41DE886D8", "FF-EE03")."""
    serial, _, kind = arg.rpartition(":") if re.search(r":(ee0[23])$", arg, re.I) else (arg, "", default)
    serial = re.sub(r"[^0-9A-Fa-f]", "", serial).upper()
    if len(serial) != 12:
        raise argparse.ArgumentTypeError(f"not a device id: {arg}")
    kind = kind.lower()
    if kind not in MODELS:
        raise argparse.ArgumentTypeError(f"no model for {arg}: add :ee03 or :ee02, or pass --model")
    return serial, MODELS[kind]


def chrome() -> str:
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("Needs Google Chrome or Chromium to write the PDF.")


def to_pdf(page: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.html"
        src.write_text(page)
        subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--allow-file-access-from-files", "--virtual-time-budget=5000",
                        f"--print-to-pdf={out}", src.as_uri()],
                       check=True, capture_output=True)
    print(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    c = sub.add_parser("card", help="the setup card, the same in every box")
    c.add_argument("--out", type=Path, default=Path("."))
    lab = sub.add_parser("labels", help="one label per kit, for the frame's back")
    lab.add_argument("kits", nargs="*", help="device ids, each with :ee03 or :ee02 unless --model says")
    lab.add_argument("--model", choices=sorted(MODELS), default=None)
    lab.add_argument("--out", type=Path, default=Path("."))
    lab.add_argument("--top", type=float, default=0.6, help="inches from the sheet's top to the first row")
    lab.add_argument("--left", type=float, default=0.833, help="inches from the sheet's left to the first column")
    lab.add_argument("--gap-x", type=float, default=0.833)
    lab.add_argument("--gap-y", type=float, default=0.6)
    lab.add_argument("--skip", type=int, default=0, help="labels already used on the first sheet")
    lab.add_argument("--calibrate", action="store_true", help="print the grid's outlines, no labels")
    a = ap.parse_args(argv)
    a.out.expanduser().mkdir(parents=True, exist_ok=True)
    out = a.out.expanduser()
    if a.what == "card":
        to_pdf(card_html(), out / "card.pdf")
        return
    if not a.kits and not a.calibrate:
        ap.error("name at least one kit, or pass --calibrate")
    try:
        kits = [parse_kit(k, a.model or "") for k in a.kits]
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
    cells = grid(a.top, a.left, a.gap_x, a.gap_y)
    name = "labels-calibrate.pdf" if a.calibrate else "labels.pdf"
    to_pdf(labels_html(kits, cells, a.skip, a.calibrate), out / name)


if __name__ == "__main__":
    main()
