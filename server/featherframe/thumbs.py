"""Small JPEG stand-ins for the page's lists of full-size sheets.

A generated illustration or a kept collage is a PNG of a megabyte or more;
the Settings lists show each at 64×85. `thumb_for` draws one JPEG per image,
once, into a `thumbs/` folder beside it, and draws it again only when the
image is newer (a regenerate). Best-effort: None if it cannot be drawn, and
the caller serves the full image instead.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PIL import Image

from . import hosted

log = logging.getLogger(__name__)

# Three times the lists' 64×85 cell: sharp on a 3× phone screen.
THUMB_SIZE = (192, 256)
THUMB_DIR = "thumbs"


def thumb_for(src: Path) -> Optional[Path]:
    """The thumbnail of `src`, drawn if it is missing or older than `src`."""
    dest = src.parent / THUMB_DIR / f"{src.stem}.jpg"
    try:
        # A source still at the front door (W-915) has not changed since its
        # thumbnail was drawn: a new one is written here.
        if dest.exists() and (not src.exists() or dest.stat().st_mtime >= src.stat().st_mtime):
            return dest
        with Image.open(hosted.local(src)) as im:
            im.thumbnail(THUMB_SIZE, Image.LANCZOS)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, (255, 255, 255))
                flat.paste(im, mask=im.getchannel("A"))
                im = flat
            elif im.mode != "RGB":
                im = im.convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=82, optimize=True)
        os.replace(tmp, dest)
        return dest
    except Exception:  # noqa: BLE001 — the full image is always there to serve
        log.warning("no thumbnail for %s", src, exc_info=True)
        return None


def drop_thumb(src: Path) -> None:
    hosted.remove(src.parent / THUMB_DIR / f"{src.stem}.jpg")
